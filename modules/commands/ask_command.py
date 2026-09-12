"""Compatibility entry for network questions, delegating to the mesh capability."""
from .mesh_command import MeshCommand


class AskCommand(MeshCommand):
    name = "ask"
    keywords = ["ask"]
    description = "Ask a question about observed mesh network data"

    def __init__(self, bot):
        super().__init__(bot)
        self.enabled = self.get_config_value("Ask_Command", "enabled", fallback=True, value_type="bool")
