#!/usr/bin/env python3
"""Pretend whisper-cli for tests: same options, same output files, no real recognition."""
import json, os, sys, time, wave

HELP = """usage: whisper-cli [options] file0 file1 ...
  -m FNAME,  --model FNAME   model path
  -f FNAME,  --file FNAME    input audio
  -ng,       --no-gpu        disable GPU
  --prompt PROMPT
  --carry-initial-prompt
  --vad
  -vm FNAME, --vad-model FNAME
"""
argv = sys.argv[1:]
if argv and argv[0].startswith("@"):
    with open(argv[0][1:], "r", encoding="utf-8") as f:
        argv = [l.rstrip("\n") for l in f]
if "--help" in argv or "-h" in argv:
    sys.stderr.write(HELP); sys.exit(0)
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(argv, ensure_ascii=False) + "\n")
def opt(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default
wav, outbase, lang, prompt = opt("-f"), opt("-of"), opt("-l", "en"), opt("--prompt", "")
if os.environ.get("FAKE_GPU_FAIL") and "-ng" not in argv:
    sys.stderr.write("CUDA error: no kernel image is available\n"); sys.exit(3)
if os.environ.get("FAKE_FAIL"):
    sys.stderr.write("fatal: " + os.environ["FAKE_FAIL"] + "\n"); sys.exit(2)
if "-ng" in argv:
    sys.stderr.write("whisper_backend_init_gpu: no GPU found\n")
else:
    sys.stderr.write("whisper_backend_init_gpu: device 0: NVIDIA Fake GPU (type: 1)\n")
with wave.open(wav, "rb") as w:
    assert w.getframerate() == 16000 and w.getnchannels() == 1, "engine wants 16 kHz mono"
    dur = w.getnframes() / 16000.0
slow = float(os.environ.get("FAKE_SLOW", "0"))
for p in range(0, 101, 20):
    sys.stderr.write(f"whisper_print_progress_callback: progress = {p:3d}%\n"); sys.stderr.flush()
    time.sleep(slow)
segs, t, k = [], 0.0, 1
word = prompt.split(",")[0].strip(" .") if prompt else "ohne"
while t + 4 <= dur:
    segs.append({"timestamps": {}, "offsets": {"from": int(t * 1000), "to": int((t + 4) * 1000)},
                 "text": f" Satz {k} {word}"})
    t += 10; k += 1
if os.environ.get("FAKE_NOISE"):
    segs.append({"timestamps": {}, "offsets": {"from": 0, "to": 1000}, "text": " [Musik]"})
    segs.append({"timestamps": {}, "offsets": {"from": 1000, "to": 2000}, "text": " Untertitel der Amara.org-Community"})
    for i in range(6):
        segs.append({"timestamps": {}, "offsets": {"from": 2000 + i * 100, "to": 2100 + i * 100}, "text": " Und weiter."})
with open(outbase + ".json", "w", encoding="utf-8") as f:
    json.dump({"result": {"language": "de" if lang in ("auto", "de") else lang}, "transcription": segs}, f, ensure_ascii=False)
