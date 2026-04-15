"""
library_manager.py — Gestion des librairies locales (sans Komga)

Une librairie = un dossier racine contenant des sous-dossiers (= séries).
Fonctions principales :
  get_libraries()         — Liste les librairies configurées
  scan_library(lib_id)    — Scanne les séries et tomes d'une librairie
  get_series_by_id()      — Retrouve une série par son ID (hash MD5 du path)
  get_series_cover()      — Extrait la cover (via image_cache)
  start_watcher()         — Surveillance des nouveaux fichiers
"""
import os, json, threading, hashlib, zipfile, re
import config as _cfg

_LOCK = threading.Lock()


# ════════════════════════════════════════════════════════
# LIBRAIRIES (config)
# ════════════════════════════════════════════════════════

def get_libraries() -> list:
    """Retourne la liste des librairies configurées."""
    return _cfg.get("libraries", [])


def add_library(name: str, path: str) -> dict:
    """Ajoute une librairie. Retourne {ok, id, message}."""
    if not name or not path:
        return {"ok": False, "message": "Nom et chemin requis"}
    if not os.path.isdir(path):
        return {"ok": False, "message": f"Dossier introuvable : {path}"}
    cfg  = _cfg.load()
    libs = cfg.setdefault("libraries", [])
    # Vérifie doublon
    if any(l["path"] == path for l in libs):
        return {"ok": False, "message": "Cette librairie est déjà ajoutée"}
    lib_id = hashlib.md5(path.encode()).hexdigest()[:8].upper()
    lib = {"id": lib_id, "name": name, "path": path}
    libs.append(lib)
    _cfg.save(cfg)
    return {"ok": True, "id": lib_id, "message": f"Librairie '{name}' ajoutée"}


def delete_library(lib_id: str) -> dict:
    cfg  = _cfg.load()
    libs = cfg.get("libraries", [])
    cfg["libraries"] = [l for l in libs if l["id"] != lib_id]
    _cfg.save(cfg)
    return {"ok": True}


def get_library(lib_id: str) -> dict | None:
    return next((l for l in get_libraries() if l["id"] == lib_id), None)


# ════════════════════════════════════════════════════════
# SCAN : séries dans une librairie
# ════════════════════════════════════════════════════════

def scan_library(lib_id: str) -> list:
    """
    Scanne une librairie et retourne la liste des séries avec leurs tomes.
    Utilise le cache image pour les covers.
    """
    import renamer as _r
    lib = get_library(lib_id)
    if not lib or not os.path.isdir(lib["path"]):
        return []

    series_list = []
    root = lib["path"]

    for entry in sorted(os.listdir(root)):
        series_dir = os.path.join(root, entry)
        if not os.path.isdir(series_dir):
            continue

        files = sorted([
            f for f in os.listdir(series_dir)
            if f.lower().endswith((".cbz", ".cbr", ".pdf"))
        ])
        # On garde même les dossiers vides (série ajoutée mais sans fichiers encore)

        # Extrait les numéros de tomes
        tomes = []
        for fn in files:
            t = _r.detect_tome(fn)
            n = int(t.lstrip("T").lstrip("0") or "0") if t else None
            tomes.append({"filename": fn, "numero": n})

        tomes_sorted = sorted(tomes, key=lambda x: x["numero"] or 0)
        first_file   = os.path.join(series_dir, tomes_sorted[0]["filename"]) if tomes_sorted else None

        series_id = _series_id(series_dir)
        series_list.append({
            "id":          series_id,
            "name":        entry,
            "path":        series_dir,
            "lib_id":      lib_id,
            "booksCount":  len(files),
            "tomes":       tomes_sorted,
            "first_file":  first_file,
            "thumbnail":   f"/api/local/series/{series_id}/thumbnail",
            "slug":        _slugify(entry) + "--" + series_id,
        })

    return series_list


def _series_id(path: str) -> str:
    """ID stable basé sur le chemin du dossier."""
    return hashlib.md5(path.encode()).hexdigest()[:12].upper()


def _slugify(name: str) -> str:
    import unicodedata
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s]+", "-", name)
    return re.sub(r"-+", "-", name).strip("-")


def get_series_by_id(series_id: str) -> dict | None:
    """Cherche une série dans toutes les librairies par son ID."""
    for lib in get_libraries():
        lib_id = lib["id"]
        root   = lib["path"]
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            series_dir = os.path.join(root, entry)
            if os.path.isdir(series_dir) and _series_id(series_dir) == series_id:
                # Reconstruit les infos
                series_list = scan_library(lib_id)
                return next((s for s in series_list if s["id"] == series_id), None)
    return None


