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

# Dossiers internes persistants
RUN mkdir -p /data/config /data/cache /data/emulecollections

# ── Variables d'environnement ──────────────────────────
ENV MANGAARR_PORT=7474
ENV MANGAARR_CONFIG=/data/config/config.json

# Chemins montés via volumes — définis dans docker-compose / Unraid template
# MANGAARR_INCOMING : dossier eMule/aMule Incoming (lecture des téléchargements finis)
ENV MANGAARR_INCOMING=/incoming
# MANGAARR_DEST : dossier de destination des séries (là où vont les fichiers traités)
ENV MANGAARR_DEST=/media
# MANGAARR_CACHE : cache metadata MangaDB + covers (persistant)
ENV MANGAARR_CACHE=/data/cache
# MANGAARR_EMULE : dossier de sortie des .emulecollection
ENV MANGAARR_EMULE=/data/emulecollections
# MANGAARR_INCOMING_INTERVAL : fréquence du scan Incoming en secondes (défaut: 60)
ENV MANGAARR_INCOMING_INTERVAL=60
# MANGAARR_INCOMING_INTERVAL : fréquence scan Incoming en secondes (défaut: 60)
ENV MANGAARR_INCOMING_INTERVAL=60

EXPOSE 7474

CMD ["python", "app.py"]
