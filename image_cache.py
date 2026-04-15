"""
image_cache.py — Cache des couvertures en WebP

Extrait la première image de chaque CBZ/CBR/PDF et la compresse en WebP
(50% de la résolution originale, qualité 75).
Requiert : Pillow (pip install Pillow) ou cwebp (apt install webp)
Cache sur disque : invalide seulement si le fichier source a changé (mtime).
Usage standalone : python image_cache.py --stats | --clear | --rebuild
"""
import os
import json
import hashlib
import threading
import struct
import zlib
import zipfile
import tempfile
import subprocess

_LOCK = threading.Lock()


# ════════════════════════════════════════════════════════
# DOSSIER CACHE
# ════════════════════════════════════════════════════════

def _cache_dir() -> str:
    try:
        import config as _cfg
        base = _cfg.get("_cache_dir") or os.path.join(os.path.dirname(__file__), ".cache")
    except Exception:
        base = os.path.join(os.path.dirname(__file__), ".cache")
    d = os.path.join(base, "covers")
    os.makedirs(d, exist_ok=True)
    return d


def _cover_path(key: str, ext: str = ".webp") -> str:
    h = hashlib.md5(key.encode()).hexdigest()
    return os.path.join(_cache_dir(), h + ext)


def _meta_path(key: str) -> str:
    return _cover_path(key, ".meta")


def _load_meta(key: str) -> dict:
    try:
        with open(_meta_path(key)) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_meta(key: str, meta: dict):
    try:
        with open(_meta_path(key), "w") as f:
            json.dump(meta, f)
    except Exception:
        pass


# ════════════════════════════════════════════════════════
# EXTRACTION DE LA PREMIÈRE IMAGE
# ════════════════════════════════════════════════════════

def _extract_first_image_bytes(file_path: str) -> bytes | None:
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext in (".cbz", ".zip"):
            with zipfile.ZipFile(file_path, "r") as zf:
                imgs = sorted([
                    n for n in zf.namelist()
                    if os.path.splitext(n)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")
                    and not os.path.basename(n).startswith(".")
                ])
                if imgs:
                    return zf.read(imgs[0])

        elif ext == ".cbr":
            with tempfile.TemporaryDirectory() as tmp:
                for cmd in [["unrar", "x", "-y", file_path, tmp],
                             ["7z",   "x",       file_path, f"-o{tmp}", "-y"]]:
                    try:
                        r = subprocess.run(cmd, capture_output=True, timeout=30)
                        if r.returncode == 0:
                            break
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        continue
                imgs = sorted([
                    os.path.join(dp, fn)
                    for dp, _, fns in os.walk(tmp)
                    for fn in fns
                    if os.path.splitext(fn)[1].lower() in (".jpg",".jpeg",".png",".webp")
                ])
                if imgs:
                    with open(imgs[0], "rb") as f:
                        return f.read()

        elif ext == ".pdf":
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "page")
                r = subprocess.run(
                    ["pdftoppm", "-jpeg", "-r", "96", "-l", "1", file_path, out],
                    capture_output=True, timeout=30
                )
                if r.returncode == 0:
                    imgs = sorted(os.listdir(tmp))
                    if imgs:
                        with open(os.path.join(tmp, imgs[0]), "rb") as f:
                            return f.read()
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════
# CONVERSION EN WEBP (sans Pillow — via cwebp si dispo)
# ════════════════════════════════════════════════════════

