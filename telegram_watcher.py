"""
telegram_watcher.py — Surveillance du dossier de téléchargements Telegram

Fallback pour les fichiers déposés manuellement dans le dossier Telegram,
ou en cas de reprise après crash.
Suit le même pattern que watcher.py (eMule) et torrent_watcher.py.

Le téléchargement normal passe par telegram_client.start_download() qui
appelle file_organizer directement. Ce watcher est un filet de sécurité.
"""
import os, re, unicodedata, threading, logging
from datetime import datetime

import config
import queue_manager

log = logging.getLogger("mangaarr.telegram_watcher")

_WATCH_INTERVAL = 60
_MANGA_EXTS     = (".cbz", ".cbr", ".pdf", ".zip")

_watcher_thread = None
_stop_event     = threading.Event()


def get_watch_path() -> str:
    return os.environ.get("MANGAARR_TELEGRAM_WATCH", "").strip()


# ══════════════════════════════════════════════════════
# DÉMARRAGE / ARRÊT
# ══════════════════════════════════════════════════════

def start_watcher():
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return
    watch_path = get_watch_path()
    if not watch_path:
        log.info("[TelegramWatcher] MANGAARR_TELEGRAM_WATCH non défini — watcher désactivé")
        return
    _stop_event.clear()
    _watcher_thread = threading.Thread(
        target=_watch_loop, name="telegram-watcher", daemon=True
    )
    _watcher_thread.start()
    log.info("[TelegramWatcher] Démarré — surveille %s (intervalle %ds)", watch_path, _WATCH_INTERVAL)


def stop_watcher():
    _stop_event.set()


# ══════════════════════════════════════════════════════
# BOUCLE PRINCIPALE
# ══════════════════════════════════════════════════════

def _watch_loop():
    while not _stop_event.is_set():
        try:
            _run_once()
        except Exception as e:
            log.error("[TelegramWatcher] Erreur : %s", e, exc_info=True)
        _stop_event.wait(_WATCH_INTERVAL)


def _run_once():
    watch_path = get_watch_path()
    if not watch_path or not os.path.isdir(watch_path):
        return
    result = do_scan_telegram_incoming(watch_path)
    if result["updated"] > 0:
        log.info("[TelegramWatcher] %s", result["message"])


# ══════════════════════════════════════════════════════
# SCAN PRINCIPAL
# ══════════════════════════════════════════════════════

def do_scan_telegram_incoming(watch_path: str) -> dict:
    """
    Parcourt le dossier watch_path et associe les fichiers présents
    aux items Telegram en attente dans la queue.
    """
    import file_organizer as _fo

    if not os.path.isdir(watch_path):
        return {"ok": False, "updated": 0, "message": f"watch_path introuvable : {watch_path}", "errors": []}

    pending = [i for i in queue_manager.get_queue()
               if i.get("source") == "telegram"
               and i.get("status") not in ("done", "error")]

    if not pending:
        return {"ok": True, "updated": 0, "message": "Aucun item Telegram en attente", "errors": []}

    # Index des fichiers présents dans le dossier
    files_in_dir = {}
    for fn in os.listdir(watch_path):
        if not any(fn.lower().endswith(ext) for ext in _MANGA_EXTS):
            continue
        # Ignore les fichiers partiels (suffixe courant des DL en cours)
        if fn.endswith(".part") or fn.endswith(".tmp"):
            continue
        files_in_dir[_norm(fn)] = (fn, os.path.join(watch_path, fn))

    if not files_in_dir:
        return {"ok": True, "updated": 0, "message": "Dossier Telegram vide", "errors": []}

    updated = 0
    errors  = []
    now     = datetime.now()

    for item in list(pending):
        expected_fn = item.get("filename", "")
        if not expected_fn:
            continue

        # Cherche par nom exact normalisé
        norm_expected = _norm(expected_fn)
        matched_fn    = None
        matched_path  = None

        if norm_expected in files_in_dir:
            matched_fn, matched_path = files_in_dir[norm_expected]
        else:
            # Recherche fuzzy par série + tome
            series = item.get("series_name", "")
            tomes  = item.get("tomes", [])
            if series and tomes:
                s_norm = _norm(series)
                s_words = [w for w in s_norm.split() if len(w) > 2]
                n = int(tomes[0]) if tomes else 0

                for fnorm, (fn, fp) in files_in_dir.items():
                    if not all(w in fnorm for w in s_words):
                        continue
                    if n:
                        patterns = [
                            rf'(?:T|Tome)[.\-_ ]*0*{n}(?:\b|[.\-_@])',
                            rf'(?<![0-9])0*{n:02d}(?![0-9])',
                        ]
                        if not any(re.search(p, fn, re.IGNORECASE) for p in patterns):
                            continue
                    matched_fn, matched_path = fn, fp
                    break

        if not matched_fn:
            continue

        # Fichier déjà en cours de traitement (status downloading = dl via telegram_client)
        if item.get("status") == "downloading":
            continue

        log.info("[TelegramWatcher] ✓ %s → %s T%s",
                 matched_fn, item.get("series_name", "?"), item.get("tome_number", "?"))

        item["local_path"] = matched_path

        try:
            result = _fo.organize_file(item)
            history = {
                "source_file":  matched_fn,
                "processed_at": now.isoformat(timespec="seconds"),
            }
            if result.get("ok"):
                history["dest_filename"] = os.path.basename(result.get("dest_path", ""))
                config.add_log(
                    f"[Telegram] ✓ {item.get('series_name','?')} T{item.get('tome_number','?')}"
                    f" : {matched_fn} → {history['dest_filename']}",
                    "info",
                )
                _update_status(item, "done", history)
                updated += 1
            else:
                msg = result.get("message", "?")
                config.add_log(f"[Telegram] ✗ Organisation : {msg}", "warning")
                errors.append(msg)
        except Exception as e:
            log.error("[TelegramWatcher] organize_file : %s", e, exc_info=True)
            errors.append(str(e))

    msg = f"{updated} fichier(s) Telegram traité(s)"
    if errors:
        msg += f" ({len(errors)} erreur(s))"
    return {"ok": True, "updated": updated, "message": msg, "errors": errors}


# ══════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c if c.isalnum() or c.isspace() else " "
                for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def _update_status(item: dict, status: str, history: dict = None):
    """Met à jour le statut d'un item Telegram dans la queue."""
    with queue_manager._queue_lock:
        items = queue_manager._load()
        for i in items:
            if i.get("source") != "telegram":
                continue
            fh_match = item.get("filehash") and i.get("filehash") == item.get("filehash")
            fn_match = (i.get("filename") == item.get("filename") and
                        i.get("series_name") == item.get("series_name"))
            if fh_match or fn_match:
                i["status"] = status
                if status == "done":
                    i["done_at"] = datetime.now().isoformat(timespec="seconds")
                if history:
                    i["history"] = history
                break
        queue_manager._save(items)
