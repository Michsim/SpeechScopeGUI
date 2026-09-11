"""Vzhled aplikace: barvy, písmo a stylesheet.

Jeden světlý motiv nad stylem Fusion, aby okno vypadalo stejně na každém
Windows bez ohledu na systémový tmavý režim. Barvy stavů (`OK`, `MISSING`,
`NEUTRAL`) používají stránky pro tečky a štítky, ostatní widgety si vezmou
vzhled ze stylesheetu podle `objectName` a vlastnosti `role`.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_SOFT = "#dbeafe"
OK = "#16a34a"
OK_SOFT = "#dcfce7"
MISSING = "#dc2626"
MISSING_SOFT = "#fee2e2"
WARN = "#d97706"
WARN_SOFT = "#fef3c7"
NEUTRAL = "#6b7280"
NEUTRAL_SOFT = "#f3f4f6"
BG = "#f4f5f7"
CARD = "#ffffff"
BORDER = "#e5e7eb"
TEXT = "#1f2937"
MUTED = "#6b7280"
SIDEBAR = "#1e293b"
SIDEBAR_TEXT = "#cbd5e1"

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-size: 10pt;
}}
QMainWindow, QDialog, QStackedWidget > QWidget, QScrollArea, QScrollArea > QWidget > QWidget {{
    background: {BG};
}}
QMenuBar {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; border-radius: 4px; }}

/* boční menu */
QWidget#sidebar {{ background: {SIDEBAR}; }}
QWidget#brand_panel {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
QLabel#brand {{ color: {SIDEBAR}; font-size: 15pt; font-weight: 600; background: transparent; }}
QLabel#brand_sub {{ color: {MUTED}; font-size: 8.5pt; background: transparent; }}
QListWidget#nav {{
    background: transparent; border: none; outline: none; padding: 0 8px;
}}
QListWidget#nav::item {{
    color: {SIDEBAR_TEXT}; padding: 9px 12px; margin: 2px 0; border-radius: 6px;
}}
QListWidget#nav::item:hover {{ background: rgba(255, 255, 255, 0.07); }}
QListWidget#nav::item:selected {{ background: {ACCENT}; color: white; }}

/* stránky */
QLabel#page_title {{ font-size: 17pt; font-weight: 600; }}
QLabel#page_subtitle {{ color: {MUTED}; }}
QLabel#section {{ font-size: 10.5pt; font-weight: 600; color: {TEXT}; margin-top: 6px; }}
QLabel#step_no, QFrame#card QLabel#step_no {{
    background: {ACCENT}; color: white; border-radius: 12px; min-width: 24px; max-width: 24px;
    min-height: 24px; max-height: 24px; font-weight: 700; qproperty-alignment: AlignCenter;
}}
QLabel#step_title {{ font-size: 12pt; font-weight: 600; color: {TEXT}; }}
QLabel#step_hint {{ color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#headline {{ font-size: 13pt; font-weight: 600; }}
QLabel#card_title {{ font-weight: 600; }}

/* karty */
QFrame#card {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px;
}}
QFrame#card QLabel {{ background: transparent; border: none; }}
QFrame#card[role="ok"] {{ border-left: 4px solid {OK}; }}
QFrame#card[role="missing"] {{ border-left: 4px solid {MISSING}; }}
QFrame#card[role="warn"] {{ border-left: 4px solid {WARN}; }}
QFrame#card[role="neutral"] {{ border-left: 4px solid {NEUTRAL}; }}
QFrame#card[role="selected"] {{ border: 1px solid {ACCENT}; border-left: 4px solid {ACCENT}; }}

/* štítky stavu */
QLabel#pill {{ padding: 2px 9px; border-radius: 9px; font-size: 8.5pt; font-weight: 600; }}
QLabel#pill[role="ok"] {{ background: {OK_SOFT}; color: {OK}; }}
QLabel#pill[role="missing"] {{ background: {MISSING_SOFT}; color: {MISSING}; }}
QLabel#pill[role="warn"] {{ background: {WARN_SOFT}; color: {WARN}; }}
QLabel#pill[role="neutral"] {{ background: {NEUTRAL_SOFT}; color: {NEUTRAL}; }}
QLabel#pill[role="accent"] {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}

/* tlačítka */
QPushButton {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:disabled {{ color: #9ca3af; background: {NEUTRAL_SOFT}; }}
QPushButton[role="primary"] {{
    background: {ACCENT}; color: white; border: none; font-weight: 600; padding: 7px 18px;
}}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; color: white; }}
QPushButton[role="primary"]:disabled {{ background: #93c5fd; color: white; }}

/* tabulky a stromy */
QTableView, QTreeView, QTableWidget, QTreeWidget, QPlainTextEdit, QListWidget {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
    alternate-background-color: #fafafa;
}}
QTableView::item, QTreeView::item {{ padding: 4px 6px; }}
QHeaderView::section {{
    background: #f9fafb; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 8px; font-weight: 600;
}}
QTableCornerButton::section {{ background: #f9fafb; border: none; }}

/* vstupy */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {ACCENT}; selection-color: white;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QProgressBar {{
    background: {BORDER}; border: none; border-radius: 5px; height: 10px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #cbd5e1; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background: {BORDER}; }}
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 8px; margin-top: 12px; padding-top: 6px;
    background: {CARD};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}
"""


def apply(app: QApplication) -> None:
    """Nastaví styl, paletu, písmo a stylesheet celé aplikace."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(CARD))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#fafafa"))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(CARD))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(MUTED))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(CARD))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    app.setPalette(palette)
    font = QFont("Segoe UI" if sys.platform == "win32" else app.font().family(), 10)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)


def set_role(widget: QWidget, role: str) -> None:
    """Přepne vlastnost `role` a donutí stylesheet ji znovu vyhodnotit."""
    widget.setProperty("role", role)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
