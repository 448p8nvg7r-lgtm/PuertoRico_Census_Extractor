"""Entry point for the Windows build (census-extract.exe).

  census-extract.exe              -> starts the web UI and opens your browser
  census-extract.exe ui           -> same
  census-extract.exe <command>    -> command-line tool (search, run, quick, ...)
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

# Importing these here makes PyInstaller bundle every dependency (pandas, requests, yaml).
import census_extractor.core  # noqa: F401
from census_extractor.cli import main as cli_main

APP_TITLE = "Census Variable Extractor"


def base_dir() -> str:
    """Folder holding the bundled files (PyInstaller) or the project root (dev)."""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_saved_api_key() -> None:
    """The installer saves the key to the user's environment in the registry.
    Processes started right after install don't see it yet, so read it directly."""
    if os.environ.get("CENSUS_API_KEY") or os.name != "nt":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            value, _ = winreg.QueryValueEx(k, "CENSUS_API_KEY")
            if value:
                os.environ["CENSUS_API_KEY"] = str(value)
    except OSError:
        pass


def free_port(start: int = 8501, tries: int = 50) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found between {start} and {start + tries}.")


def open_browser_when_ready(port: int, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    time.sleep(0.8)
    webbrowser.open(f"http://127.0.0.1:{port}")


def run_ui() -> int:
    from streamlit.web import cli as stcli

    port = free_port()
    app = os.path.join(base_dir(), "census_extractor", "app.py")
    if os.name == "nt":
        os.system(f"title {APP_TITLE}")
    print("=" * 64)
    print(f"  {APP_TITLE}")
    print(f"  Running at  http://127.0.0.1:{port}  (opening your browser...)")
    print("  Keep this window open while you use the app.")
    print("  Close this window (or press Ctrl+C) to quit.")
    print("=" * 64)
    threading.Thread(target=open_browser_when_ready, args=(port,), daemon=True).start()
    sys.argv = [
        "streamlit", "run", app,
        "--global.developmentMode=false",
        "--server.headless=true",          # no first-run email prompt; we open the browser
        f"--server.port={port}",
        "--server.address=127.0.0.1",      # only reachable from this computer
        "--browser.gatherUsageStats=false",
        "--server.fileWatcherType=none",
    ]
    return stcli.main()


def main() -> int:
    load_saved_api_key()
    args = sys.argv[1:]
    if not args or args[0] == "ui":
        try:
            return run_ui() or 0
        except SystemExit as e:
            return int(e.code or 0)
        except Exception as e:  # noqa: BLE001 - keep the window open so the user can read it
            print(f"\nCould not start the app: {e}")
            input("Press Enter to close...")
            return 1
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
