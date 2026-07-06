"""
discover_ed2k_manager.py — Découverte ED2K (eMule/aMule)

Pendant ED2K de discover_manager.py (Torznab) :
Phase 1 : lister les séries de la librairie depuis le disque (tomes possédés + tags).
Phase 2 : pour chaque série, chercher le thread ebdz, scraper les liens ed2k et calculer
          les tomes manquants + les upgrades (meilleur tag disponible).
Cache 24h : persiste entre les recharges de page.
"""
import os
import json
import time

import library_manager as lib_mgr
import folder_scanner
import ebdz_scraper
import profiles as _p

# ── Cache 24h ────────────────────────────────────────────────────────────────

_CACHE_DIR         = os.path.join(os.path.dirname(__file__), ".cache")
CACHE_FILE         = os.path.join(_CACHE_DIR, "discover_ed2k_cache.json")
COMPARE_CACHE_FILE = os.path.join(_CACHE_DIR, "ebdz_compare_cache.json")
# Historique des comparaisons : dans le cache PERSISTANT (MANGAARR_CACHE → /data/cache)
# pour survivre aux mises à jour du conteneur Docker.
_PERSIST_CACHE_DIR = os.environ.get("MANGAARR_CACHE") or _CACHE_DIR
CACHE_TTL          = 86400  # 24 heures


def load_cache() -> list | None:
    """Retourne les résultats mis en cache (< 24h) ou None."""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                return data.get("series", [])
    except Exception:
        pass
    return None


def save_cache(series_list: list) -> None:
    """Sauvegarde la liste des séries avec résultats dans le cache."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"timestamp": time.time(), "series": series_list}, f)


def clear_cache() -> None:
    """Supprime le cache."""
    try:
        os.remove(CACHE_FILE)
    except FileNotFoundError:
        pass


def cache_info() -> dict:
    """Retourne des infos sur le cache (âge, nombre de séries)."""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            age = time.time() - data.get("timestamp", 0)
            return {
                "exists": True,
                "age_seconds": int(age),
                "valid": age < CACHE_TTL,
                "series_count": len(data.get("series", [])),
                "timestamp": int(data.get("timestamp", 0)),
            }
    except Exception:
        pass
    return {"exists": False, "valid": False, "series_count": 0}


# ── Historique des scans de comparaison eBDZ ─────────────────────────────────
# Chaque scan est sauvegardé dans .cache/ebdz_compare/compare_<YYYYMMDD_HHMMSS>.json
# → historique daté consultable dans la page Comparaison eBDZ.
# La sauvegarde est incrémentale pendant le scan : même si la connexion est
# interrompue (onglet suspendu, PC en veille), les résultats déjà trouvés
# restent enregistrés.

COMPARE_HISTORY_DIR = os.path.join(_PERSIST_CACHE_DIR, "ebdz_compare")

_SCAN_ID_RE = None  # initialisé au premier usage


def _valid_scan_id(scan_id: str) -> bool:
    """Valide le format d'un scan_id (YYYYMMDD_HHMMSS) — évite tout path traversal."""
    global _SCAN_ID_RE
    if _SCAN_ID_RE is None:
        import re
        _SCAN_ID_RE = re.compile(r"^\d{8}_\d{6}$")
    return bool(_SCAN_ID_RE.match(scan_id or ""))


def _compare_scan_path(scan_id: str) -> str:
    return os.path.join(COMPARE_HISTORY_DIR, f"compare_{scan_id}.json")


def new_scan_id() -> str:
    """Génère un identifiant de scan basé sur la date/heure."""
    return time.strftime("%Y%m%d_%H%M%S")


def save_compare_scan(scan_id: str, series_list: list, finished: bool = False) -> None:
    """
    Sauvegarde (ou met à jour) un scan de comparaison dans l'historique.
    Appelée de façon incrémentale pendant le scan, puis avec finished=True à la fin.
    """
    if not _valid_scan_id(scan_id):
        return
    os.makedirs(COMPARE_HISTORY_DIR, exist_ok=True)
    tmp = _compare_scan_path(scan_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "scan_id":   scan_id,
            "timestamp": time.time(),
            "finished":  finished,
            "series":    series_list,
        }, f)
    os.replace(tmp, _compare_scan_path(scan_id))


