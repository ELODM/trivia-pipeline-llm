"""
SILVER (partie 1) - Nettoyage et normalisation des questions.

Entrée  : data/bronze/questions_raw.csv
Sorties : data/silver/questions_clean.parquet      questions propres, typées, prêtes pour l'IA
          data/silver/questions_rejected.csv       questions écartées, avec la raison
          data/silver/clean_report.json            rapport de qualité

Transformations :
  1. Nettoyage du texte    : entités HTML décodées, Unicode normalisé, espaces superflus retirés
  2. Normalisation         : type et difficulté en minuscules, catégorie découpée (principale / sous-catégorie)
  3. Dépliage              : incorrect_answers (texte JSON) -> vraie liste + 3 colonnes incorrect_answer_1..3
  4. Contrôles qualité     : champs vides, valeurs inconnues, nombre de réponses, réponses en double
  5. Typage explicite      : schéma Parquet défini colonne par colonne

Usage : python src/processing/clean_questions.py
"""
import html
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

BRONZE_CSV = Path("data/bronze/questions_raw.csv")
SILVER = Path("data/silver")
CLEAN_FILE = SILVER / "questions_clean.parquet"
REJECTED_FILE = SILVER / "questions_rejected.csv"
REPORT_FILE = SILVER / "clean_report.json"

VALID_TYPES = {"multiple": 3, "boolean": 1}          # type -> nombre attendu de mauvaises réponses
VALID_DIFFICULTIES = {"easy", "medium", "hard"}

SCHEMA = pa.schema([
    ("question_id", pa.string()),
    ("category_id", pa.int32()),
    ("category", pa.string()),
    ("category_main", pa.string()),
    ("category_sub", pa.string()),
    ("type", pa.string()),
    ("difficulty", pa.string()),
    ("question", pa.string()),
    ("correct_answer", pa.string()),
    ("incorrect_answers", pa.list_(pa.string())),
    ("incorrect_answer_1", pa.string()),
    ("incorrect_answer_2", pa.string()),
    ("incorrect_answer_3", pa.string()),
    ("n_choices", pa.int8()),
    ("fetched_at", pa.timestamp("us", tz="UTC")),
    ("cleaned_at", pa.timestamp("us", tz="UTC")),
])


