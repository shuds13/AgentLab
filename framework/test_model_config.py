import os

import pytest

from model_config import normalize_schema, resolve_agent_config


def test_legacy_schema_is_converted():
    schema = normalize_schema({"name": str, "count": int, "enabled": bool, "metadata": dict})
    assert schema["type"] == "object"
    assert schema["properties"]["count"] == {"type": "integer"}
    assert schema["properties"]["metadata"]["type"] == "object"


def test_explicit_schema_is_preserved():
    schema = {"type": "object", "properties": {"x": {"type": "number"}}}
    assert normalize_schema(schema) is schema


def test_openai_config_environment_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "override-model")
    monkeypatch.setenv("AGENT_BASE_URL", "https://override.example/v1")
    config = resolve_agent_config({"agent": {
        "provider": "openai", "model": "campaign-model",
        "base_url": "https://campaign.example/v1"
    }})
    assert config["model"] == "override-model"
    assert config["base_url"] == "https://override.example/v1"
    assert config["api"] == "chat_completions"


def test_claude_is_default(monkeypatch):
    for key in ("AGENT_PROVIDER", "AGENT_MODEL", "AGENT_BASE_URL", "AGENT_API", "AGENT_API_KEY_ENV"):
        monkeypatch.delenv(key, raising=False)
    assert resolve_agent_config({})["provider"] == "claude"


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unsupported agent provider"):
        resolve_agent_config({"agent": {"provider": "other"}})
