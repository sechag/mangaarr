"""
queue_manager.py — Gestion de la queue de téléchargements MangaArr
- Stockage JSON persistant des items en queue
- Génération de fichiers .emulecollection datés
- Détection des tomes manquants via cache ebdz + Komga
"""
import os, json, re, threading
from datetime import datetime
from urllib.parse import unquote
import config as _config_mod

# EMULE_DIR : overridable via env var Docker (MANGAARR_EMULE)
EMULE_DIR = os.environ.get(
    "MANGAARR_EMULE",
    os.path.join(os.path.dirname(__file__), "emulecollections")
)
os.makedirs(EMULE_DIR, exist_ok=True)

def _cache_dir() -> str:
    """Résout le dossier cache (persistant en container via /data/cache)."""
    try:
        import config as _c
        d = _c.get("_cache_dir") or os.path.join(os.path.dirname(__file__), ".cache")
    except Exception:
        d = os.path.join(os.path.dirname(__file__), ".cache")
    os.makedirs(d, exist_ok=True)
    return d

def _queue_file() -> str:
    return os.path.join(_cache_dir(), "queue.json")

_queue_lock = threading.Lock()


# ═══════════════════════════════════════════════════
# PERSISTANCE
# ═══════════════════════════════════════════════════

def _load() -> list:
    try:
        with open(_queue_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save(items: list):
    with open(_queue_file(), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════
# ACCÈS QUEUE
# ═══════════════════════════════════════════════════

def get_queue() -> list:
    with _queue_lock:
        return _load()

def get_queue_stats() -> dict:
    items = get_queue()
    ebdz    = [i for i in items if i.get("source", "ebdz") != "torrent"]
    torrent = [i for i in items if i.get("source") == "torrent"]
    return {
        "total":        len(items),
        "pending":      sum(1 for i in items if i.get("status") == "pending"),
        "downloading":  sum(1 for i in items if i.get("status") == "downloading"),
        "done":         sum(1 for i in items if i.get("status") == "done"),
        "ebdz_total":    len(ebdz),
        "torrent_total": len(torrent),
        "torrent_pending": sum(1 for i in torrent if i.get("status") == "pending"),
        "torrent_done":    sum(1 for i in torrent if i.get("status") == "done"),
    }

def add_to_queue(items: list) -> dict:
    """
    Ajoute des items à la queue en une seule écriture disque.
    Retourne {"added": int, "skipped": int}.
    Doublons détectés par : filehash OU (series_name + tome_number) si item pending.
    """
    if not items:
        return {"added": 0, "skipped": 0}
    with _queue_lock:
        existing        = _load()
        existing_hashes = {i.get("filehash", "") for i in existing if i.get("filehash")}
        # Index (series_name_lower, tome_number) des items encore en attente
        pending_keys    = {
            (_norm_key(i.get("series_name", "")), str(i.get("tome_number", "")).strip())
            for i in existing
            if i.get("status") not in ("done",) and i.get("tome_number")
        }
        added   = 0
        skipped = 0
        now     = datetime.now().isoformat(timespec="seconds")
        for item in items:
            h = item.get("filehash", "")
            if h and h in existing_hashes:
                skipped += 1
                continue
            # Anti-doublon par (série, tome) pour les items en attente
            sn  = _norm_key(item.get("series_name", ""))
            tn  = str(item.get("tome_number", "")).strip()
            if sn and tn and (sn, tn) in pending_keys:
                skipped += 1
                continue
            item.setdefault("status",   "pending")
            item.setdefault("added_at", now)
            existing.append(item)
            if h:
                existing_hashes.add(h)
            if sn and tn:
                pending_keys.add((sn, tn))
            added += 1
        if added:
            _save(existing)
    return {"added": added, "skipped": skipped}


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


def delete_items(filehashes: list) -> dict:
    """
    Supprime des items de la queue par leurs filehashes.
    Met aussi à jour les fichiers .emulecollection existants (retire les liens ed2k).
    Retourne {"deleted": int}.
    """
    hashes = set(filehashes)
    with _queue_lock:
        items      = _load()
        to_delete  = [i for i in items if i.get("filehash", "") in hashes]
        kept       = [i for i in items if i.get("filehash", "") not in hashes]
        _save(kept)

    # Retire les liens ed2k des .emulecollection et .txt existants
    urls_to_remove = {i.get("url", "").strip() for i in to_delete if i.get("url")}
    if urls_to_remove and os.path.isdir(EMULE_DIR):
        for fname in os.listdir(EMULE_DIR):
            if not (fname.endswith(".emulecollection") or fname.endswith(".txt")):
                continue
            fpath = os.path.join(EMULE_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = [l for l in lines if l.strip() not in urls_to_remove]
                if len(new_lines) != len(lines):
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
            except Exception:
                pass

    return {"deleted": len(to_delete)}

def update_status(filehash: str, status: str, history: dict = None):
    """Met à jour le statut d'un item par son hash. Optionnellement ajoute l'historique."""
    with _queue_lock:
        items = _load()
        for item in items:
            if item.get("filehash") == filehash:
                item["status"] = status
                if status == "done":
                    item["done_at"] = datetime.now().isoformat(timespec="seconds")
                if history:
                    item["history"] = history
        _save(items)

def remove_done(older_than_days: int = 7, source_filter: str = None):
    """
    Supprime les items terminés depuis plus de N jours.
    source_filter : "ebdz" | "torrent" | None (tous)
    """
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
    with _queue_lock:
        items = _load()
        kept = []
        for i in items:
            is_done   = i.get("status") == "done"
            is_old    = i.get("done_at", "9999") < cutoff
            src_match = (source_filter is None
                         or (source_filter == "torrent" and i.get("source") == "torrent")
                         or (source_filter == "ebdz"    and i.get("source", "ebdz") != "torrent"))
            if is_done and is_old and src_match:
                continue  # supprime
            kept.append(i)
        _save(kept)

def clear_queue():
    with _queue_lock:
        _save([])


# ═══════════════════════════════════════════════════
# GÉNÉRATION .emulecollection
# ═══════════════════════════════════════════════════

MAX_LINKS_PER_FILE = 150  # Au-delà → découpe en parties

def _filter_items(items: list) -> list:
    """Applique les filtres profiles (must_contain/must_not_contain)."""
    try:
        import profiles as _p
        result = []
        for item in items:
            fn = item.get("filename", item.get("url", ""))
            ok, reason = _p.passes_filters(fn)
            if ok:
                result.append(item)
            else:
                config.add_log(f"[Collection] Ignoré ({reason}) : {fn}", "info")
        return result
    except Exception:
        return items


def _collection_ext() -> str:
    """Retourne .txt ou .emulecollection selon la config."""
    try:
        mm = _config_mod.get("media_management", {})
        if mm.get("emulecollection_as_txt", False):
            return ".txt"
    except Exception:
        pass
    return ".emulecollection"


def _write_collection_files(links: list, base_name: str) -> list:
    """
    Écrit une ou plusieurs parties .emulecollection / .txt (max MAX_LINKS_PER_FILE liens).
    Retourne la liste des chemins créés.
    """
    if not links:
        return []
    ext   = _collection_ext()
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = []
    parts = [links[i:i+MAX_LINKS_PER_FILE] for i in range(0, len(links), MAX_LINKS_PER_FILE)]
    for idx, part in enumerate(parts, 1):
        suffix = f"_part{idx}" if len(parts) > 1 else ""
        fname  = f"{ts}_{base_name}{suffix}{ext}"
        fpath  = os.path.join(EMULE_DIR, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(part) + "\n")
        paths.append(fpath)
    return paths


def generate_emulecollection(items: list = None, label: str = "",
                             series_prefix: str = "") -> str:
    """
    Génère les fichiers .emulecollection en séparant ADD et UPGRADE.
    series_prefix : si fourni, nomme le fichier {prefix}.{ts}_ADD.emulecollection
    Si >MAX_LINKS_PER_FILE liens → découpe en parties numérotées.
    Retourne le chemin du premier fichier créé (compatibilité).
    """
    if items is None:
        items = [i for i in get_queue() if i.get("status") == "pending"]

    items = _filter_items(items)
    if not items:
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        fpath = os.path.join(EMULE_DIR, f"{ts}_empty.emulecollection")
        open(fpath, "w").close()
        return fpath

    add_links     = [i["url"] for i in items if i.get("url","").startswith("ed2k://")
                     and i.get("action", "missing") != "upgrade"]
    upgrade_links = [i["url"] for i in items if i.get("url","").startswith("ed2k://")
                     and i.get("action") == "upgrade"]

    if series_prefix:
        ext    = _collection_ext()
        safe   = re.sub(r"[^A-Za-z0-9._\-]", "_", series_prefix)[:40]
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        all_paths = []
        if add_links:
            chunks = [add_links[i:i+MAX_LINKS_PER_FILE] for i in range(0, len(add_links), MAX_LINKS_PER_FILE)]
            for idx, chunk in enumerate(chunks, 1):
                suffix = f"_part{idx}" if len(chunks) > 1 else ""
                fname  = f"{safe}.{ts}_ADD{suffix}{ext}"
                fp     = os.path.join(EMULE_DIR, fname)
                with open(fp, "w", encoding="utf-8") as f:
                    f.write("\n".join(chunk) + "\n")
                all_paths.append(fp)
        if upgrade_links:
            fname = f"{safe}.{ts}_UPGRADE{ext}"
            fpath = os.path.join(EMULE_DIR, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("\n".join(upgrade_links) + "\n")
            all_paths.append(fpath)
        return all_paths[0] if all_paths else ""

    all_paths = []
    all_paths.extend(_write_collection_files(add_links,     "ADD"))
    all_paths.extend(_write_collection_files(upgrade_links, "UPGRADE"))

    if not all_paths:
        all_links = [i["url"] for i in items if i.get("url","").startswith("ed2k://")]
        safe      = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:20] if label else "missing"
        all_paths = _write_collection_files(all_links, safe)

    return all_paths[0] if all_paths else ""


def list_emulecollections() -> list:
    """Liste les fichiers .emulecollection et .txt générés [{filename, path, size, created}]."""
    files = []
    for fn in sorted(os.listdir(EMULE_DIR), reverse=True):
        if not (fn.endswith(".emulecollection") or fn.endswith(".txt")):
            continue
        fp = os.path.join(EMULE_DIR, fn)
        stat = os.stat(fp)
        files.append({
            "filename": fn,
            "path":     fp,
            "size_kb":  round(stat.st_size / 1024, 1),
            "created":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "links":    sum(1 for line in open(fp) if line.startswith("ed2k://")),
        })
    return files


# ═══════════════════════════════════════════════════
# DÉTECTION TOMES MANQUANTS
# ═══════════════════════════════════════════════════
