"""Large, focusable controls shared by the three screens."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


class Switch(QWidget):
    """70x38 touch switch. Space and click both toggle it."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(70, 38)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on = checked

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if checked == self._on:
            return
        self._on = checked
        self.update()
        self.toggled.emit(self._on)

    def mousePressEvent(self, event):  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self.setChecked(not self._on)
        event.accept()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self._on)
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor("#7a5a1a" if self._on else "#3a2a1c")
        border = QColor("#e0b65a" if self._on or self.hasFocus() else "#6a5030")
        painter.setPen(QPen(border, 2))
        painter.setBrush(track)
        painter.drawRoundedRect(1, 1, 68, 36, 18, 18)
        knob = QColor("#f0d38a" if self._on else "#9a8a70")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob)
        x = 36 if self._on else 4
        painter.drawEllipse(x, 4, 30, 30)


class ToggleRow(QWidget):
    """Switch plus caption. A click on the caption toggles the switch."""

    def __init__(self, switch: Switch, parent=None):
        super().__init__(parent)
        self.switch = switch
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802
        pos = event.position().toPoint()
        if self.childAt(pos) is self.switch:
            super().mouseReleaseEvent(event)
            return
        self.switch.setChecked(not self.switch.isChecked())
        event.accept()


class Segment(QWidget):
    """Two-choice bar. `changed` emits the selected key."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str, parent=None):
        super().__init__(parent)
        self.setObjectName("seg")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._buttons: dict[str, QPushButton] = {}
        last = len(options) - 1
        for index, (key, label) in enumerate(options):
            button = QPushButton(label)
            button.setProperty("role", "seg")
            button.setProperty("end", "left" if index == 0 else "right" if index == last else "mid")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, item=key: self.set_current(item))
            layout.addWidget(button)
            self._buttons[key] = button
        self.set_current(current, emit=False)

    def current(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return next(iter(self._buttons))

    def set_current(self, key: str, emit: bool = True) -> None:
        if key not in self._buttons:
            return
        for item, button in self._buttons.items():
            button.setChecked(item == key)
        if emit:
            self.changed.emit(key)


class Badge(QLabel):
    def __init__(self, text: str, status: str, parent=None):
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        self.set_status(status)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(36)

    def set_status(self, status: str) -> None:
        self.setProperty("status", status)
        self.style().unpolish(self)
        self.style().polish(self)


class NavButton(QPushButton):
    def __init__(self, glyph: str, label: str, parent=None):
        super().__init__(parent)
        self.setProperty("role", "nav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(80, 76)
        inner = QVBoxLayout(self)
        inner.setContentsMargins(0, 6, 0, 6)
        inner.setSpacing(0)
        self._mark = QLabel(glyph)
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text = QLabel(label)
        self._text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(self._mark)
        inner.addWidget(self._text)
        self._mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.toggled.connect(self._recolor)
        self._recolor(False)

    def _recolor(self, checked: bool) -> None:
        color = "#f0d38a" if checked else "#9a8a70"
        self._mark.setStyleSheet(f"font-size: 24px; background: transparent; color: {color};")
        self._text.setStyleSheet(f"font-size: 12px; background: transparent; color: {color};")
