"""外挂镜像：agent 工具（文件读/写/列目录 + 指令执行）.

这是内建 ``src/mcp/tools/agent`` 的**外挂等价物**，供打包安装版（exe/dmg）
用户无需改主程序即可启用。纯标准库、自包含（无第三方依赖），默认以
``python-subprocess`` runtime 运行，让指令执行与宿主进程隔离。

配置（可选，经 host.get("config_readonly") 读取）：
    AGENT_TOOLS.ALLOWED_DIRS     允许目录，空 = 用户家目录（沙盒边界）
    AGENT_TOOLS.SHELL_ALLOWLIST  指令白名单，空 = 不限制
    AGENT_TOOLS.SHELL_TIMEOUT_S  指令超时秒（默认 30）
    AGENT_TOOLS.MAX_READ_BYTES   单次读上限（默认 65536）
    AGENT_TOOLS.MAX_OUTPUT_BYTES 单条输出截断（默认 16000）

工具名：
    self.agent.file.read / self.agent.file.write / self.agent.file.list
    self.agent.shell.run
安全：这不是 OS 沙盒，权限等同本机同用户；只应用可信来源并自行收紧配置。
"""

from __future__ import annotations

import json
import locale
import os
import subprocess
from pathlib import Path

_DEFAULT_MAX_READ = 65536
_DEFAULT_MAX_OUTPUT = 16000
_DEFAULT_TIMEOUT = 30
_BINARY_SNIFF = 8192
_MAX_LIST = 2000


# --------------------------------------------------------------------------- #
# 配置读取（兼容 inprocess 的 ConfigManager 与 subprocess 的 dict 快照）
# --------------------------------------------------------------------------- #
def _cfg_get(cfg, path, default):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        cur = cfg
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return default
        return default if cur is None else cur
    getter = getattr(cfg, "get_config", None)
    if callable(getter):
        try:
            val = getter(path, default)
            return default if val is None else val
        except Exception:
            return default
    return default


class _Opts:
    def __init__(self, cfg):
        self.allowed_dirs = _cfg_get(cfg, "AGENT_TOOLS.ALLOWED_DIRS", []) or []
        self.shell_allowlist = _cfg_get(cfg, "AGENT_TOOLS.SHELL_ALLOWLIST", []) or []
        self.shell_timeout = (
            _cfg_get(cfg, "AGENT_TOOLS.SHELL_TIMEOUT_S", _DEFAULT_TIMEOUT)
            or _DEFAULT_TIMEOUT
        )
        self.max_read = (
            _cfg_get(cfg, "AGENT_TOOLS.MAX_READ_BYTES", _DEFAULT_MAX_READ)
            or _DEFAULT_MAX_READ
        )
        self.max_output = (
            _cfg_get(cfg, "AGENT_TOOLS.MAX_OUTPUT_BYTES", _DEFAULT_MAX_OUTPUT)
            or _DEFAULT_MAX_OUTPUT
        )


# --------------------------------------------------------------------------- #
# 文件沙盒
# --------------------------------------------------------------------------- #
class PathNotAllowed(PermissionError):
    pass


def _roots(allowed_dirs):
    dirs = [d for d in (allowed_dirs or []) if isinstance(d, str) and d.strip()]
    if not dirs:
        dirs = [str(Path.home())]
    return [Path(os.path.expandvars(os.path.expanduser(d))).resolve() for d in dirs]


def _resolve(path, allowed_dirs):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 不可为空")
    roots = _roots(allowed_dirs)
    candidate = Path(os.path.expandvars(os.path.expanduser(path.strip())))
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    resolved = candidate.resolve()
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        raise PathNotAllowed(f"路径不在允许目录内: {resolved}")
    return resolved


def _read(path, allowed_dirs, max_bytes, offset):
    target = _resolve(path, allowed_dirs)
    if not target.exists():
        raise FileNotFoundError(f"文件不存在: {target}")
    if target.is_dir():
        raise IsADirectoryError(f"是目录，非文件: {target}")
    limit = int(max_bytes or 0) or _DEFAULT_MAX_READ
    start = max(0, int(offset or 0))
    size = target.stat().st_size
    with target.open("rb") as fh:
        if start:
            fh.seek(start)
        data = fh.read(limit)
    if b"\x00" in data[:_BINARY_SNIFF]:
        return f"[二进制文件，共 {size} bytes]"
    text = data.decode("utf-8", errors="replace")
    if start + len(data) < size:
        text += f"\n...[已截断: byte {start}..{start + len(data)} / {size}]"
    return text


def _write(path, content, allowed_dirs, append):
    target = _resolve(path, allowed_dirs)
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"是目录，非文件: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content if isinstance(content, str) else str(content)
    with target.open("a" if append else "w", encoding="utf-8", newline="") as fh:
        written = fh.write(data)
    return f"{'追加' if append else '写入'} {written} 字符到 {target}"


