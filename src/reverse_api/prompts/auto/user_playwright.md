<mission>
{prompt}
</mission>

<output_directory>
{scripts_dir}
</output_directory>

## Workflow

Use this workflow as a guide — adapt as needed:

### Phase 1: BROWSE
Use browser MCP tools to accomplish the mission goal. Some available tools:
- `browser_navigate` - Navigate to a URL
- `browser_click` - Click an element
- `browser_close` - Close the browser
- `browser_evaluate` - Evaluate JavaScript code (also how to scroll)
- `browser_press_key` - Press a key
- `browser_run_code_unsafe` - Run Playwright code
- `browser_type` - Type text into input
- `browser_wait_for` - Wait for text/time
- `browser_snapshot` - Get accessibility tree (good alternative to screenshots)
- `browser_take_screenshot` - Take screenshot (prefer element-specific to avoid 1MB limit)
- `browser_network_requests` - List captured network requests

### Phase 2: MONITOR
Periodically call `browser_network_requests()` to monitor API traffic. You'll also get full traffic when closing the browser. Look for auth patterns (cookies, tokens, headers), endpoints, and response structures.

### Phase 3: CAPTURE
When done, call `browser_close()` to save the HAR file to: {har_path}

### Phase 4: REVERSE ENGINEER
Analyze the HAR file at `{har_path}` and generate the code. Extract all API calls, authentication patterns, and data structures.

Call `browser_network_requests()` frequently to monitor traffic.