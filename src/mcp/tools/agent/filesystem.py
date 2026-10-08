"""Agent 檔案系統工具：讀 / 寫 / 列目錄，一律受 ALLOWED_DIRS 沙盒限制.

設計要點：
- 路徑先 expandvars/expanduser → resolve()（含 symlink）→ 檢查落在
  ALLOWED_DIRS 任一 root 之內；否則拋 PathNotAllowedError。
- ALLOWED_DIRS 為空時，以使用者家目錄（Path.home()）為唯一 root。
- 回傳純文字或 JSON 字串；讀取依 max_bytes 截斷，避免撐爆模型 context。
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

from src.logging import get_logger

logger = get_logger()

DEFAULT_MAX_READ_BYTES = 65536
_BINARY_SNIFF_BYTES = 8192
_MAX_LIST_ENTRIES = 2000


class PathNotAllowedError(PermissionError):
    """路徑超出允許的沙盒目錄."""


def allowed_roots(allowed_dirs: Sequence[str] | None) -> list[Path]:
    """把 ALLOWED_DIRS 正規化為已 resolve 的 root 清單；空則用家目錄."""
    dirs = [d for d in (allowed_dirs or []) if isinstance(d, str) and d.strip()]
    if not dirs:
        dirs = [str(Path.home())]
    roots: list[Path] = []
    for d in dirs:
        expanded = os.path.expandvars(os.path.expanduser(d))
        roots.append(Path(expanded).resolve())
    return roots


def resolve_allowed_path(
    path: str, allowed_dirs: Sequence[str] | None
) -> Path:
    """將 path 正規化並確認落在沙盒內，回傳 resolve 後路徑.

    Raises:
        ValueError: path 為空.
        PathNotAllowedError: 解析後落在所有允許 root 之外（含 ../ 穿越）.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 不可為空")
    roots = allowed_roots(allowed_dirs)
    expanded = os.path.expandvars(os.path.expanduser(path.strip()))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    resolved = candidate.resolve()
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        raise PathNotAllowedError(
            f"路徑不在允許目錄內: {resolved}"
            f"（允許: {[str(r) for r in roots]}）"
        )
    return resolved


def _effective_max(max_bytes: int | None, fallback: int) -> int:
    try:
        value = int(max_bytes or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else fallback


def read_text_file(
    path: str,
    allowed_dirs: Sequence[str] | None,
    *,
    max_bytes: int | None = DEFAULT_MAX_READ_BYTES,
    offset: int = 0,
) -> str:
    """讀取（沙盒內）文字檔；二進位檔回傳說明，超長依 max_bytes 截斷."""
    target = resolve_allowed_path(path, allowed_dirs)
    if not target.exists():
        raise FileNotFoundError(f"檔案不存在: {target}")
    if target.is_dir():
        raise IsADirectoryError(f"是目錄，非檔案: {target}")

    limit = _effective_max(max_bytes, DEFAULT_MAX_READ_BYTES)
    try:
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        start = 0
    size = target.stat().st_size

    with target.open("rb") as fh:
        if start:
            fh.seek(start)
        data = fh.read(limit)

    if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
        return f"[二進位檔案，共 {size} bytes，無法以文字讀取]"

    text = data.decode("utf-8", errors="replace")
    if start + len(data) < size:
        text += (
            f"\n...[已截斷：僅顯示 byte {start}..{start + len(data)} / {size}]"
        )
    return text


def write_text_file(
    path: str,
    content: str,
    allowed_dirs: Sequence[str] | None,
    *,
    append: bool = False,
) -> str:
    """寫入/建立（沙盒內）文字檔；必要時建立父目錄，回傳結果說明."""
    target = resolve_allowed_path(path, allowed_dirs)
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"是目錄，非檔案: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content if isinstance(content, str) else str(content)
    with target.open("a" if append else "w", encoding="utf-8", newline="") as fh:
        written = fh.write(data)
    verb = "追加" if append else "寫入"
    return f"已{verb} {written} 字元到 {target}"


def list_directory(
    path: str,
    allowed_dirs: Sequence[str] | None,
    *,
    recursive: bool = False,
) -> str:
    """列出（沙盒內）目錄內容，回傳 JSON 字串；超過上限會標記 truncated."""
    target = resolve_allowed_path(path, allowed_dirs)
    if not target.exists():
        raise FileNotFoundError(f"目錄不存在: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"不是目錄: {target}")

    walker = sorted(
        target.rglob("*") if recursive else target.iterdir(),
        key=lambda p: str(p),
    )
    entries: list[dict] = []
    truncated = False
    for p in walker:
        if len(entries) >= _MAX_LIST_ENTRIES:
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
