"""Dialogs: new instance, sign-in, per-instance settings, progress and console."""
from __future__ import annotations

import webbrowser
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .. import auth, loaders, mojang
from ..config import Settings
from ..instances import Instance
from .workers import Worker


# ------------------------------------------------------------------ progress

class TaskDialog(QDialog):
    """Runs a Worker and shows its progress. Returns the result via `result_value`."""

    def __init__(self, parent, title: str, worker: Worker):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.result_value = None
        self.error: str = ""

        self._worker = worker
        self._label = QLabel("Starting...")
        self._label.setWordWrap(True)
        self._detail = QLabel("")
        self._detail.setObjectName("Muted")
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)

        self._cancel = QPushButton("Cancel")
        self._cancel.setObjectName("Ghost")
        self._cancel.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._cancel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addWidget(self._detail)
        layout.addLayout(row)

        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)

    def exec(self) -> int:  # noqa: A003 - Qt's name
        self._worker.start()
        return super().exec()

    def _on_progress(self, current: int, total: int, label: str) -> None:
        if total > 0:
            self._bar.setRange(0, total)
            self._bar.setValue(current)
            self._label.setText(label or "Working...")
            self._detail.setText(f"{current} of {total}")
        else:
            self._bar.setRange(0, 0)
            self._label.setText(label or "Working...")
            self._detail.setText("")

    def _on_done(self, value) -> None:
        self.result_value = value
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.error = message
        self.reject()


def run_task(parent, title: str, fn: Callable, *args, wants_progress: bool = True, **kwargs):
    """Run `fn` in a worker behind a progress dialog. Returns (ok, result_or_error)."""
    worker = Worker(fn, *args, wants_progress=wants_progress, **kwargs)
    dialog = TaskDialog(parent, title, worker)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    if not accepted and worker.isRunning():
        worker.requestInterruption()
    if accepted:
        return True, dialog.result_value
    return False, dialog.error or "Cancelled."


# -------------------------------------------------------------------- sign in

class SignInDialog(QDialog):
    """Microsoft device-code sign-in."""

    def __init__(self, parent, client_id: str):
        super().__init__(parent)
        self.setWindowTitle("Sign in with Microsoft")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.account: Optional[auth.Account] = None
        self.error: str = ""
        self._cancelled = False
        self._client_id = client_id
        self._worker: Optional[Worker] = None

        self._status = QLabel("Contacting Microsoft...")
        self._status.setWordWrap(True)

        self._code = QLabel("")
        self._code.setObjectName("PageTitle")
        self._code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._code.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._open = QPushButton("Open microsoft.com/link")
        self._open.setObjectName("Primary")
        self._open.setEnabled(False)
        self._open.clicked.connect(self._open_browser)

        self._copy = QPushButton("Copy code")
        self._copy.setEnabled(False)
        self._copy.clicked.connect(self._copy_code)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self._open)
        buttons.addWidget(self._copy)
        buttons.addStretch(1)
        buttons.addWidget(cancel)

        hint = QLabel(
            "You need a Microsoft account that owns Minecraft: Java Edition. "
            "Nothing is typed into this window - you approve the sign-in on "
            "Microsoft's own page."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)
        layout.addWidget(self._status)
        layout.addWidget(self._code)
        layout.addWidget(hint)
        layout.addLayout(buttons)

        self._pending: Optional[auth.PendingSignIn] = None
        self._start()

    def _start(self) -> None:
        starter = Worker(auth.start_device_code, self._client_id, wants_progress=False)
        starter.done.connect(self._on_code)
        starter.failed.connect(self._on_failed)
        self._worker = starter
        starter.start()

    def _on_code(self, pending: auth.PendingSignIn) -> None:
        self._pending = pending
        self._code.setText(pending.user_code)
        self._status.setText(
            f"Go to {pending.verification_uri} and enter this code:"
        )
        self._open.setEnabled(True)
        self._copy.setEnabled(True)

        poller = Worker(
            auth.complete_sign_in,
            pending,
            lambda: self._cancelled,
            wants_progress=False,
        )
        poller.done.connect(self._on_account)
        poller.failed.connect(self._on_failed)
        self._worker = poller
        poller.start()

    def _on_account(self, account: auth.Account) -> None:
        self.account = account
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.error = message
        self._status.setText(message)
        self._code.setText("")
        self._open.setEnabled(False)
        self._copy.setEnabled(False)

    def _open_browser(self) -> None:
        if self._pending:
            self._copy_code()
            webbrowser.open(self._pending.verification_uri)

    def _copy_code(self) -> None:
        if self._pending:
            QGuiApplication.clipboard().setText(self._pending.user_code)
            self._copy.setText("Copied")

    def reject(self) -> None:
        self._cancelled = True
        super().reject()


