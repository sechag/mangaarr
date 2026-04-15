#!/usr/bin/env python3
"""
rename_library.py — Renomme tous les fichiers manga de la librairie MangaArr
                    selon le format configuré dans Settings > Media Management.

Usage CLI (optionnel — tout est aussi accessible depuis l'interface web) :
  python rename_library.py [--dry-run] [--rollback [FICHIER]] [--format 1|2|3] [--lib ID]
"""

import os
import sys
import json
import argparse
from datetime import datetime

# ── path pour imports MangaArr ───────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import config as _config
import renamer as _r
import profiles
import library_manager as _lm

# ── Répertoire persistant pour l'historique ──────────────
# Stocké à côté du fichier de config (volume Docker persistant)
HISTORY_DIR = os.path.join(
    os.path.dirname(_config.CONFIG_FILE),
    "rename_history"
)

MANGA_EXTS = {".cbz", ".cbr", ".pdf", ".zip"}


# ══════════════════════════════════════════════════════════
# HISTORIQUE
# ══════════════════════════════════════════════════════════

def _ensure_history_dir():
    os.makedirs(HISTORY_DIR, exist_ok=True)


def list_history() -> list[dict]:
    """
    Retourne la liste des fichiers d'historique, du plus récent au plus ancien.
    Chaque entrée : {file, timestamp, renamed, dry_run, format_id, rolled_back}
    """
    _ensure_history_dir()
    entries = []
    for fname in sorted(os.listdir(HISTORY_DIR), reverse=True):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(HISTORY_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries.append({
                "file":        fname,
                "timestamp":   data.get("timestamp", ""),
                "renamed":     len(data.get("renames", {})),
                "dry_run":     data.get("dry_run", False),
                "format_id":   data.get("format_id", 1),
                "rolled_back": data.get("rolled_back", False),
            })
        except Exception:
            pass
    return entries


def _save_history(ts: str, dry_run: bool, format_id: int, renames: dict) -> str:
    """Sauvegarde un historique et retourne son nom de fichier."""
    _ensure_history_dir()
    fname = f"rename_{ts}.json"
    fpath = os.path.join(HISTORY_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "dry_run":   dry_run,
            "format_id": format_id,
            "renames":   renames,
        }, f, ensure_ascii=False, indent=2)
    return fname


def _mark_rolled_back(fname: str):
    fpath = os.path.join(HISTORY_DIR, fname)
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["rolled_back"] = True
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
# RENOMMAGE
# ══════════════════════════════════════════════════════════

