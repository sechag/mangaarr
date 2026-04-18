"""
app.py — Serveur Flask MangaArr

Routes API organisées par section :
  /api/libraries/*        — Gestion des librairies locales
  /api/collection/*       — Séries, tomes, metadata
  /api/queue/*            — Queue de téléchargement, scan Incoming
  /api/metadata/*         — Synchronisation MangaDB
  /api/profiles/*         — Tags, filtres qualité
  /api/settings/*         — Configuration générale
  /api/ebdz/*             — Scraper ebdz.net
  /api/indexers/torznab/* — Indexers Torznab (Prowlarr/Jackett)
  /api/settings/download-clients/* — Clients qBittorrent
  /api/torrent/*          — Recherche et téléchargement torrents
  /api/browse             — Navigateur de dossiers (Docker)
  /api/debug/*            — Diagnostic chemins container
"""
import os, uuid, threading, re, unicodedata, json, time
import config, profiles, renamer, media_manager
import ebdz_scraper, mangadb_client, cache as cache_mod, queue_manager
import library_manager as lib_mgr
import torznab_client, qbittorrent_client, amule_client
import discover_manager
import ebdz_browser

# Dossier de stockage des fichiers .torrent téléchargés
TORRENT_FILES_DIR = os.environ.get("MANGAARR_TORRENT_FILES", "/torrent_files")
os.makedirs(TORRENT_FILES_DIR, exist_ok=True)

from flask import Flask, jsonify, request, render_template, Response

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "mangaarr-secret-2024"

# ── tâche arrière-plan ──────────────────────────────────
_task = {"running": False, "label": "", "results": []}
_lock = threading.Lock()
def _set_task(**kw):
    with _lock: _task.update(kw)

# ── enrichissement asynchrone local ──────────────────────
_enrich_events: dict = {}
_ev_lock = threading.Lock()


# ════════════════════════════════════════════════════════
# PAGES HTML
# ════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/series/<path:series_slug>")
def series_page(series_slug):
    return render_template("series.html", series_slug=series_slug)


# ════════════════════════════════════════════════════════
# PROXY IMAGES KOMGA
# ════════════════════════════════════════════════════════

@app.route("/api/local/series/<series_id>/thumbnail")
def api_local_series_thumb(series_id):
    """Cover série en WebP (cache disque, 50% résolution)."""
    import image_cache as _ic
    data = _ic.get_series_cover(series_id)
    if data:
        mime = "image/webp" if data[:4] == b"RIFF" or data[:4] == b"WEBP" else "image/jpeg"
        return Response(data, mimetype=mime)
    return Response(b"", mimetype="image/webp", status=204)

@app.route("/api/local/books/<series_id>/<path:filename>/thumbnail")
def api_local_book_thumb(series_id, filename):
    """Cover tome en WebP (cache disque, 50% résolution)."""
    import image_cache as _ic
    data = _ic.get_book_cover(series_id, filename)
    if data:
        mime = "image/webp" if data[:4] in (b"RIFF", b"WEBP") else "image/jpeg"
        return Response(data, mimetype=mime)
    return Response(b"", mimetype="image/webp", status=204)


# ════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = config.load()
    return jsonify({k: v for k, v in cfg.items() if k != "logs"})

@app.route("/api/config", methods=["POST"])
def api_set_config():
    d = request.json; cfg = config.load()
    for k in ("mybbuser", "download_dir", "media_management", "_active_komga_unused"):
        if k in d: cfg[k] = d[k]
    # Assure que force_organize_enabled est dans media_management si passé directement
    if "force_organize_enabled" in d:
        cfg.setdefault("media_management", {})["force_organize_enabled"] = bool(d["force_organize_enabled"])
    config.save(cfg)
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
# INDEXERS
# ════════════════════════════════════════════════════════

@app.route("/api/indexers/test", methods=["POST"])
def api_test_indexer():
    mybbuser = request.json.get("mybbuser", "")
    if not mybbuser: return jsonify({"ok": False, "message": "Cookie vide"})
    session = ebdz_scraper.make_session(mybbuser)
    ok = ebdz_scraper.check_login(session)
    if ok: config.set_value("mybbuser", mybbuser)
    return jsonify({"ok": ok, "message": "Connecté à ebdz.net" if ok else "Cookie invalide"})


# ════════════════════════════════════════════════════════
# INDEXERS — TORZNAB
# ════════════════════════════════════════════════════════

@app.route("/api/indexers/torznab", methods=["GET"])
def api_list_torznab():
    return jsonify({"ok": True, "indexers": config.get("torznab_indexers", [])})

@app.route("/api/indexers/torznab", methods=["POST"])
def api_add_torznab():
    d    = request.json or {}
    name = d.get("name", "").strip()
    url  = d.get("url",  "").strip().rstrip("/")
    apikey = d.get("apikey", "").strip()
    if not name or not url:
        return jsonify({"ok": False, "message": "Nom et URL requis"})
    cfg = config.load()
    idx = {
        "id":      str(uuid.uuid4())[:8],
        "name":    name,
        "url":     url,
        "apikey":  apikey,
        "enabled": True,
    }
    cfg.setdefault("torznab_indexers", []).append(idx)
    config.save(cfg)
    return jsonify({"ok": True, "indexer": idx})

@app.route("/api/indexers/torznab/<idx_id>", methods=["DELETE"])
def api_delete_torznab(idx_id):
    cfg = config.load()
    cfg["torznab_indexers"] = [i for i in cfg.get("torznab_indexers", []) if i.get("id") != idx_id]
    config.save(cfg)
    return jsonify({"ok": True})

@app.route("/api/indexers/torznab/<idx_id>", methods=["PATCH"])
def api_update_torznab(idx_id):
    d   = request.json or {}
    cfg = config.load()
    for idx in cfg.get("torznab_indexers", []):
        if idx.get("id") == idx_id:
            for k in ("name", "url", "apikey", "enabled"):
                if k in d:
                    idx[k] = d[k]
    config.save(cfg)
    return jsonify({"ok": True})

@app.route("/api/indexers/torznab/<idx_id>/test", methods=["POST"])
def api_test_torznab(idx_id):
    indexers = config.get("torznab_indexers", [])
    idx = next((i for i in indexers if i.get("id") == idx_id), None)
    if not idx:
        return jsonify({"ok": False, "message": "Indexer introuvable"})
    result = torznab_client.test_indexer(idx)
    return jsonify(result)


# ════════════════════════════════════════════════════════
# SETTINGS — CLIENTS DE TÉLÉCHARGEMENT (qBittorrent)
# ════════════════════════════════════════════════════════

@app.route("/api/settings/download-clients", methods=["GET"])
def api_list_download_clients():
    clients = config.get("download_clients", [])
    # Ne pas exposer les mots de passe en clair dans le listing
    safe = []
    for c in clients:
        sc = dict(c)
        if sc.get("password"):
            sc["password"] = "••••••••"
        safe.append(sc)
    return jsonify({"ok": True, "clients": safe})

@app.route("/api/settings/download-clients", methods=["POST"])
def api_add_download_client():
    d         = request.json or {}
    name      = d.get("name", "").strip()
    host      = d.get("host", "").strip()
    client_type = d.get("type", "qbittorrent").strip()
    if not name or not host:
        return jsonify({"ok": False, "message": "Nom et host requis"})
    cfg = config.load()
    if client_type == "amule":
        client = {
            "id":       str(uuid.uuid4())[:8],
            "name":     name,
            "type":     "amule",
            "host":     host,
            "ec_port":  int(d.get("ec_port", 4712)),
            "password": d.get("password", "").strip(),
            "enabled":  True,
        }
    else:
        client = {
            "id":         str(uuid.uuid4())[:8],
            "name":       name,
            "type":       "qbittorrent",
            "host":       host,
            "port":       int(d.get("port", 8080)),
            "username":   d.get("username", "").strip(),
            "password":   d.get("password", "").strip(),
            "category":   d.get("category", "").strip(),
            "save_path":  d.get("save_path", "").strip(),
            "watch_path": d.get("watch_path", "").strip(),
            "enabled":    True,
        }
    cfg.setdefault("download_clients", []).append(client)
    config.save(cfg)
    safe = dict(client)
    if safe.get("password"): safe["password"] = "••••••••"
    return jsonify({"ok": True, "client": safe})

@app.route("/api/settings/download-clients/<client_id>", methods=["DELETE"])
def api_delete_download_client(client_id):
    cfg = config.load()
    cfg["download_clients"] = [c for c in cfg.get("download_clients", []) if c.get("id") != client_id]
    config.save(cfg)
    return jsonify({"ok": True})

@app.route("/api/settings/download-clients/<client_id>", methods=["PATCH"])
def api_update_download_client(client_id):
    d   = request.json or {}
    cfg = config.load()
    for c in cfg.get("download_clients", []):
        if c.get("id") == client_id:
            for k in ("name", "host", "port", "username", "category", "save_path", "watch_path", "enabled"):
                if k in d:
                    c[k] = d[k]
            # Mot de passe uniquement si non masqué
            if "password" in d and d["password"] != "••••••••":
                c["password"] = d["password"]
    config.save(cfg)
    return jsonify({"ok": True})

@app.route("/api/settings/download-clients/<client_id>/test", methods=["POST"])
def api_test_download_client(client_id):
    clients = config.get("download_clients", [])
    client  = next((c for c in clients if c.get("id") == client_id), None)
    if not client:
        return jsonify({"ok": False, "message": "Client introuvable"})
    if client.get("type") == "amule":
        result = amule_client.test_connection(client)
    else:
        result = qbittorrent_client.test_connection(client)
    return jsonify(result)

@app.route("/api/settings/download-clients/info", methods=["GET"])
def api_download_clients_info():
    """Retourne les infos d'environnement pour le Download Client (chemin container, etc.)."""
    return jsonify({
        "qbt_watch_dir": os.environ.get("MANGAARR_QBT_WATCH", ""),
    })


@app.route("/api/ebdz-browser/series-tomes")
def api_ebdz_series_tomes():
    """
    Cherche une série par nom dans toutes les librairies et retourne ses tomes.
    GET /api/ebdz-browser/series-tomes?name=Dragon+Ball
    Retourne {ok, owned: [1,2,3], missing: [4,5], total: 5, slug: "name--id"}
    """
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"ok": False})
    name_lower = name.lower()
    for lib in lib_mgr.get_libraries():
        try:
            series_list = lib_mgr.scan_library(lib["id"])
        except Exception:
            continue
        for s in series_list:
            if s["name"].lower() == name_lower:
                owned = sorted([
                    t["numero"] for t in s.get("tomes", [])
                    if t.get("numero") is not None
                ])
                meta  = cache_mod.get_series_meta(lib["id"], s["id"]) or {}
                total = 0
                try:
                    total = int(meta.get("tomes_vf") or 0)
                except Exception:
                    pass
                missing = [n for n in range(1, total + 1) if n not in owned] if total else []
                return jsonify({
                    "ok":      True,
                    "name":    s["name"],
                    "owned":   owned,
                    "missing": missing,
                    "total":   total,
                    "slug":    f"{s['name']}--{s['id']}",
                })
    return jsonify({"ok": False, "message": "Série non trouvée dans la collection"})


# ════════════════════════════════════════════════════════
# TORRENT — RECHERCHE
# ════════════════════════════════════════════════════════

@app.route("/api/collection/series/<path:series_slug>/torrent-search")
def api_torrent_search(series_slug):
    """Recherche Torznab pour une série. Retourne les releases classées par type."""
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable", "releases": []})

    q        = request.args.get("q", series_info["name"]).strip()
    indexers = [i for i in config.get("torznab_indexers", []) if i.get("enabled", True)]

    if not indexers:
        return jsonify({"ok": False, "message": "Aucun indexer Torznab configuré (Indexers > Torznab)", "releases": []})

    releases = torznab_client.search_all(indexers, q, categories=[7000])

    # Tomes possédés
    tomes_owned = {t["numero"] for t in series_info.get("tomes", []) if t.get("numero")}

    # Enrichit chaque release avec les infos owned/manquant
    enriched = []
    for r in releases:
        vol_type  = r.get("vol_type", "unknown")
        tomes     = r.get("tomes", [])
        missing   = []
        if vol_type == "single" and tomes:
            n = tomes[0]
            missing = [] if n in tomes_owned else [n]
        elif vol_type == "pack" and tomes:
            missing = [n for n in tomes if n not in tomes_owned]
        elif vol_type == "integrale":
            missing = []  # Toujours proposé

        enriched.append({**r, "missing_tomes": missing})

    # Tri : intégrale en tête, puis packs, puis singles — par seeders décroissant
    def _sort_key(r):
        t = r.get("vol_type", "unknown")
        order = {"integrale": 0, "pack": 1, "single": 2, "unknown": 3}
        return (order.get(t, 3), -r.get("seeders", 0))

    enriched.sort(key=_sort_key)

    return jsonify({
        "ok":          True,
        "query":       q,
        "releases":    enriched,
        "owned_tomes": sorted(tomes_owned),
    })


# ════════════════════════════════════════════════════════
# TORRENT — TÉLÉCHARGEMENT (envoi vers qBittorrent)
# ════════════════════════════════════════════════════════

