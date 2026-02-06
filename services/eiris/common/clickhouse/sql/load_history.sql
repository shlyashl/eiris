SELECT
    ts,
    role,
    text,
    chat_id,
    user_id,
    request_id,
    tg_msg_id
FROM
(
    SELECT
        ts,
        role,
        text,
        chat_id,
        user_id,
        request_id,
        tg_msg_id
    FROM {db}.{table}
    WHERE session_id = '{session_id}'
    ORDER BY ts DESC
    LIMIT {limit}
)
ORDER BY ts ASC

