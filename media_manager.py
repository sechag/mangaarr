"""
media_manager.py — Conversion et packaging CBZ/CBR/PDF

Pipeline pour chaque fichier :
  1. Extraction du contenu vers un dossier portant le nom original du fichier
  2. Archive ZIP de ce dossier → .cbz final au format MangaArr

Exemples :
  Super HxEros T09 (...)(PapriKa+).cbz  →  Super.HxEROS.T09.FRENCH.CBZ.eBook-Paprika+.cbz
                                              └── Super HxEros T09 (...)(PapriKa+)/
                                                    ├── 001.jpg
                                                    └── 002.jpg ...

  Super HxEros T09 (...).cbr  →  même résultat (extraction unrar/7z)
  Super HxEros T09 (...).pdf  →  même résultat (rasterisation pdftoppm/convert)
"""
import os
import shutil
import zipfile
import subprocess
import tempfile
import re
import config
import renamer
import profiles


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


# ════════════════════════════════════════════════════════
# EXTRACTION
# ════════════════════════════════════════════════════════

def _extract_cbz(cbz_path: str, dest_dir: str) -> bool:
    """Extrait un CBZ (= ZIP) dans dest_dir."""
    try:
        with zipfile.ZipFile(cbz_path, "r") as zf:
            zf.extractall(dest_dir)
        return True
    except Exception as e:
        config.add_log(f"Erreur extraction CBZ {cbz_path}: {e}", "error")
        return False


def _extract_cbr(cbr_path: str, dest_dir: str) -> bool:
    """Extrait un CBR dans dest_dir via unrar ou 7z."""
    if _has_tool("unrar"):
        r = subprocess.run(["unrar", "x", "-y", cbr_path, dest_dir],
                           capture_output=True)
        return r.returncode == 0
    if _has_tool("7z"):
        r = subprocess.run(["7z", "x", cbr_path, f"-o{dest_dir}", "-y"],
                           capture_output=True)
        return r.returncode == 0
    config.add_log(f"unrar/7z manquant — impossible d'extraire {cbr_path}", "error")
    return False


def _extract_pdf(pdf_path: str, dest_dir: str) -> bool:
    """Rasterise un PDF en images JPEG dans dest_dir."""
    if _has_tool("pdftoppm"):
        r = subprocess.run(
            ["pdftoppm", "-jpeg", "-r", "150", pdf_path,
             os.path.join(dest_dir, "page")],
            capture_output=True,
        )
        return r.returncode == 0 and bool(os.listdir(dest_dir))
    if _has_tool("convert"):
        r = subprocess.run(
            ["convert", "-density", "150", pdf_path,
             os.path.join(dest_dir, "page-%04d.jpg")],
            capture_output=True,
        )
        return r.returncode == 0 and bool(os.listdir(dest_dir))
    config.add_log(f"pdftoppm/convert manquant — impossible de convertir {pdf_path}", "error")
    return False


# ════════════════════════════════════════════════════════
# PACKAGING : dossier → .cbz
# ════════════════════════════════════════════════════════

