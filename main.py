import sys
import os
import faulthandler
import traceback
import logging
# Audio Recording
import sounddevice as sd
import soundfile as sf
import threading
import queue
import datetime
import numpy as np
import subprocess
import re


# Shown in the window title. Keep it the same as the GitHub release tag.
APP_VERSION = "1.0.5-beta"


def _app_dir():
    # Folder of the running script, or of the .exe once frozen by PyInstaller
    # (for a onefile build __file__ points into a temp folder that is deleted on exit).
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _resource_path(rel):
    # Bundled read-only files (icon...). PyInstaller unpacks --add-data files to sys._MEIPASS.
    cands = []
    mp = getattr(sys, "_MEIPASS", None)
    if mp:
        cands.append(os.path.join(mp, rel))
    cands += [os.path.join(_app_dir(), rel), os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


from PyQt6.QtCore import Qt, QPoint, QPointF, pyqtSignal, QObject, QUrl, QTimer, QRectF, QVariantAnimation
###pip install sounddevice soundfile
###convert to exe
####pip install pyinstaller
####pyinstaller --noconsole --onefile --name "Astratera-Weltentor" main.py


# --- CRASH LOGGER SETUP ---
log_path = os.path.join(_app_dir(), "crash.log")
logging.basicConfig(
    filename=log_path,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Catches segfaults and stack overflows → writes C-level traceback to crash.log
faulthandler.enable(open(log_path, "a"))


# Catches Python-level exceptions that would otherwise be swallowed
def handle_exception(exc_type, exc_value, exc_tb):
    logging.critical("Uncaught Python exception", exc_info=(exc_type, exc_value, exc_tb))
    try:
        traceback.print_exception(exc_type, exc_value, exc_tb)
    except Exception:
        pass        # windowed exe has no console


sys.excepthook = handle_exception
# --- END CRASH LOGGER ---
import sys
import os
import json
import math
import random
import ctypes
import urllib.request
import shutil

def _setup_vlc():
    # Finds libvlc without any hardcoded user path. Order: a "vlc" folder shipped with the
    # app (portable build) -> the one bundled inside the exe -> a normal VLC installation.
    if os.name != "nt":
        return
    candidates = [os.path.join(_app_dir(), "vlc"),
                  os.path.join(getattr(sys, "_MEIPASS", ""), "vlc")]
    for env in ("VLC_HOME", "VLC_PATH"):
        if os.environ.get(env):
            candidates.append(os.environ[env])
    for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
               r"C:\Program Files", r"C:\Program Files (x86)"):
        if pf:
            candidates.append(os.path.join(pf, "VideoLAN", "VLC"))
    for c in candidates:
        try:
            if c and os.path.isfile(os.path.join(c, "libvlc.dll")):
                os.add_dll_directory(c)
                os.environ.setdefault("PYTHON_VLC_LIB_PATH", os.path.join(c, "libvlc.dll"))
                os.environ.setdefault("PYTHON_VLC_MODULE_PATH", c)
                if os.path.isdir(os.path.join(c, "plugins")):
                    os.environ.setdefault("VLC_PLUGIN_PATH", os.path.join(c, "plugins"))
                return
        except Exception as e:
            logging.warning(f"VLC path {c!r} unusable: {e}")
    logging.warning("No VLC found next to the app or in Program Files - install VLC (64-bit).")


_setup_vlc()

import vlc
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QPushButton,
                             QVBoxLayout, QHBoxLayout, QListWidget, QLabel,
                             QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                             QInputDialog, QSlider, QLineEdit, QGraphicsProxyWidget,
                             QCheckBox, QFileDialog, QMessageBox, QDialog, QMenu,
                             QComboBox, QGraphicsRectItem, QDoubleSpinBox, QListWidgetItem, QSplitter,
                             QTreeWidget, QTreeWidgetItem, QSizePolicy)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QObject, QUrl, QTimer, QRectF, QVariantAnimation, QSize
from PyQt6.QtGui import QDesktopServices, QPixmap, QImage, QAction, QPen, QColor, QPalette, QPainter, QBrush, QShortcut, QKeySequence, QIcon
try:
    import lights as lights_mod                        # scripts + smart lights (Hue)
except Exception as _lights_err:
    lights_mod = None
    logging.warning(f"lights.py not available: {_lights_err}")
try:
    import transcribe_dialog                           # 📝 local transcription of recordings (whisper.cpp)
except Exception as _tr_err:
    transcribe_dialog = None
    logging.warning(f"transcribe_dialog.py not available: {_tr_err}")
try:
    import light_designer                              # 💡 Light Designer window (Hue)
except Exception as _ld_err:
    light_designer = None
    logging.warning(f"light_designer.py not available: {_ld_err}")
try:
    from quickfire_engine import QuickFireEngine      # optional: fast preloaded Quick Fire playback
except Exception as _qf_err:                           # missing file / broken install -> plain VLC playback
    QuickFireEngine = None
    logging.warning(f"Fast Quick Fire engine not available: {_qf_err}")

# The config lives next to the app (script or .exe), NOT in whatever folder it was started from.
CONFIG_FILE = os.path.join(_app_dir(), "director_config.json")
if not os.path.exists(CONFIG_FILE) and os.path.exists("director_config.json"):
    CONFIG_FILE = os.path.abspath("director_config.json")    # config from an older version
SCENE_SETTINGS_FILE = "scene_settings.json"
# Session Manager integration: an external tool (not part of this script)
# can write a small JSON command here to remotely trigger a session/scene/
# layout change — see check_remote_command()/apply_remote_command() below,
# the only two places that read this file. Nothing else in this script
# depends on it, and if it never appears, nothing changes.
#
# Resolved next to wherever this is actually running from — but that is
# NOT simply os.path.dirname(os.path.abspath(__file__)): once this is a
# frozen PyInstaller executable, __file__ no longer points at the real
# exe location at all — for a onefile build specifically, it resolves
# inside a temporary extraction folder that gets deleted the moment the
# app closes, which is exactly what silently broke this after the exe
# was built (it worked correctly during development, running as a plain
# script, since __file__ behaves normally there). sys.executable is the
# right source of truth once frozen (PyInstaller sets sys.frozen=True
# and points sys.executable at the actual exe), so both cases are
# checked explicitly rather than assuming either one.
REMOTE_COMMAND_FILE = os.path.join(_app_dir(), "director_remote_command.json")


# ---------------------------------------------------------------------------
# Portable paths. Everything the app WRITES into project files (scene_settings.json,
# layouts, playlists) is stored relative to an anchor folder instead of as a full
# path, so a project can be copied to another PC:
#   {ROOT}/Session/Scene/visuals/x.png   relative to the sessions folder (top bar)
#   {MUSIC}/Jazz/a.mp3                   relative to the music library folder
#   {SFX}/Rain/r.wav   {VISUALS}/...     likewise for the SFX / visuals library
# Paths are expanded to normal absolute paths when loaded, so the rest of the app
# never sees the tokens. Only machine settings (director_config.json) hold full paths.
# ---------------------------------------------------------------------------
_ANCHOR_PROVIDER = lambda: {}
_TOKEN_RE = re.compile(r"^\{([A-Z]+)\}(?:[/\\](.*))?$")


def set_anchor_provider(fn):
    global _ANCHOR_PROVIDER
    _ANCHOR_PROVIDER = fn


def _anchors():
    try:
        return {k: v for k, v in _ANCHOR_PROVIDER().items() if v and isinstance(v, str)}
    except Exception:
        return {}


def to_portable(p):
    if not isinstance(p, str) or not p or _TOKEN_RE.match(p) or not os.path.isabs(p):
        return p
    np_ = os.path.normcase(os.path.normpath(p))
    best = None
    for token, base in _anchors().items():
        nb = os.path.normcase(os.path.normpath(base))
        if np_ == nb or np_.startswith(nb.rstrip("\\/") + os.sep):
            if best is None or len(nb) > best[0]:
                best = (len(nb), token, base)
    if not best:
        return p
    rel = os.path.relpath(os.path.normpath(p), os.path.normpath(best[2])).replace("\\", "/")
    return "{%s}" % best[1] if rel == "." else "{%s}/%s" % (best[1], rel)


def resolve_path(p):
    """Token path -> absolute path on this PC. A full path that no longer exists is
    re-anchored in the sessions folder if its last four parts (session/scene/folder/file)
    exist there (old project files written before paths became portable)."""
    if not isinstance(p, str) or not p:
        return p
    m = _TOKEN_RE.match(p)
    if m:
        base = _anchors().get(m.group(1))
        if not base:
            return p
        rel = [x for x in (m.group(2) or "").replace("\\", "/").split("/") if x]
        return os.path.normpath(os.path.join(base, *rel))
    if os.path.exists(p):
        return p
    root = _anchors().get("ROOT")
    parts = [x for x in re.split(r"[\\/]+", p) if x]
    if root and len(parts) >= 4:
        cand = os.path.join(root, *parts[-4:])
        if os.path.exists(cand):
            return cand
    return p

STAGE_W, STAGE_H = 1920, 1080
STAGE_X, STAGE_Y = (5000 - STAGE_W) // 2, (5000 - STAGE_H) // 2

DARK_STYLE = """
    QMainWindow, QDialog { background-color: #121212; color: #e0e0e0; }
    QWidget { background-color: #121212; color: #e0e0e0; font-family: 'Segoe UI', Arial; }
    QLabel { background-color: transparent; color: #e0e0e0; font-weight: bold; }
    QLineEdit, QComboBox, QListWidget, QDoubleSpinBox { background-color: #1e1e1e; border: 1px solid #333333; border-radius: 4px; color: #e0e0e0; padding: 4px; }
    QListWidget::item:selected { background-color: #3d3d3d; color: #ffffff; }
    QPushButton { background-color: #2d2d2d; border: 1px solid #444444; border-radius: 4px; color: #e0e0e0; padding: 6px; min-height: 20px; }
    QPushButton:hover { background-color: #3d3d3d; border: 1px solid #555555; }
    QSlider::groove:horizontal { border: 1px solid #333; height: 6px; background: #1e1e1e; margin: 2px 0; border-radius: 3px; }
    QSlider::handle:horizontal { background: #4caf50; border: 1px solid #4caf50; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
    QCheckBox::indicator { width: 18px; height: 18px; background-color: #1e1e1e; border: 1px solid #444; border-radius: 3px; }
    QCheckBox::indicator:checked { background-color: #4caf50; }
"""

ACTIVE_BTN_STYLE = "background-color: #050505; color: #ffffff; border: 1px inset #111; font-weight: bold;"
INACTIVE_BTN_STYLE = "background-color: #2d2d2d; color: #e0e0e0; border: 1px solid #444;"
AUDIO_EXTS = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.wma', '.opus'}
ROOT_LABEL = "--- Root Folder ---"


def list_subfolders_recursive(root):
    """All sub-folders below root, any depth, as '/'-joined relative paths in
    depth-first order (parent directly followed by its children), A-Z per level."""
    out, seen = [], set()

    def walk(abs_dir, rel):
        try:
            real = os.path.realpath(abs_dir)
            if real in seen:          # guards against symlink loops
                return
            seen.add(real)
            names = sorted((d for d in os.listdir(abs_dir)
                            if os.path.isdir(os.path.join(abs_dir, d))), key=str.lower)
        except Exception:
            return
        for d in names:
            r = f"{rel}/{d}" if rel else d
            out.append(r)
            walk(os.path.join(abs_dir, d), r)

    if root and os.path.isdir(root):
        walk(root, "")
    return out
PLAYLIST_CATEGORY = "★ Playlist"

class BlankingRect(QGraphicsRectItem):
    def __init__(self, x, y, w, h, parent_app):
        super().__init__(0, 0, w, h)
        self.main = parent_app
        self.setPos(x, y)
        self.setBrush(QBrush(Qt.GlobalColor.black))
        self.setPen(QPen(Qt.GlobalColor.transparent))
        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsRectItem.GraphicsItemFlag.ItemIsFocusable)
        self.setZValue(99999)
        self.last_mouse_pos = None
        self.intended_opacity = 1.0

    def contextMenuEvent(self, event):
        menu = QMenu()
        save_act = QAction("⭐ Apply position to layouts...", menu)
        save_act.triggered.connect(self.open_apply_dialog)
        menu.addAction(save_act)
        menu.exec(event.screenPos())

    def open_apply_dialog(self):
        new_data = {
            "x": self.x(), "y": self.y(),
            "w": self.rect().width(), "h": self.rect().height(),
            "scale": self.scale(), "opacity": self.intended_opacity
        }
        dlg = ApplyBlankingDialog(self.main, new_data)
        dlg.exec()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.last_mouse_pos = event.pos();
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.RightButton and self.last_mouse_pos:
            delta_x = event.pos().x() - self.last_mouse_pos.x()
            factor = math.pow(1.1, delta_x * 0.005)
            self.setScale(self.scale() * factor)
            self.last_mouse_pos = event.pos()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.scene(): self.scene().removeItem(self)
        else:
            super().keyPressEvent(event)


class MiniControlWindow(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.main = parent
        self.setWindowTitle("DM Mini Controls")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setStyleSheet(DARK_STYLE + "QWidget { background-color: #1a1a1a; border: 1px solid #444; }")
        self.setFixedWidth(600)
        self.setFixedHeight(120)
        self.last_sync_path = ""
        self.current_layout_index = -1

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 5, 10, 5)
        main_layout.setSpacing(4)

        # Row 1: Next Layout | Layout dropdown | Next Scene
        row1 = QHBoxLayout()
        self.btn_next_layout = QPushButton("⏭ NEXT LAYOUT")
        self.btn_next_layout.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold;")
        self.btn_next_layout.clicked.connect(self.trigger_next_layout)
        self.combo_layouts = QComboBox()
        self.combo_layouts = QComboBox()
        self.combo_layouts.setPlaceholderText("Select Layout...")
        self.combo_layouts.currentTextChanged.connect(self.on_layout_selected)
        self.combo_layouts.currentTextChanged.connect(
            lambda t: self.combo_layouts.setToolTip(t))
        self.combo_layouts.view().setMinimumWidth(350)
        self.combo_scenes = QComboBox()
        self.combo_scenes.setPlaceholderText("Select Scene...")
        self.combo_scenes.currentTextChanged.connect(self.on_scene_selected)
        self.combo_scenes.currentTextChanged.connect(
            lambda t: self.combo_scenes.setToolTip(t))
        self.combo_scenes.view().setMinimumWidth(350)
        self.btn_next_scene = QPushButton("🎬 NEXT SCENE")
        self.btn_next_scene.setStyleSheet("background-color: #fbc02d; color: black; font-weight: bold;")
        self.btn_next_scene.setFixedWidth(110)
        self.btn_next_scene.clicked.connect(self.main.trigger_next_scene)
        row1.addWidget(self.btn_next_layout, 2)
        row1.addWidget(self.combo_layouts, 2)
        row1.addWidget(self.combo_scenes, 2)
        row1.addWidget(self.btn_next_scene, 2)
        main_layout.addLayout(row1)

        # Next layout / next scene info row
        info_row = QHBoxLayout()
        self.lbl_next_layout = QLabel("next: ---")
        self.lbl_next_layout.setStyleSheet("color: #4caf50; font-size: 10px; padding-left: 2px;")
        self.lbl_next_scene = QLabel("next: ---")
        self.lbl_next_scene.setStyleSheet("color: #4caf50; font-size: 10px; padding-right: 2px;")
        self.lbl_next_scene.setAlignment(Qt.AlignmentFlag.AlignRight)
        info_row.addWidget(self.lbl_next_layout, 1)
        info_row.addWidget(self.lbl_next_scene, 1)
        main_layout.addLayout(info_row)

        # Row 2: audio controls + mark
        row2 = QHBoxLayout()
        btn_prev = QPushButton("⏮")
        btn_prev.setFixedWidth(35)
        btn_prev.clicked.connect(self.main.prev_music)
        self.btn_m_pause = QPushButton("⏸ MUS")
        self.btn_m_pause.clicked.connect(self.main.toggle_music_pause)
        self.btn_s_pause = QPushButton("⏸ SFX")
        self.btn_s_pause.clicked.connect(self.main.toggle_sfx_pause)
        btn_next = QPushButton("⏭")
        btn_next.setFixedWidth(35)
        btn_next.clicked.connect(self.main.next_music)
        self.btn_mini_mark = QPushButton("📍 Mark")
        self.btn_mini_mark.setStyleSheet("background-color: #1a237e; color: white; font-weight: bold;")
        self.btn_mini_mark.setEnabled(False)
        self.btn_mini_mark.clicked.connect(self.main.rec_mark)
        row2.addWidget(btn_prev)
        row2.addWidget(self.btn_m_pause)
        row2.addWidget(self.btn_s_pause)
        row2.addWidget(btn_next)
        row2.addWidget(self.btn_mini_mark)
        main_layout.addLayout(row2)

        # Row 3: track info
        row3 = QHBoxLayout()
        info_v = QVBoxLayout()
        self.lbl_music = QLabel("Now: ---")
        self.lbl_music.setStyleSheet("color: #4caf50; font-size: 11px;")
        self.lbl_next = QLabel("Next: ---")
        self.lbl_next.setStyleSheet("color: #81c784; font-size: 10px;")
        info_v.addWidget(self.lbl_music)
        info_v.addWidget(self.lbl_next)
        self.lbl_time = QLabel("00:00")
        self.lbl_time.setStyleSheet("color: #aaa; font-family: 'Consolas'; font-size: 12px;")
        row3.addLayout(info_v, 1)
        row3.addWidget(self.lbl_time)
        main_layout.addLayout(row3)

        self.timer = QTimer()
        self.timer.timeout.connect(self.sync_info)
        self.timer.start(500)

    def on_layout_selected(self, text):
        if text and text != "Select Layout...":
            self.current_layout_index = self.combo_layouts.currentIndex() - 1  # -1 for placeholder
            self.main.load_layout_with_fade(text)
            self.update_next_layout_label()

    def on_scene_selected(self, text):
        if text and text != "Select Scene...":
            items = self.main.scene_list.findItems(text, Qt.MatchFlag.MatchExactly)
            if items:
                self.main.scene_list.setCurrentItem(items[0])
                self.main.set_active_scene(items[0])
                # Clear music selection so new scene starts fresh
                self.main.music_list.blockSignals(True)
                self.main.music_list.clearSelection()
                self.main.music_list.setCurrentRow(-1)
                self.main.music_list.blockSignals(False)
                self.main.start_all_master()
                self.last_sync_path = ""
                self.update_next_scene_label()

    def trigger_next_layout(self):
        count = self.combo_layouts.count()
        # index 0 is placeholder "Select Layout...", layouts start at 1
        current = self.combo_layouts.currentIndex()
        next_idx = current + 1 if current >= 1 else 1
        if next_idx < count:
            self.combo_layouts.setCurrentIndex(next_idx)
        # if already at last, do nothing

    def update_next_layout_label(self):
        count = self.combo_layouts.count()
        current = self.combo_layouts.currentIndex()
        next_idx = current + 1
        if current >= 1 and next_idx < count:
            self.lbl_next_layout.setText(f"next: {self.combo_layouts.itemText(next_idx)}")
            self.lbl_next_layout.setStyleSheet("color: #4caf50; font-size: 10px; padding-left: 2px;")
        else:
            self.lbl_next_layout.setText("no more layouts")
            self.lbl_next_layout.setStyleSheet("color: #e53935; font-size: 10px; padding-left: 2px;")

    def update_next_scene_label(self):
        scene_list = self.main.scene_list
        count = scene_list.count()
        current = scene_list.currentRow()
        next_idx = (current + 1) % count if count > 0 else -1
        if count > 1 and next_idx != 0:
            name = scene_list.item(next_idx).text()
            self.lbl_next_scene.setText(f"next: {name}")
            self.lbl_next_scene.setStyleSheet("color: #4caf50; font-size: 10px; padding-right: 2px;")
        elif count > 1 and next_idx == 0:
            # wrapped around to beginning
            name = scene_list.item(0).text()
            self.lbl_next_scene.setText(f"↩ {name}")
            self.lbl_next_scene.setStyleSheet("color: #e53935; font-size: 10px; padding-right: 2px;")
        else:
            self.lbl_next_scene.setText("no more scenes")
            self.lbl_next_scene.setStyleSheet("color: #e53935; font-size: 10px; padding-right: 2px;")

    def sync_info(self):
        self.lbl_music.setText(self.main.lbl_now_playing.text())
        self.lbl_next.setText(self.main.lbl_next_playing.text())
        self.lbl_time.setText(self.main.lbl_time_rem.text() or "--:--")
        # Sync mark button with recording state
        can_mark = self.main.recorder.is_recording and not self.main.recorder.is_paused
        self.btn_mini_mark.setEnabled(can_mark)
        if self.main.scene_path != self.last_sync_path:
            self.last_sync_path = self.main.scene_path
            self.combo_layouts.blockSignals(True)
            self.combo_layouts.clear()
            self.combo_layouts.addItem("Select Layout...")
            for i in range(self.main.layout_list.count()):
                self.combo_layouts.addItem(self.main.layout_list.item(i).text())
            # Auto-select first layout if available
            if self.combo_layouts.count() > 1:
                self.combo_layouts.setCurrentIndex(1)
            self.combo_layouts.blockSignals(False)
            # Populate scene dropdown
            self.combo_scenes.blockSignals(True)
            self.combo_scenes.clear()
            self.combo_scenes.addItem("Select Scene...")
            for i in range(self.main.scene_list.count()):
                self.combo_scenes.addItem(self.main.scene_list.item(i).text())
            # Select current scene
            current_scene = os.path.basename(self.main.scene_path) if self.main.scene_path else ""
            if current_scene:
                idx = self.combo_scenes.findText(current_scene)
                if idx != -1:
                    self.combo_scenes.setCurrentIndex(idx)
            self.combo_scenes.blockSignals(False)
            self.update_next_layout_label()
            self.update_next_scene_label()


class PreviewView(QGraphicsView):
    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        painter.save();
        pen = QPen(QColor(0, 255, 0, 180));
        pen.setWidth(4);
        pen.setCosmetic(True);
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen);
        painter.drawRect(STAGE_X, STAGE_Y, STAGE_W, STAGE_H)
        painter.setBrush(QColor(0, 255, 0, 100));
        painter.drawText(STAGE_X + 10, STAGE_Y + 30, "PROJECTION AREA (PLAYER VIEW)");
        painter.restore()


class StageWindow(QMainWindow):
    """The projector window. Tells the app when its size changes so the stage can be re-fitted."""
    def __init__(self):
        super().__init__()
        self.on_resize = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.on_resize:
            try:
                self.on_resize()
            except Exception:
                logging.exception("stage resize handler failed")


class SettingsDialog(QDialog):
    def __init__(self, parent_app):
        super().__init__(parent_app)
        self.main = parent_app
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self.setStyleSheet(DARK_STYLE)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("🚀 Projector"))
        self.chk_fit = QCheckBox("Fit the stage to the projector screen")
        self.chk_fit.setChecked(bool(parent_app.fit_projector))
        self.chk_fit.toggled.connect(self._on_fit_toggled)
        lay.addWidget(self.chk_fit)
        info = QLabel(
            "Your layouts are always built on a 1920×1080 stage and never change. On: the whole stage is scaled to fill "
            "the projector screen, whatever its resolution (for example 1280×720 or 4K), and nothing outside the stage "
            "is shown. Off: the stage is shown pixel for pixel, as in earlier versions. On a 1080p projector both look "
            "the same. Videos keep their own quality either way. Scaling up to a 4K screen needs more processor power.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-weight: normal; font-size: 11px;")
        lay.addWidget(info)
        self.lbl_screen = QLabel(parent_app._projector_screen_text())
        self.lbl_screen.setStyleSheet("color: #81c784; font-weight: normal;")
        lay.addWidget(self.lbl_screen)

        lay.addSpacing(8)
        lay.addWidget(QLabel("⚡ Quick Fire"))
        self.chk_fast_qf = QCheckBox("Fast Quick Fire engine (experimental)")
        self.chk_fast_qf.setChecked(bool(parent_app.fast_quickfire))
        self.chk_fast_qf.setEnabled(QuickFireEngine is not None)
        self.chk_fast_qf.toggled.connect(self._on_fast_qf_toggled)
        lay.addWidget(self.chk_fast_qf)
        info2 = QLabel(
            "Loads your Quick Fire sounds into memory and plays them through an audio output that stays open. "
            "A sound then starts the moment you press FIRE, always at the same point, so nothing is cut off at "
            "the beginning and you need no extra silence. All channels are kept (7.1 stays 7.1) and the Quick Fire "
            "volume slider works as usual. The button shows ⚡ FIRE when the fast engine will play the selected sound. "
            "A sound that is not loaded yet, files longer than 2 minutes, or any problem fall back to the normal "
            "VLC playback automatically.")
        info2.setWordWrap(True)
        info2.setStyleSheet("color: #aaa; font-weight: normal; font-size: 11px;")
        lay.addWidget(info2)
        self.lbl_qf_status = QLabel(parent_app._qf_status_text())
        self.lbl_qf_status.setWordWrap(True)
        self.lbl_qf_status.setStyleSheet("color: #81c784; font-weight: normal;")
        lay.addWidget(self.lbl_qf_status)
        self.btn_qf_test = QPushButton("🔊 Test speakers (one beep per speaker)")
        self.btn_qf_test.clicked.connect(self._on_qf_test)
        lay.addWidget(self.btn_qf_test)
        self.lbl_qf_test = QLabel("")
        self.lbl_qf_test.setWordWrap(True)
        self.lbl_qf_test.setStyleSheet("color: #aaa; font-weight: normal; font-size: 11px;")
        lay.addWidget(self.lbl_qf_test)
        self._refresh_qf()
        parent_app._qf_bridge.changed.connect(self._refresh_qf)
        self.finished.connect(self._disconnect_qf)

        row = QHBoxLayout()
        row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        lay.addLayout(row)

    def _on_fit_toggled(self, checked):
        self.main.set_fit_projector(bool(checked))

    def _on_fast_qf_toggled(self, checked):
        self.main.set_fast_quickfire(bool(checked))
        self._refresh_qf()

    def _refresh_qf(self, *args):
        try:
            self.lbl_qf_status.setText(self.main._qf_status_text())
            e = self.main.qf_engine
            self.btn_qf_test.setEnabled(bool(e is not None and e.active))
        except Exception:
            pass

    def _disconnect_qf(self, *args):
        try:
            self.main._qf_bridge.changed.disconnect(self._refresh_qf)
        except Exception:
            pass

    def _on_qf_test(self):
        e = self.main.qf_engine
        if e is None or not e.active:
            return
        from quickfire_engine import SPEAKER_NAMES
        names = SPEAKER_NAMES.get(e.channels, [f"Channel {i + 1}" for i in range(e.channels)])
        if e.speaker_test():
            self.lbl_qf_test.setText("Beeps in this order: " + "  →  ".join(names) +
                                     "\nIf a speaker stays silent or the order is wrong, the Windows speaker setup "
                                     "for this output is probably not 7.1.")


