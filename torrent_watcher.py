"""
torrent_watcher.py — Surveillance des téléchargements qBittorrent terminés

Approche correcte :
  1. Interroge l'API qBittorrent pour les torrents terminés (progress=100%)
  2. Reconstruit le chemin local via MANGAARR_QBT_WATCH + nom_du_torrent
  3. Associe le torrent à un item queue par :
       a. qbt_hash exact  (stocké lors de l'ajout d'un magnet)
       b. série + tome    (matching normalisé, insensible à la casse/accents)
  4. Organise les fichiers via file_organizer (copie + renommage)

Le titre Torznab (item["filename"]) ≠ nom du contenu qBittorrent →
on ne compare plus les titres entre eux.
"""
import os
import re
import unicodedata
import threading
import logging

import config
import queue_manager

log = logging.getLogger("mangaarr.torrent_watcher")

_WATCH_INTERVAL = 60
_MANGA_EXTS     = (".cbz", ".cbr", ".pdf", ".zip")

_watcher_thread = None
_stop_event     = threading.Event()

# États qBittorrent qui signifient "téléchargement terminé / en seed"
_DONE_STATES = {
    "uploading", "stalledUP", "pausedUP", "forcedUP",
    "queuedUP", "checkingUP", "completed", "missingFiles",
}


def get_watch_path() -> str:
    return os.environ.get("MANGAARR_QBT_WATCH", "").strip()


# ══════════════════════════════════════════════════════
# DÉMARRAGE / ARRÊT
# ══════════════════════════════════════════════════════

def start_watcher():
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return
    watch_path = get_watch_path()
    if not watch_path:
        log.info("[TorrentWatcher] MANGAARR_QBT_WATCH non défini — watcher désactivé")
        return
    _stop_event.clear()
    _watcher_thread = threading.Thread(
        target=_watch_loop, name="torrent-watcher", daemon=True
    )
    _watcher_thread.start()
    log.info("[TorrentWatcher] Démarré — surveille %s (intervalle %ds)", watch_path, _WATCH_INTERVAL)


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
            log.error("[TorrentWatcher] Erreur : %s", e, exc_info=True)
        _stop_event.wait(_WATCH_INTERVAL)


def _run_once():
    watch_path = get_watch_path()
    if not watch_path or not os.path.isdir(watch_path):
        return
    result = do_scan_torrent_incoming(watch_path)
    if result["updated"] > 0:
        log.info("[TorrentWatcher] %s", result["message"])


# ══════════════════════════════════════════════════════
# SCAN PRINCIPAL
# ══════════════════════════════════════════════════════

def do_scan_torrent_incoming(watch_path: str) -> dict:
    """
    1. Récupère les torrents terminés depuis l'API qBittorrent.
    2. Reconstruit leur chemin local (watch_path / torrent_name).
    3. Associe à un item queue (par hash ou par série+tome).
    4. Organise les fichiers (copie + renommage vers la librairie).
    """
    import file_organizer as _fo
    import qbittorrent_client as _qbt
    from datetime import datetime

    if not os.path.isdir(watch_path):
        return {"ok": False, "updated": 0, "message": f"watch_path introuvable : {watch_path}", "errors": []}

    # Items torrent en attente (exclure done ET action_pending — déjà en attente utilisateur)
    pending = [i for i in queue_manager.get_queue()
               if i.get("source") == "torrent"
               and i.get("status") not in ("done", "action_pending")]

    if not pending:
        return {"ok": True, "updated": 0, "message": "Aucun item torrent en attente", "errors": []}

    # Clients qBittorrent configurés
    cfg     = config.load()
    clients = [c for c in cfg.get("download_clients", []) if c.get("enabled", True)]

    updated = 0
    errors  = []

    if clients:
        # ── Mode primaire : API qBittorrent ──────────────────────────
        for qbt_client in clients:
            all_t = _qbt.get_torrents(qbt_client, category=qbt_client.get("category", ""))

            # Garde uniquement les torrents 100% terminés
            completed = [
                t for t in all_t
                if float(t.get("progress", 0)) >= 0.9999
                or t.get("state", "") in _DONE_STATES
            ]

            for torrent in completed:
                t_name  = torrent.get("name", "")
                t_hash  = torrent.get("hash", "").lower()

                # Chemin local = watch_path / nom_du_torrent (monté dans le container)
                local = os.path.join(watch_path, t_name)
                if not os.path.exists(local):
                    continue   # Pas dans notre dossier de surveillance

                matched = _find_matching_item(pending, torrent)
                if not matched:
                    continue

                u, e = _process_matched(matched, local, _fo, datetime.now())
                updated += u
                errors  += e

                if u > 0:
                    # Retire l'item des pending pour éviter double traitement
                    pending = [i for i in pending if i is not matched]

    else:
        # ── Mode fallback : scan filesystem avec matching fuzzy ──────
        log.warning("[TorrentWatcher] Aucun client qBittorrent configuré — scan filesystem")
        u, e = _scan_filesystem_fallback(watch_path, pending)
        updated += u
        errors  += e

    msg = f"{updated} fichier(s) torrent traité(s)"
    if errors:
        msg += f" ({len(errors)} erreur(s))"
    return {"ok": True, "updated": updated, "message": msg, "errors": errors}


