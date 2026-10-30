"""desktop_launcher --mcp: stdio MCP server against the running app. It sets
the two environment settings when missing and starts nothing else."""

import sys

import pytest

import desktop_launcher as dl


@pytest.fixture
def mcp_run(monkeypatch, tmp_path):
    import app.mcp.server as server

    calls = []
    monkeypatch.setattr(server, "run", lambda: calls.append("run") or 0)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(dl, "get_env_value", lambda key: None)

    def no_server(*a, **k):
        raise AssertionError("--mcp must not start a server")

    monkeypatch.setattr(dl, "start_server", no_server)
    monkeypatch.setattr(dl, "launch_company", no_server)
    monkeypatch.setattr(dl, "prepare_env", no_server)
    return calls


def test_mcp_sets_defaults_and_runs(monkeypatch, tmp_path, mcp_run):
    monkeypatch.delenv("SLOWBOOKS_URL", raising=False)
    monkeypatch.delenv("SLOWBOOKS_TOKEN_FILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["SlowBooksPro", "--mcp"])
    assert dl.main() == 0
    assert mcp_run == ["run"]
    import os

    assert os.environ["SLOWBOOKS_URL"] == "http://127.0.0.1:3001"
    assert os.environ["SLOWBOOKS_TOKEN_FILE"] == str(tmp_path / "mcp-token")


def test_mcp_port_flag_and_env_file_port(monkeypatch, mcp_run):
    import os

    monkeypatch.delenv("SLOWBOOKS_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["SlowBooksPro", "--mcp", "--port", "4100"])
    assert dl.main() == 0
    assert os.environ["SLOWBOOKS_URL"] == "http://127.0.0.1:4100"

    monkeypatch.delenv("SLOWBOOKS_URL")
    monkeypatch.setattr(dl, "get_env_value", lambda key: "4200")
    monkeypatch.setattr(sys, "argv", ["SlowBooksPro", "--mcp"])
    assert dl.main() == 0
    assert os.environ["SLOWBOOKS_URL"] == "http://127.0.0.1:4200"


def test_mcp_leaves_set_values_alone(monkeypatch, mcp_run):
    import os

    monkeypatch.setenv("SLOWBOOKS_URL", "http://localhost:9000")
    monkeypatch.setenv("SLOWBOOKS_TOKEN_FILE", "somewhere")
    monkeypatch.setattr(sys, "argv", ["SlowBooksPro", "--mcp"])
    assert dl.main() == 0
    assert os.environ["SLOWBOOKS_URL"] == "http://localhost:9000"
    assert os.environ["SLOWBOOKS_TOKEN_FILE"] == "somewhere"
    # An explicit --port wins over the variable (it was ignored silently).
    monkeypatch.setattr(sys, "argv", ["SlowBooksPro", "--mcp", "--port", "4300"])
    assert dl.main() == 0
    assert os.environ["SLOWBOOKS_URL"] == "http://127.0.0.1:4300"
