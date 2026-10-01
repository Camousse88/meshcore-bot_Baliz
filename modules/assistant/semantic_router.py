"""Single closed-catalog interpreter. Failure never chooses another capability."""

import asyncio
import time
from typing import Any

import requests

from .llm_client import post_chat


class SemanticRouter:
    """Classify a question without executing any capability."""

    def __init__(self, owner: Any):
        self.logger = owner.logger
        read = owner.get_config_value
        self.enabled = read(
            "Ask_Command", "semantic_routing_enabled", fallback=True, value_type="bool"
        )
        self.endpoint = read(
            "Llm_Command",
            "endpoint",
            fallback="http://127.0.0.1:8080/v1/chat/completions",
            value_type="str",
        )
        self.model = read("Llm_Command", "model", fallback="", value_type="str")
        self.timeout_seconds = max(
            1.0,
            min(
                60.0,
                read(
                    "Ask_Command",
                    "semantic_timeout_seconds",
                    fallback=45.0,
                    value_type="float",
                ),
            ),
        )

    def _classify(self, question, enabled_routes):
        import json
        from .catalog import CATALOG, ROUTING_EXAMPLES, parse_plan
        catalog = {k: v for k, v in CATALOG.items() if k in enabled_routes}
        from .network_plan import argument_schema
        variants = []
        for route, operations in catalog.items():
            for operation in operations:
                args = argument_schema(operation) if route == 'mesh' else {
                    'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
                if route == 'weather':
                    args = {'type': 'object', 'properties': {'location': {'type': 'string'},
                            'period': {'type': 'string', 'enum': ['today', 'tomorrow']}},
                            'required': ['location', 'period'], 'additionalProperties': False}
                variants.append({'type': 'object', 'properties': {
                    'function': {'const': route + '.' + operation}, 'args': args},
                    'required': ['function', 'args'], 'additionalProperties': False})
        payload = {
            "messages": [
                {"role": "system", "content": (
                    "Classe la demande par sens. Retourne seulement {\"function\":\"route.operation\",\"args\":{...}}. "
                    "N'exécute rien. Observations réseau=>mesh, documentation=>wiki, conversation ou reproche=>llm. "
                    "Fonction réseau absente=>mesh/unsupported. N'invente pas de lieu ni de cible. "
                    "weather: args location (lieu cité ou chaîne vide), period today/tomorrow. "
                    "mesh: tous les arguments du schéma sont requis. Défauts hours=0,country='',limit=5,sort=recent,hashes=false,target='',role=repeater,roundtrip=true,path=''. "
                    "count_nodes: actifs=hours24, tous connus=hours0. relay_connectivity: hours24,limit3. "
                    "Autres routes: args={}. Catalogue: " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
                    + " Exemples: " + json.dumps([(q, {"function": p["route"] + "." + p["operation"], "args": p["args"]}) for q, p in ROUTING_EXAMPLES if p["route"] in catalog], ensure_ascii=False, separators=(",", ":"))
                )},
                {"role": "user", "content": question},
            ],
            "temperature": 0, "max_tokens": 160,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "route_plan", "strict": True, "schema": {"anyOf": variants}}},
        }
        if self.model:
            payload["model"] = self.model
        payload["reasoning_effort"] = "none"
        started = time.monotonic()
        for attempt in range(2):
            remaining = self.timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                response = post_chat(self.endpoint, payload, remaining)
                response.raise_for_status()
            except requests.RequestException as exc:
                # Do not duplicate a request that may still be running upstream.
                self.logger.warning("Catalog transport failure after %.2fs: %s", time.monotonic() - started, exc)
                return None
            try:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                raw = json.loads(content)
                if not isinstance(raw, dict) or set(raw) != {'function', 'args'} or not isinstance(raw['function'], str):
                    raise ValueError('Invalid function call')
                route, operation = raw['function'].split('.', 1)
                plan = parse_plan(json.dumps(dict(route=route, operation=operation, args=raw['args'])), question, enabled_routes)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                self.logger.warning("Catalog invalid plan attempt=%s elapsed=%.2fs: %s", attempt + 1, time.monotonic() - started, exc)
                if attempt == 0:
                    payload['messages'].append({'role': 'user', 'content':
                        'Le plan précédent a été rejeté : ' + str(exc)[:160] +
                        '. Réinterprète la demande initiale. Respecte strictement le catalogue, le schéma et les valeurs citées. Retourne uniquement un appel JSON valide.'})
                continue
            usage = data.get("usage") or {}
            details = usage.get("prompt_tokens_details") or {}
            self.logger.info("Catalog tokens prompt=%s cached=%s completion=%s", usage.get("prompt_tokens"), details.get("cached_tokens"), usage.get("completion_tokens"))
            self.logger.info("Catalog selected route=%s operation=%s elapsed=%.2fs", plan.route.value, plan.operation, time.monotonic() - started)
            return plan
        return None

    async def decide(self, question, enabled_routes):
        if not self.enabled or not enabled_routes:
            return None
        return await asyncio.to_thread(self._classify, question, enabled_routes)
