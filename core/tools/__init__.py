"""A tool is a discrete, schema-described capability with explicit
permissions and a risk level — never a blanket 'execute anything'
capability. ToolRegistry is the single point every tool call passes
through, so permission and approval enforcement can't be bypassed by
calling a handler directly."""

from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition, ToolResult
from core.tools.registry import ToolRegistry
from core.tools.standard import register_standard_tools

__all__ = ["RiskLevel", "ExecutionPolicy", "ToolDefinition", "ToolResult", "ToolRegistry", "register_standard_tools"]
