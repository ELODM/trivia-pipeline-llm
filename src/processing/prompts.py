"""
Prompts du benchmark : construction des prompts et analyse des réponses.

Ce fichier est la SOURCE UNIQUE des prompts : il est utilisé par le test (test_models.py)
et par le benchmark (run_benchmark.py), pour garantir des prompts strictement identiques.

Trois versions sont comparées :
  v1_open        question ouverte, sans choix          -> mesure le rappel pur de connaissances
  v2_mcq_letter  QCM, réponse par une lettre (A-D)     -> format QCM classique
  v3_mcq_text    QCM sans lettres, réponse = l'option  -> réduit le biais de lettre / position
"""
import random
import re
import unicodedata
from difflib import get_close_matches

# Rôle + règles générales, communs à toutes les versions
SYSTEM_PROMPT = (
    "You are an expert trivia player answering general-knowledge quiz questions. "
    "Always follow the requested answer format exactly. Never explain your answer."
)

PROMPT_VERSIONS = ["v1_open", "v2_mcq_letter", "v3_mcq_text"]

# Longueur maximale de réponse autorisée par version (évite les réponses bavardes et lentes)
MAX_TOKENS = {"v1_open": 30, "v2_mcq_letter": 5, "v3_mcq_text": 40}

LETTERS = "ABCD"


# ------------------------------------------------------------------ construction
def shuffled_choices(question_id, correct, incorrect):
    """Mélange reproductible des options : même question => même ordre à chaque exécution."""
    choices = [correct] + list(incorrect)
    random.Random(question_id).shuffle(choices)
    return choices


def build_prompt(version, question, qtype, choices):
    """Construit le texte envoyé au modèle. L'instruction finale 'Answer:' sert d'amorce."""
    if version == "v1_open":
        if qtype == "boolean":
            return (
                "Is the following statement true or false?\n"
                "Reply with exactly one word: True or False.\n\n"
                f"Statement: {question}\n"
                "Answer:"
            )
        return (
            "Answer the following trivia question.\n"
            "Give only the answer itself, as short as possible "
            "(a name, a number, a word or a short phrase). No explanation, no full sentence.\n\n"
            f"Question: {question}\n"
            "Answer:"
        )

    if version == "v2_mcq_letter":
        letters = LETTERS[:len(choices)]
        options = "\n".join(f"{letter}. {choice}" for letter, choice in zip(letters, choices))
        return (
            "Answer the following multiple-choice trivia question. Exactly one option is correct.\n"
            f"Reply with only the letter of the correct option ({', '.join(letters)}), nothing else.\n\n"
            f"Question: {question}\n\n"
            f"Options:\n{options}\n\n"
            "Answer:"
        )

    if version == "v3_mcq_text":
        options = "\n".join(f"- {choice}" for choice in choices)
        return (
            "Answer the following multiple-choice trivia question. Exactly one option is correct.\n"
            "Reply by copying the correct option exactly as written, nothing else.\n\n"
            f"Question: {question}\n\n"
            f"Options:\n{options}\n\n"
            "Answer:"
        )

    raise ValueError(f"Version de prompt inconnue : {version}")


# ------------------------------------------------------------------ analyse des réponses
def _light(text):
    """Normalisation légère : casse, espaces, guillemets et point final."""
    text = re.sub(r"\s+", " ", str(text).casefold()).strip()
    return text.strip(" \"'`.").strip()


def normalize(text):
    """Normalisation forte : accents, ponctuation et espaces supprimés."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def parse_letter(raw, choices):
    """'B', 'B.', '(B)', 'B. Jupiter' -> option correspondante. Retourne (réponse, position)."""
    letters = LETTERS[:len(choices)]
    match = re.match(r"^\W*([A-Da-d])\b", raw.strip())      # lettre en tout début de réponse
    if not match:
        match = re.search(r"\b([A-D])\b", raw)                # sinon, une lettre majuscule isolée
    if match:
        idx = letters.find(match.group(1).upper())
        if idx >= 0:
            return choices[idx], idx
    return None, None


def parse_text(raw, choices):
    """Retrouve l'option recopiée par le modèle. Retourne (réponse, position)."""
    light = _light(raw)
    if not light:
        return None, None

    # 1. correspondance exacte (normalisation légère)
    exact = [i for i, c in enumerate(choices) if _light(c) == light]
    if len(exact) == 1:
        return choices[exact[0]], exact[0]

    # 2. correspondance exacte (normalisation forte)
    resp = normalize(raw)
    norms = [normalize(c) for c in choices]
    exact = [i for i, n in enumerate(norms) if n and n == resp]
    if len(exact) == 1:
        return choices[exact[0]], exact[0]

    # 3. une option apparaît en entier dans la réponse (on garde la plus longue)
    contained = [i for i, n in enumerate(norms) if n and f" {n} " in f" {resp} "]
    if contained:
        idx = max(contained, key=lambda i: len(norms[i]))
        return choices[idx], idx

    # 4. réponse très proche d'une option (petite faute de recopie)
    close = get_close_matches(resp, norms, n=1, cutoff=0.85)
    if close:
        idx = norms.index(close[0])
        return choices[idx], idx

    return None, None


def evaluate_open(raw, correct, qtype):
    """Question ouverte : la bonne réponse doit apparaître dans la réponse du modèle."""
    resp, gold = normalize(raw), normalize(correct)
    if not resp:
        return None, False
    if qtype == "boolean":
        first = resp.split()[0]
        if first in ("true", "false"):
            answer = first.capitalize()
            return answer, answer == correct
        return None, False
    padded = f" {resp} "
    is_correct = bool(gold) and (
        f" {gold} " in padded                                       # "leonardo da vinci" dans la réponse
        or (len(resp) >= 4 and padded in f" {gold} ")               # "leonardo" pour "leonardo da vinci"
    )
    return raw.strip(), is_correct


def evaluate(version, raw, qtype, correct, choices):
    """Retourne (ai_answer, ai_correct, parsable, chosen_position)."""
    if version == "v1_open":
        answer, is_correct = evaluate_open(raw, correct, qtype)
        return answer, is_correct, answer is not None, None
    if version == "v2_mcq_letter":
        answer, pos = parse_letter(raw, choices)
    else:
        answer, pos = parse_text(raw, choices)
    return answer, answer == correct, answer is not None, pos