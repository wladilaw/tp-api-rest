# TP API REST

Une API pour g�rer les conteneurs Docker qui portent le label `adminapi.managed=true` et garder leur inventaire � jour dans NetBox.

Le projet comprend le script d'inventaire du TP2, les neuf routes du contrat, le webhook et les m�triques Prometheus. Les fichiers du sujet et le v�rificateur sont conserv�s dans le d�p�t.

## Installation

Il faut Python 3.12 et Docker Desktop d�marr� en mode conteneurs Linux.

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

Le fichier `.env` reste sur la machine. Les six variables doivent �tre renseign�es :

| Variable | Utilisation |
|---|---|
| `DOCKER_API_URL` | API Docker, `http://127.0.0.1:2375` pour les scripts locaux |
| `NETBOX_URL` | NetBox, `http://127.0.0.1:8080` pour les scripts locaux |
| `NETBOX_TOKEN` | Token NetBox au format `nbt_<cl�>.<token>` |
| `API_TOKEN_ADMIN` | Token pour lire et modifier les conteneurs |
| `API_TOKEN_READER` | Token pour lire les conteneurs |
| `WEBHOOK_SECRET` | Secret de l'�metteur d'alertes |

Pour g�n�rer les trois secrets de l'API, lance cette commande trois fois et reporte chaque valeur dans `.env` :

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Ils doivent �tre diff�rents. Ne colle aucun secret dans le code, les captures ou les commits.

## D�marrer l'environnement

Commence par les services du cours pour pouvoir obtenir le token NetBox :

```bash
docker compose up -d docker-api netbox
docker pull nginx:alpine
docker pull redis:alpine
docker pull httpd:alpine
docker compose ps
```

NetBox prend plusieurs minutes au premier d�marrage. Ouvre `http://127.0.0.1:8080` quand le service est pr�t. Le compte du laboratoire fourni dans le compose est `admin` / `admin` ; il sert uniquement au TP local.

Cr�e un token avec les droits d'�criture depuis ton profil NetBox. Enregistre imm�diatement sa valeur compl�te dans `NETBOX_TOKEN`. La documentation de son API est disponible sur `http://127.0.0.1:8080/api/schema/swagger-ui/`.

Une fois `.env` rempli :

```bash
docker compose up -d --build api
```

L'API �coute sur `http://127.0.0.1:8000`, sa documentation sur `http://127.0.0.1:8000/docs`. Le compose utilise les noms des services pour joindre Docker et NetBox depuis le conteneur. Le cluster `docker-local` est cr�� automatiquement au premier enregistrement si besoin.

Pour d�velopper sans reconstruire l'image, arr�te le service `api`, puis lance :

```bash
docker compose stop api
uvicorn app.main:app --reload --env-file .env --host 127.0.0.1 --port 8000
```

Un seul des deux doit utiliser le port 8000.

## Routes

| M�thode | Route | Acc�s | R�sultat |
|---|---|---|---|
| GET | `/health` | Ouvert | �tat de l'API |
| GET | `/containers` | Lecteur ou admin | Conteneurs g�r�s |
| GET | `/containers/{id}` | Lecteur ou admin | D�tail d'un conteneur |
| POST | `/containers` | Admin | Cr�ation et d�marrage |
| POST | `/containers/{id}/stop` | Admin | Arr�t |
| POST | `/containers/{id}/start` | Admin | D�marrage |
| DELETE | `/containers/{id}` | Admin | Suppression, m�me si le conteneur tourne |
| POST | `/webhooks/alerts` | Secret du webhook | Red�marrage apr�s une alerte |
| GET | `/metrics` | Ouvert | M�triques Prometheus |

Les routes de conteneurs attendent `Authorization: Bearer <token>`. Un token absent ou inconnu renvoie 401 ; un lecteur qui tente une �criture re�oit 403. Un conteneur sans le label est trait� comme inexistant, m�me si son identifiant est connu.

Pour cr�er un conteneur, le corps JSON est :

```json
{"name": "web1", "image": "nginx:alpine"}
```

