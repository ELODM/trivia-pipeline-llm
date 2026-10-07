-- Question métier : le modèle réussit-il mieux les QCM ou les vrai/faux,
-- une fois rapporté au score du hasard (25 % contre 50 %) ?
SELECT
    model,
    prompt_version,
    question_type,
    COUNT(*)                                                           AS nb_questions,
    ROUND(AVG(ai_correct::INT) * 100, 1)                               AS precision_pct,
    ROUND(AVG(chance_score) * 100, 1)                                  AS hasard_pct,
    ROUND((AVG(ai_correct::INT) - AVG(chance_score)) * 100, 1)         AS gain_vs_hasard_pts
FROM {{ ref('int_answers') }}
GROUP BY model, prompt_version, question_type