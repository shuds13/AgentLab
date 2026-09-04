import importlib
import json


def test_newest_run_prefers_live_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    module = importlib.import_module("framework.watch")
    live = tmp_path / "workspace" / "camp" / "runs" / "live"
    finished = tmp_path / "workspace" / "camp" / "runs" / "finished"
    live.mkdir(parents=True)
    finished.mkdir(parents=True)
    (live / "meta.json").write_text("{}")
    (finished / "meta.json").write_text("{}")
    (live / "heartbeat").write_text("1")
    assert module.newest_run("camp") == str(live)


def test_status_counts_jobs_and_results(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    module = importlib.import_module("framework.watch")
    module.LAB_DIR = str(tmp_path)
    run = tmp_path / "workspace" / "camp" / "runs" / "r"
    run.mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({"run_id": "r", "status": "stopped"}))
    ws = tmp_path / "workspace" / "camp"
    (ws / "jobs.jsonl").write_text(
        '{"event":"submit","run":"r"}\n{"event":"completed","run":"r"}\n'
    )
    (ws / "results.jsonl").write_text("one\ntwo\n")
    assert module.status("camp")["jobs_run"] == 1
    assert module.status("camp")["jobs_done"] == 1
    assert module.status("camp")["results"] == 2


def test_render_and_file_safety(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    module = importlib.import_module("framework.watch")
    rendered = module._render("# Heading\n\n![plot](figure.png)")
    assert "figure.png" in rendered
    handler = object.__new__(module.Handler)
    handler.campaign = "camp"
    assert handler._file("secret.txt") == "not a file this watcher serves"