Le nom contient 2 � 31 caract�res : une minuscule, puis des minuscules, chiffres ou tirets. Les images accept�es sont `nginx:alpine`, `redis:alpine` et `httpd:alpine`.

Le webhook attend `X-Webhook-Secret: <secret>` et :

```json
{"alert": "container_down", "container": "web1"}
```

Il renvoie 202 apr�s traitement. Une alerte rejou�e sur un conteneur d�j� d�marr� ne le red�marre pas et n'incr�mente pas le compteur. Les actions d'�criture sont s�rialis�es pour �viter deux red�marrages sur des alertes simultan�es.

NetBox passe la machine virtuelle en `active` au d�marrage, en `offline` � l'arr�t et la supprime � la suppression du conteneur. Une panne d'un service externe renvoie 502. Docker et NetBox n'offrent pas de transaction commune : si NetBox tombe apr�s une action Docker, cette action peut d�j� avoir eu lieu. V�rifie les deux services avant de rejouer une �criture.

Le compteur `adminapi_remediations_total` est conserv� en m�moire et repart � z�ro au red�marrage de l'API. Le lancement pr�vu utilise un seul processus Uvicorn.

## Inventaire et surveillance

```bash
python inventaire.py
python surveillant.py
```

L'inventaire charge `.env`, liste tous les conteneurs de la machine et mesure le CPU et la m�moire de ceux qui tournent. Il suit la pagination NetBox et �crit la date du relev�, les conteneurs, la version NetBox et le nombre de types d'objets dans `inventaire.json`. Si un service ne r�pond pas, il affiche une erreur, sort avec le code 1 et conserve le relev� pr�c�dent.

Le surveillant fourni envoie une alerte toutes les cinq secondes pour les conteneurs g�r�s qui sont arr�t�s. Il se lance � part de l'API.

## V�rifications

Tests locaux, avec Docker et NetBox remplac�s par des services HTTP de test :

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Ils couvrent les six blocs du v�rificateur fourni, les droits d'acc�s, les entr�es invalides, les conteneurs sans label, les pannes de services, la pagination et les alertes simultan�es.

Validation sur les vrais services, une fois l'environnement lanc� et `.env` rempli :

```bash
python verifier.py
docker build -t tp-api-rest .
git log --all --oneline -- .env
```

La derni�re commande ne doit rien afficher. La [validation sur GitHub](https://github.com/wladilaw/tp-api-rest/actions/runs/40769794066) a r�ussi : 47 tests, construction de l'image et 50/50 v�rifications sur les vrais Docker et NetBox. Le script d'inventaire a aussi fonctionn�. Sur le poste local, Docker Desktop reste � d�marrer pour rejouer ces commandes et compl�ter le relev� TP1.

Le workflow GitHub lance les tests, construit l'image puis v�rifie le contrat sur un environnement Docker et NetBox temporaire. Les secrets de ce laboratoire sont g�n�r�s pendant l'ex�cution et ne sont pas publi�s.

## Organisation

| Fichier | R�le |
|---|---|
| `app/main.py` | Routes, authentification, webhook et m�triques |
| `app/models.py` | Validation des corps JSON et format des r�ponses |
| `app/docker_client.py` | Appels Docker et contr�le du label |
| `app/netbox_client.py` | Machines virtuelles, cluster et pagination |
| `app/http_client.py` | Session HTTP, d�lais et erreurs externes |
| `app/config.py` | Lecture et contr�le de la configuration |
| `inventaire.py` | Relev� Docker et NetBox du TP2 |
| `tp1-exploration.md` | Commandes de pr�paration du TP1 |
| `tests/` | V�rifications avec des services HTTP de test |

R�f�rences : [contrat du sujet](contrat-openapi.yaml), [sujet complet](sujet.html), [FastAPI](https://fastapi.tiangolo.com/), [API Docker](https://docs.docker.com/reference/api/engine/), [API NetBox](https://netboxlabs.com/docs/netbox/integrations/rest-api/).

