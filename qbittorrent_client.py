"""
qbittorrent_client.py — Client qBittorrent Web API pour MangaArr
"""
import os
import requests
import logging

log = logging.getLogger("mangaarr.qbt")


def _base(client: dict) -> str:
    host = client.get("host", "localhost").rstrip("/")
    port = client.get("port", 8080)
    if not str(host).startswith("http"):
        host = f"http://{host}"
    return f"{host}:{port}"


def _session(client: dict):
    """
    Crée une session authentifiée avec qBittorrent.
    Retourne (session, error_message).
    """
    base = _base(client)
    s = requests.Session()
    # qBittorrent exige le Content-Type form pour /auth/login
    s.headers.update({"Referer": base})
    try:
        r = s.post(
            f"{base}/api/v2/auth/login",
            data={
                "username": client.get("username", "admin"),
                "password": client.get("password", ""),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        body = r.text.strip()
        if body == "Ok.":
            return s, None
        if body == "Fails.":
            return None, "Identifiants incorrects (qBittorrent répond Fails.)"
        # Certaines versions sans auth retournent directement une page
        # Si on a un cookie SID, l'auth a quand même fonctionné
        if "SID" in s.cookies or r.status_code == 200:
            return s, None
        return None, f"Réponse inattendue de qBittorrent : {body[:80]}"
    except requests.exceptions.ConnectionError:
        return None, f"Impossible de joindre qBittorrent à {base} (connexion refusée)"
    except requests.exceptions.Timeout:
        return None, f"Timeout en tentant de joindre qBittorrent à {base}"
    except Exception as e:
        return None, str(e)


def test_connection(client: dict) -> dict:
    """Teste la connexion à qBittorrent."""
    s, err = _session(client)
    if s is None:
        return {"ok": False, "message": err or "Connexion échouée"}
    base = _base(client)
    try:
        r = s.get(f"{base}/api/v2/app/version", timeout=5)
        version = r.text.strip()
        if not version:
            return {"ok": False, "message": "Connecté mais réponse vide (version inconnue)"}
        return {"ok": True, "message": f"Connecté — qBittorrent {version}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _send_to_qbt(s, base: str, data: dict, files=None) -> dict:
    """Envoie la requête /torrents/add et interprète la réponse."""
    try:
        r = s.post(
            f"{base}/api/v2/torrents/add",
            data=data,
            files=files,
            timeout=30,
        )
        body = r.text.strip()
        log.info(f"[qBittorrent] torrents/add → HTTP {r.status_code} : {body!r}")
        cat = data.get("category", "")
        if body == "Ok.":
            return {"ok": True, "message": f"Torrent envoyé à qBittorrent ✓ (catégorie : {cat or 'aucune'})"}
        if body == "Fails.":
            return {"ok": False, "message": "qBittorrent a rejeté le torrent (Fails.)"}
        if r.status_code == 200:
            return {"ok": True, "message": f"Torrent envoyé ✓ (réponse : {body[:40] or 'vide'})"}
        return {"ok": False, "message": f"qBittorrent HTTP {r.status_code} : {body[:80]}"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "message": f"Impossible de joindre qBittorrent à {base}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def add_torrent(client: dict, url_or_magnet: str,
                save_path: str = "", category: str = "") -> dict:
    """
    Ajoute un torrent à qBittorrent via lien magnet ou URL.
    Retourne {"ok": bool, "message": str}.
    """
    s, err = _session(client)
    if s is None:
        return {"ok": False, "message": err or "Authentification qBittorrent échouée"}

    base = _base(client)
    cat  = category or client.get("category", "")

    data = {"urls": url_or_magnet}
    if save_path:
        data["savepath"] = save_path
    if cat:
        data["category"] = cat

    return _send_to_qbt(s, base, data)


def add_torrent_file(client: dict, file_path: str,
                     save_path: str = "", category: str = "") -> dict:
    """
    Envoie un fichier .torrent local à qBittorrent via upload multipart.
    Retourne {"ok": bool, "message": str}.
    """
    s, err = _session(client)
    if s is None:
        return {"ok": False, "message": err or "Authentification qBittorrent échouée"}

    base = _base(client)
    cat  = category or client.get("category", "")

    data = {}
    if save_path:
        data["savepath"] = save_path
    if cat:
        data["category"] = cat

    try:
        with open(file_path, "rb") as fh:
            filename = os.path.basename(file_path)
            files = {"torrents": (filename, fh, "application/x-bittorrent")}
            return _send_to_qbt(s, base, data, files=files)
    except OSError as e:
        return {"ok": False, "message": f"Impossible de lire le fichier .torrent : {e}"}


def get_torrents(client: dict, category: str = "") -> list:
    """Retourne la liste des torrents (filtrés par catégorie si précisé)."""
    s, _ = _session(client)
    if s is None:
        return []
    base = _base(client)
    params = {}
    cat = category or client.get("category", "")
    if cat:
        params["category"] = cat
    try:
        r = s.get(f"{base}/api/v2/torrents/info", params=params, timeout=10)
        return r.json()
    except Exception:
        return []


def get_torrent_files(client: dict, torrent_hash: str) -> list:
    """Retourne les fichiers d'un torrent par son hash."""
    s, _ = _session(client)
    if s is None:
        return []
    base = _base(client)
    try:
        r = s.get(f"{base}/api/v2/torrents/files",
                  params={"hash": torrent_hash}, timeout=10)
        return r.json()
    except Exception:
        return []


def create_category(client: dict, name: str, save_path: str = "") -> dict:
    """
    Crée ou met à jour une catégorie dans qBittorrent.
    Si la catégorie existe déjà avec le même save_path, ne fait rien.
    Retourne {"ok": bool, "message": str}.
    """
    if not name:
        return {"ok": False, "message": "Nom de catégorie requis"}

    s, err = _session(client)
    if s is None:
        return {"ok": False, "message": err or "Authentification échouée"}

    base = _base(client)

    # Vérifie si la catégorie existe déjà
    try:
        r = s.get(f"{base}/api/v2/torrents/categories", timeout=10)
        categories = r.json()
        if name in categories:
            existing_path = categories[name].get("savePath", "")
            if not save_path or existing_path == save_path:
                return {"ok": True, "message": f"Catégorie '{name}' déjà configurée"}
            # Met à jour le save_path
            r2 = s.post(
                f"{base}/api/v2/torrents/editCategory",
                data={"category": name, "savePath": save_path},
                timeout=10,
            )
            if r2.status_code == 200:
                return {"ok": True, "message": f"Catégorie '{name}' mise à jour (chemin : {save_path})"}
            return {"ok": False, "message": f"Impossible de mettre à jour la catégorie : HTTP {r2.status_code}"}
    except Exception:
        pass  # Continue vers la création

    # Crée la catégorie
    try:
        r = s.post(
            f"{base}/api/v2/torrents/createCategory",
            data={"category": name, "savePath": save_path or ""},
            timeout=10,
        )
        if r.status_code == 200:
            return {"ok": True, "message": f"Catégorie '{name}' créée" + (f" (chemin : {save_path})" if save_path else "")}
        return {"ok": False, "message": f"Erreur création catégorie : HTTP {r.status_code} {r.text[:60]}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}
