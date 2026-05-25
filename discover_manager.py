"""
discover_manager.py — Gestion de la découverte Torznab

Phase 1 : lister les séries incomplètes depuis la librairie locale (metadata tomes_vf requis).
Phase 2 : pour chaque série, chercher des releases via Torznab.
Cache 24h : persist entre les recharges de page.
"""
import os
import json
import time

import cache as cache_mod
import library_manager as lib_mgr
import torznab_client

# ── Cache 24h ────────────────────────────────────────────────────────────────

_CACHE_DIR  = os.path.join(os.path.dirname(__file__), ".cache")
CACHE_FILE  = os.path.join(_CACHE_DIR, "discover_cache.json")
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


# ── Phase 1 : séries incomplètes ─────────────────────────────────────────────

def get_incomplete_series(lib_id: str = "") -> list:
    """
    Phase 1 (rapide) : liste les séries avec metadata tomes_vf > 0 et incomplètes.
    Retourne les séries triées par nom sans appeler les indexers.
    """
    libraries = lib_mgr.get_libraries()
    if lib_id:
        libraries = [l for l in libraries if l["id"] == lib_id]

    results = []
    for lib in libraries:
        if not os.path.isdir(lib["path"]):
            continue
        lib_cache   = cache_mod.get_library_cache(lib["id"])
        series_list = lib_mgr.scan_library(lib["id"])

        for s in series_list:
            sid      = s["id"]
            meta     = lib_cache.get(sid) or {}
            total_vf = int(meta.get("tomes_vf") or 0)

            # Sauter si pas de metadata ou déjà complète
            if not total_vf:
                continue
            owned_n = s["booksCount"]
            if owned_n >= total_vf:
                continue

            tomes_owned = sorted({
                t["numero"] for t in s.get("tomes", []) if t.get("numero")
            })
            missing = [n for n in range(1, total_vf + 1) if n not in set(tomes_owned)]

            results.append({
                "series_id":     sid,
                "series_name":   s["name"],
                "series_slug":   s.get("slug", ""),
                "lib_id":        lib["id"],
                "lib_name":      lib["name"],
                "owned_count":   owned_n,
                "total_vf":      total_vf,
                "missing_count": len(missing),
                "missing_tomes": missing,
                "tomes_owned":   tomes_owned,
            })

    results.sort(key=lambda x: x["series_name"].lower())
    return results


# ── Phase 2 : enrichissement avec releases ───────────────────────────────────

def enrich_with_releases(series_entry: dict, indexers: list) -> dict | None:
    """
    Cherche des releases Torznab pour une série et retourne un dict enrichi,
    ou None si aucune release utile n'est trouvée.
    """
    name    = series_entry["series_name"]
    missing = series_entry.get("missing_tomes", [])

    releases = torznab_client.search_all(indexers, name, categories=[7000])
    if not releases:
        return None

    useful = []
    for r in releases:
        vt     = r.get("vol_type", "unknown")
        rtomes = r.get("tomes", [])

        if vt == "integrale":
            covered = list(missing) if missing else []
            r["covered_missing"]       = covered
            r["missing_covered_count"] = len(covered)
            useful.append(r)

        elif vt == "single" and rtomes:
            n = rtomes[0]
            r["covered_missing"]       = [n] if (not missing or n in missing) else []
            r["missing_covered_count"] = len(r["covered_missing"])
            if not missing or n in missing:
                useful.append(r)

        elif vt == "pack" and rtomes:
            covered = [t for t in rtomes if not missing or t in missing]
            r["covered_missing"]       = covered
            r["missing_covered_count"] = len(covered)
            if not missing or covered:
                useful.append(r)

        else:
            r["covered_missing"]       = []
            r["missing_covered_count"] = 0
            useful.append(r)

    if not useful:
        return None

    useful.sort(key=lambda r: (-r.get("missing_covered_count", 0), -r.get("seeders", 0)))

    return {
        **series_entry,
        "releases":       useful,
        "releases_count": len(useful),
        "has_integrale":  any(r.get("vol_type") == "integrale" for r in useful),
    }
