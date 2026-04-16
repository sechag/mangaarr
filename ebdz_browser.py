"""
ebdz_browser.py — Proxy de navigation ebdz.net pour MangaArr

- Fetche les pages ebdz.net avec le cookie mybbuser stocké
- Réécrit tous les liens internes pour naviguer via le proxy
- Intercepte les liens ed2k via postMessage vers la page parent
- Réécrit les images pour les servir directement depuis ebdz.net
"""
import re, requests
from urllib.parse import urljoin, quote
from bs4 import BeautifulSoup
import config

EBDZ_BASE  = "https://ebdz.net"
EBDZ_HOME  = "https://ebdz.net/forum/forumdisplay.php?fid=29"
PROXY_PATH = "/api/ebdz-proxy"


def make_session(mybbuser: str) -> requests.Session:
    s = requests.Session()
    s.cookies.set("mybbuser", mybbuser, domain="ebdz.net")
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": EBDZ_BASE,
    })
    return s


def fetch_and_rewrite(url: str, mybbuser: str) -> dict:
    """
    Fetche une page ebdz.net et réécrit les liens pour le proxy.
    Retourne {"ok": bool, "html": str, "final_url": str}
    """
    if not (url.startswith("https://ebdz.net") or url.startswith("http://ebdz.net")):
        return {"ok": False, "html": "<p>URL non autorisée</p>", "final_url": url}

    session = make_session(mybbuser)
    try:
        r = session.get(url, timeout=15, allow_redirects=True)
        r.raise_for_status()
        final_url = r.url
    except Exception as e:
        return {"ok": False, "html": f"<p>Erreur réseau : {e}</p>", "final_url": url}

    soup = BeautifulSoup(r.content, "html.parser")

    # Supprime les scripts ebdz (évite les erreurs JS dans notre contexte)
    for tag in soup.find_all("script"):
        tag.decompose()
    for tag in soup.find_all("iframe"):
        tag.decompose()

    # Réécrit les liens <a href>
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        abs_url = urljoin(final_url, href)

        if href.lower().startswith("ed2k://"):
            # Lien ed2k → intercepte via postMessage vers la page parent
            safe = href.replace("\\", "\\\\").replace("'", "\\'")
            a["href"] = "#"
            a["onclick"] = f"parent.postMessage({{type:'ed2k',url:'{safe}'}}, '*'); return false;"
            a["style"] = (a.get("style", "") +
                          ";color:#f59e0b!important;font-weight:bold;cursor:pointer")
            a["title"] = "Ajouter à la queue MangaArr"
        elif abs_url.startswith(EBDZ_BASE):
            # Lien interne → passe par le proxy
            # safe=':/?=&#' évite le double-encodage quand le navigateur suit le lien
            a["href"] = f"{PROXY_PATH}?url={quote(abs_url, safe=':/?=&#')}"
            if a.get("target"):
                del a["target"]
        else:
            # Lien externe → nouvelle fenêtre
            a["target"] = "_blank"
            a["rel"] = "noopener noreferrer"

    # Réécrit les src des images pour les charger directement
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if src.startswith("//"):
            img["src"] = "https:" + src
        elif src.startswith("/") and not src.startswith("//"):
            img["src"] = EBDZ_BASE + src
        elif not src.startswith("http"):
            img["src"] = urljoin(final_url, src)

    # Réécrit les formulaires pour passer par le proxy
    for form in soup.find_all("form", action=True):
        abs_action = urljoin(final_url, form["action"])
        if abs_action.startswith(EBDZ_BASE):
            form["action"] = f"{PROXY_PATH}?url={quote(abs_action, safe=':/?=&#')}"

    # Injecte un script qui notifie le parent de l'URL courante
    inject = soup.new_tag("script")
    inject.string = (
        f"try{{ parent.postMessage({{type:'nav',url:{repr(final_url)}}}, '*'); }}catch(e){{}}"
    )
    if soup.body:
        soup.body.append(inject)
    else:
        soup.append(inject)

    return {"ok": True, "html": str(soup), "final_url": final_url}
