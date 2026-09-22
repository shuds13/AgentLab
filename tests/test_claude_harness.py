import asyncio
import importlib


def test_engineer_answer_records_query_and_session(
    fake_claude_sdk, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    monkeypatch.setenv("SLACK_INBOX", str(tmp_path / "run" / "inbox.md"))
    sys_module = importlib.import_module("sys")
    sys_module.modules.pop("engineer", None)
    monkeypatch.syspath_prepend(str(tmp_path.parent / "AgentLab" / "framework"))
    engineer = importlib.import_module("framework.engineer")
    engineer.SESSION_FILE = str(tmp_path / "run" / "session")
    fake_claude_sdk.plan_client(
        turns=[
            fake_claude_sdk.turn(
                fake_claude_sdk.assistant(fake_claude_sdk.text("answer")),
                fake_claude_sdk.result("success", "session-1"),
                expected_query="question\n\nAnswer it, then stop.",
            )
        ]
    )
    client = fake_claude_sdk.module.ClaudeSDKClient(options=object())

    asyncio.run(engineer.answer(client, "question"))

    assert fake_claude_sdk.last_client.queries == ["question\n\nAnswer it, then stop."]
    assert (tmp_path / "run" / "session").read_text() == f"session-1\n{tmp_path}\n"
    assert "answer" in capsys.readouterr().out


def test_secretary_answer_includes_live_agent_status(
    fake_claude_sdk, monkeypatch, tmp_path
):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    sys_module = importlib.import_module("sys")
    sys_module.modules.pop("framework.secretary", None)
    secretary = importlib.import_module("framework.secretary")
    (tmp_path / "run").mkdir()
    fake_claude_sdk.plan_client(
        turns=[
            fake_claude_sdk.turn(
                fake_claude_sdk.result("success", "session-2"),
                expected_query=lambda prompt: (
                    "Research agents running:\nagent1" in prompt
                ),
            )
        ]
    )
    client = fake_claude_sdk.module.ClaudeSDKClient(options=object())

    asyncio.run(secretary.answer(client, "status?", ["agent1 -- campaign demo"]))

    assert "New from Slack:\n\nstatus?" in fake_claude_sdk.last_client.queries[0]
    assert (
        tmp_path / "run" / "secretary_session"
    ).read_text() == f"session-2\n{tmp_path}\n"
