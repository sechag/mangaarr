"""
amule_client.py — Interface amulecmd pour MangaArr
Commandes :
  add <ed2k://...>   — Ajoute un lien à eMule
  cancel <hash>      — Annule un téléchargement par hash ed2k
"""
import subprocess, shutil, time

DELAY_BETWEEN_COMMANDS = 0.4  # secondes entre commandes (évite saturation aMule)


def _amulecmd(client: dict, command: str) -> dict:
    binary = shutil.which("amulecmd") or "amulecmd"
    host   = client.get("host", "localhost")
    port   = str(client.get("ec_port", 4712))
    passwd = client.get("password", "")
    cmd    = [binary, "-h", host, "-p", port, "-P", passwd, "-c", command]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        ok     = result.returncode == 0
        return {"ok": ok, "output": result.stdout.strip(), "error": result.stderr.strip()}
    except FileNotFoundError:
        return {"ok": False, "output": "", "error": "amulecmd introuvable dans PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": "Timeout amulecmd"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def test_connection(client: dict) -> dict:
    r = _amulecmd(client, "status")
    if r["ok"]:
        msg = r["output"][:80] or "Connecté"
        return {"ok": True, "message": f"aMule connecté — {msg}"}
    return {"ok": False, "message": r["error"] or "Connexion échouée"}


def add_ed2k(client: dict, url: str) -> dict:
    return _amulecmd(client, f"add {url}")


def add_ed2k_batch(client: dict, urls: list) -> dict:
    """Ajoute une liste de liens ed2k un par un avec délai."""
    added  = 0
    errors = []
    for url in urls:
        r = _amulecmd(client, f"add {url}")
        if r["ok"]:
            added += 1
        else:
            errors.append({"url": url, "error": r["error"]})
        time.sleep(DELAY_BETWEEN_COMMANDS)
    return {"ok": True, "added": added, "errors": errors}


def cancel_hash(client: dict, filehash: str) -> dict:
    return _amulecmd(client, f"cancel {filehash}")


def cancel_hashes_batch(client: dict, hashes: list) -> dict:
    """Annule plusieurs téléchargements un par un avec délai."""
    cancelled = 0
    errors    = []
    for h in hashes:
        r = _amulecmd(client, f"cancel {h}")
        if r["ok"]:
            cancelled += 1
        else:
            errors.append({"hash": h, "error": r["error"]})
        time.sleep(DELAY_BETWEEN_COMMANDS)
    return {"ok": True, "cancelled": cancelled, "errors": errors}
