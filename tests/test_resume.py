"""Resume across SDKs: each engine records its session and reopens it from the user's next message."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reverse_api import cli


@pytest.fixture
def har_path(tmp_path: Path) -> Path:
    p = tmp_path / "recording.har"
    p.write_text('{"log":{"entries":[]}}')
    return p


def _cursor(har_path: Path, tmp_path: Path):
    from reverse_api.cursor_engineer import CursorEngineer

    with patch("reverse_api.base_engineer.get_scripts_dir", return_value=tmp_path / "scripts"), patch("reverse_api.base_engineer.MessageStore"):
        return CursorEngineer(run_id="abc123", har_path=har_path, prompt="jobs", cursor_model="composer-2", sdk="cursor", verbose=False)


class TestCursor:
    @pytest.mark.asyncio
    async def test_a_resumed_agent_gets_only_the_next_message(self, har_path, tmp_path):
        eng = _cursor(har_path, tmp_path)
        eng.resume_session = "agent-1"
        eng._prompt_follow_up = AsyncMock(side_effect=["now add pagination", None])  # type: ignore[method-assign]
        eng._one_turn = AsyncMock(return_value={"ok": True, "agentId": "agent-1"})  # type: ignore[method-assign]
        with patch.dict("os.environ", {"CURSOR_API_KEY": "k"}), patch("reverse_api.cursor_engineer._ensure_cursor_bridge_deps", return_value=None):
            out = await eng.analyze_and_generate()
        assert out is not None and "script_path" in out
        first = eng._one_turn.await_args_list[0]
        assert first.args[0] == "now add pagination" and first.kwargs["resume_agent_id"] == "agent-1"

    @pytest.mark.asyncio
    async def test_a_new_run_sends_the_brief_and_records_the_agent(self, har_path, tmp_path):
        eng = _cursor(har_path, tmp_path)
        eng.interactive = False
        seen: list[str] = []
        eng.on_session = seen.append

        async def one_turn(prompt, *, mcp_servers, resume_agent_id):
            eng._record_session("agent-9")  # what the bridge's early "agent" line does
            return {"ok": True, "agentId": "agent-9"}

        eng._one_turn = AsyncMock(side_effect=one_turn)  # type: ignore[method-assign]
        with patch.dict("os.environ", {"CURSOR_API_KEY": "k"}), patch("reverse_api.cursor_engineer._ensure_cursor_bridge_deps", return_value=None):
            await eng.analyze_and_generate()
        first = eng._one_turn.await_args_list[0]
        assert first.kwargs["resume_agent_id"] is None and "jobs" in first.args[0] and len(first.args[0]) > 200
        assert seen == ["agent-9"]


class TestCopilot:
    def _eng(self, har_path, tmp_path):
        from reverse_api.copilot_engineer import CopilotEngineer

        with patch("reverse_api.base_engineer.get_scripts_dir", return_value=tmp_path / "scripts"), patch("reverse_api.base_engineer.MessageStore"):
            return CopilotEngineer(run_id="abc123", har_path=har_path, prompt="jobs", copilot_model="gpt-5", sdk="copilot", verbose=False)

    @pytest.mark.asyncio
    async def test_resume_reopens_the_saved_session(self, har_path, tmp_path):
        eng = self._eng(har_path, tmp_path)
        seen: list[str] = []
        eng.on_session = seen.append
        client = MagicMock()
        client.resume_session = AsyncMock(return_value=MagicMock(session_id="cp-1"))
        client.create_session = AsyncMock()
        eng.resume_session = "cp-1"
        await eng._open_copilot_session(client, {"model": "gpt-5"})
        client.resume_session.assert_awaited_once_with("cp-1", {"model": "gpt-5"})
        client.create_session.assert_not_called()
        assert seen == ["cp-1"]

    @pytest.mark.asyncio
    async def test_a_new_run_creates_one_and_records_it(self, har_path, tmp_path):
        eng = self._eng(har_path, tmp_path)
        seen: list[str] = []
        eng.on_session = seen.append
        client = MagicMock()
        client.create_session = AsyncMock(return_value=MagicMock(session_id="cp-2"))
        await eng._open_copilot_session(client, {})
        assert seen == ["cp-2"]

    @pytest.mark.asyncio
    async def test_ending_a_resume_before_a_message_sends_nothing(self, har_path, tmp_path):
        pytest.importorskip("copilot")
        eng = self._eng(har_path, tmp_path)
        eng.resume_session = "cp-1"
        eng._prompt_follow_up = AsyncMock(return_value=None)  # type: ignore[method-assign]
        with patch("copilot.CopilotClient") as client:
            assert await eng.analyze_and_generate() == {}
        client.assert_not_called()


class TestOpenCode:
    def _eng(self, har_path, tmp_path):
        from reverse_api.opencode_engineer import OpenCodeEngineer

        with (
            patch("reverse_api.base_engineer.get_scripts_dir", return_value=tmp_path / "scripts"),
            patch("reverse_api.base_engineer.MessageStore"),
            patch("reverse_api.opencode_engineer.OpenCodeUI"),
        ):
            return OpenCodeEngineer(run_id="abc123", har_path=har_path, prompt="jobs", output_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_resume_checks_the_saved_session_instead_of_creating_one(self, har_path, tmp_path):
        eng = self._eng(har_path, tmp_path)
        eng.resume_session = "ses_1"
        client = MagicMock()
        client.get = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
        client.post = AsyncMock()
        assert await eng._open_session(client) == "ses_1"
        client.get.assert_awaited_once_with("/session/ses_1")
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_new_run_creates_a_session_and_records_it(self, har_path, tmp_path):
        eng = self._eng(har_path, tmp_path)
        seen: list[str] = []
        eng.on_session = seen.append
        response = MagicMock(raise_for_status=MagicMock())
        response.json.return_value = {"id": "ses_2"}
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        assert await eng._open_session(client) == "ses_2"
        assert seen == ["ses_2"]


class TestCli:
    @pytest.fixture
    def history(self, monkeypatch):
        sm = MagicMock()
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {"sdk": "claude", "real_time_sync": True}.get(key, default)
        monkeypatch.setattr(cli, "session_manager", sm)
        monkeypatch.setattr(cli, "config_manager", config)
        return sm

    def test_sessions_are_recorded_with_their_sdk(self, history):
        cli._session_recorder("r1", "agent", "cursor")("agent-1")
        history.update_run.assert_called_once_with(run_id="r1", sdk_session={"id": "agent-1", "mode": "agent", "sdk": "cursor"})

    def test_a_resume_uses_the_run_sdk_even_when_settings_changed(self):
        assert cli._session_sdk({"sdk_session": {"id": "s", "sdk": "copilot"}}) == "copilot"
        assert cli._session_sdk({"sdk_session": {"id": "s"}}) == "claude", "sessions saved before the sdk was recorded"
        assert cli._session_sdk(None) is None

    def test_run_engineer_reopens_a_cursor_session_with_cursor(self, history, tmp_path):
        run = {
            "run_id": "r1",
            "prompt": "jobs",
            "paths": {"har_dir": str(tmp_path)},
            "usage": {"estimated_cost_usd": 0.0},
            "sdk_session": {"id": "agent-1", "mode": "engineer", "sdk": "cursor"},
        }
        history.get_run.return_value = run
        with patch.object(cli, "run_reverse_engineering", return_value=None) as rre:
            cli.run_engineer("r1", resume=run)
        kwargs = rre.call_args.kwargs
        assert kwargs["sdk"] == "cursor" and kwargs["resume_session"] == "agent-1"
        kwargs["on_session"]("agent-1")
        history.update_run.assert_called_with(run_id="r1", sdk_session={"id": "agent-1", "mode": "engineer", "sdk": "cursor"})

    def test_run_reverse_engineering_hands_the_session_to_any_engine(self, har_path):
        from reverse_api.engineer import run_reverse_engineering

        record = MagicMock()
        engine = MagicMock()
        engine.analyze_and_generate = AsyncMock(return_value={"script_path": "x"})
        with patch("reverse_api.cursor_engineer.CursorEngineer", return_value=engine):
            run_reverse_engineering(run_id="r1", har_path=har_path, prompt="p", sdk="cursor", resume_session="agent-1", on_session=record)
        assert engine.resume_session == "agent-1" and engine.on_session is record


def test_opencode_resume_runs_in_the_classic_screen(monkeypatch):
    from reverse_api.repl.app import Session

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: default
    history = MagicMock()
    history.get_run.return_value = {"run_id": "r1", "prompt": "jobs", "sdk_session": {"id": "ses_1", "mode": "agent", "sdk": "opencode"}}
    monkeypatch.setattr(cli, "config_manager", config)
    monkeypatch.setattr(cli, "session_manager", history)
    monkeypatch.setattr(cli, "default_model_for_configured_sdk", lambda sdk: "claude-sonnet-5-5")
    session = Session()
    session.app = MagicMock()
    with patch.object(session, "work") as work:
        session.run_command("/resume r1")
    work.assert_not_called()
    session.app.exit.assert_called_once()
    assert session.take_classic_job() is not None
