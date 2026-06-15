"""Modern technical dark theme (Qt Style Sheet) + small palette helpers.

A single source for colours so dialogs and the main window stay consistent.
Look: deep slate background, cyan accent, monospace for technical data.
"""
from __future__ import annotations

# --- palette ---------------------------------------------------------------
BG          = "#0d1117"   # window background
PANEL       = "#161b22"   # cards / panels
PANEL_HI    = "#1c2230"   # hovered / header rows
BORDER      = "#2b3340"
TEXT        = "#e6edf3"
TEXT_DIM    = "#8b97a6"
ACCENT      = "#2dd4bf"   # teal/cyan
ACCENT_DIM  = "#13343b"
OK          = "#3fb950"
WARN        = "#d29922"
ERR         = "#f85149"
FREE_ROW    = "#7d8590"
# row/item selection: a neutral slate-blue, clearly distinct from PANEL yet dark
# enough that the per-cell colours (teal type, grey UID, light text) all stay
# readable on top of it — the old teal-tinted ACCENT_DIM made teal-on-teal mush.
SELECTION   = "#33425f"

MONO = "Cascadia Mono, Consolas, 'Courier New', monospace"
SANS = "Segoe UI, system-ui, sans-serif"


def usage_color(used: int, total: int) -> str:
    """Green -> amber -> red as an interface fills up."""
    if total <= 0:
        return TEXT_DIM
    frac = used / total
    if frac >= 0.95:
        return ERR
    if frac >= 0.8:
        return WARN
    return OK


QSS = f"""
* {{
    font-family: {SANS};
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {BG}; }}

QWidget#Card {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#H1 {{ font-size: 18px; font-weight: 600; }}
QLabel#H2 {{ font-size: 13px; font-weight: 600; color: {TEXT_DIM};
            letter-spacing: 1px; }}
QLabel#Dim  {{ color: {TEXT_DIM}; }}
QLabel#Mono {{ font-family: {MONO}; }}
QLabel#Badge {{
    background: {ACCENT_DIM}; color: {ACCENT};
    border: 1px solid {ACCENT}; border-radius: 9px;
    padding: 2px 10px; font-weight: 600;
}}

/* ---- toolbar ---- */
QToolBar {{ background: {PANEL}; border: 0; border-bottom: 1px solid {BORDER};
           padding: 6px; spacing: 6px; }}
QToolButton {{
    background: {PANEL_HI}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 7px 12px; font-weight: 600;
}}
QToolButton:hover  {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QToolButton:pressed {{ background: {ACCENT_DIM}; }}
QToolButton:disabled {{ color: {TEXT_DIM}; background: {PANEL};
                        border-color: {BORDER}; }}

/* ---- buttons ---- */
QPushButton {{
    background: {PANEL_HI}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#Primary {{ background: {ACCENT}; color: #04201d; border: 0; }}
QPushButton#Primary:hover {{ background: #46e6d2; }}
QPushButton#Danger:hover {{ border-color: {ERR}; color: {ERR}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; background: {PANEL}; }}

/* ---- tables ---- */
QTableWidget {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px;
    gridline-color: {BORDER}; font-family: {MONO};
    selection-background-color: {SELECTION}; selection-color: {TEXT};
}}
QTableWidget::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
QHeaderView::section {{
    background: {PANEL_HI}; color: {TEXT_DIM}; border: 0;
    border-bottom: 1px solid {BORDER}; padding: 6px 8px; font-weight: 600;
}}
/* round the outer top corners so the header doesn't square off the table's rounded
   border (most visible with the active-table accent border). */
QHeaderView::section:first {{ border-top-left-radius: 7px; }}
QHeaderView::section:last {{ border-top-right-radius: 7px; }}
QTableWidget::item {{ padding: 3px 6px; }}
/* in-cell rename editor: raised field (not a dark hole), teal frame, no
   vertical padding so the text is never clipped inside the row */
QTableWidget QLineEdit {{
    background: {PANEL_HI}; color: {TEXT};
    border: 1px solid {ACCENT}; border-radius: 4px;
    padding: 0 6px; margin: 0; font-family: {MONO};
    selection-background-color: {ACCENT}; selection-color: #04201d;
}}

/* ---- inputs ---- */
QLineEdit, QSpinBox, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {ACCENT}; selection-color: #04201d;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: 0; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER}; color: {TEXT};
    selection-background-color: {SELECTION}; selection-color: {TEXT};
}}
QRadioButton, QCheckBox {{ spacing: 8px; padding: 3px 0; }}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {TEXT_DIM};
    background: {BG}; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator:hover, QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{
    border: 5px solid {ACCENT}; background: #04201d; }}
QCheckBox::indicator:checked {{ border-color: {ACCENT}; background: {ACCENT}; }}
/* selectable options read as a segmented control */
QRadioButton {{ border: 1px solid {BORDER}; border-radius: 7px;
               padding: 5px 12px 5px 8px; background: {PANEL_HI}; }}
QRadioButton:checked {{ border-color: {ACCENT}; }}

/* ---- progress (capacity bars) ---- */
QProgressBar {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 6px;
    text-align: center; height: 18px; font-family: {MONO}; font-size: 11px;
}}
QProgressBar::chunk {{ border-radius: 5px; }}

/* ---- misc ---- */
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER};
             color: {TEXT_DIM}; font-family: {MONO}; }}
QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 6px;
                              min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {ACCENT}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
QListWidget {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px;
              outline: 0; }}
QListWidget::item {{ padding: 5px 8px; }}
QListWidget::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
QListWidget::item:hover {{ background: {PANEL_HI}; }}
/* invisible handle = pure spacing between the In/Out tables */
QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:hover {{ background: {ACCENT_DIM}; }}

/* ---- docked inspector panel ---- */
QFrame#Inspector {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}
QWidget#InspectorHead {{ background: {PANEL_HI};
                        border-bottom: 1px solid {BORDER}; }}

/* ---- in-window overlay card (About / Safety) ---- */
QFrame#OverlayCard {{ background: {PANEL}; border: 1px solid {BORDER};
                     border-radius: 14px; }}
"""