class SaveLayoutDialog(QDialog):
    def __init__(self, existing_layouts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save Layout");
        self.setMinimumWidth(300);
        self.setStyleSheet(DARK_STYLE)
        layout = QVBoxLayout(self);
        layout.addWidget(QLabel("Name your layout:"))
        self.name_edit = QLineEdit();
        layout.addWidget(self.name_edit);
        self.list_widget = QListWidget()
        for l in existing_layouts: self.list_widget.addItem(l.replace(".json", ""))
        self.list_widget.itemClicked.connect(lambda it: self.name_edit.setText(it.text()));
        layout.addWidget(self.list_widget)
        btns = QHBoxLayout();
        save_btn = QPushButton("Save");
        save_btn.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        save_btn.clicked.connect(self.accept);
        cancel_btn = QPushButton("Cancel");
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(save_btn);
        btns.addWidget(cancel_btn);
        layout.addLayout(btns)

    def get_name(self): return self.name_edit.text()


class ApplyBlankingDialog(QDialog):
    def __init__(self, parent_app, new_blanking_data):
        super().__init__()
        self.main = parent_app
        self.new_data = new_blanking_data
        self.setWindowTitle("Apply Blanking to Layouts")
        self.setMinimumWidth(450)
        self.setMinimumHeight(500)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select layouts to apply this blanking position to:"))
        layout.addWidget(QLabel("(All existing blanking in selected layouts will be replaced)"))

        # Select all / none buttons
        btn_row = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_none = QPushButton("Select None")
        btn_all.clicked.connect(self.select_all)
        btn_none.clicked.connect(self.select_none)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        layout.addLayout(btn_row)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        layout.addWidget(self.list_widget)

        # Populate with all layouts from all sessions
        self.layout_paths = {}  # display_name -> full path
        root = self.main.root_path
        if os.path.exists(root):
            for session in sorted(os.listdir(root)):
                session_path = os.path.join(root, session)
                if not os.path.isdir(session_path): continue
                for scene in sorted(os.listdir(session_path)):
                    layouts_path = os.path.join(session_path, scene, "layouts")
                    if not os.path.exists(layouts_path): continue
                    for lf in sorted(os.listdir(layouts_path)):
                        if not lf.endswith(".json"): continue
                        display = f"{session} / {scene} / {lf.replace('.json', '')}"
                        full_path = os.path.join(layouts_path, lf)
                        self.layout_paths[display] = full_path
                        self.list_widget.addItem(display)

        btns = QHBoxLayout()
        ok_btn = QPushButton("✅ Apply to Selected")
        ok_btn.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        ok_btn.clicked.connect(self.apply)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

        self.result_label = QLabel("")
        self.result_label.setStyleSheet("color: #4caf50; font-size: 10px;")
        layout.addWidget(self.result_label)

    def select_all(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setSelected(True)

    def select_none(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setSelected(False)

    def apply(self):
        selected = [item.text() for item in self.list_widget.selectedItems()]
        if not selected:
            self.result_label.setText("Nothing selected.")
            return

        success, failed = 0, 0
        new_blanking_entry = {
            "x": self.new_data["x"], "y": self.new_data["y"],
            "w": self.new_data["w"], "h": self.new_data["h"],
            "scale": self.new_data["scale"], "opacity": self.new_data["opacity"]
        }

        for display in selected:
            path = self.layout_paths[display]
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                # Handle legacy list format
                if isinstance(data, list):
                    data = {"visuals": data, "blanking": [], "music_order": []}
                # Replace ALL blanking with the single new one
                data["blanking"] = [new_blanking_entry]
                with open(path, 'w') as f:
                    json.dump(data, f)
                success += 1
            except Exception as e:
                logging.warning(f"Failed to update blanking in {path}: {e}")
                failed += 1

        msg = f"✅ Updated {success} layout(s)."
        if failed: msg += f" ⚠️ {failed} failed."
        self.result_label.setText(msg)
        # Also update default_blanking_data on the main app
        self.main.default_blanking_data = new_blanking_entry
        self.main.save_config()


# Audio
class RecordingSetupDialog(QDialog):
    def __init__(self, parent_app, suggested_name, recording_path):
        super().__init__()
        self.main = parent_app
        self.setWindowTitle("Start Session Recording")
        self.setMinimumWidth(480)
        self.setStyleSheet(DARK_STYLE)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Recording filename (no extension):"))
        self.name_edit = QLineEdit(suggested_name)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Save recordings to folder:"))
        path_row = QHBoxLayout()
        self.txt_path = QLineEdit(recording_path)
        btn_browse = QPushButton("...")
        btn_browse.setFixedWidth(30)
        btn_browse.clicked.connect(self.browse)
        path_row.addWidget(self.txt_path)
        path_row.addWidget(btn_browse)
        layout.addLayout(path_row)

        layout.addWidget(QLabel("Input device:"))
        self.combo_device = QComboBox()
        self._devices = sd.query_devices()
        for i, d in enumerate(self._devices):
            if d['max_input_channels'] > 0:
                self.combo_device.addItem(f"{i}: {d['name']}", i)
        # pre-select default input
        try:
            default = sd.default.device[0]
            for j in range(self.combo_device.count()):
                if self.combo_device.itemData(j) == default:
                    self.combo_device.setCurrentIndex(j)
                    break
        except:
            pass
        layout.addWidget(self.combo_device)

        btns = QHBoxLayout()
        ok_btn = QPushButton("⏺ Start Recording")
        ok_btn.setStyleSheet("background-color: #b71c1c; color: white; font-weight: bold;")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    def browse(self):
        p = QFileDialog.getExistingDirectory(self, "Select Recording Folder", self.txt_path.text())
        if p:
            self.txt_path.setText(p)

    def get_values(self):
        return (
            self.name_edit.text().strip(),
            self.txt_path.text().strip(),
            self.combo_device.currentData()
        )


class VLCBridge(QObject):
    track_ended = pyqtSignal()


class DraggableVideo(QGraphicsPixmapItem):
    def __init__(self, file_name, path):
        base_pix = QPixmap(640, 360);
        base_pix.fill(Qt.GlobalColor.black)
        super().__init__(base_pix);
        self.file_name = file_name;
        self.path = path
        self.setFlags(
            QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable | QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsPixmapItem.GraphicsItemFlag.ItemIsFocusable)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setTransformOriginPoint(0, 0)  # keep top-left as origin, consistent with setPos math
        self.instance = vlc.Instance("--quiet", "--no-xlib", "--aout=directsound")
        self.player = self.instance.media_player_new()
        self.player.audio_set_volume(100)
        self.v_width, self.v_height = 1280, 720;
        self.buffer = (ctypes.c_ubyte * (self.v_width * self.v_height * 4))()
        self._lock_cb = vlc.CallbackDecorators.VideoLockCb(self._lock);
        self._unlock_cb = vlc.CallbackDecorators.VideoUnlockCb(self._unlock);
        self._display_cb = vlc.CallbackDecorators.VideoDisplayCb(self._display)
        self.player.video_set_callbacks(self._lock_cb, self._unlock_cb, self._display_cb, None);
        self.player.video_set_format("RV32", self.v_width, self.v_height, self.v_width * 4)
        media = self.instance.media_new(path);
        media.add_option('input-repeat=65535');
        self.player.set_media(media)
        self.player.play();
        self._active_video_item = None
        QTimer.singleShot(100, lambda: self.player.audio_set_volume(100))
        self.timer = QTimer();
        self.timer.timeout.connect(self.update_frame);
        self.timer.start(32);
        self.last_mouse_pos = None
        self.intended_opacity = 1.0

    def _lock(self, opaque, planes):
        planes[0] = ctypes.cast(self.buffer, ctypes.c_void_p);
        return None

    def _unlock(self, opaque, picture, planes):
        pass

    def _display(self, opaque, picture):
        pass

    def update_frame(self):
        img = QImage(self.buffer, self.v_width, self.v_height, QImage.Format.Format_ARGB32)
        if not img.isNull():
            pix = QPixmap.fromImage(img);
            self.setPixmap(pix.scaled(self.v_width, self.v_height, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.last_mouse_pos = event.pos();
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.RightButton and self.last_mouse_pos:
            delta_x = event.pos().x() - self.last_mouse_pos.x();
            factor = math.pow(1.1, delta_x * 0.005);
            self.setScale(self.scale() * factor);
            self.last_mouse_pos = event.pos();
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.timer.stop();
            self.player.stop();
            self.player.release();
            [
                self.scene().removeItem(self) if self.scene() else None]
        else:
            super().keyPressEvent(event)


class DraggableImage(QGraphicsPixmapItem):
    def __init__(self, pixmap, file_name, path):
        super().__init__(pixmap);
        self.file_name = file_name;
        self.path = path
        self.setFlags(
            QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable | QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsPixmapItem.GraphicsItemFlag.ItemIsFocusable)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation);
        self.setTransformOriginPoint(self.boundingRect().center());
        self.last_mouse_pos = None
        self.intended_opacity = 1.0

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.last_mouse_pos = event.pos();
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.RightButton and self.last_mouse_pos:
            delta_x = event.pos().x() - self.last_mouse_pos.x();
            factor = math.pow(1.1, delta_x * 0.005);
            self.setScale(self.scale() * factor);
            self.last_mouse_pos = event.pos();
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            [self.scene().removeItem(self) if self.scene() else None]
        else:
            super().keyPressEvent(event)


class AnnotationOverlay(QGraphicsPixmapItem):
    def __init__(self, w, h):
        self.canvas_w, self.canvas_h = w, h
        self.image = QImage(w, h, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)
        super().__init__(QPixmap.fromImage(self.image))
        self.setPos(STAGE_X, STAGE_Y)
        self.setZValue(999998)  # below blanking (99999) but above everything else
        self.setAcceptHoverEvents(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self.drawing = False
        self.last_point = None
        self.draw_mode = "brush"  # "brush" or "rect"
        self.rect_start = None
        self.image_before_rect = None
        self.brush_color = QColor(255, 50, 50, 220)
        self.brush_size = 18
        self.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable, False)

    def clear(self):
        self.image.fill(Qt.GlobalColor.transparent)
        self.setPixmap(QPixmap.fromImage(self.image))

    def _commit(self):
        self.setPixmap(QPixmap.fromImage(self.image))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drawing = True
            p = event.pos().toPoint()
            if self.draw_mode == "rect":
                self.rect_start = p
                self.image_before_rect = self.image.copy()
            else:
                painter = QPainter(self.image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(self.brush_color, self.brush_size,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawPoint(p)
                painter.end()
                self._commit()
            self.last_point = p
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drawing and event.buttons() & Qt.MouseButton.LeftButton:
            p = event.pos().toPoint()
            if self.draw_mode == "rect" and self.rect_start:
                # Redraw from snapshot so rect preview updates live
                self.image = self.image_before_rect.copy()
                painter = QPainter(self.image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                r = QColor(self.brush_color)
                r.setAlpha(80)
                fill = QColor(self.brush_color)
                fill.setAlpha(40)
                painter.setPen(QPen(r, 3))
                painter.setBrush(fill)
                x1, y1 = min(self.rect_start.x(), p.x()), min(self.rect_start.y(), p.y())
                x2, y2 = max(self.rect_start.x(), p.x()), max(self.rect_start.y(), p.y())
                painter.drawRect(x1, y1, x2 - x1, y2 - y1)
                painter.end()
                self._commit()
            else:
                painter = QPainter(self.image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(self.brush_color, self.brush_size,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawLine(self.last_point, p)
                painter.end()
                self._commit()
            self.last_point = p
        event.accept()

    def mouseReleaseEvent(self, event):
        self.drawing = False
        self.rect_start = None
        self.image_before_rect = None
        event.accept()


# Audio
class SessionRecorder:
    """
    Chunk-based session recorder. Writes 30-minute WAV chunks continuously.
    Each chunk is a complete valid file the moment it closes.
    Thread-safe: recording runs in a daemon thread, Qt never blocks.
    """
    CHUNK_SECONDS = 30 * 60  # 30 minutes per chunk
    SAMPLERATE = 44100
    CHANNELS = 1

    def __init__(self):
        self.device_index = None
        self.output_folder = None
        self.base_name = None
        self.is_recording = False
        self.is_paused = False
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused by default
        self._thread = None
        self._queue = queue.Queue()
        self._chunk_index = 0
        self._elapsed_seconds = 0.0
        self._chunk_elapsed = 0.0
        self._current_file = None
        self._current_writer = None
        self.chunk_files = []  # list of written chunk paths
        self.on_chunk_rotated = None  # callback(new_path)

    def start(self, device_index, output_folder, base_name):
        self.device_index = device_index
        self.output_folder = output_folder
        self.base_name = base_name
        self.is_recording = True
        self.is_paused = False
        self._stop_event.clear()
        self._pause_event.set()
        self._chunk_index = 0
        self._elapsed_seconds = 0.0
        self._chunk_elapsed = 0.0
        self.chunk_files = []
        os.makedirs(output_folder, exist_ok=True)
        self._open_new_chunk()
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

    def _chunk_path(self):
        return os.path.join(
            self.output_folder,
            f"{self.base_name}_part{self._chunk_index:03d}.wav"
        )

    def _open_new_chunk(self):
        if self._current_writer:
            self._current_writer.close()
        path = self._chunk_path()
        self._current_writer = sf.SoundFile(
            path, mode='w',
            samplerate=self.SAMPLERATE,
            channels=self.CHANNELS,
            format='WAV', subtype='PCM_16'
        )
        self.chunk_files.append(path)
        self._chunk_elapsed = 0.0
        if self.on_chunk_rotated:
            self.on_chunk_rotated(path)
        return path

    def _record_loop(self):
        blocksize = 1024
        with sd.InputStream(
                device=self.device_index,
                samplerate=self.SAMPLERATE,
                channels=self.CHANNELS,
                dtype='int16',
                blocksize=blocksize
        ) as stream:
            while not self._stop_event.is_set():
                self._pause_event.wait()  # blocks while paused
                if self._stop_event.is_set():
                    break
                data, _ = stream.read(blocksize)
                if self.is_paused:
                    continue
                self._current_writer.write(data)
                seconds = blocksize / self.SAMPLERATE
                self._elapsed_seconds += seconds
                self._chunk_elapsed += seconds
                # Rotate chunk every CHUNK_SECONDS
                if self._chunk_elapsed >= self.CHUNK_SECONDS:
                    self._chunk_index += 1
                    self._open_new_chunk()
        if self._current_writer:
            self._current_writer.close()
            self._current_writer = None

    def pause(self):
        if self.is_recording and not self.is_paused:
            self.is_paused = True
            self._pause_event.clear()
            if self._current_writer:
                self._current_writer.flush()

    def resume(self):
        if self.is_recording and self.is_paused:
            self.is_paused = False
            self._pause_event.set()

    def stop(self):
        self.is_recording = False
        self.is_paused = False
        self._pause_event.set()
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._current_writer:
            try:
                self._current_writer.close()
            except:
                pass
            self._current_writer = None

    @property
    def elapsed(self):
        return self._elapsed_seconds

_FFMPEG_EXE = None


def _find_ffmpeg():
    """ffmpeg for the waveform previews: next to the app, on PATH, or the copy shipped
    with the imageio-ffmpeg package. Returns None if nothing is found."""
    global _FFMPEG_EXE
    if _FFMPEG_EXE is None:
        found = ""
        for name in ("ffmpeg.exe", "ffmpeg"):
            p = os.path.join(_app_dir(), name)
            if os.path.isfile(p):
                found = p
                break
        if not found:
            found = shutil.which("ffmpeg") or ""
        if not found:
            try:
                import imageio_ffmpeg
                found = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                found = ""
        _FFMPEG_EXE = found
    return _FFMPEG_EXE or None


def compute_waveform_peaks(path, num_points=300):
    """Decodes any audio/video file via ffmpeg into raw mono PCM and
    reduces it to num_points peak values (0.0-1.0) for drawing."""
    try:
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            return []
        cmd = [ffmpeg, "-i", path, "-ac", "1", "-ar", "8000",
               "-f", "s16le", "-v", "quiet", "-"]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              creationflags=creationflags)
        raw = proc.stdout
        if not raw:
            return []
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if len(data) == 0:
            return []
        chunk_size = max(1, len(data) // num_points)
        peaks = []
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            if len(chunk) == 0:
                continue
            peaks.append(float(np.max(np.abs(chunk))))
        # Normalize so the loudest point fills ~95% of the display height.
        # Purely visual — does not touch the actual audio in any way.
        max_peak = max(peaks) if peaks else 0
        if max_peak > 0.001:
            peaks = [min(1.0, (p / max_peak) * 0.95) for p in peaks]
        return peaks
    except Exception as e:
        logging.warning(f"Waveform decode failed for {path}: {e}")
        return []
class WaveformBridge(QObject):
    peaks_ready = pyqtSignal(str, list)


class ScriptBridge(QObject):
    finished = pyqtSignal(str, bool, int)      # message, ok, run id (helper thread -> window)


class QuickFireEngineBridge(QObject):
    changed = pyqtSignal(str)          # engine thread -> window: "opened", "progress", "failed", ...


class WaveformWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.peaks = []
        self.playhead = -1.0  # -1 = hidden
        self.empty_message = "No waveform"
        self.setFixedHeight(50)
        self.setStyleSheet("background-color: #0a0a0a; border: 1px solid #333;")

    def set_peaks(self, peaks):
        self.peaks = peaks
        self.update()

    def set_playhead(self, frac):
        self.playhead = frac
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0a0a0a"))
        w, h = self.width(), self.height()
        if self.peaks:
            n = len(self.peaks)
            bar_w = w / n
            mid = h / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#4fc3f7"))
            for i, p in enumerate(self.peaks):
                bar_h = max(1.0, p * (h - 4))
                x = i * bar_w
                rect = QRectF(x, mid - bar_h / 2, max(bar_w - 0.3, 0.3), bar_h)
                painter.drawRect(rect)
        else:
            painter.setPen(QColor("#555"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_message)
        if self.playhead >= 0:
            pen = QPen(QColor("#ff5252"))
            pen.setWidth(2)
            painter.setPen(pen)
            x = self.playhead * w
            painter.drawLine(QPointF(x, 0), QPointF(x, h))
class QuickAddIndexBridge(QObject):
    index_ready = pyqtSignal(list)


class QuickAddDialog(QDialog):
    """
    Floating "Quick Add" search — Ctrl+Tab from anywhere opens this.
    Indexes every visuals/ folder (and all its subfolders) across every
    session/scene in the project, live-filters by filename, and adds
    the chosen file into the CURRENT scene's visuals list as a linked
    asset — same mechanism as the existing 🔗 Link button. Does not
    touch the canvas.
    """
    def __init__(self, parent_app):
        super().__init__(parent_app)
        self.main = parent_app
        self.setWindowTitle("Quick Add Visual")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(DARK_STYLE)
        self.setFixedWidth(520)
        self.setFixedHeight(420)

        self._full_index = []
        self._bridge = QuickAddIndexBridge()
        self._bridge.index_ready.connect(self._on_index_ready)

        layout = QVBoxLayout(self)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Type to search visuals across the whole project...")
        self.txt_search.textChanged.connect(self._refilter)
        self.txt_search.installEventFilter(self)
        layout.addWidget(self.txt_search)

        self.lbl_status = QLabel("Indexing...")
        self.lbl_status.setStyleSheet("color: #888; font-size: 10px; font-weight: normal;")
        layout.addWidget(self.lbl_status)

        self.list_results = QListWidget()
        self.list_results.installEventFilter(self)
        self.list_results.itemActivated.connect(lambda it: self._add_and_close(it))
        layout.addWidget(self.list_results, 1)

        hint = QLabel("↑↓ navigate · Enter add · Esc cancel")
        hint.setStyleSheet("color: #666; font-size: 10px; font-weight: normal;")
        layout.addWidget(hint)

    def open_and_focus(self):
        self.txt_search.blockSignals(True)
        self.txt_search.clear()
        self.txt_search.blockSignals(False)
        self.list_results.clear()
        self.lbl_status.setText("Indexing...")
        if self.main:
            geo = self.main.geometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 3)
        self.show()
        self.raise_()
        self.activateWindow()
        self.txt_search.setFocus()
        self._start_index()

    def _start_index(self):
        root = self.main.root_path

        def worker():
            exts = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp',
                    '.mp4', '.mkv', '.avi', '.mov', '.wmv'}
            results = []
            try:
                if root and os.path.isdir(root):
                    for session in sorted(os.listdir(root)):
                        session_path = os.path.join(root, session)
                        if not os.path.isdir(session_path):
                            continue
                        for scene in sorted(os.listdir(session_path)):
                            visuals_path = os.path.join(session_path, scene, "visuals")
                            if not os.path.isdir(visuals_path):
                                continue
                            for dirpath, _, filenames in os.walk(visuals_path):
                                for f in filenames:
                                    if os.path.splitext(f)[1].lower() in exts:
                                        results.append({
                                            "name": f,
                                            "path": os.path.join(dirpath, f),
                                            "context": f"{session} / {scene}"
                                        })
            except Exception as e:
                logging.warning(f"Quick Add index failed: {e}")
            results.sort(key=lambda r: r["name"].lower())
            self._bridge.index_ready.emit(results)

        threading.Thread(target=worker, daemon=True).start()

    def _on_index_ready(self, results):
        self._full_index = results
        self.lbl_status.setText(f"{len(results)} visual(s) indexed")
        self._refilter()

    def _refilter(self):
        query = self.txt_search.text().strip().lower()
        self.list_results.clear()
        if not query:
            return
        starts, contains = [], []
        for r in self._full_index:
            n = r["name"].lower()
            if n.startswith(query):
                starts.append(r)
            elif query in n:
                contains.append(r)
        matches = (starts + contains)[:80]
        for r in matches:
            it = QListWidgetItem(f"{r['name']}   —   {r['context']}")
            it.setData(Qt.ItemDataRole.UserRole, r)
            self.list_results.addItem(it)
        if matches:
            self.list_results.setCurrentRow(0)

    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress:
            key = event.key()
            if obj is self.txt_search:
                if key == Qt.Key.Key_Down:
                    if self.list_results.count() > 0:
                        row = min(self.list_results.currentRow() + 1, self.list_results.count() - 1)
                        self.list_results.setCurrentRow(max(row, 0))
                    return True
                if key == Qt.Key.Key_Up:
                    if self.list_results.count() > 0:
                        row = max(self.list_results.currentRow() - 1, 0)
                        self.list_results.setCurrentRow(row)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    it = self.list_results.currentItem()
                    if it:
                        self._add_and_close(it)
                    return True
                if key == Qt.Key.Key_Escape:
                    self.close()
                    return True
            elif obj is self.list_results:
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    it = self.list_results.currentItem()
                    if it:
                        self._add_and_close(it)
                    return True
                if key == Qt.Key.Key_Escape:
                    self.close()
                    return True
        return super().eventFilter(obj, event)

    def _add_and_close(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self.main.quick_add_insert_asset(data["name"], data["path"])
        self.close()
class MusicManagerDialog(QDialog):
    """
    Big, resizable reorder + blacklist/whitelist view for the music list.
    Mirrors self.main.music_list's order and stays synced both ways.
    Reordering here writes back via take/insertItem — never load_assets().
    The search box only HIDES rows (never removes them), and drag-reorder
    is disabled while a search is active so order can't be corrupted.
    """
    def __init__(self, parent_app):
        super().__init__(parent_app)
        self.main = parent_app
        self.setWindowTitle("Music Order & Filters")
        self.setStyleSheet(DARK_STYLE)
        self.resize(520, 700)
        self.setWindowFlags(Qt.WindowType.Window)
        self._sync_guard = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drag to reorder · Ctrl/Shift multi-select · Double-click to play"))

        search_row = QHBoxLayout()
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Search keywords in filenames (e.g. 'battle drums')...")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._apply_search_filter)
        btn_select_shown = QPushButton("Select All Shown")
        btn_select_shown.clicked.connect(self._select_all_visible)
        search_row.addWidget(self.txt_search, 1)
        search_row.addWidget(btn_select_shown)
        layout.addLayout(search_row)

        self.lbl_search_status = QLabel("")
        self.lbl_search_status.setStyleSheet("color: #888; font-size: 10px; font-weight: normal;")
        layout.addWidget(self.lbl_search_status)

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, 1)

        # Ctrl+A inside the list selects only the rows currently shown
        sc = QShortcut(QKeySequence("Ctrl+A"), self.list_widget)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._select_all_visible)

        legend = QLabel("🔴 Blacklisted (never auto-played)   🟢 Whitelisted (only these auto-play)")
        legend.setStyleSheet("color: #aaa; font-size: 10px; font-weight: normal;")
        layout.addWidget(legend)

        btn_row = QHBoxLayout()
        btn_blacklist = QPushButton("🚫 Add to Blacklist")
        btn_blacklist.setStyleSheet("background-color: #b71c1c; color: white; font-weight: bold;")
        btn_blacklist.setToolTip("Blacklist the selected tracks. Press again on already blacklisted tracks to undo.")
        btn_blacklist.clicked.connect(self._add_selected_to_blacklist)
        btn_whitelist = QPushButton("✅ Add to Whitelist")
        btn_whitelist.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        btn_whitelist.setToolTip("Whitelist the selected tracks. Press again on already whitelisted tracks to undo.")
        btn_whitelist.clicked.connect(self._add_selected_to_whitelist)
        btn_clear = QPushButton("Clear Filter for Selected")
        btn_clear.clicked.connect(self._clear_selected_filters)
        btn_row.addWidget(btn_blacklist)
        btn_row.addWidget(btn_whitelist)
        btn_row.addWidget(btn_clear)
        layout.addLayout(btn_row)

        btn_save_playlist = QPushButton("💾 Save Current Order as Playlist...")
        btn_save_playlist.clicked.connect(self.main.save_music_list_as_playlist)
        layout.addWidget(btn_save_playlist)

        self.list_widget.model().rowsMoved.connect(self._on_order_changed)

    # ---------- sync with main ----------
    def open_and_sync(self):
        self.refresh_from_main()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh_from_main(self):
        self._sync_guard = True
        names = [self.main.music_list.item(i).text() for i in range(self.main.music_list.count())]
        self.list_widget.clear()
        for name in names:
            self.list_widget.addItem(QListWidgetItem(name))
        self.refresh_colors()
        self._apply_search_filter()
        self._sync_guard = False

    def set_order_from_main(self, names):
        if self._sync_guard:
            return
        self._sync_guard = True
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for name in names:
            self.list_widget.addItem(QListWidgetItem(name))
        self.list_widget.blockSignals(False)
        self.refresh_colors()
        self._apply_search_filter()
        self._sync_guard = False

    def refresh_colors(self):
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            name = it.text()
            if name in self.main.music_blacklist:
                it.setForeground(QColor("#e57373"))
            elif self.main.music_whitelist and name in self.main.music_whitelist:
                it.setForeground(QColor("#81c784"))
            else:
                it.setForeground(QColor("#e0e0e0"))

    def _on_order_changed(self, *args):
        if self._sync_guard:
            return
        names = [self.list_widget.item(i).text() for i in range(self.list_widget.count())]
        self.main.apply_music_order_from_dialog(names)

    # ---------- double-click to play ----------
    def _on_item_double_clicked(self, item):
        self.main.play_music_track_by_name(item.text())

    # ---------- search ----------
    @staticmethod
    def _normalize(text):
        # "Boss_Fight-01.mp3" -> "boss fight 01 mp3"
        t = text.lower()
        for ch in ("_", "-", "."):
            t = t.replace(ch, " ")
        return " ".join(t.split())

    def _apply_search_filter(self, *args):
        tokens = self._normalize(self.txt_search.text()).split()
        total = self.list_widget.count()
        shown = 0
        for i in range(total):
            it = self.list_widget.item(i)
            haystack = self._normalize(it.text())
            match = all(tok in haystack for tok in tokens)
            it.setHidden(not match)
            if not match and it.isSelected():
                it.setSelected(False)
            if match:
                shown += 1
        if tokens:
            # Reordering while rows are hidden would be ambiguous — lock it
            self.list_widget.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
            self.lbl_search_status.setText(
                f"Showing {shown} of {total} tracks — reordering is locked while searching")
        else:
            self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
            self.lbl_search_status.setText(f"{total} tracks")

    def _select_all_visible(self):
        self.list_widget.clearSelection()
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if not it.isHidden():
                it.setSelected(True)

    # ---------- filters ----------
    def _selected_names(self):
        # Hidden rows are never included, however they got selected
        return [it.text() for it in self.list_widget.selectedItems() if not it.isHidden()]

    def _add_selected_to_blacklist(self):
        names = self._selected_names()
        if names:
            self.main.toggle_music_blacklist(names)
            self.refresh_colors()

    def _add_selected_to_whitelist(self):
        names = self._selected_names()
        if names:
            self.main.toggle_music_whitelist(names)
            self.refresh_colors()

    def _clear_selected_filters(self):
        names = self._selected_names()
        if names:
            self.main.remove_music_from_filters(names)
            self.refresh_colors()

class PlaylistManagerDialog(QDialog):
    """
    Genre / playlist version of the music manager.
    Top:    genres (left) + tracks of the chosen genre, or of ALL genres (center)
            -> double-click a track to preview it, select + "Add" to put it in the playlist
    Bottom: the working playlist - reorder, remove, black/whitelist, save, load.
    The playlist lives in self.main.music_playlist and is persisted per scene.
    This dialog never calls load_assets() itself; it only goes through the
    main-window methods that already handle ordering safely.
    """

    def __init__(self, parent_app):
        super().__init__(parent_app)
        self.main = parent_app
        self.setWindowTitle("Music — Genres & Playlist")
        self.setStyleSheet(DARK_STYLE)
        self.resize(1050, 780)
        self.setWindowFlags(Qt.WindowType.Window)
        self._sync_guard = False
        self._all_index = None

        root_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Vertical)
        root_layout.addWidget(splitter)

        # ===== TOP: search + genres + tracks =====
        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)

        search_row = QHBoxLayout()
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Search keywords in filenames (e.g. 'tavern jazz')...")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._refresh_center)
        self.chk_all_genres = QCheckBox("Search all genres")
        self.chk_all_genres.toggled.connect(self._on_all_genres_toggled)
        self.chk_subfolders = QCheckBox("Include subfolders")
        self.chk_subfolders.setToolTip("Also list the tracks of all folders below the selected genre")
        self.chk_subfolders.toggled.connect(self._refresh_center)
        search_row.addWidget(self.txt_search, 1)
        search_row.addWidget(self.chk_subfolders)
        search_row.addWidget(self.chk_all_genres)
        top_lay.addLayout(search_row)

        h_split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(QLabel("🎼 Genres"))
        self.list_genres = QListWidget()
        self.list_genres.currentItemChanged.connect(self._refresh_center)
        left_lay.addWidget(self.list_genres, 1)

        center = QWidget()
        center_lay = QVBoxLayout(center)
        center_lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_center = QLabel("Tracks")
        center_lay.addWidget(self.lbl_center)
        self.list_center = QListWidget()
        self.list_center.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_center.itemDoubleClicked.connect(self._on_center_double_clicked)
        center_lay.addWidget(self.list_center, 1)
        center_btns = QHBoxLayout()
        btn_sel_all = QPushButton("Select All Shown")
        btn_sel_all.clicked.connect(lambda: self.list_center.selectAll())
        btn_add = QPushButton("➕ Add Selected to Playlist")
        btn_add.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        btn_add.clicked.connect(self._add_selected)
        center_btns.addWidget(btn_sel_all)
        center_btns.addWidget(btn_add, 1)
        center_lay.addLayout(center_btns)
        # Blacklist / whitelist for the TOP list (works without a playlist, e.g. plain genre mode)
        center_filter_btns = QHBoxLayout()
        btn_c_black = QPushButton("🚫 Blacklist")
        btn_c_black.setStyleSheet("background-color: #b71c1c; color: white; font-weight: bold;")
        btn_c_black.setToolTip("Never auto-play the selected tracks (no playlist needed). Press again on blacklisted tracks to undo.")
        btn_c_black.clicked.connect(self._blacklist_center)
        btn_c_white = QPushButton("✅ Whitelist")
        btn_c_white.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        btn_c_white.setToolTip("Only whitelisted tracks auto-play (once at least one is whitelisted). Press again on whitelisted tracks to undo.")
        btn_c_white.clicked.connect(self._whitelist_center)
        btn_c_clear = QPushButton("Clear Filter")
        btn_c_clear.clicked.connect(self._unfilter_center)
        for b in (btn_c_black, btn_c_white, btn_c_clear):
            center_filter_btns.addWidget(b)
        center_lay.addLayout(center_filter_btns)

        h_split.addWidget(left)
        h_split.addWidget(center)
        h_split.setStretchFactor(0, 1)
        h_split.setStretchFactor(1, 3)
        top_lay.addWidget(h_split, 1)
        splitter.addWidget(top)

        # ===== BOTTOM: playlist =====
        bottom = QWidget()
        bot_lay = QVBoxLayout(bottom)
        bot_lay.setContentsMargins(0, 4, 0, 0)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("🎧 Playlist"))
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("Playlist name...")
        btn_save = QPushButton("💾 Save")
        btn_save.clicked.connect(self._on_save)
        name_row.addWidget(self.txt_name, 1)
        name_row.addWidget(btn_save)
        bot_lay.addLayout(name_row)

        load_row = QHBoxLayout()
        self.combo_saved = QComboBox()
        self.combo_saved.setMinimumWidth(300)
        self.combo_saved.view().setMinimumWidth(500)
        btn_load = QPushButton("📂 Load into editor")
        btn_load.setToolTip("Replace or merge a saved playlist into the playlist below")
        btn_load.clicked.connect(self._on_load)
        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(30)
        btn_refresh.setToolTip("Rescan all scenes for saved playlists")
        btn_refresh.clicked.connect(self.refresh_saved_combo)
        load_row.addWidget(QLabel("Saved playlists:"))
        load_row.addWidget(self.combo_saved, 1)
        load_row.addWidget(btn_load)
        load_row.addWidget(btn_refresh)
        bot_lay.addLayout(load_row)

        self.chk_active = QCheckBox("▶ Play from this playlist (same as “Use Playlist” in the music panel)")
        self.chk_active.toggled.connect(self._on_active_toggled)
        bot_lay.addWidget(self.chk_active)

        self.list_playlist = QListWidget()
        self.list_playlist.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_playlist.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_playlist.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_playlist.itemDoubleClicked.connect(self._on_playlist_double_clicked)
        self.list_playlist.model().rowsMoved.connect(self._on_playlist_reordered)
        bot_lay.addWidget(self.list_playlist, 1)
        del_sc = QShortcut(QKeySequence("Delete"), self.list_playlist)
        del_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        del_sc.activated.connect(self._remove_selected)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #888; font-size: 10px; font-weight: normal;")
        bot_lay.addWidget(self.lbl_status)

        legend = QLabel("🔴 Blacklisted (never auto-played)   🟢 Whitelisted (only these auto-play)   "
                        "⚪ grey = file missing")
        legend.setStyleSheet("color: #aaa; font-size: 10px; font-weight: normal;")
        bot_lay.addWidget(legend)

        btn_row = QHBoxLayout()
        btn_remove = QPushButton("➖ Remove Selected")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("Clear Playlist")
        btn_clear.clicked.connect(self._clear_playlist)
        btn_black = QPushButton("🚫 Blacklist")
        btn_black.setStyleSheet("background-color: #b71c1c; color: white; font-weight: bold;")
        btn_black.setToolTip("Blacklist the selected tracks. Press again on already blacklisted tracks to undo.")
        btn_black.clicked.connect(self._blacklist_selected)
        btn_white = QPushButton("✅ Whitelist")
        btn_white.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        btn_white.setToolTip("Whitelist the selected tracks. Press again on already whitelisted tracks to undo.")
        btn_white.clicked.connect(self._whitelist_selected)
        btn_unfilter = QPushButton("Clear Filter")
        btn_unfilter.clicked.connect(self._unfilter_selected)
        for b in (btn_remove, btn_clear, btn_black, btn_white, btn_unfilter):
            btn_row.addWidget(b)
        bot_lay.addLayout(btn_row)

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # Keep the "play from playlist" checkbox honest if "Use Playlist" is
        # toggled in the main window while this dialog is open.
        self.main.chk_use_playlist.toggled.connect(self._on_main_playlist_mode_changed)

    # ---------- open / refresh ----------
    def open_and_sync(self):
        self.chk_all_genres.setChecked(False)     # so the genre we jump to is actually shown
        self.refresh_all()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh_all(self):
        self._all_index = None
        self.refresh_genres(jump=True)
        self.refresh_playlist_from_main()
        self.txt_name.setText(self.main.music_playlist_name)
        self.refresh_saved_combo()
        self._refresh_center()

    def _on_main_playlist_mode_changed(self, checked):
        if self.main.is_loading or not self.isVisible():
            return
        self.refresh_playlist_from_main()

    # ---------- genres / center ----------
    @staticmethod
    def _normalize(text):
        t = text.lower()
        for ch in ("_", "-", "."):
            t = t.replace(ch, " ")
        return " ".join(t.split())

    def _improv_root(self):
        return self.main.txt_improv_music_path.text().strip()

    def _list_genres(self):
        # relative '/'-paths of every folder below the music root, any depth
        return list_subfolders_recursive(self._improv_root())

    def _genre_path(self, genre):
        return os.path.join(self._improv_root(), *genre.split("/"))

    def _scan_genre(self, genre, recursive=False):
        out = []
        base = self._genre_path(genre)
        try:
            if recursive:
                folders = [genre] + [f"{genre}/{s}" for s in list_subfolders_recursive(base)]
            else:
                folders = [genre]
            for g in folders:
                p = self._genre_path(g)
                if not os.path.isdir(p):
                    continue
                for f in sorted(os.listdir(p), key=str.lower):
                    full = os.path.join(p, f)
                    if os.path.isfile(full) and os.path.splitext(f)[1].lower() in AUDIO_EXTS:
                        out.append({"name": f, "path": full, "genre": g})
        except Exception as e:
            logging.warning(f"Playlist dialog: scanning {base} failed: {e}")
        return out

    def _get_all_index(self):
        if self._all_index is None:
            idx = []
            for g in self._list_genres():
                idx.extend(self._scan_genre(g))
            self._all_index = idx
        return self._all_index

    def _current_genre(self):
        it = self.list_genres.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def refresh_genres(self, jump=False):
        # jump=True: select the genre that is chosen in the main window
        main_genre = self.main.combo_improv_folders.currentText()
        wanted = self._current_genre()
        if (jump and not self.main._playlist_mode_active() and main_genre) or wanted is None:
            wanted = main_genre or wanted
        self.list_genres.blockSignals(True)
        self.list_genres.clear()
        genres = self._list_genres()
        for g in genres:
            depth = g.count("/")
            it = QListWidgetItem(("      " * depth) + (("↳ ") if depth else "") + g.split("/")[-1])
            it.setData(Qt.ItemDataRole.UserRole, g)
            it.setToolTip(g)
            self.list_genres.addItem(it)
        if genres:
            row = genres.index(wanted) if wanted in genres else 0
            self.list_genres.setCurrentRow(row)
            self.list_genres.scrollToItem(self.list_genres.item(row),
                                          QListWidget.ScrollHint.PositionAtCenter)
        self.list_genres.blockSignals(False)

    def _on_all_genres_toggled(self, checked):
        self.list_genres.setEnabled(not checked)
        self._refresh_center()

    def _playlist_path_keys(self):
        return {self.main._path_key(e["path"]) for e in self.main.music_playlist}

    def _show_genre_in_labels(self):
        return self.chk_all_genres.isChecked() or self.chk_subfolders.isChecked()

    def _center_label(self, e, show_genre, in_playlist):
        text = f"{e['name']}   —   {e['genre']}" if show_genre else e["name"]
        return ("✓ " + text) if in_playlist else text

    def _refresh_center(self, *args):
        tokens = self._normalize(self.txt_search.text()).split()
        all_mode = self.chk_all_genres.isChecked()
        rel = self._current_genre()
        if all_mode:
            entries = self._get_all_index()
            self.lbl_center.setText("Tracks — all genres   (double-click to preview)")
        else:
            sub = self.chk_subfolders.isChecked()
            entries = self._scan_genre(rel, sub) if rel else []
            self.lbl_center.setText(
                f"Tracks — {rel}{' (+ subfolders)' if sub else ''}   (double-click to preview)"
                if rel else "Tracks")
        show_genre = self._show_genre_in_labels()
        have = self._playlist_path_keys()
        self.list_center.clear()
        shown = 0
        for e in entries:
            hay = self._normalize(e["name"] + ((" " + e["genre"]) if show_genre else ""))
            if tokens and not all(t in hay for t in tokens):
                continue
            li = QListWidgetItem(self._center_label(e, show_genre, self.main._path_key(e["path"]) in have))
            li.setData(Qt.ItemDataRole.UserRole, e)
            self._color_center_item(li, e)
            self.list_center.addItem(li)
            shown += 1
            if shown >= 3000:
                break
        capped = " (first 3000 shown — refine your search)" if shown >= 3000 else ""
        self.lbl_status.setText(f"{shown} track(s) shown{capped}   ·   ✓ = already in playlist")

    def _on_center_double_clicked(self, item):
        e = item.data(Qt.ItemDataRole.UserRole)
        if not e:
            return
        if not os.path.exists(e["path"]):
            self.lbl_status.setText("That file no longer exists on disk.")
            return
        self.main.play_music_file(e["path"], e["name"])
        self.lbl_status.setText(f"Playing: {e['name']}")

    def _color_center_item(self, li, e):
        # same colours as the bottom list and the main music list; filters are per file name
        name = (e or {}).get("name", "")
        if name in self.main.music_blacklist:
            li.setForeground(QColor("#e57373"))
        elif self.main.music_whitelist and name in self.main.music_whitelist:
            li.setForeground(QColor("#81c784"))
        else:
            li.setForeground(QColor("#e0e0e0"))

    def _selected_center_names(self):
        out = []
        for li in self.list_center.selectedItems():
            e = li.data(Qt.ItemDataRole.UserRole)
            if e and e["name"] not in out:
                out.append(e["name"])
        return out

    def _blacklist_center(self):
        names = self._selected_center_names()
        if not names:
            self.lbl_status.setText("Select one or more tracks in the top list first.")
            return
        was = all(n in self.main.music_blacklist for n in names)
        self.main.toggle_music_blacklist(names)      # recolours all lists
        self.lbl_status.setText(f"Blacklist removed from {len(names)} track(s)" if was
                                else f"Blacklisted {len(names)} track(s)")

    def _whitelist_center(self):
        names = self._selected_center_names()
        if not names:
            self.lbl_status.setText("Select one or more tracks in the top list first.")
            return
        was = all(n in self.main.music_whitelist for n in names)
        self.main.toggle_music_whitelist(names)
        self.lbl_status.setText(f"Whitelist removed from {len(names)} track(s)" if was
                                else f"Whitelisted {len(names)} track(s)")

    def _unfilter_center(self):
        names = self._selected_center_names()
        if not names:
            self.lbl_status.setText("Select one or more tracks in the top list first.")
            return
        self.main.remove_music_from_filters(names)
        self.lbl_status.setText(f"Cleared the filter on {len(names)} track(s)")

    def _mark_in_playlist(self):
        # Updates the ✓ marks in place so the user's selection/scroll survive.
        have = self._playlist_path_keys()
        all_mode = self._show_genre_in_labels()
        for i in range(self.list_center.count()):
            li = self.list_center.item(i)
            e = li.data(Qt.ItemDataRole.UserRole)
            if e:
                li.setText(self._center_label(e, all_mode, self.main._path_key(e["path"]) in have))

    def _add_selected(self):
        entries = [li.data(Qt.ItemDataRole.UserRole) for li in self.list_center.selectedItems()]
        entries = [e for e in entries if e]
        if not entries:
            self.lbl_status.setText("Select one or more tracks in the middle list first.")
            return
        added, dup = self.main.playlist_add_tracks(entries)
        self.refresh_playlist_from_main()
        self._mark_in_playlist()
        msg = f"Added {added} track(s)"
        if dup:
            msg += f", skipped {dup} already in the playlist"
        if added and not self.main._playlist_mode_active():
            msg += "  ·  tick “Use Playlist” in the music panel to play from it"
        self.lbl_status.setText(msg)

    # ---------- playlist (bottom) ----------
    def refresh_playlist_from_main(self):
        self._sync_guard = True
        try:
            self.list_playlist.blockSignals(True)
            self.list_playlist.clear()
            for e in self.main.music_playlist:
                it = QListWidgetItem(e["name"])
                it.setData(Qt.ItemDataRole.UserRole, e)
                it.setToolTip(e["path"])
                self.list_playlist.addItem(it)
            self.list_playlist.blockSignals(False)
            self.refresh_colors()
            self.chk_active.blockSignals(True)
            self.chk_active.setChecked(self.main._playlist_mode_active())
            self.chk_active.blockSignals(False)
        finally:
            self._sync_guard = False

    def refresh_colors(self):
        for i in range(self.list_playlist.count()):
            it = self.list_playlist.item(i)
            e = it.data(Qt.ItemDataRole.UserRole) or {}
            name = it.text()
            if name in self.main.music_blacklist:
                it.setForeground(QColor("#e57373"))
            elif self.main.music_whitelist and name in self.main.music_whitelist:
                it.setForeground(QColor("#81c784"))
            elif e.get("path") and not os.path.exists(e["path"]):
                it.setForeground(QColor("#777777"))
            else:
                it.setForeground(QColor("#e0e0e0"))
        for i in range(self.list_center.count()):
            li = self.list_center.item(i)
            self._color_center_item(li, li.data(Qt.ItemDataRole.UserRole))

    def _on_playlist_reordered(self, *args):
        if self._sync_guard:
            return
        names = [self.list_playlist.item(i).text() for i in range(self.list_playlist.count())]
        self.main.set_playlist_order(names)

    def _on_playlist_double_clicked(self, item):
        if not self.main._playlist_mode_active():
            e = item.data(Qt.ItemDataRole.UserRole) or {}
            if e.get("path") and os.path.exists(e["path"]):
                # Not the active source: just preview the file, like the middle list does
                self.main.play_music_file(e["path"], e["name"])
                self.lbl_status.setText(f"Playing: {e['name']}  ·  (tick “Use Playlist” to play from the whole list)")
            return
        self.main.play_music_track_by_name(item.text())

    def _selected_playlist_names(self):
        return [it.text() for it in self.list_playlist.selectedItems()]

    def _remove_selected(self):
        names = self._selected_playlist_names()
        if not names:
            return
        self.main.playlist_remove_tracks(names)
        self.refresh_playlist_from_main()
        self._mark_in_playlist()
        self.lbl_status.setText(f"Removed {len(names)} track(s)")

    def _clear_playlist(self):
        if not self.main.music_playlist:
            return
        if QMessageBox.question(self, "Clear Playlist",
                                "Remove ALL tracks from the working playlist?\n"
                                "(Saved playlist files are not affected.)") != QMessageBox.StandardButton.Yes:
            return
        self.main.playlist_clear()
        self.refresh_playlist_from_main()
        self._mark_in_playlist()

    def _on_active_toggled(self, checked):
        if self._sync_guard:
            return
        if checked and not self.main.music_playlist:
            self.chk_active.blockSignals(True)
            self.chk_active.setChecked(False)
            self.chk_active.blockSignals(False)
            self.lbl_status.setText("Add some tracks to the playlist first.")
            return
        ok = self.main.set_playlist_active(checked)
        self.refresh_playlist_from_main()
        if not ok:
            self.lbl_status.setText("Could not switch the music source.")
        elif checked:
            self.lbl_status.setText("Main music list now plays from this playlist.")
        else:
            self.lbl_status.setText("Main music list is back on the selected genre.")

    # ---------- filters ----------
    def _blacklist_selected(self):
        names = self._selected_playlist_names()
        if names:
            self.main.toggle_music_blacklist(names)
            self.refresh_colors()

    def _whitelist_selected(self):
        names = self._selected_playlist_names()
        if names:
            self.main.toggle_music_whitelist(names)
            self.refresh_colors()

    def _unfilter_selected(self):
        names = self._selected_playlist_names()
        if names:
            self.main.remove_music_from_filters(names)
            self.refresh_colors()

    # ---------- save / load ----------
    def refresh_saved_combo(self):
        self.combo_saved.clear()
        found = self.main.list_all_playlists()
        for p in found:
            self.combo_saved.addItem(p["label"], p)
        if not found:
            self.combo_saved.addItem("(no saved playlists yet)", None)

    def _on_save(self):
        name = self.txt_name.text().strip()
        safe = self.main.save_named_playlist(name, self)
        if safe:
            self.txt_name.setText(safe)
            self.refresh_saved_combo()
            self.lbl_status.setText(f"Saved playlist “{safe}” in this scene.")

    def _on_load(self):
        data = self.combo_saved.currentData()
        if not data:
            self.lbl_status.setText("No saved playlist selected.")
            return
        entries, _ = self.main.read_playlist_file(data["path"])
        valid = [e for e in entries if os.path.exists(e["path"])]
        missing = len(entries) - len(valid)
        if not valid:
            QMessageBox.warning(self, "Load Playlist",
                                "None of the tracks in this playlist exist on disk any more.")
            return
        replace = True
        if self.main.music_playlist:
            box = QMessageBox(self)
            box.setWindowTitle("Load Playlist")
            box.setText(f"Load “{data['label']}” into the editor?\n\n"
                        f"Your current playlist has {len(self.main.music_playlist)} track(s).")
            b_replace = box.addButton("Replace current playlist", QMessageBox.ButtonRole.DestructiveRole)
            b_merge = box.addButton("Merge into current playlist", QMessageBox.ButtonRole.AcceptRole)
            b_cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is None or clicked is b_cancel:
                return
            replace = clicked is b_replace
        added = self.main.playlist_load_entries(valid, replace, data["name"] if replace else None,
                                                data["path"] if replace else None)
        self.refresh_playlist_from_main()
        self.txt_name.setText(self.main.music_playlist_name)
        self._mark_in_playlist()
        msg = f"{'Loaded' if replace else 'Merged'} {added} track(s) from “{data['name']}”"
        if missing:
            msg += f"  ·  {missing} missing file(s) skipped"
        if not self.main._playlist_mode_active():
            msg += "  ·  tick “Use Playlist” in the music panel to play from it"
        self.lbl_status.setText(msg)

class LinkToScenesDialog(QDialog):
    """Tick any number of scenes (grouped by session) to link the chosen content into."""

    def __init__(self, parent, sessions, summary):
        # sessions: [(session_name, [(scene_name, scene_path), ...]), ...]
        super().__init__(parent)
        self.setWindowTitle("Link to scenes")
        self.setStyleSheet(DARK_STYLE)
        self.resize(420, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(summary))
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("🔍 Filter scenes...")
        self.txt_filter.setClearButtonEnabled(True)
        self.txt_filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self.txt_filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        lay.addWidget(self.tree, 1)
        self._scene_items = []
        for session, scenes in sessions:
            top = QTreeWidgetItem([session])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            for name, path in scenes:
                ch = QTreeWidgetItem([name])
                ch.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                ch.setCheckState(0, Qt.CheckState.Unchecked)
                ch.setData(0, Qt.ItemDataRole.UserRole, path)
                top.addChild(ch)
                self._scene_items.append(ch)
            top.setExpanded(True)
        row = QHBoxLayout()
        b_all = QPushButton("Tick all shown")
        b_all.clicked.connect(lambda: self._set_shown(Qt.CheckState.Checked))
        b_none = QPushButton("Untick all")
        b_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        row.addWidget(b_all)
        row.addWidget(b_none)
        row.addStretch()
        lay.addLayout(row)
        self.lbl_count = QLabel("")
        lay.addWidget(self.lbl_count)
        btns = QHBoxLayout()
        self.btn_ok = QPushButton("🔗 Link")
        self.btn_ok.clicked.connect(self.accept)
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(self.btn_ok)
        btns.addWidget(b_cancel)
        lay.addLayout(btns)
        self.tree.itemChanged.connect(self._update_count)
        self._update_count()

    def _apply_filter(self, text):
        t = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            any_visible = False
            for j in range(top.childCount()):
                ch = top.child(j)
                vis = (not t) or t in ch.text(0).lower() or t in top.text(0).lower()
                ch.setHidden(not vis)
                any_visible = any_visible or vis
            top.setHidden(not any_visible)

    def _set_shown(self, state):
        for ch in self._scene_items:
            if not ch.isHidden():
                ch.setCheckState(0, state)

    def _set_all(self, state):
        for ch in self._scene_items:
            ch.setCheckState(0, state)

    def chosen_scene_paths(self):
        return [ch.data(0, Qt.ItemDataRole.UserRole) for ch in self._scene_items
                if ch.checkState(0) == Qt.CheckState.Checked]

    def _update_count(self, *args):
        n = len(self.chosen_scene_paths())
        self.lbl_count.setText(f"{n} scene(s) ticked")
        self.btn_ok.setEnabled(n > 0)


class DirectorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.is_loading = False
        self.setWindowTitle(f"Astratera Weltentor v{APP_VERSION} - DM Tool");
        self.resize(1600, 950);
        self.setStyleSheet(DARK_STYLE)
        self.config = self.load_config()
        config = self.config
        self.default_blanking_data = config.get("default_blanking", None)
        # Blanking is OFF unless switched on: for every scene ("all scenes", remembered here in the
        # config = machine setting) or for single scenes (scene_settings.json: "blanking_enabled").
        self.blanking_all_scenes = bool(config.get("blanking_all_scenes", False))
        self.blanking_scene_enabled = False
        self.fit_projector = bool(config.get("fit_projector", True))    # on = stage scaled to the projector screen; off = pixel for pixel
        self._loaded_layout = None            # (scene_path, layout file name) last loaded onto the stage
        # open/closed state of the foldable toolbar menus (remembered between runs)
        self.ui_folds = {"annotate": False, "blanking": False}
        _uf = config.get("ui_folds")
        if isinstance(_uf, dict):
            for _k in self.ui_folds:
                self.ui_folds[_k] = bool(_uf.get(_k, False))
        _ico = _resource_path("assets/icon.png") or _resource_path("assets/icon.ico")
        if _ico:
            self.setWindowIcon(QIcon(_ico))
        self.root_path = config.get("root_path", "");
        set_anchor_provider(self._portable_anchors)
        self.current_session = config.get("last_session", "")
        self.last_scene = config.get("last_scene", "");
        self.improv_path = config.get("improv_music_path", "")
        self.improv_visuals_path = config.get("improv_visuals_path",
                                              "")
        self.improv_sfx_path = config.get("improv_sfx_path", "")
        self.scene_path = "";
        # Change back if Audio weird
        self.vlc_music = vlc.Instance("--aout=directsound", "--quiet")
        self.vlc_sfx = vlc.Instance("--aout=directsound", "--quiet")
        self.music_player = self.vlc_music.media_player_new()
        self.sfx_player = self.vlc_sfx.media_player_new()
        self.bridge = VLCBridge();
        self.bridge.track_ended.connect(self.next_music)
        self.music_events = self.music_player.event_manager();
        self.music_events.event_attach(vlc.EventType.MediaPlayerEndReached, lambda e: self.bridge.track_ended.emit())
        self.is_shuffle = False;
        self.is_frozen = False;
        self.shared_scene = QGraphicsScene(0, 0, 5000, 5000);
        self.z_counter = 1
        self.annotation_overlay = None
        self.annotation_active = False

        self.vlc_quickfire = vlc.Instance("--aout=directsound", "--quiet")
        self.quickfire_player = self.vlc_quickfire.media_player_new()
        self.quickfire_bridge = VLCBridge()
        self.quickfire_events = self.quickfire_player.event_manager()
        self.quickfire_events.event_attach(
            vlc.EventType.MediaPlayerEndReached,
            lambda e: self.quickfire_bridge.track_ended.emit()
        )
        self.quickfire_bridge.track_ended.connect(
            lambda: QTimer.singleShot(500, self.quickfire_player.stop)
        )
        self.quickfire_path = config.get("quickfire_path", "")
        self.quickfire_enabled = False  # ← this line must exist
        self._quickfire_playing_path = None
        self._active_video_item = None   # read by the waveform playhead timer before any video is selected
        # optional fast Quick Fire engine (preloaded sounds + always-open output). Off unless switched on in Settings.
        self.fast_quickfire = bool(config.get("fast_quickfire", False))
        # smart lights of THIS computer (bridge address + key live here, never inside scripts)
        _lc = config.get("lights")
        self.lights_config = _lc if isinstance(_lc, dict) else {}
        _tc = config.get("transcribe")
        self.transcribe_config = _tc if isinstance(_tc, dict) else {}
        self._transcribe_dlg = None
        self.scripts_order = []                  # drag order of the scripts list (per scene)
        self._script_cancel = None
        self._script_run_id = 0
        self._script_bridge = ScriptBridge()
        self._script_bridge.finished.connect(self._on_script_finished)
        self.qf_engine = None
        self._qf_bridge = QuickFireEngineBridge()
        self._qf_bridge.changed.connect(self._on_qf_engine_event)
        self._quickfire_was_playing = False

        self.waveform_bridge = WaveformBridge()
        self.waveform_bridge.peaks_ready.connect(self._on_waveform_ready)
        self._waveform_cache = {}

        self.quickfire_playhead_timer = QTimer()
        self.quickfire_playhead_timer.timeout.connect(self._update_quickfire_playhead)
        self.quickfire_playhead_timer.start(20)
        self._music_loaded_path = None
        self._music_waveform_shown_path = None
        self._sfx_loaded_path = None
        self._sfx_waveform_shown_path = None
        self._visuals_waveform_shown_path = None

        self.music_waveform_bridge = WaveformBridge()
        self.music_waveform_bridge.peaks_ready.connect(self._on_music_waveform_ready)
        self.sfx_waveform_bridge = WaveformBridge()
        self.sfx_waveform_bridge.peaks_ready.connect(self._on_sfx_waveform_ready)
        self.visuals_waveform_bridge = WaveformBridge()
        self.visuals_waveform_bridge.peaks_ready.connect(self._on_visuals_waveform_ready)

        self.music_sfx_waveform_timer = QTimer()
        self.music_sfx_waveform_timer.timeout.connect(self._update_music_sfx_waveform_playheads)
        self.music_sfx_waveform_timer.start(40)

        # Break mode state
        self.break_mode = False
        self.break_music_folder = self.config.get("break_music_folder", "")
        self.break_image_folder = self.config.get("break_image_folder", "")
        self._break_slideshow_timer = QTimer()
        self._break_slideshow_timer.timeout.connect(self._break_next_slide)
        self._break_images = []
        self._break_slide_index = 0
        self._pre_break_layout = None
        self._pre_break_music_track = None
        self._pre_break_music_pos = 0

        self.recorder = SessionRecorder()
        self.recording_path = config.get("recording_path", "")
        self._rec_timer = QTimer()
        self._rec_timer.timeout.connect(self._update_rec_display)
        self._markers = []
        self._markers_path = None
        # what music / SFX / Quick Fire played when (only filled while a recording runs)
        self._audio_log = []
        self._audio_log_path = None

        self.track_timer = QTimer();
        self.track_timer.timeout.connect(self.update_track_time);
        self.track_timer.start(1000);
        self.mini_controls = None
        self._quick_add_dialog = None
        self.quick_add_shortcut = QShortcut(QKeySequence("Ctrl+Tab"), self)
        self.quick_add_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.quick_add_shortcut.activated.connect(self.open_quick_add)
        self._music_manager_dialog = None
        self._playlist_dialog = None
        self.stage_window = StageWindow();
        self.stage_window.setStyleSheet("background-color: black;")
        self.stage_view = QGraphicsView(self.shared_scene);
        self.stage_view.setFrameStyle(0);
        self.stage_view.setBackgroundBrush(Qt.GlobalColor.black);
        self.stage_window.setCentralWidget(self.stage_view)
        self.stage_window.on_resize = self._fit_stage_view

        self.linked_assets = {"music": {}, "sfx": {}, "visuals": {}, "layouts": {}}
        self.folder_mode_orders = {"music": [], "sfx": [], "visuals": [], "layouts": []}
        self.music_blacklist = set()
        self.music_whitelist = set()
        # --- music source state (genre orders, playlist, now-playing) ---
        self.music_orders = {}              # per-source order memory: "folder" / "genre:<name>"
        self._music_list_key = None         # what the music list was last BUILT for
        self._music_list_scene = None       # ...and for which scene
        self.music_playlist = []
        self.music_playlist_name = ""
        self.music_playlist_source = ""     # path of the saved playlist file last loaded/saved
        self._now_playing_name = None
        self._now_playing_set_at = datetime.datetime.min
        self.collapsed_sections = {"music": False, "sfx": False, "visuals": False, "layouts": False}
        self.section_collapse_buttons = {}
        self._section_gates = {}
        self._section_lists = {}
        self.init_ui();

        self.update_root();
        self.restore_selection()
        self.shared_scene.selectionChanged.connect(self.sync_opacity_slider)
        self.shared_scene.selectionChanged.connect(self._on_canvas_selection_for_video_waveform)
        QTimer.singleShot(100, self.reset_preview_view)
        QTimer.singleShot(200, self._lock_window_size)

        # --- Session Manager integration (additive, self-contained) ---
        # Polling rather than QFileSystemWatcher deliberately: a watcher can
        # silently drop the file from its watch list when it's replaced
        # rather than edited in place (common for atomic JSON writes), and
        # a fixed poll is simpler to reason about for something that only
        # needs to react within well under a second, not instantly.
        # Pre-seed mtime so any command file already on disk at startup
        # is treated as already-handled — prevents stale commands from
        # auto-firing music/lights when the tool is first opened
        try:
            self._remote_last_mtime = os.path.getmtime(REMOTE_COMMAND_FILE) \
                if os.path.exists(REMOTE_COMMAND_FILE) else 0
        except:
            self._remote_last_mtime = 0
        self.remote_poll_timer = QTimer()
        self.remote_poll_timer.timeout.connect(self.check_remote_command)
        self.remote_poll_timer.start(750)
        # Logged once at startup specifically so a mismatch (the Session
        # Manager writing to one path, this tool watching another — the
        # exact failure mode if CWD-based resolution were used instead)
        # is something that shows up in crash.log rather than needing to
        # be guessed at. logging is configured file-only (see log_path
        # above), not console — this line will not appear in a terminal
        # or an IDE's Run window, only in crash.log next to this script.
        logging.debug(f"Remote command watcher active, watching: {REMOTE_COMMAND_FILE} (frozen={getattr(sys, 'frozen', False)})")

    def check_remote_command(self):
        # Reads REMOTE_COMMAND_FILE if it has changed since the last poll.
        # Every failure mode here is a silent no-op (at worst logged at
        # debug level) rather than a raised exception — this timer runs
        # forever in the background, and nothing about an external tool's
        # behavior should ever be able to interrupt or crash a live session.
        try:
            if not os.path.exists(REMOTE_COMMAND_FILE):
                return
            mtime = os.path.getmtime(REMOTE_COMMAND_FILE)
            if mtime <= self._remote_last_mtime:
                return
            with open(REMOTE_COMMAND_FILE, 'r') as f:
                cmd = json.load(f)
            # Only mark this mtime as handled after a successful read/parse
            # — if the writer is mid-write and this catches a partial file,
            # the mtime will still look "new" on the next poll and it
            # retries, rather than silently losing that command forever.
            self._remote_last_mtime = mtime
        except Exception as e:
            logging.debug(f"Remote command read failed, will retry next poll: {e}")
            return
        self.apply_remote_command(cmd)

    def _remote_find_music_row(self, name):
        if not name:
            return -1
        for i in range(self.music_list.count()):
            if self.music_list.item(i).text() == name:
                return i
        low = str(name).lower()
        for i in range(self.music_list.count()):
            if self.music_list.item(i).text().lower() == low:
                return i
        return -1

    def _remote_play_music(self, name):
        row = self._remote_find_music_row(name)
        if row < 0:
            logging.debug(f"Remote command: music track {name!r} not found in the active music list")
            return
        self.play_music_track_by_name(self.music_list.item(row).text())

    def _remote_scene_settings(self, scene_path):
        try:
            fp = os.path.join(scene_path, SCENE_SETTINGS_FILE)
            if os.path.exists(fp):
                with open(fp, 'r') as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    la = d.get("linked_assets")
                    if isinstance(la, dict):
                        for cat, m in la.items():
                            if isinstance(m, dict):
                                la[cat] = {k: resolve_path(v) for k, v in m.items() if isinstance(v, str)}
                    if isinstance(d.get("quickfire_path"), str):
                        d["quickfire_path"] = resolve_path(d["quickfire_path"])
                    if isinstance(d.get("music_playlist"), list):
                        d["music_playlist"] = [dict(e, path=resolve_path(e.get("path")))
                                               for e in d["music_playlist"]
                                               if isinstance(e, dict) and isinstance(e.get("path"), str)]
                    return d
        except Exception as e:
            logging.debug(f"Remote: could not read settings of {scene_path}: {e}")
        return {}

    @staticmethod
    def _remote_files_in(folder, exts=None):
        pool = {}
        try:
            if folder and os.path.isdir(folder):
                for f in os.listdir(folder):
                    full = os.path.join(folder, f)
                    if os.path.isfile(full) and (exts is None or os.path.splitext(f)[1].lower() in exts):
                        pool[f] = full
        except Exception:
            pass
        return pool

    @staticmethod
    def _remote_pick(pools, name):
        for pool in pools:
            if name in pool and os.path.exists(pool[name]):
                return pool[name]
        low = str(name).lower()
        for pool in pools:
            for k, v in pool.items():
                if k.lower() == low and os.path.exists(v):
                    return v
        return None

    def _remote_resolve_scene_music(self, scene_path, name):
        data = self._remote_scene_settings(scene_path)
        linked = data.get("linked_assets", {})
        linked = linked.get("music", {}) if isinstance(linked, dict) else {}
        linked = {k: v for k, v in linked.items() if isinstance(k, str) and isinstance(v, str)}
        scene_pool = self._remote_files_in(os.path.join(scene_path, "music"))
        playlist = {e["name"]: e["path"] for e in self._sanitize_playlist(data.get("music_playlist", []))}
        cat = data.get("improv_category", "") or ""
        genre_pool = self._remote_files_in(os.path.join(self.txt_improv_music_path.text(), *cat.split("/"))) \
            if cat and cat not in (ROOT_LABEL, PLAYLIST_CATEGORY) else {}
        use_pl = bool(data.get("use_playlist", False)) or cat == PLAYLIST_CATEGORY
        if data.get("use_scene_music", True):
            pools = [scene_pool, linked, playlist, genre_pool]
        elif use_pl:
            pools = [playlist, linked, genre_pool, scene_pool]
        else:
            pools = [genre_pool, linked, playlist, scene_pool]
        return self._remote_pick(pools, name)

    def _remote_resolve_scene_quickfire(self, scene_path, name):
        data = self._remote_scene_settings(scene_path)
        qf = data.get("quickfire_path", "") or ""
        pool = self._remote_files_in(qf, {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.mp4'})
        return self._remote_pick([pool], name)

    # ----- remote: music from genre folders / absolute paths / playlists / sfx -----
    @staticmethod
    def _remote_safe_parts(rel):
        parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]
        return None if any(p == ".." for p in parts) else parts

    def _remote_direct_music(self, music, genre):
        # music file that doesn't depend on the music list: abs path or genre folder + name
        try:
            if music and os.path.isabs(music):
                if os.path.isfile(music) and os.path.splitext(music)[1].lower() in AUDIO_EXTS:
                    return music
                return None
            if genre and music:
                parts = self._remote_safe_parts(genre)
                if parts is None:
                    return None
                folder = os.path.join(self.txt_improv_music_path.text().strip(), *parts)
                pool = self._remote_files_in(folder, AUDIO_EXTS)
                return self._remote_pick([pool], music)
        except Exception as e:
            logging.debug(f"Remote: direct music lookup failed: {e}")
        return None

    def _remote_direct_sfx(self, sfx, category):
        try:
            if sfx and os.path.isabs(sfx):
                return sfx if os.path.isfile(sfx) else None
            if category is not None and sfx:
                parts = [] if category in ("", ROOT_LABEL) else self._remote_safe_parts(category)
                if parts is None:
                    return None
                folder = os.path.join(self.txt_improv_sfx_path.text().strip(), *parts)
                return self._remote_pick([self._remote_files_in(folder)], sfx)
        except Exception as e:
            logging.debug(f"Remote: direct sfx lookup failed: {e}")
        return None

    def _remote_find_playlist(self, spec, scene_hint=None):
        spec = spec.strip()
        if os.path.isabs(spec):
            if os.path.isfile(spec):
                return {"path": spec, "name": os.path.splitext(os.path.basename(spec))[0]}
            return None
        low = spec.lower()
        matches = []
        for p in self.list_all_playlists():
            fname = os.path.basename(p["path"])
            if spec in (p["name"], p["label"], fname) or low in (p["name"].lower(), p["label"].lower(), fname.lower()):
                matches.append(p)
        if not matches:
            return None
        hint = os.path.normpath(scene_hint) if scene_hint else None

        def rank(p):
            in_hint = hint is not None and os.path.normpath(p["path"]).startswith(hint + os.sep)
            exact = p["name"] == spec or p["label"] == spec
            return (0 if in_hint else 1, 0 if exact else 1)
        matches.sort(key=rank)
        return matches[0]

    def _remote_apply_music_source(self, playlist, playlist_off, genre_select, scene_hint):
        # Playlist / genre choice of the ACTIVE scene, done through the same UI path as clicking.
        if playlist:
            info = self._remote_find_playlist(playlist, scene_hint)
            if not info:
                logging.debug(f"Remote command: playlist {playlist!r} not found")
                return
            entries, _ = self.read_playlist_file(info["path"])
            valid = [e for e in entries if os.path.exists(e["path"])]
            if not valid:
                logging.debug(f"Remote command: playlist {playlist!r} has no playable tracks")
                return
            self.music_playlist = []
            self.music_playlist_name = info["name"]
            self.music_playlist_source = info["path"]
            self._playlist_append(valid)
            if self.chk_use_playlist.isChecked():
                self._after_playlist_changed()
            else:
                self.chk_use_playlist.setChecked(True)     # rebuilds the list, saves, refreshes dialogs
            self._sync_playlist_combo_selection()
            return
        if playlist_off and self.chk_use_playlist.isChecked():
            self.chk_use_playlist.setChecked(False)
        if genre_select:
            idx = self.combo_improv_folders.findText(genre_select, Qt.MatchFlag.MatchExactly)
            if idx < 0:
                idx = self.combo_improv_folders.findText(genre_select, Qt.MatchFlag.MatchFixedString)
            if idx < 0:
                logging.debug(f"Remote command: genre {genre_select!r} not found")
                return
            if self.chk_use_playlist.isChecked():
                self.chk_use_playlist.setChecked(False)
            if self.chk_use_scene_music.isChecked():
                self.chk_use_scene_music.setChecked(False)
            self.combo_improv_folders.setCurrentIndex(idx)
            self.load_assets(self._capture_current_orders())
            self.save_scene_settings()

    def _remote_play_sfx(self, name):
        for i in range(self.sfx_list.count()):
            if self.sfx_list.item(i).text().lower() == str(name).lower():
                self.sfx_list.setCurrentRow(i)
                self.play_sfx()
                return
        logging.debug(f"Remote command: sfx {name!r} not found in the active SFX list")

    def _remote_resolve_scene_sfx(self, scene_path, name):
        data = self._remote_scene_settings(scene_path)
        linked = data.get("linked_assets", {})
        linked = linked.get("sfx", {}) if isinstance(linked, dict) else {}
        linked = {k: v for k, v in linked.items() if isinstance(k, str) and isinstance(v, str)}
        scene_pool = self._remote_files_in(os.path.join(scene_path, "sfx"))
        cat = data.get("improv_sfx_category", "") or ""
        parts = [] if cat in ("", ROOT_LABEL) else (self._remote_safe_parts(cat) or [])
        lib_pool = self._remote_files_in(os.path.join(self.txt_improv_sfx_path.text().strip(), *parts))
        pools = [scene_pool, linked, lib_pool] if data.get("use_scene_sfx", True) else [lib_pool, linked, scene_pool]
        return self._remote_pick(pools, name)

    def _remote_play_in_other_scene(self, scene_path, music, quickfire, fire, sfx=''):
        if not os.path.isdir(scene_path):
            logging.debug(f"Remote command: scene folder not found: {scene_path}")
            return
        if music:
            path = self._remote_resolve_scene_music(scene_path, music)
            if path:
                self.play_music_file(path, os.path.basename(path))
            else:
                logging.debug(f"Remote command: music track {music!r} not found for scene {scene_path!r}")
        if sfx:
            path = self._remote_resolve_scene_sfx(scene_path, sfx)
            if path:
                self._play_sfx_file(path, os.path.basename(path))
            else:
                logging.debug(f"Remote command: sfx {sfx!r} not found for scene {scene_path!r}")
        if quickfire or fire:
            if not quickfire:
                logging.debug("Remote command: quickfire for another scene needs a file name")
                return
            path = self._remote_resolve_scene_quickfire(scene_path, quickfire)
            if not path:
                logging.debug(f"Remote command: quickfire {quickfire!r} not found for scene {scene_path!r}")
            elif fire:
                self._fire_quickfire_path(path)

    def _remote_quickfire(self, name, fire):
        if name:
            idx = self.combo_quickfire.findText(name, Qt.MatchFlag.MatchExactly)
            if idx < 0:
                idx = self.combo_quickfire.findText(name, Qt.MatchFlag.MatchFixedString)
            if idx < 0:
                logging.debug(f"Remote command: quickfire file {name!r} not found in this scene's quickfire list")
                return
            self.combo_quickfire.setCurrentIndex(idx)
        if fire:
            self.fire_quickfire_sfx()

    def apply_remote_command(self, cmd):
        # Applies a command file written by the Session Manager. Everything is
        # additive; any combination of fields may be sent together.
        #
        # SCENE / LAYOUT
        #   "session" + "scene"  switch to that scene (starts everything, like clicking it)
        #   "layout"             load that layout
        #   "switch_scene": false  with session+scene: do NOT switch; "music", "sfx" and
        #                        "quickfire" are looked up in THAT scene's own settings
        #                        and played right away (playlist/genre-select need the
        #                        active scene and are ignored in this mode)
        # MUSIC
        #   "music": "track.mp3"      track from the active scene's current music list
        #   "music": "C:/full/path.mp3"  any audio file by absolute path
        #   "music_genre": "Jazz/Bebop" + "music": "x.mp3"  play x.mp3 from that genre
        #                        folder (relative to the music root, '/'-nested). Stateless:
        #                        the UI/scene settings are not changed.
        #   "music_genre": "Jazz"     (no "music") select that genre in the music panel
        #   "playlist": "Name"        load a saved playlist (name, "Session / Scene — Name"
        #                        label, or full path to its .json) and switch the music
        #                        panel to "Use Playlist". Add "music" to start a track of it.
        #   "playlist_off": true      leave playlist mode (back to the genre list)
        #   "play": true              with playlist/music_genre-select: start playing the list
        # SFX
        #   "sfx": "name.wav"         SFX from the active scene's SFX list (loops, like Play)
        #   "sfx_category": "Rain" + "sfx": "x.wav"  from that folder of the SFX library
        #                        ("" or "--- Root Folder ---" = library root). Stateless.
        #   "sfx": "C:/full/path.wav" any file by absolute path
        #   "sfx_stop": true          stop the SFX
        # QUICKFIRE
        #   "quickfire": "file.wav", "quickfire_fire": true
        try:
            session = cmd.get('session', '')
            scene = cmd.get('scene', '')
            layout = cmd.get('layout', '')
            music = str(cmd.get('music', '') or '')
            music_genre = str(cmd.get('music_genre', '') or '').replace('\\', '/').strip('/')
            playlist = str(cmd.get('playlist', '') or '')
            playlist_off = bool(cmd.get('playlist_off', False))
            want_play = bool(cmd.get('play', False))
            sfx = str(cmd.get('sfx', '') or '')
            sfx_category = cmd.get('sfx_category', None)
            sfx_category = None if sfx_category is None else str(sfx_category).replace('\\', '/').strip('/')
            sfx_stop = bool(cmd.get('sfx_stop', False))
            quickfire = cmd.get('quickfire', '')
            quickfire_fire = bool(cmd.get('quickfire_fire', False))

            # Sources that don't depend on any list: resolved to a file path up front
            direct_music = self._remote_direct_music(music, music_genre)
            direct_sfx = self._remote_direct_sfx(sfx, sfx_category)
            music_in_list = '' if (music_genre or os.path.isabs(music)) else music
            sfx_in_list = '' if (sfx_category is not None or os.path.isabs(sfx)) else sfx
            genre_select = music_genre if (music_genre and not music) else ''
            if (music_genre and music and not direct_music) or (os.path.isabs(music) and not direct_music):
                logging.debug(f"Remote command: music file not found (genre={music_genre!r}, music={music!r})")
            if ((sfx_category is not None and sfx) or os.path.isabs(sfx)) and not direct_sfx:
                logging.debug(f"Remote command: sfx file not found (category={sfx_category!r}, sfx={sfx!r})")

            has_extras = bool(music or music_genre or playlist or playlist_off or sfx or sfx_stop
                              or quickfire or quickfire_fire)
            switching = bool(session and scene)
            if not switching and not has_extras:
                logging.debug(f"Remote command missing session/scene: {cmd}")
                return
            if not switching and not self.scene_path:
                logging.debug(f"Remote command has no session/scene and no scene is active yet: {cmd}")
                return

            # Play/fire something that belongs to ANOTHER scene without leaving the
            # scene that is active now.
            if switching and cmd.get('switch_scene', True) is False and has_extras:
                target = os.path.join(self.root_path, session, scene)
                if os.path.normpath(target) != os.path.normpath(self.scene_path or ""):
                    if direct_music:
                        self.play_music_file(direct_music, os.path.basename(direct_music))
                        music_in_list = ''
                    if direct_sfx:
                        self._play_sfx_file(direct_sfx, os.path.basename(direct_sfx))
                        sfx_in_list = ''
                    if sfx_stop:
                        self.stop_sfx_only()
                    self._remote_play_in_other_scene(target, music_in_list, quickfire, quickfire_fire, sfx_in_list)
                    if playlist or playlist_off or genre_select:
                        logging.debug("Remote command: playlist/genre selection needs the active scene - ignored with switch_scene=false")
                    return
                # it IS the active scene: handled by the normal code below

            is_new_scene = False
            if switching:
                if session != self.current_session:
                    its = self.session_list.findItems(session, Qt.MatchFlag.MatchExactly)
                    if not its:
                        available = [self.session_list.item(i).text() for i in range(self.session_list.count())]
                        logging.debug(f"Remote command: session {session!r} not found. root_path={self.root_path!r}, available sessions={available}")
                        return
                    self.session_list.setCurrentItem(its[0])
                    self.load_scenes_for_session(its[0])
                s_its = self.scene_list.findItems(scene, Qt.MatchFlag.MatchExactly)
                if not s_its:
                    available_scenes = [self.scene_list.item(i).text() for i in range(self.scene_list.count())]
                    logging.debug(f"Remote command: scene {scene!r} not found in session {session!r}. available scenes={available_scenes}")
                    return

                target_scene_path = os.path.join(self.root_path, session, scene)
                is_new_scene = (self.scene_path != target_scene_path)

                if is_new_scene:
                    self.scene_list.setCurrentItem(s_its[0])
                    self.set_active_scene(s_its[0])
                    # playlist / genre choice first, so the list the music is picked from is the right one
                    self._remote_apply_music_source(playlist, playlist_off, genre_select, target_scene_path)
                    if layout:
                        l_its = self.layout_list.findItems(layout, Qt.MatchFlag.MatchExactly)
                        if l_its:
                            self.layout_list.setCurrentItem(l_its[0])
                        else:
                            logging.debug(f"Remote command: layout {layout!r} not found in new scene {scene!r}, start_all_master will fall back to the first layout instead")
                    # Clear the music selection so start_all_master starts a fresh
                    # track - or select the requested one, which start_all_master's
                    # play_music() then picks up as the current item.
                    self.music_list.blockSignals(True)
                    self.music_list.clearSelection()
                    self.music_list.setCurrentRow(-1)
                    if music_in_list:
                        row = self._remote_find_music_row(music_in_list)
                        if row >= 0:
                            self.music_list.setCurrentRow(row)
                        else:
                            logging.debug(f"Remote command: music track {music_in_list!r} not found in new scene {scene!r}, starting the default track instead")
                    self.music_list.blockSignals(False)
                    self.start_all_master()
                elif layout:
                    self.load_layout_with_fade(layout)

            if not is_new_scene:
                self._remote_apply_music_source(playlist, playlist_off, genre_select, self.scene_path)

            # Same scene (or no scene given): the track plays on its own,
            # exactly like double-clicking it in the list.
            if music_in_list and not is_new_scene:
                self._remote_play_music(music_in_list)
            elif want_play and not is_new_scene and not music_in_list and not direct_music \
                    and (playlist or genre_select):
                self.music_list.blockSignals(True)
                self.music_list.setCurrentRow(-1)
                self.music_list.blockSignals(False)
                self.play_music()
            if direct_music:
                # after a scene change start_all_master has just started its own track; replace it
                if is_new_scene:
                    QTimer.singleShot(500, lambda p=direct_music: self.play_music_file(p, os.path.basename(p)))
                else:
                    self.play_music_file(direct_music, os.path.basename(direct_music))
            if sfx_in_list:
                self._remote_play_sfx(sfx_in_list)
            if direct_sfx:
                if is_new_scene:
                    QTimer.singleShot(600, lambda p=direct_sfx: self._play_sfx_file(p, os.path.basename(p)))
                else:
                    self._play_sfx_file(direct_sfx, os.path.basename(direct_sfx))
            if sfx_stop:
                self.stop_sfx_only()
            if quickfire or quickfire_fire:
                self._remote_quickfire(quickfire, quickfire_fire)
        except Exception as e:
            logging.critical(f"Remote command apply failed: {e}", exc_info=True)

    def init_ui(self):
        main_widget = QWidget();
        full_v_layout = QVBoxLayout()
        path_layout = QHBoxLayout();
        path_layout.addWidget(QLabel("Sessions Folder:"));
        self.txt_root = QLineEdit(self.root_path)
        self.txt_root.setPlaceholderText("Folder that contains your session folders...")
        self.txt_root.returnPressed.connect(self.update_root)
        self.txt_root.setMaximumWidth(900)          # long paths scroll; the full path is in the tooltip
        self.txt_root.setToolTip(self.root_path or "")
        self.txt_root.textChanged.connect(self.txt_root.setToolTip)
        btn_set_root = QPushButton("📁 Browse...");
        btn_set_root.setToolTip("Select the folder that contains your session folders")
        btn_set_root.clicked.connect(self.browse_root_path);
        btn_portable = QPushButton("🧳")
        btn_portable.setFixedWidth(34)
        btn_portable.setToolTip("Make project portable: store file paths relative so the whole "
                                "sessions folder can be copied to another PC")
        btn_portable.clicked.connect(self.make_project_portable)
        path_layout.addWidget(self.txt_root, 3)
        path_layout.addWidget(btn_set_root)
        path_layout.addWidget(btn_portable)
        path_layout.addStretch(1)      # the free space beyond the path field goes here
        btn_mini = QPushButton("🗔 MINI CONTROLS");
        btn_mini.setStyleSheet("background-color: #444; color: white; font-weight: bold;");
        btn_mini.clicked.connect(self.toggle_mini_controls);
        path_layout.addWidget(btn_mini);
        btn_settings = QPushButton("⚙ Settings");
        btn_settings.setStyleSheet("background-color: #444; color: white; font-weight: bold;");
        btn_settings.clicked.connect(self.open_settings);
        path_layout.addWidget(btn_settings);
        full_v_layout.addLayout(path_layout)

        # --- SESSION RECORDING BAR ---
        rec_bar = QHBoxLayout()
        rec_bar.addStretch()
        self.btn_rec_start = QPushButton("⏺ Record Session")
        self.btn_rec_start.setStyleSheet("background-color: #b71c1c; color: white; font-weight: bold;")
        self.btn_rec_start.clicked.connect(self.rec_start)
        self.btn_rec_pause = QPushButton("⏸ Pause")
        self.btn_rec_pause.setStyleSheet("background-color: #555; color: white; font-weight: bold;")
        self.btn_rec_pause.setEnabled(False)
        self.btn_rec_pause.clicked.connect(self.rec_pause_resume)
        self.btn_rec_stop = QPushButton("⏹ End")
        self.btn_rec_stop.setStyleSheet("background-color: #333; color: #aaa; font-weight: bold;")
        self.btn_rec_stop.setEnabled(False)
        self.btn_rec_stop.clicked.connect(self.rec_stop)
        self.btn_rec_mark = QPushButton("📍 Mark (Ctrl+M)")
        self.btn_rec_mark.setStyleSheet("background-color: #1a237e; color: white; font-weight: bold;")
        self.btn_rec_mark.setEnabled(False)
        self.btn_rec_mark.clicked.connect(self.rec_mark)
        self.lbl_rec_time = QLabel("00:00:00")
        self.lbl_rec_time.setStyleSheet("color: #ef9a9a; font-family: 'Consolas'; font-size: 13px; font-weight: bold;")
        # Pause / End / Mark / timer only matter while a recording runs, so they appear with it
        # (rec_start shows them, rec_stop hides them). Break and Transcribe stay where they are.
        self.rec_live = QWidget()
        live_l = QHBoxLayout(self.rec_live)
        live_l.setContentsMargins(0, 0, 0, 0)
        for w in (self.btn_rec_pause, self.btn_rec_stop, self.btn_rec_mark, self.lbl_rec_time):
            live_l.addWidget(w)
        self.rec_live.setVisible(False)
        self.btn_break = QPushButton("☕ BREAK")
        self.btn_break.setStyleSheet("background-color: #4a148c; color: white; font-weight: bold;")
        self.btn_break.setCheckable(True)
        self.btn_break.clicked.connect(self.toggle_break_mode)
        btn_break_cfg = QPushButton("⚙")
        btn_break_cfg.setFixedWidth(28)
        btn_break_cfg.setToolTip("Configure break folders")
        btn_break_cfg.clicked.connect(self.configure_break_folders)
        self.btn_transcribe = QPushButton("📝 Transcribe")
        self.btn_transcribe.setToolTip("Turn recorded session parts into a text transcript, locally on this PC.")
        self.btn_transcribe.clicked.connect(self.open_transcriber)
        rec_bar.addWidget(self.btn_rec_start)
        rec_bar.addWidget(self.rec_live)
        rec_bar.addSpacing(20)
        rec_bar.addWidget(self.btn_break)
        rec_bar.addWidget(btn_break_cfg)
        rec_bar.addSpacing(20)
        rec_bar.addWidget(self.btn_transcribe)
        full_v_layout.addLayout(rec_bar)
        # --- END RECORDING BAR ---

        content_layout = QHBoxLayout();
        campaign_sidebar = QVBoxLayout();
        sess_head = QHBoxLayout();
        sess_head.addWidget(QLabel("📁 Sessions"))
        btn_add_sess = QPushButton("+");
        btn_add_sess.setFixedSize(22, 22);
        btn_add_sess.clicked.connect(self.add_new_session);
        sess_head.addWidget(btn_add_sess);
        campaign_sidebar.addLayout(sess_head)
        self.session_list = QListWidget();
        self.session_list.itemClicked.connect(self.load_scenes_for_session);
        campaign_sidebar.addWidget(self.session_list, 1)
        scene_head = QHBoxLayout();
        scene_head.addWidget(QLabel("🎬 Scenes"));
        btn_add_scene = QPushButton("+");
        btn_add_scene.setFixedSize(22, 22);
        btn_add_scene.clicked.connect(self.add_new_scene);
        scene_head.addWidget(btn_add_scene);
        campaign_sidebar.addLayout(scene_head)
        self.scene_list = QListWidget();
        self.scene_list.itemClicked.connect(self.set_active_scene)
        self.scene_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);
        self.scene_list.customContextMenuRequested.connect(self.show_scene_context_menu)
        campaign_sidebar.addWidget(self.scene_list, 2);
        content_layout.addLayout(campaign_sidebar, 1)

        type_sidebar = QVBoxLayout();
        self.music_list = QListWidget();
        self.music_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.sfx_list = QListWidget();
        self.sfx_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.visuals_list = QListWidget();
        self.visuals_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.layout_list = QListWidget();
        self.layout_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.layout_list.setIconSize(QSize(10, 10))     # room for the blue "uses linked visuals" dot

        for w in [self.music_list, self.sfx_list, self.visuals_list, self.layout_list]:
            w.model().rowsMoved.connect(lambda: self.save_scene_settings())
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.music_list.model().rowsMoved.connect(self._on_main_music_order_changed)

        self.music_list.customContextMenuRequested.connect(
            lambda p: self.show_asset_context_menu(p, self.music_list, "music"))
        self.sfx_list.customContextMenuRequested.connect(
            lambda p: self.show_asset_context_menu(p, self.sfx_list, "sfx"))
        self.visuals_list.customContextMenuRequested.connect(
            lambda p: self.show_asset_context_menu(p, self.visuals_list, "visuals"))
        self.layout_list.customContextMenuRequested.connect(self.show_layout_context_menu)

        self.visuals_list.itemDoubleClicked.connect(
            lambda it: self.add_asset_to_canvas(it.text(), path=it.data(Qt.ItemDataRole.UserRole)))
        self.layout_list.itemDoubleClicked.connect(lambda it: self.load_layout_with_fade(it.text()));
        self.music_list.itemDoubleClicked.connect(self.play_music);
        self.sfx_list.itemDoubleClicked.connect(self.play_sfx)
        self.visuals_list.currentItemChanged.connect(self._on_visuals_selection_changed_for_waveform)

        def create_header(label, list_type):
            h = QHBoxLayout()
            h.addWidget(QLabel(label))
            b = QPushButton("🔗")
            b.setFixedSize(24, 24)
            b.setToolTip(f"Link {list_type} from another folder")
            b.clicked.connect(lambda: self.add_external_assets(list_type))
            h.addWidget(b)
            cb = QPushButton("▼")
            cb.setFixedSize(24, 24)
            cb.setToolTip("Collapse / expand this panel")
            cb.clicked.connect(lambda: self.toggle_section(list_type))
            self.section_collapse_buttons[list_type] = cb
            h.addWidget(cb)
            return h

        type_sidebar.addLayout(create_header("🎵 Music", "music"));
        self.chk_use_scene_music = QCheckBox("Use Music from Folder");
        self.chk_use_scene_music.setChecked(True)
        self.chk_use_scene_music.toggled.connect(self.on_music_source_toggled)
        self.improv_container = QWidget();
        improv_vbox = QVBoxLayout();
        improv_vbox.setContentsMargins(0, 0, 0, 0);
        path_row = QHBoxLayout();
        self.txt_improv_music_path = QLineEdit(self.improv_path);
        self.txt_improv_music_path.textChanged.connect(self.populate_improv_categories)
        btn_browse_improv = QPushButton("...");
        btn_browse_improv.setFixedWidth(30);
        btn_browse_improv.clicked.connect(self.browse_improv_music_path);
        path_row.addWidget(self.txt_improv_music_path);
        path_row.addWidget(btn_browse_improv);
        self.combo_improv_folders = QComboBox();
        self.combo_improv_folders.currentTextChanged.connect(self.on_improv_folder_changed)
        improv_vbox.addLayout(path_row);
        improv_vbox.addWidget(self.combo_improv_folders);
        self.improv_container.setLayout(improv_vbox);
        self.improv_container.setVisible(False);
        # "Use Playlist" sits to the right of "Use Music from Folder"
        self.chk_use_playlist = QCheckBox("Use Playlist")
        self.chk_use_playlist.setToolTip("Replace the genre list with one of your saved playlists")
        self.chk_use_playlist.toggled.connect(self.on_playlist_source_toggled)
        self.music_source_widget = QWidget()
        music_source_lay = QHBoxLayout(self.music_source_widget)
        music_source_lay.setContentsMargins(0, 0, 0, 0)
        music_source_lay.addWidget(self.chk_use_scene_music)
        music_source_lay.addWidget(self.chk_use_playlist)
        music_source_lay.addStretch()
        # Dropdown of all saved playlists (shown instead of the genre dropdown)
        self.playlist_container = QWidget()
        playlist_lay = QHBoxLayout(self.playlist_container)
        playlist_lay.setContentsMargins(0, 0, 0, 0)
        self.combo_playlist_source = QComboBox()
        self.combo_playlist_source.setToolTip("All saved playlists in this project")
        self.combo_playlist_source.activated.connect(self.on_playlist_source_selected)
        btn_refresh_playlists = QPushButton("↻")
        btn_refresh_playlists.setFixedWidth(30)
        btn_refresh_playlists.setToolTip("Rescan all scenes for saved playlists")
        btn_refresh_playlists.clicked.connect(self.refresh_playlist_source_combo)
        playlist_lay.addWidget(self.combo_playlist_source, 1)
        playlist_lay.addWidget(btn_refresh_playlists)
        self.playlist_container.setVisible(False)
        type_sidebar.addWidget(self.music_source_widget)
        type_sidebar.addWidget(self.improv_container)
        type_sidebar.addWidget(self.playlist_container)
        music_wave_row = QHBoxLayout()
        self.chk_music_waveform = QCheckBox("〰️ Preview Waveform")
        self.chk_music_waveform.toggled.connect(self.on_music_waveform_toggled)
        music_wave_row.addWidget(self.chk_music_waveform)
        music_wave_row.addStretch()
        btn_music_manager = QPushButton("☰ Reorder / Filter / Playlist")
        btn_music_manager.setToolTip("Open the big view: reorder, blacklist/whitelist, build and save playlists")
        btn_music_manager.clicked.connect(self.open_music_manager)
        music_wave_row.addWidget(btn_music_manager)
        type_sidebar.addLayout(music_wave_row)
        self.music_waveform = WaveformWidget()
        self.music_waveform.setVisible(False)
        type_sidebar.addWidget(self.music_waveform)
        type_sidebar.addWidget(self.music_list, 1)

        type_sidebar.addLayout(create_header("💥 SFX", "sfx"))
        self.chk_use_scene_sfx = QCheckBox("Use SFX from Folder")
        self.chk_use_scene_sfx.setChecked(True)
        self.chk_use_scene_sfx.toggled.connect(self.on_sfx_source_toggled)
        self.improv_sfx_container = QWidget()
        improv_sfx_vbox = QVBoxLayout()
        improv_sfx_vbox.setContentsMargins(0, 0, 0, 0)
        sfx_path_row = QHBoxLayout()
        self.txt_improv_sfx_path = QLineEdit(self.improv_sfx_path)
        self.txt_improv_sfx_path.textChanged.connect(self.populate_improv_sfx_categories)
        btn_browse_sfx = QPushButton("...")
        btn_browse_sfx.setFixedWidth(30)
        btn_browse_sfx.clicked.connect(self.browse_improv_sfx_path)
        sfx_path_row.addWidget(self.txt_improv_sfx_path)
        sfx_path_row.addWidget(btn_browse_sfx)
        self.combo_improv_sfx_folders = QComboBox()
        self.combo_improv_sfx_folders.currentTextChanged.connect(self.on_improv_sfx_folder_changed)
        improv_sfx_vbox.addLayout(sfx_path_row)
        improv_sfx_vbox.addWidget(self.combo_improv_sfx_folders)
        self.improv_sfx_container.setLayout(improv_sfx_vbox)
        self.improv_sfx_container.setVisible(False)
        type_sidebar.addWidget(self.chk_use_scene_sfx)
        type_sidebar.addWidget(self.improv_sfx_container)

        # --- QUICKFIRE SFX ---
        qf_header = QHBoxLayout()
        self.chk_quickfire = QCheckBox("⚡ Quick Fire SFX")
        self.chk_quickfire.toggled.connect(self.on_quickfire_toggled)
        btn_qf_cfg = QPushButton("⚙")
        btn_qf_cfg.setFixedWidth(28)
        btn_qf_cfg.setToolTip("Set Quick Fire folder")
        btn_qf_cfg.clicked.connect(self.browse_quickfire_path)
        qf_header.addWidget(self.chk_quickfire)
        qf_header.addWidget(btn_qf_cfg)
        type_sidebar.addLayout(qf_header)

        self.quickfire_container = QWidget()
        qf_vbox = QVBoxLayout()
        qf_vbox.setContentsMargins(0, 0, 0, 0)
        qf_row = QHBoxLayout()
        self.combo_quickfire = QComboBox()
        self.combo_quickfire.setMinimumWidth(10)
        self.combo_quickfire.currentTextChanged.connect(self._load_waveform_for_current_quickfire)
        self.combo_quickfire.currentTextChanged.connect(lambda *_: self._qf_update_fire_button())
        self.btn_quickfire_fire = QPushButton("🔴 FIRE")
        self.btn_quickfire_fire.setStyleSheet(
            "background-color: #b71c1c; color: white; font-weight: bold;")
        self.btn_quickfire_fire.setFixedWidth(70)
        self.btn_quickfire_fire.clicked.connect(self.fire_quickfire_sfx)
        self.btn_quickfire_stop = QPushButton("■")
        self.btn_quickfire_stop.setFixedWidth(28)
        self.btn_quickfire_stop.setToolTip("Stop quickfire audio")
        self.btn_quickfire_stop.clicked.connect(self.stop_quickfire)
        qf_row.addWidget(self.combo_quickfire, 1)
        qf_row.addWidget(self.btn_quickfire_fire)
        qf_row.addWidget(self.btn_quickfire_stop)
        qf_vbox.addLayout(qf_row)
        qf_vol_row = QHBoxLayout()
        lbl_qf_vol = QLabel("🔊 Quick Fire volume")
        lbl_qf_vol.setToolTip("Volume of the Quick Fire sounds. Independent of the SFX slider.")
        self.quickfire_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.quickfire_vol_slider.setRange(0, 100)
        self.quickfire_vol_slider.setValue(80)
        self.quickfire_vol_slider.setToolTip("Quick Fire volume (saved per scene)")
        self.quickfire_vol_slider.valueChanged.connect(self._on_quickfire_volume_changed)
        qf_vol_row.addWidget(lbl_qf_vol)
        qf_vol_row.addWidget(self.quickfire_vol_slider, 1)
        qf_vbox.addLayout(qf_vol_row)
        self.quickfire_waveform = WaveformWidget()
        qf_vbox.addWidget(self.quickfire_waveform)
        self.quickfire_container.setLayout(qf_vbox)
        self.quickfire_container.setVisible(False)
        type_sidebar.addWidget(self.quickfire_container)
        # --- END QUICKFIRE SFX ---


        self.chk_sfx_waveform = QCheckBox("〰️ Preview Waveform")
        self.chk_sfx_waveform.toggled.connect(self.on_sfx_waveform_toggled)
        type_sidebar.addWidget(self.chk_sfx_waveform)
        self.sfx_waveform = WaveformWidget()
        self.sfx_waveform.setVisible(False)
        type_sidebar.addWidget(self.sfx_waveform)
        type_sidebar.addWidget(self.sfx_list, 1)

        type_sidebar.addLayout(create_header("🖼️ Visuals", "visuals"))
        self.chk_use_scene_visuals = QCheckBox("Use Visuals from Folder")
        self.chk_use_scene_visuals.setChecked(True)
        self.chk_use_scene_visuals.toggled.connect(self.on_visuals_source_toggled)
        self.improv_visuals_container = QWidget()
        improv_visuals_vbox = QVBoxLayout()
        improv_visuals_vbox.setContentsMargins(0, 0, 0, 0)
        visuals_path_row = QHBoxLayout()
        self.txt_improv_visuals_path = QLineEdit(self.improv_visuals_path)
        self.txt_improv_visuals_path.textChanged.connect(self.populate_improv_visuals_categories)
        btn_browse_visuals = QPushButton("...")
        btn_browse_visuals.setFixedWidth(30)
        btn_browse_visuals.clicked.connect(self.browse_improv_visuals_path)
        visuals_path_row.addWidget(self.txt_improv_visuals_path)
        visuals_path_row.addWidget(btn_browse_visuals)
        self.combo_improv_visuals_folders = QComboBox()
        self.combo_improv_visuals_folders.currentTextChanged.connect(self.on_improv_visuals_folder_changed)
        improv_visuals_vbox.addLayout(visuals_path_row)
        improv_visuals_vbox.addWidget(self.combo_improv_visuals_folders)
        self.improv_visuals_container.setLayout(improv_visuals_vbox)
        self.improv_visuals_container.setVisible(False)
        type_sidebar.addWidget(self.chk_use_scene_visuals)
        type_sidebar.addWidget(self.improv_visuals_container)
        self.chk_visuals_waveform = QCheckBox("〰️ Preview Waveform (video audio)")
        self.chk_visuals_waveform.toggled.connect(self.on_visuals_waveform_toggled)
        type_sidebar.addWidget(self.chk_visuals_waveform)
        self.visuals_waveform = WaveformWidget()
        self.visuals_waveform.setVisible(False)
        type_sidebar.addWidget(self.visuals_waveform)
        type_sidebar.addWidget(self.visuals_list, 2)

        layouts_head = QHBoxLayout()
        layouts_head.addWidget(QLabel("📂 Layouts"))
        btn_link_layout = QPushButton("🔗")
        btn_link_layout.setFixedSize(24, 24)
        btn_link_layout.setToolTip("Link layout from another folder")
        btn_link_layout.clicked.connect(lambda: self.add_external_assets("layouts"))
        btn_new_layout = QPushButton("+")
        btn_new_layout.setFixedSize(24, 24)
        btn_new_layout.setToolTip("Create new empty layout")
        btn_new_layout.clicked.connect(self.create_new_empty_layout)
        layouts_head.addWidget(btn_link_layout)
        layouts_head.addWidget(btn_new_layout)
        btn_collapse_layouts = QPushButton("▼")
        btn_collapse_layouts.setFixedSize(24, 24)
        btn_collapse_layouts.setToolTip("Collapse / expand this panel")
        btn_collapse_layouts.clicked.connect(lambda: self.toggle_section("layouts"))
        self.section_collapse_buttons["layouts"] = btn_collapse_layouts
        layouts_head.addWidget(btn_collapse_layouts)
        type_sidebar.addLayout(layouts_head)
        type_sidebar.addWidget(self.layout_list, 1);
        self._build_collapsible_sections(type_sidebar, music_wave_row, qf_header)
        # When many options are open at once (Quick Fire, improv folders, waveform previews) the left
        # column can need more height than the window has. The track lists should give way first
        # (they scroll), instead of Qt squeezing dropdowns and sliders until they overlap.
        for _lst in (self.music_list, self.sfx_list, self.visuals_list, self.layout_list):
            _lst.setMinimumHeight(30)
        content_layout.addLayout(type_sidebar, 1)
        canvas_area = QVBoxLayout()
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("🖥️ DM PREVIEW"))

        # Foldable menus: click to open / close. Their state is remembered between runs.
        self.btn_fold_annotate = QPushButton()
        self.btn_fold_annotate.setToolTip("Drawing tools: brush, rectangle, clear")
        self.btn_fold_annotate.clicked.connect(lambda: self.toggle_fold("annotate"))
        self.btn_fold_blanking = QPushButton()
        self.btn_fold_blanking.setToolTip("Blanking: black boxes that hide parts of the stage")
        self.btn_fold_blanking.clicked.connect(lambda: self.toggle_fold("blanking"))
        header_row.addWidget(self.btn_fold_annotate)
        header_row.addWidget(self.btn_fold_blanking)
        header_row.addStretch()

        # Annotation tools (shown in the Annotate menu)
        self.btn_annotate_brush = QPushButton("Brush (Alt+A)")
        self.btn_annotate_brush.setCheckable(True)
        self.btn_annotate_brush.setFixedWidth(100)
        self.btn_annotate_brush.clicked.connect(lambda: self.set_annotation_mode("brush"))
        self.btn_annotate_rect = QPushButton("Rect")
        self.btn_annotate_rect.setCheckable(True)
        self.btn_annotate_rect.setFixedWidth(40)
        self.btn_annotate_rect.clicked.connect(lambda: self.set_annotation_mode("rect"))
        self.btn_annotate_clear = QPushButton("🗑 Clear (Alt+Shift+A)")
        self.btn_annotate_clear.setFixedWidth(150)
        self.btn_annotate_clear.clicked.connect(self.clear_annotations)

        # "Ghost" (see-through) slider: lives in the Management column, created here
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal);
        self.opacity_slider.setMinimumWidth(40);
        self.opacity_slider.setRange(0, 100);
        self.opacity_slider.setValue(100);
        self.opacity_slider.valueChanged.connect(self.change_object_opacity);

        # Blanking menu widgets
        self.chk_blank_scene = QCheckBox("In this scene")
        self.chk_blank_scene.setToolTip("Turn blanking on for the current scene only")
        self.chk_blank_scene.toggled.connect(self.on_blank_scene_toggled)
        self.chk_blank_all = QCheckBox("In all scenes")
        self.chk_blank_all.setToolTip("Turn blanking on for every scene (remembered on this PC)")
        self.chk_blank_all.toggled.connect(self.on_blank_all_toggled)
        self.chk_show_blanking = QCheckBox("🔳 Show Blanking");
        self.chk_show_blanking.setChecked(True);
        self.chk_show_blanking.toggled.connect(self.toggle_blanking_visibility);
        self.btn_add_blanking = QPushButton("➕ Add box (Ctrl+B)")
        self.btn_add_blanking.clicked.connect(self.add_blanking_box_manual)

        header_row.addWidget(QLabel("⏱ Fade (s):"));
        self.spin_fade_dur = QDoubleSpinBox();
        self.spin_fade_dur.setRange(0.0, 10.0);
        self.spin_fade_dur.setSingleStep(0.5);
        self.spin_fade_dur.setValue(1.0);
        self.spin_fade_dur.valueChanged.connect(self.save_scene_settings);
        header_row.addWidget(self.spin_fade_dur)
        btn_home = QPushButton("🏠 Home (Reset View)");
        btn_home.setFixedWidth(140);
        btn_home.clicked.connect(self.reset_preview_view);
        header_row.addWidget(btn_home);
        canvas_area.addLayout(header_row);

        # Foldable strips under the header row
        self.annotate_strip = QWidget()
        a_lay = QHBoxLayout(self.annotate_strip)
        a_lay.setContentsMargins(0, 0, 0, 0)
        a_lay.addWidget(self.btn_annotate_brush)
        a_lay.addWidget(self.btn_annotate_rect)
        a_lay.addWidget(self.btn_annotate_clear)
        a_lay.addStretch()
        canvas_area.addWidget(self.annotate_strip)
        self.blanking_strip = QWidget()
        b_lay = QHBoxLayout(self.blanking_strip)
        b_lay.setContentsMargins(0, 0, 0, 0)
        b_lay.addWidget(QLabel("Blanking on:"))
        b_lay.addWidget(self.chk_blank_scene)
        b_lay.addWidget(self.chk_blank_all)
        b_lay.addSpacing(12)
        b_lay.addWidget(self.chk_show_blanking)
        b_lay.addWidget(self.btn_add_blanking)
        b_lay.addStretch()
        canvas_area.addWidget(self.blanking_strip)
        self.preview_view = PreviewView(self.shared_scene);
        self.preview_view.setStyleSheet("background-color: #080808; border: 1px solid #333;");
        # keeps the column proportions exactly as in earlier versions (the old, wider toolbar used to force this width)
        self.preview_view.setMinimumWidth(974);
        canvas_area.addWidget(self.preview_view);
        content_layout.addLayout(canvas_area, 3)
        controls = QVBoxLayout();
        controls.addWidget(QLabel("💾 MANAGEMENT"));
        btn_save = QPushButton("Save Current Layout");
        btn_save.clicked.connect(self.save_layout_dialog);
        controls.addWidget(btn_save);
        btn_clear = QPushButton("🗑️ Clear Canvas");
        btn_clear.clicked.connect(self.clear_canvas_master);
        controls.addSpacing(3);
        row_z = QHBoxLayout()
        btn_front = QPushButton("🔼 Front");
        btn_front.clicked.connect(self.bring_to_front);
        btn_back = QPushButton("🔽 Back");
        btn_back.clicked.connect(self.send_to_back);
        row_z.addWidget(btn_front);
        row_z.addWidget(btn_back);
        controls.addLayout(row_z);
        ghost_row = QHBoxLayout()
        lbl_ghost = QLabel("👻 Ghost")
        lbl_ghost.setToolTip("See-through slider for the selected picture, video or box")
        ghost_row.addWidget(lbl_ghost)
        ghost_row.addWidget(self.opacity_slider, 1)
        controls.addLayout(ghost_row)
        controls.addSpacing(5);
        controls.addWidget(QLabel("🎮 AUDIO MIXER"));
        track_info_layout = QHBoxLayout()
        self.lbl_now_playing = QLabel("Now: ---");
        self.lbl_now_playing.setStyleSheet("color: #4caf50; font-weight: bold; font-size: 13px;");
        self.lbl_now_playing.setFixedWidth(180)
        self.lbl_now_playing.setWordWrap(False)
        self.lbl_time_rem = QLabel("");
        self.lbl_time_rem.setStyleSheet("color: #aaa; font-family: 'Consolas', monospace;");
        track_info_layout.addWidget(self.lbl_now_playing, 1);
        track_info_layout.addWidget(self.lbl_time_rem);
        controls.addLayout(track_info_layout);
        self.lbl_next_playing = QLabel("Next: ---");
        self.lbl_next_playing.setStyleSheet("color: #81c784; font-size: 11px;");
        self.lbl_next_playing.setFixedWidth(180)
        self.lbl_next_playing.setWordWrap(False)
        controls.addWidget(self.lbl_next_playing)
        row_m = QHBoxLayout();
        btn_m_p = QPushButton("🎵 Play");
        btn_m_p.clicked.connect(self.play_music);
        btn_m_s = QPushButton("Stop");
        btn_m_s.clicked.connect(self.stop_music_only);
        row_m.addWidget(btn_m_p);
        row_m.addWidget(btn_m_s);
        controls.addLayout(row_m);
        row_nav = QHBoxLayout();
        btn_p = QPushButton("⏮ Prev");
        btn_p.clicked.connect(self.prev_music);
        btn_n = QPushButton("Next ⏭");
        btn_n.clicked.connect(self.next_music);
        row_nav.addWidget(btn_p);
        row_nav.addWidget(btn_n);
        controls.addLayout(row_nav)
        row_mode = QHBoxLayout();
        self.btn_order = QPushButton("In Order");
        self.btn_order.clicked.connect(lambda: self.set_music_mode(False));
        self.btn_shuffle = QPushButton("Shuffle");
        self.btn_shuffle.clicked.connect(lambda: self.set_music_mode(True));
        row_mode.addWidget(self.btn_order);
        row_mode.addWidget(self.btn_shuffle);
        controls.addLayout(row_mode);
        self.music_vol_slider = QSlider(Qt.Orientation.Horizontal);
        self.music_vol_slider.setRange(0, 100);
        self.music_vol_slider.setValue(80);
        self.music_vol_slider.valueChanged.connect(self.music_player.audio_set_volume)
        self.music_vol_slider.valueChanged.connect(lambda v: self.save_scene_settings())
        controls.addWidget(self.music_vol_slider);
        self.lbl_sfx_playing = QLabel("SFX: ---");
        self.lbl_sfx_playing.setStyleSheet("color: #ff9800; font-weight: bold;");
        controls.addWidget(self.lbl_sfx_playing)
        row_sfx = QHBoxLayout();
        btn_s_p = QPushButton("💥 SFX Play");
        btn_s_p.clicked.connect(self.play_sfx);
        btn_s_s = QPushButton("Stop");
        btn_s_s.clicked.connect(self.stop_sfx_only)
        row_sfx.addWidget(btn_s_p);
        row_sfx.addWidget(btn_s_s);
        controls.addLayout(row_sfx);
        self.sfx_vol_slider = QSlider(Qt.Orientation.Horizontal);
        self.sfx_vol_slider.setRange(0, 100);
        self.sfx_vol_slider.setValue(80);
        self.sfx_vol_slider.valueChanged.connect(self.sfx_player.audio_set_volume)
        self.sfx_vol_slider.valueChanged.connect(lambda v: self.save_scene_settings())
        controls.addWidget(self.sfx_vol_slider);
        btn_pa = QPushButton("▶ MASTER PLAY (Space)");
        btn_pa.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; height: 35px;");
        btn_pa.clicked.connect(self.play_all);
        controls.addWidget(btn_pa);
        btn_sa = QPushButton("🛑 MASTER STOP (Space)");
        btn_sa.setStyleSheet("background-color: #c62828; color: white; font-weight: bold;");
        btn_sa.clicked.connect(self.stop_all_audio);
        controls.addWidget(btn_sa);
        controls.addSpacing(15)
        btn_proj = QPushButton("🚀 PROJECTOR (Ctrl+P)");
        btn_proj.setStyleSheet("background-color: #1a73e8; color: white; font-weight: bold; height: 45px;");
        btn_proj.clicked.connect(self.launch_projector);
        controls.addWidget(btn_proj);
        self.btn_freeze = QPushButton("❄️ FREEZE PROJECTOR");
        self.btn_freeze.setStyleSheet("background-color: #455a64; color: white; font-weight: bold; height: 35px;");
        self.btn_freeze.clicked.connect(self.toggle_freeze)
        controls.addWidget(self.btn_freeze);
        btn_rel = QPushButton("🔄 RELOAD (Ctrl+R)");
        btn_rel.clicked.connect(self.reload_all);
        controls.addWidget(btn_rel);
        controls.addSpacing(15);
        controls.addWidget(QLabel("📜 SCRIPTS"))
        self.scripts_list = QListWidget();
        self.scripts_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.scripts_list.model().rowsMoved.connect(self._on_scripts_reordered)
        self.scripts_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scripts_list.customContextMenuRequested.connect(self.show_scripts_context_menu)
        self.scripts_list.itemDoubleClicked.connect(lambda *_: self.run_hue_script())
        self.scripts_list.setToolTip("Double-click or press ▶ to run. Drag to reorder. Right-click for more.")
        controls.addWidget(self.scripts_list, 1);
        script_row = QHBoxLayout()
        btn_start_script = QPushButton("▶ Start Script");
        btn_start_script.setStyleSheet("background-color: #673ab7; color: white; font-weight: bold;");
        btn_start_script.clicked.connect(self.run_hue_script);
        script_row.addWidget(btn_start_script, 1)
        btn_designer = QPushButton("💡 Lights")
        btn_designer.setToolTip("Connect your Hue bridge, set up your lights and save the look as a script.")
        btn_designer.clicked.connect(lambda: self.open_light_designer())
        script_row.addWidget(btn_designer)
        controls.addLayout(script_row);
        self.lbl_script_status = QLabel("Status: Idle");
        self.lbl_script_status.setStyleSheet("color: #aaa; font-size: 10px;");
        controls.addWidget(self.lbl_script_status);
        controls.addSpacing(10);
        btn_master_start = QPushButton("🌟 START ALL (Ctrl+Enter)");
        btn_master_start.setStyleSheet("background-color: #fbc02d; color: black; font-weight: bold; height: 50px;");
        btn_master_start.clicked.connect(self.start_all_master);
        controls.addWidget(btn_master_start);
        controls.addStretch();
        content_layout.addLayout(controls, 1);
        full_v_layout.addLayout(content_layout);
        main_widget.setLayout(full_v_layout);
        self.setCentralWidget(main_widget);
        self.populate_improv_categories();
        self.populate_improv_sfx_categories()  # ← add
        self.populate_improv_visuals_categories()
        self.set_music_mode(False)
        self._stabilize_sizes()
        self._apply_folds()
        self._refresh_blanking_ui()

    def _stabilize_sizes(self):
        # Anything whose text / item list changes (track names, playlist names,
        # genre paths, status messages) must never be able to push the layout
        # wider or taller - that is what made panels jump between scenes and
        # could blow the window up past the screen edge.
        for cb in self.findChildren(QComboBox):
            cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)
            cb.view().setMinimumWidth(max(cb.view().minimumWidth(), 380))   # popup can still be wide
        for lbl in (self.lbl_sfx_playing, self.lbl_script_status):
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lbl.setMinimumWidth(1)
        self.lbl_time_rem.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_time_rem.setMinimumWidth(1)
        self.lbl_time_rem.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Safety net: if the maximized window ever ends up bigger than the screen
        # anyway, re-maximize it (what you did by hand with un-/re-maximize).
        self._fit_fix_size = None
        self._fit_timer = QTimer(self)
        self._fit_timer.timeout.connect(self._check_fits_screen)
        self._fit_timer.start(700)

    def _check_fits_screen(self):
        try:
            if not self.isMaximized() or self.isFullScreen():
                self._fit_fix_size = None
                return
            scr = self.screen()
            if scr is None:
                return
            avail = scr.availableGeometry()
            sz = self.size()
            if sz.width() <= avail.width() + 2 and sz.height() <= avail.height() + 2:
                self._fit_fix_size = None
                return
            key = (sz.width(), sz.height())
            if self._fit_fix_size == key:      # already tried at this size - don't loop
                return
            self._fit_fix_size = key
            logging.debug(f"Window {key} exceeds screen {avail.width()}x{avail.height()} - re-maximizing")
            self.setMinimumSize(min(self.minimumWidth(), avail.width()),
                                min(self.minimumHeight(), avail.height()))
            self.showNormal()
            self.showMaximized()
        except Exception as e:
            logging.debug(f"fit-to-screen check failed: {e}")

    def _lock_window_size(self):
        # Set minimum size to current size to prevent content-driven shrinking
        # but don't set maximum so user can still resize manually.
        # Never larger than the screen, otherwise a maximize could not fit.
        scr = self.screen()
        if scr is not None:
            av = scr.availableGeometry()
            self.setMinimumSize(min(self.width(), av.width()), min(self.height(), av.height()))
            return
        self.setMinimumSize(self.size())
        # Fix label widths to prevent horizontal shifts from long track names
        #self.lbl_now_playing.setFixedWidth(self.lbl_now_playing.width())
        #self.lbl_next_playing.setFixedWidth(self.lbl_next_playing.width())
        #self.lbl_time_rem.setFixedWidth(80)

    def _show_waveform_for_path(self, path):
        if not path or not os.path.exists(path):
            self.quickfire_waveform.set_peaks([])
            return
        if path in self._waveform_cache:
            self.quickfire_waveform.set_peaks(self._waveform_cache[path])
            return
        self.quickfire_waveform.set_peaks([])

        def worker():
            peaks = compute_waveform_peaks(path)
            self.waveform_bridge.peaks_ready.emit(path, peaks)

        threading.Thread(target=worker, daemon=True).start()

    def _load_waveform_for_current_quickfire(self):
        # While something is playing (or about to play), the waveform
        # stays locked to that file regardless of dropdown selection.
        if self._quickfire_playing_path is not None:
            return
        if self.combo_quickfire.count() == 0 or not self.combo_quickfire.currentText():
            self.quickfire_waveform.set_peaks([])
            self.quickfire_waveform.set_playhead(-1)
            return
        filename = self.combo_quickfire.currentText()
        path = os.path.join(self.quickfire_path, filename)
        self._show_waveform_for_path(path)

    def _on_waveform_ready(self, path, peaks):
        self._waveform_cache[path] = peaks
        if self._quickfire_playing_path is not None:
            target_path = self._quickfire_playing_path
        elif self.combo_quickfire.count() > 0:
            target_path = os.path.join(self.quickfire_path, self.combo_quickfire.currentText())
        else:
            target_path = None
        if path == target_path:
            self.quickfire_waveform.set_peaks(peaks)
    def _set_active_video_for_waveform(self, item):
        self._active_video_item = item
        if not self.chk_visuals_waveform.isChecked():
            return
        if item is None:
            self.visuals_waveform.empty_message = "No video selected"
            self.visuals_waveform.set_peaks([])
            self.visuals_waveform.set_playhead(-1)
            return
        self._show_visuals_waveform(item.path)

    def _on_canvas_selection_for_video_waveform(self):
        try:
            sel = self.shared_scene.selectedItems()
        except RuntimeError:
            return
        for it in sel:
            if isinstance(it, DraggableVideo):
                self._set_active_video_for_waveform(it)
                return

    def _update_quickfire_playhead(self):
        if not hasattr(self, 'quickfire_waveform'):
            return
        playing = self.quickfire_player.is_playing()
        engine_frac = None
        e = self.qf_engine
        if e is not None and e.active:
            ep, ef = e.playback_state()
            if ep:
                playing, engine_frac = True, ef
        if playing:
            if engine_frac is not None:
                frac = engine_frac
            else:
                length = self.quickfire_player.get_length()
                t = self.quickfire_player.get_time() + 80  # compensate for directsound output latency
                frac = (t / length) if length and length > 0 else 0
                frac = min(1.0, frac)
            self.quickfire_waveform.set_playhead(frac)
            self.btn_quickfire_fire.setStyleSheet(
                "background-color: #b71c1c; color: white; font-weight: bold;")
            self._quickfire_was_playing = True
        else:
            self.quickfire_waveform.set_playhead(-1)
            self.btn_quickfire_fire.setStyleSheet(
                "background-color: #555; color: #ccc; font-weight: bold;")
            # Only unlock on a real playing->stopped transition — not during
            # the brief window between firing and play() actually starting.
            if self._quickfire_was_playing and self._quickfire_playing_path is not None:
                self._quickfire_playing_path = None
                self._load_waveform_for_current_quickfire()
            self._quickfire_was_playing = False

    def _resolve_music_item_path(self, item):
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            return path
        b = self.scene_path if self.chk_use_scene_music.isChecked() else self.txt_improv_music_path.text()
        cat = self.combo_improv_folders.currentText()
        p = os.path.join(b, "music") if self.chk_use_scene_music.isChecked() else (
            b if cat == "--- Root Folder ---" else os.path.join(b, cat))
        return os.path.join(p, item.text())

    def _resolve_sfx_item_path(self, item):
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole) or os.path.join(self.scene_path, "sfx", item.text())

    def _resolve_visual_item_path(self, item):
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            return path
        return os.path.join(self.scene_path, "visuals", item.text())

    def on_music_waveform_toggled(self, checked):
        self.music_waveform.setVisible(checked)
        if checked:
            if self._music_loaded_path and self.music_player.is_playing():
                self._show_music_waveform(self._music_loaded_path)
            else:
                self.music_waveform.set_peaks([])
                self.music_waveform.set_playhead(-1)

    def on_sfx_waveform_toggled(self, checked):
        self.sfx_waveform.setVisible(checked)
        if checked:
            if self._sfx_loaded_path and self.sfx_player.is_playing():
                self._show_sfx_waveform(self._sfx_loaded_path)
            else:
                self.sfx_waveform.set_peaks([])
                self.sfx_waveform.set_playhead(-1)

    def on_visuals_waveform_toggled(self, checked):
        self.visuals_waveform.setVisible(checked)
        if checked:
            path = self._resolve_visual_item_path(self.visuals_list.currentItem())
            self._show_visuals_waveform(path)



    def _show_music_waveform(self, path):
        self._music_waveform_shown_path = path
        if not path or not os.path.exists(path):
            self.music_waveform.set_peaks([])
            return
        if path in self._waveform_cache:
            self.music_waveform.set_peaks(self._waveform_cache[path])
            return
        self.music_waveform.set_peaks([])

        def worker():
            peaks = compute_waveform_peaks(path)
            self.music_waveform_bridge.peaks_ready.emit(path, peaks)

        threading.Thread(target=worker, daemon=True).start()

    def _on_music_waveform_ready(self, path, peaks):
        self._waveform_cache[path] = peaks
        if path == self._music_waveform_shown_path:
            self.music_waveform.set_peaks(peaks)



    def _show_sfx_waveform(self, path):
        self._sfx_waveform_shown_path = path
        if not path or not os.path.exists(path):
            self.sfx_waveform.set_peaks([])
            return
        if path in self._waveform_cache:
            self.sfx_waveform.set_peaks(self._waveform_cache[path])
            return
        self.sfx_waveform.set_peaks([])

        def worker():
            peaks = compute_waveform_peaks(path)
            self.sfx_waveform_bridge.peaks_ready.emit(path, peaks)

        threading.Thread(target=worker, daemon=True).start()

    def _on_sfx_waveform_ready(self, path, peaks):
        self._waveform_cache[path] = peaks
        if path == self._sfx_waveform_shown_path:
            self.sfx_waveform.set_peaks(peaks)

    def _on_visuals_selection_changed_for_waveform(self, current, previous):
        if not self.chk_visuals_waveform.isChecked():
            return
        self._active_video_item = None
        path = self._resolve_visual_item_path(current)
        self._show_visuals_waveform(path)

    def _show_visuals_waveform(self, path):
        self._visuals_waveform_shown_path = path
        if not path or not os.path.exists(path):
            self.visuals_waveform.empty_message = "No video selected"
            self.visuals_waveform.set_peaks([])
            return
        video_exts = {'mp4', 'mkv', 'avi', 'mov', 'wmv'}
        ext = os.path.splitext(path)[1].lower().lstrip('.')
        if ext not in video_exts:
            self.visuals_waveform.empty_message = "No audio detected"
            self.visuals_waveform.set_peaks([])
            return
        if path in self._waveform_cache:
            peaks = self._waveform_cache[path]
            self.visuals_waveform.empty_message = "No audio detected" if not peaks else "No waveform"
            self.visuals_waveform.set_peaks(peaks)
            return
        self.visuals_waveform.empty_message = "Checking for audio..."
        self.visuals_waveform.set_peaks([])

        def worker():
            peaks = compute_waveform_peaks(path)
            self.visuals_waveform_bridge.peaks_ready.emit(path, peaks)

        threading.Thread(target=worker, daemon=True).start()

    def _on_visuals_waveform_ready(self, path, peaks):
        self._waveform_cache[path] = peaks
        if path == self._visuals_waveform_shown_path:
            self.visuals_waveform.empty_message = "No audio detected" if not peaks else "No waveform"
            self.visuals_waveform.set_peaks(peaks)

    def _update_music_sfx_waveform_playheads(self):
        if hasattr(self, 'music_waveform') and self.chk_music_waveform.isChecked():
            if self.music_player.is_playing() and self._music_waveform_shown_path == self._music_loaded_path:
                length = self.music_player.get_length()
                t = self.music_player.get_time()
                frac = min(1.0, t / length) if length and length > 0 else 0
                self.music_waveform.set_playhead(frac)
            else:
                self.music_waveform.set_playhead(-1)
        if hasattr(self, 'sfx_waveform') and self.chk_sfx_waveform.isChecked():
            if self.sfx_player.is_playing() and self._sfx_waveform_shown_path == self._sfx_loaded_path:
                length = self.sfx_player.get_length()
                t = self.sfx_player.get_time()
                frac = min(1.0, t / length) if length and length > 0 else 0
                self.sfx_waveform.set_playhead(frac)
            else:
                self.sfx_waveform.set_playhead(-1)
        if hasattr(self, 'visuals_waveform') and self.chk_visuals_waveform.isChecked():
            item = self._active_video_item
            valid = False
            if item is not None:
                try:
                    valid = item.scene() is not None
                except RuntimeError:
                    valid = False
            if valid and self._visuals_waveform_shown_path == item.path:
                length = item.player.get_length()
                t = item.player.get_time()
                frac = min(1.0, t / length) if length and length > 0 else 0
                self.visuals_waveform.set_playhead(frac)
            else:
                self.visuals_waveform.set_playhead(-1)
                if item is not None and not valid:
                    self._active_video_item = None

    def on_quickfire_toggled(self, checked):
        self.quickfire_enabled = checked
        self.quickfire_container.setVisible(checked)
        if checked and self.quickfire_path:
            self.populate_quickfire_combo()
        self._qf_engine_sync(explicit=True)
        if not self.is_loading:
            self.save_scene_settings()

    def browse_quickfire_path(self):
        p = QFileDialog.getExistingDirectory(
            self, "Select Quick Fire SFX Folder", self.quickfire_path)
        if p:
            self.quickfire_path = p
            self.populate_quickfire_combo()
            self.save_config()
            self.save_scene_settings()

    def populate_quickfire_combo(self):
        self.combo_quickfire.blockSignals(True)
        self.combo_quickfire.clear()
        exts = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.mp4', '.mkv', '.mka', '.ac3', '.eac3'}
        if self.quickfire_path and os.path.exists(self.quickfire_path):
            files = sorted([
                f for f in os.listdir(self.quickfire_path)
                if os.path.splitext(f)[1].lower() in exts
            ])
            self.combo_quickfire.addItems(files)
        self.combo_quickfire.blockSignals(False)
        self._load_waveform_for_current_quickfire()
        self._qf_engine_sync()

    def _on_quickfire_volume_changed(self, v):
        # own slider, independent of the SFX slider; takes effect immediately on a sound that is playing
        try:
            self.quickfire_player.audio_set_volume(int(v))
        except Exception:
            pass
        try:
            if self.qf_engine is not None:
                self.qf_engine.set_gain(int(v) / 100.0)
        except Exception:
            pass
        self.save_scene_settings()

    # ===== optional fast Quick Fire engine =====
    def _qf_engine_want(self):
        return bool(self.fast_quickfire and self.quickfire_enabled and QuickFireEngine is not None)

    def _qf_engine_sync(self, explicit=False):
        # Opens / closes the engine to match the settings. Opening the audio output can take a moment, so it
        # happens on a helper thread: the window never waits for it.
        try:
            e = self.qf_engine
            if self._qf_engine_want():
                if e is None:
                    e = self.qf_engine = QuickFireEngine(sd, _find_ffmpeg(), notify=self._qf_bridge.changed.emit)
                    e.set_gain(self.quickfire_vol_slider.value() / 100.0)
                if e.active:
                    self._qf_preload_folder()
                elif not e.opening and (explicit or e.can_retry_open()):
                    threading.Thread(target=e.open, name="qf-open", daemon=True).start()
            elif e is not None and (e.active or e.opening):
                e.begin_close()
            self._qf_update_fire_button()
        except Exception as ex:
            logging.warning(f"Quick Fire engine sync failed: {ex}")

    def _qf_folder_paths(self):
        names = [self.combo_quickfire.itemText(i) for i in range(self.combo_quickfire.count())]
        if not names or not self.quickfire_path:
            return []
        cur = max(0, self.combo_quickfire.currentIndex())
        names = names[cur:] + names[:cur]                # the sound you will fire next is decoded first
        return [os.path.join(self.quickfire_path, n) for n in names]

    def _qf_preload_folder(self):
        e = self.qf_engine
        if e is None or not e.active:
            return
        paths = self._qf_folder_paths()
        e.retain(paths)
        e.preload(paths)

    def _on_qf_engine_event(self, kind):
        try:
            e = self.qf_engine
            if kind == "opened" and e is not None and e.active:
                e.set_gain(self.quickfire_vol_slider.value() / 100.0)
                self._qf_preload_folder()
            self._qf_update_fire_button()
        except Exception as ex:
            logging.warning(f"Quick Fire engine event failed: {ex}")

    def _qf_current_path(self):
        if self.combo_quickfire.count() == 0 or not self.combo_quickfire.currentText():
            return None
        return os.path.join(self.quickfire_path, self.combo_quickfire.currentText())

    def _qf_update_fire_button(self):
        # "⚡ FIRE" = the selected sound is preloaded and the fast engine will play it; "🔴 FIRE" = VLC as before
        try:
            e = self.qf_engine
            cur = self._qf_current_path()
            fast = bool(self._qf_engine_want() and e is not None and e.active and cur and e.is_ready(cur))
            self.btn_quickfire_fire.setText("⚡ FIRE" if fast else "🔴 FIRE")
            if not self._qf_engine_want():
                tip = ""
            elif e is None or not e.active:
                tip = "Fast engine: " + (e.describe() if e is not None else "starting") + " (VLC is used meanwhile)"
            elif fast:
                tip = f"Fast engine: this sound is preloaded and starts at once. {e.describe()}"
            else:
                reason = e.failed_reason(cur) if cur else None
                tip = "Fast engine: " + (f"this sound uses VLC ({reason})" if reason else "this sound is still loading (VLC is used until it is ready)")
            self.btn_quickfire_fire.setToolTip(tip)
        except Exception:
            pass

    def _qf_status_text(self):
        if QuickFireEngine is None:
            return "Not available: quickfire_engine.py was not found next to the program."
        if not self.fast_quickfire:
            return "Off. Quick Fire plays through VLC, as before."
        if not self.quickfire_enabled:
            return "On. It starts when you tick ⚡ Quick Fire in the main window."
        e = self.qf_engine
        if e is None:
            return "Starting..."
        txt = e.describe()
        if e.active:
            txt += f"\nSounds ready: {e.ready_count()}"
            if e.pending_count():
                txt += f", loading: {e.pending_count()}"
            if e.failed_count():
                txt += f", will use VLC: {e.failed_count()}"
        return txt

    def set_fast_quickfire(self, on):
        self.fast_quickfire = bool(on)
        self.save_config()
        self._qf_engine_sync(explicit=True)

    def stop_quickfire(self, *args):
        try:
            if self.qf_engine is not None:
                self.qf_engine.stop_all()
        except Exception:
            pass
        try:
            self.quickfire_player.stop()
        except Exception:
            pass

    def _qf_try_engine(self, path):
        # True = the fast engine started the sound. False = caller plays it through VLC.
        try:
            e = self.qf_engine
            if e is None or not self._qf_engine_want():
                return False
            if not e.active:
                self._qf_engine_sync()               # e.g. the device was lost: try to bring it back for next time
                return False
            if not e.is_ready(path):
                e.preload([path], first=True)        # not decoded yet: VLC this time, instant next time
                return False
            if not e.fire(path, self.quickfire_vol_slider.value() / 100.0):
                return False
            try:
                self.quickfire_player.stop()         # a sound that fell back to VLC must not keep playing
            except Exception:
                pass
            self._quickfire_playing_path = path
            self._show_waveform_for_path(path)
            self.log_audio_event("quickfire", os.path.basename(path), path)
            return True
        except Exception as ex:
            logging.warning(f"Quick Fire engine fire failed, using VLC: {ex}")
            return False

    def _fire_quickfire_path(self, path):
        # Plays one quickfire file. Does not touch the quickfire dropdown, so it
        # can also be used for files that belong to a scene that isn't active.
        if self._qf_try_engine(path):
            return
        if self.qf_engine is not None:
            self.qf_engine.stop_all()                # the new (VLC) sound replaces one the engine may still be playing
        _path = path

        def _play():
            m = self.vlc_quickfire.media_new(_path)
            self.quickfire_player.set_media(m)
            self.quickfire_player.play()
            self.log_audio_event("quickfire", os.path.basename(_path), _path)
            QTimer.singleShot(100, lambda: self.quickfire_player.audio_set_volume(
                self.quickfire_vol_slider.value()))

        self._quickfire_playing_path = path
        self._show_waveform_for_path(path)
        QTimer.singleShot(50, _play)

    def fire_quickfire_sfx(self):
        if self.combo_quickfire.count() == 0:
            return
        filename = self.combo_quickfire.currentText()
        if not filename:
            return
        path = os.path.join(self.quickfire_path, filename)
        if not os.path.exists(path):
            print(f"[QF] File not found: {path}")
            return
        self._fire_quickfire_path(path)
        current = self.combo_quickfire.currentIndex()
        if current < self.combo_quickfire.count() - 1:
            self.combo_quickfire.setCurrentIndex(current + 1)

    def stop_music_only(self):
        def after_stop():
            self.music_player.stop()
            self.music_player.audio_set_volume(self.music_vol_slider.value())
        self.fade_music_volume(0, 1000, after_stop)
        self._now_playing_name = None
        self.lbl_now_playing.setText("Now: ---")

    def toggle_music_pause(self):
        if self.music_player.is_playing():
            self.fade_music_volume(0, 500, self.music_player.pause)
        elif self.music_player.get_state() == vlc.State.Paused:
            self.music_player.play()
            QTimer.singleShot(50, lambda: self.music_player.audio_set_volume(
                self.music_vol_slider.value()))
        else:
            # Stopped — restart
            self.play_music()

    def toggle_sfx_pause(self):
        if self.sfx_player.is_playing():
            self.sfx_player.pause()
        elif self.sfx_player.get_state() == vlc.State.Paused:
            self.sfx_player.play()
            QTimer.singleShot(50, lambda: self.sfx_player.audio_set_volume(
                self.sfx_vol_slider.value()))
        else:
            # Stopped — restart
            self.play_sfx()

    def toggle_break_mode(self):
        if not self.break_mode:
            self._enter_break_mode()
        else:
            self._exit_break_mode()

    def _enter_break_mode(self):
        self.break_mode = True
        self.btn_break.setText("▶ RESUME")
        self.btn_break.setStyleSheet("background-color: #e65100; color: white; font-weight: bold;")

        # Save current state
        self._pre_break_music_track = (
            self.music_list.currentItem().text() if self.music_list.currentItem() else None)
        self._pre_break_music_pos = self.music_player.get_time()
        self._pre_break_layout = (
            self.layout_list.currentItem().text() if self.layout_list.currentItem() else None)

        # Pause recording and add marker
        if self.recorder.is_recording and not self.recorder.is_paused:
            self._break_paused_recording = True
            self.auto_mark_recording("--- BREAK START ---")
            # Also log wall-clock time for video sync
            self._markers.append({
                "event": "BREAK_START",
                "datetime": datetime.datetime.now().isoformat(timespec='seconds'),
                "elapsed_seconds": self.recorder.elapsed,
                "chunk": os.path.basename(self.recorder.chunk_files[-1]) if self.recorder.chunk_files else "",
                "note": "Break started"
            })
            self._save_markers()
            self.recorder.pause()
        else:
            self._break_paused_recording = False

        # Stop audio cleanly without the delayed volume restore
        self.music_player.audio_set_volume(0)
        self.music_player.stop()
        self.sfx_player.stop()
        self._now_playing_name = None
        self.lbl_now_playing.setText("Now: ---")
        self.lbl_sfx_playing.setText("SFX: ---")

        # Clear canvas
        items_to_remove = [i for i in self.shared_scene.items()
                           if isinstance(i, (DraggableImage, DraggableVideo, BlankingRect))]
        for i in items_to_remove:
            if isinstance(i, DraggableVideo):
                i.timer.stop();
                i.player.stop();
                i.player.release()
            self.shared_scene.removeItem(i)

        # Load break images
        self._break_images = []
        if self.break_image_folder and os.path.exists(self.break_image_folder):
            exts = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
            self._break_images = sorted([
                os.path.join(self.break_image_folder, f)
                for f in os.listdir(self.break_image_folder)
                if os.path.splitext(f)[1].lower() in exts
            ])
        if self._break_images:
            self._break_slide_index = random.randrange(len(self._break_images))
            self._show_break_slide(self._break_slide_index)
            self._break_slideshow_timer.start(10000)

        # Play break music — delayed so directsound is ready
        if self.break_music_folder and os.path.exists(self.break_music_folder):
            music_files = sorted([
                f for f in os.listdir(self.break_music_folder)
                if os.path.splitext(f)[1].lower() in {'.mp3', '.flac', '.wav', '.ogg', '.m4a'}
            ])
            if music_files:
                pick = random.choice(music_files)
                path = os.path.join(self.break_music_folder, pick)
                m = self.vlc_music.media_new(path)
                self.music_player.set_media(m)
                self.music_player.play()
                QTimer.singleShot(100, lambda: self.music_player.audio_set_volume(
                    self.music_vol_slider.value()))

    def _show_break_slide(self, index):
        # Remove previous break slide image only
        for i in self.shared_scene.items():
            if isinstance(i, DraggableImage) and getattr(i, '_is_break_slide', False):
                self.shared_scene.removeItem(i)
        if not self._break_images:
            return
        path = self._break_images[index % len(self._break_images)]
        pix = QPixmap(path)
        if pix.isNull():
            return
        # Scale to fill stage
        pix = pix.scaled(STAGE_W, STAGE_H,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        item = DraggableImage(pix, os.path.basename(path), path)
        item._is_break_slide = True
        item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, False)
        item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable, False)
        # Center on stage
        bounds = item.boundingRect()
        item.setPos(STAGE_X + (STAGE_W - bounds.width()) / 2,
                    STAGE_Y + (STAGE_H - bounds.height()) / 2)
        item.setZValue(1)
        self.shared_scene.addItem(item)

    def _break_next_slide(self):
        if not self._break_images:
            return
        # Pick a random index different from the current one
        if len(self._break_images) > 1:
            choices = [i for i in range(len(self._break_images)) if i != self._break_slide_index]
            self._break_slide_index = random.choice(choices)
        self._show_break_slide(self._break_slide_index)

    def _exit_break_mode(self):
        self.break_mode = False
        self.btn_break.setText("☕ BREAK")
        self.btn_break.setStyleSheet("background-color: #4a148c; color: white; font-weight: bold;")
        self.btn_break.setChecked(False)

        # Stop slideshow
        self._break_slideshow_timer.stop()
        for i in list(self.shared_scene.items()):
            if isinstance(i, DraggableImage) and getattr(i, '_is_break_slide', False):
                self.shared_scene.removeItem(i)

        # Stop break music cleanly without delayed volume restore
        self.music_player.audio_set_volume(0)
        self.music_player.stop()
        self.sfx_player.stop()
        self._now_playing_name = None
        self.lbl_now_playing.setText("Now: ---")
        self.lbl_sfx_playing.setText("SFX: ---")

        # Resume recording and add marker
        if getattr(self, '_break_paused_recording', False) and self.recorder.is_recording:
            self.recorder.resume()
            self._markers.append({
                "event": "BREAK_END",
                "datetime": datetime.datetime.now().isoformat(timespec='seconds'),
                "elapsed_seconds": self.recorder.elapsed,
                "chunk": os.path.basename(self.recorder.chunk_files[-1]) if self.recorder.chunk_files else "",
                "note": "Break ended"
            })
            self._save_markers()
            QTimer.singleShot(100, lambda: self.auto_mark_recording("--- BREAK END ---"))

        # Restore canvas
        if self._pre_break_layout:
            QTimer.singleShot(50, lambda: self.load_layout_with_fade(self._pre_break_layout))

        # Restore music — delay so directsound is ready after stop
        if self._pre_break_music_track:
            for i in range(self.music_list.count()):
                if self.music_list.item(i).text() == self._pre_break_music_track:
                    self.music_list.setCurrentRow(i)
                    break
            QTimer.singleShot(300, self.play_music)

    def configure_break_folders(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Break Mode Settings")
        dlg.setMinimumWidth(500)
        dlg.setStyleSheet(DARK_STYLE)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Break Music Folder:"))
        row1 = QHBoxLayout()
        txt_music = QLineEdit(self.break_music_folder)
        btn_m = QPushButton("...");
        btn_m.setFixedWidth(30)
        btn_m.clicked.connect(lambda: txt_music.setText(
            QFileDialog.getExistingDirectory(dlg, "Break Music Folder", txt_music.text()) or txt_music.text()))
        row1.addWidget(txt_music);
        row1.addWidget(btn_m)
        layout.addLayout(row1)
        layout.addWidget(QLabel("Break Image Folder:"))
        row2 = QHBoxLayout()
        txt_img = QLineEdit(self.break_image_folder)
        btn_i = QPushButton("...");
        btn_i.setFixedWidth(30)
        btn_i.clicked.connect(lambda: txt_img.setText(
            QFileDialog.getExistingDirectory(dlg, "Break Image Folder", txt_img.text()) or txt_img.text()))
        row2.addWidget(txt_img);
        row2.addWidget(btn_i)
        layout.addLayout(row2)
        btns = QHBoxLayout()
        ok = QPushButton("Save");
        ok.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        ok.clicked.connect(dlg.accept)
        cancel = QPushButton("Cancel");
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok);
        btns.addWidget(cancel)
        layout.addLayout(btns)
        if dlg.exec():
            self.break_music_folder = txt_music.text().strip()
            self.break_image_folder = txt_img.text().strip()
            self.save_config()

    def set_annotation_mode(self, mode):
        # Toggle off if already active in same mode
        if self.annotation_active and self.annotation_overlay and self.annotation_overlay.draw_mode == mode:
            self.stop_annotation()
            return

        self.annotation_active = True
        if not self.annotation_overlay:
            self.annotation_overlay = AnnotationOverlay(STAGE_W, STAGE_H)
            self.shared_scene.addItem(self.annotation_overlay)

        self.annotation_overlay.draw_mode = mode
        self.annotation_overlay.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, False)
        self.annotation_overlay.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)  # ← re-enable on activation
        self.annotation_overlay.setZValue(999998)  # ← add this
        self.shared_scene.clearSelection()  # ← add this

        # Make overlay intercept mouse events
        self.preview_view.setDragMode(QGraphicsView.DragMode.NoDrag)
        for item in self.shared_scene.items():
            if not isinstance(item, AnnotationOverlay):
                item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, False)
                item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable, False)

        self.annotation_overlay.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable, False)

        # Update button styles
        active = "background-color: #c62828; color: white; font-weight: bold;"
        inactive = INACTIVE_BTN_STYLE
        self.btn_annotate_brush.setStyleSheet(active if mode == "brush" else inactive)
        self.btn_annotate_rect.setStyleSheet(active if mode == "rect" else inactive)
        self.btn_annotate_brush.setChecked(mode == "brush")
        self.btn_annotate_rect.setChecked(mode == "rect")
        self._update_fold_buttons()

    def stop_annotation(self):
        self.annotation_active = False
        # Remove overlay from intercepting mouse events
        if self.annotation_overlay:
            self.annotation_overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        # Restore interactivity to scene items
        for item in self.shared_scene.items():
            if isinstance(item, (DraggableImage, DraggableVideo)):
                item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, True)
                item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable, True)
            elif isinstance(item, BlankingRect):
                item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, True)
                item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.preview_view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.btn_annotate_brush.setChecked(False)
        self.btn_annotate_rect.setChecked(False)
        self.btn_annotate_brush.setStyleSheet(INACTIVE_BTN_STYLE)
        self.btn_annotate_rect.setStyleSheet(INACTIVE_BTN_STYLE)
        self._update_fold_buttons()

    def clear_annotations(self):
        if self.annotation_overlay:
            self.annotation_overlay.clear()

    def toggle_annotation_brush(self):
        self.set_annotation_mode("brush")

    def on_sfx_source_toggled(self, checked):
        if self.is_loading: return
        self.improv_sfx_container.setVisible(not checked)
        if not checked:
            self.folder_mode_orders["sfx"] = [
                self.sfx_list.item(i).text() for i in range(self.sfx_list.count())]
            orders = self._capture_current_orders()
        else:
            orders = self._capture_current_orders()
            orders["sfx"] = self.folder_mode_orders["sfx"]
        self.load_assets(orders)
        self.save_scene_settings()

    def populate_improv_sfx_categories(self):
        self.combo_improv_sfx_folders.blockSignals(True)
        self.combo_improv_sfx_folders.clear()
        path = self.txt_improv_sfx_path.text()
        if os.path.exists(path):
            dirs = sorted([d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))])
            self.combo_improv_sfx_folders.addItem("--- Root Folder ---")
            self.combo_improv_sfx_folders.addItems(dirs)
        self.combo_improv_sfx_folders.blockSignals(False)

    def on_improv_sfx_folder_changed(self, text):
        if self.is_loading: return
        orders = self._capture_current_orders()
        self.load_assets(orders)
        self.save_scene_settings()


    def browse_improv_sfx_path(self):
        p = QFileDialog.getExistingDirectory(self, "Select SFX Folder", self.txt_improv_sfx_path.text())
        if p:
            self.txt_improv_sfx_path.setText(p)
            self.populate_improv_sfx_categories()
            self.load_assets(self._capture_current_orders())
            self.save_config()

    def on_visuals_source_toggled(self, checked):
        if self.is_loading: return
        self.improv_visuals_container.setVisible(not checked)
        if not checked:
            self.folder_mode_orders["visuals"] = [
                self.visuals_list.item(i).text() for i in range(self.visuals_list.count())]
            orders = self._capture_current_orders()
        else:
            orders = self._capture_current_orders()
            orders["visuals"] = self.folder_mode_orders["visuals"]
        self.load_assets(orders)
        self.save_scene_settings()

    def _capture_current_orders(self):
        return {
            "music": [self.music_list.item(i).text() for i in range(self.music_list.count())],
            "sfx": [self.sfx_list.item(i).text() for i in range(self.sfx_list.count())],
            "visuals": [self.visuals_list.item(i).text() for i in range(self.visuals_list.count())],
            "layouts": [self.layout_list.item(i).text() for i in range(self.layout_list.count())]
        }
    def populate_improv_visuals_categories(self):
        self.combo_improv_visuals_folders.blockSignals(True)
        self.combo_improv_visuals_folders.clear()
        path = self.txt_improv_visuals_path.text()
        if os.path.exists(path):
            dirs = sorted([d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))])
            self.combo_improv_visuals_folders.addItem("--- Root Folder ---")
            self.combo_improv_visuals_folders.addItems(dirs)
        self.combo_improv_visuals_folders.blockSignals(False)

    def on_improv_visuals_folder_changed(self, text):
        if self.is_loading: return
        orders = self._capture_current_orders()
        self.load_assets(orders)
        self.save_scene_settings()

    def browse_improv_visuals_path(self):
        p = QFileDialog.getExistingDirectory(self, "Select Visuals Folder", self.txt_improv_visuals_path.text())
        if p:
            self.txt_improv_visuals_path.setText(p)
            self.populate_improv_visuals_categories()
            self.load_assets(self._capture_current_orders())
            self.save_config()

    def add_external_assets(self, list_type):
        default_dir = os.path.join(self.scene_path, list_type) if self.scene_path and os.path.exists(
            os.path.join(self.scene_path, list_type)) else ""
        files, _ = QFileDialog.getOpenFileNames(self, f"Add Linked {list_type.upper()}", default_dir, "All Files (*.*)")
        if files:
            for f in files: self.linked_assets[list_type][os.path.basename(f)] = f
            self.save_scene_settings()
            self.load_assets(self._capture_current_orders())

    # ===== "Link to..." : spread content to other scenes =====
    def _list_all_scenes(self):
        out = []
        try:
            for session in sorted(os.listdir(self.root_path), key=str.lower):
                sp = os.path.join(self.root_path, session)
                if not os.path.isdir(sp):
                    continue
                scenes = [(s, os.path.join(sp, s)) for s in sorted(os.listdir(sp), key=str.lower)
                          if os.path.isdir(os.path.join(sp, s))]
                if scenes:
                    out.append((session, scenes))
        except Exception as e:
            logging.warning(f"Listing scenes failed: {e}")
        return out

    def _asset_item_path(self, category, item):
        if category == "music":
            return self._resolve_music_item_path(item)
        if category == "sfx":
            return self._resolve_sfx_item_path(item)
        if category == "visuals":
            return self._resolve_visual_item_path(item)
        if category == "layouts":
            p = os.path.join(self.scene_path, "layouts", item.text())
            if not os.path.exists(p) and item.text() in self.linked_assets['layouts']:
                p = self.linked_assets['layouts'][item.text()]
            return p
        return None

    def link_items_to_scenes(self, list_widget, category, clicked_item):
        items = list_widget.selectedItems()
        if clicked_item not in items:
            items = [clicked_item]
        entries = []
        for it in items:
            p = self._asset_item_path(category, it)
            if p and os.path.exists(p):
                entries.append((os.path.basename(p), p))
        if not entries:
            QMessageBox.warning(self, "Link to", "The file could not be found on disk.")
            return
        here = os.path.normpath(self.scene_path or "")
        sessions = []
        for session, scenes in self._list_all_scenes():
            scenes = [(n, p) for n, p in scenes if os.path.normpath(p) != here]
            if scenes:
                sessions.append((session, scenes))
        if not sessions:
            QMessageBox.information(self, "Link to", "There are no other scenes to link to.")
            return
        what = entries[0][0] if len(entries) == 1 else f"{len(entries)} {category} files"
        dlg = LinkToScenesDialog(self, sessions, f"Link “{what}” to these scenes:")
        if not dlg.exec():
            return
        targets = dlg.chosen_scene_paths()
        linked, skipped, failed = self._link_assets_to_scenes(category, entries, targets)
        msg = f"Linked {linked} item(s) into {len(targets)} scene(s)"
        if skipped:
            msg += f" · {skipped} already there"
        self.lbl_script_status.setText(msg)
        QTimer.singleShot(5000, lambda: self.lbl_script_status.setText("Status: Idle"))
        if failed:
            QMessageBox.warning(self, "Link to",
                                "Could not update these scenes (settings file unreadable or not writable):\n\n"
                                + "\n".join(failed))

    def _link_assets_to_scenes(self, category, entries, scene_paths):
        linked = skipped = 0
        failed = []
        for sp in scene_paths:
            fp = os.path.join(sp, SCENE_SETTINGS_FILE)
            try:
                data = {}
                if os.path.exists(fp):
                    with open(fp, 'r') as f:
                        data = json.load(f)       # unreadable file -> skip, never overwrite
                    if not isinstance(data, dict):
                        raise ValueError("settings file is not an object")
                la = data.get("linked_assets")
                if not isinstance(la, dict):
                    la = {}
                for k in ("music", "sfx", "visuals", "layouts"):
                    if not isinstance(la.get(k), dict):
                        la[k] = {}
                data["linked_assets"] = la
                changed = False
                for name, path in entries:
                    own = os.path.join(sp, category, name)
                    if os.path.exists(own) or os.path.normpath(own) == os.path.normpath(path):
                        skipped += 1          # the scene already owns this file
                        continue
                    if resolve_path(la[category].get(name)) == path:
                        skipped += 1
                        continue
                    la[category][name] = to_portable(path)
                    linked += 1
                    changed = True
                if changed:
                    tmp = fp + ".tmp"
                    with open(tmp, 'w') as f:
                        json.dump(data, f)
                    os.replace(tmp, fp)
            except Exception as e:
                logging.warning(f"Link to: failed for {sp}: {e}")
                failed.append(os.path.basename(sp))
        return linked, skipped, failed

    def show_asset_context_menu(self, pos, list_widget, category):
        it = list_widget.itemAt(pos)
        if it:
            menu = QMenu()
            act_to = QAction("🔗 Link to...", self)
            act_to.triggered.connect(lambda: self.link_items_to_scenes(list_widget, category, it))
            menu.addAction(act_to)
            if it.text() in self.linked_assets[category]:
                act = QAction("🔗 Remove Link (Keep File)", self);
                act.triggered.connect(lambda: self.remove_linked_asset(category, it.text()));
                menu.addAction(act)
            menu.exec(list_widget.mapToGlobal(pos))

    def remove_linked_asset(self, cat, name):
        if name in self.linked_assets[cat]:
            del self.linked_assets[cat][name]
            self.save_scene_settings()
            self.load_assets(self._capture_current_orders())

    def show_scene_context_menu(self, pos):
        it = self.scene_list.itemAt(pos);
        if it:
            menu = QMenu();
            name = it.text()
            for cat in ["music", "sfx", "visuals", "layouts"]:
                act = QAction(f"➕ Add from scene: {cat}", self)
                act.triggered.connect(lambda checked, c=cat, s=name: self.import_assets_from_scene(s, c))
                menu.addAction(act)
            menu.exec(self.scene_list.mapToGlobal(pos))

    def import_assets_from_scene(self, target_scene_name, category):
        if not self.scene_path: return
        target_path = os.path.join(self.root_path, self.current_session, target_scene_name, category)
        if os.path.exists(target_path):
            files = [f for f in os.listdir(target_path) if os.path.isfile(os.path.join(target_path, f))]
            if category == "layouts":
                dest_path = os.path.join(self.scene_path, "layouts")
                os.makedirs(dest_path, exist_ok=True)
                for f in files:
                    src_f = os.path.join(target_path, f)
                    dst_f = os.path.join(dest_path, f)
                    if src_f != dst_f: shutil.copy2(src_f, dst_f)
            else:
                for f in files: self.linked_assets[category][f] = os.path.join(target_path, f)
            self.save_scene_settings()
            self.load_assets(self._capture_current_orders())

    def trigger_next_scene(self):
        c = self.scene_list.count()
        if c > 0:
            idx = (self.scene_list.currentRow() + 1) % c
            self.scene_list.setCurrentRow(idx)
            self.set_active_scene(self.scene_list.item(idx))
            # Clear music selection so new scene starts fresh
            self.music_list.blockSignals(True)
            self.music_list.clearSelection()
            self.music_list.setCurrentRow(-1)
            self.music_list.blockSignals(False)
            self.start_all_master()


    def play_music_track_by_name(self, name):
        for i in range(self.music_list.count()):
            if self.music_list.item(i).text() == name:
                self.music_list.setCurrentRow(i)
                break
        else:
            return
        self.play_music()

    # ===== which music source is active =====
    def _playlist_mode_active(self):
        return (not self.chk_use_scene_music.isChecked()) and self.chk_use_playlist.isChecked()

    def _current_music_key(self):
        # Identity of whatever the music list is showing right now. Orders are
        # remembered PER KEY, so switching source never overwrites another's order.
        if self.chk_use_scene_music.isChecked():
            return "folder"
        if self.chk_use_playlist.isChecked():
            return "playlist"
        return "genre:" + (self.combo_improv_folders.currentText() or "")

    def _music_list_is_playlist(self):
        return self._music_list_key == "playlist" and self._music_list_scene == self.scene_path

    def _remember_music_order(self):
        # Stores the order currently DISPLAYED under the key it was built for
        # (not the key the checkboxes say now), and only for the scene it
        # belongs to. Playlist order is stored inside the playlist itself.
        key = self._music_list_key
        if not key or key == "playlist" or self._music_list_scene != self.scene_path:
            return
        names = [self.music_list.item(i).text() for i in range(self.music_list.count())]
        if names:
            self.music_orders[key] = names

    def _fill_music_list(self, w):
        # Playlist branch of load_assets(). Returns True when it filled the list.
        if not self._playlist_mode_active():
            return False
        for entry in self.music_playlist:
            it = QListWidgetItem(entry["name"])
            it.setData(Qt.ItemDataRole.UserRole, entry["path"])
            w.addItem(it)
        self._music_list_key = "playlist"
        self._music_list_scene = self.scene_path
        return True

    def _update_music_source_ui(self):
        folder = self.chk_use_scene_music.isChecked()
        pl = self.chk_use_playlist.isChecked() and not folder
        self.improv_container.setVisible((not folder) and (not pl))
        self.playlist_container.setVisible(pl)

    def on_music_source_toggled(self, checked):
        if self.is_loading: return
        self._remember_music_order()
        if checked and self.chk_use_playlist.isChecked():
            self.chk_use_playlist.blockSignals(True)
            self.chk_use_playlist.setChecked(False)
            self.chk_use_playlist.blockSignals(False)
        self._update_music_source_ui()
        self.load_assets(self._capture_current_orders())
        self.save_scene_settings()
        self._refresh_music_dialogs()

    def on_playlist_source_toggled(self, checked):
        if self.is_loading: return
        self._remember_music_order()
        if checked and self.chk_use_scene_music.isChecked():
            self.chk_use_scene_music.blockSignals(True)
            self.chk_use_scene_music.setChecked(False)
            self.chk_use_scene_music.blockSignals(False)
        self._update_music_source_ui()
        if checked:
            self.refresh_playlist_source_combo()
        self.load_assets(self._capture_current_orders())
        self.save_scene_settings()
        self._refresh_music_dialogs()

    def on_improv_folder_changed(self, text):
        if self.is_loading: return
        # load_assets() remembers the order of the genre we're leaving first
        self.load_assets(self._capture_current_orders())
        self.save_scene_settings()

    def _restore_music_source_state(self, data, saved_orders):
        # Called from load_scene_settings while is_loading is True.
        raw = data.get("music_orders", {})
        self.music_orders = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, list):
                    self.music_orders[k] = [n for n in v if isinstance(n, str)]
        self.music_playlist_source = resolve_path(str(data.get("music_playlist_source", "") or ""))
        use_pl = bool(data.get("use_playlist", False)) or data.get("improv_category") == PLAYLIST_CATEGORY
        if self.chk_use_scene_music.isChecked():
            use_pl = False
        self.chk_use_playlist.setChecked(use_pl)
        if not use_pl:
            key = "folder" if self.chk_use_scene_music.isChecked() \
                else "genre:" + (self.combo_improv_folders.currentText() or "")
            legacy = saved_orders.get("music") if isinstance(saved_orders, dict) else None
            if key not in self.music_orders and isinstance(legacy, list) and legacy:
                # scenes saved before per-source orders existed
                self.music_orders[key] = [n for n in legacy if isinstance(n, str)]
        self._update_music_source_ui()
        if use_pl:
            self.refresh_playlist_source_combo()

    # ===== playlist dropdown in the music panel =====
    def refresh_playlist_source_combo(self, *args):
        combo = self.combo_playlist_source
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("(working playlist)", None)
        for p in self.list_all_playlists():
            combo.addItem(p["label"], p)
        combo.blockSignals(False)
        self._sync_playlist_combo_selection()

    def _sync_playlist_combo_selection(self):
        combo = self.combo_playlist_source
        idx = 0
        if self.music_playlist_source:
            want = self._path_key(self.music_playlist_source)
            for i in range(1, combo.count()):
                d = combo.itemData(i)
                if d and self._path_key(d["path"]) == want:
                    idx = i
                    break
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _playlist_is_dirty(self):
        # True when the working playlist holds changes that exist in no saved file
        if not self.music_playlist:
            return False
        if not self.music_playlist_source or not os.path.exists(self.music_playlist_source):
            return True
        saved, _ = self.read_playlist_file(self.music_playlist_source)
        cur = [(e["name"], self._path_key(e["path"])) for e in self.music_playlist]
        old = [(e["name"], self._path_key(e["path"])) for e in saved if os.path.exists(e["path"])]
        return cur != old

    def on_playlist_source_selected(self, idx):
        data = self.combo_playlist_source.itemData(idx)
        if not data:
            return
        entries, _ = self.read_playlist_file(data["path"])
        valid = [e for e in entries if os.path.exists(e["path"])]
        missing = len(entries) - len(valid)
        if not valid:
            QMessageBox.warning(self, "Playlist", "None of the tracks in this playlist exist on disk any more.")
            self._sync_playlist_combo_selection()
            return
        if self._playlist_is_dirty():
            ans = QMessageBox.question(
                self, "Load Playlist",
                f"The current playlist has changes that are not saved to a file.\n\n"
                f"Load “{data['name']}” and discard them?")
            if ans != QMessageBox.StandardButton.Yes:
                self._sync_playlist_combo_selection()
                return
        self.music_playlist = []
        self.music_playlist_name = data["name"]
        self.music_playlist_source = data["path"]
        self._playlist_append(valid)
        self._after_playlist_changed()
        if missing:
            self.lbl_script_status.setText(f"Playlist loaded — {missing} missing file(s) skipped")
            QTimer.singleShot(4000, lambda: self.lbl_script_status.setText("Status: Idle"))
        if self._playlist_dialog is not None and self._playlist_dialog.isVisible():
            self._playlist_dialog.refresh_all()

    # ===== playlist data =====
    @staticmethod
    def _path_key(p):
        return os.path.normcase(os.path.normpath(p))

    def _unique_playlist_name(self, name, path, used):
        # Every playlist entry needs a unique display name, because the whole
        # ordering / filter machinery identifies tracks by their list text.
        if name not in used:
            return name
        parent = os.path.basename(os.path.dirname(path)) or "root"
        base = f"{parent} - {name}"
        cand, n = base, 2
        while cand in used:
            cand = f"{base} ({n})"
            n += 1
        return cand

    def _sanitize_playlist(self, items):
        out, used, paths = [], set(), set()
        if not isinstance(items, list):
            return out
        for e in items:
            if not isinstance(e, dict):
                continue
            path = e.get("path")
            if not isinstance(path, str) or not path:
                continue
            key = self._path_key(path)
            if key in paths:
                continue
            name = e.get("name")
            if not isinstance(name, str) or not name:
                name = os.path.basename(path)
            name = self._unique_playlist_name(name, path, used)
            out.append({"name": name, "path": path})
            used.add(name)
            paths.add(key)
        return out

    def _sync_playlist_from_music_list(self):
        # While the playlist IS the music list, a drag in the main list is the
        # source of truth for the order. Only reorders - can never drop entries.
        names = [self.music_list.item(i).text() for i in range(self.music_list.count())]
        by_name = {e["name"]: e for e in self.music_playlist}
        ordered, seen = [], set()
        for n in names:
            if n in by_name and n not in seen:
                ordered.append(by_name[n])
                seen.add(n)
        ordered += [e for e in self.music_playlist if e["name"] not in seen]
        self.music_playlist = ordered

    def _playlist_append(self, entries):
        paths = {self._path_key(e["path"]) for e in self.music_playlist}
        used = {e["name"] for e in self.music_playlist}
        added = 0
        for e in entries:
            path = e.get("path")
            if not path:
                continue
            key = self._path_key(path)
            if key in paths:
                continue
            name = self._unique_playlist_name(e.get("name") or os.path.basename(path), path, used)
            self.music_playlist.append({"name": name, "path": path})
            used.add(name)
            paths.add(key)
            added += 1
        return added

    def _after_playlist_changed(self):
        if self._playlist_mode_active():
            self.load_assets(self._capture_current_orders())
        self.save_scene_settings()
        self._sync_playlist_combo_selection()

    def playlist_add_tracks(self, entries):
        added = self._playlist_append(entries)
        if added:
            self._after_playlist_changed()
        return added, len(entries) - added

    def playlist_remove_tracks(self, names):
        drop = set(names)
        self.music_playlist = [e for e in self.music_playlist if e["name"] not in drop]
        self._after_playlist_changed()

    def playlist_clear(self):
        self.music_playlist = []
        self._after_playlist_changed()

    def playlist_load_entries(self, entries, replace, name=None, source=None):
        if replace:
            self.music_playlist = []
            if name is not None:
                self.music_playlist_name = name
            if source is not None:
                self.music_playlist_source = source
        added = self._playlist_append(entries)
        self._after_playlist_changed()
        return added

    def set_playlist_order(self, names):
        by_name = {e["name"]: e for e in self.music_playlist}
        ordered, seen = [], set()
        for n in names:
            if n in by_name and n not in seen:
                ordered.append(by_name[n])
                seen.add(n)
        ordered += [e for e in self.music_playlist if e["name"] not in seen]
        self.music_playlist = ordered
        if self._music_list_is_playlist():
            self._reorder_list_widget_by_names(self.music_list, [e["name"] for e in ordered])
            self.update_info_labels()
        self.save_scene_settings()

    def set_playlist_active(self, active):
        if self.chk_use_scene_music.isChecked():
            return False
        # Goes through the normal on_playlist_source_toggled path
        self.chk_use_playlist.setChecked(bool(active))
        return True

    # ----- saved playlist files -----
    def _write_playlist_file(self, name, entries, parent=None):
        parent = parent or self
        safe = "".join(c for c in (name or "") if c not in '<>:"/\\|?*').strip().strip(".")
        if not safe:
            QMessageBox.warning(parent, "Playlist", "Please enter a playlist name.")
            return None
        if not entries:
            QMessageBox.warning(parent, "Playlist", "The playlist is empty — nothing to save.")
            return None
        if not self.scene_path or not os.path.isdir(self.scene_path):
            QMessageBox.warning(parent, "Playlist", "No active scene to save the playlist into.")
            return None
        folder = os.path.join(self.scene_path, "playlists")
        target = os.path.join(folder, safe + ".json")
        if os.path.exists(target):
            if QMessageBox.question(parent, "Playlist",
                                    f"“{safe}” already exists in this scene. Overwrite it?"
                                    ) != QMessageBox.StandardButton.Yes:
                return None
        try:
            os.makedirs(folder, exist_ok=True)
            payload = {
                "name": safe,
                "version": 1,
                "saved": datetime.datetime.now().isoformat(timespec='seconds'),
                "tracks": [{"name": e["name"], "path": to_portable(e["path"])} for e in entries]
            }
            tmp = target + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp, target)
        except Exception as e:
            logging.warning(f"Failed to save playlist: {e}")
            QMessageBox.warning(parent, "Playlist", f"Could not save the playlist:\n{e}")
            return None
        return safe

    def save_named_playlist(self, name, parent=None):
        safe = self._write_playlist_file(name, list(self.music_playlist), parent)
        if safe:
            self.music_playlist_name = safe
            self.music_playlist_source = os.path.join(self.scene_path, "playlists", safe + ".json")
            self.save_scene_settings()
            self.refresh_playlist_source_combo()
        return safe

    def save_music_list_as_playlist(self, *args):
        # Folder-mode variant: saves whatever order the main music list has now.
        parent = self._music_manager_dialog or self
        if self.music_list.count() == 0:
            QMessageBox.warning(parent, "Playlist", "The music list is empty.")
            return
        entries = []
        for i in range(self.music_list.count()):
            it = self.music_list.item(i)
            path = self._resolve_music_item_path(it)
            if path:
                entries.append({"name": it.text(), "path": path})
        default = self.music_playlist_name or os.path.basename(self.scene_path)
        name, ok = QInputDialog.getText(parent, "Save as Playlist", "Playlist name:", text=default)
        if not ok:
            return
        safe = self._write_playlist_file(name.strip(), entries, parent)
        if safe:
            QMessageBox.information(parent, "Playlist",
                                    f"Saved “{safe}” ({len(entries)} tracks) in this scene's playlists folder.\n"
                                    "Pick it with “Use Playlist” in the music panel.")

    def list_all_playlists(self):
        found = []
        root = self.root_path
        try:
            if not root or not os.path.isdir(root):
                return found
            for session in sorted(os.listdir(root), key=str.lower):
                sp = os.path.join(root, session)
                if not os.path.isdir(sp):
                    continue
                for scene in sorted(os.listdir(sp), key=str.lower):
                    pdir = os.path.join(sp, scene, "playlists")
                    if not os.path.isdir(pdir):
                        continue
                    for f in sorted(os.listdir(pdir), key=str.lower):
                        if f.lower().endswith(".json"):
                            nm = os.path.splitext(f)[0]
                            found.append({"label": f"{session} / {scene}  —  {nm}",
                                          "path": os.path.join(pdir, f), "name": nm})
        except Exception as e:
            logging.warning(f"Listing playlists failed: {e}")
        return found

    def read_playlist_file(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logging.warning(f"Failed to read playlist {path}: {e}")
            return [], ""
        if isinstance(data, dict):
            tracks = [dict(e, path=resolve_path(e.get("path"))) for e in (data.get("tracks") or [])
                      if isinstance(e, dict) and isinstance(e.get("path"), str)]
            return self._sanitize_playlist(tracks), str(data.get("name", ""))
        return [], ""

    # ===== playing a file that isn't in the main list (dialog preview) =====
    def play_music_file(self, path, name):
        if not path or not os.path.exists(path):
            return

        def swap():
            self.music_player.set_media(self.vlc_music.media_new(path))
            self.music_player.play()
            QTimer.singleShot(50, lambda: self.music_player.audio_set_volume(
                self.music_vol_slider.value()))
            self._now_playing_name = name
            self._now_playing_set_at = datetime.datetime.now()
            self._music_loaded_path = path
            self.log_audio_event("music", name, path)
            if self.chk_music_waveform.isChecked():
                self._show_music_waveform(path)
            self.update_info_labels()

        if self.music_player.is_playing():
            self.fade_music_volume(0, 800, swap)
        else:
            swap()

    # ===== "Now:" label that survives list rebuilds =====
    def _playing_track_name(self):
        name = self._now_playing_name
        if not name:
            return None
        try:
            st = self.music_player.get_state()
        except Exception:
            st = None
        active = st in (vlc.State.Playing, vlc.State.Paused, vlc.State.Opening, vlc.State.Buffering)
        recent = (datetime.datetime.now() - self._now_playing_set_at).total_seconds() < 2.0
        return name if (active or recent) else None

    def update_info_labels(self):
        it = self.music_list.currentItem()
        playing = self._playing_track_name()
        shown = playing or (it.text() if it else None)
        self.lbl_now_playing.setText(f"Now: {shown}" if shown else "Now: ---")
        self.lbl_next_playing.setText(
            f"Next: {self.music_list.item(self.get_next_index()).text()}" if self.music_list.count() > 1 else "Next: ---")

    # ----- dialog plumbing -----
    def _close_music_dialogs(self):
        for d in (self._music_manager_dialog, self._playlist_dialog):
            if d is not None and d.isVisible():
                d.close()

    def _refresh_music_dialogs(self):
        folder_mode = self.chk_use_scene_music.isChecked()
        d1, d2 = self._music_manager_dialog, self._playlist_dialog
        if d1 is not None and d1.isVisible():
            if folder_mode:
                d1.refresh_from_main()
            else:
                d1.close()
        if d2 is not None and d2.isVisible():
            if not folder_mode:
                d2.refresh_all()
            else:
                d2.close()

    def open_music_manager(self):
        if self.chk_use_scene_music.isChecked():
            if self._music_manager_dialog is None:
                self._music_manager_dialog = MusicManagerDialog(self)
            self._music_manager_dialog.open_and_sync()
        else:
            if self._playlist_dialog is None:
                self._playlist_dialog = PlaylistManagerDialog(self)
            self._playlist_dialog.open_and_sync()

    def _on_main_music_order_changed(self, *args):
        if self._music_list_is_playlist():
            self._sync_playlist_from_music_list()
        if self._music_manager_dialog and self._music_manager_dialog.isVisible():
            names = [self.music_list.item(i).text() for i in range(self.music_list.count())]
            self._music_manager_dialog.set_order_from_main(names)
        if self._playlist_dialog is not None and self._playlist_dialog.isVisible():
            self._playlist_dialog.refresh_playlist_from_main()

    def apply_music_order_from_dialog(self, order_names):
        self._reorder_list_widget_by_names(self.music_list, order_names)
        self.update_info_labels()
        self.save_scene_settings()

    def _reorder_list_widget_by_names(self, list_widget, order_names):
        # Reorders EXISTING items in place (never recreates them), so any
        # UserRole data and foreground colors survive untouched.
        current_text = list_widget.currentItem().text() if list_widget.currentItem() else None
        items_by_name = {}
        for i in range(list_widget.count()):
            it = list_widget.item(i)
            items_by_name[it.text()] = it
        final_names = [n for n in order_names if n in items_by_name]
        leftover = [n for n in items_by_name if n not in final_names]
        final_names += leftover
        list_widget.blockSignals(True)
        while list_widget.count() > 0:
            list_widget.takeItem(0)
        for name in final_names:
            list_widget.addItem(items_by_name[name])
        if current_text:
            for i in range(list_widget.count()):
                if list_widget.item(i).text() == current_text:
                    list_widget.setCurrentRow(i)
                    break
        list_widget.blockSignals(False)

    def load_music_filters(self):
        self.music_blacklist = set()
        self.music_whitelist = set()
        self.music_playlist = []
        self.music_playlist_name = ""
        self.music_playlist_source = ""
        if not self.scene_path:
            return
        path = os.path.join(self.scene_path, "filterMusic.json")
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                self.music_blacklist = set(data.get("blacklist", []))
                self.music_whitelist = set(data.get("whitelist", []))
            except Exception as e:
                logging.warning(f"Failed to load music filters: {e}")

    def save_music_filters(self):
        if not self.scene_path or not os.path.exists(self.scene_path):
            return
        payload = {
            "blacklist": sorted(self.music_blacklist),
            "whitelist": sorted(self.music_whitelist)
        }
        try:
            target = os.path.join(self.scene_path, "filterMusic.json")
            tmp = target + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, target)
        except Exception as e:
            logging.warning(f"Failed to save music filters: {e}")

    def add_music_to_blacklist(self, names):
        for n in names:
            self.music_blacklist.add(n)
            self.music_whitelist.discard(n)
        self.save_music_filters()
        self._apply_music_filter_colors()

    def add_music_to_whitelist(self, names):
        for n in names:
            self.music_blacklist.discard(n)      # same as blacklisting: the newest choice wins
            self.music_whitelist.add(n)
        self.save_music_filters()
        self._apply_music_filter_colors()

    def toggle_music_blacklist(self, names):
        # Pressing Blacklist on tracks that are ALL already blacklisted takes it off again;
        # otherwise every selected track gets blacklisted.
        names = list(dict.fromkeys(names))
        if names and all(n in self.music_blacklist for n in names):
            self.remove_music_from_filters(names)
        else:
            self.add_music_to_blacklist(names)

    def toggle_music_whitelist(self, names):
        names = list(dict.fromkeys(names))
        if names and all(n in self.music_whitelist for n in names):
            self.remove_music_from_filters(names)
        else:
            self.add_music_to_whitelist(names)

    def remove_music_from_filters(self, names):
        for n in names:
            self.music_blacklist.discard(n)
            self.music_whitelist.discard(n)
        self.save_music_filters()
        self._apply_music_filter_colors()

    def _apply_music_filter_colors(self):
        for i in range(self.music_list.count()):
            it = self.music_list.item(i)
            name = it.text()
            if name in self.music_blacklist:
                it.setForeground(QColor("#e57373"))
            elif self.music_whitelist and name in self.music_whitelist:
                it.setForeground(QColor("#81c784"))
            elif name in self.linked_assets.get('music', {}):
                it.setForeground(QColor("#4fc3f7"))
            else:
                it.setForeground(QColor("#e0e0e0"))
        # keep an open music dialog in step (top + bottom list), whichever one triggered the change
        for dlg in (getattr(self, "_playlist_dialog", None), getattr(self, "_music_manager_dialog", None)):
            try:
                if dlg is not None:
                    dlg.refresh_colors()
            except Exception as ex:
                logging.warning(f"Could not refresh filter colours in a dialog: {ex}")

    def _is_track_allowed(self, name):
        if name in self.music_blacklist:
            return False
        if self.music_whitelist and name not in self.music_whitelist:
            return False
        return True

    def _first_allowed_index(self):
        for i in range(self.music_list.count()):
            if self._is_track_allowed(self.music_list.item(i).text()):
                return i
        return 0 if self.music_list.count() > 0 else -1

    def get_prev_index(self):
        c, curr = self.music_list.count(), self.music_list.currentRow()
        if c == 0:
            return 0
        allowed = [i for i in range(c) if self._is_track_allowed(self.music_list.item(i).text())] \
            or list(range(c))
        if c <= 1:
            return allowed[0]
        if curr == -1:
            return allowed[-1]
        try:
            pos = allowed.index(curr)
            return allowed[(pos - 1) % len(allowed)]
        except ValueError:
            before = [i for i in allowed if i < curr]
            return before[-1] if before else allowed[-1]

    def toggle_mini_controls(self):
        if not self.mini_controls: self.mini_controls = MiniControlWindow(self)
        if self.mini_controls.isVisible():
            self.mini_controls.hide()
        else:
            self.mini_controls.show()
    def _build_collapsible_sections(self, sidebar, music_wave_row, qf_header):
        # Groups each section's controls into one container ("gate") so a
        # collapse can hide them as a unit without fighting the existing
        # setVisible() logic on the individual widgets inside.
        self._section_lists = {
            "music": self.music_list, "sfx": self.sfx_list,
            "visuals": self.visuals_list, "layouts": self.layout_list}
        groups = {
            "music": [self.music_source_widget, self.improv_container, self.playlist_container,
                      music_wave_row, self.music_waveform],
            "sfx": [self.chk_use_scene_sfx, self.improv_sfx_container, qf_header,
                    self.quickfire_container, self.chk_sfx_waveform, self.sfx_waveform],
            "visuals": [self.chk_use_scene_visuals, self.improv_visuals_container,
                        self.chk_visuals_waveform, self.visuals_waveform],
        }
        for key, items in groups.items():
            try:
                idx = sidebar.indexOf(items[0])
                if idx < 0:
                    continue
                gate = QWidget()
                gate_lay = QVBoxLayout(gate)
                gate_lay.setContentsMargins(0, 0, 0, 0)
                # Insert the gate first, then move items into it, so a failure
                # part-way can never leave widgets orphaned.
                sidebar.insertWidget(idx, gate)
                for it in items:
                    if isinstance(it, QWidget):
                        sidebar.removeWidget(it)
                        gate_lay.addWidget(it)
                    else:
                        sidebar.removeItem(it)
                        gate_lay.addLayout(it)
                self._section_gates[key] = gate
            except Exception as e:
                logging.warning(f"Collapsible section build failed for {key}: {e}")
        self._apply_all_section_collapse()

    def toggle_section(self, key):
        self.collapsed_sections[key] = not self.collapsed_sections.get(key, False)
        self._apply_section_collapse(key)
        self.save_scene_settings()

    def _apply_section_collapse(self, key):
        collapsed = bool(self.collapsed_sections.get(key, False))
        gate = self._section_gates.get(key)
        if gate is not None:
            gate.setVisible(not collapsed)
        lst = self._section_lists.get(key)
        if lst is not None:
            lst.setVisible(not collapsed)
        btn = self.section_collapse_buttons.get(key)
        if btn is not None:
            btn.setText("▶" if collapsed else "▼")

    def _apply_all_section_collapse(self):
        for key in ("music", "sfx", "visuals", "layouts"):
            self._apply_section_collapse(key)

    def open_quick_add(self):
        if not self.scene_path or not os.path.exists(self.scene_path):
            return
        if self._quick_add_dialog is None:
            self._quick_add_dialog = QuickAddDialog(self)
        self._quick_add_dialog.open_and_focus()

    def quick_add_insert_asset(self, name, path):
        self.linked_assets['visuals'][name] = path
        self.save_scene_settings()
        self.load_assets(self._capture_current_orders())
        # Feedback: select the newly added item in the visuals list
        for i in range(self.visuals_list.count()):
            if self.visuals_list.item(i).text() == name:
                self.visuals_list.setCurrentRow(i)
                self.visuals_list.scrollToItem(self.visuals_list.item(i))
                break
    def keyPressEvent(self, event):
        if isinstance(self.focusWidget(), (QLineEdit, QComboBox, QDoubleSpinBox)):
            super().keyPressEvent(event);
            return
        mod, key = event.modifiers(), event.key()
        ctrl = mod & Qt.KeyboardModifier.ControlModifier
        alt = mod & Qt.KeyboardModifier.AltModifier
        shift = mod & Qt.KeyboardModifier.ShiftModifier

        if ctrl:
            if key == Qt.Key.Key_P:
                self.launch_projector()
            elif key == Qt.Key.Key_R:
                self.reload_all()
            elif key == Qt.Key.Key_B:
                self.add_blanking_box_manual()  # ctrl+b
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.start_all_master()
            elif key == Qt.Key.Key_M:
                self.rec_mark()
        elif alt:
            if key == Qt.Key.Key_A:
                if shift:
                    self.clear_annotations()  # alt+shift+a
                else:
                    self.set_annotation_mode("brush")  # alt+a
        elif key == Qt.Key.Key_S and (mod & Qt.KeyboardModifier.ShiftModifier):
            self.fire_quickfire_sfx()
        elif key == Qt.Key.Key_Space:
            if self.music_player.is_playing() or self.sfx_player.is_playing():
                self.stop_all_audio()
            else:
                self.play_all()
        else:
            super().keyPressEvent(event)

    def toggle_freeze(self):
        if not self.is_frozen:
            rect = QRectF(STAGE_X, STAGE_Y, STAGE_W, STAGE_H);
            img = QImage(STAGE_W, STAGE_H, QImage.Format.Format_ARGB32);
            img.fill(Qt.GlobalColor.black);
            p = QPainter(img);
            self.shared_scene.render(p, target=QRectF(img.rect()), source=rect);
            p.end()
            self.freeze_scene = QGraphicsScene(0, 0, STAGE_W, STAGE_H);
            pix = QGraphicsPixmapItem(QPixmap.fromImage(img));
            self.freeze_scene.addItem(pix);
            self.stage_view.setScene(self.freeze_scene);
            self.stage_view.setSceneRect(QRectF())
            self.stage_view.fitInView(self.freeze_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio);
            self.is_frozen = True
            self.btn_freeze.setText("🔄 SYNC PROJECTOR");
            self.btn_freeze.setStyleSheet("background-color: #f57c00; color: white; font-weight: bold; height: 35px;")
        else:
            self.stage_view.setScene(self.shared_scene);
            self.is_frozen = False
            self._fit_stage_view()
            self.btn_freeze.setText("❄️ FREEZE PROJECTOR");
            self.btn_freeze.setStyleSheet("background-color: #455a64; color: white; font-weight: bold; height: 35px;")

    # ===== projector scaling / settings =====
    def _projector_screen(self):
        scr = QApplication.screens()
        primary = QApplication.primaryScreen()
        return next((x for x in scr if x != primary), primary)

    def _projector_screen_text(self):
        try:
            t = self._projector_screen()
            g = t.geometry()
            scr = QApplication.screens()
            which = "second screen" if len(scr) > 1 else "only screen"
            return f"Projector screen: {g.width()}×{g.height()} ({which})"
        except Exception:
            return "Projector screen: unknown"

    def _fit_stage_view(self):
        v = self.stage_view
        if self.is_frozen:
            v.setSceneRect(QRectF())
            v.fitInView(self.freeze_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            return
        v.resetTransform()
        if self.fit_projector:
            vw, vh = v.viewport().width(), v.viewport().height()
            if vw > 0 and vh > 0:
                k = min(vw / STAGE_W, vh / STAGE_H)
                v.setSceneRect(QRectF(STAGE_X, STAGE_Y, STAGE_W, STAGE_H))   # players never see the backstage area
                v.scale(k, k)
                v.centerOn(2500, 2500)
                return
        v.setSceneRect(QRectF())
        v.centerOn(2500, 2500)

    def set_fit_projector(self, on):
        self.fit_projector = bool(on)
        self.save_config()
        self._fit_stage_view()

    def open_settings(self):
        SettingsDialog(self).exec()

    # ===== foldable toolbar menus =====
    def toggle_fold(self, key):
        self.ui_folds[key] = not self.ui_folds.get(key, False)
        self._apply_folds()
        self.save_config()

    def _apply_folds(self):
        self.annotate_strip.setVisible(bool(self.ui_folds.get("annotate")))
        self.blanking_strip.setVisible(bool(self.ui_folds.get("blanking")))
        self._update_fold_buttons()

    def _update_fold_buttons(self):
        if not hasattr(self, "btn_fold_annotate"):
            return
        ao, bo = bool(self.ui_folds.get("annotate")), bool(self.ui_folds.get("blanking"))
        self.btn_fold_annotate.setText("🖌 Annotate " + ("▾" if ao else "▸") + (" ●" if self.annotation_active else ""))
        self.btn_fold_blanking.setText("🔳 Blanking " + ("▾" if bo else "▸") + (" ●" if self.blanking_active() else ""))
        self.btn_fold_annotate.setStyleSheet(ACTIVE_BTN_STYLE if ao else INACTIVE_BTN_STYLE)
        self.btn_fold_blanking.setStyleSheet(ACTIVE_BTN_STYLE if bo else INACTIVE_BTN_STYLE)

    # ===== blanking on/off =====
    def blanking_active(self):
        return bool(self.blanking_all_scenes or self.blanking_scene_enabled)

    def _refresh_blanking_ui(self):
        if not hasattr(self, "chk_blank_scene"):
            return
        all_on = bool(self.blanking_all_scenes)
        for w, val in ((self.chk_blank_scene, all_on or self.blanking_scene_enabled), (self.chk_blank_all, all_on)):
            w.blockSignals(True)
            w.setChecked(val)
            w.blockSignals(False)
        self.chk_blank_scene.setEnabled(bool(self.scene_path) and not all_on)
        self.chk_blank_scene.setToolTip("Always on because \"In all scenes\" is ticked" if all_on
                                        else "Turn blanking on for the current scene only")
        active = self.blanking_active()
        self.chk_show_blanking.setEnabled(active)
        self.btn_add_blanking.setEnabled(active)
        vis = active and self.chk_show_blanking.isChecked()
        for i in self.shared_scene.items():
            if isinstance(i, BlankingRect):
                i.setVisible(vis)
        self._update_fold_buttons()

    def on_blank_scene_toggled(self, checked):
        if self.is_loading or self.blanking_all_scenes:
            return
        self.blanking_scene_enabled = bool(checked)
        self._refresh_blanking_ui()
        if checked:
            self._load_blanking_from_current_layout()
        self.save_scene_settings()

    def on_blank_all_toggled(self, checked):
        self.blanking_all_scenes = bool(checked)
        self._refresh_blanking_ui()
        if self.blanking_active():
            self._load_blanking_from_current_layout()
        self.save_config()

    def _layout_file_path(self, ln):
        if not self.scene_path or not ln:
            return None
        p = os.path.join(self.scene_path, "layouts", ln)
        if not os.path.exists(p) and ln in self.linked_assets.get("layouts", {}):
            p = self.linked_assets["layouts"][ln]
        return p if os.path.exists(p) else None

    def _read_layout_blanking(self, ln):
        # Blanking list stored in a layout file ([] when none / unreadable / legacy list format)
        p = self._layout_file_path(ln)
        if not p:
            return []
        try:
            with open(p, 'r') as f:
                d = json.load(f)
            return [] if isinstance(d, list) else list(d.get("blanking", []) or [])
        except Exception:
            return []

    def _load_blanking_from_current_layout(self):
        # Blanking was off while the layout was loaded: put its boxes on the stage now (visuals untouched)
        if any(isinstance(i, BlankingRect) for i in self.shared_scene.items()):
            return
        if not self._loaded_layout or self._loaded_layout[0] != self.scene_path:
            return
        for o in self._read_layout_blanking(self._loaded_layout[1]):
            item = self.add_blanking_rect(o.get('x', STAGE_X), o.get('y', STAGE_Y),
                                          o.get('w', 300), o.get('h', 300), o.get('scale', 1.0), 1.0)
            item.intended_opacity = o.get('opacity', 1.0)
            item.setOpacity(item.intended_opacity)

    def add_blanking_box_manual(self):
        if not self.blanking_active():
            self.lbl_script_status.setText("Blanking is off here (🔳 Blanking menu)")
            QTimer.singleShot(3000, lambda: self.lbl_script_status.setText("Status: Idle"))
            return
        self.add_blanking_rect()

    def add_blanking_rect(self, x=None, y=None, w=300, h=300, scale=1.0, opacity=1.0):
        if x is None: x, y = STAGE_X + (STAGE_W - w) // 2, STAGE_Y + (STAGE_H - h) // 2
        item = BlankingRect(x, y, w, h, self);
        item.setScale(scale);
        item.intended_opacity = opacity;
        item.setOpacity(opacity);
        item.setVisible(self.chk_show_blanking.isChecked());
        self.shared_scene.addItem(item);
        return item

    def toggle_blanking_visibility(self, visible):
        visible = bool(visible) and self.blanking_active()
        [i.setVisible(visible) for i in self.shared_scene.items() if isinstance(i, BlankingRect)]

    def sync_opacity_slider(self):
        try:
            sel = self.shared_scene.selectedItems()
        except RuntimeError:
            return
        if sel: self.opacity_slider.blockSignals(True); self.opacity_slider.setValue(
            int(sel[0].opacity() * 100)); self.opacity_slider.blockSignals(False)

    def change_object_opacity(self, val):
        for i in self.shared_scene.selectedItems():
            i.setOpacity(val / 100.0)
            if hasattr(i, 'intended_opacity'): i.intended_opacity = val / 100.0

    def clear_canvas_master(self):
        self.shared_scene.clear()

    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f: return json.load(f)
        except:
            pass
        return {}

    def save_config(self):
        data = {
            "root_path": self.root_path,
            "last_session": self.current_session,
            "last_scene": self.scene_list.currentItem().text() if self.scene_list.currentItem() else "",
            "improv_music_path": self.txt_improv_music_path.text() if hasattr(self,
                                                                              'txt_improv_music_path') else self.improv_path,
            "improv_visuals_path": self.txt_improv_visuals_path.text() if hasattr(self,
                                                                                  'txt_improv_visuals_path') else self.improv_visuals_path,
            "improv_sfx_path": self.txt_improv_sfx_path.text() if hasattr(self,
                                                                          'txt_improv_sfx_path') else self.improv_sfx_path,
            "break_music_folder": self.break_music_folder,
            "break_image_folder": self.break_image_folder,

            "default_blanking": self.default_blanking_data,
            "blanking_all_scenes": self.blanking_all_scenes,
            "fit_projector": self.fit_projector,
            "fast_quickfire": self.fast_quickfire,
            "lights": self.lights_config,
            "transcribe": self.transcribe_config,
            "ui_folds": dict(self.ui_folds),
            "recording_path": self.recording_path
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f)

    def save_scene_settings(self):
        if self.is_loading or not self.scene_path or not os.path.exists(self.scene_path): return
        if self._music_list_is_playlist():
            self._sync_playlist_from_music_list()
        self._remember_music_order()
        order = {
            "music": [self.music_list.item(i).text() for i in range(self.music_list.count())],
            "sfx": [self.sfx_list.item(i).text() for i in range(self.sfx_list.count())],
            "visuals": [self.visuals_list.item(i).text() for i in range(self.visuals_list.count())],
            "layouts": [self.layout_list.item(i).text() for i in range(self.layout_list.count())]
        }
        payload = {
            "use_scene_music": self.chk_use_scene_music.isChecked(),
            "improv_category": self.combo_improv_folders.currentText(),
            "use_playlist": self.chk_use_playlist.isChecked(),
            "music_orders": self.music_orders,
            "music_playlist_source": to_portable(self.music_playlist_source),
            "use_scene_visuals": self.chk_use_scene_visuals.isChecked(),
            "improv_visuals_category": self.combo_improv_visuals_folders.currentText(),
            "use_scene_sfx": self.chk_use_scene_sfx.isChecked(),
            "improv_sfx_category": self.combo_improv_sfx_folders.currentText(),
            "is_shuffle": self.is_shuffle,
            "fade_duration": self.spin_fade_dur.value(),
            "linked_assets": self._portable_linked(),
            "music_volume": self.music_vol_slider.value(),
            "sfx_volume": self.sfx_vol_slider.value(),
            "quickfire_volume": self.quickfire_vol_slider.value(),
            "quickfire_path": to_portable(self.quickfire_path),
            "scripts_order": list(self.scripts_order),
            "quickfire_enabled": self.quickfire_enabled,
            "collapsed_sections": dict(self.collapsed_sections),
            "blanking_enabled": bool(self.blanking_scene_enabled),
            "music_playlist": self._portable_playlist(self.music_playlist),
            "music_playlist_name": self.music_playlist_name,
            "orders": order
        }
        try:
            # Write to temp file first, then rename — prevents empty file on crash
            target = os.path.join(self.scene_path, SCENE_SETTINGS_FILE)
            tmp = target + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(payload, f)
            os.replace(tmp, target)
        except Exception as e:
            print(f"Save failed: {e}")

    def load_scene_settings(self):
        path = os.path.join(self.scene_path, SCENE_SETTINGS_FILE)
        self.linked_assets = {"music": {}, "sfx": {}, "visuals": {}, "layouts": {}}
        # Default = everything expanded (also covers scenes with no saved file yet)
        self.collapsed_sections = {"music": False, "sfx": False, "visuals": False, "layouts": False}
        self.scripts_order = []
        self._apply_all_section_collapse()
        self.blanking_scene_enabled = False      # default for scenes without the setting
        self._refresh_blanking_ui()
        self.music_playlist = []
        self.music_playlist_name = ""
        self.music_playlist_source = ""
        self.music_orders = {}
        self._music_list_key = None   # loaded state wins over whatever the old list showed
        self.chk_use_playlist.blockSignals(True)
        self.chk_use_playlist.setChecked(False)
        self.chk_use_playlist.blockSignals(False)
        self._update_music_source_ui()
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                self.is_loading = True
                self.chk_use_scene_sfx.setChecked(data.get("use_scene_sfx", True))
                self.improv_sfx_container.setVisible(not self.chk_use_scene_sfx.isChecked())
                idx = self.combo_improv_sfx_folders.findText(data.get("improv_sfx_category", "--- Root Folder ---"))
                if idx != -1: self.combo_improv_sfx_folders.setCurrentIndex(idx)
                self.chk_use_scene_music.setChecked(data.get("use_scene_music", True))
                idx = self.combo_improv_folders.findText(data.get("improv_category", ""))
                if idx != -1:
                    self.combo_improv_folders.setCurrentIndex(idx)
                elif self.combo_improv_folders.count() > 0:
                    # old "--- Root Folder ---" / "★ Playlist" entries no longer exist
                    self.combo_improv_folders.setCurrentIndex(0)
                self.set_music_mode(data.get("is_shuffle", False))
                self.spin_fade_dur.setValue(data.get("fade_duration", 1.0))
                la = data.get("linked_assets", {})
                la = la if isinstance(la, dict) else {}
                self.linked_assets = {
                    cat: {k: resolve_path(v) for k, v in (la.get(cat) or {}).items() if isinstance(v, str)}
                    for cat in ("music", "sfx", "visuals", "layouts")}
                self.music_vol_slider.setValue(data.get("music_volume", 80))
                self.sfx_vol_slider.setValue(data.get("sfx_volume", 80))
                # scenes saved before the Quick Fire slider existed keep sounding as before (= their SFX volume)
                self.quickfire_vol_slider.setValue(data.get("quickfire_volume", data.get("sfx_volume", 80)))
                self.chk_use_scene_visuals.setChecked(data.get("use_scene_visuals", True))
                self.improv_visuals_container.setVisible(not self.chk_use_scene_visuals.isChecked())
                idx = self.combo_improv_visuals_folders.findText(
                    data.get("improv_visuals_category", "--- Root Folder ---"))
                if idx != -1: self.combo_improv_visuals_folders.setCurrentIndex(idx)
                self.chk_use_scene_sfx.setChecked(data.get("use_scene_sfx", True))
                self.improv_sfx_container.setVisible(not self.chk_use_scene_sfx.isChecked())
                idx = self.combo_improv_sfx_folders.findText(data.get("improv_sfx_category", "--- Root Folder ---"))
                if idx != -1: self.combo_improv_sfx_folders.setCurrentIndex(idx)
                self.quickfire_path = resolve_path(data.get("quickfire_path", "")) or ""
                qf_enabled = data.get("quickfire_enabled", False)
                self.chk_quickfire.setChecked(qf_enabled)
                self.quickfire_container.setVisible(qf_enabled)
                if self.quickfire_path:
                    self.populate_quickfire_combo()
                else:
                    self.combo_quickfire.blockSignals(True)
                    self.combo_quickfire.clear()
                    self.combo_quickfire.blockSignals(False)
                self.music_playlist = self._sanitize_playlist(
                    [dict(e, path=resolve_path(e.get("path"))) for e in (data.get("music_playlist") or [])
                     if isinstance(e, dict) and isinstance(e.get("path"), str)])
                self.music_playlist_name = str(data.get("music_playlist_name", ""))
                loaded_collapse = data.get("collapsed_sections", {})
                for k in ("music", "sfx", "visuals", "layouts"):
                    self.collapsed_sections[k] = bool(loaded_collapse.get(k, False))
                self._apply_all_section_collapse()
                self.blanking_scene_enabled = bool(data.get("blanking_enabled", False))
                self._refresh_blanking_ui()
                _so = data.get("scripts_order", [])
                self.scripts_order = [n for n in _so if isinstance(n, str)] if isinstance(_so, list) else []
                saved_orders = data.get("orders", {})
                self._restore_music_source_state(data, saved_orders)
                self.folder_mode_orders = {
                    "music": saved_orders.get("music", []),
                    "sfx": saved_orders.get("sfx", []),
                    "visuals": saved_orders.get("visuals", []),
                    "layouts": saved_orders.get("layouts", [])
                }
                self.is_loading = False  # ← last thing before return
                return saved_orders
            except Exception as e:
                self.is_loading = False
                print(f"Load failed: {e}")
        return {}

    def set_active_scene(self, item):
        self.save_scene_settings()
        self.scene_path = os.path.join(self.root_path, self.current_session, item.text())
        self.load_music_filters()
        self.auto_mark_recording(f"Scene: {item.text()}")
        orders = self.load_scene_settings();
        self.load_assets(orders);
        self.save_config()
        self._refresh_music_dialogs()

    def populate_improv_categories(self):
        self.combo_improv_folders.blockSignals(True)
        self.combo_improv_folders.clear()
        path = self.txt_improv_music_path.text()
        if os.path.exists(path):
            # every folder at any depth, shown as "Parent/Child"
            self.combo_improv_folders.addItems(list_subfolders_recursive(path))
        self.combo_improv_folders.blockSignals(False)

    def reset_preview_view(self):
        self.preview_view.fitInView(QRectF(STAGE_X, STAGE_Y, STAGE_W, STAGE_H), Qt.AspectRatioMode.KeepAspectRatio)

    def reload_all(self):
        self.update_root()
        self.populate_improv_categories()
        # Re-apply scene settings to preserve folder selections
        if self.scene_path and os.path.exists(self.scene_path):
            orders = self.load_scene_settings()
            self.load_assets(orders)
        else:
            orders = {
                "music": [self.music_list.item(i).text() for i in range(self.music_list.count())],
                "sfx": [self.sfx_list.item(i).text() for i in range(self.sfx_list.count())],
                "visuals": [self.visuals_list.item(i).text() for i in range(self.visuals_list.count())],
                "layouts": [self.layout_list.item(i).text() for i in range(self.layout_list.count())]
            }
            self.load_assets(orders)

    # ===== portable paths =====
    def _portable_anchors(self):
        d = {"ROOT": getattr(self, "root_path", "")}
        for token, attr in (("MUSIC", "txt_improv_music_path"), ("SFX", "txt_improv_sfx_path"),
                            ("VISUALS", "txt_improv_visuals_path")):
            w = getattr(self, attr, None)
            if w is not None:
                d[token] = w.text().strip()
        return d

    def _portable_linked(self):
        out = {}
        for cat in ("music", "sfx", "visuals", "layouts"):
            out[cat] = {k: to_portable(v) for k, v in (self.linked_assets.get(cat) or {}).items()}
        return out

    def _portable_playlist(self, entries):
        return [{"name": e["name"], "path": to_portable(e["path"])} for e in entries]

    def _resolve_layout_visual_path(self, o):
        fn = o.get('file', '')
        p = resolve_path(o.get('path')) if o.get('path') else None
        if p and os.path.exists(p):
            return p
        for cand in (os.path.join(self.scene_path, "visuals", fn),
                     (self.linked_assets.get('visuals') or {}).get(fn)):
            if cand and os.path.exists(cand):
                return cand
        return p

    def _portable_convert_file(self, fp, kind, report):
        # kind: settings | layout | playlist. Returns True when the file changed.
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            report["unreadable"].append(fp)
            return False
        changed = [False]

        def conv(p):
            if not isinstance(p, str) or not p:
                return p
            new = to_portable(resolve_path(p))
            if os.path.isabs(new) and new == p:
                report["external"].add(p)
            elif new != p:
                changed[0] = True
            elif os.path.isabs(new):
                report["external"].add(p)
            return new

        if kind == "settings" and isinstance(data, dict):
            la = data.get("linked_assets")
            if isinstance(la, dict):
                for cat, d in la.items():
                    if isinstance(d, dict):
                        for k in list(d):
                            d[k] = conv(d[k])
            if data.get("quickfire_path"):
                data["quickfire_path"] = conv(data["quickfire_path"])
            if data.get("music_playlist_source"):
                data["music_playlist_source"] = conv(data["music_playlist_source"])
            for e in data.get("music_playlist", []) or []:
                if isinstance(e, dict) and e.get("path"):
                    e["path"] = conv(e["path"])
        elif kind == "layout":
            vis = data if isinstance(data, list) else (data.get("visuals", []) if isinstance(data, dict) else [])
            for o in vis:
                if isinstance(o, dict) and o.get("path"):
                    o["path"] = conv(o["path"])
        elif kind == "playlist" and isinstance(data, dict):
            for e in data.get("tracks", []) or []:
                if isinstance(e, dict) and e.get("path"):
                    e["path"] = conv(e["path"])
        if not changed[0]:
            return False
        try:
            rel = os.path.relpath(fp, self.root_path)
            bak = os.path.join(report["backup_dir"], rel)
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(fp, bak)
            tmp = fp + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, fp)
            return True
        except Exception as e:
            logging.warning(f"Make portable: could not rewrite {fp}: {e}")
            report["unreadable"].append(fp)
            return False

    def make_project_portable(self):
        if not self.root_path or not os.path.isdir(self.root_path):
            QMessageBox.warning(self, "Make portable", "Set the sessions folder first.")
            return
        if QMessageBox.question(
                self, "Make project portable",
                "This rewrites the scene settings, layouts and playlists of ALL sessions so that "
                "file paths inside your sessions folder (and your music / sfx / visuals libraries) "
                "are stored relative instead of as full paths.\n\n"
                "Your media files are not touched. The original files are copied to a backup "
                "folder next to the program first.\n\nContinue?") != QMessageBox.StandardButton.Yes:
            return
        self.save_scene_settings()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        report = {"backup_dir": os.path.join(_app_dir(), f"portable_backup_{ts}"),
                  "external": set(), "unreadable": []}
        n_files = n_changed = 0
        for session in sorted(os.listdir(self.root_path)):
            sp = os.path.join(self.root_path, session)
            if not os.path.isdir(sp):
                continue
            for scene in sorted(os.listdir(sp)):
                sc = os.path.join(sp, scene)
                if not os.path.isdir(sc):
                    continue
                jobs = []
                fp = os.path.join(sc, SCENE_SETTINGS_FILE)
                if os.path.isfile(fp):
                    jobs.append((fp, "settings"))
                for sub, kind in (("layouts", "layout"), ("playlists", "playlist")):
                    d = os.path.join(sc, sub)
                    if os.path.isdir(d):
                        jobs += [(os.path.join(d, f), kind) for f in sorted(os.listdir(d)) if f.lower().endswith(".json")]
                for fp, kind in jobs:
                    n_files += 1
                    if self._portable_convert_file(fp, kind, report):
                        n_changed += 1
        msg = f"Checked {n_files} file(s), converted {n_changed}."
        if n_changed:
            msg += f"\nBackup of the originals: {report['backup_dir']}"
        ext = sorted(report["external"])
        if ext:
            msg += (f"\n\n{len(ext)} path(s) point OUTSIDE your sessions folder and libraries, so they "
                    "will NOT work on another PC unless you copy those files into the project:\n"
                    + "\n".join(ext[:12]) + ("\n..." if len(ext) > 12 else ""))
        if report["unreadable"]:
            msg += "\n\nCould not read/write:\n" + "\n".join(report["unreadable"][:8])
        QMessageBox.information(self, "Make project portable", msg)
        cur = self.scene_list.currentItem()
        if cur is not None:
            self.set_active_scene(cur)

    def browse_root_path(self):
        p = QFileDialog.getExistingDirectory(
            self, "Select the folder that contains your sessions", self.txt_root.text() or "")
        if not p or os.path.normpath(p) == os.path.normpath(self.root_path or ""):
            return
        self.save_scene_settings()            # still the old root / scene here
        self._close_music_dialogs()
        self.txt_root.setText(p)
        self.update_root()
        self.current_session = ""
        self.scene_path = ""
        self.scene_list.clear()
        self.load_assets()
        self.save_config()

    def update_root(self):
        self.root_path = self.txt_root.text();
        self.load_sessions();
        self.save_config()

    def add_new_session(self):
        name, ok = QInputDialog.getText(self, "New Session", "Session Name:")
        if ok and name: [os.makedirs(os.path.join(self.root_path, name)), self.load_sessions()] if not os.path.exists(
            os.path.join(self.root_path, name)) else None

    def add_new_scene(self):
        name, ok = QInputDialog.getText(self, "New Scene", "Scene Name:")
        if ok and name and self.current_session:
            d = os.path.join(self.root_path, self.current_session, name)
            if not os.path.exists(d):
                os.makedirs(d);
                [os.makedirs(os.path.join(d, s)) for s in ["layouts", "music", "scripts", "sfx", "visuals"]]
                with open(os.path.join(d, "layout.json"), 'w') as f: json.dump([], f)
                self.load_scenes_for_session(self.session_list.currentItem())

    def load_sessions(self):
        self.session_list.clear();
        [self.session_list.addItems(sorted([d for d in os.listdir(self.root_path) if
                                            os.path.isdir(os.path.join(self.root_path, d))])) if os.path.exists(
            self.root_path) else None]

    def load_scenes_for_session(self, item):
        self.current_session = item.text();
        self.scene_list.clear();
        p = os.path.join(self.root_path, self.current_session)
        [self.scene_list.addItems(
            sorted([d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))])) if os.path.exists(p) else None];
        self.save_config()

    def restore_selection(self):
        its = self.session_list.findItems(self.current_session, Qt.MatchFlag.MatchExactly)
        if its:
            self.session_list.setCurrentItem(its[0]);
            self.load_scenes_for_session(its[0])
            s_its = self.scene_list.findItems(self.last_scene, Qt.MatchFlag.MatchExactly)
            if s_its: self.scene_list.setCurrentItem(s_its[0]); self.set_active_scene(s_its[0])

    def browse_improv_music_path(self):
        p = QFileDialog.getExistingDirectory(self, "Select Music Folder", self.txt_improv_music_path.text())
        if p:
            self.txt_improv_music_path.setText(p)
            self.populate_improv_categories()
            self.load_assets(self._capture_current_orders())
            self.save_config()

    def update_track_time(self):
        if self.music_player.is_playing():
            rem = (self.music_player.get_length() - self.music_player.get_time()) // 1000
            if rem > 0: m, s = divmod(rem, 60); self.lbl_time_rem.setText(f"-{m:02d}:{s:02d}")
        else:
            self.lbl_time_rem.setText("")

    def load_assets(self, orders=None):
        # Remember the order of the music list that is shown right now, under
        # the source it was built for, BEFORE the lists are cleared.
        self._remember_music_order()
        prev_loading = self.is_loading
        self.is_loading = True
        prev_music_current = self.music_list.currentItem().text() if self.music_list.currentItem() else None

        for w in [self.music_list, self.sfx_list, self.visuals_list, self.layout_list, self.scripts_list]:
            w.blockSignals(True)
            w.clear()

        if not self.scene_path or not os.path.exists(self.scene_path):
            for w in [self.music_list, self.sfx_list, self.visuals_list, self.layout_list, self.scripts_list]:
                w.blockSignals(False)
            self.is_loading = prev_loading
            return

        for sub, w in {"music": self.music_list, "sfx": self.sfx_list, "visuals": self.visuals_list,
                       "layouts": self.layout_list, "scripts": self.scripts_list}.items():
            music_key = None
            if sub == "music":
                if self._fill_music_list(w):      # playlist mode
                    w.blockSignals(False)
                    continue
                music_key = self._current_music_key()
            if sub == "music" and not self.chk_use_scene_music.isChecked():
                b, c = self.txt_improv_music_path.text(), self.combo_improv_folders.currentText()
                path = b if (c == "--- Root Folder ---" or not c) else os.path.join(b, c)
            elif sub == "visuals" and not self.chk_use_scene_visuals.isChecked():
                b = self.txt_improv_visuals_path.text()
                c = self.combo_improv_visuals_folders.currentText()
                path = b if (c == "--- Root Folder ---" or not c) else os.path.join(b, c)
            elif sub == "sfx" and not self.chk_use_scene_sfx.isChecked():
                b = self.txt_improv_sfx_path.text()
                c = self.combo_improv_sfx_folders.currentText()
                path = b if (c == "--- Root Folder ---" or not c) else os.path.join(b, c)
            else:
                path = os.path.join(self.scene_path, sub)

            disk_files = []
            if os.path.exists(path):
                try:
                    disk_files = sorted([f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))])
                except:
                    disk_files = []
                if sub == "layouts":
                    disk_files = [x for x in disk_files if x.endswith(".json")]

            linked = self.linked_assets.get(sub, {})
            all_files = list(set(disk_files) | set(linked.keys()))
            if sub == "music":
                # each source (folder / every genre) has its own remembered order
                saved_order = self.music_orders.get(music_key, [])
            elif sub == "scripts":
                saved_order = list(self.scripts_order)
            else:
                saved_order = (orders or {}).get(sub, [])
            final_list = [f for f in saved_order if f in all_files]
            final_list += [f for f in sorted(all_files) if f not in final_list]

            for f in final_list:
                it = QListWidgetItem(f)
                if f in linked:
                    it.setForeground(QColor("#4fc3f7"))
                    it.setData(Qt.ItemDataRole.UserRole, linked[f])
                elif sub == "visuals" and not self.chk_use_scene_visuals.isChecked():
                    it.setData(Qt.ItemDataRole.UserRole, os.path.join(path, f))
                elif sub == "sfx" and not self.chk_use_scene_sfx.isChecked():
                    it.setData(Qt.ItemDataRole.UserRole, os.path.join(path, f))
                w.addItem(it)
            if sub == "music":
                self._music_list_key = music_key
                self._music_list_scene = self.scene_path
            w.blockSignals(False)

        # Restore music selection AFTER the loop, not inside it
        if prev_music_current:
            for i in range(self.music_list.count()):
                if self.music_list.item(i).text() == prev_music_current:
                    self.music_list.blockSignals(True)
                    self.music_list.setCurrentRow(i)
                    self.music_list.blockSignals(False)
                    break

        self.is_loading = prev_loading
        self.update_info_labels()
        self._apply_music_filter_colors()
        self._mark_layouts_with_linked_visuals()

    # ===== blue dot: layouts that use linked visuals =====
    def _link_dot_icon(self, on):
        # always an icon of the same size (transparent when off) so all layout names stay aligned
        cache = getattr(self, "_link_dot_icons", None)
        if cache is None:
            cache = self._link_dot_icons = {}
        if on not in cache:
            pm = QPixmap(10, 10)
            pm.fill(Qt.GlobalColor.transparent)
            if on:
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#4fc3f7"))
                p.drawEllipse(1, 1, 8, 8)
                p.end()
            cache[on] = QIcon(pm)
        return cache[on]

    def _layout_linked_visuals(self, ln):
        """File names of the visuals in layout `ln` that do NOT live in this scene's own visuals folder
        (linked from another scene, from a library, or referenced by path)."""
        fp = self._layout_file_path(ln)
        if not fp:
            return []
        try:
            mtime = os.path.getmtime(fp)
            cache = getattr(self, "_layout_vis_cache", None)
            if cache is None:
                cache = self._layout_vis_cache = {}
            hit = cache.get(fp)
            if hit and hit[0] == mtime:
                visuals = hit[1]
            else:
                with open(fp, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                visuals = d if isinstance(d, list) else (d.get("visuals", []) if isinstance(d, dict) else [])
                visuals = [o for o in visuals if isinstance(o, dict)]
                cache[fp] = (mtime, visuals)
        except Exception:
            return []
        own = os.path.normcase(os.path.normpath(os.path.join(self.scene_path, "visuals")))
        out = []
        for o in visuals:
            try:
                res = self._resolve_layout_visual_path(o)
                if res and os.path.exists(res) and \
                        os.path.normcase(os.path.normpath(os.path.dirname(res))) != own:
                    name = o.get('file') or os.path.basename(res)
                    if name not in out:
                        out.append(name)
            except Exception:
                continue
        return out

    def _mark_layouts_with_linked_visuals(self):
        try:
            for i in range(self.layout_list.count()):
                it = self.layout_list.item(i)
                names = self._layout_linked_visuals(it.text())
                it.setIcon(self._link_dot_icon(bool(names)))
                it.setToolTip(("Uses linked visuals: " + ", ".join(names)) if names else "")
        except Exception as ex:
            logging.warning(f"Could not mark layouts with linked visuals: {ex}")

    # ===== scripts: running in the background, list management =====
    def run_hue_script(self):
        # Runs the selected script on a helper thread: an unreachable lamp or hub can no longer freeze the window.
        it = self.scripts_list.currentItem()
        if not it or not self.scene_path:
            return
        if lights_mod is None:
            self.lbl_script_status.setText("Error: lights.py is missing next to the program")
            return
        path = os.path.join(self.scene_path, "scripts", it.text())
        if self._script_cancel is not None:
            self._script_cancel.set()             # a script that is still running stops at its next step
        cancel = threading.Event()
        self._script_cancel = cancel
        self._script_run_id += 1
        run_id = self._script_run_id
        cfg = json.loads(json.dumps(self.lights_config))     # private copy for the helper thread
        self.lbl_script_status.setText(f"Status: Running {it.text()}...")
        bridge = self._script_bridge

        def work():
            try:
                lights_mod.run_script(path, cfg, cancel)
                msg, ok = ("Status: Stopped" if cancel.is_set() else "Status: Executed"), True
            except lights_mod.ScriptError as e:
                msg, ok = f"Error: {e}", False
            except Exception as e:
                logging.warning(f"Script failed: {lights_mod.redact(e, cfg)}")
                msg, ok = f"Error: {lights_mod.redact(e, cfg)}", False
            try:
                bridge.finished.emit(msg, ok, run_id)
            except Exception:
                pass

        threading.Thread(target=work, name="script-run", daemon=True).start()

    def _on_script_finished(self, msg, ok, run_id):
        if run_id != self._script_run_id:
            return                                  # a newer run owns the status line now
        self.lbl_script_status.setText(msg)
        if ok:
            QTimer.singleShot(3000, lambda: self.lbl_script_status.setText("Status: Idle")
                              if run_id == self._script_run_id else None)

    def open_light_designer(self, edit_name=None):
        if light_designer is None or lights_mod is None:
            QMessageBox.warning(self, "Light Designer", "light_designer.py / lights.py are missing next to the program.")
            return
        dlg = light_designer.LightDesigner(
            self, self.lights_config, self._scripts_dir(),
            on_cfg_changed=self._on_lights_cfg_changed, on_saved=self._on_designer_saved, edit_script=edit_name)
        dlg.exec()

    def _on_lights_cfg_changed(self, cfg):
        self.lights_config = cfg
        self.save_config()

    def _on_designer_saved(self, name):
        names = [self.scripts_list.item(i).text() for i in range(self.scripts_list.count())]
        if name not in names:
            names.append(name)
        self.scripts_order = names
        self.save_scene_settings()
        self._reload_scripts_list(select=name)

    def stop_script(self):
        if self._script_cancel is not None:
            self._script_cancel.set()

    def _scripts_dir(self):
        return os.path.join(self.scene_path, "scripts") if self.scene_path else None

    def _on_scripts_reordered(self, *args):
        if self.is_loading:
            return
        self.scripts_order = [self.scripts_list.item(i).text() for i in range(self.scripts_list.count())]
        self.save_scene_settings()

    def _reload_scripts_list(self, select=None):
        # self.scripts_order is already updated by the caller
        self.load_assets(self._capture_current_orders())
        if select:
            for i in range(self.scripts_list.count()):
                if self.scripts_list.item(i).text() == select:
                    self.scripts_list.setCurrentRow(i)
                    break

    @staticmethod
    def _clean_script_name(name):
        name = (name or "").strip()
        if not name or any(c in name for c in '\\/:*?"<>|'):
            return None
        if not name.lower().endswith(".json"):
            name += ".json"
        return name

    def _unique_script_name(self, base):
        d = self._scripts_dir()
        stem = base[:-5] if base.lower().endswith(".json") else base
        cand, n = stem + ".json", 2
        while os.path.exists(os.path.join(d, cand)):
            cand = f"{stem} {n}.json"
            n += 1
        return cand

    def rename_script(self, old):
        d = self._scripts_dir()
        if not d or not os.path.exists(os.path.join(d, old)):
            return
        new, ok = QInputDialog.getText(self, "Rename script", "New name:", text=old[:-5] if old.lower().endswith(".json") else old)
        if not ok:
            return
        new = self._clean_script_name(new)
        if not new:
            QMessageBox.warning(self, "Rename script", "That name is not allowed (no \\ / : * ? \" < > |).")
            return
        if new == old:
            return
        if os.path.exists(os.path.join(d, new)):
            QMessageBox.warning(self, "Rename script", f"A script called {new} already exists.")
            return
        try:
            os.rename(os.path.join(d, old), os.path.join(d, new))
        except Exception as e:
            QMessageBox.warning(self, "Rename script", f"Could not rename: {e}")
            return
        self.scripts_order = [new if n == old else n for n in
                              [self.scripts_list.item(i).text() for i in range(self.scripts_list.count())]]
        self.save_scene_settings()
        self._reload_scripts_list(select=new)

    def duplicate_script(self, name):
        d = self._scripts_dir()
        src = os.path.join(d, name) if d else None
        if not src or not os.path.exists(src):
            return
        new = self._unique_script_name((name[:-5] if name.lower().endswith(".json") else name) + " copy")
        try:
            shutil.copy2(src, os.path.join(d, new))
        except Exception as e:
            QMessageBox.warning(self, "Duplicate script", f"Could not copy: {e}")
            return
        names = [self.scripts_list.item(i).text() for i in range(self.scripts_list.count())]
        if name in names:
            names.insert(names.index(name) + 1, new)
        self.scripts_order = names
        self.save_scene_settings()
        self._reload_scripts_list(select=new)

    def delete_script(self, name):
        d = self._scripts_dir()
        p = os.path.join(d, name) if d else None
        if not p or not os.path.exists(p):
            return
        if QMessageBox.question(self, "Delete script",
                                f"Delete the script \"{name}\"?\nThis cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(p)
        except Exception as e:
            QMessageBox.warning(self, "Delete script", f"Could not delete: {e}")
            return
        self.scripts_order = [n for n in self.scripts_order if n != name]
        self.save_scene_settings()
        self._reload_scripts_list()

    def open_scripts_folder(self):
        d = self._scripts_dir()
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def show_scripts_context_menu(self, pos):
        if not self.scene_path:
            return
        it = self.scripts_list.itemAt(pos)
        menu = QMenu()
        if it:
            name = it.text()
            a = QAction("▶ Run", self); a.triggered.connect(lambda: (self.scripts_list.setCurrentItem(it), self.run_hue_script())); menu.addAction(a)
            a = QAction("✏ Rename...", self); a.triggered.connect(lambda: self.rename_script(name)); menu.addAction(a)
            a = QAction("💡 Edit in Light Designer...", self); a.triggered.connect(lambda: self.open_light_designer(name)); menu.addAction(a)
            a = QAction("⧉ Duplicate", self); a.triggered.connect(lambda: self.duplicate_script(name)); menu.addAction(a)
            a = QAction("🗑 Delete...", self); a.triggered.connect(lambda: self.delete_script(name)); menu.addAction(a)
            menu.addSeparator()
        a = QAction("■ Stop running script", self); a.triggered.connect(self.stop_script); menu.addAction(a)
        a = QAction("💡 New script in Light Designer...", self); a.triggered.connect(lambda: self.open_light_designer()); menu.addAction(a)
        a = QAction("📂 Open scripts folder", self); a.triggered.connect(self.open_scripts_folder); menu.addAction(a)
        menu.exec(self.scripts_list.mapToGlobal(pos))

    def start_all_master(self):
        lay = self.layout_list.currentItem() or (self.layout_list.item(0) if self.layout_list.count() > 0 else None)
        if lay: self.layout_list.setCurrentItem(lay); self.load_layout_with_fade(lay.text(), sync=True)
        scr = self.scripts_list.currentItem() or (self.scripts_list.item(0) if self.scripts_list.count() > 0 else None)
        if scr: self.scripts_list.setCurrentItem(scr); QTimer.singleShot(50, self.run_hue_script)
        QTimer.singleShot(150, self.play_music);
        QTimer.singleShot(300, self.play_sfx)

    def add_asset_to_canvas(self, filename, x=None, y=None, scale=1.0, z=None, opacity=1.0, path=None):
        if path is None: path = os.path.join(self.scene_path, "visuals", filename)
        # print(f"[DEBUG VIDEO] filename={filename} ext={filename.lower().split('.')[-1]} path={path} exists={os.path.exists(path)}")
        # print(f"[DEBUG VIDEO] os.listdir match: {[f for f in os.listdir(os.path.dirname(path)) if filename in f]}")
        if not os.path.exists(path): return None
        ext = filename.lower().split('.')[-1]

        user_placed = x is None  # track whether this is a manual add or layout load

        if user_placed:
            x = STAGE_X + STAGE_W // 2
            y = STAGE_Y + STAGE_H // 2

        item = DraggableVideo(filename, path) if ext in ['mp4', 'mkv', 'avi', 'mov', 'wmv'] else DraggableImage(
            QPixmap(path), filename, path)
        if item:
            self.shared_scene.addItem(item)
            item.setScale(scale)
            item.intended_opacity = opacity
            item.setOpacity(opacity)
            if user_placed:
                if isinstance(item, DraggableVideo):
                    # Use actual video render size (1280x720), not placeholder (640x360)
                    item.setPos(x - item.v_width // 2, y - item.v_height // 2)
                else:
                    bounds = item.boundingRect()
                    item.setPos(x - bounds.width() * scale / 2,
                                y - bounds.height() * scale / 2)
            else:
                item.setPos(x, y)
            if z is None:
                self.z_counter += 1;
                item.setZValue(self.z_counter)
            else:
                item.setZValue(z);
                self.z_counter = max(self.z_counter, z)
            if isinstance(item, DraggableVideo):
                self._set_active_video_for_waveform(item)
            return item

    def show_layout_context_menu(self, pos):
        it = self.layout_list.itemAt(pos)
        if it:
            m = QMenu();
            alt = QAction("🔗 Link to...", self)
            alt.triggered.connect(lambda: self.link_items_to_scenes(self.layout_list, "layouts", it))
            m.addAction(alt)
            a = QAction("🗑 Delete Layout", self);
            a.triggered.connect(lambda: self.delete_layout(it.text()));
            m.addAction(a)
            if it.text() in self.linked_assets['layouts']:
                al = QAction("🔗 Remove Link", self);
                al.triggered.connect(lambda: self.remove_linked_asset('layouts', it.text()));
                m.addAction(al)
            m.exec(self.layout_list.mapToGlobal(pos))

    def delete_layout(self, fn):
        if QMessageBox.question(self, "Delete", f"Delete {fn}?") == QMessageBox.StandardButton.Yes:
            lp = os.path.join(self.scene_path, "layouts", fn)
            if os.path.exists(lp): os.remove(lp)
            if fn in self.linked_assets['layouts']: del self.linked_assets['layouts'][fn]
            self.save_scene_settings()
            orders = {
                "music": [self.music_list.item(i).text() for i in range(self.music_list.count())],
                "sfx": [self.sfx_list.item(i).text() for i in range(self.sfx_list.count())],
                "visuals": [self.visuals_list.item(i).text() for i in range(self.visuals_list.count())],
                "layouts": [self.layout_list.item(i).text() for i in range(self.layout_list.count())
                            if self.layout_list.item(i).text() != fn]
            }
            self.load_assets(orders)

    def create_new_empty_layout(self):
        if not self.scene_path:
            return
        name, ok = QInputDialog.getText(self, "New Layout", "Layout name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        lp = os.path.join(self.scene_path, "layouts")
        os.makedirs(lp, exist_ok=True)
        filepath = os.path.join(lp, f"{name}.json")

        # Derive blanking from whatever is currently on the canvas
        # Derive blanking from whatever is currently on the canvas
        blanking = []
        canvas_blanking = [i for i in self.shared_scene.items() if isinstance(i, BlankingRect)] \
            if self.blanking_active() else []
        if canvas_blanking:
            for br in canvas_blanking:
                entry = {
                    "x": br.x(), "y": br.y(),
                    "w": br.rect().width(), "h": br.rect().height(),
                    "scale": br.scale(),
                    "opacity": getattr(br, 'intended_opacity', 1.0)
                }
                blanking.append(entry)
        elif self.default_blanking_data and self.blanking_active():

            blanking = [self.default_blanking_data]

        with open(filepath, 'w') as f:
            json.dump({"visuals": [], "blanking": blanking}, f)

        orders = {
            "music": [self.music_list.item(i).text() for i in range(self.music_list.count())],
            "sfx": [self.sfx_list.item(i).text() for i in range(self.sfx_list.count())],
            "visuals": [self.visuals_list.item(i).text() for i in range(self.visuals_list.count())],
            "layouts": [self.layout_list.item(i).text() for i in range(self.layout_list.count())] + [f"{name}.json"]
        }
        self.load_assets(orders)

    def save_layout_dialog(self):
        lp = os.path.join(self.scene_path, "layouts");
        [os.makedirs(lp) if not os.path.exists(lp) else None]
        d = SaveLayoutDialog(sorted([f for f in os.listdir(lp) if f.endswith(".json")]), self)
        if d.exec():
            n = d.get_name()
            if n:
                visuals = []
                for i in self.shared_scene.items():
                    if isinstance(i, (DraggableImage, DraggableVideo)):
                        visuals.append(
                            {"file": i.file_name, "x": i.x(), "y": i.y(), "scale": i.scale(), "z": i.zValue(),
                             "opacity": getattr(i, 'intended_opacity', i.opacity()), "path": to_portable(i.path)})
                if self.blanking_active():
                    b = [{"x": i.x(), "y": i.y(), "w": i.rect().width(), "h": i.rect().height(), "scale": i.scale(),
                          "opacity": getattr(i, 'intended_opacity', i.opacity())} for i in self.shared_scene.items() if
                         isinstance(i, BlankingRect)]
                    if not b and self.default_blanking_data:
                        b = [self.default_blanking_data]
                else:
                    # blanking is off here: never wipe the blanking boxes an existing layout already has
                    b = self._read_layout_blanking(f"{n}.json")
                with open(os.path.join(lp, f"{n}.json"), 'w') as f:
                    json.dump({"visuals": visuals, "blanking": b}, f)
                orders = {
                    "music": [self.music_list.item(i).text() for i in range(self.music_list.count())],
                    "sfx": [self.sfx_list.item(i).text() for i in range(self.sfx_list.count())],
                    "visuals": [self.visuals_list.item(i).text() for i in range(self.visuals_list.count())],
                    "layouts": [self.layout_list.item(i).text() for i in range(self.layout_list.count())]
                }
                self.load_assets(orders)

    def sync_music_order_only(self, ln):
        p = os.path.join(self.scene_path, "layouts", ln)
        if not os.path.exists(p) and ln in self.linked_assets['layouts']:
            p = self.linked_assets['layouts'][ln]
        if not os.path.exists(p): return

        with open(p, 'r') as f:
            try:
                data = json.load(f)
            except:
                return

        if not (isinstance(data, dict) and "music_order" in data):
            return

        saved_order = data["music_order"]

        # Collect current names BEFORE clearing
        current_names = [self.music_list.item(i).text() for i in range(self.music_list.count())]

        # Only apply saved order if it contains exactly the same tracks
        # If there's any mismatch, preserve current UI order to avoid surprise resets
        if set(saved_order) != set(current_names):
            return  # different track sets — don't touch the order

        # Build final order: saved order first, then anything not in saved order
        final_order = [n for n in saved_order if n in current_names]
        final_order += [n for n in current_names if n not in final_order]

        # Now rebuild the list with fresh items (never reuse old QListWidgetItems)
        prev_loading = self.is_loading
        self.is_loading = True
        # Remember what was playing before the clear
        playing_name = ""
        if self.music_player.is_playing():
            it = self.music_list.currentItem()
            if it:
                playing_name = it.text()

        self.music_list.blockSignals(True)
        self.music_list.clear()
        self.music_list.addItems(final_order)

        # Restore current item selection so Now: label stays correct
        if playing_name:
            for i in range(self.music_list.count()):
                if self.music_list.item(i).text() == playing_name:
                    self.music_list.setCurrentRow(i)
                    break
        self.music_list.blockSignals(False)
        self.is_loading = prev_loading

        self.update_info_labels()

    def load_layout_with_fade(self, ln, sync=True):
        logging.debug(f"load_layout_with_fade called: '{ln}' sync={sync}")
        try:
            p = os.path.join(self.scene_path, "layouts", ln)
            if not os.path.exists(p) and ln in self.linked_assets['layouts']: p = self.linked_assets['layouts'][ln]
            if not os.path.exists(p): return
            try:
                with open(p, 'r') as f:
                    d = json.load(f)
            except:
                return
            self.auto_mark_recording(f"Layout: {ln.replace('.json', '')}")
            v_data = d if isinstance(d, list) else d.get("visuals", [])
            b_data = [] if isinstance(d, list) else d.get("blanking", [])
            self._loaded_layout = (self.scene_path, ln)
            if not self.blanking_active():
                b_data = []          # blanking is off for this scene: the layout file keeps its boxes untouched
            old_items = [i for i in self.shared_scene.items() if
                         isinstance(i, (DraggableImage, DraggableVideo, BlankingRect))]
            new_items = []

            # LEGACY SHIELD: Use .get() with defaults for all visual data
            for o in v_data:
                item = self.add_asset_to_canvas(
                    o.get('file', ''), o.get('x', STAGE_X), o.get('y', STAGE_Y),
                    o.get('scale', 1.0), o.get('z'), 0.0, path=self._resolve_layout_visual_path(o)
                )
                if item:
                    item.intended_opacity = o.get('opacity', 1.0)
                    new_items.append(item)

            # LEGACY SHIELD: Default handling for blanking rectangles
            for o in b_data:
                item = self.add_blanking_rect(
                    o.get('x', STAGE_X), o.get('y', STAGE_Y),
                    o.get('w', 300), o.get('h', 300), o.get('scale', 1.0), 0.0
                )
                item.intended_opacity = o.get('opacity', 1.0)
                new_items.append(item)

            def finalize_cross_fade():
                logging.debug("finalize_cross_fade called")
                for i in old_items:
                    if i.scene():
                        if isinstance(i, DraggableVideo): i.timer.stop(); i.player.stop(); i.player.release()
                        self.shared_scene.removeItem(i)

            self.va = QVariantAnimation();
            self.va.setDuration(int(self.spin_fade_dur.value() * 1000));
            self.va.setStartValue(0.0);
            self.va.setEndValue(1.0)

            def on_step(val):
                logging.debug(f"on_step val={val:.3f} old={len(old_items)} new={len(new_items)}")
                for i in old_items: i.setOpacity(max(0, getattr(i, 'intended_opacity', i.opacity()) * (1.0 - val)))
                for i in new_items: i.setOpacity(getattr(i, 'intended_opacity', 1.0) * val)

            self.va.valueChanged.connect(on_step);
            self.va.finished.connect(finalize_cross_fade);
            self.va.start()
        except Exception as e:
            logging.exception(f"CRASH inside load_layout_with_fade: {e}")
            raise

    def launch_projector(self):
        self.stage_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stage_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stage_view.centerOn(2500, 2500)

        scr = QApplication.screens()
        primary = QApplication.primaryScreen()
        target = next((s for s in scr if s != primary), primary)

        geo = target.geometry()
        # Move to target screen FIRST, then go fullscreen
        self.stage_window.setScreen(target)
        self.stage_window.move(geo.topLeft())
        self.stage_window.resize(geo.width(), geo.height())
        self.stage_window.showFullScreen()
        # Force geometry again after fullscreen in case the WM overrides it
        QTimer.singleShot(100, lambda: self.stage_window.setGeometry(geo))
        QTimer.singleShot(250, self._fit_stage_view)

    def bring_to_front(self):
        [(setattr(self, 'z_counter', self.z_counter + 1), i.setZValue(self.z_counter)) for i in
         self.shared_scene.selectedItems()]

    def send_to_back(self):
        [i.setZValue(0) for i in self.shared_scene.selectedItems()]

    def set_music_mode(self, shuf):
        self.is_shuffle = shuf;
        self.btn_shuffle.setStyleSheet(ACTIVE_BTN_STYLE if shuf else INACTIVE_BTN_STYLE);
        self.btn_order.setStyleSheet(INACTIVE_BTN_STYLE if shuf else ACTIVE_BTN_STYLE);
        self.update_info_labels();
        self.save_scene_settings()

    def get_next_index(self):
        c, curr = self.music_list.count(), self.music_list.currentRow()
        if c == 0:
            return 0
        allowed = [i for i in range(c) if self._is_track_allowed(self.music_list.item(i).text())] \
            or list(range(c))
        if c <= 1:
            return allowed[0]
        if self.is_shuffle:
            choices = [i for i in allowed if i != curr] or allowed
            return random.choice(choices)
        if curr == -1:
            return allowed[0]
        try:
            pos = allowed.index(curr)
            return allowed[(pos + 1) % len(allowed)]
        except ValueError:
            after = [i for i in allowed if i > curr]
            return after[0] if after else allowed[0]

    def fade_music_volume(self, t, d=1000, cb=None):
        if hasattr(self, 'ma') and self.ma and self.ma.state() == QVariantAnimation.State.Running:
            self.ma.stop()
        self.ma = QVariantAnimation()
        self.ma.setDuration(d)
        self.ma.setStartValue(self.music_player.audio_get_volume())
        self.ma.setEndValue(t)
        self.ma.valueChanged.connect(self.music_player.audio_set_volume)
        if cb: self.ma.finished.connect(cb)
        self.ma.start()

    def play_music(self):
        if self.is_shuffle and not self.music_list.currentItem() and self.music_list.count() > 0:
            allowed_indices = [i for i in range(self.music_list.count())
                               if self._is_track_allowed(self.music_list.item(i).text())] \
                               or list(range(self.music_list.count()))
            self.music_list.setCurrentRow(random.choice(allowed_indices))
        it = self.music_list.currentItem() or (
            self.music_list.item(self._first_allowed_index()) if self.music_list.count() > 0 else None)
        if it:
            def swap():
                self.music_list.blockSignals(True)
                self.music_list.setCurrentItem(it)
                self.music_list.blockSignals(False)
                path = it.data(Qt.ItemDataRole.UserRole)
                if not path:
                    b = self.scene_path if self.chk_use_scene_music.isChecked() else self.txt_improv_music_path.text()
                    cat = self.combo_improv_folders.currentText()
                    p = os.path.join(b, "music") if self.chk_use_scene_music.isChecked() else (
                        b if cat == "--- Root Folder ---" else os.path.join(b, cat))
                    path = os.path.join(p, it.text())
                if os.path.exists(path):
                    self.music_player.set_media(self.vlc_music.media_new(path))
                    self.music_player.play()
                    # Set volume after play() for directsound compatibility
                    QTimer.singleShot(50, lambda: self.music_player.audio_set_volume(
                        self.music_vol_slider.value()))
                    self._now_playing_name = it.text()
                    self._now_playing_set_at = datetime.datetime.now()
                    self.update_info_labels()
                    self._music_loaded_path = path
                    self.log_audio_event("music", it.text(), path)
                    if self.chk_music_waveform.isChecked():
                        self._show_music_waveform(path)

            if self.music_player.is_playing():
                self.fade_music_volume(0, 800, swap)
            else:
                # Not playing — just swap directly, no fade needed
                swap()

    def next_music(self):
        [self.music_list.setCurrentRow(self.get_next_index()),
         self.play_music()] if self.music_list.count() > 0 else None

    def prev_music(self):
        [self.music_list.setCurrentRow(self.get_prev_index()),
         self.play_music()] if self.music_list.count() > 0 else None

    def play_sfx(self):
        it = self.sfx_list.currentItem() or (self.sfx_list.item(0) if self.sfx_list.count() > 0 else None)
        if it:
            path = it.data(Qt.ItemDataRole.UserRole) or os.path.join(self.scene_path, "sfx", it.text())
            self._play_sfx_file(path, it.text())

    def _play_sfx_file(self, path, label):
        # Plays one SFX file (looping, like the Play button). Independent of the SFX list.
        if path and os.path.exists(path):
            m = self.vlc_sfx.media_new(path)
            m.add_option('input-repeat=65535')
            self.sfx_player.set_media(m)
            self.sfx_player.play()
            QTimer.singleShot(50, lambda: self.sfx_player.audio_set_volume(
                self.sfx_vol_slider.value()))
            self.lbl_sfx_playing.setText(f"SFX: {label}")
            self._sfx_loaded_path = path
            self.log_audio_event("sfx", label, path)
            if self.chk_sfx_waveform.isChecked():
                self._show_sfx_waveform(path)

    def stop_sfx_only(self):
        self.sfx_player.stop()
        self.lbl_sfx_playing.setText("SFX: ---")

    def stop_all_audio(self):
        self.fade_music_volume(0, 800, self.music_player.stop)
        self.sfx_player.stop()
        self._now_playing_name = None
        self.lbl_now_playing.setText("Now: ---")
        self.lbl_sfx_playing.setText("SFX: ---")
        # Restore volumes so next play starts at correct level
        QTimer.singleShot(900, lambda: (
            self.music_player.audio_set_volume(self.music_vol_slider.value()),
            self.sfx_player.audio_set_volume(self.sfx_vol_slider.value())
        ))

    def play_all(self):
        self.play_music();
        self.play_sfx()

    # Audio
    def auto_mark_recording(self, label):
        if not self.recorder.is_recording or self.recorder.is_paused:
            return
        elapsed = self.recorder.elapsed
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        tc = f"{h:02d}:{m:02d}:{s:02d}"
        entry = {
            "timecode": tc,
            "elapsed_seconds": elapsed,
            "chunk": os.path.basename(self.recorder.chunk_files[-1]) if self.recorder.chunk_files else "",
            "note": label
        }
        self._markers.append(entry)
        self._save_markers()

    def transcribing(self):
        d = self._transcribe_dlg
        try:
            return bool(d is not None and d.running)
        except Exception:
            return False

    def shutdown_transcriber(self):
        d = self._transcribe_dlg
        if d is not None:
            try:
                d.shutdown()
            except Exception:
                pass

    def _on_transcribe_cfg(self, cfg):
        self.transcribe_config = cfg
        self.save_config()

    def open_transcriber(self):
        if transcribe_dialog is None:
            QMessageBox.warning(self, "Transcribe", "transcribe_dialog.py / transcriber.py are missing next to the program.")
            return
        d = self._transcribe_dlg
        try:
            if d is not None and d.isVisible():
                d.raise_(); d.activateWindow()
                return
        except RuntimeError:
            pass
        files = [f for f in getattr(self.recorder, "chunk_files", []) if os.path.isfile(f)] \
            if not self.recorder.is_recording else []

        def busy():
            if self.recorder.is_recording:
                return "A recording is running. Transcribe after the session so the recording is not disturbed."
            return None

        def playing():
            try:
                return bool(self.music_player.is_playing() or self.sfx_player.is_playing())
            except Exception:
                return False
        self._transcribe_dlg = transcribe_dialog.TranscribeDialog(
            self, _app_dir(), _find_ffmpeg(), self.transcribe_config, self._on_transcribe_cfg, files, busy, playing)
        self._transcribe_dlg.show()

    def rec_start(self):
        if self.transcribing():
            QMessageBox.warning(self, "Recording", "A transcription is still running. Stop it first (📝 Transcribe > Stop) "
                                "so it does not disturb the recording.")
            return
        today = datetime.date.today().strftime("%Y-%m-%d")
        suggested = f"{today}_session"
        dlg = RecordingSetupDialog(self, suggested, self.recording_path)
        if not dlg.exec():
            return
        base_name, rec_path, device_index = dlg.get_values()
        if not base_name or not rec_path:
            QMessageBox.warning(self, "Recording", "Please provide a filename and folder.")
            return

        # Save path for next time
        self.recording_path = rec_path
        self.save_config()

        # Create session subfolder: rec_path / YYYY-MM-DD_basename /
        session_folder = os.path.join(rec_path, f"{today}_{base_name}")
        os.makedirs(session_folder, exist_ok=True)

        # Markers file sits alongside the chunks
        self._markers = []
        self._markers_path = os.path.join(session_folder, f"{base_name}_markers.json")
        self._save_markers()

        self.recorder.start(device_index, session_folder, base_name)
        # Log wall-clock start time for later video sync
        self._markers.append({
            "event": "RECORDING_START",
            "datetime": datetime.datetime.now().isoformat(timespec='seconds'),
            "timecode": "00:00:00",
            "elapsed_seconds": 0.0,
            "chunk": "",
            "note": "Recording started"
        })
        self._save_markers()

        # Separate log of music / SFX / Quick Fire changes, next to the markers file
        self._audio_log = []
        self._audio_log_path = os.path.join(session_folder, f"{base_name}_audio_log.json")
        self._save_audio_log()
        try:
            if self.music_player.is_playing() and self._now_playing_name:
                self.log_audio_event("music", self._now_playing_name,
                                     getattr(self, "_music_loaded_path", "") or "",
                                     note="already playing at recording start")
            if self.sfx_player.is_playing() and getattr(self, "_sfx_loaded_path", None):
                self.log_audio_event("sfx", os.path.basename(self._sfx_loaded_path),
                                     self._sfx_loaded_path, note="already playing at recording start")
        except Exception as e:
            logging.warning(f"Audio log start state failed: {e}")

        self._rec_timer.start(500)
        self.btn_rec_start.setEnabled(False)
        self.btn_rec_start.setVisible(False)
        self.rec_live.setVisible(True)
        self.btn_rec_pause.setEnabled(True)
        self.btn_rec_stop.setEnabled(True)
        self.btn_rec_mark.setEnabled(True)
        self.btn_rec_stop.setStyleSheet("background-color: #b71c1c; color: white; font-weight: bold;")

    def rec_pause_resume(self):
        if self.recorder.is_paused:
            self.recorder.resume()
            self.btn_rec_pause.setText("⏸ Pause")
            self.btn_rec_pause.setStyleSheet("background-color: #555; color: white; font-weight: bold;")
        else:
            self.recorder.pause()
            self.btn_rec_pause.setText("▶ Resume")
            self.btn_rec_pause.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")

    def rec_stop(self):
        if QMessageBox.question(
                self, "End Recording", "Stop and finalise the session recording?"
        ) != QMessageBox.StandardButton.Yes:
            return
        elapsed = self.recorder.elapsed
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        self._markers.append({
            "event": "RECORDING_END",
            "datetime": datetime.datetime.now().isoformat(timespec='seconds'),
            "timecode": f"{h:02d}:{m:02d}:{s:02d}",
            "elapsed_seconds": elapsed,
            "chunk": os.path.basename(self.recorder.chunk_files[-1]) if self.recorder.chunk_files else "",
            "note": "Recording ended"
        })
        self._save_markers()
        self.recorder.stop()
        self._rec_timer.stop()
        self._save_markers()
        self.lbl_rec_time.setText("00:00:00")
        self.btn_rec_start.setEnabled(True)
        self.btn_rec_start.setVisible(True)
        self.rec_live.setVisible(False)
        self.btn_rec_pause.setEnabled(False)
        self.btn_rec_pause.setText("⏸ Pause")
        self.btn_rec_pause.setStyleSheet("background-color: #555; color: white; font-weight: bold;")
        self.btn_rec_stop.setEnabled(False)
        self.btn_rec_stop.setStyleSheet("background-color: #333; color: #aaa; font-weight: bold;")
        self.btn_rec_mark.setEnabled(False)
        chunks = self.recorder.chunk_files
        QMessageBox.information(
            self, "Recording Saved",
            f"Recording complete.\n{len(chunks)} chunk(s) saved to:\n{self.recorder.output_folder}"
        )

    def rec_mark(self):
        if not self.recorder.is_recording or self.recorder.is_paused:
            return
        elapsed = self.recorder.elapsed
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        tc = f"{h:02d}:{m:02d}:{s:02d}"
        note, ok = QInputDialog.getText(
            self, "Mark Recording",
            f"Timecode {tc} — add a note (optional):"
        )
        entry = {
            "timecode": tc,
            "elapsed_seconds": elapsed,
            "chunk": os.path.basename(self.recorder.chunk_files[-1]) if self.recorder.chunk_files else "",
            "note": note.strip() if ok else ""
        }
        self._markers.append(entry)
        self._save_markers()
        self.lbl_script_status.setText(f"📍 Marked {tc}")
        QTimer.singleShot(3000, lambda: self.lbl_script_status.setText("Status: Idle"))

    def log_audio_event(self, kind, name, path="", note=""):
        # Cheap by design: does nothing unless a recording is running, and a track change only
        # happens every now and then, so one small file write per change costs nothing.
        # It must never disturb playback, so every error is swallowed.
        try:
            if not self._audio_log_path or not self.recorder.is_recording:
                return
            elapsed = self.recorder.elapsed
            h, rem = divmod(int(elapsed), 3600)
            m, s = divmod(rem, 60)
            entry = {
                "kind": kind,
                "name": name,
                "folder": os.path.basename(os.path.dirname(path)) if path else "",
                "timecode": f"{h:02d}:{m:02d}:{s:02d}",
                "elapsed_seconds": round(elapsed, 2),
                "datetime": datetime.datetime.now().isoformat(timespec='seconds'),
                "scene": os.path.basename(self.scene_path) if self.scene_path else "",
                "recording_paused": bool(self.recorder.is_paused),
            }
            if note:
                entry["note"] = note
            self._audio_log.append(entry)
            self._save_audio_log()
        except Exception as e:
            logging.warning(f"Audio log failed: {e}")

    def _save_audio_log(self):
        if not self._audio_log_path:
            return
        try:
            tmp = self._audio_log_path + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._audio_log, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._audio_log_path)
        except Exception as e:
            logging.warning(f"Failed to save audio log: {e}")

    def _save_markers(self):
        if self._markers_path:
            try:
                with open(self._markers_path, 'w') as f:
                    json.dump(self._markers, f, indent=2)
            except Exception as e:
                logging.warning(f"Failed to save markers: {e}")

    def _update_rec_display(self):
        if self.recorder.is_recording and not self.recorder.is_paused:
            e = int(self.recorder.elapsed)
            h, rem = divmod(e, 3600)
            m, s = divmod(rem, 60)
            self.lbl_rec_time.setText(f"{h:02d}:{m:02d}:{s:02d}")
            self.lbl_rec_time.setStyleSheet(
                "color: #ef5350; font-family: 'Consolas'; font-size: 13px; font-weight: bold;")
        elif self.recorder.is_paused:
            # Blink label while paused
            current = self.lbl_rec_time.styleSheet()
            if "#ef5350" in current:
                self.lbl_rec_time.setStyleSheet(
                    "color: #555; font-family: 'Consolas'; font-size: 13px; font-weight: bold;")
            else:
                self.lbl_rec_time.setStyleSheet(
                    "color: #ef5350; font-family: 'Consolas'; font-size: 13px; font-weight: bold;")


if __name__ == "__main__":
    # Explicit DPI awareness — prevents Windows from applying its own
    # bitmap scaling/stretching to the window when it's not running at
    # a "native" size (e.g. resized to half-screen). That OS-level
    # stretching goes through the compositor and adds latency, which
    # showed up as the quickfire waveform playhead drifting out of sync
    # whenever the window wasn't maximized/fullscreen.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # fallback for older Windows
        except Exception:
            pass
    try:
        # own taskbar identity so Windows shows OUR icon instead of the generic Python one
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Astratera.Weltentor")
    except Exception:
        pass
    app = QApplication(sys.argv);
    _ico = _resource_path("assets/icon.png") or _resource_path("assets/icon.ico")
    if _ico:
        app.setWindowIcon(QIcon(_ico))
    window = DirectorApp();
    window.show();

    def _qf_shutdown():
        try:
            if window.qf_engine is not None:
                window.qf_engine.close()
        except Exception:
            pass
    app.aboutToQuit.connect(_qf_shutdown)
    app.aboutToQuit.connect(window.shutdown_transcriber)
    sys.exit(app.exec())