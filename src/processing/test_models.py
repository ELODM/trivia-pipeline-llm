"""
Test rapide : comparaison des modèles ET des versions de prompt sur 20 questions.

Mesure, pour chaque couple (modèle, prompt) :
  - la précision (% de bonnes réponses)
  - le % de réponses exploitables (format respecté)
  - le temps de réponse moyen et maximum
  - le % de fois où le modèle choisit la 1re option (biais de position, attendu ~25-30 %)

Prérequis : serveur LM Studio démarré (onglet Developer > Status : Running).
Usage     : python src/processing/test_models.py
Sortie    : docs/test_prompts_20q.csv + tableaux récapitulatifs à l'écran
"""
import html
import json
import os
import time

import pandas as pd
import requests
from dotenv import load_dotenv

from prompts import MAX_TOKENS, PROMPT_VERSIONS, SYSTEM_PROMPT, build_prompt, evaluate, shuffled_choices

load_dotenv()
URL = os.getenv("LMSTUDIO_URL", "http://127.0.0.1:1234/v1") + "/chat/completions"
MODELS = os.getenv("BENCHMARK_MODELS", "llama-3.2-1b-instruct,qwen2.5-1.5b-instruct,qwen3.5-2b").split(",")
N_QUESTIONS = 20
SEED = 42                     # mêmes 20 questions à chaque exécution => comparaison équitable


def ask(model, prompt, max_tokens):
    """Envoie le prompt au modèle et mesure le temps de réponse."""
    start = time.perf_counter()
    resp = requests.post(URL, json={
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        "temperature": 0,     # réponse déterministe => benchmark reproductible
        "max_tokens": max_tokens,
    }, timeout=180)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"] or ""
    return raw.strip(), time.perf_counter() - start


def main():
    df = pd.read_csv("data/bronze/questions_raw.csv")
    sample = df.sample(N_QUESTIONS, random_state=SEED)

    results = []
    for model in MODELS:
        print(f"\n=== {model} ===")
        ask(model, "Say OK.", 5)                      # appel de chauffe : charge le modèle, non mesuré

        for version in PROMPT_VERSIONS:
            print(f"--- {version}")
            for _, row in sample.iterrows():
                question = html.unescape(row["question"])
                correct = html.unescape(row["correct_answer"])
                incorrect = [html.unescape(a) for a in json.loads(row["incorrect_answers"])]
                choices = shuffled_choices(row["question_id"], correct, incorrect)

                prompt = build_prompt(version, question, row["type"], choices)
                raw, duration = ask(model, prompt, MAX_TOKENS[version])
                answer, is_correct, parsable, pos = evaluate(version, raw, row["type"], correct, choices)

                results.append({
                    "model": model, "prompt_version": version,
                    "question": question, "type": row["type"], "difficulty": row["difficulty"],
                    "correct_answer": correct, "raw_response": raw, "ai_answer": answer,
                    "ai_correct": is_correct, "parsable": parsable,
                    "chosen_position": pos, "response_time": round(duration, 3),
                })
                status = "OK" if is_correct else "KO"
                print(f"{status}  {duration:5.2f}s  {raw[:25]!r:28} {question[:50]}")

    res = pd.DataFrame(results)
    res["first_option"] = res["chosen_position"].eq(0)

    summary = res.groupby(["model", "prompt_version"]).agg(
        precision_pct=("ai_correct", lambda s: round(s.mean() * 100, 1)),
        lisibles_pct=("parsable", lambda s: round(s.mean() * 100, 1)),
        temps_moyen_s=("response_time", lambda s: round(s.mean(), 2)),
        temps_max_s=("response_time", lambda s: round(s.max(), 2)),
    )
    mcq = res[res["chosen_position"].notna()]
    bias = mcq.groupby(["model", "prompt_version"])["first_option"].mean().mul(100).round(1)
    summary["choix_1re_option_pct"] = bias

    print("\n=== Récapitulatif (modèle x prompt) ===")
    print(summary.to_string())
    print("\n=== Précision (%) : modèles en lignes, prompts en colonnes ===")
    print(summary["precision_pct"].unstack().to_string())

    os.makedirs("docs", exist_ok=True)
    res.to_csv("docs/test_prompts_20q.csv", index=False, encoding="utf-8")
    print("\nDétail enregistré dans docs/test_prompts_20q.csv")


if __name__ == "__main__":
    main()