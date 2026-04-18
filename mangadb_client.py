"""
mangadb_client.py — Client pour l'API MangaDB personnelle
Structure réelle :
  GET /api/search?q=<titre>&threshold=0.6&limit=5
  GET /api/series/<titre>
  GET /api/cover/<titre>/<numero>
  GET /api/stats
"""
import re, requests
import config

TIMEOUT = 10

def _get_sources():
    return config.get("metadata_sources", [])

def _base(source):
    return source.get("url", "").rstrip("/")

def _clean_auteur(text):
    if not text: return ""
    text = re.sub(r'\s*[\r\n]+\s*', ' ', str(text))
    text = re.sub(r'\s+-\s*$', '', text)
    text = re.sub(r'^\s*-\s*', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def _normalize_result(r):
    genres_raw = r.get("genres", "") or ""
    if isinstance(genres_raw, list):
        genres = genres_raw
    else:
        genres = [g.strip() for g in re.split(r'[|,]', str(genres_raw)) if g.strip()]
    try:
        tomes_vf = int(r.get("tomes", 0) or 0)
    except:
        tomes_vf = 0
    return {
        "titre":          r.get("titre", ""),
        "auteur":         _clean_auteur(r.get("auteur", "")),
        "editeur":        r.get("editeur", ""),
        "genres":         genres,
        "statut_vf":      r.get("statut_vf", "Inconnu"),
        "tomes_vf":       tomes_vf,
        "manga_news_url": r.get("url", ""),
        "score":          r.get("score", 0),
    }

def search_series(title, source_id=None):
    sources = _get_sources()
    if source_id:
        sources = [s for s in sources if s.get("id") == source_id]
    results = []
    for source in sources:
        try:
            r = requests.get(f"{_base(source)}/api/search",
                params={"q": title, "threshold": 0.65, "limit": 5}, timeout=TIMEOUT)
            if r.status_code == 200:
                results.extend(r.json().get("results", []))
        except: pass
    return results

def find_best_match(title, source_id=None):
    results = search_series(title, source_id)
    if not results: return None
    best = results[0]
    if best.get("score", 0) < 0.65:
        return None
    meta = _normalize_result(best)
    # /api/search ne retourne pas "url" → on le récupère via /api/series si manquant
    if not meta.get("manga_news_url") and meta.get("titre"):
        detail = get_series_detail(meta["titre"], source_id)
        if detail:
            meta["manga_news_url"] = str(detail.get("url", "") or "")
    return meta

def get_series_detail(titre, source_id=None):
    sources = _get_sources()
    if source_id:
        sources = [s for s in sources if s.get("id") == source_id]
    from urllib.parse import quote
    for source in sources:
        try:
            r = requests.get(f"{_base(source)}/api/series/{quote(titre, safe='')}", timeout=TIMEOUT)
            if r.status_code == 200: return r.json()
        except: pass
    return None

def get_cover_url(titre, numero, source_id=None):
    sources = _get_sources()
    if source_id: sources = [s for s in sources if s.get("id") == source_id]
    if not sources: return None
    from urllib.parse import quote
    return f"{_base(sources[0])}/api/cover/{quote(titre, safe='')}/{numero}"

def test_connection(url):
    try:
        r = requests.get(f"{url.rstrip('/')}/api/stats", timeout=TIMEOUT)
        if r.status_code == 200:
            total = r.json().get("total_series", "?")
            return {"ok": True, "message": f"Connecté — {total} séries disponibles"}
        r2 = requests.get(f"{url.rstrip('/')}/api/search", params={"q":"test","limit":1}, timeout=TIMEOUT)
        if r2.status_code in (200, 400):
            return {"ok": True, "message": "Instance MangaDB accessible"}
        return {"ok": False, "message": f"HTTP {r.status_code}"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "message": "Impossible de se connecter"}
    except Exception as e:
        return {"ok": False, "message": str(e)}