def _pack_folder_to_cbz(folder_path: str, cbz_dest: str) -> bool:
    """
    Archive le contenu de folder_path en cbz_dest.
    La structure interne est : NomDossier/fichiers...
    (le dossier lui-même est inclus comme racine de l'archive)
    """
    try:
        folder_name = os.path.basename(folder_path)
        with zipfile.ZipFile(cbz_dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(folder_path):
                for fn in sorted(files):
                    fp      = os.path.join(root, fn)
                    arcname = os.path.join(
                        folder_name,
                        os.path.relpath(fp, folder_path)
                    )
                    zf.write(fp, arcname)
        return True
    except Exception as e:
        config.add_log(f"Erreur packaging CBZ {cbz_dest}: {e}", "error")
        return False


# ════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ════════════════════════════════════════════════════════

def process_file(src_path: str, series_folder_name: str) -> dict:
    """
    Pipeline complet pour un fichier manga :

    1. Déterminer le nom MangaArr final (série, tome, tag)
    2. Extraire le contenu dans un dossier nommé comme le fichier original
    3. Archiver ce dossier en .cbz au format MangaArr
    4. Supprimer le fichier source

    Retourne {"status": "ok"|"error", "file": nom_final, "reason": ...}
    """
    cfg = config.load()
    mm  = cfg.get("media_management", {})
    ext = os.path.splitext(src_path)[1].lower()
    src_dir  = os.path.dirname(src_path)
    src_base = os.path.splitext(os.path.basename(src_path))[0]  # nom sans ext

    # ── 1. Nom MangaArr cible ────────────────────────────
    if mm.get("auto_rename", True):
        tag          = profiles.detect_tag(os.path.basename(src_path))
        tome_tag     = renamer.detect_tome(os.path.basename(src_path)) or "T00"
        series_clean = renamer.clean_title(renamer.extract_leading_article(series_folder_name))
        final_name   = renamer.build_filename(series_clean, tome_tag, tag)
    else:
        final_name = src_base + ".cbz"

    final_path = os.path.join(src_dir, final_name)

    # Si le fichier cible existe déjà et que auto_replace est activé → supprime
    if os.path.exists(final_path) and mm.get("auto_replace", True):
        os.remove(final_path)
    elif os.path.exists(final_path):
        config.add_log(f"Fichier cible déjà existant (auto_replace désactivé) : {final_name}", "warning")
        return {"status": "skipped", "file": final_name, "reason": "already_exists"}

    # ── 2. Extraction dans un dossier nommé comme l'original ──
    # Le dossier interne garde le nom original du fichier (sans ext)
    with tempfile.TemporaryDirectory() as tmpbase:
        # Crée le sous-dossier avec le nom original
        inner_dir = os.path.join(tmpbase, src_base)
        os.makedirs(inner_dir, exist_ok=True)

        extracted = False
        if ext == ".cbz":
            extracted = _extract_cbz(src_path, inner_dir)
        elif ext == ".cbr":
            if not mm.get("auto_convert_cbr", True):
                return {"status": "skipped", "file": os.path.basename(src_path),
                        "reason": "cbr_conversion_disabled"}
            extracted = _extract_cbr(src_path, inner_dir)
        elif ext == ".pdf":
            if not mm.get("auto_convert_pdf", True):
                return {"status": "skipped", "file": os.path.basename(src_path),
                        "reason": "pdf_conversion_disabled"}
            extracted = _extract_pdf(src_path, inner_dir)
        else:
            return {"status": "error", "file": os.path.basename(src_path),
                    "reason": f"format non supporté : {ext}"}

        if not extracted:
            return {"status": "error", "file": os.path.basename(src_path),
                    "reason": f"extraction échouée ({ext})"}

        # Vérifie qu'on a bien des fichiers extraits
        all_files = [f for _, _, fs in os.walk(inner_dir) for f in fs]
        if not all_files:
            return {"status": "error", "file": os.path.basename(src_path),
                    "reason": "dossier extrait vide"}

        # ── 3. Repack → .cbz final ───────────────────────────
        tmp_cbz = os.path.join(tmpbase, final_name)
        if not _pack_folder_to_cbz(inner_dir, tmp_cbz):
            return {"status": "error", "file": final_name, "reason": "packaging échoué"}

        # ── 4. Déplace le .cbz final à sa destination, supprime l'original ──
        shutil.move(tmp_cbz, final_path)

    # Supprime le fichier source original (sauf si c'est déjà le fichier final)
    if os.path.abspath(src_path) != os.path.abspath(final_path) and os.path.exists(src_path):
        try:
            os.remove(src_path)
        except Exception:
            pass

    config.add_log(f"Traité : {os.path.basename(src_path)} → {final_name}", "info")
    return {"status": "ok", "file": final_name}


def process_series_folder(series_dir: str) -> list[dict]:
    """Traite tous les fichiers d'un dossier série."""
    folder_name = os.path.basename(series_dir)
    results     = []
    for filename in sorted(os.listdir(series_dir)):
        fp  = os.path.join(series_dir, filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".cbz", ".cbr", ".pdf"):
            continue
        result           = process_file(fp, folder_name)
        result["series"] = folder_name
        results.append(result)
    return results


def scan_and_process_root(root_dir: str) -> list[dict]:
    """Scanne et traite un dossier racine contenant plusieurs séries."""
    all_results = []
    for folder in sorted(os.listdir(root_dir)):
        full_path = os.path.join(root_dir, folder)
        if os.path.isdir(full_path):
            results = process_series_folder(full_path)
            all_results.extend(results)
    return all_results


# ════════════════════════════════════════════════════════
# COMPATIBILITÉ (anciens appels)
# ════════════════════════════════════════════════════════

def convert_cbr_to_cbz(cbr_path: str) -> str | None:
    """Wrapper compatibilité — utilise le nouveau pipeline."""
    folder_name = os.path.basename(os.path.dirname(cbr_path))
    result = process_file(cbr_path, folder_name)
    if result["status"] == "ok":
        return os.path.join(os.path.dirname(cbr_path), result["file"])
    return None


def convert_pdf_to_cbz(pdf_path: str) -> str | None:
    """Wrapper compatibilité — utilise le nouveau pipeline."""
    folder_name = os.path.basename(os.path.dirname(pdf_path))
    result = process_file(pdf_path, folder_name)
    if result["status"] == "ok":
        return os.path.join(os.path.dirname(pdf_path), result["file"])
    return None


# ════════════════════════════════════════════════════════
# DÉTECTION ET RÉPARATION DES FAUX CBZ (RAR renommés)
# Logique du script Réparation_CBZ (repair_cbz.py) :
#   1. Détection par magic bytes "Rar!"
#   2. Extraction unrar vers un dossier temporaire
#   3. Recompression en ZIP (STORED, sans compression) — nom de fichier conservé
#   4. Remplacement atomique via un .cbz.tmp
# ════════════════════════════════════════════════════════

RAR_MAGIC = b"Rar!"


def is_valid_zip(file_path: str) -> bool:
    """Vérifie qu'un fichier CBZ est bien un ZIP valide (magic bytes PK\\x03\\x04)."""
    try:
        with open(file_path, "rb") as f:
            magic = f.read(4)
        return magic[:2] == b"PK"
    except Exception:
        return False


def is_rar_file(file_path: str) -> bool:
    """Vérifie qu'un fichier est une archive RAR (magic bytes Rar!)."""
    try:
        with open(file_path, "rb") as f:
            return f.read(4) == RAR_MAGIC
    except OSError:
        return False


def _extract_rar_to(rar_path: str, dest_dir: str) -> tuple[bool, str]:
    """Extrait une archive RAR vers dest_dir via unrar (fallback 7z)."""
    if _has_tool("unrar"):
        r = subprocess.run(
            ["unrar", "x", "-y", rar_path, dest_dir + os.sep],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or "").strip()
    else:
        err = "unrar indisponible"
    if _has_tool("7z"):
        r = subprocess.run(
            ["7z", "x", rar_path, f"-o{dest_dir}", "-y"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return True, ""
    return False, err or "unrar/7z indisponibles"


def repair_fake_cbz(file_path: str) -> dict:
    """
    Répare un .cbz qui est en réalité une archive RAR renommée.
    Extrait le contenu RAR et le recompresse en ZIP (STORED),
    en conservant le nom de fichier (remplacement atomique).
    Retourne {"ok", "repaired", "message", "new_path"}.
    """
    if not os.path.isfile(file_path):
        return {"ok": False, "repaired": False, "message": f"Fichier introuvable : {file_path}"}

    if not is_rar_file(file_path):
        if is_valid_zip(file_path):
            return {"ok": True, "repaired": False,
                    "message": "Déjà un ZIP valide", "new_path": file_path}
        return {"ok": False, "repaired": False,
                "message": "Ni ZIP ni RAR — format non réparable"}

    with tempfile.TemporaryDirectory() as tmp_dir:
        ok, err = _extract_rar_to(file_path, tmp_dir)
        if not ok:
            return {"ok": False, "repaired": False,
                    "message": f"Extraction échouée : {err}"}

        files_to_add = sorted(
            os.path.join(root, fn)
            for root, _, fns in os.walk(tmp_dir)
            for fn in fns
        )
        if not files_to_add:
            return {"ok": False, "repaired": False, "message": "Aucun fichier extrait"}

        tmp_zip = file_path + ".tmp"
        try:
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_STORED) as zf:
                for f in files_to_add:
                    zf.write(f, os.path.relpath(f, tmp_dir))
            os.replace(tmp_zip, file_path)
            config.add_log(f"[Repair] {os.path.basename(file_path)} : RAR→ZIP réparé", "info")
            return {"ok": True, "repaired": True, "message": "Réparé ✓", "new_path": file_path}
        except Exception as e:
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
            return {"ok": False, "repaired": False, "message": f"Création ZIP échouée : {e}"}


def scan_series_for_fake_cbz(series_dir: str) -> list:
    """
    Scanne un dossier de série et retourne la liste des CBZ,
    avec valid=False pour les archives RAR renommées.
    Retourne [{filename, path, valid}].
    """
    results = []
    if not os.path.isdir(series_dir):
        return results
    for fn in sorted(os.listdir(series_dir)):
        if not fn.lower().endswith(".cbz"):
            continue
        fp = os.path.join(series_dir, fn)
        results.append({"filename": fn, "path": fp, "valid": not is_rar_file(fp)})
    return results


def scan_library_for_fake_cbz(library_root: str) -> list:
    """
    Scanne une bibliothèque (un dossier par série) et retourne les faux CBZ.
    Logique du script repair_cbz_library.py.
    Retourne [{series, filename, path}].
    """
    fakes = []
    if not os.path.isdir(library_root):
        return fakes
    for entry in sorted(os.listdir(library_root)):
        series_dir = os.path.join(library_root, entry)
        if not os.path.isdir(series_dir):
            continue
        for root, _, fns in os.walk(series_dir):
            for fn in sorted(fns):
                if not fn.lower().endswith(".cbz"):
                    continue
                fp = os.path.join(root, fn)
                if is_rar_file(fp):
                    fakes.append({"series": entry, "filename": fn, "path": fp})
    return fakes
