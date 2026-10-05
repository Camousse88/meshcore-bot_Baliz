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
