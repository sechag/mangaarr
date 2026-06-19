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

_CACHE_DIR  = os.path.join(os.path.dirname(__file__), ".cache")
CACHE_FILE  = os.path.join(_CACHE_DIR, "discover_ed2k_cache.json")
CACHE_TTL   = 86400  # 24 heures


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
            results.append({
                "series_name": series_name,
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


def enrich_with_ed2k(session, entry: dict) -> dict | None:
    """
    Cherche le thread ebdz d'une série, scrape les liens ed2k, et calcule :
      - missing_items : tomes absents du disque
      - upgrade_items : tomes possédés dont ebdz propose un meilleur tag
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

    if not missing_items and not upgrade_items:
        return None

    missing_items.sort(key=lambda i: _extract_num(i.get("tome_number")) or 0)
    upgrade_items.sort(key=lambda i: _extract_num(i.get("tome_number")) or 0)

    return {
        "series_name":   series_name,
        "lib_id":        lib_id,
        "lib_name":      entry.get("lib_name", ""),
        "owned_count":   entry.get("owned_count", 0),
        "thread_url":    thread.get("url", ""),
        "thread_name":   thread.get("name", ""),
        "missing_items": missing_items,
        "upgrade_items": upgrade_items,
        "missing_count": len(missing_items),
        "upgrade_count": len(upgrade_items),
    }
