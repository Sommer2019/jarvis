"""Gemeinsame Basis für Jarvis' MCP-Server (mcp 1.x und 2.x kompatibel)."""

from __future__ import annotations

import functools

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError


class JarvisMCP:
    """Wie `FastMCP.tool()`, reicht aber Fehlertexte (z.B. "bitte google-auth ausführen")
    an Claude durch, statt nur "Error executing tool" zu melden."""

    def __init__(self, name: str):
        self._server = _Server(name)

    def tool(self, enabled: bool = True):
        def deco(fn):
            if not enabled:  # Tool nur anbieten, wenn der Dienst konfiguriert ist
                return fn

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except ToolError:
                    raise
                except Exception as e:  # noqa: BLE001
                    raise ToolError(f"{type(e).__name__}: {e}") from e

            self._server.tool()(wrapper)
            return fn

        return deco

    def run(self):
        self._server.run()