def run_rename(dry_run: bool = False, format_id: int = None, lib_id_filter: str = None) -> dict:
    """
    Renomme tous les fichiers manga dans toutes les librairies configurées.

    Retourne :
      {ok, renamed, unchanged, skipped, errors, history_file, details}
    """
    fmt = format_id if format_id is not None else _r.get_rename_format()
    libraries = _lm.get_libraries()

    if not libraries:
        return {"ok": False, "message": "Aucune librairie configurée"}

    if lib_id_filter:
        libraries = [l for l in libraries if l.get("id") == lib_id_filter]
        if not libraries:
            return {"ok": False, "message": f"Librairie '{lib_id_filter}' introuvable"}

    renames:  dict[str, str] = {}   # ancien_path → nouveau_path
    details:  list[dict]     = []
    n_unchanged = 0
    n_skipped   = 0
    n_errors    = 0

    for lib in libraries:
        lib_path = lib.get("path", "")
        lib_name = lib.get("name", lib.get("id", "?"))
        if not os.path.isdir(lib_path):
            details.append({"status": "error", "msg": f"Librairie '{lib_name}' : chemin introuvable"})
            continue

        try:
            series_folders = sorted([
                d for d in os.listdir(lib_path)
                if os.path.isdir(os.path.join(lib_path, d))
            ])
        except PermissionError as e:
            details.append({"status": "error", "msg": str(e)})
            continue

        for series_folder in series_folders:
            series_path = os.path.join(lib_path, series_folder)
            try:
                files = sorted([
                    f for f in os.listdir(series_path)
                    if os.path.isfile(os.path.join(series_path, f))
                    and os.path.splitext(f)[1].lower() in MANGA_EXTS
                ])
            except PermissionError:
                continue

            if not files:
                continue

            series_raw   = _r.extract_leading_article(series_folder)
            series_clean = _r.clean_title(series_raw)
            series_arg   = series_clean if fmt == 3 else _r.clean_title_readable(series_raw)

            for fname in files:
                old_path = os.path.join(series_path, fname)

                tome = _r.detect_tome(fname)
                if not tome:
                    n_skipped += 1
                    continue

                tag      = profiles.detect_tag(fname)
                new_name = _r.build_filename(series_arg, tome, tag, format_id=fmt)
                new_path = os.path.join(series_path, new_name)

                if old_path == new_path:
                    n_unchanged += 1
                    continue

                # Conflit : un autre fichier occupe déjà ce tome
                existing = _r._find_existing_tome(series_path, series_clean, tome)
                if existing and existing != old_path:
                    existing_tag = profiles.detect_tag(os.path.basename(existing))
                    if not profiles.is_better_than(tag, existing_tag):
                        n_skipped += 1
                        details.append({
                            "status":  "conflict",
                            "series":  series_folder,
                            "old":     fname,
                            "blocker": os.path.basename(existing),
                        })
                        continue

                try:
                    if not dry_run:
                        os.rename(old_path, new_path)
                    renames[old_path] = new_path
                    details.append({
                        "status": "renamed",
                        "series": series_folder,
                        "old":    fname,
                        "new":    new_name,
                    })
                except OSError as e:
                    n_errors += 1
                    details.append({
                        "status": "error",
                        "series": series_folder,
                        "old":    fname,
                        "msg":    str(e),
                    })

    history_file = None
    if renames:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        history_file = _save_history(ts, dry_run, fmt, renames)

    return {
        "ok":           True,
        "dry_run":      dry_run,
        "format_id":    fmt,
        "renamed":      len(renames),
        "unchanged":    n_unchanged,
        "skipped":      n_skipped,
        "errors":       n_errors,
        "history_file": history_file,
        "details":      details,
    }


# ══════════════════════════════════════════════════════════
# ROLLBACK
# ══════════════════════════════════════════════════════════

def run_rollback(history_fname: str) -> dict:
    """
    Annule un renommage à partir d'un fichier d'historique.
    Retourne {ok, restored, skipped, errors, message}
    """
    fpath = os.path.join(HISTORY_DIR, history_fname)
    if not os.path.isfile(fpath):
        return {"ok": False, "message": f"Fichier d'historique introuvable : {history_fname}"}

    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("dry_run"):
        return {"ok": False, "message": "Cet historique provient d'un dry-run (aucun fichier n'avait été modifié)"}

    if data.get("rolled_back"):
        return {"ok": False, "message": "Ce renommage a déjà été annulé"}

    renames = data.get("renames", {})
    if not renames:
        return {"ok": False, "message": "Historique vide"}

    n_restored = 0
    n_skipped  = 0
    n_errors   = 0
    details    = []

    for old_path, new_path in renames.items():
        if not os.path.isfile(new_path):
            n_skipped += 1
            details.append({"status": "absent",   "file": os.path.basename(new_path)})
            continue
        if os.path.isfile(old_path):
            n_skipped += 1
            details.append({"status": "occupied", "file": os.path.basename(old_path)})
            continue
        try:
            os.rename(new_path, old_path)
            n_restored += 1
            details.append({
                "status": "restored",
                "old": os.path.basename(new_path),
                "new": os.path.basename(old_path),
            })
        except OSError as e:
            n_errors += 1
            details.append({"status": "error", "file": os.path.basename(new_path), "msg": str(e)})

    if n_errors == 0:
        _mark_rolled_back(history_fname)

    return {
        "ok":       True,
        "restored": n_restored,
        "skipped":  n_skipped,
        "errors":   n_errors,
        "details":  details,
        "message":  f"{n_restored} fichier(s) restauré(s)",
    }


