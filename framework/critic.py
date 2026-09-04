#!/usr/bin/env python3
"""
The critic: a second model that checks a cycle's write-up against the recorded results.

It is a plain request-and-reply, not an agent. The evidence it judges is chosen by the
framework and travels with the verdict, so a review can be read later and checked --
which is the point of having one. It cannot submit work, write files, or continue the
research.

Which model it is depends on what the lab can reach. When the agent talks to a gateway
serving several models (see docs/llm.md), `CRITIC_MODEL=auto` picks one from a different
family than the agent's own, because two instances of one model share their blind spots.
With no gateway there is only Claude, and a Claude critic still helps: no memory of the
reasoning, a different prompt, and only the rows to go on.

Resolved once at startup, never mid-run: a critic that vanishes between rounds is worse
than one you knew you did not have.

Env:
    CRITIC_MODEL         model name, `auto`, or unset for no critic
    CRITIC_REQUIRED      1/true to refuse to start when no critic can be resolved
    CRITIC_LEVEL         full (every claim) or light (only claims resting on a number)
    CRITIC_PROMPT_FILE   a prompt of your own, instead of either
    CRITIC_MAX_TOKENS    reply cap (default 8000; a reasoning model spends most of it
                         thinking, so a tight cap returns an empty reply)
    CRITIC_BASE_URL      gateway serving the critic's model, if not the agent's own
    CRITIC_API_KEY       credential for it, or CRITIC_API_KEY_FILE holding one
    CRITIC_GATEWAY_START command that starts that gateway, when the lab runs one of its
                         own (see docs/llm.md). Used only if it is not already up
    CRITIC_GATEWAY_WAIT  seconds to wait for it to answer (default 60)
"""

