from app.chatbot.chat_history import append_message, create_session, load_recent_messages


def test_create_session_sets_fields(chatbot_db_session):
    session = create_session(chatbot_db_session, user_id="user-1", role="candidate")
    assert session.id is not None
    assert session.user_id == "user-1"
    assert session.role == "candidate"


def test_append_and_load_messages_in_order(chatbot_db_session):
    chat_session = create_session(chatbot_db_session, user_id="user-2", role="admin")
    append_message(chatbot_db_session, chat_session.id, "user", "hello")
    append_message(chatbot_db_session, chat_session.id, "assistant", "hi there", retrieved_chunk_ids=[1, 2])

    messages = load_recent_messages(chatbot_db_session, chat_session.id)

    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "hello"
    assert messages[1].retrieved_chunk_ids == [1, 2]


def test_load_recent_messages_respects_limit(chatbot_db_session):
    chat_session = create_session(chatbot_db_session, user_id="user-3", role="employer")
    for i in range(5):
        append_message(chatbot_db_session, chat_session.id, "user", f"message {i}")

    messages = load_recent_messages(chatbot_db_session, chat_session.id, limit=2)

    assert len(messages) == 2
    assert messages[-1].content == "message 4"
