"""Real command adapters and SQLite; mock only external model and RF transport."""
import asyncio
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from modules.assistant.mesh_service import MeshService
from modules.commands.ask_command import AskCommand
from modules.commands.llm_command import LlmCommand
from modules.commands.mesh_command import MeshCommand
from modules.commands.path_command import PathCommand
from modules.commands.test_command import TestCommand as RFTestCommand
from tests.conftest import mock_message


def setup_bot(bot, **ask_options):
    for section in ("Ask_Command", "Mesh_Command", "Llm_Command"):
        bot.config.add_section(section)
        bot.config.set(section, "enabled", "true")
    bot.config.set("Ask_Command", "aliases", "baliz")
    bot.config.set("Llm_Command", "cpu_temp_threshold", "0")
    for key, value in ask_options.items():
        bot.config.set("Ask_Command", key, str(value))
    commands = {c.name: c(bot) for c in (AskCommand, MeshCommand, LlmCommand, RFTestCommand, PathCommand)}
    bot.command_manager.commands = commands
    sent = []

    async def capture(message, text, **kw):
        if message.capture_sink is not None:
            message.capture_sink.append(text)
        else:
            sent.append((message, text))
        return True

    bot.command_manager.send_response = AsyncMock(side_effect=capture)

    async def chunks(message, pages, **kw):
        for p in pages:
            await capture(message, p)
        return True

    bot.command_manager.send_response_chunked = AsyncMock(side_effect=chunks)
    return commands, sent


def model_reply(text):
    response = Mock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": text}}]}
    return response


