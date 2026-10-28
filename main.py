import json
import os
import re
import secrets
from threading import Lock
from typing import Literal

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field


app = FastAPI(title="API Docker", version="1.0.0")
securite = HTTPBearer(auto_error=False)
verrou = Lock()
remediations = 0
erreurs = {
    401: {"description": "Token absent ou inconnu"},
    403: {"description": "Accès admin requis"},
    404: {"description": "Conteneur inconnu"},
    409: {"description": "Conflit avec l'état actuel"},
    502: {"description": "Service externe indisponible"},
}


class Creation(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,30}$")
    image: Literal["nginx:alpine", "redis:alpine", "httpd:alpine"]


class Conteneur(BaseModel):
    id: str
    name: str
    image: str
    state: str


class Alerte(BaseModel):
    alert: Literal["container_down"]
    container: str = Field(pattern=r"^[a-z][a-z0-9-]{1,30}$")


def variable(nom):
    valeur = os.getenv(nom, "")
    if not valeur:
        raise HTTPException(503, "Configuration manquante : " + nom)
    return valeur


def comparer(valeur, secret):
    return secrets.compare_digest(valeur.encode(), secret.encode())


def role(token=Depends(securite)):
    if token:
        if comparer(token.credentials, variable("API_TOKEN_ADMIN")):
            return "admin"
        if comparer(token.credentials, variable("API_TOKEN_READER")):
            return "lecteur"
    raise HTTPException(401, "Token absent ou inconnu", headers={"WWW-Authenticate": "Bearer"})


def admin(acces=Depends(role)):
    if acces != "admin":
        raise HTTPException(403, "Accès admin requis")


def secret_webhook(x_webhook_secret: str | None = Header(default=None)):
    if not x_webhook_secret or not comparer(x_webhook_secret, variable("WEBHOOK_SECRET")):
        raise HTTPException(401, "Secret incorrect")


def requete(methode, url, codes=None, **options):
    try:
        reponse = requests.request(methode, url, timeout=(3, 15), allow_redirects=False, **options)
    except requests.RequestException:
        raise HTTPException(502, "Service externe indisponible") from None
    if reponse.status_code not in (200, 201, 204):
        code = (codes or {}).get(reponse.status_code, 502)
        raise HTTPException(code, "Appel refusé par le service externe")
    if not reponse.content:
        return {}
    try:
        return reponse.json()
    except ValueError:
        raise HTTPException(502, "Réponse externe invalide") from None


def docker(methode, chemin, **options):
    url = variable("DOCKER_API_URL").rstrip("/") + chemin
    codes = {404: 404, 304: 409, 409: 409}
    if chemin == "/containers/create":
        codes[404] = 502
    return requete(methode, url, codes=codes, **options)


def netbox(methode, chemin, **options):
    url = variable("NETBOX_URL").rstrip("/") + "/api/virtualization/" + chemin
    entetes = {"Authorization": "Bearer " + variable("NETBOX_TOKEN"), "Accept": "application/json"}
    return requete(methode, url, headers=entetes, **options)


def lire_conteneur(identifiant):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", identifiant):
        raise HTTPException(404, "Conteneur inconnu")
    donnees = docker("GET", f"/containers/{identifiant}/json")
    labels = donnees["Config"]["Labels"] or {}
    if labels.get("adminapi.managed") != "true":
        raise HTTPException(404, "Conteneur inconnu")
    return {
        "id": donnees["Id"], "name": donnees["Name"].lstrip("/"),
        "image": donnees["Config"]["Image"], "state": donnees["State"]["Status"],
    }


def liste_conteneurs():
    filtres = json.dumps({"label": ["adminapi.managed=true"]})
    donnees = docker("GET", "/containers/json", params={"all": "true", "filters": filtres})
    resultat = []
    for conteneur in donnees:
        if (conteneur.get("Labels") or {}).get("adminapi.managed") == "true":
            resultat.append({
                "id": conteneur["Id"], "name": conteneur["Names"][0].lstrip("/"),
                "image": conteneur["Image"], "state": conteneur["State"],
            })
    return resultat


def chercher_vm(nom):
    donnees = netbox("GET", "virtual-machines/", params={"name": nom, "cluster": "docker-local"})
    if donnees["results"]:
        return donnees["results"][0]


