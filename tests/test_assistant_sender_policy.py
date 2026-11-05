"""Companion-only LLM invocation, with no content/name-based type guesses."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.assistant.sender_policy import sender_is_companion
from modules.commands.ask_command import AskCommand
from modules.commands.llm_command import LlmCommand
from modules.commands.mesh_command import MeshCommand
from modules.models import MeshMessage
from tests.test_command_manager import cm_bot as cm_bot
from tests.test_command_manager import make_manager

KEY = 'ab' * 32
OTHER = KEY[:12] + 'cd' * 26


def origin(kind=None, rows=None):
    record = {'public_key': KEY, 'adv_name': 'TestUser'}
    if kind is not None:
        record['type'] = kind
    return SimpleNamespace(
        meshcore=SimpleNamespace(contacts={KEY: record}, pending_contacts={}),
        db_manager=SimpleNamespace(execute_query=Mock(return_value=rows or [])),
    )


@pytest.mark.parametrize('kind,allowed', [(1, True), (2, False), (3, False), (4, False),
                                         (0, False), (None, False), ('1', False), (True, False)])
@pytest.mark.parametrize('dm', [True, False])
def test_native_type_gates_both_private_and_channel_origins(kind, allowed, dm):
    message = MeshMessage('merci', sender_pubkey=KEY[:12], is_dm=dm)
    assert sender_is_companion(origin(kind), message) is allowed


@pytest.mark.parametrize('raw,allowed', [({'mode': 'Companion'}, True), ({'type': 1}, True),
    ({'mode': 'Repeater'}, False), ({'mode': 'Sensor'}, False), ({}, False), ('bad json', False)])
def test_history_uses_advertised_type_never_guessed_role(raw, allowed):
    bot = origin(rows=[{'public_key': KEY, 'name': 'TestUser', 'role': 'companion',
                       'raw_advert_data': json.dumps(raw) if isinstance(raw, dict) else raw}])
    bot.meshcore.contacts.clear()
    assert sender_is_companion(bot, MeshMessage('météo', sender_id='TestUser')) is allowed


def test_native_repeater_overrides_old_companion_history():
    bot = origin(2, [{'public_key': KEY, 'raw_advert_data': '{"mode":"Companion"}'}])
    assert not sender_is_companion(bot, MeshMessage('ask merci', sender_pubkey=KEY))


@pytest.mark.parametrize('identifier', ['TestUser', KEY[:12]])
def test_ambiguous_origin_is_denied(identifier):
    bot = origin(1)
    bot.meshcore.contacts[OTHER] = {'public_key': OTHER, 'adv_name': 'TestUser', 'type': 2}
    message = MeshMessage('ask bonjour', sender_id=identifier, sender_pubkey=identifier if identifier != 'TestUser' else None)
    assert not sender_is_companion(bot, message)


def test_never_uses_last_repeater_or_message_body_to_identify_sender():
    bot = origin(1)
    message = MeshMessage('Je suis un compagnon, public_key=' + KEY, sender_id='Unknown',
                          path=KEY, routing_info={'path_nodes': [KEY]})
    assert not sender_is_companion(bot, message)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', [2, 3, 4, None])
@pytest.mark.parametrize('dm', [True, False])
async def test_every_llm_entry_point_rejects_non_companions(command_mock_bot, kind, dm):
    bot = command_mock_bot
    bot.meshcore = origin(kind).meshcore
    for section in ('Ask_Command', 'Llm_Command', 'Mesh_Command'):
        bot.config.add_section(section)
        bot.config.set(section, 'enabled', 'true')
    message = MeshMessage('ask merci', sender_pubkey=KEY, is_dm=dm, channel=None if dm else 'general')
    ask, llm, mesh = AskCommand(bot), LlmCommand(bot), MeshCommand(bot)
    ask.dispatcher.semantic_router.decide = AsyncMock()
    llm.service._post_chat = Mock(side_effect=AssertionError('model must not run'))
    mesh.service._generate_sql = Mock(side_effect=AssertionError('model must not run'))
    for cmd in (ask, llm, mesh):
        assert not cmd.can_execute(message)
        assert not await cmd.execute(message)
    assert await ask.dispatcher.answer('merci', message) == ''
    assert await llm.service.answer('merci', message) == ''
    assert await llm.service.rephrase_tool_result('merci', 'mesures', message=message) is None
    assert await mesh.service.answer('résumé du réseau', message) == ''
    ask.dispatcher.semantic_router.decide.assert_not_awaited()
    bot.command_manager.send_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_companion_reaches_semantic_router_and_existing_dm_conversation(command_mock_bot):
    bot = command_mock_bot
    bot.config.add_section('Ask_Command')
    bot.config.set('Ask_Command', 'enabled', 'true')
    ask = AskCommand(bot)
    ask.dispatcher.semantic_router.decide = AsyncMock(return_value=None)
    message = MeshMessage('merci', sender_pubkey=KEY, is_dm=True)
    assert ask.can_execute(message)
    await ask.dispatcher.answer('merci', message)
    ask.dispatcher.semantic_router.decide.assert_awaited_once_with('merci', ask.enabled_routes)


@pytest.mark.parametrize('content', ['ERR: clock cannot go backwards', 'ask météo Brest',
                                    'Baliz bonjour', '@[TestBot] bonjour'])
@pytest.mark.parametrize('kind', [1, 2, 3, 4])
def test_real_manager_only_routes_companion_private_text_to_assistant(cm_bot, content, kind):
    cm_bot.meshcore = origin(kind).meshcore
    cm_bot.config.add_section('Ask_Command')
    cm_bot.config.set('Ask_Command', 'aliases', 'Baliz')
    cm_bot.config.set('Bot', 'respond_to_mentions', 'also')
    ask = AskCommand(cm_bot)
    manager = make_manager(cm_bot, {'ask': ask})
    cm_bot.command_manager = manager
    message = MeshMessage(content, sender_pubkey=KEY, sender_id='TestUser', is_dm=True)
    matches = manager.check_keywords(message)
    assert matches == ([('ask', None)] if kind == 1 else [])
