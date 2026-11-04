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

## The ALCF inference service

[ALCF's inference endpoints](https://docs.alcf.anl.gov/services/inference-endpoints/)
serve open models on facility hardware, across three clusters. Which one a model is on
decides how a campaign reaches it:

| Cluster | Serves | Path here |
|---|---|---|
| Minerva (B200) | Messages API, among others | directly, as above |
| Sophia (A100, vLLM) | chat completions | through LiteLLM |
| Metis (SambaNova SN40L) | chat completions | through LiteLLM |

Minerva is the one that needs no translation layer, so a model there is the simplest
case in this document. `nemotron-3-ultra` is on Minerva, and
`MODEL=nemotron campaigns/example-quick-optimum/run_open_model.sh` runs against it.

### Authenticating

The service has no static API key. It authenticates with Globus, and the OAuth access
token goes wherever a key would. ALCF's helper script does the exchange; fetch it once
and run it in the lab's environment, which is where a campaign will call it from:

```
wget -O ~/inference_auth_token.py https://raw.githubusercontent.com/argonne-lcf/inference-endpoints/refs/heads/main/inference_auth_token.py && python3 ~/inference_auth_token.py authenticate
```

It needs `globus_sdk`, which `globus-compute-sdk` in `requirements.txt` already brings
in, so there is nothing to install. It prints a URL to authorise in a browser and
writes tokens under `~/.globus/app/`. An access token lasts 48
hours and `get_access_token` refreshes an expired one by itself; ALCF requires
re-authentication every 30 days, with `--force`.

The token is a bearer credential, so it has to arrive as `ANTHROPIC_AUTH_TOKEN`.
`apiKeyHelper` and `ANTHROPIC_API_KEY` send `x-api-key` instead, which the service
answers with 401. Fetch it in the campaign's `run.sh`, so nothing is stored and each
run gets a current one:

```
export ANTHROPIC_AUTH_TOKEN="$(python3 ~/inference_auth_token.py get_access_token)"
```

A run lasting more than 48 hours outlives its token; there is no in-flight refresh,
since the mechanism for one sends the wrong header.

The settings file holds the endpoint and the models it serves; `AGENT_MODEL` picks one:

```json
{
  "env": {"ANTHROPIC_BASE_URL": "https://inference-api.alcf.anl.gov/resource_server/minerva/api"},
  "modelPicker": {"options": [
    {"model": "muse-spark-bf16", "label": "Muse Spark", "behavesAs": "claude-opus-5"},
    {"model": "nemotron-3-ultra", "label": "Nemotron Ultra", "behavesAs": "claude-opus-5"}]}
}
```

Two details in that file are easy to get wrong, and both fail as "There's an issue with
the selected model", which names neither cause:

- The base URL stops at `/api`. Claude Code appends `/v1/messages` itself, so ALCF's
  published URL -- which ends in `/v1` -- produces `/v1/v1/messages` and a 404.
- `modelPicker` is what makes an unknown model selectable at all. Claude Code offers
  only models in its own catalog, and `behavesAs` names a model it does know whose
  client-side handling to borrow. The model ID sent upstream is unchanged. Without the
  row the model is refused before any request is made.

`behavesAs` also settles the context window, which would otherwise default to 200k
regardless of the model's own. `CLAUDE_CODE_MAX_CONTEXT_TOKENS` sets it explicitly.

Point `CLAUDE_CONFIG_DIR` at a directory holding that file, as under "A different
model for one campaign" below, and the rest of the lab keeps whatever endpoint it was
using. That directory is Claude Code's whole config home, not just a settings file --
it accumulates transcripts, debug logs and caches -- so a campaign is tidier staging
it into the workspace at launch than keeping it beside the task:

```
export CLAUDE_CONFIG_DIR="$WORKSPACE_DIR/claude"
mkdir -p "$CLAUDE_CONFIG_DIR"
cp alcf.settings.json "$CLAUDE_CONFIG_DIR/settings.json"
export AGENT_MODEL=nemotron-3-ultra
```

`campaigns/example-quick-optimum/run_open_model.sh`, with `alcf.settings.json` beside it, is a
working example.

A model on Sophia or Metis is the LiteLLM case instead: those clusters serve the OpenAI
chat completions API rather than the Messages API the agent speaks, so the proxy
translates. `litellm/config.yaml.template` has an entry for Metis, which reaches it with
the same access token, held in `ALCF_INFERENCE_API_KEY` where the proxy runs.

## A model that is not Claude, through LiteLLM

[LiteLLM](https://docs.litellm.ai) exposes a `/v1/messages` endpoint in Anthropic
format and routes it to any provider it supports. Running it in front of an
OpenAI-style backend lets a campaign run on that backend through the same SDK path.

Install it in its own environment, since its proxy extra pulls in a large dependency
set:

```
python -m venv ~/venvs/litellm && ~/venvs/litellm/bin/pip install "litellm[proxy]"
```

Tested with LiteLLM 1.106.6 and FastAPI 0.162.0.

Write the config that names each model, its upstream and the key to reach it. It
belongs at the top of the lab, where anyone can see which models are on offer:

```
cp litellm/config.yaml.template litellm/config.yaml
```

That copy is not tracked by git, since keys may be in it.

```yaml
model_list:
  - model_name: my-model
    litellm_params:
      model: custom_openai/<upstream-model-name>
      api_base: https://backend.example/v1
      api_key: <key>

litellm_settings:
  drop_params: true
```

The prefix of `model` is the API LiteLLM speaks to the upstream, not who made the model.
`custom_openai/` is OpenAI's chat completions, which nearly every OpenAI-style backend
serves. `openai/` is taken to be OpenAI itself and gets the newer Responses API, which a
backend that implements only `/v1/chat/completions` answers with a 404. Choosing per
entry lets one proxy serve backends that want different APIs.

`drop_params` is needed because the agent sends parameters an OpenAI backend has no
equivalent for -- `context_management` among them -- and LiteLLM refuses the request
rather than dropping them, so the first turn fails with `UnsupportedParamsError` on a
proxy that otherwise works.

The proxy has a key of its own, without which LiteLLM will not start. Put one in a file
only you can read, and name the file in `lab.yaml`:

```
(umask 077; echo "sk-$(openssl rand -hex 32)" > ~/.litellm_key)
```

```
litellm-key-file: ~/.litellm_key
```

`bin/lab.sh start` starts the proxy from the `litellm` lines in `lab.yaml`, with that
key. Check it with a model from the config:

```
curl -s -X POST http://0.0.0.0:4000/v1/messages -H 'content-type: application/json' -H "x-api-key: $(cat ~/.litellm_key)" -H 'anthropic-version: 2023-06-01' -d '{"model":"my-model","max_tokens":64,"messages":[{"role":"user","content":"say hi"}]}'
```

A run whose `AGENT_MODEL` is a `model_name` in the config is sent through the proxy, and
given its key, with nothing else to set: `AGENT_MODEL=my-model ./run.sh`.

Give each entry the model's context window, under `model_info`, as `max_input_tokens:
1000000`. Claude Code cannot tell the window of a model it does not know, so it assumes
200K and compacts the conversation there; the framework passes the entry's window on to
the run. The upstream
gets the `api_key` from the config, so the proxy should only be reachable by people
entitled to use that key.

## Argo

Argo, Argonne's model gateway, serves GPT and Gemini models on an OpenAI-style chat
endpoint and takes the caller's ANL username as the key. It differs from the OpenAI API
in two ways, which `litellm/argo.py`, a LiteLLM hook, makes up for: it reads the
request's `user` field as the username, where the agent puts its session id, and some of
its models take a message's content only as a string, not as a list of parts.

`litellm/argo.yaml` holds the Argo models and the hook, for a lab's `config.yaml` to
include:

```yaml
include:
  - argo.yaml
```

`curl -s https://apps.inside.anl.gov/argoapi/v1/models -H "Authorization: Bearer $USER"`
lists the model names Argo serves.

Tested with the agent: GPT-5.6 Sol and Gemini 4 Argon. GPT-6 Astra and GPT-6.1 Sol
refuse the agent's tool calls on the chat endpoint ("Chat-to-Responses translation:
messages.content for 'user' role must be a string"): Argo converts chat requests to its
Responses API for them and accepts only string content, while a conversation with tool
results has content as a list.

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
