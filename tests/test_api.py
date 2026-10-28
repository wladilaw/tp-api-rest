from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from fastapi import HTTPException

import main
import verifier


def test_course_verifier_against_http_test_services(client, backend):
    env = {
        "VERIF_DOCKER_API_URL": backend.url, "VERIF_NETBOX_URL": backend.url,
        "NETBOX_TOKEN": "test-netbox", "API_TOKEN_ADMIN": "test-admin",
        "API_TOKEN_READER": "test-reader", "WEBHOOK_SECRET": "test-hook",
    }
    check = verifier.Verificateur("http://testserver", env)

    def api(method, path, jeton="admin", **kwargs):
        headers = kwargs.pop("headers", {})
        if jeton:
            headers.setdefault("Authorization", f"Bearer {check.jetons[jeton]}")
        return client.request(method, path, headers=headers, **kwargs)

    check.api = api
    results = verifier.executer(check, list(verifier.BLOCS))
    failures = [f"{block}: {label}: {detail}" for block, checks in results.items()
                for label, passed, detail in checks if not passed]
    assert not failures, "\n".join(failures)
    assert sum(len(checks) for checks in results.values()) == 50


@pytest.mark.parametrize("method,path", [
    ("GET", "/containers"), ("GET", "/containers/ghost"),
    ("POST", "/containers"), ("POST", "/containers/ghost/start"),
    ("POST", "/containers/ghost/stop"), ("DELETE", "/containers/ghost"),
])
def test_no_token_never_calls_backends(client, backend, method, path):
    response = client.request(method, path, json={"name": "web1", "image": "nginx:alpine"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert not backend.calls


@pytest.mark.parametrize("method,path", [
    ("POST", "/containers"), ("POST", "/containers/ghost/start"),
    ("POST", "/containers/ghost/stop"), ("DELETE", "/containers/ghost"),
])
def test_reader_never_writes(client, backend, method, path):
    response = client.request(method, path, headers={"Authorization": "Bearer test-reader"},
                              json={"name": "web1", "image": "nginx:alpine"})
    assert response.status_code == 403
    assert not backend.calls


@pytest.mark.parametrize("method,suffix", [("GET", ""), ("POST", "/start"),
                                          ("POST", "/stop"), ("DELETE", "")])
def test_foreign_container_is_invisible(client, backend, admin_headers, method, suffix):
    identifier = backend.container("intrus", managed=False)
    response = client.request(method, f"/containers/{identifier}{suffix}", headers=admin_headers)
    assert response.status_code == 404
    assert backend.containers[identifier]["State"]["Status"] == "running"
    assert all(call[0] == "GET" for call in backend.calls)


@pytest.mark.parametrize("name", ["a", "Aweb", "web test", "a" * 32, "web1\n", "../web1", "web_1", "éé"])
def test_invalid_name_has_no_side_effect(client, backend, admin_headers, name):
    response = client.post("/containers", headers=admin_headers, json={"name": name, "image": "nginx:alpine"})
    assert response.status_code == 422
    assert not backend.calls


@pytest.mark.parametrize("body", [{}, {"name": "web1"}, {"name": "web1", "image": "ubuntu:latest"},
                                  {"name": None, "image": "nginx:alpine"}])
def test_invalid_body_has_no_side_effect(client, backend, admin_headers, body):
    assert client.post("/containers", headers=admin_headers, json=body).status_code == 422
    assert not backend.calls


@pytest.mark.parametrize("image", ["nginx:alpine", "redis:alpine", "httpd:alpine"])
def test_allowed_images_are_started_and_recorded(client, backend, admin_headers, image):
    response = client.post("/containers", headers=admin_headers, json={"name": "web1", "image": image})
    assert response.status_code == 201
    body = response.json()
    assert len(body["id"]) == 64
    assert body == {"id": body["id"], "name": "web1", "image": image, "state": "running"}
    assert backend.containers[body["id"]]["Config"]["Labels"] == {"adminapi.managed": "true"}
    assert next(iter(backend.machines.values()))["status"]["value"] == "active"


def test_webhook_is_isolated_and_replays_do_not_increment(client, backend):
    foreign = backend.container("intrus", managed=False, state="exited")
    body = {"alert": "container_down", "container": "intrus"}
    assert client.post("/webhooks/alerts", json=body,
                       headers={"Authorization": "Bearer test-admin"}).status_code == 401
    assert not backend.calls
    assert client.post("/webhooks/alerts", json=body,
                       headers={"X-Webhook-Secret": "test-hook"}).status_code == 404
    assert backend.containers[foreign]["State"]["Status"] == "exited"
    backend.container("web1", state="exited")
    body["container"] = "web1"
    for _ in range(2):
        assert client.post("/webhooks/alerts", json=body,
                           headers={"X-Webhook-Secret": "test-hook"}).status_code == 202
    assert "adminapi_remediations_total 1\n" in client.get("/metrics").text


def test_parallel_alerts_only_restart_once(client, backend):
    backend.container("web1", state="exited")
    def alert(_):
        return client.post("/webhooks/alerts", headers={"X-Webhook-Secret": "test-hook"},
                           json={"alert": "container_down", "container": "web1"}).status_code
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(alert, range(4))) == [202] * 4
    starts = [call for call in backend.calls if call[0] == "POST" and call[1].endswith("/start")]
    assert len(starts) == 1
    assert "adminapi_remediations_total 1\n" in client.get("/metrics").text


def test_docker_failure_is_502_but_health_stays_open(client, backend, admin_headers):
    backend.failure = "docker"
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/containers", headers=admin_headers).status_code == 502
    assert client.get("/metrics").status_code == 502


def test_timeout_does_not_leak_internal_error(client, monkeypatch, admin_headers):
    def timeout(*args, **kwargs):
        raise requests.Timeout("secret contenu dans une erreur interne")
    monkeypatch.setattr(main.requests, "request", timeout)
    response = client.get("/containers", headers=admin_headers)
    assert response.status_code == 502
    assert "secret" not in response.text


def test_netbox_failure_does_not_claim_success(client, backend, admin_headers):
    backend.failure = "netbox"
    response = client.post("/containers", headers=admin_headers,
                           json={"name": "web1", "image": "nginx:alpine"})
    assert response.status_code == 502


def test_invalid_docker_response_is_502(client, monkeypatch, admin_headers):
    response = requests.Response()
    response.status_code = 200
    response._content = b"not-json"
    monkeypatch.setattr(main.requests, "request", lambda *a, **k: response)
    assert client.get("/containers", headers=admin_headers).status_code == 502


def test_invalid_identifier_cannot_become_a_docker_path(client, backend):
    with pytest.raises(HTTPException) as error:
        main.lire_conteneur("../../images/json")
    assert error.value.status_code == 404
    assert not backend.calls


def test_empty_metrics_have_zero_series(client):
    response = client.get("/metrics")
    assert response.headers["content-type"].startswith("text/plain")
    for state in ("running", "exited", "created"):
        assert f'adminapi_containers{{state="{state}"}} 0\n' in response.text
