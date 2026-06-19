"""
queue_manager.py — Gestion de la queue de téléchargements MangaArr
- Stockage JSON persistant des items en queue
"""
import os, json, re, threading
from datetime import datetime
from urllib.parse import unquote
import config as _config_mod

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
    items    = get_queue()
    ebdz     = [i for i in items if i.get("source", "ebdz") not in ("torrent", "telegram")]
    torrent  = [i for i in items if i.get("source") == "torrent"]
    telegram = [i for i in items if i.get("source") == "telegram"]
    return {
        "total":        len(items),
        "pending":      sum(1 for i in items if i.get("status") == "pending"),
        "downloading":  sum(1 for i in items if i.get("status") == "downloading"),
        "done":         sum(1 for i in items if i.get("status") == "done"),
        "ebdz_total":    len(ebdz),
        "torrent_total": len(torrent),
        "torrent_pending": sum(1 for i in torrent if i.get("status") == "pending"),
        "torrent_done":    sum(1 for i in torrent if i.get("status") == "done"),
        "telegram_total":   len(telegram),
        "telegram_pending": sum(1 for i in telegram if i.get("status") == "pending"),
        "telegram_downloading": sum(1 for i in telegram if i.get("status") == "downloading"),
        "telegram_done":    sum(1 for i in telegram if i.get("status") == "done"),
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
    Retourne {"deleted": int}.
    """
    hashes = set(filehashes)
    with _queue_lock:
        items      = _load()
        to_delete  = [i for i in items if i.get("filehash", "") in hashes]
        kept       = [i for i in items if i.get("filehash", "") not in hashes]
        _save(kept)
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
                         or (source_filter == "torrent"  and i.get("source") == "torrent")
                         or (source_filter == "telegram" and i.get("source") == "telegram")
                         or (source_filter == "ebdz"     and i.get("source", "ebdz") not in ("torrent", "telegram")))
            if is_done and is_old and src_match:
                continue  # supprime
            kept.append(i)
        _save(kept)

def clear_queue():
    with _queue_lock:
        _save([])


