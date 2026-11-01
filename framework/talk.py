"""Talk with the agent of a run that has ended.

The run's own Claude session is reopened as it ran -- same working directory, Claude
config, settings and sandbox, same model and effort -- and forked, so the run's record
is left as it was. The agent keeps everything it saw and did, and is told the run is
over: it has its file tools for looking at what it did, and none of the job tools, so a
conversation cannot spend the run's budget or carry the run on.

The fork is kept in the run's directory as talk.json, so a later message carries on the
same conversation.

    python talk.py <run dir>                 # a conversation in the terminal
    python talk.py <run dir> -m "message"    # one message; the reply goes to stdout
    python talk.py <run dir> --new ...       # start a fresh fork of the run's session
    python talk.py <run dir> -m "..." --record <MESSAGES.md>   # as the watch's chat does
"""

import argparse
import asyncio
import fcntl
import gc
import glob
import io
import json
import os
import sys
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

# Looking, not doing: the run's results, records and code are files.
TALK_TOOLS = ["Read", "Glob", "Grep"]

TALK_NOTE = (
    "Your run has ended. This is a conversation about it with the person who runs this "
    "lab. Your job tools are gone, and you can read files but not change them; answer "
    "from what you did and saw during the run, and look at its files where that helps.")


def read_meta(run_dir):
    with open(os.path.join(run_dir, "meta.json")) as f:
        return json.load(f)


def config_dir(meta, given):
    """The Claude config directory the run kept its session in: the one it recorded,
    else the one given, else the default."""
    return (meta.get("claude_config_dir") or given or os.environ.get("CLAUDE_CONFIG_DIR")
            or os.path.expanduser("~/.claude"))


def has_session(config, session_id):
    """Whether the config directory holds the session, under projects/<its directory>."""
    return bool(glob.glob(os.path.join(glob.escape(config), "projects", "*",
                                       f"{session_id}.jsonl")))


def talk_state(run_dir):
    try:
        with open(os.path.join(run_dir, "talk.json")) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_talk_state(run_dir, state):
    with open(os.path.join(run_dir, "talk.json"), "w") as f:
        json.dump(state, f, indent=2)


def options_for(run_dir, meta, config, resume, fork):
    """The run's session options, with the file tools only."""
    instructions = os.path.join(run_dir, "instructions.md")
    system_prompt = (open(instructions).read() + "\n\n# Now\n" + TALK_NOTE
                     if os.path.exists(instructions) else TALK_NOTE)
    settings = os.path.join(run_dir, "agent_settings.json")
    cli = os.path.join(run_dir, "agent_cli.sh")
    return ClaudeAgentOptions(
        tools=TALK_TOOLS,
        **({"model": meta["model"]} if meta.get("model") else {}),
        **({"effort": meta["effort"]} if meta.get("effort") else {}),
        **({"settings": settings} if os.path.exists(settings) else {}),
        # A sandboxed run left the command that starts its CLI in the box.
        **({"cli_path": cli} if os.path.exists(cli) else {}),
        permission_mode="bypassPermissions",
        system_prompt=system_prompt,
        cwd=meta.get("session_cwd") or meta.get("cwd"),
        env={"CLAUDE_CONFIG_DIR": config},
        resume=resume,
        fork_session=fork,
    )


async def say(client, text, out):
    """Send one message and write the reply as it comes. Returns the session id."""
    await client.query(text)
    sid = None
    async for m in client.receive_response():
        if isinstance(m, SystemMessage) and m.subtype == "init":
            sid = m.data.get("session_id") or sid
        elif isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, TextBlock):
                    out.write(b.text + "\n")
                    out.flush()
        elif isinstance(m, ResultMessage):
            sid = getattr(m, "session_id", None) or sid
            if m.is_error:
                out.write(f"[error: {m.result}]\n")
    return sid


class Refused(Exception):
    """A reason this run cannot be talked to."""


def alive(run_dir, within=600):
    """Whether the run's agent is still going: a recent heartbeat, as the watch judges it."""
    try:
        with open(os.path.join(run_dir, "heartbeat")) as f:
            return time.time() - float(f.read().strip()) < within
    except (OSError, ValueError):
        return False


def record(path, who, text):
    """One line of a run's conversation, in the format the watch reads."""
    with open(path, "a") as f:
        f.write(f"`{time.strftime('%H:%M')}` **{who}** {text.strip()}\n\n")


async def converse(a, run_dir, meta, out):
    if alive(run_dir):
        raise Refused("this run is still going; it reads messages between its turns.")
    if not meta.get("session_id"):
        raise Refused("the run recorded no session.")
    state = {} if a.new else talk_state(run_dir)
    # The first message forks the run's session; later ones carry on the fork, which is
    # kept where the run's session was.
    if state.get("session_id"):
        resume, fork, config = state["session_id"], False, state["claude_config_dir"]
    else:
        resume, fork, config = meta["session_id"], True, config_dir(meta, a.config_dir)
    if not has_session(config, resume):
        raise Refused(f"session {resume} is not in {config}; give the run's Claude "
                      "config directory with --config-dir.")
    first = TALK_NOTE + "\n\n" if fork else ""

    async with ClaudeSDKClient(options=options_for(run_dir, meta, config, resume, fork)) as c:
        async def turn(text):
            nonlocal first
            sid = await say(c, first + text, out)
            first = ""
            if sid and sid != state.get("session_id"):
                state["session_id"] = sid
                state["forked_from"] = meta["session_id"]
                state["claude_config_dir"] = config
                save_talk_state(run_dir, state)

        if a.message:
            await turn(a.message)
            return
        print(f"Talking with {meta.get('handle') or meta.get('run_id')} "
              f"({meta.get('model', '')}). Ctrl-D to leave.")
        while True:
            try:
                text = input("> ").strip()
            except EOFError:
                print()
                return
            if text:
                await turn(text)


async def main():
    ap = argparse.ArgumentParser(description="Talk with the agent of a run that has ended.")
    ap.add_argument("run_dir", help="the run's directory: <workspace>/runs/<run id>")
    ap.add_argument("-m", "--message", help="send one message and print the reply")
    ap.add_argument("--record", metavar="FILE",
                    help="with -m: add the reply to this conversation file (MESSAGES.md) "
                         "instead of printing it")
    ap.add_argument("--new", action="store_true",
                    help="start a fresh fork of the run's session")
    ap.add_argument("--config-dir",
                    help="the run's Claude config directory, for a run that did not record it")
    a = ap.parse_args()
    if a.record and not a.message:
        ap.error("--record needs -m")

    run_dir = os.path.abspath(a.run_dir)
    meta = read_meta(run_dir)
    who = meta.get("handle") or "agent"
    # One conversation at a time per run: a second message waits for the reply to the
    # first, so both carry on the same fork.
    with open(os.path.join(run_dir, "talk.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        out = io.StringIO() if a.record else sys.stdout
        try:
            await converse(a, run_dir, meta, out)
        except Refused as e:
            if not a.record:
                sys.exit(f"talk: {e}")
            out.write(f"[cannot answer: {e}]")
        except Exception as e:
            if not a.record:
                raise
            out.write(f"[cannot answer: {type(e).__name__}: {e}]")
        if a.record:
            record(a.record, who, out.getvalue() or "[no reply]")


async def run():
    try:
        await main()
    finally:
        # The CLI's pipes are closed when their transport is collected; collect it while
        # the loop is still open, or closing them reports the loop as closed.
        gc.collect()
        await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(run())
