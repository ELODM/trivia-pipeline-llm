-- Staging : les questions nettoyées (une ligne par question)
SELECT
    question_id,
    category,
    category_main,
    type AS question_type,
    difficulty,
    question,
    correct_answer,
    n_choices
FROM {{ source('silver', 'questions') }}