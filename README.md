# TP API REST

Une API pour gérer les conteneurs Docker qui portent le label `adminapi.managed=true` et garder leur inventaire à jour dans NetBox.

Le projet comprend le script d'inventaire du TP2, les neuf routes du contrat, le webhook et les métriques Prometheus. Les fichiers du sujet et le vérificateur sont conservés dans le dépôt.

## Installation

Il faut Python 3.12 et Docker Desktop démarré en mode conteneurs Linux.

```bash
git clone https://github.com/wladilaw/tp-api-rest.git
cd tp-api-rest
python -m venv .venv
```

Dans PowerShell :

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Dans Git Bash ou sous Linux :

```bash
source .venv/Scripts/activate  # Windows avec Git Bash
# source .venv/bin/activate   # Linux
python -m pip install -r requirements.txt
cp .env.example .env
```

Le fichier `.env` reste sur la machine. Les six variables doivent être renseignées :

| Variable | Utilisation |
|---|---|
| `DOCKER_API_URL` | API Docker, `http://127.0.0.1:2375` pour les scripts locaux |
| `NETBOX_URL` | NetBox, `http://127.0.0.1:8080` pour les scripts locaux |
| `NETBOX_TOKEN` | Token NetBox au format `nbt_<clé>.<token>` |
| `API_TOKEN_ADMIN` | Token pour lire et modifier les conteneurs |
| `API_TOKEN_READER` | Token pour lire les conteneurs |
| `WEBHOOK_SECRET` | Secret de l'émetteur d'alertes |

Pour générer les trois secrets de l'API, lance cette commande trois fois et reporte chaque valeur dans `.env` :

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Ils doivent être différents. Ne colle aucun secret dans le code, les captures ou les commits.

## Démarrer l'environnement

Commence par les services du cours pour pouvoir obtenir le token NetBox :

```bash
docker compose up -d docker-api netbox
docker pull nginx:alpine
docker pull redis:alpine
docker pull httpd:alpine
docker compose ps
```

NetBox prend plusieurs minutes au premier démarrage. Ouvre `http://127.0.0.1:8080` quand le service est prêt. Le compte du laboratoire fourni dans le compose est `admin` / `admin` ; il sert uniquement au TP local.

Crée un token avec les droits d'écriture depuis ton profil NetBox. Enregistre immédiatement sa valeur complète dans `NETBOX_TOKEN`. La documentation de son API est disponible sur `http://127.0.0.1:8080/api/schema/swagger-ui/`.

Une fois `.env` rempli :

```bash
docker compose up -d --build api
```

L'API écoute sur `http://127.0.0.1:8000`, sa documentation sur `http://127.0.0.1:8000/docs`. Le compose utilise les noms des services pour joindre Docker et NetBox depuis le conteneur. Le cluster `docker-local` est créé automatiquement au premier enregistrement si besoin.

Pour développer sans reconstruire l'image, arrête le service `api`, puis lance :

```bash
docker compose stop api
uvicorn app.main:app --reload --env-file .env --host 127.0.0.1 --port 8000
```

Un seul des deux doit utiliser le port 8000.

## Routes

| Méthode | Route | Accès | Résultat |
|---|---|---|---|
| GET | `/health` | Ouvert | État de l'API |
| GET | `/containers` | Lecteur ou admin | Conteneurs gérés |
| GET | `/containers/{id}` | Lecteur ou admin | Détail d'un conteneur |
| POST | `/containers` | Admin | Création et démarrage |
| POST | `/containers/{id}/stop` | Admin | Arrêt |
| POST | `/containers/{id}/start` | Admin | Démarrage |
| DELETE | `/containers/{id}` | Admin | Suppression, même si le conteneur tourne |
| POST | `/webhooks/alerts` | Secret du webhook | Redémarrage après une alerte |
| GET | `/metrics` | Ouvert | Métriques Prometheus |