# ══════════════════════════════════════════════════════
# MATCHING : torrent qBittorrent → item queue
# ══════════════════════════════════════════════════════

def _find_matching_item(pending: list, torrent: dict) -> dict | None:
    """
    Cherche l'item queue correspondant à un torrent qBittorrent terminé.
    Priorité :
      1. Hash exact (stocké dans item["qbt_hash"] lors de l'ajout d'un magnet)
      2. Nom de série normalisé présent dans le nom du torrent + numéro de tome
    """
    t_hash     = torrent.get("hash", "").lower()
    t_name_raw = torrent.get("name", "")
    t_norm     = _norm(t_name_raw)

    for item in pending:
        # ── 1. Hash exact ─────────────────────────────────────────────
        if item.get("qbt_hash") and item["qbt_hash"].lower() == t_hash:
            return item

        # ── 2. Série dans le nom du torrent ───────────────────────────
        series = item.get("series_name", "")
        if not series:
            continue

        series_norm  = _norm(series)
        series_words = [w for w in series_norm.split() if len(w) > 2]
        if not series_words:
            continue

        if not all(w in t_norm for w in series_words):
            continue

        # ── 3. Vérification du numéro de tome (single) ────────────────
        vol_type = item.get("vol_type", "single")
        tomes    = item.get("tomes", [])

        if vol_type == "single" and tomes:
            n = int(tomes[0])
            # Patterns : T01, T1, Tome01, Tome 01, Vol01, 01 (isolé)
            patterns = [
                rf'(?:T|Tome|Vol)[.\-_ ]*0*{n}(?:\b|[.\-_])',
                rf'(?<![0-9])0*{n:02d}(?![0-9])',
            ]
            if not any(re.search(p, t_name_raw, re.IGNORECASE) for p in patterns):
                continue

        return item

    return None


# ══════════════════════════════════════════════════════
# TRAITEMENT D'UN TORRENT APPARIÉ
# ══════════════════════════════════════════════════════

