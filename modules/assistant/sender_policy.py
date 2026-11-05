"""Only identified companion origins may invoke an LLM (DMs and channels)."""

import json

from ..dm_contacts import DmContactResolver


def sender_is_companion(bot, message) -> bool:
    """Use the origin's advertised type, never its name or a repeater in the path.

    Unknown types and ambiguous identities fail closed. The historical `role`
    column is deliberately ignored: older rows may contain name-based guesses.
    This lookup does not query or modify the radio.
    """
    identifier = message.sender_pubkey or message.sender_id
    try:
        resolver = DmContactResolver(bot)
        key, row, _ = resolver.identify(identifier)
        if not key:
            return False
        records = resolver._radio_contacts() + resolver._radio_contacts(pending=True)
        native_types = {
            record['type'] for record in records
            if resolver._key(record) == key and type(record.get('type')) is int
        }
        if native_types:
            return native_types == {1}
        raw = row.get('raw_advert_data') if isinstance(row, dict) else None
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            return False
        if type(raw.get('type')) is int:
            return raw['type'] == 1
        return str(raw.get('mode', '')).casefold() == 'companion'
    except Exception:
        # No fallback to name heuristics or an unverified first prefix match.
        return False