def resolve_slug(slug: str) -> dict | None:
    """Résout un slug (nom--ID) vers une série."""
    parts = slug.rsplit("--", 1)
    sid   = parts[1] if len(parts) == 2 else slug
    return get_series_by_id(sid)


# ════════════════════════════════════════════════════════
# COVERS — EXTRACTION ET CACHE
# ════════════════════════════════════════════════════════

import config as _cfg

def _cover_cache_dir() -> str:
    base = _cfg.get("_cache_dir") or os.path.join(os.path.dirname(__file__), ".cache")
    d = os.path.join(base, "covers")
    os.makedirs(d, exist_ok=True)
    return d


def get_series_cover(series_id: str, series_info: dict = None) -> bytes | None:
    """
    Retourne les bytes de la cover de la série (= première page du tome 1).
    Met en cache sur disque. Recrée seulement si le fichier source a changé.
    """
    cache_dir  = _cover_cache_dir()
    cover_path = os.path.join(cache_dir, f"series_{series_id}.jpg")

    if series_info is None:
        series_info = get_series_by_id(series_id)
    if not series_info:
        return None

    first_file = series_info.get("first_file")
    if not first_file or not os.path.isfile(first_file):
        return None

    # Vérifie si le cache est valide (même fichier, même mtime)
    meta_path = cover_path + ".meta"
    try:
        if os.path.exists(cover_path) and os.path.exists(meta_path):
            with open(meta_path) as f:
                saved_meta = json.load(f)
            cur_mtime = os.path.getmtime(first_file)
            if (saved_meta.get("source") == first_file and
                    abs(saved_meta.get("mtime", 0) - cur_mtime) < 1):
                with open(cover_path, "rb") as f:
                    return f.read()
    except Exception:
        pass

    # Extrait la première image du CBZ/CBR
    img_data = _extract_first_image(first_file)
    if img_data:
        try:
            with open(cover_path, "wb") as f:
                f.write(img_data)
            with open(meta_path, "w") as f:
                json.dump({"source": first_file, "mtime": os.path.getmtime(first_file)}, f)
        except Exception:
            pass
    return img_data


def get_book_cover(series_id: str, filename: str) -> bytes | None:
    """
    Retourne la cover d'un tome spécifique (première image du fichier).
    Met en cache sur disque.
    """
    cache_dir  = _cover_cache_dir()
    file_hash  = hashlib.md5(filename.encode()).hexdigest()[:12]
    cover_path = os.path.join(cache_dir, f"book_{series_id}_{file_hash}.jpg")
    meta_path  = cover_path + ".meta"

    series_info = get_series_by_id(series_id)
    if not series_info:
        return None

    file_path = os.path.join(series_info["path"], filename)
    if not os.path.isfile(file_path):
        return None

    # Vérifie cache
    try:
        if os.path.exists(cover_path) and os.path.exists(meta_path):
            with open(meta_path) as f:
                saved = json.load(f)
            if (saved.get("source") == file_path and
                    abs(saved.get("mtime", 0) - os.path.getmtime(file_path)) < 1):
                with open(cover_path, "rb") as f:
                    return f.read()
    except Exception:
        pass

    img_data = _extract_first_image(file_path)
    if img_data:
        try:
            with open(cover_path, "wb") as f:
                f.write(img_data)
            with open(meta_path, "w") as f:
                json.dump({"source": file_path, "mtime": os.path.getmtime(file_path)}, f)
        except Exception:
            pass
    return img_data


