import asyncio


def test_configure_and_path_confinement(transfer_module, tmp_path):
    cfg = transfer_module.configure(
        {
            "work_dir": "/remote/work",
            "globus": {"remote_collection": "r", "local_collection": "l"},
        },
        str(tmp_path),
        str(tmp_path / "campaign"),
        {"globus_collection_root": "/projects"},
    )
    assert cfg["remote_write_root"] == "/remote/work"
    assert cfg["collection_root"] == ["/projects"]
    assert transfer_module._local_dest("file.txt", [str(tmp_path)]) == str(
        tmp_path / "file.txt"
    )
    assert transfer_module._local_dest("../secret", [str(tmp_path)]) is None


def test_transfer_requires_configuration_and_authentication(
    transfer_module, monkeypatch
):
    transfer_module.CFG = None
    result = asyncio.run(transfer_module.transfer({"op": "ls", "path": "/"}))
    assert result["is_error"]
    assert "not configured" in result["content"][0]["text"]

    transfer_module.CFG = {
        "remote_collection": "r",
        "local_collection": "l",
        "remote_write_root": "/work",
        "remote_read_root": "",
        "collection_root": [],
        "local_roots": ["/tmp"],
    }
    monkeypatch.setattr(
        transfer_module, "_authenticated", lambda: (False, "login required")
    )
    result = asyncio.run(transfer_module.transfer({"op": "ls", "path": "/"}))
    assert "login required" in result["content"][0]["text"]


def test_ls_and_unknown_operation(transfer_module, monkeypatch):
    transfer_module.CFG = {
        "remote_collection": "r",
        "local_collection": "l",
        "remote_write_root": "/work",
        "remote_read_root": "",
        "collection_root": [],
        "local_roots": ["/tmp"],
    }
    monkeypatch.setattr(transfer_module, "_authenticated", lambda: (True, "user"))
    monkeypatch.setattr(
        transfer_module, "_globus", lambda *args, **kwargs: (0, "a\nb", "")
    )
    result = asyncio.run(transfer_module.transfer({"op": "ls", "path": "/data"}))
    assert result["content"][0]["text"] == "a\nb"
    result = asyncio.run(transfer_module.transfer({"op": "wat", "path": "x"}))
    assert result["is_error"]