@app.route("/api/torrent/download", methods=["POST"])
def api_torrent_download():
    """
    Envoie un (ou plusieurs) torrent(s) à qBittorrent et les ajoute à la queue.
    Body : {
      client_id, series_slug, series_name,
      releases: [{title, link, vol_type, tomes, tome_start, tome_end, assigned_tomes}]
    }
    assigned_tomes : dict {index_in_tomes: tome_number} pour corriger les détections
    """
    d           = request.json or {}
    client_id   = d.get("client_id", "")
    series_slug = d.get("series_slug", "")
    series_name = d.get("series_name", "")
    releases    = d.get("releases", [])

    if not releases:
        return jsonify({"ok": False, "message": "Aucune release fournie"})

    # Cherche le client actif
    clients = config.get("download_clients", [])
    if client_id:
        client = next((c for c in clients if c.get("id") == client_id), None)
    else:
        client = next((c for c in clients if c.get("enabled", True)), None)

    if not client:
        return jsonify({"ok": False, "message": "Aucun client de téléchargement configuré/actif (Settings > Download Client)"})

    # Info série
    series_info = lib_mgr.resolve_slug(series_slug) if series_slug else None
    if series_info:
        series_name = series_info["name"]

    # Assure que la catégorie existe dans qBittorrent avec le bon save_path
    cat       = client.get("category", "")
    save_path = client.get("save_path", "")
    if cat:
        cat_result = qbittorrent_client.create_category(client, cat, save_path)
        config.add_log(
            f"[Torrent] create_category '{cat}' → {cat_result.get('message','?')}",
            "info" if cat_result.get("ok") else "warning"
        )

    added_torrents = []
    errors = []

    for rel in releases:
        link          = rel.get("link", "")
        title         = rel.get("title", "")
        vol_type      = rel.get("vol_type", "unknown")
        tomes         = rel.get("assigned_tomes") or rel.get("tomes", [])
        indexer       = rel.get("indexer", "")
        replace_tomes = rel.get("replace_tomes", [])   # Tomes à remplacer (pack/intégrale)

        if not link:
            errors.append(f"Lien manquant pour : {title}")
            continue

        # ── Envoi à qBittorrent ──
        client_save_path = client.get("save_path", "")
        client_category  = client.get("category", "")

        if link.startswith("magnet:"):
            # Lien magnet → envoi direct par URL
            config.add_log(f"[Torrent] Envoi magnet vers qBittorrent : {link[:80]}…", "info")
            result = qbittorrent_client.add_torrent(
                client, link,
                save_path=client_save_path,
                category=client_category,
            )
        else:
            # URL .torrent → téléchargement local puis upload fichier
            import requests as _req, re as _re
            config.add_log(f"[Torrent] Téléchargement .torrent : {link[:80]}{'…' if len(link)>80 else ''}", "info")
            try:
                tr = _req.get(link, timeout=30)
                tr.raise_for_status()
                # Nom du fichier depuis Content-Disposition ou titre nettoyé
                cd = tr.headers.get("Content-Disposition", "")
                filename = ""
                if "filename=" in cd:
                    m = _re.search(r'filename=["\']?([^"\';\n]+)', cd)
                    if m:
                        filename = m.group(1).strip()
                if not filename:
                    safe_title = _re.sub(r'[^a-zA-Z0-9_\-]', '_', title)[:60]
                    filename = f"{safe_title}.torrent"
                if not filename.endswith(".torrent"):
                    filename += ".torrent"
                torrent_path = os.path.join(TORRENT_FILES_DIR, filename)
                with open(torrent_path, "wb") as f:
                    f.write(tr.content)
                config.add_log(f"[Torrent] Fichier sauvegardé : {filename}", "info")
            except Exception as e:
                errors.append(f"{title} : impossible de télécharger le .torrent — {e}")
                continue

            result = qbittorrent_client.add_torrent_file(
                client, torrent_path,
                save_path=client_save_path,
                category=client_category,
            )

        config.add_log(
            f"[Torrent] → ok={result.get('ok')} : {result.get('message','?')}",
            "info" if result.get("ok") else "warning"
        )
        if not result.get("ok"):
            errors.append(f"{title} : {result.get('message', 'Erreur')}")
            continue

        # Ajoute à la queue MangaArr
        now = __import__("datetime").datetime.now().isoformat(timespec="seconds")

        # Extrait le hash qBittorrent depuis le lien magnet (pour matching fiable)
        qbt_hash = ""
        if link.startswith("magnet:"):
            _mh = re.search(r'xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', link, re.IGNORECASE)
            if _mh:
                qbt_hash = _mh.group(1).lower()

        base_item = {
            "source":        "torrent",
            "filename":      title,
            "tag":           "",
            "series_name":   series_name,
            "series_slug":   series_slug,
            "status":        "pending",
            "added_at":      now,
            "torrent_link":  link,
            "vol_type":      vol_type,
            "indexer":       indexer,
            "qbt_hash":      qbt_hash,
            "replace_tomes": replace_tomes,   # Tomes à forcer en remplacement
        }

        if vol_type == "single" and tomes:
            n   = tomes[0]
            res = queue_manager.add_to_queue([{**base_item,
                "tome_number": f"T{n:02d}",
                "tomes":       tomes,
            }])
        elif vol_type in ("pack", "integrale"):
            res = queue_manager.add_to_queue([{**base_item,
                "tome_number": f"T{tomes[0]:02d}-T{tomes[-1]:02d}" if tomes else "—",
                "tomes":       tomes,
            }])
        else:
            res = queue_manager.add_to_queue([{**base_item,
                "tome_number": "?",
                "tomes":       [],
            }])

        if res["added"]:
            added_torrents.append(title)
            config.add_log(f"[Torrent] Ajouté : {title} → qBittorrent", "info")
        else:
            errors.append(f"{title} : doublon ignoré (déjà en queue)")
            config.add_log(f"[Torrent] Doublon ignoré : {title}", "info")
            continue

    return jsonify({
        "ok":     len(added_torrents) > 0,
        "added":  len(added_torrents),
        "errors": errors,
        "message": f"{len(added_torrents)} torrent(s) envoyé(s)" +
                   (f", {len(errors)} erreur(s)" if errors else ""),
    })


# ════════════════════════════════════════════════════════
# TORRENT — TÉLÉCHARGEMENT FICHIER .torrent (proxy)
# ════════════════════════════════════════════════════════

@app.route("/api/torrent/fetch-file")
def api_torrent_fetch_file():
    """
    Proxy-télécharge un fichier .torrent depuis une URL Torznab et le renvoie au navigateur.
    Paramètre : ?url=<URL du .torrent>
    Utile quand le lien n'est pas un magnet mais une URL de fichier .torrent.
    """
    import requests as _req
    from flask import Response
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "message": "Paramètre url manquant"}), 400
    if url.startswith("magnet:"):
        return jsonify({"ok": False, "message": "Lien magnet — pas un fichier .torrent"}), 400
    try:
        r = _req.get(url, timeout=20, stream=True)
        r.raise_for_status()
        # Tente de récupérer le nom du fichier depuis Content-Disposition
        cd = r.headers.get("Content-Disposition", "")
        filename = "release.torrent"
        if "filename=" in cd:
            import re as _re
            m = _re.search(r'filename=["\']?([^"\';\n]+)', cd)
            if m:
                filename = m.group(1).strip()
        if not filename.endswith(".torrent"):
            filename += ".torrent"
        return Response(
            r.content,
            status=200,
            headers={
                "Content-Type": "application/x-bittorrent",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except _req.exceptions.HTTPError as e:
        return jsonify({"ok": False, "message": f"Erreur HTTP {e.response.status_code} en récupérant le fichier"}), 502
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 502


# ════════════════════════════════════════════════════════
# TORRENT — QUEUE (items source=torrent uniquement)
# ════════════════════════════════════════════════════════

@app.route("/api/torrent/queue")
def api_torrent_queue():
    """Retourne les items torrent de la queue."""
    items = [i for i in queue_manager.get_queue() if i.get("source") == "torrent"]
    items.sort(key=lambda x: (x.get("status") == "done", x.get("added_at", "")))
    return jsonify({"ok": True, "items": items, "total": len(items)})


@app.route("/api/torrent/scan-incoming")
def api_torrent_scan_incoming():
    """
    Déclenche manuellement le scan du dossier qBittorrent (MANGAARR_QBT_WATCH).
    Même logique que /api/queue/scan-incoming pour eMule.
    """
    import torrent_watcher as _tw
    watch_path = _tw.get_watch_path()
    if not watch_path:
        return jsonify({
            "ok": False,
            "message": "MANGAARR_QBT_WATCH non défini dans docker-compose.yml",
            "updated": 0,
        })
    result = _tw.do_scan_torrent_incoming(watch_path)
    return jsonify(result)


# ════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════
# DÉCOUVERTE — Page dédiée /discover
# ════════════════════════════════════════════════════════

@app.route("/discover")
def page_discover():
    return render_template("discover.html")


@app.route("/ebdz")
def page_ebdz_browser():
    return render_template("ebdz_browser.html", home_url=ebdz_browser.EBDZ_HOME)


@app.route("/api/ebdz-proxy")
def api_ebdz_proxy():
    url = request.args.get("url", "").strip() or ebdz_browser.EBDZ_HOME
    if "#" in url:
        url = url[:url.index("#")]
    cfg      = config.load()
    mybbuser = cfg.get("mybbuser", "")
    if not mybbuser:
        return ("<p style='font-family:sans-serif;color:red;padding:20px'>"
                "Cookie ebdz non configuré (Settings &gt; Indexers)</p>"), 200
    try:
        result = ebdz_browser.fetch_and_rewrite(url, mybbuser)
        return result["html"], 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        app.logger.error(f"[ebdz-proxy] {e}\n{err}")
        return (f"<pre style='font-family:monospace;padding:20px;color:red'>"
                f"Erreur proxy:\n{err}</pre>"), 200


@app.route("/api/ebdz-proxy/extract-ed2k")
def api_ebdz_extract_ed2k():
    """
    Extrait côté serveur tous les liens ed2k d'une page ebdz.net.
    GET /api/ebdz-proxy/extract-ed2k?url=https://ebdz.net/forum/showthread.php?tid=...
    Retourne {ok, links: [{url, filename, filesize, filehash, tome_number, tag}]}
    """
    url      = request.args.get("url", "").strip()
    # Ignore le fragment (#ancre) — non transmis au serveur HTTP
    if "#" in url:
        url = url[:url.index("#")]
    mybbuser = config.get("mybbuser", "")
    if not url:
        return jsonify({"ok": False, "links": [], "message": "URL manquante"})
    result = ebdz_browser.extract_ed2k_from_page(url, mybbuser)
    return jsonify(result)


@app.route("/api/ebdz-proxy/generate-collection", methods=["POST"])
def api_ebdz_generate_collection():
    """
    Reçoit une liste de liens ed2k avec numéros de tome corrigés,
    les ajoute à la queue et génère un .emulecollection pour la série.
    """
    d           = request.json or {}
    series_name = d.get("series_name", "").strip()
    items_in    = d.get("items", [])   # [{url, tome_number}]

    if not series_name:
        return jsonify({"ok": False, "message": "Nom de série requis"})
    if not items_in:
        return jsonify({"ok": False, "message": "Aucun lien fourni"})

    items = []
    for it in items_in:
        parsed = ebdz_scraper.parse_ed2k(it.get("url", ""))
        if not parsed:
            continue
        items.append({
            "filename":     parsed["filename"],
            "filesize":     parsed["filesize"],
            "filehash":     parsed["filehash"],
            "url":          parsed["url"],
            "tome_number":  it.get("tome_number") or parsed.get("tome_number", ""),
            "tag":          parsed.get("tag", ""),
            "series_name":  series_name,
            "series_exact": True,   # nom fourni explicitement → pas de fuzzy matching
            "action":       "missing",
        })

    if not items:
        return jsonify({"ok": False, "message": "Aucun lien ed2k valide"})

    r  = queue_manager.add_to_queue(items)
    series_label = re.sub(r"[^A-Za-z0-9_\-]", ".", series_name.replace(" ", "."))
    fp = queue_manager.generate_emulecollection(items, series_prefix=series_label)
    return jsonify({
        "ok":      True,
        "added":   r["added"],
        "skipped": r["skipped"],
        "file":    os.path.basename(fp) if fp else None,
    })


@app.route("/api/ebdz-proxy/add-ed2k", methods=["POST"])
def api_ebdz_add_ed2k():
    """Ajoute un lien ed2k intercepté depuis le navigateur ebdz à la queue."""
    d           = request.json or {}
    url         = d.get("url", "").strip()
    series_name = d.get("series_name", "").strip()
    if not url or not url.lower().startswith("ed2k://"):
        return jsonify({"ok": False, "message": "Lien ed2k invalide"})
    parsed = ebdz_scraper.parse_ed2k(url)
    if not parsed:
        return jsonify({"ok": False, "message": "Impossible de parser le lien ed2k"})
    item = {
        "filename":     parsed["filename"],
        "filesize":     parsed["filesize"],
        "filehash":     parsed["filehash"],
        "url":          parsed["url"],
        "tome_number":  parsed.get("tome_number", ""),
        "tag":          parsed.get("tag", ""),
        "series_name":  series_name,
        "series_exact": bool(series_name),  # nom fourni explicitement → pas de fuzzy
        "action":       "missing",
    }
    r = queue_manager.add_to_queue([item])
    if r["added"]:
        queue_manager.generate_emulecollection(label="ebdz-browser")
    return jsonify({"ok": True, "added": r["added"], "skipped": r["skipped"],
                    "filename": parsed["filename"]})


# ══════════════════════════════════════════════════════════
# AMULE
# ══════════════════════════════════════════════════════════

def _get_amule_client():
    """Retourne le premier client amule actif ou None."""
    clients = config.get("download_clients", [])
    return next((c for c in clients if c.get("type") == "amule" and c.get("enabled", True)), None)


@app.route("/api/amule/add-links", methods=["POST"])
def api_amule_add_links():
    """
    Envoie une liste de liens ed2k à aMule via amulecmd, un par un.
    Body : { items: [{url, tome_number, ...}], series_name: str }
    """
    d       = request.json or {}
    urls    = [it.get("url", "") for it in d.get("items", []) if it.get("url", "").startswith("ed2k://")]
    if not urls:
        return jsonify({"ok": False, "message": "Aucun lien ed2k fourni"})
    client = _get_amule_client()
    if not client:
        return jsonify({"ok": False, "message": "Aucun client aMule configuré et activé"})
    # Exécution en thread pour ne pas bloquer (envoi un par un avec délai)
    def _send():
        amule_client.add_ed2k_batch(client, urls)
    threading.Thread(target=_send, daemon=True).start()
    return jsonify({"ok": True, "queued": len(urls), "message": f"{len(urls)} lien(s) envoyé(s) à aMule"})


@app.route("/api/amule/cancel", methods=["POST"])
def api_amule_cancel():
    """
    Annule des téléchargements aMule par leurs hashes, un par un.
    Body : { hashes: ["hash1", "hash2", ...] }
    """
    d      = request.json or {}
    hashes = d.get("hashes", [])
    if not hashes:
        return jsonify({"ok": False, "message": "Aucun hash fourni"})
    client = _get_amule_client()
    if not client:
        return jsonify({"ok": False, "message": "Aucun client aMule configuré et activé"})
    def _cancel():
        amule_client.cancel_hashes_batch(client, hashes)
    threading.Thread(target=_cancel, daemon=True).start()
    return jsonify({"ok": True, "queued": len(hashes), "message": f"{len(hashes)} annulation(s) envoyée(s)"})


@app.route("/api/discover/series")
def api_discover_series():
    """
    Phase 1 (rapide, sans indexer) : liste les séries incomplètes ayant une metadata tomes_vf.
    ?lib_id=XXX pour filtrer une librairie.
    """
    lib_id  = request.args.get("lib_id", "")
    series  = discover_manager.get_incomplete_series(lib_id)
    return jsonify({"ok": True, "series": series, "total": len(series)})


@app.route("/api/discover/cache")
def api_discover_cache():
    """Retourne les résultats mis en cache (< 24h) ou liste vide."""
    series = discover_manager.load_cache()
    if series is None:
        info = discover_manager.cache_info()
        return jsonify({"ok": True, "cached": False, "series": [], "cache_info": info})
    info = discover_manager.cache_info()
    return jsonify({"ok": True, "cached": True, "series": series, "cache_info": info})


@app.route("/api/discover/cache", methods=["DELETE"])
def api_discover_cache_clear():
    """Supprime le cache découverte."""
    discover_manager.clear_cache()
    return jsonify({"ok": True})


@app.route("/api/discover/stream")
def api_discover_stream():
    """
    SSE : stream les résultats de la découverte Torznab en temps réel.
    ?lib_id=XXX&delay_ms=5000
    Événements : start | progress | series | done | error
    """
    lib_id   = request.args.get("lib_id", "")
    delay_ms = max(500, int(request.args.get("delay_ms", 5000)))

    cfg      = config.load()
    indexers = [i for i in cfg.get("torznab_indexers", []) if i.get("enabled", True)]

    def generate():
        if not indexers:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Aucun indexer Torznab configuré'})}\n\n"
            return

        series_list = discover_manager.get_incomplete_series(lib_id)
        total       = len(series_list)

        if total == 0:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Aucune série incomplète avec metadata tomes_vf. Liez une librairie à des métadonnées MangaDB d abord.'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

        found_series = []
        for i, s in enumerate(series_list):
            if i > 0:
                time.sleep(delay_ms / 1000)

            # Progrès
            yield f"data: {json.dumps({'type': 'progress', 'current': i + 1, 'total': total, 'series_name': s['series_name']})}\n\n"

            try:
                result = discover_manager.enrich_with_releases(s, indexers)
            except Exception as e:
                yield f"data: {json.dumps({'type': 'progress', 'current': i + 1, 'total': total, 'series_name': s['series_name'], 'error': str(e)})}\n\n"
                continue

            if result:
                found_series.append(result)
                yield f"data: {json.dumps({'type': 'series', 'series': result})}\n\n"

        # Sauvegarde cache
        discover_manager.save_cache(found_series)

        yield f"data: {json.dumps({'type': 'done', 'total_found': len(found_series), 'total_scanned': total})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/discover/download", methods=["POST"])
def api_discover_download():
    """
    Envoie les releases sélectionnées depuis la page découverte à qBittorrent.
    Délègue à api_torrent_download en réutilisant sa logique.
    """
    return api_torrent_download()


# ════════════════════════════════════════════════════════
# TORRENT — MONITORING SÉRIES
# ════════════════════════════════════════════════════════

@app.route("/api/torrent/monitoring")
def api_torrent_monitoring():
    """
    Analyse les séries dans les librairies :
    - Pour chaque série incomplète, cherche des releases Torznab.
    - Retourne la liste avec badges (nb releases trouvées).
    Paramètre optionnel : ?lib_id=XXX pour filtrer une librairie.
    """
    lib_id   = request.args.get("lib_id", "")
    libraries = lib_mgr.get_libraries()
    if lib_id:
        libraries = [l for l in libraries if l["id"] == lib_id]

    indexers = [i for i in config.get("torznab_indexers", []) if i.get("enabled", True)]
    if not indexers:
        return jsonify({"ok": False, "message": "Aucun indexer Torznab configuré", "series": []})

    results = []
    for lib in libraries:
        if not os.path.isdir(lib["path"]):
            continue
        lib_cache = cache_mod.get_library_cache(lib["id"])
        series_list = lib_mgr.scan_library(lib["id"])

        for s in series_list:
            sid      = s["id"]
            name     = s["name"]
            owned_n  = s["booksCount"]
            meta     = lib_cache.get(sid) or {}
            total_vf = meta.get("tomes_vf") or 0

            # Séries dont on a tous les tomes → skip
            if total_vf > 0 and owned_n >= total_vf:
                continue

            # Calcule les tomes manquants
            tomes_owned = {t["numero"] for t in s.get("tomes", []) if t.get("numero")}
            missing = []
            if total_vf > 0:
                missing = [n for n in range(1, total_vf + 1) if n not in tomes_owned]
            else:
                # Pas de metadata → on cherche quand même
                missing = []

            # Cherche des releases
            releases = torznab_client.search_all(indexers, name, categories=[7000])

            if not releases:
                continue

            # Filtre les releases utiles (manquants ou intégrale si incomplet)
            useful = []
            for r in releases:
                vt = r.get("vol_type", "unknown")
                tomes = r.get("tomes", [])
                if vt == "integrale":
                    useful.append(r)
                elif vt == "single" and tomes and tomes[0] in (missing or range(1, 9999)):
                    useful.append(r)
                elif vt == "pack" and tomes:
                    if missing and any(t in missing for t in tomes):
                        useful.append(r)

            if not useful:
                continue

            has_integrale = any(r.get("vol_type") == "integrale" for r in useful)

            results.append({
                "series_id":     sid,
                "series_name":   name,
                "series_slug":   s["slug"],
                "lib_id":        lib["id"],
                "lib_name":      lib["name"],
                "owned_count":   owned_n,
                "total_vf":      total_vf,
                "missing_count": len(missing),
                "missing_tomes": missing,
                "releases_count": len(useful),
                "has_integrale": has_integrale,
            })

    results.sort(key=lambda x: (-x["releases_count"], x["series_name"]))
    return jsonify({"ok": True, "series": results})



# ════════════════════════════════════════════════════════
# SÉRIE — HISTORIQUE TOMES, RECHERCHE ED2K, AJOUT
# ════════════════════════════════════════════════════════

@app.route("/api/collection/series/<path:series_slug>/history")
def api_series_history(series_slug):
    """Retourne l'historique des tomes traités pour une série (depuis la queue)."""
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "history": []})
    name = series_info["name"]
    items = queue_manager.get_queue()
    history = []
    for item in items:
        if (item.get("series_name", "").lower() == name.lower()
                and item.get("history")):
            h = dict(item["history"])
            h["filename"]    = item.get("filename", "")
            h["tome_number"] = item.get("tome_number", "")
            h["tag"]         = item.get("tag", "")
            h["status"]      = item.get("status", "")
            history.append(h)
    # Trie par tome
    history.sort(key=lambda x: int(str(x.get("tome_number","0")).lstrip("T").lstrip("0") or "0"))
    return jsonify({"ok": True, "history": history})


