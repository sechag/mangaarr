"""
renamer.py — Logique de renommage des fichiers manga
Adapté depuis Renommage_{titre}.TXX.cbz.FR-{TAG}_AIO_FIX.py
Format de sortie : {Titre}.T{XX}.FRENCH.CBZ.eBook-{TAG}.cbz
"""
import os
import re
import shutil
import unicodedata
import config
import profiles


# ═══════════════════════════════════════════════════════
# NORMALISATION
# ═══════════════════════════════════════════════════════

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def extract_leading_article(folder_name: str) -> str:
    """Déplace l'article en fin de parenthèse vers le début."""
    match = re.search(
        r"\((l'|d'|le|la|les|du|des|une|un|the)\)$",
        folder_name.strip(),
        re.IGNORECASE,
    )
    if match:
        article_raw = match.group(1).lower()
        title = folder_name[: match.start()].strip()
        if article_raw in ("l'", "d'"):
            return f"{article_raw.upper()}{title}"
        return f"{article_raw.capitalize()} {title}"
    return folder_name


def clean_title(title: str) -> str:
    title = normalize(title)
    title = title.replace("'", ".")
    title = title.replace("-", ".")
    title = re.sub(r"[^A-Za-z0-9\s.]", "", title)
    title = re.sub(r"\s+", ".", title)
    title = re.sub(r"\.+", ".", title)
    return title.strip(".")


# ═══════════════════════════════════════════════════════
# DÉTECTION TOME
# ═══════════════════════════════════════════════════════

