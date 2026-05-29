"""
ebdz_scraper.py — Scraping ebdz.net
- Supporte plusieurs sources (fid=29 Mangas, fid=23 BD, etc.)
- Scrape complet ou partiel par source
- Cache dans .cache/ebdz_threads.json (chaque thread porte un source_id)
- Flux RSS depuis le cache
"""
import re, time, threading, json, os, uuid
from datetime import datetime
from urllib.parse import urljoin, unquote
from bs4 import BeautifulSoup
import requests
import config, profiles

FORUM_BASE_URL = "https://ebdz.net/forum/"
DELAY          = 2.0

# URL de la source par défaut (rétrocompatibilité)
_DEFAULT_SOURCE = {
    "id":          "manga",
    "name":        "Mangas",
    "url":         "https://ebdz.net/forum/forumdisplay.php?fid=29",
    "library_ids": [],
    "enabled":     True,
}


def _get_cache_dir() -> str:
    try:
        d = config.get("_cache_dir") or os.path.join(os.path.dirname(__file__), ".cache")
    except Exception:
        d = os.path.join(os.path.dirname(__file__), ".cache")
    os.makedirs(d, exist_ok=True)
    return d

def _threads_cache() -> str:
    return os.path.join(_get_cache_dir(), "ebdz_threads.json")

def _state_file() -> str:
    return os.path.join(_get_cache_dir(), "scrape_state.json")


# ── Sources ──────────────────────────────────────────────

def get_sources() -> list:
    """Retourne la liste des sources configurées (avec fallback sur la source par défaut)."""
    sources = config.get("ebdz_sources", [])
    if not sources:
        return [_DEFAULT_SOURCE]
    return sources

def get_enabled_sources() -> list:
    return [s for s in get_sources() if s.get("enabled", True)]

def get_source(source_id: str) -> dict | None:
    return next((s for s in get_sources() if s.get("id") == source_id), None)


# ── State ────────────────────────────────────────────────

_scrape_state = {"running": False, "mode": "", "page": 0, "total": 0, "threads": 0, "source_name": ""}
_state_lock   = threading.Lock()
_scheduler    = None

