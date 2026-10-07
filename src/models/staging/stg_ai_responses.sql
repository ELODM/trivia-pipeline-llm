-- Staging : les réponses de l'IA (une ligne par question x modèle x prompt)
SELECT
    question_id,
    model,
    prompt_version,
    raw_response,
    ai_answer,
    ai_correct,
    parsable,
    chosen_position,
    response_time
FROM {{ source('silver', 'ai_responses') }}