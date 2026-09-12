#!/usr/bin/env python3
"""
Network data service extracted from the original Ask command.
Generates a SQL query from a natural language question and executes it.
"""

import asyncio
import re
import sqlite3
import time
from typing import Any

import requests

from ..db_manager import validate_readonly_sql
from ..models import MeshMessage
from ..utils import get_cpu_temperature
from .llm_client import post_chat

DB_SCHEMA = """\
Tables:
- complete_contact_tracking: name, public_key, role(repeater/companion/roomserver/sensor), city, country, last_heard, hop_count, snr, signal_strength, is_currently_tracked, latitude, longitude
- message_stats: timestamp, sender_id, channel, content, is_dm, hops, snr, rssi
- observed_paths: public_key, path_hex, path_length, bytes_per_hop, observation_count, last_seen, snr, rssi
- mesh_connections: from_prefix, to_prefix, from_public_key, to_public_key, observation_count, last_seen, geographic_distance
- neighbor_links: self_public_key, neighbor_public_key, last_snr, best_snr, last_status, last_seen
- daily_stats: date, public_key, advert_count

Notes:
- last_heard is a datetime string (ISO format)
- timestamp in message_stats is Unix epoch (integer)
- Use LIMIT 20 max
- Read-only: SELECT only
- Many rows have NULL latitude/longitude. For distance queries ALWAYS add: WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND latitude != 0
- No firmware version or hardware/model info is stored in this database. If asked about version or hardware, reply: 'not tracked in DB'
- Only query the tables listed above. Do NOT query: bbs_messages, bot_metadata, channels, clock_sync_*, command_stats, daily_rollup, dashboard_snapshot, feed_*, generic_cache, geocoding_cache, greeted_users, greeter_rollout, neighbor_observations, packet_stream, purging_log, schema_version
- ALWAYS resolve public_key to name: JOIN complete_contact_tracking c ON c.public_key = <table>.public_key and SELECT c.name. Truncate names to 15 chars: SUBSTR(c.name, 1, 15) AS name. Never SELECT a raw public_key or prefix as the primary identifier.
- When selecting a distance, alias it with its unit, e.g. ROUND(<haversine>,1) AS distance_km
"""