def _load_state():
    try:
        with open(_state_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_full": None, "last_partial": None}

def _save_state(state):
    with open(_state_file(), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

def get_state():
    with _state_lock:
        s = dict(_scrape_state)
    s.update(_load_state())
    return s


# ── Persistance threads ──────────────────────────────────

def _load_threads() -> dict:
    try:
        with open(_threads_cache(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_threads(data: dict):
    with open(_threads_cache(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_all_threads(source_id: str = None) -> list:
    """Retourne les threads triés par last_seen desc, filtrés par source_id si fourni."""
    data = _load_threads()
    items = list(data.values())
    if source_id:
        # Inclut les threads sans source_id (legacy) uniquement si source_id = source principale
        items = [t for t in items
                 if t.get("source_id") == source_id
                 or (not t.get("source_id") and source_id == "manga")]
    return sorted(items, key=lambda x: x.get("last_seen", ""), reverse=True)


# ── Session / login ──────────────────────────────────────

def make_session(mybbuser: str) -> requests.Session:
    s = requests.Session()
    s.cookies.set("mybbuser", mybbuser, domain="ebdz.net")
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    return s

def check_login(session: requests.Session) -> bool:
    """Vérifie le cookie en chargeant la première source disponible."""
    sources = get_enabled_sources()
    check_url = sources[0]["url"] if sources else _DEFAULT_SOURCE["url"]
    try:
        r = session.get(check_url, timeout=15)
        return "member.php?action=login" not in r.url and r.status_code == 200
    except Exception:
        return False


# ── Scrape d'une source ───────────────────────────────────

def _get_total_pages(session: requests.Session, category_url: str) -> int:
    try:
        r = session.get(category_url, timeout=15)
        soup = BeautifulSoup(r.content, "html.parser")
        pag = soup.find("div", class_="pagination")
        if not pag:
            return 1
        m = re.search(r"(?:sur|of|/)\s*(\d+)", pag.get_text(), re.IGNORECASE)
        if m:
            return int(m.group(1))
        last = 1
        for a in pag.find_all("a"):
            t = a.get_text(strip=True)
            if t.isdigit():
                last = max(last, int(t))
            mm = re.search(r"page=(\d+)", a.get("href", ""))
            if mm:
                last = max(last, int(mm.group(1)))
        return last
    except Exception:
        return 1

def _scrape_page(session: requests.Session, page: int, category_url: str) -> list:
    url = category_url if page == 1 else f"{category_url}&page={page}"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        threads = []
        for tr in soup.find_all("tr", class_="inline_row"):
            span = tr.find("span", class_=re.compile(r"subject_"))
            if not span:
                continue
            a = span.find("a")
            if not a:
                continue
            thread_url = urljoin(FORUM_BASE_URL, a.get("href", ""))
            name = a.get_text(strip=True)
            if thread_url and name:
                threads.append((thread_url, name))
        return threads
    except Exception as e:
        config.add_log(f"Erreur scraping page {page} ({category_url}): {e}", "error")
        return []

# Alias de rétrocompatibilité
def scrape_page(session, page, category_url=None):
    if category_url is None:
        sources = get_enabled_sources()
        category_url = sources[0]["url"] if sources else _DEFAULT_SOURCE["url"]
    return _scrape_page(session, page, category_url)


# ── Scrape complet / partiel ──────────────────────────────

def _run_scrape(mode: str, max_pages: int = None, source_id: str = None):
    mybbuser = config.get("mybbuser", "")
    if not mybbuser:
        config.add_log("Scrape ebdz : cookie mybbuser non configuré", "error")
        with _state_lock: _scrape_state["running"] = False
        return

    session = make_session(mybbuser)
    if not check_login(session):
        config.add_log("Scrape ebdz : cookie invalide", "error")
        with _state_lock: _scrape_state["running"] = False
        return

    # Sources à scraper
    if source_id:
        src = get_source(source_id)
        sources_to_run = [src] if src else []
    else:
        sources_to_run = get_enabled_sources()

    if not sources_to_run:
        config.add_log("Scrape ebdz : aucune source configurée", "error")
        with _state_lock: _scrape_state["running"] = False
        return

    existing = _load_threads()
    now = datetime.now().isoformat(timespec="seconds")
    total_new = 0

    for src in sources_to_run:
        cat_url   = src["url"]
        src_id    = src["id"]
        src_name  = src["name"]
        total_pages = _get_total_pages(session, cat_url)
        n = total_pages if mode == "full" else min(max_pages or 3, total_pages)

        with _state_lock:
            _scrape_state.update({"mode": mode, "page": 0, "total": n, "threads": len(existing), "source_name": src_name})

        config.add_log(f"Scrape ebdz {mode} [{src_name}] démarré ({n} pages)", "info")
        new_count = 0

        for page in range(1, n + 1):
            with _state_lock:
                _scrape_state["page"] = page
            threads = _scrape_page(session, page, cat_url)
            if not threads:
                break
            for url, name in threads:
                if url not in existing:
                    new_count += 1
                existing[url] = {"url": url, "name": name, "last_seen": now, "source_id": src_id}
            with _state_lock:
                _scrape_state["threads"] = len(existing)
            if page < n:
                time.sleep(DELAY)

        total_new += new_count
        config.add_log(f"Scrape ebdz [{src_name}] : {new_count} nouveaux forums", "info")
        if len(sources_to_run) > 1 and src is not sources_to_run[-1]:
            time.sleep(DELAY)

    _save_threads(existing)
    state = _load_state()
    if mode == "full":
        state["last_full"] = now
    state["last_partial"] = now
    _save_state(state)
    config.add_log(f"Scrape ebdz {mode} terminé : {len(existing)} forums ({total_new} nouveaux)", "info")
    with _state_lock: _scrape_state["running"] = False


def start_scrape(mode="partial", max_pages=3, source_id=None):
    """Lance un scrape en arrière-plan. Retourne False si déjà en cours."""
    with _state_lock:
        if _scrape_state["running"]:
            return False
        _scrape_state["running"] = True
    threading.Thread(target=_run_scrape, args=(mode, max_pages, source_id), daemon=True).start()
    return True


# ── Scheduler ────────────────────────────────────────────

def _scheduler_loop():
    def _interval():
        hours = config.get("scrape_interval_hours", 12)
        try:
            return max(1, int(hours)) * 3600
        except Exception:
            return 12 * 3600
    time.sleep(_interval())
    while True:
        hours = config.get("scrape_interval_hours", 12)
        config.add_log(f"Scrape automatique ebdz (toutes les {hours}h)", "info")
        start_scrape(mode="partial", max_pages=3)
        time.sleep(_interval())

def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.is_alive():
        return
    _scheduler = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler.start()


# ── ED2K ─────────────────────────────────────────────────

def scrape_thread_ed2k(session, thread_url):
    """Extrait tous les liens ed2k d'un thread (toutes pages)."""
    ed2k_links = []
    page = 1
    while True:
        url = thread_url if page == 1 else f"{thread_url}&page={page}"
        try:
            time.sleep(DELAY)
            r = session.get(url, timeout=15)
            r.raise_for_status()
            r.encoding = 'utf-8'
        except Exception as e:
            config.add_log(f"Erreur thread {thread_url}: {e}", "error")
            break
        found = []
        for pat in [r'ed2k://\|file\|[^"<>\s]+', r'https?://ed2k//?(?:\|file\|)[^"<>\s]+']:
            for link in re.findall(pat, r.text):
                link = link.split('"')[0].split("<")[0].split(">")[0]
                for pref, repl in [("https://ed2k//","ed2k://"),("http://ed2k//","ed2k://"),
                                    ("https://ed2k/","ed2k://"),("http://ed2k/","ed2k://")]:
                    if link.startswith(pref): link = repl + link[len(pref):]
                if link.startswith("ed2k://") and link not in ed2k_links:
                    ed2k_links.append(link); found.append(link)
        soup = BeautifulSoup(r.content, "html.parser")
        pag = soup.find("div", class_="pagination")
        has_next = False
        if pag:
            for a in pag.find_all("a"):
                mm = re.search(r"page=(\d+)", a.get("href",""))
                if mm and int(mm.group(1)) == page + 1:
                    has_next = True; break
        if not has_next or not found:
            break
        page += 1
    return ed2k_links


def parse_ed2k(ed2k_url):
    import renamer as _r, html as _html
    m = re.search(r"ed2k://\|file\|([^|]+)\|(\d+)\|([A-Fa-f0-9]+)\|", ed2k_url, re.IGNORECASE)
    if m:
        filename = _html.unescape(unquote(m.group(1)))
        return {
            "filename":    filename,
            "filesize":    int(m.group(2)),
            "filehash":    m.group(3).lower(),
            "url":         ed2k_url,
            "tome_number": _r.detect_tome(filename),
            "tag":         profiles.detect_tag(filename),
        }
    return None


def get_best_ed2k_per_tome(ed2k_links):
    best = {}
    for raw in ed2k_links:
        p = parse_ed2k(raw)
        if not p or not p["tome_number"]: continue
        t = p["tome_number"]
        if t not in best or profiles.is_better_than(p["tag"], best[t]["tag"]):
            best[t] = p
    return best


def find_thread_for_series(series_name: str, lib_id: str = None, threshold: float = 0.88):
    """
    Cherche le thread ebdz correspondant à une série.
    Si lib_id est fourni, filtre les threads selon les sources liées à cette librairie.
    """
    import cache as cache_mod

    # Détermine les source_ids pertinents pour cette librairie
    relevant_source_ids = None
    if lib_id:
        sources = get_sources()
        matching = [s["id"] for s in sources if lib_id in (s.get("library_ids") or [])]
        if matching:
            relevant_source_ids = set(matching)

    threads = get_all_threads()
    if not threads:
        return None

    # Filtre par source si pertinent
    if relevant_source_ids:
        filtered = [t for t in threads
                    if t.get("source_id") in relevant_source_ids
                    or (not t.get("source_id") and "manga" in relevant_source_ids)]
        # Fallback si aucun thread ne correspond aux sources filtrées
        if not filtered:
            filtered = threads
        threads = filtered

    norm_query = cache_mod._normalize(series_name)
    best = None
    best_score = 0.0
    for t in threads:
        norm_thread = cache_mod.normalize_filename_for_series(t["name"] + ".cbz")
        score = cache_mod._similarity(norm_query, norm_thread)
        if score > best_score:
            best_score = score
            best = t
    return best if best_score >= threshold else None


# ── RSS ──────────────────────────────────────────────────

def generate_rss(max_items=50, source_id=None):
    """Génère un flux RSS XML des derniers forums ebdz."""
    threads = get_all_threads(source_id=source_id)[:max_items]
    now_rfc = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")

    src = get_source(source_id) if source_id else None
    src_name = src["name"] if src else "ebdz.net"
    src_url  = src["url"]  if src else "https://ebdz.net/forum/forumdisplay.php?fid=29"

    def xe(s):
        return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    items = "\n".join(f"""    <item>
      <title>{xe(t.get('name',''))}</title>
      <link>{xe(t.get('url',''))}</link>
      <guid isPermaLink="true">{xe(t.get('url',''))}</guid>
      <description>{xe(t.get('name',''))}</description>
      <pubDate>{xe(t.get('last_seen',''))}</pubDate>
    </item>""" for t in threads)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>MangaArr — ebdz.net {xe(src_name)}</title>
    <link>{xe(src_url)}</link>
    <description>Nouveautés {xe(src_name)} ebdz.net via MangaArr</description>
    <language>fr</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
{items}
  </channel>
</rss>"""
