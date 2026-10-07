-- Question métier : sur quels thèmes le modèle est-il fort ou faible,
-- et quels thèmes pèsent le plus dans ses erreurs ?
WITH par_theme AS (
    SELECT
        model,
        prompt_version,
        category_main,
        category,
        COUNT(*)                                                           AS nb_questions,
        COUNT(*) - SUM(ai_correct::INT)                                    AS nb_erreurs,
        ROUND(AVG(ai_correct::INT) * 100, 1)                               AS precision_pct,
        ROUND(AVG(chance_score) * 100, 1)                                  AS hasard_pct,
        ROUND(1.96 * SQRT(AVG(ai_correct::INT) * (1 - AVG(ai_correct::INT)) / COUNT(*)) * 100, 1) AS marge_erreur_pts,
        ROUND(AVG(response_time), 2)                                       AS temps_moyen_s
    FROM {{ ref('int_answers') }}
    GROUP BY model, prompt_version, category_main, category
)
SELECT
    *,
    -- Part du thème dans le dataset et dans le total des erreurs du modèle
    ROUND(nb_questions * 100.0 / SUM(nb_questions) OVER (PARTITION BY model, prompt_version), 1) AS part_questions_pct,
    ROUND(nb_erreurs * 100.0 / SUM(nb_erreurs) OVER (PARTITION BY model, prompt_version), 1)     AS part_erreurs_pct
FROM par_theme
ORDER BY precision_pct DESC