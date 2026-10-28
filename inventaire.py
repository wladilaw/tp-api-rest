import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.http_client import HttpClient, ServiceError
from app.netbox_client import NetBoxClient


def usage(stats):
    cpu = stats.get("cpu_stats", {})
    previous = stats.get("precpu_stats", {})
    current_usage = cpu.get("cpu_usage", {})
    previous_usage = previous.get("cpu_usage", {})
    delta_cpu = current_usage.get("total_usage", 0) - previous_usage.get("total_usage", 0)
    delta_system = cpu.get("system_cpu_usage", 0) - previous.get("system_cpu_usage", 0)
    cpus = cpu.get("online_cpus") or len(current_usage.get("percpu_usage", [])) or 1
    percent = delta_cpu / delta_system * cpus * 100 if delta_system > 0 and delta_cpu >= 0 else 0
    memory = stats.get("memory_stats", {}).get("usage", 0) / 1024 / 1024
    return round(percent, 2), round(memory, 2)


def collect(docker, netbox):
    containers = docker.json(docker.request("GET", "/containers/json", params={"all": "true"}))
    items = []
    for container in containers:
        cpu, memory = None, None
        if container["State"] == "running":
            stats = docker.json(docker.request(
                "GET", f"/containers/{container['Id']}/stats", params={"stream": "false"},
            ))
            cpu, memory = usage(stats)
        items.append({
            "id": container["Id"], "name": container["Names"][0].lstrip("/"),
            "image": container["Image"], "state": container["State"],
            "cpu_percent": cpu, "memory_mb": memory,
        })
    status = netbox.json(netbox.request("GET", "/status/"))
    object_types = netbox.all_results("/core/object-types/", {"limit": 50})
    return {
        "date": datetime.now(timezone.utc).isoformat(), "containers": items,
        "netbox": {"version": status["netbox-version"], "object_types": len(object_types)},
    }


def main():
    load_dotenv()
    required = ("DOCKER_API_URL", "NETBOX_URL", "NETBOX_TOKEN")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        print("Variables manquantes : " + ", ".join(missing), file=sys.stderr)
        return 1
    docker = HttpClient(os.environ["DOCKER_API_URL"], "Docker")
    netbox = NetBoxClient(os.environ["NETBOX_URL"], os.environ["NETBOX_TOKEN"])
    try:
        report = collect(docker, netbox)
        # Le relevé précédent reste intact si un service échoue.
        Path("inventaire.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        for item in report["containers"]:
            resources = (f"CPU {item['cpu_percent']} % | RAM {item['memory_mb']} Mo"
                         if item["cpu_percent"] is not None else "ressources non relevées")
            print(f"{item['name']} | {item['image']} | {item['state']} | {resources}")
        print(f"NetBox {report['netbox']['version']} : {report['netbox']['object_types']} types d'objets")
        print("Relevé enregistré dans inventaire.json")
        return 0
    except (ServiceError, KeyError, TypeError, ValueError, OSError) as error:
        # Ne pas afficher les réponses HTTP brutes : elles peuvent contenir des secrets.
        message = error.detail if isinstance(error, ServiceError) else "Réponse invalide ou fichier impossible à écrire."
        print(f"Inventaire impossible : {message}", file=sys.stderr)
        return 1
    finally:
        docker.close()
        netbox.close()


if __name__ == "__main__":
    sys.exit(main())