def load_compare_scan(scan_id: str) -> dict | None:
    """Charge un scan de comparaison depuis l'historique."""
    if not _valid_scan_id(scan_id):
        return None
    try:
        with open(_compare_scan_path(scan_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def delete_compare_scan(scan_id: str) -> bool:
    """Supprime un scan de l'historique."""
    if not _valid_scan_id(scan_id):
        return False
    try:
        os.remove(_compare_scan_path(scan_id))
        return True
    except FileNotFoundError:
        return False


def _migrate_old_compare_cache() -> None:
    """Migre l'ancien fichier ebdz_compare_cache.json (v1.1.7) vers l'historique."""
    if not os.path.exists(COMPARE_CACHE_FILE):
        return
    try:
        with open(COMPARE_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ts      = data.get("timestamp", time.time())
        scan_id = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))
        if not os.path.exists(_compare_scan_path(scan_id)):
            save_compare_scan(scan_id, data.get("series", []), finished=True)
        os.remove(COMPARE_CACHE_FILE)
    except Exception:
        pass


def list_compare_scans() -> list:
    """
    Liste les scans de comparaison enregistrés, du plus récent au plus ancien.
    Retourne [{scan_id, timestamp, finished, series_count, tomes_count}].
    """
    _migrate_old_compare_cache()
    scans = []
    if not os.path.isdir(COMPARE_HISTORY_DIR):
        return scans
    for fn in os.listdir(COMPARE_HISTORY_DIR):
        if not (fn.startswith("compare_") and fn.endswith(".json")):
            continue
        scan_id = fn[len("compare_"):-len(".json")]
        data = load_compare_scan(scan_id)
        if data is None:
            continue
        series = data.get("series", [])
        scans.append({
            "scan_id":      scan_id,
            "timestamp":    int(data.get("timestamp", 0)),
            "finished":     bool(data.get("finished", True)),
            "series_count": len(series),
            "tomes_count":  sum(s.get("ebdz_missing_count", 0) for s in series),
        })
    scans.sort(key=lambda s: s["scan_id"], reverse=True)
    return scans


def latest_compare_scan() -> dict | None:
    """Retourne le scan de comparaison le plus récent, ou None."""
    scans = list_compare_scans()
    if not scans:
        return None
    return load_compare_scan(scans[0]["scan_id"])


# ── Phase 1 : séries candidates (depuis le disque) ───────────────────────────

def get_candidate_series(lib_id: str = "") -> list:
    """
    Liste toutes les séries (sous-dossiers) des librairies depuis le disque.
    Chaque entrée porte les tomes possédés + tomes_info (tag/score) nécessaires
    pour détecter les upgrades. Pas d'appel ebdz à cette étape.
    """
    libraries = lib_mgr.get_libraries()
    if lib_id:
        libraries = [l for l in libraries if l["id"] == lib_id]

    results = []
    for lib in libraries:
        if not os.path.isdir(lib["path"]):
            continue
        series_map = folder_scanner.scan_series_on_disk(lib["path"])
        for series_name, data in series_map.items():
            # Slug = même construction que library_manager.scan_library
            # → permet de lier vers la page /series/<slug> de la collection
            series_dir = os.path.join(lib["path"], series_name)
            slug = lib_mgr._slugify(series_name) + "--" + lib_mgr._series_id(series_dir)
            results.append({
                "series_name": series_name,
                "series_slug": slug,
                "lib_id":      lib["id"],
                "lib_name":    lib["name"],
                "owned_count": len(data.get("tomes_presents", set())),
                "tomes_info":  data.get("tomes_info", {}),
            })

    results.sort(key=lambda x: x["series_name"].lower())
    return results


# ── Phase 2 : enrichissement avec liens ed2k (ebdz) ──────────────────────────

def _extract_num(val):
    import re
    if val is None:
        return None
    m = re.search(r"(\d+)", str(val))
    return int(m.group(1)) if m else None


def _owned_item(tomes_info: dict, n: int) -> dict:
    """Construit un item {tome_number, filename, tag} depuis tomes_info."""
    owned = tomes_info.get(str(n)) or tomes_info.get(n) or {}
    return {
        "tome_number": n,
        "filename":    owned.get("filename", ""),
        "tag":         owned.get("tag", ""),
    }


def enrich_with_ed2k(session, entry: dict, include_ebdz_missing: bool = False) -> dict | None:
    """
    Cherche le thread ebdz d'une série, scrape les liens ed2k, et calcule :
      - missing_items      : tomes absents du disque
      - upgrade_items      : tomes possédés dont ebdz propose un meilleur tag
      - ebdz_missing_items : tomes possédés qu'eBDZ NE possède PAS (si include_ebdz_missing)
    Retourne None si aucun thread ou aucun résultat.

    Réutilise la même logique de comparaison que folder_scanner.detect_missing_from_disk.
    """
    import config

    series_name = entry["series_name"]
    lib_id      = entry.get("lib_id")
    tomes_info  = entry.get("tomes_info", {}) or {}
    owned_nums  = {int(k) for k in tomes_info.keys()} if tomes_info else set()

    thread = ebdz_scraper.find_thread_for_series(series_name, lib_id=lib_id)
    if not thread:
        # Série introuvable sur eBDZ → tous les tomes possédés sont "absents d'eBDZ"
        if include_ebdz_missing and owned_nums:
            return {
                "series_name":        series_name,
                "series_slug":        entry.get("series_slug", ""),
                "lib_id":             lib_id,
                "lib_name":           entry.get("lib_name", ""),
                "owned_count":        entry.get("owned_count", 0),
                "thread_url":         "",
                "thread_name":        "",
                "no_thread":          True,
                "missing_items":      [],
                "upgrade_items":      [],
                "missing_count":      0,
                "upgrade_count":      0,
                "ebdz_missing_items": [_owned_item(tomes_info, n) for n in sorted(owned_nums)],
                "ebdz_missing_count": len(owned_nums),
            }
        return None

    raw_links    = ebdz_scraper.scrape_thread_ed2k(session, thread["url"])
    best_by_tome = ebdz_scraper.get_best_ed2k_per_tome(raw_links)

    missing_items = []
    upgrade_items = []

    for tome_str, parsed in best_by_tome.items():
        n = _extract_num(tome_str)
        if not n:
            continue

        filename   = parsed.get("filename", parsed.get("url", ""))
        ebdz_tag   = parsed.get("tag", "Notag")
        ebdz_score = _p.get_tag_score(ebdz_tag) if ebdz_tag != "Notag" else 0

        # Filtre must_contain / must_not_contain
        ok, raison = _p.passes_filters(filename)
        if not ok:
            config.add_log(f"[ED2K] Ignoré ({raison}) : {filename}", "info")
            continue

        if n not in owned_nums:
            item = dict(parsed)
            item["action"] = "missing"
            missing_items.append(item)
        elif n in owned_nums:
            owned = tomes_info.get(str(n)) or tomes_info.get(n) or {}
            owned_score = owned.get("score", 0)
            if ebdz_score > owned_score:
                item = dict(parsed)
                item["action"]      = "upgrade"
                item["owned_file"]  = owned.get("filename", "")
                item["owned_tag"]   = owned.get("tag", "")
                item["owned_score"] = owned_score
                upgrade_items.append(item)

    # Comparaison inverse : tomes possédés qu'eBDZ ne propose pas
    ebdz_missing_items = []
    if include_ebdz_missing:
        ebdz_nums = {n for n in (_extract_num(t) for t in best_by_tome.keys()) if n}
        ebdz_missing_items = [
            _owned_item(tomes_info, n)
            for n in sorted(owned_nums)
            if n not in ebdz_nums
        ]

    if not missing_items and not upgrade_items and not ebdz_missing_items:
        return None

    missing_items.sort(key=lambda i: _extract_num(i.get("tome_number")) or 0)
    upgrade_items.sort(key=lambda i: _extract_num(i.get("tome_number")) or 0)

    return {
        "series_name":        series_name,
        "series_slug":        entry.get("series_slug", ""),
        "lib_id":             lib_id,
        "lib_name":           entry.get("lib_name", ""),
        "owned_count":        entry.get("owned_count", 0),
        "thread_url":         thread.get("url", ""),
        "thread_name":        thread.get("name", ""),
        "no_thread":          False,
        "missing_items":      missing_items,
        "upgrade_items":      upgrade_items,
        "missing_count":      len(missing_items),
        "upgrade_count":      len(upgrade_items),
        "ebdz_missing_items": ebdz_missing_items,
        "ebdz_missing_count": len(ebdz_missing_items),
    }
