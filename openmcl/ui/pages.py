"""The four main pages: Instances, Mods, Accounts, Settings."""
from __future__ import annotations

import logging
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .. import java, loaders, mods as mods_api, packs
from ..config import Settings
from ..instances import Instance, InstanceStore
from ..providers import PROVIDERS, ModFile, ModHit, curseforge, modrinth
from .dialogs import InstanceSettingsDialog, NewInstanceDialog, run_task
from .theme import PALETTES, shade
from .workers import IconLoader, Worker

log = logging.getLogger(__name__)

LOADER_FOR_SEARCH = {"vanilla": "", "fabric": "fabric", "quilt": "quilt",
                     "forge": "forge", "neoforge": "neoforge"}


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        log.warning("could not open %s: %s", path, exc)


def human_time(stamp: float) -> str:
    if not stamp:
        return "Never played"
    delta = time.time() - stamp
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)} d ago"
    return time.strftime("%d %b %Y", time.localtime(stamp))


def human_playtime(seconds: int) -> str:
    if seconds < 60:
        return "under a minute"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


class Avatar(QLabel):
    """Instance icon: a custom image, or the first letter on an accent tile."""

    def __init__(self, text: str, accent: str, size: int = 44):
        super().__init__()
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText((text or "?")[0].upper())
        self.setStyleSheet(
            f"background: {shade(accent, -0.1)}; color: white; border-radius: 10px;"
            f"font-size: {int(size * 0.44)}px; font-weight: 700;"
        )


# ------------------------------------------------------------------ instances

