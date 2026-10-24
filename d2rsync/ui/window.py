"""Danish desktop window. Network and disk scans stay off the UI thread."""

from __future__ import annotations

import copy
import hashlib
import html
import os
import socket
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from d2rsync.backup import BackupManager
from d2rsync.client import RemoteSave, connect, sync_with_peer
from d2rsync.config import (
    Config,
    ConfigError,
    load_config,
    parse_peer,
    state_path,
    write_config,
)
from d2rsync.d2s import read_local_d2s
from d2rsync.discover import discover, safe_service_name
from d2rsync.errors import D2RSyncError, SavePathError
from d2rsync.game import game_is_running
from d2rsync.paths import choose_save_folder, detect_save_folders
from d2rsync.preview import preview_with_peer
from d2rsync.server import Server
from d2rsync.state import load_state
from d2rsync.store import DirectorySave
from d2rsync.text import format_outcomes, format_plan
from d2rsync.ui.widgets import Badge, NavButton, Segment, Switch, ToggleRow
from d2rsync.viewmodel import (
    ConflictView,
    DashboardView,
    ResultView,
    SettingsDraft,
    SideCard,
    apply_draft,
    build_result_view,
    build_rows,
    danish_error,
    draft_from_config,
    gui_conflict_policy,
    make_dashboard_view,
)


class FuncWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 — shown in the window
            self.failed.emit(exc)
            return
        self.succeeded.emit(result)


class _HostLog:
    def __init__(self, window: "MainWindow"):
        self._window = window

    def info(self, message: str) -> None:
        self._window.log_line.emit(message)

    def warn(self, message: str) -> None:
        self._window.log_line.emit("advarsel: " + message)

    def error(self, message: str) -> None:
        self._window.log_line.emit("fejl: " + message)


class Backdrop(QWidget):
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        radius = max(self.width(), int(self.height() * 1.15))
        gradient = QRadialGradient(self.width() / 2, 0, radius)
        gradient.setColorAt(0.0, QColor("#2a1a14"))
        gradient.setColorAt(0.58, QColor("#120d0b"))
        gradient.setColorAt(1.0, QColor("#0a0807"))
        painter.fillRect(self.rect(), gradient)


