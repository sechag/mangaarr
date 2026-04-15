"""
cache.py — Cache persistant pour MangaArr
- Cache des métadonnées MangaDB (JSON sur disque)
- Normalisation avancée des titres pour le matching
- Enrichissement asynchrone en arrière-plan
"""
import os, json, time, threading, re, unicodedata
from difflib import SequenceMatcher

CACHE_DIR  = os.path.join(os.path.dirname(__file__), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "metadata_cache.json")

_mem_cache = {}
_mem_lock  = threading.Lock()
_enriching = {}
_enrich_lock = threading.Lock()

os.makedirs(CACHE_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════
# PERSISTANCE
# ═══════════════════════════════════════════════════

def _load_disk():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_disk(data):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _init_mem():
    global _mem_cache
    with _mem_lock:
        _mem_cache = _load_disk()

_init_mem()


# ═══════════════════════════════════════════════════
# ACCÈS CACHE
# ═══════════════════════════════════════════════════

def get_series_meta(library_id, series_id):
    with _mem_lock:
        return _mem_cache.get(library_id, {}).get(series_id)

def set_series_meta(library_id, series_id, meta):
    with _mem_lock:
        _mem_cache.setdefault(library_id, {})[series_id] = meta
    threading.Thread(target=_save_disk, args=(_mem_cache.copy(),), daemon=True).start()

def get_library_cache(library_id):
    with _mem_lock:
        return dict(_mem_cache.get(library_id, {}))

def clear_cache(library_id=None):
    with _mem_lock:
        if library_id:
            _mem_cache.pop(library_id, None)
        else:
            _mem_cache.clear()
    _save_disk(_mem_cache)

def get_cache_stats():
    with _mem_lock:
        libs = {lid: len(s) for lid, s in _mem_cache.items()}
    total = sum(libs.values())
    disk_size = 0
    try:
        disk_size = os.path.getsize(CACHE_FILE)
    except Exception:
        pass
    return {
        "libraries":    libs,
        "total_entries": total,
        "disk_size_kb": round(disk_size / 1024, 1),
        "cache_file":   CACHE_FILE,
    }


# ═══════════════════════════════════════════════════
# NORMALISATION AVANCÉE
# ═══════════════════════════════════════════════════

# Regex articles finaux entre parenthèses : "titre (the)" ou "titre (l')"
_RE_ART_PAREN = re.compile(
    r'\s*\(\s*(la|le|les|du|des|de|d|une?|the|in|on|an?|l)\s*\)\s*$',
    re.IGNORECASE
)
# Regex articles en début
_RE_ART_START = re.compile(
    r'^(?:la\s+|le\s+|les\s+|du\s+|des?\s+|une?\s+|dans\s+|the\s+|in\s+|on\s+|an?\s+|de\s+)',
    re.IGNORECASE
)
# Regex numéros de tome (tous formats)
_RE_TOME = re.compile(
    r'\b[tT](?:ome)?\s*\d{1,3}\b'
    r'|\b[Vv]ol(?:ume)?[.]?\s*\d{1,3}\b'
    r'|\b#\d{1,3}\b',
    re.IGNORECASE
)


def _normalize(text):
    """
    Normalise un titre pour comparaison maximale :
    - l'/d' -> espace avant suppression (l'ile -> l ile -> ile)
    - Accents supprimés (é->e, à->a, ç->c)
    - Article final entre parenthèses supprimé : titre (the) -> titre
    - Article en début supprimé : the titre -> titre
    - & -> espace (and/et gardés comme mots du titre)
    - Tout non-alphanumérique -> espace
    - Lowercase, espaces normalisés
    """
    if not text:
        return ""

    # 1. l'/L' et d'/D' -> article + espace pour détecter "l ile" -> supprimable
    text = re.sub(r'[lL][\u2019\']', 'l ', text)
    text = re.sub(r'[dD][\u2019\']', 'd ', text)
    # Autres apostrophes -> rien
    text = re.sub(r'[\u2019\'`]', '', text)

    # 2. NFD + suppression diacritiques
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")

    # 3. Lowercase
    text = text.lower()

    # 4. Article final entre parenthèses -> supprime juste la parenthèse
    m = re.search(
        r'\s*\(\s*(la|le|les|du|des|de|d|une?|the|in|on|an?|l)\s*\)\s*$',
        text, re.IGNORECASE
    )
    if m:
        text = text[:m.start()].strip()

    # 5. Article en début -> supprime
    text = _RE_ART_START.sub('', text)
    # Cas spécial : 'l ' résiduel (de L'ile -> l ile) supprimé
    text = re.sub(r'^l ', '', text)

    # 6. & -> espace (and/et gardés car font partie des titres)
    text = re.sub(r'\s*&\s*', ' ', text)

    # 7. Tout non-alphanumérique -> espace
    text = re.sub(r'[^a-z0-9 ]', ' ', text)

    # 8. Collapse espaces
    return re.sub(r' +', ' ', text).strip()


def _normalize_no_tome(text):
    """Comme _normalize mais supprime aussi les numéros de tome."""
    text = _normalize(text)
    text = _RE_TOME.sub(' ', text)
    return re.sub(r' +', ' ', text).strip()


def _similarity(a, b):
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def normalize_filename_for_series(filename):
    """
    Extrait et normalise le titre de série depuis un nom de fichier.
    Exemples :
      "Elusive Samurai (The) T03 (Matsui) (2022) [Digital] (Paprika).cbz"
       -> "elusive samurai"
      "One Piece T08 [1920u] [MangaFR].cbz"
       -> "one piece"
      "My.Hero.Academia.T32.FRENCH.CBZ.eBook-NEO.RIP-Club.cbz"
       -> "my hero academia"
    Stratégie :
    1. Supprime extension
    2. Supprime crochets [...]
    3. Supprime parenthèses non-article (auteurs, années, tags, résolution)
    4. Supprime numéros de tome (T01, Vol.1, etc.)
    5. Normalise via _normalize
    6. Supprime mots parasites résiduels (french, cbz, ebook, etc.)
    """
    name = os.path.splitext(filename)[0]

    # 1. Remplace les points par espaces (format MangaArr "My.Hero.Academia")
    #    mais seulement si pas de vrais espaces (pour éviter "U.S.A." -> "U S A ")
    if ' ' not in name:
        name = name.replace('.', ' ')

    # 2. Crochets -> espace
    name = re.sub(r'\[[^\]]*\]', ' ', name)

    # 3. Parenthèses : deux passes
    # Passe A : supprime toutes les parenthèses non-article
    def _is_article(s):
        return bool(re.match(
            r'^(la|le|les|du|des|de|d|une?|the|in|on|an?|l)$',
            s.strip(), re.IGNORECASE
        ))
    # Collecte les positions à supprimer en une passe
    while True:
        found = False
        for m in re.finditer(r'\(([^)]*)\)', name):
            if not _is_article(m.group(1)):
                name = name[:m.start()] + ' ' + name[m.end():]
                found = True
                break  # relance après chaque suppression
        if not found:
            break
    # Passe B : supprime les parenthèses d'article restantes (ex: "(The)" final -> rien)
    # L'article en fin de titre a déjà été traité par _normalize (article final)
    name = re.sub(r'\([^)]*\)', ' ', name)

    # 4. Supprime numéros de tome AVANT _normalize (pour éviter que t32 survive)
    name = re.sub(
        r'\b[tT](?:ome)?\s*\d{1,3}\b'
        r'|\b[Vv]ol(?:ume)?[.]?\s*\d{1,3}\b'
        r'|\b#\d{1,3}\b',
        ' ', name
    )

    # 5. Normalise
    name = _normalize(name)

    # 6. Supprime mots parasites résiduels
    name = re.sub(
        r'\bfrench\b|\bcbz\b|\bcbr\b|\bebook\b|\bneo\b',
        ' ', name, flags=re.IGNORECASE
    )
    # Supprime noms de groupes connus
    name = re.sub(
        r'\brip ?club\b|\bpaprika\b|\bprinter\b|\btoner\b',
        ' ', name, flags=re.IGNORECASE
    )
    # Supprime nombres isolés résiduels (années, résolutions)
    name = re.sub(r'\b\d{2,4}\b', ' ', name)

    return re.sub(r' +', ' ', name).strip()


# ═══════════════════════════════════════════════════
# MATCHING CSV
# ═══════════════════════════════════════════════════

def find_in_csv(series_name, csv_df, threshold=0.60):
    """
    Cherche la meilleure correspondance dans le DataFrame CSV.
    Si series_name ressemble à un nom de fichier (contient [ ou (auteur)),
    utilise normalize_filename_for_series pour l'extraire proprement.
    """
    if csv_df is None or csv_df.empty:
        return None

    # Détecte si c'est un nom de fichier ou un titre simple
    looks_like_file = ('[' in series_name or
                       series_name.lower().endswith(('.cbz', '.cbr', '.pdf')) or
                       bool(re.search(r'[(].*[(]', series_name)))  # parenthèses multiples
    if looks_like_file:
        norm_query = normalize_filename_for_series(series_name)
    else:
        norm_query = _normalize(series_name)

    if not norm_query:
        return None

    best_row   = None
    best_score = 0.0
    for _, row in csv_df.iterrows():
        titre = str(row.get("titre_francais", ""))
        if not titre:
            continue
        score = SequenceMatcher(None, norm_query, _normalize(titre)).ratio()
        if score > best_score:
            best_score = score
            best_row   = row
    if best_score >= threshold and best_row is not None:
        return best_row.to_dict()
    return None


def _build_meta(row):
    """Construit le dict metadata depuis une ligne CSV."""
    if not row:
        return {}
    auteur = str(row.get("auteur", "") or "")
    auteur = re.sub(r'\s*[\r\n]+\s*', ' ', auteur)
    auteur = re.sub(r'\s+-\s+', '', auteur)
    auteur = re.sub(r'\s+', ' ', auteur).strip()

    genres_raw = str(row.get("genres", "") or "")
    genres = [g.strip() for g in re.split(r'[|,]', genres_raw) if g.strip()] if genres_raw else []

    try:
        tomes_vf = int(row.get("tomes", 0) or 0)
    except Exception:
        tomes_vf = 0

    return {
        "titre":          str(row.get("titre_francais", "") or ""),
        "auteur":         auteur,
        "editeur":        str(row.get("editeur", "") or ""),
        "genres":         genres,
        "statut_vf":      str(row.get("statut_vf", "Inconnu") or "Inconnu"),
        "tomes_vf":       tomes_vf,
        "manga_news_url": str(row.get("url", "") or ""),
    }


# ═══════════════════════════════════════════════════
# ENRICHISSEMENT ASYNCHRONE
# ═══════════════════════════════════════════════════

def is_enriching(library_id):
    with _enrich_lock:
        return _enriching.get(library_id, False)


def find_in_csv_by_titre(titre: str) -> dict | None:
    """Cherche un titre exact dans le cache CSV (pour association manuelle)."""
    c = load()
    df_data = c.get("_csv_df")
    if df_data:
        try:
            import pandas as _pd
            df = _pd.DataFrame(df_data)
            if "titre_francais" in df.columns:
                row = df[df["titre_francais"].str.lower() == titre.lower()]
                if not row.empty:
                    return _build_meta(row.iloc[0].to_dict())
        except Exception:
            pass
    return None
