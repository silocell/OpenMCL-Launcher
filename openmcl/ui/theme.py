"""Theme palettes and the stylesheet built from them."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .. import config


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_dim: str
    danger: str
    is_dark: bool


PALETTES: dict[str, Palette] = {
    "dark": Palette(
        bg="#16181c", surface="#1e2127", surface_alt="#262a32", border="#343943",
        text="#e4e7ec", text_dim="#9aa3b2", danger="#e0564f", is_dark=True,
    ),
    "midnight": Palette(
        bg="#0c0f17", surface="#141926", surface_alt="#1c2333", border="#273047",
        text="#dfe6f5", text_dim="#8c97b3", danger="#e2605a", is_dark=True,
    ),
    "light": Palette(
        bg="#f4f5f7", surface="#ffffff", surface_alt="#eceef2", border="#d6dae1",
        text="#1f2329", text_dim="#5e6673", danger="#c9382f", is_dark=False,
    ),
}


def _clamp(n: int) -> int:
    return max(0, min(255, n))


def shade(hex_colour: str, amount: float) -> str:
    """Lighten (amount > 0) or darken (amount < 0) a #rrggbb colour."""
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_colour
    if amount >= 0:
        r, g, b = (int(c + (255 - c) * amount) for c in (r, g, b))
    else:
        r, g, b = (int(c * (1 + amount)) for c in (r, g, b))
    return f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}"


def readable_on(hex_colour: str) -> str:
    """Black or white, whichever is legible on the given background."""
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "#ffffff"
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#111318" if luminance > 0.6 else "#ffffff"


_ARROW_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="7" '
    'viewBox="0 0 10 7"><path d="{path}" fill="{colour}"/></svg>'
)
_ARROW_PATHS = {
    "down": "M1 1.6 L5 5.6 L9 1.6 L8 0.6 L5 3.6 L2 0.6 Z",
    "up": "M1 5.4 L5 1.4 L9 5.4 L8 6.4 L5 3.4 L2 6.4 Z",
}


def arrow_icon(direction: str, colour: str) -> Path:
    """Write (and cache) a small SVG arrow. Qt stylesheets need a real file."""
    cache = config.CACHE_DIR / "ui"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{direction}{colour}".encode()).hexdigest()[:12]
    path = cache / f"arrow-{direction}-{key}.svg"
    if not path.exists():
        path.write_text(
            _ARROW_SVG.format(path=_ARROW_PATHS[direction], colour=colour), "utf-8"
        )
    return path


