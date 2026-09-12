"""One UTF-8-aware output boundary for assistant commands."""
from typing import Any

from ..models import MeshMessage


def split_reply(text: str, max_bytes: int, max_pages: int = 4) -> list[str]:
    """Bound airtime without cutting a UTF-8 code point; disclose truncation."""
    if max_bytes < 4 or max_pages < 1:
        raise ValueError("Invalid reply budget")
    remaining = text.strip()
    pages: list[str] = []
    while remaining and len(pages) < max_pages:
        data = remaining.encode("utf-8")
        if len(data) <= max_bytes:
            pages.append(remaining)
            break
        chunk = data[:max_bytes].decode("utf-8", errors="ignore")
        if len(pages) == max_pages - 1:
            pages.append(data[:max_bytes - 3].decode("utf-8", errors="ignore").rstrip() + "…")
            break
        boundary = max(chunk.rfind("\n"), chunk.rfind(" "))
        if boundary > 0:
            chunk = chunk[:boundary]
        pages.append(chunk)
        remaining = remaining[len(chunk):].lstrip()
    return pages or ["Aucune réponse disponible."]


async def send_answer(command: Any, message: MeshMessage, text: str, *, max_pages: int = 4) -> bool:
    pages = split_reply(text, command.get_max_message_length(message), max_pages)
    if len(pages) == 1:
        return await command.send_response(message, pages[0])
    return await command.send_response_chunked(message, pages)
