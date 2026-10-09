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
        from .catalog import CATALOG, ROUTING_DIALOG_EXAMPLES, STATS_FUNCTIONS, parse_plan
        catalog = {k: dict(v) for k, v in CATALOG.items() if k in enabled_routes}
        if "mesh" in catalog:
            catalog["mesh"].pop("stats", None)
            catalog["mesh"].update({name: description for name, (_, description) in STATS_FUNCTIONS.items()})
        from .network_plan import argument_schema
        variants = []
        for route, operations in catalog.items():
            for operation in operations:
                args = argument_schema(operation) if route == 'mesh' and operation not in STATS_FUNCTIONS else {
                    'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
                if route == 'mesh' and operation in STATS_FUNCTIONS:
                    args = {'type': 'object', 'properties': {'hashes': {'type': 'boolean'}},
                            'required': ['hashes'], 'additionalProperties': False}
                if route == 'weather':
                    args = {'type': 'object', 'properties': {'location': {'type': 'string'},
                            'period': {'type': 'string', 'enum': ['today', 'tomorrow']}},
                            'required': ['location', 'period'], 'additionalProperties': False}
                variants.append({'type': 'object', 'properties': {
                    'function': {'const': route + '.' + operation}, 'args': args},
                    'required': ['function', 'args'], 'additionalProperties': False})
        examples = []
        for question_example, plan in ROUTING_DIALOG_EXAMPLES:
            if plan["route"] in catalog:
                function = plan["route"] + "." + plan["operation"]
                arguments = plan["args"]
                if function == "mesh.stats":
                    function = "mesh." + next(name for name, (topic, _) in STATS_FUNCTIONS.items()
                                               if topic == arguments["topic"])
                    arguments = {"hashes": arguments["hashes"]}
                examples.extend([
                    {"role": "user", "content": question_example},
                    {"role": "assistant", "content": json.dumps({
                        "function": function, "args": arguments}, ensure_ascii=False)},
                ])
        payload = {
            "messages": [
                {"role": "system", "content": (
                    "Tu classes les demandes adressées à Baliz, le bot du réseau radio MeshCore Bretagne. Classe la demande par sens. Retourne seulement {\"function\":\"route.operation\",\"args\":{...}}. "
                    "N'exécute rien. Choisis selon le résultat demandé et les capacités exactes du catalogue, jamais sur un mot commun. "
                    "La télémétrie désigne les mesures des capteurs ou équipements (batterie, signal, température), pas les compteurs d’activité du réseau ou du bot. Si la mesure souhaitée n’est pas précisée, demande une clarification. "
                    "Une salutation, un remerciement ou un reproche appelle llm.chat. Ce ne sont pas des demandes de mesure à clarifier. "
                    "Demande ambiguë avec plusieurs sens plausibles ou cible essentielle manquante=>llm.clarify. Ne choisis pas une statistique par défaut. "
                    "Un jugement de qualité sans critère (meilleur relais) exige llm.clarify. Le nombre d’annonces ne mesure pas la qualité. Un classement explicitement par liens utilise mesh.relay_connectivity. "
                    "Fonction réseau clairement absente=>mesh.unsupported. N'invente pas de lieu ni de cible. "
                    "weather: args location (lieu cité ou chaîne vide), period today/tomorrow. "
                    "mesh: tous les arguments du schéma sont requis. Défauts hours=0,country='',limit=5,sort=recent,hashes=false,target='',role=repeater,roundtrip=true,path=''. "
                    "count_nodes: actifs=hours24, tous connus=hours0. relay_connectivity: hours24,limit3. "
                    "Autres routes: args={}. Catalogue: " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
                    + " Règle finale : si le résultat attendu reste indéterminé, choisis llm.clarify. Ne remplace jamais une mesure par une autre statistique disponible. Une conversation simple utilise llm.chat. Une demande claire utilise sa fonction sans clarification inutile."
                )},
                *examples,
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
                if route not in catalog or operation not in catalog[route]:
                    raise ValueError('Function not exposed')
                if route == 'mesh' and operation in STATS_FUNCTIONS:
                    if (not isinstance(raw['args'], dict) or set(raw['args']) != {'hashes'}
                            or type(raw['args']['hashes']) is not bool):
                        raise ValueError('Invalid statistic arguments')
                    raw['args'] = {'topic': STATS_FUNCTIONS[operation][0], 'hashes': raw['args']['hashes']}
                    operation = 'stats'
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
