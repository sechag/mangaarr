"""
file_organizer.py — Pipeline de traitement des fichiers téléchargés

Pipeline pour chaque fichier arrivant dans Incoming :
  1. Identifier la série et le tome depuis le nom de fichier
  2. Trouver le dossier de destination dans les librairies configurées
  3. Copier le fichier (jamais déplacer, le source reste dans Incoming)
  4. Conversion CBR/PDF → CBZ sur la copie
  5. Renommage au format MangaArr sur la copie
Sécurité upgrade : l'ancien fichier est supprimé SEULEMENT après copie réussie.
"""

#!/usr/bin/env python3
"""
file_organizer.py — Organisation automatique des fichiers manga
Script indépendant (appelable aussi depuis watcher.py).

- Identifie la série et le tome d'un fichier
- Copie/déplace vers le dossier de série correspondant
- Renomme selon le format MangaArr si activé

Usage standalone :
    python file_organizer.py /chemin/vers/fichier.cbz
    python file_organizer.py --scan /chemin/incoming
"""
import os, sys, re, shutil, argparse, json
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def get_config():
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")
    # Append au log MangaArr
    try:
        import config as _cfg
        _cfg.add_log(f"[organizer] {msg}", "info")
    except Exception:
        pass


# ═══════════════════════════════════════════════════
# IDENTIFICATION
# ═══════════════════════════════════════════════════

def detect_series_from_filename(filename: str) -> tuple[str | None, str | None]:
    """
    Extrait le nom de série et le numéro de tome depuis un nom de fichier.
    Utilise renamer.detect_tome pour gérer tous les formats.
    Retourne (series_name, tome_number_str) ex: ("One Piece", "8") ou (None, None).
    """
    try:
        import renamer as _r
    except Exception:
        return None, None

    base = os.path.splitext(filename)[0]

    # Format MangaArr : Titre.T08.FRENCH.CBZ.eBook-TAG
    m = re.match(r"^(.+?)\.(T\d{2,3})\.FRENCH", base, re.IGNORECASE)
    if m:
        series   = m.group(1).replace(".", " ").strip()
        tome_raw = m.group(2).lstrip("Tt").lstrip("0") or "0"
        return series, tome_raw

    # Tous les autres formats via detect_tome (exhaustif)
    tome_tag = _r.detect_tome(filename)  # retourne "T08", "T108" ou None
    if tome_tag:
        # Extrait le numéro propre
        tome_num = tome_tag.lstrip("T").lstrip("0") or "0"
        tome_int = int(tome_num)

        # Isole le titre : tout ce qui précède la marque de tome
        # On cherche dans le nom original le motif qui a matché
        # et on coupe juste avant
        cut = base
        for pat in [
            rf"[.\-_]T{tome_int:02d}[.\-_\s]",
            rf"[.\-_]T{tome_int:03d}[.\-_\s]",
            rf"\bT{tome_int:02d}\b",
            rf"\bT{tome_int:03d}\b",
            rf"\bTome[.\-_\s]*{tome_int}\b",
            rf"\bVol(?:ume)?[.\-_\s]*\.?\s*{tome_int}\b",
            rf"#\s*{tome_int}\b",
            rf"\[{tome_int:02d}\]",
            rf"[-\s]{tome_int:02d}(?:\s*[-\s]|$)",
            rf"[\s.\-_]{tome_int:02d}$",
            rf"[\s.\-_]{tome_int:03d}$",
        ]:
            m2 = re.search(pat, base, re.IGNORECASE)
            if m2:
                cut = base[:m2.start()]
                break

        # Nettoie le titre extrait
        series = re.sub(r"[\s.\-_]+$", "", cut)              # trailing ponctuation
        series = re.sub(r"\(.*?\)$", "", series).strip()      # trailing parenthèse
        series = re.sub(r"\[.*?\]$", "", series).strip()      # trailing crochet
        series = series.replace(".", " ").replace("_", " ")
        # Supprime les mots Volume/Vol/Tome résiduels en fin de titre
        series = re.sub(r"\s+(?:Volume|Vol|Tome)\.?\s*$", "", series, flags=re.IGNORECASE)
        series = re.sub(r"\s+", " ", series).strip()

        if series:
            return series, str(tome_int)

    return None, None


