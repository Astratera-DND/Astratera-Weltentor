import http.server, json, os, shutil, struct, sys, tempfile, threading, time, wave, zipfile, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import transcriber as T

ok = 0
def check(c, m):
    global ok
    if not c: print("FAIL:", m); sys.exit(1)
    ok += 1

root = tempfile.mkdtemp(prefix="täst_")           # non-ASCII on purpose
rec = os.path.join(root, "rec"); os.makedirs(rec)
def mkwav(path, seconds, rate=44100):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i * 0.05))) for i in range(int(rate * seconds))))
for n, sec in (("sess_part000.wav", 25), ("sess_part001.wav", 25), ("sess_part002.wav", 12)):
    mkwav(os.path.join(rec, n), sec)
json.dump([{"timecode": "00:00:05", "elapsed_seconds": 5.0, "note": "Scene: 02_Taverne_Zum_Drachen"},
           {"timecode": "00:00:30", "elapsed_seconds": 30.0, "note": "Remember: the key"},
           {"timecode": "00:00:41", "elapsed_seconds": 41.0, "note": "--- BREAK START ---"},
           {"timecode": "00:00:50", "elapsed_seconds": 50.0, "note": "Layout: Karte"},
           {"event": "RECORDING_END", "elapsed_seconds": 62.0, "note": "Recording ended"}], open(os.path.join(rec, "sess_markers.json"), "w"))
json.dump([{"kind": "music", "name": "01_Dark_Theme.mp3", "elapsed_seconds": 3.0, "scene": "Astratera Hafen"},
           {"kind": "sfx", "name": "door.wav", "elapsed_seconds": 33.0, "scene": "Taverne"},
           {"kind": "quickfire", "name": "zap.wav", "elapsed_seconds": 34.0}], open(os.path.join(rec, "sess_audio_log.json"), "w"))
fake = os.path.join(HERE, "fake_whisper_cli.py")
model = os.path.join(root, "ggml-test.bin"); open(model, "wb").write(b"lmgg" + b"\0" * 2000)
vadm = os.path.join(root, "vad.bin"); open(vadm, "wb").write(b"lmgg" + b"\0" * 100)
ffmpeg = shutil.which("ffmpeg")
parts = T.find_parts(rec)
check([os.path.basename(p) for p in parts] == ["sess_part000.wav", "sess_part001.wav", "sess_part002.wav"], "parts in natural order")
check(T.find_parts(os.path.join(rec, "sess_part001.wav")) == [os.path.join(rec, "sess_part001.wav")], "single file")
check(T.natural_key("p10") > T.natural_key("p9"), "natural sort")
mk, al = T.guess_session_files(parts[0])
check(mk.endswith("sess_markers.json") and al.endswith("sess_audio_log.json"), "session files guessed")
check(T.probe_duration(ffmpeg, parts[0]) == 25.0, "wav duration")
ev = T.build_events(mk, al)
check([e["kind"] for e in ev] == ["music", "scene", "marker", "break", "layout"], f"events {[e['kind'] for e in ev]}")
check(ev[1]["text"] == "Scene: Taverne Zum Drachen" and ev[0]["text"] == "Music: Dark Theme", "event names cleaned")
check(len(T.build_events(mk, al, kinds=("scene", "sfx", "quickfire"))) == 3, "kinds filter")
real = os.path.join(root, "real_markers.json")
json.dump([{"event": "RECORDING_START", "datetime": "2026-10-22T19:00:00", "elapsed_seconds": 0.0, "note": "Recording started"},
           {"timecode": "00:50:00", "elapsed_seconds": 3000.0, "note": "--- BREAK START ---"},
           {"event": "BREAK_START", "datetime": "2026-10-22T19:50:00", "elapsed_seconds": 3000.0, "note": "Break started"},
           {"event": "BREAK_END", "datetime": "2026-10-22T20:05:00", "elapsed_seconds": 3000.0, "note": "Break ended"},
           {"timecode": "00:50:00", "elapsed_seconds": 3000.1, "note": "--- BREAK END ---"},
           {"event": "RECORDING_END", "elapsed_seconds": 4000.0, "note": "Recording ended"}], open(real, "w"))
