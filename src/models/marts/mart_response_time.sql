-- Question métier : le modèle met-il plus de temps quand il se trompe ?
-- La médiane est plus fiable que la moyenne : quelques réponses très lentes tirent la moyenne vers le haut.
SELECT
    model,
    prompt_version,
    ai_correct,
    COUNT(*)                                        AS nb_questions,
    ROUND(AVG(response_time), 2)                    AS temps_moyen_s,
    ROUND(MEDIAN(response_time), 2)                 AS temps_median_s,
    ROUND(QUANTILE_CONT(response_time, 0.9), 2)     AS temps_p90_s,
    ROUND(MAX(response_time), 2)                    AS temps_max_s
FROM {{ ref('int_answers') }}
GROUP BY model, prompt_version, ai_correct