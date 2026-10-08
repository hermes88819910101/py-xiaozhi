# Agent Tools (file read/write + shell)

The built-in `agent` tool group gives the assistant the ability to **read/write local files, list directories, and run shell commands** — for requests like "read this file", "save that to a file", or "run git status".

> Like the camera/music built-ins, it is mounted explicitly via `register_agent_tools`; no global decorator and no directory auto-discovery. See the [MCP tool guide](./index.md) for conventions.

## Tools

| Tool | Description | Required | Optional |
|------|-------------|----------|----------|
| `self.agent.file.read` | Read a text file | `path` | `offset` (byte), `max_bytes` (0 = configured default) |
| `self.agent.file.write` | Write / create a file (parent dirs auto-created) | `path`, `content` | `append` (default `false` = overwrite) |
| `self.agent.file.list` | List a directory, returns `{path,type,size}` JSON | `path` | `recursive` (default `false`) |
| `self.agent.shell.run` | Run a shell command, returns `exit_code/stdout/stderr` | `command` | `cwd`, `timeout_s` (0 = configured default) |

- Binary files return a description (not decoded); long content is truncated to `max_bytes` with a marker.
- Command output is truncated to `MAX_OUTPUT_BYTES`; commands exceeding `SHELL_TIMEOUT_S` are **killed** and reported as an error.

## Configuration (`AGENT_TOOLS`)

```json
"AGENT_TOOLS": {
  "ENABLED": true,
  "ALLOWED_DIRS": [],
  "SHELL_ENABLED": true,
  "SHELL_ALLOWLIST": [],
  "SHELL_TIMEOUT_S": 30,
  "MAX_READ_BYTES": 65536,
  "MAX_OUTPUT_BYTES": 16000
}
```

| Key | Default | Meaning |
|-----|---------|---------|
| `ENABLED` | `true` | Whole-group switch; `false` registers no agent tools |
| `ALLOWED_DIRS` | `[]` | **Sandbox boundary** for file read/write/list and `cwd`; empty = user home |
| `SHELL_ENABLED` | `true` | `false` skips `self.agent.shell.run` (file tools remain) |
| `SHELL_ALLOWLIST` | `[]` | Command allowlist; empty = unrestricted. Non-empty allows only matching first tokens (path and `.exe` stripped) |
| `SHELL_TIMEOUT_S` | `30` | Command timeout (seconds) |
| `MAX_READ_BYTES` | `65536` | Per-read byte cap |
| `MAX_OUTPUT_BYTES` | `16000` | Per-stream output truncation cap (chars) |

Configuration is read **on every tool call**; edits take effect without a restart (except for an already-connected protocol).

## Security

- **File sandbox**: every path is `expanduser`/`expandvars`-ed then `resolve()`d (symlinks included) and must land inside one of `ALLOWED_DIRS`; otherwise it is refused — both `../` traversal and absolute escapes are blocked.
- **Command execution is not an OS sandbox**: with `shell=True` it runs with the same user's privileges. Recommended:
  - constrain accessible dirs with `ALLOWED_DIRS`;
  - narrow executable commands with `SHELL_ALLOWLIST` (e.g. `["git","python","ls"]`);
  - disable execution entirely with `SHELL_ENABLED=false`.
- A single tool can also be disabled via `MCP_TOOLS.DISABLED` (e.g. `["self.agent.shell.run"]`).

## Example triggers

| User says | Tool |
|-----------|------|
| "read config.json" | `self.agent.file.read` |
| "save this to notes.txt" | `self.agent.file.write` |
| "what's in my Downloads folder?" | `self.agent.file.list` |
| "run git status" | `self.agent.shell.run` |

## External plugin mirror (packaged installs)

`examples/mcp_plugins/com.hermes.agent/` is an equivalent external plugin (stdlib-only, `python-subprocess` runtime) for exe/dmg users who cannot modify the main program. Install steps: [External MCP Plugins](./plugins.md).

## See also

- [MCP tool guide](./index.md)
- [External plugins](./plugins.md)
- [System tools (volume / apps)](./system.md)
