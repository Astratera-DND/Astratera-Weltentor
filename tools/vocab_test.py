"""Logic tests for vocab.py (word list, Kurator import, spelling fix). Run: python3 tools/vocab_test.py [campaign.json]"""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import vocab as V

ok = bad = 0
def check(name, cond, info=""):
    global ok, bad
    if cond: ok += 1
    else: bad += 1; print("FAIL:", name, info)

# ---- list
m, a, d = V.merge_words(["Astratera"], ["astratera", " Weltentor ", "Karthy  Drift", "karthy drift", "x"])
check("merge adds new, skips doubles/short", m == ["Astratera", "Weltentor", "Karthy Drift"] and (a, d) == (2, 2), (m, a, d))
m2, a2, d2 = V.merge_words(m, ["Weltentor", "ASTRATERA"])
check("merge twice changes nothing", m2 == m and a2 == 0 and d2 == 2)
check("order untouched", V.merge_words(["b b", "a a"], ["c c"])[0] == ["b b", "a a", "c c"])
check("split_words", V.split_words("A1, B2;C3\nD4 ,, ") == ["A1", "B2", "C3", "D4"])
check("migrate legacy", V.migrate_words({"words": "Astratera, Weltentor"}) == ["Astratera", "Weltentor"])
check("migrate list wins", V.migrate_words({"vocab": ["X Y"], "words": "ignored"}) == ["X Y"])
check("migrate empty", V.migrate_words({}) == [])

# ---- clean_name
cn = V.clean_name
check("plain", cn("Glassgrove") == ["Glassgrove"])
check("code prefix", cn("A3 - Glassgrove") == ["Glassgrove"], cn("A3 - Glassgrove"))
check("trailing number", cn("Dockarbeiter 01") == ["Dockarbeiter"])
check("alias comma", cn("Rul Dane, der Schmied") == ["Rul Dane", "der Schmied"], cn("Rul Dane, der Schmied"))
check("alias paren", cn("Kelmor Virex (Professor)") == ["Kelmor Virex", "Professor"], cn("Kelmor Virex (Professor)"))
check("no split", cn("Rul Dane, der Schmied", split=False) == ["Rul Dane, der Schmied"], cn("Rul Dane, der Schmied", split=False))
check("note dropped", cn("Hafen [wichtig]") == ["Hafen"])
check("pure code dropped", cn("M-1-DG-3") == [])
check("keeps codes when asked", cn("A3 - Glassgrove", strip_codes=False) != ["Glassgrove"])
check("empty", cn("   ") == [])

