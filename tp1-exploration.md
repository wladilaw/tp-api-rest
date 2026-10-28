# TP1 : exploration des API

Les commandes sont prévues pour Git Bash. Les codes ci-dessous sont les résultats attendus ; ils restent à relever sur la machine après le démarrage de Docker. Aucun résultat réel n'est renseigné pour le moment.

## Docker

```bash
docker run -d --name web1 --label adminapi.managed=true nginx:alpine
docker run -d --name intrus nginx:alpine

D=http://127.0.0.1:2375
curl -i "$D/version"
curl -i "$D/containers/json?all=true"
curl -i --get "$D/containers/json" --data-urlencode 'all=true' \
  --data-urlencode 'filters={"label":["adminapi.managed=true"]}'
curl -i "$D/containers/web1/json"
curl -i -X POST "$D/containers/web1/stop?t=1"
curl -i -X POST "$D/containers/web1/stop?t=1"
curl -i "$D/containers/conteneur-inconnu/json"
```

| Appel | Code attendu | Code constaté |
|---|---|---|
| Version Docker | 200 | À relever |
| Tous les conteneurs | 200 | À relever |
| Conteneurs avec label | 200, `web1` présent et `intrus` absent | À relever |
| Détail de `web1` | 200 | À relever |
| Premier arrêt | 204 | À relever |
| Second arrêt | 304 | À relever |
| Conteneur inconnu | 404 | À relever |

## NetBox

Créer un token depuis le profil NetBox ou son endpoint de provisionnement, puis enregistrer sa valeur complète dans `.env`. Ne pas copier le token dans ce fichier.

Avec les variables chargées dans Git Bash :

```bash
set -a
source .env
set +a
N="$NETBOX_URL/api"

curl -i "$N/status/" -H "Authorization: Bearer $NETBOX_TOKEN"
curl -i "$N/virtualization/cluster-types/" \
  -H "Authorization: Bearer $NETBOX_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Docker","slug":"docker"}'
```

Relever l'identifiant du type Docker renvoyé, puis le saisir dans une variable :

```bash
read -p "Identifiant du type Docker : " TYPE_ID
curl -i "$N/virtualization/clusters/" \
  -H "Authorization: Bearer $NETBOX_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"docker-local\",\"type\":$TYPE_ID}"
```

Le statut attend 200 ; les deux créations attendent 201. Si le type ou le cluster existe déjà, le relire par un GET au lieu de le recréer. Les codes constatés et la présence du cluster dans NetBox restent à vérifier.

Dépôt : https://github.com/wladilaw/tp-api-rest
