-- Question métier : le modèle réussit-il mieux les questions courtes ou longues ?
SELECT
    model,
    prompt_version,
    CASE
        WHEN question_words <= 8  THEN '1. Courte (8 mots ou moins)'
        WHEN question_words <= 15 THEN '2. Moyenne (9 à 15 mots)'
        ELSE                           '3. Longue (plus de 15 mots)'
    END                                                                AS longueur,
    COUNT(*)                                                           AS nb_questions,
    ROUND(AVG(ai_correct::INT) * 100, 1)                               AS precision_pct,
    ROUND(AVG(chance_score) * 100, 1)                                  AS hasard_pct,
    ROUND(1.96 * SQRT(AVG(ai_correct::INT) * (1 - AVG(ai_correct::INT)) / COUNT(*)) * 100, 1) AS marge_erreur_pts,
    ROUND(MEDIAN(response_time), 2)                                    AS temps_median_s
FROM {{ ref('int_answers') }}
GROUP BY model, prompt_version, longueur
ORDER BY longueur