class InstanceCard(QFrame):
    play = Signal(str)
    edit = Signal(str)
    manage_mods = Signal(str)
    deleted = Signal()

    def __init__(self, instance: Instance, settings: Settings, store: InstanceStore):
        super().__init__()
        self.setObjectName("Card")
        self.instance = instance
        self.store = store
        self.settings = settings
        self.setFixedHeight(142)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        title = QLabel(instance.name)
        title.setObjectName("CardTitle")
        title.setWordWrap(True)

        pill = QLabel(f"{instance.mc_version} · {instance.loader_label}")
        pill.setObjectName("Pill")
        pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        played = QLabel(human_time(instance.last_played))
        played.setObjectName("CardSub")

        head = QHBoxLayout()
        head.setSpacing(11)
        head.addWidget(Avatar(instance.name, settings.accent))
        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        text_column.addWidget(title)
        text_column.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft)
        head.addLayout(text_column, 1)

        play_button = QPushButton("Play")
        play_button.setObjectName("Primary")
        play_button.clicked.connect(lambda: self.play.emit(instance.id))

        mods_button = QPushButton("Mods")
        mods_button.clicked.connect(lambda: self.manage_mods.emit(instance.id))

        more = QPushButton("\u22ee")
        more.setObjectName("MoreButton")
        more.setToolTip("More actions")
        more.setFixedSize(30, 30)
        more.clicked.connect(self._show_menu)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(play_button)
        row.addWidget(mods_button)
        row.addStretch(1)
        row.addWidget(played)
        row.addWidget(more)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 14, 15, 13)
        layout.setSpacing(10)
        layout.addLayout(head)
        layout.addStretch(1)
        layout.addLayout(row)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Settings...", lambda: self.edit.emit(self.instance.id))
        menu.addAction("Open game folder", lambda: open_folder(self.instance.game_dir))
        menu.addAction("Duplicate", self._duplicate)
        menu.addAction("Export as .mrpack...", self._export)
        menu.addSeparator()
        menu.addAction("Delete...", self._delete)
        menu.exec(self.cursor().pos())

    def _duplicate(self) -> None:
        clone = self.store.duplicate(self.instance, f"{self.instance.name} copy")
        log.info("duplicated instance to %s", clone.id)
        self.deleted.emit()  # triggers a refresh

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export modpack", f"{self.instance.id}.mrpack", "Modrinth pack (*.mrpack)"
        )
        if not path:
            return
        ok, result = run_task(
            self, "Exporting", packs.export_mrpack, self.instance, Path(path),
            wants_progress=False,
        )
        if ok:
            QMessageBox.information(self, "Exported", f"Written to {result}")
        else:
            QMessageBox.warning(self, "Export failed", str(result))

    def _delete(self) -> None:
        answer = QMessageBox.question(
            self,
            "Delete instance",
            f"Delete “{self.instance.name}” and everything in it "
            "(worlds, mods, screenshots)?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete(self.instance)
            self.deleted.emit()


class InstancesPage(QWidget):
    play_requested = Signal(str)
    mods_requested = Signal(str)

    def __init__(self, store: InstanceStore, settings: Settings):
        super().__init__()
        self.store = store
        self.settings = settings
        self._columns = 0

        title = QLabel("Instances")
        title.setObjectName("PageTitle")

        new_button = QPushButton("New instance")
        new_button.setObjectName("Primary")
        new_button.clicked.connect(self.new_instance)

        import_button = QPushButton("Import modpack")
        import_button.clicked.connect(self.import_pack)

        header = QHBoxLayout()
        header.addWidget(title, 1)
        header.addWidget(import_button)
        header.addWidget(new_button)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 10, 0)
        self.grid.setSpacing(13)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.grid_host)

        self.empty = QLabel(
            "No instances yet.\n\nCreate one, or import a Modrinth (.mrpack) "
            "or CurseForge modpack."
        )
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 20, 18)
        layout.setSpacing(16)
        layout.addLayout(header)
        layout.addWidget(self.empty)
        layout.addWidget(self.scroll, 1)

        self.refresh()

    def resizeEvent(self, event):  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        columns = max(1, (self.scroll.viewport().width() - 10) // 290)
        if columns != self._columns:
            self.refresh()

    def refresh(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        instances = self.store.all()
        self.empty.setVisible(not instances)
        self.scroll.setVisible(bool(instances))
        if not instances:
            return

        columns = max(1, (self.scroll.viewport().width() - 10) // 290)
        self._columns = columns
        for n, instance in enumerate(instances):
            card = InstanceCard(instance, self.settings, self.store)
            card.play.connect(self.play_requested)
            card.manage_mods.connect(self.mods_requested)
            card.edit.connect(self._edit)
            card.deleted.connect(self.refresh)
            self.grid.addWidget(card, n // columns, n % columns)
        for c in range(columns):
            self.grid.setColumnStretch(c, 1)

    def _edit(self, instance_id: str) -> None:
        instance = self.store.get(instance_id)
        if instance and InstanceSettingsDialog(self, instance, self.settings).exec():
            self.refresh()

    def new_instance(self) -> None:
        dialog = NewInstanceDialog(self, self.settings.show_snapshots)
        dialog.created.connect(self._create)
        dialog.exec()

    def _create(self, name: str, mc_version: str, loader: str, loader_version: str) -> None:
        instance = self.store.create(name, mc_version, loader, loader_version)
        ok, result = run_task(
            self,
            f"Installing {mc_version}",
            loaders.install,
            loader,
            mc_version,
            loader_version,
        )
        if not ok:
            self.store.delete(instance)
            QMessageBox.warning(self, "Install failed", str(result))
            return
        instance.version_id = result
        instance.save()
        self.refresh()

    def import_pack(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import modpack", "", "Modpacks (*.mrpack *.zip);;All files (*)"
        )
        if not path:
            return
        ok, result = run_task(
            self, "Importing modpack", packs.import_pack, Path(path), self.store
        )
        if ok:
            self.refresh()
        else:
            QMessageBox.warning(self, "Import failed", str(result))


# ----------------------------------------------------------------------- mods

class ModRow(QWidget):
    def __init__(self, hit: ModHit, icons: IconLoader, on_install, on_open):
        super().__init__()
        self.hit = hit
        self.icon = QLabel()
        self.icon.setFixedSize(46, 46)
        self.icon.setScaledContents(True)
        pix = icons.request(hit.icon_url)
        if pix:
            self.icon.setPixmap(pix)
        else:
            self.icon.setText("")

        name = QLabel(hit.name)
        name.setObjectName("CardTitle")
        meta = QLabel(
            f"{hit.downloads_human} downloads"
            + (f" · by {hit.author}" if hit.author else "")
            + (f" · {', '.join(hit.categories[:3])}" if hit.categories else "")
        )
        meta.setObjectName("CardSub")
        summary = QLabel(hit.summary)
        summary.setObjectName("CardSub")
        summary.setWordWrap(True)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(name)
        text.addWidget(meta)
        text.addWidget(summary)

        install = QPushButton("Install")
        install.setObjectName("Primary")
        install.clicked.connect(lambda: on_install(hit))

        page = QPushButton("Page")
        page.setObjectName("Ghost")
        page.clicked.connect(lambda: on_open(hit))

        buttons = QVBoxLayout()
        buttons.addWidget(install)
        buttons.addWidget(page)
        buttons.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(13)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)
        layout.addLayout(buttons)

    def set_icon(self, pix: QPixmap) -> None:
        self.icon.setPixmap(pix)


class ModsPage(QWidget):
    def __init__(self, store: InstanceStore, settings: Settings):
        super().__init__()
        self.store = store
        self.settings = settings
        self.icons = IconLoader(self)
        self.icons.loaded.connect(self._icon_arrived)
        self._rows: dict[str, list[ModRow]] = {}
        self._offset = 0
        self._total = 0
        self._worker: Optional[Worker] = None

        title = QLabel("Mods")
        title.setObjectName("PageTitle")

        self.instance_picker = QComboBox()
        self.instance_picker.setMinimumWidth(240)
        self.instance_picker.currentIndexChanged.connect(self._instance_changed)

        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("Instance"))
        header.addWidget(self.instance_picker)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_browse_tab(), "Browse")
        self.tabs.addTab(self._build_installed_tab(), "Installed")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 20, 18)
        layout.setSpacing(14)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)

        self.refresh_instances()

    # -------------------------------------------------------------- browse tab
    def _build_browse_tab(self) -> QWidget:
        self.source = QComboBox()
        self.source.addItems(["Modrinth", "CurseForge"])
        self.source.currentTextChanged.connect(lambda _: self.search())

        self.project_type = QComboBox()
        self.project_type.addItems(["Mods", "Modpacks", "Resource packs", "Shaders"])
        self.project_type.currentTextChanged.connect(lambda _: self.search())

        self.query = QLineEdit()
        self.query.setPlaceholderText("Search mods...")
        self.query.returnPressed.connect(self.search)

        self.sort = QComboBox()
        self.sort.addItems(list(modrinth.SORTS))
        self.sort.currentTextChanged.connect(lambda _: self.search())

        self.filter_version = QCheckBox("Match this instance")
        self.filter_version.setChecked(True)
        self.filter_version.toggled.connect(lambda _: self.search())

        search_button = QPushButton("Search")
        search_button.setObjectName("Primary")
        search_button.clicked.connect(self.search)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.source)
        controls.addWidget(self.project_type)
        controls.addWidget(self.query, 1)
        controls.addWidget(self.sort)
        controls.addWidget(self.filter_version)
        controls.addWidget(search_button)

        self.results = QListWidget()
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.results.setSpacing(3)

        self.status = QLabel("")
        self.status.setObjectName("Muted")

        self.more_button = QPushButton("Load more")
        self.more_button.setVisible(False)
        self.more_button.clicked.connect(self._load_more)

        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.more_button)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)
        layout.addLayout(controls)
        layout.addWidget(self.results, 1)
        layout.addLayout(footer)
        return tab

    # ----------------------------------------------------------- installed tab
    def _build_installed_tab(self) -> QWidget:
        self.installed = QListWidget()
        self.installed.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )

        toggle = QPushButton("Enable / disable")
        toggle.clicked.connect(self._toggle_selected)

        remove = QPushButton("Remove")
        remove.setObjectName("Danger")
        remove.clicked.connect(self._remove_selected)

        add_local = QPushButton("Add jar...")
        add_local.clicked.connect(self._add_local)

        updates = QPushButton("Check for updates")
        updates.setObjectName("Primary")
        updates.clicked.connect(self._check_updates)

        folder = QPushButton("Open mods folder")
        folder.setObjectName("Ghost")
        folder.clicked.connect(
            lambda: open_folder(self.current_instance().mods_dir)
            if self.current_instance() else None
        )

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(updates)
        row.addWidget(toggle)
        row.addWidget(add_local)
        row.addWidget(remove)
        row.addStretch(1)
        row.addWidget(folder)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.installed, 1)
        layout.addLayout(row)
        return tab

    # -------------------------------------------------------------------- data
    def refresh_instances(self, select_id: str = "") -> None:
        current = select_id or self.current_instance_id()
        self.instance_picker.blockSignals(True)
        self.instance_picker.clear()
        for instance in self.store.all():
            self.instance_picker.addItem(
                f"{instance.name}  ({instance.mc_version} {instance.loader_label})",
                instance.id,
            )
        if current:
            index = self.instance_picker.findData(current)
            if index >= 0:
                self.instance_picker.setCurrentIndex(index)
        self.instance_picker.blockSignals(False)
        self._instance_changed()

    def current_instance_id(self) -> str:
        return self.instance_picker.currentData() or ""

    def current_instance(self) -> Optional[Instance]:
        instance_id = self.current_instance_id()
        return self.store.get(instance_id) if instance_id else None

    def _instance_changed(self) -> None:
        self.refresh_installed()
        if self.results.count():
            self.search()

    def refresh_installed(self) -> None:
        self.installed.clear()
        instance = self.current_instance()
        if not instance:
            return
        on_disk = set(instance.scan_mods_dir())
        known = {m.filename for m in instance.mod_records()}
        for record in instance.mod_records():
            present = record.filename in on_disk or (record.filename + ".disabled") in on_disk
            state = "" if record.enabled else "  (disabled)"
            missing = "" if present else "  (file missing)"
            item = QListWidgetItem(
                f"{record.name or record.filename}{state}{missing}\n"
                f"    {record.filename} · {record.source or 'local'}"
                + (f" · {record.version}" if record.version else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, record.filename)
            self.installed.addItem(item)
        for filename in sorted(on_disk):
            base = filename.removesuffix(".disabled")
            if base in known:
                continue
            item = QListWidgetItem(f"{base}  (added by hand)\n    {filename} · local")
            item.setData(Qt.ItemDataRole.UserRole, base)
            self.installed.addItem(item)

    def _selected_filenames(self) -> list[str]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.installed.selectedItems()
        ]

    def _toggle_selected(self) -> None:
        instance = self.current_instance()
        if not instance:
            return
        existing = {m.filename: m.enabled for m in instance.mod_records()}
        for filename in self._selected_filenames():
            instance.set_mod_enabled(filename, not existing.get(filename, True))
        self.refresh_installed()

    def _remove_selected(self) -> None:
        instance = self.current_instance()
        names = self._selected_filenames()
        if not instance or not names:
            return
        answer = QMessageBox.question(
            self, "Remove mods", f"Remove {len(names)} mod file(s) from this instance?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for filename in names:
            instance.remove_mod(filename)
        self.refresh_installed()

    def _add_local(self) -> None:
        instance = self.current_instance()
        if not instance:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Add mod jars", "", "Jars (*.jar)")
        for path in paths:
            mods_api.import_local_jar(instance, Path(path))
        self.refresh_installed()

    def _check_updates(self) -> None:
        instance = self.current_instance()
        if not instance:
            return
        ok, result = run_task(self, "Checking for updates", mods_api.check_updates, instance)
        if not ok:
            QMessageBox.warning(self, "Update check failed", str(result))
            return
        if not result:
            QMessageBox.information(self, "Up to date", "Every mod is on its newest build.")
            return
        lines = "\n".join(f"  {r.name}  ->  {f.version_number}" for r, f in result[:15])
        extra = f"\n  ...and {len(result) - 15} more" if len(result) > 15 else ""
        answer = QMessageBox.question(
            self,
            "Updates available",
            f"{len(result)} mod(s) have a newer build:\n\n{lines}{extra}\n\nUpdate them all?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for record, new_file in result:
            try:
                mods_api.apply_update(instance, record, new_file)
            except Exception as exc:  # noqa: BLE001
                log.warning("update failed for %s: %s", record.name, exc)
        self.refresh_installed()

    # ------------------------------------------------------------------ search
    def _provider(self):
        return curseforge if self.source.currentText() == "CurseForge" else modrinth

    def _project_type(self) -> str:
        return {
            "Mods": "mod",
            "Modpacks": "modpack",
            "Resource packs": "resourcepack",
            "Shaders": "shader",
        }[self.project_type.currentText()]

    def search(self) -> None:
        self._offset = 0
        self.results.clear()
        self._rows.clear()
        self._run_search()

    def _load_more(self) -> None:
        self._offset += 30
        self._run_search()

    def _run_search(self) -> None:
        provider = self._provider()
        instance = self.current_instance()
        mc_version = loader = ""
        if instance and self.filter_version.isChecked():
            mc_version = instance.mc_version
            loader = LOADER_FOR_SEARCH.get(instance.loader, "")

        if provider is curseforge:
            curseforge.configure(self.settings.curseforge_api_key)
            if not curseforge.available():
                self.status.setText(
                    "Add a CurseForge API key in Settings to browse CurseForge."
                )
                self.more_button.setVisible(False)
                return
            sort = curseforge.SORTS.get(self.sort.currentText(), 2)
        else:
            sort = modrinth.SORTS.get(self.sort.currentText(), "relevance")

        self.status.setText("Searching...")
        self.more_button.setEnabled(False)

        worker = Worker(
            provider.search,
            query=self.query.text().strip(),
            project_type=self._project_type(),
            mc_version=mc_version,
            loader=loader,
            sort=sort,
            limit=30,
            offset=self._offset,
            wants_progress=False,
        )
        worker.done.connect(self._on_results)
        worker.failed.connect(self._on_search_failed)
        self._worker = worker
        worker.start()

    def _on_search_failed(self, message: str) -> None:
        self.status.setText(message)
        self.more_button.setEnabled(True)

    def _on_results(self, payload) -> None:
        hits, total = payload
        self._total = total
        self.more_button.setEnabled(True)
        for hit in hits:
            row = ModRow(hit, self.icons, self._install, self._open_page)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, max(86, row.sizeHint().height())))
            self.results.addItem(item)
            self.results.setItemWidget(item, row)
            if hit.icon_url:
                self._rows.setdefault(hit.icon_url, []).append(row)
        shown = self.results.count()
        self.status.setText(
            f"{shown} of {total} results" if total else "No results."
        )
        self.more_button.setVisible(shown < total)

    def _icon_arrived(self, url: str, pix: QPixmap) -> None:
        for row in self._rows.get(url, []):
            try:
                row.set_icon(pix)
            except RuntimeError:
                pass  # the row was removed by a new search

    def _open_page(self, hit: ModHit) -> None:
        if hit.page_url:
            webbrowser.open(hit.page_url)

    def _install(self, hit: ModHit) -> None:
        instance = self.current_instance()
        if not instance:
            QMessageBox.information(self, "No instance", "Create an instance first.")
            return
        if hit.project_type == "modpack":
            QMessageBox.information(
                self,
                "Modpacks",
                "Download the pack file from the project page, then use "
                "Instances → Import modpack.",
            )
            self._open_page(hit)
            return

        provider = PROVIDERS[hit.source]
        ok, files = run_task(
            self,
            f"Finding builds of {hit.name}",
            provider.versions,
            hit.project_id,
            instance.mc_version,
            LOADER_FOR_SEARCH.get(instance.loader, ""),
            wants_progress=False,
        )
        if not ok:
            QMessageBox.warning(self, "Lookup failed", str(files))
            return
        if not files:
            QMessageBox.information(
                self,
                "No matching build",
                f"{hit.name} has no build for Minecraft {instance.mc_version} "
                f"with {instance.loader_label}.",
            )
            return

        chosen: ModFile = files[0]
        ok, result = run_task(
            self,
            f"Installing {hit.name}",
            mods_api.install_with_dependencies,
            instance,
            chosen,
        )
        if not ok:
            QMessageBox.warning(self, "Install failed", str(result))
            return
        self.refresh_installed()
        extra = len(result) - 1
        self.status.setText(
            f"Installed {hit.name}" + (f" and {extra} dependency file(s)." if extra else ".")
        )


