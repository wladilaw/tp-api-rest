from collections import Counter
from contextlib import asynccontextmanager
from secrets import compare_digest
from threading import RLock
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.docker_client import DockerClient
from app.http_client import ServiceError
from app.models import Alert, Container, ContainerCreate
from app.netbox_client import NetBoxClient


bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
READ_ERRORS = {401: {"description": "Token absent ou inconnu"},
               502: {"description": "Docker ou NetBox indisponible"}}
DETAIL_ERRORS = {**READ_ERRORS, 404: {"description": "Conteneur inconnu ou non géré"}}
WRITE_ERRORS = {**DETAIL_ERRORS, 403: {"description": "Le rôle admin est requis"}}
STATE_ERRORS = {**WRITE_ERRORS, 409: {"description": "Conflit avec l'état actuel"}}


def matches(value, expected):
    # compare_digest sur des bytes accepte aussi les caractères non ASCII reçus.
    return compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


def reader(request: Request, credentials: Credentials):
    settings = request.app.state.settings
    if credentials:
        if matches(credentials.credentials, settings.admin_token):
            return "admin"
        if matches(credentials.credentials, settings.reader_token):
            return "reader"
    raise HTTPException(401, "Token absent ou inconnu.", headers={"WWW-Authenticate": "Bearer"})


def admin(role: Annotated[str, Depends(reader)]):
    if role != "admin":
        raise HTTPException(403, "Le rôle admin est requis.")


def webhook_auth(request: Request, x_webhook_secret: Annotated[str | None, Header()] = None):
    if not x_webhook_secret or not matches(x_webhook_secret, request.app.state.settings.webhook_secret):
        raise HTTPException(401, "Secret de webhook absent ou incorrect.")


def create_app(settings=None):
    @asynccontextmanager
    async def lifespan(application):
        application.state.settings = settings or Settings.from_env()
        application.state.docker = DockerClient(application.state.settings.docker_url)
        application.state.netbox = NetBoxClient(
            application.state.settings.netbox_url, application.state.settings.netbox_token,
        )
        application.state.mutation_lock = RLock()
        application.state.remediations = 0
        try:
            yield
        finally:
            application.state.docker.close()
            application.state.netbox.close()

    application = FastAPI(
        title="API d'administration de conteneurs", version="1.0.0", lifespan=lifespan,
        description="Gestion des conteneurs adminapi.managed=true et inventaire NetBox.",
    )

    @application.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @application.get("/health", summary="Vérifie que l'API répond", tags=["supervision"])
    def health():
        return {"status": "ok"}

    @application.get("/containers", response_model=list[Container], responses=READ_ERRORS,
                     summary="Liste les conteneurs gérés", tags=["conteneurs"],
                     dependencies=[Depends(reader)])
    def list_containers(request: Request):
        return request.app.state.docker.list_managed()

    @application.get("/containers/{identifier}", response_model=Container, responses=DETAIL_ERRORS,
                     summary="Affiche un conteneur géré", tags=["conteneurs"],
                     dependencies=[Depends(reader)])
    def get_container(identifier: str, request: Request):
        return request.app.state.docker.inspect_managed(identifier)

    @application.post("/containers", status_code=201, response_model=Container,
                      responses=STATE_ERRORS, summary="Crée et démarre un conteneur",
                      tags=["conteneurs"], dependencies=[Depends(admin)])
    def create_container(body: ContainerCreate, request: Request):
        state = request.app.state
        with state.mutation_lock:
            identifier = state.docker.create(body.name, body.image)
            state.docker.change_state(identifier, "start")
            container = state.docker.inspect_managed(identifier)
            state.netbox.sync(container["name"], "active")
            return container

    def change_container(identifier, request, action):
        state = request.app.state
        with state.mutation_lock:
            container = state.docker.inspect_managed(identifier)
            running = container["state"] == "running"
            if (action == "start" and running) or (action == "stop" and not running):
                raise HTTPException(409, "Le conteneur est déjà dans cet état.")
            state.docker.change_state(container["id"], action)
            container = state.docker.inspect_managed(container["id"])
            state.netbox.sync(container["name"], "active" if action == "start" else "offline")
            return container

    @application.post("/containers/{identifier}/stop", response_model=Container,
                      responses=STATE_ERRORS, summary="Arrête un conteneur démarré",
                      tags=["conteneurs"], dependencies=[Depends(admin)])
    def stop_container(identifier: str, request: Request):
        return change_container(identifier, request, "stop")

    @application.post("/containers/{identifier}/start", response_model=Container,
                      responses=STATE_ERRORS, summary="Démarre un conteneur arrêté",
                      tags=["conteneurs"], dependencies=[Depends(admin)])
    def start_container(identifier: str, request: Request):
        return change_container(identifier, request, "start")

    @application.delete("/containers/{identifier}", status_code=204, responses=WRITE_ERRORS,
                        summary="Supprime un conteneur géré", tags=["conteneurs"],
                        dependencies=[Depends(admin)])
    def delete_container(identifier: str, request: Request):
        state = request.app.state
        with state.mutation_lock:
            container = state.docker.inspect_managed(identifier)
            # Vérifier NetBox avant de supprimer Docker limite les suppressions partielles.
            machine = state.netbox.find_vm(container["name"])
            state.docker.delete(container["id"])
            if machine:
                state.netbox.request(
                    "DELETE", f"/virtualization/virtual-machines/{machine['id']}/",
                    expected=(204, 404),
                )
        return Response(status_code=204)

    @application.post("/webhooks/alerts", status_code=202, response_model=Container,
                      responses=DETAIL_ERRORS, summary="Traite une alerte de conteneur arrêté",
                      tags=["webhooks"], dependencies=[Depends(webhook_auth)])
    def receive_alert(body: Alert, request: Request):
        state = request.app.state
        with state.mutation_lock:
            container = state.docker.inspect_managed(body.container)
            if container["state"] != "running":
                try:
                    state.docker.change_state(container["id"], "start")
                except ServiceError as error:
                    # Un démarrage externe concurrent ne compte pas comme remédiation.
                    if error.status_code != 409:
                        raise
                    container = state.docker.inspect_managed(container["id"])
                    if container["state"] != "running":
                        raise
                else:
                    state.remediations += 1
                    container = state.docker.inspect_managed(container["id"])
            state.netbox.sync(container["name"], "active")
            return container

    @application.get("/metrics", response_class=PlainTextResponse,
                     responses={502: {"description": "Docker indisponible"}},
                     summary="Expose les métriques Prometheus", tags=["supervision"])
    def metrics(request: Request):
        state = request.app.state
        counts = Counter(item["state"] for item in state.docker.list_managed())
        lines = ["# HELP adminapi_containers Nombre de conteneurs gérés par état.",
                 "# TYPE adminapi_containers gauge"]
        for status in sorted({"running", "exited", "created"} | counts.keys()):
            lines.append(f'adminapi_containers{{state="{status}"}} {counts[status]}')
        lines.extend([
            "# HELP adminapi_remediations_total Redémarrages déclenchés par une alerte.",
            "# TYPE adminapi_remediations_total counter",
            f"adminapi_remediations_total {state.remediations}",
        ])
        return PlainTextResponse("\n".join(lines) + "\n")

    return application


app = create_app()
