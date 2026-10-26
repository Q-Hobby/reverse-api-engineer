# Sessions and blocked sites

## Logged-in sites

The default `chrome-mcp` provider drives the user's own Chrome, so existing sessions carry over. Check `agent_provider` in `~/.reverse-api/config.json`: configs created before chrome-mcp became the default may still say `auto` (a fresh Playwright browser with no cookies). Switch it to `chrome-mcp` only with the user's agreement.

Headed `chrome-mcp` requires Chrome 146+ running with auto-connect enabled at `chrome://inspect/#remote-debugging`. `agent --dry-run` checks this as `chrome-mcp:auto-connect`, and a headed run stops with `error_kind: config_invalid` when it fails. With `--headless`, rae starts its own Chrome: no existing sessions, and no check.

`chrome-mcp` keeps no HAR, so these runs cannot be re-engineered with `engineer`. When the user expects to iterate on the same capture many times, the `auto` provider records a HAR, at the cost of a fresh browser without their sessions.

The generated client will need the same auth. Make sure it reads cookies or tokens from the environment or a file the user controls, never from values hardcoded out of the capture.

## When the agent cannot get through

Captchas, 2FA, device checks and aggressive bot detection can stop agent mode. Do not try to bypass them. Instead, ask the user to capture the flow themselves:

1. The user runs `reverse-api-engineer` in a terminal, switches to `manual` mode with Shift+Tab, and enters the goal.
2. A browser opens. The user logs in and performs the task, then closes the browser.
3. rae generates a client from that capture and saves the run.

Then pick the run up from your side:

```bash
reverse-api-engineer list --mode manual -n 5 --json
reverse-api-engineer engineer <run_id> -p "<refinements>" --json
```

## Good capture goals

- Name the data and its fields: "product name, price, stock and seller for a search query".
- Name the variations the client must handle: pagination, filters, sorting, detail pages.
- Keep one goal per run. A client for "search" and one for "checkout" are easier to verify separately.