import json
import os
import re
import shlex
import subprocess
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_SETTING = (os.environ.get("CRITIC_MODEL") or "").strip()
REQUIRED = os.environ.get("CRITIC_REQUIRED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
MAX_TOKENS = int(os.environ.get("CRITIC_MAX_TOKENS", "8000"))
# Usually the agent's own gateway, with a different model on it. Separable because the
# common arrangement is the reverse of that: the agent on Claude directly, and a proxy
# standing alongside purely to reach a second family for review.
BASE_URL = (
    os.environ.get("CRITIC_BASE_URL")
    or os.environ.get("ANTHROPIC_BASE_URL")
    or "https://api.anthropic.com"
).rstrip("/")


def _claude_key():
    """Whatever Claude Code itself authenticates with. A lab reaching a second model
    usually reaches it through the same gateway the agent uses, with the same
    credential, and that credential is already configured -- asking a person to write
    it out again invites a second, staler copy of it."""
    cfg = os.path.join(
        os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")),
        "settings.json",
    )
    try:
        with open(cfg) as f:
            settings = json.load(f)
    except Exception:
        return ""
    key = (settings.get("env") or {}).get("ANTHROPIC_API_KEY")
    if key:
        return key
    helper = settings.get("apiKeyHelper")
    if helper:
        try:
            out = subprocess.run(
                helper, shell=True, capture_output=True, text=True, timeout=15
            )
            return out.stdout.strip()
        except Exception as e:
            print(f"[critic] apiKeyHelper failed (ignored): {e}", flush=True)
    return ""


def _key():
    """The critic's credential, in the order that needs the least of anyone: one given
    for the critic, a file holding one, the agent's own from the environment, or what
    Claude Code is already configured to use."""
    path = (os.environ.get("CRITIC_API_KEY_FILE") or "").strip()
    if path:
        try:
            with open(os.path.expanduser(path)) as f:
                return f.read().strip()
        except OSError as e:
            print(
                f"[critic] cannot read CRITIC_API_KEY_FILE ({e}); falling back",
                flush=True,
            )
    return (
        os.environ.get("CRITIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or _claude_key()
    )


API_KEY = _key()

BLOCK_RE = re.compile(
    r"CLAIM:\s*(?P<claim>.+?)\n\s*VERDICT:\s*(?P<verdict>\w+).*?"
    r"SEVERITY:\s*(?P<severity>\w+)",
    re.DOTALL | re.IGNORECASE,
)


class CriticUnavailable(Exception):
    """No critic could be resolved and the campaign asked for one."""


def _prompt_text():
    """What the critic is asked to do. The level picks how much of a write-up is in
    scope: everything it asserts, or only what a recorded number can settle."""
    level = (os.environ.get("CRITIC_LEVEL") or "full").strip().lower()
    path = os.environ.get("CRITIC_PROMPT_FILE") or os.path.join(
        SCRIPT_DIR, f"critic_prompt_{level}.md"
    )
    try:
        with open(path) as f:
            return f.read()
    except OSError as e:
        raise CriticUnavailable(
            f"no critic prompt at {path} (CRITIC_LEVEL={level}): {e}"
        )


def gateway_up():
    """Whether the critic's gateway answers. Cheap, and the only thing worth asking
    before starting one."""
    try:
        urllib.request.urlopen(BASE_URL + "/health/liveliness", timeout=5).read()
        return True
    except Exception:
        return bool(_served_models())


def ensure_gateway(needed=False):
    """Start the lab's model gateway if something needs it and nothing is serving.

    The gateway converts between the Messages API and a backend that does not speak
    it, so it is needed by whoever is on a model behind it -- the critic, the agent,
    or both. `needed` is the caller's own claim on it; a critic asks for itself.

    A proxy is a lab's own thing -- its path, its config, its port -- so the lab says
    how to start it and this only decides whether it needs starting. Returns a line
    about what happened, or None when there was nothing to do."""
    start = (os.environ.get("CRITIC_GATEWAY_START") or "").strip()
    if not (MODEL_SETTING or needed) or not start or gateway_up():
        return None
    wait = int(os.environ.get("CRITIC_GATEWAY_WAIT", "60"))
    try:
        subprocess.Popen(
            shlex.split(start),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return f"could not start the model gateway (ignored): {e}"
    for _ in range(wait):
        time.sleep(1)
        if gateway_up():
            return f"started the model gateway at {BASE_URL}"
    return (
        f"started the model gateway but {BASE_URL} did not answer within "
        f"{wait}s; continuing without it"
    )


def _served_models():
    """Model names the gateway offers, with what each resolves to upstream. Empty when
    there is no gateway -- talking to Anthropic directly means Claude only."""
    try:
        req = urllib.request.Request(
            BASE_URL + "/model/info", headers={"x-api-key": API_KEY}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r).get("data", [])
    except Exception:
        return {}
    out = {}
    for m in data:
        name = m.get("model_name")
        if name:
            out[name] = (m.get("litellm_params", {}).get("model", "") or "").split("/")[
                -1
            ]
    return out


def resolve(agent_model=""):
    """Pick the critic model, once, at startup. Returns (name, label) or (None, reason)."""
    if not MODEL_SETTING:
        return None, "none"
    served = _served_models()
    if MODEL_SETTING != "auto":
        if served and MODEL_SETTING not in served:
            raise CriticUnavailable(
                f"CRITIC_MODEL={MODEL_SETTING} is not served by {BASE_URL} "
                f"(it has: {', '.join(sorted(served)) or 'nothing'})"
            )
        return MODEL_SETTING, served.get(MODEL_SETTING) or MODEL_SETTING
    if not served:
        if REQUIRED:
            raise CriticUnavailable(
                f"CRITIC_MODEL=auto but {BASE_URL} lists no models, so there is nothing "
                "to choose from"
            )
        return None, "none (asked for one, but no second model is reachable)"
    # A different family than the agent's own, where there is one.
    agent_family = (agent_model or "").split("-")[0].lower()
    for name, upstream in sorted(served.items()):
        if agent_family and agent_family in (upstream or name).lower():
            continue
        return name, upstream or name
    name, upstream = sorted(served.items())[0]
    return name, upstream or name


def review(model, write_up, evidence, prompt=None):
    """Send one piece of work to a reviewing model. Returns its raw reply; never raises.

    `prompt` overrides what the reviewer is asked to do, so the same request-and-reply
    serves a cycle review and the campaign review before a run."""
    if prompt is not None:
        prompt = prompt + "\n\n" + write_up
    else:
        prompt = (
            _prompt_text()
            + "\n\n# The write-up\n\n"
            + write_up
            + "\n\n# The recorded results\n\n"
            + (evidence or "(no rows recorded)")
        )
    body = json.dumps(
        {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        BASE_URL + "/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            reply = json.load(r)
        text = "".join(b.get("text", "") for b in reply.get("content", [])).strip()
        # A reasoning model can spend the whole budget thinking and return nothing.
        # Silence and truncation look identical from here, so say which it was.
        if not text and reply.get("stop_reason") == "max_tokens":
            print(
                f"[critic] no review: the reply hit CRITIC_MAX_TOKENS "
                f"({MAX_TOKENS}) before writing anything",
                flush=True,
            )
        return text
    except Exception as e:
        print(f"[critic] review failed (ignored): {e}", flush=True)
        return ""


def blocking(reply):
    """The blocking findings in a reply, as (claim, verdict) pairs. A reply that does
    not parse yields none: a critic that ignored the format does not get to halt a run
    on the strength of prose nobody can act on."""
    out = []
    for m in BLOCK_RE.finditer(reply or ""):
        if m.group("severity").strip().lower() == "blocking":
            out.append((m.group("claim").strip(), m.group("verdict").strip().lower()))
    return out
