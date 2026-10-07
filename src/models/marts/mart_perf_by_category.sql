-- Question métier : sur quels thèmes le modèle est-il fort ou faible ?
SELECT
    model,
    prompt_version,
    category_main,
    category,
    COUNT(*)                                                           AS nb_questions,
    ROUND(AVG(ai_correct::INT) * 100, 1)                               AS precision_pct,
    ROUND(AVG(chance_score) * 100, 1)                                  AS hasard_pct,
    ROUND(1.96 * SQRT(AVG(ai_correct::INT) * (1 - AVG(ai_correct::INT)) / COUNT(*)) * 100, 1) AS marge_erreur_pts,
    ROUND(AVG(response_time), 2)                                       AS temps_moyen_s
FROM {{ ref('int_answers') }}
GROUP BY model, prompt_version, category_main, category
ORDER BY precision_pct DESC