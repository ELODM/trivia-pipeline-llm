-- Question métier : dans chaque thème, comment la précision évolue-t-elle avec la difficulté ?
-- (sert à la carte de chaleur thème x difficulté)
SELECT
    model,
    prompt_version,
    category,
    difficulty,
    CASE difficulty WHEN 'easy' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END AS ordre_difficulte,
    COUNT(*)                                AS nb_questions,
    ROUND(AVG(ai_correct::INT) * 100, 1)    AS precision_pct
FROM {{ ref('int_answers') }}
GROUP BY model, prompt_version, category, difficulty