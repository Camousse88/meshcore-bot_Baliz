"""Capability invocation, retaining caller identity and command restrictions."""
import asyncio
from copy import deepcopy
from typing import Any

from ..models import MeshMessage
from .router import AssistantRouter, Decision, Route
from .semantic_router import SemanticRouter

HELP = "ask <question> — réseau, Wiki, réception ou chemin. Routes explicites : mesh, wiki, test, path, llm."


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
            return await self._rf_tool(route.value, message)
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

    async def _rf_tool(self, name: str, message: MeshMessage) -> str:
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
            if not self._allowed(command, cloned):
                return f"L'outil {name} est désactivé, limité ou non autorisé ici."
            command.record_execution(message.sender_id or None)
            await command.execute(cloned)
            if cloned.capture_sink:
                return "\n".join(cloned.capture_sink)
            if name == "path":
                return "Ce message ne contient pas de chemin radio exploitable."
            return "Ce message ne contient pas de mesure radio exploitable."
