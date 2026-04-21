"""
profiles.py — Tags de qualité et filtres de releases

Gère :
  - Tags/groupes de release avec scores (Paprika+ > NEO RIP-Club > ...)
  - detect_tag()       : identifie le groupe depuis un nom de fichier
  - is_better_than()   : compare deux tags par leur score
  - passes_filters()   : vérifie must_contain / must_not_contain
  - matches_filter()   : recherche mot entier (word boundary) insensible à la casse
"""
import re, unicodedata, config

def normalize(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))

def get_tags() -> list:
    """Retourne la liste [{name, score}] triée par score décroissant."""
    tags = config.get("profiles", {}).get("tags", config.DEFAULTS["profiles"]["tags"])
    return sorted(tags, key=lambda t: t["score"], reverse=True)

def set_tags(tags: list):
    cfg = config.load()
    cfg["profiles"]["tags"] = tags
    config.save(cfg)

def get_tag_score(tag: str) -> int:
    for t in get_tags():
        if t["name"] == tag:
            return t["score"]
    return 0

def detect_tag(filename: str) -> str:
    """
    Détecte le tag/mot clé de release dans un nom de fichier.
    Teste d'abord les tags configurés par l'utilisateur (config),
    puis se rabat sur KNOWN_TAGS si aucun tag configuré ne matche.
    Retourne "Notag" si aucun tag reconnu.
    """
    text = normalize(filename).lower()
    text = re.sub(r"[\(\)\[\]]", "", text)

    # Priorité absolue : Paprika+ (contient +, doit être testé avant Paprika)
    if re.search(r"(?<![a-z0-9])paprika\+(?![a-z0-9])", text) or "paprika+" in text:
        return "Paprika+"

    # Teste les tags configurés par l'utilisateur
    user_tags = get_tags()
    for t in user_tags:
        tag = t["name"]
        if tag in ("Notag", "Paprika+"):
            continue
        tag_norm = normalize(tag).lower()
        if re.search(rf"(?<![a-z0-9]){re.escape(tag_norm)}(?![a-z0-9])", text):
            return _resolve_neo(tag, text)

    # Fallback : teste KNOWN_TAGS (toujours, même si config vide)
    for tag in config.KNOWN_TAGS:
        if tag in ("Notag", "Paprika+"):
            continue
        # Normalise le tag pour la comparaison
        tag_norm = normalize(tag).lower()
        if re.search(rf"(?<![a-z0-9]){re.escape(tag_norm)}(?![a-z0-9])", text):
            return _resolve_neo(tag, text)

    return "Notag"


def detect_tag_from_cbz(filepath: str) -> str:
    """
    Ouvre un CBZ (ZIP) et cherche un tag connu dans les noms de dossiers internes.
    Utile quand le nom de fichier ne contient pas de tag.
    Retourne "Notag" si rien trouvé.
    """
    import zipfile
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            dirs = set()
            for name in zf.namelist():
                parts = name.replace("\\", "/").split("/")
                # Prend les noms de dossiers (au moins 2 segments = dossier + fichier)
                if len(parts) >= 2:
                    dirs.add(parts[0])
            for d in dirs:
                tag = detect_tag(d)
                if tag != "Notag":
                    return tag
    except Exception:
        pass
    return "Notag"


def _resolve_neo(tag: str, text: str) -> str:
    """Résout RIP-Club -> NEO RIP-Club si 'neo' est présent dans le texte."""
    if tag == "RIP-Club" and "neo" in text:
        return "NEO RIP-Club"
    # Normalise aussi NEO.RIP-Club et Neo Rip-Club -> NEO RIP-Club
    if tag in ("NEO.RIP-Club", "Neo Rip-Club"):
        return "NEO RIP-Club"
    return tag

def is_better_than(new_tag: str, existing_tag: str) -> bool:
    return get_tag_score(new_tag) > get_tag_score(existing_tag)


def _clean_for_filter(text: str) -> str:
    """
    Nettoie un texte pour la comparaison des filtres must_contain / must_not_contain.
    Supprime : accents, parenthèses, crochets, ponctuation → ne garde que alphanum + espaces.
    Exemples :
      "[Scantrads]"  → "scantrads"
      "(Paprika+)"   → "paprika "
      "NEO RIP-Club" → "neo rip club"
    """
    import unicodedata, re
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return re.sub(r" +", " ", text).strip()


def matches_filter(filename: str, keywords: list) -> bool:
    """
    Vérifie si le filename contient AU MOINS UN des mots clés.
    - Comparaison insensible à la casse et aux caractères spéciaux
    - Le mot clé doit correspondre à un MOT ENTIER (word boundary)
      pour éviter que "eng" bloque "Revenging"
    """
    import re as _re
    fn_clean = _clean_for_filter(filename)
    for kw in keywords:
        kw_clean = _clean_for_filter(kw).strip()
        if not kw_clean:
            continue
        # Word boundary : le mot clé ne doit pas être collé à une lettre/chiffre
        pattern = r"(?<![a-z0-9])" + _re.escape(kw_clean) + r"(?![a-z0-9])"
        if _re.search(pattern, fn_clean):
            return True
    return False


def passes_filters(filename: str) -> tuple[bool, str]:
    """
    Vérifie qu'un fichier passe les filtres must_contain et must_not_contain.
    Retourne (ok: bool, raison: str).
    """
    must_not = get_must_not_contain()
    if must_not and matches_filter(filename, must_not):
        matched = next(
            (kw for kw in must_not if _clean_for_filter(kw) in _clean_for_filter(filename)),
            "?"
        )
        return False, f"contient '{matched}' (interdit)"

    must = get_must_contain()
    if must and not matches_filter(filename, must):
        return False, "ne contient aucun mot clé requis"

    return True, ""

# Must contain / must not contain
def get_must_contain() -> list:
    return config.get("profiles", {}).get("must_contain", [])

def get_must_not_contain() -> list:
    return config.get("profiles", {}).get("must_not_contain", [])

def set_must_contain(lst: list):
    cfg = config.load()
    cfg["profiles"]["must_contain"] = lst
    config.save(cfg)

def set_must_not_contain(lst: list):
    cfg = config.load()
    cfg["profiles"]["must_not_contain"] = lst
    config.save(cfg)

# Compatibilité ancienne API (priorité par ordre)
def get_priority_list() -> list:
    return [t["name"] for t in get_tags()]

def set_priority_list(ordered: list):
    existing = {t["name"]: t["score"] for t in get_tags()}
    new_tags = []
    total = len(ordered)
    for i, name in enumerate(ordered):
        score = existing.get(name, total - i)
        new_tags.append({"name": name, "score": score})
    set_tags(new_tags)
