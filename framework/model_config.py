"""Provider-neutral model configuration and schema normalization."""

import json
import os


DEFAULT_PROVIDER = "claude"
DEFAULT_OPENAI_API = "chat_completions"


def resolve_agent_config(campaign_config):
    """Resolve campaign model settings with environment overrides.

    The returned shape is deliberately transport-neutral so a future LiteLLM
    adapter can consume it without changing campaign files.
    """
    configured = dict(campaign_config.get("agent", {}))
    provider = os.environ.get("AGENT_PROVIDER", configured.get("provider", DEFAULT_PROVIDER))
    model = os.environ.get("AGENT_MODEL", configured.get("model", ""))
    base_url = os.environ.get("AGENT_BASE_URL", configured.get("base_url"))
    api = os.environ.get("AGENT_API", configured.get("api", DEFAULT_OPENAI_API))
    api_key_env = os.environ.get("AGENT_API_KEY_ENV", configured.get("api_key_env"))

    if provider not in ("claude", "openai"):
        raise ValueError(f"unsupported agent provider: {provider!r}; use 'claude' or 'openai'")
    if provider == "openai" and api not in ("chat_completions",):
        raise ValueError(f"unsupported OpenAI API mode: {api!r}")
    if provider == "openai" and not model:
        raise ValueError("OpenAI agent configuration needs agent.model")
    if provider == "openai" and base_url == "":
        base_url = None

    return {
        "provider": provider,
        "model": model or None,
        "base_url": base_url,
        "api": api,
        "api_key_env": api_key_env or ("OPENAI_API_KEY" if provider == "openai" else None),
    }


def _type_schema(value):
    types = {str: "string", int: "integer", float: "number", bool: "boolean",
             dict: "object", list: "array"}
    if value in types:
        result = {"type": types[value]}
        if value is dict:
            result["additionalProperties"] = True
        return result
    raise TypeError(f"unsupported JOB_SCHEMA value: {value!r}")


def normalize_schema(schema):
    """Convert legacy type shorthand or validate an explicit JSON Schema."""
    if not isinstance(schema, dict):
        raise TypeError("JOB_SCHEMA must be a dict")
    if "type" in schema or "properties" in schema:
        json.dumps(schema)
        return schema
    properties = {name: _type_schema(value) for name, value in schema.items()}
    return {"type": "object", "properties": properties, "additionalProperties": True}