# ---------------------------------------------------------------- nettoyage du texte
def clean_text(value):
    """Décode les entités HTML, normalise l'Unicode et les espaces."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = html.unescape(str(value))                 # &quot; -> "   &#039; -> '
    text = unicodedata.normalize("NFC", text)        # é composé de 2 caractères -> é en 1 caractère
    text = re.sub(r"\s+", " ", text)                 # tabulations, retours à la ligne, doubles espaces
    return text.strip()


def split_category(category):
    """'Entertainment: Film' -> ('Entertainment', 'Film') ; 'History' -> ('History', 'History')."""
    if ": " in category:
        main, sub = category.split(": ", 1)
        return main.strip(), sub.strip()
    return category, category


# ---------------------------------------------------------------- contrôles qualité
def check_row(row):
    """Retourne la liste des problèmes détectés (vide = question valide)."""
    issues = []
    if not row["question"]:
        issues.append("question_vide")
    if not row["correct_answer"]:
        issues.append("bonne_reponse_vide")
    if row["type"] not in VALID_TYPES:
        issues.append(f"type_inconnu:{row['type']}")
    if row["difficulty"] not in VALID_DIFFICULTIES:
        issues.append(f"difficulte_inconnue:{row['difficulty']}")

    wrong = row["incorrect_answers"]
    if row["type"] in VALID_TYPES and len(wrong) != VALID_TYPES[row["type"]]:
        issues.append(f"nb_mauvaises_reponses:{len(wrong)}")
    if any(not w for w in wrong):
        issues.append("mauvaise_reponse_vide")

    answers = [row["correct_answer"]] + wrong
    if len({a.casefold() for a in answers}) != len(answers):
        issues.append("reponses_en_double")
    if row["type"] == "boolean" and {a.casefold() for a in answers} != {"true", "false"}:
        issues.append("vrai_faux_invalide")
    if "&" in row["question"] and re.search(r"&[#a-zA-Z0-9]+;", row["question"]):
        issues.append("entite_html_restante")
    return issues


# ---------------------------------------------------------------- pipeline
def main():
    raw = pd.read_csv(BRONZE_CSV, dtype=str, keep_default_na=False)
    n_bronze = len(raw)
    cleaned_at = datetime.now(timezone.utc)

    df = pd.DataFrame({
        "question_id": raw["question_id"].str.strip(),
        "category_id": pd.to_numeric(raw["category_id"], errors="coerce"),
        "category": raw["category"].map(clean_text),
        "type": raw["type"].map(clean_text).str.lower(),
        "difficulty": raw["difficulty"].map(clean_text).str.lower(),
        "question": raw["question"].map(clean_text),
        "correct_answer": raw["correct_answer"].map(clean_text),
        "incorrect_answers": raw["incorrect_answers"].map(lambda s: [clean_text(a) for a in json.loads(s or "[]")]),
        "fetched_at": pd.to_datetime(raw["fetched_at"], utc=True, errors="coerce"),
    })

    # Normalisation de la catégorie
    df[["category_main", "category_sub"]] = df["category"].apply(lambda c: pd.Series(split_category(c)))

    # Dépliage des mauvaises réponses en colonnes (None si absente, ex. vrai/faux)
    for i in range(3):
        df[f"incorrect_answer_{i + 1}"] = df["incorrect_answers"].map(lambda l, i=i: l[i] if len(l) > i else None)
    df["n_choices"] = df["incorrect_answers"].map(len) + 1
    df["cleaned_at"] = cleaned_at

    # Contrôles qualité
    df["issues"] = df.apply(check_row, axis=1)
    duplicated = df["question_id"].duplicated(keep="first")
    df.loc[duplicated, "issues"] = df.loc[duplicated, "issues"].map(lambda l: l + ["doublon_question_id"])

    rejected = df[df["issues"].map(len) > 0].copy()
    clean = df[df["issues"].map(len) == 0].drop(columns="issues")

    # Écriture : parquet typé (fichier temporaire puis renommage) + rejets en CSV
    SILVER.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(clean[SCHEMA.names], schema=SCHEMA, preserve_index=False)
    tmp = CLEAN_FILE.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, CLEAN_FILE)

    rejected["issues"] = rejected["issues"].map(lambda l: ";".join(l))
    rejected["incorrect_answers"] = rejected["incorrect_answers"].map(json.dumps)
    rejected.to_csv(REJECTED_FILE, index=False, encoding="utf-8")

    # Rapport de qualité
    issue_counts = rejected["issues"].str.split(";").explode().value_counts().to_dict() if len(rejected) else {}
    report = {
        "cleaned_at": cleaned_at.isoformat(),
        "bronze_rows": n_bronze,
        "clean_rows": len(clean),
        "rejected_rows": len(rejected),
        "rejection_reasons": {k: int(v) for k, v in issue_counts.items()},
        "by_type": {k: int(v) for k, v in clean["type"].value_counts().items()},
        "by_difficulty": {k: int(v) for k, v in clean["difficulty"].value_counts().items()},
        "categories": int(clean["category"].nunique()),
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Bronze   : {n_bronze} questions")
    print(f"Silver   : {len(clean)} questions propres -> {CLEAN_FILE}")
    print(f"Rejetées : {len(rejected)} -> {REJECTED_FILE}")
    for reason, count in report["rejection_reasons"].items():
        print(f"   - {reason} : {count}")
    print(f"Types        : {report['by_type']}")
    print(f"Difficultés  : {report['by_difficulty']}")
    print(f"Catégories   : {report['categories']}")


if __name__ == "__main__":
    main()