"""Offline department lookup and a geographic breakdown of received repeater adverts."""

import gzip
import json
import math
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from itertools import zip_longest
from pathlib import Path

REFERENCE = Path(__file__).with_name('geo_data') / 'departements-2026-100m.geojson.gz'
BRITTANY = ('22', '29', '35', '56')
FRANCE = frozenset({'fr', 'france', 'république française', 'nouvelle-calédonie',
                    'new caledonia', 'polynésie française', 'french polynesia',
                    'saint-pierre-et-miquelon', 'wallis-et-futuna', 'saint-barthélemy'})
COUNTRY_NAMES = {'uk': 'Royaume-Uni', 'united kingdom': 'Royaume-Uni', 'royaume-uni': 'Royaume-Uni',
                 'belgium': 'Belgique', 'belgië': 'Belgique', 'germany': 'Allemagne',
                 'deutschland': 'Allemagne', 'spain': 'Espagne', 'españa': 'Espagne',
                 'switzerland': 'Suisse', 'the netherlands': 'Pays-Bas', 'netherlands': 'Pays-Bas'}


@dataclass(frozen=True)
class Polygon:
    department: str
    bounds: tuple[float, float, float, float]
    rings: list[list[list[float]]]


@lru_cache(maxsize=1)
def _polygons() -> list[Polygon]:
    with gzip.open(REFERENCE, 'rt', encoding='utf-8') as source:
        features = json.load(source)['features']
    result = []
    for feature in features:
        code = feature['properties']['code']
        if not re.fullmatch(r'0[1-9]|1[0-9]|2[1-9]|[3-8][0-9]|9[0-5]|2[AB]|97[12346]', code):
            continue  # Overseas collectivities are not departments.
        geometry = feature['geometry']
        polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
        for rings in polygons:
            xs, ys = zip(*rings[0], strict=True)
            result.append(Polygon(code, (min(xs), min(ys), max(xs), max(ys)), rings))
    return result


def _contains(ring: list[list[float]], x: float, y: float) -> bool:
    inside = False
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        if min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            if abs((x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)) < 1e-12:
                return True
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        x1, y1 = x2, y2
    return inside


def department_at(latitude: float | None, longitude: float | None) -> str | None:
    if (latitude is None or longitude is None or not math.isfinite(latitude) or not math.isfinite(longitude)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or (latitude, longitude) == (0, 0)):
        return None
    codes = set()
    for polygon in _polygons():
        west, south, east, north = polygon.bounds
        if (west <= longitude <= east and south <= latitude <= north
                and _contains(polygon.rings[0], longitude, latitude)
                and not any(_contains(ring, longitude, latitude) for ring in polygon.rings[1:])):
            codes.add(polygon.department)
    return next(iter(codes)) if len(codes) == 1 else None


