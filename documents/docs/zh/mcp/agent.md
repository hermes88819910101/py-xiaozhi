# Agent 工具（文件读写 / 指令执行）

内置的 `agent` 工具组让 AI 具备**读写本地文件、列目录、执行 shell 命令**的能力，用于「读一下这个文件」「帮我保存到文件」「跑一下 git status」这类操作。

> 与相机、音乐等内置工具一样，走 `register_agent_tools` 显式挂载，不使用全局装饰器与目录自动发现。开发约定见 [MCP 工具开发指南](./index.md)。

## 工具列表

| 工具名 | 说明 | 必填参数 | 可选参数 |
|--------|------|----------|----------|
| `self.agent.file.read` | 读取文本文件 | `path` | `offset`（字节偏移）、`max_bytes`（0=配置默认） |
| `self.agent.file.write` | 写入 / 创建文件（父目录自动创建） | `path`、`content` | `append`（默认 `false`=覆盖） |
| `self.agent.file.list` | 列出目录内容，返回 `{path,type,size}` JSON | `path` | `recursive`（默认 `false`） |
| `self.agent.shell.run` | 执行 shell 命令，返回 `exit_code/stdout/stderr` | `command` | `cwd`、`timeout_s`（0=配置默认） |

- 读取二进制文件时返回说明文本（不做解码）；超长内容按 `max_bytes` 截断并标注。
- 命令输出按 `MAX_OUTPUT_BYTES` 截断；命令超时由 `SHELL_TIMEOUT_S` 控制，超时会**终止子进程**并返回错误。

## 配置（`AGENT_TOOLS`）

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

| 键 | 默认 | 说明 |
|----|------|------|
| `ENABLED` | `true` | 整组开关；`false` 时不注册任何 agent 工具 |
| `ALLOWED_DIRS` | `[]` | 文件读写/列目录/`cwd` 的**沙盒边界**；空 = 用户家目录 |
| `SHELL_ENABLED` | `true` | 关掉则不注册 `self.agent.shell.run`（只保留文件工具） |
| `SHELL_ALLOWLIST` | `[]` | 指令白名单；空 = 不限制。非空时只允许首 token（去路径、去 `.exe`）命中的命令 |
| `SHELL_TIMEOUT_S` | `30` | 指令执行超时（秒） |
| `MAX_READ_BYTES` | `65536` | 单次读文件上限（字节） |
| `MAX_OUTPUT_BYTES` | `16000` | 单条 stdout/stderr 截断上限（字符） |

配置在**每次工具调用时**读取，改完配置无需重启即可生效（已连接的协议除外）。

## 安全说明

- **文件沙盒**：所有路径先 `expanduser/expandvars` 再 `resolve()`（含符号链接），必须落在 `ALLOWED_DIRS` 任一目录之内，否则拒绝——`../` 穿越与绝对路径越界都会被挡下。
- **指令执行不是 OS 沙盒**：`shell=True` 下权限等同本机同用户。建议：
  - 用 `ALLOWED_DIRS` 限制可访问目录；
  - 用 `SHELL_ALLOWLIST` 收窄可执行命令（如 `["git","python","ls"]`）；
  - 用 `SHELL_ENABLED=false` 彻底关闭命令执行。
- 也可用 `MCP_TOOLS.DISABLED` 单独禁用某个工具（如 `["self.agent.shell.run"]`），或按组屏蔽。

## 示例触发

| 用户说法 | 调用 |
|----------|------|
| 「读一下 config.json」 | `self.agent.file.read` |
| 「把这段保存到 notes.txt」 | `self.agent.file.write` |
| 「看看下载文件夹里有什么」 | `self.agent.file.list` |
| 「跑一下 git status」 | `self.agent.shell.run` |

## 外挂镜像（打包安装版）

`examples/mcp_plugins/com.hermes.agent/` 是等价的外挂插件（纯标准库、`python-subprocess` runtime），供 exe/dmg 用户无需改主程序即可启用。安装方式见 [MCP 外挂扩展](./plugins.md)。

## 相关

- [MCP 工具开发指南](./index.md)
- [MCP 外挂扩展](./plugins.md)
- [系统工具（音量 / 应用）](./system.md)
