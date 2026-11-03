"""No conversational content leaves the bot through the web log stream."""
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import flask_socketio
import pytest

from modules.log_privacy import message_metadata, web_log_line
from modules.models import MeshMessage
from tests.test_web_viewer import socketio_viewer, managed_socketio_clients
from tests.test_message_handler import bot, handler

PREFIX = '2026-11-03 09:25:50 - MeshCoreBot - INFO - '


@pytest.mark.parametrize('body',[
    "Received channel message (#private) from Alice: CONFIDENTIAL",
    "Processing message: 'CONFIDENTIAL' from Alice in #private",
    'Sending channel message to #private (channel 2): CONFIDENTIAL',
    "Channel message payload: {'text': 'CONFIDENTIAL'}",
    "LLM prompt: CONFIDENTIAL",
    "LLM raw response (12 chars): 'CONFIDENTIAL'",
    "Unknown new component prints CONFIDENTIAL",
    '2026-11-03 INFO CONFIDENTIAL',
])
def test_legacy_and_unknown_log_bodies_are_not_published(body):
    result = web_log_line(PREFIX+body)
    assert 'CONFIDENTIAL' not in (result or '')
    assert result.startswith(PREFIX)


def test_only_closed_radio_metadata_is_preserved():
    raw = PREFIX+'📡 DIRECT MESSAGE: TRANSPORT_FLOOD | Type: GRP_TXT'
    assert web_log_line(raw)==PREFIX+'📡 CHANNEL MESSAGE: TRANSPORT_FLOOD | Type: GRP_TXT'
    assert web_log_line(raw+' | text: CONFIDENTIAL')==PREFIX+'Événement technique — détail réservé aux logs locaux'
    route = '🛣️  ROUTING INFO: TRANSPORT_FLOOD | Path: aabb,ccdd (2 hops, 4 bytes) | Payload: 81 bytes | Type: GRP_TXT'
    assert 'Path: aabb,ccdd' in web_log_line(PREFIX+route)
    assert web_log_line('    CONFIDENTIAL traceback continuation') is None
    assert web_log_line(PREFIX+'payload\nCONFIDENTIAL continuation') is None


def test_json_logs_and_ansi_are_filtered_too():
    data = dict(timestamp='2026-11-03T09:25:50.123Z',logger='MeshCoreBot',level='DEBUG',message='CONFIDENTIAL',exc_info='CONFIDENTIAL traceback')
    result = web_log_line(json.dumps(data))
    assert result and 'CONFIDENTIAL' not in result
    assert 'CONFIDENTIAL' not in web_log_line('\x1b[32m'+PREFIX+'CONFIDENTIAL\x1b[0m')


def test_metadata_keeps_analysis_dimensions_without_body_or_sender():
    msg = MeshMessage(content='CONFIDENTIAL',sender_id='PRIVATE SENDER',channel='#private',hops=3,snr=-7.5,rssi=-110,
                      routing_info={'route_type':'TRANSPORT_FLOOD'})
    result = message_metadata(msg)
    assert 'CONFIDENTIAL' not in result and 'PRIVATE SENDER' not in result
    for value in ['GRP_TXT','TRANSPORT_FLOOD','Hops: 3','SNR: -7.5 dB','RSSI: -110 dBm','Bytes: 12']:
        assert value in result
    assert web_log_line(PREFIX+result)==PREFIX+result
    assert msg.content=='CONFIDENTIAL'


@pytest.mark.asyncio
async def test_statistics_still_receive_the_original_hidden_message(handler):
    stats = Mock()
    handler.bot.command_manager.commands = {'stats':stats}
    handler.multitest_listener = None
    handler.should_process_message = Mock(return_value=False)
    message = MeshMessage(content='CONFIDENTIAL',channel='#hidden',sender_id='node',is_dm=False)
    await handler.process_message(message)
    stats.record_message.assert_called_once_with(message)
    stats.record_path_stats.assert_called_once_with(message)
    assert stats.record_message.call_args.args[0].content=='CONFIDENTIAL'


@pytest.mark.asyncio
async def test_llm_can_answer_without_logging_prompt_or_answer(caplog):
    from modules.assistant.llm_service import LlmService
    service=object.__new__(LlmService)
    service.logger=logging.getLogger('privacy-test')
    service.wiki_rag=None;service.endpoint='test';service.timeout_seconds=2
    service._user_key=lambda message:None
    service._build_payload=Mock(return_value={'messages':[{'role':'user','content':'CONFIDENTIAL QUERY'}]})
    response=Mock(status_code=200)
    response.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':'CONFIDENTIAL ANSWER'}}]}
    with caplog.at_level(logging.DEBUG),patch('modules.assistant.llm_service.post_chat',return_value=response):
        result=await service._answer('CONFIDENTIAL QUERY',object(),mode='general',max_length=158)
    assert result=='CONFIDENTIAL ANSWER'
    assert 'CONFIDENTIAL' not in caplog.text


@pytest.mark.usefixtures('managed_socketio_clients')
def test_log_history_and_live_stream_never_publish_plaintext(socketio_viewer,tmp_path):
    viewer=socketio_viewer
    logfile=tmp_path/'bot.log'
    safe=PREFIX+'📡 CHANNEL MESSAGE: TRANSPORT_FLOOD | Type: GRP_TXT'
    logfile.write_text(PREFIX+"Processing message: 'CONFIDENTIAL'\n"+'CONFIDENTIAL continuation\n'+safe+'\n')
    if not viewer.config.has_section('Logging'):viewer.config.add_section('Logging')
    viewer.config.set('Logging','log_file',str(logfile))
    subscriber=flask_socketio.SocketIOTestClient(viewer.app,viewer.socketio)
    bystander=flask_socketio.SocketIOTestClient(viewer.app,viewer.socketio)
    subscriber.get_received();bystander.get_received()
    subscriber.emit('subscribe_logs')
    history=[e['args'][0]['line'] for e in subscriber.get_received() if e['name']=='log_line']
    assert safe in history and 'CONFIDENTIAL' not in json.dumps(history)
    viewer._handle_log_line(PREFIX+'LLM response: CONFIDENTIAL')
    live=[e for e in subscriber.get_received() if e['name']=='log_line']
    assert live and 'CONFIDENTIAL' not in json.dumps(live)
    assert not any(e['name']=='log_line' for e in bystander.get_received())


@pytest.mark.parametrize('body',[
    'Catalog tokens prompt=1500 cached=1400 completion=20',
    'Catalog selected route=weather operation=forecast elapsed=16.3s',
    'Assistant route=mesh operation=count_repeaters reason=catalog',
    'LLM request prepared (28 characters)',
    'Sending channel message | Channel index: 2 | Bytes: 80',
])
def test_argument_free_diagnostics_are_preserved(body):
    assert web_log_line(PREFIX+body)==PREFIX+body


@pytest.mark.parametrize('body',[
    'Connected to: CONFIDENTIAL configuration',
    'DB migration 0033 applied: internal description',
    'Processing message: CONFIDENTIAL',
    '📡 DIRECT MESSAGE: TRANSPORT_FLOOD | Type: GRP_TXT',
])
def test_projection_is_stable_when_read_again(body):
    projected=web_log_line(PREFIX+body)
    assert web_log_line(projected)==projected
    assert 'CONFIDENTIAL' not in projected
