"""
Local transcription of session recordings with whisper.cpp (no window code in here, so it can be tested alone).

What it does
  * installs / finds the whisper.cpp engine (downloaded on demand, never bundled) and the speech models
  * converts every recording part to 16 kHz mono, runs the engine as a SEPARATE PROCESS (a crash cannot take the
    program down), part by part, and keeps every finished part so an interrupted run continues where it stopped
  * chains the parts on one timeline (part 2 starts where part 1 ended)
  * merges scene changes, markers and the audio log (music / sound effects) into the transcript
  * writes txt / srt / vtt / json next to the recording
"""
import atexit
import ctypes
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
import zipfile

RELEASES_URL = "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=40"
MODEL_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
VAD_FILE = "ggml-silero-v5.1.2.bin"
VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/" + VAD_FILE
USER_AGENT = "Astratera-Weltentor"

# key, file, approx. size in MB, description
MODELS = [
    ("large-v3-turbo-q5_0", "ggml-large-v3-turbo-q5_0.bin", 574, "Large v3 Turbo, compact - recommended with a GPU"),
    ("large-v3-turbo", "ggml-large-v3-turbo.bin", 1624, "Large v3 Turbo - fast and very good"),
    ("large-v3", "ggml-large-v3.bin", 3094, "Large v3 - most accurate, slowest"),
    ("medium", "ggml-medium.bin", 1533, "Medium - good, fairly slow on a CPU"),
    ("small", "ggml-small.bin", 488, "Small - recommended without a GPU"),
    ("base", "ggml-base.bin", 148, "Base - fast, rough"),
    ("tiny", "ggml-tiny.bin", 78, "Tiny - only for quick tests"),
]
LANGUAGES = [("auto", "Detect automatically"), ("de", "German"), ("en", "English"), ("fr", "French"),
             ("es", "Spanish"), ("it", "Italian"), ("nl", "Dutch"), ("pl", "Polish"), ("pt", "Portuguese"),
             ("ru", "Russian"), ("tr", "Turkish"), ("cs", "Czech"), ("sv", "Swedish"), ("ja", "Japanese")]
AUDIO_EXT = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".mkv", ".mp4")

# release asset names per engine kind, best first (names changed over time)
ENGINE_ASSETS = {
    "cuda12": ["whisper-bin-win-cuda-12.4.0-x64.zip", "whisper-cublas-12.4.0-bin-x64.zip"],
    "cuda11": ["whisper-bin-win-cuda-11.8.0-x64.zip", "whisper-cublas-11.8.0-bin-x64.zip"],
    "cpu": ["whisper-bin-x64.zip", "whisper-blas-bin-x64.zip"],
}
ENGINE_LABEL = {"cuda12": "NVIDIA GPU (CUDA 12)", "cuda11": "NVIDIA GPU (CUDA 11, older drivers)", "cpu": "CPU only"}
GGML_MAGIC = b"lmgg"


class TranscribeError(Exception):
    """A problem a person can understand. Never contains secrets."""


class Cancelled(TranscribeError):
    pass


def is_windows():
    return os.name == "nt"


# =====================================================================================================
#  Paths
# =====================================================================================================
def data_dir(app_dir):
    return os.path.join(app_dir, "transcription")


def engine_dir(app_dir):
    return os.path.join(data_dir(app_dir), "engine")


def models_dir(app_dir):
    return os.path.join(data_dir(app_dir), "models")


def is_ascii(s):
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def ascii_path(path):
    """whisper-cli reads paths in the Windows code page, so special characters (umlauts...) can break. Returns a
    path made of plain characters (the 8.3 short name on Windows) or raises TranscribeError."""
    if is_ascii(path) or not is_windows():
        return path
    if is_windows():
        try:
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024) and is_ascii(buf.value):
                return buf.value
        except Exception:
            pass
    raise TranscribeError(
        f"The path\n{path}\ncontains special characters the speech engine cannot read. "
        "Move the program folder to a path without umlauts or other special characters (for example C:\\Astratera).")


def ascii_workdir():
    """A folder with a plain-character path for the temporary 16 kHz files."""
    cands = [tempfile.gettempdir()]
    if is_windows():
        cands += [os.environ.get("PUBLIC", r"C:\Users\Public"), os.environ.get("SystemDrive", "C:") + "\\"]
    for base in cands:
        if base and is_ascii(base) and os.path.isdir(base):
            d = os.path.join(base, "astratera_transcribe")
            try:
                os.makedirs(d, exist_ok=True)
                return d
            except OSError:
                continue
    raise TranscribeError("No temporary folder with a plain-character path is available.")


