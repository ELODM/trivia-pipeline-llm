-- Question métier : à quoi ressemblent concrètement les erreurs du modèle ?
-- Jusqu'à 5 exemples d'erreurs par thème, pour illustrer les résultats.
SELECT
    model,
    prompt_version,
    category,
    difficulty,
    question,
    correct_answer,
    ai_answer AS reponse_du_modele
FROM {{ ref('int_answers') }}
WHERE NOT ai_correct
QUALIFY ROW_NUMBER() OVER (PARTITION BY model, prompt_version, category ORDER BY question_id) <= 5