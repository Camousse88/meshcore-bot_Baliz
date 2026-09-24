"""Capability invocation, retaining caller identity and command restrictions."""
import asyncio
import re
import unicodedata
from copy import deepcopy
from typing import Any

from ..models import MeshMessage
from .response import split_reply
from .router import AssistantRouter, Decision, Route
from .semantic_router import SemanticRouter

HELP = "ask <question> — réseau, Wiki, météo, réception ou chemin. Routes explicites : mesh, wiki, test, path, llm."


class AssistantDispatcher:
    def __init__(self, owner: Any):
        self.owner = owner
        self.router = AssistantRouter()
        self.semantic_router = SemanticRouter(owner)
        self._rf_lock = asyncio.Lock()

    def _command(self, name: str) -> Any:
        return self.owner.bot.command_manager.commands.get(name)

    def _allowed(self, command: Any, message: MeshMessage, *, service: bool = False) -> bool:
        if command is None:
            return False
        check = command.can_use_service if service else command.can_execute
        return bool(check(message))

    async def answer(self, question: str, message: MeshMessage) -> str:
        decision = self.router.decide(question)
        if decision.reason == "wiki_probe":
            semantic_route = await self.semantic_router.decide(question, self.owner.enabled_routes)
            if semantic_route is not None:
                decision = Decision(semantic_route, question, "semantic")
        self.owner.logger.info("Assistant route=%s reason=%s", decision.route.value, decision.reason)
        if decision.route is Route.HELP:
            return HELP
        if not decision.question and decision.route not in {Route.TEST, Route.PATH}:
            return HELP
        try:
            return await asyncio.wait_for(self._dispatch(decision, message), self.owner.route_timeout_seconds)
        except TimeoutError:
            return "Le traitement a dépassé le délai. Réessaie plus tard."
        except Exception:
            self.owner.logger.exception("Assistant capability failed: %s", decision.route.value)
            return "Ce traitement est momentanément indisponible."

    async def _dispatch(self, decision: Decision, message: MeshMessage) -> str:
        route = decision.route
        if route is Route.WIKI and decision.reason == "wiki_probe" and "wiki" not in self.owner.enabled_routes:
            route = Route.LLM
        if route.value not in self.owner.enabled_routes:
            return f"La fonction {route.value} est désactivée."
        if route in {Route.TEST, Route.PATH}:
            return await self._rf_tool(route.value, decision.question, message)
        if route is Route.WEATHER:
            return await self._weather_tool(decision.question, message)
        command = self._command("mesh" if route is Route.MESH else "llm")
        if not self._allowed(command, message, service=True):
            return f"La fonction {route.value} est indisponible ou non autorisée ici."
        command.record_execution(message.sender_id or None)
        if route is Route.MESH:
            return await command.service.answer(decision.question, message)
        mode = "general" if route is Route.LLM else "wiki"
        if route is Route.WIKI and decision.reason == "wiki_probe":
            # Auto lookup is the sole fallback point; disabled Wiki does not bypass
            # the routing policy, and a disabled general route cannot be invoked.
            mode = "auto" if "llm" in self.owner.enabled_routes else "wiki"
        return await command.service.answer(decision.question, message, mode=mode)

    async def _rf_tool(self, name: str, question: str, message: MeshMessage) -> str:
        # Existing RF commands have no pure service yet. An explicit adapter uses
        # the established task-local capture contract, never monkeypatches send.
        from ..commands.path_command import PathCommand
        from ..commands.test_command import TestCommand
        command = self._command(name)
        expected = {"test": TestCommand, "path": PathCommand}[name]
        if type(command) is not expected:
            return f"L'outil {name} n'est pas disponible pour le routage."
        async with self._rf_lock:
            cloned = deepcopy(message)
            cloned.content = name
            cloned.content_lower = name
            cloned.prefix_normalized = True
            cloned.capture_sink = []
            if not self._allowed(command, cloned, service=name == "test"):
                return f"L'outil {name} est désactivé, limité ou non autorisé ici."
            command.record_execution(message.sender_id or None)
            await command.execute(cloned)
            if cloned.capture_sink:
                raw_answer = "\n".join(cloned.capture_sink)
                if name == "test":
                    llm = self._command("llm")
                    if self._allowed(llm, message, service=True):
                        measurements = self._reception_measurements(message, raw_answer)
                        reformulated = await llm.service.rephrase_tool_result(
                            question,
                            measurements,
                            context=(
                                "Il s'agit de la mesure de réception du message par le bot. "
                                "Réponds directement par oui et reformule brièvement les mesures. "
                                "Ne qualifie pas la liaison (bonne, moyenne, faible ou médiocre) "
                                "si cette qualification n'est pas déjà présente dans les données."
                            ),
                            max_length=220,
                        )
                        if reformulated:
                            return reformulated
                return raw_answer
            if name == "path":
                return "Ce message ne contient pas de chemin radio exploitable."
            return "Ce message ne contient pas de mesure radio exploitable."

    @staticmethod
    def _reception_measurements(message: MeshMessage, fallback: str) -> str:
        """Build a format-independent summary of the received RF measurements."""
        values = ["Message reçu par le bot"]
        if message.snr is not None:
            values.append(f"SNR : {message.snr} dB")
        if message.rssi is not None:
            values.append(f"RSSI : {message.rssi} dBm")
        if message.hops is not None:
            if message.hops > 0:
                values.append(f"Nombre de sauts : {message.hops}")
            else:
                values.append("Réception directe")
        if message.path:
            values.append(f"Chemin radio : {message.path}")
        return " ; ".join(values) if len(values) > 1 else fallback

    @staticmethod
    def _weather_command(question: str) -> str:
        """Turn a natural-language forecast question into the existing wx syntax."""
        folded = "".join(
            char for char in unicodedata.normalize("NFKD", question.casefold())
            if not unicodedata.combining(char)
        ).replace("’", "'").replace("`", "'")
        option = "tomorrow" if re.search(r"\b(demain|tomorrow)\b", folded) else ""
        # Prefer a location introduced by an unambiguous preposition. This
        # covers French and English while leaving a bare request to wx's normal
        # companion/default-location handling.
        location = ""
        matches = list(re.finditer(r"\b(?:a|pour|in|for)\s+([^?!.]+)", folded))
        if matches:
            location = matches[-1].group(1).strip()
        location = re.sub(
            r"\b(?:demain|tomorrow|aujourd'hui|today|ce soir|tonight)\b",
            "",
            location,
        ).strip(" ,")
        return " ".join(part for part in ("wx", location, option) if part)

    @staticmethod
    def _expand_weather_notation(source: str, wind_unit: str = "") -> str:
        """Give the LLM labelled values instead of ambiguous compact WX codes."""
        expanded = re.sub(
            r"\bH\s*:\s*(-?\d+(?:[.,]\d+)?\s*°[CF]?)",
            r"température maximale : \1",
            source,
            flags=re.IGNORECASE,
        )
        expanded = re.sub(
            r"\bL\s*:\s*(-?\d+(?:[.,]\d+)?\s*°[CF]?)",
            r"température minimale : \1",
            expanded,
            flags=re.IGNORECASE,
        )
        unit = f" {wind_unit}" if wind_unit else ""
        expanded = re.sub(
            r"(?<![\w.,])(-?\d+(?:[.,]\d+)?)G(-?\d+(?:[.,]\d+)?)(?![\w.,])",
            lambda match: (
                f"vent : {match.group(1)}{unit}; rafales : {match.group(2)}{unit}"
            ),
            expanded,
            flags=re.IGNORECASE,
        )
        return expanded

    async def _weather_tool(self, question: str, message: MeshMessage) -> str:
        """Invoke wx as an internal ASK capability, even when direct wx is hidden."""
        from ..commands.wx_command import WxCommand

        command = self._command("wx")
        if type(command) is not WxCommand or not self._allowed(command, message, service=True):
            return "Le service météo est indisponible ou non autorisé ici."
        async with self._rf_lock:
            cloned = deepcopy(message)
            cloned.content = self._weather_command(question)
            cloned.content_lower = cloned.content.casefold()
            cloned.prefix_normalized = True
            cloned.capture_sink = []
            await command.execute(cloned)
            if not cloned.capture_sink:
                return "Le service météo n'a renvoyé aucune prévision."
            raw_answer = "\n".join(cloned.capture_sink)

        # Tool data remains authoritative. The LLM only turns the compact wx
        # notation into natural language; any error or altered value falls back
        # to the original forecast.
        llm = self._command("llm")
        if self._allowed(llm, message, service=True):
            provider = getattr(command, "delegate_command", None)
            wind_unit = getattr(provider, "wind_speed_unit", "")
            wind_label = wind_unit or "l'unité indiquée par la source"
            labelled_answer = self._expand_weather_notation(raw_answer, wind_unit)
            context = (
                "H signifie température maximale et L température minimale. "
                f"Une notation comme 17G36 signifie vent 17 et rafales 36 en {wind_label}. "
                "Ces valeurs ne désignent jamais une température intérieure ou extérieure. "
                "Réponds en une seule phrase très courte."
            )
            reformulated = await llm.service.rephrase_tool_result(
                question,
                labelled_answer,
                context=context,
                max_length=120,
            )
            if reformulated:
                return split_reply(
                    reformulated,
                    self.owner.get_max_message_length(message),
                    max_pages=1,
                )[0]
        return split_reply(
            raw_answer,
            self.owner.get_max_message_length(message),
            max_pages=1,
        )[0]
