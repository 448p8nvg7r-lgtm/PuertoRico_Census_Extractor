"""Tests for the Windows launcher logic that can run on any OS."""
import importlib.util
import socket
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "launcher", Path(__file__).resolve().parent.parent / "windows" / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_cli_passthrough(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "x.yaml"
    monkeypatch.setattr(sys, "argv", ["census-extract.exe", "init", str(cfg)])
    assert launcher.main() == 0 and cfg.exists()
    monkeypatch.setattr(sys, "argv", ["census-extract.exe", "--help"])
    try:
        launcher.main()
    except SystemExit as e:
        assert e.code == 0
    assert "census-extract" in capsys.readouterr().out


def test_free_port_skips_busy_port():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        assert launcher.free_port(port) != port


def test_browser_opens_once_server_listens(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0)); srv.listen()
        port = srv.getsockname()[1]
        launcher.open_browser_when_ready(port, timeout=5)
    assert opened == [f"http://127.0.0.1:{port}"]


def test_paths():
    assert (Path(launcher.base_dir()) / "census_extractor" / "app.py").exists()
