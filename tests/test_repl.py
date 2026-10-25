"""Tests for the full-screen session: tool events to ledger entries, follow-ups, transcript capture, the bar."""

import asyncio
import threading
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import pytest

from reverse_api.repl import bridge as bridge_mod
from reverse_api.repl.bridge import Bridge, LedgerUI, describe, short_name
from reverse_api.repl.transcript import Raw, Transcript, TranscriptWriter, raw_lines
from reverse_api.repl.view import LedgerView, SessionState, default_effort, first_definition, model_label


def plain(transcript: Transcript) -> str:
    return "\n".join("".join(frag[1] for frag in line) for line in transcript.lines())


@pytest.fixture
def session():
    st = SessionState(model="claude-sonnet-5-5")
    t = Transcript(on_link=lambda _: None)
    view = LedgerView(st, t)
    b = Bridge(view, invalidate=lambda: None, ask_user=lambda spec: Future())
    return st, t, view, b


class TestDescribe:
    def test_mcp_prefix_is_dropped(self):
        assert short_name("mcp__chrome-devtools__navigate_page") == "navigate_page"
        assert short_name("Bash") == "Bash"

    def test_browser_and_file_tools(self):
        assert describe("navigate_page", {"url": "https://jobs.ashbyhq.com/openai"})[:3] == ("navigate", "https://jobs.ashbyhq.com/openai", "read")
        assert describe("list_network_requests", {"resourceTypes": ["fetch", "xhr"]})[:2] == ("network", "fetch, xhr")
        assert describe("Write", {"file_path": "/tmp/x/api_client.py"})[:3] == ("write", "api_client.py", "write")
        assert describe("Bash", {"command": "python api_client.py"})[:3] == ("run", "python api_client.py", "run")

    def test_plumbing_tools_are_hidden(self):
        assert describe("ToolSearch", {"query": "select:x"}) is None
        assert describe("AskUserQuestion", {}) is None


class TestLedgerUI:
    def test_steps_receipts_and_code(self, session, tmp_path):
        st, t, _view, b = session
        ui = LedgerUI(b)
        ui.header("abc123", "prompt", model="claude-sonnet-5-5", mode="agent")
        ui.tool_start("mcp__chrome-devtools__navigate_page", {"url": "https://jobs.ashbyhq.com/openai"})
        ui.tool_result("mcp__chrome-devtools__navigate_page", output="Successfully navigated")
        client = tmp_path / "api_client.py"
        ui.tool_start("Write", {"file_path": str(client), "content": "import requests\n\n\ndef get_jobs():\n    return []\n"})
        ui.tool_result("Write", output="File created successfully")
        text = plain(t)
        assert "run abc123 · agent" in text
        assert "01  navigate" in text and "Successfully navigated" in text
        assert "02  write" in text and "def get_jobs" in text
        assert "File created successfully" not in text
        assert st.host == "jobs.ashbyhq.com" and st.client_name == "api_client.py"
        assert st.verb == "writing the client"

    def test_agent_text_is_a_message_and_markup_is_literal(self, session):
        _st, t, view, b = session
        LedgerUI(b).thinking("The board uses /lst/<make>/<model> and [/x] in its URLs.")
        view.flush_live()
        assert "¶" in plain(t) and "/lst/<make>/<model>" in plain(t) and "[/x]" in plain(t)

    def test_success_shows_the_card_once_usage_arrives(self, session, tmp_path):
        st, t, _view, b = session
        ui = LedgerUI(b)
        ui.header("abc123", "prompt", model="claude-sonnet-5-5")
        client = tmp_path / "api_client.py"
        client.write_text("a\nb\nc\n", encoding="utf-8")
        ui.success(str(client))
        assert "ready" not in plain(t)
        ui.usage({"input_tokens": 1000, "output_tokens": 200, "estimated_cost_usd": 0.12})
        text = plain(t)
        assert "api_client.py ready" in text and "1.2k tokens · $0.12" in text and "sonnet 5.5" in text
        assert st.cost == pytest.approx(0.12)

    def test_folded_output_offers_ctrl_o(self, session):
        st, _t, view, b = session
        assert "expand" not in "".join(text for _, text in view.status())
        LedgerUI(b).tool_result("Bash", output="\n".join(f"line {i}" for i in range(10)))
        assert st.folded
        assert "⌃o expand" in "".join(text for _, text in view.status())


