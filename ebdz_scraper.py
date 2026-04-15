"""
ebdz_scraper.py — Scraping ebdz.net
- Scrape complet de toutes les pages (lancé manuellement)
- Scrape partiel des N premières pages (toutes les 12h auto)
- Cache dans .cache/ebdz_threads.json
- Flux RSS depuis le cache
"""
import re, time, threading, json, os
from datetime import datetime
from urllib.parse import urljoin, unquote
from bs4 import BeautifulSoup
import requests
import config, profiles

FORUM_BASE_URL     = "https://ebdz.net/forum/"
FORUM_CATEGORY_URL = "https://ebdz.net/forum/forumdisplay.php?fid=29"
DELAY              = 2.0

def _get_cache_dir() -> str:
    """Utilise /data/cache en container ou .cache local en standalone."""
    try:
        import config as _c
        d = _c.get("_cache_dir") or os.path.join(os.path.dirname(__file__), ".cache")
    except Exception:
        d = os.path.join(os.path.dirname(__file__), ".cache")
    os.makedirs(d, exist_ok=True)
    return d

# Chemins dynamiques — résolus au premier accès
def _threads_cache() -> str:
    return os.path.join(_get_cache_dir(), "ebdz_threads.json")

def _state_file() -> str:
    return os.path.join(_get_cache_dir(), "scrape_state.json")

_scrape_state = {"running": False, "mode": "", "page": 0, "total": 0, "threads": 0}
_state_lock   = threading.Lock()
_scheduler    = None


# ── Persistance ─────────────────────────────────────────────

def _load_threads():
    try:
        with open(_threads_cache(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_threads(data):
    with open(_threads_cache(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_state():
    try:
        with open(_state_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_full": None, "last_partial": None}

def _save_state(state):
    with open(_state_file(), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

def get_all_threads():
    """Retourne [{url, name, last_seen}] trié par last_seen desc."""
    data = _load_threads()
    return sorted(data.values(), key=lambda x: x.get("last_seen", ""), reverse=True)

def get_state():
    with _state_lock:
        s = dict(_scrape_state)
    s.update(_load_state())
    return s


# ── Session ─────────────────────────────────────────────────

def make_session(mybbuser):
    s = requests.Session()
    s.cookies.set("mybbuser", mybbuser, domain="ebdz.net")
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    return s

def check_login(session):
    try:
        r = session.get(FORUM_CATEGORY_URL, timeout=15)
        return "member.php?action=login" not in r.url and r.status_code == 200
    except Exception:
        return False

def _get_total_pages(session):
    try:
        r = session.get(FORUM_CATEGORY_URL, timeout=15)
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


# ── Scrape d'une page ────────────────────────────────────────

def scrape_page(session, page):
    url = FORUM_CATEGORY_URL if page == 1 else f"{FORUM_CATEGORY_URL}&page={page}"
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
        config.add_log(f"Erreur scraping page {page}: {e}", "error")
        return []


# ── Scrape complet / partiel ─────────────────────────────────

def _run_scrape(mode, max_pages=None):
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

    total_pages = _get_total_pages(session)
    n = total_pages if mode == "full" else min(max_pages or 3, total_pages)
    with _state_lock:
        _scrape_state.update({"mode": mode, "page": 0, "total": n, "threads": 0})

    config.add_log(f"Scrape ebdz {mode} démarré ({n} pages)", "info")
    existing = _load_threads()
    now = datetime.now().isoformat(timespec="seconds")
    new_count = 0

    for page in range(1, n + 1):
        with _state_lock: _scrape_state["page"] = page
        threads = scrape_page(session, page)
        if not threads:
            break
        for url, name in threads:
            if url not in existing:
                new_count += 1
            existing[url] = {"url": url, "name": name, "last_seen": now}
        with _state_lock: _scrape_state["threads"] = len(existing)
        if page < n:
            time.sleep(DELAY)

    _save_threads(existing)
    state = _load_state()
    if mode == "full":
        state["last_full"] = now
    state["last_partial"] = now
    _save_state(state)
    config.add_log(f"Scrape ebdz {mode} terminé : {len(existing)} forums ({new_count} nouveaux)", "info")
    with _state_lock: _scrape_state["running"] = False


def start_scrape(mode="partial", max_pages=3):
    """Lance un scrape en arrière-plan. Retourne False si déjà en cours."""
    with _state_lock:
        if _scrape_state["running"]:
            return False
        _scrape_state["running"] = True
    threading.Thread(target=_run_scrape, args=(mode, max_pages), daemon=True).start()
    return True


# ── Scheduler 12h ────────────────────────────────────────────

def _scheduler_loop():
    """Scrape automatique selon la fréquence configurée dans Settings."""
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


# ── ED2K ─────────────────────────────────────────────────────

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
    import renamer as _r
    m = re.search(r"ed2k://\|file\|([^|]+)\|(\d+)\|([A-Fa-f0-9]+)\|", ed2k_url, re.IGNORECASE)
    if m:
        filename = unquote(m.group(1))
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


def find_thread_for_series(series_name, threshold=0.88):
    """
    Cherche le thread ebdz correspondant à une série par nom.
    Utilise normalize_filename_for_series sur les noms de forums (format "Titre (AUTEUR)")
    pour supprimer les auteurs entre parenthèses avant comparaison.
    Seuil par défaut 0.88.
    """
    import cache as cache_mod
    threads = get_all_threads()
    if not threads:
        return None
    norm_query = cache_mod._normalize(series_name)
    best = None
    best_score = 0.0
    for t in threads:
        # Normalise le nom du forum comme un nom de fichier pour supprimer "(AUTEUR)(AUTEUR2)"
        norm_thread = cache_mod.normalize_filename_for_series(t["name"] + ".cbz")
        score = cache_mod._similarity(norm_query, norm_thread)
        if score > best_score:
            best_score = score
            best = t
    return best if best_score >= threshold else None


# ── RSS ──────────────────────────────────────────────────────

def generate_rss(max_items=50):
    """Génère un flux RSS XML des derniers forums ebdz."""
    threads = get_all_threads()[:max_items]
    now_rfc = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")

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
    <title>MangaArr — ebdz.net Manga</title>
    <link>https://ebdz.net/forum/forumdisplay.php?fid=29</link>
    <description>Nouveautés manga ebdz.net via MangaArr</description>
    <language>fr</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
{items}
  </channel>
</rss>"""
