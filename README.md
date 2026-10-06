# Trivia LLM Benchmark

Benchmark de modèles d'IA locaux (Ollama) sur les questions de culture générale d'OpenTDB,
avec une architecture médaillon (bronze → silver → gold) construite avec Python, dbt et DuckDB,
et restituée dans un dashboard Streamlit.

## Architecture

| Couche | Contenu | Format | Outil |
|---|---|---|---|
| Bronze | Questions brutes issues de l'API OpenTDB | `questions_raw.csv` | Python |
| Silver | Questions nettoyées + réponses brutes des modèles | Parquet | Python + Ollama |
| Gold | Performances des modèles et des prompts | DuckDB | dbt |
| Restitution | Dashboard interactif | — | Streamlit |

_(schéma et lignage dbt à compléter)_

## Setup

1. Cloner le dépôt
2. Créer et activer l'environnement virtuel :
   `python -m venv .venv` puis `.venv\Scripts\Activate.ps1` (Windows)
3. Installer les dépendances : `pip install -r requirements.txt`
4. Copier `.env.example` en `.env`
5. Installer Ollama et télécharger les modèles _(à compléter)_

## Exécution du pipeline

_(à compléter)_

## Résultats

_(à compléter)_

## Organisation

Projet réalisé en solo, le binôme étant absent pour raison de santé.