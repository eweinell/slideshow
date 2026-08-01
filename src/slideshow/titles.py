"""Titel- und Zwischenfolien (``docs/briefing-titelfolien.md``).

Eine Titelfolie ist ein **gebackenes Bild** (Entscheidung 1a): der Generator
erzeugt aus Ueberschrift, zweiter Zeile und Hintergrund eine Datei in
``cache/``, und von da an ist sie ein Standbild wie jedes andere. ``render.py``,
``planner.py`` und ``mlt.py`` wissen von Titeln nichts.

Dieses Modul haelt deshalb zwei sehr verschiedene Dinge auseinander:

*Die Nahtstelle* — Schriftfindung, Layoutparameter, Assetpfad und
Frische-Schluessel. Sie ist rein rechnend, macht kein Datei-I/O und bestimmt,
**wie** eine Folie heisst. ``build`` braucht nur das.

*Der Generator* — :func:`render_title`, der die Pixel erzeugt. Er ist die
zweite Stufe des Briefings und noch nicht umgesetzt.

.. rubric:: Warum der Dateiname nicht vom Bildinhalt abhaengt

Das Briefing verlangt, dass Schriftdatei und Hintergrundinhalt in den Hash
eingehen — sonst sieht dieselbe Folie unter WSL anders aus als unter Windows,
und der Cache merkt es nicht. Beide gehen hier aber in den *Frische-Schluessel*
(``.key``-Datei neben dem Asset, Muster aus ``preprocess.py``), nicht in den
Dateinamen:

* Der Name ergibt sich allein aus der Absicht — Text, Hintergrundangabe,
  Layout, ``TITLE_VERSION``. Damit laesst er sich ohne Datei-I/O berechnen, und
  ``plan_from_edit`` bleibt eine reine Funktion ueber ``edit.yaml``.
* Aendert sich Schrift oder Hintergrundbild, schlaegt ``_is_fresh`` fehl, die
  Datei wird neu erzeugt, ihr Inhaltshash aendert sich — und weil der
  Segment-Cache ueber den Inhalt geht, rendert genau dieses Segment neu. Die
  Zusage aus dem Briefing bleibt also erhalten, nur ohne wandernde Dateinamen
  in ``edit.yaml``.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

from .cache import cache_key, param_hash
from .errors import SlideshowError
from .models import Defaults, TitleSegment

#: Version des Layouts. Muss bei **jeder** Aenderung an Satz, Groessen oder
#: Kontrastregel hoch — dieselbe Disziplin wie bei ``PREPROC_VERSION``.
TITLE_VERSION = 1

#: Lesezeit einer Folie: Grundzeit plus Zuschlag je Wort. Untergrenze fuer die
#: Standzeit; darunter wird gewarnt (Entscheidung 3).
READ_BASE_SECONDS = 1.8
READ_PER_WORD_SECONDS = 0.25


# --------------------------------------------------------------------------
# Schriftfindung
# --------------------------------------------------------------------------

#: Kandidaten je Plattform, in der Reihenfolge der Bevorzugung. Gesucht wird
#: wie bei ``melt``: erst die Umgebungsvariable, dann bekannte Orte.
_FONT_CANDIDATES = {
    "win32": [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
    ],
    "darwin": [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ],
    "linux": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ],
}

_FONT_HINT = {
    "win32": "Segoe UI und Arial gehoeren zu Windows — fehlen beide, "
             "SLIDESHOW_FONT auf eine .ttf setzen.",
    "darwin": "SLIDESHOW_FONT auf eine .ttf/.ttc setzen.",
    "linux": "sudo apt install fonts-dejavu-core",
}


def find_font(preferred: str = "auto") -> Path:
    """Die Schriftdatei fuer die Titelfolien.

    Praezedenz: ``SLIDESHOW_FONT`` gewinnt immer — dieselbe Regel wie bei
    ``SLIDESHOW_MELT``, damit man eine Vorgabe ohne Projektaenderung
    ueberstimmen kann. Danach ``defaults.title.font``, danach die
    Kandidatenliste der Plattform.

    Kein Fund ist ein :class:`SlideshowError` mit Installationsbefehl, kein
    Traceback (Abnahmekriterium T8).
    """
    env = os.environ.get("SLIDESHOW_FONT", "").strip()
    for wunsch, quelle in ((env, "SLIDESHOW_FONT"),
                           (preferred if preferred and preferred != "auto" else "",
                            "defaults.title.font")):
        if not wunsch:
            continue
        p = Path(wunsch).expanduser()
        if p.is_file():
            return p
        raise SlideshowError(f"Schriftdatei aus {quelle} nicht gefunden: {p}")

    platform = "win32" if sys.platform.startswith("win") else \
        "darwin" if sys.platform == "darwin" else "linux"
    for kandidat in _FONT_CANDIDATES[platform]:
        p = Path(kandidat)
        if p.is_file():
            return p
    raise SlideshowError(
        "Keine Schriftdatei fuer die Titelfolien gefunden. "
        + _FONT_HINT[platform]
        + "  Geprueft: " + ", ".join(_FONT_CANDIDATES[platform]))


def font_available(preferred: str = "auto") -> Path | None:
    """Wie :func:`find_font`, aber ohne Abbruch — fuer ``doctor``."""
    try:
        return find_font(preferred)
    except SlideshowError:
        return None


# --------------------------------------------------------------------------
# Hintergrund und Absicht aufloesen
# --------------------------------------------------------------------------

def resolve_bg(segments: list, index: int) -> str:
    """``bg: auto`` auf das **erste Bild des neuen Abschnitts** aufloesen.

    ``build`` schreibt den aufgeloesten Wert in die Datei, damit er sichtbar
    und korrigierbar bleibt (Entscheidung 4). Steht in einer von Hand
    geschriebenen Edit-List trotzdem noch ``auto``, greift dieselbe Regel —
    sonst haetten die beiden Wege verschiedene Ergebnisse.

    Gesucht wird das naechste **Standbild**; ein Clip taugt nicht als
    Standhintergrund. Findet sich keines, faellt es auf ``none`` (Text auf
    Schwarz) zurueck: ein Titel am Ende des Films hat kein "danach".
    """
    seg = segments[index]
    if getattr(seg, "bg", "auto") != "auto":
        return seg.bg
    for folge in segments[index + 1:]:
        src = getattr(folge, "src", None)
        if src and getattr(folge, "type", "") == "still":
            return src
    return "none"


def resolved(segments: list, index: int) -> TitleSegment:
    """Die Titelfolie mit aufgeloestem Hintergrund — die Form, die zaehlt."""
    seg = segments[index]
    bg = resolve_bg(segments, index)
    return seg if bg == seg.bg else seg.model_copy(update={"bg": bg})


def bg_kind(bg: str) -> str:
    """``image`` | ``color`` | ``none`` — woraus der Hintergrund entsteht."""
    if not bg or bg == "none":
        return "none"
    if _HEX_RE.match(bg):
        return "color"
    return "image"


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# --------------------------------------------------------------------------
# Layout, Assetpfad, Frische
# --------------------------------------------------------------------------

def layout_params(defaults: Defaults) -> dict:
    """Die Layoutwerte, die das Aussehen bestimmen — und nur die.

    Bewusst ausgeschrieben statt ``model_dump()``: kommt ein Feld hinzu, das
    das Bild *nicht* veraendert (etwa die Blendenchoreografie ``xfade_in``),
    soll es nicht jedes Asset invalidieren.
    """
    t = defaults.title
    return {"size": t.size, "subtitle_scale": t.subtitle_scale, "blur": t.blur,
            "darken": t.darken, "min_contrast": t.min_contrast, "safe": t.safe}


def title_params(seg: TitleSegment, defaults: Defaults,
                 size: tuple[int, int]) -> dict:
    """Alles, was den Dateinamen des Assets bestimmt."""
    return {"op": "title", "v": TITLE_VERSION,
            "title": seg.title, "subtitle": seg.subtitle or "",
            "bg": seg.bg, "style": seg.style,
            "canvas": list(size), **layout_params(defaults)}


def title_asset(seg: TitleSegment, defaults: Defaults,
                size: tuple[int, int]) -> str:
    """Projektrelativer Pfad des gebackenen Assets.

    ``seg`` muss einen aufgeloesten Hintergrund tragen (:func:`resolved`),
    sonst zeigen zwei gleich aussehende Folien auf verschiedene Dateien.

    Der Slug im Namen ist reine Lesbarkeit — beim Durchblaettern von ``cache/``
    will man sehen, welche Folie das ist. Eindeutig ist allein der Hash.
    """
    key = param_hash(title_params(seg, defaults, size))
    return f"cache/title_{_slug(seg.title)}_{key[:16]}.jpg"


def freshness_key(seg: TitleSegment, defaults: Defaults, size: tuple[int, int],
                  *, bg_hash: str = "", font_hash: str = "") -> str:
    """Schluessel fuer die ``.key``-Datei neben dem Asset.

    Enthaelt zusaetzlich zum Dateinamen die Inhaltshashes von Hintergrundbild
    und Schriftdatei. Genau dadurch erkennt ein zweiter Rechner mit anderer
    Schrift, dass die vorhandene Datei nicht seine ist.
    """
    return cache_key([bg_hash, font_hash], title_params(seg, defaults, size))


def _slug(text: str, limit: int = 24) -> str:
    """Dateinamenstauglicher Rest eines Titels: ASCII, klein, ohne Sonderzeichen."""
    zerlegt = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in zerlegt if not unicodedata.combining(c))
    ascii_text = (ascii_text.replace("ß", "ss").replace("Ø", "O")
                  .encode("ascii", "ignore").decode("ascii"))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:limit].strip("-") or "titel"


# --------------------------------------------------------------------------
# Lesezeit
# --------------------------------------------------------------------------

def reading_seconds(seg: TitleSegment) -> float:
    """Untergrenze der Standzeit: ``1,8 s + 0,25 s je Wort`` (Entscheidung 3).

    Keine Naturkonstante, sondern eine Faustregel — sie begruendet eine
    *Warnung*, nie eine stille Korrektur nach oben in einer Beat-Region.
    """
    woerter = len((seg.title + " " + (seg.subtitle or "")).split())
    return READ_BASE_SECONDS + READ_PER_WORD_SECONDS * woerter


# --------------------------------------------------------------------------
# Generator (Stufe 1 des Briefings)
# --------------------------------------------------------------------------

def render_title(seg: TitleSegment, defaults: Defaults, *, bg_source: Path | None,
                 out: Path, size: tuple[int, int], font: Path) -> dict:
    """Erzeugt das Titelasset.

    Zweischichtig gedacht (Entscheidung 1): eine Hintergrundebene und eine
    Textebene mit Alpha, in Stufe 1 beim Schreiben zusammengeflacht. Zeigt die
    Sichtpruefung, dass mitzoomender Text stoert, entfaellt in Stufe 2 nur das
    Zusammenflachen.

    ``bg_source`` ist das **Original** aus dem Manifest, nicht das
    Zwischenprodukt aus ``cache/``: bei einem Hochformat ist Letzteres bereits
    ein Blur-Komposit, und ein zweiter Blur darueber ergaebe einen
    verwaschenen Rahmen um ein leicht verwaschenes Hochformat. In ``edit.yaml``
    steht trotzdem der ``cache/``-Pfad — das ist die Kennung, unter der ein
    Bild im ganzen Projekt auftritt; die Rueckabbildung leistet
    ``Manifest.by_cache_path``.

    Gibt die Kennzahlen der Kontrastmessung zurueck (Abnahmekriterium T6).
    """
    raise SlideshowError(
        "Der Titelgenerator ist noch nicht umgesetzt (Stufe 1 aus "
        "docs/briefing-titelfolien.md). `slideshow build` erzeugt die Edit-List "
        "mit Titelfolien bereits vollstaendig; zum Rendern fehlt das Asset.")