def detect_tome(filename: str) -> str | None:
    """
    Extrait le numéro de tome depuis un nom de fichier.
    Gère tous les formats courants :
      T01, T001, Tome 1, Tome 01, Vol 1, Vol. 01, Volume 01,
      #01, [01], - 01 -, _01_, numéro seul en fin de nom.
    Retourne "T08", "T108" etc. ou None si non trouvé.
    """
    name = os.path.splitext(filename)[0]
    patterns = [
        # Format MangaArr : .T08. / .T108. (priorité haute)
        r"[.\-_]T(\d{1,3})[.\-_\s]",
        # T + chiffres (word boundary) : T09, T108
        r"\bT(\d{1,3})\b",
        # Tome / tome + chiffres : Tome 1, Tome 27, Tome001
        r"\bTome[.\-_\s]*(\d{1,3})\b",
        # Vol / Volume + chiffres : Vol 1, Vol. 41, Volume 01
        r"\bVol(?:ume)?[.\-_\s]*\.?\s*(\d{1,3})\b",
        # #01
        r"#\s*(\d{1,3})\b",
        # [01]
        r"\[(\d{1,3})\]",
        # - 01 - ou - 01 en fin
        r"[-\s](\d{1,3})(?:\s*[-\s]|$)",
        # _01_ ou _01 fin
        r"_(\d{1,3})(?:_|$)",
        # numéro 2-3 chiffres en fin de nom (après espace/point/tiret)
        r"[\s.\-_](\d{2,3})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            number = int(match.group(1))
            if 0 < number <= 999:
                return f"T{number:02d}"
    return None


# ═══════════════════════════════════════════════════════
# CONSTRUCTION NOM DE FICHIER
# ═══════════════════════════════════════════════════════

def get_rename_format() -> int:
    """Retourne le format de renommage configuré (1, 2, ou 3). Défaut : 1."""
    try:
        import config as _cfg
        return int(_cfg.load().get("media_management", {}).get("rename_format", 1))
    except Exception:
        return 1


def clean_title_readable(title: str) -> str:
    """Titre lisible avec espaces — pour les formats 1 et 2."""
    title = normalize(title)
    title = re.sub(r"[^A-Za-z0-9\s'\-]", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def build_filename(series_clean: str, tome: str, tag: str, format_id: int = None) -> str:
    """
    Construit le nom de fichier selon le format choisi.
    format_id :
      1 → "{Titre} Tome XX ({TAG}).cbz"          (défaut)
      2 → "Tome XX ({TAG}).cbz"
      3 → "{Titre}.TXX.FRENCH.CBZ.eBook-{TAG}.cbz"
    Si format_id est None, lit la config.
    """
    if format_id is None:
        format_id = get_rename_format()

    # Numéro propre depuis "T08" → 8
    try:
        n = int(re.sub(r"[^0-9]", "", tome) or "0")
    except Exception:
        n = 0

    if format_id == 2:
        return f"Tome {n:02d} ({tag.strip()}).cbz"
    elif format_id == 3:
        tag_normalized = tag.replace(" ", ".")
        return f"{series_clean}.{tome}.FRENCH.CBZ.eBook-{tag_normalized}.cbz"
    else:  # Format 1 (défaut)
        # Convertit les points en espaces pour le titre lisible
        series_readable = series_clean.replace(".", " ").strip()
        return f"{series_readable} Tome {n:02d} ({tag.strip()}).cbz"


def parse_existing_filename(filename: str) -> dict | None:
    """
    Tente de parser un fichier déjà renommé par MangaArr (tous formats).
    Retourne {series, tome, tag} ou None.
    series peut être None pour le Format 2.
    """
    # Format 3 : One.Piece.T08.FRENCH.CBZ.eBook-Paprika+.cbz
    m = re.match(r"^(.+?)\.(T\d{2,3})\.FRENCH\.CBZ\.eBook-(.+)\.cbz$", filename, re.IGNORECASE)
    if m:
        tag_raw  = m.group(3).replace(".", " ")
        detected = profiles.detect_tag(tag_raw)
        return {
            "series": m.group(1),
            "tome":   m.group(2).upper(),
            "tag":    detected if detected != "Notag" else tag_raw,
        }

    # Format 1 : One Piece Tome 08 (Paprika+).cbz
    m = re.match(r"^(.+?)\s+Tome\s+(\d{1,3})\s+\((.+?)\)\.cbz$", filename, re.IGNORECASE)
    if m:
        n   = int(m.group(2))
        tag = profiles.detect_tag(m.group(3)) or m.group(3)
        return {"series": m.group(1), "tome": f"T{n:02d}", "tag": tag}

    # Format 2 : Tome 08 (Paprika+).cbz
    m = re.match(r"^Tome\s+(\d{1,3})\s+\((.+?)\)\.cbz$", filename, re.IGNORECASE)
    if m:
        n   = int(m.group(1))
        tag = profiles.detect_tag(m.group(2)) or m.group(2)
        return {"series": None, "tome": f"T{n:02d}", "tag": tag}

    return None


# ═══════════════════════════════════════════════════════
# RENOMMAGE D'UN FICHIER
# ═══════════════════════════════════════════════════════

def rename_file(old_path: str, series_folder_name: str) -> dict:
    """
    Renomme un fichier CBZ/CBR selon le format MangaArr.
    Retourne un dict avec le résultat de l'opération.
    """
    filename = os.path.basename(old_path)
    folder = os.path.dirname(old_path)

    series_raw = extract_leading_article(series_folder_name)
    series = clean_title(series_raw)

    tome = detect_tome(filename)
    if not tome:
        return {"status": "skipped", "reason": "tome_not_found", "file": filename}

    tag        = profiles.detect_tag(filename)
    fmt        = get_rename_format()
    series_arg = series if fmt == 3 else clean_title_readable(series_raw)
    new_name   = build_filename(series_arg, tome, tag, format_id=fmt)
    new_path   = os.path.join(folder, new_name)

    if old_path == new_path:
        return {"status": "unchanged", "file": filename}

    # Vérifie si un fichier avec ce tome existe déjà
    existing = _find_existing_tome(folder, series, tome)
    if existing and existing != old_path:
        existing_tag = profiles.detect_tag(os.path.basename(existing))

        # Vérifie must_contain / must_not_contain (ignore parenthèses et caractères spéciaux)
        ok, raison = profiles.passes_filters(filename)
        if not ok:
            return {
                "status": "skipped",
                "reason": raison,
                "file":   filename,
            }

        if profiles.is_better_than(tag, existing_tag):
            # Remplace l'existant si meilleur score
            try:
                os.remove(existing)
                shutil.move(old_path, new_path)
                msg = f"Remplacé (score {profiles.get_tag_score(tag)} > {profiles.get_tag_score(existing_tag)}, {tag} > {existing_tag})"
                config.add_log(f"{series} {tome}: {msg}", "info")
                return {
                    "status": "replaced",
                    "old": filename,
                    "new": new_name,
                    "replaced": os.path.basename(existing),
                    "tag": tag,
                    "score": profiles.get_tag_score(tag),
                }
            except Exception as e:
                config.add_log(f"Erreur remplacement {filename}: {e}", "error")
                return {"status": "error", "reason": str(e), "file": filename}
        else:
            return {
                "status": "skipped",
                "reason": f"score_inferior ({profiles.get_tag_score(tag)} < {profiles.get_tag_score(existing_tag)})",
                "file": filename,
            }

    try:
        shutil.move(old_path, new_path)
        config.add_log(f"Renommé: {filename} → {new_name}", "info")
        return {"status": "renamed", "old": filename, "new": new_name, "tag": tag}
    except Exception as e:
        config.add_log(f"Erreur renommage {filename}: {e}", "error")
        return {"status": "error", "reason": str(e), "file": filename}


def _find_existing_tome(folder: str, series: str | None, tome: str) -> str | None:
    """
    Cherche un fichier existant pour ce tome dans le dossier (tous formats).
    series peut être None — on compare uniquement sur le numéro de tome
    puisque le dossier appartient déjà à la série.
    """
    for fn in os.listdir(folder):
        if not fn.lower().endswith((".cbz", ".cbr")):
            continue
        parsed = parse_existing_filename(fn)
        if parsed and parsed["tome"] == tome:
            return os.path.join(folder, fn)
    return None


# ═══════════════════════════════════════════════════════
# RENOMMAGE D'UN DOSSIER SÉRIE
# ═══════════════════════════════════════════════════════

def rename_series_folder(series_dir: str) -> list[dict]:
    """
    Renomme tous les CBZ/CBR d'un dossier de série.
    Retourne la liste des résultats.
    """
    folder_name = os.path.basename(series_dir)
    results = []

    for filename in sorted(os.listdir(series_dir)):
        if not filename.lower().endswith((".cbz", ".cbr")):
            continue
        old_path = os.path.join(series_dir, filename)
        result = rename_file(old_path, folder_name)
        result["series"] = folder_name
        results.append(result)

    return results


# ═══════════════════════════════════════════════════════
# RENOMMAGE D'UN DOSSIER RACINE (contenant plusieurs séries)
# ═══════════════════════════════════════════════════════

def rename_root_folder(root_dir: str) -> list[dict]:
    """
    Renomme toutes les séries dans un dossier racine.
    Retourne la liste complète des résultats.
    """
    all_results = []
    for folder in sorted(os.listdir(root_dir)):
        full_path = os.path.join(root_dir, folder)
        if not os.path.isdir(full_path):
            continue
        results = rename_series_folder(full_path)
        all_results.extend(results)
    return all_results