# -------------------------------------------------------------- new instance

class NewInstanceDialog(QDialog):
    created = Signal(str, str, str, str)  # name, mc_version, loader, loader_version

    def __init__(self, parent, show_snapshots: bool = False):
        super().__init__(parent)
        self.setWindowTitle("New instance")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._loader_worker: Optional[Worker] = None

        self.name = QLineEdit()
        self.name.setPlaceholderText("My world")

        self.snapshots = QCheckBox("Include snapshots and old versions")
        self.snapshots.setChecked(show_snapshots)
        self.snapshots.toggled.connect(self._reload_versions)

        self.mc_version = QComboBox()
        self.mc_version.currentTextChanged.connect(self._reload_loader_versions)

        self.loader = QComboBox()
        self.loader.addItems(["Vanilla", "Fabric", "Quilt", "NeoForge", "Forge"])
        self.loader.currentTextChanged.connect(self._reload_loader_versions)

        self.loader_version = QComboBox()
        self.loader_version.setEnabled(False)

        self.loader_note = QLabel("")
        self.loader_note.setObjectName("Muted")
        self.loader_note.setWordWrap(True)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Name", self.name)
        form.addRow("Minecraft", self.mc_version)
        form.addRow("", self.snapshots)
        form.addRow("Mod loader", self.loader)
        form.addRow("Loader version", self.loader_version)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Create")
        ok.setObjectName("Primary")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)
        layout.addLayout(form)
        layout.addWidget(self.loader_note)
        layout.addWidget(self.buttons)

        self._reload_versions()

    def _reload_versions(self) -> None:
        self.mc_version.clear()
        self.mc_version.addItem("Loading...")
        self.mc_version.setEnabled(False)
        worker = Worker(
            mojang.list_versions, self.snapshots.isChecked(), wants_progress=False
        )
        worker.done.connect(self._on_versions)
        worker.failed.connect(lambda m: self._on_versions([]))
        self._versions_worker = worker
        worker.start()

    def _on_versions(self, versions: list) -> None:
        self.mc_version.clear()
        self.mc_version.setEnabled(True)
        if not versions:
            self.mc_version.addItem("Could not reach Mojang")
            return
        self.mc_version.addItems([v["id"] for v in versions])
        self._reload_loader_versions()

    def selected_loader(self) -> str:
        return self.loader.currentText().lower()

    def _reload_loader_versions(self) -> None:
        loader = self.selected_loader()
        mc = self.mc_version.currentText()
        self.loader_version.clear()
        if loader == "vanilla" or not mc or mc.startswith(("Loading", "Could not")):
            self.loader_version.setEnabled(False)
            self.loader_note.setText("")
            return
        self.loader_version.setEnabled(False)
        self.loader_version.addItem("Loading...")
        self.loader_note.setText("")

        worker = Worker(loaders.available_versions, loader, mc, wants_progress=False)
        worker.done.connect(self._on_loader_versions)
        worker.failed.connect(lambda m: self._on_loader_versions([]))
        self._loader_worker = worker
        worker.start()

    def _on_loader_versions(self, versions: list) -> None:
        self.loader_version.clear()
        if not versions:
            self.loader_version.addItem("None available")
            self.loader_version.setEnabled(False)
            self.loader_note.setText(
                f"{self.loader.currentText()} has no build for "
                f"{self.mc_version.currentText()}."
            )
            return
        self.loader_version.addItems(versions)
        self.loader_version.setEnabled(True)
        self.loader_note.setText("The newest build is selected by default.")

    def _accept(self) -> None:
        mc = self.mc_version.currentText()
        if not mc or mc.startswith(("Loading", "Could not")):
            return
        loader = self.selected_loader()
        loader_version = (
            self.loader_version.currentText() if loader != "vanilla" else ""
        )
        if loader != "vanilla" and (
            not loader_version or loader_version in ("Loading...", "None available")
        ):
            return
        name = self.name.text().strip() or (
            mc if loader == "vanilla" else f"{mc} {self.loader.currentText()}"
        )
        self.created.emit(name, mc, loader, loader_version)
        self.accept()


# ------------------------------------------------------- per-instance settings

