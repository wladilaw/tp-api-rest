from urllib.parse import urlsplit

from app.http_client import HttpClient, ServiceError


class NetBoxClient(HttpClient):
    def __init__(self, url, token):
        super().__init__(url.rstrip("/") + "/api", "NetBox", {
            "Authorization": f"Bearer {token}", "Accept": "application/json",
        })

    def all_results(self, path, params=None):
        results = []
        seen = set()
        while path:
            key = (path, str(params))
            if key in seen:
                raise ServiceError("NetBox", detail="Pagination NetBox incohérente.")
            seen.add(key)
            page = self.json(self.request("GET", path, params=params))
            results.extend(page["results"])
            next_url = page.get("next")
            if not next_url:
                break
            # On garde l'hôte configuré, même si NetBox annonce une autre adresse.
            parsed = urlsplit(next_url)
            if not parsed.path.startswith("/api/"):
                raise ServiceError("NetBox", detail="Pagination NetBox invalide.")
            path = parsed.path[4:] + ("?" + parsed.query if parsed.query else "")
            params = None
        return results

    def ensure_cluster(self):
        clusters = self.all_results("/virtualization/clusters/", {"name": "docker-local"})
        if clusters:
            return clusters[0]["id"]
        types = self.all_results("/virtualization/cluster-types/", {"slug": "docker"})
        if types:
            type_id = types[0]["id"]
        else:
            type_id = self.json(self.request(
                "POST", "/virtualization/cluster-types/", expected=(201,),
                json={"name": "Docker", "slug": "docker"},
            ))["id"]
        return self.json(self.request(
            "POST", "/virtualization/clusters/", expected=(201,),
            json={"name": "docker-local", "type": type_id},
        ))["id"]

    def find_vm(self, name):
        machines = self.all_results(
            "/virtualization/virtual-machines/", {"name": name, "cluster": "docker-local"},
        )
        if len(machines) > 1:
            raise ServiceError("NetBox", detail="Plusieurs machines NetBox portent ce nom dans docker-local.")
        return machines[0] if machines else None

    def sync(self, name, status):
        machine = self.find_vm(name)
        if machine:
            self.request(
                "PATCH", f"/virtualization/virtual-machines/{machine['id']}/",
                json={"status": status},
            )
        else:
            cluster_id = self.ensure_cluster()
            self.request(
                "POST", "/virtualization/virtual-machines/", expected=(201,),
                json={"name": name, "cluster": cluster_id, "status": status},
            )

    def delete_vm(self, name):
        machine = self.find_vm(name)
        if machine:
            self.request(
                "DELETE", f"/virtualization/virtual-machines/{machine['id']}/",
                expected=(204, 404),
            )

