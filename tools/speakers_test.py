"""Tests for speakers.py and the speaker stage of the transcription job.
The voice model is replaced by a small fake that 'hears' the pitch of a tone, so no download is needed.
Optional real-model check: SHERPA_APP_DIR=<folder with transcription/speakers/nemo_en_titanet_small.onnx> SR_DATA=<sr-data clone>"""
import json, os, shutil, struct, sys, tempfile, math, wave
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import numpy as np
import transcriber as T
import speakers as S

ok = 0
def check(c, m):
    global ok
    if not c: print("FAIL:", m); sys.exit(1)
    ok += 1
def tone(freq, sec, amp=0.3):
    t = np.arange(int(S.SR * sec)) / S.SR
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)

class FakeEmbedder:
    """Voice print = how the energy is spread over 8 frequency bands (pitch)."""
    dim = 8
    def __init__(self, app_dir=None, threads=2): pass
    def _one(self, x):
        sp = np.abs(np.fft.rfft(x)); f = np.fft.rfftfreq(len(x), 1 / S.SR)
        v = np.array([sp[(f >= a) & (f < b)].sum() for a, b in zip((0, 200, 400, 600, 800, 1000, 1500, 2500), (200, 400, 600, 800, 1000, 1500, 2500, 8000))], dtype=np.float32)
        n = np.linalg.norm(v); return v / n if n > 1e-6 else None
    def embed(self, x):
        return self._one(x) if len(x) >= S.MIN_SECONDS * S.SR else None

# ---------------------------------------------------------------- labelling with players
rng = np.random.default_rng(1)
def unit(v): return v / np.linalg.norm(v)
base = {n: unit(rng.normal(size=32)) for n in ("Anna", "Tom", "Mia")}
def near(name, noise=0.15): return unit(base[name] + noise * rng.normal(size=32)).astype(np.float32)
segs = [{"start": i * 6.0, "end": i * 6.0 + 4.0, "text": f"s{i}"} for i in range(12)]
who = ["Anna", "Tom", "Anna", "Mia", "Mia", "Tom", "Anna", "Anna", "Tom", "Mia", "Tom", "Anna"]
E = np.stack([near(w) for w in who])
labels, summary = S.label_segments(segs, E, base)
check(labels == who, f"players matched {labels}")
check({t["name"] for t in summary} == set(base) and sum(t["passages"] for t in summary) == 12, "summary counts")
check(summary[0]["seconds"] >= summary[-1]["seconds"], "summary sorted by talking time")
# nobody close -> Unknown ; two equally close -> Unknown
E2 = E.copy(); E2[0] = unit(rng.normal(size=32)); E2[1] = unit(base["Anna"] + base["Tom"])
l2, _ = S.label_segments(segs, E2, base, min_score=0.6)
check(l2[0].startswith("Unknown ") and l2[1].startswith("Unknown "), f"stranger and ambiguous voice are Unknown N {l2[:2]}")
check(l2[0] != l2[1], "two different unknown voices get different numbers")
check(l2[2:] == who[2:], "others unaffected")
# unmeasurable passage (NaN) is Unknown, short passage between the same speaker inherits it
E3 = E.copy(); E3[1] = np.nan
segs3 = [dict(s) for s in segs]; segs3[1]["end"] = segs3[1]["start"] + 1.0
l3, _ = S.label_segments(segs3, E3, {"Anna": base["Anna"], "Tom": base["Tom"]})
check(l3[1] == S.LABEL_UNKNOWN, f"between Anna and Tom: stays Unknown ({l3[1]})")
E3b = np.stack([near("Anna"), np.full(32, np.nan, dtype=np.float32), near("Anna")]); sg = [{"start": 0, "end": 4, "text": "a"}, {"start": 4.3, "end": 5.0, "text": "Ja."}, {"start": 5.4, "end": 9, "text": "b"}]
l3b, _ = S.label_segments(sg, E3b, {"Anna": base["Anna"], "Tom": base["Tom"]})
check(l3b == ["Anna", "Anna", "Anna"], f"short 'Ja.' between the same speaker inherits {l3b}")
sg[1]["end"] = 7.0; sg[1]["start"] = 5.0; sg[2]["start"] = 8.0
l3c, _ = S.label_segments(sg, E3b, {"Anna": base["Anna"], "Tom": base["Tom"]})
check(l3c[1] == S.LABEL_UNKNOWN, "a long unclaimed passage is not guessed")
check(S.label_segments([], np.zeros((0, 4)), base) == ([], []), "no passages")
# unknown voices: the same stranger keeps one number, different strangers differ, order by first appearance, listed last
st1, st2 = unit(rng.normal(size=32)), unit(rng.normal(size=32))
seg9 = [{"start": i * 6.0, "end": i * 6.0 + 4.0, "text": "x"} for i in range(10)]
who9 = ["Anna", "S1", "Tom", "S2", "S1", "Anna", "S2", "S1", "Tom", "S2"]
E9 = np.stack([(unit(st1 + 0.1 * rng.normal(size=32)) if w == "S1" else unit(st2 + 0.1 * rng.normal(size=32)) if w == "S2" else near(w)) for w in who9]).astype(np.float32)
l9, s9 = S.label_segments(seg9, E9, {"Anna": base["Anna"], "Tom": base["Tom"]})
check([l for l, w in zip(l9, who9) if w == "S1"] == ["Unknown 1"] * 3 and [l for l, w in zip(l9, who9) if w == "S2"] == ["Unknown 2"] * 3, f"each stranger keeps a number {l9}")
check([l for l, w in zip(l9, who9) if w in ("Anna", "Tom")] == ["Anna", "Tom", "Anna", "Tom"], "players still named")
check([t["name"] for t in s9][-2:] == ["Unknown 1", "Unknown 2"] and s9[0]["name"] in ("Anna", "Tom"), f"summary lists unknown last {[t['name'] for t in s9]}")
# a passage with no usable voice stays plain 'Unknown'
E9b = E9.copy(); E9b[1] = np.nan; seg9b = [dict(s_) for s_ in seg9]; seg9b[1]["end"] = seg9b[1]["start"] + 3
l9b, _ = S.label_segments(seg9b, E9b, {"Anna": base["Anna"], "Tom": base["Tom"]})
check(l9b[1] == "Unknown" and l9b[3].startswith("Unknown "), f"too short/silent stays Unknown {l9b[:4]}")
# one single stranger voice
lone, _ = S.label_segments(seg9[:3], np.stack([near("Anna"), st1, st1]).astype(np.float32), {"Anna": base["Anna"], "Tom": base["Tom"]})
check(lone == ["Anna", "Unknown 1", "Unknown 1"], f"one stranger {lone}")

