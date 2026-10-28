import json

import pytest

import inventaire
from app.config import Settings
from app.http_client import HttpClient
from app.netbox_client import NetBoxClient


def test_inventory_includes_foreign_containers_and_all_pages(backend):
    backend.container("web1")
    backend.container("intrus", managed=False, state="exited")
    docker = HttpClient(backend.url, "Docker")
    netbox = NetBoxClient(backend.url, "test-netbox")
    try:
        report = inventaire.collect(docker, netbox)
    finally:
        docker.close()
        netbox.close()
    assert report["netbox"] == {"version": "4.7-test", "object_types": 3}
    assert report["date"].endswith("+00:00")
    assert len(report["containers"]) == 2
    running, stopped = report["containers"]
    assert (running["cpu_percent"], running["memory_mb"]) == (20.0, 5.0)
    assert stopped["name"] == "intrus" and stopped["cpu_percent"] is None
    assert len([c for c in backend.calls if c[1] == "/api/core/object-types/"]) == 2


def configure_env(monkeypatch, backend):
    monkeypatch.setattr(inventaire, "load_dotenv", lambda: None)
    for key, value in {"DOCKER_API_URL": backend.url, "NETBOX_URL": backend.url,
                       "NETBOX_TOKEN": "test-netbox"}.items():
        monkeypatch.setenv(key, value)


def test_inventory_writes_report(backend, monkeypatch, tmp_path):
    configure_env(monkeypatch, backend)
    monkeypatch.chdir(tmp_path)
    assert inventaire.main() == 0
    assert json.loads((tmp_path / "inventaire.json").read_text(encoding="utf-8"))["netbox"]["object_types"] == 3


def test_inventory_failure_keeps_previous_report(backend, monkeypatch, tmp_path, capsys):
    configure_env(monkeypatch, backend)
    monkeypatch.chdir(tmp_path)
    previous = tmp_path / "inventaire.json"
    previous.write_text("ancien relevé", encoding="utf-8")
    backend.failure = "docker"
    assert inventaire.main() == 1
    assert previous.read_text(encoding="utf-8") == "ancien relevé"
    assert "Docker" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["DOCKER_API_URL", "NETBOX_URL", "NETBOX_TOKEN"])
def test_inventory_reports_missing_variable(backend, monkeypatch, capsys, name):
    configure_env(monkeypatch, backend)
    monkeypatch.delenv(name)
    assert inventaire.main() == 1
    assert name in capsys.readouterr().err


def test_cpu_without_previous_sample():
    assert inventaire.usage({}) == (0, 0)
    assert inventaire.usage({"cpu_stats": {"system_cpu_usage": 0}}) == (0, 0)


def test_settings_require_separate_tokens(backend, monkeypatch):
    for key, value in {
        "DOCKER_API_URL": backend.url, "NETBOX_URL": backend.url, "NETBOX_TOKEN": "test-netbox",
        "API_TOKEN_ADMIN": "same-test-token", "API_TOKEN_READER": "same-test-token", "WEBHOOK_SECRET": "test-hook",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="différents"):
        Settings.from_env()


def test_settings_do_not_print_secrets(settings):
    assert "test-admin" not in repr(settings)
    assert "test-netbox" not in repr(settings)