def find_series_folder(series_name: str, root_dir: str) -> str | None:
    """
    Cherche le dossier d'une série dans root_dir.
    Priorité :
      1. Exact match insensible à la casse
      2. Exact match après normalisation (accents, apostrophes…)
      3. Matching flou ≥ 0.65 (comportement général)
    """
    if not series_name or not os.path.isdir(root_dir):
        return None

    try:
        import cache as _cache
    except Exception:
        return None

    name_lower = series_name.strip().lower()
    name_norm  = _cache._normalize(series_name)

    best_folder = None
    best_score  = 0.0

    for entry in os.listdir(root_dir):
        full = os.path.join(root_dir, entry)
        if not os.path.isdir(full):
            continue

        # ── Passe 1 : exact match insensible à la casse ──
        if entry.strip().lower() == name_lower:
            return full

        # ── Passe 2 : exact match normalisé ──
        if _cache._normalize(entry) == name_norm:
            return full

        # ── Passe 3 : fuzzy ──
        score = _cache._similarity(series_name, entry)
        if score > best_score:
            best_score  = score
            best_folder = full

    return best_folder if best_score >= 0.65 else None


# ═══════════════════════════════════════════════════
# ORGANISATION
# ═══════════════════════════════════════════════════

def organize_file(item: dict | None = None, filepath: str | None = None) -> dict:
    """
    Organise un fichier.
    Peut être appelé avec :
      - item : dict de la queue {filename, local_path, series_name, tome_number, ...}
      - filepath : chemin direct vers le fichier

    Pipeline :
      1. Conversion CBR/PDF → CBZ si activé
      2. Copie vers le dossier de la série
      3. Renommage au format MangaArr si activé

    Retourne {ok, message, dest_path}
    """
    cfg = get_config()
    mm  = cfg.get("media_management", {})
    auto_copy    = False  # TOUJOURS copier (jamais déplacer) depuis Incoming
    auto_rename  = mm.get("auto_rename",      True)
    auto_conv_cbr = mm.get("auto_convert_cbr", True)
    auto_conv_pdf = mm.get("auto_convert_pdf", True)
    # Pas besoin de download_dir — on cherche dans les librairies configurées
    root_dir = ""  # sera résolu depuis les librairies

    # Résolution du fichier source
    if item:
        src_path     = item.get("local_path", "")
        series_name  = item.get("series_name", "")
        raw_tome     = str(item.get("tome_number", "") or "")
        tome_number  = raw_tome.lstrip("Tt").lstrip("0") or "0"
        action       = item.get("action", "missing")    # "missing" ou "upgrade"
        owned_file   = item.get("owned_file", "")       # fichier à remplacer si upgrade
        series_exact = item.get("series_exact", False)  # nom de série fourni explicitement
    elif filepath:
        src_path     = filepath
        series_name  = None
        tome_number  = None
        action       = "missing"
        owned_file   = ""
        series_exact = False
    else:
        return {"ok": False, "message": "Aucun fichier fourni"}

    if not src_path or not os.path.isfile(src_path):
        return {"ok": False, "message": f"Fichier introuvable : {src_path}"}

    filename = os.path.basename(src_path)
    ext      = os.path.splitext(filename)[1].lower()

    # Identification AVANT conversion (depuis le nom original du fichier)
    if not series_name or not tome_number:
        series_name, tome_number = detect_series_from_filename(filename)

    if not series_name:
        return {"ok": False, "message": f"Impossible d'identifier la série de : {filename}"}

    log(f"Identification : '{series_name}' T{tome_number} — {filename}")

    # ── Trouve le dossier de destination dans les librairies configurées ──
    dest_folder = None
    try:
        import library_manager as _lm, cache as _cache
        libraries   = _lm.get_libraries()
        name_lower  = series_name.strip().lower()
        name_norm   = _cache._normalize(series_name)

        exact_folder = None
        best_score   = 0.0
        best_folder  = None
        best_lib     = None

        for lib in libraries:
            if not os.path.isdir(lib["path"]):
                continue

            # ── Exact match dans cette librairie (toujours prioritaire) ──
            for entry in os.listdir(lib["path"]):
                full = os.path.join(lib["path"], entry)
                if not os.path.isdir(full):
                    continue
                if entry.strip().lower() == name_lower or _cache._normalize(entry) == name_norm:
                    exact_folder = full
                    best_lib     = lib
                    break

            if exact_folder:
                break

            # ── Fuzzy : uniquement si le nom n'est PAS fourni de façon explicite ──
            if not series_exact:
                found = find_series_folder(series_name, lib["path"])
                if found:
                    score = _cache._similarity(series_name, os.path.basename(found))
                    if score > best_score:
                        best_score  = score
                        best_folder = found
                        best_lib    = lib

        if exact_folder:
            dest_folder = exact_folder
            log(f"Dossier trouvé (exact) dans '{best_lib['name']}' : {dest_folder}")
        elif best_folder:
            dest_folder = best_folder
            log(f"Dossier trouvé (fuzzy {best_score:.2f}) dans '{best_lib['name']}' : {dest_folder}")
        else:
            # Pas trouvé → crée dans la première librairie disponible
            if libraries and os.path.isdir(libraries[0]["path"]):
                safe_name   = re.sub(r'[<>:"/\\|?*]', '_', series_name)
                dest_folder = os.path.join(libraries[0]["path"], safe_name)
                os.makedirs(dest_folder, exist_ok=True)
                log(f"Dossier créé dans '{libraries[0]['name']}' : {dest_folder}")
            else:
                return {"ok": False, "message": "Aucune librairie configurée ou accessible (Settings > Librairies)"}
    except Exception as e:
        return {"ok": False, "message": f"Erreur recherche librairie : {e}"}

    # Nom de fichier final
    dest_filename = filename
    if auto_rename:
        try:
            import renamer as _r, profiles as _p
            tag          = _p.detect_tag(filename)
            folder_name  = os.path.basename(dest_folder)
            fmt          = _r.get_rename_format()
            _t_clean     = str(tome_number).lstrip("Tt").lstrip("0") or "0"
            tome_str     = f"T{int(_t_clean):02d}" if _t_clean else "T00"
            if fmt == 3:
                series_arg = _r.clean_title(_r.extract_leading_article(folder_name))
            else:
                series_arg = _r.clean_title_readable(_r.extract_leading_article(folder_name))
            dest_filename = _r.build_filename(series_arg, tome_str, tag, format_id=fmt)
        except Exception as e:
            log(f"Renommage échoué ({e}), nom original conservé")

    # ── Copie d'abord le fichier brut dans le dossier destination ──
    tmp_dest = os.path.join(dest_folder, filename)
    try:
        shutil.copy2(src_path, tmp_dest)
        log(f"Copié (brut) → {tmp_dest}")
    except Exception as e:
        return {"ok": False, "message": f"Erreur copie : {e}"}

    folder_name  = os.path.basename(dest_folder)
    working_path = tmp_dest

    try:
        import media_manager as _mm_mod

        # ── Vérifie si un .cbz est réellement un ZIP ou un RAR renommé ──
        if ext == ".cbz" and not _mm_mod.is_valid_zip(working_path):
            log(f"Faux CBZ détecté (RAR renommé) : {filename} — conversion en cours")
            result = _mm_mod.repair_fake_cbz(working_path)
            if result.get("ok"):
                working_path = result.get("new_path", working_path)
                log(f"Réparé CBZ : {os.path.basename(working_path)}")
            else:
                log(f"Réparation échouée : {result.get('message')} — fichier conservé tel quel")

        elif ext == ".cbr" and auto_conv_cbr:
            converted = _mm_mod.convert_cbr_to_cbz(working_path)
            if converted:
                working_path = converted
                log(f"Converti CBR → CBZ : {os.path.basename(working_path)}")

        elif ext == ".pdf" and auto_conv_pdf:
            converted = _mm_mod.convert_pdf_to_cbz(working_path)
            if converted:
                working_path = converted
                log(f"Converti PDF → CBZ : {os.path.basename(working_path)}")
    except Exception as e:
        log(f"Conversion ignorée ({e})")

    # ── Renommage au format MangaArr sur le fichier final ──
    dest_path = working_path  # par défaut : nom original copié
    if auto_rename:
        try:
            import renamer as _r, profiles as _p
            tag          = _p.detect_tag(os.path.basename(working_path))
            fmt          = _r.get_rename_format()
            _t_clean     = str(tome_number).lstrip("Tt").lstrip("0") or "0"
            tome_str     = f"T{int(_t_clean):02d}"
            if fmt == 3:
                series_arg = _r.clean_title(_r.extract_leading_article(folder_name))
            else:
                series_arg = _r.clean_title_readable(_r.extract_leading_article(folder_name))
            dest_filename = _r.build_filename(series_arg, tome_str, tag, format_id=fmt)
            dest_path     = os.path.join(dest_folder, dest_filename)
            if working_path != dest_path:
                if os.path.exists(dest_path) and mm.get("auto_replace", True):
                    # Sécurité : ne supprime l'ancien QUE si le nouveau est bien copié
                    os.replace(working_path, dest_path)
                    log(f"Renommé → {dest_filename}")
                elif not os.path.exists(dest_path):
                    os.rename(working_path, dest_path)
                    log(f"Renommé → {dest_filename}")
                else:
                    dest_path = working_path  # garde l'original si écrasement désactivé
        except Exception as e:
            log(f"Renommage échoué ({e}), nom original conservé")
            dest_path = working_path

    # ── Si upgrade : supprime l'ancien fichier APRÈS que le nouveau est bien en place ──
    if action == "upgrade" and owned_file and os.path.exists(dest_path):
        owned_path = os.path.join(dest_folder, owned_file)
        if os.path.exists(owned_path) and os.path.abspath(owned_path) != os.path.abspath(dest_path):
            try:
                os.remove(owned_path)
                log(f"Ancien fichier supprimé (remplacé) : {owned_file}")
            except Exception as e:
                log(f"Impossible de supprimer l'ancien fichier {owned_file} : {e}")

    log(f"Traitement terminé → {dest_path}")
    return {"ok": True, "message": f"Copié vers {dest_path}", "dest_path": dest_path,
            "action": action}


