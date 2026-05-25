#!/usr/bin/env python3
"""
edit_emulecollection.py — Utilitaire de gestion des fichiers .emulecollection

Fonctions :
  list        — liste tous les fichiers .emulecollection et leur nombre de liens
  show <file> — affiche tous les liens ed2k d'un fichier
  remove <file> <ed2k://...> [...]  — supprime un ou plusieurs liens d'un fichier
  purge       — supprime les fichiers vides (0 liens)
  count <file> — affiche le nombre de liens ed2k dans un fichier
  rebuild     — recompte et affiche les stats de tous les fichiers

Usage :
  python edit_emulecollection.py list
  python edit_emulecollection.py show 20240101_120000_ADD.emulecollection
  python edit_emulecollection.py remove 20240101_120000_ADD.emulecollection "ed2k://|file|..."
  python edit_emulecollection.py purge
  python edit_emulecollection.py rebuild
"""
import os
import sys
import re

# Répertoire contenant les .emulecollection
EMULE_DIR = os.environ.get(
    "MANGAARR_EMULE",
    os.path.join(os.path.dirname(__file__), "emulecollections")
)


def _all_files():
    """Retourne la liste des fichiers .emulecollection triés par date (plus récent en premier)."""
    if not os.path.isdir(EMULE_DIR):
        return []
    return sorted(
        [f for f in os.listdir(EMULE_DIR) if f.endswith(".emulecollection")],
        reverse=True,
    )


def _count_links(filepath: str) -> int:
    """Compte le nombre de liens ed2k valides dans un fichier."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip().startswith("ed2k://"))
    except Exception:
        return 0


def _read_links(filepath: str) -> list:
    """Retourne tous les liens ed2k d'un fichier."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip().startswith("ed2k://")]
    except Exception:
        return []


def _resolve(name_or_path: str) -> str:
    """Résout un nom de fichier ou chemin relatif vers le chemin absolu."""
    if os.path.isabs(name_or_path):
        return name_or_path
    candidate = os.path.join(EMULE_DIR, name_or_path)
    if os.path.exists(candidate):
        return candidate
    return name_or_path


def cmd_list():
    """Liste tous les fichiers .emulecollection avec leur nombre de liens."""
    files = _all_files()
    if not files:
        print(f"Aucun fichier .emulecollection dans {EMULE_DIR}")
        return
    print(f"\n{'Fichier':<55} {'Liens':>6}")
    print("─" * 63)
    for fn in files:
        fp    = os.path.join(EMULE_DIR, fn)
        count = _count_links(fp)
        size  = os.path.getsize(fp)
        print(f"{fn:<55} {count:>6}  ({size} octets)")
    print()


def cmd_show(name: str):
    """Affiche tous les liens ed2k d'un fichier."""
    fp    = _resolve(name)
    links = _read_links(fp)
    if not links:
        print(f"Aucun lien ed2k dans {fp}")
        return
    print(f"\n{len(links)} lien(s) dans {os.path.basename(fp)} :\n")
    for i, lnk in enumerate(links, 1):
        print(f"  {i:3}. {lnk}")
    print()


def cmd_remove(name: str, targets: list):
    """Supprime un ou plusieurs liens ed2k d'un fichier."""
    fp = _resolve(name)
    if not os.path.exists(fp):
        print(f"Fichier introuvable : {fp}", file=sys.stderr)
        sys.exit(1)

    to_remove = set(t.strip() for t in targets)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Erreur lecture : {e}", file=sys.stderr)
        sys.exit(1)

    kept    = [l for l in lines if l.strip() not in to_remove]
    removed = len(lines) - len(kept)

    if removed == 0:
        print("Aucun lien correspondant trouvé — rien modifié.")
        return

    with open(fp, "w", encoding="utf-8") as f:
        f.writelines(kept)

    remaining = _count_links(fp)
    print(f"✓ {removed} lien(s) supprimé(s). Reste : {remaining} lien(s) dans {os.path.basename(fp)}")


def cmd_purge():
    """Supprime les fichiers .emulecollection vides (0 liens)."""
    files   = _all_files()
    deleted = 0
    for fn in files:
        fp = os.path.join(EMULE_DIR, fn)
        if _count_links(fp) == 0:
            os.remove(fp)
            print(f"  Supprimé (vide) : {fn}")
            deleted += 1
    if deleted == 0:
        print("Aucun fichier vide à supprimer.")
    else:
        print(f"\n✓ {deleted} fichier(s) supprimé(s).")


def cmd_count(name: str):
    """Affiche le nombre de liens ed2k dans un fichier."""
    fp    = _resolve(name)
    count = _count_links(fp)
    print(f"{count} lien(s) dans {os.path.basename(fp)}")


def cmd_rebuild():
    """Recompte et affiche les statistiques de tous les fichiers."""
    files = _all_files()
    if not files:
        print(f"Aucun fichier .emulecollection dans {EMULE_DIR}")
        return
    total = 0
    print(f"\nReconstruction des statistiques depuis {EMULE_DIR}\n")
    for fn in files:
        fp    = os.path.join(EMULE_DIR, fn)
        count = _count_links(fp)
        total += count
        tag   = "(vide)" if count == 0 else ""
        print(f"  {fn:<55} {count:>4} liens {tag}")
    print(f"\nTotal : {total} liens répartis dans {len(files)} fichier(s)\n")


# ─────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────

COMMANDS = {
    "list":    (cmd_list,    0, ""),
    "show":    (cmd_show,    1, "<fichier>"),
    "remove":  (cmd_remove,  2, "<fichier> <ed2k://...> [...]"),
    "purge":   (cmd_purge,   0, ""),
    "count":   (cmd_count,   1, "<fichier>"),
    "rebuild": (cmd_rebuild, 0, ""),
}


def usage():
    print(__doc__)
    print("\nCommandes disponibles :")
    for cmd, (_, min_args, sig) in COMMANDS.items():
        print(f"  {cmd:<10} {sig}")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        usage()
        sys.exit(0)

    cmd = args[0].lower()
    if cmd not in COMMANDS:
        print(f"Commande inconnue : {cmd}", file=sys.stderr)
        usage()
        sys.exit(1)

    fn, min_args, sig = COMMANDS[cmd]
    rest = args[1:]

    if len(rest) < min_args:
        print(f"Usage : python edit_emulecollection.py {cmd} {sig}", file=sys.stderr)
        sys.exit(1)

    if cmd == "remove":
        fn(rest[0], rest[1:])
    elif min_args == 1:
        fn(rest[0])
    else:
        fn()
