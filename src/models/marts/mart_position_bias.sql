-- Question métier : le modèle choisit-il certaines lettres par réflexe ?
-- Sur les QCM, les bonnes réponses sont réparties à environ 25 % sur A, B, C et D.
-- Si le modèle choisit une lettre bien plus souvent que ça, il a un biais de position.
WITH qcm AS (
    SELECT *
    FROM {{ ref('int_answers') }}
    WHERE question_type = 'multiple'
      AND chosen_position IS NOT NULL
),
choisies AS (
    SELECT model, prompt_version, chosen_position AS position, COUNT(*) AS nb
    FROM qcm
    GROUP BY model, prompt_version, chosen_position
),
correctes AS (
    SELECT model, prompt_version, correct_position AS position, COUNT(*) AS nb
    FROM qcm
    GROUP BY model, prompt_version, correct_position
),
totaux AS (
    SELECT model, prompt_version, COUNT(*) AS nb_total
    FROM qcm
    GROUP BY model, prompt_version
)
SELECT
    t.model,
    t.prompt_version,
    chr(65 + p.position::INT)                                   AS lettre,
    ROUND(COALESCE(ch.nb, 0) * 100.0 / t.nb_total, 1)           AS pct_choisie_par_modele,
    ROUND(COALESCE(co.nb, 0) * 100.0 / t.nb_total, 1)           AS pct_bonne_reponse
FROM totaux t
CROSS JOIN range(4) AS p(position)
LEFT JOIN choisies ch
    ON ch.model = t.model AND ch.prompt_version = t.prompt_version AND ch.position = p.position
LEFT JOIN correctes co
    ON co.model = t.model AND co.prompt_version = t.prompt_version AND co.position = p.position
ORDER BY t.model, t.prompt_version, lettre