@app.route("/api/collection/series/<path:series_slug>/search-ebdz")
def api_series_search_ebdz(series_slug):
    """Recherche manuelle de liens ed2k pour une série (nom custom ou par défaut)."""
    q    = request.args.get("q", "").strip()
    info = lib_mgr.resolve_slug(series_slug)
    name = q or (info["name"] if info else "")
    if not name:
        return jsonify({"ok": False, "results": [], "message": "Nom requis"})

    thread = ebdz_scraper.find_thread_for_series(name)
    if not thread:
        return jsonify({"ok": False, "results": [],
                        "message": f"Aucun thread trouvé pour '{name}'"})

    mybbuser = config.get("mybbuser", "")
    if not mybbuser:
        return jsonify({"ok": False, "results": [], "message": "Cookie ebdz non configuré"})

    session   = ebdz_scraper.make_session(mybbuser)
    raw_links = ebdz_scraper.scrape_thread_ed2k(session, thread["url"])
    parsed    = [ebdz_scraper.parse_ed2k(l) for l in raw_links]
    parsed    = [p for p in parsed if p]

    return jsonify({
        "ok":      True,
        "thread":  thread["name"],
        "results": parsed,
    })


@app.route("/api/collection/series/<path:series_slug>/add-to-queue", methods=["POST"])
def api_series_add_to_queue(series_slug):
    """Ajoute des liens ed2k sélectionnés à la queue + génère emulecollection."""
    info  = lib_mgr.resolve_slug(series_slug)
    d     = request.json or {}
    items = d.get("items", [])   # [{filename, filesize, filehash, url, tome_number, tag}]
    if not items:
        return jsonify({"ok": False, "message": "Aucun item fourni"})

    name = info["name"] if info else d.get("series_name", "")
    for item in items:
        item.setdefault("series_name", name)
        item.setdefault("series_id",   "")
        item.setdefault("series_slug", series_slug)
        item.setdefault("action",      "missing")

    r     = queue_manager.add_to_queue(items)
    added   = r["added"]
    skipped = r["skipped"]
    fp    = queue_manager.generate_emulecollection(label="manual")
    links = sum(1 for line in open(fp) if line.startswith("ed2k://"))
    msg = f"{added} ajouté(s)"
    if skipped:
        msg += f", {skipped} doublon(s) ignoré(s)"
    return jsonify({"ok": True, "added": added, "skipped": skipped, "collection_file": os.path.basename(fp), "links": links, "message": msg})


@app.route("/api/collection/series/<path:series_slug>/detect", methods=["POST"])
def api_series_detect(series_slug):
    """Détection automatique des tomes manquants pour UNE série."""
    info = lib_mgr.resolve_slug(series_slug)
    if not info:
        return jsonify({"ok": False, "message": "Série introuvable"})
    if _task["running"]:
        return jsonify({"ok": False, "message": "Tâche déjà en cours"})

    def _run():
        try:
            import folder_scanner
            _set_task(running=True, label=f"Analyse '{info['name']}'…")
            result = folder_scanner.detect_missing_from_disk(
                info["path"].rsplit("/", 1)[0],   # dossier parent = librairie
                serie_filter=info["name"],
            )
            added = result.get("added", 0)
            _set_task(running=False,
                      label=f"'{info['name']}' — {added} tome(s) manquant(s)",
                      results=[result])
        except Exception as e:
            _set_task(running=False, label=f"Erreur : {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/collection/add-search")
def api_collection_add_search():
    """Recherche MangaDB pour ajouter une nouvelle série."""
    q      = request.args.get("q", "").strip()
    lib_id = request.args.get("lib_id", "")
    if not q:
        return jsonify({"ok": False, "results": []})
    results = mangadb_client.search_series(q)
    return jsonify({"ok": True, "results": results[:12], "lib_id": lib_id})


@app.route("/api/collection/series/add", methods=["POST"])
def api_collection_series_add():
    """Crée un dossier série dans une librairie et initialise les métadonnées."""
    d        = request.json or {}
    lib_id   = d.get("lib_id", "")
    name     = d.get("name", "").strip()
    mangadb_titre = d.get("mangadb_titre", "")

    lib = lib_mgr.get_library(lib_id)
    if not lib:
        return jsonify({"ok": False, "message": "Librairie introuvable"})
    if not name:
        return jsonify({"ok": False, "message": "Nom requis"})

    # Crée le dossier série
    import re as _re
    safe = _re.sub(r'[<>:"/\\|?*]', '_', name)
    folder = os.path.join(lib["path"], safe)
    if os.path.exists(folder):
        return jsonify({"ok": False, "message": f"Le dossier existe déjà : {folder}"})
    os.makedirs(folder, exist_ok=True)

    # Initialise les métadonnées MangaDB si un titre est fourni
    if mangadb_titre:
        series_id = lib_mgr._series_id(folder)
        sources   = config.get("metadata_sources", [])
        src_id    = sources[0].get("id") if sources else None
        meta      = mangadb_client.find_best_match(mangadb_titre, src_id) or {}
        if meta:
            cache_mod.set_series_meta(lib_id, series_id, meta)

    return jsonify({"ok": True, "message": f"Série '{name}' créée dans '{lib['name']}'",
                    "path": folder})

@app.route("/api/collection/series/<path:series_slug>/scan-cbz")
def api_scan_cbz(series_slug):
    """Scanne les CBZ d'une série et détecte les faux (RAR renommés)."""
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable"})
    import media_manager as _mm
    results = _mm.scan_series_for_fake_cbz(series_info["path"])
    fake    = [r for r in results if not r["valid"]]
    return jsonify({
        "ok":    True,
        "total": len(results),
        "fake":  len(fake),
        "files": results,
    })


