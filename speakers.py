"""
Who is speaking? (no window code in here, so it can be tested alone)

How it works, in short:
  * the speech engine has already cut the recording into passages with times
  * for every passage a "voice print" (256 numbers describing the voice) is computed with a small speaker model
  * players: a few voice samples per player give one voice print per player; every passage is given to the player
    whose voice print is closest, or to nobody ("Unknown") when it is not close enough or two players are too close
  * no players: the voice prints are grouped, giving "Speaker 1", "Speaker 2", ...

The speaker model is NVIDIA's TitaNet-small (CC-BY-4.0), run with sherpa-onnx.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave

try:
    import numpy as np
except Exception:                                  # pragma: no cover
    np = None

import transcriber as T

MODEL_FILE = "nemo_en_titanet_small.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "nemo_en_titanet_small.onnx")
MODEL_SIZE = 40257283
MODEL_TAG = "titanet-s"                            # part of cache names: a different model never reuses old prints

SR = 16000
MIN_SECONDS = 0.6          # shorter passages ("Ja.") are too short for a reliable voice print
WINDOW_SECONDS = 8.0       # longer passages are measured in pieces of this length
MIN_SAMPLE_SECONDS = 1.5   # voice samples shorter than this are rejected
MAX_SAMPLE_SECONDS = 120.0

DEFAULT_MIN_SCORE = 0.35   # how close a passage must be to a player's voice (0..1)
DEFAULT_MARGIN = 0.08      # how much closer to the best player than to the second best
MERGE_AT = 0.45            # (no players) voices closer than this are the same speaker
LABEL_UNKNOWN = "Unknown"


class SpeakerError(T.TranscribeError):
    """A problem a person can understand."""


# =====================================================================================================
#  Availability and model
# =====================================================================================================
def available():
    """(True, '') when sherpa-onnx and numpy can be used, else (False, reason)."""
    if np is None:
        return False, "numpy is missing."
    try:
        import sherpa_onnx  # noqa: F401
    except Exception as e:
        return False, f"The speaker recognition component (sherpa-onnx) is not part of this program version ({e})."
    return True, ""


def model_path(app_dir):
    return os.path.join(T.data_dir(app_dir), "speakers", MODEL_FILE)


def model_installed(app_dir):
    p = model_path(app_dir)
    return os.path.isfile(p) and os.path.getsize(p) > MODEL_SIZE * 0.95


def download_model(app_dir, progress=None, cancel=None):
    dest = model_path(app_dir)
    if not model_installed(app_dir):
        T.download(MODEL_URL, dest, progress, cancel, expected_size=MODEL_SIZE)
        if not model_installed(app_dir):
            try:
                os.remove(dest)
            except OSError:
                pass
            raise SpeakerError("The downloaded speaker model is not valid. Please try again.")
    return dest


# =====================================================================================================
#  Audio helpers
# =====================================================================================================
def _hidden():
    return T._hidden()


def read_wav_range(path, start, end):
    """Samples (float32, -1..1) of a 16 kHz mono wav between two times in seconds."""
    with wave.open(path, "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if width != 2:
            raise SpeakerError("Only 16 bit audio can be read here.")
        a = max(0, int(start * sr))
        b = min(w.getnframes(), int(end * sr))
        if b <= a:
            return np.zeros(0, dtype=np.float32)
        w.setpos(a)
        raw = w.readframes(b - a)
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if sr != SR:
        x = resample(x, sr)
    return x


def resample(x, sr):
    if sr == SR or len(x) == 0:
        return x
    n = int(len(x) * SR / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def read_clip(ffmpeg, path, start, length):
    """Any audio file -> mono 16 kHz samples of [start, start+length) seconds. Uses ffmpeg when it is there."""
    length = max(0.1, float(length))
    start = max(0.0, float(start))
    if ffmpeg:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path,
               "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=300, **_hidden())
        except Exception as e:
            raise SpeakerError(f"Could not read {os.path.basename(path)}: {e}")
        if r.returncode != 0 or not r.stdout:
            msg = r.stderr.decode("utf-8", "replace").strip()[-200:] if r.stderr else ""
            raise SpeakerError(f"Could not read {os.path.basename(path)}: {msg}" if msg else
                               "There is no audio at that time. The recording is shorter.")
        return np.frombuffer(r.stdout[: len(r.stdout) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    if path.lower().endswith(".wav"):
        return read_wav_range(path, start, start + length)
    raise SpeakerError("ffmpeg was not found, but it is needed to read this kind of audio file.")


def write_wav(path, samples):
    x = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


# =====================================================================================================
#  Voice prints
# =====================================================================================================
class Embedder:
    """Turns audio into voice prints (unit length numpy vectors)."""

    def __init__(self, app_dir, threads=2):
        ok, why = available()
        if not ok:
            raise SpeakerError(why)
        if not model_installed(app_dir):
            raise SpeakerError("The speaker model is not downloaded yet.")
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=T.ascii_path(model_path(app_dir)),
                                                          num_threads=max(1, int(threads)))
        try:
            self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        except Exception as e:
            raise SpeakerError(f"The speaker model could not be loaded: {e}")
        self.dim = int(self._ex.dim)

    def _one(self, x):
        st = self._ex.create_stream()
        st.accept_waveform(sample_rate=SR, waveform=np.ascontiguousarray(x, dtype=np.float32))
        st.input_finished()
        v = np.asarray(self._ex.compute(st), dtype=np.float32)
        n = float(np.linalg.norm(v))
        if not np.isfinite(n) or n < 1e-6:
            return None
        return v / n

    def embed(self, samples):
        """Voice print of the samples, or None when they are too short or silent. Long audio is measured in pieces."""
        x = np.asarray(samples, dtype=np.float32)
        if len(x) < MIN_SECONDS * SR:
            return None
        win = int(WINDOW_SECONDS * SR)
        if len(x) <= win * 1.5:
            return self._one(x)
        parts = []
        for a in range(0, len(x), win):
            piece = x[a:a + win]
            if len(piece) < MIN_SECONDS * SR:
                continue
            v = self._one(piece)
            if v is not None:
                parts.append((v, len(piece)))
        if not parts:
            return None
        m = sum(v * w for v, w in parts)
        n = float(np.linalg.norm(m))
        return (m / n).astype(np.float32) if n > 1e-6 else None


def embed_segments(embedder, wav_path, segments, cancel=None, progress=None):
    """(n, dim) array, one row per passage; rows that could not be measured are NaN."""
    out = np.full((len(segments), embedder.dim), np.nan, dtype=np.float32)
    last = 0.0
    for i, s in enumerate(segments):
        if cancel is not None and cancel.is_set():
            raise T.Cancelled("Cancelled.")
        if s["end"] - s["start"] >= MIN_SECONDS:
            v = embedder.embed(read_wav_range(wav_path, s["start"], s["end"]))
            if v is not None:
                out[i] = v
        if progress and time.time() - last > 0.5:
            last = time.time()
            progress(i + 1, len(segments))
    if progress:
        progress(len(segments), len(segments))
    return out


def save_voices(path, arr):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        np.save(f, arr.astype(np.float32))
    os.replace(tmp, path)


def load_voices(path, expected_rows):
    try:
        arr = np.load(path)
    except Exception:
        return None
    return arr if arr.ndim == 2 and arr.shape[0] == expected_rows else None


# =====================================================================================================
#  Players and their voice samples
# =====================================================================================================
def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


class Profiles:
    """The players and their voice samples. Samples are kept as small wav files, so the voice prints can always be
    recomputed (for example after a model change). Folder: <app>/transcription/speakers/players."""

    def __init__(self, app_dir):
        self.root = os.path.join(T.data_dir(app_dir), "speakers", "players")
        self.path = os.path.join(self.root, "players.json")
        self.data = {"players": []}
        self._lock = threading.Lock()
        self.load()

    # ---- storage
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("players"), list):
                self.data = d
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.warning(f"Could not read the players: {e}")

    def save(self):
        os.makedirs(self.root, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---- players
    def players(self):
        return list(self.data["players"])

    def get(self, pid):
        for p in self.data["players"]:
            if p["id"] == pid:
                return p
        return None

    @staticmethod
    def _clean(name):
        return re.sub(r"\s+", " ", str(name or "")).strip(" :,;")[:40]

    def _name_taken(self, name, skip=None):
        return any(p["name"].lower() == name.lower() and p["id"] != skip for p in self.data["players"])

    def add_player(self, name):
        name = self._clean(name)
        if not name:
            raise SpeakerError("Please type a name.")
        if name.lower() == LABEL_UNKNOWN.lower():
            raise SpeakerError(f"'{name}' is used for passages nobody matches. Please pick another name.")
        if self._name_taken(name):
            raise SpeakerError(f"There is already a player called {name}.")
        p = {"id": uuid.uuid4().hex[:8], "name": name, "samples": []}
        self.data["players"].append(p)
        self.save()
        return p

    def rename(self, pid, name):
        p = self.get(pid)
        name = self._clean(name)
        if not p or not name:
            raise SpeakerError("Please type a name.")
        if self._name_taken(name, skip=pid):
            raise SpeakerError(f"There is already a player called {name}.")
        p["name"] = name
        self.save()

    def remove_player(self, pid):
        p = self.get(pid)
        if not p:
            return
        shutil.rmtree(os.path.join(self.root, pid), ignore_errors=True)
        self.data["players"] = [q for q in self.data["players"] if q["id"] != pid]
        self.save()

    # ---- samples
    def sample_file(self, pid, sample):
        return os.path.join(self.root, pid, sample["file"])

    def add_sample(self, pid, samples, source=""):
        """Stores audio (16 kHz mono floats) as a sample of the player. Returns the sample entry."""
        p = self.get(pid)
        if not p:
            raise SpeakerError("This player does not exist any more.")
        x = np.asarray(samples, dtype=np.float32)
        if len(x) < MIN_SAMPLE_SECONDS * SR:
            raise SpeakerError(f"That is too short ({len(x) / SR:.1f} s). Samples need at least {MIN_SAMPLE_SECONDS:.0f} seconds of speech.")
        if len(x) > MAX_SAMPLE_SECONDS * SR:
            x = x[: int(MAX_SAMPLE_SECONDS * SR)]
        if float(np.sqrt(np.mean(x ** 2))) < 0.003:
            raise SpeakerError("That part is silent.")
        name = f"s_{uuid.uuid4().hex[:8]}.wav"
        write_wav(os.path.join(self.root, pid, name), x)
        s = {"file": name, "seconds": round(len(x) / SR, 2), "source": str(source)[:200]}
        p["samples"].append(s)
        p.pop("emb", None)
        self.save()
        return s

    def remove_sample(self, pid, file):
        p = self.get(pid)
        if not p:
            return
        p["samples"] = [s for s in p["samples"] if s["file"] != file]
        try:
            os.remove(os.path.join(self.root, pid, file))
        except OSError:
            pass
        p.pop("emb", None)
        self.save()

    def seconds(self, pid):
        p = self.get(pid)
        return sum(float(s.get("seconds", 0)) for s in p["samples"]) if p else 0.0

    @staticmethod
    def quality(seconds):
        """(word, hint) for the amount of sample audio."""
        if seconds < 1:
            return "none", "No samples yet."
        if seconds < 15:
            return "little", "A bit short. 30 seconds or more works best."
        if seconds < 30:
            return "ok", "Good. A little more makes it more reliable."
        return "good", "Plenty."

    # ---- voice prints
    def centroid(self, pid, embedder):
        """The voice print of one player (cached until the samples change)."""
        p = self.get(pid)
        if not p or not p["samples"]:
            return None
        sig = [MODEL_TAG, sorted(s["file"] for s in p["samples"])]
        emb = p.get("emb")
        if emb and emb.get("sig") == sig and len(emb.get("vec", [])) == embedder.dim:
            return np.asarray(emb["vec"], dtype=np.float32)
        vecs, weights = [], []
        for s in p["samples"]:
            try:
                x = read_wav_range(self.sample_file(pid, s), 0, 1e9)
            except Exception as e:
                logging.warning(f"Sample {s['file']} could not be read: {e}")
                continue
            win = int(WINDOW_SECONDS * SR)
            for a in range(0, max(1, len(x)), win):
                piece = x[a:a + win]
                if len(piece) < MIN_SECONDS * SR:
                    continue
                v = embedder._one(piece)
                if v is not None:
                    vecs.append(v)
                    weights.append(len(piece))
        if not vecs:
            return None
        c = _unit(sum(v * w for v, w in zip(vecs, weights)))
        p["emb"] = {"sig": sig, "vec": [float(t) for t in c]}
        self.save()
        return c

    def centroids(self, embedder):
        """{name: voice print} of all players that have samples."""
        out = {}
        for p in self.players():
            c = self.centroid(p["id"], embedder)
            if c is not None:
                out[p["name"]] = c
        return out


def compare_players(centroids):
    """[(name_a, name_b, similarity)] sorted from most to least similar. Close pairs get mixed up easily."""
    names = list(centroids)
    out = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            out.append((names[i], names[j], float(np.dot(centroids[names[i]], centroids[names[j]]))))
    out.sort(key=lambda t: -t[2])
    return out


# =====================================================================================================
#  Giving passages to speakers
# =====================================================================================================
def _valid(embs):
    return ~np.isnan(embs).any(axis=1)


def _assign_to_players(embs, centroids, min_score, margin):
    names = list(centroids)
    C = np.stack([centroids[n] for n in names])
    labels = [None] * len(embs)
    ok = _valid(embs)
    if not ok.any():
        return labels
    sims = embs[ok] @ C.T
    idx = np.flatnonzero(ok)
    for row, i in zip(sims, idx):
        order = np.argsort(-row)
        best = float(row[order[0]])
        second = float(row[order[1]]) if len(order) > 1 else -1.0
        if best >= min_score and (best - second) >= margin:
            labels[i] = names[int(order[0])]
    return labels


def cluster_voices(embs, durations, num_speakers=None, merge_at=MERGE_AT):
    """Groups voice prints without knowing the speakers. Returns a list of cluster numbers (or None) per passage."""
    n = len(embs)
    ok = _valid(embs)
    labels = [None] * n
    if not ok.any():
        return labels
    durations = np.asarray(durations, dtype=np.float64)
    reliable = np.flatnonzero(ok & (durations >= 1.5))
    if len(reliable) < 3:
        reliable = np.flatnonzero(ok)
    if len(reliable) > 1500:                               # the longest passages carry the clearest voices
        reliable = reliable[np.argsort(-durations[reliable])[:1500]]
    # 1) leader clustering into many small groups (fast)
    sums, wts = [], []
    for i in reliable:
        v = embs[i]
        if sums:
            cs = np.stack([_unit(s) for s in sums])
            sim = cs @ v
            j = int(np.argmax(sim))
            if sim[j] >= 0.7:
                sums[j] = sums[j] + v * durations[i]
                wts[j] += durations[i]
                continue
        sums.append(v * durations[i])
        wts.append(durations[i])
    # 2) merge the closest groups until they are far enough apart (or the wanted number is reached)
    k_target = int(num_speakers) if num_speakers else None
    while len(sums) > 1:
        cs = np.stack([_unit(s) for s in sums])
        sim = cs @ cs.T
        np.fill_diagonal(sim, -2)
        a, b = np.unravel_index(int(np.argmax(sim)), sim.shape)
        best = float(sim[a, b])
        if k_target is not None:
            if len(sums) <= k_target:
                break
        elif best < merge_at:
            break
        sums[a] = sums[a] + sums[b]
        wts[a] += wts[b]
        del sums[b], wts[b]
    # 3) tiny groups (a cough, a far-away voice) are not speakers of their own
    if k_target is None and len(sums) > 1:
        total = sum(wts)
        while len(sums) > 1:
            small = min(range(len(sums)), key=lambda t: wts[t])
            if wts[small] >= 0.03 * total:
                break
            cs = np.stack([_unit(s) for s in sums])
            sim = cs @ cs[small]
            sim[small] = -2
            tgt = int(np.argmax(sim))
            sums[tgt] = sums[tgt] + sums[small]
            wts[tgt] += wts[small]
            del sums[small], wts[small]
    C = np.stack([_unit(s) for s in sums])
    sims = embs[ok] @ C.T
    for i, row in zip(np.flatnonzero(ok), sims):
        labels[i] = int(np.argmax(row))
    # number the speakers by first appearance
    order = {}
    for lab in labels:
        if lab is not None and lab not in order:
            order[lab] = len(order)
    return [None if lab is None else order[lab] for lab in labels]


def _smooth(segments, labels, max_len=1.5, max_gap=2.0):
    """A very short passage nobody claimed ('Ja.') inherits the speaker when the passages before and after it
    have the same speaker."""
    out = list(labels)
    for i in range(1, len(segments) - 1):
        if out[i] is not None:
            continue
        s = segments[i]
        if s["end"] - s["start"] > max_len:
            continue
        a, b = out[i - 1], out[i + 1]
        if a is None or a != b:
            continue
        if s["start"] - segments[i - 1]["end"] <= max_gap and segments[i + 1]["start"] - s["end"] <= max_gap:
            out[i] = a
    return out


def label_segments(segments, embs, centroids=None, num_speakers=None, min_score=DEFAULT_MIN_SCORE,
                   margin=DEFAULT_MARGIN):
    """Returns (labels, summary). labels[i] is a player name, 'Speaker N' (no players) or, for voices no player
    claimed, 'Unknown N' (different unknown voices get different numbers, so speaker changes stay visible).
    'Unknown' without a number means the passage was too short or silent to tell. summary lists every speaker
    with the talking time and number of passages."""
    n = len(segments)
    if n == 0:
        return [], []
    embs = np.asarray(embs, dtype=np.float32).reshape(n, -1)
    durs = [s["end"] - s["start"] for s in segments]
    if centroids:
        raw = _assign_to_players(embs, centroids, min_score, margin)
        raw = _smooth(segments, raw)
        # voices nobody claimed: group them among themselves (cheap, the voice prints are already there)
        rest = [i for i, r in enumerate(raw) if r is None and not np.isnan(embs[i]).any()]
        if rest:
            groups = cluster_voices(embs[rest], [durs[i] for i in rest], None)
            for i, g in zip(rest, groups):
                if g is not None:
                    raw[i] = f"{LABEL_UNKNOWN} {g + 1}"
    else:
        raw = cluster_voices(embs, durs, num_speakers)
        raw = [None if r is None else f"Speaker {r + 1}" for r in raw]
        raw = _smooth(segments, raw)
    labels = [r if r is not None else LABEL_UNKNOWN for r in raw]
    stats = {}
    for lab, d in zip(labels, durs):
        t = stats.setdefault(lab, {"name": lab, "seconds": 0.0, "passages": 0})
        t["seconds"] += d
        t["passages"] += 1
    summary = sorted(stats.values(), key=lambda t: (t["name"].startswith(LABEL_UNKNOWN), -t["seconds"]))
    for t in summary:
        t["seconds"] = round(t["seconds"], 1)
    return labels, summary