def create_department_cache(cursor: sqlite3.Cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS repeater_departments (
            contact_id INTEGER PRIMARY KEY REFERENCES complete_contact_tracking(id) ON DELETE CASCADE,
            latitude REAL, longitude REAL, department_code TEXT
        )
    """)
    if cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='complete_contact_tracking'").fetchone():
        refresh_departments(cursor)


def refresh_departments(cursor: sqlite3.Cursor) -> None:
    """Persist new positions and recalculate changed ones; null is a cached result."""
    rows = cursor.execute("""
        SELECT c.id, c.latitude, c.longitude
        FROM complete_contact_tracking c LEFT JOIN repeater_departments d ON d.contact_id = c.id
        WHERE lower(c.role) = 'repeater' AND c.public_key IS NOT NULL AND c.public_key != ''
          AND (d.contact_id IS NULL OR c.latitude IS NOT d.latitude OR c.longitude IS NOT d.longitude)
    """).fetchall()
    if rows:
        cursor.executemany("""
            INSERT INTO repeater_departments (contact_id, latitude, longitude, department_code) VALUES (?, ?, ?, ?)
            ON CONFLICT(contact_id) DO UPDATE SET latitude=excluded.latitude, longitude=excluded.longitude,
                                                  department_code=excluded.department_code
        """, [(contact_id, lat, lon, department_at(lat, lon)) for contact_id, lat, lon in rows])


@dataclass
class RepeaterGeography:
    departments: Counter[str] = field(default_factory=Counter)
    countries: Counter[str] = field(default_factory=Counter)
    france_without_department: int = 0
    unlocated: int = 0

    @property
    def total(self) -> int:
        return self.departments.total() + self.countries.total() + self.france_without_department + self.unlocated


def repeater_geography(conn: sqlite3.Connection, *, now: float | None = None) -> RepeaterGeography:
    """Use the same local-clock convention as the advert writer, with a rolling 24 h cutoff."""
    end = time.time() if now is None else now
    start = datetime.fromtimestamp(end - 86_400)
    cursor = conn.cursor()
    refresh_departments(cursor)
    conn.commit()
    report = RepeaterGeography()
    for department, country, count in cursor.execute("""
        WITH latest AS (
            SELECT id, country, latitude, longitude,
                row_number() OVER (PARTITION BY lower(public_key) ORDER BY datetime(last_heard) DESC, id DESC) AS rank
            FROM complete_contact_tracking
            WHERE lower(role) = 'repeater' AND public_key IS NOT NULL AND public_key != ''
              AND datetime(last_heard) BETWEEN datetime(?) AND datetime(?)
        )
        SELECT d.department_code, c.country, count(*)
        FROM latest c LEFT JOIN repeater_departments d
            ON d.contact_id = c.id AND c.latitude IS d.latitude AND c.longitude IS d.longitude
        WHERE c.rank = 1
        GROUP BY d.department_code, c.country
    """, (start, datetime.fromtimestamp(end))):
        if department:
            report.departments[department] += count
            continue
        label = ' '.join(str(country or '').split())
        name = label.casefold()
        if name in FRANCE:
            report.france_without_department += count
        elif name and name not in {'?', 'unknown', 'inconnu', 'n/a'}:
            report.countries[COUNTRY_NAMES.get(name, name.capitalize())] += count
        else:
            report.unlocated += count
    return report


def geography_reply(report: RepeaterGeography, budget: int) -> str:
    if not report.total:
        return 'Aucun répéteur observé par Baliz sur 24 h.'
    top = f'Baliz · 24 h : {report.total} répéteur' + ('s' if report.total > 1 else '')
    breton = ' '.join(f'{code}={report.departments[code]}' for code in BRITTANY if report.departments[code])
    elsewhere = sorted((code, count) for code, count in report.departments.items() if code not in BRITTANY)
    abroad = sorted(report.countries.items(), key=lambda item: (-item[1], item[0]))
    elsewhere_total = sum(n for _, n in elsewhere)
    abroad_total = report.countries.total()

    def render(fr: list[tuple[str, int]], countries: list[tuple[str, int]]) -> str:
        lines = [top]
        if breton:
            lines.append('Bretagne : ' + breton)
        for label, items, total in (('Autres dép.', fr, elsewhere_total), ('Étranger', countries, abroad_total)):
            if total:
                detail = ' '.join(f'{name}={n}' for name, n in items)
                missing = total - sum(n for _, n in items)
                rest = str(total) if not detail else (f' +{missing} autres' if missing else '')
                lines.append(f'{label} : {detail}{rest}')
        if report.france_without_department:
            lines.append(f'France sans dép. : {report.france_without_department}')
        if report.unlocated:
            lines.append(f'Lieu inconnu : {report.unlocated}')
        return '\n'.join(lines)

    if len((full := render(elsewhere, abroad)).encode('utf-8')) <= budget:
        return full
    fr_visible: list[tuple[str, int]] = []
    abroad_visible: list[tuple[str, int]] = []
    for fr, foreign in zip_longest(elsewhere, abroad):
        if fr and len(render([*fr_visible, fr], abroad_visible).encode('utf-8')) <= budget:
            fr_visible.append(fr)
        if foreign and len(render(fr_visible, [*abroad_visible, foreign]).encode('utf-8')) <= budget:
            abroad_visible.append(foreign)
    summary = render(fr_visible, abroad_visible)
    if len(summary.encode('utf-8')) <= budget:
        return summary
    # Custom radios can leave less room than the smallest complete breakdown.
    short = top + ('\nBretagne : ' + breton if breton else '') + '\nDétail incomplet.'
    return short if len(short.encode('utf-8')) <= budget else top
