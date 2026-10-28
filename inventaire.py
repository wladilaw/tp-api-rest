import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv


def usage(stats):
    cpu = stats.get("cpu_stats", {})
    avant = stats.get("precpu_stats", {})
    utilisation = cpu.get("cpu_usage", {})
    delta_cpu = utilisation.get("total_usage", 0) - avant.get("cpu_usage", {}).get("total_usage", 0)
    delta_systeme = cpu.get("system_cpu_usage", 0) - avant.get("system_cpu_usage", 0)
    coeurs = cpu.get("online_cpus") or len(utilisation.get("percpu_usage", [])) or 1
    pourcent = 0
    if delta_systeme > 0 and delta_cpu >= 0:
        pourcent = delta_cpu / delta_systeme * coeurs * 100
    memoire = stats.get("memory_stats", {}).get("usage", 0) / 1024 / 1024
    return round(pourcent, 2), round(memoire, 2)


def lire(session, url, **options):
    reponse = session.get(url, timeout=(3, 15), allow_redirects=False, **options)
    reponse.raise_for_status()
    return reponse.json()


def collect(session, docker_url, netbox_url, token):
    docker_url = docker_url.rstrip("/")
    netbox_url = netbox_url.rstrip("/")
    conteneurs = lire(session, docker_url + "/containers/json", params={"all": "true"})
    resultat = []
    for conteneur in conteneurs:
        cpu, memoire = None, None
        if conteneur["State"] == "running":
            stats = lire(session, docker_url + f"/containers/{conteneur['Id']}/stats", params={"stream": "false"})
            cpu, memoire = usage(stats)
        resultat.append({
            "id": conteneur["Id"], "name": conteneur["Names"][0].lstrip("/"),
            "image": conteneur["Image"], "state": conteneur["State"],
            "cpu_percent": cpu, "memory_mb": memoire,
        })
    entetes = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    statut = lire(session, netbox_url + "/api/status/", headers=entetes)
    url = netbox_url + "/api/core/object-types/?limit=50"
    nombre = 0
    while url:
        page = lire(session, url, headers=entetes)
        nombre += len(page["results"])
        url = page["next"]
        if url:
            suite = urlsplit(url)
            url = netbox_url + suite.path + ("?" + suite.query if suite.query else "")
    return {
        "date": datetime.now(timezone.utc).isoformat(), "containers": resultat,
        "netbox": {"version": statut["netbox-version"], "object_types": nombre},
    }


def main():
    load_dotenv()
    for nom in ("DOCKER_API_URL", "NETBOX_URL", "NETBOX_TOKEN"):
        if not os.getenv(nom):
            print("Variable manquante : " + nom, file=sys.stderr)
            return 1
    try:
        with requests.Session() as session:
            rapport = collect(session, os.environ["DOCKER_API_URL"], os.environ["NETBOX_URL"], os.environ["NETBOX_TOKEN"])
        with open("inventaire.json", "w", encoding="utf-8") as fichier:
            json.dump(rapport, fichier, ensure_ascii=False, indent=2)
        for conteneur in rapport["containers"]:
            print(conteneur["name"], conteneur["image"], conteneur["state"],
                  "CPU :", conteneur["cpu_percent"], "% ; mémoire :", conteneur["memory_mb"], "Mo")
        print("NetBox", rapport["netbox"]["version"], ":", rapport["netbox"]["object_types"], "types d'objets")
        return 0
    except (requests.RequestException, ValueError, KeyError, TypeError, OSError):
        print("Inventaire impossible : vérifie Docker, NetBox et la configuration.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