def _process_matched(item: dict, local_path: str, fo, now) -> tuple[int, list]:
    """
    Organise le contenu d'un torrent terminé (fichier ou dossier).
    Si des conflits (tomes déjà présents) sont détectés, passe l'item
    en statut "action_pending" au lieu d'organiser automatiquement.
    Retourne (nb_ok, liste_erreurs).
    """
    series_name = item.get("series_name", "")
    tome_number = item.get("tome_number", "")
    tomes       = item.get("tomes", [])
    updated     = 0
    errors      = []

    ext = os.path.splitext(local_path)[1].lower()

    # ── Détection des conflits avant tout traitement ──────────────────
    try:
        dest_folder, conflicts = fo.detect_conflicts(local_path, series_name)
    except Exception as e:
        log.warning("[TorrentWatcher] Vérification conflits échouée : %s", e)
        dest_folder, conflicts = None, []

    if conflicts:
        # Des tomes existent déjà → demande confirmation utilisateur
        log.info("[TorrentWatcher] %d conflit(s) détecté(s) pour %s — action_pending",
                 len(conflicts), series_name)
        _set_action_pending(item, local_path, conflicts)
        return 0, []

    # ── Aucun conflit → traitement normal ─────────────────────────────
    if os.path.isfile(local_path) and ext in _MANGA_EXTS:
        log.info("[TorrentWatcher] ✓ fichier : %s → %s T%s",
                 os.path.basename(local_path), series_name, tome_number)
        ok, err = _organize_one(fo, local_path, series_name, tome_number, item)
        if ok:
            _mark_done(item, local_path, None, now)
            updated += 1
        else:
            errors.append(err)

    elif os.path.isdir(local_path):
        manga_files = sorted([
            os.path.join(local_path, f)
            for f in os.listdir(local_path)
            if os.path.splitext(f)[1].lower() in _MANGA_EXTS
            and not f.endswith(".!qB")
        ])
        log.info("[TorrentWatcher] ✓ dossier : %s (%d fichier(s)) → %s",
                 os.path.basename(local_path), len(manga_files), series_name)

        pack_ok = False
        for fp in manga_files:
            t_num = _detect_tome(os.path.basename(fp), tomes, tome_number)
            ok, err = _organize_one(fo, fp, series_name, t_num, item)
            if ok:
                pack_ok = True
            else:
                errors.append(err)

        if pack_ok:
            _mark_done(item, None, local_path, now)
            updated += 1

    else:
        log.warning("[TorrentWatcher] Chemin non reconnu : %s", local_path)

    return updated, errors


def _set_action_pending(item: dict, local_path: str, conflicts: list):
    """Passe l'item en statut action_pending avec les données de conflit."""
    with queue_manager._queue_lock:
        items = queue_manager._load()
        for i in items:
            if i.get("source") != "torrent":
                continue
            hash_m  = item.get("qbt_hash") and i.get("qbt_hash") == item.get("qbt_hash")
            tl_m    = item.get("torrent_link") and i.get("torrent_link") == item.get("torrent_link")
            fn_m    = (i.get("filename") == item.get("filename") and
                       i.get("series_name") == item.get("series_name"))
            if hash_m or tl_m or fn_m:
                i["status"] = "action_pending"
                i["pending_action"] = {
                    "type":       "upgrade_conflict",
                    "local_path": local_path,
                    "conflicts":  conflicts,
                }
                break
        queue_manager._save(items)


# ══════════════════════════════════════════════════════
# FALLBACK : scan filesystem avec matching fuzzy
# ══════════════════════════════════════════════════════

def _scan_filesystem_fallback(watch_path: str, pending: list) -> tuple[int, list]:
    """
    Fallback quand aucun client qBittorrent n'est joignable.
    Parcourt le dossier watch_path et tente de matcher par série+tome.
    """
    import file_organizer as _fo
    from datetime import datetime

    updated = 0
    errors  = []
    now     = datetime.now()

    # Index du contenu du dossier
    entries = {}
    for name in os.listdir(watch_path):
        full = os.path.join(watch_path, name)
        if name.endswith(".!qB"):
            continue
        entries[_norm(name)] = (name, full)

    if not entries:
        return 0, []

    for item in list(pending):
        series = item.get("series_name", "")
        if not series:
            continue

        series_norm  = _norm(series)
        series_words = [w for w in series_norm.split() if len(w) > 2]
        tomes        = item.get("tomes", [])
        vol_type     = item.get("vol_type", "single")

        for entry_norm, (entry_name, entry_path) in entries.items():
            if not all(w in entry_norm for w in series_words):
                continue

            if vol_type == "single" and tomes:
                n = int(tomes[0])
                patterns = [
                    rf'(?:T|Tome|Vol)[.\-_ ]*0*{n}(?:\b|[.\-_])',
                    rf'(?<![0-9])0*{n:02d}(?![0-9])',
                ]
                if not any(re.search(p, entry_name, re.IGNORECASE) for p in patterns):
                    continue

            u, e = _process_matched(item, entry_path, _fo, now)
            updated += u
            errors  += e
            if u > 0:
                pending = [i for i in pending if i is not item]
            break

    return updated, errors


