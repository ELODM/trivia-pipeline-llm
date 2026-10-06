"""
BRONZE - Ingestion complète de l'API Open Trivia DB.

Principes appliqués (inspirés du projet GitHub OTDB-Source et des bonnes pratiques d'ingestion) :
  1. raw immuable : chaque réponse de l'API est écrite telle quelle dans son propre fichier
  2. écrire avant d'avancer : le lot est sur disque avant la mise à jour de l'état
  3. écritures atomiques (fichier temporaire puis renommage)
  4. reprise : token et avancement dans state.json, index des doublons reconstruit depuis raw/
  5. limiteur de débit : 1 requête toutes les 5 s minimum, quel que soit l'endpoint
  6. backoff exponentiel + jitter, nombre d'essais plafonné, respect de Retry-After
  7. erreurs temporaires réessayées, erreurs permanentes signalées
  8. fin de catégorie : taille de lot décroissante 50 -> 25 -> 10 -> 5 -> 1
  9. garde-fous : plafonds de requêtes, de lots sans nouveauté et de renouvellements de token
 10. réconciliation avec le référentiel de l'API dans un rapport

Sorties :
  data/bronze/raw/                 réponses brutes de l'API (source de vérité)
  data/bronze/questions_raw.csv    livrable bronze (questions uniques)
  data/bronze/scrape_report.json   comparaison avec le nombre officiel de l'API
  data/bronze/state.json           token + avancement (pour la reprise)
  data/bronze/scrape.log           journal d'exécution

Usage :
  python src/ingestion/scrape_opentdb.py            collecte complète (reprend si interrompu) + CSV
  python src/ingestion/scrape_opentdb.py --build    régénère seulement le CSV depuis raw/, sans réseau
"""
import argparse
import hashlib
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import requests

# ---------------------------------------------------------------- configuration
BASE_URL = "https://opentdb.com"
MIN_INTERVAL = 5.2            # secondes entre deux requêtes (limite API : 5 s par IP)
MAX_RETRIES = 5               # essais pour une erreur temporaire
BACKOFF_BASE = 5              # premier délai de backoff, doublé à chaque essai
BACKOFF_MAX = 120             # délai maximum
BATCH_SIZES = [50, 25, 10, 5, 1]
MAX_STALE_BATCHES = 3         # lots consécutifs sans nouvelle question avant abandon
MAX_TOKEN_RENEWALS = 3        # renouvellements de token par catégorie

BRONZE = Path("data/bronze")
RAW = BRONZE / "raw"
REFERENCE = RAW / "reference"
STATE_FILE = BRONZE / "state.json"
CSV_FILE = BRONZE / "questions_raw.csv"
REPORT_FILE = BRONZE / "scrape_report.json"

log = logging.getLogger("opentdb")


# ---------------------------------------------------------------- utilitaires
def setup_logging():
    BRONZE.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(BRONZE / "scrape.log", encoding="utf-8"),
        ],
    )


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write_json(path, data):
    """Écrit dans un fichier temporaire puis renomme : jamais de fichier à moitié écrit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def decode_record(q):
    """Supprime l'encodage de transport RFC 3986 (aucun nettoyage métier ici)."""
    return {k: [unquote(x) for x in v] if isinstance(v, list) else unquote(v) for k, v in q.items()}


def question_id(q):
    """Identifiant stable : même question => même id, à chaque exécution."""
    content = "|".join([q["category"], q["question"], q["correct_answer"]])
    return hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- client API
class OpenTDBClient:
    def __init__(self):
        self.session = requests.Session()              # réutilise la connexion HTTP
        self.session.headers["User-Agent"] = "efrei-trivia-benchmark/1.0 (projet etudiant)"
        self._last_call = 0.0

    def _throttle(self):
        """Limiteur de débit : attend seulement le temps restant depuis le dernier appel."""
        wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def get(self, endpoint, params=None):
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            resp = None
            try:
                resp = self.session.get(f"{BASE_URL}/{endpoint}", params=params, timeout=(10, 30))
            except requests.RequestException as err:
                reason = f"erreur réseau ({err.__class__.__name__})"
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    reason = f"HTTP {resp.status_code}"
                elif resp.status_code >= 400:
                    resp.raise_for_status()             # erreur permanente : inutile de réessayer
                else:
                    try:
                        data = resp.json()
                    except ValueError:
                        reason = "JSON illisible"
                    else:
                        if data.get("response_code") != 5:
                            return data
                        reason = "code API 5 (limite de débit)"

            delay = min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (attempt - 1)) * random.uniform(0.8, 1.2)
            retry_after = resp.headers.get("Retry-After") if resp is not None else None
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
            log.warning(f"   {reason} - essai {attempt}/{MAX_RETRIES}, nouvelle tentative dans {delay:.0f}s")
            time.sleep(delay)
        raise RuntimeError(f"Échec définitif sur {endpoint} après {MAX_RETRIES} essais")

    def new_token(self):
        return self.get("api_token.php", {"command": "request"})["token"]


