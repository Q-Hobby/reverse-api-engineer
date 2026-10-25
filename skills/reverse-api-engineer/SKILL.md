---
name: reverse-api-engineer
description: Reverse engineer a website's private HTTP API into a typed, runnable client (Python, TypeScript, Go and 7 more) with the reverse-api-engineer CLI. A browser captures the site's real traffic to a HAR file, then a model writes the client. Use when the user wants to call a site programmatically and it has no public API, or the public one lacks what the browser can do. Skip it when an official API or SDK already covers the task.
---

# Reverse API Engineer

`reverse-api-engineer` (rae) opens a browser, records the requests a website makes while it performs a task, and turns that HAR capture into an API client. Every run is saved, so a capture can be re-engineered later without browsing again.

This skill only supplies instructions. It drives the CLI through its scripted, JSON-emitting commands.

## 1. Check the install

```bash
reverse-api-engineer --version
```

If the command is missing, ask the user before installing anything, then:

```bash
uv tool install reverse-api-engineer   # or: pipx install reverse-api-engineer
```

Agent mode also needs Node.js 20 to 24 on `PATH` (it launches browser tooling through `npx`). On Node 26 the default browser hangs when it closes, so the run never finishes; check `node --version` first. See [setup and configuration](references/setup.md) for model access and settings.

## 2. Reuse an earlier capture when one exists

```bash
reverse-api-engineer list --search "<site or task>" --json
```

If a matching run exists, skip to step 5 and improve it with `engineer` instead of browsing again.

## 3. Preflight

```bash
reverse-api-engineer agent -p "<goal>" -u "<start url>" --dry-run
```

This validates the prompt, URL, config and environment without opening a browser. Fix every `error` check before running. A `warn` on the API key is fine when the SDK authenticates another way (for example a logged-in Claude Code).

Write the goal as the data the user wants, not the clicks: "fetch open roles with title, location and team from the careers page, including pagination" works better than "click the jobs tab".

## 4. Capture and generate

A run takes several minutes, often longer than a shell tool's default timeout. Start it in the background from the outset, with progress going to a log file:

```bash
reverse-api-engineer agent -p "<goal>" -u "<start url>" --json-stream > rae-run.ndjson 2> rae-run.log
```

`--json-stream` writes one NDJSON event per line and ends with a `{"event":"result",...}` line. Wait for that line, or for the process to exit, before reading the result. Check progress with `tail rae-run.log`. Never start a second capture while one is still running: it repeats the work and the cost.

- Add `--headless` on machines without a display.
- Add `-o <dir>` to write runs somewhere other than `~/.reverse-api/runs`.
- `--json` prints the same result as a single object at the end, with no progress. Use it only when the shell can wait for the whole run.
- Sites that need the user's logged-in session: use the `chrome-mcp` provider. See [sessions and blocked sites](references/sessions.md).

The final result line has these fields:

| Field | Meaning |
|---|---|
| `status` | `ok` or `error` |
| `run_id` | Id for `engineer`, `run` and `show` |
| `script_path` | The generated client, inside `<runs>/scripts/<run_id>/` next to its `README.md` |
| `har_path` | The raw capture |
| `usage.total_cost_usd` | Model cost of the run; report it to the user |
| `error_kind` | Set on failure; see [troubleshooting](references/troubleshooting.md) |

Exit codes: `0` success, `1` capture or engineering failed, `2` misuse.

## 5. Verify the client

Read the generated client and its `README.md` before running anything: it is model-written code. Then run it:

```bash
reverse-api-engineer run <run_id> --ls                       # list generated files
reverse-api-engineer run <run_id> --file <client file> --json -- <script args>
```

`--auto-install` installs missing dependencies without prompting; ask the user first. Check that the output contains real data from the site, not just a 200 status.

## 6. Iterate on the same capture

```bash
reverse-api-engineer engineer <run_id> -p "add pagination and a typed Job model" --json
reverse-api-engineer engineer <run_id> --fresh -p "only the search endpoint" --json
```

Without `--fresh`, the prompt is layered on top of the original goal and the existing client. With `--fresh`, the client is regenerated from the HAR. If the capture itself is missing an endpoint, run `agent` again with a goal that exercises it.

## 7. Hand it over

rae also writes a copy to `./scripts/<descriptive_name>/` in the working directory, so run it from the user's project or move that folder where it belongs. Adapt names and types to their codebase, and keep the generated `README.md` as usage notes. Tell the user which endpoints it wraps and how auth is handled (cookies, bearer token, CSRF), since captured credentials expire.

## Boundaries

- Only capture sites the user is allowed to access, and respect their terms and rate limits.
- Captures can contain cookies and tokens. Do not paste HAR contents into chats, issues or commits.
- When a site needs a human (captcha, 2FA, unusual login), ask the user to capture it in manual mode, then continue with `engineer`. See [sessions and blocked sites](references/sessions.md).