def synchroniser(nom, statut):
    machine = chercher_vm(nom)
    if machine:
        netbox("PATCH", f"virtual-machines/{machine['id']}/", json={"status": statut})
        return
    clusters = netbox("GET", "clusters/", params={"name": "docker-local"})["results"]
    if clusters:
        cluster = clusters[0]
    else:
        types = netbox("GET", "cluster-types/", params={"slug": "docker"})["results"]
        if types:
            type_cluster = types[0]
        else:
            type_cluster = netbox("POST", "cluster-types/", json={"name": "Docker", "slug": "docker"})
        cluster = netbox("POST", "clusters/", json={"name": "docker-local", "type": type_cluster["id"]})
    netbox("POST", "virtual-machines/", json={"name": nom, "cluster": cluster["id"], "status": statut})


@app.get("/health", summary="Vérifie que l'API répond")
def health():
    return {"status": "ok"}


@app.get("/containers", response_model=list[Conteneur], responses=erreurs,
         summary="Liste les conteneurs gérés", dependencies=[Depends(role)])
def conteneurs():
    return liste_conteneurs()


@app.get("/containers/{identifiant}", response_model=Conteneur, responses=erreurs,
         summary="Affiche un conteneur géré", dependencies=[Depends(role)])
def conteneur(identifiant: str):
    return lire_conteneur(identifiant)


@app.post("/containers", status_code=201, response_model=Conteneur, responses=erreurs,
          summary="Crée et démarre un conteneur", dependencies=[Depends(admin)])
def creer(corps: Creation):
    with verrou:
        donnees = docker("POST", "/containers/create", params={"name": corps.name},
                         json={"Image": corps.image, "Labels": {"adminapi.managed": "true"}})
        identifiant = donnees["Id"]
        docker("POST", f"/containers/{identifiant}/start")
        synchroniser(corps.name, "active")
        return lire_conteneur(identifiant)


@app.post("/containers/{identifiant}/stop", response_model=Conteneur, responses=erreurs,
          summary="Arrête un conteneur démarré", dependencies=[Depends(admin)])
def arreter(identifiant: str):
    with verrou:
        donnees = lire_conteneur(identifiant)
        if donnees["state"] != "running":
            raise HTTPException(409, "Conteneur déjà arrêté")
        docker("POST", f"/containers/{donnees['id']}/stop", params={"t": 1})
        synchroniser(donnees["name"], "offline")
        return lire_conteneur(donnees["id"])


@app.post("/containers/{identifiant}/start", response_model=Conteneur, responses=erreurs,
          summary="Démarre un conteneur arrêté", dependencies=[Depends(admin)])
def demarrer(identifiant: str):
    with verrou:
        donnees = lire_conteneur(identifiant)
        if donnees["state"] == "running":
            raise HTTPException(409, "Conteneur déjà démarré")
        docker("POST", f"/containers/{donnees['id']}/start")
        synchroniser(donnees["name"], "active")
        return lire_conteneur(donnees["id"])


@app.delete("/containers/{identifiant}", status_code=204, responses=erreurs,
            summary="Supprime un conteneur géré", dependencies=[Depends(admin)])
def supprimer(identifiant: str):
    with verrou:
        donnees = lire_conteneur(identifiant)
        machine = chercher_vm(donnees["name"])
        docker("DELETE", f"/containers/{donnees['id']}", params={"force": "true"})
        if machine:
            netbox("DELETE", f"virtual-machines/{machine['id']}/")
    return Response(status_code=204)


@app.post("/webhooks/alerts", status_code=202, response_model=Conteneur, responses=erreurs,
          summary="Traite une alerte de conteneur arrêté", dependencies=[Depends(secret_webhook)])
def alerte(corps: Alerte):
    global remediations
    with verrou:
        donnees = lire_conteneur(corps.container)
        if donnees["state"] != "running":
            docker("POST", f"/containers/{donnees['id']}/start")
            remediations += 1
        synchroniser(donnees["name"], "active")
        return lire_conteneur(donnees["id"])


@app.get("/metrics", response_class=PlainTextResponse, summary="Expose les métriques Prometheus")
def metriques():
    compte = {"running": 0, "exited": 0, "created": 0}
    for conteneur in liste_conteneurs():
        etat = conteneur["state"]
        compte[etat] = compte.get(etat, 0) + 1
    lignes = ["# HELP adminapi_containers Nombre de conteneurs gérés.", "# TYPE adminapi_containers gauge"]
    for etat, nombre in compte.items():
        lignes.append(f'adminapi_containers{{state="{etat}"}} {nombre}')
    lignes.append("# HELP adminapi_remediations_total Redémarrages par alerte.")
    lignes.append("# TYPE adminapi_remediations_total counter")
    lignes.append(f"adminapi_remediations_total {remediations}")
    return "\n".join(lignes) + "\n"
