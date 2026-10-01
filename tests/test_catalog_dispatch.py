import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from modules.assistant.catalog import parse_plan, CATALOG
from modules.assistant.dispatcher import AssistantDispatcher

@pytest.mark.parametrize('q,route,op,args',[
 ('Baliz statistique des canaux','mesh','stats',{'topic':'channels','hashes':False}),
 ('tu en as mis du temps pour répondre','llm','chat',{}),
 ('quels sont les relais les plus sollicités','mesh','relay_connectivity',{'hours':24,'limit':3}),
 ('comment configurer un répéteur','wiki','lookup',{}),
 ('quelle est la route','path','message',{}),
 ('les relais à proximité','mesh','near',{'limit':5,'role':'repeater','target':''}),
])
@pytest.mark.asyncio
async def test_selected_plan_is_executed_without_lexical_override(q,route,op,args):
    plan=parse_plan(json.dumps(dict(route=route,operation=op,args=args)),q,set(CATALOG))
    d=object.__new__(AssistantDispatcher)
    d.owner=SimpleNamespace(enabled_routes=set(CATALOG),logger=logging.getLogger(),route_timeout_seconds=2)
    d.semantic_router=SimpleNamespace(decide=AsyncMock(return_value=plan))
    d._dispatch=AsyncMock(return_value='ok')
    assert await d.answer(q,SimpleNamespace())=='ok'
    assert d._dispatch.call_args.args[0]==plan

@pytest.mark.asyncio
async def test_classifier_failure_never_dispatches():
    d=object.__new__(AssistantDispatcher)
    d.owner=SimpleNamespace(enabled_routes=set(CATALOG))
    d.semantic_router=SimpleNamespace(decide=AsyncMock(return_value=None))
    d._dispatch=AsyncMock()
    assert 'pas pu interpréter' in await d.answer('tu as mis du temps',SimpleNamespace())
    d._dispatch.assert_not_called()

@pytest.mark.parametrize('args',[
 {}, {'topic':'channels','hashes':'false'}, {'topic':'other','hashes':False},
 {'topic':'channels','hashes':False,'sql':'SELECT 1'},
])
def test_reject_bad_typed_arguments(args):
    with pytest.raises(ValueError):
        parse_plan(json.dumps(dict(route='mesh',operation='stats',args=args)),'statistique des canaux',set(CATALOG))

@pytest.mark.parametrize('limit',[0,21,True])
def test_reject_invalid_limits(limit):
    with pytest.raises(ValueError):
        parse_plan(json.dumps(dict(route='mesh',operation='near',args=dict(limit=limit,role='repeater',target=''))),'proximité',set(CATALOG))


def test_catalog_examples_obey_the_contract():
    from modules.assistant.catalog import ROUTING_EXAMPLES
    for question, plan in ROUTING_EXAMPLES:
        parse_plan(json.dumps(plan), question, set(CATALOG))


def test_trace_cannot_invent_radio_path():
    with pytest.raises(ValueError):
        parse_plan(json.dumps(dict(route='mesh',operation='trace',args=dict(path='aa,bb',target='',roundtrip=True))),'teste le chemin vers Alpha',set(CATALOG))


def test_semantic_transport_uses_closed_schema_and_separate_timeout():
    from unittest.mock import patch
    from modules.assistant.semantic_router import SemanticRouter
    def read(section,key,fallback=None,value_type=None):
        return 23 if (section,key)==('Ask_Command','semantic_timeout_seconds') else fallback
    router=SemanticRouter(SimpleNamespace(logger=logging.getLogger(),get_config_value=read))
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':{'content':'{"function":"llm.chat","args":{}}'}}]})
    with patch('modules.assistant.semantic_router.post_chat',return_value=response) as post:
        assert router._classify('bonjour',{'llm'}).route.value=='llm'
        payload=post.call_args.args[1]
        assert 22 < post.call_args.args[2] <= 23
        variants=payload['response_format']['json_schema']['schema']['anyOf']
        assert {v['properties']['function']['const'] for v in variants}=={'llm.chat'}


@pytest.mark.parametrize('content', [
    '{"function":"mesh.count_nodes","args":{"hours":24,"country":""}}',
    '{"function":"llm.chat","args":{},"sql":"SELECT 1"}',
    '{"function":"llm.unknown","args":{}}',
])
def test_semantic_transport_rejects_disabled_or_unknown_calls(content):
    from unittest.mock import patch
    from modules.assistant.semantic_router import SemanticRouter
    router=SemanticRouter(SimpleNamespace(logger=logging.getLogger(),get_config_value=lambda *a, fallback=None, **k:fallback))
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':{'content':content}}]})
    with patch('modules.assistant.semantic_router.post_chat',return_value=response):
        assert router._classify('bonjour',{'llm'}) is None


def test_invalid_model_plan_gets_one_repair_within_same_budget():
    from unittest.mock import patch
    from modules.assistant.semantic_router import SemanticRouter
    router=SemanticRouter(SimpleNamespace(logger=logging.getLogger(),get_config_value=lambda *a, fallback=None, **k:fallback))
    def reply(content):
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':{'content':content}}]})
    with patch('modules.assistant.semantic_router.post_chat',side_effect=[reply('{"function":"bad","args":{}}'),reply('{"function":"llm.chat","args":{}}')]) as post:
        assert router._classify('bonjour',{'llm'}).operation=='chat'
        assert post.call_count==2
        assert post.call_args_list[1].args[2] <= post.call_args_list[0].args[2]


def test_http_timeout_is_not_retried():
    import requests
    from unittest.mock import patch
    from modules.assistant.semantic_router import SemanticRouter
    router=SemanticRouter(SimpleNamespace(logger=logging.getLogger(),get_config_value=lambda *a, fallback=None, **k:fallback))
    with patch('modules.assistant.semantic_router.post_chat',side_effect=requests.Timeout('busy')) as post:
        assert router._classify('bonjour',{'llm'}) is None
        assert post.call_count==1
