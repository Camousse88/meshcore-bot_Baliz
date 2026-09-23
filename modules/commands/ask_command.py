"""Assistant entry point. Routing and capabilities live in modules.assistant."""
from ..assistant.dispatcher import AssistantDispatcher
from ..assistant.response import send_answer
from ..models import MeshMessage
from .base_command import BaseCommand


class AskCommand(BaseCommand):
    name = "ask"
    keywords = ["ask"]
    description = "Assistant: route questions to mesh data, Wiki, RF tools or conversation"
    short_description = description
    category = "special"
    cooldown_seconds = 5
    usage = "ask <question>"
    examples = ["ask comment tu me reçois ?", "ask combien de répéteurs actifs ?"]
    settings_schema = [
        {"key": "enabled_routes", "label": "Enabled routes", "type": "list",
         "default": "test,path,mesh,wiki,weather,llm", "help": "Allowed capabilities: test,path,mesh,wiki,weather,llm."},
        {"key": "route_timeout_seconds", "label": "Processing timeout", "type": "int",
         "default": 120, "min": 1, "max": 300, "unit": "seconds"},
        {"key": "semantic_routing_enabled", "label": "LLM routing for ambiguous questions", "type": "bool",
         "default": True, "help": "Use the configured LLM only when deterministic routing is inconclusive."},
        {"key": "max_pages", "label": "Maximum reply pages", "type": "int",
         "default": 4, "min": 1, "max": 8},
    ]

    def __init__(self, bot):
        super().__init__(bot)
        self.ask_enabled = self.get_config_value("Ask_Command", "enabled", fallback=True, value_type="bool")
        self.enabled_routes = {r.strip().lower() for r in self.get_config_value(
            "Ask_Command", "enabled_routes", fallback="test,path,mesh,wiki,weather,llm", value_type="str"
        ).split(",") if r.strip()}
        unknown = self.enabled_routes - {"test", "path", "mesh", "wiki", "weather", "llm"}
        if unknown:
            raise ValueError(f"Unknown assistant routes: {sorted(unknown)}")
        self.route_timeout_seconds = max(1, min(300, self.get_config_value(
            "Ask_Command", "route_timeout_seconds", fallback=120, value_type="int")))
        self.max_pages = max(1, min(8, self.get_config_value("Ask_Command", "max_pages", fallback=4, value_type="int")))
        self.dispatcher = AssistantDispatcher(self)

    def can_execute(self, message: MeshMessage, skip_channel_check: bool = False) -> bool:
        return self.ask_enabled and super().can_execute(message, skip_channel_check)

    async def execute(self, message: MeshMessage) -> bool:
        _, question = self.split_trigger_and_args(self._strip_mentions(message.content))
        text = await self.dispatcher.answer(question, message)
        return await send_answer(self, message, text, max_pages=self.max_pages)