def _has_tool(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def _to_webp(img_bytes: bytes, quality: int = 75, scale: float = 0.5) -> bytes | None:
    """
    Convertit des bytes image (JPEG/PNG) en WebP redimensionné.
    Utilise Pillow si disponible, sinon cwebp en ligne de commande.
    """
    # Essai Pillow (meilleur)
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        # Redimensionne à 50%
        new_w = max(1, int(img.width  * scale))
        new_h = max(1, int(img.height * scale))
        img   = img.resize((new_w, new_h), Image.LANCZOS)
        # Convertit en WebP
        out = io.BytesIO()
        img.convert("RGB").save(out, format="WEBP", quality=quality, method=4)
        return out.getvalue()
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback : cwebp en ligne de commande
    if _has_tool("cwebp"):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                src = os.path.join(tmp, "input.jpg")
                dst = os.path.join(tmp, "output.webp")
                with open(src, "wb") as f:
                    f.write(img_bytes)
                subprocess.run(
                    ["cwebp", "-q", str(quality), "-resize", "0", "0", src, "-o", dst],
                    capture_output=True, timeout=30
                )
                if os.path.exists(dst):
                    with open(dst, "rb") as f:
                        return f.read()
        except Exception:
            pass

    # Dernier recours : retourne les bytes originaux (JPEG) sans conversion
    return img_bytes


# ════════════════════════════════════════════════════════
# API PUBLIQUE
# ════════════════════════════════════════════════════════

def get_series_cover(series_id: str, first_file: str | None = None) -> bytes | None:
    """
    Retourne les bytes WebP de la cover série.
    Utilise le cache si valide (même mtime), sinon recrée.
    """
    if not first_file:
        try:
            import library_manager as _lm
            info = _lm.get_series_by_id(series_id)
            if info:
                first_file = info.get("first_file")
        except Exception:
            pass

    if not first_file or not os.path.isfile(first_file):
        return None

    key       = f"series_{series_id}"
    cache_file = _cover_path(key)
    meta       = _load_meta(key)

    try:
        cur_mtime = os.path.getmtime(first_file)
        if (os.path.exists(cache_file)
                and meta.get("source") == first_file
                and abs(meta.get("mtime", 0) - cur_mtime) < 1):
            with open(cache_file, "rb") as f:
                return f.read()
    except Exception:
        pass

    # (Re)crée le cache
    with _LOCK:
        img = _extract_first_image_bytes(first_file)
        if not img:
            return None
        webp = _to_webp(img)
        if not webp:
            return None
        try:
            with open(cache_file, "wb") as f:
                f.write(webp)
            _save_meta(key, {"source": first_file, "mtime": os.path.getmtime(first_file)})
        except Exception:
            pass
        return webp


def get_book_cover(series_id: str, filename: str) -> bytes | None:
    """Retourne les bytes WebP de la cover d'un tome."""
    try:
        import library_manager as _lm
        info = _lm.get_series_by_id(series_id)
        if not info:
            return None
        file_path = os.path.join(info["path"], filename)
    except Exception:
        return None

    if not os.path.isfile(file_path):
        return None

    key        = f"book_{series_id}_{filename}"
    cache_file = _cover_path(key)
    meta       = _load_meta(key)

    try:
        cur_mtime = os.path.getmtime(file_path)
        if (os.path.exists(cache_file)
                and meta.get("source") == file_path
                and abs(meta.get("mtime", 0) - cur_mtime) < 1):
            with open(cache_file, "rb") as f:
                return f.read()
    except Exception:
        pass

    with _LOCK:
        img = _extract_first_image_bytes(file_path)
        if not img:
            return None
        webp = _to_webp(img)
        if not webp:
            return None
        try:
            with open(cache_file, "wb") as f:
                f.write(webp)
            _save_meta(key, {"source": file_path, "mtime": os.path.getmtime(file_path)})
        except Exception:
            pass
        return webp


def clear_cache(series_id: str = None):
    """Vide le cache covers (tout ou pour une série)."""
    d       = _cache_dir()
    prefix  = hashlib.md5(f"series_{series_id}".encode()).hexdigest()[:8] if series_id else None
    removed = 0
    try:
        for fn in os.listdir(d):
            if prefix is None or fn.startswith(prefix):
                try:
                    os.remove(os.path.join(d, fn))
                    removed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return removed


def stats() -> dict:
    """Statistiques du cache."""
    d     = _cache_dir()
    files = [f for f in os.listdir(d) if f.endswith(".webp")] if os.path.isdir(d) else []
    size  = sum(
        os.path.getsize(os.path.join(d, f))
        for f in files
        if os.path.isfile(os.path.join(d, f))
    )
    return {
        "count":   len(files),
        "size_kb": round(size / 1024, 1),
        "path":    d,
        "format":  "WebP (50% résolution, qualité 75)",
    }


# ════════════════════════════════════════════════════════
# MAIN (usage standalone)
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser(description="MangaArr — Cache covers WebP")
    parser.add_argument("--clear",   action="store_true", help="Vider tout le cache")
    parser.add_argument("--stats",   action="store_true", help="Afficher les statistiques")
    parser.add_argument("--rebuild", action="store_true", help="Reconstruire tout le cache")
    args = parser.parse_args()

    if args.clear:
        n = clear_cache()
        print(f"Cache vidé : {n} fichier(s) supprimé(s)")

    elif args.stats:
        s = stats()
        print(f"Cache covers : {s['count']} image(s) · {s['size_kb']} Ko")
        print(f"Format : {s['format']}")
        print(f"Dossier : {s['path']}")

    elif args.rebuild:
        try:
            import library_manager as _lm
            libs = _lm.get_libraries()
            total = 0
            for lib in libs:
                series_list = _lm.scan_library(lib["id"])
                for s in series_list:
                    print(f"  {s['name']}…", end=" ", flush=True)
                    # Cover série
                    r = get_series_cover(s["id"], s.get("first_file"))
                    # Covers tomes
                    for t in s.get("tomes", []):
                        get_book_cover(s["id"], t["filename"])
                    total += 1
                    print(f"✓")
            print(f"\nTerminé : {total} série(s) traitée(s)")
            s = stats()
            print(f"Cache : {s['count']} image(s) · {s['size_kb']} Ko")
        except Exception as e:
            print(f"Erreur : {e}")
            sys.exit(1)
    else:
        parser.print_help()
