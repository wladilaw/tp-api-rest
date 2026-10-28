from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


ContainerName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,30}$")]


class ContainerCreate(BaseModel):
    name: ContainerName = Field(examples=["web1"])
    image: Literal["nginx:alpine", "redis:alpine", "httpd:alpine"]


class Container(BaseModel):
    id: str
    name: str
    image: str
    state: str


class Alert(BaseModel):
    alert: Literal["container_down"]
    container: ContainerName

