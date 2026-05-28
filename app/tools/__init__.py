import importlib
import logging
import pkgutil
import sys
from types import ModuleType
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server
from claude_agent_sdk.types import McpSdkServerConfig

logger = logging.getLogger(__name__)


def extract_tools(module_label: str, module: ModuleType) -> list[SdkMcpTool[Any]]:
    tools_attr = getattr(module, "TOOLS", None)
    if tools_attr is None:
        return []
    if not isinstance(tools_attr, list):
        logger.error(
            "tool module %s has malformed TOOLS attribute (not a list): %r",
            module_label,
            tools_attr,
        )
        return []
    valid: list[SdkMcpTool[Any]] = []
    for entry in tools_attr:
        if isinstance(entry, SdkMcpTool):
            valid.append(entry)
        else:
            logger.error(
                "tool module %s TOOLS contains non-SdkMcpTool entry: %r",
                module_label,
                entry,
            )
    return valid


def walk_package(package: ModuleType) -> list[SdkMcpTool[Any]]:
    discovered: list[SdkMcpTool[Any]] = []
    for module_info in pkgutil.iter_modules(package.__path__, prefix=package.__name__ + "."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:
            logger.exception("failed to import tool module %s", module_info.name)
            continue
        discovered.extend(extract_tools(module_info.name, module))
    return discovered


_DISCOVERED: list[SdkMcpTool[Any]] = walk_package(sys.modules[__name__])
MCP_SERVER: McpSdkServerConfig = create_sdk_mcp_server(
    name="local",
    version="0.1.0",
    tools=_DISCOVERED,
)
ALLOWED_TOOLS: list[str] = [f"mcp__local__{tool.name}" for tool in _DISCOVERED]
