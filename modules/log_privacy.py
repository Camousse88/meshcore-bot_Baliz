"""Keep message bodies in the processing layer, not in web log output."""
import json
import math
import re

_ROUTES = {'UNKNOWN', 'TRANSPORT_FLOOD', 'TRANSPORT_DIRECT', 'FLOOD', 'DIRECT',
           'TRANSPORT_SCOPED_FLOOD', 'TRANSPORT_TRANSPORT'}
_PAYLOADS = {'TXT_MSG', 'GRP_TXT', 'GRP_DATA', 'REQ', 'RESPONSE', 'ACK', 'ADVERT',
             'ANON_REQ', 'PATH', 'TRACE', 'MULTIPART', 'RAW_CUSTOM', 'UNKNOWN'}
_ANSI = re.compile(r'\x1b\[[0-9;]*m')
_PREFIX = re.compile(r'^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+Z?)?) - ([A-Za-z0-9_.-]+) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$')


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return str(int(value)) if integer else f'{value:g}'


def message_metadata(message):
    """Do not mutate the message: the bot/statistics still need its full body."""
    private = getattr(message, 'is_dm', False) is True
    routing = getattr(message, 'routing_info', None)
    route = routing.get('route_type', 'UNKNOWN') if isinstance(routing, dict) else 'UNKNOWN'
    route = route if isinstance(route, str) and route in _ROUTES else 'UNKNOWN'
    result = f"📡 {'PRIVATE' if private else 'CHANNEL'} MESSAGE: {route} | Type: {'TXT_MSG' if private else 'GRP_TXT'}"
    for attr, label, integer, unit in [('hops','Hops',True,''),('snr','SNR',False,' dB'),('rssi','RSSI',False,' dBm')]:
        value = _number(getattr(message, attr, None), integer=integer)
        if value is not None:
            result += f' | {label}: {value}{unit}'
    body = getattr(message, 'original_content', None) or getattr(message, 'content', '')
    if isinstance(body, str):
        result += f' | Bytes: {len(body.encode("utf-8"))}'
    return result


def _safe_radio_body(body):
    """Reconstruct radio metadata from a closed grammar, never forward free text."""
    direct = re.fullmatch(r'📡 (DIRECT|CHANNEL|PRIVATE) MESSAGE: ([A-Z_]+) \| Type: ([A-Z_]+)((?: \| (?:Hops|SNR|RSSI|Bytes): -?\d+(?:\.\d+)?(?: dB| dBm)?)*)(?: \| Contenu masqué)?', body)
    if direct and direct[2] in _ROUTES and direct[3] in _PAYLOADS:
        kind = 'CHANNEL' if direct[3] == 'GRP_TXT' else direct[1]
        return f'📡 {kind} MESSAGE: {direct[2]} | Type: {direct[3]}{direct[4]}'
    routing = re.fullmatch(r'🛣️\s+ROUTING INFO: ([A-Z_]+) \| Path: ([0-9a-fA-F,]+) \((\d+) hops, (\d+) bytes\) \| Payload: (\d+) bytes \| Type: ([A-Z_]+)', body)
    if routing and routing[1] in _ROUTES and routing[6] in _PAYLOADS:
        return f'🛣️ ROUTING INFO: {routing[1]} | Path: {routing[2]} ({routing[3]} hops, {routing[4]} bytes) | Payload: {routing[5]} bytes | Type: {routing[6]}'
    # Closed, argument-free diagnostic counters remain useful on the web.
    if re.fullmatch(r'Catalog tokens prompt=\d+ cached=\d+ completion=\d+', body):
        return body
    if re.fullmatch(r'(?:Catalog selected|Assistant) route=(?:mesh|llm|wiki|weather|test|path) operation=(?:summary|hfcond|relay_connectivity|count_nodes|count_repeaters|list_repeaters|onebyte|near|stats|neighbors|trace|advert|unsupported|forecast|lookup|clarify|chat|receive|message)(?: elapsed=\d+(?:\.\d+)?s| reason=catalog)', body):
        return body
    if re.fullmatch(r'(?:LLM request prepared|LLM visible response received|LLM retry response received|LLM Wiki response cleaned) \(\d+ characters\)', body):
        return body
    if re.fullmatch(r'Sending channel message \| Channel index: \d+ \| Bytes: \d+', body):
        return body
    migration = re.fullmatch(r'DB migration (\d{4}) applied: .*', body)
    if migration:
        return 'DB migration ' + migration[1] + ' applied'
    if body.startswith('Connected to:'):
        return 'Connexion radio établie'
    return None


def web_log_line(line):
    """A metadata-only projection for both new log lines and legacy history.

    Unknown/unstructured lines are not trusted to be free of plaintext: retain
    their timestamp/severity, but leave their details in the local technical log.
    Continuations are withheld, including traceback messages and multiline input.
    """
    line = _ANSI.sub('', str(line)).rstrip('\r\n')
    if line.startswith('{'):
        try:
            item = json.loads(line)
            line = f"{item['timestamp']} - {item['logger']} - {item['level']} - {item['message']}"
        except (ValueError, KeyError, TypeError):
            return None
    match = _PREFIX.fullmatch(line)
    if not match:
        return None
    logger = match[2].split('.')[0]
    if logger not in {'MeshCoreBot','meshcore','meshcore_cli','modern_web_viewer','apscheduler','root'}:
        logger = 'technical'
    prefix = f'{match[1]} - {logger} - {match[3]} - '
    body = _safe_radio_body(match[4])
    return prefix + (body or 'Événement technique — détail réservé aux logs locaux')
