"""
telegram_client.py — Client Telegram (Telethon) pour MangaArr
- Authentification via numéro de téléphone (API MTProto officielle)
- Listage des canaux rejoints
- Recherche de fichiers .cbz/.cbr dans les canaux sélectionnés (avec cache par canal)
- Téléchargement de fichiers avec suivi de progression
"""
import os, re, asyncio, logging, threading, json, time

log = logging.getLogger("mangaarr.telegram_client")

# Stockage temporaire des phone_code_hash en attente de validation
_pending_auth: dict = {}  # {indexer_id: {phone, phone_code_hash}}

# Verrou pour éviter plusieurs téléchargements simultanés sur le même indexer
_download_locks: dict = {}

# Suivi des builds de cache en cours {channel_id: True}
_cache_building: dict = {}
_cache_lock = threading.Lock()

CACHE_TTL_HOURS = 24


# ═══════════════════════════════════════════════════
# HELPERS ASYNC
# ═══════════════════════════════════════════════════

def _run(coro):
    """Exécute une coroutine de façon synchrone dans une boucle dédiée."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def _make_client(cfg: dict, session_override: str = None):
    """Crée et connecte un TelegramClient Telethon."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id   = int(cfg.get("api_id", 0))
    api_hash = cfg.get("api_hash", "").strip()
    sess_str = session_override if session_override is not None else cfg.get("session_string", "")
    client   = TelegramClient(StringSession(sess_str), api_id, api_hash)
    await client.connect()
    return client


# ═══════════════════════════════════════════════════
# AUTHENTIFICATION
# ═══════════════════════════════════════════════════

def send_code(cfg: dict) -> dict:
    """Envoie le code de vérification SMS/Telegram au numéro configuré."""
    async def _inner():
        client = await _make_client(cfg, "")
        try:
            result = await client.send_code_request(cfg.get("phone", ""))
            # Sauvegarde la session partielle (contient le DC de connexion)
            session_after = client.session.save()
            return {
                "ok":               True,
                "phone_code_hash":  result.phone_code_hash,
                "partial_session":  session_after,
            }
        finally:
            await client.disconnect()

    try:
        result = _run(_inner())
        if result.get("ok"):
            _pending_auth[cfg.get("id", "")] = {
                "phone":           cfg.get("phone", ""),
                "phone_code_hash": result["phone_code_hash"],
                "partial_session": result["partial_session"],
            }
        return {"ok": result.get("ok", False), "message": result.get("message", "")}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def sign_in(cfg: dict, code: str, password: str = "") -> dict:
    """Finalise l'authentification avec le code reçu. Retourne session_string."""
    pending = _pending_auth.get(cfg.get("id", ""))
    if not pending:
        return {"ok": False, "message": "Aucune auth en cours. Renvoyez le code d'abord."}

    async def _inner():
        # Réutilise la session partielle pour rester sur le même DC Telegram
        partial = pending.get("partial_session", "")
        client = await _make_client(cfg, partial)
        try:
            try:
                await client.sign_in(
                    phone=pending["phone"],
                    code=code,
                    phone_code_hash=pending["phone_code_hash"],
                )
            except Exception as e:
                # 2FA — si mot de passe requis
                if "two-steps" in str(e).lower() or "password" in str(e).lower():
                    if not password:
                        return {"ok": False, "message": "2FA requis : entrez votre mot de passe Telegram", "need_2fa": True}
                    from telethon.errors import SessionPasswordNeededError
                    await client.sign_in(password=password)
                else:
                    raise
            session_string = client.session.save()
            me = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "?"
            return {"ok": True, "session_string": session_string, "name": name}
        finally:
            await client.disconnect()

    try:
        result = _run(_inner())
        if result.get("ok"):
            _pending_auth.pop(cfg.get("id", ""), None)
        return result
    except Exception as e:
        return {"ok": False, "message": str(e)}


