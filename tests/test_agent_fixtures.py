import asyncio
import importlib
import sys


def test_agent_drain_turn_tracks_delegate_phase_and_session(
    tools_lab, fake_claude_sdk, monkeypatch, capsys
):
    lab = tools_lab(remote=False, local=True)
    sys.modules.pop("agent", None)
    agent = importlib.import_module("agent")
    agent.RUN_DIR = str(lab.workspace / "runs" / "test")
    (lab.workspace / "runs" / "test").mkdir(parents=True)
    phases = []
    monkeypatch.setattr(agent, "_set_phase", phases.append)
    fake_claude_sdk.plan_client(
        turns=[
            fake_claude_sdk.turn(
                fake_claude_sdk.assistant(
                    fake_claude_sdk.tool_use(
                        "Agent", {"subagent_type": "reader"}, "call-1"
                    ),
                ),
                fake_claude_sdk.assistant(
                    fake_claude_sdk.tool_use("mcp__cas__submit_local"),
                    parent_tool_use_id="call-1",
                ),
                fake_claude_sdk.result("success", "agent-session"),
            )
        ],
        contexts=[None],
    )
    client = fake_claude_sdk.module.ClaudeSDKClient(options=object())

    async def run():
        await client.query("turn")
        await agent.drain_turn(client, 2)
        await agent._context_task

    asyncio.run(run())

    assert any("waiting on subagent reader" in phase for phase in phases)
    assert any("subagent reader (submit_local)" in phase for phase in phases)
    assert agent._session_id == "agent-session"
    assert "[round 2 turn end] success" in capsys.readouterr().out