class InstanceSettingsDialog(QDialog):
    def __init__(self, parent, instance: Instance, settings: Settings):
        super().__init__(parent)
        self.setWindowTitle(f"{instance.name} - settings")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.instance = instance

        self.name = QLineEdit(instance.name)
        self.notes = QPlainTextEdit(instance.notes)
        self.notes.setFixedHeight(70)

        self.memory = QSpinBox()
        self.memory.setRange(0, 65536)
        self.memory.setSingleStep(512)
        self.memory.setSuffix(" MB")
        self.memory.setSpecialValueText(f"Global ({settings.memory_mb} MB)")
        self.memory.setValue(instance.memory_mb)

        self.java_path = QLineEdit(instance.java_path)
        self.java_path.setPlaceholderText("Global setting - auto-detected")

        self.jvm_args = QPlainTextEdit(instance.jvm_args)
        self.jvm_args.setFixedHeight(70)
        self.jvm_args.setPlaceholderText("Leave empty to use the global JVM arguments")

        self.width = QSpinBox()
        self.width.setRange(0, 7680)
        self.width.setSpecialValueText("Global")
        self.width.setValue(instance.window_width)
        self.height = QSpinBox()
        self.height.setRange(0, 4320)
        self.height.setSpecialValueText("Global")
        self.height.setValue(instance.window_height)

        self.fullscreen = QComboBox()
        self.fullscreen.addItems(["Use global setting", "Windowed", "Fullscreen"])
        self.fullscreen.setCurrentIndex(
            0 if instance.fullscreen is None else (2 if instance.fullscreen else 1)
        )

        self.pre_launch = QLineEdit(instance.pre_launch_command)
        self.pre_launch.setPlaceholderText("Shell command run before the game starts")

        general = QWidget()
        general_form = QFormLayout(general)
        general_form.setSpacing(10)
        general_form.addRow("Name", self.name)
        general_form.addRow("Notes", self.notes)
        general_form.addRow("Minecraft", QLabel(instance.mc_version))
        general_form.addRow("Loader", QLabel(instance.loader_label))
        general_form.addRow("Folder", self._path_label(str(instance.game_dir)))

        java_tab = QWidget()
        java_form = QFormLayout(java_tab)
        java_form.setSpacing(10)
        java_form.addRow("Memory", self.memory)
        java_form.addRow("Java binary", self.java_path)
        java_form.addRow("JVM arguments", self.jvm_args)
        java_form.addRow("Pre-launch command", self.pre_launch)

        window_tab = QWidget()
        window_form = QFormLayout(window_tab)
        window_form.setSpacing(10)
        window_form.addRow("Width", self.width)
        window_form.addRow("Height", self.height)
        window_form.addRow("Mode", self.fullscreen)

        tabs = QTabWidget()
        tabs.addTab(general, "General")
        tabs.addTab(java_tab, "Java")
        tabs.addTab(window_tab, "Window")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    @staticmethod
    def _path_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def _save(self) -> None:
        inst = self.instance
        inst.name = self.name.text().strip() or inst.name
        inst.notes = self.notes.toPlainText()
        inst.memory_mb = self.memory.value()
        inst.java_path = self.java_path.text().strip()
        inst.jvm_args = self.jvm_args.toPlainText().strip()
        inst.window_width = self.width.value()
        inst.window_height = self.height.value()
        inst.pre_launch_command = self.pre_launch.text().strip()
        index = self.fullscreen.currentIndex()
        inst.fullscreen = None if index == 0 else (index == 2)
        inst.save()
        self.accept()


# -------------------------------------------------------------------- console

class ConsoleWindow(QDialog):
    """Live game output, with a kill switch."""

    MAX_LINES = 5000

    def __init__(self, parent, instance_name: str):
        super().__init__(parent)
        self.setWindowTitle(f"{instance_name} - log")
        self.resize(900, 560)
        self.process = None

        self.view = QPlainTextEdit()
        self.view.setObjectName("Console")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(self.MAX_LINES)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        self.status = QLabel("Starting...")
        self.status.setObjectName("Muted")

        self.kill = QPushButton("Kill game")
        self.kill.setObjectName("Danger")
        self.kill.clicked.connect(self._kill)

        copy_button = QPushButton("Copy all")
        copy_button.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.view.toPlainText())
        )

        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(copy_button)
        row.addWidget(self.kill)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self.view, 1)
        layout.addLayout(row)

    def append(self, line: str) -> None:
        self.view.appendPlainText(line)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def attach(self, process) -> None:
        self.process = process
        self.set_status(f"Running (pid {process.pid})")

    def _kill(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.set_status("Killed")
        self.kill.setEnabled(False)
