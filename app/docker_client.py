import json
import re

from app.http_client import HttpClient, ServiceError


MANAGED_LABEL = "adminapi.managed"


class DockerClient(HttpClient):
    def __init__(self, url):
        super().__init__(url, "Docker")

    def list_managed(self):
        filters = json.dumps({"label": [f"{MANAGED_LABEL}=true"]})
        response = self.request(
            "GET", "/containers/json", params={"all": "true", "filters": filters},
        )
        containers = self.json(response)
        return [
            {"id": item["Id"], "name": item["Names"][0].lstrip("/"),
             "image": item["Image"], "state": item["State"]}
            for item in containers
            if (item.get("Labels") or {}).get(MANAGED_LABEL) == "true"
        ]

    def inspect_managed(self, identifier):
        # Aucun chemin arbitraire ne doit pouvoir sortir de /containers/.
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", identifier):
            raise ServiceError("Docker", 404, "Conteneur inconnu.")
        response = self.request(
            "GET", f"/containers/{identifier}/json", expected=(200, 404),
        )
        if response.status_code == 404:
            raise ServiceError("Docker", 404, "Conteneur inconnu.")
        item = self.json(response)
        if (item["Config"].get("Labels") or {}).get(MANAGED_LABEL) != "true":
            raise ServiceError("Docker", 404, "Conteneur inconnu.")
        return {"id": item["Id"], "name": item["Name"].lstrip("/"),
                "image": item["Config"]["Image"], "state": item["State"]["Status"]}

    def create(self, name, image):
        response = self.request(
            "POST", "/containers/create", params={"name": name},
            json={"Image": image, "Labels": {MANAGED_LABEL: "true"}},
            expected=(201, 404, 409),
        )
        if response.status_code == 409:
            raise ServiceError("Docker", 409, "Ce nom de conteneur est déjà pris.")
        if response.status_code == 404:
            raise ServiceError("Docker", 502, "Image absente de Docker : télécharge-la avant de créer le conteneur.")
        return self.json(response)["Id"]

    def change_state(self, identifier, action):
        response = self.request(
            "POST", f"/containers/{identifier}/{action}",
            params={"t": 1} if action == "stop" else None,
            expected=(204, 304, 404, 409),
        )
        if response.status_code == 404:
            raise ServiceError("Docker", 404, "Conteneur inconnu.")
        if response.status_code in {304, 409}:
            raise ServiceError("Docker", 409, "L'état du conteneur ne permet pas cette action.")

    def delete(self, identifier):
        response = self.request(
            "DELETE", f"/containers/{identifier}", params={"force": "true"},
            expected=(204, 404),
        )
        if response.status_code == 404:
            raise ServiceError("Docker", 404, "Conteneur inconnu.")

