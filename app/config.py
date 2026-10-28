import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    docker_url: str
    netbox_url: str
    netbox_token: str = field(repr=False)
    admin_token: str = field(repr=False)
    reader_token: str = field(repr=False)
    webhook_secret: str = field(repr=False)

    @classmethod
    def from_env(cls):
        names = (
            "DOCKER_API_URL", "NETBOX_URL", "NETBOX_TOKEN",
            "API_TOKEN_ADMIN", "API_TOKEN_READER", "WEBHOOK_SECRET",
        )
        missing = [name for name in names if not os.environ.get(name, "").strip()]
        if missing:
            raise ValueError("Variables manquantes : " + ", ".join(missing))
        settings = cls(*(os.environ[name].strip() for name in names))
        for url in (settings.docker_url, settings.netbox_url):
            parsed = urlsplit(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("Les adresses doivent être des URL HTTP(S) sans identifiants.")
        if len({settings.admin_token, settings.reader_token, settings.webhook_secret}) != 3:
            raise ValueError("Les trois secrets de l'API doivent être différents.")
        return settings