check([e["text"] for e in T.build_events(real)] == ["Break start", "Break end"], "clock-time entries are not listed twice")
hints = T.collect_hints(mk, al, "Astratera, Weltentor; Zum Drachen")
check(hints[:3] == ["Taverne Zum Drachen", "Astratera Hafen", "Taverne"] and "Weltentor" in hints, f"hints {hints}")
check(len({h.lower() for h in hints}) == len(hints), "hints unique")
check(T.build_prompt(["Ärgerliche Dämonen", "Köln"]) == "Ärgerliche Dämonen, Köln." and T.build_prompt([]) == "", "prompt")
check(len(T.build_prompt(["x" * 120] * 5)) <= 301, "prompt is cut")

def run(**kw):
    log = os.path.join(root, "calls.log")
    if os.path.exists(log): os.remove(log)
    os.environ["FAKE_LOG"] = log
    msgs, prog = [], []
    args = dict(parts=parts, out_dir=os.path.join(root, "out"), base_name="sess", engine_exe=fake, model=model, language="de",
                hints_text="Ärgerliche Dämonen, Astratera.", vad_model=vadm, use_gpu=True, ffmpeg=ffmpeg,
                markers_path=mk, audio_log_path=al, formats=("txt", "srt", "vtt", "json"), log=msgs.append, progress=prog.append)
    args.update(kw)
    j = T.Job(**args)
    return j, msgs, prog, log
def calls(log):
    return [json.loads(l) for l in open(log, encoding="utf-8")] if os.path.exists(log) else []