class MeshService:
    """Natural-language queries over locally observed mesh data; no RF output."""

    def __init__(self, bot: Any, config_reader):
        self.bot = bot
        self.logger = bot.logger
        self.get_config_value = config_reader
        self.endpoint = self.get_config_value(
            "Llm_Command", "endpoint", fallback="http://127.0.0.1:8080/v1/chat/completions", value_type="str"
        )
        self.timeout_seconds = max(
            1.0,
            min(
                300.0,
                self.get_config_value("Llm_Command", "timeout_seconds", fallback=60.0, value_type="float"),
            ),
        )
        self.max_tokens = max(
            8, min(500, self.get_config_value("Llm_Command", "max_tokens", fallback=200, value_type="int"))
        )
        self.model = self.get_config_value("Llm_Command", "model", fallback="", value_type="str")


    def _get_sender_position(self, message: MeshMessage) -> tuple[float, float] | None:
        """Look up the sender's GPS position from the DB."""
        sender_id = message.sender_id or ""
        if not sender_id:
            return None
        try:
            with self.bot.db_manager.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT latitude, longitude FROM complete_contact_tracking WHERE name = ? AND latitude IS NOT NULL AND longitude IS NOT NULL AND latitude != 0 LIMIT 1",
                    (sender_id,),
                )
                row = cursor.fetchone()
                if not row:
                    cursor.execute(
                        "SELECT latitude, longitude FROM complete_contact_tracking WHERE public_key = ? AND latitude IS NOT NULL AND longitude IS NOT NULL AND latitude != 0 LIMIT 1",
                        (sender_id,),
                    )
                    row = cursor.fetchone()
                if row:
                    return (float(row[0]), float(row[1]))
        except Exception:
            pass
        return None


    def _generate_sql(self, question: str, sender_pos: tuple[float, float] | None) -> str | None:
        """Ask the LLM to generate a SQL query for the question."""
        pos_info = ""
        haversine = ""
        if sender_pos:
            lat, lon = sender_pos
            pos_info = f"\nUser position: {lat:.5f}, {lon:.5f} (use for distance calculations)"
            haversine = (
                f"\nHaversine formula: 6371*2*ASIN(SQRT("
                f"POWER(SIN(RADIANS(latitude-{lat:.5f})/2),2)+"
                f"COS(RADIANS({lat:.5f}))*COS(RADIANS(latitude))*"
                f"POWER(SIN(RADIANS(longitude-{lon:.5f})/2),2)))"
            )

        system_prompt = (
            "You are a SQL query generator for a mesh network database. "
            "Given a question, respond with ONLY a single SQL SELECT query. "
            "No explanations, no markdown, just the SQL. "
            "Use LIMIT 20. Read-only. " + DB_SCHEMA + haversine + pos_info
        )
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.1,
            "top_p": 0.9,
        }
        if self.model:
            payload["model"] = self.model
        try:
            response = post_chat(self.endpoint, payload, self.timeout_seconds)
            if response.status_code != 200:
                return None
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()
            sql_match = re.search(r"```(?:sql)?\s*(SELECT.+?)```", content, re.DOTALL | re.IGNORECASE)
            if sql_match:
                return sql_match.group(1).strip()
            select_match = re.search(r"(SELECT\s+.+?)(?:;\s*)$", content, re.DOTALL | re.IGNORECASE)
            if select_match:
                return select_match.group(1).strip()
            if content.upper().startswith("SELECT"):
                return content.rstrip(";").strip()
            return None
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            self.logger.warning(f"Mesh service SQL generation error: {e}")
            return None


    def _execute_sql(self, sql: str) -> str:
        """Execute a read-only SQL query and return compact results."""
        ok, reason = validate_readonly_sql(sql)
        if not ok:
            return f"(rejected: {reason})"
        sql = sql.strip().rstrip(";")
        if not re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
            sql += " LIMIT 20"
        sql = re.sub(r"\bLIMIT\s+\d+", "LIMIT 20", sql, flags=re.IGNORECASE)
        sql = sql.rstrip(";")

        allowed_tables = {
            "complete_contact_tracking", "message_stats", "observed_paths",
            "mesh_connections", "neighbor_links", "daily_stats",
        }
        def authorize(action, table, column, database, source):
            if action == sqlite3.SQLITE_READ and table not in allowed_tables:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        try:
            with self.bot.db_manager.connection() as conn:
                previous_query_only = conn.execute("PRAGMA query_only").fetchone()[0]
                conn.execute("PRAGMA query_only = ON")
                deadline = time.monotonic() + 2.0
                conn.set_authorizer(authorize)
                conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                try:
                    rows = conn.execute(sql).fetchmany(20)
                finally:
                    conn.set_authorizer(None)
                    conn.set_progress_handler(None, 0)
                    conn.execute(f"PRAGMA query_only = {int(previous_query_only)}")
                if not rows:
                    return "(no results)"
                return "\n".join(", ".join(str(v) for v in row if v is not None) for row in rows)
        except Exception as e:
            self.logger.warning("Mesh query failed: %s", e)
            return "(query error: unavailable or unauthorized data)"


    def _question_asks_for_datetime(self, question: str) -> bool:
        """Return True if the question is specifically about a date/time (keep timestamps)."""
        q = (question or "").lower()
        markers = (
            "date", "jour", "quand", "qu'elle", "qu'elle est", "dernier", "derniere", "dernière",
            "récent", "recente", "récente", "recent", "latest", "when", "time", "heure",
            "timestamp", "moment", "plus récent", "plus recemment", "plus récente",
        )
        return any(m in q for m in markers)


    def _postprocess_results(self, results: str, keep_datetime: bool = False) -> str:
        """Clean raw SQL results before they are formatted for the mesh.

        1. Replace hex public keys / prefixes with node names (best-effort).
           The LLM sometimes selects a ``public_key`` (hex, commonly 64 chars but
           occasionally longer) or a short prefix (e.g. ``from_prefix``) instead of a
           name. Each such token is resolved against ``complete_contact_tracking``
           (exact match first, then prefix) and swapped for the node name.
           A token is a candidate if it is 8+ hex chars; prefix matching is only
           attempted when it contains at least one hex letter (a-f), so pure numeric
           values (timestamps, counts) are not mistaken for key prefixes.
        2. Drop verbose ISO datetimes (date + time) to save message space — unless
           the question is specifically about a date/time, in which case they are kept.
        """
        if not results or not results.strip():
            return results
        candidates: set[str] = set()
        for line in results.splitlines():
            for part in line.split(", "):
                p = part.strip()
                if re.fullmatch(r"[0-9a-fA-F]{8,}", p):
                    candidates.add(p.lower())
        name_map: dict[str, str] = {}
        if candidates:
            try:
                with self.bot.db_manager.connection() as conn:
                    cursor = conn.cursor()
                    for cand in candidates:
                        name = None
                        # 1) Exact match (any length).
                        cursor.execute(
                            "SELECT name FROM complete_contact_tracking WHERE public_key = ? LIMIT 1",
                            (cand,),
                        )
                        row = cursor.fetchone()
                        if row:
                            name = row[0]
                        # 2) Prefix match, only if the token has at least one hex letter.
                        if not name and re.search(r"[a-fA-F]", cand):
                            cursor.execute(
                                "SELECT name FROM complete_contact_tracking WHERE public_key LIKE ? LIMIT 1",
                                (cand + "%",),
                            )
                            row = cursor.fetchone()
                            if row:
                                name = row[0]
                        if name:
                            name_map[cand] = name[:15]
            except Exception as e:
                self.logger.debug(f"Mesh service pubkey->name resolution error: {e}")

        def _process_part(part: str) -> str | None:
            p = part.strip()
            # Resolve a public key / prefix to a name.
            if p.lower() in name_map:
                return name_map[p.lower()]
            # Drop verbose datetimes unless the question is about a date/time.
            if not keep_datetime and self._DATETIME_RE.match(p):
                return None
            return part

        out_lines = []
        for line in results.splitlines():
            parts = [processed for processed in (_process_part(part) for part in line.split(", ")) if processed is not None]
            if parts:
                out_lines.append(", ".join(parts))
        return "\n".join(out_lines)


    def _format_followup(self, question: str, sql_results: str) -> str | None:
        """Second LLM call to format results into a mesh-friendly answer."""
        prompt = (
            f"The query returned these results:\n{sql_results}\n\n"
            f"Answer the question: {question}\n\n"
            "FORMAT RULES (mesh network, max 150 chars per message):\n"
            "- One item per line: 'name: value unit'\n"
            "- ALWAYS attach a unit to every number: km for distance, hops for path length, messages for counts, days/hours/minutes for time, % for percentages, dBm for signal strength, bytes for data\n"
            "- Use node NAMES, never hex public keys or short prefixes\n"
            "- NEVER show raw coordinates (lat/lon) or raw hex keys\n"
            "- Max 10 items, no tables, no pipes\n"
            "- Total response under 500 chars\n"
            "- If the data is a single aggregate (count, total), just answer with the number and its unit"
        )
        payload: dict[str, Any] = {
            "messages": [
                {
                    "role": "system",
                    "content": "You are a concise assistant on a low-bandwidth mesh network. Reply briefly.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 150,
            "temperature": 0.2,
            "top_p": 0.9,
        }
        if self.model:
            payload["model"] = self.model
        try:
            response = post_chat(self.endpoint, payload, self.timeout_seconds)
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
            self.logger.warning(f"Mesh service followup error: {e}")
        return None


    _DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?$")

    async def answer(self, question: str, message: MeshMessage) -> str:
        threshold = self.get_config_value("Llm_Command", "cpu_temp_threshold", fallback=60.0, value_type="float")
        temperature = get_cpu_temperature() if threshold > 0 else None
        if temperature is not None and temperature >= threshold:
            return "trop chaud:"
        # Get sender position for distance-based queries
        sender_pos = self._get_sender_position(message)

        # Step 1: Generate SQL
        sql = await asyncio.to_thread(self._generate_sql, question, sender_pos)
        if not sql:
            return "Could not generate a query. Try rephrasing."

        self.logger.debug(f"Mesh service generated SQL: {sql}")

        # Step 2: Execute
        sql_results = await asyncio.to_thread(self._execute_sql, sql)
        if sql_results.startswith(("(rejected:", "(query error:", "(no results)")):
            return "Aucune donnée disponible." if sql_results == "(no results)" else "La requête réseau n’a pas pu être exécutée."
        # Step 2b: Post-process — resolve pubkeys to names, drop verbose datetimes
        #          (unless the question is specifically about a date/time).
        keep_dt = self._question_asks_for_datetime(question)
        sql_results = await asyncio.to_thread(self._postprocess_results, sql_results, keep_dt)
        self.logger.debug(f"Mesh service SQL results: {sql_results[:500]}")

        # Step 3: Format with LLM followup
        formatted = await asyncio.to_thread(self._format_followup, question, sql_results)
        if not formatted:
            formatted = sql_results

        return formatted
