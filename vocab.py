"""
Special words for the Transcribe tool (no window code, so it can be tested alone).

  * a word list that can be filled from several sources without ever creating doubles
  * import of names from the Kurator campaign data (campaign-data/index.json + campaigns/<id>/campaign.json)
  * a spelling fix that compares the finished transcript with the list and repairs near-misses
"""
import difflib
import json
import os
import re

try:
    import dnd_terms
except Exception:                      # the list is optional
    dnd_terms = None


class VocabError(Exception):
    """A problem a person can understand."""


# =====================================================================================================
#  The list
# =====================================================================================================
def norm_key(word):
    return re.sub(r"\s+", " ", str(word).strip()).lower()


def tidy(word):
    return re.sub(r"\s+", " ", str(word).strip()).strip(" ,;")


def split_words(text):
    """'Astratera, Weltentor; Karthy Drift Reach' -> words. Commas, semicolons and line breaks separate."""
    return [w for w in (tidy(p) for p in re.split(r"[,;\n\r]+", text or "")) if w]


def merge_words(existing, new, front=False):
    """Adds new words without doubles (ignoring upper/lower case and extra spaces). New words go to the end, or to the
    front when front=True (in the order given). Returns (merged list, number added, number that were already there).
    Words that are already in the list keep their place."""
    merged = [tidy(w) for w in existing if tidy(w)]
    seen = {norm_key(w) for w in merged}
    fresh = []
    dup = 0
    for w in new:
        w = tidy(w)
        if len(w) < 2:
            continue
        k = norm_key(w)
        if k in seen:
            dup += 1
            continue
        seen.add(k)
        fresh.append(w)
    return (fresh + merged if front else merged + fresh), len(fresh), dup


# =====================================================================================================
#  Where a word came from, and ordering
# =====================================================================================================
SRC_TYPED, SRC_KURATOR, SRC_DND = "typed", "kurator", "dnd"
SOURCES = [(SRC_TYPED, "Typed"), (SRC_KURATOR, "Campaign"), (SRC_DND, "D&D terms")]


def dnd_keys():
    """Normalised names of all built-in D&D terms."""
    return {norm_key(w) for w in dnd_words([k for k, _, _, _ in dnd_categories()])}


def migrate_sources(words, src):
    """Source of every word: what was stored, else D&D when it is a known term, else typed."""
    src = src if isinstance(src, dict) else {}
    known = None
    out = {}
    for w in words:
        k = norm_key(w)
        v = src.get(k)
        if v not in (SRC_TYPED, SRC_KURATOR, SRC_DND):
            if known is None:
                known = dnd_keys()
            v = SRC_DND if k in known else SRC_TYPED
        out[k] = v
    return out


def add_words(words, src, new, source, front=True):
    """Adds words of one source. Returns (words, sources, added, duplicates). New words go to the front by default,
    because words added later are usually the more important ones."""
    merged, added, dup = merge_words(words, new, front=front)
    old = {norm_key(w) for w in words}
    out = dict(src)
    for w in merged:
        if norm_key(w) not in old:
            out[norm_key(w)] = source
    return merged, migrate_sources(merged, out), added, dup


