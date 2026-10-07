-- Intermediate : chaque réponse de l'IA enrichie avec les infos de sa question.
-- C'est la table de base de tous les marts.
SELECT
    r.question_id,
    r.model,
    r.prompt_version,
    q.category,
    q.category_main,
    q.question_type,
    q.difficulty,
    q.question,
    q.correct_answer,
    r.raw_response,
    r.ai_answer,
    r.ai_correct,
    r.parsable,
    r.chosen_position,
    r.response_time,
    q.n_choices,
    -- Score qu'on obtiendrait en répondant au hasard : 25 % (QCM) ou 50 % (vrai/faux)
    1.0 / q.n_choices AS chance_score
FROM {{ ref('stg_ai_responses') }} r
JOIN {{ ref('stg_questions') }} q
    ON r.question_id = q.question_id