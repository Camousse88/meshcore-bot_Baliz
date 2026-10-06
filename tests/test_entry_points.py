import json
import sqlite3

from modules.web_viewer.entry_points import entry_points


def test_entry_points_direction_dedup_collision_and_time():
    c=sqlite3.connect(':memory:')
    c.execute('CREATE TABLE complete_contact_tracking(public_key,name,country,latitude,longitude,role)')
    c.execute('CREATE TABLE packet_stream(timestamp,type,data)')
    for key,country in [('aaaa11','United Kingdom'),('bbbb22','France'),('cccc33','Guernsey'),('dddd44','France'),('eeee55',None),('ffff11','France'),('ffff22','France')]:
        c.execute('INSERT INTO complete_contact_tracking VALUES(?,?,?,?,?,?)',(key,key,country,48,-3,'repeater'))
    def packet(path,hash='1234',time=99999,route='FLOOD',width=2):
        c.execute('INSERT INTO packet_stream VALUES(?,?,?)',(time,'packet',json.dumps(dict(path_hex=path,packet_hash=hash,route_type_name=route,bytes_per_hop=width))))
    packet('aaaabbbb');packet('aaaabbbb')  # duplicates
    packet('ccccbbbb')  # same packet via a different foreign relay: counted once per entry
    packet('aaaabbbbdddd','2345')  # internal French edge must not count
    packet('bbbbaaaa','3456')  # outbound
    packet('aaaaffff','4567')  # collision
    packet('eeeeaaaa','5678')  # unknown country
    packet('aaaabbbb','6789',time=1)  # outside 24 h
    packet('aaaabbbb','7890',route='DIRECT')  # planned path
    packet('aabb','8901',width=1)  # single byte
    packet('aaaaeeeebbbb','9012')  # never bridge across unknown relay
    d=entry_points(c,1,now=100000)
    assert len(d['ranking'])==1
    assert d['ranking'][0]['public_key']=='bbbb22'
    assert d['ranking'][0]['count']==2
    assert len(d['edges'])==2
    assert d['countries']==['Guernsey','United Kingdom']
    assert d['excluded']>0
    assert entry_points(c,1,'Guernsey',now=100000)['ranking'][0]['count']==1
    assert entry_points(c,7,now=100000)['ranking'][0]['count']==3


def test_arrival_hops_shortest_path_and_unscoped_filter():
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE complete_contact_tracking(public_key,name,country,latitude,longitude,role)')
    c.execute('CREATE TABLE packet_stream(timestamp,type,data)')
    for key, country in [('aaaa11', 'United Kingdom'), ('bbbb22', 'France'), ('cccc33', 'Guernsey')]:
        c.execute('INSERT INTO complete_contact_tracking VALUES(?,?,?,?,?,?)',
                  (key, key, country, None, None, 'repeater'))
    def add(path, packet, route='FLOOD', width=2):
        c.execute('INSERT INTO packet_stream VALUES(?,?,?)', (99999, 'packet', json.dumps(dict(
            path_hex=path, packet_hash=packet, route_type_name=route, bytes_per_hop=width))))
    add('9999ccccaaaabbbb', '1234')  # unknown preceding relay still occupies a hop
    add('aaaabbbb', '1234')  # shortest arrival wins
    add('ccccaaaabbbb', '2345')
    add('ccccaaaabbbb', '2345')  # duplicate
    add('ccccaaaabbbb', '3456', 'TRANSPORT_FLOOD')
    add('aaaa11bbbb22', '4567', width=3)
    result = entry_points(c, now=100000)
    assert result['ranking'][0]['arrival_hops'] == [{'hops': 1, 'count': 2}, {'hops': 2, 'count': 2}]
    filtered = entry_points(c, now=100000, unscoped_only=True)
    node = filtered['ranking'][0]
    assert filtered['unscoped_only'] is True
    assert node['count'] == 3
    assert node['arrival_hops'] == [{'hops': 1, 'count': 2}, {'hops': 2, 'count': 1}]
    assert sum(b['count'] for b in node['arrival_hops']) == node['count']


def test_global_histogram_deduplicates_across_gateways_and_respects_filters():
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE complete_contact_tracking(public_key,name,country,latitude,longitude,role)')
    c.execute('CREATE TABLE packet_stream(timestamp,type,data)')
    for key, country in [('aaaa11', 'United Kingdom'), ('bbbb22', 'France'), ('cccc33', 'Guernsey'), ('dddd44', 'France')]:
        c.execute('INSERT INTO complete_contact_tracking VALUES(?,?,?,?,?,?)',
                  (key, key, country, 48, -3, 'repeater'))
    for path, packet, route in [('ccccaaaabbbb', '1234', 'FLOOD'), ('ccccdddd', '1234', 'FLOOD'), ('aaaabbbb', '2345', 'TRANSPORT_FLOOD')]:
        c.execute('INSERT INTO packet_stream VALUES(?,?,?)', (99999, 'packet', json.dumps(dict(
            path_hex=path, packet_hash=packet, route_type_name=route, bytes_per_hop=2))))
    result = entry_points(c, now=100000)
    assert sum(n['count'] for n in result['ranking']) == 3
    assert result['global_summary'] == {'count': 2, 'arrival_hops': [{'hops': 1, 'count': 2}]}
    filtered = entry_points(c, now=100000, country='United Kingdom', unscoped_only=True)
    assert filtered['global_summary'] == {'count': 1, 'arrival_hops': [{'hops': 2, 'count': 1}]}
    assert entry_points(c, now=100000, country='Spain')['global_summary'] == {'count': 0, 'arrival_hops': []}
