from __future__ import annotations

import ast
import operator
from pathlib import Path
from typing import Any

from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolRegistry, ToolResult


_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_number(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _eval_number(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left = _eval_number(node.left)
        right = _eval_number(node.right)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > 12:
            raise ValueError("calculator exponent is outside the safe range")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval_number(node.operand))
    raise ValueError("calculator supports numeric arithmetic only")


def _calculator(_context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    expression = str(arguments["expression"]).strip()
    if len(expression) > 512:
        raise ValueError("calculator expression is too long")
    tree = ast.parse(expression, mode="eval")
    value = _eval_number(tree)
    return ToolResult(ok=True, content=str(value), data={"value": value})


def _echo(_context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    text = str(arguments["text"])
    return ToolResult(ok=True, content=text, data={"text": text})


def _list_workspace(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    relative = str(arguments.get("path") or ".")
    root = context.resolve_workspace_path(relative)
    if not root.exists():
        return ToolResult(ok=False, content=f"Workspace path does not exist: {relative}")
    if not root.is_dir():
        return ToolResult(ok=False, content=f"Workspace path is not a directory: {relative}")
    entries = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold())[:200]:
        entries.append({"name": path.name, "kind": "directory" if path.is_dir() else "file"})
    return ToolResult(ok=True, content=f"{len(entries)} workspace entries", data={"entries": entries})


def _read_workspace_text(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    relative = str(arguments["path"])
    path = context.resolve_workspace_path(relative)
    if not path.is_file():
        return ToolResult(ok=False, content=f"Workspace file does not exist: {relative}")
    if path.stat().st_size > 256_000:
        return ToolResult(ok=False, content="Workspace text file exceeds the 256 KB read limit")
    text = path.read_text(encoding="utf-8")
    return ToolResult(ok=True, content=text, data={"path": relative, "chars": len(text)})


def builtin_read_only_tools() -> ToolRegistry:
    return ToolRegistry(
        (
            AgentTool(
                name="calculator",
                description="Evaluate deterministic numeric arithmetic without executing code.",
                input_schema={
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                    "additionalProperties": False,
                },
                handler=_calculator,
                effect=ToolEffect.READ_ONLY,
            ),
            AgentTool(
                name="echo",
                description="Return the supplied text unchanged. Useful for harness diagnostics.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                handler=_echo,
                effect=ToolEffect.READ_ONLY,
            ),
            AgentTool(
                name="list_workspace_files",
                description="List files and directories inside this agent session workspace only.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "additionalProperties": False,
                },
                handler=_list_workspace,
                effect=ToolEffect.READ_ONLY,
            ),
            AgentTool(
                name="read_workspace_text",
                description="Read one UTF-8 text file inside this agent session workspace only.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=_read_workspace_text,
                effect=ToolEffect.READ_ONLY,
            ),
        )
    )


__all__ = ["builtin_read_only_tools"]