# ---- full run
j, msgs, prog, log = run()
res = j.run()
c = calls(log)
check(len(c) == 3, "engine called once per part")
a = c[0]
check(a[a.index("-m") + 1] == model and a[a.index("-l") + 1] == "de" and "-oj" in a and "-pp" in a, "basic args")
check(a[a.index("--prompt") + 1] == "Ärgerliche Dämonen, Astratera." and "--carry-initial-prompt" in a, "umlaut prompt arrives intact (response file)")
check("--vad" in a and a[a.index("-vm") + 1] == vadm and "-ng" not in a, "vad + gpu args")
check(res["gpu"] == "NVIDIA Fake GPU" and res["language"] == "de", "gpu name + language reported")
check(abs(res["duration"] - 62.0) < 0.1 and res["segments"] > 0, f"duration chained {res['duration']}")
out = os.path.join(root, "out")
check(sorted(os.path.basename(f) for f in res["files"]) == ["sess_transcript.json", "sess_transcript.srt", "sess_transcript.txt", "sess_transcript.vtt"], "all four files")
check(not os.path.exists(os.path.join(out, "_work")), "work cache removed after success")
data = json.load(open(os.path.join(out, "sess_transcript.json"), encoding="utf-8"))
starts = [s["start"] for s in data["segments"]]
check(starts == sorted(starts), "segments in time order")
check(any(abs(s - 25.0) < 0.2 for s in starts) and any(abs(s - 50.0) < 0.2 for s in starts), f"part 2 starts at 25 s, part 3 at 50 s {starts}")
check([p["start"] for p in data["parts"]] == [0, 25.0, 50.0], "part offsets")
txt = open(os.path.join(out, "sess_transcript.txt"), encoding="utf-8").read().splitlines()
check(txt[0] == "Transcript: sess" and txt[1] == "Length: 00:01:02", "txt header")
i_scene = next(i for i, l in enumerate(txt) if "Scene: Taverne Zum Drachen" in l)
i_first = next(i for i, l in enumerate(txt) if l.startswith("[00:00:00]"))
i_music = next(i for i, l in enumerate(txt) if "Music: Dark Theme" in l)
check(i_first < i_scene or i_scene < i_first, "scene present")
check(i_music < i_scene, "music at 3 s before scene at 5 s"); check(txt[i_first].startswith("[00:00:00] Satz 1 Ärgerliche Dämonen"), "text with umlauts")
check(any(l.startswith("--- 00:00:41") and "Break start" in l for l in txt) and any("Layout: Karte" in l for l in txt), "break + layout in txt")
check(not any("Sound effect" in l or "Quick Fire" in l for l in txt), "sfx off by default")
srt = open(os.path.join(out, "sess_transcript.srt"), encoding="utf-8").read()
check(srt.startswith("1\n00:00:00,000 --> 00:00:04,000\nSatz 1 Ärgerliche Dämonen\n"), "srt format")
vtt = open(os.path.join(out, "sess_transcript.vtt"), encoding="utf-8").read()
check(vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:04.000"), "vtt format")
check(prog[-1]["stage"] == "done" and prog[-1]["overall_pct"] == 100.0, "progress ends at 100")
check(any(p.get("stage") == "transcribe" and p.get("speed") for p in prog), "speed reported")
check(max(p["overall_pct"] for p in prog[:-1]) <= 100, "pct sane")
check([p["overall_pct"] for p in prog if p["stage"] == "transcribe"][0] < 40, "first part starts at the beginning")

# ---- options: no prompt, no vad, no gpu, other formats, events kinds
j, msgs, prog, log = run(hints_text="", vad_model=None, use_gpu=False, formats=("txt",), event_kinds=("sfx", "quickfire"), out_dir=os.path.join(root, "out2"))
res = j.run(); a = calls(log)[0]
check("--prompt" not in a and "--vad" not in a and "-ng" in a, "no prompt / vad, cpu flag")
check([os.path.basename(f) for f in res["files"]] == ["sess_transcript.txt"], "only txt")
t2 = open(res["files"][0], encoding="utf-8").read()
check("Sound effect: door" in t2 and "Quick Fire: zap" in t2 and "Scene:" not in t2, "chosen event kinds only")
check(res["gpu"] is None, "cpu run: no gpu name")
# ---- no markers
j, *_ , log = run(markers_path=None, audio_log_path=None, out_dir=os.path.join(root, "out3"), formats=("txt",)); res = j.run()
check("---" not in open(res["files"][0], encoding="utf-8").read(), "no events without files")

# ---- noise removal
os.environ["FAKE_NOISE"] = "1"
j, *_ = run(out_dir=os.path.join(root, "out4"), formats=("txt",), parts=parts[:1]); res = j.run(); del os.environ["FAKE_NOISE"]
t4 = open(res["files"][0], encoding="utf-8").read()
check("[Musik]" not in t4 and "Amara" not in t4 and t4.count("Und weiter.") == 2, "tags, credits removed, endless repeats collapsed (a real double stays)")

# ---- GPU failure falls back to CPU, and keeps using it
os.environ["FAKE_GPU_FAIL"] = "1"
j, msgs, prog, log = run(out_dir=os.path.join(root, "out5"), formats=("txt",)); res = j.run(); del os.environ["FAKE_GPU_FAIL"]
c = calls(log)
check(len(c) == 4 and "-ng" not in c[0] and all("-ng" in x for x in c[1:]), f"gpu failed once then cpu for the rest ({len(c)} calls)")
check(any("continuing on the CPU" in m for m in msgs), "user told about the fallback")
# ---- real engine failure
os.environ["FAKE_FAIL"] = "model is broken"
j, *_ = run(out_dir=os.path.join(root, "out6"), use_gpu=False)
try: j.run(); check(False, "needs error")
except T.TranscribeError as e: check("model is broken" in str(e), "engine error shown")
del os.environ["FAKE_FAIL"]

# ---- cancel + resume
os.environ["FAKE_SLOW"] = "0.4"
j, msgs, prog, log = run(out_dir=os.path.join(root, "out7"))
th_res = {}
def go():
    try: j.run()
    except T.Cancelled: th_res["cancelled"] = True
th = threading.Thread(target=go); th.start()
while len([p for p in prog if p.get("part") == 2]) < 2: time.sleep(0.05)
t0 = time.time(); j.cancel(); th.join(5)
check(th_res.get("cancelled") and time.time() - t0 < 2.5, f"cancel stops quickly ({time.time() - t0:.1f}s)")
work = os.path.join(root, "out7", "_work")
check(len([f for f in os.listdir(work) if f.endswith(".json")]) == 1, "part 1 result kept")
check(not [f for f in os.listdir(T.ascii_workdir()) if f.startswith("part_") or f.startswith("out_") or f.endswith(".rsp")], "no temp files left")
os.environ["FAKE_SLOW"] = "0"
j2, msgs2, prog2, log2 = run(out_dir=os.path.join(root, "out7"))
res = j2.run(); c = calls(log2)
check(len(c) == 2, f"resume: only the 2 missing parts run ({len(c)})")
check(any("already done" in m for m in msgs2), "resume message")
check(abs(res["duration"] - 62.0) < 0.1 and res["segments"] > 0, "resumed result complete")
# changed settings must not reuse the cache
os.environ["FAKE_SLOW"] = "0.4"
j, msgs, prog, log = run(out_dir=os.path.join(root, "out8"))
th = threading.Thread(target=lambda: [j.run() if True else 0] and None); 
def go2():
    try: j.run()
    except T.Cancelled: pass
th = threading.Thread(target=go2); th.start()
while len([p for p in prog if p.get("part") == 2]) < 1: time.sleep(0.05)
j.cancel(); th.join(5); os.environ["FAKE_SLOW"] = "0"
j3, msgs3, *_ , log3 = run(out_dir=os.path.join(root, "out8"), language="en"); j3.run()
check(len(calls(log3)) == 3, "other language -> nothing reused")

# ---- errors before starting
for kw, word in (({"parts": []}, "at least one"), ({"parts": [os.path.join(rec, "nope.wav")]}, "not found"),
                 ({"model": os.path.join(root, "none.bin")}, "model")):
    j, *_ = run(out_dir=os.path.join(root, "oute"), **kw)
    try: j.run(); check(False, "needs error")
    except T.TranscribeError as e: check(word in str(e).lower(), f"error '{word}'")
j, *_ = run(out_dir=os.path.join(root, "oute"), engine_exe=os.path.join(root, "missing.exe"))
try: j.run(); check(False, "needs error")
except T.TranscribeError: ok += 1
bad = os.path.join(root, "bad_engine.sh"); open(bad, "w").write("#!/bin/sh\necho hello\n"); os.chmod(bad, 0o755)
j, *_ = run(out_dir=os.path.join(root, "oute"), engine_exe=bad)
try: j.run(); check(False, "needs error")
except T.TranscribeError as e: check("did not start" in str(e), "non-engine executable rejected")
# mp3 input needs ffmpeg conversion
mp3 = os.path.join(rec, "x.mp3"); os.system(f'{ffmpeg} -loglevel error -y -i "{parts[2]}" "{mp3}"')
j, *_ = run(out_dir=os.path.join(root, "outm"), parts=[mp3], markers_path=None, audio_log_path=None, formats=("txt",)); res = j.run()
check(abs(res["duration"] - 12.0) < 0.2, "mp3 converted and measured")
j, *_ = run(out_dir=os.path.join(root, "outm2"), parts=[mp3], ffmpeg=None)
try: j.run(); check(False, "needs error")
except T.TranscribeError as e: check("ffmpeg" in str(e), "mp3 without ffmpeg explained")

# ---- engine / model helpers
rels = [{"tag_name": "v1.9.5", "assets": [{"name": "whisper-1.9.5.tar.gz", "browser_download_url": "x"}]},
        {"tag_name": "b5454", "prerelease": True, "assets": [{"name": "whisper-bin-x64.zip", "browser_download_url": "u1"},
                                                              {"name": "whisper-bin-win-cuda-12.4.0-x64.zip", "browser_download_url": "u2"}]},
        {"tag_name": "b5300", "assets": [{"name": "whisper-cublas-12.4.0-bin-x64.zip", "browser_download_url": "u3"}]},
        {"tag_name": "b9999", "draft": True, "assets": [{"name": "whisper-bin-x64.zip", "browser_download_url": "d"}]}]
check(T.pick_asset(rels, "cuda12")[0] == "b5454" and T.pick_asset(rels, "cpu")[0] == "b5454", "newest release with the right asset (skips source-only + drafts)")
check(T.pick_asset(rels, "cuda11") is None, "no cuda11 -> None")
check(T.pick_asset(rels[2:3], "cuda12")[1]["name"] == "whisper-cublas-12.4.0-bin-x64.zip", "old asset name still found")
check(T.pick_asset([], "cpu") is None and T.pick_asset(None, "cpu") is None, "empty releases")
check(T.model_file("small") == "ggml-small.bin" and T.model_file("x") is None, "model files")
app = os.path.join(root, "app"); os.makedirs(T.models_dir(app))
open(os.path.join(T.models_dir(app), "ggml-tiny.bin"), "wb").write(b"lmgg" + b"\0" * (50 * 1024 * 1024))
open(os.path.join(T.models_dir(app), "ggml-base.bin"), "wb").write(b"<html>error</html>" * 100)
open(os.path.join(T.models_dir(app), "ggml-small.bin"), "wb").write(b"lmgg" + b"\0" * 1000)
check(list(T.installed_models(app)) == ["tiny"], "only valid, complete models count as installed")
check(T.ascii_path(os.path.join(root, "x")) .endswith("x"), "ascii_path passes on non-windows")

# ---- download with resume over a flaky local server
payload = os.urandom(3 * 1024 * 1024 + 123)
state = {"fail_once": True, "ranges": []}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(s, *a): pass
    def do_GET(s):
        if s.path == "/missing": s.send_response(404); s.end_headers(); return
        rng = s.headers.get("Range"); start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        state["ranges"].append(start)
        body = payload[start:]
        s.send_response(206 if rng else 200)
        s.send_header("Content-Length", str(len(body)))
        if rng: s.send_header("Content-Range", f"bytes {start}-{len(payload)-1}/{len(payload)}")
        s.end_headers()
        if state["fail_once"] and not rng:
            state["fail_once"] = False
            s.wfile.write(body[:1024 * 1024]); s.wfile.flush(); s.connection.close(); return
        try: s.wfile.write(body)
        except OSError: pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/f.bin"
seen = []
dest = T.download(url, os.path.join(root, "dl", "f.bin"), lambda d, t: seen.append((d, t)))
check(open(dest, "rb").read() == payload, "download complete after an interrupted connection (resumed)")
check(len(state["ranges"]) >= 2 and state["ranges"][1] > 0, f"resumed with Range {state['ranges']}")
check(seen[-1][0] == len(payload) and not os.path.exists(dest + ".part"), "progress + no .part left")
import hashlib
T.download(url, os.path.join(root, "dl", "g.bin"), sha256=hashlib.sha256(payload).hexdigest()); ok += 1
try: T.download(url, os.path.join(root, "dl", "h.bin"), sha256="0" * 64); check(False, "needs error")
except T.TranscribeError as e: check("checksum" in str(e) and not os.path.exists(os.path.join(root, "dl", "h.bin")), "wrong checksum rejected, file removed")
try: T.download(f"http://127.0.0.1:{srv.server_address[1]}/missing", os.path.join(root, "dl", "m.bin")); check(False, "404")
except T.TranscribeError as e: check("refused" in str(e), "404 explained")
ev = threading.Event(); ev.set()
try: T.download(url, os.path.join(root, "dl", "c.bin"), cancel=ev); check(False, "cancel")
except T.Cancelled: ok += 1

# ---- engine install from a local zip (windows paths simulated)
zsrc = os.path.join(root, "eng.zip")
with zipfile.ZipFile(zsrc, "w") as z:
    z.writestr("Release/whisper-cli.exe", "x"); z.writestr("Release/ggml.dll", "y")
T.is_windows = lambda: True
rels = [{"tag_name": "b5454", "assets": [{"name": "whisper-bin-x64.zip", "browser_download_url": "file://" + zsrc, "size": os.path.getsize(zsrc)}]}]
os.makedirs(T.engine_dir(app), exist_ok=True); os.makedirs(os.path.join(T.engine_dir(app), "old_cpu")); 
info = T.install_engine(app, "cpu", releases=rels)
check(info["tag"] == "b5454" and info["exe"].endswith("whisper-cli.exe") and os.path.isfile(info["exe"]), "engine installed")
check(T.current_engine(app)["tag"] == "b5454" and not os.path.exists(os.path.join(T.engine_dir(app), "old_cpu")), "current engine + old removed")
check(not any(n.endswith(".zip") for n in os.listdir(T.engine_dir(app))), "zip removed")
evil = os.path.join(root, "evil.zip")
with zipfile.ZipFile(evil, "w") as z: z.writestr("../../evil.txt", "x")
rels2 = [{"tag_name": "b1", "assets": [{"name": "whisper-bin-x64.zip", "browser_download_url": "file://" + evil}]}]
try: T.install_engine(app, "cpu", releases=rels2); check(False, "zip slip")
except T.TranscribeError as e: check("unsafe" in str(e), "zip-slip rejected")
check(T.current_engine(app)["tag"] == "b5454", "failed install keeps the working engine")
try: T.install_engine(app, "cpu", releases=[]); check(False, "none")
except T.TranscribeError as e: check("No ready-made build" in str(e), "no build explained")
print("transcriber tests passed:", ok)
