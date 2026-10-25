# Setup and configuration

## Requirements

- Python 3.11+ and `uv` (or `pipx` / `pip`).
- Node.js 20 to 24 with `npx` on `PATH` for agent mode. The `chrome-mcp` provider needs Node.js 20.19+. Node 26 is not supported yet: the default `auto` browser hangs when it closes and the capture never finishes.
- Manual mode only: `uv tool install "reverse-api-engineer[manual]"`, then `playwright install chromium`.

## Model access

rae calls a model through the SDK selected in its config:

| `sdk` | Auth |
|---|---|
| `claude` (default) | `ANTHROPIC_API_KEY`, or an existing Claude Code login |
| `opencode` | An OpenCode server rae starts or reuses. Defaults to a free model; can route to local models through Ollama |
| `copilot` | GitHub Copilot credentials (`GITHUB_COPILOT_TOKEN`) |
| `cursor` | `CURSOR_API_KEY` |

`agent --dry-run` reports which of these it can see. A missing env var is a warning, not an error, because some SDKs find credentials on their own.

## Settings

Settings live in `~/.reverse-api/config.json`. The CLI has no flags for them, so change them by editing that file, and only with the user's agreement since it affects every later run. Keys that matter most:

| Key | Values |
|---|---|
| `agent_provider` | `auto` (Playwright MCP, default), `chrome-mcp` (the user's real Chrome), `agent-browser` (Vercel agent-browser CLI) |
| `output_language` | `python`, `javascript`, `typescript`, `go`, `java`, `csharp`, `php`, `ruby`, `c`, `powershell` |
| `sdk` | `claude`, `opencode`, `copilot`, `cursor` |
| `claude_code_model` | Model for the `claude` SDK; `agent -m` overrides it per run |
| `output_dir` | Where runs are stored; `null` means `~/.reverse-api/runs` |

The user can also change them interactively by running `reverse-api-engineer` and typing `/settings`.

Language notes: C needs a POSIX toolchain with libcurl headers. PowerShell needs `pwsh` 7+.
