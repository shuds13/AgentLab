import importlib


def test_forward_orders_messages_and_routes_to_campaigns(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    module = importlib.import_module("framework.slack_to_board")
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    monkeypatch.setattr(module, "DEDICATED", False)
    monkeypatch.setattr(module, "READ_ALL", False)
    delivered = module.forward(
        [
            {"user": "u2", "text": "<@BOT> beta second"},
            {"user": "u1", "text": "<@BOT> alpha first"},
        ],
        "BOT",
    )
    assert delivered == 2
    assert "alpha first" in (tmp_path / "alpha" / "ANNOUNCEMENTS.md").read_text()
    assert "beta second" in (tmp_path / "beta" / "ANNOUNCEMENTS.md").read_text()


def test_forward_skips_bots_and_unaddressed_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    module = importlib.import_module("framework.slack_to_board")
    (tmp_path / "alpha").mkdir()
    delivered = module.forward(
        [
            {"bot_id": "B", "text": "<@BOT> bot"},
            {"user": "u", "text": "ordinary"},
        ],
        "BOT",
    )
    assert delivered == 0
    assert not (tmp_path / "alpha" / "ANNOUNCEMENTS.md").exists()


def test_check_first_run_does_not_fetch_history(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    module = importlib.import_module("framework.slack_to_board")
    called = []
    monkeypatch.setattr(
        module, "slack_get", lambda *args, **kwargs: called.append(args)
    )
    module.check("token", "BOT")
    assert called == []
    assert module.read_state()