@app.route("/api/collection/series/<path:series_slug>/repair-cbz", methods=["POST"])
def api_repair_cbz(series_slug):
    """Répare les faux CBZ (RAR renommés) d'une série."""
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable"})

    d         = request.json or {}
    filenames = d.get("filenames")  # None = tous les faux CBZ

    import media_manager as _mm
    if not filenames:
        results  = _mm.scan_series_for_fake_cbz(series_info["path"])
        to_fix   = [r["path"] for r in results if not r["valid"]]
    else:
        to_fix   = [os.path.join(series_info["path"], fn) for fn in filenames]

    repaired = 0
    errors   = []
    for fp in to_fix:
        result = _mm.repair_fake_cbz(fp)
        if result.get("ok") and result.get("message") != "Déjà un ZIP valide":
            repaired += 1
            # Invalide le cache cover
            import image_cache as _ic
            _ic.clear_cache(series_info["id"])
        elif not result.get("ok"):
            errors.append(f"{os.path.basename(fp)}: {result.get('message')}")

    return jsonify({
        "ok":      True,
        "repaired": repaired,
        "errors":  errors,
        "message": f"{repaired} fichier(s) réparé(s)" + (f", {len(errors)} erreur(s)" if errors else ""),
    })


# ════════════════════════════════════════════════════════
# PAGE SÉRIE — RECHERCHE TOMES + HISTORIQUE + ÉDITION
# ════════════════════════════════════════════════════════

@app.route("/api/collection/series/<path:series_slug>/tome-history")
def api_tome_history(series_slug):
    """Retourne l'historique de traitement de tous les tomes d'une série."""
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "history": []})

    sid    = series_info["id"]
    lib_id = series_info["lib_id"]

    # Cherche dans la queue les items done de cette série
    items = [
        i for i in queue_manager.get_queue()
        if i.get("series_name", "").lower() == series_info["name"].lower()
        and i.get("status") == "done"
        and i.get("history")
    ]
    return jsonify({"ok": True, "history": items})


@app.route("/api/collection/series/<path:series_slug>/search-tomes")
def api_search_tomes(series_slug):
    """
    Cherche les tomes disponibles sur ebdz pour une série.
    Retourne les liens ed2k disponibles avec leur score.
    """
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable", "tomes": []})

    q = request.args.get("q", series_info["name"])  # terme de recherche

    try:
        cfg      = config.load()
        mybbuser = cfg.get("mybbuser", "")
        if not mybbuser:
            return jsonify({"ok": False, "message": "Cookie ebdz non configuré", "tomes": []})

        import ebdz_scraper as _ebdz
        session = _ebdz.make_session(mybbuser)
        if not _ebdz.check_login(session):
            return jsonify({"ok": False, "message": "Cookie ebdz invalide", "tomes": []})

        thread = _ebdz.find_thread_for_series(q)
        if not thread:
            return jsonify({"ok": False, "message": f"Série '{q}' non trouvée sur ebdz", "tomes": []})

        raw_links    = _ebdz.scrape_thread_ed2k(session, thread["url"])
        best_by_tome = _ebdz.get_best_ed2k_per_tome(raw_links)

        # Tomes possédés sur disque
        tomes_owned    = {t["numero"] for t in series_info.get("tomes", []) if t["numero"]}
        tomes_by_num   = {t["numero"]: t["filename"] for t in series_info.get("tomes", []) if t["numero"]}

        tomes = []
        for tome_str, parsed in sorted(best_by_tome.items()):
            n      = int(str(tome_str).lstrip("T").lstrip("0") or "0")
            tomes.append({
                "tome_str":   tome_str,
                "numero":     n,
                "filename":   parsed.get("filename", ""),
                "tag":        parsed.get("tag", ""),
                "url":        parsed.get("url", ""),
                "filehash":   parsed.get("filehash", ""),
                "filesize":   parsed.get("filesize", 0),
                "owned":      n in tomes_owned,
                "owned_file": tomes_by_num.get(n, "") if n in tomes_owned else "",
                "in_queue":   any(
                    i.get("filehash") == parsed.get("filehash")
                    for i in queue_manager.get_queue()
                ),
            })

        return jsonify({
            "ok":     True,
            "thread": thread["name"],
            "tomes":  tomes,
        })
    except Exception as e:
        return jsonify({"ok": False, "message": str(e), "tomes": []})


@app.route("/api/collection/series/<path:series_slug>/add-tomes", methods=["POST"])
def api_add_tomes_to_queue(series_slug):
    """Ajoute des tomes sélectionnés à la queue + génère .emulecollection."""
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable"})

    d          = request.json or {}
    tomes_data = d.get("tomes", [])  # [{url, filename, filehash, filesize, tag, tome_str, action}]
    if not tomes_data:
        return jsonify({"ok": False, "message": "Aucun tome fourni"})

    items = []
    for t in tomes_data:
        owned_file = t.get("owned_file", "")
        action     = "upgrade" if owned_file else t.get("action", "missing")
        items.append({
            "filename":    t.get("filename", ""),
            "filesize":    t.get("filesize", 0),
            "filehash":    t.get("filehash", ""),
            "url":         t.get("url", ""),
            "tome_number": t.get("tome_str", ""),
            "tag":         t.get("tag", ""),
            "series_name": series_info["name"],
            "series_id":   series_info["id"],
            "series_slug": series_info["slug"],
            "action":      action,
            "owned_file":  owned_file,
        })

    r       = queue_manager.add_to_queue(items)
    added   = r["added"]
    skipped = r["skipped"]
    # Nom de fichier : {Serie}.{datetime}_ADD.emulecollection
    series_label = re.sub(r"[^A-Za-z0-9_\-]", ".", series_info["name"].replace(" ", "."))
    fp      = queue_manager.generate_emulecollection(items, series_prefix=series_label)
    msg = f"{added} tome(s) ajouté(s) à la queue"
    if skipped:
        msg += f", {skipped} doublon(s) ignoré(s)"
    return jsonify({
        "ok":         True,
        "added":      added,
        "skipped":    skipped,
        "file":       os.path.basename(fp) if fp else None,
        "download":   True,   # Indique au frontend de déclencher le téléchargement
        "message":    msg,
    })


@app.route("/api/collection/series/<path:series_slug>/rename-tome", methods=["POST"])
def api_rename_tome(series_slug):
    """
    Renomme un tome dans son dossier de destination selon le format MangaArr.
    Conserve l'historique avec le nom original.
    """
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable"})

    d            = request.json or {}
    old_filename = d.get("filename", "").strip()
    new_tag      = d.get("tag", "").strip()
    new_tome_num = d.get("tome_number", "").strip()

    if not old_filename:
        return jsonify({"ok": False, "message": "filename requis"})

    old_path = os.path.join(series_info["path"], old_filename)
    if not os.path.isfile(old_path):
        return jsonify({"ok": False, "message": f"Fichier introuvable : {old_filename}"})

    try:
        tag      = new_tag or profiles.detect_tag(old_filename)
        raw_tome = new_tome_num or renamer.detect_tome(old_filename) or "T00"
        t_clean  = str(raw_tome).lstrip("Tt").lstrip("0") or "0"
        tome_str = f"T{int(t_clean):02d}"

        series_clean  = renamer.clean_title(renamer.extract_leading_article(series_info["name"]))
        new_filename  = renamer.build_filename(series_clean, tome_str, tag)
        new_path      = os.path.join(series_info["path"], new_filename)

        if os.path.abspath(old_path) == os.path.abspath(new_path):
            return jsonify({"ok": True, "message": "Nom inchangé", "new_filename": new_filename})

        os.rename(old_path, new_path)
        config.add_log(
            f"[Rename] {series_info['name']} : {old_filename} → {new_filename}", "info"
        )
        # Invalide le cache cover pour ce tome
        import image_cache as _ic
        _ic.clear_cache(series_info["id"])

        return jsonify({
            "ok":           True,
            "old_filename": old_filename,
            "new_filename": new_filename,
            "message":      f"Renommé : {new_filename}",
        })
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


# ════════════════════════════════════════════════════════
# LIBRAIRIES LOCALES
# ════════════════════════════════════════════════════════




# ════════════════════════════════════════════════════════
# COLLECTION — SÉRIES LOCALES
# ════════════════════════════════════════════════════════


@app.route("/api/libraries/<lib_id>/create-series", methods=["POST"])
def api_create_series(lib_id):
    """Crée un dossier de série dans une librairie (depuis la recherche MangaDB)."""
    lib = lib_mgr.get_library(lib_id)
    if not lib or not os.path.isdir(lib["path"]):
        return jsonify({"ok": False, "message": "Librairie introuvable"})

    d           = request.json or {}
    series_name = d.get("name", "").strip()
    mangadb_titre = d.get("mangadb_titre", "").strip()

    if not series_name:
        return jsonify({"ok": False, "message": "Nom de série requis"})

    # Sanitize le nom pour le dossier
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', series_name).strip()
    series_dir = os.path.join(lib["path"], safe_name)

    if os.path.exists(series_dir):
        return jsonify({"ok": False, "message": f"Le dossier existe déjà : {safe_name}"})

    try:
        os.makedirs(series_dir, exist_ok=True)
        config.add_log(f"[Série] Dossier créé : {series_dir}", "info")

        # Si on a un titre MangaDB → pré-charge les metadata dans le cache
        if mangadb_titre:
            series_id = lib_mgr._series_id(series_dir)
            meta = mangadb_client.find_best_match(mangadb_titre) or {}
            if meta:
                cache_mod.set_series_meta(lib_id, series_id, meta)

        return jsonify({
            "ok":      True,
            "message": f"Série '{safe_name}' créée dans {lib['name']}",
            "path":    series_dir,
        })
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})

@app.route("/api/collection/libraries")
def api_collection_libraries():
    """Compatibilité : retourne les librairies locales."""
    return jsonify({"ok": True, "libraries": lib_mgr.get_libraries()})

@app.route("/api/collection/series")
def api_series():
    lib_id = request.args.get("library_id", "")
    if not lib_id:
        return jsonify({"series": [], "total": 0})

    series_list = lib_mgr.scan_library(lib_id)
    sources     = config.get("metadata_sources", [])
    linked      = [s for s in sources if lib_id in s.get("library_ids", [])]

    lib_cache = cache_mod.get_library_cache(lib_id)
    result    = []
    to_enrich = []

    for s in series_list:
        sid     = s["id"]
        name    = s["name"]
        cached  = lib_cache.get(sid)
        meta    = cached or {}

        result.append({
            "id":         sid,
            "name":       name,
            "slug":       s["slug"],
            "booksCount": s["booksCount"],
            "thumbnail":  f"/api/local/series/{sid}/thumbnail",
            "totalVF":    meta.get("tomes_vf") or None,
            "statut_vf":  meta.get("statut_vf") or None,
            "genres":     meta.get("genres") or [],
            "metaLoaded": cached is not None,
            "diskPath":   s["path"],
        })
        if linked and cached is None:
            to_enrich.append({"id": sid, "name": name})

    # Enrichissement asynchrone
    if to_enrich and linked and not cache_mod.is_enriching(lib_id):
        src_url = linked[0].get("url", "")
        src_id  = linked[0].get("id")

        def _enrich(lid, series_lst, s_id, s_url):
            import time, requests, pandas as pd
            cache_mod._enriching[lid] = True
            try:
                csv_df = None
                try:
                    r = requests.get(f"{s_url.rstrip('/')}/api/series", timeout=30)
                    if r.status_code == 200:
                        data = r.json().get("series", [])
                        if data:
                            csv_df = pd.DataFrame(data)
                except Exception:
                    pass
                for s in series_lst:
                    existing_meta = cache_mod.get_series_meta(lid, s["id"])
                    # Déjà associé/dissocié manuellement → ne pas écraser
                    if existing_meta is not None:
                        continue
                    if csv_df is not None:
                        row  = cache_mod.find_in_csv(s["name"], csv_df)
                        meta = cache_mod._build_meta(row) if row else {}
                    else:
                        meta = mangadb_client.find_best_match(s["name"], s_id) or {}
                    cache_mod.set_series_meta(lid, s["id"], meta)
                    with _ev_lock:
                        _enrich_events.setdefault(lid, []).append({"series_id": s["id"], "meta": meta})
                    time.sleep(0.02)
            finally:
                import threading as _t
                with cache_mod._enrich_lock:
                    cache_mod._enriching[lid] = False

        threading.Thread(target=_enrich, args=(lib_id, to_enrich, src_id, src_url), daemon=True).start()

    return jsonify({
        "series":    result,
        "total":     len(result),
        "enriching": cache_mod.is_enriching(lib_id),
    })

@app.route("/api/collection/series/enrich-events/<library_id>")
def api_enrich_events(library_id):
    with _ev_lock:
        events = _enrich_events.pop(library_id, [])
    return jsonify({
        "events":    events,
        "enriching": cache_mod.is_enriching(library_id),
    })

@app.route("/api/collection/series/<path:series_slug>")
def api_series_detail(series_slug):
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable"})

    sid    = series_info["id"]
    lib_id = series_info["lib_id"]
    name   = series_info["name"]
    tomes  = series_info["tomes"]

    books_data = [{
        "id":        t["filename"],
        "name":      t["filename"],
        "number":    str(t["numero"]) if t["numero"] else "",
        "thumbnail": f"/api/local/books/{sid}/{t['filename']}/thumbnail",
    } for t in tomes]

    sources    = config.get("metadata_sources", [])
    linked     = [s for s in sources if lib_id in s.get("library_ids", [])]
    manga_meta = cache_mod.get_series_meta(lib_id, sid)
    # Ne jamais écraser une association/dissociation manuelle (_manual ou _dissociated)
    if manga_meta is None and linked:
        manga_meta = mangadb_client.find_best_match(name, linked[0].get("id")) or {}
        if manga_meta:
            cache_mod.set_series_meta(lib_id, sid, manga_meta)
    # Série dissociée manuellement → afficher comme non-associée
    if manga_meta and manga_meta.get("_dissociated"):
        manga_meta = {}
    manga_meta = manga_meta or {}

    mangadb_tomes = []
    if manga_meta.get("titre") and linked:
        try:
            detail_api = mangadb_client.get_series_detail(manga_meta["titre"], linked[0].get("id"))
            if detail_api and isinstance(detail_api.get("tomes"), list):
                mangadb_tomes = detail_api["tomes"]
                if mangadb_tomes and not manga_meta.get("resume_t01"):
                    manga_meta["resume_t01"] = mangadb_tomes[0].get("resume", "") or ""
        except Exception:
            pass

    return jsonify({
        "ok":           True,
        "id":           sid,
        "slug":         series_info["slug"],
        "name":         name,
        "booksCount":   len(tomes),
        "books":        books_data,
        "thumbnail":    f"/api/local/series/{sid}/thumbnail",
        "komgaMeta":    {},
        "mangadb":      manga_meta,
        "mangadbTomes": mangadb_tomes,
        "libraryId":    lib_id,
        "diskPath":     series_info["path"],
    })


