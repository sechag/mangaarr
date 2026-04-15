"""
config.py — Configuration centrale de MangaArr
"""
import json, os

CONFIG_FILE = os.environ.get(
    "MANGAARR_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
)

DEFAULTS = {
    "mybbuser": "",
    "download_dir": "",
    "libraries": [],           # [{id, name, path}] — librairies locales
    "media_management": {
        "auto_rename":      True,
        "auto_convert_cbr": True,
        "auto_convert_pdf": True,
        "auto_replace":     True,
    },
    "profiles": {
        "tags":             [],
        "must_contain":     [],
        "must_not_contain": [],
    },
    "metadata_sources": [],
    "scrape_interval_hours":    24,
    "meta_sync_interval_hours": 72,
    "watcher_interval":         0,
    # Indexers Torznab : [{id, name, url, apikey, enabled}]
    "torznab_indexers": [],
    # Clients de téléchargement : [{id, name, type, host, port, username, password, category, save_path, enabled}]
    "download_clients": [],
}
# Note : les logs sont dans .cache/mangaarr.log.json (pas dans config.json)

# Clés Komga obsolètes à ignorer à la lecture (migration)
_OBSOLETE_KEYS = {"komga_connections", "active_komga", "_active_komga_unused"}

KNOWN_TAGS = [
    "NEO RIP-Club", "NEO.RIP-Club", "Neo Rip-Club",
    "RIP-Club", "Paprika+", "Paprika",
    "TONER", "PRiNTER", "Gooby",
    "Crossread+", "Crossread", "ScanTrad",
    "Di3an", "Pitoufos", "NRC ALLIANCE",
    "OpazeSenpai", "slaine", "NoFace696",
    "FireLion", "Moi",
]

def load() -> dict:
    if not os.path.exists(CONFIG_FILE):
        save(DEFAULTS.copy())
        return DEFAULTS.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        import sys
        print(f"[Config] Erreur lecture config.json : {e} — utilise les défauts", file=sys.stderr)
        return DEFAULTS.copy()

    # Migration : supprime les clés obsolètes (Komga, logs dans config, etc.)
    _OBSOLETE_KEYS_FULL = _OBSOLETE_KEYS | {"logs"}
    for k in list(data.keys()):
        if k in _OBSOLETE_KEYS_FULL:
            del data[k]

    # Ajoute les clés manquantes avec leurs valeurs par défaut
    for k, v in DEFAULTS.items():
        if k not in data:
            data[k] = v

    # Migration metadata_sources : retire komga_index obsolète
    for src in data.get("metadata_sources", []):
        src.pop("komga_index", None)

    return data

def save(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def get(key: str, default=None):
    return load().get(key, default)

def set_value(key: str, value):
    cfg = load()
    cfg[key] = value
    save(cfg)

# ── Logs séparés du config.json (fichier dédié) ─────────────
_LOG_FILE  = None   # sera résolu dynamiquement depuis MANGAARR_CACHE ou dossier local
_log_lock  = __import__("threading").Lock()

def _log_file() -> str:
    global _LOG_FILE
    if _LOG_FILE:
        return _LOG_FILE
    import os as _os
    cache = get("_cache_dir") or _os.path.join(_os.path.dirname(CONFIG_FILE), ".cache")
    _os.makedirs(cache, exist_ok=True)
    _LOG_FILE = _os.path.join(cache, "mangaarr.log.json")
    return _LOG_FILE

def add_log(message: str, level: str = "info"):
    """Ajoute un log dans un fichier JSON dédié — NE TOUCHE PAS config.json."""
    from datetime import datetime
    import json as _json, os as _os
    entry = {
        "time":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level":   level,
        "message": message,
    }
    lf = _log_file()
    with _log_lock:
        try:
            if _os.path.exists(lf):
                with open(lf, "r", encoding="utf-8") as f:
                    logs = _json.load(f)
            else:
                logs = []
        except Exception:
            logs = []
        logs.append(entry)
        logs = logs[-2000:]   # garde les 2000 derniers
        with open(lf, "w", encoding="utf-8") as f:
            _json.dump(logs, f, ensure_ascii=False)

def get_logs(n: int = 200) -> list:
    """Lit les N derniers logs depuis le fichier dédié."""
    import json as _json, os as _os
    lf = _log_file()
    try:
        with open(lf, "r", encoding="utf-8") as f:
            logs = _json.load(f)
        return list(reversed(logs[-n:]))
    except Exception:
        return []

def clear_logs():
    """Vide le fichier de logs."""
    import os as _os
    lf = _log_file()
    with _log_lock:
        try:
            with open(lf, "w", encoding="utf-8") as f:
                _json.dump([], f)
        except Exception:
            pass
