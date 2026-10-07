-- Question métier : quel est le niveau global du modèle, et fait-il mieux que le hasard ?
SELECT
    model,
    prompt_version,
    COUNT(*)                                                           AS nb_questions,
    SUM(ai_correct::INT)                                               AS nb_correct,
    ROUND(AVG(ai_correct::INT) * 100, 1)                               AS precision_pct,
    ROUND(AVG(chance_score) * 100, 1)                                  AS hasard_pct,
    ROUND((AVG(ai_correct::INT) - AVG(chance_score)) * 100, 1)         AS gain_vs_hasard_pts,
    ROUND(1.96 * SQRT(AVG(ai_correct::INT) * (1 - AVG(ai_correct::INT)) / COUNT(*)) * 100, 1) AS marge_erreur_pts,
    ROUND(AVG(parsable::INT) * 100, 1)                                 AS reponses_lisibles_pct,
    ROUND(AVG(response_time), 2)                                       AS temps_moyen_s,
    ROUND(MEDIAN(response_time), 2)                                    AS temps_median_s
FROM {{ ref('int_answers') }}
GROUP BY model, prompt_version