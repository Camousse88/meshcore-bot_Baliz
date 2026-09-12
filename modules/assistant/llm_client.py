"""Shared OpenAI-compatible HTTP transport for conversation and mesh queries."""
from typing import Any

import requests


def post_chat(endpoint: str, payload: dict[str, Any], timeout: float) -> requests.Response:
    return requests.post(endpoint, json=payload, timeout=timeout)