# ------------------------------------------------------------------- accounts

class AccountsPage(QWidget):
    changed = Signal()

    def __init__(self, store, settings: Settings):
        super().__init__()
        self.store = store
        self.settings = settings

        title = QLabel("Accounts")
        title.setObjectName("PageTitle")

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._selection_changed)

        add = QPushButton("Add account")
        add.setObjectName("Primary")
        add.clicked.connect(self.sign_in)

        self.use = QPushButton("Use this account")
        self.use.clicked.connect(self._set_default)
        self.use.setEnabled(False)

        self.remove = QPushButton("Remove")
        self.remove.setObjectName("Danger")
        self.remove.clicked.connect(self._remove)
        self.remove.setEnabled(False)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(add)
        row.addWidget(self.use)
        row.addWidget(self.remove)
        row.addStretch(1)

        self.note = QLabel()
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 20, 18)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(self.note)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for account in self.store.accounts:
            marker = "  ·  default" if account.uuid == self.store.selected else ""
            state = "signed in" if not account.expired else "needs refresh"
            item = QListWidgetItem(f"{account.name}{marker}\n    {state} · {account.uuid}")
            item.setData(Qt.ItemDataRole.UserRole, account.uuid)
            self.list.addItem(item)
        if not self.settings.ms_client_id:
            self.note.setText(
                "No Microsoft client ID is configured yet. Register an Azure "
                "application (see the README) and paste its Application (client) ID "
                "into Settings before signing in."
            )
        elif not self.store.accounts:
            self.note.setText(
                "Add the Microsoft account that owns Minecraft: Java Edition. "
                "You approve the sign-in on Microsoft's own page - this launcher "
                "never sees your password."
            )
        else:
            self.note.setText("")

    def _selection_changed(self) -> None:
        has = bool(self.list.selectedItems())
        self.use.setEnabled(has)
        self.remove.setEnabled(has)

    def _selected_uuid(self) -> str:
        items = self.list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else ""

    def sign_in(self) -> None:
        from .dialogs import SignInDialog

        if not self.settings.ms_client_id:
            QMessageBox.information(
                self,
                "Set up sign-in first",
                "Open Settings and paste a Microsoft Application (client) ID. "
                "The README explains how to register one - it takes a couple of minutes "
                "and is free.",
            )
            return
        dialog = SignInDialog(self, self.settings.ms_client_id)
        if dialog.exec() and dialog.account:
            self.store.add(dialog.account)
            self.refresh()
            self.changed.emit()
        elif dialog.error:
            QMessageBox.warning(self, "Sign-in failed", dialog.error)

    def _set_default(self) -> None:
        uuid = self._selected_uuid()
        if uuid:
            self.store.selected = uuid
            self.store.save()
            self.refresh()
            self.changed.emit()

    def _remove(self) -> None:
        uuid = self._selected_uuid()
        if uuid:
            self.store.remove(uuid)
            self.refresh()
            self.changed.emit()