# ═══════════════════════════════════════════════════
# DÉTECTION DES CONFLITS (ACTION EN ATTENTE)
# ═══════════════════════════════════════════════════

_MANGA_EXTS = {".cbz", ".cbr", ".pdf", ".zip"}


def detect_conflicts(local_path: str, series_name: str) -> tuple:
    """
    Vérifie si des tomes du chemin local_path existent déjà dans la bibliothèque.
    Retourne (dest_folder: str | None, conflicts: list)
    conflicts = [{tome: int, new_file: str, current_file: str}]
    """
    import renamer as _r

    # ── Trouve le dossier de la série dans les librairies ──
    dest_folder = None
    try:
        import library_manager as _lm
        import cache as _cache
        best = 0.0
        for lib in _lm.get_libraries():
            if not os.path.isdir(lib["path"]):
                continue
            found = find_series_folder(series_name, lib["path"])
            if found:
                sc = _cache._similarity(series_name, os.path.basename(found))
                if sc > best:
                    best        = sc
                    dest_folder = found
    except Exception:
        pass

    if not dest_folder or not os.path.isdir(dest_folder):
        return None, []

    # ── Fichiers à vérifier ──
    files_to_check = []
    if os.path.isfile(local_path) and os.path.splitext(local_path)[1].lower() in _MANGA_EXTS:
        files_to_check = [local_path]
    elif os.path.isdir(local_path):
        files_to_check = sorted([
            os.path.join(local_path, f)
            for f in os.listdir(local_path)
            if os.path.splitext(f)[1].lower() in _MANGA_EXTS
            and not f.endswith(".!qB")
        ])

    conflicts = []
    for fp in files_to_check:
        fname    = os.path.basename(fp)
        tome_tag = _r.detect_tome(fname)
        if not tome_tag:
            continue
        n        = int(re.sub(r"[^0-9]", "", tome_tag) or "0")
        existing = _r._find_existing_tome(dest_folder, None, tome_tag)
        if existing:
            conflicts.append({
                "tome":         n,
                "new_file":     fname,
                "current_file": os.path.basename(existing),
            })

    return dest_folder, conflicts