def test_connection(cfg: dict) -> dict:
    """Vérifie si la session Telegram est valide."""
    async def _inner():
        client = await _make_client(cfg)
        try:
            if not await client.is_user_authorized():
                return {"ok": False, "message": "Session invalide ou expirée. Reconnectez-vous."}
            me   = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "?"
            return {"ok": True, "message": f"Connecté en tant que {name}"}
        finally:
            await client.disconnect()

    try:
        return _run(_inner())
    except Exception as e:
        return {"ok": False, "message": str(e)}


# ═══════════════════════════════════════════════════
# CANAUX
# ═══════════════════════════════════════════════════

def get_channels(cfg: dict) -> dict:
    """Liste tous les canaux/groupes dont l'utilisateur est membre."""
    async def _inner():
        from telethon.tl.types import Channel, Chat

        client = await _make_client(cfg)
        try:
            if not await client.is_user_authorized():
                return {"ok": False, "message": "Session invalide", "channels": []}

            channels = []
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                if isinstance(entity, (Channel, Chat)):
                    channels.append({
                        "id":   str(entity.id),
                        "name": dialog.name or str(entity.id),
                        "type": "channel" if isinstance(entity, Channel) else "group",
                    })
            channels.sort(key=lambda x: x["name"].lower())
            return {"ok": True, "channels": channels}
        finally:
            await client.disconnect()

    try:
        return _run(_inner())
    except Exception as e:
        return {"ok": False, "message": str(e), "channels": []}


# ═══════════════════════════════════════════════════
# CACHE PAR CANAL
# ═══════════════════════════════════════════════════

def _cache_dir() -> str:
    base = os.environ.get("MANGAARR_CACHE", "/data/cache")
    d = os.path.join(base, "telegram_channels")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(channel_id: str) -> str:
    return os.path.join(_cache_dir(), f"{channel_id}.json")


