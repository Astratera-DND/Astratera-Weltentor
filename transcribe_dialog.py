"""
📝 Transcribe window: turns the recorded session parts into a transcript, locally on this PC.
All heavy work runs on helper threads / a separate engine process (see transcriber.py), so the window stays usable.
"""
import importlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import Qt, QObject, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                             QScrollArea, QFrame, QVBoxLayout, QWidget)

import transcriber as T
import speakers as SP
import vocab as V
from speakers_dialog import PlayersDialog, summary_text
from vocab_dialog import WordsDialog, preview_text


class _Bg(QObject):
    done = pyqtSignal(int, object, object)      # token, result, error
    progress = pyqtSignal(object)
    log = pyqtSignal(str)


def fmt_eta(seconds):
    if seconds is None:
        return ""
    m = int(seconds // 60)
    return f"about {m // 60} h {m % 60:02d} min left" if m >= 60 else f"about {max(1, m)} min left"


class TranscribeDialog(QDialog):
    def __init__(self, parent, app_dir, ffmpeg, cfg=None, on_cfg_changed=None, initial_files=None,
                 busy_reason=None, playing_check=None):
        super().__init__(parent)
        self.setWindowTitle("📝 Transcribe session")
        self.resize(760, 820)
        self.app_dir, self.ffmpeg = app_dir, ffmpeg
        self.cfg = dict(cfg or {})
        self.on_cfg_changed = on_cfg_changed
        self.busy_reason = busy_reason or (lambda: None)
        self.playing_check = playing_check or (lambda: False)
        self.job = None
        self._busy = None                       # "download" | "job" | None
        self._cancel = None
        self._tok = 0
        self._cbs = {}
        self._markers = self._audio_log = None
        self._base_name = "session"
        self._durations = {}
        self._loading = True
        self._closed = False
        self._gpu_info = None
        self._bg_obj = _Bg()
        self._bg_obj.done.connect(self._on_bg_done)
        self._bg_obj.progress.connect(self._on_progress)
        self._bg_obj.log.connect(self._append_log)
        self._build_ui()
        self._load_cfg()
        self._loading = False
        self._set_busy(None)
        self._refresh_engine()
        self._refresh_models()
        self._show_speakers()
        if initial_files:
            self.add_files(initial_files)
        self._bg(T.detect_nvidia, self._got_gpu)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # everything sits in a scroll area, so the window also fits on small screens
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # only scroll up and down
        self._scroll = scroll
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)

        g = QGroupBox("1. Recording parts (in the order they were recorded)")
        gl = QVBoxLayout(g)
        self.lst = QListWidget()
        self.lst.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.lst.setMinimumHeight(90)
        self.lst.model().rowsMoved.connect(lambda *a: self._parts_changed())
        gl.addWidget(self.lst)
        row = QHBoxLayout()
        for text, fn in (("Add files...", self._pick_files), ("Add folder...", self._pick_folder),
                         ("Remove", self._remove_selected), ("Clear", self._clear_parts)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        gl.addLayout(row)
        self.lbl_parts = QLabel("No files yet. Add the _part000.wav, _part001.wav ... files of a session.")
        self.lbl_parts.setStyleSheet("color: #9e9e9e;")
        gl.addWidget(self.lbl_parts)
        root.addWidget(g)

        g = QGroupBox("2. Speech engine")
        gl = QVBoxLayout(g)
        self.lbl_gpu = QLabel("Checking your graphics card...")
        gl.addWidget(self.lbl_gpu)
        self.lbl_engine = QLabel("")
        gl.addWidget(self.lbl_engine)
        row = QHBoxLayout()
        self.cmb_kind = QComboBox()
        for k in ("cuda12", "cuda11", "cpu"):
            self.cmb_kind.addItem(T.ENGINE_LABEL[k], k)
        self.cmb_kind.currentIndexChanged.connect(self._save_cfg)
        row.addWidget(self.cmb_kind)
        self.btn_engine = QPushButton("Download engine")
        self.btn_engine.clicked.connect(self._install_engine)
        row.addWidget(self.btn_engine, 1)
        gl.addLayout(row)
        self.chk_own = QCheckBox("Use my own engine")
        self.chk_own.setToolTip("Use a whisper-cli program you built or downloaded yourself (for example a Vulkan build "
                                "for AMD / Intel graphics). Untick to return to the downloaded engine.")
        self.chk_own.toggled.connect(self._own_toggled)
        gl.addWidget(self.chk_own)
        self.own_row = QWidget()
        orow = QHBoxLayout(self.own_row)
        orow.setContentsMargins(0, 0, 0, 0)
        self.ed_own = QLineEdit()
        self.ed_own.setReadOnly(True)
        self.ed_own.setPlaceholderText("Path of whisper-cli (.exe)")
        orow.addWidget(self.ed_own, 1)
        self.btn_own = QPushButton("Choose...")
        self.btn_own.clicked.connect(self._pick_own_engine)
        orow.addWidget(self.btn_own)
        gl.addWidget(self.own_row)
        self.chk_gpu = QCheckBox("Use the graphics card when the engine supports it")
        self.chk_gpu.setChecked(True)
        self.chk_gpu.toggled.connect(self._save_cfg)
        gl.addWidget(self.chk_gpu)
        root.addWidget(g)

        g = QGroupBox("3. Speech model")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        self.cmb_model = QComboBox()
        for key, _f, mb, desc in T.MODELS:
            self.cmb_model.addItem(f"{desc}  (~{mb / 1024:.1f} GB)" if mb >= 1000 else f"{desc}  (~{mb} MB)", key)
        self.cmb_model.currentIndexChanged.connect(self._on_model_changed)
        row.addWidget(self.cmb_model, 1)
        self.btn_model = QPushButton("Download")
        self.btn_model.clicked.connect(self._download_model)
        row.addWidget(self.btn_model)
        gl.addLayout(row)
        self.lbl_model = QLabel("")
        gl.addWidget(self.lbl_model)
        self.chk_vad = QCheckBox("Skip silence (recommended: fewer invented sentences during quiet parts)")
        self.chk_vad.setChecked(True)
        self.chk_vad.toggled.connect(self._save_cfg)
        gl.addWidget(self.chk_vad)
        root.addWidget(g)

        g = QGroupBox("4. Transcript")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        row.addWidget(QLabel("Language:"))
        self.cmb_lang = QComboBox()
        for code, name in T.LANGUAGES:
            self.cmb_lang.addItem(name, code)
        self.cmb_lang.currentIndexChanged.connect(self._save_cfg)
        row.addWidget(self.cmb_lang)
        row.addSpacing(16)
        row.addWidget(QLabel("Files:"))
        self.chk_fmt = {}
        for key, text in (("txt", "Text"), ("srt", "SRT"), ("vtt", "VTT"), ("json", "JSON")):
            c = QCheckBox(text)
            c.toggled.connect(self._save_cfg)
            self.chk_fmt[key] = c
            row.addWidget(c)
        row.addStretch(1)
        gl.addLayout(row)
        self.chk_scenes = QCheckBox("Add scene changes, layouts, breaks and markers to the text")
        self.chk_music = QCheckBox("Add music changes")
        self.chk_sfx = QCheckBox("Add sound effects and Quick Fire sounds (can be a lot)")
        for c in (self.chk_scenes, self.chk_music, self.chk_sfx):
            c.toggled.connect(self._save_cfg)
            gl.addWidget(c)
        self.btn_words = QPushButton("📖 Special words...")
        self.btn_words.setToolTip("Names, places, spells and monsters the transcript should spell right")
        self.btn_words.clicked.connect(self._edit_words)
        gl.addWidget(self.btn_words)
        self.lbl_words = QLabel("")
        self.lbl_words.setWordWrap(True)
        self.lbl_words.setStyleSheet("color: #9e9e9e;")
        gl.addWidget(self.lbl_words)
        self.chk_fix = QCheckBox("Repair misspelled names afterwards using the word list")
        self.chk_fix.setToolTip("After the transcription, near-misses like 'Kellmar Wirex' become 'Kelmor Virex'. "
                                "Changes are listed in the log.")
        self.chk_fix.toggled.connect(self._save_cfg)
        gl.addWidget(self.chk_fix)
        row = QHBoxLayout()
        self.chk_spk = QCheckBox("Label who is speaking")
        self.chk_spk.setToolTip("Writes the speaker in front of every passage: the names of your players, or "
                                "Speaker 1, Speaker 2, ... Needs a one-time download of a small voice model (40 MB).")
        self.chk_spk.toggled.connect(self._spk_toggled)
        row.addWidget(self.chk_spk)
        self.btn_players = QPushButton("👥 Players and voices...")
        self.btn_players.clicked.connect(self._edit_players)
        row.addWidget(self.btn_players, 1)
        self.cmb_nspk = QComboBox()
        self.cmb_nspk.addItem("Speakers: automatic", 0)
        for n in range(1, 9):
            self.cmb_nspk.addItem(f"Speakers: {n}", n)
        self.cmb_nspk.setToolTip("Only used without players: how many different people speak")
        self.cmb_nspk.currentIndexChanged.connect(self._save_cfg)
        row.addWidget(self.cmb_nspk)
        gl.addLayout(row)
        row = QHBoxLayout()
        self.lbl_spk = QLabel("")
        self.lbl_spk.setStyleSheet("color: #9e9e9e;")
        self.lbl_spk.setWordWrap(True)
        row.addWidget(self.lbl_spk, 1)
        self.btn_spk_dl = QPushButton("Download voice model")
        self.btn_spk_dl.clicked.connect(self._spk_button)
        row.addWidget(self.btn_spk_dl)
        gl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Save into:"))
        self.ed_out = QLineEdit()
        row.addWidget(self.ed_out, 1)
        b = QPushButton("...")
        b.setFixedWidth(34)
        b.clicked.connect(self._pick_out)
        row.addWidget(b)
        gl.addLayout(row)
        root.addWidget(g)

        row = QHBoxLayout()
        self.btn_start = QPushButton("▶ Start transcription")
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        self.btn_start.clicked.connect(self._start)
        row.addWidget(self.btn_start)
        self.btn_cancel = QPushButton("■ Stop")
        self.btn_cancel.clicked.connect(self._stop)
        row.addWidget(self.btn_cancel)
        row.addStretch(1)
        self.btn_open = QPushButton("📂 Open folder")
        self.btn_open.clicked.connect(self._open_out)
        row.addWidget(self.btn_open)
        root.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        root.addWidget(self.bar)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(70)
        root.addWidget(self.log, 1)

        # long texts wrap instead of widening the window
        for lbl in (self.lbl_parts, self.lbl_gpu, self.lbl_engine, self.lbl_model, self.lbl_words, self.lbl_status):
            lbl.setWordWrap(True)
        self._content = content
        self.setMinimumWidth(max(560, content.minimumSizeHint().width() + 30))

    # ------------------------------------------------------------------ settings
    def _load_cfg(self):
        c = self.cfg
        i = self.cmb_lang.findData(c.get("language", "de"))
        self.cmb_lang.setCurrentIndex(max(0, i))
        fm = c.get("formats", ["txt", "srt"])
        for k, cb in self.chk_fmt.items():
            cb.setChecked(k in fm)
        self.chk_scenes.setChecked(bool(c.get("events_scenes", True)))
        self.chk_music.setChecked(bool(c.get("events_music", True)))
        self.chk_sfx.setChecked(bool(c.get("events_sfx", False)))
        self.chk_gpu.setChecked(bool(c.get("use_gpu", True)))
        self.ed_own.setText(c.get("engine_exe", "") or "")
        self.chk_own.setChecked(bool(c.get("own_engine_on", False)))
        self.chk_vad.setChecked(bool(c.get("vad", True)))
        self._vocab = V.migrate_words(c)
        self._vocab_src = V.migrate_sources(self._vocab, c.get("vocab_src"))
        self.chk_fix.setChecked(bool(c.get("fix_spelling", True)))
        self.chk_spk.setChecked(bool(c.get("label_speakers", False)))
        self.cmb_nspk.setCurrentIndex(max(0, self.cmb_nspk.findData(int(c.get("num_speakers", 0) or 0))))
        self._show_words()
        # the graphics card is checked on a helper thread (_got_gpu); until then CPU is the safe default
        self.cmb_kind.setCurrentIndex(max(0, self.cmb_kind.findData(c.get("engine_kind") or "cpu")))
        mk = c.get("model") or "large-v3-turbo-q5_0"
        self.cmb_model.setCurrentIndex(max(0, self.cmb_model.findData(mk)))

    def _save_cfg(self, *_):
        if self._loading:
            return
        self.cfg.update({
            "language": self.cmb_lang.currentData(),
            "formats": [k for k, cb in self.chk_fmt.items() if cb.isChecked()],
            "events_scenes": self.chk_scenes.isChecked(), "events_music": self.chk_music.isChecked(),
            "events_sfx": self.chk_sfx.isChecked(), "use_gpu": self.chk_gpu.isChecked(), "vad": self.chk_vad.isChecked(),
            "own_engine_on": self.chk_own.isChecked(), "engine_exe": self.cfg.get("engine_exe", ""),
            "vocab": list(self._vocab), "vocab_src": dict(self._vocab_src), "fix_spelling": self.chk_fix.isChecked(),
            "label_speakers": self.chk_spk.isChecked(), "num_speakers": self.cmb_nspk.currentData(), "engine_kind": self.cmb_kind.currentData(),
            "model": self.cmb_model.currentData()})
        if self.on_cfg_changed:
            try:
                self.on_cfg_changed(dict(self.cfg))
            except Exception as e:
                logging.warning(f"Could not store transcription settings: {e}")

    # ------------------------------------------------------------------ special words
    def _show_words(self):
        self.lbl_words.setText(preview_text(self._vocab))
        self.btn_words.setText(f"📖 Special words... ({len(self._vocab)})" if self._vocab else "📖 Special words...")

    def _remember_kurator(self, path):
        self.cfg["kurator_path"] = path
        self._save_cfg()

    def _edit_words(self):
        dlg = WordsDialog(self, self._vocab, self.cfg.get("kurator_path", ""), self._remember_kurator,
                          self._vocab_src, os.path.join(T.data_dir(self.app_dir), "word_presets.json"))
        if dlg.exec():
            self._vocab = list(dlg.words)
            self._vocab_src = dict(dlg.sources)
            self._show_words()
            self._save_cfg()

    # ------------------------------------------------------------------ speakers
    def _show_speakers(self):
        """Shows what is missing for speaker labels: the component, the voice model, or nothing."""
        ok, why = SP.available()
        on = self.chk_spk.isChecked()
        idle = not self.running
        self.btn_players.setEnabled(ok and idle)
        self.cmb_nspk.setEnabled(on and ok and idle)
        self._spk_mode = None
        if not ok:
            frozen = bool(getattr(sys, "frozen", False))
            self.lbl_spk.setStyleSheet("color: #ffb74d;")
            if frozen:
                self.lbl_spk.setText(why)
                self.btn_spk_dl.setVisible(False)
            else:
                self.lbl_spk.setText("The voice component is not installed in this Python. Install it once with the "
                                     "button (or run: pip install sherpa-onnx), then download the voice model.")
                self.btn_spk_dl.setText("Install voice component")
                self.btn_spk_dl.setVisible(True)
                self.btn_spk_dl.setEnabled(idle)
                self._spk_mode = "pip"
            return
        self.btn_spk_dl.setVisible(True)
        if not SP.model_installed(self.app_dir):
            self.lbl_spk.setStyleSheet("color: #ffb74d;")
            self.lbl_spk.setText("The voice model (40 MB) is not on this PC yet. Press Download (one time).")
            self.btn_spk_dl.setText("Download voice model")
            self.btn_spk_dl.setEnabled(idle)
            self._spk_mode = "model"
            return
        self.btn_spk_dl.setText("Voice model installed ✔")
        self.btn_spk_dl.setEnabled(False)
        self.lbl_spk.setStyleSheet("color: #9e9e9e;")
        self.lbl_spk.setText(summary_text(SP.Profiles(self.app_dir)) if on else
                             "Off. Tick it to have names or speaker numbers in the transcript.")

    def _spk_button(self):
        if self._spk_mode == "model":
            self._download_spk_model()
        elif self._spk_mode == "pip":
            self._install_sherpa()

    def _download_spk_model(self):
        self._begin("download", "Downloading the voice model...")
        cancel = self._cancel
        bridge = self._bg_obj

        def job():
            return SP.download_model(self.app_dir, lambda d, t: bridge.progress.emit(("dl", d, t, "voice model")), cancel)

        def done(res, err):
            self._end()
            self._show_speakers()
            self._status(str(err) if err is not None else "Voice model ready.", bad=err is not None)
        self._bg(job, done)

    def _install_sherpa(self):
        """Only when running from the Python files: installs the voice component with pip."""
        self._begin("download", "Installing the voice component with pip (about 25 MB)...")
        self.bar.setRange(0, 0)

        def job():
            r = subprocess.run([sys.executable, "-m", "pip", "install", "sherpa-onnx>=1.10"], capture_output=True,
                               text=True, errors="replace", timeout=900, **T._hidden())
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
                raise T.TranscribeError("pip could not install it: " + " ".join(tail))
            importlib.invalidate_caches()
            ok, why = SP.available()
            if not ok:
                raise T.TranscribeError("Installed, but it cannot be loaded yet. Please restart the program. " + why)
            return True

        def done(res, err):
            self.bar.setRange(0, 1000)
            self._end()
            self._show_speakers()
            self._status(str(err) if err is not None else "Voice component installed. Now download the voice model.",
                         bad=err is not None)
        self._bg(job, done)

    def _spk_toggled(self, on):
        self._show_speakers()
        self._save_cfg()

    def _edit_players(self):
        dlg = PlayersDialog(self, self.app_dir, self.ffmpeg, self._parts, self.ed_out.text().strip)
        dlg.exec()
        self._show_speakers()

    # ------------------------------------------------------------------ helpers
    def _status(self, text, bad=False):
        self.lbl_status.setStyleSheet("color: #ef5350;" if bad else "color: #9ccc65;")
        self.lbl_status.setText(text)

    def _append_log(self, text):
        self.log.appendPlainText(text)

    def _bg(self, fn, cb):
        self._tok += 1
        tok = self._tok
        self._cbs[tok] = cb
        obj = self._bg_obj

        def run():
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, e
            try:
                obj.done.emit(tok, res, err)
            except RuntimeError:
                pass
        threading.Thread(target=run, name="transcribe-ui", daemon=True).start()

    def _on_bg_done(self, tok, res, err):
        cb = self._cbs.pop(tok, None)
        if cb and not self._closed:
            if err is not None and not isinstance(err, T.TranscribeError):
                logging.warning(f"Transcribe dialog: {err}")
            cb(res, err)

    def _set_busy(self, what):
        self._busy = what
        idle = what is None
        for w in (self.lst, self.cmb_kind, self.btn_engine, self.chk_own, self.btn_own, self.chk_gpu, self.cmb_model, self.btn_model,
                  self.chk_vad, self.cmb_lang, self.btn_words, self.chk_fix, self.chk_spk, self.btn_players, self.cmb_nspk, self.ed_out, self.chk_scenes, self.chk_music, self.chk_sfx,
                  *self.chk_fmt.values()):
            w.setEnabled(idle)
        self.btn_start.setEnabled(idle)
        self.btn_cancel.setEnabled(not idle)
        if hasattr(self, "lbl_spk"):
            self._show_speakers()

    @property
    def running(self):
        return self._busy is not None

    def _got_gpu(self, res, err):
        self._gpu_info = res if not err else None
        if self._gpu_info:
            name, drv = self._gpu_info
            self.lbl_gpu.setText(f"Graphics card: {name} (driver {drv}). A GPU engine is recommended: "
                                 "an 8 hour session takes minutes to about an hour instead of many hours.")
            if not self.cfg.get("engine_kind"):
                self.cmb_kind.setCurrentIndex(max(0, self.cmb_kind.findData("cuda12" if drv >= 551 else "cuda11")))
        else:
            self.lbl_gpu.setText("No NVIDIA graphics card found. The CPU engine works but is slow for long sessions. "
                                 "AMD / Intel graphics: use 'Use my own engine...' with a Vulkan build of whisper.cpp.")
            if not self.cfg.get("engine_kind"):
                self.cmb_kind.setCurrentIndex(max(0, self.cmb_kind.findData("cpu")))

    # ------------------------------------------------------------------ parts
    def _pick_files(self):
        start = os.path.dirname(self.lst.item(0).data(Qt.ItemDataRole.UserRole)) if self.lst.count() else ""
        files, _ = QFileDialog.getOpenFileNames(self, "Choose recording parts", start,
                                                "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus *.mp4 *.mkv);;All files (*)")
        if files:
            self.add_files(files)

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a recording folder")
        if d:
            self.add_files(T.find_parts(d))

    def add_files(self, files):
        have = {self.lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.lst.count())}
        new = [f for f in files if f not in have and os.path.isfile(f)]
        if len(files) > 1 or not self.lst.count():
            new.sort(key=lambda p: T.natural_key(os.path.basename(p)))
        for f in new:
            self.lst.addItem(os.path.basename(f))
            item = self.lst.item(self.lst.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, f)
            item.setToolTip(f)
        self._parts_changed(first_add=bool(new))

    def _remove_selected(self):
        for it in self.lst.selectedItems():
            self.lst.takeItem(self.lst.row(it))
        self._parts_changed()

    def _clear_parts(self):
        self.lst.clear()
        self._parts_changed()

    def _parts(self):
        return [self.lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.lst.count())]

    def _parts_changed(self, first_add=False):
        parts = self._parts()
        if not parts:
            self._markers = self._audio_log = None
            self.lbl_parts.setText("No files yet. Add the _part000.wav, _part001.wav ... files of a session.")
            return
        first = parts[0]
        self._markers, self._audio_log = T.guess_session_files(first)
        stem = os.path.splitext(os.path.basename(first))[0]
        self._base_name = re.sub(r"_part\d+$", "", stem) or "session"
        if first_add and not self.ed_out.text().strip():
            self.ed_out.setText(os.path.join(os.path.dirname(first), "transcript"))
        found = []
        if self._markers:
            found.append("markers")
        if self._audio_log:
            found.append("audio log")
        self.lbl_parts.setText(f"{len(parts)} part(s). " + (f"Found next to them: {', '.join(found)}." if found
                                                          else "No markers / audio log found (the transcript will have speech only)."))
        ffm = self.ffmpeg

        def measure():
            return {p: T.probe_duration(ffm, p) for p in parts}
        self._bg(measure, lambda res, err: self._got_durations(parts, res))

    def _got_durations(self, parts, res):
        if not res or parts != self._parts():
            return
        total = sum(v for v in res.values() if v)
        unknown = sum(1 for v in res.values() if not v)
        h, rem = divmod(int(total), 3600)
        txt = self.lbl_parts.text().split(" Length")[0]
        self.lbl_parts.setText(f"{txt} Length: {h}:{rem // 60:02d}:{rem % 60:02d}" + (f" (+{unknown} unknown)" if unknown else ""))

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Save transcript into", self.ed_out.text())
        if d:
            self.ed_out.setText(d)

    def _open_out(self):
        d = self.ed_out.text().strip()
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))
        else:
            self._status("The output folder does not exist yet.", bad=True)

    # ------------------------------------------------------------------ engine + model
    def _engine_info(self):
        """The engine that will be used: the person's own (when ticked and valid), else the downloaded one."""
        if self.chk_own.isChecked():
            own = self.cfg.get("engine_exe")
            if own and os.path.isfile(own):
                return {"exe": own, "tag": "own", "kind": "own"}
            return None
        return T.current_engine(self.app_dir)

    def _refresh_engine(self):
        own_on = self.chk_own.isChecked()
        self.own_row.setVisible(own_on)
        self.cmb_kind.setVisible(not own_on)
        self.btn_engine.setVisible(not own_on)
        downloaded = T.current_engine(self.app_dir)
        self.btn_engine.setText("Update engine" if downloaded else "Download engine")
        info = self._engine_info()
        if own_on:
            if info:
                self.lbl_engine.setText(f"Engine: your own ({info['exe']})")
                self.lbl_engine.setStyleSheet("color: #9ccc65;")
            else:
                self.lbl_engine.setText("Engine: choose your whisper-cli below.")
                self.lbl_engine.setStyleSheet("color: #ffb74d;")
        elif downloaded:
            self.lbl_engine.setText(f"Engine: ✔ {T.ENGINE_LABEL.get(downloaded['kind'], downloaded['kind'])}, "
                                    f"build {downloaded['tag']}")
            self.lbl_engine.setStyleSheet("color: #9ccc65;")
        else:
            self.lbl_engine.setText("Engine: not installed yet. Choose a type and press 'Download engine'.")
            self.lbl_engine.setStyleSheet("color: #ffb74d;")

    def _own_toggled(self, on):
        if self._loading:
            return
        self._save_cfg()
        self._refresh_engine()
        if on and not self._engine_info():
            self._status("Choose your whisper-cli with 'Choose...'.")

    def _pick_own_engine(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose whisper-cli", self.ed_own.text(),
                                           "Programs (*.exe whisper-cli);;All files (*)")
        if not f:
            return
        try:
            T.engine_help(f)
        except T.TranscribeError as e:
            self._status(str(e), bad=True)
            return
        self.cfg["engine_exe"] = f
        self.ed_own.setText(f)
        self._save_cfg()
        self._refresh_engine()
        self._status("Using your own engine.")

    def _install_engine(self):
        kind = self.cmb_kind.currentData()
        self._save_cfg()
        self._begin("download", f"Looking for the newest {T.ENGINE_LABEL[kind]} build...")
        cancel = self._cancel
        bridge = self._bg_obj

        def job():
            return T.install_engine(self.app_dir, kind, lambda d, t: bridge.progress.emit(("dl", d, t, "engine")), cancel)

        def done(res, err):
            self._end()
            if err is not None:
                self._status(str(err), bad=True)
                return
            self._refresh_engine()
            try:
                T.engine_help(res["exe"])
                self._status(f"Engine ready (build {res['tag']}).")
            except T.TranscribeError as e:
                self._status(f"Downloaded, but the engine does not start: {e}", bad=True)
        self._bg(job, done)

    def _model_state(self):
        return T.installed_models(self.app_dir)

    def _refresh_models(self):
        key = self.cmb_model.currentData()
        have = key in self._model_state()
        self.btn_model.setEnabled(not have and self._busy is None)
        self.btn_model.setText("Installed ✔" if have else "Download")
        self.lbl_model.setText("" if have else "This model is not on this PC yet. Press Download (one time).")

    def _on_model_changed(self, *_):
        self._save_cfg()
        self._refresh_models()

    def _download_model(self):
        key = self.cmb_model.currentData()
        self._begin("download", "Downloading the speech model...")
        cancel = self._cancel
        bridge = self._bg_obj

        def job():
            return T.download_model(self.app_dir, key, lambda d, t: bridge.progress.emit(("dl", d, t, "model")), cancel)

        def done(res, err):
            self._end()
            self._refresh_models()
            self._status(str(err) if err is not None else "Model ready.", bad=err is not None)
        self._bg(job, done)

    # ------------------------------------------------------------------ running
    def _begin(self, what, text):
        self._cancel = threading.Event()
        self._set_busy(what)
        self.bar.setValue(0)
        self._status(text)

    def _end(self):
        self._cancel = None
        self.job = None
        self._set_busy(None)
        self._refresh_models()

    def _stop(self):
        if self._cancel is not None:
            self._cancel.set()
        if self.job is not None:
            self.job.cancel()
        self._status("Stopping...")

    def _on_progress(self, p):
        if isinstance(p, tuple) and p and p[0] == "dl":
            _, done, total, what = p
            if total:
                self.bar.setValue(int(1000 * done / total))
                self._status(f"Downloading the {what}: {done / 1048576:.0f} of {total / 1048576:.0f} MB")
            else:
                self._status(f"Downloading the {what}: {done / 1048576:.0f} MB")
            return
        if not isinstance(p, dict):
            return
        self.bar.setValue(int(10 * p.get("overall_pct", 0)))
        if p.get("stage") == "transcribe":
            bits = [p.get("message", ""), f"{p.get('part_pct', 0)}%"]
            if p.get("speed"):
                bits.append(f"{p['speed']:.1f}x faster than real time")
            if p.get("eta_s") is not None:
                bits.append(fmt_eta(p["eta_s"]))
            if p.get("gpu"):
                bits.append(f"GPU: {p['gpu']}")
            self._status(" · ".join(b for b in bits if b))
        else:
            self._status(p.get("message", ""))

    def _start(self):
        reason = self.busy_reason()
        if reason:
            self._status(reason, bad=True)
            return
        parts = self._parts()
        if not parts:
            self._status("Add the recording parts first.", bad=True)
            return
        info = self._engine_info()
        if not info:
            self._status("Download the speech engine first (step 2).", bad=True)
            return
        key = self.cmb_model.currentData()
        models = self._model_state()
        if key not in models:
            self._status("Download the speech model first (step 3).", bad=True)
            return
        out = self.ed_out.text().strip()
        if not out:
            self._status("Choose where to save the transcript.", bad=True)
            return
        formats = [k for k, cb in self.chk_fmt.items() if cb.isChecked()]
        if not formats:
            self._status("Tick at least one file type (Text, SRT, VTT or JSON).", bad=True)
            return
        if self.playing_check():
            if QMessageBox.question(self, "Transcribe", "Music, sounds or video are playing. Transcribing at the same "
                                    "time can make them stutter. Start anyway?") != QMessageBox.StandardButton.Yes:
                return
        self._save_cfg()
        kinds = []
        if self.chk_scenes.isChecked():
            kinds += ["scene", "layout", "break", "marker"]
        if self.chk_music.isChecked():
            kinds += ["music"]
        if self.chk_sfx.isChecked():
            kinds += ["sfx", "quickfire"]
        hints = T.collect_hints(self._markers, self._audio_log, "\n".join(self._vocab))
        fix_names, fix_terms = V.split_names_terms(self._vocab, self._vocab_src) if self.chk_fix.isChecked() else ([], [])
        spk_cfg = None
        if self.chk_spk.isChecked():
            ok, why = SP.available()
            if not ok:
                self._status(why, bad=True)
                return
            spk_cfg = {"app_dir": self.app_dir, "num_speakers": self.cmb_nspk.currentData() or None, "use_players": True}
        self.log.clear()
        self._begin("job", "Preparing...")
        cancel = self._cancel
        bridge = self._bg_obj
        want_vad = self.chk_vad.isChecked()
        app_dir = self.app_dir

        job = T.Job(parts, out, self._base_name, info["exe"], models[key], language=self.cmb_lang.currentData(),
                    hints_text=T.build_prompt(hints), vad_model=None, use_gpu=self.chk_gpu.isChecked(), ffmpeg=self.ffmpeg,
                    markers_path=self._markers, audio_log_path=self._audio_log, formats=formats, event_kinds=kinds,
                    progress=lambda p: bridge.progress.emit(p), log=lambda m: bridge.log.emit(m),
                    engine_tag=str(info.get("tag", "")), fix_names=fix_names, fix_terms=fix_terms,
                    speaker_cfg=spk_cfg)
        self.job = job

        def work():
            if want_vad:
                try:
                    job.vad_model = T.download_vad(app_dir, None, job.cancel_event)
                except T.Cancelled:
                    raise
                except T.TranscribeError as e:
                    bridge.log.emit(f"Silence detection not available ({e}); continuing without it.")
            if spk_cfg and not SP.model_installed(app_dir):
                bridge.log.emit("Downloading the speaker model (one time, 40 MB)...")
                SP.download_model(app_dir, lambda d, t: bridge.progress.emit(("dl", d, t, "speaker model")), job.cancel_event)
            if hints:
                bridge.log.emit("Words given to the engine: " + T.build_prompt(hints))
            return job.run()

        def done(res, err):
            self._end()
            if isinstance(err, T.Cancelled):
                self._status("Stopped. Finished parts are kept; start again to continue where it stopped.")
            elif err is not None:
                self._status(str(err), bad=True)
                self._append_log("ERROR: " + str(err))
            else:
                mins = res["duration"] / 60
                self.bar.setValue(1000)
                extra = ""
                if res.get("speakers"):
                    tot = sum(t["seconds"] for t in res["speakers"]) or 1.0
                    extra = " Speakers: " + ", ".join(f"{t['name']} {100 * t['seconds'] / tot:.0f}%" for t in res["speakers"][:6]) + "."
                self._status(f"Done: {res['segments']} passages from {mins:.0f} min of audio.{extra} "
                             f"Saved: {', '.join(os.path.basename(f) for f in res['files'])}")
        self._bg(work, done)

    # ------------------------------------------------------------------ closing
    def shutdown(self):
        """Called when the program closes: stop everything, including the engine process."""
        self._stop()
        self._closed = True

    def closeEvent(self, e):
        if self.running:
            if QMessageBox.question(self, "Transcribe", "A transcription is still running. Stop it and close?") \
                    != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self._stop()
        self._closed = True
        super().closeEvent(e)
