"""
torznab_client.py — Client Torznab pour MangaArr
Recherche de releases manga via protocole Torznab (Prowlarr/Jackett).
"""
import re
import requests
import xml.etree.ElementTree as ET
from typing import Optional

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"


# ═══════════════════════════════════════════════════
# RECHERCHE
# ═══════════════════════════════════════════════════

def search_all(indexers: list, query: str, categories: list = None) -> list:
    """
    Recherche sur tous les indexers actifs.
    Retourne la liste fusionnée de releases.
    """
    if categories is None:
        categories = [7000]
    results = []
    for idx in indexers:
        if not idx.get("enabled", True):
            continue
        releases = search(idx, query, categories)
        results.extend(releases)
    # Déduplique par titre
    seen = set()
    unique = []
    for r in results:
        key = r["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def search(indexer: dict, query: str, categories: list = None) -> list:
    """
    Recherche via un indexer Torznab.
    indexer: {name, url, apikey}
    Retourne une liste de releases.
    """
    if categories is None:
        categories = [7000]

    url = indexer.get("url", "").rstrip("/")
    apikey = indexer.get("apikey", "")

    if not url:
        return []

    params = {
        "t": "search",
        "q": query,
        "cat": ",".join(str(c) for c in categories),
    }
    if apikey:
        params["apikey"] = apikey

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return _parse_rss(r.content, indexer.get("name", ""))
    except Exception as e:
        try:
            import config as _cfg
            _cfg.add_log(f"[Torznab] Erreur {indexer.get('name','?')} : {e}", "warning")
        except Exception:
            pass
        return []


def test_indexer(indexer: dict) -> dict:
    """Teste la connexion à un indexer Torznab (capabilities)."""
    url = indexer.get("url", "").rstrip("/")
    apikey = indexer.get("apikey", "")
    if not url:
        return {"ok": False, "message": "URL requise"}
    params = {"t": "caps"}
    if apikey:
        params["apikey"] = apikey
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        title_el = root.find(".//title")
        name = title_el.text if title_el is not None else "Indexer OK"
        return {"ok": True, "message": f"Connecté : {name}"}
    except ET.ParseError:
        return {"ok": True, "message": "Connecté (réponse non XML standard)"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


# ═══════════════════════════════════════════════════
# PARSING XML RSS TORZNAB
# ═══════════════════════════════════════════════════

def _parse_rss(content: bytes, indexer_name: str = "") -> list:
    """Parse la réponse XML Torznab et retourne les releases enrichies."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    releases = []
    for item in channel.findall("item"):
        title    = _text(item, "title") or ""
        link     = _text(item, "link") or ""
        guid     = _text(item, "guid") or ""
        comments = _text(item, "comments") or ""
        size    = 0
        seeders = 0
        peers   = 0
        magnet  = ""

        # Attributs Torznab
        for attr in item.findall(f"{{{TORZNAB_NS}}}attr"):
            n = attr.get("name", "")
            v = attr.get("value", "")
            if n == "size":
                try: size = int(v)
                except: pass
            elif n == "seeders":
                try: seeders = int(v)
                except: pass
            elif n in ("peers", "leechers"):
                try: peers += int(v)
                except: pass
            elif n == "magneturl":
                magnet = v

        # Taille depuis enclosure si pas trouvée
        enc = item.find("enclosure")
        if enc is not None:
            if size == 0:
                try: size = int(enc.get("length", 0))
                except: pass
            # enclosure url = lien de téléchargement .torrent si pas de link
            if not link:
                link = enc.get("url", "")

        if not title:
            continue

        # URL de la release (page web sur l'indexer).
        # Priorité : <comments> (page forum — ce que Prowlarr y met)
        #            puis <guid> si c'est une URL HTTP externe (pas une URL Prowlarr)
        # On EXCLUT les URLs qui ressemblent à une URL interne Prowlarr (/api, /download, etc.)
        def _is_indexer_page(u: str) -> bool:
            if not u or not u.startswith("http"):
                return False
            # Exclut les URLs Prowlarr internes contenant apikey= ou /api/
            import re as _re
            return not _re.search(r'(apikey=|/api/|/download|\.torrent)', u, _re.IGNORECASE)

        if _is_indexer_page(comments):
            release_url = comments
        elif _is_indexer_page(guid):
            release_url = guid
        else:
            release_url = ""

        vol_info = detect_volume_info(title)

        releases.append({
            "title":       title,
            "link":        magnet or link,
            "release_url": release_url,
            "size":        size,
            "seeders":     seeders,
            "peers":       peers,
            "indexer":     indexer_name,
            "vol_type":    vol_info["type"],
            "tomes":       vol_info["tomes"],
            "tome_start":  vol_info.get("start"),
            "tome_end":    vol_info.get("end"),
        })

    return releases


def _text(el, tag: str) -> Optional[str]:
    child = el.find(tag)
    return child.text if child is not None else None


# ═══════════════════════════════════════════════════
# DÉTECTION TOME / TYPE DE RELEASE
# ═══════════════════════════════════════════════════

def detect_volume_info(title: str) -> dict:
    """
    Analyse le titre d'une release torrent et détecte :
      - type  : 'single' | 'integrale' | 'pack' | 'unknown'
      - tomes : liste des numéros de tome (int)
      - start / end : pour packs et single

    Exemples supportés :
      "One.Piece.T08.FRENCH.CBZ-NOTAG"                    → single, [8]
      "Dragon.Ball.INTEGRALE.FRENCH"                       → integrale, []
      "Ai.Non.Stop.T01.a.T08.FRENCH.CBR-NOTAG"            → pack, [1..8]
      "YAWARA.[T01.T09].FR.[CBZ]-TONER"                   → pack, [1..9]
      "No.Longer.Rangers.[T01-12].FR.[CBZ]-NOTAG"         → pack, [1..12]
    """
    upper = title.upper()

    # ── INTÉGRALE ──
    if re.search(r'\b(INTEGRALE|INTEGRAL|COMPLETE|COMPLET|OMNIBUS|COFFRET)\b', upper):
        return {"type": "integrale", "tomes": [], "start": None, "end": None}

    # ── PACK [T01.T09] ou [T01-09] ou [T01-T09] ou [01-41] ou [01 à 41] ──
    # Avec ou sans préfixe T, avec séparateur . - à a au to
    m = re.search(
        r'\[T?(\d{1,3})\s*[.\-]\s*T?(\d{1,3})\]'   # [T01.T09] [01-41]
        r'|\[T?(\d{1,3})\s+[àaA][uU]?\s+T?(\d{1,3})\]',  # [1 à 41] [01 au 41]
        title, re.IGNORECASE
    )
    if m:
        if m.group(1) is not None:
            s, e = int(m.group(1)), int(m.group(2))
        else:
            s, e = int(m.group(3)), int(m.group(4))
        if e >= s:
            return {"type": "pack", "tomes": list(range(s, e + 1)), "start": s, "end": e}

    # ── PACK T01.a.T08 / T01 A T08 / T01 AU T08 / T01 À T08 ──
    m = re.search(
        r'T(\d{1,3})[.\s]+(?:AU?|TO|À)[.\s]+T(\d{1,3})',
        title, re.IGNORECASE
    )
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        if e >= s:
            return {"type": "pack", "tomes": list(range(s, e + 1)), "start": s, "end": e}

    # ── PACK T01-T08 sans crochets ──
    m = re.search(r'\bT(\d{1,3})-T(\d{1,3})\b', title, re.IGNORECASE)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        if e >= s:
            return {"type": "pack", "tomes": list(range(s, e + 1)), "start": s, "end": e}

    # ── PACK Tome.N.à.N / Tome N à N (sans crochets) ──
    m = re.search(
        r'(?:Tome|Vol|T)[.\s]*(\d{1,3})\s*[àa]\s*(?:Tome|Vol|T)?[.\s]*(\d{1,3})',
        title, re.IGNORECASE
    )
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        if e > s:   # e > s pour éviter de confondre "Tome 1" seul
            return {"type": "pack", "tomes": list(range(s, e + 1)), "start": s, "end": e}

    # ── SINGLE T08 / Tome 08 / Vol 08 ──
    m = re.search(r'\bT(?:ome)?[.\-_\s]*(\d{1,3})\b', title, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if 0 < n <= 999:
            return {"type": "single", "tomes": [n], "start": n, "end": n}

    # Numéro isolé en fin de titre
    m = re.search(r'[.\-_\s](\d{2,3})(?:[.\-_\s]|$)', title)
    if m:
        n = int(m.group(1))
        if 0 < n <= 999:
            return {"type": "single", "tomes": [n], "start": n, "end": n}

    return {"type": "unknown", "tomes": [], "start": None, "end": None}
