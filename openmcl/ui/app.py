"""Main window and application entry point."""
from __future__ import annotations

import argparse
import faulthandler
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Optional
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import __version__, auth, config, launch, loaders, mojang
from ..config import Settings
from ..instances import Instance, InstanceStore
from ..providers import curseforge
from .dialogs import ConsoleWindow, run_task
from .pages import AccountsPage, InstancesPage, ModsPage, SettingsPage
from .theme import stylesheet

log = logging.getLogger(__name__)

PAGES = [("Instances", 0), ("Mods", 1), ("Accounts", 2), ("Settings", 3)]


def app_icon() -> Optional[QIcon]:
    """The window icon, whether running from source or from a frozen build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    for candidate in (
        base / "packaging" / "icons" / "openmcl.png",
        base / "openmcl.png",
    ):
        if candidate.is_file():
            return QIcon(str(candidate))
    return None


class MainWindow(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.instances = InstanceStore()
        self.accounts = auth.AccountStore()
        self._consoles: list[ConsoleWindow] = []
        self._running: dict[int, tuple[Instance, float, ConsoleWindow]] = {}

        curseforge.configure(settings.curseforge_api_key)

        self.setWindowTitle(f"OpenMCL {__version__}")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(1120, 720)
        self.setMinimumSize(880, 560)

        self.stack = QStackedWidget()
        self.instances_page = InstancesPage(self.instances, settings)
        self.mods_page = ModsPage(self.instances, settings)
        self.accounts_page = AccountsPage(self.accounts, settings)
        self.settings_page = SettingsPage(settings)

        for page in (self.instances_page, self.mods_page,
                     self.accounts_page, self.settings_page):
            self.stack.addWidget(page)

        self.instances_page.play_requested.connect(self.play)
        self.instances_page.mods_requested.connect(self._open_mods_for)
        self.accounts_page.changed.connect(self._update_status)
        self.settings_page.appearance_changed.connect(self.apply_theme)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.stack, 1)
        right.addWidget(self._build_statusbar())
        layout.addLayout(right, 1)

        self.apply_theme()
        self._update_status()

    # ------------------------------------------------------------------ chrome
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(196)

        brand = QLabel("OpenMCL")
        brand.setObjectName("BrandLabel")
        sub = QLabel("Minecraft launcher")
        sub.setObjectName("BrandSub")

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(4)
        layout.addWidget(brand)
        layout.addWidget(sub)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for label, index in PAGES:
            button = QPushButton(label)
            button.setObjectName("SidebarButton")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _=False, i=index: self._go(i))
            self.nav_group.addButton(button, index)
            layout.addWidget(button)

        layout.addStretch(1)

        self.account_label = QLabel()
        self.account_label.setObjectName("CardSub")
        self.account_label.setWordWrap(True)
        layout.addWidget(self.account_label)
        return sidebar

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(32)
        self.status = QLabel("Ready")
        self.status.setObjectName("Muted")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.addWidget(self.status, 1)
        return bar

    def _go(self, index: int) -> None:
        if index == 1:
            self.mods_page.refresh_instances()
        self.stack.setCurrentIndex(index)
        button = self.nav_group.button(index)
        if button:
            button.setChecked(True)

    def _open_mods_for(self, instance_id: str) -> None:
        self.mods_page.refresh_instances(instance_id)
        self._go(1)

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if app:
            app.setStyleSheet(
                stylesheet(self.settings.theme, self.settings.accent,
                           self.settings.font_size)
            )
        # Instance avatars are painted with the accent, so rebuild them too.
        self.instances_page.refresh()

    def _update_status(self, message: str = "") -> None:
        account = self.accounts.current()
        self.account_label.setText(
            f"Signed in as\n{account.name}" if account else "Not signed in"
        )
        if message:
            self.status.setText(message)
        elif self._running:
            self.status.setText(f"{len(self._running)} game(s) running")
        else:
            self.status.setText("Ready")

    # ------------------------------------------------------------------ launch
    def play(self, instance_id: str) -> None:
        instance = self.instances.get(instance_id)
        if instance is None:
            return

        account = self.accounts.current()
        if account is None:
            QMessageBox.information(
                self,
                "Sign in first",
                "Add the Microsoft account that owns Minecraft before launching.",
            )
            self._go(2)
            return

        if account.expired:
            ok, result = run_task(
                self,
                "Refreshing your sign-in",
                self.accounts.ensure_fresh,
                account,
                self.settings.ms_client_id,
                wants_progress=False,
            )
            if not ok:
                QMessageBox.warning(self, "Sign-in expired", str(result))
                self._go(2)
                return
            account = result

        version_id = instance.launch_version
        ok, version = run_task(
            self,
            f"Preparing {instance.name}",
            self._prepare,
            instance,
            version_id,
        )
        if not ok:
            QMessageBox.warning(self, "Could not prepare the game", str(version))
            return

        try:
            plan = launch.build_plan(instance, account, self.settings, version)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Cannot launch", str(exc))
            return

        console = ConsoleWindow(self, instance.name)
        console.append(f"$ {plan.pretty()}\n")
        console.show()
        self._consoles.append(console)

        try:
            process = launch.launch(
                plan,
                on_output=console.append,
                pre_launch_command=instance.pre_launch_command,
            )
        except Exception as exc:  # noqa: BLE001
            console.append(f"\nFailed to start: {exc}")
            QMessageBox.warning(self, "Launch failed", str(exc))
            return

        console.attach(process)
        instance.last_played = time.time()
        instance.save()
        self.instances_page.refresh()

        self._running[process.pid] = (instance, time.time(), console)
        self._watch(process)
        self._update_status(f"Launched {instance.name}")

        if self.settings.close_launcher_on_launch:
            self.showMinimized()

    def _prepare(self, instance: Instance, version_id: str, progress=None) -> dict:
        """Install (or repair) everything the instance needs, then load the profile."""
        if not instance.version_id and instance.loader != "vanilla":
            instance.version_id = loaders.install(
                instance.loader, instance.mc_version, instance.loader_version, progress
            )
            instance.save()
            version_id = instance.version_id
        return mojang.install_version(
            version_id, progress=progress, workers=self.settings.concurrent_downloads
        )

    def _watch(self, process) -> None:
        timer = QTimer(self)
        timer.setInterval(1500)

        def check():
            code = process.poll()
            if code is None:
                return
            timer.stop()
            entry = self._running.pop(process.pid, None)
            if entry:
                instance, started, console = entry
                elapsed = int(time.time() - started)
                fresh = self.instances.get(instance.id)
                if fresh:
                    fresh.play_seconds += elapsed
                    fresh.save()
                console.set_status(
                    f"Exited with code {code} after {elapsed // 60} min"
                    + ("" if code == 0 else "  -  check the log above for the cause")
                )
                console.kill.setEnabled(False)
                if code != 0:
                    console.raise_()
                self.instances_page.refresh()
            self._update_status()

        timer.timeout.connect(check)
        timer.start()

    def closeEvent(self, event):  # noqa: N802 - Qt's name
        if self._running:
            answer = QMessageBox.question(
                self,
                "Games are running",
                f"{len(self._running)} game(s) are still running. "
                "Closing the launcher leaves them running. Close anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.settings.save()
        self.mods_page.icons.shutdown()
        event.accept()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="openmcl", description="OpenMCL launcher")
    parser.add_argument("--version", action="version", version=f"OpenMCL {__version__}")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="start the interface, verify it builds, and exit (used by the build scripts)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config.ensure_dirs()
    log_path = config.LOGS_DIR / "launcher.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    # The released builds have no console, so a crash would otherwise leave
    # nothing behind at all. faulthandler catches the hard ones (a segfault,
    # or Qt aborting on a destroyed thread); excepthook catches Python errors
    # that would otherwise vanish inside a Qt slot.
    crash_path = config.LOGS_DIR / "crash.log"
    try:
        crash_file = open(crash_path, "a", encoding="utf-8")
        crash_file.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        crash_file.flush()
        faulthandler.enable(file=crash_file)
    except OSError:
        crash_file = None

    def report(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("unhandled error\n%s", text)
        if crash_file is not None:
            crash_file.write(f"\n{time.strftime('%H:%M:%S')} unhandled error\n{text}")
            crash_file.flush()
        try:
            QMessageBox.critical(
                None,
                "OpenMCL hit a problem",
                f"{exc}\n\nThe details were written to:\n{crash_path}\n\n"
                "Please report this, with that file attached, at\n"
                "https://github.com/silocell/OpenMCL-Launcher/issues",
            )
        except Exception:  # noqa: BLE001 - never fail inside the error handler
            pass

    sys.excepthook = report

    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, False)
    app = QApplication(sys.argv)
    app.setApplicationName("OpenMCL")
    app.setOrganizationName("OpenMCL")

    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    settings = Settings.load()
    window = MainWindow(settings)
    window.show()

    if args.selftest:
        app.processEvents()
        assert window.stack.count() == len(PAGES), "pages missing"
        for index in range(len(PAGES)):
            window._go(index)
            app.processEvents()

        # Things a frozen build can silently lose: the bundled icon, and the
        # SVG support the stylesheet's arrows are drawn with.
        assert icon is not None and not icon.isNull(), "application icon not bundled"
        from PySide6.QtGui import QPixmap

        from .theme import arrow_icon

        probe = QPixmap(str(arrow_icon("down", "#ffffff")))
        assert not probe.isNull(), "SVG image plugin not bundled (QtSvg missing)"

        print(f"OpenMCL {__version__} self-test passed ({config.DATA})")
        return 0

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
