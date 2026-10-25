"""What the engines talk to while the full-screen session is open: their UI calls become ledger entries.

Engines run in a worker thread; everything here only appends to the transcript or waits on
thread-safe queues, and asks the app to redraw.
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import IO, Any, cast
from urllib.parse import urlparse

from rich.console import Console

from .style import THEME, width
from .transcript import Raw, TranscriptWriter
from .view import KIND_VERB, LedgerView, language_for

# Tools that are plumbing for the agent, not steps worth showing.
HIDDEN_TOOLS = {"ToolSearch", "AskUserQuestion"}
QUIET_RESULTS = {"Write", "Edit", "MultiEdit", "TodoWrite", "TaskCreate", "TaskUpdate", "report_client_verified"}
LOOK_TOOLS = {"take_snapshot", "browser_snapshot", "take_screenshot", "browser_take_screenshot"}


def short_name(tool_name: str) -> str:
    """mcp__chrome-devtools__navigate_page reads as navigate_page."""
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return tool_name


def first_line(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe(name: str, args: dict[str, Any]) -> tuple[str, str, str, str | None, str | None] | None:
    """(verb, target, kind, url, path) for a tool call, or None when the call is not worth a step."""
    if name in HIDDEN_TOOLS:
        return None
    url = args.get("url") if isinstance(args.get("url"), str) else None
    path = args.get("file_path") or args.get("path")
    path = path if isinstance(path, str) else None
    if name in ("navigate_page", "browser_navigate", "new_page"):
        return "navigate", first_line(url or args.get("type") or "page"), "read", url, None
    if name in ("list_network_requests", "browser_network_requests"):
        types = args.get("resourceTypes")
        return "network", ", ".join(types) if isinstance(types, list) and types else "all requests", "read", None, None
    if name == "get_network_request":
        return "request", f"#{args.get('reqid', '?')}", "read", None, None
    if name in LOOK_TOOLS:
        return "look", "screenshot" if "screenshot" in name else "page structure", "read", None, None
    if name in ("click", "browser_click", "hover", "browser_hover"):
        return "click", first_line(args.get("element") or args.get("uid") or args.get("ref") or "element"), "read", None, None
    if name in ("fill", "fill_form", "browser_type", "type_text", "press_key", "browser_press_key"):
        return "type", first_line(args.get("value") or args.get("text") or args.get("key") or "text"), "read", None, None
    if name in ("evaluate_script", "browser_evaluate"):
        return "script", first_line(args.get("function") or args.get("script") or "page script"), "read", None, None
    if name in ("wait_for", "browser_wait_for"):
        return "wait", first_line(args.get("text") or args.get("time") or "page"), "read", None, None
    if name in ("list_pages", "select_page", "close_page", "browser_tabs", "browser_close"):
        return "tabs", name.replace("_", " "), "read", None, None
    if name == "report_client_verified":
        return "verified", first_line(args.get("summary") or "the client ran on the live site"), "other", None, None
    if name == "Write":
        return "write", Path(path).name if path else "file", "write", None, path
    if name in ("Edit", "MultiEdit"):
        return "edit", Path(path).name if path else "file", "write", None, path
    if name == "Read":
        return "read", Path(path).name if path else "file", "read", None, path
    if name in ("Bash", "shell"):
        return "run", first_line(args.get("command") or args.get("description") or "command"), "run", None, None
    if name in ("Glob", "Grep"):
        return "search", first_line(args.get("pattern") or args.get("query") or ""), "read", None, None
    if name == "WebFetch":
        return "fetch", first_line(url or ""), "read", url, None
    if name == "WebSearch":
        return "search", first_line(args.get("query") or ""), "read", None, None
    if name in ("TaskCreate", "TaskUpdate"):
        return "plan", first_line(args.get("subject") or args.get("description") or args.get("status") or "task"), "other", None, None
    return name.replace("_", " "), first_line(json.dumps(args, default=str) if args else ""), "other", None, None


class Bridge:
    """One per session: routes engine calls to the view and carries follow-ups, questions and cancellation."""

    def __init__(self, view: LedgerView, invalidate: Callable[[], None], ask_user: Callable[[dict[str, Any]], Future]) -> None:
        self.view = view
        self.st = view.st
        self.invalidate = invalidate
        self.ask_user = ask_user
        self.follow_ups: queue.Queue[str | None] = queue.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task | None = None
        self.pending_card: Path | None = None
        self.cost_base = 0.0
        self.verified = False  # the agent called report_client_verified in this run

    def ui(self) -> LedgerUI:
        return LedgerUI(self)

    def bind_task(self) -> None:
        """Remember the engine's task so esc can cancel it from the app's thread."""
        try:
            self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
        except RuntimeError:
            self.loop, self.task = None, None

    def cancel(self) -> bool:
        if self.loop and self.task and not self.task.done():
            self.loop.call_soon_threadsafe(self.task.cancel)
            return True
        return False

    def flush_card(self) -> None:
        path, self.pending_card = self.pending_card, None
        if path is None:
            return
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            return  # no file, no card: the run reported a path it never wrote
        self.view.card(path, source.count("\n") + 1 if source else 0, language_for(path))
        self.invalidate()

    async def follow_up(self) -> str | None:
        """The next message for this run: a queued one, or whatever the user types; None ends the run."""
        st = self.st
        self.flush_card()
        if st.queue:
            text: str | None = st.queue.pop(0)
            self.view.user(text or "")
        else:
            st.running, st.waiting = False, True
            self.invalidate()
            text = await asyncio.get_running_loop().run_in_executor(None, self.follow_ups.get)
            st.waiting = False
            if text is None:
                self.invalidate()
                return None
        st.running, st.verb, st.kind = True, "thinking", "other"
        self.invalidate()
        return text

    async def ask(self, questions: list[Any]) -> dict[str, str]:
        """Questions from the agent, answered through the input bar."""
        answers: dict[str, str] = {}
        for q in questions:
            get = q.get if isinstance(q, dict) else lambda key, default=None, q=q: getattr(q, key, default)
            text = str(get("question", "") or "")
            if not text:
                continue
            options = []
            for opt in get("options", []) or []:
                label = opt.get("label", "") if isinstance(opt, dict) else getattr(opt, "label", "")
                desc = opt.get("description", "") if isinstance(opt, dict) else getattr(opt, "description", "")
                if label:
                    options.append((str(label), str(desc or "")))
            self.view.question(text)
            self.invalidate()
            answer = await asyncio.wrap_future(self.ask_user({"question": text, "options": options}))
            answers[text] = answer or ""
            self.view.answer(answer or "skipped")
            self.invalidate()
        return answers


class LedgerUI:
    """Drop-in for ClaudeUI while the session is open."""

    shows_tool_results = True  # engines read tool results from user messages only for this UI

    def __init__(self, bridge: Bridge, verbose: bool = True) -> None:
        self.b = bridge
        self.view = bridge.view
        self.st = bridge.st
        self.verbose = verbose
        # Engines also print directly; that output joins the conversation as plain lines.
        writer = TranscriptWriter(self._printed)
        self.console = Console(file=cast(IO[str], writer), force_terminal=True, color_system="truecolor", width=width(), theme=THEME, highlight=False)

    def _printed(self, raw: Raw) -> None:
        self.view.out(raw)
        self.b.invalidate()

    def _changed(self) -> None:
        self.b.invalidate()

    def header(self, run_id: str, prompt: str, model: str | None = None, sdk: str | None = None, mode: str | None = None) -> None:
        self.b.bind_task()
        st = self.st
        st.run_id, st.step, st.tokens = run_id, 0, 0
        st.running, st.waiting, st.verb, st.kind = True, False, "starting", "other"
        self.b.cost_base = st.cost
        self.b.verified = False
        if model:
            st.model = model
        self.view.run_line(run_id, mode)
        self._changed()

    def start_analysis(self) -> None:
        pass

    def tool_start(self, tool_name: str, tool_input: dict | Any, call_id: str | None = None) -> None:
        _ = call_id
        self.b.flush_card()
        name = short_name(tool_name)
        args = tool_input if isinstance(tool_input, dict) else {}
        if isinstance(tool_input, str) and tool_input.strip().startswith("{"):
            try:
                args = json.loads(tool_input)
            except json.JSONDecodeError:
                args = {}
        found = describe(name, args)
        if found is None:
            return
        verb, target, kind, url, path = found
        if name == "report_client_verified":
            self.b.verified = True
        st = self.st
        st.kind = kind
        st.verb = "reading traffic" if verb in ("network", "request") else KIND_VERB.get(kind, "thinking")
        if url:
            st.host = urlparse(url).netloc or st.host
        self.view.step(verb, target, kind, path=Path(path) if path else None, url=url)
        if name == "Write" and isinstance(args.get("content"), str) and path:
            st.client_name = Path(path).name
            self.view.code(args["content"], language_for(path))
        elif name in ("Edit", "MultiEdit") and path:
            edits: list[Any] = args["edits"] if isinstance(args.get("edits"), list) else [args]
            for edit in edits[:3]:
                if isinstance(edit, dict) and isinstance(edit.get("old_string"), str) and isinstance(edit.get("new_string"), str):
                    self.view.diff(edit["old_string"], edit["new_string"], language_for(path))
        self._changed()

    def tool_result(self, tool_name: str, is_error: bool = False, output: str | None = None, call_id: str | None = None) -> None:
        _ = call_id
        name = short_name(tool_name)
        if name in HIDDEN_TOOLS:
            return
        text = str(output or "").strip()
        lines = text.splitlines()
        if is_error:
            self.view.receipt(lines[:60] or ["failed"], error=True)
        elif name in QUIET_RESULTS:
            return
        elif name in LOOK_TOOLS:
            self.view.receipt([f"{len(lines)} lines captured"] if lines else ["captured"])
        elif name == "Read":
            self.view.receipt([f"{len(lines)} lines"])
        else:
            self.view.receipt(lines[:400])
        self._changed()

    def todo_updated(self, todos: list) -> None:
        if todos:
            self.view.plan(todos)
            self._changed()

    def stream_thinking(self, delta: str) -> None:
        """A delta of the model's reasoning summary, shown live in the margin."""
        if delta:
            self.b.flush_card()
            self.view.stream(delta, kind="note")
            self.st.verb, self.st.kind = "thinking", "other"
            self._changed()

    def thinking_note(self, text: str) -> None:
        """The complete reasoning summary for a turn; settles the live note."""
        if text and text.strip():
            self.view.stream_done(text.strip(), kind="note")
            self._changed()

    def stream_text(self, delta: str) -> None:
        """A text delta from the SDK, shown as it arrives."""
        if delta:
            self.b.flush_card()
            self.view.stream(delta)
            self._changed()

    def thinking(self, text: str, max_length: int = 500) -> None:
        _ = max_length
        if text and text.strip():
            self.b.flush_card()
            self.view.stream_done(text.strip())
            self._changed()

    def thinking_block(self, text: str, max_chars: int = 8000) -> None:
        self.thinking(text[:max_chars])

    def progress(self, message: str) -> None:
        self.view.notice(message)
        self._changed()

    def success(self, script_path: str, local_path: str | None = None) -> None:
        _ = local_path
        # The card promises a working client, so it waits for the agent to have verified one.
        self.b.pending_card = Path(script_path) if self.b.verified else None
        self._changed()

    def usage(self, usage: dict[str, Any]) -> None:
        st = self.st
        keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        st.tokens = sum(int(usage.get(key, 0) or 0) for key in keys)
        cost = usage.get("estimated_cost_usd")
        if isinstance(cost, (int, float)):
            st.cost = self.b.cost_base + float(cost)
        self.b.flush_card()

    def error(self, message: str) -> None:
        self.view.error(message)
        self._changed()

    def sync_started(self, dest_dir: str) -> None:
        _ = dest_dir

    def sync_flash(self, message: str) -> None:
        _ = message

    def sync_error(self, message: str) -> None:
        self.view.notice(f"sync error: {message}")
        self._changed()