# ══════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════

def _norm(s: str) -> str:
    """Normalise une chaîne : minuscules, sans accents, sans ponctuation."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c if c.isalnum() or c.isspace() else " "
                for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def _organize_one(fo, file_path: str, series_name: str,
                  tome_number: str, item: dict) -> tuple[bool, str]:
    """
    Appelle organize_file et retourne (ok, error_message).
    Si le tome est dans item["replace_tomes"], utilise action="upgrade" pour remplacer l'existant.
    """
    replace_tomes = item.get("replace_tomes", [])
    try:
        n = int(re.sub(r"[^0-9]", "", str(tome_number))) if tome_number else 0
    except Exception:
        n = 0
    action = "upgrade" if (n and n in replace_tomes) else "missing"

    try:
        result = fo.organize_file(item={
            "local_path":  file_path,
            "series_name": series_name,
            "tome_number": str(tome_number),
            "filename":    os.path.basename(file_path),
            "action":      action,
            "owned_file":  "",
        })
        if result.get("ok"):
            dest = result.get("dest_path", "?")
            config.add_log(
                f"[Torrent] ✓ {series_name} T{str(tome_number).lstrip('T')}"
                f" : {os.path.basename(file_path)} → {os.path.basename(dest)}",
                "info",
            )
            return True, ""
        msg = f"{os.path.basename(file_path)} : {result.get('message', '')}"
        config.add_log(f"[Torrent] ✗ {msg}", "warning")
        return False, msg
    except Exception as e:
        config.add_log(f"[Torrent] Exception organize_file : {e}", "error")
        log.error("[TorrentWatcher] Exception : %s", e, exc_info=True)
        return False, str(e)


def _mark_done(item: dict, file_path, folder_path, now):
    """Met l'item en status 'done' dans la queue."""
    history = {
        "source_file":  os.path.basename(file_path) if file_path else str(folder_path),
        "processed_at": now.isoformat(timespec="seconds"),
    }
    _update_torrent_status(item, "done", history)


def _update_torrent_status(item: dict, status: str, history: dict = None):
    """
    Met à jour le statut d'un item torrent dans la queue.
    Identifie l'item par qbt_hash, torrent_link ou filename+series_name.
    """
    from datetime import datetime

    with queue_manager._queue_lock:
        items = queue_manager._load()
        for i in items:
            if i.get("source") != "torrent":
                continue

            hash_match = (item.get("qbt_hash") and
                          i.get("qbt_hash") == item.get("qbt_hash"))
            tl_match   = (item.get("torrent_link") and
                          i.get("torrent_link") == item.get("torrent_link"))
            fn_match   = (i.get("filename")    == item.get("filename") and
                          i.get("series_name") == item.get("series_name"))

            if hash_match or tl_match or fn_match:
                i["status"] = status
                if status == "done":
                    i["done_at"] = datetime.now().isoformat(timespec="seconds")
                if history:
                    i["history"] = history
        queue_manager._save(items)


def _detect_tome(filename: str, tomes: list, default) -> str:
    """Extrait le numéro de tome depuis un nom de fichier de pack."""
    m = re.search(r'T(?:ome)?[.\-_\s]*(\d{1,3})', filename, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if not tomes or n in tomes:
            return str(n)
    m = re.search(r'[.\-_\s](\d{2,3})(?:[.\-_\s]|$)', filename)
    if m:
        return m.group(1)
    return str(default).lstrip("T") if default else "0"
