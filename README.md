# TP API REST

API pour gérer les conteneurs Docker avec le label `adminapi.managed=true` et mettre leur état à jour dans NetBox.

## Installation

Python 3.12 et Docker Desktop sont nécessaires.

```bash
python -m venv .venv
```

Dans PowerShell :

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Remplir les six variables dans `.env`. Pour chaque secret de l'API, générer une valeur différente :

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Lancement

```bash
docker compose up -d docker-api netbox
docker pull nginx:alpine
docker pull redis:alpine
docker pull httpd:alpine
```

Attendre que NetBox soit prêt sur `http://127.0.0.1:8080`. Le compte du TP est `admin` / `admin`. Créer un token avec les droits d'écriture et mettre sa valeur complète dans `NETBOX_TOKEN` : `nbt_<clé>.<token>`.

```bash
docker compose up -d --build api
```

Documentation : `http://127.0.0.1:8000/docs`.

Pour lancer l'API en local à la place du conteneur :

```bash
docker compose stop api
uvicorn main:app --reload --env-file .env --host 127.0.0.1 --port 8000
```

## Vérification

```bash
python verifier.py
python inventaire.py
```

L'inventaire écrit les conteneurs, leurs ressources et les informations NetBox dans `inventaire.json`. Pour tester les alertes, lancer `python surveillant.py` puis arrêter un conteneur géré.

L'API est dans `main.py`, le script d'inventaire dans `inventaire.py`. Les tokens lecteur et admin passent dans `Authorization: Bearer <token>`. Le webhook utilise `X-Webhook-Secret`.

Les tests se lancent avec `python -m pip install -r requirements-dev.txt`, puis `python -m pytest -q`. GitHub vérifie aussi le projet avec Docker et NetBox.

Le fichier `.env` reste local. Si NetBox tombe après une action Docker, vérifier l'état des deux services avant de relancer l'action. Le compteur de remédiations repart à zéro au redémarrage de l'API.

Références : [sujet](sujet.html), [contrat](contrat-openapi.yaml), [bases de Python](https://docs.python.org/fr/3.12/tutorial/).
