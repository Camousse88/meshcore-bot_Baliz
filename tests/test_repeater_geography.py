"""Department assignment and a bounded, local 24-hour repeater report."""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from modules.db_migrations import MIGRATIONS, MigrationRunner
from modules.repeater_geography import RepeaterGeography, department_at, geography_reply, repeater_geography
from tests.conftest import mock_message
from tests.test_assistant_integration import setup_bot


@pytest.mark.parametrize(('lat', 'lon', 'code'), [
    (48.3904, -4.4861, '29'), (48.5139, -2.7657, '22'),
    (48.1173, -1.6778, '35'), (47.6582, -2.7608, '56'),
    (47.2184, -1.5536, '44'), (41.9192, 8.7386, '2A'),
    (-20.88, 55.45, '974'), (49.455, -2.536, None), (0, 0, None),
])
def test_reference_contours(lat, lon, code):
    assert department_at(lat, lon) == code


def run_migrations(conn):
    MigrationRunner(conn, logging.getLogger(__name__)).run()


def add(conn, key, timestamp, *, role='repeater', country='France', lat=None, lon=None):
    conn.execute("""
        INSERT INTO complete_contact_tracking (public_key, name, role, last_heard, country, latitude, longitude)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (key, 'FR29 test', role, datetime.fromtimestamp(timestamp), country, lat, lon))


def test_upgrade_backfills_and_report_uses_unique_repeaters_in_24_hours(monkeypatch):
    now = 1_795_010_000
    with sqlite3.connect(':memory:') as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        with monkeypatch.context() as previous:
            previous.setattr('modules.db_migrations.MIGRATIONS', [m for m in MIGRATIONS if m[0] < 40])
            run_migrations(conn)
        add(conn, 'A', now - 3600, lat=48.3904, lon=-4.4861)
        add(conn, 'a', now - 1800, lat=48.1173, lon=-1.6778)
        add(conn, 'b', now - 86400, lat=47.2184, lon=-1.5536)
        add(conn, 'c', now - 86401, lat=47.2184, lon=-1.5536)
        add(conn, 'd', now + 60, lat=47.2184, lon=-1.5536)
        add(conn, 'e', now, country=None)
        add(conn, 'f', now, country='United Kingdom')
        add(conn, 'g', now, country='Royaume-Uni')
        add(conn, 'h', now, country='France')
        add(conn, 'i', now, role='companion', lat=48.3904, lon=-4.4861)
        conn.commit()
        run_migrations(conn)
        assert conn.execute('SELECT count(*) FROM repeater_departments').fetchone()[0] == 9
        report = repeater_geography(conn, now=now)
        assert report.departments == {'35': 1, '44': 1}
        assert report.countries == {'Royaume-Uni': 2}
        assert (report.france_without_department, report.unlocated, report.total) == (1, 1, 6)
        conn.execute("UPDATE complete_contact_tracking SET latitude=48.3904, longitude=-4.4861 WHERE public_key='a'")
        assert repeater_geography(conn, now=now).departments == {'29': 1, '44': 1}
        assert conn.execute('SELECT department_code FROM repeater_departments WHERE contact_id=2').fetchone() == ('29',)


def test_radio_summary_keeps_brittany_and_unlocated_when_space_is_limited():
    report = RepeaterGeography()
    report.departments.update({'22': 12, '29': 26, '35': 8, '56': 6, **{str(i): 4 for i in range(60, 80)}})
    report.countries.update({'Royaume-Uni': 3, 'Belgique': 2, 'Pays-Bas': 1, 'Espagne': 1})
    report.unlocated = 9
    text = geography_reply(report, 140)
    assert len(text.encode('utf-8')) <= 140
    assert text.splitlines()[1] == 'Bretagne : 22=12 29=26 35=8 56=6'
    assert 'Étranger : ' in text and 'Lieu inconnu : 9' in text


async def test_exact_channel_question_routes_to_repeater_breakdown_without_llm(command_mock_bot, tmp_path):
    command_mock_bot.command_manager.monitor_channels = ['#bretagne']
    command_mock_bot.meshcore.self_info = {'name': '[BOT] Baliz'}
    commands, sent = setup_bot(command_mock_bot)
    db_path = tmp_path / 'test.db'
    with sqlite3.connect(db_path) as conn:
        run_migrations(conn)
        add(conn, 'brest', datetime.now().timestamp() - 10, lat=48.3904, lon=-4.4861)
        add(conn, 'nantes', datetime.now().timestamp() - 10, lat=47.2184, lon=-1.5536)
        add(conn, 'uk', datetime.now().timestamp() - 10, country='United Kingdom')

    @contextmanager
    def connection():
        with sqlite3.connect(db_path) as conn:
            yield conn

    command_mock_bot.db_manager.connection = connection
    commands['llm'].service.rephrase_tool_result = AsyncMock()
    with patch('modules.assistant.semantic_router.post_chat') as classify:
        await commands['ask'].execute(mock_message(
            content='Baliz nombre de nœuds par départements', channel='#bretagne',
        ))
    assert [message for _, message in sent] == [
        'Baliz · 24 h : 3 répéteurs\nBretagne : 29=1\nAutres dép. : 44=1\nÉtranger : Royaume-Uni=1'
    ]
    classify.assert_not_called()
    commands['llm'].service.rephrase_tool_result.assert_not_awaited()