# ---- kurator folder
src = sys.argv[1] if len(sys.argv) > 1 else None
tmp = tempfile.mkdtemp()
try:
    root = os.path.join(tmp, "x", "campaign-data")
    os.makedirs(os.path.join(root, "campaigns", "camp_a"))
    os.makedirs(os.path.join(root, "campaigns", "camp_b"))
    os.makedirs(os.path.join(root, "campaigns", "camp_c"))     # listed but no data
    json.dump([{"id": "camp_a", "name": "Alpha", "createdAt": "2026-01-01T00:00:00Z"},
               {"id": "camp_b", "name": "Beta", "createdAt": "2026-05-01T00:00:00Z"},
               {"id": "camp_c", "name": "Ghost", "createdAt": "2026-03-01T00:00:00Z"},
               {"name": "no id"}], open(os.path.join(root, "index.json"), "w"))
    if src and os.path.exists(src):
        shutil.copy(src, os.path.join(root, "campaigns", "camp_a", "campaign.json"))
    else:
        json.dump({"entities": {"1": {"type": "location", "name": "A3 - Glassgrove"}, "2": {"type": "npc", "name": "Rul Dane, der Schmied"},
                                "3": {"type": "quest", "name": "Das Rätsel"}, "4": {"type": "item", "name": "Schwert"}},
                   "entityGroups": [{"name": "Hafenviertel"}]}, open(os.path.join(root, "campaigns", "camp_a", "campaign.json"), "w"))
    json.dump({"entities": {"1": {"type": "location", "name": "Glassgrove"}, "2": {"type": "npc", "name": "Neue Figur"}}},
              open(os.path.join(root, "campaigns", "camp_b", "campaign.json"), "w"), ensure_ascii=False)
    for label, p in (("root", root), ("parent", os.path.dirname(root)), ("campaigns", os.path.join(root, "campaigns")),
                     ("campaign folder", os.path.join(root, "campaigns", "camp_a")),
                     ("campaign.json", os.path.join(root, "campaigns", "camp_a", "campaign.json")),
                     ("index.json", os.path.join(root, "index.json"))):
        check("resolve " + label, V.resolve_kurator_root(p) == os.path.abspath(root), p)
    check("resolve nothing", V.resolve_kurator_root(tmp + "/nope") is None and V.resolve_kurator_root("") is None)
    camps = V.list_campaigns(root)
    check("list newest first, skips no-id", [c["name"] for c in camps] == ["Beta", "Ghost", "Alpha"], camps)
    check("available flag", {c["name"]: c["available"] for c in camps} == {"Beta": True, "Ghost": False, "Alpha": True})
    cats = V.load_campaign(root, "camp_a")
    check("categories filled", cats["locations"] and cats["npcs"], {k: len(v) for k, v in cats.items()})
    check("items ignored", "Schwert" not in sum(cats.values(), []))
    merged, notes = V.load_campaigns(root, ["camp_a", "camp_b", "camp_c"])
    check("missing campaign noted", len(notes) == 1 and "camp_c" in notes[0], notes)
    check("merge dedupes across campaigns", sum(1 for n in merged["locations"] if V.norm_key(n) == "glassgrove") <= 1 or src)
    ws = V.names_to_words(merged["locations"] + merged["npcs"])
    check("words produced", len(ws) > 0 and len(ws) == len({V.norm_key(w) for w in ws}))
    # importing twice must not change the list
    lst, a1, _ = V.merge_words([], ws)
    lst2, a2, d2 = V.merge_words(lst, ws)
    check("second import adds nothing", a2 == 0 and d2 == len(ws) and lst2 == lst)
    bad_idx = os.path.join(tmp, "bad"); os.makedirs(os.path.join(bad_idx, "campaigns"))
    open(os.path.join(bad_idx, "index.json"), "w").write("{not json")
    try: V.list_campaigns(bad_idx); check("bad index raises", False)
    except V.VocabError: check("bad index raises", True)
    json.dump({"a": 1}, open(os.path.join(bad_idx, "index.json"), "w"))
    try: V.list_campaigns(bad_idx); check("wrong index raises", False)
    except V.VocabError: check("wrong index raises", True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---- D&D terms
cats = V.dnd_categories()
check("categories", len(cats) >= 6 and all(c[3] > 10 for c in cats))
allw = V.dnd_words([c[0] for c in cats])
check("all terms", len(allw) > 600)
check("subset", 0 < len(V.dnd_words([cats[0][0]])) < len(allw))
check("no key = nothing", V.dnd_words([]) == [])
n, t = V.split_names_terms(["Kelmor Virex", "Fireball", "Glassgrove"])
check("split names/terms", n == ["Kelmor Virex", "Glassgrove"] and t == ["Fireball"], (n, t))

# ---- spelling fix
names = ["Kelmor Virex", "Lysa Verenn", "Garrick Vale", "Hollow Archive", "Glassgrove", "Professor Kelmor Virex"]
terms = V.dnd_words([c[0] for c in cats])
def fx(text, n=names, t=terms):
    out, corr = V.fix_spelling([{"start": 0, "end": 1, "text": text}], n, t)
    return out[0]["text"], corr
check("fix 2-word name", fx("Dann kam Kellmar Wirex herein.")[0] == "Dann kam Kelmor Virex herein.", fx("Dann kam Kellmar Wirex herein."))
check("fix single", fx("Wir gehen nach Glasgrove.")[0] == "Wir gehen nach Glassgrove.", fx("Wir gehen nach Glasgrove."))
check("fix spell name", fx("Er wirkt Fireball.")[0] == "Er wirkt Fireball.")
check("fix spell term", fx("Er wirkt Magic Missle.")[0] == "Er wirkt Magic Missile.", fx("Er wirkt Magic Missle."))
check("correct text untouched", fx("Kelmor Virex und Hollow Archive.")[1] == [])
check("ending kept", fx("Kelmors Büro, die Professoren.")[0] == "Kelmors Büro, die Professoren.", fx("Kelmors Büro, die Professoren."))
check("punctuation kept", fx("Garrik Vale, sagt er!")[0] == "Garrick Vale, sagt er!", fx("Garrik Vale, sagt er!"))
check("phrase not across comma", fx("Kellmar, Wirex")[0] == "Kellmar, Wirex")
check("capitals of whole names", fx("Er traf kelmor virex.")[0] == "Er traf Kelmor Virex.", fx("Er traf kelmor virex."))
for sample in ["Der Magier sagt: Wir sollten vorsichtig sein, weil die Wächter aufmerksam sind.",
               "Starkes Beben, Gesteinsbrocken, Staub überall.", "Sicherheitschef wartet am Eingang des Archivs.",
               "Der Barde, der Kleriker, der Paladin und der Hexenmeister sind bereit.",
               "Die Händler am Marktplatz verkaufen Schiffsteile an die Piloten.", "Er sagt, dass sein Archiv offen ist."]:
    check("no false positive: " + sample[:30], fx(sample)[0] == sample, fx(sample))
out, corr = V.fix_spelling([{"start": i, "end": i + 1, "text": "Kellmar Wirex kommt."} for i in range(3)], names, terms)
check("counts", corr == [{"from": "Kellmar Wirex", "to": "Kelmor Virex", "count": 3}], corr)
check("times kept", [s["start"] for s in out] == [0, 1, 2])
check("empty lists", V.fix_spelling([{"start": 0, "end": 1, "text": "Hallo"}], [], [])[1] == [])
import time
segs = [{"start": 0, "end": 1, "text": t} for t in ["Kellmar Wirex hat die Tür geöffnet und Professor Kelmor Virex sagt hallo."] * 5000]
t0 = time.time(); V.fix_spelling(segs, names, terms); el = time.time() - t0
check("fast enough (5000 passages < 5 s)", el < 5, el)
# ---- new words in front, sources, moving, presets
m, a, d = V.merge_words(["Old One", "Old Two"], ["New A", "old one", "New B"], front=True)
check("front insertion keeps given order, old words stay behind", m == ["New A", "New B", "Old One", "Old Two"] and (a, d) == (2, 1), m)
w, src, a, d = V.add_words(["Alpha Name"], {"alpha name": "typed"}, ["Beta Name", "alpha name"], "kurator")
check("add_words: front, source recorded, doubles keep old source", w == ["Beta Name", "Alpha Name"] and src == {"beta name": "kurator", "alpha name": "typed"} and (a, d) == (1, 1), (w, src))
check("migrate_sources guesses", V.migrate_sources(["Fireball", "Kelmor Virex"], None) == {"fireball": "dnd", "kelmor virex": "typed"})
check("migrate_sources keeps stored", V.migrate_sources(["Fireball"], {"fireball": "typed"}) == {"fireball": "typed"})
check("migrate_sources ignores nonsense", V.migrate_sources(["Xy Zq"], {"xy zq": "banana"}) == {"xy zq": "typed"})
L = ["a1", "b2", "c3", "d4", "e5", "f6"]
check("move top (several keep their order)", V.move_words(L, {"d4", "b2"}, "top")[0] == ["b2", "d4", "a1", "c3", "e5", "f6"])
check("move bottom", V.move_words(L, {"a1"}, "bottom")[0] == ["b2", "c3", "d4", "e5", "f6", "a1"])
check("move up", V.move_words(L, {"c3", "e5"}, "up")[0] == ["a1", "c3", "b2", "e5", "d4", "f6"])
check("move down", V.move_words(L, {"c3", "e5"}, "down")[0] == ["a1", "b2", "d4", "c3", "f6", "e5"])
check("up at the top does nothing", V.move_words(L, {"a1"}, "up") == (L, False))
check("down at the end does nothing", V.move_words(L, {"f6"}, "down") == (L, False))
check("block at the top stays together", V.move_words(L, {"a1", "b2"}, "up") == (L, False))
check("nothing selected", V.move_words(L, set(), "top") == (L, False))
S2 = {"a1": "typed", "b2": "typed", "c3": "kurator", "d4": "kurator", "e5": "dnd", "f6": "dnd"}
check("in front of group", V.move_words(L, {"b2"}, "before_group", S2, "dnd")[0] == ["a1", "c3", "d4", "b2", "e5", "f6"])
check("in front of a group that is only selected itself", V.move_words(L, {"e5", "f6"}, "before_group", S2, "dnd")[1] is False)
check("in front of a missing group", V.move_words(["a1"], {"a1"}, "before_group", {"a1": "typed"}, "kurator")[1] is False)
check("behind group", V.move_words(L, {"a1"}, "after_group", S2, "kurator")[0] == ["b2", "c3", "d4", "a1", "e5", "f6"])
check("behind the last word of the group even when selected words sit inside", V.move_words(L, {"c3"}, "after_group", S2, "kurator")[0] == ["a1", "b2", "d4", "c3", "e5", "f6"])
check("behind a missing group", V.move_words(["a1"], {"a1"}, "after_group", {"a1": "typed"}, "dnd")[1] is False)
check("drop in the middle", V.move_to_index(L, {"a1", "b2"}, 4)[0] == ["c3", "d4", "a1", "b2", "e5", "f6"])
check("drop at the top", V.move_to_index(L, {"e5", "f6"}, 0)[0] == ["e5", "f6", "a1", "b2", "c3", "d4"])
check("drop at the end", V.move_to_index(L, {"a1"}, 6)[0] == ["b2", "c3", "d4", "e5", "f6", "a1"])
check("drop onto itself", V.move_to_index(L, {"c3"}, 2) == (L, False) and V.move_to_index(L, {"c3"}, 3) == (L, False))
check("drop with a nonsense position is clamped", V.move_to_index(L, {"a1"}, 99)[0][-1] == "a1" and V.move_to_index(L, {"f6"}, -5)[0][0] == "f6")
check("drop with nothing selected", V.move_to_index(L, set(), 2) == (L, False))
big = [f"Word{i:04d}" for i in range(5000)]
import time as _t; t0 = _t.time(); out, mv = V.move_to_index(big, {f"word{i:04d}" for i in range(0, 4000, 2)}, 2500); check("5000 words / 2000 selected are fast", _t.time() - t0 < 0.5 and len(out) == 5000 and len(set(out)) == 5000)
check("keys are normalised", V.move_words(["Ab Cd", "Ef Gh"], {"EF  GH"}, "top")[0] == ["Ef Gh", "Ab Cd"])
import tempfile
pp = os.path.join(tempfile.mkdtemp(), "sub", "p.json")
check("no presets yet", V.load_presets(pp) == {})
nm = V.save_preset(pp, "  Akt  2 ", ["Kelmor Virex", "Fireball"], {"kelmor virex": "kurator"})
pre = V.load_presets(pp)
check("preset saved and loaded", nm == "Akt 2" and pre["Akt 2"]["words"] == ["Kelmor Virex", "Fireball"] and pre["Akt 2"]["src"] == {"kelmor virex": "kurator", "fireball": "dnd"} and pre["Akt 2"]["saved"], pre)
V.save_preset(pp, "Zweiter", ["Nur Einer"], {}); check("two presets", set(V.load_presets(pp)) == {"Akt 2", "Zweiter"})
V.save_preset(pp, "Akt 2", ["Neu Neu"], {}); check("overwrite", V.load_presets(pp)["Akt 2"]["words"] == ["Neu Neu"])
for bad_args in (("", ["x y"]), ("Name", [])):
    try: V.save_preset(pp, bad_args[0], bad_args[1], {}); check(False, f"refused {bad_args}")
    except V.VocabError: check(True, f"refused {bad_args}")
V.delete_preset(pp, "Zweiter"); V.delete_preset(pp, "gibt es nicht"); check("delete", set(V.load_presets(pp)) == {"Akt 2"})
pr = {"words": ["Pre One", "Pre Two"], "src": {"pre one": "kurator"}}
w, sr, a, d = V.apply_preset(["My Own"], {"my own": "typed"}, pr, "add")
check("apply add: preset words in front with their sources", w == ["Pre One", "Pre Two", "My Own"] and sr["pre one"] == "kurator" and sr["pre two"] == "typed" and sr["my own"] == "typed" and (a, d) == (2, 0), (w, sr))
w, sr, a, d = V.apply_preset(["Pre Two", "My Own"], {}, pr, "add"); check("apply add twice-safe", w == ["Pre One", "Pre Two", "My Own"] and (a, d) == (1, 1), w)
w, sr, a, d = V.apply_preset(["My Own"], {}, pr, "replace"); check("apply replace", w == ["Pre One", "Pre Two"] and "my own" not in sr)
open(pp, "w").write("{broken"); check("broken preset file", V.load_presets(pp) == {})
n2, t2 = V.split_names_terms(["Fireball", "Kelmor Virex"], {"fireball": "typed"}); check("split uses stored sources (typed Fireball is a name)", n2 == ["Fireball", "Kelmor Virex"] and t2 == [])
print(f"{ok} ok, {bad} failed")
sys.exit(1 if bad else 0)
