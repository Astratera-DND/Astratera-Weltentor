"""
Windows for the special words of the Transcribe tool:
  WordsDialog          the list: add by hand, import from Kurator, add D&D terms, remove
  DndTermsDialog       pick which groups of D&D terms to add
  KuratorImportDialog  two steps: 1) pick the campaigns  2) pick the names (locations and NPCs are preselected)
The word logic itself lives in vocab.py.
"""
import logging
import os

from PyQt6.QtCore import QItemSelection, QItemSelectionModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDrag, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                             QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import vocab as V

GOOD = "color: #9ccc65;"
BAD = "color: #ef5350;"
DIM = "color: #9e9e9e;"
GROUP_COLORS = {V.SRC_TYPED: "#64b5f6", V.SRC_KURATOR: "#ffb74d", V.SRC_DND: "#ba68c8"}
GROUP_NAMES = {V.SRC_TYPED: "Typed", V.SRC_KURATOR: "Campaign", V.SRC_DND: "D&D terms"}


def hint_count(words, limit=300):
    """How many words from the top of the list fit into the engine hint (same rule as the real hint)."""
    n = length = 0
    for w in words:
        nxt = length + (2 if n else 0) + len(w)
        if nxt > limit:
            break
        length, n = nxt, n + 1
    return n


def preview_text(words, limit=12):
    """Short text for the transcription window: the first words and how many more there are."""
    if not words:
        return "No words yet. Scene names of the recording are added automatically."
    shown = ", ".join(words[:limit])
    more = len(words) - limit
    return f"{len(words)} words: {shown}" + (f" … and {more} more" if more > 0 else "")


# =====================================================================================================
class DndTermsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add D&D terms")
        self.resize(420, 420)
        self.keys = []
        lay = QVBoxLayout(self)
        lay.addWidget(self._wrap("Pick the groups to add. Words that are already in your list are skipped."))
        self.checks = []
        cats = V.dnd_categories()
        if not cats:
            lay.addWidget(self._wrap("The D&D term list is not part of this version."))
        for lang, title in (("English", "English names"), ("German", "German terms")):
            items = [c for c in cats if c[1] == lang]
            if not items:
                continue
            box = QGroupBox(title)
            bl = QVBoxLayout(box)
            for key, _, label, count in items:
                cb = QCheckBox(f"{label}  ({count})")
                cb.setChecked(True)
                cb.toggled.connect(self._update)
                cb.setProperty("key", key)
                self.checks.append(cb)
                bl.addWidget(cb)
            lay.addWidget(box)
        row = QHBoxLayout()
        b = QPushButton("All")
        b.clicked.connect(lambda: self._set_all(True))
        row.addWidget(b)
        b = QPushButton("None")
        b.clicked.connect(lambda: self._set_all(False))
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        note = self._wrap("Names of monsters, spells and rules terms from the free D&D 5.1 System Reference Document "
                          "(Wizards of the Coast, CC-BY-4.0) plus common German terms. Only names, no rules text.")
        note.setStyleSheet(DIM)
        lay.addWidget(note)
        lay.addStretch(1)
        row = QHBoxLayout()
        self.lbl = QLabel("")
        row.addWidget(self.lbl, 1)
        self.btn_ok = QPushButton("Add")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._accept)
        row.addWidget(self.btn_ok)
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        row.addWidget(b)
        lay.addLayout(row)
        self._update()

    @staticmethod
    def _wrap(text):
        l = QLabel(text)
        l.setWordWrap(True)
        return l

    def _set_all(self, on):
        for cb in self.checks:
            cb.setChecked(on)

    def _selected(self):
        return [cb.property("key") for cb in self.checks if cb.isChecked()]

    def _update(self, *_):
        n = len(V.dnd_words(self._selected()))
        self.lbl.setText(f"{n} terms selected")
        self.btn_ok.setEnabled(n > 0)

    def _accept(self):
        self.keys = self._selected()
        self.accept()

    def words(self):
        return V.dnd_words(self.keys)