# ════════════════════════════════════════════════════════
# SERIES — ASSOCIATION MANUELLE MANGADB
# ════════════════════════════════════════════════════════

@app.route("/api/collection/series/<path:series_slug>/mangadb-search")
def api_mangadb_search_for_series(series_slug):
    """Recherche des candidats MangaDB pour une série (association manuelle)."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": False, "message": "Paramètre q requis", "results": []})
    try:
        raw = mangadb_client.search_series(q)
        results = [{"titre": r.get("titre",""), "score": r.get("score",0),
                    "auteur": r.get("auteur",""), "statut": r.get("statut","")} for r in raw]
        return jsonify({"ok": True, "results": results[:10]})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e), "results": []})


@app.route("/api/collection/series/<path:series_slug>/mangadb-link", methods=["POST"])
def api_mangadb_link(series_slug):
    """Associe manuellement une série Komga à une entrée MangaDB."""
    d          = request.json or {}
    mangadb_id = d.get("mangadb_titre", "").strip()  # titre exact MangaDB
    if not mangadb_id:
        return jsonify({"ok": False, "message": "mangadb_titre requis"})

    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable"})
    series_id  = series_info["id"]
    library_id = series_info["lib_id"]

    # Récupère les méta MangaDB depuis le titre exact
    sources = config.get("metadata_sources", [])
    src_id  = sources[0].get("id") if sources else None
    try:
        meta = mangadb_client.get_series_detail(mangadb_id, src_id)
        if not meta:
            return jsonify({"ok": False, "message": f"Titre '{mangadb_id}' introuvable dans MangaDB"})
        # Construit le cache comme find_best_match
        built = cache_mod.find_in_csv_by_titre(mangadb_id) or {}
        if not built:
            # Fallback : on stocke les données directement depuis l'API detail
            built = {
                "titre":          mangadb_id,
                "auteur":         meta.get("auteur", ""),
                "editeur":        meta.get("editeur", ""),
                "tomes_vf":       meta.get("tomes_total", 0),
                "statut_vf":      meta.get("statut", ""),
                "genres":         meta.get("genres", []),
                "manga_news_url": meta.get("url", ""),
                "resume_t01":     (meta.get("tomes") or [{}])[0].get("resume", "") if meta.get("tomes") else "",
                "_manual":        True,
            }
        built["_manual"] = True
        cache_mod.set_series_meta(library_id, series_id, built)
        return jsonify({"ok": True, "message": f"Associé à '{mangadb_id}' ✓", "meta": built})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/collection/series/<path:series_slug>/mangadb-unlink", methods=["POST"])
def api_mangadb_unlink(series_slug):
    """
    Dissocie manuellement la série de MangaDB.
    Stocke un sentinel {_dissociated: True, _manual: True} pour empêcher le re-match automatique.
    """
    series_info = lib_mgr.resolve_slug(series_slug)
    if not series_info:
        return jsonify({"ok": False, "message": "Série introuvable"})
    series_id  = series_info["id"]
    library_id = series_info["lib_id"]
    # Ne pas mettre None (serait re-matché auto) — stocker un sentinel verrouillé
    cache_mod.set_series_meta(library_id, series_id, {"_dissociated": True, "_manual": True})
    return jsonify({"ok": True, "message": "Association supprimée et verrouillée — aucun re-match automatique"})

@app.route("/api/collection/scan/<library_id>", methods=["POST"])
def api_scan(library_id):
    """Invalide le cache de covers pour forcer un rechargement."""
    lib_mgr.clear_cover_cache()
    return jsonify({"ok": True, "message": "Cache covers invalidé"})


# ════════════════════════════════════════════════════════
# PROFILES
# ════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════
# SETTINGS — LIBRAIRIES
# ════════════════════════════════════════════════════════

@app.route("/api/libraries", methods=["GET"])
def api_get_libraries():
    return jsonify({"ok": True, "libraries": lib_mgr.get_libraries()})

@app.route("/api/libraries", methods=["POST"])
def api_add_library():
    d = request.json or {}
    result = lib_mgr.add_library(d.get("name","").strip(), d.get("path","").strip())
    return jsonify(result)

@app.route("/api/libraries/<lib_id>", methods=["DELETE"])
def api_delete_library(lib_id):
    cache_mod.clear_cache(lib_id)
    return jsonify(lib_mgr.delete_library(lib_id))

@app.route("/api/libraries/<lib_id>/test")
def api_test_library(lib_id):
    lib = lib_mgr.get_library(lib_id)
    if not lib:
        return jsonify({"ok": False, "message": "Introuvable"})
    if not os.path.isdir(lib["path"]):
        return jsonify({"ok": False, "message": f"Dossier introuvable : {lib['path']}"})
    count = sum(1 for e in os.listdir(lib["path"]) if os.path.isdir(os.path.join(lib["path"], e)))
    return jsonify({"ok": True, "message": f"{count} dossier(s) trouvé(s)"})


# ════════════════════════════════════════════════════════
# SETTINGS — MEDIA MANAGEMENT (watcher)
# ════════════════════════════════════════════════════════

@app.route("/api/settings/media", methods=["GET"])
def api_get_media_settings():
    cfg = config.load()
    mm  = cfg.get("media_management", {})
    return jsonify({
        "auto_rename":      mm.get("auto_rename",      True),
        "auto_convert_cbr": mm.get("auto_convert_cbr", True),
        "auto_convert_pdf": mm.get("auto_convert_pdf", True),
        "auto_replace":     mm.get("auto_replace",     True),
        "download_dir":     cfg.get("download_dir",    ""),
        "watcher_interval": cfg.get("watcher_interval", 0),
    })

@app.route("/api/settings/media", methods=["POST"])
def api_set_media_settings():
    d   = request.json or {}
    cfg = config.load()
    mm  = cfg.setdefault("media_management", {})
    for key in ("auto_rename", "auto_convert_cbr", "auto_convert_pdf", "auto_replace"):
        if key in d:
            mm[key] = bool(d[key])
    if "download_dir" in d:
        cfg["download_dir"] = d["download_dir"]
    if "watcher_interval" in d:
        interval = int(d["watcher_interval"])
        cfg["watcher_interval"] = interval
        # Redémarre le watcher avec le nouvel intervalle
        lib_mgr.start_watcher(interval)
    config.save(cfg)
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
# RENOMMAGE BIBLIOTHÈQUE
# ════════════════════════════════════════════════════════

@app.route("/api/library/rename/history", methods=["GET"])
def api_rename_history():
    """Liste les historiques de renommage disponibles."""
    import rename_library as _rl
    return jsonify({"ok": True, "history": _rl.list_history()})


@app.route("/api/library/rename", methods=["POST"])
def api_library_rename():
    """
    Lance le renommage de la bibliothèque.
    Body : { dry_run: bool, format_id: int (1|2|3) }
    """
    import rename_library as _rl
    d         = request.json or {}
    dry_run   = bool(d.get("dry_run", False))
    format_id = d.get("format_id")
    if format_id is not None:
        try:
            format_id = int(format_id)
        except (ValueError, TypeError):
            format_id = None
    result = _rl.run_rename(dry_run=dry_run, format_id=format_id)
    return jsonify(result)


@app.route("/api/library/rename/rollback", methods=["POST"])
def api_library_rename_rollback():
    """
    Annule un renommage.
    Body : { file: str }  ← nom du fichier d'historique
    """
    import rename_library as _rl
    d    = request.json or {}
    fname = d.get("file", "").strip()
    if not fname:
        return jsonify({"ok": False, "message": "Fichier d'historique non spécifié"})
    result = _rl.run_rollback(fname)
    return jsonify(result)


# ════════════════════════════════════════════════════════
# CACHE COVERS — STATS ET PURGE
# ════════════════════════════════════════════════════════

@app.route("/api/cache/covers/stats")
def api_cover_cache_stats():
    import image_cache as _ic
    return jsonify(_ic.stats())

@app.route("/api/cache/covers/clear", methods=["POST"])
def api_cover_cache_clear():
    import image_cache as _ic
    removed = _ic.clear_cache()
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/profiles", methods=["GET"])
def api_get_profiles():
    return jsonify({
        "tags": profiles.get_tags(),
        "must_contain": profiles.get_must_contain(),
        "must_not_contain": profiles.get_must_not_contain(),
        "known_tags": config.KNOWN_TAGS,
    })

@app.route("/api/profiles", methods=["POST"])
def api_set_profiles():
    d = request.json
    if "tags" in d: profiles.set_tags(d["tags"])
    if "must_contain" in d: profiles.set_must_contain(d["must_contain"])
    if "must_not_contain" in d: profiles.set_must_not_contain(d["must_not_contain"])
    return jsonify({"ok": True})

@app.route("/api/profiles/tags", methods=["POST"])
def api_add_tag():
    d = request.json; name, score = d.get("name","").strip(), int(d.get("score", 50))
    if not name: return jsonify({"ok": False, "message": "Nom requis"})
    tags = profiles.get_tags()
    if any(t["name"] == name for t in tags): return jsonify({"ok": False, "message": "Tag déjà existant"})
    tags.append({"name": name, "score": score}); profiles.set_tags(tags)
    return jsonify({"ok": True})

@app.route("/api/profiles/tags/<tag_name>", methods=["DELETE"])
def api_delete_tag(tag_name):
    profiles.set_tags([t for t in profiles.get_tags() if t["name"] != tag_name])
    return jsonify({"ok": True})

@app.route("/api/profiles/tags/<tag_name>/score", methods=["PATCH"])
def api_update_tag_score(tag_name):
    score = int(request.json.get("score", 50))
    tags = profiles.get_tags()
    for t in tags:
        if t["name"] == tag_name: t["score"] = score
    profiles.set_tags(tags); return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
# METADATA SOURCES
# ════════════════════════════════════════════════════════

@app.route("/api/metadata/sources", methods=["GET"])
def api_list_sources():
    return jsonify({"sources": config.get("metadata_sources", [])})

@app.route("/api/metadata/sources", methods=["POST"])
def api_add_source():
    d = request.json
    url, name = d.get("url","").strip().rstrip("/"), d.get("name","MangaDB").strip()
    komga_index, library_ids = d.get("komga_index"), d.get("library_ids", [])
    if not url: return jsonify({"ok": False, "message": "URL requise"})
    result = mangadb_client.test_connection(url)
    if not result["ok"]: return jsonify(result)
    cfg = config.load()
    source = {"id": str(uuid.uuid4())[:8], "name": name, "url": url,
              "komga_index": komga_index, "library_ids": library_ids}
    cfg.setdefault("metadata_sources", []).append(source)
    config.save(cfg)
    return jsonify({"ok": True, "source": source, "message": result["message"]})

@app.route("/api/metadata/sources/<source_id>", methods=["DELETE"])
def api_delete_source(source_id):
    cfg = config.load()
    cfg["metadata_sources"] = [s for s in cfg.get("metadata_sources",[]) if s.get("id") != source_id]
    config.save(cfg); return jsonify({"ok": True})

@app.route("/api/metadata/sources/<source_id>", methods=["PATCH"])
def api_update_source(source_id):
    d = request.json; cfg = config.load()
    for s in cfg.get("metadata_sources", []):
        if s.get("id") == source_id:
            for k in ("library_ids", "komga_index", "name"):
                if k in d: s[k] = d[k]
    config.save(cfg); return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
# CACHE
# ════════════════════════════════════════════════════════

@app.route("/api/cache/stats")
def api_cache_stats():
    return jsonify(cache_mod.get_cache_stats())

@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    d = request.json or {}
    library_id = d.get("library_id")
    cache_mod.clear_cache(library_id)
    return jsonify({"ok": True, "message": "Cache vidé"})


# ════════════════════════════════════════════════════════
# SETTINGS — SCRAPE INTERVAL
# ════════════════════════════════════════════════════════

@app.route("/api/settings/scrape-interval", methods=["GET"])
def api_get_scrape_interval():
    return jsonify({"interval_hours": config.get("scrape_interval_hours", 12)})

@app.route("/api/settings/scrape-interval", methods=["POST"])
def api_set_scrape_interval():
    hours = int(request.json.get("interval_hours", 12))
    hours = max(1, min(168, hours))  # 1h min, 7j max
    config.set_value("scrape_interval_hours", hours)
    return jsonify({"ok": True, "interval_hours": hours})


# ════════════════════════════════════════════════════════
# MEDIA MANAGEMENT
# ════════════════════════════════════════════════════════

@app.route("/api/media/process", methods=["POST"])
def api_process():
    path = request.json.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "message": "Chemin invalide"})
    if _task["running"]:
        return jsonify({"ok": False, "message": "Tâche déjà en cours"})
    def _run():
        _set_task(running=True, label="Traitement…", results=[])
        has_sub = any(os.path.isdir(os.path.join(path, x)) for x in os.listdir(path))
        results = media_manager.scan_and_process_root(path) if has_sub else media_manager.process_series_folder(path)
        _set_task(running=False, label="Terminé", results=results)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/media/status")
def api_task_status():
    with _lock: return jsonify(dict(_task))


# ════════════════════════════════════════════════════════
# LOGS
# ════════════════════════════════════════════════════════



@app.route("/api/browse")
def api_browse():
    """
    Parcourt les dossiers accessibles dans le container.
    Utilisé par le file browser pour choisir une librairie.
    """
    path = request.args.get("path", "/")
    # Sécurité : empêche de sortir des chemins autorisés
    path = os.path.normpath(path)

    if not os.path.isdir(path):
        return jsonify({"ok": False, "message": f"Dossier introuvable : {path}", "entries": []})

    try:
        entries = []
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full) and not entry.startswith("."):
                # Compte les sous-dossiers (= séries potentielles)
                try:
                    sub_count = sum(1 for e in os.listdir(full) if os.path.isdir(os.path.join(full, e)))
                except Exception:
                    sub_count = 0
                entries.append({
                    "name":      entry,
                    "path":      full,
                    "sub_count": sub_count,
                })
        # Chemin parent
        parent = str(os.path.dirname(path)) if path != "/" else None
        return jsonify({
            "ok":      True,
            "current": path,
            "parent":  parent,
            "entries": entries,
        })
    except PermissionError:
        return jsonify({"ok": False, "message": "Accès refusé", "entries": []})


@app.route("/api/debug/paths")
def api_debug_paths():
    """Diagnostic des chemins configurés — vérifie leur existence dans le container."""
    cfg = config.load()
    paths_to_check = {
        "emule_incoming_dir": cfg.get("emule_incoming_dir", ""),
        "download_dir":       cfg.get("download_dir", ""),
    }
    for lib in cfg.get("libraries", []):
        paths_to_check[f"library:{lib['name']}"] = lib["path"]

    result = {}
    for key, path in paths_to_check.items():
        if not path:
            result[key] = {"path": "(vide)", "exists": False, "is_dir": False, "contents": []}
        else:
            exists = os.path.exists(path)
            is_dir = os.path.isdir(path)
            contents = []
            if is_dir:
                try:
                    contents = sorted(os.listdir(path))[:10]
                except Exception:
                    contents = ["(erreur lecture)"]
            result[key] = {
                "path":     path,
                "exists":   exists,
                "is_dir":   is_dir,
                "contents": contents,
            }
    return jsonify(result)

@app.route("/api/logs")
def api_logs():
    n = int(request.args.get("n", 200))
    return jsonify({"logs": config.get_logs(n)})

@app.route("/api/logs/clear", methods=["POST"])
def api_clear_logs():
    config.clear_logs()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════
# SCRAPE EBDZ
# ════════════════════════════════════════════════════════

@app.route("/api/ebdz/state")
def api_ebdz_state():
    return jsonify(ebdz_scraper.get_state())

@app.route("/api/ebdz/scrape", methods=["POST"])
def api_ebdz_scrape():
    d    = request.json or {}
    mode = d.get("mode", "partial")          # "full" | "partial"
    n    = int(d.get("max_pages", 3))
    ok   = ebdz_scraper.start_scrape(mode=mode, max_pages=n)
    return jsonify({"ok": ok, "message": "Scrape lancé" if ok else "Scrape déjà en cours"})

@app.route("/api/ebdz/threads")
def api_ebdz_threads():
    page  = int(request.args.get("page", 1))
    size  = int(request.args.get("size", 50))
    q     = request.args.get("q", "").strip().lower()
    threads = ebdz_scraper.get_all_threads()
    if q:
        import unicodedata
        def _norm(t):
            t = unicodedata.normalize("NFD", t)
            t = "".join(c for c in t if unicodedata.category(c) != "Mn")
            return t.lower()
        threads = [t for t in threads if q in _norm(t.get("name",""))]
    total  = len(threads)
    start  = (page - 1) * size
    return jsonify({"threads": threads[start:start+size], "total": total, "page": page})

@app.route("/rss")
def rss_feed():
    xml = ebdz_scraper.generate_rss(max_items=100)
    return Response(xml, mimetype="application/rss+xml")


# ════════════════════════════════════════════════════════
# QUEUE
# ════════════════════════════════════════════════════════

@app.route("/api/queue")
def api_queue():
    page  = int(request.args.get("page", 1))
    size  = int(request.args.get("size", 20))
    # eMule uniquement : exclut les items torrent
    items = [i for i in queue_manager.get_queue()
             if i.get("source", "ebdz") != "torrent"]
    items.sort(key=lambda x: (x.get("status") == "done", x.get("added_at","")), reverse=False)
    total = len(items)
    start = (page - 1) * size
    return jsonify({
        "items": items[start:start+size],
        "total": total,
        "page":  page,
        "stats": queue_manager.get_queue_stats(),
    })

@app.route("/api/queue/clear", methods=["POST"])
def api_queue_clear():
    d      = request.json or {}
    mode   = d.get("mode", "done")    # "done" | "all"
    source = d.get("source", "")      # "" = tous | "ebdz" | "torrent"
    if mode == "all" and not source:
        queue_manager.clear_queue()
    else:
        queue_manager.remove_done(older_than_days=0, source_filter=source or None)
    return jsonify({"ok": True})


@app.route("/api/queue/series-on-disk")
def api_series_on_disk():
    """
    Liste les séries disponibles groupées par librairie configurée.
    Retourne {ok, libraries: [{id, name, path, series: [...]}]}
    """
    libraries = lib_mgr.get_libraries()
    if not libraries:
        return jsonify({"ok": False, "libraries": [],
                        "message": "Aucune librairie configurée (Settings > Librairies)"})
    result = []
    for lib in libraries:
        if not os.path.isdir(lib["path"]):
            continue
        try:
            series = sorted([
                e for e in os.listdir(lib["path"])
                if os.path.isdir(os.path.join(lib["path"], e))
            ])
        except Exception:
            series = []
        result.append({
            "id":     lib["id"],
            "name":   lib["name"],
            "path":   lib["path"],
            "series": series,
        })
    return jsonify({"ok": True, "libraries": result})

@app.route("/api/queue/detect-missing", methods=["POST"])
def api_detect_missing():
    """
    Détecte les tomes manquants en analysant le dossier de destination sur disque.
    Ne dépend plus de Komga — lit directement les sous-dossiers de download_dir.
    """
    if _task["running"]:
        return jsonify({"ok": False, "message": "Tâche déjà en cours"})

    d          = request.json or {}
    lib_id     = d.get("lib_id")       # None = toutes les librairies
    serie_name = d.get("serie_name")   # None = toutes les séries de la lib
    mode       = d.get("mode")         # None = tout, "missing" ou "upgrade"

    libraries = lib_mgr.get_libraries()
    if not libraries:
        return jsonify({"ok": False,
                        "message": "Aucune librairie configurée (Settings > Librairies)"})

    # Filtre sur une librairie spécifique si demandé
    if lib_id:
        libraries = [l for l in libraries if l["id"] == lib_id]
        if not libraries:
            return jsonify({"ok": False, "message": f"Librairie '{lib_id}' introuvable"})

    def _run():
        try:
            import folder_scanner
            def _progress(label):
                _set_task(running=True, label=label)

            total_added = 0
            all_details = []

            for lib in libraries:
                lib_path = lib["path"]
                lib_name = lib["name"]

                if not os.path.isdir(lib_path):
                    app.logger.warning(f"[DetectMissing] Librairie '{lib_name}' introuvable : {lib_path}")
                    continue

                _set_task(running=True, label=f"Analyse librairie '{lib_name}'…")
                result = folder_scanner.detect_missing_from_disk(
                    lib_path,
                    progress_cb=_progress,
                    serie_filter=serie_name,
                    mode=mode,
                )
                total_added += result.get("added", 0)
                all_details.extend(result.get("details", []))

            _set_task(running=False,
                      label=f"Terminé — {total_added} tome(s) manquant(s) détecté(s)",
                      results=all_details)
        except Exception as e:
            app.logger.error(f"[DetectMissing] {e}")
            _set_task(running=False, label=f"Erreur : {str(e)[:80]}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/queue/apply-filters", methods=["POST"])
def api_queue_apply_filters():
    """
    Purge de la queue les items pending qui ne passent plus les filtres
    must_contain / must_not_contain actuels.
    """
    items   = queue_manager.get_queue()
    removed = 0
    kept    = []
    for item in items:
        if item.get("status") != "pending":
            kept.append(item)
            continue
        fn = item.get("filename", item.get("url", ""))
        ok, reason = profiles.passes_filters(fn)
        if ok:
            kept.append(item)
        else:
            removed += 1
            config.add_log(f"[Filtre] Retiré de la queue : {fn} ({reason})", "info")

    if removed:
        import queue_manager as _qm
        with _qm._queue_lock:
            _qm._save(kept)

    return jsonify({"ok": True, "removed": removed,
                    "message": f"{removed} item(s) retiré(s) de la queue"})

@app.route("/api/queue/generate-collection", methods=["POST"])
def api_generate_collection():
    """Génère un .emulecollection avec tous les items pending de la queue."""
    fp    = queue_manager.generate_emulecollection(label="manual")
    links = sum(1 for line in open(fp) if line.startswith("ed2k://"))
    if links == 0:
        return jsonify({"ok": False, "message": "Aucun item en attente dans la queue"})
    return jsonify({
        "ok":       True,
        "filename": os.path.basename(fp),
        "links":    links,
    })


@app.route("/api/queue/collections/download/<collection_type>")
def api_download_collection_zip(collection_type):
    """
    Télécharge un ZIP contenant toutes les parties .emulecollection
    d'un type donné : "ADD", "UPGRADE", ou "all".
    """
    import zipfile, io
    if collection_type not in ("ADD", "UPGRADE", "all"):
        return jsonify({"error": "Type invalide"}), 400

    emule_dir = queue_manager.EMULE_DIR
    if not os.path.isdir(emule_dir):
        return jsonify({"error": "Dossier emulecollections introuvable"}), 404

    files = []
    for fn in sorted(os.listdir(emule_dir)):
        if not fn.endswith(".emulecollection"):
            continue
        if collection_type == "all":
            files.append(fn)
        elif collection_type.upper() in fn.upper():
            files.append(fn)

    if not files:
        return jsonify({"error": f"Aucun fichier {collection_type} trouvé"}), 404

    if len(files) == 1:
        # Un seul fichier → téléchargement direct
        from flask import send_file
        return send_file(
            os.path.join(emule_dir, files[0]),
            as_attachment=True,
            download_name=files[0]
        )

    # Plusieurs fichiers → ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in files:
            zf.write(os.path.join(emule_dir, fn), fn)
    buf.seek(0)
    from flask import send_file
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"mangaarr_{collection_type}_{__import__('datetime').datetime.now().strftime('%Y%m%d')}.zip"
    )

@app.route("/api/queue/collections")
def api_list_collections():
    return jsonify({"files": queue_manager.list_emulecollections()})

@app.route("/api/queue/collections/<filename>")
def api_download_collection(filename):
    import re as _re
    if not _re.match(r'^[\w\.\-]+\.emulecollection$', filename):
        return jsonify({"error": "Nom invalide"}), 400
    fp = os.path.join(queue_manager.EMULE_DIR, filename)
    if not os.path.isfile(fp):
        return jsonify({"error": "Fichier introuvable"}), 404
    from flask import send_file
    return send_file(fp, as_attachment=True)

@app.route("/api/queue/collections/<filename>", methods=["DELETE"])
def api_delete_collection(filename):
    import re as _re
    if not _re.match(r'^[\w\.\-]+\.emulecollection$', filename):
        return jsonify({"ok": False, "message": "Nom invalide"}), 400
    fp = os.path.join(queue_manager.EMULE_DIR, filename)
    if not os.path.isfile(fp):
        return jsonify({"ok": False, "message": "Fichier introuvable"}), 404
    try:
        os.remove(fp)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@app.route("/api/queue/status/<filehash>", methods=["PATCH"])
def api_update_queue_status(filehash):
    status = request.json.get("status", "")
    if status not in ("pending", "downloading", "done"):
        return jsonify({"ok": False, "message": "Statut invalide"}), 400
    queue_manager.update_status(filehash, status)
    return jsonify({"ok": True})


@app.route("/api/queue/item", methods=["PATCH"])
def api_update_queue_item():
    """
    Met à jour tome_number et/ou tomes d'un item de queue (eMule ou torrent).
    Body : { key_field, key_value, tome_number, tomes }
    Identifie l'item par filehash (eMule) ou torrent_link+filename (torrent).
    """
    d          = request.json or {}
    key_field  = d.get("key_field", "filehash")   # "filehash" | "torrent_link" | "filename"
    key_value  = d.get("key_value", "")
    tome_number = d.get("tome_number")
    tomes       = d.get("tomes")                   # liste d'entiers ou None

    if not key_value:
        return jsonify({"ok": False, "message": "key_value requis"})

    with queue_manager._queue_lock:
        items = queue_manager._load()
        updated = False
        for item in items:
            if item.get(key_field) == key_value:
                if tome_number is not None:
                    item["tome_number"] = str(tome_number)
                if tomes is not None:
                    item["tomes"] = tomes
                    # Recalcule tome_number lisible si tomes fournis
                    if tomes:
                        if len(tomes) == 1:
                            item["tome_number"] = f"T{tomes[0]:02d}"
                        else:
                            item["tome_number"] = f"T{tomes[0]:02d}-T{tomes[-1]:02d}"
                updated = True
        if updated:
            queue_manager._save(items)

    return jsonify({"ok": updated, "message": "Mis à jour" if updated else "Item introuvable"})


@app.route("/api/queue/items", methods=["DELETE"])
def api_delete_queue_items():
    """
    Supprime des items de la queue eMule par leurs filehashes.
    Body : { filehashes: ["hash1", "hash2", ...] }
    Met aussi à jour les .emulecollection / .txt existants.
    Si un client aMule est configuré, annule aussi dans aMule.
    """
    d          = request.json or {}
    filehashes = d.get("filehashes", [])
    if not filehashes:
        return jsonify({"ok": False, "message": "Aucun filehash fourni"})
    result = queue_manager.delete_items(filehashes)
    # Annulation aMule en arrière-plan si client actif
    amule_cl = _get_amule_client()
    if amule_cl:
        def _cancel():
            amule_client.cancel_hashes_batch(amule_cl, filehashes)
        threading.Thread(target=_cancel, daemon=True).start()
    return jsonify({"ok": True, **result})


@app.route("/api/torrent/queue/items", methods=["DELETE"])
def api_delete_torrent_queue_items():
    """
    Supprime des items torrent de la queue par leur torrent_link ou filename.
    Body : { keys: ["torrent_link_or_filename", ...] }
    """
    d    = request.json or {}
    keys = set(d.get("keys", []))
    if not keys:
        return jsonify({"ok": False, "message": "Aucune clé fournie"})
    with queue_manager._queue_lock:
        items   = queue_manager._load()
        kept    = []
        deleted = 0
        for i in items:
            if i.get("source") == "torrent":
                item_key = i.get("torrent_link") or i.get("filename") or ""
                if item_key in keys:
                    deleted += 1
                    continue
            kept.append(i)
        queue_manager._save(kept)
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/queue/resolve-pending", methods=["POST"])
def api_resolve_pending():
    """
    Résout une action en attente (upgrade_conflict).
    Body : {
      filehash      : str  (eMule)     ← l'un ou l'autre
      torrent_link  : str  (torrent)
      replace_tomes : [int]   ← numéros de tomes à remplacer
      skip_all      : bool    ← si True, ignore tout (ne copie rien)
    }
    """
    import file_organizer as _fo
    import torrent_watcher as _tw
    from datetime import datetime

    d             = request.json or {}
    filehash      = d.get("filehash", "").strip()
    torrent_link  = d.get("torrent_link", "").strip()
    series_name   = d.get("series_name", "").strip()
    filename      = d.get("filename", "").strip()
    replace_tomes = set(d.get("replace_tomes", []))
    skip_all      = d.get("skip_all", False)

    # ── Trouve l'item ──
    with queue_manager._queue_lock:
        items    = queue_manager._load()
        item_ref = None
        for i in items:
            if filehash and i.get("filehash") == filehash:
                item_ref = i; break
            if torrent_link and i.get("torrent_link") == torrent_link:
                item_ref = i; break
        if not item_ref:
            # Fallback : correspondance par filename + series_name
            for i in items:
                fn_m = filename and i.get("filename") == filename
                sn_m = series_name and i.get("series_name") == series_name
                if fn_m and sn_m and i.get("status") == "action_pending":
                    item_ref = i; break
                if fn_m and not series_name and i.get("status") == "action_pending":
                    item_ref = i; break
    if not item_ref:
        return jsonify({"ok": False, "message": "Item introuvable"})

    pending = item_ref.get("pending_action", {})
    if not pending:
        return jsonify({"ok": False, "message": "Aucune action en attente"})

    local_path = pending.get("local_path", "")
    conflicts  = pending.get("conflicts", [])
    conflict_map = {c["tome"]: c["current_file"] for c in conflicts}

    now     = datetime.now()
    updated = 0
    errors  = []

    if skip_all:
        # L'utilisateur refuse tout remplacement — marque done sans copier
        _fw_mark(item_ref, filehash, torrent_link, "done", {"processed_at": now.isoformat(timespec="seconds"), "action": "skipped"})
        return jsonify({"ok": True, "message": "Action annulée — aucun fichier copié"})

    # ── Traite les fichiers ──
    MANGA_EXTS = {".cbz", ".cbr", ".pdf", ".zip"}
    files_to_process = []
    if os.path.isfile(local_path) and os.path.splitext(local_path)[1].lower() in MANGA_EXTS:
        files_to_process = [local_path]
    elif os.path.isdir(local_path):
        import renamer as _r
        files_to_process = sorted([
            os.path.join(local_path, f)
            for f in os.listdir(local_path)
            if os.path.splitext(f)[1].lower() in MANGA_EXTS and not f.endswith(".!qB")
        ])

    series_name = item_ref.get("series_name", "")

    for fp in files_to_process:
        import renamer as _r
        fname    = os.path.basename(fp)
        tome_tag = _r.detect_tome(fname)
        n        = int(re.sub(r"[^0-9]", "", tome_tag) or "0") if tome_tag else 0

        if n in conflict_map:
            if n not in replace_tomes:
                # Tome en conflit non sélectionné → skip
                continue
            # Remplace l'existant
            action     = "upgrade"
            owned_file = conflict_map[n]
        else:
            # Tome nouveau → copie normale
            action     = "missing"
            owned_file = ""

        tome_str = str(n) if n else item_ref.get("tome_number", "")
        result   = _fo.organize_file(item={
            "local_path":   fp,
            "series_name":  series_name,
            "tome_number":  tome_str,
            "filename":     fname,
            "action":       action,
            "owned_file":   owned_file,
            "series_exact": item_ref.get("series_exact", False),
        })
        if result.get("ok"):
            updated += 1
        else:
            errors.append(f"{fname}: {result.get('message', '')}")

    # ── Marque done ──
    history = {
        "processed_at":  now.isoformat(timespec="seconds"),
        "action":        "resolve_pending",
        "replaced_tomes": list(replace_tomes),
    }
    _fw_mark(item_ref, filehash, torrent_link, "done", history)

    if updated:
        config.add_log(f"[Action] {series_name} : {updated} fichier(s) organisé(s) après confirmation", "info")
    return jsonify({"ok": True, "message": f"{updated} fichier(s) organisé(s)", "errors": errors})


def _fw_mark(item, filehash, torrent_link, status, history):
    """Met à jour le statut d'un item (eMule ou torrent) et retire pending_action."""
    from datetime import datetime
    with queue_manager._queue_lock:
        items = queue_manager._load()
        for i in items:
            match = (filehash and i.get("filehash") == filehash) or \
                    (torrent_link and i.get("torrent_link") == torrent_link) or \
                    (i.get("filename") == item.get("filename") and
                     i.get("series_name") == item.get("series_name"))
            if match:
                i["status"]  = status
                if status == "done":
                    i["done_at"] = datetime.now().isoformat(timespec="seconds")
                if history:
                    i["history"] = history
                i.pop("pending_action", None)
                break
        queue_manager._save(items)


