# MangaArr

**Gestionnaire de manga automatisé** — récupère, renomme, convertit et organise ta bibliothèque depuis eMule/aMule, qBittorrent, Telegram et ebdz.net, avec enrichissement des métadonnées via MangaDB.

Au premier lancement, un **assistant de configuration** te guide pas à pas (librairies, clients de téléchargement, sources, automatisation). Tu n'as donc presque rien à régler dans Docker.

---

## 🚀 Installation Docker (simple)

> **L'idée :** tu ne configures que **tes dossiers**. Tous les réglages internes ont déjà des valeurs par défaut dans l'image — **aucune variable d'environnement n'est obligatoire**.

### 1. Récupère le `docker-compose.yml`

```bash
mkdir mangaarr && cd mangaarr
curl -O https://raw.githubusercontent.com/sechag/mangaarr/main/docker-compose.yml
```

### 2. Indique tes dossiers

Ouvre `docker-compose.yml` et **modifie uniquement la partie à GAUCHE des `:`** dans la section `volumes:`.
La partie à droite (`/media`, `/incoming`, `/qbt-category`…) ne se touche **jamais** : ce sont les chemins internes du conteneur.

```yaml
volumes:
  - /mon/dossier/aMule/Incoming:/incoming:ro      # ← édite la gauche
  - /mon/dossier/Mangas:/media:rw                 # ← édite la gauche
  - /mon/dossier/qbittorrent/Mangaarr:/qbt-category:rw
  - /mon/dossier/torrent_files:/torrent_files:rw
  - /mon/dossier/telegram:/telegram:rw
```

### 3. Lance

```bash
docker compose up -d
```

Ouvre **http://IP-DU-SERVEUR:7474** → l'assistant de configuration démarre automatiquement. 🎉

---

## 📁 Les volumes à mapper

Seule la colonne « Ton dossier hôte » te concerne. Le chemin conteneur est fixe.

| Ton dossier hôte (à adapter) | Chemin conteneur (fixe) | Rôle | Obligatoire ? |
|---|---|---|---|
| `/mnt/user/appdata/mangaarr/config` | `/data/config` | Config persistante | ✅ Oui |
| `/mnt/user/appdata/mangaarr/cache` | `/data/cache` | Cache metadata + couvertures | ✅ Oui |
| `/mnt/user/appdata/mangaarr/emulecollections` | `/data/emulecollections` | Fichiers `.emulecollection` | ✅ Oui |
| Ton dossier Incoming aMule | `/incoming` | Téléchargements eMule/aMule finis | Si tu utilises eMule/aMule |
| Ta bibliothèque manga | `/media` | Destination des séries traitées | ✅ Oui |
| Dossier de la catégorie qBittorrent | `/qbt-category` | Torrents terminés à traiter | Si tu utilises qBittorrent |
| Un dossier dédié `.torrent` | `/torrent_files` | Stockage des `.torrent` | Si tu utilises qBittorrent |
| Dossier de DL Telegram | `/telegram` | Téléchargements Telegram | Si tu utilises Telegram |

---

## ⚙️ Variables d'environnement

**Aucune n'est obligatoire** : l'image fournit déjà toutes les valeurs par défaut ci-dessous. Tu peux en surcharger une dans `environment:` seulement si tu veux changer le défaut.

| Variable | Défaut | Description |
|---|---|---|
| `TZ` | système | Fuseau horaire (ex. `Europe/Paris`) — la seule qu'on conseille de régler |
| `MANGAARR_PORT` | `7474` | Port HTTP interne |
| `MANGAARR_DEST` | `/media` | Destination des séries |
| `MANGAARR_INCOMING` | `/incoming` | Dossier Incoming eMule/aMule |
| `MANGAARR_QBT_WATCH` | `/qbt-category` | Dossier surveillé pour les torrents terminés |
| `MANGAARR_TORRENT_FILES` | `/torrent_files` | Stockage des `.torrent` |
| `MANGAARR_TELEGRAM_WATCH` | `/telegram` | Dossier de téléchargement Telegram |
| `MANGAARR_CACHE` | `/data/cache` | Cache metadata + covers |
| `MANGAARR_EMULE` | `/data/emulecollections` | Dossier `.emulecollection` |
| `MANGAARR_CONFIG` | `/data/config/config.json` | Fichier de config |
| `MANGAARR_INCOMING_INTERVAL` | `60` | Fréquence du scan Incoming (secondes) |

---

## 🧲 À propos de qBittorrent

MangaArr ne traite (renommage/conversion/rangement) que les fichiers qu'il **voit** dans `/qbt-category`. Pour que ça marche :

1. Monte sur `/qbt-category` le **dossier physique** où qBittorrent dépose les torrents terminés.
2. Dans MangaArr → **Settings → Download Client** (ou via l'assistant), renseigne ton client qBittorrent. Le **save path** de la catégorie se configure là (ou directement dans qBittorrent) : il doit pointer vers **ce même dossier physique**.

> Il n'y a **pas** de variable d'environnement pour le save path de qBittorrent : ce réglage se fait dans l'interface (par client), car c'est qBittorrent qui écrit les fichiers.

---

## 🔄 Mises à jour

MangaArr suit le versioning sémantique. Le tag `latest` pointe toujours vers la dernière version stable.

```bash
docker compose pull && docker compose up -d
```

Pour épingler une version précise, dans `docker-compose.yml` :
```yaml
image: ghcr.io/sechag/mangaarr:v1.0.9
```

Versions et changelogs : [Releases GitHub](https://github.com/sechag/mangaarr/releases).

---

## 🛠️ Build depuis les sources

```bash
git clone https://github.com/sechag/mangaarr.git
cd mangaarr
# Dans docker-compose.yml, remplace la ligne « image: » par « build: . »
docker compose up -d --build
```
