import sqlite3
import json
import time
import pytest
from modules.assistant.relay_usage import relay_usage
from modules.assistant.conversation import complete_reply


def test_distinct_packets_collisions_and_multibyte():
    c=sqlite3.connect(':memory:')
    c.execute('CREATE TABLE complete_contact_tracking(public_key,name,role)')
    c.executemany('INSERT INTO complete_contact_tracking VALUES (?,?,?)', [('aabb11','Alpha','repeater'),('aacc22','Collision','repeater'),('bb0011','Bravo','repeater')])
    c.execute('CREATE TABLE packet_stream(timestamp,data,type)')
    for packet,path,width,age in [('p1','aabbaabb',2,'now'),('p1','aabb',2,'now'),('p2','aabb',2,'now'),('p3','aa',1,'now'),('p4','bb00',2,'now'),('old','aabb',2,'-3 days'),('bad','aa',None,'now')]:
        c.execute('INSERT INTO packet_stream VALUES (?,?,?)',(time.time()-(3*86400 if age!='now' else 10),json.dumps(dict(packet_hash=packet,path_hex=path,bytes_per_hop=width,route_type_name='FLOOD')),'packet'))
    # Same path but a new packet must count; the old aggregate table lost this.
    c.execute('INSERT INTO packet_stream VALUES (?,?,?)',(time.time()-10,json.dumps(dict(packet_hash='p5',path_hex='aabb',bytes_per_hop=2,route_type_name='FLOOD')),'packet'))
    c.execute('INSERT INTO packet_stream VALUES (?,?,?)',(time.time()-10,json.dumps(dict(packet_hash='direct',path_hex='aabb',bytes_per_hop=2,route_type_name='DIRECT')),'packet'))

    text=relay_usage(c,24,3,220)
    assert 'Alpha : 3' in text and 'Bravo : 1' in text
    assert 'Collision :' not in text and 'exclus' in text
    assert len(relay_usage(c,24,3,158).encode())<=158

@pytest.mark.parametrize('text,budget', [('Oui, désolé pour l’attente. Je reste disponible pour beaucoup de choses.',50),('É'*100+'.',100),('Merci pour ta patience !',100)])
def test_conversation_complete_and_bounded(text,budget):
    result=complete_reply(text,budget)
    assert len(result.encode())<=budget
    assert result.endswith(('.', '!', '?')) and not result.endswith('…')

@pytest.mark.asyncio
async def test_conversation_compresses_before_sending():
    import logging
    from unittest.mock import Mock,patch
    from modules.assistant.llm_service import LlmService
    s=object.__new__(LlmService)
    s.wiki_rag=None;s.logger=logging.getLogger();s.endpoint='test';s.timeout_seconds=2
    s._user_key=lambda m:None
    s._build_payload=Mock(return_value={'messages':[{'role':'system','content':'Identity'},{'role':'user','content':'retard'}]})
    def response(text):
        r=Mock(status_code=200);r.json.return_value={'choices':[{'message':{'content':text}}]};return r
    with patch('modules.assistant.llm_service.post_chat',side_effect=[response('Une très longue réponse. '*30),response('Désolé pour l’attente.')]) as post:
        result=await s._answer('tu as mis du temps',object(),mode='general',max_length=100)
    assert result=='Désolé pour l’attente.' and post.call_count==2


def test_truncated_fragment_is_not_a_sentence():
    assert complete_reply('Merci. Je voulais aussi...',100)=='Merci.'


def test_short_model_reply_does_not_require_punctuation():
    assert complete_reply('Avec plaisir', 158, 'stop') == 'Avec plaisir'
    assert complete_reply('De rien 🙂', 158, 'stop') == 'De rien 🙂'
    assert complete_reply('  D’accord  ', 158) == 'D’accord'


def test_truncated_model_reply_keeps_only_complete_sentences():
    assert complete_reply('Bien reçu. Je voulais aussi', 158, 'length') == 'Bien reçu.'
    assert complete_reply('Avec plaisir…', 158, 'stop') != 'Avec plaisir'
    assert complete_reply('Une réponse interrompue', 158, 'length') != 'Une réponse interrompue'


def test_conversation_budget_counts_utf8_bytes():
    assert complete_reply('é' * 80, 158, 'stop') != 'é' * 80
    assert complete_reply('é' * 79, 158, 'stop') == 'é' * 79

@pytest.mark.asyncio
async def test_direct_conversation_preserves_short_unpunctuated_model_answer():
    import logging
    from unittest.mock import Mock, patch
    from modules.assistant.llm_service import LlmService
    service = object.__new__(LlmService)
    service.wiki_rag = None
    service.logger = logging.getLogger()
    service.endpoint = 'test'
    service.timeout_seconds = 2
    service._user_key = lambda message: None
    service._build_payload = Mock(return_value={'messages': [{'role': 'system', 'content': 'Tu es Baliz.'}, {'role': 'user', 'content': 'merci'}]})
    response = Mock(status_code=200)
    response.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Avec plaisir 🙂'}}]}
    with patch('modules.assistant.llm_service.post_chat', return_value=response) as post:
        result = await service._answer('merci', object(), mode='general', max_length=158)
    assert result == 'Avec plaisir 🙂'
    assert post.call_count == 1


def test_conversation_payload_excludes_global_channel_context():
    import logging
    from unittest.mock import Mock
    from modules.assistant.llm_service import LlmService
    service = object.__new__(LlmService)
    service.logger = logging.getLogger()
    service.system_prompt = 'Tu es Baliz.'
    service.datetime_format = '%Y-%m-%d'
    service.max_tokens = 100
    service.temperature = 0
    service.top_p = 1
    service.model = ''
    service.reasoning_effort = 'none'
    service._build_local_context = Mock(return_value='Un autre canal discute du bug merci.')
    service._get_prompt_node_neighbors = Mock(return_value='Unrelated network observations')
    payload = service._build_payload(prompt='merci', rag_context='', include_local_context=False)
    assert payload['messages'][-1] == {'role':'user', 'content':'merci'}
    assert 'bug' not in str(payload)
    service._build_local_context.assert_not_called()
    service._get_prompt_node_neighbors.assert_not_called()