def move_words(words, selected, where, src=None, group=None):
    """Moves the selected words (a set of normalised keys) inside the list.
    where: 'top', 'bottom', 'up', 'down', 'before_group' (in front of the first word of source `group`),
    'after_group' (behind the last word of that group).
    Returns (new list, moved) where moved is False when nothing could move."""
    sel = {norm_key(k) for k in selected}
    picked = [w for w in words if norm_key(w) in sel]
    rest = [w for w in words if norm_key(w) not in sel]
    if not picked:
        return list(words), False
    if where == "top":
        out = picked + rest
    elif where == "bottom":
        out = rest + picked
    elif where == "before_group":
        src = src or {}
        idx = next((i for i, w in enumerate(rest) if src.get(norm_key(w)) == group), None)
        if idx is None:
            return list(words), False
        out = rest[:idx] + picked + rest[idx:]
    elif where == "after_group":
        src = src or {}
        idx = [i for i, w in enumerate(rest) if src.get(norm_key(w)) == group]
        if not idx:
            return list(words), False
        out = rest[:idx[-1] + 1] + picked + rest[idx[-1] + 1:]
    elif where in ("up", "down"):
        out = list(words)
        rng = range(len(out)) if where == "up" else range(len(out) - 1, -1, -1)
        step = -1 if where == "up" else 1
        for i in rng:
            j = i + step
            if norm_key(out[i]) in sel and 0 <= j < len(out) and norm_key(out[j]) not in sel:
                out[i], out[j] = out[j], out[i]
    else:
        raise VocabError(f"Unknown move: {where}")
    return out, out != list(words)


def move_to_index(words, selected, index):
    """Drag and drop: moves the selected words (set of normalised keys) so that they sit where `index` (a position
    in the current list, 0..len) was. One step for any number of words. Returns (new list, moved)."""
    sel = {norm_key(k) for k in selected}
    picked = [w for w in words if norm_key(w) in sel]
    if not picked:
        return list(words), False
    index = max(0, min(int(index), len(words)))
    above = sum(1 for w in words[:index] if norm_key(w) in sel)
    rest = [w for w in words if norm_key(w) not in sel]
    at = index - above
    out = rest[:at] + picked + rest[at:]
    return out, out != list(words)