class TestFollowUps:
    def test_queued_message_is_delivered_first(self, session):
        st, t, _view, b = session
        st.queue.append("add a department filter")
        assert asyncio.run(b.follow_up()) == "add a department filter"
        assert "add a department filter" in plain(t)
        assert st.running and not st.waiting

    def test_waits_for_the_input_bar_and_none_ends_the_run(self, session):
        st, _t, _view, b = session

        async def scenario():
            waiter = asyncio.ensure_future(b.follow_up())
            while not st.waiting:
                await asyncio.sleep(0.01)
            threading.Thread(target=lambda: b.follow_ups.put("more filters")).start()
            first = await waiter
            waiter = asyncio.ensure_future(b.follow_up())
            while not st.waiting:
                await asyncio.sleep(0.01)
            b.follow_ups.put(None)
            return first, await waiter

        assert asyncio.run(scenario()) == ("more filters", None)
        assert not st.waiting


class TestTranscript:
    def test_writer_turns_prints_into_lines(self):
        seen: list[Raw] = []
        writer = TranscriptWriter(seen.append)
        writer.write("first line\nsecond")
        writer.write(" line\n")
        assert [line for raw in seen for line in raw.text.splitlines()] == ["first line", "second line"]
        assert not writer.isatty()

    def test_ansi_colors_survive(self):
        raw = raw_lines("\x1b[38;2;255;61;139mpink\x1b[0m plain")
        assert "".join(frag[1] for frag in raw.lines[0]) == "pink plain"
        assert "ff3d8b" in raw.lines[0][0][0]

    def test_width_change_rerenders(self, monkeypatch):
        t = Transcript(on_link=lambda _: None)
        t.add("word " * 30)
        monkeypatch.setattr("reverse_api.repl.transcript.width", lambda: 60)
        narrow = len(t.lines())
        monkeypatch.setattr("reverse_api.repl.transcript.width", lambda: 200)
        assert len(t.lines()) < narrow


class TestViewHelpers:
    def test_model_labels_and_default_effort(self):
        assert model_label("claude-sonnet-5-5") == "sonnet 5.5"
        assert model_label("gpt-5") == "gpt-5"
        assert default_effort("claude-opus-5-5") == "medium"
        assert default_effort("claude-sonnet-5-5") == "high"

    def test_preview_starts_at_the_first_definition(self):
        source = '"""Doc."""\n\nimport requests\n\nURL = "x"\n\n\ndef get():\n    pass\n'
        assert first_definition(source) == 8
        assert first_definition("import os\n\nx = 1\n") == 3


