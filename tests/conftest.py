import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import main


class Backend:
    def __init__(self):
        self.containers = {}
        self.machines = {}
        self.clusters = []
        self.cluster_types = []
        self.calls = []
        self.sequence = 0
        self.failure = None

    def container(self, name, *, managed=True, state="running", image="nginx:alpine"):
        self.sequence += 1
        identifier = f"{self.sequence:064x}"
        self.containers[identifier] = {
            "Id": identifier, "Name": "/" + name,
            "Config": {"Image": image, "Labels": {"adminapi.managed": "true"} if managed else None},
            "State": {"Status": state},
        }
        return identifier

    def handle(self, method, raw_path, body, headers):
        url = urlsplit(raw_path)
        path, query = url.path, parse_qs(url.query)
        self.calls.append((method, path, query, body))
        if self.failure == ("netbox" if path.startswith("/api/") else "docker"):
            return 503, {"detail": "indisponible"}
        if path.startswith("/api/"):
            if headers.get("Authorization") != "Bearer test-netbox":
                return 403, {}
            return self.netbox(method, path, query, body)
        if path == "/version":
            return 200, {"Version": "test"}
        if path == "/containers/json":
            filters = json.loads(query.get("filters", ["{}"])[0])
            items = []
            for item in self.containers.values():
                name = item["Name"].lstrip("/")
                labels = item["Config"]["Labels"] or {}
                if "label" in filters and labels.get("adminapi.managed") != "true":
                    continue
                if "name" in filters and not any(part in name for part in filters["name"]):
                    continue
                if "status" in filters and item["State"]["Status"] not in filters["status"]:
                    continue
                items.append({"Id": item["Id"], "Names": [item["Name"]],
                              "Image": item["Config"]["Image"], "Labels": item["Config"]["Labels"],
                              "State": item["State"]["Status"]})
            return 200, items
        if path == "/containers/create":
            name = query["name"][0]
            if any(item["Name"] == "/" + name for item in self.containers.values()):
                return 409, {}
            if body["Image"] not in {"nginx:alpine", "redis:alpine", "httpd:alpine"}:
                return 404, {}
            identifier = self.container(name, managed=False, state="created", image=body["Image"])
            self.containers[identifier]["Config"]["Labels"] = body["Labels"]
            return 201, {"Id": identifier}
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "containers":
            item = self.containers.get(parts[1]) or next(
                (c for c in self.containers.values() if c["Name"] == "/" + parts[1]), None,
            )
            if not item:
                return 404, {}
            if len(parts) == 2 and method == "DELETE":
                assert query.get("force") == ["true"]
                del self.containers[item["Id"]]
                return 204, None
            if len(parts) == 3 and parts[2] == "json":
                return 200, item
            if len(parts) == 3 and parts[2] == "stats":
                assert query.get("stream") == ["false"]
                return 200, {
                    "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000, "online_cpus": 2},
                    "precpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 1000},
                    "memory_stats": {"usage": 5 * 1024 * 1024},
                }
            if len(parts) == 3 and parts[2] in {"start", "stop"}:
                target = "running" if parts[2] == "start" else "exited"
                if item["State"]["Status"] == target:
                    return 304, None
                item["State"]["Status"] = target
                return 204, None
        return 404, {}

    def netbox(self, method, path, query, body):
        if path == "/api/status/":
            return 200, {"netbox-version": "4.7-test"}
        if path == "/api/core/object-types/":
            offset = int(query.get("offset", [0])[0])
            return 200, {"count": 3, "results": [{"id": n} for n in range(offset, min(offset + 2, 3))],
                         "next": "http://netbox-interne/api/core/object-types/?offset=2" if offset == 0 else None}
        if path == "/api/virtualization/cluster-types/":
            if method == "POST":
                item = {"id": len(self.cluster_types) + 1, **body}
                self.cluster_types.append(item)
                return 201, item
            return 200, {"results": self.cluster_types, "next": None}
        if path == "/api/virtualization/clusters/":
            if method == "POST":
                item = {"id": len(self.clusters) + 1, **body}
                self.clusters.append(item)
                return 201, item
            return 200, {"results": self.clusters, "next": None}
        if path == "/api/virtualization/virtual-machines/":
            if method == "POST":
                self.sequence += 1
                item = {**body, "id": self.sequence, "status": {"value": body["status"]}}
                assert body["cluster"] in {c["id"] for c in self.clusters}
                self.machines[item["id"]] = item
                return 201, item
            machines = list(self.machines.values())
            if "name" in query:
                machines = [item for item in machines if item["name"] == query["name"][0]]
            if "name__isw" in query:
                machines = [item for item in machines if item["name"].startswith(query["name__isw"][0])]
            return 200, {"results": machines, "next": None}
        prefix = "/api/virtualization/virtual-machines/"
        if path.startswith(prefix):
            identifier = int(path[len(prefix):].strip("/"))
            item = self.machines.get(identifier)
            if not item:
                return 404, {}
            if method == "DELETE":
                del self.machines[identifier]
                return 204, None
            if method == "PATCH":
                item["status"] = {"value": body["status"]}
                return 200, item
        return 404, {}


@pytest.fixture
def backend():
    backend = Backend()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            try:
                code, result = backend.handle(self.command, self.path, body, self.headers)
            except Exception as error:
                code, result = 500, {"detail": str(error)}
            content = json.dumps(result).encode() if result is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = do_POST = do_PATCH = do_DELETE = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    backend.url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield backend
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def client(backend, monkeypatch):
    for nom, valeur in {
        "DOCKER_API_URL": backend.url, "NETBOX_URL": backend.url,
        "NETBOX_TOKEN": "test-netbox", "API_TOKEN_ADMIN": "test-admin",
        "API_TOKEN_READER": "test-reader", "WEBHOOK_SECRET": "test-hook",
    }.items():
        monkeypatch.setenv(nom, valeur)
    monkeypatch.setattr(main, "remediations", 0)
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def admin_headers():
    return {"Authorization": "Bearer test-admin"}
