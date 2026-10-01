"""Closed assistant capabilities and strict model-output validation."""
import json
import re
from .router import Decision, Route, normalize
from .network_plan import infer_operation, ACTIVE

CATALOG = {
    'test': {'receive': 'Measure reception of THIS message: tu me reçois, SNR, RSSI.'},
    'path': {'message': 'Show the actual path of THIS message, default sender: quelle est la route, chemin entre toi et moi. Not a definition.'},
    'mesh': {
        'relay_connectivity': 'Rank important, most connected or most solicited relays by observed graph degree, the map dot-size metric. Counts attached directed links, NOT packets or load. Default hours=24, limit=3.',
        'count_nodes': 'Count ALL locally observed nodes, all roles. Active means heard in last 24 hours by default; hours=0 means all known nodes, country empty unless specified.',
        'count_repeaters': 'How many locally tracked repeaters (not a list).',
        'list_repeaters': 'List locally tracked repeaters. activity sorts cumulative advert counts, NOT forwarded traffic or relay utilization.',
        'onebyte': 'Who uses 1 octet / 1 byte routing? Use measured encoding evidence.',
        'near': 'Nearest nodes using sender GPS or first incoming relay as an approximate origin.',
        'stats': 'Tigro statistics over 24 hours. topic=channels ranks channels by message count and unique users; messages ranks senders; paths shows longest observed paths; adverts counts advertisements; general summarizes traffic. Does NOT measure forwarded traffic per relay.',
        'neighbors': 'Discover directly reachable neighbors. Active radio, DM only.',
        'trace': 'Actively test radio path, roundtrip by default; explicit hex path or named target.',
        'advert': 'User explicitly requests the bot to announce itself by radio. DM only.',
        'unsupported': 'Observed-network requests not measurable by available operations, outside the available observations. Never substitute advert count or documentation.'},
    'weather': {'forecast': 'Actual weather forecast for a place and date. Args location (exact place from question or empty), period (today or tomorrow).'},
    'wiki': {'lookup': 'Documentation: explain, configure, commands, regions, repeater/companion setup, how routing works.'},
    'llm': {'chat': 'Greeting, identity, thanks, complaints about the bot or response delay, creative and general conversation. Never search documentation for casual remarks.'},
}


# Few-shot examples teach intent, never execute lexical overrides.
ROUTING_EXAMPLES = [
    ("nombre de nœuds actifs", {"route": "mesh", "operation": "count_nodes", "args": {"hours": 24, "country": ""}}),
    ('météo demain à Lyon', {'route': 'weather', 'operation': 'forecast', 'args': {'location': 'Lyon', 'period': 'tomorrow'}}),
    ('quelle est la route ?', {'route': 'path', 'operation': 'message', 'args': {}}),
    ('tu me reçois ?', {'route': 'test', 'operation': 'receive', 'args': {}}),
    ('comment fonctionne le routage ?', {'route': 'wiki', 'operation': 'lookup', 'args': {}}),
    ('statistique des canaux', {'route': 'mesh', 'operation': 'stats', 'args': {'topic': 'channels', 'hashes': False}}),
    ('quels sont les relais les plus sollicités ?', {'route': 'mesh', 'operation': 'relay_connectivity', 'args': {'hours': 24, 'limit': 3}}),
    ('tu en as mis du temps pour répondre', {'route': 'llm', 'operation': 'chat', 'args': {}}),
]


def validate_arguments(args, schema):
    """Validate the same closed schema that is sent to the classifier."""
    properties = schema['properties']
    if set(args) != set(properties):
        raise ValueError('Invalid argument fields')
    types = {'string': str, 'integer': int, 'boolean': bool}
    for key, spec in properties.items():
        value = args[key]
        if type(value) is not types[spec['type']]:
            raise ValueError('Invalid argument type: ' + key)
        if 'enum' in spec and value not in spec['enum']:
            raise ValueError('Invalid argument value: ' + key)
        if isinstance(value, str) and (len(value) > 200 or any(c in value for c in '\r\n\x00')):
            raise ValueError('Invalid argument text: ' + key)
        if 'minimum' in spec and value < spec['minimum'] or 'maximum' in spec and value > spec['maximum']:
            raise ValueError('Argument out of bounds: ' + key)


def parse_plan(text, question, enabled):
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {'route', 'operation', 'args'}:
        raise ValueError('Invalid plan fields')
    route, operation, args = value['route'], value['operation'], value['args']
    if not isinstance(route, str) or route not in enabled or route not in CATALOG:
        raise ValueError('Route not enabled')
    if not isinstance(operation, str) or operation not in CATALOG[route] or not isinstance(args, dict):
        raise ValueError('Unknown operation')
    if route == 'weather':
        if set(args) != {'location', 'period'} or args['period'] not in ('today', 'tomorrow'):
            raise ValueError('Invalid forecast parameters')
        location = args['location']
        if not isinstance(location, str) or len(location) > 100 or any(c in location for c in '\n\r;|'):
            raise ValueError('Invalid place')
        if location and normalize(location) not in normalize(question):
            raise ValueError('Invented place')
        expected_period = 'tomorrow' if re.search(r'\b(demain|tomorrow)\b', normalize(question)) else 'today'
        if args['period'] != expected_period:
            raise ValueError('Wrong forecast date')
    elif route == 'mesh':
        if operation in ACTIVE and infer_operation(question) != operation:
            raise ValueError('Active radio action requires explicit user intent')
        from .network_plan import argument_schema
        validate_arguments(args, argument_schema(operation))
        # Literal targets must come from the request; intent and typed filters
        # belong to the model, not to a second keyword-based interpreter.
        if operation == 'trace' and args.get('path'):
            path = args['path']
            nodes = path.split(',')
            if not all(re.fullmatch('[0-9a-fA-F]{2}|[0-9a-fA-F]{4}', n) for n in nodes) or len({len(n) for n in nodes}) != 1 or len(nodes) > 32:
                raise ValueError('Invalid trace path')
            if re.sub(r'[\s,]', '', path).casefold() not in re.sub(r'[\s,]', '', question).casefold():
                raise ValueError('Invented trace path')
        for name in ('target', 'country'):
            value = args.get(name, '')
            if value and normalize(value) not in normalize(question):
                raise ValueError('Invented network target')
    elif args:
        raise ValueError('Unexpected arguments')
    return Decision(Route(route), question, 'catalog', operation, args)