# =====================================================================================================
#  Presets: named word lists kept in one small file
# =====================================================================================================
def load_presets(path):
    """{name: {'words': [...], 'src': {...}, 'saved': 'YYYY-MM-DD'}}. A missing or broken file gives {}."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    for name, p in (data.get("presets", {}) if isinstance(data, dict) else {}).items():
        if isinstance(p, dict) and isinstance(p.get("words"), list):
            words = merge_words([], p["words"])[0]
            out[str(name)] = {"words": words, "src": migrate_sources(words, p.get("src")), "saved": str(p.get("saved", ""))}
    return out


def _write_presets(path, presets):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"presets": presets}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def save_preset(path, name, words, src):
    """Stores (or overwrites) a preset."""
    import datetime
    name = tidy(name)[:60]
    if not name:
        raise VocabError("Please type a name for the preset.")
    if not words:
        raise VocabError("The word list is empty, so there is nothing to save.")
    presets = load_presets(path)
    presets[name] = {"words": list(words), "src": migrate_sources(words, src), "saved": datetime.date.today().isoformat()}
    _write_presets(path, presets)
    return name


def delete_preset(path, name):
    presets = load_presets(path)
    if presets.pop(name, None) is not None:
        _write_presets(path, presets)


def apply_preset(words, src, preset, mode):
    """mode 'add': the preset's words are added (new ones to the front, no doubles); 'replace': the list becomes the
    preset. Returns (words, sources, added, duplicates)."""
    if mode == "replace":
        w = list(preset["words"])
        return w, migrate_sources(w, preset.get("src")), len(w), 0
    merged, added, dup = merge_words(words, preset["words"], front=True)
    old = {norm_key(w) for w in words}
    out = dict(src)
    ps = preset.get("src") or {}
    for w in merged:
        if norm_key(w) not in old:
            out[norm_key(w)] = ps.get(norm_key(w)) or SRC_TYPED
    return merged, migrate_sources(merged, out), added, dup


def migrate_words(cfg):
    """Old settings kept the words as one text; new ones keep a list. Returns the list."""
    v = cfg.get("vocab")
    if isinstance(v, list):
        return [tidy(x) for x in v if tidy(x)]
    return merge_words([], split_words(cfg.get("words", "")))[0]


# =====================================================================================================
#  Kurator import
# =====================================================================================================
_CODE_PREFIX = re.compile(r"^\s*[A-Za-z]{1,2}\s?\d+\s*[-–—:]\s*")
_TRAIL_NUM = re.compile(r"\s*\d+\s*$")
_BRACKET_NOTE = re.compile(r"\s*\[[^\]]*\]")


def clean_name(name, split=True, strip_codes=True):
    """One Kurator entry name -> list of words.
      'Dockmarshall Dane, (Rul Dane)'  -> 'Dockmarshall Dane', 'Rul Dane'
      'A3 - Glassgrove'                -> 'Glassgrove'
      'Dockarbeiter02'                 -> 'Dockarbeiter'
      'Hollow Archive Ebene 2 [geschlossen]' -> 'Hollow Archive Ebene 2'  (the note in [] is dropped)
      'M-1-DG-3'                       -> nothing (a pure code)"""
    s = tidy(name)
    if not s:
        return []
    s = _BRACKET_NOTE.sub("", s)
    parts = [s]
    if split:
        parts = [p for p in re.split(r"[,()]", s)]
    else:
        s = s.replace("(", "").replace(")", "")
        parts = [s]
    out = []
    for p in parts:
        p = tidy(p)
        if strip_codes:
            p = _CODE_PREFIX.sub("", p)
            if re.fullmatch(r"[A-Z0-9\-_/. ]+", p) and re.search(r"\d", p) and re.search(r"[-_]", p):
                continue                                   # codes such as M-1-DG-3
            if re.search(r"[A-Za-zÄÖÜäöüß]{3,}\s*\d+$", p):
                p = _TRAIL_NUM.sub("", p)                   # 'Dockarbeiter 01' -> 'Dockarbeiter'
        p = tidy(p)
        if len(p) >= 3 and not p.isdigit():
            out.append(p)
    return out


def resolve_kurator_root(path):
    """Finds the 'campaign-data' folder (the one with index.json) from whatever folder the person picked.
    Returns its path or None."""
    if not path:
        return None
    p = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(p):
        p = os.path.dirname(p)
    cands = [p, os.path.join(p, "campaign-data")]
    cur = p
    for _ in range(4):                      # picked 'campaigns' or a campaign folder: look upwards
        cur = os.path.dirname(cur)
        cands.append(cur)
        cands.append(os.path.join(cur, "campaign-data"))
    for c in cands:
        if os.path.isfile(os.path.join(c, "index.json")) and os.path.isdir(os.path.join(c, "campaigns")):
            return c
    return None


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        raise VocabError(f"File not found: {path}")
    except Exception as e:
        raise VocabError(f"Could not read {os.path.basename(path)}: {e}")


def list_campaigns(root):
    """[{'id','name','created','available'}] newest first."""
    data = _load_json(os.path.join(root, "index.json"))
    if not isinstance(data, list):
        raise VocabError("index.json does not look like a Kurator campaign list.")
    out = []
    for c in data:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        cid = str(c["id"])
        out.append({"id": cid, "name": str(c.get("name") or cid), "created": str(c.get("createdAt") or ""),
                    "available": os.path.isfile(os.path.join(root, "campaigns", cid, "campaign.json"))})
    out.sort(key=lambda c: c["created"], reverse=True)
    return out


CATEGORY_LABELS = [("locations", "Locations"), ("npcs", "NPCs"), ("quests", "Quests"), ("regions", "Regions and groups")]
DEFAULT_ON = {"locations": True, "npcs": True, "quests": False, "regions": False}


def load_campaign(root, campaign_id):
    """{'locations': [names], 'npcs': [...], 'quests': [...], 'regions': [...]} with raw names, no doubles."""
    data = _load_json(os.path.join(root, "campaigns", campaign_id, "campaign.json"))
    if not isinstance(data, dict):
        raise VocabError("campaign.json does not look like Kurator campaign data.")
    cats = {"locations": [], "npcs": [], "quests": [], "regions": []}
    ents = data.get("entities")
    for e in (ents.values() if isinstance(ents, dict) else ents or []):
        if not isinstance(e, dict):
            continue
        kind = {"location": "locations", "npc": "npcs", "quest": "quests"}.get(e.get("type"))
        name = tidy(e.get("name", ""))
        if kind and name:
            cats[kind].append(name)
    for g in data.get("entityGroups") or []:
        if isinstance(g, dict) and tidy(g.get("name", "")):
            cats["regions"].append(tidy(g["name"]))
    for k, v in cats.items():
        seen, uniq = set(), []
        for n in v:
            if norm_key(n) not in seen:
                seen.add(norm_key(n))
                uniq.append(n)
        cats[k] = uniq
    return cats


def load_campaigns(root, ids):
    """Merges several campaigns. Returns (categories, notes) where notes lists campaigns that could not be read."""
    merged = {"locations": [], "npcs": [], "quests": [], "regions": []}
    notes = []
    for cid in ids:
        try:
            cats = load_campaign(root, cid)
        except VocabError as e:
            notes.append(f"{cid}: {e}")
            continue
        for k in merged:
            merged[k], _, _ = merge_words(merged[k], cats[k])
    return merged, notes


def names_to_words(names, split=True, strip_codes=True):
    words = []
    for n in names:
        words, _, _ = merge_words(words, clean_name(n, split, strip_codes))
    return words


# =====================================================================================================
#  D&D terms
# =====================================================================================================
def dnd_categories():
    """[(key, language, label, count)]"""
    if dnd_terms is None:
        return []
    return [(k, lang, label, len(words)) for k, lang, label, words in dnd_terms.CATEGORIES]


def dnd_words(keys):
    return dnd_terms.words_for(set(keys)) if dnd_terms else []


# =====================================================================================================
#  Spelling fix for the finished transcript
# =====================================================================================================
_TOKEN = re.compile(r"[A-Za-zÄÖÜäöüß0-9][A-Za-zÄÖÜäöüß0-9'’\-]*")
MIN_LEN = 6                  # shorter words are never touched: too easy to confuse with ordinary words
STRICT_MIN_LEN = 8           # single words from D&D lists / name parts must be at least this long ...
THRESHOLD_SINGLE = 0.86
THRESHOLD_MULTI = 0.84
STRICT_SINGLE = 0.90         # ... and match better
STRICT_MULTI = 0.88


def _phon(s):
    """Rough 'sounds like' form, so Kelmor/Kellmar and Virex/Wirex compare as nearly equal."""
    s = s.lower().replace("ß", "ss")
    for a, b in (("ph", "f"), ("ck", "k"), ("w", "v"), ("y", "i"), ("ä", "a"), ("ö", "o"), ("ü", "u"), ("c", "k"),
                 ("z", "s"), ("th", "t"), ("dt", "t")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)
    return re.sub(r"[^a-z0-9 ]", "", s)


class _Entry:
    __slots__ = ("text", "tokens", "phon", "k", "strict")

    def __init__(self, text, strict):
        self.text = text
        self.tokens = _TOKEN.findall(text)
        self.phon = _phon(" ".join(self.tokens))
        self.k = len(self.tokens)
        self.strict = strict


def _letters(s):
    return len(re.sub(r"[^A-Za-zÄÖÜäöüß]", "", s))


def _build_entries(names, terms):
    """Names count as they are. Parts of longer names and the D&D terms are 'strict': they must be longer and match
    better, because they are often ordinary words (Professor, Stärke, Archivist)."""
    entries, seen = [], set()

    def add(text, strict):
        e = _Entry(text, strict)
        key = norm_key(text)
        if e.k == 0 or key in seen or _letters(text) < MIN_LEN:
            return
        if strict and e.k == 1 and _letters(text) < STRICT_MIN_LEN:
            return
        seen.add(key)
        entries.append(e)
    for w in names:
        add(w, False)
    for w in names:
        toks = _TOKEN.findall(w)
        if 2 <= len(toks) <= 3:
            for t in toks:
                if t[:1].isupper():
                    add(t, True)
            if len(toks) == 3:
                for i in range(2):
                    add(" ".join(toks[i:i + 2]), True)
    for w in terms:
        add(w, True)
    return entries


def split_names_terms(words, src=None):
    """Splits the word list into the person's own names and the built-in D&D terms (the latter are treated more
    strictly by the spelling fix because many of them are ordinary words)."""
    src = migrate_sources(words, src)
    names = [w for w in words if src[norm_key(w)] != SRC_DND]
    terms = [w for w in words if src[norm_key(w)] == SRC_DND]
    return names, terms


def _match(raw, wp, k, index):
    """Best list entry for a phrase of k words, as (score, entry) or None. 2.0 = already right."""
    best = None
    for e in index.get((k, wp[:1]), ()):
        if abs(len(wp) - len(e.phon)) > 2:
            continue
        rl, el = raw.lower(), e.text.lower()
        if raw == e.text:
            score = 2.0
        elif rl != el and (rl.startswith(el) or el.startswith(rl)):
            continue                                   # an ending (Kelmors, Professoren), not a misspelling
        elif rl == el:
            score = 1.5 if e.text[:1].isupper() and k > 1 else 0   # fix capitals of whole names only
        else:
            if e.strict and k == 1 and _letters(raw) < STRICT_MIN_LEN:
                continue
            need = (STRICT_MULTI if k > 1 else STRICT_SINGLE) if e.strict else \
                   (THRESHOLD_MULTI if k > 1 else THRESHOLD_SINGLE)
            r = difflib.SequenceMatcher(None, wp, e.phon).ratio()
            score = r if r >= need else 0
        if score and (best is None or score > best[0]):
            best = (score, e)
    return best


def fix_spelling(segments, names, terms=()):
    """Compares every passage with the word lists and repairs near-misses ('Kellmar Wirex' -> 'Kelmor Virex').
    Returns (new segments, corrections) where corrections is [{'from','to','count'}] sorted by count.
    Conservative on purpose: first letter must match, similar length, high similarity, at least 6 letters."""
    entries = _build_entries(names, terms)
    if not entries:
        return segments, []
    index = {}
    for e in entries:
        index.setdefault((e.k, e.phon[:1]), []).append(e)
    ks = sorted({e.k for e in entries}, reverse=True)
    memo = {}                                          # the same phrase comes back again and again
    counts = {}
    out = []
    for seg in segments:
        text = seg["text"]
        toks = list(_TOKEN.finditer(text))
        repl = []                                      # (start, end, replacement)
        i = 0
        while i < len(toks):
            hit = None                                 # (k, entry, raw)
            for k in ks:
                if i + k > len(toks):
                    continue
                window = toks[i:i + k]
                # the window must be one phrase: only spaces between the words
                if any(text[window[j].end():window[j + 1].start()].strip(" ") for j in range(k - 1)):
                    continue
                raw = text[window[0].start():window[-1].end()]
                if _letters(raw) < MIN_LEN:
                    continue
                key = (k, raw)
                if key not in memo:
                    wp = _phon(" ".join(t.group() for t in window))
                    memo[key] = _match(raw, wp, k, index) if len(wp) >= 4 else None
                m = memo[key]
                if m:
                    hit = (k, m[1], raw, m[0])
                    break                              # the longest phrase wins
            if hit:
                k, e, raw, score = hit
                if score < 2.0 and raw != e.text:
                    repl.append((toks[i].start(), toks[i + k - 1].end(), e.text))
                    counts[(raw, e.text)] = counts.get((raw, e.text), 0) + 1
                i += k
            else:
                i += 1
        if repl:
            for s0, e0, new in reversed(repl):
                text = text[:s0] + new + text[e0:]
            seg = dict(seg, text=text)
        out.append(seg)
    corrections = [{"from": a, "to": b, "count": n} for (a, b), n in counts.items()]
    corrections.sort(key=lambda c: (-c["count"], c["from"]))
    return out, corrections