# ------------------------------------------------------------------- settings

class SettingsPage(QWidget):
    appearance_changed = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings

        title = QLabel("Settings")
        title.setObjectName("PageTitle")

        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 12, 0)
        column.setSpacing(16)
        column.addWidget(self._appearance_group())
        column.addWidget(self._java_group())
        column.addWidget(self._game_group())
        column.addWidget(self._accounts_group())
        column.addWidget(self._about_group())
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)

        save = QPushButton("Save")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 20, 18)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(scroll, 1)
        layout.addLayout(row)

    def _appearance_group(self) -> QGroupBox:
        self.theme = QComboBox()
        self.theme.addItems(list(PALETTES))
        self.theme.setCurrentText(self.settings.theme)
        self.theme.currentTextChanged.connect(self._preview)

        self.accent = QLineEdit(self.settings.accent)
        self.accent.setMaximumWidth(130)
        self.accent.textChanged.connect(self._preview)

        swatches = QHBoxLayout()
        swatches.setSpacing(6)
        for colour in ("#4f9d69", "#4b7bec", "#9b59b6", "#e67e22", "#e05566", "#16a3a3"):
            button = QPushButton()
            button.setFixedSize(26, 26)
            button.setStyleSheet(
                f"background:{colour}; border-radius:13px; border:2px solid transparent;"
            )
            button.clicked.connect(lambda _=False, c=colour: self.accent.setText(c))
            swatches.addWidget(button)
        swatches.addStretch(1)

        self.font_size = QSpinBox()
        self.font_size.setRange(10, 20)
        self.font_size.setSuffix(" px")
        self.font_size.setValue(self.settings.font_size)
        self.font_size.valueChanged.connect(self._preview)

        group = QGroupBox("Appearance")
        layout = QGridLayout(group)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Theme"), 0, 0)
        layout.addWidget(self.theme, 0, 1)
        layout.addWidget(QLabel("Accent"), 1, 0)
        layout.addWidget(self.accent, 1, 1)
        layout.addLayout(swatches, 2, 1)
        layout.addWidget(QLabel("Font size"), 3, 0)
        layout.addWidget(self.font_size, 3, 1)
        layout.setColumnStretch(2, 1)
        return group

    def _java_group(self) -> QGroupBox:
        self.java_path = QLineEdit(self.settings.java_path)
        self.java_path.setPlaceholderText("Auto-detect")

        browse = QPushButton("Browse...")
        browse.clicked.connect(self._pick_java)

        detect = QPushButton("Detect installed")
        detect.clicked.connect(self._detect_java)

        self.java_found = QLabel("")
        self.java_found.setObjectName("Muted")
        self.java_found.setWordWrap(True)

        self.memory = QSpinBox()
        self.memory.setRange(512, 65536)
        self.memory.setSingleStep(512)
        self.memory.setSuffix(" MB")
        self.memory.setValue(self.settings.memory_mb)

        self.jvm_args = QPlainTextEdit(self.settings.jvm_args)
        self.jvm_args.setFixedHeight(76)

        path_row = QHBoxLayout()
        path_row.addWidget(self.java_path, 1)
        path_row.addWidget(browse)
        path_row.addWidget(detect)

        group = QGroupBox("Java")
        layout = QGridLayout(group)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Java binary"), 0, 0)
        layout.addLayout(path_row, 0, 1)
        layout.addWidget(self.java_found, 1, 1)
        layout.addWidget(QLabel("Max memory"), 2, 0)
        layout.addWidget(self.memory, 2, 1, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(QLabel("JVM arguments"), 3, 0)
        layout.addWidget(self.jvm_args, 3, 1)
        layout.setColumnStretch(1, 1)
        return group

    def _game_group(self) -> QGroupBox:
        self.width = QSpinBox()
        self.width.setRange(320, 7680)
        self.width.setValue(self.settings.window_width)
        self.height = QSpinBox()
        self.height.setRange(240, 4320)
        self.height.setValue(self.settings.window_height)

        self.fullscreen = QCheckBox("Start fullscreen")
        self.fullscreen.setChecked(self.settings.fullscreen)

        self.close_on_launch = QCheckBox("Hide the launcher while the game runs")
        self.close_on_launch.setChecked(self.settings.close_launcher_on_launch)

        self.show_snapshots = QCheckBox("Offer snapshots when creating instances")
        self.show_snapshots.setChecked(self.settings.show_snapshots)

        self.downloads = QSpinBox()
        self.downloads.setRange(1, 64)
        self.downloads.setValue(self.settings.concurrent_downloads)

        size_row = QHBoxLayout()
        size_row.addWidget(self.width)
        size_row.addWidget(QLabel("x"))
        size_row.addWidget(self.height)
        size_row.addStretch(1)

        group = QGroupBox("Game")
        layout = QGridLayout(group)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Window size"), 0, 0)
        layout.addLayout(size_row, 0, 1)
        layout.addWidget(self.fullscreen, 1, 1)
        layout.addWidget(self.close_on_launch, 2, 1)
        layout.addWidget(self.show_snapshots, 3, 1)
        layout.addWidget(QLabel("Parallel downloads"), 4, 0)
        layout.addWidget(self.downloads, 4, 1, Qt.AlignmentFlag.AlignLeft)
        layout.setColumnStretch(1, 1)
        return group

    def _accounts_group(self) -> QGroupBox:
        self.client_id = QLineEdit(self.settings.ms_client_id)
        self.client_id.setPlaceholderText("Azure Application (client) ID")

        self.cf_key = QLineEdit(self.settings.curseforge_api_key)
        self.cf_key.setPlaceholderText("CurseForge API key")
        self.cf_key.setEchoMode(QLineEdit.EchoMode.Password)

        azure = QPushButton("Open Azure portal")
        azure.setObjectName("Ghost")
        azure.clicked.connect(lambda: webbrowser.open("https://portal.azure.com/"))

        cf = QPushButton("Get a key")
        cf.setObjectName("Ghost")
        cf.clicked.connect(lambda: webbrowser.open("https://console.curseforge.com/"))

        note = QLabel(
            "The Microsoft client ID is yours - register an Azure app with the "
            "device-code flow enabled and the XboxLive.signin scope. The CurseForge "
            "key is free; using it means accepting CurseForge's third-party API terms."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)

        ms_row = QHBoxLayout()
        ms_row.addWidget(self.client_id, 1)
        ms_row.addWidget(azure)

        cf_row = QHBoxLayout()
        cf_row.addWidget(self.cf_key, 1)
        cf_row.addWidget(cf)

        group = QGroupBox("API keys")
        layout = QGridLayout(group)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Microsoft client ID"), 0, 0)
        layout.addLayout(ms_row, 0, 1)
        layout.addWidget(QLabel("CurseForge key"), 1, 0)
        layout.addLayout(cf_row, 1, 1)
        layout.addWidget(note, 2, 1)
        layout.setColumnStretch(1, 1)
        return group

    def _about_group(self) -> QGroupBox:
        from .. import __version__, config

        group = QGroupBox("About")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)
        portable = config.portable_root() is not None
        version = QLabel(
            f"OpenMCL {__version__}" + ("  ·  portable install" if portable else "")
        )
        folder = QLabel(
            ("Worlds, mods and accounts travel with this folder:\n" if portable
             else "Worlds, mods and accounts are kept in:\n") + str(config.DATA)
        )
        folder.setObjectName("Muted")
        folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_data = QPushButton("Open data folder")
        open_data.setObjectName("Ghost")
        open_data.clicked.connect(lambda: open_folder(config.DATA))
        layout.addWidget(version)
        layout.addWidget(folder)
        layout.addWidget(open_data, 0, Qt.AlignmentFlag.AlignLeft)
        return group

    def _pick_java(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select the java binary")
        if path:
            self.java_path.setText(path)

    def _detect_java(self) -> None:
        ok, result = run_task(self, "Looking for Java", java.discover, wants_progress=False)
        if not ok or not result:
            self.java_found.setText("No Java runtime found.")
            return
        self.java_found.setText(
            "Found: " + ";  ".join(f"Java {r.major}" for r in result[:4])
        )
        menu = QMenu(self)
        for runtime in result:
            menu.addAction(
                str(runtime), lambda _=False, r=runtime: self.java_path.setText(str(r.path))
            )
        menu.exec(self.cursor().pos())

    def _preview(self) -> None:
        self.settings.theme = self.theme.currentText()
        accent = self.accent.text().strip()
        if len(accent) in (4, 7) and accent.startswith("#"):
            self.settings.accent = accent
        self.settings.font_size = self.font_size.value()
        self.appearance_changed.emit()

    def save(self) -> None:
        s = self.settings
        s.theme = self.theme.currentText()
        accent = self.accent.text().strip()
        if len(accent) in (4, 7) and accent.startswith("#"):
            s.accent = accent
        s.font_size = self.font_size.value()
        s.java_path = self.java_path.text().strip()
        s.memory_mb = self.memory.value()
        s.jvm_args = self.jvm_args.toPlainText().strip()
        s.window_width = self.width.value()
        s.window_height = self.height.value()
        s.fullscreen = self.fullscreen.isChecked()
        s.close_launcher_on_launch = self.close_on_launch.isChecked()
        s.show_snapshots = self.show_snapshots.isChecked()
        s.concurrent_downloads = self.downloads.value()
        s.ms_client_id = self.client_id.text().strip()
        s.curseforge_api_key = self.cf_key.text().strip()
        s.save()
        curseforge.configure(s.curseforge_api_key)
        self.appearance_changed.emit()
