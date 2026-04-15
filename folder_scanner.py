"""
folder_scanner.py — Détection des tomes manquants par analyse du disque

Flux : scan dossier série → compare avec ebdz → ajoute à la queue
Pas de dépendance à Komga. Utilise les librairies configurées.
Fonctions principales :
  scan_series_on_disk()          — Inventaire des tomes sur disque avec leur tag/score
  detect_missing_from_disk()     — Compare disque vs ebdz, retourne les manquants + upgrades
"""
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


# ════════════════════════════════════════════════════════
# LECTURE DU DOSSIER DESTINATION
# ════════════════════════════════════════════════════════

def scan_series_on_disk(root_dir: str) -> dict:
    """
    Scanne root_dir et retourne un dict :
      { "Nom Série": {tomes_presents: {1,3,5}, files: [...]} }

    Exemple de structure attendue :
      root_dir/
        7 Shakespeares/
          7.Shakespeares.T03.FRENCH.CBZ.eBook-Paprika+.cbz
          7.Shakespeares.T04.FRENCH.CBZ.eBook-Paprika+.cbz
        One Piece/
          One.Piece.T001.FRENCH.CBZ.eBook-PRiNTER.cbz
    """
    import renamer as _r

    if not root_dir or not os.path.isdir(root_dir):
        return {}

    series_map = {}

    for entry in sorted(os.listdir(root_dir)):
        series_dir = os.path.join(root_dir, entry)
        if not os.path.isdir(series_dir):
            continue

        tomes   = set()
        files   = []

        # {numero: {filename, tag, score}}
        tomes_info = {}

        for fn in sorted(os.listdir(series_dir)):
            if not fn.lower().endswith((".cbz", ".cbr", ".pdf", ".zip")):
                continue
            files.append(fn)
            tome_tag = _r.detect_tome(fn)
            if tome_tag:
                n = int(tome_tag.lstrip("T").lstrip("0") or "0")
                if n > 0:
                    import profiles as _p
                    tag   = _p.detect_tag(fn)
                    score = _p.get_tag_score(tag)
                    # Garde le meilleur score si plusieurs fichiers pour le même tome
                    if n not in tomes_info or score > tomes_info[n]["score"]:
                        tomes_info[n] = {"filename": fn, "tag": tag, "score": score}

        series_map[entry] = {
            "tomes_presents": set(tomes_info.keys()),
            "tomes_info":     tomes_info,   # {num: {filename, tag, score}}
            "files":          files,
            "path":           series_dir,
        }

    return series_map


# ════════════════════════════════════════════════════════
# COMPARAISON AVEC EBDZ
# ════════════════════════════════════════════════════════