@app.route("/api/queue/force-organize", methods=["POST"])
def api_force_organize():
    """
    Force la copie+renommage+conversion d'un torrent terminé depuis un chemin manuel.
    Body : { torrent_link: str, series_name: str, tome_number: str, tomes: [], vol_type: str,
             local_path: str  ← chemin du fichier ou dossier téléchargé }
    Nécessite force_organize_enabled = True dans media_management.
    """
    cfg = config.load()
    mm  = cfg.get("media_management", {})
    if not mm.get("force_organize_enabled", False):
        return jsonify({"ok": False, "message": "Option 'Forcer l'organisation' désactivée dans Media Management"})

    d           = request.json or {}
    local_path  = d.get("local_path", "").strip()
    series_name = d.get("series_name", "").strip()
    tome_number = d.get("tome_number", "").strip()
    tomes       = d.get("tomes", [])
    vol_type    = d.get("vol_type", "single")
    replace_tomes = d.get("replace_tomes", [])

    if not local_path or not os.path.exists(local_path):
        return jsonify({"ok": False, "message": f"Chemin introuvable : {local_path}"})
    if not series_name:
        return jsonify({"ok": False, "message": "Nom de série requis"})

    import torrent_watcher as _tw
    import file_organizer as _fo
    from datetime import datetime

    # Construit un item factice pour réutiliser _process_matched
    fake_item = {
        "source":       "torrent",
        "series_name":  series_name,
        "tome_number":  tome_number,
        "tomes":        tomes,
        "vol_type":     vol_type,
        "replace_tomes": replace_tomes,
        "torrent_link": d.get("torrent_link", ""),
        "filename":     d.get("filename", ""),
    }

    updated, errors = _tw._process_matched(fake_item, local_path, _fo, datetime.now())

    if updated:
        # Met à jour le statut dans la queue si torrent_link est connu
        if fake_item["torrent_link"] or fake_item["filename"]:
            _tw._update_torrent_status(fake_item, "done", {
                "source_file":  os.path.basename(local_path),
                "processed_at": datetime.now().isoformat(timespec="seconds"),
                "forced":       True,
            })
        return jsonify({"ok": True, "message": f"{updated} fichier(s) organisé(s) avec succès"})
    else:
        return jsonify({"ok": False, "message": errors[0] if errors else "Aucun fichier traité"})