def _load_cache(channel_id: str) -> dict | None:
    path = _cache_path(channel_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(channel_id: str, channel_name: str, files: list):
    data = {
        "channel_id":   channel_id,
        "channel_name": channel_name,
        "last_scan":    time.time(),
        "files":        files,
    }
    try:
        with open(_cache_path(channel_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log.warning("[Telegram] _save_cache %s : %s", channel_id, e)


def _cache_is_fresh(cache: dict) -> bool:
    last = cache.get("last_scan", 0)
    return (time.time() - last) < CACHE_TTL_HOURS * 3600


async def _build_cache_async(client, entity, channel_id: str, channel_name: str):
    """Scanne TOUT le canal et sauvegarde le cache. Appelé dans un thread dédié."""
    from telethon.tl.types import InputMessagesFilterDocument
    files = []
    try:
        async for msg in client.iter_messages(entity, filter=InputMessagesFilterDocument):
            if not msg.file:
                continue
            fname = (msg.file.name or "").strip()
            if not fname.lower().endswith((".cbz", ".cbr")):
                continue
            files.append({
                "filename":   fname,
                "message_id": msg.id,
                "size":       msg.file.size or 0,
                "date":       msg.date.isoformat() if msg.date else "",
            })
        _save_cache(channel_id, channel_name, files)
        log.info("[Telegram] Cache construit pour %s : %d fichiers", channel_name, len(files))
    except Exception as e:
        log.error("[Telegram] _build_cache_async %s : %s", channel_id, e)
    finally:
        with _cache_lock:
            _cache_building.pop(channel_id, None)


def build_channel_cache(cfg: dict, channel_ids: list) -> dict:
    """Reconstruit le cache pour les canaux donnés (scan complet, synchrone)."""
    async def _resolve_and_build():
        from telethon.tl.types import Channel, Chat
        client = await _make_client(cfg)
        try:
            if not await client.is_user_authorized():
                return {"ok": False, "message": "Session invalide"}
            target_ids = {str(i) for i in channel_ids}
            entity_map = {}
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                if not isinstance(entity, (Channel, Chat)):
                    continue
                eid = str(entity.id)
                if eid in target_ids:
                    entity_map[eid] = (entity, dialog.name or eid)
                if len(entity_map) == len(target_ids):
                    break

            launched = []
            for ch_id, (entity, ch_name) in entity_map.items():
                with _cache_lock:
                    if ch_id in _cache_building:
                        continue
                    _cache_building[ch_id] = True
                # Build cache inline (still in this async context)
                await _build_cache_async(client, entity, ch_id, ch_name)
                launched.append(ch_name)
            return {"ok": True, "launched": launched}
        finally:
            await client.disconnect()

    try:
        return _run(_resolve_and_build())
    except Exception as e:
        return {"ok": False, "message": str(e)}


def get_cache_status(channel_ids: list) -> dict:
    """Retourne l'état du cache pour chaque canal."""
    statuses = {}
    for ch_id in channel_ids:
        ch_id = str(ch_id)
        with _cache_lock:
            building = ch_id in _cache_building
        cache = _load_cache(ch_id)
        if building:
            statuses[ch_id] = {"status": "building"}
        elif cache is None:
            statuses[ch_id] = {"status": "missing"}
        elif not _cache_is_fresh(cache):
            statuses[ch_id] = {
                "status":     "stale",
                "file_count": len(cache.get("files", [])),
                "last_scan":  cache.get("last_scan", 0),
            }
        else:
            statuses[ch_id] = {
                "status":     "ok",
                "file_count": len(cache.get("files", [])),
                "last_scan":  cache.get("last_scan", 0),
            }
    return statuses


# ═══════════════════════════════════════════════════
# RECHERCHE DE FICHIERS
# ═══════════════════════════════════════════════════

def search_files(cfg: dict, query: str, channel_ids: list) -> dict:
    """
    Recherche des fichiers .cbz/.cbr dans les canaux sélectionnés.
    - Si le cache du canal est frais (< 24h) : recherche instantanée dans le cache.
    - Sinon : construit d'abord le cache (scan complet), puis cherche.
    Les canaux dont le cache est en cours de construction retournent un avertissement.
    """
    query_words = [w.lower() for w in query.split() if len(w) > 1]
    results     = []
    seen_hashes = set()
    chan_errors  = []
    chan_building = []

    # ── Canaux avec cache frais : recherche locale ────────────────────────────
    ids_need_scan = []
    for ch_id in channel_ids:
        ch_id = str(ch_id)
        with _cache_lock:
            if ch_id in _cache_building:
                chan_building.append(ch_id)
                continue
        cache = _load_cache(ch_id)
        if cache and _cache_is_fresh(cache):
            ch_name = cache.get("channel_name", ch_id)
            for entry in cache.get("files", []):
                fname = entry.get("filename", "")
                fname_lower = fname.lower()
                if query_words and not all(w in fname_lower for w in query_words):
                    continue
                fhash = f"tg_{ch_id}_{entry['message_id']}"
                if fhash in seen_hashes:
                    continue
                seen_hashes.add(fhash)
                tome_num = _detect_tome(fname)
                results.append({
                    "filename":     fname,
                    "size":         entry.get("size", 0),
                    "tome_number":  tome_num,
                    "tome_str":     f"T{tome_num:02d}" if tome_num else "?",
                    "message_id":   entry["message_id"],
                    "channel_id":   ch_id,
                    "channel_name": ch_name,
                    "date":         entry.get("date", ""),
                    "filehash":     fhash,
                    "tag":          _detect_tag(fname),
                })
        else:
            ids_need_scan.append(ch_id)

    # ── Canaux sans cache ou cache périmé : scan + build cache ────────────────
    if ids_need_scan:
        async def _scan_and_cache():
            from telethon.tl.types import Channel, Chat, InputMessagesFilterDocument
            client = await _make_client(cfg)
            try:
                if not await client.is_user_authorized():
                    return {"ok": False, "message": "Session invalide", "files": []}

                target_ids = set(ids_need_scan)
                entity_map = {}
                async for dialog in client.iter_dialogs():
                    entity = dialog.entity
                    if not isinstance(entity, (Channel, Chat)):
                        continue
                    eid = str(entity.id)
                    if eid in target_ids:
                        entity_map[eid] = (entity, dialog.name or eid)
                    if len(entity_map) == len(target_ids):
                        break

                scan_results = []
                scan_errors  = []
                for ch_id in ids_need_scan:
                    entry = entity_map.get(str(ch_id))
                    if not entry:
                        scan_errors.append(f"Canal {ch_id} non trouvé dans vos dialogs")
                        continue
                    entity, ch_name = entry
                    channel_files = []
                    try:
                        async for msg in client.iter_messages(entity, filter=InputMessagesFilterDocument):
                            if not msg.file:
                                continue
                            fname = (msg.file.name or "").strip()
                            if not fname.lower().endswith((".cbz", ".cbr")):
                                continue
                            channel_files.append({
                                "filename":   fname,
                                "message_id": msg.id,
                                "size":       msg.file.size or 0,
                                "date":       msg.date.isoformat() if msg.date else "",
                            })
                        _save_cache(ch_id, ch_name, channel_files)
                        log.info("[Telegram] Cache construit %s : %d fichiers", ch_name, len(channel_files))

                        for fe in channel_files:
                            fname = fe["filename"]
                            fname_lower = fname.lower()
                            if query_words and not all(w in fname_lower for w in query_words):
                                continue
                            fhash = f"tg_{ch_id}_{fe['message_id']}"
                            tome_num = _detect_tome(fname)
                            scan_results.append({
                                "filename":     fname,
                                "size":         fe.get("size", 0),
                                "tome_number":  tome_num,
                                "tome_str":     f"T{tome_num:02d}" if tome_num else "?",
                                "message_id":   fe["message_id"],
                                "channel_id":   ch_id,
                                "channel_name": ch_name,
                                "date":         fe.get("date", ""),
                                "filehash":     fhash,
                                "tag":          _detect_tag(fname),
                            })
                    except Exception as e:
                        err = f"Canal {ch_name} : {e}"
                        log.warning("[Telegram] %s", err)
                        scan_errors.append(err)

                return {"ok": True, "files": scan_results, "errors": scan_errors}
            finally:
                await client.disconnect()

        try:
            r = _run(_scan_and_cache())
            if r.get("ok"):
                for f in r["files"]:
                    fhash = f["filehash"]
                    if fhash not in seen_hashes:
                        seen_hashes.add(fhash)
                        results.append(f)
                chan_errors.extend(r.get("errors", []))
            else:
                chan_errors.append(r.get("message", "Erreur scan"))
        except Exception as e:
            log.error("[Telegram] search_files scan : %s", e, exc_info=True)
            chan_errors.append(str(e))

    if chan_building:
        chan_errors.append(
            f"Cache en construction pour {len(chan_building)} canal(aux) — réessayez dans quelques instants"
        )

    results.sort(key=lambda x: (x["tome_number"] or 9999, x["filename"]))
    warnings = chan_errors
    return {
        "ok":       True,
        "files":    results,
        "warnings": warnings,
        "message":  " | ".join(warnings) if warnings else "",
    }


# ═══════════════════════════════════════════════════
# TÉLÉCHARGEMENT
# ═══════════════════════════════════════════════════

def start_download(cfg: dict, message_id: int, channel_id: str,
                   dest_dir: str, filename: str, filehash: str) -> dict:
    """
    Lance le téléchargement d'un fichier Telegram en arrière-plan.
    Retourne immédiatement — le download se fait dans un thread dédié.
    """
    def _worker():
        async def _inner():
            client = await _make_client(cfg)
            try:
                entity    = await client.get_entity(int(channel_id))
                msg       = await client.get_messages(entity, ids=message_id)
                if not msg or not msg.media:
                    log.error("[Telegram] Message %s introuvable dans canal %s", message_id, channel_id)
                    _update_status(filehash, "error")
                    return

                os.makedirs(dest_dir, exist_ok=True)
                dest_path = os.path.join(dest_dir, filename)

                log.info("[Telegram] Début téléchargement : %s", filename)
                _update_status(filehash, "downloading")

                await client.download_media(msg, file=dest_path)

                log.info("[Telegram] ✓ Terminé : %s", filename)
                _finalize(filehash, dest_path)

            except Exception as e:
                log.error("[Telegram] ✗ %s : %s", filename, e)
                import config as _cfg
                _cfg.add_log(f"[Telegram] ✗ {filename} : {e}", "error")
                _update_status(filehash, "error")
            finally:
                await client.disconnect()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_inner())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    t = threading.Thread(target=_worker, daemon=True, name=f"tg-dl-{message_id}")
    t.start()
    return {"ok": True, "message": f"Téléchargement de {filename} démarré"}


def _update_status(filehash: str, status: str):
    """Met à jour le statut d'un item queue Telegram."""
    try:
        import queue_manager
        queue_manager.update_status(filehash, status)
    except Exception as e:
        log.warning("[Telegram] update_status : %s", e)


def _finalize(filehash: str, dest_path: str):
    """Marque l'item comme done et déclenche l'organisation du fichier."""
    try:
        import queue_manager, file_organizer, config as _cfg

        items = queue_manager.get_queue()
        item  = next((i for i in items if i.get("filehash") == filehash), None)
        if not item:
            log.warning("[Telegram] Item %s introuvable en queue pour finalisation", filehash)
            queue_manager.update_status(filehash, "done")
            return

        item["local_path"] = dest_path
        result = file_organizer.organize_file(item)

        history = {
            "source_file":  os.path.basename(dest_path),
            "processed_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        }
        if result.get("ok"):
            history["dest_filename"] = os.path.basename(result.get("dest_path", ""))
            _cfg.add_log(
                f"[Telegram] ✓ {item.get('series_name','?')} T{item.get('tome_number','?')}"
                f" : {os.path.basename(dest_path)} → {history['dest_filename']}",
                "info",
            )
        else:
            _cfg.add_log(f"[Telegram] Organisation échouée : {result.get('message','?')}", "warning")

        queue_manager.update_status(filehash, "done", history)

    except Exception as e:
        log.error("[Telegram] _finalize : %s", e)
        try:
            import queue_manager
            queue_manager.update_status(filehash, "done")
        except Exception:
            pass


# ═══════════════════════════════════════════════════
# HELPERS DÉTECTION
# ═══════════════════════════════════════════════════

def _detect_tome(filename: str) -> int | None:
    """Extrait le numéro de tome depuis un nom de fichier."""
    patterns = [
        r'T(?:ome)?[.\-_\s]*0*(\d{1,3})(?:\b|[.\-_@])',
        r'[-\s_]0*(\d{2,3})(?:[-\s_@]|$)',
        r'#\s*0*(\d{1,3})\b',
    ]
    for pat in patterns:
        m = re.search(pat, filename, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 999:
                return n
    return None


def _detect_tag(filename: str) -> str:
    """Extrait le tag/groupe depuis un nom de fichier."""
    try:
        import profiles
        return profiles.detect_tag(filename)
    except Exception:
        pass
    # Fallback : cherche après @ ou entre parenthèses en fin de nom
    m = re.search(r'@([A-Za-z0-9_+\-]+)', filename)
    if m:
        return m.group(1)
    m = re.search(r'\(([A-Za-z0-9_+\-]+)\)(?:\.[a-z]+)?$', filename)
    if m:
        return m.group(1)
    return "Notag"