Les routes de conteneurs attendent `Authorization: Bearer <token>`. Un token absent ou inconnu renvoie 401 ; un lecteur qui tente une écriture reçoit 403. Un conteneur sans le label est traité comme inexistant, même si son identifiant est connu.

Pour créer un conteneur, le corps JSON est :

```json
{"name": "web1", "image": "nginx:alpine"}
```

Le nom contient 2 à 31 caractères : une minuscule, puis des minuscules, chiffres ou tirets. Les images acceptées sont `nginx:alpine`, `redis:alpine` et `httpd:alpine`.

Le webhook attend `X-Webhook-Secret: <secret>` et :

```json
{"alert": "container_down", "container": "web1"}
```

Il renvoie 202 après traitement. Une alerte rejouée sur un conteneur déjà démarré ne le redémarre pas et n'incrémente pas le compteur. Les actions d'écriture sont sérialisées pour éviter deux redémarrages sur des alertes simultanées.

NetBox passe la machine virtuelle en `active` au démarrage, en `offline` à l'arrêt et la supprime à la suppression du conteneur. Une panne d'un service externe renvoie 502. Docker et NetBox n'offrent pas de transaction commune : si NetBox tombe après une action Docker, cette action peut déjà avoir eu lieu. Vérifie les deux services avant de rejouer une écriture.

Le compteur `adminapi_remediations_total` est conservé en mémoire et repart à zéro au redémarrage de l'API. Le lancement prévu utilise un seul processus Uvicorn.

## Inventaire et surveillance

```bash
python inventaire.py
python surveillant.py
```

L'inventaire charge `.env`, liste tous les conteneurs de la machine et mesure le CPU et la mémoire de ceux qui tournent. Il suit la pagination NetBox et écrit la date du relevé, les conteneurs, la version NetBox et le nombre de types d'objets dans `inventaire.json`. Si un service ne répond pas, il affiche une erreur, sort avec le code 1 et conserve le relevé précédent.

Le surveillant fourni envoie une alerte toutes les cinq secondes pour les conteneurs gérés qui sont arrêtés. Il se lance à part de l'API.

## Vérifications

Tests locaux, avec Docker et NetBox remplacés par des services HTTP de test :

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Ils couvrent les six blocs du vérificateur fourni, les droits d'accès, les entrées invalides, les conteneurs sans label, les pannes de services, la pagination et les alertes simultanées.

Validation sur les vrais services, une fois l'environnement lancé et `.env` rempli :

```bash
python verifier.py
docker build -t tp-api-rest .
git log --all --oneline -- .env
```

La dernière commande ne doit rien afficher. Les tests HTTP locaux ne remplacent pas la validation Docker et NetBox. Au moment de la préparation du dépôt, Docker n'était pas démarré sur la machine : le build local, le relevé TP1 et le vérificateur sur les vrais services restent à exécuter.

Le workflow GitHub lance les tests et construit l'image Docker à chaque push. Il ne démarre pas l'environnement NetBox complet.

## Organisation

| Fichier | Rôle |
|---|---|
| `app/main.py` | Routes, authentification, webhook et métriques |
| `app/models.py` | Validation des corps JSON et format des réponses |
| `app/docker_client.py` | Appels Docker et contrôle du label |
| `app/netbox_client.py` | Machines virtuelles, cluster et pagination |
| `app/http_client.py` | Session HTTP, délais et erreurs externes |
| `app/config.py` | Lecture et contrôle de la configuration |
| `inventaire.py` | Relevé Docker et NetBox du TP2 |
| `tp1-exploration.md` | Commandes de préparation du TP1 |
| `tests/` | Vérifications avec des services HTTP de test |

Références : [contrat du sujet](contrat-openapi.yaml), [sujet complet](sujet.html), [FastAPI](https://fastapi.tiangolo.com/), [API Docker](https://docs.docker.com/reference/api/engine/), [API NetBox](https://netboxlabs.com/docs/netbox/integrations/rest-api/).