# ════════════════════════════════════════════════════════
# EXPLORATEUR DE FICHIERS (pour force-organize)
# ════════════════════════════════════════════════════════

@app.route("/api/files/browse")
def api_files_browse():
    """
    Retourne le contenu d'un répertoire pour le navigateur de fichiers.
    ?path=/some/dir  (défaut : MANGAARR_QBT_WATCH ou /)
    Retourne : { path, parent, items: [{name, type, size, ext}] }
    """
    import stat as _stat
    watch_root  = os.environ.get("MANGAARR_QBT_WATCH", "").strip() or "/"
    req_path    = request.args.get("path", "").strip() or watch_root
    # Résolution sûre — empêche les remontées hors de /
    req_path    = os.path.realpath(req_path)

    if not os.path.isdir(req_path):
        return jsonify({"ok": False, "message": f"Répertoire introuvable : {req_path}"})

    items = []
    try:
        for name in sorted(os.listdir(req_path), key=lambda x: (not os.path.isdir(os.path.join(req_path, x)), x.lower())):
            if name.startswith(".") or name.endswith(".!qB"):
                continue
            full = os.path.join(req_path, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            is_dir = _stat.S_ISDIR(st.st_mode)
            ext    = os.path.splitext(name)[1].lower() if not is_dir else ""
            items.append({
                "name":  name,
                "type":  "dir" if is_dir else "file",
                "size":  st.st_size if not is_dir else 0,
                "ext":   ext,
            })
    except PermissionError:
        return jsonify({"ok": False, "message": "Accès refusé"})

    parent = os.path.dirname(req_path) if req_path != "/" else None
    return jsonify({
        "ok":      True,
        "path":    req_path,
        "parent":  parent,
        "root":    watch_root,
        "items":   items,
    })


# ════════════════════════════════════════════════════════
# SETTINGS — INCOMING
# ════════════════════════════════════════════════════════

@app.route("/api/settings/incoming", methods=["GET"])
def api_get_incoming():
    cfg = config.load()
    mm  = cfg.get("media_management", {})
    return jsonify({
        "emule_incoming_dir":   cfg.get("emule_incoming_dir", ""),
        "auto_organize":        mm.get("auto_organize",    False),
        "auto_convert":         mm.get("auto_convert_cbr", True),
        "auto_rename_incoming": mm.get("auto_rename",      True),
    })

@app.route("/api/settings/incoming", methods=["POST"])
def api_set_incoming():
    d   = request.json or {}
    cfg = config.load()
    mm  = cfg.setdefault("media_management", {})
    if "emule_incoming_dir" in d:
        cfg["emule_incoming_dir"] = d["emule_incoming_dir"]
    if "auto_organize" in d:
        mm["auto_organize"] = bool(d["auto_organize"])
    if "auto_convert" in d:
        # Contrôle CBR et PDF en même temps
        mm["auto_convert_cbr"] = bool(d["auto_convert"])
        mm["auto_convert_pdf"] = bool(d["auto_convert"])
    if "auto_rename_incoming" in d:
        mm["auto_rename"] = bool(d["auto_rename_incoming"])
    config.save(cfg)
    return jsonify({"ok": True})

# ════════════════════════════════════════════════════════
# QUEUE — SCAN INCOMING (met à jour les statuts "done")
# ════════════════════════════════════════════════════════

def _do_scan_incoming() -> dict:
    """
    Logique de scan Incoming — appelable depuis le watcher serveur OU la route HTTP.
    Correspondance par NOM EXACT du fichier annoncé dans la queue.
    Aucun matching fuzzy : si le fichier exact n'est pas là, on attend.
    Retourne {"ok", "updated", "message", "errors"}.
    """
    import file_organizer as _fo

    incoming_dir = config.get("emule_incoming_dir", "")
    if not incoming_dir or not os.path.isdir(incoming_dir):
        return {"ok": False, "message": "Dossier Incoming non configuré ou introuvable", "updated": 0, "errors": []}

    # Index des fichiers présents dans Incoming par nom exact (lowercase)
    # UN SEUL niveau de correspondance : le nom de fichier doit être IDENTIQUE
    # à celui annoncé dans la queue (insensible à la casse uniquement).
    # Aucun fuzzy, aucune normalisation — si le fichier exact n'est pas là, on attend.
    incoming_exact = {}  # nom_lowercase → full_path

    for fn in os.listdir(incoming_dir):
        if not fn.lower().endswith((".cbz", ".cbr", ".pdf", ".zip")):
            continue
        incoming_exact[fn.lower()] = os.path.join(incoming_dir, fn)

    if not incoming_exact:
        return {"ok": True, "message": "Dossier vide", "updated": 0, "errors": []}

    items   = queue_manager.get_queue()
    updated = 0
    errors  = []

    for item in items:
        if item.get("status") in ("done", "action_pending"):
            continue

        expected_fn = item.get("filename", "").strip()
        if not expected_fn:
            continue

        # Correspondance NOM EXACT (insensible à la casse uniquement)
        matched_path = incoming_exact.get(expected_fn.lower())

        if not matched_path:
            # Fichier pas encore arrivé → on attend le prochain scan
            continue

        app.logger.info(f"[Incoming] ✓ Fichier exact : {expected_fn}")

        action     = item.get("action", "missing")
        owned_file = item.get("owned_file", "")

        # ── Si remplacement (upgrade) → demande confirmation (action_pending) ──
        if action == "upgrade" and owned_file:
            try:
                tone_num = str(item.get("tome_number", "")).lstrip("Tt").lstrip("0") or "0"
                conflicts = [{
                    "tome":         int(tone_num) if tone_num.isdigit() else 0,
                    "new_file":     expected_fn,
                    "current_file": owned_file,
                }]
                with queue_manager._queue_lock:
                    _items = queue_manager._load()
                    for _i in _items:
                        if _i.get("filehash") == item.get("filehash"):
                            _i["status"] = "action_pending"
                            _i["pending_action"] = {
                                "type":       "upgrade_conflict",
                                "local_path": matched_path,
                                "conflicts":  conflicts,
                            }
                            break
                    queue_manager._save(_items)
                app.logger.info(f"[Incoming] Action en attente (upgrade) : {expected_fn}")
                continue
            except Exception as e:
                app.logger.error(f"[Incoming] Erreur action_pending : {e}", exc_info=True)

        try:
            result = _fo.organize_file(item={
                "local_path":   matched_path,
                "series_name":  item.get("series_name", ""),
                "tome_number":  item.get("tome_number", ""),
                "filename":     item.get("filename", ""),
                "action":       action,
                "owned_file":   owned_file,
                "series_exact": item.get("series_exact", False),
            })
            if result.get("ok"):
                dest = result.get("dest_path", "")
                history = {
                    "source_file":    os.path.basename(matched_path),
                    "source_path":    matched_path,
                    "dest_path":      dest,
                    "dest_filename":  os.path.basename(dest),
                    "processed_at":   __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                    "action":         action,
                    "owned_replaced": owned_file or None,
                }
                queue_manager.update_status(item.get("filehash", ""), "done", history=history)
                updated += 1
                config.add_log(
                    f"[Incoming] {item.get('series_name')} T{str(item.get('tome_number','')).lstrip('T')}"
                    f" : {os.path.basename(matched_path)} → {os.path.basename(dest)}", "info"
                )
            else:
                errors.append(f"{item.get('filename')}: {result.get('message','')}")
                config.add_log(f"[Incoming] Échec copie : {result.get('message','')}", "error")
        except Exception as e:
            errors.append(str(e))
            config.add_log(f"[Incoming] Exception : {e}", "error")
            app.logger.error(f"[Incoming] Exception : {e}", exc_info=True)

    msg = f"{updated} fichier(s) traité(s)"
    if errors:
        msg += f" ({len(errors)} erreur(s))"
    return {"ok": True, "updated": updated, "message": msg, "errors": errors}


@app.route("/api/queue/scan-incoming")
def api_scan_incoming():
    """Route HTTP — délègue à _do_scan_incoming() pour éviter la duplication de code."""
    return jsonify(_do_scan_incoming())


def _normalize_fn(fn: str) -> str:
    """Normalise un nom de fichier pour comparaison (sans ext, alphanum lowercase)."""
    import unicodedata
    fn = os.path.splitext(fn)[0]
    fn = unicodedata.normalize("NFD", fn)
    fn = "".join(c for c in fn if unicodedata.category(c) != "Mn")
    fn = fn.lower()
    fn = re.sub(r"[^a-z0-9]", "", fn)
    return fn


# ════════════════════════════════════════════════════════
# METADATA — SYNC MANUEL + INTERVALLE AUTO
# ════════════════════════════════════════════════════════

@app.route("/api/metadata/sync", methods=["POST"])
def api_metadata_sync():
    """
    Synchronise les métadonnées pour toutes les bibliothèques liées à des sources.
    Force le re-fetch des séries sans cache (ou skip si cache existant selon le paramètre).
    """
    d          = request.json or {}
    force      = d.get("force", False)  # True = réécrit même si déjà en cache
    library_id = d.get("library_id")   # None = toutes les bibliothèques liées

    if _task["running"]:
        return jsonify({"ok": False, "message": "Tâche déjà en cours"})

    def _run():
        try:
            _set_task(running=True, label="Chargement des données MangaDB…")
            import requests as _req, pandas as _pd

            sources = config.get("metadata_sources", [])
            if not sources:
                _set_task(running=False, label="Aucune source configurée dans Settings > Metadata")
                return

            total_updated = 0

            for source in sources:
                src_url  = source.get("url", "").rstrip("/")
                src_name = source.get("name", "source")

                # Bibliothèques à syncer : celles liées à la source, ou toutes
                lib_ids = source.get("library_ids", [])
                if library_id:
                    lib_ids = [l for l in lib_ids if l == library_id]
                if not lib_ids:
                    # Aucune lib liée → prend toutes les librairies locales
                    lib_ids = [l["id"] for l in lib_mgr.get_libraries()]

                if not lib_ids:
                    _set_task(running=False, label="Aucune librairie configurée (Settings > Librairies)")
                    return

                # Charge le catalogue MangaDB une seule fois
                _set_task(label=f"Chargement catalogue {src_name}…")
                csv_df = None
                try:
                    r = _req.get(f"{src_url}/api/series", timeout=60)
                    if r.status_code == 200:
                        data = r.json().get("series", [])
                        if data:
                            csv_df = _pd.DataFrame(data)
                            app.logger.info(f"[MetaSync] Catalogue chargé : {len(data)} séries")
                except Exception as e:
                    app.logger.warning(f"[MetaSync] Impossible de charger le catalogue : {e}")

                # Traite chaque librairie locale
                for lib_id in lib_ids:
                    _set_task(label=f"Scan librairie {lib_id}…")
                    local_series = lib_mgr.scan_library(lib_id)
                    total_s      = len(local_series)
                    app.logger.info(f"[MetaSync] {total_s} séries dans lib {lib_id}")

                    for i, s in enumerate(local_series, 1):
                        sid  = s["id"]
                        name = s["name"]
                        _set_task(label=f"Sync {i}/{total_s} — {name[:40]}…")

                        # Skip si déjà en cache et pas force
                        if not force:
                            existing = cache_mod.get_series_meta(lib_id, sid)
                            if existing is not None:
                                continue

                        # Match dans le catalogue
                        meta = {}
                        if csv_df is not None:
                            row = cache_mod.find_in_csv(name, csv_df)
                            if row:
                                meta = cache_mod._build_meta(row)

                        # Fallback : requête individuelle MangaDB
                        if not meta:
                            try:
                                meta = mangadb_client.find_best_match(name, source.get("id")) or {}
                            except Exception:
                                meta = {}

                        if meta:
                            cache_mod.set_series_meta(lib_id, sid, meta)
                            total_updated += 1

            _set_task(running=False, label=f"Sync terminée — {total_updated} série(s) enrichie(s)")

        except Exception as e:
            app.logger.error(f"[MetaSync] Erreur fatale : {e}")
            _set_task(running=False, label=f"Erreur : {str(e)[:80]}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Synchronisation lancée"})


