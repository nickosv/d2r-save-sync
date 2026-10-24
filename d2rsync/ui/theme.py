"""Dark Diablo-inspired stylesheet, matched to the 1280x800 mockup."""

STYLESHEET = """
* { font-family: "Segoe UI", "Liberation Sans", "DejaVu Sans", sans-serif; }
QMainWindow, QWidget#root { background: transparent; color: #e8dcc4; }
QWidget#sidebar {
    background: #0d0a09;
    border-right: 1px solid #3a2a1c;
}
QLabel#logo {
    color: #c9a24a;
    border: 2px solid #c9a24a;
    border-radius: 10px;
    background: #1c120d;
    font-family: Georgia, "Liberation Serif", "DejaVu Serif", serif;
    font-size: 20px;
    font-weight: 800;
}
QPushButton[role="nav"] {
    background: transparent;
    color: #9a8a70;
    border: 1px solid transparent;
    border-radius: 12px;
    font-size: 13px;
    padding: 6px 2px;
}
QPushButton[role="nav"]:hover { background: #1c1410; }
QPushButton[role="nav"]:checked {
    background: #3a1410;
    color: #f0d38a;
    border: 1px solid #8a2a1e;
}
QPushButton[role="nav"]:focus { border: 1px solid #e0b65a; }
QLabel#title {
    font-family: Georgia, "Liberation Serif", "DejaVu Serif", serif;
    color: #e0b65a;
    font-size: 28px;
}
QWidget#mainColumn, QWidget#page { background: transparent; }
QFrame#pill {
    background: #13221a;
    border: 1px solid #2f7a4a;
    border-radius: 18px;
}
QFrame#pill[kind="wait"] {
    background: #2a2412;
    border: 1px solid #8a7030;
}
QFrame#pill[kind="bad"] {
    background: #3a1410;
    border: 1px solid #c0392b;
}
QLabel#pillText { background: transparent; font-size: 16px; color: #8fe0a8; }
QFrame#pill[kind="wait"] QLabel#pillText { color: #f0cf6a; }
QFrame#pill[kind="bad"] QLabel#pillText { color: #ffb4a8; }
QLabel#dot {
    background: #45d07a;
    border-radius: 6px;
    min-width: 12px;
    max-width: 12px;
    min-height: 12px;
    max-height: 12px;
}
QLabel#dot[kind="wait"] { background: #e0b65a; }
QLabel#dot[kind="bad"] { background: #e05040; }
QLabel#statusLine { color: #f0cf6a; font-size: 15px; }
QLabel#conflictTitle { color: #f3e6c8; font-size: 20px; }
QLabel#cardHeading { color: #f3e6c8; font-size: 20px; font-weight: 700; background: transparent; }
QLabel#cardLine { color: #e8dcc4; font-size: 16px; background: transparent; }
QFrame#field QLineEdit {
    border: none;
    background: transparent;
    min-height: 40px;
}
QSpinBox::up-button, QSpinBox::down-button { width: 28px; }
QDialog { background: #120d0b; }
QMessageBox { background: #1a120e; }
QMessageBox QLabel { color: #e8dcc4; font-size: 16px; }
QMessageBox QPushButton {
    min-height: 40px;
    min-width: 96px;
    padding: 6px 16px;
    background: #22180f;
    color: #e8dcc4;
    border: 1px solid #6a5030;
    border-radius: 8px;
}
QFrame#warn {
    background: #4a1410;
    border: 1px solid #c0392b;
    border-radius: 10px;
}
QLabel#warnText { color: #ffd0c8; font-size: 16px; }
QFrame#panel {
    background: rgba(24, 18, 15, 230);
    border: 1px solid #4a3523;
    border-radius: 12px;
}
QLabel[role="colhead"] {
    color: #b39a6a;
    font-size: 13px;
    font-weight: 600;
}
QLabel[role="charName"] { color: #f3e6c8; font-size: 18px; font-weight: 700; }
QLabel[role="charSub"] { color: #cbbfa6; font-size: 16px; }
QLabel[role="charDetail"] { color: #8a7a62; font-size: 13px; }
QLabel[role="time"] { color: #e8dcc4; font-size: 16px; }
QLabel[role="time"][newer="true"] { color: #e0b65a; font-weight: 700; }
QLabel[role="badge"] {
    border-radius: 14px;
    font-size: 15px;
    font-weight: 600;
    padding: 4px 12px;
}
QLabel[role="badge"][status="ok"] { background: #16261c; color: #7fd69a; border: 1px solid #2f6a44; }
QLabel[role="badge"][status="send"] { background: #1a2030; color: #8fb4ff; border: 1px solid #3a5a9a; }
QLabel[role="badge"][status="get"] { background: #2a2412; color: #f0cf6a; border: 1px solid #8a7030; }
QLabel[role="badge"][status="conflict"] { background: #3a1410; color: #ff9a88; border: 1px solid #c0392b; }
QLabel[role="badge"][status="mixed"] { background: #2a2030; color: #d0b0ff; border: 1px solid #6a5090; }
QLabel[role="badge"][status="delete"] { background: #2a1814; color: #e0a090; border: 1px solid #804030; }
QLabel[role="badge"][status="unknown"] { background: #1a1612; color: #9a8a70; border: 1px solid #4a4030; }
QLabel[role="badge"][status="newest"] { background: #2a2412; color: #f0cf6a; border: 1px solid #e0b65a; }
QLabel[role="badge"][status="older"] { background: #222; color: #bbb; border: 1px solid #555; }
QPushButton#primary {
    min-height: 64px;
    padding: 0 28px;
    border-radius: 12px;
    font-size: 22px;
    font-weight: 700;
    color: #fff1cf;
    border: 2px solid #c9a24a;
    background: #8a1c14;
}
QPushButton#primary:hover { background: #a3241a; }
QPushButton#primary:focus { border: 2px solid #ffe0a0; }
QPushButton#primary:disabled {
    color: #b0a090;
    background: #3a2a24;
    border: 2px solid #6a5840;
}
QPushButton#secondary {
    min-height: 56px;
    padding: 0 18px;
    border-radius: 12px;
    font-size: 18px;
    font-weight: 600;
    color: #e8dcc4;
    border: 1px solid #6a5030;
    background: #22180f;
}
QPushButton#secondary:hover { background: #2c2014; }
QPushButton#secondary:focus { border: 1px solid #e0b65a; }
QPushButton[role="mini"] {
    min-height: 44px;
    padding: 0 14px;
    border-radius: 10px;
    font-size: 16px;
    color: #e8dcc4;
    border: 1px solid #6a5030;
    background: #22180f;
}
QPushButton[role="mini"]:focus { border: 1px solid #e0b65a; }
QLabel[role="hint"] { color: #8a7a62; font-size: 14px; }
QLabel#pad { color: #6a5a48; font-size: 13px; }
QLabel[role="fieldLabel"] {
    color: #b39a6a;
    font-size: 13px;
    font-weight: 600;
}
QLineEdit, QSpinBox {
    min-height: 48px;
    border-radius: 10px;
    background: #0f0b09;
    border: 1px solid #5a4028;
    color: #f3e6c8;
    font-size: 18px;
    padding: 0 12px;
    selection-background-color: #6a3018;
}
QLineEdit:focus, QSpinBox:focus { border: 1px solid #e0b65a; }
QLineEdit:disabled { color: #d9cbb4; }
QFrame#field {
    background: #0f0b09;
    border: 1px solid #5a4028;
    border-radius: 10px;
}
QFrame#seg {
    border: 1px solid #6a5030;
    border-radius: 12px;
    background: #1a130e;
}
QPushButton[role="seg"] {
    min-height: 56px;
    border: none;
    border-radius: 0;
    background: #1a130e;
    color: #e8dcc4;
    font-size: 18px;
}
QPushButton[role="seg"][end="left"] { border-top-left-radius: 11px; border-bottom-left-radius: 11px; }
QPushButton[role="seg"][end="right"] { border-top-right-radius: 11px; border-bottom-right-radius: 11px; }
QPushButton[role="seg"]:checked {
    background: #8a1c14;
    color: #fff1cf;
    font-weight: 700;
}
QPushButton[role="seg"]:focus { border: 1px solid #e0b65a; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical {
    background: #140f0c;
    width: 16px;
    margin: 4px;
    border-radius: 8px;
}
QScrollBar::handle:vertical {
    background: #6a5030;
    min-height: 40px;
    border-radius: 7px;
}
QPlainTextEdit {
    background: #0f0b09;
    color: #e8dcc4;
    border: 1px solid #5a4028;
    border-radius: 10px;
    font-size: 15px;
}
QFrame#winner {
    border: 1px solid #e0b65a;
    border-radius: 12px;
    background: rgba(24, 18, 15, 230);
}
QFrame#loser {
    border: 1px solid #4a3523;
    border-radius: 12px;
    background: rgba(24, 18, 15, 230);
}
"""
