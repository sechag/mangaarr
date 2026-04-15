#!/usr/bin/env python3
"""
watcher.py — Surveillance du dossier Incoming d'eMule/aMule
Script indépendant qui tourne en arrière-plan et détecte quand
un fichier de la queue MangaArr arrive dans le dossier Incoming.

Usage :
    python watcher.py [--incoming /chemin/Incoming] [--interval 30]

Configure le chemin Incoming dans MangaArr Settings > Incoming
ou passez-le en argument.
"""
import os, sys, time, json, re, argparse
from pathlib import Path
from datetime import datetime

# Ajout du répertoire mangaarr au path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def get_config_value(key, default=None):
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f).get(key, default)
    except Exception:
        return default


def load_queue():
    queue_path = os.path.join(SCRIPT_DIR, ".cache", "queue.json")
    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_queue(items):
    queue_path = os.path.join(SCRIPT_DIR, ".cache", "queue.json")
    with open(queue_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def normalize_filename(fn):
    """Normalise un nom de fichier pour comparaison via cache.normalize_filename_for_series."""
    try:
        sys.path.insert(0, SCRIPT_DIR)
        from cache import normalize_filename_for_series
        return normalize_filename_for_series(fn)
    except Exception:
        # Fallback basique si cache non disponible
        fn = os.path.splitext(fn)[0].lower()
        return re.sub(r"[^a-z0-9]", " ", fn).strip()


def check_incoming(incoming_dir: str, interval: int = 30):
    """
    Boucle principale : surveille le dossier Incoming toutes les `interval` secondes.
    Compare les fichiers présents avec la queue MangaArr.
    Met à jour le statut en "done" si un fichier correspond.
    """
    print(f"[watcher] Surveillance de : {incoming_dir}")
    print(f"[watcher] Intervalle      : {interval}s")
    print(f"[watcher] Ctrl+C pour arrêter\n")

    while True:
        try:
            # Fichiers présents dans Incoming
            incoming_files = {}
            if os.path.isdir(incoming_dir):
                for fn in os.listdir(incoming_dir):
                    if fn.lower().endswith((".cbz", ".cbr", ".pdf")):
                        incoming_files[normalize_filename(fn)] = fn

            if not incoming_files:
                time.sleep(interval)
                continue

            # Compare avec la queue
            queue = load_queue()
            changed = False

            for item in queue:
                if item.get("status") == "done":
                    continue

                item_norm = normalize_filename(item.get("filename", ""))
                if not item_norm:
                    continue

                # Correspondance exacte ou partielle (hash si disponible)
                matched_fn = None
                filehash = item.get("filehash", "").lower()

                # 1. Correspondance exacte
                if item_norm in incoming_files:
                    matched_fn = incoming_files[item_norm]

                # 2. Similarité floue si pas de match exact
                if not matched_fn:
                    try:
                        from cache import _similarity
                        best_score = 0.0
                        for norm_key, real_fn in incoming_files.items():
                            score = _similarity(item_norm, norm_key)
                            if score > best_score and score >= 0.88:
                                best_score = score
                                matched_fn = real_fn
                    except Exception:
                        # Fallback sous-chaîne
                        for norm_key, real_fn in incoming_files.items():
                            if item_norm[:15] in norm_key:
                                matched_fn = real_fn
                                break

                if matched_fn:
                    item["status"]  = "done"
                    item["done_at"] = datetime.now().isoformat(timespec="seconds")
                    item["local_path"] = os.path.join(incoming_dir, matched_fn)
                    changed = True
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"[{ts}] ✓ Terminé : {matched_fn}")
                    print(f"        Série : {item.get('series_name', '?')} T{item.get('tome_number','?')}")

                    # Déclenche l'organiseur si configuré
                    _trigger_organizer(item)

            if changed:
                save_queue(queue)

        except KeyboardInterrupt:
            print("\n[watcher] Arrêt.")
            break
        except Exception as e:
            print(f"[watcher] Erreur : {e}")

        time.sleep(interval)


def _trigger_organizer(item: dict):
    """Appelle file_organizer si l'auto-organisation est activée."""
    try:
        auto_org = get_config_value("media_management", {}).get("auto_organize", False)
        if not auto_org:
            return
        import file_organizer
        file_organizer.organize_file(item)
    except Exception as e:
        print(f"[watcher] Erreur organiseur : {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MangaArr — Watcher dossier Incoming eMule")
    parser.add_argument(
        "--incoming",
        default=None,
        help="Chemin du dossier Incoming eMule (défaut : config MangaArr)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Intervalle de vérification en secondes (défaut: 30)"
    )
    args = parser.parse_args()

    incoming = args.incoming or get_config_value("emule_incoming_dir", "")
    if not incoming:
        print("ERREUR : Chemin Incoming non configuré.")
        print("Définissez-le dans Settings > Incoming ou avec --incoming /chemin")
        sys.exit(1)

    if not os.path.isdir(incoming):
        print(f"ERREUR : Le dossier '{incoming}' n'existe pas.")
        sys.exit(1)

    check_incoming(incoming, args.interval)