# ══════════════════════════════════════════════════════════
# CLI (optionnel)
# ══════════════════════════════════════════════════════════

def _print_sep(char="─", w=72): print(char * w)

def _cli_rename(args):
    fmt_names = {1: "Format 1 — Titre Tome XX (TAG)", 2: "Format 2 — Tome XX (TAG)", 3: "Format 3 — héritage"}
    fmt = args.format or _r.get_rename_format()
    mode = "[DRY-RUN] " if args.dry_run else ""
    print(); _print_sep("═")
    print(f"  {mode}Renommage bibliothèque MangaArr — {fmt_names.get(fmt, f'Format {fmt}')}")
    _print_sep("═")

    res = run_rename(dry_run=args.dry_run, format_id=args.format, lib_id_filter=args.lib)
    if not res["ok"]:
        print(f"  ✗ {res['message']}"); sys.exit(1)

    for d in res["details"]:
        s = d["status"]
        if s == "renamed":
            print(f"  ✓  {d['series']}/{d['old']}\n      → {d['new']}")
        elif s == "conflict":
            print(f"  ⊘  CONFLIT {d['series']}/{d['old']} (bloqué par {d['blocker']})")
        elif s == "error":
            print(f"  ✗  ERREUR {d.get('old','?')} : {d.get('msg','')}")

    print(); _print_sep("═")
    print(f"  {res['renamed']} renommé(s) | {res['unchanged']} inchangé(s) | {res['skipped']} ignoré(s) | {res['errors']} erreur(s)")
    _print_sep("═")
    if res["history_file"]:
        print(f"  Historique → {os.path.join(HISTORY_DIR, res['history_file'])}")
        if args.dry_run:
            print("  (dry-run : aucun fichier modifié)")
        else:
            print(f"  Pour annuler : python rename_library.py --rollback")
    print()


def _cli_rollback(args):
    hist_file = args.rollback if isinstance(args.rollback, str) else None
    if not hist_file:
        entries = list_history()
        entries = [e for e in entries if not e["dry_run"] and not e["rolled_back"]]
        if not entries:
            print("Aucun historique de renommage disponible.")
            sys.exit(1)
        hist_file = entries[0]["file"]
        print(f"  Rollback du dernier renommage : {hist_file}")

    print(); _print_sep("═")
    print(f"  Rollback — {hist_file}")
    _print_sep("═")

    res = run_rollback(hist_file)
    if not res["ok"]:
        print(f"  ✗ {res['message']}"); sys.exit(1)

    for d in res["details"]:
        s = d["status"]
        if s == "restored":
            print(f"  ↩  {d['old']}\n      → {d['new']}")
        elif s == "absent":
            print(f"  ⊘  Absent : {d['file']}")
        elif s == "occupied":
            print(f"  ⊘  Destination occupée : {d['file']}")
        elif s == "error":
            print(f"  ✗  {d['file']} : {d.get('msg','')}")

    print(); _print_sep("═")
    print(f"  {res['restored']} restauré(s) | {res['skipped']} ignoré(s) | {res['errors']} erreur(s)")
    _print_sep("═"); print()


def main():
    parser = argparse.ArgumentParser(description="Renommage bibliothèque MangaArr")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--rollback", nargs="?", const=True, metavar="FICHIER")
    parser.add_argument("--format",   type=int, choices=[1, 2, 3], metavar="N")
    parser.add_argument("--lib",      metavar="ID")
    args = parser.parse_args()

    if args.rollback:
        _cli_rollback(args)
    else:
        _cli_rename(args)


if __name__ == "__main__":
    main()
