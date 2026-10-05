"""Observed foreign-to-France flood transitions, without inferred path edges."""
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone


def entry_points(conn, days=1, country='', now=None):
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    contacts = {}
    prefixes = defaultdict(set)
    for row in conn.execute('SELECT public_key,name,country,latitude,longitude FROM complete_contact_tracking WHERE lower(role) IN (\'repeater\',\'roomserver\')'):
        key, name, nation, lat, lon = row
        if not isinstance(key, str) or not key:
            continue
        key = key.lower()
        contacts[key] = dict(public_key=key, name=name or key[:8], country=(nation or '').strip(), latitude=lat, longitude=lon)
        for width in (2, 4, 6):
            prefixes[key[:width]].add(key)
    def is_fr(nation):
        return nation.casefold() in {'france', 'fr', 'français', 'francais'}
    pairs = {}
    gateways = {}
    countries = set()
    excluded = 0
    scanned = 0
    retained_since = conn.execute("SELECT MIN(timestamp) FROM packet_stream WHERE type='packet'").fetchone()[0]
    for timestamp, raw in conn.execute("SELECT timestamp,data FROM packet_stream WHERE type='packet' AND timestamp>=? AND timestamp<=? ORDER BY timestamp", (cutoff, now)):
        scanned += 1
        try:
            data = json.loads(raw)
            if data.get('route_type_name') not in ('FLOOD', 'TRANSPORT_FLOOD'):
                continue
            routing = data.get('routing_info') or {}
            packet = data.get('packet_hash') or routing.get('packet_hash')
            path = data.get('path_hex') or routing.get('path_hex')
            width = data.get('bytes_per_hop', routing.get('bytes_per_hop'))
            if (not isinstance(packet, str) or not re.fullmatch(r'[0-9a-fA-F]+', packet) or not packet.strip('0')
                    or type(width) is not int or width not in (2, 3)
                    or not isinstance(path, str) or not re.fullmatch(r'[0-9a-fA-F]+', path)
                    or len(path) % (width * 2)):
                excluded += 1
                continue
            hops = [prefixes.get(path[i:i+width*2].lower(), set()) for i in range(0, len(path), width*2)]
            for source, dest in zip(hops, hops[1:], strict=False):
                if len(source) != 1 or len(dest) != 1:
                    excluded += 1
                    continue
                a, b = next(iter(source)), next(iter(dest))
                ca, cb = contacts[a], contacts[b]
                if not ca['country'] or not cb['country']:
                    excluded += 1
                    continue
                if is_fr(ca['country']) or not is_fr(cb['country']):
                    continue
                countries.add(ca['country'])
                if country and ca['country'].casefold() != country.casefold():
                    continue
                packet = packet.lower()
                edge = pairs.setdefault((a, b), {'packets': set(), 'last_seen': timestamp})
                edge['packets'].add(packet)
                edge['last_seen'] = max(timestamp, edge['last_seen'])
                node = gateways.setdefault(b, {'packets': set(), 'last_seen': timestamp, 'countries': set()})
                node['packets'].add(packet)
                node['countries'].add(ca['country'])
                node['last_seen'] = max(timestamp, node['last_seen'])
        except (ValueError, TypeError, AttributeError):
            excluded += 1
    def date(value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None
    ranking = [dict(contacts[key], count=len(v['packets']), last_seen=date(v['last_seen']), origins=sorted(v['countries'])) for key, v in gateways.items()]
    ranking.sort(key=lambda r: (-r['count'], r['public_key']))
    edges = [dict(source=contacts[a], target=contacts[b], count=len(v['packets']), last_seen=date(v['last_seen'])) for (a,b),v in pairs.items()]
    edges.sort(key=lambda e: (-e['count'], e['source']['public_key'], e['target']['public_key']))
    return dict(ranking=ranking, edges=edges, countries=sorted(countries), days=days,
                retained_since=date(retained_since), scanned=scanned, excluded=excluded,
                generated_at=date(now))