def _list(path, allowed_dirs, recursive):
    target = _resolve(path, allowed_dirs)
    if not target.exists():
        raise FileNotFoundError(f"目录不存在: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"不是目录: {target}")
    walker = sorted(
        target.rglob("*") if recursive else target.iterdir(), key=lambda p: str(p)
    )
    entries, truncated = [], False
    for p in walker:
        if len(entries) >= _MAX_LIST:
            truncated = True
            break
        try:
            is_dir = p.is_dir()
            entries.append(
                {
                    "path": str(p.relative_to(target)),
                    "type": "dir" if is_dir else "file",
                    "size": None if is_dir else p.stat().st_size,
                }
            )
        except OSError:
            continue
    return json.dumps(
        {
            "root": str(target),
            "recursive": bool(recursive),
            "count": len(entries),
            "truncated": truncated,
            "entries": entries,
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------- #
# 指令执行
# --------------------------------------------------------------------------- #
def _first_token(command):
    token = command.strip().split()[0] if command.strip() else ""
    return token.strip("\"'")


def _check_allowlist(command, allowlist):
    allowed = [a for a in (allowlist or []) if isinstance(a, str) and a.strip()]
    if not allowed:
        return
    token = _first_token(command)
    base = token.replace("\\", "/").split("/")[-1]
    name = base[:-4] if base.lower().endswith(".exe") else base
    if name not in allowed and token not in allowed and base not in allowed:
        raise PermissionError(f"指令 {name or token!r} 不在允许清单 {allowed}")


def _decode(data):
    if not data:
        return ""
    for enc in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _truncate(text, limit):
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n...[已截断，原长 {len(text)} 字符]"


def _run(command, cwd, timeout, allowlist, allowed_dirs, max_output):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command 不可为空")
    _check_allowlist(command, allowlist)
    timeout = float(timeout or _DEFAULT_TIMEOUT)
    workdir = str(_resolve(str(cwd), allowed_dirs)) if cwd else None
    limit = int(max_output or 0) or _DEFAULT_MAX_OUTPUT
    try:
        proc = subprocess.run(
            command, shell=True, cwd=workdir, capture_output=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"指令逾时（>{timeout:g}s）: {command}") from None
    return json.dumps(
        {
            "exit_code": proc.returncode,
            "stdout": _truncate(_decode(proc.stdout), limit),
            "stderr": _truncate(_decode(proc.stderr), limit),
            "cwd": workdir,
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------- #
# 注册入口
# --------------------------------------------------------------------------- #
def register(host):  # noqa: ANN001 - host 由宿主注入
    cfg = host.get("config_readonly")

    @host.tool(
        name="self.agent.file.read",
        description=(
            "Read a text file (restricted to AGENT_TOOLS.ALLOWED_DIRS). "
            "参数: path 必填; offset 字节偏移; max_bytes 上限(0=配置默认)."
        ),
        props=[
            {"name": "path", "type": "string"},
            {"name": "offset", "type": "integer", "default": 0},
            {"name": "max_bytes", "type": "integer", "default": 0},
        ],
    )
    async def file_read(args):
        opts = _Opts(cfg)
        return _read(
            args["path"],
            opts.allowed_dirs,
            args.get("max_bytes") or opts.max_read,
            args.get("offset") or 0,
        )

    @host.tool(
        name="self.agent.file.write",
        description=(
            "Create/overwrite/append a text file (restricted to ALLOWED_DIRS). "
            "参数: path, content 必填; append 可选(默认 false)."
        ),
        props=[
            {"name": "path", "type": "string"},
            {"name": "content", "type": "string"},
            {"name": "append", "type": "boolean", "default": False},
        ],
    )
    async def file_write(args):
        opts = _Opts(cfg)
        return _write(
            args["path"],
            args["content"],
            opts.allowed_dirs,
            bool(args.get("append", False)),
        )

    @host.tool(
        name="self.agent.file.list",
        description=(
            "List a directory (restricted to ALLOWED_DIRS). "
            "参数: path 必填; recursive 可选(默认 false)."
        ),
        props=[
            {"name": "path", "type": "string"},
            {"name": "recursive", "type": "boolean", "default": False},
        ],
    )
    async def file_list(args):
        opts = _Opts(cfg)
        return _list(args["path"], opts.allowed_dirs, bool(args.get("recursive", False)))

    @host.tool(
        name="self.agent.shell.run",
        description=(
            "Run a shell command; returns exit_code/stdout/stderr. Optional "
            "ALLOWED list and timeout. 参数: command 必填; cwd; timeout_s(0=配置默认)."
        ),
        props=[
            {"name": "command", "type": "string"},
            {"name": "cwd", "type": "string", "default": ""},
            {"name": "timeout_s", "type": "integer", "default": 0},
        ],
    )
    async def shell_run(args):
        opts = _Opts(cfg)
        return _run(
            args["command"],
            args.get("cwd") or None,
            args.get("timeout_s") or opts.shell_timeout,
            opts.shell_allowlist,
            opts.allowed_dirs,
            opts.max_output,
        )