# ---------------------------------------------------------------- stockage brut
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"token": None, "categories": {}, "runs": []}


def raw_batch_files():
    return sorted(RAW.glob("category_*/batch_*.json"))


def save_batch(cat_id, params, response):
    """Un fichier par appel, jamais écrasé. Le token n'est pas enregistré."""
    folder = RAW / f"category_{cat_id:02d}"
    n = len(list(folder.glob("batch_*.json"))) + 1 if folder.exists() else 1
    path = folder / f"batch_{n:05d}.json"
    atomic_write_json(path, {
        "category_id": cat_id,
        "fetched_at": now_iso(),
        "request": params,
        "response": response,
    })
    return path


def rebuild_index():
    """Reconstruit la liste des questions déjà possédées depuis raw/ (source de vérité)."""
    seen = {}
    for f in raw_batch_files():
        batch = json.loads(f.read_text(encoding="utf-8"))
        cid = batch["category_id"]
        for q in batch["response"]["results"]:
            seen.setdefault(cid, set()).add(question_id(decode_record(q)))
    return seen


# ---------------------------------------------------------------- collecte
def download_category(client, state, cat, expected, seen):
    cid = cat["id"]
    cstate = state["categories"].setdefault(str(cid), {"name": cat["name"], "status": "pending"})
    have = seen.setdefault(cid, set())

    if cstate["status"] == "complete" or (expected and len(have) >= expected):
        cstate.update(status="complete", stored=len(have))
        log.info(f"   déjà complète : {len(have)}/{expected}")
        return

    size_idx, stale, renewals, done, refill = 0, 0, 0, 0, False
    max_requests = expected // 50 + 20

    while True:
        if done >= max_requests:
            cstate.update(status="incomplete", reason="plafond de requêtes atteint")
            break

        size = BATCH_SIZES[size_idx]
        amount = size if refill else min(size, max(expected - len(have), 1))
        params = {"amount": amount, "category": cid, "encode": "url3986"}
        data = client.get("api.php", {**params, "token": state["token"]})
        done += 1
        code = data["response_code"]

        if code == 0:
            path = save_batch(cid, params, data)                    # 1. écrire d'abord
            new = 0
            for q in data["results"]:
                qid = question_id(decode_record(q))
                if qid not in have:
                    have.add(qid)
                    new += 1
            stale = 0 if new else stale + 1
            cstate["stored"] = len(have)
            atomic_write_json(STATE_FILE, state)                    # 2. puis l'état
            log.info(f"   {path.parent.name}/{path.name} : +{new} nouvelles -> {len(have)}/{expected}")

            if expected and len(have) >= expected:
                cstate.update(status="complete", reason="nombre attendu atteint")
                break
            stale_limit = MAX_STALE_BATCHES + (len(have) // 50 if refill else 0)
            if stale >= stale_limit:
                cstate.update(status="incomplete", reason="lots successifs sans nouveauté")
                break

        elif code in (1, 4):                                        # moins de questions dispo
            if size_idx < len(BATCH_SIZES) - 1:
                size_idx += 1
            else:
                cstate.update(status="complete", reason=f"catégorie épuisée (code {code})")
                break

        elif code == 3:                                             # token expiré
            if renewals >= MAX_TOKEN_RENEWALS:
                cstate.update(status="failed", reason="token rejeté à répétition")
                break
            state["token"] = client.new_token()
            renewals += 1
            refill, size_idx = True, 0
            atomic_write_json(STATE_FILE, state)
            log.warning("   token expiré : nouveau token, les doublons seront ignorés")

        else:                                                       # code 2 ou inconnu
            cstate.update(status="failed", reason=f"code API {code}")
            break

    cstate["stored"] = len(have)
    atomic_write_json(STATE_FILE, state)
    log.info(f"   => {cstate['status']} ({cstate.get('reason', '')}) : {len(have)}/{expected}")


def download():
    client = OpenTDBClient()
    state = load_state()
    seen = rebuild_index()

    categories = client.get("api_category.php")["trivia_categories"]
    counts = client.get("api_count_global.php")
    run_stamp = stamp()
    atomic_write_json(REFERENCE / f"categories_{run_stamp}.json", {"fetched_at": now_iso(), "response": categories})
    atomic_write_json(REFERENCE / f"count_global_{run_stamp}.json", {"fetched_at": now_iso(), "response": counts})

    expected = {int(k): v["total_num_of_verified_questions"] for k, v in counts["categories"].items()}
    log.info(f"Référentiel API : {counts['overall']['total_num_of_verified_questions']} questions vérifiées, "
             f"{len(categories)} catégories")

    if not state["token"]:
        state["token"] = client.new_token()
        atomic_write_json(STATE_FILE, state)

    for i, cat in enumerate(categories, 1):
        log.info(f"[{i}/{len(categories)}] {cat['name']} (id {cat['id']})")
        download_category(client, state, cat, expected.get(cat["id"], 0), seen)

    state["runs"].append({"finished_at": now_iso()})
    atomic_write_json(STATE_FILE, state)


# ---------------------------------------------------------------- build du CSV
def build():
    files = raw_batch_files()
    if not files:
        log.error("Aucun fichier brut dans data/bronze/raw : lancez d'abord la collecte")
        return

    rows = []
    for f in files:
        batch = json.loads(f.read_text(encoding="utf-8"))
        for q in batch["response"]["results"]:
            d = decode_record(q)
            rows.append({
                "question_id": question_id(d),
                "category_id": batch["category_id"],
                "category": d["category"],
                "type": d["type"],
                "difficulty": d["difficulty"],
                "question": d["question"],
                "correct_answer": d["correct_answer"],
                "incorrect_answers": json.dumps(d["incorrect_answers"], ensure_ascii=False),
                "fetched_at": batch["fetched_at"],
                "source_file": f.relative_to(BRONZE).as_posix(),
            })

    df = pd.DataFrame(rows)
    n_raw = len(df)
    df = df.drop_duplicates("question_id", keep="first")
    tmp = CSV_FILE.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, CSV_FILE)

    # Réconciliation avec le dernier référentiel enregistré
    ref_files = sorted(REFERENCE.glob("count_global_*.json"))
    counts = json.loads(ref_files[-1].read_text(encoding="utf-8"))["response"] if ref_files else None
    per_cat = df.groupby("category_id").size().to_dict()
    report = {
        "built_at": now_iso(),
        "raw_records": n_raw,
        "duplicates_removed": n_raw - len(df),
        "unique_questions": len(df),
        "api_verified_total": counts["overall"]["total_num_of_verified_questions"] if counts else None,
        "categories": [
            {
                "category_id": int(cid),
                "api_verified": v["total_num_of_verified_questions"],
                "collected": int(per_cat.get(int(cid), 0)),
            }
            for cid, v in (counts["categories"].items() if counts else [])
        ],
    }
    atomic_write_json(REPORT_FILE, report)

    log.info(f"CSV bronze : {len(df)} questions uniques ({n_raw - len(df)} doublons retirés) -> {CSV_FILE}")
    if counts:
        gap = report["api_verified_total"] - len(df)
        log.info(f"Référentiel API : {report['api_verified_total']} | écart : {gap}")
        for c in report["categories"]:
            if c["collected"] != c["api_verified"]:
                log.warning(f"   catégorie {c['category_id']} : {c['collected']}/{c['api_verified']}")


# ---------------------------------------------------------------- point d'entrée
def parse_args():
    p = argparse.ArgumentParser(description="Ingestion bronze de l'API Open Trivia DB")
    p.add_argument("--build", action="store_true", help="régénère le CSV depuis raw/ sans appeler l'API")
    return p.parse_args()


def main():
    args = parse_args()
    setup_logging()
    try:
        if not args.build:
            download()
        build()
    except KeyboardInterrupt:
        log.warning("Interrompu : la progression est sauvegardée, relancez la même commande pour reprendre")
        sys.exit(130)


if __name__ == "__main__":
    main()