async def test_baliz_alias_routes_network_to_mesh_only(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["mesh"].service.answer = AsyncMock(return_value="4 répéteurs actifs")
    commands["llm"].service.answer = AsyncMock()
    message = mock_message(content="baliz combien de répéteurs actifs ?")
    assert commands["ask"].matches_keyword(message)
    assert not commands["mesh"].matches_keyword(message)
    assert not commands["llm"].matches_keyword(message)
    assert await commands["ask"].execute(message)
    commands["mesh"].service.answer.assert_awaited_once_with("combien de répéteurs actifs ?", message)
    commands["llm"].service.answer.assert_not_called()
    assert len(sent) == 1 and sent[0][1] == "4 répéteurs actifs"


async def test_ambiguous_question_uses_semantic_router(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["mesh"].service.answer = AsyncMock(return_value="Ouessant via Alpha")
    with patch(
        "modules.assistant.semantic_router.post_chat",
        return_value=model_reply("paths"),
    ) as classify:
        await commands["ask"].execute(mock_message(content="baliz qui peut relayer vers Ouessant ?"))
    classify.assert_called_once()
    commands["mesh"].service.answer.assert_awaited_once()
    assert sent[0][1] == "Ouessant via Alpha"


async def test_explicit_route_does_not_call_semantic_router(command_mock_bot):
    commands, _ = setup_bot(command_mock_bot)
    commands["mesh"].service.answer = AsyncMock(return_value="ok")
    with patch("modules.assistant.semantic_router.post_chat") as classify:
        await commands["ask"].execute(mock_message(content="baliz mesh contacts"))
    classify.assert_not_called()


async def test_semantic_router_failure_keeps_safe_wiki_probe(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["llm"].service.answer = AsyncMock(return_value="réponse Wiki")
    with patch(
        "modules.assistant.semantic_router.post_chat",
        return_value=model_reply("not-a-route"),
    ):
        await commands["ask"].execute(mock_message(content="baliz modulation LoRa avancée"))
    assert commands["llm"].service.answer.call_args.kwargs["mode"] == "auto"
    assert sent[0][1] == "réponse Wiki"


async def test_semantic_router_cannot_trigger_rf_tools(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["mesh"].service.answer = AsyncMock(return_value="chemin réseau observé")
    commands["path"].execute = AsyncMock()
    with patch(
        "modules.assistant.semantic_router.post_chat",
        return_value=model_reply("path"),
    ):
        await commands["ask"].execute(mock_message(content="baliz qui peut relayer vers Ouessant ?"))
    commands["path"].execute.assert_not_called()
    commands["mesh"].service.answer.assert_awaited_once()
    assert sent[0][1] == "chemin réseau observé"


async def test_ask_tables_restores_tigro_database_introspection(command_mock_bot, tmp_path):
    commands, sent = setup_bot(command_mock_bot)
    database = tmp_path / "mesh.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE complete_contact_tracking (name TEXT, public_key TEXT)")
        conn.execute("CREATE TABLE repeater_contacts (name TEXT, public_key TEXT)")
        conn.execute("CREATE TABLE path_stats (path_length INTEGER, hops INTEGER)")

    @contextmanager
    def connection():
        conn = sqlite3.connect(database)
        try:
            yield conn
        finally:
            conn.close()

    command_mock_bot.db_manager.connection = connection
    await commands["ask"].execute(mock_message(content="baliz tables"))
    response = "\n".join(text for _, text in sent)
    assert "complete_contact_tracking:" in response
    assert "repeater_contacts:" in response
    assert "path_stats:" in response


async def test_direct_commands_and_services_are_independently_enabled(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    command = commands["llm"]
    command.public_enabled = False
    command.service.answer = AsyncMock(return_value="Bonjour")
    assert not command.can_execute(mock_message(content="llm bonjour"))
    assert await commands["ask"].execute(mock_message(content="baliz llm bonjour"))
    assert sent[0][1] == "Bonjour"
    assert command.service.answer.call_args.kwargs["mode"] == "general"


async def test_mesh_channel_permission_is_not_bypassed(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["mesh"].allowed_channels = ["private"]
    commands["mesh"].service.answer = AsyncMock()
    await commands["ask"].execute(mock_message(content="baliz mesh combien de contacts"))
    commands["mesh"].service.answer.assert_not_called()
    assert "non autorisée" in sent[0][1]


async def test_disabled_route_cannot_fall_back(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot, enabled_routes="wiki,llm")
    commands["mesh"].service.answer = AsyncMock()
    commands["llm"].service.answer = AsyncMock()
    await commands["ask"].execute(mock_message(content="baliz mesh contacts"))
    assert "désactivée" in sent[0][1]
    commands["mesh"].service.answer.assert_not_called()
    commands["llm"].service.answer.assert_not_called()


async def test_wiki_isolated_and_no_history_or_sql(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    service = commands["llm"].service
    service._store_context("TestUser", "secret history", "secret")
    service.wiki_rag = Mock()
    source = SimpleNamespace(section=SimpleNamespace(content="region EU_868", path="configuration/radio"))
    service.wiki_rag.retrieve.return_value = SimpleNamespace(context="WIKI_REFERENCE_DATA_BEGIN region EU_868",
                                                           best_score=10, matches=[source])
    commands["mesh"].service.answer = AsyncMock()
    with patch("modules.assistant.llm_client.requests.post", return_value=model_reply("region EU_868")) as post:
        await commands["ask"].execute(mock_message(content="baliz comment configurer la région ?"))
    payload = post.call_args.kwargs["json"]
    assert payload["temperature"] == 0
    assert "secret" not in str(payload)
    assert "Local Context" not in str(payload)
    commands["mesh"].service.answer.assert_not_called()
    assert len(sent) == 1


async def test_documentation_no_match_never_fabricates_answer(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["llm"].service.wiki_rag = Mock()
    commands["llm"].service.wiki_rag.retrieve.return_value = None
    with patch("modules.assistant.llm_client.requests.post") as post:
        await commands["ask"].execute(mock_message(content="baliz comment configurer région ?"))
    post.assert_not_called()
    assert "Aucune source" in sent[0][1]


async def test_general_fallback_with_no_wiki(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["llm"].service._inject_current_time_into_prompt = Mock(return_value="general")
    with patch("modules.assistant.llm_client.requests.post", return_value=model_reply("Bonjour")):
        await commands["ask"].execute(mock_message(content="baliz bonjour"))
    assert sent[0][1] == "Bonjour"


async def test_general_disabled_prevents_wiki_probe_fallback(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot, enabled_routes="wiki")
    with patch("modules.assistant.llm_client.requests.post") as post:
        await commands["ask"].execute(mock_message(content="baliz LoRa spreading factor"))
    post.assert_not_called()
    assert "Aucune source" in sent[0][1]


@pytest.mark.parametrize("route", ["test", "path"])
async def test_rf_adapter_preserves_message_metadata_and_captures_output(command_mock_bot, route):
    commands, sent = setup_bot(command_mock_bot)
    original = mock_message(content=f"baliz {route}", sender_pubkey="ab"*32, path="abcd", hops=2,
                            snr=7.5, rssi=-92, routing_info={"bytes_per_hop": 2, "path_hex": "abcd"}, reply_scope="#bzh")
    async def execute(message):
        assert message is not original
        assert (message.sender_pubkey, message.path, message.snr, message.rssi, message.reply_scope) == (
            original.sender_pubkey, original.path, original.snr, original.rssi, original.reply_scope)
        assert message.routing_info == original.routing_info
        assert message.content == route
        await commands[route].send_response(message, "mesure réelle")
        return True
    commands[route].execute = AsyncMock(side_effect=execute)
    await commands["ask"].execute(original)
    assert len(sent) == 1 and sent[0][0] is original
    assert original.capture_sink is None and original.content == f"baliz {route}"


async def test_actual_test_command_uses_received_snr(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    command_mock_bot.config.remove_option("Keywords", "test")
    commands["test"].enforce_path_byte_requirement = AsyncMock(return_value=True)
    await commands["ask"].execute(mock_message(content="baliz comment tu me reçois ?", snr=7.5, rssi=-92, hops=0))
    assert len(sent) >= 1
    assert "7.5" in " ".join(t for _, t in sent)


async def test_timeout_has_one_response_and_no_fallback(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["ask"].route_timeout_seconds = 0.01
    async def slow(*a, **kw):
        await asyncio.sleep(10)
    commands["mesh"].service.answer = slow
    await commands["ask"].execute(mock_message(content="baliz mesh contacts"))
    assert len(sent) == 1 and "délai" in sent[0][1]


async def test_mesh_real_sqlite_query_and_readonly_rejection(command_mock_bot, tmp_path):
    commands, sent = setup_bot(command_mock_bot)
    database = tmp_path / "mesh.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE complete_contact_tracking (name TEXT, public_key TEXT, latitude REAL, longitude REAL, role TEXT)")
        conn.executemany("INSERT INTO complete_contact_tracking VALUES (?, ?, NULL, NULL, 'repeater')", [("Alpha", "a"*64), ("Bravo", "b"*64)])
    @contextmanager
    def connection():
        conn = sqlite3.connect(database)
        try:
            yield conn
        finally:
            conn.close()
    command_mock_bot.db_manager.connection = connection
    service: MeshService = commands["mesh"].service
    with patch("modules.assistant.llm_client.requests.post", side_effect=[model_reply("SELECT COUNT(*) FROM complete_contact_tracking"), model_reply("2 répéteurs")]) as post:
        await commands["ask"].execute(mock_message(content="baliz combien de répéteurs ?"))
    assert len(sent) == 1 and sent[0][1] == "2 répéteurs"
    assert post.call_count == 2
    assert service._execute_sql("DELETE FROM complete_contact_tracking").startswith("(rejected:")
    assert service._execute_sql("SELECT COUNT(*) FROM complete_contact_tracking") == "2"
    assert service._execute_sql("SELECT name FROM complete_contact_tracking ORDER BY name DESC LIMIT 1") == "Bravo"


async def test_mesh_uses_live_schema_and_repairs_invalid_generated_column(command_mock_bot, tmp_path):
    commands, sent = setup_bot(command_mock_bot)
    database = tmp_path / "mesh.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE complete_contact_tracking "
            "(name TEXT, public_key TEXT, role TEXT, last_heard TEXT, snr REAL)"
        )
        conn.execute(
            "INSERT INTO complete_contact_tracking VALUES "
            "('Alpha', ?, 'repeater', '2026-09-20 10:00:00', 9.5)",
            ("a" * 64,),
        )

    @contextmanager
    def connection():
        conn = sqlite3.connect(database)
        try:
            yield conn
        finally:
            conn.close()

    command_mock_bot.db_manager.connection = connection
    replies = [
        model_reply(
            "SELECT c.name, c.snr FROM complete_contact_tracking c "
            "WHERE c.role = 'repeater' ORDER BY c.last_seen DESC LIMIT 1"
        ),
        model_reply(
            "SELECT c.name, c.snr FROM complete_contact_tracking c "
            "WHERE c.role = 'repeater' ORDER BY c.last_heard DESC LIMIT 1"
        ),
        model_reply("Alpha: 9.5 dB"),
    ]
    with patch("modules.assistant.llm_client.requests.post", side_effect=replies) as post:
        await commands["ask"].execute(mock_message(content="baliz quel est le meilleur répéteur ?"))

    assert sent[0][1] == "Alpha: 9.5 dB"
    assert post.call_count == 3
    first_schema = post.call_args_list[0].kwargs["json"]["messages"][0]["content"]
    assert "Live SQLite schema (authoritative)" in first_schema
    assert "complete_contact_tracking [rows=1]" in first_schema
    assert "last_heard TEXT" in first_schema
    assert "last_seen" not in first_schema
    repair_prompt = post.call_args_list[1].kwargs["json"]["messages"][0]["content"]
    assert "no such column: c.last_seen" in repair_prompt
    assert "FAILED_SQL_BEGIN" in repair_prompt


def test_settings_have_no_trigger_collision(command_mock_bot):
    commands, _ = setup_bot(command_mock_bot)
    for trigger, expected in [("ask", "ask"), ("baliz", "ask"), ("mesh", "mesh"), ("query", "mesh"), ("sql", "mesh"), ("llm", "llm")]:
        assert [c.name for c in commands.values() if trigger in c.keywords] == [expected]


def test_mesh_restricts_tables_and_restores_connection(command_mock_bot):
    commands, _ = setup_bot(command_mock_bot)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE private_tokens (secret TEXT)")
    conn.execute("INSERT INTO private_tokens VALUES ('not-network-data')")
    @contextmanager
    def connection():
        yield conn
    command_mock_bot.db_manager.connection = connection
    result = commands["mesh"].service._execute_sql("SELECT secret FROM private_tokens")
    assert result.startswith("(query error:")
    assert "not-network-data" not in result
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 0
    conn.execute("INSERT INTO private_tokens VALUES ('still-writable')")
    conn.close()


def test_mesh_rejects_literal_only_queries_and_ungrounded_formatted_values(command_mock_bot):
    commands, _ = setup_bot(command_mock_bot)
    service = commands["mesh"].service
    result, error = service._execute_sql_detailed("SELECT 'Invented repeater', 1200")
    assert result.startswith("(query error:")
    assert error == "query did not read an allowed network table"
    assert service._is_repairable_sql_error(error)
    assert service._formatted_is_grounded("Alpha: 9.5 dB", "Alpha, 9.5")
    assert not service._formatted_is_grounded("Alpha: 9.5 dB, 20 km", "Alpha, 9.5")
    assert service._question_shape_error("quel est le meilleur répéteur ?", "SELECT name FROM complete_contact_tracking LIMIT 20")
    assert service._question_shape_error("quel est le meilleur répéteur ?", "SELECT name FROM complete_contact_tracking ORDER BY advert_count DESC LIMIT 1")
    assert service._question_shape_error("quel est le meilleur répéteur ?", "SELECT name FROM complete_contact_tracking WHERE role = 'repeater' ORDER BY advert_count DESC LIMIT 1") is None
    constrained = service._apply_requested_role_constraint(
        "quel est le meilleur répéteur ?",
        "SELECT name FROM complete_contact_tracking ORDER BY advert_count DESC LIMIT 1",
    )
    assert "WHERE role = 'repeater' ORDER BY advert_count DESC LIMIT 1" in constrained


async def test_mesh_retries_empty_table_using_live_row_counts(command_mock_bot, tmp_path):
    commands, sent = setup_bot(command_mock_bot)
    database = tmp_path / "mesh.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE repeater_contacts (name TEXT, is_active INTEGER, purge_count INTEGER)")
        conn.execute("CREATE TABLE complete_contact_tracking (name TEXT, role TEXT, advert_count INTEGER)")
        conn.execute("INSERT INTO complete_contact_tracking VALUES ('Alpha', 'repeater', 12)")

    @contextmanager
    def connection():
        conn = sqlite3.connect(database)
        try:
            yield conn
        finally:
            conn.close()

    command_mock_bot.db_manager.connection = connection
    replies = [
        model_reply("SELECT name, purge_count FROM repeater_contacts WHERE is_active = 1 ORDER BY purge_count DESC LIMIT 1"),
        model_reply("SELECT name, advert_count FROM complete_contact_tracking WHERE role = 'repeater' ORDER BY advert_count DESC LIMIT 1"),
        model_reply("Alpha: 12 annonces"),
    ]
    with patch("modules.assistant.llm_client.requests.post", side_effect=replies) as post:
        await commands["ask"].execute(mock_message(content="baliz quel est le meilleur répéteur ?"))

    assert sent[0][1] == "Alpha: 12 annonces"
    assert post.call_count == 3
    first_prompt = post.call_args_list[0].kwargs["json"]["messages"][0]["content"]
    assert "repeater_contacts [rows=0]" in first_prompt
    assert "complete_contact_tracking [rows=1]" in first_prompt
    repair_prompt = post.call_args_list[1].kwargs["json"]["messages"][0]["content"]
    assert "query returned no rows" in repair_prompt
    assert "repeater_contacts [rows=0]" not in repair_prompt
    assert "complete_contact_tracking [rows=1]" in repair_prompt
    assert "Use a different table" in repair_prompt


async def test_mesh_disabled_preserves_other_capabilities(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["mesh"].enabled = False
    commands["mesh"].service.answer = AsyncMock()
    await commands["ask"].execute(mock_message(content="baliz mesh contacts"))
    assert "indisponible" in sent[0][1]
    commands["mesh"].service.answer.assert_not_called()


async def test_disabled_wiki_still_allows_general_route(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot, enabled_routes="llm")
    commands["llm"].service.answer = AsyncMock(return_value="Bonjour")
    await commands["ask"].execute(mock_message(content="baliz bonjour"))
    assert commands["llm"].service.answer.call_args.kwargs["mode"] == "general"
    assert sent[0][1] == "Bonjour"


async def test_target_cooldown_applies_across_direct_and_routed_access(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands["mesh"].record_execution("TestUser")
    commands["mesh"].service.answer = AsyncMock()
    await commands["ask"].execute(mock_message(content="baliz mesh contacts"))
    commands["mesh"].service.answer.assert_not_called()
    assert "non autorisée" in sent[0][1]


@pytest.mark.parametrize("question", ["bonjour, qui es-tu ?", "raconte une blague courte"])
async def test_conversation_ignores_spurious_wiki_match(command_mock_bot, question):
    commands, sent = setup_bot(command_mock_bot)
    service = commands["llm"].service
    service.wiki_rag = Mock()
    service.wiki_rag.retrieve.side_effect = AssertionError("Conversation must not query Wiki")
    service._inject_current_time_into_prompt = Mock(return_value="Tu es Baliz, réponds en français.")
    with patch("modules.assistant.llm_client.requests.post", return_value=model_reply("Je suis Baliz.")) as post:
        await commands["ask"].execute(mock_message(content="baliz " + question))
    service.wiki_rag.retrieve.assert_not_called()
    service.wiki_rag.ensure_fresh.assert_not_called()
    assert "WIKI_REFERENCE_DATA_BEGIN" not in str(post.call_args.kwargs["json"])
    assert sent[0][1] == "Je suis Baliz."


async def test_disabled_conversation_does_not_escape_through_wiki(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot, enabled_routes="wiki")
    commands["llm"].service.answer = AsyncMock()
    await commands["ask"].execute(mock_message(content="baliz raconte une blague courte"))
    commands["llm"].service.answer.assert_not_called()
    assert "llm est désactivée" in sent[0][1]
