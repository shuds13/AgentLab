#!/usr/bin/env python3
"""
Read lab.yaml -- what this lab runs, and where its own things are.

The file is meant to be read at a glance by whoever runs the lab, so it is a flat list
of `name: value`. This turns it into the environment variables the framework already
uses, which is the only reason it needs a reader at all.

    eval "$(python3 framework/lab_config.py --export)"     from a shell
    python3 framework/lab_config.py --get gateway          one value, for a script

A missing file is not an error: a lab that runs nothing and points at nothing is a
valid lab, and every setting has a default elsewhere.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", os.path.join(SCRIPT_DIR, "..")))
PATH = os.path.join(LAB_DIR, "lab.yaml")

# lab.yaml -> the environment the framework reads. Names on the left are what a person
# sees; names on the right are what the code has always called them. CRITIC_GATEWAY_START
# is not among them: it is built, below, out of the bin, the config and the port.
AS_ENV = {
    "activate": "LAB_ACTIVATE",
    "litellm-url": "CRITIC_BASE_URL",
    "litellm-config": "LITELLM_CONFIG",
    "litellm-key-file": "CRITIC_API_KEY_FILE",
    "slack-channel": "SLACK_CHANNEL",
    "engineer-slack-channel": "ENGINEER_SLACK_CHANNEL",
    "engineer-webhook-file": "ENGINEER_WEBHOOK_FILE",
    "engineer-allow": "ENGINEER_ALLOW",
    "engineer-resume": "ENGINEER_RESUME",
    "startable-campaigns": "SLACK_CAMPAIGNS",
}
SERVICES = ("bridge", "secretary", "engineer", "litellm")

# Values naming a file, so a relative one can be written against the lab rather than
# against whichever directory a command happened to run in.
PATHS = ("litellm-bin", "litellm-config", "litellm-key-file", "engineer-webhook-file")


def load(path=PATH):
    """The file as a dict. Flat `name: value` only -- no nesting, no lists, no types:
    anything that needs those belongs in a campaign or in the code, not here."""
    out = {}
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return out
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), os.path.expanduser(value.strip())
        if value and key in PATHS and not os.path.isabs(value):
            value = os.path.join(LAB_DIR, value)
        out[key] = value
    return out


def start_command(config=None):
    """What starts the proxy. LiteLLM takes the rest of its settings from its own
    config, so the command is only ever these three parts -- which is why lab.yaml
    names them separately instead of holding a line of shell."""
    config = config if config is not None else load()
    binary, conf = config.get("litellm-bin", ""), config.get("litellm-config", "")
    if not binary or not conf:
        return ""
    _, _, port = config.get("litellm-url", "").rpartition(":")
    command = f"{binary} --config {conf}"
    return f"{command} --port {port}" if port.isdigit() else command


def on(name, config=None):
    """Whether a service is switched on. `when-needed` is not on: it means something
    else starts it at the moment it is wanted."""
    return (config or load()).get(name, "off").lower() in ("on", "true", "yes", "1")


def main():
    config = load()
    if "--get" in sys.argv:
        print(config.get(sys.argv[sys.argv.index("--get") + 1], ""))
        return
    if "--services" in sys.argv:
        print(" ".join(s for s in SERVICES if on(s, config)))
        return
    # --export: only what is set, and never over something already in the environment,
    # so a value given on the command line still wins.
    for key, env in list(AS_ENV.items()) + [(None, "CRITIC_GATEWAY_START")]:
        value = start_command(config) if key is None else config.get(key, "")
        if value:
            print(f'{env}="${{{env}:-{value}}}"; export {env}')


if __name__ == "__main__":
    main()
