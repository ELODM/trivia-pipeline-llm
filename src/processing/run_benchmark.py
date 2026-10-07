"""
SILVER (partie 2) - Enrichissement par IA : chaque question est posée à chaque modèle, avec chaque prompt.

Entrée  : data/silver/questions_clean.parquet
Sorties : data/silver/benchmark_sample_<N>.parquet   échantillon figé (mêmes questions pour tous)
          data/silver/ai_responses/*.parquet          réponses des modèles, écrites par lots de 50
          data/silver/benchmark.log                   journal d'exécution

Principes (les mêmes que pour la collecte bronze) :
  - enrichissement progressif : chaque lot de 50 réponses est écrit dès qu'il est complet
  - reprise : au redémarrage, les couples (question, modèle, prompt) déjà traités sont ignorés
  - reproductibilité : échantillon figé, choix mélangés de façon fixe, température 0
  - traçabilité : prompt envoyé, réponse brute, tokens et date conservés pour chaque réponse

Prérequis : serveur LM Studio démarré (onglet Developer > Status : Running).

Usage (relancer la même commande pour reprendre) :
  python src/processing/run_benchmark.py
      -> réglages du .env (BENCHMARK_MODELS, SAMPLE_SIZE) et les 3 prompts
  python src/processing/run_benchmark.py --models qwen2.5-1.5b-instruct --prompts v2_mcq_letter --sample all
      -> un modèle, un prompt, toutes les questions du dataset
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from dotenv import load_dotenv
from tqdm import tqdm

from prompts import MAX_TOKENS, PROMPT_VERSIONS, SYSTEM_PROMPT, build_prompt, evaluate, shuffled_choices

# ---------------------------------------------------------------- configuration
load_dotenv()
BASE_URL = os.getenv("LMSTUDIO_URL", "http://127.0.0.1:1234/v1")
MODELS = [m.strip() for m in os.getenv("BENCHMARK_MODELS", "").split(",") if m.strip()]
PROMPTS = list(PROMPT_VERSIONS)
SAMPLE_SIZE = os.getenv("SAMPLE_SIZE", "300")     # un nombre, ou "all" pour toutes les questions
SEED = 42
BATCH_SIZE = 50
TEMPERATURE = 0
MAX_RETRIES = 3

SILVER = Path("data/silver")
QUESTIONS_FILE = SILVER / "questions_clean.parquet"
RESPONSES_DIR = SILVER / "ai_responses"
LOG_FILE = SILVER / "benchmark.log"

SCHEMA = pa.schema([
    ("question_id", pa.string()),
    ("model", pa.string()),
    ("prompt_version", pa.string()),
    ("system_prompt", pa.string()),
    ("prompt_text", pa.string()),
    ("choices", pa.list_(pa.string())),
    ("raw_response", pa.string()),
    ("ai_answer", pa.string()),
    ("ai_correct", pa.bool_()),
    ("parsable", pa.bool_()),
    ("chosen_position", pa.int8()),
    ("n_choices", pa.int8()),
    ("response_time", pa.float64()),
    ("prompt_tokens", pa.int32()),
    ("completion_tokens", pa.int32()),
    ("temperature", pa.float32()),
    ("max_tokens", pa.int16()),
    ("run_id", pa.string()),
    ("answered_at", pa.timestamp("us", tz="UTC")),
])

log = logging.getLogger("benchmark")


def setup_logging():
    SILVER.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )


# ---------------------------------------------------------------- échantillon
def load_sample(questions, sample_size):
    """Toutes les questions ('all'), ou un échantillon stratifié catégorie x difficulté, figé sur disque."""
    if str(sample_size).lower() == "all" or int(sample_size) >= len(questions):
        log.info(f"Dataset complet : {len(questions)} questions")
        return questions

    sample_file = SILVER / f"benchmark_sample_{int(sample_size)}.parquet"
    if sample_file.exists():
        ids = pd.read_parquet(sample_file)["question_id"]
        log.info(f"Échantillon existant réutilisé : {sample_file.name}")
        return questions[questions["question_id"].isin(ids)]

    frac = int(sample_size) / len(questions)
    sample = questions.groupby(["category", "difficulty"]).sample(frac=frac, random_state=SEED)
    sample[["question_id", "category", "difficulty", "type"]].to_parquet(sample_file, index=False)
    log.info(f"Nouvel échantillon stratifié créé : {len(sample)} questions -> {sample_file.name}")
    return sample


# ---------------------------------------------------------------- reprise et écriture
def load_done():
    """Couples (question, modèle, prompt) déjà traités lors des exécutions précédentes."""
    files = sorted(RESPONSES_DIR.glob("*.parquet"))
    if not files:
        return set()
    done = pd.concat(pd.read_parquet(f, columns=["question_id", "model", "prompt_version"]) for f in files)
    return set(done.itertuples(index=False, name=None))


class BatchWriter:
    """Accumule les réponses et écrit un fichier parquet tous les BATCH_SIZE résultats."""

    def __init__(self, run_id):
        self.run_id = run_id
        self.buffer = []
        self.part = 0
        RESPONSES_DIR.mkdir(parents=True, exist_ok=True)

    def add(self, row):
        self.buffer.append(row)
        if len(self.buffer) >= BATCH_SIZE:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        self.part += 1
        path = RESPONSES_DIR / f"{self.run_id}_part_{self.part:05d}.parquet"
        table = pa.Table.from_pylist(self.buffer, schema=SCHEMA)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, path)                       # écriture atomique
        tqdm.write(f"   lot enregistré : {path.name} ({len(self.buffer)} réponses)")
        self.buffer = []


# ---------------------------------------------------------------- appels au modèle
def check_server(session):
    try:
        available = [m["id"] for m in session.get(f"{BASE_URL}/models", timeout=10).json()["data"]]
    except requests.RequestException:
        log.error(f"Serveur LM Studio injoignable sur {BASE_URL} : démarrez-le (Developer > Status : Running)")
        sys.exit(1)
    missing = [m for m in MODELS if m not in available]
    if missing:
        log.error(f"Modèles introuvables dans LM Studio : {missing}. Disponibles : {available}")
        sys.exit(1)


def ask(session, model, prompt, max_tokens):
    """Retourne (réponse, durée en secondes, usage des tokens), ou (None, None, None) après échecs."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            start = time.perf_counter()
            resp = session.post(f"{BASE_URL}/chat/completions", json=payload, timeout=300)
            duration = time.perf_counter() - start
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"].get("content") or ""
            return content.strip(), duration, data.get("usage") or {}
        except (requests.RequestException, KeyError, ValueError) as err:
            tqdm.write(f"   ! erreur ({err.__class__.__name__}), essai {attempt}/{MAX_RETRIES}")
            time.sleep(5 * attempt)
    return None, None, None