def detect_missing_from_disk(root_dir: str, progress_cb=None, serie_filter: str = None) -> dict:
    """
    Analyse le dossier disque et retourne les tomes manquants.

    progress_cb(label: str)  — appelé à chaque étape pour afficher la progression.
    serie_filter             — si renseigné, analyse seulement ce sous-dossier.

    Retourne :
    {
      "ok": True,
      "added": N,
      "details": [
        {"series": "...", "owned": N, "available": N, "missing": N, "added": N},
        ...
      ]
    }
    """
    import config as _cfg
    import config
    import ebdz_scraper
    import queue_manager
    import profiles as _p

    mybbuser = _cfg.get("mybbuser", "")
    if not mybbuser:
        return {"ok": False, "message": "Cookie ebdz non configuré (Settings > Indexers)"}

    if not root_dir or not os.path.isdir(root_dir):
        return {"ok": False, "message": f"Dossier de destination introuvable : '{root_dir}'"}

    session = ebdz_scraper.make_session(mybbuser)
    if not ebdz_scraper.check_login(session):
        return {"ok": False, "message": "Cookie ebdz invalide ou expiré"}

    if progress_cb:
        progress_cb("Lecture du dossier de destination…")

    series_map = scan_series_on_disk(root_dir)

    # Filtre sur une série spécifique si demandé
    if serie_filter:
        series_map = {k: v for k, v in series_map.items() if k == serie_filter}

    total = len(series_map)

    if not total:
        msg = f"Série '{serie_filter}' introuvable dans {root_dir}" if serie_filter               else "Aucun sous-dossier trouvé dans le dossier de destination"
        return {"ok": False, "message": msg}

    all_new_items = []
    details       = []

    for i, (series_name, data) in enumerate(series_map.items(), 1):
        if progress_cb:
            progress_cb(f"Analyse {i}/{total} — {series_name[:40]}")

        owned_nums = data["tomes_presents"]

        # Cherche le thread ebdz correspondant
        thread = ebdz_scraper.find_thread_for_series(series_name)
        if not thread:
            details.append({
                "series":  series_name,
                "found":   False,
                "owned":   len(owned_nums),
                "missing": 0,
                "added":   0,
            })
            continue

        # Scrape les liens ed2k disponibles
        raw_links    = ebdz_scraper.scrape_thread_ed2k(session, thread["url"])
        best_by_tome = ebdz_scraper.get_best_ed2k_per_tome(raw_links)

        # Compare : manquants OU tomes avec un score inférieur disponible sur ebdz
        new_items  = []
        tomes_info = data.get("tomes_info", {})

        for tome_str, parsed in best_by_tome.items():
            n = _extract_num(tome_str)
            if not n:
                continue

            filename   = parsed.get("filename", parsed.get("url", ""))
            ebdz_tag   = parsed.get("tag", "Notag")
            ebdz_score = _p.get_tag_score(ebdz_tag) if ebdz_tag != "Notag" else 0

            # ── Filtre must_contain / must_not_contain ──────────
            ok, raison = _p.passes_filters(filename)
            if not ok:
                config.add_log(
                    f"[Queue] Ignoré ({raison}) : {filename}", "info"
                )
                continue

            if n not in owned_nums:
                # Tome manquant → à télécharger
                item = dict(parsed)
                item["series_name"] = series_name
                item["series_id"]   = ""
                item["series_slug"] = ""
                item["action"]      = "missing"
                new_items.append(item)

            elif n in tomes_info:
                # Tome possédé → vérifie si le score ebdz est meilleur
                owned_score = tomes_info[n]["score"]
                owned_tag   = tomes_info[n]["tag"]
                if ebdz_score > owned_score:
                    item = dict(parsed)
                    item["series_name"]   = series_name
                    item["series_id"]     = ""
                    item["series_slug"]   = ""
                    item["action"]        = "upgrade"
                    item["owned_file"]    = tomes_info[n]["filename"]
                    item["owned_tag"]     = owned_tag
                    item["owned_score"]   = owned_score
                    new_items.append(item)

        all_new_items.extend(new_items)
        details.append({
            "series":    series_name,
            "found":     True,
            "thread":    thread["name"],
            "owned":     len(owned_nums),
            "available": len(best_by_tome),
            "missing":   len(new_items),
        })

    # Ajoute TOUS les items en une seule écriture disque
    total_added = queue_manager.add_to_queue(all_new_items)
    config.add_log(f"[Scan] {total_added} tome(s) ajouté(s) à la queue sur {total} séries", "info")

    # Génère le .emulecollection avec TOUS les items pending de la queue
    # (pas seulement les nouveaux — les doublons sont filtrés par add_to_queue
    #  donc all_new_items peut être vide même si des items sont en attente)
    collection_file = queue_manager.generate_emulecollection(label="missing")

    return {
        "ok":              True,
        "added":           len(all_new_items),
        "collection_file": os.path.basename(collection_file) if collection_file else None,
        "details":         details,
        "total_series":    total,
    }


def _extract_num(val) -> int | None:
    if val is None:
        return None
    m = re.search(r"(\d+)", str(val))
    return int(m.group(1)) if m else None


# ════════════════════════════════════════════════════════
# MAIN (usage standalone)
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse, json as _json
    import config as _cfg

    parser = argparse.ArgumentParser(description="MangaArr — Scanner tomes manquants (disque)")
    parser.add_argument("--dir", help="Dossier racine (défaut: download_dir de la config)")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parser.parse_args()

    root = args.dir or _cfg.get("download_dir", "")
    if not root:
        print("ERREUR : dossier non spécifié et download_dir non configuré.")
        sys.exit(1)

    def _print(msg): print(f"  → {msg}")

    result = detect_missing_from_disk(root, progress_cb=_print)

    if args.json:
        print(_json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\nRésultat : {result.get('added', 0)} tome(s) manquant(s) ajouté(s) à la queue")
        for d in result.get("details", []):
            if d.get("missing", 0) > 0:
                print(f"  {d['series']} : {d['owned']} possédés, {d['missing']} manquants")