def _extract_first_image(file_path: str) -> bytes | None:
    """Extrait la première image d'un CBZ/CBR/PDF."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext in (".cbz", ".zip"):
            with zipfile.ZipFile(file_path, "r") as zf:
                imgs = sorted([
                    n for n in zf.namelist()
                    if os.path.splitext(n)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")
                    and not os.path.basename(n).startswith(".")
                ])
                if imgs:
                    return zf.read(imgs[0])
        elif ext == ".cbr":
            import subprocess, tempfile
            with tempfile.TemporaryDirectory() as tmp:
                for cmd in [["unrar", "x", "-y", file_path, tmp],
                             ["7z", "x", file_path, f"-o{tmp}", "-y"]]:
                    try:
                        r = subprocess.run(cmd, capture_output=True, timeout=30)
                        if r.returncode == 0:
                            break
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        continue
                imgs = sorted([
                    os.path.join(dp, f)
                    for dp, _, fs in os.walk(tmp)
                    for f in fs
                    if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")
                ])
                if imgs:
                    with open(imgs[0], "rb") as f:
                        return f.read()
        elif ext == ".pdf":
            import subprocess, tempfile
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "page")
                r = subprocess.run(
                    ["pdftoppm", "-jpeg", "-r", "72", "-l", "1", file_path, out],
                    capture_output=True, timeout=30
                )
                if r.returncode == 0:
                    imgs = sorted(os.listdir(tmp))
                    if imgs:
                        with open(os.path.join(tmp, imgs[0]), "rb") as f:
                            return f.read()
    except Exception:
        pass
    return None


def clear_cover_cache(series_id: str = None):
    """Vide le cache de covers (toutes ou pour une série)."""
    cache_dir = _cover_cache_dir()
    prefix    = f"series_{series_id}" if series_id else None
    removed   = 0
    try:
        for fn in os.listdir(cache_dir):
            if prefix is None or fn.startswith(prefix):
                try:
                    os.remove(os.path.join(cache_dir, fn))
                    removed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return removed


def cover_cache_stats() -> dict:
    cache_dir = _cover_cache_dir()
    files = [f for f in os.listdir(cache_dir) if f.endswith(".jpg")] if os.path.isdir(cache_dir) else []
    size  = sum(os.path.getsize(os.path.join(cache_dir, f)) for f in files if os.path.isfile(os.path.join(cache_dir, f)))
    return {"count": len(files), "size_kb": round(size / 1024, 1), "path": cache_dir}


# ════════════════════════════════════════════════════════
# WATCHER — Détection de nouveaux fichiers
# ════════════════════════════════════════════════════════

_watcher_thread  = None
_watcher_stop    = threading.Event()
_last_scan_state = {}  # {lib_id: {series_name: set(filenames)}}


def _snapshot(lib_id: str) -> dict:
    """Prend un snapshot de l'état actuel d'une librairie."""
    lib = get_library(lib_id)
    if not lib or not os.path.isdir(lib["path"]):
        return {}
    snap = {}
    for entry in os.listdir(lib["path"]):
        d = os.path.join(lib["path"], entry)
        if not os.path.isdir(d):
            continue
        snap[entry] = set(
            f for f in os.listdir(d)
            if f.lower().endswith((".cbz", ".cbr", ".pdf"))
        )
    return snap


def start_watcher(interval_seconds: int = 0):
    """
    Démarre le watcher de détection de nouveaux fichiers.
    interval_seconds=0 → désactivé.
    """
    global _watcher_thread, _watcher_stop

    # Arrête le watcher précédent
    _watcher_stop.set()
    if _watcher_thread and _watcher_thread.is_alive():
        _watcher_thread.join(timeout=2)

    if interval_seconds <= 0:
        _cfg.add_log("Watcher désactivé", "info")
        return

    _watcher_stop = threading.Event()

    def _watch():
        import time
        _cfg.add_log(f"Watcher démarré (intervalle {interval_seconds}s)", "info")
        # Snapshot initial
        state = {}
        for lib in get_libraries():
            state[lib["id"]] = _snapshot(lib["id"])

        while not _watcher_stop.wait(interval_seconds):
            for lib in get_libraries():
                lid     = lib["id"]
                new_snap = _snapshot(lid)
                old_snap = state.get(lid, {})

                # Détecte nouveaux fichiers
                new_files = []
                for series_name, files in new_snap.items():
                    old_files = old_snap.get(series_name, set())
                    added = files - old_files
                    for fn in added:
                        new_files.append((series_name, fn))

                if new_files:
                    _cfg.add_log(f"[Watcher] {len(new_files)} nouveau(x) fichier(s) détecté(s) dans '{lib['name']}'", "info")
                    for series_name, fn in new_files:
                        _cfg.add_log(f"[Watcher]   → {series_name}/{fn}", "info")
                    # Invalide le cache cover des séries concernées
                    for series_name, _ in new_files:
                        series_dir = os.path.join(lib["path"], series_name)
                        sid = _series_id(series_dir)
                        clear_cover_cache(sid)

                state[lid] = new_snap

    _watcher_thread = threading.Thread(target=_watch, daemon=True)
    _watcher_thread.start()