class TestEngineHooks:
    def test_engines_use_the_session_ui_and_its_follow_ups(self, session, tmp_path):
        _st, _t, _view, b = session
        from reverse_api.engineer import ClaudeEngineer

        bridge_mod.activate(b)
        try:
            har = tmp_path / "x.har"
            har.touch()
            with (
                patch("reverse_api.base_engineer.get_scripts_dir", return_value=tmp_path / "scripts"),
                patch("reverse_api.base_engineer.MessageStore"),
            ):
                eng = ClaudeEngineer(run_id="r1", har_path=har, prompt="p", output_dir=str(tmp_path))
            assert isinstance(eng.ui, LedgerUI)
            b.follow_ups.put("next step")
            assert asyncio.run(eng._prompt_follow_up()) == "next step"
        finally:
            bridge_mod.activate(None)

    def test_classic_ui_without_a_session(self, tmp_path):
        from reverse_api.engineer import ClaudeEngineer
        from reverse_api.tui import ClaudeUI

        har = tmp_path / "x.har"
        har.touch()
        with patch("reverse_api.base_engineer.get_scripts_dir", return_value=tmp_path / "scripts"), patch("reverse_api.base_engineer.MessageStore"):
            eng = ClaudeEngineer(run_id="r1", har_path=har, prompt="p", output_dir=str(tmp_path))
        assert type(eng.ui) is ClaudeUI

    def test_tool_results_from_user_messages_reach_the_session_ui(self, session, tmp_path):
        from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage

        _st, t, _view, b = session
        from reverse_api.engineer import ClaudeEngineer

        bridge_mod.activate(b)
        try:
            har = tmp_path / "x.har"
            har.touch()
            with (
                patch("reverse_api.base_engineer.get_scripts_dir", return_value=tmp_path / "scripts"),
                patch("reverse_api.base_engineer.MessageStore"),
            ):
                eng = ClaudeEngineer(run_id="r1", har_path=har, prompt="p", output_dir=str(tmp_path))

            class Client:
                async def receive_response(self):
                    yield AssistantMessage(content=[ToolUseBlock(id="t1", name="Bash", input={"command": "python api_client.py"})], model="m")
                    yield UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="Found 794 open positions", is_error=False)])

            asyncio.run(eng._process_streaming_response(Client()))
        finally:
            bridge_mod.activate(None)
        assert "Found 794 open positions" in plain(t)

    def test_classic_ui_ignores_user_message_results(self, tmp_path):
        from claude_agent_sdk import ToolResultBlock, UserMessage

        from reverse_api.engineer import ClaudeEngineer

        har = tmp_path / "x.har"
        har.touch()
        with patch("reverse_api.base_engineer.get_scripts_dir", return_value=tmp_path / "scripts"), patch("reverse_api.base_engineer.MessageStore"):
            eng = ClaudeEngineer(run_id="r1", har_path=har, prompt="p", output_dir=str(tmp_path))

        class Client:
            async def receive_response(self):
                yield UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="noise", is_error=False)])

        with patch.object(eng.ui, "tool_result") as tool_result:
            asyncio.run(eng._process_streaming_response(Client()))
        tool_result.assert_not_called()


class TestSmoothStreaming:
    def test_deltas_reveal_gradually_then_settle(self, session):
        _st, t, view, b = session
        ui = LedgerUI(b)
        sentence = "Good, the page is loaded. Now let me check the network requests to capture the API calls."
        for word in sentence.split(" "):
            ui.stream_text(word + " ")
        view.tick()
        partial = plain(t)
        assert "▍" in partial and "calls." not in partial
        ui.thinking(sentence)
        for _ in range(200):
            view.tick()
        settled = plain(t)
        assert "calls." in settled and "▍" not in settled

    def test_items_wait_behind_the_live_message(self, session):
        _st, t, view, b = session
        ui = LedgerUI(b)
        ui.thinking("Now let's test the script.")
        ui.tool_start("Bash", {"command": "python api_client.py"})
        assert "01  run" not in plain(t)
        view.flush_live()
        text = plain(t)
        assert text.index("test the script") < text.index("01  run")

    def test_partial_messages_only_for_the_session(self, session):
        from reverse_api.repl.bridge import stream_options

        _st, _t, _view, b = session
        assert stream_options() == {}
        bridge_mod.activate(b)
        try:
            assert stream_options() == {"include_partial_messages": True}
        finally:
            bridge_mod.activate(None)

    def test_engine_forwards_text_deltas(self, tmp_path):
        from claude_agent_sdk.types import StreamEvent

        from reverse_api.engineer import ClaudeEngineer

        har = tmp_path / "x.har"
        har.touch()
        with (
            patch("reverse_api.base_engineer.get_scripts_dir", return_value=tmp_path / "scripts"),
            patch("reverse_api.base_engineer.MessageStore"),
        ):
            eng = ClaudeEngineer(run_id="r1", har_path=har, prompt="p", output_dir=str(tmp_path))
        seen: list[str] = []
        eng.ui.stream_text = seen.append  # type: ignore[attr-defined]

        def delta(kind: str, **fields: str) -> StreamEvent:
            return StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": kind, **fields}})

        class Client:
            async def receive_response(self):
                yield delta("text_delta", text="Hel")
                yield delta("text_delta", text="lo")
                yield delta("input_json_delta", partial_json="{")

        asyncio.run(eng._process_streaming_response(Client()))
        assert seen == ["Hel", "lo"]


def test_card_path_is_a_link(session, tmp_path):
    _st, t, view, _b = session
    client = tmp_path / "api_client.py"
    view.card(Path(client), 3, "python")
    handlers = [frag for line in t.lines() for frag in line if len(frag) == 3]
    assert handlers, "the client name in the card should be clickable"
