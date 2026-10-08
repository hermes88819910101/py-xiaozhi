"""agent 工具組測試：沙盒檔讀寫/列目錄、指令執行、註冊與外掛鏡像.

覆蓋：
- 路徑沙盒（允許內、../ 穿越、絕對越界）
- 讀/寫/追加 roundtrip、不存在檔、二進位偵測、讀取截斷
- 列目錄（非遞迴/遞迴）
- shell 執行、allowlist、逾時、輸出截斷
- register_agent_tools 的 ENABLED / SHELL_ENABLED 開關
- examples/mcp_plugins/com.hermes.agent 外掛鏡像可註冊可呼叫
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from src.mcp.tools.agent import filesystem, shell
from src.mcp.tools.agent import register as register_mod
from src.mcp.tools.agent.register import build_agent_tools, register_agent_tools

# --------------------------------------------------------------------------- #
# 路徑沙盒
# --------------------------------------------------------------------------- #


def test_resolve_allowed_path_allows_inside(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hi", encoding="utf-8")
    resolved = filesystem.resolve_allowed_path(str(target), [str(tmp_path)])
    assert resolved == target.resolve()


def test_resolve_allowed_path_blocks_traversal(tmp_path):
    outside = tmp_path.parent / "evil.txt"
    with pytest.raises(filesystem.PathNotAllowedError):
        filesystem.resolve_allowed_path(str(outside), [str(tmp_path)])


def test_resolve_allowed_path_blocks_dotdot(tmp_path):
    sneaky = str(tmp_path / ".." / "evil.txt")
    with pytest.raises(filesystem.PathNotAllowedError):
        filesystem.resolve_allowed_path(sneaky, [str(tmp_path)])


def test_resolve_allowed_path_empty_raises(tmp_path):
    with pytest.raises(ValueError):
        filesystem.resolve_allowed_path("   ", [str(tmp_path)])


# --------------------------------------------------------------------------- #
# 讀 / 寫 / 列目錄
# --------------------------------------------------------------------------- #


def test_write_then_read_roundtrip(tmp_path):
    path = str(tmp_path / "sub" / "note.txt")
    msg = filesystem.write_text_file(path, "hello 世界", [str(tmp_path)])
    assert "寫入" in msg
    assert filesystem.read_text_file(path, [str(tmp_path)]) == "hello 世界"


def test_write_append(tmp_path):
    path = str(tmp_path / "log.txt")
    filesystem.write_text_file(path, "a\n", [str(tmp_path)])
    filesystem.write_text_file(path, "b\n", [str(tmp_path)], append=True)
    assert filesystem.read_text_file(path, [str(tmp_path)]) == "a\nb\n"


def test_read_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        filesystem.read_text_file(str(tmp_path / "nope.txt"), [str(tmp_path)])


def test_write_outside_sandbox_blocked(tmp_path):
    outside = tmp_path.parent / "denied.txt"
    with pytest.raises(filesystem.PathNotAllowedError):
        filesystem.write_text_file(str(outside), "x", [str(tmp_path)])
    assert not outside.exists()


def test_read_truncates(tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("x" * 100, encoding="utf-8")
    out = filesystem.read_text_file(str(path), [str(tmp_path)], max_bytes=10)
    assert out.startswith("x" * 10)
    assert "已截斷" in out


def test_read_binary_detected(tmp_path):
    path = tmp_path / "bin.dat"
    path.write_bytes(b"\x00\x01\x02hello")
    out = filesystem.read_text_file(str(path), [str(tmp_path)])
    assert "二進位" in out


def test_list_directory(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    data = json.loads(filesystem.list_directory(str(tmp_path), [str(tmp_path)]))
    names = {e["path"] for e in data["entries"]}
    assert names == {"a.txt", "d"}
    assert data["count"] == 2


def test_list_directory_recursive(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "deep.txt").write_text("x", encoding="utf-8")
    data = json.loads(
        filesystem.list_directory(str(tmp_path), [str(tmp_path)], recursive=True)
    )
    paths = {e["path"] for e in data["entries"]}
    assert "d" in paths
    assert str(Path("d") / "deep.txt") in paths


def test_list_not_a_directory(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        filesystem.list_directory(str(f), [str(tmp_path)])


# --------------------------------------------------------------------------- #
# shell
# --------------------------------------------------------------------------- #


def test_shell_allowlist_blocks():
    with pytest.raises(shell.CommandNotAllowedError):
        shell.run_command("echo hi", allowlist=["ls"], allowed_dirs=None)


def test_shell_allowlist_allows():
    result = json.loads(
        shell.run_command("echo hi", allowlist=["echo"], allowed_dirs=None)
    )
    assert result["exit_code"] == 0
    assert "hi" in result["stdout"]


def test_shell_run_and_exit_code():
    result = json.loads(shell.run_command("echo hello", allowed_dirs=None))
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


def test_shell_output_truncated():
    cmd = f'"{sys.executable}" -c "print(\'y\' * 500)"'
    result = json.loads(
        shell.run_command(cmd, allowed_dirs=None, max_output_bytes=20)
    )
    assert "已截斷" in result["stdout"]


def test_shell_timeout():
    cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    with pytest.raises(TimeoutError):
        shell.run_command(cmd, timeout_s=1, allowed_dirs=None)


def test_shell_cwd_sandbox(tmp_path):
    with pytest.raises(filesystem.PathNotAllowedError):
        shell.run_command(
            "echo hi", cwd=str(tmp_path.parent), allowed_dirs=[str(tmp_path)]
        )


# --------------------------------------------------------------------------- #
# 註冊 / 工具物件
# --------------------------------------------------------------------------- #


def _patch_cfg(monkeypatch, **overrides):
    cfg = {
        "enabled": True,
        "allowed_dirs": [],
        "shell_enabled": True,
        "shell_allowlist": [],
        "shell_timeout_s": 30,
        "max_read_bytes": filesystem.DEFAULT_MAX_READ_BYTES,
        "max_output_bytes": shell.DEFAULT_MAX_OUTPUT_BYTES,
    }
    cfg.update(overrides)
    monkeypatch.setattr(register_mod, "_agent_cfg", lambda: cfg)
    return cfg


def test_register_registers_four_tools(monkeypatch):
    _patch_cfg(monkeypatch)
    collected = []
    register_agent_tools(collected.append)
    names = {t.name for t in collected}
    assert names == {
        "self.agent.file.read",
        "self.agent.file.write",
        "self.agent.file.list",
        "self.agent.shell.run",
    }


def test_register_disabled_registers_none(monkeypatch):
    _patch_cfg(monkeypatch, enabled=False)
    collected = []
    register_agent_tools(collected.append)
    assert collected == []


def test_register_shell_disabled(monkeypatch):
    _patch_cfg(monkeypatch, shell_enabled=False)
    collected = []
    register_agent_tools(collected.append)
    names = {t.name for t in collected}
    assert "self.agent.shell.run" not in names
    assert "self.agent.file.read" in names


async def test_tool_call_end_to_end(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, allowed_dirs=[str(tmp_path)])
    tools = {t.name: t for t in build_agent_tools()}

    written = json.loads(
        await tools["self.agent.file.write"].call(
            {"path": str(tmp_path / "e2e.txt"), "content": "e2e"}
        )
    )
    assert written["isError"] is False

    read = json.loads(
        await tools["self.agent.file.read"].call(
            {"path": str(tmp_path / "e2e.txt"), "offset": 0, "max_bytes": 0}
        )
    )
    assert read["isError"] is False
    assert read["content"][0]["text"] == "e2e"


async def test_tool_call_sandbox_error_is_reported(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, allowed_dirs=[str(tmp_path)])
    tools = {t.name: t for t in build_agent_tools()}
    out = json.loads(
        await tools["self.agent.file.read"].call(
            {"path": str(tmp_path.parent / "x.txt"), "offset": 0, "max_bytes": 0}
        )
    )
    assert out["isError"] is True


# --------------------------------------------------------------------------- #
# 外掛鏡像
# --------------------------------------------------------------------------- #


class _FakeHost:
    def __init__(self, cfg=None):
        self.tools = {}
        self._cfg = cfg

    def get(self, name):
        return self._cfg if name == "config_readonly" else None

    def tool(self, *, name, description, props=None):
        def deco(func):
            self.tools[name] = func
            return func

        return deco


def _load_plugin_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "mcp_plugins"
        / "com.hermes.agent"
        / "plugin.py"
    )
    spec = importlib.util.spec_from_file_location("com_hermes_agent_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_registers_tools():
    plugin = _load_plugin_module()
    host = _FakeHost()
    plugin.register(host)
    assert set(host.tools) == {
        "self.agent.file.read",
        "self.agent.file.write",
        "self.agent.file.list",
        "self.agent.shell.run",
    }


async def test_plugin_roundtrip_and_sandbox(tmp_path):
    plugin = _load_plugin_module()
    cfg = {"AGENT_TOOLS": {"ALLOWED_DIRS": [str(tmp_path)]}}
    host = _FakeHost(cfg)
    plugin.register(host)

    await host.tools["self.agent.file.write"](
        {"path": str(tmp_path / "p.txt"), "content": "ok", "append": False}
    )
    out = await host.tools["self.agent.file.read"](
        {"path": str(tmp_path / "p.txt"), "offset": 0, "max_bytes": 0}
    )
    assert out == "ok"

    with pytest.raises(plugin.PathNotAllowed):
        await host.tools["self.agent.file.read"](
            {"path": str(tmp_path.parent / "x.txt"), "offset": 0, "max_bytes": 0}
        )
