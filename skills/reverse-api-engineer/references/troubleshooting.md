# Troubleshooting

Failed runs return `status: "error"` with an `error` message and an `error_kind`:

| `error_kind` | Usual cause | What to do |
|---|---|---|
| `misuse` | Missing `-p` in scripted mode, bad flag, invalid URL, `engineer` on a run without a HAR | Fix the command; `--dry-run` shows which check fails |
| `config_invalid` | Bad value in `~/.reverse-api/config.json`, unknown SDK or provider | Run `agent --dry-run`, fix the named key |
| `permission_denied` | Model credentials rejected, or the host blocked a file or process | Check the SDK auth in [setup](setup.md) |
| `network` | Site unreachable, model API unreachable, proxy issues | Retry once; if it persists, check connectivity before rerunning |
| `engine_failure` | The browser tooling or model session failed mid-run | Rerun with `RAE_DEBUG=1` to get a traceback on stderr |
| `interrupted` | The run was cancelled or timed out on the host side | Rerun with a longer timeout, or in the background |

Other cases:

- **`config_invalid` with "Chrome remote debugging is not enabled" or "Chrome is not running"**: the default `chrome-mcp` provider could not reach the user's Chrome. Ask them to open Chrome 146+ and enable auto-connect at `chrome://inspect/#remote-debugging`, or add `--headless` when the site does not need their login.
- **`engineer` exits `2` with "captured with chrome-mcp"**: that run has no HAR. Run `agent` again with a more specific goal instead.
- **The run stalls right after the agent closes the browser**, and `<runs>/har/<run_id>/` only holds `recording.har.tmp`: the `auto` provider ran a `rae-playwright-mcp` older than 0.0.9, which hangs on Node 26. Run `npx -y rae-playwright-mcp@latest --version` to fetch the fixed release (it prints `Version 0.0.9` or later), then rerun.
- **No browser on the machine**: add `--headless`.
- **`node` or `npx` not found**: agent mode needs Node.js 20.19+; install it only with the user's approval.
- **The client runs but returns empty or wrong data**: the capture probably missed the request. Rerun `agent` with a goal that exercises it (for example "scroll to load page 2"), or, when the run has a HAR, check `har_path` for the endpoint before re-engineering.
- **Auth works once, then fails**: captured cookies or tokens expired. Make the client take fresh credentials instead of embedding them.
- **`run` picks the wrong file or asks which script to run**: pass `--file` explicitly; `run <run_id> --ls` lists the files.

Do not retry an unchanged command that already failed twice. Report the `error` text to the user instead.
