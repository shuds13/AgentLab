import importlib
from pathlib import Path


def test_load_strips_comments_and_resolves_path_values(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    module = importlib.import_module("framework.lab_config")
    config = tmp_path / "lab.yaml"
    config.write_text("""
    # comment
    litellm-bin: ~/bin/litellm # inline
    litellm-url: http://localhost:4000
    malformed
    key: value:with:colons
    """)

    loaded = module.load(config)
    assert loaded["litellm-bin"] == str(Path.home() / "bin" / "litellm")
    assert loaded["key"] == "value:with:colons"


def test_start_command_and_service_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    module = importlib.import_module("framework.lab_config")
    config = {
        "litellm-bin": "/bin/litellm",
        "litellm-config": "/tmp/config.yaml",
        "litellm-url": "http://localhost:4000",
        "bridge": "yes",
        "secretary": "off",
    }
    assert (
        module.start_command(config)
        == "/bin/litellm --config /tmp/config.yaml --port 4000"
    )
    assert module.on("bridge", config)
    assert not module.on("secretary", config)


def test_main_get_services_and_export(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    (tmp_path / "lab.yaml").write_text("slack-channel: C123\nbridge: on\n")
    module = importlib.import_module("framework.lab_config")
    module.load = lambda path=str(tmp_path / "lab.yaml"): {
        "slack-channel": "C123",
        "bridge": "on",
    }

    monkeypatch.setattr(module.sys, "argv", ["lab_config.py", "--get", "slack-channel"])
    module.main()
    assert capsys.readouterr().out.strip() == "C123"

    monkeypatch.setattr(module.sys, "argv", ["lab_config.py", "--services"])
    module.main()
    assert capsys.readouterr().out.strip() == "bridge"