# =====================================================================================================
class KuratorImportDialog(QDialog):
    """Step 1: where is the campaign data, which campaigns.  Step 2: which names."""

    def __init__(self, parent, path=""):
        super().__init__(parent)
        self.setWindowTitle("Import from Kurator")
        self.resize(560, 560)
        self.path = path or ""
        self.root = None
        self._raw = {}                  # category -> raw names
        self._state = {}                # (category, key) -> checked, remembers the person's choices
        self._bulk = False
        self.result_words = []
        lay = QVBoxLayout(self)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.stack.addWidget(self._page1())
        self.stack.addWidget(self._page2())
        if self.path:
            self._load_path(self.path)

    # ------------------------------------------------------------------ page 1
    def _page1(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        t = QLabel("<b>Step 1 of 2: your campaigns</b>")
        lay.addWidget(t)
        info = QLabel("Choose the 'campaign-data' folder of your Kurator session manager (the folder that contains "
                      "index.json).")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        self.ed_path = QLineEdit(self.path)
        self.ed_path.setPlaceholderText("...\\kurator-session-manager\\campaign-data")
        self.ed_path.editingFinished.connect(lambda: self._load_path(self.ed_path.text()))
        row.addWidget(self.ed_path, 1)
        b = QPushButton("Browse...")
        b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)
        self.lbl1 = QLabel("")
        self.lbl1.setWordWrap(True)
        lay.addWidget(self.lbl1)
        lay.addWidget(QLabel("Campaigns (tick one or more):"))
        self.lst = QListWidget()
        self.lst.itemChanged.connect(self._camps_changed)
        lay.addWidget(self.lst, 1)
        row = QHBoxLayout()
        b = QPushButton("All")
        b.clicked.connect(lambda: self._tick_camps(True))
        row.addWidget(b)
        b = QPushButton("None")
        b.clicked.connect(lambda: self._tick_camps(False))
        row.addWidget(b)
        row.addStretch(1)
        self.btn_next = QPushButton("Next  ▶")
        self.btn_next.setEnabled(False)
        self.btn_next.clicked.connect(self._to_page2)
        row.addWidget(self.btn_next)
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        row.addWidget(b)
        lay.addLayout(row)
        return w

    def _browse(self):
        start = self.ed_path.text().strip() or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "Choose the Kurator campaign-data folder", start)
        if d:
            self.ed_path.setText(d)
            self._load_path(d)

    def _msg1(self, text, bad=False):
        self.lbl1.setStyleSheet(BAD if bad else GOOD)
        self.lbl1.setText(text)

    def _load_path(self, path):
        path = (path or "").strip().strip('"')
        self.lst.clear()
        self.root = None
        self.btn_next.setEnabled(False)
        if not path:
            self._msg1("")
            return
        root = V.resolve_kurator_root(path)
        if not root:
            self._msg1("No Kurator campaign data found there. Pick the 'campaign-data' folder: it holds index.json and "
                       "a 'campaigns' folder.", bad=True)
            return
        try:
            camps = V.list_campaigns(root)
        except V.VocabError as e:
            self._msg1(str(e), bad=True)
            return
        self.root, self.path = root, root
        if self.ed_path.text() != root:
            self.ed_path.setText(root)
        if not camps:
            self._msg1("This folder has no campaigns yet.", bad=True)
            return
        self.lst.blockSignals(True)
        for c in camps:
            when = c["created"][:10]
            it = QListWidgetItem(f"{c['name']}" + (f"   ({when})" if when else "") +
                                 ("" if c["available"] else "   - no data"))
            it.setData(Qt.ItemDataRole.UserRole, c["id"])
            flags = it.flags() | Qt.ItemFlag.ItemIsUserCheckable
            if not c["available"]:
                flags &= ~Qt.ItemFlag.ItemIsEnabled
            it.setFlags(flags)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.lst.addItem(it)
        self.lst.blockSignals(False)
        self._msg1(f"{len(camps)} campaign{'s' if len(camps) != 1 else ''} found" + (", newest first." if len(camps) > 1 else "."))
        self._camps_changed()

    def _ticked(self):
        out = []
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if it.checkState() == Qt.CheckState.Checked and (it.flags() & Qt.ItemFlag.ItemIsEnabled):
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def _tick_camps(self, on):
        self.lst.blockSignals(True)
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if it.flags() & Qt.ItemFlag.ItemIsEnabled:
                it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.lst.blockSignals(False)
        self._camps_changed()

    def _camps_changed(self, *_):
        self.btn_next.setEnabled(bool(self._ticked()))

    # ------------------------------------------------------------------ page 2
    def _page2(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>Step 2 of 2: which names?</b>"))
        info = QLabel("Locations and NPCs are ticked. Untick what you do not need. Select several lines with Shift or "
                      "Ctrl and use the buttons below to tick or untick them together.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemChanged.connect(self._item_changed)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        b = QPushButton("Tick highlighted")
        b.clicked.connect(lambda: self._mark_selected(True))
        row.addWidget(b)
        b = QPushButton("Untick highlighted")
        b.clicked.connect(lambda: self._mark_selected(False))
        row.addWidget(b)
        row.addStretch(1)
        b = QPushButton("Tick all")
        b.clicked.connect(lambda: self._mark_all(True))
        row.addWidget(b)
        b = QPushButton("Untick all")
        b.clicked.connect(lambda: self._mark_all(False))
        row.addWidget(b)
        lay.addLayout(row)
        self.chk_split = QCheckBox("Split 'Name, Alias' and 'Name (Alias)' into separate words")
        self.chk_split.setChecked(True)
        self.chk_codes = QCheckBox("Remove codes like 'A3 -' and numbering like '01'")
        self.chk_codes.setChecked(True)
        for c in (self.chk_split, self.chk_codes):
            c.toggled.connect(self._rebuild)
            lay.addWidget(c)
        row = QHBoxLayout()
        self.lbl2 = QLabel("")
        row.addWidget(self.lbl2, 1)
        b = QPushButton("◀ Back")
        b.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        row.addWidget(b)
        self.btn_add = QPushButton("Add to word list")
        self.btn_add.setDefault(True)
        self.btn_add.clicked.connect(self._finish)
        row.addWidget(self.btn_add)
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        row.addWidget(b)
        lay.addLayout(row)
        return w

    def _to_page2(self):
        ids = self._ticked()
        if not ids:
            return
        cats, notes = V.load_campaigns(self.root, ids)
        if notes:
            QMessageBox.warning(self, "Import from Kurator", "Some campaigns could not be read:\n" + "\n".join(notes))
        if not any(cats.values()):
            self._msg1("The chosen campaigns have no locations, NPCs, quests or groups.", bad=True)
            return
        self._raw = cats
        self._state.clear()
        self._rebuild()
        self.stack.setCurrentIndex(1)

    def _rebuild(self, *_):
        if not self._raw:
            return
        self._bulk = True
        self.tree.clear()
        for key, label in V.CATEGORY_LABELS:
            words = V.names_to_words(self._raw.get(key, []), self.chk_split.isChecked(), self.chk_codes.isChecked())
            if not words:
                continue
            top = QTreeWidgetItem([f"{label}"])
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            top.setData(0, Qt.ItemDataRole.UserRole, None)
            self.tree.addTopLevelItem(top)
            for w in words:
                ch = QTreeWidgetItem([w])
                ch.setFlags((ch.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsAutoTristate)
                on = self._state.get((key, V.norm_key(w)), V.DEFAULT_ON.get(key, False))
                ch.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                ch.setData(0, Qt.ItemDataRole.UserRole, (key, V.norm_key(w)))
                top.addChild(ch)
            top.setText(0, f"{label}  ({len(words)})")
            top.setExpanded(V.DEFAULT_ON.get(key, False))
        self._bulk = False
        self._update2()

    def _leaves(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                yield top.child(j)

    def _item_changed(self, item, col):
        if self._bulk:
            return
        self._sync_state()
        self._update2()

    def _sync_state(self):
        for ch in self._leaves():
            self._state[ch.data(0, Qt.ItemDataRole.UserRole)] = ch.checkState(0) == Qt.CheckState.Checked

    def _mark_selected(self, on):
        self._bulk = True
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for it in self.tree.selectedItems():
            if it.childCount():                       # a category line: all of its words
                for j in range(it.childCount()):
                    it.child(j).setCheckState(0, state)
            else:
                it.setCheckState(0, state)
        self._bulk = False
        self._fix_parents()
        self._sync_state()
        self._update2()

    def _mark_all(self, on):
        self._bulk = True
        for ch in self._leaves():
            ch.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._bulk = False
        self._fix_parents()
        self._sync_state()
        self._update2()

    def _fix_parents(self):
        self._bulk = True
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            n = sum(1 for j in range(top.childCount()) if top.child(j).checkState(0) == Qt.CheckState.Checked)
            top.setCheckState(0, Qt.CheckState.Unchecked if n == 0 else
                              Qt.CheckState.Checked if n == top.childCount() else Qt.CheckState.PartiallyChecked)
        self._bulk = False

    def chosen(self):
        words = []
        for ch in self._leaves():
            if ch.checkState(0) == Qt.CheckState.Checked:
                words, _, _ = V.merge_words(words, [ch.text(0)])
        return words

    def _update2(self):
        n = len(self.chosen())
        self.lbl2.setText(f"{n} words selected")
        self.btn_add.setEnabled(n > 0)

    def _finish(self):
        self.result_words = self.chosen()
        self.accept()


# =====================================================================================================
class WordList(QListWidget):
    """A list whose words can be dragged to a new place. Qt would move the words one by one (very slow for hundreds
    of words), so the drop is only reported: `dropped(row)` and the owner reorders once."""
    dropped = pyqtSignal(int)

    def startDrag(self, supported_actions):
        idx = self.selectedIndexes()
        if not idx:
            return
        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(idx))
        n = len(idx)
        pm = QPixmap(150, 26)
        pm.fill(QColor("#2e7d32"))
        p = QPainter(pm)
        p.setPen(QColor("white"))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, f"{n} word" + ("s" if n != 1 else ""))
        p.end()
        drag.setPixmap(pm)
        drag.exec(Qt.DropAction.MoveAction)

    def drop_row(self, pos):
        """Position in the list (0..count) where a drop at `pos` would put the words."""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return self.count()
        row = idx.row()
        return row + 1 if pos.y() > self.visualRect(idx).center().y() else row

    def dropEvent(self, e):
        if e.source() is not self:
            e.ignore()
            return
        row = self.drop_row(e.position().toPoint())
        e.setDropAction(Qt.DropAction.MoveAction)
        e.accept()
        QTimer.singleShot(0, lambda: self.dropped.emit(row))      # after the drag has ended


class PresetsDialog(QDialog):
    """Saved word lists. The person picks one and whether to add it to the list or to replace the list."""

    def __init__(self, parent, path, current_count):
        super().__init__(parent)
        self.setWindowTitle("Word presets")
        self.resize(460, 380)
        self.path, self.current_count = path, current_count
        self.choice = None                       # (mode, preset)
        lay = QVBoxLayout(self)
        info = QLabel("Load a saved list of words. 'Add to list' keeps your current words and puts the preset's new "
                      "words in front. 'Replace list' swaps your current list for the preset.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.lst = QListWidget()
        self.lst.itemSelectionChanged.connect(self._sel)
        self.lst.itemDoubleClicked.connect(lambda *_: self._pick("add"))
        lay.addWidget(self.lst, 1)
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        row = QHBoxLayout()
        self.btn_add = QPushButton("Add to list")
        self.btn_add.clicked.connect(lambda: self._pick("add"))
        row.addWidget(self.btn_add)
        self.btn_replace = QPushButton("Replace list")
        self.btn_replace.clicked.connect(lambda: self._pick("replace"))
        row.addWidget(self.btn_replace)
        self.btn_del = QPushButton("Delete")
        self.btn_del.clicked.connect(self._delete)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        row.addWidget(b)
        lay.addLayout(row)
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
        self._fill()

    def _fill(self):
        self.presets = V.load_presets(self.path)
        self.lst.clear()
        for name in sorted(self.presets, key=str.lower):
            p = self.presets[name]
            it = QListWidgetItem(f"{name}   ({len(p['words'])} words" + (f", {p['saved']}" if p["saved"] else "") + ")")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.lst.addItem(it)
        self.lbl.setText("" if self.presets else "No presets yet. Use 'Save as preset...' in the word list first.")
        self.lbl.setStyleSheet(DIM)
        self._sel()

    def _name(self):
        it = self.lst.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it and it.isSelected() else None

    def _sel(self):
        has = self._name() is not None
        for b in (self.btn_add, self.btn_replace, self.btn_del):
            b.setEnabled(has)

    def _pick(self, mode):
        name = self._name()
        if not name:
            return
        if mode == "replace" and self.current_count and QMessageBox.question(
                self, "Word presets", f"Replace your current {self.current_count} words with '{name}'?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.choice = (mode, self.presets[name], name)
        self.accept()

    def _delete(self):
        name = self._name()
        if name and QMessageBox.question(self, "Word presets", f"Delete the preset '{name}'?") == QMessageBox.StandardButton.Yes:
            V.delete_preset(self.path, name)
            self._fill()


# =====================================================================================================
class WordsDialog(QDialog):
    """The word list. 'Done' keeps the changes, 'Cancel' drops them. Words are grouped by where they came from
    (colour), the order matters: the first words are the ones given to the engine as a hint."""

    def __init__(self, parent, words, kurator_path="", on_kurator_path=None, sources=None, presets_path=None):
        super().__init__(parent)
        self.setWindowTitle("Special words")
        self.resize(640, 700)
        self.words = V.merge_words([], list(words))[0]
        self.sources = V.migrate_sources(self.words, sources)
        self.kurator_path = kurator_path or ""
        self.on_kurator_path = on_kurator_path
        self.presets_path = presets_path
        lay = QVBoxLayout(self)
        info = QLabel("Names the transcript should spell right. The engine gets the words at the top of the list as a "
                      "hint (shown in bold), and after the transcription all words are used to repair misspelled "
                      "names. New words are added at the top. Drag words to reorder them, double-click a word to rename it.")
        info.setWordWrap(True)
        lay.addWidget(info)

        row = QHBoxLayout()
        self.ed_add = QLineEdit()
        self.ed_add.setPlaceholderText("Type names, separated by commas, then press Enter")
        self.ed_add.returnPressed.connect(self._add_manual)
        row.addWidget(self.ed_add, 1)
        self.btn_add = QPushButton("Add")
        self.btn_add.clicked.connect(self._add_manual)
        row.addWidget(self.btn_add)
        lay.addLayout(row)

        row = QHBoxLayout()
        self.btn_kur = QPushButton("📚 Import from Kurator...")
        self.btn_kur.clicked.connect(self._import_kurator)
        row.addWidget(self.btn_kur)
        self.btn_dnd = QPushButton("🐉 Add D&&D terms...")
        self.btn_dnd.clicked.connect(self._add_dnd)
        row.addWidget(self.btn_dnd)
        self.btn_save = QPushButton("💾 Save as preset...")
        self.btn_save.clicked.connect(self._save_preset)
        row.addWidget(self.btn_save)
        self.btn_presets = QPushButton("📂 Presets...")
        self.btn_presets.clicked.connect(self._load_preset)
        row.addWidget(self.btn_presets)
        lay.addLayout(row)
        if not presets_path:
            self.btn_save.setEnabled(False)
            self.btn_presets.setEnabled(False)

        self.lbl_msg = QLabel("")
        self.lbl_msg.setWordWrap(True)
        lay.addWidget(self.lbl_msg)

        # groups: one click selects every word of a group
        row = QHBoxLayout()
        row.addWidget(QLabel("Groups (click to select):"))
        self.chips = {}
        for key, _ in V.SOURCES:
            b = QPushButton("")
            b.setFlat(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"color: {GROUP_COLORS[key]}; font-weight: bold; text-align: left;")
            b.clicked.connect(lambda _=False, k=key: self._select_group(k))
            self.chips[key] = b
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("Search the list...")
        self.ed_find.setClearButtonEnabled(True)
        self.ed_find.textChanged.connect(lambda *_: self._refill())
        lay.addWidget(self.ed_find)
        self.lst = WordList()
        self.lst.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.lst.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.lst.itemSelectionChanged.connect(self._sel_changed)
        self.lst.dropped.connect(self._dropped)
        self.lst.itemChanged.connect(self._item_edited)
        lay.addWidget(self.lst, 1)

        row = QHBoxLayout()
        self.move_btns = []
        for text, tip, where in (("⤒ To top", "Move the selected words to the top of the list", "top"),
                                 ("↑ Up", "One place up", "up"), ("↓ Down", "One place down", "down"),
                                 ("⤓ To bottom", "Move the selected words to the end of the list", "bottom")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, w=where: self._move(w))
            row.addWidget(b)
            self.move_btns.append((b, where))
        self.btn_before = QPushButton("In front of group ▾")
        self.btn_before.setToolTip("Move the selected words in front of the first word of a group")
        self.btn_before.clicked.connect(lambda: self._group_menu("before_group", "In front of", self.btn_before))
        row.addWidget(self.btn_before)
        self.btn_after = QPushButton("Behind group ▾")
        self.btn_after.setToolTip("Move the selected words behind the last word of a group")
        self.btn_after.clicked.connect(lambda: self._group_menu("after_group", "Behind", self.btn_after))
        row.addWidget(self.btn_after)
        row.addStretch(1)
        lay.addLayout(row)

        row = QHBoxLayout()
        self.lbl_count = QLabel("")
        self.lbl_count.setWordWrap(True)
        row.addWidget(self.lbl_count, 1)
        self.btn_rm = QPushButton("Remove selected")
        self.btn_rm.clicked.connect(self._remove_selected)
        row.addWidget(self.btn_rm)
        self.btn_clear = QPushButton("Clear all")
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_done = QPushButton("Done")
        self.btn_done.clicked.connect(self.accept)
        row.addWidget(self.btn_done)
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        row.addWidget(b)
        lay.addLayout(row)
        # Enter in the text field must only add words, never close the window
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        self._refill()

    # ------------------------------------------------------------------ list
    def _say(self, text, bad=False):
        self.lbl_msg.setStyleSheet(BAD if bad else GOOD)
        self.lbl_msg.setText(text)

    def _filter_on(self):
        return bool(self.ed_find.text().strip())

    def _selected_keys(self):
        return {V.norm_key(i.text()) for i in self.lst.selectedItems()}

    def _refill(self, select=None):
        keys = self._selected_keys() if select is None else {V.norm_key(k) for k in select}
        needle = self.ed_find.text().strip().lower()
        n_hint = hint_count(self.words)
        bold = QFont(self.lst.font())
        bold.setBold(True)
        self.lst.blockSignals(True)
        self.lst.clear()
        shown = 0
        pick_rows = []
        for i, w in enumerate(self.words):
            if needle and needle not in w.lower():
                continue
            it = QListWidgetItem(w)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            it.setData(Qt.ItemDataRole.UserRole, w)
            it.setForeground(QBrush(QColor(GROUP_COLORS[self.sources[V.norm_key(w)]])))
            it.setToolTip(f"{GROUP_NAMES[self.sources[V.norm_key(w)]]}" + (" - goes into the engine hint" if i < n_hint else ""))
            if i < n_hint:
                it.setFont(bold)
            self.lst.addItem(it)
            if V.norm_key(w) in keys:
                pick_rows.append(shown)
            shown += 1
        self._select_rows(pick_rows)
        self.lst.blockSignals(False)
        self.lst.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop if self._filter_on()
                                 else QAbstractItemView.DragDropMode.InternalMove)
        counts = {k: 0 for k, _ in V.SOURCES}
        for w in self.words:
            counts[self.sources[V.norm_key(w)]] += 1
        for k, b in self.chips.items():
            b.setText(f"● {GROUP_NAMES[k].replace('&', '&&')} ({counts[k]})")
            b.setEnabled(counts[k] > 0)
        total = len(self.words)
        txt = f"{total} words" if shown == total else f"{shown} of {total} words"
        if total:
            txt += f" · the first {n_hint} fit into the engine hint (scene names of the recording use part of that room)"
        if self._filter_on():
            txt += " · dragging is off while searching"
        self.lbl_count.setText(txt)
        self.lbl_count.setStyleSheet(DIM)
        self.btn_clear.setEnabled(total > 0)
        self._sel_changed()

    def _sel_changed(self):
        has = bool(self.lst.selectedItems())
        self.btn_rm.setEnabled(has)
        free = not self._filter_on()
        for b, where in self.move_btns:
            b.setEnabled(has and (free or where in ("top", "bottom")))
        self.btn_before.setEnabled(has)
        self.btn_after.setEnabled(has)

    def _select_rows(self, rows):
        """Selects the rows in one step (selecting hundreds of items one by one is slow)."""
        sm = self.lst.selectionModel()
        sel = QItemSelection()
        rows = sorted(rows)
        i = 0
        while i < len(rows):
            j = i
            while j + 1 < len(rows) and rows[j + 1] == rows[j] + 1:
                j += 1
            sel.select(self.lst.model().index(rows[i], 0), self.lst.model().index(rows[j], 0))
            i = j + 1
        sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _select_group(self, key):
        self._select_rows([i for i in range(self.lst.count())
                           if self.sources[V.norm_key(self.lst.item(i).text())] == key])

    def _item_edited(self, item):
        """The person renamed a word (double-click)."""
        old = item.data(Qt.ItemDataRole.UserRole)
        new = V.tidy(item.text())
        if old is None or new == old:
            if old is not None and item.text() != old:
                QTimer.singleShot(0, lambda: self._refill())
            return
        okey, nkey = V.norm_key(old), V.norm_key(new)
        problem = None
        if len(new) < 2:
            problem = "A word needs at least 2 characters."
        elif nkey != okey and any(V.norm_key(w) == nkey for w in self.words):
            problem = f"'{new}' is already in the list."
        if problem:
            self._say(problem, bad=True)
        else:
            self.words = [new if V.norm_key(w) == okey else w for w in self.words]
            self.sources[nkey] = self.sources.get(okey, V.SRC_TYPED)
            if nkey != okey:
                self.sources.pop(okey, None)
            self._say(f"Renamed '{old}' to '{new}'.")
        QTimer.singleShot(0, lambda: self._refill(select=[new if not problem else old]))

    # ------------------------------------------------------------------ moving
    def _move(self, where, group=None):
        keys = self._selected_keys()
        if not keys:
            return
        new, moved = V.move_words(self.words, keys, where, self.sources, group)
        if not moved:
            self._say("Nothing to move there." if where not in ("before_group", "after_group") else
                      f"There are no {GROUP_NAMES.get(group, 'such')} words to use as a place.", bad=True)
            return
        self.words = new
        self._refill(select=keys)
        self._say("")

    def _group_menu(self, where, text, button):
        keys = self._selected_keys()
        if not keys:
            return
        present = {k for k in self.sources.values()}
        menu = QMenu(self)
        for key, label in V.SOURCES:
            if key in present:
                menu.addAction(f"{text} the {label} words".replace("&", "&&"), lambda k=key: self._move(where, k))
        if menu.isEmpty():
            return
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _dropped(self, row):
        """Words were dragged somewhere: one reorder, however many words."""
        if self._filter_on():
            return
        keys = self._selected_keys()
        new, moved = V.move_to_index(self.words, keys, row)
        if moved:
            self.words = new
            self._refill(select=keys)

    # ------------------------------------------------------------------ adding
    def _merge(self, new, source, label):
        self.words, self.sources, added, dup = V.add_words(self.words, self.sources, new, source, front=True)
        self._refill(select=[w for w in self.words[:added]])
        if not new:
            self._say(f"{label}: nothing to add.", bad=True)
        elif added and dup:
            self._say(f"{label}: added {added} new at the top, {dup} were already in the list.")
        elif added:
            self._say(f"{label}: added {added} new words at the top.")
        else:
            self._say(f"{label}: all {dup} were already in the list, nothing changed.")
        if added:
            self.lst.scrollToTop()
        return added, dup

    def _add_manual(self):
        new = V.split_words(self.ed_add.text())
        if not new:
            return
        self._merge(new, V.SRC_TYPED, "Typed words")
        self.ed_add.clear()

    def _import_kurator(self):
        dlg = KuratorImportDialog(self, self.kurator_path)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        if dlg.path and dlg.path != self.kurator_path:
            self.kurator_path = dlg.path
            if self.on_kurator_path:
                try:
                    self.on_kurator_path(dlg.path)
                except Exception as e:
                    logging.warning(f"Could not remember the Kurator path: {e}")
        if ok:
            self._merge(dlg.result_words, V.SRC_KURATOR, "Kurator")

    def _add_dnd(self):
        dlg = DndTermsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._merge(dlg.words(), V.SRC_DND, "D&D terms")

    # ------------------------------------------------------------------ removing
    def _remove_selected(self):
        gone = self._selected_keys()
        if not gone:
            return
        self.words = [w for w in self.words if V.norm_key(w) not in gone]
        self.sources = V.migrate_sources(self.words, self.sources)
        self._refill(select=[])
        self._say(f"Removed {len(gone)} words.")

    def _clear(self):
        if self.words and QMessageBox.question(self, "Special words", f"Remove all {len(self.words)} words from the list?") \
                == QMessageBox.StandardButton.Yes:
            self.words, self.sources = [], {}
            self._refill(select=[])
            self._say("List emptied.")

    # ------------------------------------------------------------------ presets
    def _save_preset(self):
        if not self.words:
            self._say("The list is empty, so there is nothing to save.", bad=True)
            return
        name, ok = QInputDialog.getText(self, "Save as preset", "Name of the preset (for example the campaign):")
        if not ok:
            return
        try:
            if V.tidy(name) in V.load_presets(self.presets_path) and QMessageBox.question(
                    self, "Save as preset", f"A preset called '{V.tidy(name)}' exists. Overwrite it?") \
                    != QMessageBox.StandardButton.Yes:
                return
            name = V.save_preset(self.presets_path, name, self.words, self.sources)
        except (V.VocabError, OSError) as e:
            self._say(str(e), bad=True)
            return
        self._say(f"Saved the preset '{name}' with {len(self.words)} words.")

    def _load_preset(self):
        dlg = PresetsDialog(self, self.presets_path, len(self.words))
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.choice:
            return
        mode, preset, name = dlg.choice
        self.words, self.sources, added, dup = V.apply_preset(self.words, self.sources, preset, mode)
        self._refill(select=self.words[:added] if mode == "add" else [])
        if mode == "replace":
            self._say(f"The list is now the preset '{name}' ({len(self.words)} words).")
        elif added and dup:
            self._say(f"Preset '{name}': added {added} new at the top, {dup} were already in the list.")
        elif added:
            self._say(f"Preset '{name}': added {added} new words at the top.")
        else:
            self._say(f"Preset '{name}': all {dup} were already in the list, nothing changed.")
        self.lst.scrollToTop()
