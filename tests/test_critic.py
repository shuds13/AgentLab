import types


def test_resolve_explicit_and_auto(import_module, monkeypatch):
    critic = import_module("critic", {"CRITIC_MODEL": "auto"})
    monkeypatch.setattr(
        critic, "_served_models", lambda: {"gpt": "openai/gpt", "claude": "claude-3"}
    )
    assert critic.resolve("claude-sonnet")[0] == "gpt"

    critic.MODEL_SETTING = "missing"
    monkeypatch.setattr(critic, "_served_models", lambda: {"gpt": "openai/gpt"})
    try:
        critic.resolve()
    except critic.CriticUnavailable as exc:
        assert "not served" in str(exc)
    else:
        raise AssertionError("expected unavailable critic")


def test_blocking_only_returns_structured_blocking_findings(import_module):
    critic = import_module("critic")
    reply = """CLAIM: x is lower
VERDICT: unsupported
SEVERITY: blocking

CLAIM: y improved
VERDICT: supported
SEVERITY: minor"""
    assert critic.blocking(reply) == [("x is lower", "unsupported")]
    assert critic.blocking("plain prose") == []


def test_review_builds_request_and_joins_content(import_module, monkeypatch):
    critic = import_module("critic", {"CRITIC_API_KEY": "secret"})
    request = types.SimpleNamespace()
    request.data = None

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    response = Response()
    monkeypatch.setattr(critic.urllib.request, "urlopen", lambda req, timeout: response)
    monkeypatch.setattr(
        critic.json, "load", lambda _: {"content": [{"text": "a"}, {"text": " b "}]}
    )
    result = critic.review("model", "write-up", "evidence")
    assert result == "a b"
    assert request.data is None