class MainWindow(QMainWindow):
    log_line = Signal(str)
    host_failed = Signal(str)
    host_stopped = Signal()
    session_arrived = Signal()

    def __init__(self, autostart: bool = True, config_file: Path | None = None):
        super().__init__()
        self.setWindowTitle("D2R Save Sync")
        self.resize(1280, 800)
        self.setMinimumSize(1100, 700)
        self._config_file = Path(config_file) if config_file is not None else None
        self._worker: FuncWorker | None = None
        self._server: Server | None = None
        self._server_thread: threading.Thread | None = None
        self._host_stop_requested = False
        self._closing = False
        self._log_lines: list[str] = []
        self._archive_rel = ""
        self._detected = ""
        self._pinned = False
        self._retention = 30
        self._session_note = ""
        self._note_sticky = False
        self._last_logged_note = ""

        self.local_files: dict = {}
        self.remote_files: dict = {}
        self.decisions = None
        self.local_d2s: dict = {}
        self.remote_d2s: dict = {}
        self.peer_name = ""
        self.peer_host = ""
        self.connected = False
        self.searching = False
        self.busy = False
        self.hosting = False
        self.game_running: bool | None = False
        self.peer_game_running = False
        self.synced_at: float | None = None
        self.save_folder: Path | None = None
        self._note = ""

        try:
            self.cfg = load_config(self._config_file)
        except ConfigError as exc:
            self.cfg = Config(path=self._config_file)
            self._note = danish_error(exc)

        self._build()
        self._fill_settings(draft_from_config(self.cfg))
        self._render_dashboard_from_state()
        self.log_line.connect(self._append_log)
        self.host_failed.connect(self._on_host_failed)
        self.host_stopped.connect(self._on_host_stopped)
        self.session_arrived.connect(self._on_session)

        self._timer = QTimer(self)
        self._timer.setInterval(20000)
        self._timer.timeout.connect(self.refresh)
        if autostart:
            self._timer.start()
            QTimer.singleShot(0, self.refresh)

    def _build(self) -> None:
        root = Backdrop()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        column = QWidget()
        column.setObjectName("mainColumn")
        column.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._column = QVBoxLayout(column)
        self._column.setContentsMargins(28, 22, 28, 16)
        self._column.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_dashboard())
        self.stack.addWidget(self._build_result())
        self.stack.addWidget(self._build_settings())
        self._column.addWidget(self.stack, 1)
        outer.addWidget(column, 1)

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(96)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(12)
        logo = QLabel("D2")
        logo.setObjectName("logo")
        logo.setFixedSize(60, 60)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        self.nav_sync = NavButton("⟳", "Synk")
        self.nav_result = NavButton("⚔", "Resultat")
        self.nav_settings = NavButton("⚙", "Indstil.")
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, button in enumerate((self.nav_sync, self.nav_result, self.nav_settings)):
            self.nav_group.addButton(button, index)
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.nav_sync.setChecked(True)
        self.nav_group.idClicked.connect(self._show_page)
        layout.addStretch(1)
        return side

    def _build_dashboard(self) -> QWidget:
        page = self._page()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.dash_title = self._title("D2R Save Sync")
        self.dash_pill, self.dash_pill_text, self.dash_dot = self._pill()
        layout.addLayout(self._top(self.dash_title, self.dash_pill))

        self.warn = QFrame()
        self.warn.setObjectName("warn")
        warn_row = QHBoxLayout(self.warn)
        warn_row.setContentsMargins(16, 12, 16, 12)
        warn_row.setSpacing(12)
        mark = QLabel("⚠")
        mark.setStyleSheet("font-size: 22px; color: #ffb4a8; background: transparent;")
        self.warn_text = QLabel()
        self.warn_text.setObjectName("warnText")
        self.warn_text.setWordWrap(True)
        self.warn_text.setTextFormat(Qt.TextFormat.RichText)
        warn_row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        warn_row.addWidget(self.warn_text, 1)
        self.warn.hide()
        layout.addWidget(self.warn)

        self.dash_note = QLabel()
        self.dash_note.setProperty("role", "hint")
        self.dash_note.setWordWrap(True)
        self.dash_note.hide()
        layout.addWidget(self.dash_note)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        self.table_scroll = self._scroll()
        panel_layout.addWidget(self.table_scroll)
        layout.addWidget(panel, 1)

        self.sync_btn = self._push("⟳  Synkroniser nu", "primary")
        self.sync_btn.setMinimumWidth(280)
        self.sync_btn.clicked.connect(self._primary_action)
        self.dry = Switch(False)
        self.dry_wrap = self._toggle_block(self.dry, "Prøvekørsel", "Vis kun hvad der ville ske")
        self.footer = QLabel("Sidste synk: aldrig · Backup før overskrivning: til")
        self.footer.setProperty("role", "hint")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pad = QLabel("Ⓐ Vælg    Ⓨ Synkroniser    Ⓑ Tilbage")
        pad.setObjectName("pad")
        pad.setAlignment(Qt.AlignmentFlag.AlignRight)
        side = QVBoxLayout()
        side.setSpacing(2)
        side.addWidget(self.footer)
        side.addWidget(pad)
        bottom = QHBoxLayout()
        bottom.setSpacing(18)
        bottom.addWidget(self.sync_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        bottom.addWidget(self.dry_wrap, 0, Qt.AlignmentFlag.AlignVCenter)
        bottom.addStretch(1)
        bottom.addLayout(side)
        layout.addLayout(bottom)
        return page

    def _build_result(self) -> QWidget:
        page = self._page()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.result_title = self._title("Synkronisering fuldført")
        self.result_pill, self.result_pill_text, self.result_dot = self._pill()
        layout.addLayout(self._top(self.result_title, self.result_pill))

        scroll = self._scroll()
        inner = QWidget()
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner.setStyleSheet("background: transparent;")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(14)
        self.conflict_host = QFrame()
        self.conflict_host.setObjectName("panel")
        self.conflict_layout = QVBoxLayout(self.conflict_host)
        self.conflict_layout.setContentsMargins(18, 16, 18, 16)
        self.conflict_layout.setSpacing(12)
        self.conflict_host.hide()
        inner_layout.addWidget(self.conflict_host)

        file_panel = QFrame()
        file_panel.setObjectName("panel")
        file_layout = QVBoxLayout(file_panel)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self.file_scroll = self._scroll()
        self.file_scroll.setMinimumHeight(160)
        file_layout.addWidget(self.file_scroll)
        inner_layout.addWidget(file_panel)
        self.result_empty = QLabel("Ingen synkronisering endnu.")
        self.result_empty.setProperty("role", "hint")
        inner_layout.addWidget(self.result_empty)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        self.done_btn = self._push("✓  Færdig", "primary")
        self.done_btn.clicked.connect(self._done)
        self.archive_btn = self._push("📁  Åbn arkiv", "secondary")
        self.archive_btn.clicked.connect(self._open_archive)
        self.log_btn = self._push("Vis log", "secondary")
        self.log_btn.clicked.connect(self._show_log)
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        bottom.addWidget(self.done_btn)
        bottom.addWidget(self.archive_btn)
        bottom.addWidget(self.log_btn)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return page

    def _build_settings(self) -> QWidget:
        page = self._page()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._title("Indstillinger"))

        scroll = self._scroll()
        form = QWidget()
        form.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        form.setStyleSheet("background: transparent;")
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 8, 0)
        form_layout.setSpacing(16)

        self.path_label = self._field_label("GEMMEMAPPE (FUNDET AUTOMATISK)")
        path_block, path_layout = self._block()
        path_layout.addWidget(self.path_label)
        path_frame = QFrame()
        path_frame.setObjectName("field")
        path_frame.setMinimumHeight(56)
        path_row = QHBoxLayout(path_frame)
        path_row.setContentsMargins(12, 4, 8, 4)
        path_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        change = self._push("Skift…", "mini")
        change.clicked.connect(self._choose_folder)
        auto = self._push("Find selv", "mini")
        auto.clicked.connect(self._clear_folder_override)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(change)
        path_row.addWidget(auto)
        path_layout.addWidget(path_frame)
        form_layout.addWidget(path_block)

        role_block, role_layout = self._block()
        role_layout.addWidget(self._field_label("TILSTAND"))
        self.role = Segment([("host", "Vær vært"), ("client", "Forbind")], "client")
        self.role.changed.connect(lambda _key: self._update_setting_hints())
        self.role_hint = self._hint("Denne enhed forbinder og synkroniserer")
        role_layout.addWidget(self.role)
        role_layout.addWidget(self.role_hint)

        mode_block, mode_layout = self._block()
        mode_layout.addWidget(self._field_label("MODPART"))
        self.peer_mode = Segment([("auto", "Find automatisk"), ("manual", "Manuel IP")], "auto")
        self.peer_mode.changed.connect(lambda _key: self._update_setting_hints())
        self.mode_hint = self._hint("UDP-broadcast på hjemmenetværket")
        mode_layout.addWidget(self.peer_mode)
        mode_layout.addWidget(self.mode_hint)
        form_layout.addLayout(self._pair(role_block, mode_block))

        ip_block, ip_layout = self._block()
        self.ip_label = self._field_label("MODPARTENS IP")
        ip_layout.addWidget(self.ip_label)
        ip_frame = QFrame()
        ip_frame.setObjectName("field")
        ip_frame.setMinimumHeight(56)
        ip_row = QHBoxLayout(ip_frame)
        ip_row.setContentsMargins(12, 4, 8, 4)
        ip_row.setSpacing(8)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("192.168.1.42")
        port_caption = QLabel("port")
        port_caption.setProperty("role", "hint")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(41943)
        self.port.setFixedWidth(120)
        ip_row.addWidget(self.host_edit, 1)
        ip_row.addWidget(port_caption)
        ip_row.addWidget(self.port)
        ip_layout.addWidget(ip_frame)

        secret_block, secret_layout = self._block()
        secret_layout.addWidget(self._field_label("DELT HEMMELIGHED"))
        secret_frame = QFrame()
        secret_frame.setObjectName("field")
        secret_frame.setMinimumHeight(56)
        secret_row = QHBoxLayout(secret_frame)
        secret_row.setContentsMargins(12, 4, 8, 4)
        secret_row.setSpacing(8)
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("mindst 8 tegn")
        self.secret.textChanged.connect(lambda _text: self._update_fingerprint())
        self.eye = self._push("👁", "mini")
        self.eye.setFixedWidth(48)
        self.eye.clicked.connect(self._toggle_secret)
        new_secret = self._push("Ny", "mini")
        new_secret.setFixedWidth(56)
        new_secret.clicked.connect(self._generate_secret)
        secret_row.addWidget(self.secret, 1)
        secret_row.addWidget(self.eye)
        secret_row.addWidget(new_secret)
        self.fingerprint = self._hint("Mindst 8 tegn. Den skal være ens på begge enheder.")
        secret_layout.addWidget(secret_frame)
        secret_layout.addWidget(self.fingerprint)
        form_layout.addLayout(self._pair(ip_block, secret_block))

        keep_block, keep_layout = self._block()
        keep_layout.addWidget(self._field_label("BEHOLD BACKUPS"))
        keep_frame = QFrame()
        keep_frame.setObjectName("field")
        keep_frame.setMinimumHeight(56)
        keep_row = QHBoxLayout(keep_frame)
        keep_row.setContentsMargins(8, 4, 8, 4)
        minus = self._push("−", "mini")
        minus.setFixedWidth(56)
        plus = self._push("+", "mini")
        plus.setFixedWidth(56)
        minus.clicked.connect(lambda: self._step_retention(-1))
        plus.clicked.connect(lambda: self._step_retention(1))
        self.retention_label = QLabel("30 dage")
        self.retention_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.retention_label.setStyleSheet("font-size: 20px; color: #f3e6c8; background: transparent;")
        keep_row.addWidget(minus)
        keep_row.addWidget(self.retention_label, 1)
        keep_row.addWidget(plus)
        keep_layout.addWidget(keep_frame)

        action_block = QWidget()
        action_block.setStyleSheet("background: transparent;")
        action_row = QHBoxLayout(action_block)
        action_row.setContentsMargins(0, 22, 0, 0)
        action_row.setSpacing(14)
        open_backup = self._push("📁  Åbn backupmappe", "secondary")
        open_backup.clicked.connect(self._open_backup_root)
        test = self._push("Test forbindelse", "secondary")
        test.clicked.connect(self._test_connection)
        action_row.addWidget(open_backup, 1)
        action_row.addWidget(test, 1)
        form_layout.addLayout(self._pair(keep_block, action_block))

        self.block = Switch(True)
        block_row = ToggleRow(self.block)
        block_layout = QHBoxLayout(block_row)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(12)
        block_label = QLabel("Bloker synk mens D2R.exe kører")
        block_label.setStyleSheet("font-size: 18px; color: #e8dcc4; background: transparent;")
        block_layout.addWidget(self.block)
        block_layout.addWidget(block_label)
        block_layout.addStretch(1)
        form_layout.addWidget(block_row)
        form_layout.addStretch(1)
        scroll.setWidget(form)
        layout.addWidget(scroll, 1)

        self.settings_status = QLabel()
        self.settings_status.setObjectName("statusLine")
        self.settings_status.setWordWrap(True)
        layout.addWidget(self.settings_status)
        save = self._push("💾  Gem", "primary")
        save.clicked.connect(self._save_settings)
        cancel = self._push("Annuller", "secondary")
        cancel.clicked.connect(self._cancel_settings)
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        bottom.addWidget(save)
        bottom.addWidget(cancel)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return page

    def _page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return page

    def _title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("title")
        return label

    def _pill(self):
        frame = QFrame()
        frame.setObjectName("pill")
        frame.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 6, 16, 6)
        layout.setSpacing(8)
        dot = QLabel()
        dot.setObjectName("dot")
        dot.setFixedSize(12, 12)
        text = QLabel()
        text.setObjectName("pillText")
        layout.addWidget(dot)
        layout.addWidget(text)
        return frame, text, dot

    def _top(self, title: QLabel, pill: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _scroll(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().setStyleSheet("background: transparent;")
        return scroll

    def _push(self, text: str, kind: str) -> QPushButton:
        button = QPushButton(text)
        if kind == "mini":
            button.setProperty("role", "mini")
        else:
            button.setObjectName(kind)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        _polish(button)
        return button

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "fieldLabel")
        return label

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "hint")
        label.setWordWrap(True)
        return label

    def _block(self):
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        return box, layout

    def _pair(self, left: QWidget, right: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(22)
        row.addWidget(left, 1)
        row.addWidget(right, 1)
        return row

    def _toggle_block(self, switch: Switch, title: str, hint: str) -> QWidget:
        row = ToggleRow(switch)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(0)
        caption = QLabel(title)
        caption.setStyleSheet("font-size: 20px; color: #e8dcc4; background: transparent;")
        sub = QLabel(hint)
        sub.setProperty("role", "hint")
        text.addWidget(caption)
        text.addWidget(sub)
        layout.addWidget(switch)
        layout.addLayout(text)
        return row

    def show_dashboard_view(self, view: DashboardView) -> None:
        self._render_dashboard(view)
        self._show_page(0)

    def show_result_view(self, view: ResultView) -> None:
        self._render_result(view)
        self._show_page(1)

    def show_settings_draft(self, draft: SettingsDraft, detected: str = "") -> None:
        if detected:
            self._detected = detected
        self._fill_settings(draft)
        self._show_page(2)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        button = self.nav_group.button(index)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def refresh(self) -> None:
        if self._closing or (self._worker is not None and self._worker.isRunning()):
            return
        if self.cfg.role == "client":
            self.searching = True
            self._render_dashboard_from_state()
        snap = self._snapshot()
        self._start_worker(lambda: _job_refresh(snap), self._on_refresh, self._on_refresh_fail)

    def _on_refresh(self, payload: dict) -> None:
        self.searching = False
        self.game_running = payload.get("game")
        save = payload.get("save")
        self.save_folder = save
        if save is not None and not self._pinned:
            self._detected = str(save)
            if self.path_edit.text() != self._detected and not self._pinned:
                self.path_edit.setText(self._detected)
                self._update_path_label()
        self.local_files = payload.get("local_files") or {}
        self.local_d2s = payload.get("local_d2s") or {}
        self.remote_files = payload.get("remote_files") or {}
        self.remote_d2s = payload.get("remote_d2s") or {}
        self.decisions = payload.get("decisions")
        self.synced_at = payload.get("synced_at")
        self.connected = bool(payload.get("connected"))
        self.peer_game_running = bool(payload.get("peer_game"))
        if payload.get("peer_name"):
            self.peer_name = payload["peer_name"]
        if payload.get("peer_host"):
            self.peer_host = payload["peer_host"]
        if not self.connected and self.cfg.role == "client":
            self.peer_name = ""
            self.peer_host = ""
        error = payload.get("error") or ""
        if self.game_running is None and not error:
            error = "Kunne ikke afgøre, om spillet kører. Synkronisering fortsætter."
        if error:
            self._note = error
            self._note_sticky = False
            if error != self._last_logged_note:
                self._append_log(error)
                self._last_logged_note = error
        elif self._session_note:
            self._note = self._session_note
            self._session_note = ""
            self._note_sticky = True
        elif not self._note_sticky:
            self._note = ""
        self._render_dashboard_from_state()

    def _on_refresh_fail(self, exc: BaseException) -> None:
        self.searching = False
        self._note = danish_error(exc)
        self._note_sticky = False
        self._render_dashboard_from_state()

    def _render_dashboard_from_state(self) -> None:
        try:
            has_secret = bool(self.cfg.secret_value())
        except ConfigError:
            has_secret = False
        view = make_dashboard_view(
            rows=build_rows(
                self.local_files,
                self.remote_files,
                self.decisions,
                self.local_d2s,
                self.remote_d2s,
                datetime.now(),
            ),
            peer_name=self.peer_name,
            peer_host=self.peer_host,
            connected=self.connected,
            searching=self.searching,
            role=self.cfg.role,
            hosting=self.hosting,
            game_running=bool(self.game_running),
            peer_game_running=self.peer_game_running,
            block_game=self.cfg.refuse_if_game_running,
            busy=self.busy,
            has_secret=has_secret,
            has_save=self.save_folder is not None,
            synced_at=self.synced_at,
            now=datetime.now(),
        )
        self._render_dashboard(view)
        self.dash_note.setText(self._note)
        self.dash_note.setVisible(bool(self._note))

    def _render_dashboard(self, view: DashboardView) -> None:
        self.dash_title.setText("D2R Save Sync")
        _set_pill(self.dash_pill, self.dash_pill_text, self.dash_dot, view.pill_text, view.pill_kind)
        if view.warning:
            self.warn_text.setText(_warning_html(view.warning))
            self.warn.show()
        else:
            self.warn.hide()
        headers = ["KARAKTER", "KLASSE / NIVEAU", "DENNE PC", view.peer_header.upper(), "STATUS"]
        records = []
        for row in view.rows:
            records.append(
                [
                    _name_cell(row.title, row.detail),
                    _text_cell(row.subtitle, "charSub"),
                    _text_cell(row.local_time, "time", row.local_newer),
                    _text_cell(row.remote_time, "time", row.remote_newer),
                    _badge_cell(row.status_label, row.status),
                ]
            )
        _fill_table(
            self.table_scroll,
            headers,
            records,
            [4, 3, 3, 3, 3],
            view.empty_text,
        )
        self.sync_btn.setText("⟳  " + view.button_text)
        self.sync_btn.setEnabled(view.button_enabled)
        self.footer.setText(view.footer)

    def _render_result(self, view: ResultView) -> None:
        self.result_title.setText(view.title)
        _set_pill(self.result_pill, self.result_pill_text, self.result_dot, view.pill, "ok")
        self._archive_rel = view.archive_dir
        _clear_layout(self.conflict_layout)
        if view.conflict is None:
            self.conflict_host.hide()
        else:
            self._fill_conflict(view.conflict)
            self.conflict_host.show()
        records = []
        for item in view.files:
            records.append(
                [
                    _name_cell(item.filename, ""),
                    _badge_cell(item.action, item.action_kind),
                    _text_cell(item.backup, "hint"),
                ]
            )
        _fill_table(
            self.file_scroll,
            ["FIL", "HANDLING", "BACKUP"],
            records,
            [4, 4, 3],
            "Ingen filer blev ændret.",
        )
        self.result_empty.setVisible(not view.files and view.conflict is None)

    def _fill_conflict(self, conflict: ConflictView) -> None:
        title = QLabel()
        title.setObjectName("conflictTitle")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        name = html.escape(conflict.name)
        klass = html.escape(conflict.class_name)
        reason = html.escape(conflict.reason)
        klass_bit = f"({klass}) " if klass else ""
        title.setText(
            f"⚔ Konflikt: <span style='color:#f3e6c8; font-weight:700'>{name}</span> "
            f"<span style='color:#8a7a62'>{klass_bit}– {reason}</span>"
        )
        self.conflict_layout.addWidget(title)
        cards = QHBoxLayout()
        cards.setSpacing(18)
        cards.addWidget(_side_frame(conflict.winner, True), 1)
        cards.addWidget(_side_frame(conflict.loser, False), 1)
        self.conflict_layout.addLayout(cards)
        policy = QLabel()
        policy.setTextFormat(Qt.TextFormat.RichText)
        policy.setWordWrap(True)
        policy.setProperty("role", "hint")
        policy.setText(_policy_html(conflict.policy))
        self.conflict_layout.addWidget(policy)

    def _primary_action(self) -> None:
        if self.stack.currentIndex() != 0 or not self.sync_btn.isEnabled():
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if self.cfg.role == "host":
            self._start_host()
            return
        self._start_sync()

    def _start_sync(self) -> None:
        snap = self._snapshot()
        self.busy = True
        self._note_sticky = False
        self._render_dashboard_from_state()
        self._start_worker(lambda: _job_sync(snap), self._on_sync, self._on_sync_fail)

    def _on_sync(self, payload: dict) -> None:
        self.busy = False
        self.searching = False
        view = payload["view"]
        self._append_log(view.log_text)
        self._render_result(view)
        self._show_page(1)
        self._render_dashboard_from_state()
        self.refresh()

    def _on_sync_fail(self, exc: BaseException) -> None:
        self.busy = False
        self.searching = False
        text = danish_error(exc)
        self._note = text
        self._note_sticky = False
        self._append_log(text)
        self._render_dashboard_from_state()
        self._error_dialog(text)

    def _start_host(self) -> None:
        if self.hosting or (self._server_thread is not None and self._server_thread.is_alive()):
            return
        snap = self._snapshot()
        if not snap.secret:
            self._error_dialog("Angiv en delt hemmelighed under Indstillinger.")
            return
        self._host_stop_requested = False
        self.busy = True
        self._render_dashboard_from_state()
        thread = threading.Thread(target=self._serve, args=(snap,), name="d2r-host", daemon=True)
        self._server_thread = thread
        thread.start()
        QTimer.singleShot(50, self._poll_host)

    def _serve(self, snap: SimpleNamespace) -> None:
        try:
            if self._host_stop_requested:
                return
            root = resolve_save_folder(snap.save_path)
            server = _make_server(snap, root, self.session_arrived.emit, _HostLog(self))
            self._server = server
            if self._host_stop_requested:
                return
            server.serve_forever()
        except Exception as exc:  # noqa: BLE001 — reported on the window
            self.host_failed.emit(danish_error(exc))
        finally:
            self.host_stopped.emit()

    def _poll_host(self) -> None:
        if self._closing:
            return
        server = self._server
        thread = self._server_thread
        if server is not None and server._ready.is_set():
            self.busy = False
            self.hosting = True
            self._render_dashboard_from_state()
            return
        if thread is None or not thread.is_alive():
            self.busy = False
            self.hosting = False
            self._render_dashboard_from_state()
            return
        QTimer.singleShot(50, self._poll_host)

    def _stop_host(self) -> None:
        self._host_stop_requested = True
        self.hosting = False
        server = self._server
        if server is not None:
            server.stop()
        thread = self._server_thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(2.0)
        self._server = None
        self._server_thread = None

    def _on_host_failed(self, message: str) -> None:
        self.busy = False
        self.hosting = False
        self._note = message
        self._note_sticky = False
        self._append_log(message)
        self._render_dashboard_from_state()
        self._error_dialog(message)

    def _on_host_stopped(self) -> None:
        if self._host_stop_requested:
            return
        self.hosting = False
        self.busy = False
        self._render_dashboard_from_state()

    def _on_session(self) -> None:
        self._session_note = "Modparten har synkroniseret."
        self._append_log(self._session_note)
        self.refresh()

    def _done(self) -> None:
        self._show_page(0)
        self.refresh()

    def _save_settings(self) -> None:
        draft = self._read_draft()
        try:
            apply_draft(self.cfg, draft)
            write_config(self.cfg, self._config_file)
        except (ConfigError, OSError) as exc:
            self.settings_status.setText(danish_error(exc))
            return
        self._pinned = bool(draft.save_path)
        self.settings_status.setText("Indstillinger gemt.")
        self._stop_host()
        self.busy = False
        self.hosting = False
        self.connected = False
        self._note_sticky = False
        self._render_dashboard_from_state()
        self.refresh()

    def _cancel_settings(self) -> None:
        self._fill_settings(draft_from_config(self.cfg))
        self.settings_status.setText("")
        self._show_page(0)

    def _read_draft(self) -> SettingsDraft:
        return SettingsDraft(
            save_path=self.path_edit.text().strip() if self._pinned else "",
            role=self.role.current(),
            peer_mode=self.peer_mode.current(),
            peer_host=self.host_edit.text().strip(),
            port=int(self.port.value()),
            secret=self.secret.text(),
            retention_days=self._retention,
            block_game=self.block.isChecked(),
        )

    def _fill_settings(self, draft: SettingsDraft) -> None:
        self._pinned = bool(draft.save_path)
        if draft.save_path:
            self.path_edit.setText(draft.save_path)
        else:
            self.path_edit.setText(self._detected)
        self.role.set_current(draft.role, emit=False)
        self.peer_mode.set_current(draft.peer_mode, emit=False)
        self.host_edit.setText(draft.peer_host)
        self.port.setValue(int(draft.port))
        self.secret.setText(draft.secret)
        self._retention = int(draft.retention_days)
        self._update_retention()
        self.block.blockSignals(True)
        self.block.setChecked(bool(draft.block_game))
        self.block.blockSignals(False)
        self._update_setting_hints()

    def _update_setting_hints(self) -> None:
        role = self.role.current()
        mode = self.peer_mode.current()
        manual = mode == "manual"
        if role == "host":
            self.role_hint.setText("Vært venter på den anden enhed (serve)")
            self.host_edit.setReadOnly(True)
            self.host_edit.setPlaceholderText("Denne maskine")
            self.port.setEnabled(True)
            if manual:
                self.mode_hint.setText("Porten er lytteporten. Automatisk søgning er slået fra.")
            else:
                self.mode_hint.setText("Porten er lytteporten. Modparten kan finde denne maskine på netværket.")
        else:
            self.role_hint.setText("Denne enhed forbinder og synkroniserer")
            self.host_edit.setReadOnly(not manual)
            self.port.setEnabled(manual)
            self.host_edit.setPlaceholderText("192.168.1.42" if manual else "Findes automatisk")
            if manual:
                self.mode_hint.setText("Bruger den IP og port du angiver")
            else:
                self.mode_hint.setText("UDP-broadcast på hjemmenetværket")
        self._update_path_label()
        self._update_fingerprint()

    def _update_path_label(self) -> None:
        if self._pinned:
            self.path_label.setText("GEMMEMAPPE")
        else:
            self.path_label.setText("GEMMEMAPPE (FUNDET AUTOMATISK)")

    def _update_fingerprint(self) -> None:
        secret = self.secret.text().strip()
        if len(secret) < 8:
            self.fingerprint.setText("Mindst 8 tegn. Den skal være ens på begge enheder.")
            return
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]
        extra = ""
        if os.environ.get("D2R_SYNC_SECRET"):
            extra = " Miljøvariablen D2R_SYNC_SECRET bruges i stedet, når den er sat."
        self.fingerprint.setText(f"Fingeraftryk {digest}.{extra}")

    def _update_retention(self) -> None:
        self.retention_label.setText(f"{self._retention} dage")

    def _step_retention(self, delta: int) -> None:
        self._retention = min(365, max(1, self._retention + delta))
        self._update_retention()

    def _toggle_secret(self) -> None:
        hidden = self.secret.echoMode() == QLineEdit.EchoMode.Password
        self.secret.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.eye.setText("🙈" if hidden else "👁")

    def _generate_secret(self) -> None:
        import secrets

        self.secret.setText(secrets.token_urlsafe(24))
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.eye.setText("👁")

    def _choose_folder(self) -> None:
        start = self.path_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Vælg gemmemappe", start)
        if not chosen:
            return
        self._pinned = True
        self.path_edit.setText(chosen)
        self._update_path_label()

    def _clear_folder_override(self) -> None:
        self._pinned = False
        self.path_edit.setText(self._detected)
        self._update_path_label()

    def _test_connection(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.settings_status.setText("Vent et øjeblik…")
            return
        try:
            draft = self._read_draft()
            trial = copy.copy(self.cfg)
            apply_draft(trial, draft)
        except ConfigError as exc:
            self.settings_status.setText(danish_error(exc))
            return
        if trial.role == "host":
            server = self._server
            if self.hosting and server is not None and server.bound_port:
                extra = ""
                if server.bound_port != trial.port:
                    extra = " Gem og start værten igen, hvis porten skal skiftes."
                self.settings_status.setText(f"Værten lytter på port {server.bound_port}.{extra}")
            else:
                self.settings_status.setText("Værten er ikke startet. Gem, gå til Synk, og tryk Start vært.")
            return
        snap = self._snapshot(trial)
        if not snap.secret:
            self.settings_status.setText("Angiv en delt hemmelighed først.")
            return
        self.settings_status.setText("Tester forbindelsen…")
        self._start_worker(lambda: _job_test(snap), self._on_test, self._on_test_fail)

    def _on_test(self, message: str) -> None:
        self.settings_status.setText(message)

    def _on_test_fail(self, exc: BaseException) -> None:
        self.settings_status.setText(danish_error(exc))

    def _open_backup_root(self) -> None:
        folder = self._current_save_text()
        if folder is None:
            self.settings_status.setText("Gemmemappen blev ikke fundet. Vælg den først.")
            return
        self._open_folder(folder / ".d2r-sync-backups")

    def _open_archive(self) -> None:
        folder = self.save_folder or self._current_save_text()
        if folder is None:
            self._error_dialog("Gemmemappen blev ikke fundet.")
            return
        target = folder / ".d2r-sync-backups"
        if self._archive_rel:
            target = folder / self._archive_rel
        self._open_folder(target)

    def _open_folder(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._error_dialog(danish_error(exc))
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if not opened:
            self.settings_status.setText(f"Mappen ligger her: {path}")

    def _current_save_text(self) -> Path | None:
        text = self.path_edit.text().strip()
        if text and _is_dir(Path(text)):
            return Path(text)
        if self.save_folder is not None and _is_dir(self.save_folder):
            return self.save_folder
        return None

    def _show_log(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Log")
        dialog.resize(820, 500)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(self._log_lines) or "Ingen log endnu.")
        layout.addWidget(text)
        close = self._push("Luk", "secondary")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignLeft)
        dialog.exec()

    def _error_dialog(self, text: str) -> None:
        QMessageBox.warning(self, "D2R Save Sync", text)

    def _append_log(self, line: str) -> None:
        if not line:
            return
        for part in str(line).splitlines() or [str(line)]:
            self._log_lines.append(part)
        del self._log_lines[:-200]

    def _snapshot(self, cfg: Config | None = None) -> SimpleNamespace:
        cfg = self.cfg if cfg is None else cfg
        try:
            secret = cfg.secret_value()
        except ConfigError:
            secret = ""
        raw_name = cfg.name or socket.gethostname() or "d2r"
        return SimpleNamespace(
            save_path=cfg.save_path,
            role=cfg.role,
            peer_mode=cfg.peer_mode,
            peer=cfg.peer,
            port=cfg.port,
            discover_port=cfg.discover_port,
            secret=secret,
            name=safe_service_name(raw_name),
            listen_host=cfg.listen_host,
            refuse=cfg.refuse_if_game_running,
            conflict=gui_conflict_policy(cfg.conflict),
            retention_count=cfg.backup_retention_count,
            retention_days=cfg.backup_retention_days,
            state_file=self._state_file(),
            dry_run=self.dry.isChecked(),
        )

    def _state_file(self) -> Path:
        if self._config_file is not None:
            return self._config_file.resolve().parent / "state.json"
        return state_path()

    def _start_worker(self, fn, on_ok, on_err) -> bool:
        if self._worker is not None and self._worker.isRunning():
            return False
        worker = FuncWorker(fn, self)

        def ok(result, current=worker):
            if self._worker is current:
                self._worker = None
            on_ok(result)

        def err(exc, current=worker):
            if self._worker is current:
                self._worker = None
            on_err(exc)

        worker.succeeded.connect(ok)
        worker.failed.connect(err)
        self._worker = worker
        worker.start()
        return True

    def _go_back(self) -> None:
        index = self.stack.currentIndex()
        if index == 2:
            self._cancel_settings()
        elif index == 1:
            self._done()
        else:
            self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.isAutoRepeat():
            return
        focus = QApplication.focusWidget()
        editable = isinstance(focus, (QLineEdit, QPlainTextEdit, QAbstractSpinBox))
        if event.key() == Qt.Key.Key_Escape:
            self._go_back()
            return
        if not editable and event.key() in (Qt.Key.Key_Y, Qt.Key.Key_F5):
            self._primary_action()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        self._timer.stop()
        self._stop_host()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(1500)
        event.accept()


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def resolve_save_folder(save_path: str | None) -> Path:
    try:
        locations = detect_save_folders()
        override = Path(save_path) if save_path else None
        return choose_save_folder(locations, override)
    except FileNotFoundError as exc:
        raise SavePathError(str(exc)) from exc
    except OSError as exc:
        raise SavePathError(
            "could not find a Diablo II Resurrected save folder. "
            "Set save_path in the config file."
        ) from exc


def locate_peer(peer_mode: str, peer: str | None, port: int, discover_port: int) -> tuple[str, int]:
    if peer_mode == "manual":
        if not peer:
            raise D2RSyncError("no peer address configured")
        return parse_peer(peer, port)
    found = discover(discover_port, timeout=2.5)
    if not found:
        raise D2RSyncError(
            "no peer answered LAN discovery. Start the other machine, or set a manual IP."
        )
    if len(found) > 1:
        listed = ", ".join(item.label() for item in found)
        raise D2RSyncError(f"more than one peer answered ({listed}). Set a manual IP.")
    return found[0].host, found[0].port


def _job_refresh(snap: SimpleNamespace) -> dict:
    payload = {
        "game": False,
        "save": None,
        "local_files": {},
        "local_d2s": {},
        "remote_files": {},
        "remote_d2s": {},
        "decisions": None,
        "synced_at": None,
        "connected": False,
        "peer_name": "",
        "peer_host": "",
        "peer_game": False,
        "error": "",
    }
    try:
        payload["game"] = game_is_running()
    except Exception:  # noqa: BLE001
        payload["game"] = None
    try:
        save = resolve_save_folder(snap.save_path)
    except D2RSyncError as exc:
        payload["error"] = danish_error(exc)
        return payload
    payload["save"] = save
    try:
        store = DirectorySave(save, BackupManager(save))
        payload["local_files"] = store.inventory()
        payload["local_d2s"] = read_local_d2s(save)
        payload["synced_at"] = load_state(snap.state_file, save).synced_at
    except D2RSyncError as exc:
        payload["error"] = danish_error(exc)
        return payload
    if snap.role != "client":
        return payload
    if not snap.secret:
        payload["error"] = "Angiv en delt hemmelighed under Indstillinger."
        return payload
    try:
        host, port = locate_peer(snap.peer_mode, snap.peer, snap.port, snap.discover_port)
        preview = preview_with_peer(
            local_root=save,
            state_file=snap.state_file,
            secret=snap.secret,
            host=host,
            port=port,
            name=snap.name,
        )
    except Exception as exc:  # noqa: BLE001 — expected peer failures
        payload["error"] = danish_error(exc)
        return payload
    payload["connected"] = True
    payload["peer_name"] = preview.peer_name
    payload["peer_host"] = preview.peer_host
    payload["remote_files"] = preview.remote_files
    payload["remote_d2s"] = preview.remote_d2s
    payload["decisions"] = preview.decisions
    payload["peer_game"] = preview.peer_game_running
    payload["local_files"] = preview.local_files
    return payload


def _job_sync(snap: SimpleNamespace) -> dict:
    root = resolve_save_folder(snap.save_path)
    host, port = locate_peer(snap.peer_mode, snap.peer, snap.port, snap.discover_port)
    resolved, outcomes, notes = sync_with_peer(
        local_root=root,
        state_file=snap.state_file,
        secret=snap.secret,
        host=host,
        port=port,
        conflict=snap.conflict,
        dry_run=snap.dry_run,
        force=False,
        refuse_if_game_running=snap.refuse,
        retention_count=snap.retention_count,
        retention_days=snap.retention_days,
        name=snap.name,
        chooser=None,
    )
    peer_name = safe_service_name(str(notes.get("peer_name") or host))
    decisions = list(notes.get("decisions") or [])
    log_text = format_plan(resolved, dry_run=snap.dry_run)
    errors = format_outcomes(outcomes)
    if errors:
        log_text = log_text + "\n" + errors
    view = build_result_view(
        decisions=decisions,
        resolved=resolved,
        outcomes=outcomes,
        local_d2s=dict(notes.get("local_d2s") or {}),
        remote_d2s=dict(notes.get("remote_d2s") or {}),
        peer_name=peer_name,
        dry_run=snap.dry_run,
        conflict_policy=snap.conflict,
        now=datetime.now(),
        log_text=log_text,
    )
    return {"view": view, "save": root}


def _job_test(snap: SimpleNamespace) -> str:
    host, port = locate_peer(snap.peer_mode, snap.peer, snap.port, snap.discover_port)
    conn = connect(host, port, snap.secret, snap.name, 8.0)
    RemoteSave(conn).close()
    return f"Forbundet til {host}:{port}."


def _make_server(snap: SimpleNamespace, root: Path, on_session, log) -> Server:
    store = DirectorySave(
        root,
        BackupManager(root, retention_count=snap.retention_count, retention_days=snap.retention_days),
    )
    state = load_state(snap.state_file, root)
    return Server(
        store,
        state,
        snap.secret,
        host=snap.listen_host,
        port=snap.port,
        name=snap.name,
        discover_port=snap.discover_port,
        refuse_if_game_running=snap.refuse,
        discover=snap.peer_mode != "manual",
        log=log,
        on_session=on_session,
    )


def _side_frame(card: SideCard, winner: bool) -> QFrame:
    frame = QFrame()
    frame.setObjectName("winner" if winner else "loser")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(8)
    badge = Badge(card.badge, card.badge_kind)
    layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
    heading = QLabel(card.heading)
    heading.setObjectName("cardHeading")
    line = QLabel(card.line)
    line.setObjectName("cardLine")
    hint = QLabel(card.hint)
    hint.setProperty("role", "hint")
    hint.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(line)
    layout.addWidget(hint)
    return frame


def _warning_html(text: str) -> str:
    safe = html.escape(text)
    if ". " in safe:
        head, tail = safe.split(". ", 1)
        return f"<b>{head}.</b> {tail}"
    return safe


def _policy_html(text: str) -> str:
    safe = html.escape(text)
    if safe.startswith("Politik: ") and " – " in safe:
        head, tail = safe.split(" – ", 1)
        _label, _sep, name = head.partition(": ")
        return f"Politik: <b style='color:#e0b65a'>{name}</b> – {tail}"
    return safe


def _set_pill(frame, text, dot, message: str, kind: str) -> None:
    text.setText(message)
    frame.setProperty("kind", kind)
    dot.setProperty("kind", kind)
    for widget in (frame, text, dot):
        _polish(widget)


def _polish(widget) -> None:
    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)