# ═══════════════════════════════════════════════════
# SCAN D'UN DOSSIER
# ═══════════════════════════════════════════════════

def scan_and_organize(scan_dir: str) -> list:
    """Scanne un dossier et organise tous les CBZ/CBR trouvés."""
    results = []
    for fn in os.listdir(scan_dir):
        if not fn.lower().endswith((".cbz", ".cbr", ".pdf")):
            continue
        fp  = os.path.join(scan_dir, fn)
        res = organize_file(filepath=fp)
        res["file"] = fn
        results.append(res)
        log(f"{fn} → {res['message']}")
    return results


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MangaArr — Organiseur de fichiers manga")
    parser.add_argument("path", nargs="?", help="Fichier ou dossier à organiser")
    parser.add_argument("--scan", help="Scanne un dossier entier")
    args = parser.parse_args()

    target = args.scan or args.path
    if not target:
        parser.print_help()
        sys.exit(1)

    if os.path.isdir(target):
        results = scan_and_organize(target)
        ok  = sum(1 for r in results if r.get("ok"))
        err = len(results) - ok
        print(f"\nRésultat : {ok} organisé(s), {err} erreur(s)")
    elif os.path.isfile(target):
        r = organize_file(filepath=target)
        print(r["message"])
    else:
        print(f"ERREUR : '{target}' n'existe pas.")
        sys.exit(1)
