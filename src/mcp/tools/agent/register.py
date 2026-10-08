"""Agent 檔案/指令 MCP 工具：由 register_agent_tools 顯式掛載（無模組級單例）.

四個工具：
- self.agent.file.read   讀檔（受 AGENT_TOOLS.ALLOWED_DIRS 沙盒）
- self.agent.file.write  寫/建立檔（append 可選）
- self.agent.file.list   列目錄（recursive 可選）
- self.agent.shell.run   執行指令（可選 allowlist + 逾時 + 輸出截斷）

配置於**每次呼叫時**讀取（get_config），未初始化時回退內建預設，故單元測試
與未載入配置的環境亦可正常運作。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

from .filesystem import (
    DEFAULT_MAX_READ_BYTES,
    list_directory,
    read_text_file,
    write_text_file,
)
from .shell import DEFAULT_MAX_OUTPUT_BYTES, DEFAULT_TIMEOUT_S, run_command

logger = get_logger()

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "allowed_dirs": [],
    "shell_enabled": True,
    "shell_allowlist": [],
    "shell_timeout_s": DEFAULT_TIMEOUT_S,
    "max_read_bytes": DEFAULT_MAX_READ_BYTES,
    "max_output_bytes": DEFAULT_MAX_OUTPUT_BYTES,
}


def _agent_cfg() -> dict[str, Any]:
    """讀取 AGENT_TOOLS 配置；未初始化或無鍵時回退預設."""
    try:
        from src.utils.config_manager import get_config

        cfg = get_config()
    except Exception:
        return dict(_DEFAULTS)
    return {
        "enabled": bool(cfg.get_config("AGENT_TOOLS.ENABLED", True)),
        "allowed_dirs": cfg.get_config("AGENT_TOOLS.ALLOWED_DIRS", []) or [],
        "shell_enabled": bool(cfg.get_config("AGENT_TOOLS.SHELL_ENABLED", True)),
        "shell_allowlist": cfg.get_config("AGENT_TOOLS.SHELL_ALLOWLIST", []) or [],
        "shell_timeout_s": (
            cfg.get_config("AGENT_TOOLS.SHELL_TIMEOUT_S", DEFAULT_TIMEOUT_S)
            or DEFAULT_TIMEOUT_S
        ),
        "max_read_bytes": (
            cfg.get_config("AGENT_TOOLS.MAX_READ_BYTES", DEFAULT_MAX_READ_BYTES)
            or DEFAULT_MAX_READ_BYTES
        ),
        "max_output_bytes": (
            cfg.get_config("AGENT_TOOLS.MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES)
            or DEFAULT_MAX_OUTPUT_BYTES
        ),
    }


def build_agent_tools() -> list[McpTool]:
    """建構 agent 工具（不含 enabled 判斷，供測試與外掛鏡像重用）."""

    async def file_read(args: dict[str, Any]) -> str:
        cfg = _agent_cfg()
        return await asyncio.to_thread(
            read_text_file,
            args["path"],
            cfg["allowed_dirs"],
            max_bytes=args.get("max_bytes") or cfg["max_read_bytes"],
            offset=args.get("offset") or 0,
        )

    async def file_write(args: dict[str, Any]) -> str:
        cfg = _agent_cfg()
        return await asyncio.to_thread(
            write_text_file,
            args["path"],
            args["content"],
            cfg["allowed_dirs"],
            append=bool(args.get("append", False)),
        )

    async def file_list(args: dict[str, Any]) -> str:
        cfg = _agent_cfg()
        return await asyncio.to_thread(
            list_directory,
            args["path"],
            cfg["allowed_dirs"],
            recursive=bool(args.get("recursive", False)),
        )

    async def shell_run(args: dict[str, Any]) -> str:
        cfg = _agent_cfg()
        return await asyncio.to_thread(
            run_command,
            args["command"],
            cwd=args.get("cwd") or None,
            timeout_s=args.get("timeout_s") or cfg["shell_timeout_s"],
            allowlist=cfg["shell_allowlist"],
            allowed_dirs=cfg["allowed_dirs"],
            max_output_bytes=cfg["max_output_bytes"],
        )

    return [
        McpTool(
            "self.agent.file.read",
            (
                "Read a text file from the local filesystem. Access is restricted to "
                "the configured allowed directories (AGENT_TOOLS.ALLOWED_DIRS; empty = "
                "user home).\n"
                "Use when the user asks to: read / open / show the contents of a file, "
                "inspect a config or source file, check what is inside a document.\n"
                "'读取文件', '打开文件', '看一下这个文件的内容'.\n"
                "Parameters:\n"
                "- path: Absolute or (allowed-root-relative) file path.\n"
                "- offset: Optional byte offset to start reading from (default 0).\n"
                "- max_bytes: Optional byte cap (0 = use configured default).\n"
                "Returns the file text; binary files return a description, and long "
                "content is truncated with a marker."
            ),
            PropertyList(
                [
                    Property("path", PropertyType.STRING),
                    Property("offset", PropertyType.INTEGER, default_value=0),
                    Property("max_bytes", PropertyType.INTEGER, default_value=0),
                ]
            ),
            file_read,
        ),
        McpTool(
            "self.agent.file.write",
            (
                "Create or overwrite a text file on the local filesystem (access "
                "restricted to AGENT_TOOLS.ALLOWED_DIRS; parent dirs are created).\n"
                "Use when the user asks to: save / write / create a file, append text "
                "to a note or log, dump content to disk.\n"
                "'写入文件', '保存到文件', '创建文件', '追加内容'.\n"
                "Parameters:\n"
                "- path: Target file path.\n"
                "- content: Full text to write.\n"
                "- append: true to append instead of overwrite (default false).\n"
                "Returns a confirmation with the number of characters written."
            ),
            PropertyList(
                [
                    Property("path", PropertyType.STRING),
                    Property("content", PropertyType.STRING),
                    Property("append", PropertyType.BOOLEAN, default_value=False),
                ]
            ),
            file_write,
        ),
        McpTool(
            "self.agent.file.list",
            (
                "List the contents of a directory (restricted to "
                "AGENT_TOOLS.ALLOWED_DIRS). Returns JSON with entries {path, type, "
                "size}.\n"
                "Use when the user asks to: list / show a folder, see what files are "
                "in a directory, browse a project tree.\n"
                "'列出目录', '看看这个文件夹', '有哪些文件'.\n"
                "Parameters:\n"
                "- path: Directory path.\n"
                "- recursive: true to walk subdirectories (default false)."
            ),
            PropertyList(
                [
                    Property("path", PropertyType.STRING),
                    Property("recursive", PropertyType.BOOLEAN, default_value=False),
                ]
            ),
            file_list,
        ),
        McpTool(
            "self.agent.shell.run",
            (
                "Run a shell command on the local machine and return exit code plus "
                "stdout/stderr. Optional allowlist (AGENT_TOOLS.SHELL_ALLOWLIST; empty "
                "= unrestricted) and a timeout (AGENT_TOOLS.SHELL_TIMEOUT_S).\n"
                "Use when the user asks to: run a command, execute a script, check git "
                "status, list processes, run tests or a build.\n"
                "'执行命令', '运行脚本', '跑一下这个命令'.\n"
                "Parameters:\n"
                "- command: Full command line (run via the system shell).\n"
                "- cwd: Optional working directory (must be inside allowed dirs).\n"
                "- timeout_s: Optional timeout seconds (0 = configured default).\n"
                "SECURITY: this is not an OS sandbox; output is truncated to a byte cap."
            ),
            PropertyList(
                [
                    Property("command", PropertyType.STRING),
                    Property("cwd", PropertyType.STRING, default_value=""),
                    Property("timeout_s", PropertyType.INTEGER, default_value=0),
                ]
            ),
            shell_run,
        ),
    ]


def register_agent_tools(add_tool: Callable[[McpTool], None]) -> None:
    """向 McpServer 註冊 agent 工具；AGENT_TOOLS.ENABLED=false 時整組略過."""
    cfg = _agent_cfg()
    if not cfg["enabled"]:
        logger.info("AGENT_TOOLS.ENABLED=false，略過 agent 工具註冊")
        return

    tools = build_agent_tools()
    if not cfg["shell_enabled"]:
        tools = [t for t in tools if t.name != "self.agent.shell.run"]

    for tool in tools:
        add_tool(tool)
    logger.info(
        "已註冊 %d 個 agent MCP 工具（allowed_dirs=%s, shell=%s）",
        len(tools),
        cfg["allowed_dirs"] or "home",
        cfg["shell_enabled"],
    )