class CursorLedgerUI(LedgerUI):
    """Cursor streams token-sized text; its engine buffers it and flushes whole blocks."""

    def __init__(self, bridge: Bridge, engineer: Any, verbose: bool = True) -> None:
        super().__init__(bridge, verbose=verbose)
        self._eng = engineer

    def thinking(self, text: str, max_length: int = 500) -> None:
        _ = max_length
        if text:
            self._eng._cursor_feed_thinking(text)
            self.stream_text(text)

    def thinking_block(self, text: str, max_chars: int = 8000) -> None:
        LedgerUI.thinking(self, text[:max_chars])


_bridge: Bridge | None = None


def adaptive_thinking(model: str | None) -> bool:
    """Adaptive thinking exists from the 4.6 models on, and not on Haiku 4.5; unknown names get none."""
    m = re.match(r"claude-(opus|sonnet|haiku|fable)-(\d+)-(\d+)", model or "")
    if m is None:
        return False
    return (int(m.group(2)), int(m.group(3))) >= (4, 6) and not (m.group(1) == "haiku" and m.group(2) == "4")


def stream_options(model: str | None = None) -> dict[str, Any]:
    """ClaudeAgentOptions kwargs for the session: streamed text, and reasoning summaries to show while the agent thinks."""
    if _bridge is None:
        return {}
    options: dict[str, Any] = {"include_partial_messages": True}
    if adaptive_thinking(model):
        options["thinking"] = {"type": "adaptive", "display": "summarized"}
    return options


def active() -> Bridge | None:
    """The open session's bridge, or None when rae runs in the classic interface or headless."""
    return _bridge


def activate(bridge: Bridge | None) -> None:
    global _bridge
    _bridge = bridge
