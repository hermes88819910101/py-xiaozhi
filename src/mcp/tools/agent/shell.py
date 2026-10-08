"""Agent Shell 工具：執行本機指令（可選 allowlist + 逾時 + 輸出截斷）.

安全模型（分層，預設寬鬆、可逐步收緊）：
- allowlist：AGENT_TOOLS.SHELL_ALLOWLIST 非空時，只允許首個 token（去 .exe、
  去路徑）命中的指令；空 = 不限制。
- 逾時：逾時由 subprocess.run 殺掉子程序並拋 TimeoutError。
- 輸出截斷：stdout/stderr 各依 max_output_bytes 截斷，避免撐爆 context。
- cwd 受同一個 ALLOWED_DIRS 沙盒約束。

注意：shell=True 下這**不是** OS 沙盒，權限等同本機同使用者；只應由使用者
本人使用，或配合 allowlist 收緊。子程序 runtime 的外掛另見 examples/。
"""

from __future__ import annotations

import json
import locale
import shlex
import subprocess
from collections.abc import Sequence

from src.logging import get_logger

logger = get_logger()

DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_OUTPUT_BYTES = 16000


class CommandNotAllowedError(PermissionError):
    """指令不在 allowlist 內."""


def _first_token(command: str) -> str:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    return parts[0] if parts else ""


def check_allowlist(command: str, allowlist: Sequence[str] | None) -> None:
    """allowlist 非空時，驗證指令首 token 是否被允許."""
    allowed = [a for a in (allowlist or []) if isinstance(a, str) and a.strip()]
    if not allowed:
        return
    token = _first_token(command)
    base = token.replace("\\", "/").split("/")[-1]
    name = base[:-4] if base.lower().endswith(".exe") else base
    if name not in allowed and token not in allowed and base not in allowed:
        raise CommandNotAllowedError(
            f"指令 {name or token!r} 不在允許清單 {allowed}"
        )


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    for enc in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n...[已截斷，原長 {len(text)} 字元]"


def run_command(
    command: str,
    *,
    cwd: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    allowlist: Sequence[str] | None = None,
    allowed_dirs: Sequence[str] | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> str:
    """執行指令並回傳 JSON 字串（exit_code/stdout/stderr/timeout）."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command 不可為空")
    check_allowlist(command, allowlist)

    try:
        timeout = float(timeout_s) if timeout_s else float(DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = float(DEFAULT_TIMEOUT_S)

    workdir: str | None = None
    if cwd and str(cwd).strip():
        from .filesystem import resolve_allowed_path

        workdir = str(resolve_allowed_path(str(cwd), allowed_dirs))

    try:
        limit = int(max_output_bytes or 0) or DEFAULT_MAX_OUTPUT_BYTES
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_OUTPUT_BYTES

    logger.info("[AgentShell] 執行指令 (cwd=%s): %s", workdir, command)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"指令逾時（>{timeout:g}s）: {command}") from None

    result = {
        "exit_code": proc.returncode,
        "stdout": _truncate(_decode(proc.stdout), limit),
        "stderr": _truncate(_decode(proc.stderr), limit),
        "cwd": workdir,
    }
    return json.dumps(result, ensure_ascii=False)
