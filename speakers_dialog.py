"""
Windows for "who is speaking":
  PlayersDialog     the players and their voice samples (from audio files or from a section of the recording)
  ClipPickerDialog  pick a section of the recording: by time, or from the sentences of an earlier transcription
The logic lives in speakers.py.
"""
import logging
import os
import tempfile
import threading
import uuid

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox,
                             QTabWidget, QVBoxLayout, QWidget)

import speakers as S
import transcriber as T

GOOD = "color: #9ccc65;"
WARN = "color: #ffb74d;"
BAD = "color: #ef5350;"
DIM = "color: #9e9e9e;"
AUDIO_FILTER = "Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.aac *.wma *.mp4 *.mkv);;All files (*)"


def fmt_time(seconds):
    s = int(max(0, seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def parse_time(text):
    """'1:02:03', '12:30' or '90' -> seconds, or None."""
    t = (text or "").strip().replace(",", ".")
    if not t:
        return None
    try:
        parts = [float(p) for p in t.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def summary_text(profiles):
    """One line for the transcription window."""
    ps = [p for p in profiles.players()]
    ready = [p["name"] for p in ps if p["samples"]]
    if not ps:
        return "No players yet: the voices are grouped automatically (Speaker 1, Speaker 2, ...)."
    if not ready:
        return "Players have no voice samples yet: the voices are grouped automatically (Speaker 1, Speaker 2, ...)."
    return f"Players: {', '.join(ready)}. Voices nobody matches are marked '{S.LABEL_UNKNOWN}'."


class _Bridge(QObject):
    done = pyqtSignal(int, object, object)
    progress = pyqtSignal(int, int)


class ClipPlayer:
    """Plays a short piece of audio (floats) through the default speakers. Does nothing when sound is not available."""

    def __init__(self):
        self.ok = False
        self._file = None
        try:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
            self._player = QMediaPlayer()
            self._out = QAudioOutput()
            self._player.setAudioOutput(self._out)
            self.ok = True
        except Exception as e:
            logging.info(f"Sample playback is not available: {e}")

    def play(self, samples):
        if not self.ok:
            return False
        self.stop()
        path = os.path.join(tempfile.gettempdir(), f"astratera_clip_{uuid.uuid4().hex[:8]}.wav")
        try:
            S.write_wav(path, samples)
            self._file = path
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
            return True
        except Exception as e:
            logging.warning(f"Could not play the sample: {e}")
            return False

    def stop(self):
        if not self.ok:
            return
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        except Exception:
            pass
        if self._file:
            try:
                os.remove(self._file)
            except OSError:
                pass
            self._file = None


# =====================================================================================================
class ClipPickerDialog(QDialog):
    """Pick sections of the recording as voice samples for one player. Stays open so several can be added."""

    def __init__(self, parent, player_name, parts, out_dir, ffmpeg, add_cb, player=None):
        super().__init__(parent)
        self.setWindowTitle(f"Voice samples for {player_name} from the recording")
        self.resize(640, 560)
        self.parts, self.out_dir, self.ffmpeg, self.add_cb = list(parts), out_dir, ffmpeg, add_cb
        self.player = player or ClipPlayer()
        self._own_player = player is None
        self._segs = {}
        lay = QVBoxLayout(self)
        info = QLabel(f"Find sections where only <b>{player_name}</b> speaks, listen, and add them. "
                      "About 30 seconds in total work best; 5 to 20 seconds per section is plenty.")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        row.addWidget(QLabel("Recording part:"))
        self.cmb_part = QComboBox()
        for p in self.parts:
            self.cmb_part.addItem(os.path.basename(p), p)
        self.cmb_part.currentIndexChanged.connect(self._part_changed)
        row.addWidget(self.cmb_part, 1)
        lay.addLayout(row)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self._build_time_tab()
        self._build_text_tab()
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("Done")
        b.clicked.connect(self.accept)
        row.addWidget(b)
        lay.addLayout(row)
        self._part_changed()

    # ------------------------------------------------------------------
    def _build_time_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Start at:"))
        self.ed_start = QLineEdit("0:00")
        self.ed_start.setFixedWidth(90)
        self.ed_start.setToolTip("Minutes:seconds or hours:minutes:seconds")
        row.addWidget(self.ed_start)
        row.addWidget(QLabel("Length:"))
        self.sp_len = QSpinBox()
        self.sp_len.setRange(3, 120)
        self.sp_len.setValue(15)
        self.sp_len.setSuffix(" s")
        row.addWidget(self.sp_len)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.btn_back = QPushButton("◀ Earlier")
        self.btn_back.clicked.connect(lambda: self._step(-1))
        row.addWidget(self.btn_back)
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.clicked.connect(self._play_time)
        row.addWidget(self.btn_play)
        b = QPushButton("■ Stop")
        b.clicked.connect(self.player.stop)
        row.addWidget(b)
        self.btn_fwd = QPushButton("Later ▶")
        self.btn_fwd.clicked.connect(lambda: self._step(1))
        row.addWidget(self.btn_fwd)
        row.addStretch(1)
        lay.addLayout(row)
        self.btn_add_time = QPushButton("➕ Add this section")
        self.btn_add_time.clicked.connect(self._add_time)
        lay.addWidget(self.btn_add_time)
        tip = QLabel("Tip: after you have transcribed the recording once, its sentences are listed in the other tab, "
                     "which is much quicker than searching by time.")
        tip.setWordWrap(True)
        tip.setStyleSheet(DIM)
        lay.addWidget(tip)
        lay.addStretch(1)
        if not self.player.ok:
            self.btn_play.setEnabled(False)
            self.btn_play.setToolTip("Sound playback is not available here.")
        self.tabs.addTab(w, "By time")

    def _build_text_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_text = QLabel("")
        self.lbl_text.setWordWrap(True)
        lay.addWidget(self.lbl_text)
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("Search the sentences...")
        self.ed_find.setClearButtonEnabled(True)
        self.ed_find.textChanged.connect(self._fill_text)
        lay.addWidget(self.ed_find)
        self.lst = QListWidget()
        self.lst.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lst.itemSelectionChanged.connect(self._sel_changed)
        lay.addWidget(self.lst, 1)
        row = QHBoxLayout()
        self.btn_play_sel = QPushButton("▶ Play selected")
        self.btn_play_sel.clicked.connect(self._play_selected)
        row.addWidget(self.btn_play_sel)
        b = QPushButton("■ Stop")
        b.clicked.connect(self.player.stop)
        row.addWidget(b)
        row.addStretch(1)
        self.btn_add_sel = QPushButton("➕ Add selected as one sample")
        self.btn_add_sel.clicked.connect(self._add_selected)
        row.addWidget(self.btn_add_sel)
        lay.addLayout(row)
        if not self.player.ok:
            self.btn_play_sel.setEnabled(False)
        self.tabs.addTab(w, "From the transcript")
        self._text_tab = w

    # ------------------------------------------------------------------
    def _say(self, text, bad=False):
        self.lbl.setStyleSheet(BAD if bad else GOOD)
        self.lbl.setText(text)

    def _part(self):
        return self.cmb_part.currentData()

    def _part_changed(self, *_):
        part = self._part()
        segs = None
        if part:
            if part not in self._segs:
                self._segs[part] = T.cached_segments(self.out_dir, part) if self.out_dir else None
            segs = self._segs[part]
        has = bool(segs)
        self.tabs.setTabEnabled(1, has)
        self.lbl_text.setText("Select the sentences of one player (Ctrl or Shift for several), listen, then add them."
                              if has else "No transcript of this part yet. Transcribe the recording once, then its "
                                          "sentences appear here.")
        self._fill_text()
        if has:
            self.tabs.setCurrentIndex(1)
        else:
            self.tabs.setCurrentIndex(0)
        self._sel_changed()

    def _fill_text(self, *_):
        self.lst.clear()
        segs = self._segs.get(self._part()) or []
        needle = self.ed_find.text().strip().lower()
        for s in segs:
            if s["end"] - s["start"] < 1.0:
                continue
            if needle and needle not in s["text"].lower():
                continue
            it = QListWidgetItem(f"{fmt_time(s['start'])}   {s['text'][:140]}")
            it.setData(Qt.ItemDataRole.UserRole, (float(s["start"]), float(s["end"])))
            self.lst.addItem(it)

    def _sel_changed(self):
        has = bool(self.lst.selectedItems())
        self.btn_add_sel.setEnabled(has)
        self.btn_play_sel.setEnabled(has and self.player.ok)

    # ---- by time
    def _start(self):
        t = parse_time(self.ed_start.text())
        if t is None:
            self._say("Type the start as minutes:seconds, for example 12:30.", bad=True)
        return t

    def _step(self, direction):
        t = self._start()
        if t is None:
            return
        t = max(0.0, t + direction * self.sp_len.value())
        self.ed_start.setText(fmt_time(t))
        if self.player.ok:
            self._play_time()

    def _read_time_clip(self):
        t = self._start()
        if t is None or not self._part():
            return None
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            return S.read_clip(self.ffmpeg, self._part(), t, self.sp_len.value())
        except S.SpeakerError as e:
            self._say(str(e), bad=True)
            return None
        finally:
            QApplication.restoreOverrideCursor()

    def _play_time(self):
        x = self._read_time_clip()
        if x is not None:
            if len(x) < S.SR:
                self._say("There is nothing to hear at that time.", bad=True)
            elif not self.player.play(x):
                self._say("Could not play the sound.", bad=True)
            else:
                self._say("")

    def _add(self, x, source):
        try:
            msg = self.add_cb(x, source)
        except S.SpeakerError as e:
            self._say(str(e), bad=True)
            return False
        self._say(msg or "Added.")
        return True

    def _add_time(self):
        x = self._read_time_clip()
        if x is None:
            return
        t = self._start()
        self._add(x, f"{os.path.basename(self._part())} {fmt_time(t)}")
        self.ed_start.setText(fmt_time(t + self.sp_len.value()))

    # ---- from the transcript
    def _selected_clip(self):
        spans = sorted(i.data(Qt.ItemDataRole.UserRole) for i in self.lst.selectedItems())
        if not spans:
            return None, ""
        import numpy as np
        pieces, total = [], 0.0
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            for a, b in spans:
                if total >= S.MAX_SAMPLE_SECONDS:
                    break
                x = S.read_clip(self.ffmpeg, self._part(), a, b - a)
                pieces.append(x)
                total += len(x) / S.SR
        except S.SpeakerError as e:
            self._say(str(e), bad=True)
            return None, ""
        finally:
            QApplication.restoreOverrideCursor()
        return np.concatenate(pieces) if pieces else None, f"{os.path.basename(self._part())} {fmt_time(spans[0][0])} ({len(spans)} sentences)"

    def _play_selected(self):
        x, _ = self._selected_clip()
        if x is not None and not self.player.play(x):
            self._say("Could not play the sound.", bad=True)

    def _add_selected(self):
        x, src = self._selected_clip()
        if x is not None and self._add(x, src):
            self.lst.clearSelection()

    def done(self, r):
        if self._own_player:
            self.player.stop()
        super().done(r)


# =====================================================================================================
class PlayersDialog(QDialog):
    """Players and their voice samples. Everything is saved at once, there is nothing to confirm."""

    def __init__(self, parent, app_dir, ffmpeg, get_parts, get_out_dir):
        super().__init__(parent)
        self.setWindowTitle("Players and voices")
        self.resize(720, 560)
        self.app_dir, self.ffmpeg = app_dir, ffmpeg
        self.get_parts, self.get_out_dir = get_parts, get_out_dir
        self.profiles = S.Profiles(app_dir)
        self.player = ClipPlayer()
        self._busy = False
        self._tok = 0
        self._cbs = {}
        self._bridge = _Bridge()
        self._bridge.done.connect(self._bg_done)
        self._bridge.progress.connect(self._dl_progress)
        lay = QVBoxLayout(self)
        info = QLabel("Add each player and a few voice samples of them. The program then writes the player's name in "
                      "front of what they say. Samples work best when only that player speaks, without music, and "
                      "when the sound is like in your recordings. Without players the voices are grouped as "
                      "Speaker 1, Speaker 2, ...")
        info.setWordWrap(True)
        lay.addWidget(info)
        body = QHBoxLayout()
        lay.addLayout(body, 1)

        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Players</b>"))
        self.lst_players = QListWidget()
        self.lst_players.currentRowChanged.connect(self._player_changed)
        left.addWidget(self.lst_players, 1)
        row = QHBoxLayout()
        b = QPushButton("Add player")
        b.clicked.connect(self._add_player)
        row.addWidget(b)
        self.btn_rename = QPushButton("Rename")
        self.btn_rename.clicked.connect(self._rename)
        row.addWidget(self.btn_rename)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._remove_player)
        row.addWidget(self.btn_remove)
        left.addLayout(row)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        self.lbl_samples = QLabel("<b>Voice samples</b>")
        right.addWidget(self.lbl_samples)
        self.lbl_quality = QLabel("")
        self.lbl_quality.setWordWrap(True)
        right.addWidget(self.lbl_quality)
        self.lst_samples = QListWidget()
        self.lst_samples.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lst_samples.itemSelectionChanged.connect(self._sample_sel)
        right.addWidget(self.lst_samples, 1)
        row = QHBoxLayout()
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.clicked.connect(self._play_sample)
        row.addWidget(self.btn_play)
        self.btn_rm_sample = QPushButton("Remove sample")
        self.btn_rm_sample.clicked.connect(self._remove_samples)
        row.addWidget(self.btn_rm_sample)
        row.addStretch(1)
        right.addLayout(row)
        row = QHBoxLayout()
        self.btn_file = QPushButton("➕ From audio file...")
        self.btn_file.setToolTip("A recording of just this person, for example a voice message")
        self.btn_file.clicked.connect(self._add_from_file)
        row.addWidget(self.btn_file)
        self.btn_rec = QPushButton("➕ From the recording...")
        self.btn_rec.setToolTip("Pick sections of your session recording")
        self.btn_rec.clicked.connect(self._add_from_recording)
        row.addWidget(self.btn_rec)
        right.addLayout(row)
        body.addLayout(right, 2)

        row = QHBoxLayout()
        self.btn_check = QPushButton("🔍 Check the voices")
        self.btn_check.setToolTip("Shows whether two players sound so similar that they could be mixed up")
        self.btn_check.clicked.connect(self._check)
        row.addWidget(self.btn_check)
        row.addStretch(1)
        b = QPushButton("Close")
        b.clicked.connect(self.accept)
        row.addWidget(b)
        lay.addLayout(row)
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self._fill_players()

    # ------------------------------------------------------------------ helpers
    def _say(self, text, bad=False, warn=False):
        self.lbl.setStyleSheet(BAD if bad else WARN if warn else GOOD)
        self.lbl.setText(text)

    def _current(self):
        it = self.lst_players.currentItem()
        return self.profiles.get(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def _fill_players(self, select=None):
        cur = select or (self._current() or {}).get("id")
        self.lst_players.blockSignals(True)
        self.lst_players.clear()
        for p in self.profiles.players():
            secs = self.profiles.seconds(p["id"])
            it = QListWidgetItem(f"{p['name']}   ({secs:.0f} s)" if secs else f"{p['name']}   (no samples)")
            it.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.lst_players.addItem(it)
            if p["id"] == cur:
                self.lst_players.setCurrentItem(it)
        self.lst_players.blockSignals(False)
        if self.lst_players.currentRow() < 0 and self.lst_players.count():
            self.lst_players.setCurrentRow(0)
        self._player_changed()

    def _player_changed(self, *_):
        p = self._current()
        has = p is not None
        for w in (self.btn_rename, self.btn_remove, self.btn_file, self.btn_rec):
            w.setEnabled(has and not self._busy)
        self.btn_rec.setEnabled(has and not self._busy)
        self.lst_samples.clear()
        if not p:
            self.lbl_samples.setText("<b>Voice samples</b>")
            self.lbl_quality.setText("Add a player first." if not self.profiles.players() else "")
            self.btn_check.setEnabled(False)
            self._sample_sel()
            return
        self.lbl_samples.setText(f"<b>Voice samples of {p['name']}</b>")
        for s in p["samples"]:
            it = QListWidgetItem(f"{s['seconds']:.0f} s   {s.get('source', '')}")
            it.setData(Qt.ItemDataRole.UserRole, s["file"])
            self.lst_samples.addItem(it)
        secs = self.profiles.seconds(p["id"])
        word, hint = self.profiles.quality(secs)
        self.lbl_quality.setStyleSheet(GOOD if word in ("ok", "good") else WARN)
        self.lbl_quality.setText(f"{secs:.0f} seconds in total. {hint}")
        self.btn_check.setEnabled(sum(1 for q in self.profiles.players() if q["samples"]) >= 2 and not self._busy)
        self._sample_sel()

    def _sample_sel(self):
        has = bool(self.lst_samples.selectedItems())
        self.btn_play.setEnabled(has and self.player.ok)
        self.btn_rm_sample.setEnabled(has)

    # ------------------------------------------------------------------ players
    def _add_player(self):
        name, ok = QInputDialog.getText(self, "Add player", "Name of the player (as it should appear in the transcript):")
        if not ok:
            return
        try:
            p = self.profiles.add_player(name)
        except S.SpeakerError as e:
            self._say(str(e), bad=True)
            return
        self._say(f"{p['name']} added. Now add voice samples.")
        self._fill_players(select=p["id"])

    def _rename(self):
        p = self._current()
        if not p:
            return
        name, ok = QInputDialog.getText(self, "Rename player", "New name:", text=p["name"])
        if not ok:
            return
        try:
            self.profiles.rename(p["id"], name)
        except S.SpeakerError as e:
            self._say(str(e), bad=True)
            return
        self._fill_players()

    def _remove_player(self):
        p = self._current()
        if p and QMessageBox.question(self, "Remove player", f"Remove {p['name']} and their voice samples?") \
                == QMessageBox.StandardButton.Yes:
            self.profiles.remove_player(p["id"])
            self._say(f"{p['name']} removed.")
            self._fill_players()

    # ------------------------------------------------------------------ samples
    def _add_sample_cb(self, samples, source):
        p = self._current()
        if not p:
            raise S.SpeakerError("Select a player first.")
        s = self.profiles.add_sample(p["id"], samples, source)
        self._fill_players()
        total = self.profiles.seconds(p["id"])
        return f"Added {s['seconds']:.0f} s to {p['name']} ({total:.0f} s in total)."

    def _add_from_file(self):
        p = self._current()
        if not p:
            return
        files, _ = QFileDialog.getOpenFileNames(self, f"Voice samples of {p['name']}", "", AUDIO_FILTER)
        added, problems, cut = 0.0, [], False
        for f in files:
            try:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                dur = T.probe_duration(self.ffmpeg, f)
                x = S.read_clip(self.ffmpeg, f, 0, S.MAX_SAMPLE_SECONDS)
                cut = cut or bool(dur and dur > S.MAX_SAMPLE_SECONDS + 1)
                s = self.profiles.add_sample(p["id"], x, f"file {os.path.basename(f)}")
                added += s["seconds"]
            except S.SpeakerError as e:
                problems.append(f"{os.path.basename(f)}: {e}")
            finally:
                QApplication.restoreOverrideCursor()
        if files:
            self._fill_players()
            msg = f"Added {added:.0f} s to {p['name']}." if added else ""
            if cut:
                msg += f" Only the first {S.MAX_SAMPLE_SECONDS:.0f} seconds of long files are used."
            if problems:
                self._say((msg + " " if msg else "") + " ".join(problems), bad=not added, warn=bool(added))
            else:
                self._say(msg)

    def _add_from_recording(self):
        p = self._current()
        if not p:
            return
        parts = [x for x in (self.get_parts() or []) if os.path.isfile(x)]
        if not parts:
            self._say("Add the recording parts in the transcription window first (step 1), then come back.", bad=True)
            return
        dlg = ClipPickerDialog(self, p["name"], parts, self.get_out_dir(), self.ffmpeg, self._add_sample_cb, self.player)
        dlg.exec()
        self.player.stop()

    def _play_sample(self):
        p = self._current()
        items = self.lst_samples.selectedItems()
        if not p or not items:
            return
        s = next((s for s in p["samples"] if s["file"] == items[0].data(Qt.ItemDataRole.UserRole)), None)
        if s:
            try:
                self.player.play(S.read_wav_range(self.profiles.sample_file(p["id"], s), 0, 1e9))
            except Exception as e:
                self._say(f"Could not play the sample: {e}", bad=True)

    def _remove_samples(self):
        p = self._current()
        if not p:
            return
        files = [i.data(Qt.ItemDataRole.UserRole) for i in self.lst_samples.selectedItems()]
        for f in files:
            self.profiles.remove_sample(p["id"], f)
        if files:
            self.player.stop()
            self._say(f"Removed {len(files)} sample(s).")
            self._fill_players()

    # ------------------------------------------------------------------ check
    def _bg(self, fn, cb):
        self._tok += 1
        tok = self._tok
        self._cbs[tok] = cb
        bridge = self._bridge

        def run():
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, e
            try:
                bridge.done.emit(tok, res, err)
            except RuntimeError:
                pass
        threading.Thread(target=run, name="speakers-ui", daemon=True).start()

    def _bg_done(self, tok, res, err):
        cb = self._cbs.pop(tok, None)
        if cb:
            cb(res, err)

    def _dl_progress(self, done, total):
        self._say(f"Downloading the speaker model (one time): {done / 1048576:.0f} of {max(total, 1) / 1048576:.0f} MB")

    def _check(self):
        ok, why = S.available()
        if not ok:
            self._say(why, bad=True)
            return
        self._busy = True
        self.btn_check.setEnabled(False)
        bridge = self._bridge
        app_dir = self.app_dir
        self._say("Comparing the voices...")

        def work():
            if not S.model_installed(app_dir):
                S.download_model(app_dir, lambda d, t: bridge.progress.emit(int(d), int(t)))
            prof = S.Profiles(app_dir)
            emb = S.Embedder(app_dir, 2)
            return S.compare_players(prof.centroids(emb))

        def done(res, err):
            self._busy = False
            self._player_changed()
            if err is not None:
                self._say(str(err), bad=True)
                return
            if not res:
                self._say("Add voice samples to at least two players first.", warn=True)
                return
            a, b, sim = res[0]
            if sim >= 0.55:
                self._say(f"{a} and {b} sound very alike to the program ({sim:.2f}). They may get mixed up. More or "
                          "clearer samples of both help.", warn=True)
            elif sim >= 0.4:
                self._say(f"{a} and {b} are fairly close ({sim:.2f}). Clear samples of both keep them apart.", warn=True)
            else:
                self._say(f"All voices are clearly different (closest pair: {a} and {b}, {sim:.2f}).")
        self._bg(work, done)

    def done(self, r):
        self.player.stop()
        super().done(r)
