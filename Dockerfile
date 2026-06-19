# ──────────────────────────────────────────────
#  MangaArr — Dockerfile
# ──────────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="MangaArr"
LABEL description="Gestionnaire de manga — sans Komga"

# Outils système pour conversion CBR/PDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    unrar-free \
    p7zip-full \
    poppler-utils \
    imagemagick \
    libwebp-dev \
    amule-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY . .

# Dossiers internes + points de montage (existent même si un volume n'est pas monté)
RUN mkdir -p /data/config /data/cache \
             /incoming /media /qbt-category /torrent_files /telegram

# ══════════════════════════════════════════════════════════════
#  Variables d'environnement — CHEMINS INTERNES AU CONTENEUR
#  Ces valeurs sont FIXES : l'utilisateur n'a PAS à les redéfinir.
#  Il suffit de monter ses dossiers hôte sur ces points dans les
#  `volumes:` du docker-compose (ex. /mon/dossier/Mangas:/media).
# ══════════════════════════════════════════════════════════════
ENV MANGAARR_PORT=7474
ENV MANGAARR_CONFIG=/data/config/config.json
# Dossier eMule/aMule Incoming (lecture des téléchargements finis)
ENV MANGAARR_INCOMING=/incoming
# Dossier de destination des séries (là où vont les fichiers traités)
ENV MANGAARR_DEST=/media
# Cache metadata MangaDB + covers (persistant)
ENV MANGAARR_CACHE=/data/cache
# Dossier surveillé pour les torrents qBittorrent terminés
ENV MANGAARR_QBT_WATCH=/qbt-category
# Stockage des .torrent téléchargés avant envoi à qBittorrent
ENV MANGAARR_TORRENT_FILES=/torrent_files
# Dossier de téléchargement Telegram
ENV MANGAARR_TELEGRAM_WATCH=/telegram
# Fréquence du scan Incoming en secondes
ENV MANGAARR_INCOMING_INTERVAL=60

EXPOSE 7474

CMD ["python", "app.py"]