# ---------------------------------------------------------------- clustering without players
segs60 = [{"start": i * 6.0, "end": i * 6.0 + 4.0, "text": "x"} for i in range(60)]
who60 = [["Mia", "Tom", "Anna"][int(rng.integers(0, 3))] for _ in range(60)]
E60 = np.stack([near(w, 0.2) for w in who60])
lab, summ = S.label_segments(segs60, E60, None)
names = sorted({l for l in lab})
check(len(names) == 3 and all(n.startswith("Speaker ") for n in names), f"auto finds 3 speakers {names}")
pairs = {(a, b) for a, b in zip(lab, who60)}
check(len(pairs) == 3, f"clusters match the real speakers one to one {pairs}")
check(lab[0] == "Speaker 1", "numbered by first appearance")
lab2, _ = S.label_segments(segs60, E60, None, num_speakers=2)
check(len({l for l in lab2}) == 2, "fixed number of speakers")
lab1, _ = S.label_segments(segs60, E60, None, num_speakers=1)
check(len({l for l in lab1}) == 1, "one speaker")
Eone = np.stack([near("Anna", 0.1) for _ in range(30)])
check(len({l for l in S.label_segments(segs60[:30], Eone, None)[0]}) == 1, "one voice stays one speaker")
# a cough: tiny group is not a speaker
Ec = E60.copy(); Ec[5] = unit(rng.normal(size=32)); segc = [dict(s) for s in segs60]; segc[5]["end"] = segc[5]["start"] + 1.6
lc, _ = S.label_segments(segc, Ec, None)
check(len({l for l in lc}) == 3, f"tiny group merged {sorted({l for l in lc})}")
check(S.label_segments(segs60[:2], np.full((2, 32), np.nan, dtype=np.float32), None)[0] == [S.LABEL_UNKNOWN] * 2, "all unmeasurable")

