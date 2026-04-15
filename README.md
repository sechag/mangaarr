# MangaArr

Gestionnaire de manga automatisé : eMule/qBittorrent + ebdz.net + MangaDB.

## Installation rapide (utilisateurs)

### 1. Télécharge le docker-compose.yml

```bash
curl -O https://raw.githubusercontent.com/sechag/mangaarr/main/docker-compose.yml
```

### 2. Adapte tes chemins

Ouvre `docker-compose.yml` et remplace les chemins d'exemple par les tiens :

| Placeholder | À remplacer par |
|---|---|
| `/chemin/vers/aMule/Incoming` | Ton dossier Incoming eMule/aMule |
| `/chemin/vers/Mangas` | Ton dossier de destination mangas |
| `/mnt/user/Download/complete/Mangaarr` | Ton dossier de catégorie qBittorrent |
| `/chemin/vers/torrent_files` | Un dossier pour stocker les .torrent |

### 3. Lance

```bash
docker compose up -d
```

Accès : **http://localhost:7474**

---

## Mises à jour

MangaArr utilise le versioning sémantique. Le tag `latest` pointe toujours vers la dernière version stable.

### Mise à jour automatique (recommandé)

```bash
docker compose pull && docker compose up -d
```

### Épingler une version (si tu veux de la stabilité)

Dans ton `docker-compose.yml`, remplace :
```yaml
image: ghcr.io/sechag/mangaarr:latest
```
par une version spécifique :
```yaml
image: ghcr.io/sechag/mangaarr:v1.2.0
```

Consulte les [releases GitHub](https://github.com/sechag/mangaarr/releases) pour voir les versions disponibles et les changelogs.

---

## Volumes

| Chemin conteneur | Usage |
|---|---|
| `/data/config` | Config JSON (persistant) |
| `/data/cache` | Cache metadata + covers (persistant) |
| `/data/emulecollections` | Fichiers .emulecollection (persistant) |
| `/incoming` | Dossier Incoming eMule (lecture) |
| `/media` | Destination des séries (lecture/écriture) |
| `/qbt-category` | Catégorie qBittorrent (lecture/écriture) |
| `/torrent_files` | Stockage .torrent (lecture/écriture) |

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `MANGAARR_PORT` | `7474` | Port HTTP |
| `MANGAARR_CONFIG` | `/data/config/config.json` | Chemin du fichier de config |
| `MANGAARR_CACHE` | `/data/cache` | Dossier cache metadata |
| `MANGAARR_EMULE` | `/data/emulecollections` | Dossier .emulecollection |
| `MANGAARR_INCOMING` | `/incoming` | Dossier Incoming eMule |
| `MANGAARR_DEST` | `/media` | Dossier destination séries |
| `MANGAARR_INCOMING_INTERVAL` | `60` | Intervalle scan Incoming (secondes) |
| `MANGAARR_QBT_WATCH` | — | Dossier catégorie qBittorrent |
| `MANGAARR_QBT_SAVE_PATH` | — | Save path qBittorrent (optionnel) |
| `MANGAARR_TORRENT_FILES` | — | Dossier stockage .torrent |
| `TZ` | système | Fuseau horaire |

---

## Build depuis les sources

```bash
git clone https://github.com/sechag/mangaarr.git
cd mangaarr
# Édite docker-compose.yml pour utiliser build: . au lieu de image:
docker compose up -d --build
```
