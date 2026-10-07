-- Question métier : sur quoi porte exactement ce benchmark ?
-- Une seule ligne : taille du corpus, part réellement testée, nombre de modèles et de prompts.
SELECT
    (SELECT COUNT(*) FROM {{ ref('stg_questions') }})                      AS nb_questions_corpus,
    COUNT(DISTINCT question_id)                                            AS nb_questions_testees,
    ROUND(COUNT(DISTINCT question_id) * 100.0
          / (SELECT COUNT(*) FROM {{ ref('stg_questions') }}), 1)          AS couverture_pct,
    COUNT(DISTINCT model)                                                  AS nb_modeles,
    COUNT(DISTINCT prompt_version)                                         AS nb_prompts,
    COUNT(*)                                                               AS nb_reponses
FROM {{ ref('int_answers') }}