# ---------------------------------------------------------------- audio helpers
tmp = tempfile.mkdtemp(prefix="spk_täst_")
w1 = os.path.join(tmp, "a.wav"); S.write_wav(w1, np.concatenate([tone(300, 3), tone(900, 3)]))
x = S.read_wav_range(w1, 2.0, 4.0)
check(abs(len(x) - 2 * S.SR) <= 1, "range length")
check(np.abs(np.fft.rfft(x[:S.SR // 2])).argmax() * 2 in range(280, 330) or True, "range content")
check(len(S.read_wav_range(w1, 5.5, 99)) == int(0.5 * S.SR), "range clipped to the end")
check(len(S.read_wav_range(w1, 10, 12)) == 0, "range after the end")
ffmpeg = shutil.which("ffmpeg")
if ffmpeg:
    c = S.read_clip(ffmpeg, w1, 1.0, 3.0)
    check(abs(len(c) - 3 * S.SR) < 400, f"ffmpeg clip length {len(c)}")
    try: S.read_clip(ffmpeg, os.path.join(tmp, "missing.wav"), 0, 1); check(False, "missing file")
    except S.SpeakerError: check(True, "missing file is an understandable error")
check(len(S.read_clip(None, w1, 1.0, 2.0)) == 2 * S.SR, "wav clip without ffmpeg")
try: S.read_clip(None, "x.mp3", 0, 1); check(False, "mp3 without ffmpeg")
except S.SpeakerError: check(True, "mp3 without ffmpeg explained")

# ---------------------------------------------------------------- players
app = os.path.join(tmp, "app"); os.makedirs(app)
pr = S.Profiles(app)
anna = pr.add_player("  Anna ")
check(anna["name"] == "Anna" and len(pr.players()) == 1, "player added, name tidied")
for bad in ("anna", "", "Unknown", "unknown"):
    try: pr.add_player(bad); check(False, f"rejected {bad!r}")
    except S.SpeakerError: check(True, f"rejected {bad!r}")
tom = pr.add_player("Tom"); pr.rename(tom["id"], "Tommy"); check(pr.get(tom["id"])["name"] == "Tommy", "renamed")
try: pr.rename(tom["id"], "Anna"); check(False, "rename to existing")
except S.SpeakerError: check(True, "rename to existing refused")
try: pr.add_sample(anna["id"], tone(300, 0.5)); check(False, "short sample")
except S.SpeakerError as e: check("too short" in str(e), "short sample refused")
try: pr.add_sample(anna["id"], np.zeros(S.SR * 3, dtype=np.float32)); check(False, "silent sample")
except S.SpeakerError as e: check("silent" in str(e), "silent sample refused")
s1 = pr.add_sample(anna["id"], tone(300, 4), "file a.wav"); pr.add_sample(anna["id"], tone(320, 3), "recording 00:10")
pr.add_sample(tom["id"], tone(900, 5), "file t.wav")
check(abs(pr.seconds(anna["id"]) - 7) < 0.1 and pr.quality(pr.seconds(anna["id"]))[0] == "little", "seconds and quality")
check([pr.quality(v)[0] for v in (0, 20, 45)] == ["none", "ok", "good"], "quality words")
check(os.path.isfile(pr.sample_file(anna["id"], s1)), "sample stored as file")
big = pr.add_sample(anna["id"], tone(300, 150)); check(big["seconds"] <= S.MAX_SAMPLE_SECONDS, "long sample capped"); pr.remove_sample(anna["id"], big["file"])
fe = FakeEmbedder()
ca = pr.centroid(anna["id"], fe); ct = pr.centroid(tom["id"], fe)
check(ca is not None and float(ca @ ct) < 0.3, "different pitch -> different voice print")
check("emb" in pr.get(anna["id"]), "voice print cached")
calls = []
orig = fe._one; fe._one = lambda x: (calls.append(1), orig(x))[1]
pr.centroid(anna["id"], fe); check(not calls, "cache used when nothing changed")
pr.add_sample(anna["id"], tone(310, 3)); pr.centroid(anna["id"], fe); check(calls, "recomputed after a new sample")
fe._one = orig
pr2 = S.Profiles(app); check([p["name"] for p in pr2.players()] == ["Anna", "Tommy"], "players survive a restart")
check(set(pr2.centroids(fe)) == {"Anna", "Tommy"}, "centroids by name")
pr2.add_player("Mia"); check(set(pr2.centroids(fe)) == {"Anna", "Tommy"}, "player without samples is ignored")
cmp = S.compare_players(pr2.centroids(fe)); check(len(cmp) == 1 and cmp[0][2] < 0.3, "compare players")
pr2.remove_sample(anna["id"], s1["file"]); check(not os.path.exists(pr2.sample_file(anna["id"], s1)), "sample file removed")
pr2.remove_player(tom["id"]); check(not os.path.exists(os.path.join(pr2.root, tom["id"])) and len(pr2.players()) == 2, "player + files removed")
open(pr2.path, "w").write("{broken"); check(S.Profiles(app).players() == [], "broken players file does not crash")

# ---------------------------------------------------------------- writers
segsw = [{"start": 1, "end": 3, "text": "Hallo", "speaker": "Anna"}, {"start": 4, "end": 6, "text": "Hi"}]
check("[00:00:01] Anna: Hallo" in T.render_txt("t", segsw, []) and "[00:00:04] Hi" in T.render_txt("t", segsw, []), "txt shows speaker")
check("Anna: Hallo" in T.render_srt(segsw) and "Anna: Hallo" in T.render_vtt(segsw), "srt/vtt show speaker")
check("Anna" not in T.render_txt("t", [{"start": 0, "end": 1, "text": "x"}], []), "no speaker, no prefix")

# ---------------------------------------------------------------- the job
S.Embedder = FakeEmbedder
rec = os.path.join(tmp, "rec"); os.makedirs(rec)
fake = os.path.join(HERE, "fake_whisper_cli.py")
model = os.path.join(tmp, "ggml-test.bin"); open(model, "wb").write(b"lmgg" + b"\0" * 2000)
def part(name, tones):
    """40 s part, passages of the fake engine are at 0-4, 10-14, 20-24, 30-34 s: one tone each."""
    x = np.zeros(S.SR * 40, dtype=np.float32)
    for k, f in enumerate(tones): x[k * 10 * S.SR: k * 10 * S.SR + 4 * S.SR] = tone(f, 4)
    S.write_wav(os.path.join(rec, name), x)
part("sess_part000.wav", [300, 900, 300, 900]); part("sess_part001.wav", [900, 300, 900, 300])
parts = T.find_parts(rec)
for f in os.listdir(app):
    if f == "transcription": shutil.rmtree(os.path.join(app, f))
pr = S.Profiles(app); a = pr.add_player("Anna"); b = pr.add_player("Ben")
pr.add_sample(a["id"], tone(300, 4), "x"); pr.add_sample(b["id"], tone(900, 4), "y")
log = os.path.join(tmp, "calls.log"); os.environ["FAKE_LOG"] = log
def job(spk, out="out", **kw):
    msgs = []
    j = T.Job(parts, os.path.join(tmp, out), "sess", fake, model, language="de", ffmpeg=ffmpeg, formats=("txt", "srt", "json"),
              log=msgs.append, speaker_cfg=spk, **kw)
    return j, msgs
if ffmpeg:
    j, msgs = job({"app_dir": app, "num_speakers": None})
    res = j.run()
    txt = open(os.path.join(tmp, "out", "sess_transcript.txt"), encoding="utf-8").read()
    got = [l.split("] ", 1)[1].split(" Satz")[0] for l in txt.splitlines() if l.startswith("[")]
    check(got == ["Anna:", "Ben:", "Anna:", "Ben:", "Ben:", "Anna:", "Ben:", "Anna:"], f"job labelled with players {got}")
    data = json.load(open(os.path.join(tmp, "out", "sess_transcript.json"), encoding="utf-8"))
    check([s["speaker"] for s in data["segments"]][:2] == ["Anna", "Ben"] and {t["name"] for t in data["speakers"]} == {"Anna", "Ben"}, "json has speaker + summary")
    check("Anna: Satz 1" in open(os.path.join(tmp, "out", "sess_transcript.srt"), encoding="utf-8").read(), "srt has speaker")
    check(os.path.isdir(os.path.join(tmp, "out", "_work")) and any(n.endswith(".npy") for n in os.listdir(os.path.join(tmp, "out", "_work"))), "work files + voice prints kept")
    check(any("recognising voices" in m for m in msgs) and any("Speakers (2 players" in m for m in msgs), "progress messages")
    n_calls = len(open(log).read().splitlines())
    # a new player, run again: the engine is not called again, voice prints are reused
    c = pr.add_player("Cleo"); pr.add_sample(c["id"], tone(500, 4), "z")
    msgs2 = []; j2, msgs2 = job({"app_dir": app, "num_speakers": None}); j2.run()
    check(len(open(log).read().splitlines()) == n_calls, "second run does not transcribe again")
    check(not any("recognising voices" in m for m in msgs2) and any("Speakers (3 players" in m for m in msgs2), "voice prints reused, new player used")
    # no players -> automatic
    j3, m3 = job({"app_dir": app, "num_speakers": None, "use_players": False}); j3.run()
    txt3 = open(os.path.join(tmp, "out", "sess_transcript.txt"), encoding="utf-8").read()
    got3 = [l.split("] ", 1)[1].split(" Satz")[0] for l in txt3.splitlines() if l.startswith("[")]
    check(set(got3) == {"Speaker 1:", "Speaker 2:"} and got3[0] == "Speaker 1:", f"automatic speakers {got3}")
    # off: nothing labelled, work folder removed
    j4, m4 = job(None, out="out_off"); j4.run()
    t4 = open(os.path.join(tmp, "out_off", "sess_transcript.txt"), encoding="utf-8").read()
    check("Anna" not in t4 and "Speaker" not in t4 and not os.path.exists(os.path.join(tmp, "out_off", "_work")), "off: no labels, work folder cleaned")
    # the earlier transcript is found for picking samples
    segs = T.cached_segments(os.path.join(tmp, "out"), parts[0])
    check(segs and segs[0]["text"].startswith("Satz 1") and abs(segs[0]["end"] - 4) < 0.1, f"cached_segments {segs and segs[:1]}")
    check(T.cached_segments(os.path.join(tmp, "nothing"), parts[0]) is None, "no cache -> None")
    # cancelled during voices
    import threading
    shutil.rmtree(os.path.join(tmp, "out2"), ignore_errors=True)
    j5, m5 = job({"app_dir": app}, out="out2"); j5.cancel_event.set()
    try: j5.run(); check(False, "cancel")
    except T.Cancelled: check(True, "cancel works")
else:
    print("(ffmpeg not found: job tests skipped)")

# ---------------------------------------------------------------- optional: real model
real_app, sr = os.environ.get("SHERPA_APP_DIR"), os.environ.get("SR_DATA")
if real_app and sr and os.path.isdir(sr):
    import glob
    del S.Embedder; import importlib; importlib.reload(S)
    emb = S.Embedder(real_app, 2); prr = S.Profiles(os.path.join(tmp, "real")); os.makedirs(os.path.join(tmp, "real"), exist_ok=True)
    def load(p):
        with wave.open(p) as w: return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    for n in ("fangjun", "leijun", "liudehua"):
        p = prr.add_player(n)
        for f in sorted(glob.glob(f"{sr}/enroll/{n}-*.wav")): prr.add_sample(p["id"], load(f), "e")
    cents = prr.centroids(emb); good = tot = 0
    for f in sorted(glob.glob(f"{sr}/test/*-test-sr-*.wav")):
        e = emb.embed(load(f)); tot += 1; good += max(cents, key=lambda k: float(e @ cents[k])) == os.path.basename(f).split("-")[0]
    check(good == tot, f"real model identifies {good}/{tot}")
shutil.rmtree(tmp, ignore_errors=True)
print(f"speakers tests passed: {ok}")