# =====================================================================================================
#  Downloads
# =====================================================================================================
def _open(url, start=0, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if start:
        req.add_header("Range", f"bytes={start}-")
    return urllib.request.urlopen(req, timeout=timeout)


def download(url, dest, progress=None, cancel=None, expected_size=None, sha256=None, retries=5):
    """Downloads to dest (via dest.part, resumable). progress(done_bytes, total_bytes). Raises TranscribeError."""
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    last = None
    for attempt in range(retries):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        try:
            with _open(url, have) as resp:
                code = getattr(resp, "status", 200)
                length = int(resp.headers.get("Content-Length") or 0)
                if code == 206:
                    total = have + length
                    mode = "ab"
                else:
                    total, have, mode = length, 0, "wb"      # server ignored the range: start again
                with open(part, mode) as f:
                    done = have
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled("Cancelled.")
                        block = resp.read(256 * 1024)
                        if not block:
                            break
                        f.write(block)
                        done += len(block)
                        if progress:
                            progress(done, total or expected_size or 0)
            size = os.path.getsize(part)
            if (total and size != total) or (expected_size and not total and size < expected_size * 0.9):
                raise TranscribeError("The download is incomplete.")
            break
        except Cancelled:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 416 and os.path.exists(part):           # nothing left to fetch
                break
            last = f"HTTP {e.code} {e.reason}"
            if e.code in (401, 403, 404):
                raise TranscribeError(f"The download was refused ({last}). Address: {url}")
        except Exception as e:
            last = str(e)
        time.sleep(min(2 * (attempt + 1), 8))
    else:
        raise TranscribeError(f"Download failed: {last}")
    if sha256:
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest().lower() != sha256.lower():
            os.remove(part)
            raise TranscribeError("The downloaded file is damaged (checksum mismatch). Please try again.")
    os.replace(part, dest)
    return dest


# =====================================================================================================
#  Engine (whisper.cpp)
# =====================================================================================================
def detect_nvidia():
    """(gpu name, driver major version) of an NVIDIA GPU, or None."""
    exe = shutil.which("nvidia-smi")
    if not exe and is_windows():
        p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        exe = p if os.path.exists(p) else None
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,driver_version", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=10, **_hidden()).stdout.strip().splitlines()
        name, drv = [x.strip() for x in out[0].split(",")][:2]
        return name, int(drv.split(".")[0])
    except Exception:
        return None


def recommended_kind():
    """'cuda12', 'cuda11' or 'cpu' for this PC."""
    gpu = detect_nvidia()
    if gpu is None:
        return "cpu"
    return "cuda12" if gpu[1] >= 551 else "cuda11"


def pick_asset(releases, kind):
    """First (= newest) release that really carries a build of this kind. Returns (tag, asset dict) or None."""
    names = ENGINE_ASSETS[kind]
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        by_name = {a.get("name"): a for a in rel.get("assets", []) if isinstance(a, dict)}
        for n in names:
            if n in by_name and by_name[n].get("browser_download_url"):
                return rel.get("tag_name", "?"), by_name[n]
    return None


def fetch_releases(url=RELEASES_URL):
    try:
        with _open(url, timeout=20) as resp:
            return json.loads(resp.read(8 * 1024 * 1024).decode("utf-8", "replace"))
    except Exception as e:
        raise TranscribeError(f"Could not look up the speech engine releases on GitHub: {e}")


def find_exe(root):
    want = ("whisper-cli.exe", "whisper-cli")
    for dirpath, _dirs, files in os.walk(root):
        for n in files:
            if n in want:
                return os.path.join(dirpath, n)
    return None


def _safe_extract(zip_path, dest):
    base = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = os.path.realpath(os.path.join(dest, info.filename))
            if target != base and not target.startswith(base + os.sep):
                raise TranscribeError("The engine archive contains an unsafe path.")
        z.extractall(dest)


def current_engine(app_dir):
    """Info of the installed engine {'exe','tag','kind'} or None."""
    meta = os.path.join(engine_dir(app_dir), "current.json")
    try:
        with open(meta, "r", encoding="utf-8") as f:
            info = json.load(f)
        if os.path.isfile(info.get("exe", "")):
            return info
    except Exception:
        pass
    return None


def install_engine(app_dir, kind, progress=None, cancel=None, releases=None):
    """Downloads and unpacks the newest whisper.cpp build of this kind. Returns the engine info dict."""
    if kind not in ENGINE_ASSETS:
        raise TranscribeError("Unknown engine type.")
    if not is_windows():
        raise TranscribeError("Automatic engine download is for Windows. On other systems choose your own "
                              "whisper-cli with 'Use my own engine...'.")
    rels = releases if releases is not None else fetch_releases()
    found = pick_asset(rels, kind)
    if not found:
        raise TranscribeError(f"No ready-made build for '{ENGINE_LABEL[kind]}' was found in the whisper.cpp releases. "
                              "Try again later or use 'Use my own engine...'.")
    tag, asset = found
    root = engine_dir(app_dir)
    os.makedirs(root, exist_ok=True)
    zpath = os.path.join(root, asset["name"])
    sha = None
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        sha = digest.split(":", 1)[1]
    download(asset["browser_download_url"], zpath, progress, cancel, expected_size=asset.get("size"), sha256=sha)
    folder = os.path.join(root, f"{tag}_{kind}")
    tmp = folder + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _safe_extract(zpath, tmp)
        if not find_exe(tmp):
            raise TranscribeError("The downloaded archive does not contain whisper-cli.")
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(tmp, folder)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            os.remove(zpath)
        except OSError:
            pass
    info = {"exe": find_exe(folder), "tag": tag, "kind": kind, "asset": asset["name"]}
    with open(os.path.join(root, "current.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    for d in os.listdir(root):                                   # remove older engines
        p = os.path.join(root, d)
        if os.path.isdir(p) and p != folder:
            shutil.rmtree(p, ignore_errors=True)
    return info


def _hidden():
    """Keep console windows from flashing up on Windows."""
    if is_windows():
        return {"creationflags": 0x08000000}
    return {}


_HELP_CACHE = {}


def engine_help(exe):
    """Help text of the engine, or raises TranscribeError with a readable reason."""
    if exe in _HELP_CACHE:
        return _HELP_CACHE[exe]
    try:
        r = subprocess.run([exe, "--help"], capture_output=True, text=True, errors="replace", timeout=40, **_hidden())
    except FileNotFoundError:
        raise TranscribeError("The speech engine file is missing.")
    except subprocess.TimeoutExpired:
        raise TranscribeError("The speech engine did not respond.")
    except OSError as e:
        raise TranscribeError(f"The speech engine cannot be started: {e}")
    text = (r.stdout or "") + (r.stderr or "")
    if "usage" not in text.lower() or "--model" not in text:
        hint = ""
        if is_windows() and r.returncode in (3221225781, -1073741515):
            hint = " A Windows runtime component seems to be missing (install the 'Microsoft Visual C++ Redistributable')."
        raise TranscribeError("The speech engine did not start properly." + hint)
    _HELP_CACHE[exe] = text
    return text


# =====================================================================================================
#  Models
# =====================================================================================================
def model_file(key):
    for k, f, _mb, _d in MODELS:
        if k == key:
            return f
    return None


def model_path(app_dir, key):
    f = model_file(key)
    return os.path.join(models_dir(app_dir), f) if f else None


def model_ok(path, min_mb=0):
    try:
        if os.path.getsize(path) < max(1, min_mb) * 1024 * 1024 * 0.9 and min_mb:
            return False
        with open(path, "rb") as f:
            return f.read(4) == GGML_MAGIC
    except OSError:
        return False


def installed_models(app_dir):
    out = {}
    for k, f, mb, _d in MODELS:
        p = os.path.join(models_dir(app_dir), f)
        if os.path.exists(p) and model_ok(p, int(mb * 0.5)):
            out[k] = p
    return out


def download_model(app_dir, key, progress=None, cancel=None):
    f = model_file(key)
    if not f:
        raise TranscribeError("Unknown model.")
    mb = next(m[2] for m in MODELS if m[0] == key)
    dest = os.path.join(models_dir(app_dir), f)
    download(MODEL_BASE + f, dest, progress, cancel, expected_size=mb * 1024 * 1024 * 0.95)
    if not model_ok(dest):
        os.remove(dest)
        raise TranscribeError("The downloaded file is not a valid speech model. Please try again.")
    return dest


def vad_path(app_dir):
    return os.path.join(models_dir(app_dir), VAD_FILE)


def download_vad(app_dir, progress=None, cancel=None):
    dest = vad_path(app_dir)
    if not (os.path.exists(dest) and model_ok(dest)):
        download(VAD_URL, dest, progress, cancel)
        if not model_ok(dest):
            os.remove(dest)
            raise TranscribeError("The downloaded silence-detection file is not valid. Please try again.")
    return dest


# =====================================================================================================
#  Audio
# =====================================================================================================
def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def find_parts(path):
    """A folder -> its audio files in natural order; a file -> itself."""
    if os.path.isdir(path):
        names = [n for n in os.listdir(path) if n.lower().endswith(AUDIO_EXT) and not n.startswith(".")]
        return [os.path.join(path, n) for n in sorted(names, key=natural_key)]
    return [path] if os.path.isfile(path) else []


def probe_duration(ffmpeg, path):
    """Length in seconds, or None."""
    if path.lower().endswith(".wav"):
        try:
            with wave.open(path, "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            pass
    if not ffmpeg:
        return None
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace",
                           timeout=60, **_hidden())
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return None


def convert_to_wav16k(ffmpeg, src, dst, cancel=None):
    """16 kHz mono PCM, what the speech engine wants. Returns the exact length in seconds."""
    if not ffmpeg:
        raise TranscribeError("ffmpeg was not found, but it is needed to read this kind of audio file.")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", dst]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace",
                            **_hidden())
    err = []
    t = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    t.start()
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise Cancelled("Cancelled.")
        time.sleep(0.1)
    t.join(2)
    if proc.returncode != 0 or not os.path.exists(dst):
        raise TranscribeError(f"Could not read the audio file {os.path.basename(src)}: "
                              + (err[0].strip()[-300:] if err else "ffmpeg failed"))
    with wave.open(dst, "rb") as w:
        return w.getnframes() / float(w.getframerate())


# =====================================================================================================
#  Recording extras: markers, audio log, hints
# =====================================================================================================
def _load_json_list(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def guess_session_files(first_part):
    """(markers_path, audio_log_path) belonging to a recording part (…_part000.wav), or (None, None)."""
    d, name = os.path.split(first_part)
    stem = os.path.splitext(name)[0]
    base = re.sub(r"_part\d+$", "", stem)
    found = []
    for suffix in ("_markers.json", "_audio_log.json"):
        p = os.path.join(d, base + suffix)
        if not os.path.exists(p):
            c = [n for n in os.listdir(d) if n.endswith(suffix)] if os.path.isdir(d) else []
            p = os.path.join(d, c[0]) if len(c) == 1 else None
        found.append(p)
    return found[0], found[1]


def _clean_name(name):
    name = re.sub(r"\.(wav|mp3|flac|ogg|m4a|aac|opus|mp4|mkv|wma|json)$", "", str(name).strip(), flags=re.I)
    name = re.sub(r"^\d+[\s_.\-]*", "", name).replace("_", " ").strip()
    return name


def build_events(markers_path=None, audio_log_path=None, kinds=("scene", "layout", "break", "marker", "music")):
    """Timeline events: [{'t': seconds, 'kind': ..., 'text': ...}] sorted by time."""
    events = []
    for m in _load_json_list(markers_path) if markers_path else []:
        if not isinstance(m, dict) or not isinstance(m.get("elapsed_seconds"), (int, float)):
            continue
        note = str(m.get("note", "")).strip()
        # RECORDING_START/END and BREAK_START/END are clock-time entries for video sync. The break also has its own
        # "--- BREAK START/END ---" marks, so listing these again would show every break twice.
        if m.get("event") in ("RECORDING_START", "RECORDING_END", "BREAK_START", "BREAK_END"):
            continue
        if note.startswith("Scene:"):
            kind, text = "scene", "Scene: " + _clean_name(note[6:])
        elif note.startswith("Layout:"):
            kind, text = "layout", "Layout: " + _clean_name(note[7:])
        elif note.startswith("---") and "BREAK" in note.upper():
            kind, text = "break", "Break start" if "START" in note.upper() else "Break end"
        else:
            kind, text = "marker", ("Marker: " + note) if note else "Marker"
        if kind in kinds:
            events.append({"t": float(m["elapsed_seconds"]), "kind": kind, "text": text})
    labels = {"music": "Music", "sfx": "Sound effect", "quickfire": "Quick Fire"}
    for a in _load_json_list(audio_log_path) if audio_log_path else []:
        if not isinstance(a, dict) or not isinstance(a.get("elapsed_seconds"), (int, float)):
            continue
        kind = a.get("kind")
        if kind in labels and kind in kinds:
            events.append({"t": float(a["elapsed_seconds"]), "kind": kind,
                           "text": f"{labels[kind]}: {_clean_name(a.get('name', ''))}"})
    events.sort(key=lambda e: e["t"])
    return events


def collect_hints(markers_path=None, audio_log_path=None, extra=""):
    """Names that help the engine spell things right: scene names from this recording + the person's own words."""
    out = []

    def add(x):
        x = _clean_name(x)
        if x and x.lower() not in {o.lower() for o in out}:
            out.append(x)
    for m in _load_json_list(markers_path) if markers_path else []:
        if isinstance(m, dict) and str(m.get("note", "")).startswith("Scene:"):
            add(str(m["note"])[6:])
    for a in _load_json_list(audio_log_path) if audio_log_path else []:
        if isinstance(a, dict) and a.get("scene"):
            add(a["scene"])
    for w in re.split(r"[,;\n]", extra or ""):
        add(w)
    return out


def build_prompt(hints, limit=300):
    """One line for the engine's 'initial prompt'. About 220 tokens fit, so it is cut at a word boundary."""
    prompt = ""
    for h in hints:
        nxt = (prompt + ", " if prompt else "") + h
        if len(nxt) > limit:
            break
        prompt = nxt
    return prompt + "." if prompt else ""


# =====================================================================================================
#  Running the engine
# =====================================================================================================
_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)\s*%")
HALLUCINATIONS = ("untertitel der amara.org", "untertitelung des zdf", "untertitel im auftrag", "amara.org",
                  "thanks for watching", "thank you for watching", "vielen dank fürs zuschauen",
                  "subtitles by", "copyright wdr")


def _is_noise(text):
    t = text.strip()
    if not t:
        return True
    low = t.lower()
    if any(h in low for h in HALLUCINATIONS):
        return True
    # pure sound tags like [Music], (Applause), ♪ ... ♪
    if re.fullmatch(r"[\[\(\*♪♫\s]*[^\]\)\*♪♫]{0,30}[\]\)\*♪♫\s]*", t) and re.search(r"[\[\(\*♪♫]", t) \
            and not re.search(r"[.!?]\s*\w", t):
        return True
    return False


def clean_segments(segments):
    """Drops empty segments, sound tags and the typical 'subtitle credit' hallucinations, and collapses endless
    repeats of the same line (a known failure on silence)."""
    out, run = [], 0
    for s in segments:
        text = s["text"].strip()
        if _is_noise(text):
            continue
        if out and out[-1]["text"] == text:
            run += 1
            if run >= 2:
                out[-1]["end"] = s["end"]
                continue
        else:
            run = 0
        out.append({"start": s["start"], "end": s["end"], "text": text})
    return out


def parse_engine_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    segs = []
    for t in data.get("transcription", []):
        try:
            off = t["offsets"]
            segs.append({"start": off["from"] / 1000.0, "end": off["to"] / 1000.0, "text": str(t.get("text", ""))})
        except Exception:
            continue
    lang = (data.get("result") or {}).get("language")
    return segs, lang


_ACTIVE = set()


def _kill_all():
    """Never leave an engine process running when the program ends."""
    for p in list(_ACTIVE):
        try:
            p.kill()
        except Exception:
            pass


atexit.register(_kill_all)


def _keep_awake(on):
    if is_windows():
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if on else 0x80000000)  # CONTINUOUS (+SYSTEM)
        except Exception:
            pass


def part_key(part):
    st = os.stat(part)
    return hashlib.sha1(f"{os.path.abspath(part)}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()[:10]


def cached_segments(out_dir, part):
    """Passages of a recording part that an earlier run already transcribed (times are inside the part), or None."""
    try:
        key = part_key(part)
        folder = os.path.join(out_dir, "_work")
        names = [n for n in os.listdir(folder) if f"_{key}_" in n and n.endswith(".json")]
    except OSError:
        return None
    names.sort(key=lambda n: os.path.getmtime(os.path.join(folder, n)), reverse=True)
    for n in names:
        try:
            with open(os.path.join(folder, n), "r", encoding="utf-8") as f:
                segs = json.load(f).get("segments")
            if isinstance(segs, list):
                return segs
        except Exception:
            continue
    return None


class Job:
    """One transcription run. Create it, call run() from a helper thread, cancel() from anywhere."""

    def __init__(self, parts, out_dir, base_name, engine_exe, model, language="auto", hints_text="", vad_model=None,
                 use_gpu=True, threads=None, ffmpeg=None, markers_path=None, audio_log_path=None,
                 formats=("txt", "srt"), event_kinds=("scene", "layout", "break", "marker", "music"),
                 progress=None, log=None, engine_tag="", fix_names=(), fix_terms=(), speaker_cfg=None):
        self.parts = list(parts)
        self.out_dir, self.base_name = out_dir, base_name
        self.engine_exe, self.model, self.language = engine_exe, model, language
        self.prompt = hints_text
        self.vad_model, self.use_gpu = vad_model, use_gpu
        self.threads = threads or max(2, min(8, (os.cpu_count() or 4) // 2))
        self.ffmpeg = ffmpeg
        self.markers_path, self.audio_log_path = markers_path, audio_log_path
        self.formats, self.event_kinds = tuple(formats), tuple(event_kinds)
        self.progress, self.log = progress, log
        self.engine_tag = engine_tag
        self.fix_names, self.fix_terms = list(fix_names), list(fix_terms)
        self.corrections = []
        self.speaker_cfg = speaker_cfg            # None = do not label speakers
        self.speaker_summary = []
        self._embedder = None
        self.cancel_event = threading.Event()
        self._proc = None
        self.gpu_failed = False
        self.gpu_name = None
        self.detected_language = None

    # ---- small helpers
    def cancel(self):
        self.cancel_event.set()
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def _say(self, msg):
        logging.info(f"transcribe: {msg}")
        if self.log:
            try:
                self.log(msg)
            except Exception:
                pass

    def _report(self, **kw):
        if self.progress:
            try:
                self.progress(kw)
            except Exception:
                pass

    def _signature(self):
        raw = json.dumps([os.path.basename(self.model), os.path.getsize(self.model), self.language, self.prompt,
                          bool(self.vad_model), self.engine_tag], ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

    def _cache_file(self, cache_dir, index, part, sig):
        return os.path.join(cache_dir, f"{index:03d}_{part_key(part)}_{sig}.json")

    # ---- the engine
    def _command(self, wav, outbase, gpu):
        help_text = engine_help(self.engine_exe)
        args = ["-m", ascii_path(self.model), "-f", wav, "-oj", "-of", outbase, "-pp", "-t", str(self.threads)]
        args += ["-l", self.language if self.language else "auto"]
        if self.prompt:
            args += ["--prompt", self.prompt]
            if "--carry-initial-prompt" in help_text:
                args += ["--carry-initial-prompt"]
        if self.vad_model and "--vad" in help_text:
            args += ["--vad", "-vm", ascii_path(self.vad_model)]
        if not gpu and ("-ng" in help_text or "--no-gpu" in help_text):
            args += ["-ng"]
        return args

    def _run_engine(self, args, total_audio, on_pct):
        use_rsp = not all(is_ascii(a) for a in args)
        rsp = None
        cmd = [self.engine_exe] + args
        if use_rsp:                                    # umlauts in the prompt: pass them as UTF-8 via a response file
            rsp = os.path.join(ascii_workdir(), f"args_{os.getpid()}_{int(time.time() * 1000)}.rsp")
            with open(rsp, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(args) + "\n")
            cmd = [self.engine_exe, "@" + rsp]
        flags = _hidden().get("creationflags", 0)
        if is_windows():
            flags |= 0x00004000                        # below normal priority: the PC stays usable
        kw = {"creationflags": flags} if flags else {}
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1, **kw)
        except OSError as e:
            raise TranscribeError(f"The speech engine cannot be started: {e}")
        self._proc = proc
        _ACTIVE.add(proc)
        tail, gpu_seen = [], {"name": None, "none": False}

        def reader():
            for line in proc.stderr:
                line = line.rstrip()
                m = _PROGRESS_RE.search(line)
                if m:
                    try:
                        on_pct(min(100, int(m.group(1))))
                    except Exception:
                        pass
                    continue
                tail.append(line)
                del tail[:-40]
                if "no GPU found" in line:
                    gpu_seen["none"] = True
                m = re.search(r"whisper_backend_init_gpu: device \d+: (.+?) \(type", line)
                if m and "CPU" not in m.group(1):
                    gpu_seen["name"] = m.group(1)
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            while proc.poll() is None:
                if self.cancel_event.is_set():
                    proc.kill()
                    raise Cancelled("Cancelled.")
                time.sleep(0.2)
            t.join(3)
        finally:
            self._proc = None
            _ACTIVE.discard(proc)
            if rsp:
                try:
                    os.remove(rsp)
                except OSError:
                    pass
        if gpu_seen["name"]:
            self.gpu_name = gpu_seen["name"]
        return proc.returncode, [l for l in tail if l.strip()], gpu_seen

    def _transcribe_wav(self, wav, outbase, audio_seconds, on_pct):
        """Runs the engine for one file, falling back to the CPU if the GPU part fails. Returns segments, language."""
        gpu = self.use_gpu and not self.gpu_failed
        for attempt in (0, 1):
            rc, tail, gpu_seen = self._run_engine(self._command(wav, outbase, gpu), audio_seconds, on_pct)
            out_json = outbase + ".json"
            if rc == 0 and os.path.exists(out_json):
                if gpu and gpu_seen["none"] and not self.gpu_name:
                    self._say("No GPU was found by the engine; running on the CPU.")
                return parse_engine_json(out_json)
            if gpu and attempt == 0:
                self.gpu_failed = True
                gpu = False
                self._say("The GPU run failed; continuing on the CPU. " + (tail[-1] if tail else ""))
                continue
            reason = " | ".join(tail[-3:]) if tail else f"exit code {rc}"
            raise TranscribeError(f"The speech engine stopped with an error: {reason}")
        raise TranscribeError("The speech engine failed.")

    # ---- the whole job
    def run(self):
        if not self.parts:
            raise TranscribeError("Choose at least one audio file.")
        for p in self.parts:
            if not os.path.isfile(p):
                raise TranscribeError(f"File not found: {p}")
        if not os.path.isfile(self.model):
            raise TranscribeError("The speech model is not installed.")
        engine_help(self.engine_exe)
        os.makedirs(self.out_dir, exist_ok=True)
        cache_dir = os.path.join(self.out_dir, "_work")
        os.makedirs(cache_dir, exist_ok=True)
        work = ascii_workdir()
        sig = self._signature()
        _keep_awake(True)
        try:
            return self._run(cache_dir, work, sig)
        finally:
            _keep_awake(False)

    def _run(self, cache_dir, work, sig):
        n = len(self.parts)
        durations = []
        for p in self.parts:
            durations.append(probe_duration(self.ffmpeg, p) or 1800.0)
        total_audio = sum(durations) or 1.0
        done_audio, processed_audio, t_start = 0.0, 0.0, time.time()
        all_segments, offset, language = [], 0.0, None
        meta_parts = []
        voices = []
        for i, part in enumerate(self.parts):
            if self.cancel_event.is_set():
                raise Cancelled("Cancelled.")
            name = os.path.basename(part)
            cache = self._cache_file(cache_dir, i, part, sig)
            data = None
            if os.path.exists(cache):
                try:
                    with open(cache, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._say(f"Part {i + 1}/{n} ({name}): already done, reusing the result.")
                except Exception:
                    data = None
            if data is None:
                self._report(stage="convert", part=i + 1, parts=n, part_pct=0, overall_pct=100 * done_audio / total_audio,
                             message=f"Part {i + 1}/{n}: preparing {name}")
                wav = os.path.join(work, f"part_{os.getpid()}_{i}.wav")
                outbase = os.path.join(work, f"out_{os.getpid()}_{i}")
                try:
                    if self.ffmpeg:
                        dur = convert_to_wav16k(self.ffmpeg, part, wav, self.cancel_event)
                    elif part.lower().endswith(".wav"):
                        shutil.copyfile(part, wav)
                        dur = probe_duration(None, wav) or durations[i]
                    else:
                        raise TranscribeError("ffmpeg was not found, but it is needed to read this kind of audio file.")
                    durations[i] = dur
                    total_audio = sum(durations)
                    part_started = time.time()

                    def on_pct(pct, i=i, dur=dur, ds=done_audio, ts=total_audio):
                        elapsed = max(0.001, time.time() - t_start)
                        proc_audio = processed_audio + dur * pct / 100.0
                        speed = proc_audio / elapsed
                        remaining = max(0.0, ts - (ds + dur * pct / 100.0))
                        self._report(stage="transcribe", part=i + 1, parts=n, part_pct=pct,
                                     overall_pct=100 * (ds + dur * pct / 100.0) / ts, speed=speed,
                                     eta_s=(remaining / speed) if speed > 0 and proc_audio > 5 else None,
                                     gpu=self.gpu_name, message=f"Part {i + 1}/{n}: {name}")
                    self._say(f"Part {i + 1}/{n} ({name}): transcribing {dur / 60:.1f} min of audio...")
                    segs, lang = self._transcribe_wav(wav, outbase, dur, on_pct)
                finally:
                    for f in (wav, outbase + ".json"):
                        try:
                            os.remove(f)
                        except OSError:
                            pass
                data = {"duration": dur, "language": lang, "segments": clean_segments(segs)}
                tmp = cache + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, cache)
                processed_audio += dur
                self._say(f"Part {i + 1}/{n}: {len(data['segments'])} passages "
                          f"({(time.time() - part_started) / 60:.1f} min).")
            dur = float(data["duration"])
            durations[i] = dur
            language = language or data.get("language")
            if self.speaker_cfg:
                voices.append(self._part_voices(i, part, data["segments"], cache, work, n))
            for s in data["segments"]:
                all_segments.append({"start": s["start"] + offset, "end": s["end"] + offset, "text": s["text"]})
            meta_parts.append({"file": name, "start": offset, "duration": dur})
            offset += dur
            done_audio += dur
        self.detected_language = language
        all_segments = self._fix_spelling(all_segments)
        if self.speaker_cfg:
            self._label_speakers(all_segments, voices)
        files = self._write_outputs(all_segments, meta_parts, offset, language)
        self._report(stage="done", part=n, parts=n, part_pct=100, overall_pct=100.0, message="Done")
        if self.speaker_cfg:
            self._say("The working files stay in the '_work' folder, so changing the players and starting again only "
                      "takes seconds. Delete the folder to free the space.")
        else:
            shutil.rmtree(os.path.join(self.out_dir, "_work"), ignore_errors=True)
        return {"files": files, "segments": len(all_segments), "duration": offset, "language": language,
                "gpu": self.gpu_name, "speakers": self.speaker_summary}

    # ---- who is speaking
    def _part_voices(self, i, part, segments, cache, work, n):
        """Voice prints of the passages of one part (kept next to the transcript, so they are made only once)."""
        import speakers as S
        vf = cache[:-5] + f".{S.MODEL_TAG}.npy"
        arr = S.load_voices(vf, len(segments)) if os.path.exists(vf) else None
        if arr is not None:
            return arr
        if self._embedder is None:
            self._embedder = S.Embedder(self.speaker_cfg["app_dir"], self.threads)
        name = os.path.basename(part)
        wav = os.path.join(work, f"voice_{os.getpid()}_{i}.wav")
        try:
            if self.ffmpeg:
                convert_to_wav16k(self.ffmpeg, part, wav, self.cancel_event)
            elif part.lower().endswith(".wav"):
                shutil.copyfile(part, wav)
            else:
                raise TranscribeError("ffmpeg was not found, but it is needed to read this kind of audio file.")
            self._say(f"Part {i + 1}/{n} ({name}): recognising voices in {len(segments)} passages...")
            self._report(stage="speakers", part=i + 1, parts=n, part_pct=0, message=f"Part {i + 1}/{n}: recognising voices")

            def prog(done, total, i=i):
                self._report(stage="speakers", part=i + 1, parts=n, part_pct=int(100 * done / max(1, total)),
                             message=f"Part {i + 1}/{n}: recognising voices ({done}/{total})")
            arr = S.embed_segments(self._embedder, wav, segments, self.cancel_event, prog)
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass
        S.save_voices(vf, arr)
        return arr

    def _label_speakers(self, segments, voices):
        import numpy as np
        import speakers as S
        cfg = self.speaker_cfg
        if not segments:
            return
        embs = np.vstack(voices) if voices else np.zeros((0, 1), dtype=np.float32)
        if embs.shape[0] != len(segments):
            self._say("Speaker labels skipped: the voice data does not match the transcript.")
            return
        cents = {}
        if cfg.get("use_players", True):
            try:
                if self._embedder is None:
                    self._embedder = S.Embedder(cfg["app_dir"], self.threads)
                cents = S.Profiles(cfg["app_dir"]).centroids(self._embedder)
            except S.SpeakerError as e:
                self._say(f"Players could not be used: {e}")
        labels, summary = S.label_segments(segments, embs, cents or None, cfg.get("num_speakers"),
                                           cfg.get("min_score", S.DEFAULT_MIN_SCORE), cfg.get("margin", S.DEFAULT_MARGIN))
        for seg, lab in zip(segments, labels):
            seg["speaker"] = lab
        self.speaker_summary = summary
        total = sum(t["seconds"] for t in summary) or 1.0
        mode = f"{len(cents)} players" if cents else "no players, voices grouped automatically"
        self._say(f"Speakers ({mode}): " + "; ".join(f"{t['name']} {100 * t['seconds'] / total:.0f}%" for t in summary))

    def _fix_spelling(self, segments):
        """Repairs near-misses of the person's words in the finished transcript (the cache keeps the raw text)."""
        self.corrections = []
        if not (self.fix_names or self.fix_terms):
            return segments
        try:
            import vocab
            fixed, corr = vocab.fix_spelling(segments, self.fix_names, self.fix_terms)
        except Exception as e:
            self._say(f"Spelling repair skipped: {e}")
            return segments
        self.corrections = corr
        if corr:
            total = sum(c["count"] for c in corr)
            top = "; ".join(f"{c['from']} -> {c['to']}" + (f" (x{c['count']})" if c["count"] > 1 else "") for c in corr[:12])
            self._say(f"Spelling repaired in {total} places: {top}" + (f"; and {len(corr) - 12} more" if len(corr) > 12 else ""))
        else:
            self._say("Spelling repair: nothing to change.")
        return fixed

    # ---- output
    def _write_outputs(self, segments, meta_parts, total, language):
        events = build_events(self.markers_path, self.audio_log_path, self.event_kinds) \
            if (self.markers_path or self.audio_log_path) else []
        events = [e for e in events if e["t"] <= total + 5]
        files = []
        base = os.path.join(self.out_dir, f"{self.base_name}_transcript")

        def write(ext, text):
            path = f"{base}.{ext}"
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, path)
            files.append(path)
        if "txt" in self.formats:
            write("txt", render_txt(self.base_name, segments, events, total))
        if "srt" in self.formats:
            write("srt", render_srt(segments))
        if "vtt" in self.formats:
            write("vtt", render_vtt(segments))
        if "json" in self.formats:
            write("json", json.dumps({"recording": self.base_name, "language": language, "model": os.path.basename(self.model),
                                      "parts": meta_parts, "duration": total, "segments": segments, "events": events,
                                      "corrections": self.corrections, "speakers": self.speaker_summary},
                                     ensure_ascii=False, indent=2))
        return files


# =====================================================================================================
#  Writers
# =====================================================================================================
def hms(seconds):
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _ts(seconds, sep):
    ms = int(round(max(0.0, seconds) * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d}{sep}{ms % 1000:03d}"


def _who(seg):
    """'Anna: ' in front of a passage when the speaker is known."""
    return f"{seg['speaker']}: " if seg.get("speaker") else ""


def render_txt(title, segments, events, total=None):
    """Speech lines '[hh:mm:ss] text' with the scene changes, markers and music placed where they happened."""
    items = [(s["start"], 1, s) for s in segments] + [(e["t"], 0, e) for e in events]
    items.sort(key=lambda x: (x[0], x[1]))
    lines = [f"Transcript: {title}"]
    if total:
        lines.append(f"Length: {hms(total)}")
    lines.append("")
    for t, kind, it in items:
        if kind == 0:
            lines.append(f"--- {hms(t)}  {it['text']} ---")
        else:
            lines.append(f"[{hms(t)}] {_who(it)}{it['text']}")
    return "\n".join(lines).rstrip() + "\n"


def render_srt(segments):
    out = []
    for i, s in enumerate(segments, 1):
        end = max(s["end"], s["start"] + 0.5)
        out.append(f"{i}\n{_ts(s['start'], ',')} --> {_ts(end, ',')}\n{_who(s)}{s['text']}\n")
    return "\n".join(out)


def render_vtt(segments):
    out = ["WEBVTT", ""]
    for s in segments:
        end = max(s["end"], s["start"] + 0.5)
        out.append(f"{_ts(s['start'], '.')} --> {_ts(end, '.')}\n{_who(s)}{s['text']}\n")
    return "\n".join(out)
