"""A request for an explicit reception metric uses the incoming RF frame."""
from unittest.mock import AsyncMock, Mock, patch

import pytest

from modules.assistant.router import requested_reception_metrics
from tests.conftest import mock_message
from tests.test_assistant_integration import setup_bot


@pytest.mark.parametrize(('question', 'metrics'), [
    ('Quel est le SNR de mon message ?', ('snr',)),
    ('Donne-moi le RSSI et le SNR de ce paquet.', ('rssi', 'snr')),
    ('What’s the RSSI of this packet?', ('rssi',)),
    ('Quel est mon SNR ?', ('snr',)),
    ('Quel est le SNR du répéteur de Coray ?', ()),
    ('Comment interpréter le RSSI de mon message ?', ()),
    ('Laisse un message à Kam : quel est le SNR de mon message ?', ()),
])
def test_only_explicit_current_packet_measurements(question, metrics):
    assert requested_reception_metrics(question) == metrics


@pytest.mark.parametrize(('text', 'snr', 'rssi', 'hops', 'expected'), [
    ('Baliz, quel est le SNR de mon message ?', 11.5, -62, 3,
     'À la réception : SNR : 11,5 dB (dernier relais → Baliz).'),
    ('Baliz, le SNR de ce paquet ?', 0, -62, 0, 'À la réception : SNR : 0 dB.'),
    ('Baliz, le RSSI de ce message ?', 11.5, None, 3, 'À la réception : RSSI indisponible.'),
    ('Baliz, SNR et RSSI de mon message ?', None, -62, 3,
     'À la réception : SNR indisponible · RSSI : -62 dBm (dernier relais → Baliz).'),
])
async def test_measurement_reply_without_model_or_rf_test(command_mock_bot, text, snr, rssi, hops, expected):
    command_mock_bot.command_manager.monitor_channels = ['#bretagne']
    commands, sent = setup_bot(command_mock_bot)
    commands['test'].execute = AsyncMock()
    message = mock_message(content=text, snr=snr, rssi=rssi, hops=hops, channel='#bretagne')
    with patch('modules.assistant.semantic_router.post_chat') as model:
        await commands['ask'].execute(message)
    assert [body for _, body in sent] == [expected]
    model.assert_not_called()
    commands['test'].execute.assert_not_awaited()


async def test_reception_measurement_respects_test_access(command_mock_bot):
    commands, sent = setup_bot(command_mock_bot)
    commands['test'].can_use_service = Mock(return_value=False)
    await commands['ask'].execute(mock_message(content='Baliz, quel est le SNR de mon message ?', snr=11.5))
    assert [body for _, body in sent] == ['La mesure radio est indisponible ou non autorisée ici.']