def stylesheet(theme: str, accent: str, font_size: int = 13) -> str:
    p = PALETTES.get(theme, PALETTES["dark"])
    accent_hover = shade(accent, 0.12 if p.is_dark else -0.08)
    accent_press = shade(accent, -0.15)
    accent_soft = shade(accent, 0.82 if not p.is_dark else -0.62)
    on_accent = readable_on(accent)

    down = arrow_icon("down", p.text_dim).as_posix()
    down_hot = arrow_icon("down", accent).as_posix()
    down_off = arrow_icon("down", p.border).as_posix()
    up = arrow_icon("up", p.text_dim).as_posix()
    up_hot = arrow_icon("up", accent).as_posix()
    up_off = arrow_icon("up", p.border).as_posix()

    return f"""
* {{
    font-size: {font_size}px;
    outline: none;
}}
QWidget {{
    background: {p.bg};
    color: {p.text};
    font-family: "Inter", "Segoe UI", "SF Pro Text", "Ubuntu", system-ui, sans-serif;
}}
QLabel {{
    background: transparent;
}}
QToolTip {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border};
    padding: 5px 8px;
    border-radius: 5px;
}}

/* ----------------------------------------------------------------- sidebar */
#Sidebar {{
    background: {p.surface};
    border-right: 1px solid {p.border};
}}
#SidebarButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 11px 14px;
    text-align: left;
    color: {p.text_dim};
    font-weight: 500;
}}
#SidebarButton:hover {{
    background: {p.surface_alt};
    color: {p.text};
}}
#SidebarButton:checked {{
    background: {accent_soft};
    color: {accent if not p.is_dark else p.text};
    font-weight: 600;
}}
#BrandLabel {{
    font-size: {font_size + 6}px;
    font-weight: 700;
    color: {p.text};
    padding: 4px 6px 2px 6px;
}}
#BrandSub {{
    color: {p.text_dim};
    font-size: {font_size - 2}px;
    padding: 0 6px 10px 6px;
}}

/* ------------------------------------------------------------------ cards */
#Card, QGroupBox {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 12px;
}}
#Card:hover {{
    border-color: {accent};
}}
#CardTitle {{
    font-size: {font_size + 2}px;
    font-weight: 650;
    background: transparent;
}}
#CardSub, #Muted {{
    color: {p.text_dim};
    background: transparent;
}}
#PageTitle {{
    font-size: {font_size + 9}px;
    font-weight: 700;
    background: transparent;
}}
QGroupBox {{
    margin-top: 16px;
    padding: 18px 14px 14px 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: {p.text_dim};
}}

/* ---------------------------------------------------------------- buttons */
QPushButton {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 550;
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:pressed {{ background: {shade(p.surface_alt, -0.12 if p.is_dark else -0.05)}; }}
QPushButton:disabled {{ color: {p.text_dim}; border-color: {p.border}; }}

QPushButton#Primary {{
    background: {accent};
    color: {on_accent};
    border: 1px solid {accent};
}}
QPushButton#Primary:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton#Primary:pressed {{ background: {accent_press}; }}
QPushButton#Primary:disabled {{ background: {p.surface_alt}; color: {p.text_dim};
                                border-color: {p.border}; }}
QPushButton#Danger {{ color: {p.danger}; border-color: {p.danger}; background: transparent; }}
QPushButton#Danger:hover {{ background: {p.danger}; color: #ffffff; }}
QPushButton#Ghost {{ background: transparent; border-color: transparent; color: {p.text_dim}; }}
QPushButton#Ghost:hover {{ background: {p.surface_alt}; color: {p.text}; }}

/* ------------------------------------------------------------------ input */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {accent};
    selection-color: {on_accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {accent};
}}
QComboBox::drop-down {{ border: none; width: 26px; background: transparent; }}
QComboBox::down-arrow {{ image: url({down}); width: 10px; height: 7px; margin-right: 9px; }}
QComboBox::down-arrow:hover {{ image: url({down_hot}); }}
QComboBox::down-arrow:disabled {{ image: url({down_off}); }}

QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    background: transparent;
    border: none;
    width: 22px;
    height: 13px;
}}
QSpinBox::up-button {{ subcontrol-position: top right; margin: 3px 4px 0 0; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 4px 3px 0; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {p.surface_alt}; border-radius: 4px;
}}
QSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 7px; }}
QSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 7px; }}
QSpinBox::up-arrow:hover {{ image: url({up_hot}); }}
QSpinBox::down-arrow:hover {{ image: url({down_hot}); }}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{ image: url({up_off}); }}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{ image: url({down_off}); }}

QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    selection-background-color: {accent};
    selection-color: {on_accent};
    padding: 4px;
}}
QListWidget::item, QTreeWidget::item {{
    padding: 7px 6px;
    border-radius: 6px;
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {accent_soft};
    color: {p.text};
}}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {p.border};
    background: {p.surface};
}}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {accent}; border-color: {accent};
}}

/* ----------------------------------------------------------------- others */
QProgressBar {{
    background: {p.surface_alt};
    border: none;
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}

QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{
    background: transparent;
    color: {p.text_dim};
    padding: 8px 18px;
    border-bottom: 2px solid transparent;
    font-weight: 550;
}}
QTabBar::tab:selected {{ color: {p.text}; border-bottom-color: {accent}; }}
QTabBar::tab:hover {{ color: {p.text}; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {p.border}; border-radius: 5px; min-height: 36px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_dim}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 5px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QScrollArea {{ border: none; background: transparent; }}
QSplitter::handle {{ background: {p.border}; }}
#StatusBar {{ background: {p.surface}; border-top: 1px solid {p.border}; }}
#MoreButton {{
    background: transparent;
    border: none;
    color: {p.text_dim};
    font-size: {font_size + 5}px;
    font-weight: 700;
    padding: 0;
}}
#MoreButton:hover {{ background: {p.surface_alt}; color: {p.text}; border-radius: 7px; }}
#Pill {{
    background: {accent_soft};
    color: {p.text};
    border-radius: 9px;
    padding: 2px 9px;
    font-size: {font_size - 2}px;
    font-weight: 600;
}}
#Console {{
    font-family: "JetBrains Mono", "Cascadia Mono", "SF Mono", Consolas, monospace;
    font-size: {font_size - 1}px;
    background: {shade(p.bg, -0.25 if p.is_dark else -0.03)};
}}
"""