@app.route("/api/metadata/sync-interval", methods=["GET"])
def api_get_sync_interval():
    return jsonify({"interval_hours": config.get("meta_sync_interval_hours", 24)})

@app.route("/api/metadata/sync-interval", methods=["POST"])
def api_set_sync_interval():
    hours = int(request.json.get("interval_hours", 24))
    hours = max(0, min(168, hours))
    config.set_value("meta_sync_interval_hours", hours)
    _schedule_meta_sync()  # Replanifie avec le nouvel intervalle
    return jsonify({"ok": True, "interval_hours": hours})



# ══════════════════════════════════════════════════════════════════
# SCHEDULER — SYNCHRONISATION AUTOMATIQUE METADATA
# ══════════════════════════════════════════════════════════════════

_meta_sync_timer = None

def _schedule_meta_sync():
    """Lance un timer pour la prochaine synchronisation auto metadata."""
    global _meta_sync_timer
    if _meta_sync_timer:
        _meta_sync_timer.cancel()
    hours = config.get("meta_sync_interval_hours", 0)
    if not hours:
        return  # 0 = désactivé
    def _do_sync():
        global _meta_sync_timer
        # Lance une synchro si aucune tâche en cours
        if not _task.get("running"):
            app.logger.info("[MetaSync] Synchronisation automatique démarrée")
            with app.app_context():
                import requests as _req2
                try:
                    _req2.post(
                        f"http://localhost:{config.get('port', 7474)}/api/metadata/sync",
                        json={"force": False}, timeout=5
                    )
                except Exception:
                    pass
        # Replanifie le prochain
        _schedule_meta_sync()
    _meta_sync_timer = threading.Timer(hours * 3600, _do_sync)
    _meta_sync_timer.daemon = True
    _meta_sync_timer.start()

def start_meta_sync_scheduler():
    """Démarre le scheduler de sync metadata au lancement."""
    _schedule_meta_sync()

# ════════════════════════════════════════════════════════
# INCOMING WATCHER — côté serveur, indépendant du navigateur
# ════════════════════════════════════════════════════════

_incoming_watcher_thread = None
_incoming_watcher_stop   = threading.Event()

def _start_incoming_watcher():
    """
    Lance un thread de surveillance du dossier Incoming.
    Scanne automatiquement toutes les 60s (ou selon MANGAARR_INCOMING_INTERVAL).
    Complètement indépendant du navigateur — actif 24h/24.
    """
    global _incoming_watcher_thread, _incoming_watcher_stop

    interval = int(os.environ.get("MANGAARR_INCOMING_INTERVAL", "60"))  # secondes

    _incoming_watcher_stop.set()
    if _incoming_watcher_thread and _incoming_watcher_thread.is_alive():
        _incoming_watcher_thread.join(timeout=3)

    _incoming_watcher_stop = threading.Event()

    def _watch():
        app.logger.info(f"[IncomingWatcher] Démarré — scan toutes les {interval}s")
        while not _incoming_watcher_stop.wait(interval):
            try:
                incoming_dir = config.get("emule_incoming_dir", "")
                if not incoming_dir or not os.path.isdir(incoming_dir):
                    continue

                # Vérifie s'il y a des fichiers dans Incoming
                files = [
                    f for f in os.listdir(incoming_dir)
                    if f.lower().endswith((".cbz", ".cbr", ".pdf", ".zip"))
                ]
                if not files:
                    continue

                # Appelle directement la logique de scan (sans passer par HTTP)
                data = _do_scan_incoming()
                if data.get("updated", 0) > 0:
                    app.logger.info(
                        f"[IncomingWatcher] {data['updated']} fichier(s) traité(s)"
                    )
                if data.get("errors"):
                    for err in data["errors"]:
                        app.logger.warning(f"[IncomingWatcher] {err}")
            except Exception as e:
                app.logger.error(f"[IncomingWatcher] Erreur : {e}")

    _incoming_watcher_thread = threading.Thread(target=_watch, daemon=True, name="IncomingWatcher")
    _incoming_watcher_thread.start()


def _apply_env_config():
    """
    Applique les variables d'environnement Docker à la config au démarrage.
    Les env vars ont TOUJOURS priorité sur la config sauvegardée.
    Ainsi l'utilisateur n'a jamais à re-saisir les chemins déjà définis
    dans docker-compose.yml / unraid template.
    """
    cfg     = config.load()
    changed = False

    # ── Chemins ────────────────────────────────────────────
    # MANGAARR_INCOMING : dossier eMule Incoming (/incoming dans le container)
    v = os.environ.get("MANGAARR_INCOMING", "")
    if v and cfg.get("emule_incoming_dir") != v:
        cfg["emule_incoming_dir"] = v
        changed = True

    # MANGAARR_DEST : dossier de destination des séries (/manga dans le container)
    v = os.environ.get("MANGAARR_DEST", "")
    if v and cfg.get("download_dir") != v:
        cfg["download_dir"] = v
        changed = True

    # MANGAARR_CACHE : dossier cache metadata + covers
    v = os.environ.get("MANGAARR_CACHE", "")
    if v:
        cfg["_cache_dir"] = v
        import cache as _c
        _c.CACHE_FILE = os.path.join(v, "metadata_cache.json")
        os.makedirs(v, exist_ok=True)
        changed = True

    # MANGAARR_EMULE : dossier .emulecollection
    v = os.environ.get("MANGAARR_EMULE", "")
    if v:
        cfg["_emule_dir"] = v
        import queue_manager as _qm
        _qm.EMULE_DIR = v
        os.makedirs(v, exist_ok=True)
        changed = True

    if changed:
        config.save(cfg)

    # Log de démarrage pour vérification
    print(f"  incoming_dir  : {cfg.get('emule_incoming_dir','(non configuré)')}")
    print(f"  download_dir  : {cfg.get('download_dir','(non configuré)')}")
    print(f"  cache_dir     : {cfg.get('_cache_dir','./cache')}")
    print(f"  emule_dir     : {cfg.get('_emule_dir','./emulecollections')}")


if __name__ == "__main__":
    port = int(os.environ.get("MANGAARR_PORT", 7474))
    print(f"MangaArr → http://localhost:{port}")
    print("Chemins configurés :")
    _apply_env_config()

    ebdz_scraper.start_scheduler()
    start_meta_sync_scheduler()

    # Watcher librairies (détection nouveaux fichiers)
    watcher_interval = config.get("watcher_interval", 0)
    if watcher_interval > 0:
        lib_mgr.start_watcher(watcher_interval)

    # Watcher Incoming — tourne côté SERVEUR, indépendant du navigateur
    _start_incoming_watcher()

    # Watcher torrents qBittorrent — traitement automatique des téléchargements terminés
    import torrent_watcher as _tw
    _tw.start_watcher()

    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

