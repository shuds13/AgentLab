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

The repository's pixi environment includes LiteLLM, its proxy dependencies, PostgreSQL,
and Prisma. Create the local config from the template, then start the proxy and its
local database from the repository root:

```
cp litellm/config.yaml.template litellm/config.yaml
pixi install
pixi run litellm-proxy-start
```

The config is not tracked because it contains credentials. It names the models, endpoints,
and keys to reach them. The relevant settings for an OpenAI-style upstream are already in
the template:

```yaml
litellm_settings:
  use_chat_completions_url_for_anthropic_messages: true
  drop_params: true
```

`use_chat_completions_url_for_anthropic_messages` sends the translated request to
`/v1/chat/completions` rather than the OpenAI Responses API. It is required when the
backend implements only chat completions. `drop_params` lets LiteLLM discard parameters
with no OpenAI equivalent, such as `context_management`; without it the first round can
fail with `UnsupportedParamsError`.

The launcher initializes PostgreSQL under `scratch/litellm-postgres`, uses TCP port 5433
for the database, and serves the proxy on port 4000. The database is local and gitignored.
It generates LiteLLM's Prisma client when needed and applies the packaged schema before
startup. Stop the foreground process with Ctrl-C, or stop both services from another
terminal with:

```
pixi run litellm-proxy-stop
```

Set `LITELLM_MASTER_KEY` and `LITELLM_SALT_KEY` before starting when using this beyond
local development. The defaults are intentionally only suitable for a local machine.
Set any upstream credential variables referenced by `litellm/config.yaml` before starting.
Keep those credentials out of Git. Use `LITELLM_PORT`,
`LITELLM_DB_PORT`, or `LITELLM_PGDATA` to change the proxy port, database port, or
database directory. The proxy binds to `127.0.0.1` by default; set `LITELLM_HOST`
deliberately for remote access and use a strong master key.

To run the proxy as a lab service instead, set `litellm: on`, `litellm-bin`, and
`litellm-config` in `lab.yaml`, then use `bin/lab.sh start`. The lab launcher starts the
command configured by `litellm-bin`; it does not manage the local PostgreSQL service.
For the repository launcher, `LITELLM_CONFIG` selects the config file and defaults to
`litellm/config.yaml`.

Check the proxy before pointing the agent at it. `LITELLM_MASTER_KEY` authenticates the
Agent SDK to the proxy; the configured `api_key` authenticates LiteLLM to the upstream:

```
curl -s -X POST http://127.0.0.1:4000/v1/messages -H 'content-type: application/json' -H "x-api-key: $LITELLM_MASTER_KEY" -H 'anthropic-version: 2023-06-01' -d '{"model":"my-model","max_tokens":64,"messages":[{"role":"user","content":"say hi"}]}'
```

Then configure the SDK process (or its `~/.claude/settings.json`) with the proxy URL,
model, and the same proxy master key:

```sh
export ANTHROPIC_BASE_URL=http://127.0.0.1:4000
export ANTHROPIC_API_KEY="$LITELLM_MASTER_KEY"
export AGENT_MODEL=my-model
```

Use `127.0.0.1`, not `0.0.0.0`, as a client URL. `ANTHROPIC_API_KEY` should be the
LiteLLM master key when the proxy uses its configured upstream `api_key`; use caller
credentials only when the proxy is deliberately configured for that mode.

## Offering several models from one proxy

One proxy can front several models, so people running campaigns choose one by name and
install nothing. Add an entry for each model; the `litellm_settings` from the previous
example applies to all of them:

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
```

Where a gateway serves several vendors' models on one OpenAI-compatible endpoint, every
entry uses the `openai/` handler regardless of who made the model: the handler names the
wire format, not the vendor. Reserve `gemini/` and `anthropic/` for going to those vendors
directly.

Each person then names the model they want and authenticates to the proxy:

```json
{"env": {"ANTHROPIC_BASE_URL": "http://<proxy-host>:4000", "ANTHROPIC_API_KEY": "<proxy key>"}, "model": "gemini"}
```

Each model's configured `api_key` is used upstream by default. To attribute upstream
usage to each caller instead, configure LiteLLM explicitly for caller-provided credentials
and limit proxy access to the trusted network.

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