def _name_cell(title: str, detail: str) -> QWidget:
    box = QWidget()
    box.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(0)
    name = QLabel(title)
    name.setProperty("role", "charName")
    layout.addWidget(name)
    if detail:
        sub = QLabel(detail)
        sub.setProperty("role", "charDetail")
        layout.addWidget(sub)
    return box


def _text_cell(text: str, role: str, newer: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    if newer:
        label.setProperty("newer", "true")
    label.setContentsMargins(8, 8, 8, 8)
    _polish(label)
    return label


def _badge_cell(text: str, status: str) -> QWidget:
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(8, 6, 8, 6)
    layout.addWidget(Badge(text, status))
    layout.addStretch(1)
    return wrap


def _fill_table(scroll, headers, records, stretches, empty: str) -> None:
    inner = QWidget()
    inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    inner.setStyleSheet("background: transparent;")
    grid = QGridLayout(inner)
    grid.setContentsMargins(12, 6, 12, 8)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(0)
    for column, text in enumerate(headers):
        label = QLabel(text)
        label.setProperty("role", "colhead")
        label.setContentsMargins(8, 8, 8, 8)
        grid.addWidget(label, 0, column)
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: #4a3523; border: none;")
    grid.addWidget(line, 1, 0, 1, len(headers))
    if not records:
        message = QLabel(empty)
        message.setProperty("role", "hint")
        message.setContentsMargins(8, 16, 8, 16)
        grid.addWidget(message, 2, 0, 1, len(headers))
    for row_index, cells in enumerate(records):
        for column, widget in enumerate(cells):
            grid.addWidget(widget, row_index + 2, column)
        grid.setRowMinimumHeight(row_index + 2, 52)
    for column, stretch in enumerate(stretches):
        grid.setColumnStretch(column, stretch)
    grid.setRowStretch(max(len(records), 1) + 2, 1)
    scroll.setWidget(inner)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif child is not None:
            _clear_layout(child)
