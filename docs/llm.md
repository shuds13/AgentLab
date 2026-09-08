# Which LLM the agent uses

The agent runs on the Claude Agent SDK, which talks the Anthropic Messages API and
takes its configuration from Claude Code's settings. By default that is
`~/.claude/settings.json`, so each person's own setup applies with nothing to
configure per campaign.

Any endpoint serving the Messages API can be used instead, by pointing
`ANTHROPIC_BASE_URL` at it. That is how a campaign runs against a facility gateway
rather than the public API, and — with a translation layer in front — how it runs on
a model that is not Claude.

## A gateway that already serves the Messages API

Set the base URL and the model in `~/.claude/settings.json`:

```json
{
  "env": {"ANTHROPIC_BASE_URL": "https://gateway.example/api"},
  "model": "claude-opus-4-5"
}
```

Nothing in `framework/` changes. Tools, permissions and context handling are the
SDK's, unchanged.

## A model that is not Claude, through LiteLLM

[LiteLLM](https://docs.litellm.ai) exposes a `/v1/messages` endpoint in Anthropic
format and routes it to any provider it supports. Running it in front of an
OpenAI-style backend lets a campaign run on that backend through the same SDK path.

Install it in its own environment, since its proxy extra pulls in a large dependency
set:

```
python -m venv ~/venvs/litellm && ~/venvs/litellm/bin/pip install "litellm[proxy]" "fastapi<0.140.7"
```

The FastAPI cap is needed as of LiteLLM 1.97.0. LiteLLM imports
`fastapi.dependencies.utils.get_flat_dependant`, which FastAPI removed in 0.140.7, and
LiteLLM declares `fastapi>=0.136.3,<1.0` — no upper bound below the removal — so an
uncapped install takes a FastAPI the proxy cannot import. The working range is 0.136.3
to 0.140.6. Drop the cap once a LiteLLM release no longer needs it; keep it inside that
range rather than pinning further back, since below 0.136.3 is outside what LiteLLM
supports.

Write the config that names the upstream model, its endpoint, and the key to reach it.
It belongs at the top of the lab, where anyone can see which models are on offer:

```
cp litellm/config.yaml.template litellm/config.yaml
```

That copy is not tracked by git, since the keys are in it.

```yaml
model_list:
  - model_name: my-model
    litellm_params:
      model: openai/<upstream-model-name>
      api_base: https://backend.example/v1
      api_key: <key>

litellm_settings:
  use_chat_completions_url_for_anthropic_messages: true
  drop_params: true
```

`use_chat_completions_url_for_anthropic_messages` is required for an OpenAI-style
upstream. Without it LiteLLM translates `/v1/messages` to the OpenAI Responses API,
and a backend that implements only `/v1/chat/completions` answers 404.

`drop_params` is required for the same reason from the other direction. The agent sends
parameters an OpenAI backend has no equivalent for -- `context_management` among them --
and LiteLLM refuses the request rather than dropping them, so the first turn fails with
`UnsupportedParamsError` on a proxy that otherwise works.

Start the proxy. `bin/lab.sh start` does it from the `litellm` lines in `lab.yaml`, and
by hand it is:

```
~/venvs/litellm/bin/litellm --config litellm/config.yaml --port 4000
```

Check it before pointing the agent at it:

```
curl -s -X POST http://0.0.0.0:4000/v1/messages -H 'content-type: application/json' -H 'x-api-key: <key>' -H 'anthropic-version: 2023-06-01' -d '{"model":"my-model","max_tokens":64,"messages":[{"role":"user","content":"say hi"}]}'
```

Then point the settings at the proxy:

```json
{
  "env": {"ANTHROPIC_BASE_URL": "http://0.0.0.0:4000", "ANTHROPIC_API_KEY": "<key>"},
  "model": "my-model"
}
```

LiteLLM passes the caller's credential upstream, so `ANTHROPIC_API_KEY` has to be one
the backend accepts, not an arbitrary string. Where the backend authenticates by
username, that username is the value.

## Offering several models from one proxy

One proxy can front several models, so the people running campaigns choose one by name
and install nothing. Give each an entry:

```yaml
model_list:
  - model_name: gpt
    litellm_params:
      model: openai/<gpt-model-name>
      api_base: https://gateway.example/v1
      api_key: os.environ/GATEWAY_KEY
  - model_name: gemini
    litellm_params:
      model: openai/<gemini-model-name>
      api_base: https://gateway.example/v1
      api_key: os.environ/GATEWAY_KEY

litellm_settings:
  use_chat_completions_url_for_anthropic_messages: true
  drop_params: true
```

Where a gateway serves several vendors' models on one OpenAI-compatible endpoint, every
entry uses the `openai/` handler regardless of who made the model — the handler names
the wire format, not the vendor. Reserve `gemini/` and `anthropic/` for going to those
vendors directly.

Each person then names the model they want:

```json
{"env": {"ANTHROPIC_BASE_URL": "http://<proxy-host>:4000", "ANTHROPIC_API_KEY": "<their key>"}, "model": "gemini"}
```

LiteLLM passes the caller's credential upstream rather than substituting the one in the
config, so each person's own key reaches the backend and usage is attributed to them.
That also means the proxy should only be reachable from where those credentials are
already trusted.

Mixing an Anthropic-native upstream into the same config is untested here.
`use_chat_completions_url_for_anthropic_messages` applies proxy-wide, so a Claude model
reached through its native `/v1/messages` may need its own proxy, or a check that the
setting does not disturb it.

## A different model for one campaign

Settings are per user, not per campaign, so two campaigns on one machine share them.
To give one campaign its own, put a `settings.json` in a directory of its own and
point `CLAUDE_CONFIG_DIR` at that directory in the campaign's `run.sh`:

```
export CLAUDE_CONFIG_DIR="$PWD/claude"
```

The agent then reads that file instead of `~/.claude/settings.json`. A project-level
`.claude/settings.json` does not override the user's.

## What to expect from a non-Claude model

The SDK cannot tell what is behind the endpoint, so tools, permissions and compaction
work as they always do. Two things differ:

- Context reporting is partial. `totalTokens` is real; the window size falls back to a
  default when the model name is not one the CLI knows, so the percentage in the status
  line is measured against that default rather than the model's own window.
- Tool-calling quality is the model's own. A model that follows tool schemas poorly
  will submit poorly, and no translation layer changes that. Run one short campaign and
  read `jobs.jsonl` before committing a long one to a new model.

Anthropic documents that routing Claude Code to non-Claude models through a gateway is
outside what it supports. It works, and it is worth knowing that a Claude Code release
is not tested against it.
