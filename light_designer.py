"""
💡 Light Designer: connect a Philips Hue bridge, design a light scene, save it as a normal script.

All talking to the bridge happens on helper threads, so a slow or missing bridge never freezes the window.
The result is an ordinary .json script (see lights.py); running it does not need this window.
"""
import json
import logging
import os
import threading

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
                             QDoubleSpinBox, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QSlider, QSplitter, QStyle, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

import lights as L

ID_ROLE = Qt.ItemDataRole.UserRole
BAD_NAME_CHARS = '\\/:*?"<>|'


class _Bg(QObject):
    done = pyqtSignal(int, object, object)      # token, result, error
    progress = pyqtSignal(str)
    problem = pyqtSignal(str)


def swatch_icon(rgb):
    pm = QPixmap(18, 18)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if rgb is None:
        p.setPen(QPen(QColor(130, 130, 130), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
    else:
        p.setPen(QPen(QColor(20, 20, 20), 1))
        p.setBrush(QColor(*rgb))
    p.drawEllipse(2, 2, 14, 14)
    p.end()
    return QIcon(pm)


def clean_script_name(name):
    name = (name or "").strip()
    if name.lower().endswith(".json"):
        name = name[:-5].strip()
    if not name or any(c in name for c in BAD_NAME_CHARS):
        return None
    return name + ".json"


PRESETS = [("Red", (255, 0, 0)), ("Orange", (255, 110, 0)), ("Amber", (255, 170, 0)), ("Yellow", (255, 235, 0)),
           ("Green", (0, 220, 40)), ("Teal", (0, 200, 160)), ("Cyan", (0, 220, 255)), ("Blue", (0, 70, 255)),
           ("Purple", (140, 0, 255)), ("Pink", (255, 70, 180)), ("Magenta", (255, 0, 140)), ("White", (255, 255, 255))]

_SLIDER_CSS = ("QSlider::groove:horizontal {height: 12px; border-radius: 6px; background: %s;}"
               "QSlider::handle:horizontal {background: #ffffff; border: 2px solid #202020; width: 12px; margin: -4px 0; border-radius: 8px;}")


class ColorPicker(QWidget):
    """Small colour chooser: quick colours plus hue and saturation sliders (brightness has its own slider)."""
    changed = pyqtSignal(QColor)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        prow = QHBoxLayout()
        prow.setSpacing(4)
        for name, rgb in PRESETS:
            b = QPushButton()
            b.setFixedSize(30, 24)
            b.setToolTip(name)
            b.setStyleSheet("background-color: rgb(%d,%d,%d); border: 1px solid #202020; border-radius: 4px;" % rgb)
            b.clicked.connect(lambda _=False, c=rgb: self.setColor(QColor(*c), emit=True))
            prow.addWidget(b)
        prow.addStretch(1)
        lay.addLayout(prow)
        lay.addWidget(QLabel("Hue"))
        self.sl_h = QSlider(Qt.Orientation.Horizontal)
        self.sl_h.setRange(0, 359)
        stops = ", ".join(f"stop:{i / 6:.3f} hsv({i * 60 % 360},255,255)" for i in range(7))
        self.sl_h.setStyleSheet(_SLIDER_CSS % f"qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops})")
        lay.addWidget(self.sl_h)
        lay.addWidget(QLabel("Saturation (left = white, right = full colour)"))
        self.sl_s = QSlider(Qt.Orientation.Horizontal)
        self.sl_s.setRange(0, 100)
        self.sl_s.setValue(100)
        lay.addWidget(self.sl_s)
        self.preview = QLabel()
        self.preview.setFixedHeight(18)
        lay.addWidget(self.preview)
        self.sl_h.valueChanged.connect(self._moved)
        self.sl_s.valueChanged.connect(self._moved)
        self._paint()

    def color(self):
        return QColor.fromHsv(self.sl_h.value(), int(self.sl_s.value() * 255 / 100), 255)

    def _paint(self):
        full = QColor.fromHsv(self.sl_h.value(), 255, 255).name()
        self.sl_s.setStyleSheet(_SLIDER_CSS % f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffffff, stop:1 {full})")
        self.preview.setStyleSheet(f"background-color: {self.color().name()}; border: 1px solid #202020; border-radius: 4px;")

    def _moved(self, _v):
        self._paint()
        self.changed.emit(self.color())

    def setColor(self, color, emit=False):
        h = color.hsvHue()
        for w in (self.sl_h, self.sl_s):
            w.blockSignals(True)
        if h >= 0:                                  # grey has no hue: keep the old one
            self.sl_h.setValue(h)
        self.sl_s.setValue(int(round(color.hsvSaturation() * 100 / 255)))
        for w in (self.sl_h, self.sl_s):
            w.blockSignals(False)
        self._paint()
        if emit:
            self.changed.emit(self.color())

    def currentColor(self):
        return self.color()


class LightTree(QTreeWidget):
    """Tree of rooms and lights. A click on a light's colour circle switches that light on or off."""
    iconClicked = pyqtSignal(object)

    def icon_zone(self, item):
        """(left, right) x range of the circle in viewport coordinates."""
        r = self.visualItemRect(item)
        ind = self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, None, self)
        left = r.left() + ind + 4
        return left, left + 26

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position().toPoint()
            item = self.itemAt(pos)
            if item is not None and item.childCount() == 0 and item.data(0, ID_ROLE) is not None:
                lo, hi = self.icon_zone(item)
                if lo <= pos.x() <= hi:
                    e.accept()
                    self.iconClicked.emit(item)
                    return
        super().mousePressEvent(e)


class LightDesigner(QDialog):
    def __init__(self, parent, lights_cfg, scripts_dir, on_cfg_changed=None, on_saved=None, edit_script=None):
        super().__init__(parent)
        self.setWindowTitle("💡 Light Designer")
        self.resize(960, 600)
        self.cfg = json.loads(json.dumps(lights_cfg or {}))
        self.scripts_dir = scripts_dir
        self.on_cfg_changed = on_cfg_changed
        self.on_saved = on_saved
        self._edit_script = edit_script

        self.client = None
        self.sender = None
        self.lights = {}            # id -> {"name","type","state"}
        self.groups = {}
        self.model = {}             # id -> {"include": bool, "body": dict}
        self.snapshot = {}          # id -> state when the bridge was connected (for "Restore")
        self.touched = set()
        self.delays = {}            # id -> delay kept from a loaded script
        self.loaded_seq = []        # order of a loaded script: ("light", id) / ("raw", step)
        self.loaded_name = None
        self._busy_pair = None      # cancel Event while pairing
        self._closed = False
        self._tok = 0
        self._cbs = {}
        self._items = {}            # light id -> tree item
        self._found = []

        self._bg_obj = _Bg()
        self._bg_obj.done.connect(self._on_bg_done)
        self._bg_obj.progress.connect(lambda m: self.lbl_pair.setText(m))
        self._bg_obj.problem.connect(lambda m: self._status(m, bad=True))

        self._build_ui()
        self._enable_connected(False)
        self._sync_editor()
        hue = self.cfg.get("hue") or {}
        if hue.get("ip"):
            self.ed_ip.setText(hue["ip"])
            self._connect(auto=True)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)

        box = QGroupBox("Hue bridge")
        bl = QHBoxLayout(box)
        self.lbl_bridge = QLabel("Not connected")
        self.lbl_bridge.setMinimumWidth(220)
        bl.addWidget(self.lbl_bridge, 1)
        self.btn_find = QPushButton("🔍 Find bridge")
        self.btn_find.clicked.connect(self._find)
        bl.addWidget(self.btn_find)
        self.ed_ip = QLineEdit()
        self.ed_ip.setPlaceholderText("or type the bridge IP, e.g. 192.168.1.20")
        self.ed_ip.setMinimumWidth(240)
        bl.addWidget(self.ed_ip)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self._connect_clicked)
        bl.addWidget(self.btn_connect)
        root.addWidget(box)
        self.lbl_pair = QLabel("")
        self.lbl_pair.setStyleSheet("color: #ffb74d;")
        root.addWidget(self.lbl_pair)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # ---- left: rooms and lights
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Tick the lights that belong to this script. Select lights to edit them."))
        self.tree = LightTree()
        self.tree.iconClicked.connect(self._toggle_light)
        self.tree.setHeaderHidden(True)
        self.tree.setToolTip("Click a light's circle to switch that light on or off.")
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._sync_editor)
        self.tree.itemChanged.connect(self._on_item_changed)
        ll.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.btn_capture = QPushButton("📷 Capture current look")
        self.btn_capture.setToolTip("Reads how the selected lights look right now (all lights if none is selected) "
                                    "and takes that over. Set your lights in the Hue app or by hand, then capture.")
        self.btn_capture.clicked.connect(self._capture)
        row.addWidget(self.btn_capture)
        self.btn_refresh = QPushButton("↻ Refresh")
        self.btn_refresh.clicked.connect(self._refresh)
        row.addWidget(self.btn_refresh)
        ll.addLayout(row)
        split.addWidget(left)

        # ---- right: editor
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.editor = QGroupBox("Selected lights")
        el = QVBoxLayout(self.editor)
        mrow = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_btns = {}
        for key, text in (("off", "Off"), ("white", "On / White"), ("color", "Color")):
            b = QPushButton(text)
            b.setCheckable(True)
            self.mode_group.addButton(b)
            self.mode_btns[key] = b
            b.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            mrow.addWidget(b)
        self.mode_group.setExclusive(True)
        el.addLayout(mrow)

        self.lbl_bri = QLabel("Brightness")
        el.addWidget(self.lbl_bri)
        self.sl_bri = QSlider(Qt.Orientation.Horizontal)
        self.sl_bri.setRange(1, 254)
        self.sl_bri.setValue(254)
        self.sl_bri.valueChanged.connect(self._set_bri)
        el.addWidget(self.sl_bri)

        self.lbl_ct = QLabel("White tone:  cool  ←→  warm")
        el.addWidget(self.lbl_ct)
        self.sl_ct = QSlider(Qt.Orientation.Horizontal)
        self.sl_ct.setRange(L.CT_MIN, L.CT_MAX)
        self.sl_ct.setValue(366)
        self.sl_ct.valueChanged.connect(self._set_ct)
        el.addWidget(self.sl_ct)

        self.color_dlg = ColorPicker()
        self.color_dlg.changed.connect(self._set_color)
        el.addWidget(self.color_dlg)
        el.addStretch(1)
        rl.addWidget(self.editor, 1)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("Fade time (seconds):"))
        self.sp_fade = QDoubleSpinBox()
        self.sp_fade.setRange(0.0, 600.0)
        self.sp_fade.setSingleStep(0.1)
        self.sp_fade.setDecimals(1)
        self.sp_fade.setValue(0.4)
        self.sp_fade.setToolTip("How long the lights take to change. 0 = instantly. Saved into the script.")
        frow.addWidget(self.sp_fade)
        frow.addStretch(1)
        self.chk_live = QCheckBox("Live preview")
        self.chk_live.setChecked(True)
        self.chk_live.setToolTip("Send every change to the selected lights while you edit.")
        frow.addWidget(self.chk_live)
        rl.addLayout(frow)

        arow = QHBoxLayout()
        self.btn_apply = QPushButton("▶ Apply all ticked lights")
        self.btn_apply.clicked.connect(self._apply_all)
        arow.addWidget(self.btn_apply)
        self.btn_restore = QPushButton("⟲ Restore original lights")
        self.btn_restore.setToolTip("Puts every light you changed back to how it was when you connected.")
        self.btn_restore.clicked.connect(self._restore)
        arow.addWidget(self.btn_restore)
        rl.addLayout(arow)

        sbox = QGroupBox("Script")
        sl = QVBoxLayout(sbox)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Edit existing:"))
        self.cmb_existing = QComboBox()
        self.cmb_existing.setMinimumWidth(180)
        r1.addWidget(self.cmb_existing, 1)
        self.btn_load = QPushButton("Load")
        self.btn_load.clicked.connect(self._load_clicked)
        r1.addWidget(self.btn_load)
        sl.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Name:"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("e.g. Tavern evening")
        r2.addWidget(self.ed_name, 1)
        self.btn_save = QPushButton("💾 Save script")
        self.btn_save.setStyleSheet("background-color: #673ab7; color: white; font-weight: bold;")
        self.btn_save.clicked.connect(self._save)
        r2.addWidget(self.btn_save)
        sl.addLayout(r2)
        self.lbl_kept = QLabel("")
        self.lbl_kept.setStyleSheet("color: #9e9e9e;")
        sl.addWidget(self.lbl_kept)
        rl.addWidget(sbox)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)

        bottom = QHBoxLayout()
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        bottom.addWidget(self.lbl_status, 1)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        if not self.scripts_dir:
            self.btn_save.setEnabled(False)
            self.btn_save.setToolTip("Open a scene first: scripts are saved inside the scene.")
        self._fill_existing()

    # ------------------------------------------------------------------ helpers
    def _status(self, text, bad=False):
        self.lbl_status.setStyleSheet("color: #ef5350;" if bad else "color: #9ccc65;")
        self.lbl_status.setText(text)

    def _safe(self, e):
        return L.redact(e, self.cfg)

    def _bg(self, fn, cb):
        self._tok += 1
        tok = self._tok
        self._cbs[tok] = cb
        obj = self._bg_obj

        def run():
            try:
                res, err = fn(), None
            except Exception as e:              # shown to the person, never crashes the helper thread
                res, err = None, e
            try:
                obj.done.emit(tok, res, err)
            except RuntimeError:
                pass
        threading.Thread(target=run, name="light-designer", daemon=True).start()

    def _on_bg_done(self, tok, res, err):
        cb = self._cbs.pop(tok, None)
        if cb and not self._closed:
            if err is not None and not isinstance(err, L.ScriptError):
                logging.warning(f"Light Designer: {self._safe(err)}")
            cb(res, err)

    def _enable_connected(self, on):
        for w in (self.tree, self.btn_capture, self.btn_refresh, self.btn_apply, self.btn_restore,
                  self.btn_load, self.cmb_existing, self.chk_live, self.sp_fade):
            w.setEnabled(on)

    def _fill_existing(self):
        self.cmb_existing.clear()
        if self.scripts_dir and os.path.isdir(self.scripts_dir):
            for n in sorted(os.listdir(self.scripts_dir), key=str.lower):
                if n.lower().endswith(".json"):
                    self.cmb_existing.addItem(n)

    # ------------------------------------------------------------------ connecting
    def _find(self):
        self.btn_find.setEnabled(False)
        self._status("Searching the network for a Hue bridge...")
        self._bg(L.discover, self._found_bridges)

    def _found_bridges(self, res, err):
        self.btn_find.setEnabled(True)
        if err is not None or not res:
            self._status("No bridge found automatically. Type its IP address instead "
                         "(Hue app: Settings > Bridges, or your router's device list).", bad=True)
            return
        self._found = res
        pick = res[0]
        if len(res) > 1:
            names = [f"{r['name']} ({r['ip']})" for r in res]
            choice, ok = QInputDialog.getItem(self, "Hue bridges", "Several bridges found:", names, 0, False)
            if not ok:
                return
            pick = res[names.index(choice)]
        self.ed_ip.setText(pick["ip"])
        self._status(f"Found {pick['name']} at {pick['ip']}. Press Connect.")

    def _connect_clicked(self):
        if self._busy_pair is not None:
            self._busy_pair.set()
            return
        self._connect(auto=False)

    def _connect(self, auto):
        ip = self.ed_ip.text().strip().replace("http://", "").replace("https://", "").strip("/")
        if not ip:
            self._status("Click Find bridge or type the bridge's IP address first.", bad=True)
            return
        hue = self.cfg.get("hue") or {}
        key = hue.get("key") if hue.get("ip") == ip else None
        cancel = threading.Event()
        allow_pair = not auto
        progress = self._bg_obj.progress

        def job():
            client = L.HueClient(ip)
            info = client.probe()
            if key:
                client.key = key
                try:
                    lights_ = client.lights()
                    return info, client, lights_, client.groups()
                except L.Unauthorized:
                    client.key = None
                    if not allow_pair:
                        raise
            if not allow_pair:
                raise L.ScriptError("Not paired yet.")
            progress.emit("Press the round link button on top of your Hue bridge now... (30 s)")
            client.pair(cancel, 30, progress=lambda left: progress.emit(
                f"Press the round link button on top of your Hue bridge now... ({left} s)"))
            return info, client, client.lights(), client.groups()

        self.btn_find.setEnabled(False)
        self.ed_ip.setEnabled(False)
        if allow_pair:
            self._busy_pair = cancel
            self.btn_connect.setText("Cancel")
        else:
            self.btn_connect.setEnabled(False)
        self._status("Connecting...")
        self._bg(job, lambda res, err: self._connected(res, err, auto))

    def _connected(self, res, err, auto):
        self._busy_pair = None
        self.btn_connect.setText("Connect")
        self.btn_connect.setEnabled(True)
        self.btn_find.setEnabled(True)
        self.ed_ip.setEnabled(True)
        self.lbl_pair.setText("")
        if err is not None:
            if auto:
                self.lbl_bridge.setText("Not connected")
                self._status(f"Could not reach the saved bridge ({self._safe(err)}). Check the address and press Connect.",
                             bad=True)
            else:
                self._status(self._safe(err), bad=True)
            return
        info, client, lights_, groups = res
        self.client = client
        client.scheme = info["scheme"]
        self.cfg["hue"] = {"ip": client.ip, "key": client.key, "scheme": info["scheme"], "name": info["name"]}
        if self.on_cfg_changed:
            try:
                self.on_cfg_changed(json.loads(json.dumps(self.cfg)))
            except Exception as e:
                logging.warning(f"Could not store light settings: {e}")
        self.lbl_bridge.setText(f"✔ {info['name']} ({client.ip}, {info['scheme']})")
        if self.sender:
            self.sender.stop()
        self.sender = L.LiveSender(client, on_error=lambda m: self._emit_problem(m))
        self.snapshot = {i: dict(d.get("state", {})) for i, d in lights_.items() if isinstance(d, dict)}
        self.touched.clear()
        self._load_lights(lights_, groups, reset=True)
        self._enable_connected(True)
        self._status(f"Connected. {len(self.lights)} lights found.")
        if self._edit_script:
            name, self._edit_script = self._edit_script, None
            i = self.cmb_existing.findText(name)
            if i >= 0:
                self.cmb_existing.setCurrentIndex(i)
                self._load_script(name)

    def _emit_problem(self, msg):
        try:
            self._bg_obj.problem.emit(self._safe(msg))
        except RuntimeError:
            pass

    # ------------------------------------------------------------------ lights tree
    @staticmethod
    def _num_key(s):
        return (0, int(s)) if str(s).isdigit() else (1, str(s))

    def _load_lights(self, lights_, groups, reset):
        self.lights = {i: d for i, d in lights_.items() if isinstance(d, dict)}
        self.groups = groups if isinstance(groups, dict) else {}
        old = self.model
        self.model = {}
        for i, d in self.lights.items():
            if not reset and i in old:
                self.model[i] = old[i]
            else:
                self.model[i] = {"include": False, "body": L.capture_body(d.get("state"))}
        self._rebuild_tree()

    def _rebuild_tree(self):
        selected = set(self._selected_ids())
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items = {}
        placed = set()
        rooms = sorted((g for g in self.groups.values() if isinstance(g, dict) and g.get("type") == "Room"),
                       key=lambda g: str(g.get("name", "")).lower())
        for g in rooms:
            ids = [i for i in sorted(g.get("lights", []), key=self._num_key) if i in self.lights]
            if not ids:
                continue
            self._add_parent(str(g.get("name", "Room")), ids)
            placed.update(ids)
        rest = [i for i in sorted(self.lights, key=self._num_key) if i not in placed]
        if rest:
            self._add_parent("Other lights", rest)
        self.tree.expandAll()
        for i in selected:
            if i in self._items:
                self._items[i].setSelected(True)
        self.tree.blockSignals(False)
        self._update_parent_checks()

    def _add_parent(self, title, ids):
        parent = QTreeWidgetItem([title])
        parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        parent.setCheckState(0, Qt.CheckState.Unchecked)
        self.tree.addTopLevelItem(parent)
        for i in ids:
            d = self.lights[i]
            it = QTreeWidgetItem([""])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setData(0, ID_ROLE, i)
            parent.addChild(it)
            self._items[i] = it
            self._refresh_item(i)

    def _refresh_item(self, lid):
        it, m, d = self._items.get(lid), self.model.get(lid), self.lights.get(lid, {})
        if not it or not m:
            return
        self.tree.blockSignals(True)          # changing text/icon must not look like a tick by the user
        try:
            self._fill_item(it, m, d, lid)
        finally:
            self.tree.blockSignals(False)

    def _fill_item(self, it, m, d, lid):
        unreachable = isinstance(d.get("state"), dict) and d["state"].get("reachable") is False
        it.setText(0, str(d.get("name", f"Light {lid}")) + ("  (unreachable)" if unreachable else ""))
        it.setIcon(0, swatch_icon(L.body_to_rgb(m["body"])))
        caps = L.light_caps(d)
        kinds = [k for k, v in (("dimmable", caps["dim"]), ("white tones", caps["ct"]), ("colors", caps["color"])) if v]
        it.setToolTip(0, f"{d.get('type', '')}: {', '.join(kinds) if kinds else 'on/off only'}")
        it.setCheckState(0, Qt.CheckState.Checked if m["include"] else Qt.CheckState.Unchecked)

    def _update_parent_checks(self):
        self.tree.blockSignals(True)
        for n in range(self.tree.topLevelItemCount()):
            p = self.tree.topLevelItem(n)
            states = {p.child(k).checkState(0) for k in range(p.childCount())}
            p.setCheckState(0, Qt.CheckState.Checked if states == {Qt.CheckState.Checked}
                            else Qt.CheckState.Unchecked if states <= {Qt.CheckState.Unchecked}
                            else Qt.CheckState.PartiallyChecked)
        self.tree.blockSignals(False)

    def _on_item_changed(self, item, col):
        # a tick on a light or on a room
        ids = [item.child(k).data(0, ID_ROLE) for k in range(item.childCount())] if item.childCount() \
            else [item.data(0, ID_ROLE)]
        state = item.checkState(0)
        if state == Qt.CheckState.PartiallyChecked:
            return
        for i in ids:
            if i in self.model:
                self.model[i]["include"] = state == Qt.CheckState.Checked
        self._update_parent_checks()

    def _selected_ids(self):
        ids = []
        for it in self.tree.selectedItems():
            if it.childCount():
                ids += [it.child(k).data(0, ID_ROLE) for k in range(it.childCount())]
            else:
                ids.append(it.data(0, ID_ROLE))
        out = []
        for i in ids:
            if i is not None and i in self.model and i not in out:
                out.append(i)
        return out

    def _set_included(self, ids, on=True):
        for i in ids:
            self.model[i]["include"] = on
            self._refresh_item(i)
        self._update_parent_checks()

    # ------------------------------------------------------------------ refresh / capture
    def _refresh(self):
        self._read_lights(lambda lights_, groups: self._load_lights(lights_, groups, reset=False), "Refreshed.")

    def _capture(self):
        ids = self._selected_ids() or list(self.model)

        def apply(lights_, groups):
            self._load_lights(lights_, groups, reset=False)
            for i in ids:
                if i in self.lights:
                    self.model[i]["body"] = L.capture_body(self.lights[i].get("state"))
                    self.model[i]["include"] = True
                    self._refresh_item(i)
            self._update_parent_checks()
            self._sync_editor()
            self._status(f"Captured the current look of {len(ids)} light(s).")
        self._read_lights(apply, None)

    def _read_lights(self, apply, done_text):
        if not self.client:
            return
        client = self.client
        self._status("Reading the lights...")

        def job():
            return client.lights(), client.groups()

        def done(res, err):
            if err is not None:
                self._status(self._safe(err), bad=True)
                return
            apply(*res)
            if done_text:
                self._status(done_text)
        self._bg(job, done)

    # ------------------------------------------------------------------ editor
    def _caps_of(self, lid):
        return L.light_caps(self.lights.get(lid, {}))

    def _sync_editor(self):
        ids = self._selected_ids()
        self.editor.setEnabled(bool(ids) and self.client is not None)
        self.editor.setTitle(f"Selected lights ({len(ids)})" if ids else "Selected lights (none selected)")
        if not ids:
            for w in (self.lbl_bri, self.sl_bri, self.lbl_ct, self.sl_ct, self.color_dlg, self.mode_btns["color"]):
                w.setVisible(False)
            return
        caps = [self._caps_of(i) for i in ids]
        can_color = all(c["color"] for c in caps)
        can_ct = all(c["ct"] for c in caps)
        can_dim = all(c["dim"] for c in caps)
        body = self.model[ids[0]]["body"]
        mode = L.body_mode(body)
        mode = "white" if mode == "dim" else mode
        widgets = (self.sl_bri, self.sl_ct, self.color_dlg, *self.mode_btns.values())
        for w in widgets:
            w.blockSignals(True)
        self.mode_btns[mode].setChecked(True)
        if "bri" in body:
            self.sl_bri.setValue(int(body["bri"]))
        if "ct" in body:
            self.sl_ct.setValue(int(body["ct"]))
        rgb = L.body_to_rgb(body)
        if mode == "color" and rgb:
            self.color_dlg.setColor(QColor(*rgb))
        for w in widgets:
            w.blockSignals(False)
        # What a light cannot do is not shown at all (a plug only has Off / On).
        self.mode_btns["white"].setText("On" if not (can_dim or can_ct) else "On / White")
        self.mode_btns["color"].setVisible(can_color)
        self.lbl_bri.setVisible(can_dim and mode != "off")
        self.sl_bri.setVisible(can_dim and mode != "off")
        self.lbl_ct.setVisible(can_ct and mode == "white")
        self.sl_ct.setVisible(can_ct and mode == "white")
        self.color_dlg.setVisible(can_color and mode == "color")

    def _toggle_light(self, item):
        """Click on the circle: switch this one light on or off."""
        lid = item.data(0, ID_ROLE)
        m = self.model.get(lid)
        if not m or not self.client:
            return
        caps = self._caps_of(lid)
        if m["body"].get("on") is False:
            body = m.get("last_on")
            if not body:
                st = (self.snapshot.get(lid) or {})
                body = L.capture_body(st) if st.get("on") else L.make_body("white", 254, 366)
            body = dict(body, on=True)
        else:
            m["last_on"] = dict(m["body"])
            body = {"on": False}
        m["body"] = L.fit_body(body, caps)
        m["include"] = True
        self._refresh_item(lid)
        self._update_parent_checks()
        self._live(lid)
        if lid in self._selected_ids():
            self._sync_editor()

    def _edit_selected(self, change):
        ids = self._selected_ids()
        if not ids:
            return
        for i in ids:
            body = change(dict(self.model[i]["body"]))
            self.model[i]["body"] = L.fit_body(body, self._caps_of(i))
            self.model[i]["include"] = True
            self._refresh_item(i)
            self._live(i)
        self._update_parent_checks()

    def _live(self, lid):
        if self.sender and self.chk_live.isChecked():
            self._send(lid)

    def _fade_value(self):
        return round(float(self.sp_fade.value()), 1)

    def _send(self, lid):
        body = dict(self.model[lid]["body"])
        body["transitiontime"] = int(L.clamp(round(self._fade_value() * 10), 0, 65535))
        self.touched.add(lid)
        self.sender.submit(lid, body)

    def _current_rgb(self):
        c = self.color_dlg.currentColor()
        return c.red(), c.green(), c.blue()

    def _set_mode(self, mode):
        bri, ct, rgb = self.sl_bri.value(), self.sl_ct.value(), self._current_rgb()
        self._edit_selected(lambda b: L.make_body(mode, bri, ct, rgb))
        self._sync_editor()

    def _set_bri(self, v):
        self._edit_selected(lambda b: dict(b, bri=int(v)) if b.get("on") else b)

    def _set_ct(self, v):
        def f(b):
            if not b.get("on"):
                return b
            for k in ("xy", "hue", "sat"):
                b.pop(k, None)
            b["ct"] = int(v)
            return b
        self._edit_selected(f)

    def _set_color(self, color):
        if not self.mode_btns["color"].isChecked():
            return
        hue, sat = L.rgb_to_hs(color.red(), color.green(), color.blue())

        def f(b):
            if not b.get("on"):
                return b
            for k in ("xy", "ct"):
                b.pop(k, None)
            b["hue"], b["sat"] = hue, sat
            return b
        self._edit_selected(f)

    # ------------------------------------------------------------------ apply / restore
    def _apply_all(self):
        if not self.sender:
            return
        ids = [i for i, m in self.model.items() if m["include"]]
        if not ids:
            self._status("Tick at least one light first.", bad=True)
            return
        for i in ids:
            self._send(i)
        self._status(f"Sent to {len(ids)} light(s).")

    def _restore(self, quiet=False, wait=0.0):
        if not self.sender:
            return
        ids = sorted(self.touched, key=self._num_key)
        for i in ids:
            if i in self.snapshot:
                self.sender.submit(i, L.capture_body(self.snapshot[i]))
        self.touched.clear()
        if wait:
            self.sender.wait_idle(wait)
        if not quiet:
            self._status(f"Restored {len(ids)} light(s).")

    # ------------------------------------------------------------------ script load / save
    def _load_clicked(self):
        name = self.cmb_existing.currentText()
        if name:
            self._load_script(name)

    def _load_script(self, name):
        path = os.path.join(self.scripts_dir or "", name)
        try:
            steps = L.load_script(path)
        except L.ScriptError as e:
            self._status(str(e), bad=True)
            return
        entries, fade = L.parse_script(steps, self.cfg)
        for m in self.model.values():
            m["include"] = False
        self.delays.clear()
        self.loaded_seq = []
        unknown = 0
        for e in entries:
            if "raw" in e:
                self.loaded_seq.append(("raw", e["raw"]))
                continue
            lid = e["light"]
            if lid not in self.model:                      # light no longer exists on this bridge: keep the step
                step = L.build_script([e])[0]
                self.loaded_seq.append(("raw", step))
                unknown += 1
                continue
            self.model[lid]["body"] = L.fit_body(e["body"], self._caps_of(lid))
            self.model[lid]["include"] = True
            if "delay" in e:
                self.delays[lid] = e["delay"]
            self.loaded_seq.append(("light", lid))
        if fade is not None:
            self.sp_fade.setValue(fade)
        for i in self.model:
            self._refresh_item(i)
        self._update_parent_checks()
        self._sync_editor()
        self.loaded_name = name
        self.ed_name.setText(name[:-5])
        raw = sum(1 for k, _ in self.loaded_seq if k == "raw")
        self.lbl_kept.setText(f"{raw} step(s) in this script are not light settings (or belong to missing lights) "
                              "and are kept unchanged when you save." if raw else "")
        self._status(f"Loaded {name}." + (f" {unknown} light(s) no longer exist on the bridge." if unknown else ""))

    def collect_steps(self):
        entries, used = [], set()
        for kind, val in self.loaded_seq:
            if kind == "raw":
                entries.append({"raw": val})
            elif self.model.get(val, {}).get("include"):
                entries.append(self._entry(val))
                used.add(val)
        for lid in self._tree_order():
            if self.model[lid]["include"] and lid not in used:
                entries.append(self._entry(lid))
        return L.build_script(entries, fade=self._fade_value())

    def _entry(self, lid):
        e = {"light": lid, "name": str(self.lights.get(lid, {}).get("name", "")),
             "body": L.fit_body(self.model[lid]["body"], self._caps_of(lid))}
        if lid in self.delays:
            e["delay"] = self.delays[lid]
        return e

    def _tree_order(self):
        out = []
        for n in range(self.tree.topLevelItemCount()):
            p = self.tree.topLevelItem(n)
            out += [p.child(k).data(0, ID_ROLE) for k in range(p.childCount())]
        return out

    def _save(self):
        if not self.scripts_dir:
            return
        name = clean_script_name(self.ed_name.text())
        if not name:
            self._status("Give the script a name (no \\ / : * ? \" < > |).", bad=True)
            return
        steps = self.collect_steps()
        if not steps:
            self._status("Tick at least one light first.", bad=True)
            return
        path = os.path.join(self.scripts_dir, name)
        if os.path.exists(path) and name != self.loaded_name:
            if QMessageBox.question(self, "Save script", f"{name} already exists. Replace it?") \
                    != QMessageBox.StandardButton.Yes:
                return
        try:
            os.makedirs(self.scripts_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(steps, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:
            self._status(f"Could not save: {e}", bad=True)
            return
        self.loaded_name = name
        self._fill_existing()
        self.cmb_existing.setCurrentIndex(max(0, self.cmb_existing.findText(name)))
        self._status(f"Saved {name} ({len(steps)} steps).")
        if self.on_saved:
            try:
                self.on_saved(name)
            except Exception as e:
                logging.warning(f"on_saved failed: {e}")

    # ------------------------------------------------------------------ closing
    def _finish(self):
        if self._closed:
            return
        if self.sender and self.touched:
            if QMessageBox.question(self, "Light Designer", "Put the lights back the way they were when you opened "
                                    "the designer?") == QMessageBox.StandardButton.Yes:
                self._restore(quiet=True, wait=3.0)
        self._closed = True
        if self._busy_pair is not None:
            self._busy_pair.set()
        if self.sender:
            self.sender.stop()

    def closeEvent(self, e):
        self._finish()
        super().closeEvent(e)

    def done(self, r):
        self._finish()
        super().done(r)
