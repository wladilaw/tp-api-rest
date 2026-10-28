from threading import Lock

import requests


class ServiceError(Exception):
    def __init__(self, service, status_code=502, detail=None):
        self.status_code = status_code
        self.detail = detail or f"Le service {service} ne répond pas correctement."
        super().__init__(self.detail)


class HttpClient:
    def __init__(self, base_url, service, headers=None):
        self.base_url = base_url.rstrip("/")
        self.service = service
        self.session = requests.Session()
        self.session.headers.update(headers or {})
        self.lock = Lock()

    def request(self, method, path, *, expected=(200,), timeout=(3, 15), **kwargs):
        try:
            # Une Session est partagée par les routes synchrones de FastAPI.
            with self.lock:
                response = self.session.request(
                    method, self.base_url + path, timeout=timeout,
                    allow_redirects=False, **kwargs,
                )
        except requests.RequestException:
            raise ServiceError(self.service) from None
        if response.status_code not in expected:
            raise ServiceError(self.service)
        return response

    def json(self, response):
        try:
            return response.json()
        except ValueError:
            raise ServiceError(self.service) from None

    def close(self):
        self.session.close()