# ---------------------------------------------------------------- programme principal
def parse_args():
    p = argparse.ArgumentParser(description="Benchmark des modèles LM Studio sur les questions OpenTDB")
    p.add_argument("--models", help="modèles séparés par des virgules (défaut : BENCHMARK_MODELS du .env)")
    p.add_argument("--prompts", help=f"versions séparées par des virgules (défaut : {','.join(PROMPT_VERSIONS)})")
    p.add_argument("--sample", help="nombre de questions, ou 'all' (défaut : SAMPLE_SIZE du .env)")
    return p.parse_args()


def main():
    global MODELS, PROMPTS
    args = parse_args()
    setup_logging()
    if args.models:
        MODELS = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.prompts:
        PROMPTS = [v.strip() for v in args.prompts.split(",") if v.strip()]
    unknown = [v for v in PROMPTS if v not in PROMPT_VERSIONS]
    if unknown:
        log.error(f"Versions de prompt inconnues : {unknown}. Disponibles : {PROMPT_VERSIONS}")
        sys.exit(1)
    if not MODELS:
        log.error("Aucun modèle : renseignez BENCHMARK_MODELS dans le .env ou utilisez --models")
        sys.exit(1)

    questions = pd.read_parquet(QUESTIONS_FILE)
    sample = load_sample(questions, args.sample or SAMPLE_SIZE)
    done = load_done()
    total = len(sample) * len(MODELS) * len(PROMPTS)
    already = sum(1 for q in sample["question_id"] for m in MODELS for v in PROMPTS if (q, m, v) in done)
    log.info(f"{len(sample)} questions x {len(MODELS)} modèle(s) x {len(PROMPTS)} prompt(s) = "
             f"{total} réponses ({already} déjà faites)")

    session = requests.Session()
    check_server(session)
    run_id = "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    writer = BatchWriter(run_id)
    errors = 0

    try:
        for model in MODELS:                        # modèle en boucle externe : un seul chargement par modèle
            todo = [(version, q) for version in PROMPTS for q in sample.itertuples(index=False)
                    if (q.question_id, model, version) not in done]
            if not todo:
                log.info(f"{model} : déjà terminé")
                continue
            log.info(f"=== {model} : {len(todo)} réponses à produire")
            ask(session, model, "Say OK.", 5)       # appel de chauffe : charge le modèle, non mesuré

            for version, q in tqdm(todo, desc=model, unit="rép"):
                choices = shuffled_choices(q.question_id, q.correct_answer, list(q.incorrect_answers))
                prompt = build_prompt(version, q.question, q.type, choices)
                raw, duration, usage = ask(session, model, prompt, MAX_TOKENS[version])
                if raw is None:
                    errors += 1                     # non enregistré : sera retenté à la prochaine exécution
                    continue
                answer, is_correct, parsable, pos = evaluate(version, raw, q.type, q.correct_answer, choices)
                writer.add({
                    "question_id": q.question_id,
                    "model": model,
                    "prompt_version": version,
                    "system_prompt": SYSTEM_PROMPT,
                    "prompt_text": prompt,
                    "choices": choices,
                    "raw_response": raw,
                    "ai_answer": answer,
                    "ai_correct": bool(is_correct),
                    "parsable": bool(parsable),
                    "chosen_position": pos,
                    "n_choices": len(choices),
                    "response_time": round(duration, 4),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "temperature": float(TEMPERATURE),
                    "max_tokens": MAX_TOKENS[version],
                    "run_id": run_id,
                    "answered_at": datetime.now(timezone.utc),
                })
    except KeyboardInterrupt:
        log.warning("Interruption : enregistrement du lot en cours, relancez la même commande pour reprendre")
    finally:
        writer.flush()

    # Récapitulatif sur l'ensemble des réponses enregistrées
    files = sorted(RESPONSES_DIR.glob("*.parquet"))
    if files:
        res = pd.concat(pd.read_parquet(f) for f in files)
        summary = res.groupby(["model", "prompt_version"]).agg(
            reponses=("ai_correct", "size"),
            precision_pct=("ai_correct", lambda s: round(s.mean() * 100, 1)),
            lisibles_pct=("parsable", lambda s: round(s.mean() * 100, 1)),
            temps_moyen_s=("response_time", lambda s: round(s.mean(), 2)),
        )
        log.info(f"Réponses enregistrées au total : {len(res)} | erreurs dans cette exécution : {errors}")
        print(summary.to_string())


if __name__ == "__main__":
    main()