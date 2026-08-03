"""Titel- und Zwischenfolien (``docs/briefing-titelfolien.md``).

Eine Titelfolie ist ein **gebackenes Bild** (Entscheidung 1a): der Generator
erzeugt aus Ueberschrift, zweiter Zeile und Hintergrund eine Datei in
``cache/``, und von da an ist sie ein Standbild wie jedes andere. ``render.py``,
``planner.py`` und ``mlt.py`` wissen von Titeln nichts.

Dieses Modul haelt deshalb zwei sehr verschiedene Dinge auseinander:

*Die Nahtstelle* — Schriftfindung, Layoutparameter, Assetpfad und
Frische-Schluessel. Sie ist rein rechnend, macht kein Datei-I/O und bestimmt,
**wie** eine Folie heisst. ``build`` braucht nur das.

*Der Generator* — :func:`render_title`, der die Pixel erzeugt. Er arbeitet
zweischichtig (:func:`_background`, :func:`_text_layer`) und flacht die beiden
Ebenen erst beim Schreiben zusammen.

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
from .models import Defaults, KBSpec, TitleDefaults, TitleSegment

#: Version des Layouts. Muss bei **jeder** Aenderung an Satz, Groessen oder
#: Kontrastregel hoch — dieselbe Disziplin wie bei ``PREPROC_VERSION``.
#:
#: Im Hash stehen die *Parameter* (``darken``, ``min_contrast``, ``size``), nicht
#: der *Rechenweg*. Aendert man nur letzteren, bliebe der Frische-Schluessel
#: gleich, und ein Asset mit dem alten Aussehen ueberlebte unbemerkt. Diese Zahl
#: ist der einzige Griff, mit dem sich "der Code dahinter ist ein anderer"
#: ausdruecken laesst.
#:
#: 2 — Kontrast wird am hellen Ende der Textflaeche gemessen statt im Mittel
#:     (``MEASURE_PERCENTILE``).
#: 1 — erste Fassung.
TITLE_VERSION = 2

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
# Bewegung
# --------------------------------------------------------------------------

def motion_mode(seg: TitleSegment, defaults: Defaults) -> str:
    """``kenburns`` | ``none`` — was fuer *diese* Folie gilt."""
    return seg.motion or defaults.title.motion


def title_kb(seg: TitleSegment, defaults: Defaults) -> KBSpec | None:
    """Die Ken-Burns-Vorgabe einer Folie — oder der Stillstand.

    ``motion: none`` wird hier in **gewoehnliche Absicht** uebersetzt: in genau
    das ``kb:``, das ``docs/edit-yaml.md`` unter "Bewegung fuer ein Bild
    abschalten" nennt. Damit muss keine Zeile in ``planner.py`` oder
    ``render.py`` von Titeln wissen, und in ``edit.yaml`` steht sichtbar, warum
    diese eine Folie stillsteht — derselbe Weg wie bei Phrasenlage und
    Fokusblende.

    Ein von Hand gesetztes ``kb:`` gewinnt. Wer beides schreibt, meint das
    ``kb:``; ``motion`` ist die bequeme Schreibweise, nicht die staerkere.
    """
    if seg.kb is not None:
        return seg.kb
    if motion_mode(seg, defaults) != "none":
        return None
    # Frische Instanz je Aufruf: der Wert haengt sich an einen Intent und landet
    # von dort in der Edit-List. Eine geteilte waere dieselbe fuer alle Folien.
    return KBSpec(z=(1.0, 1.0), c=(0.5, 0.5, 0.5, 0.5))


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

#: Textfarbe. Nur fuer Weiss auf abgedunkeltem Grund ist die Kontrastregel aus
#: Abschnitt 2 gerechnet; eine zweite Farbe waere eine zweite Messung.
TEXT_RGB = (255, 255, 255)

#: Die Masse aus der Tabelle in Abschnitt 2, jeweils als Anteil. Sie stehen
#: hier und nicht in ``TitleDefaults``, weil sie die Gestalt *festlegen*: wer
#: sie aendert, aendert das Aussehen aller Folien und erhoeht ``TITLE_VERSION``.
#: Einstellbar ist, was der Anwender je Projekt anders wollen kann — Groesse,
#: Blur, Abdunklung, Safe Area —, und das steht bereits dort.
LINE_GAP = 0.55          # Zeilenabstand, Anteil der Versalhoehe
RULE_LENGTH = 0.12       # Trennlinie, Anteil der Bildhoehe
RULE_WEIGHT = 0.045      # Staerke der Trennlinie, Anteil der Versalhoehe
TRACK_TITLE = 0.04       # Sperrung der Ueberschrift, em
TRACK_SUBTITLE = 0.10    # Sperrung der zweiten Zeile, em
OPTICAL_AXIS = 0.52      # optische Satzachse; geometrisch mittig wirkt zu tief

#: Schriftgroesse, bei der die Versalhoehe einmal ausgemessen wird. Gross
#: genug, dass die Rundung der Hinting-Stufen nicht durchschlaegt.
_PROBE_SIZE = 200

#: Ueberlauf: bis hierher wird stillschweigend verkleinert (T7), danach folgt
#: eine Warnung. ``HARD_MIN_SCALE`` ist die Notbremse davor, dass Text am
#: Bildrand abgeschnitten wird — das Ergebnis, das es nie geben darf.
MIN_SCALE = 0.70
SCALE_STEP = 0.02
HARD_MIN_SCALE = 0.20

#: Abdunklung: feste Schritte, feste Untergrenze (Anhang A). Beides zusammen
#: macht die Messung deterministisch und damit cachefaehig.
DARKEN_STEP = 0.05
DARKEN_FLOOR = 0.25

#: Rastergroesse der Kontrastmessung. Der Hintergrund ist unter dem Text immer
#: weichgezeichnet, hat dort also keine feinen Strukturen — 1024 Felder genuegen
#: fuer ein belastbares Perzentil und kosten nichts.
MEASURE_GRID = 32

#: Welches Perzentil der Leuchtdichte den Kontrast tragen muss.
#:
#: **Nicht der Mittelwert**, obwohl Anhang A des Briefings ihn vorschlug. Ein
#: Mittelwert sagt nichts darueber, ob der Text an seiner *hellsten* Stelle noch
#: lesbar ist: gemessen an der Beispielfolie lag er bei 4,5:1, waehrend die
#: hellsten 5 % der Flaeche unter dem Text nur 3,8:1 trugen — dort steht die
#: Ueberschrift ueber einer aufgehellten Stelle und wird grenzwertig. Das
#: Kriterium meint die Lesbarkeit, nicht den Durchschnitt.
#:
#: Das Maximum waere die naechste Stufe, reagiert aber auf ein einzelnes Feld
#: und zoege eine ganze Folie wegen eines Lichtpunkts ins Dunkle.
MEASURE_PERCENTILE = 0.95

#: Der Blur laeuft auf 1/8 der Kantenlaenge — derselbe Trick wie im
#: Hochformat-Komposit, dort mit ~50-fachem Gewinn gemessen.
BLUR_SHRINK = 8


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
    t = defaults.title
    warnungen: list[str] = []

    # Reihenfolge: erst der Satz, dann der Hintergrund. Die Abdunklung wird
    # unter der Textflaeche gemessen, und die kennt man erst, wenn der Text
    # steht — ein Sonnenuntergang bleibt auch unscharf hell, aber eben nicht
    # ueberall gleich.
    satz = _layout(seg, t, size, font)
    warnungen += satz["warnungen"]
    text = _text_layer(size, satz)
    box = _messbox(text, size)

    grund, bg_warnungen, art = _background(seg, t, bg_source, size)
    warnungen += bg_warnungen

    # Startwert nur fuer Fotos. Eine ausdruecklich gewaehlte Farbflaeche pauschal
    # auf 55 % zu daempfen hiesse, die Wahl des Anwenders zu ueberschreiben;
    # nachgefuehrt wird sie trotzdem, wenn sie den Text nicht traegt.
    start = t.darken if art == "image" else 1.0
    faktor, kontrast = _fit_darkening(grund, box, start=start,
                                      minimum=t.min_contrast)
    if kontrast < t.min_contrast:
        warnungen.append(
            f"Der Hintergrund traegt den hellen Text nicht: gemessener Kontrast "
            f"{kontrast:.1f}:1 bei maximaler Abdunklung ({DARKEN_FLOOR:g}), "
            f"gefordert sind {t.min_contrast:g}:1.")
    if faktor < 1.0:
        grund = grund.point(lambda v: int(v * faktor))

    # Das Zusammenflachen ist der einzige Schritt, der in Stufe 2 entfaellt,
    # wenn der Text als eigene Ebene stehen bleiben soll (Entscheidung 1c).
    grund.paste(text, (0, 0), text)
    out.parent.mkdir(parents=True, exist_ok=True)
    grund.save(out, format="JPEG", quality=95, subsampling=0, optimize=True,
               progressive=False)
    return {"warnungen": warnungen, "kontrast": round(kontrast, 2),
            "abdunklung": faktor, "schrift_faktor": satz["scale"],
            "box": list(box), "canvas": list(size)}


# --- Satz -----------------------------------------------------------------

def _layout(seg: TitleSegment, t: TitleDefaults, size: tuple[int, int],
            font: Path) -> dict:
    """Der Satzplan: Schriftgroessen, Grundlinien, Trennlinie, Ueberlauf.

    Rein rechnend und ohne Leinwand — dadurch kostet die Verkleinerungsschleife
    nur ein paar Metrikabfragen statt eines Neuzeichnens.
    """
    from PIL import ImageFont

    w, h = size
    warnungen: list[str] = []
    kopf = _versalien(seg.title)
    unter = (seg.subtitle or "").strip()

    # Versalhoehe je Pixel Schriftgroesse. Einmal gemessen statt je Durchlauf:
    # FreeType skaliert linear, die Rundung wird danach nachgemessen.
    probe = ImageFont.truetype(str(font), _PROBE_SIZE)
    verhaeltnis = _versalhoehe(probe) / _PROBE_SIZE or 0.7

    safe = (t.safe * w, t.safe * h, w - t.safe * w, h - t.safe * h)
    scale = 1.0
    plan = _satz(kopf, unter, t, size, font, verhaeltnis, scale)
    while not _passt(plan["box"], safe) and scale > MIN_SCALE:
        scale = round(scale - SCALE_STEP, 4)
        plan = _satz(kopf, unter, t, size, font, verhaeltnis, scale)

    if not _passt(plan["box"], safe):
        # Notbremse: abgeschnittener Text ist das eine Ergebnis, das nie
        # herauskommen darf. Die Safe Area ist dann verloren, das Bild nicht.
        leinwand = (0.0, 0.0, float(w), float(h))
        while not _passt(plan["box"], leinwand) and scale > HARD_MIN_SCALE:
            scale = round(scale - SCALE_STEP, 4)
            plan = _satz(kopf, unter, t, size, font, verhaeltnis, scale)
        warnungen.append(
            f"Die Ueberschrift passt auch bei {MIN_SCALE:.2f}x nicht in die Safe "
            f"Area ({t.safe:.0%} ringsum); gesetzt wird mit {plan['scale']:.2f}x. "
            f"Kuerzere Fassung waehlen oder defaults.title.size senken.")

    plan["warnungen"] = warnungen
    return plan


def _satz(kopf: str, unter: str, t: TitleDefaults, size: tuple[int, int], font: Path,
          verhaeltnis: float, scale: float) -> dict:
    """Ein vollstaendiger Satzplan fuer genau einen Verkleinerungsfaktor."""
    from PIL import ImageFont

    w, h = size
    kopf_font = ImageFont.truetype(str(font),
                                   max(1, round(t.size * h * scale / verhaeltnis)))
    cap = _versalhoehe(kopf_font)
    sperr_kopf = TRACK_TITLE * kopf_font.size

    unter_font = None
    cap_unter = 0.0
    sperr_unter = 0.0
    if unter:
        unter_font = ImageFont.truetype(
            str(font), max(1, round(cap * t.subtitle_scale / verhaeltnis)))
        cap_unter = _versalhoehe(unter_font)
        sperr_unter = TRACK_SUBTITLE * unter_font.size

    abstand = LINE_GAP * cap
    linie_h = max(1, round(RULE_WEIGHT * cap))
    linie_w = RULE_LENGTH * h * scale

    # Blockhoehe ueber die Versalhoehen, nicht ueber die Tintenhoehe: sonst
    # sitzt derselbe Titel je nach Umlaut oder Unterlaenge anders im Bild.
    linie_oben = cap + abstand
    linie_unten = linie_oben + linie_h
    unter_grund = linie_unten + abstand + cap_unter if unter else 0.0
    block = unter_grund if unter else linie_unten
    oben = OPTICAL_AXIS * h - block / 2
    mitte = w / 2

    zeilen = []
    kasten = []
    kopf_breite = _zeilenbreite(kopf_font, kopf, sperr_kopf)
    zeilen.append({"font": kopf_font, "text": kopf, "x": mitte - kopf_breite / 2,
                   "baseline": oben + cap, "spacing": sperr_kopf})
    kasten.append(_zeilenkasten(kopf_font, kopf, mitte - kopf_breite / 2,
                                oben + cap, kopf_breite))

    linie = (mitte - linie_w / 2, oben + linie_oben,
             mitte + linie_w / 2, oben + linie_unten)
    kasten.append(linie)

    if unter:
        unter_breite = _zeilenbreite(unter_font, unter, sperr_unter)
        zeilen.append({"font": unter_font, "text": unter,
                       "x": mitte - unter_breite / 2, "baseline": oben + unter_grund,
                       "spacing": sperr_unter})
        kasten.append(_zeilenkasten(unter_font, unter, mitte - unter_breite / 2,
                                    oben + unter_grund, unter_breite))

    return {"zeilen": zeilen, "linie": linie, "scale": scale,
            "box": (min(k[0] for k in kasten), min(k[1] for k in kasten),
                    max(k[2] for k in kasten), max(k[3] for k in kasten))}


def _versalien(text: str) -> str:
    """Die Ueberschrift steht in Versalien (Abschnitt 2, "gesperrte Versalien
    wirken ruhig"). Nur so ist die Versalhoehe auch das, was man sieht."""
    return text.strip().upper()


def _versalhoehe(font) -> float:
    """Hoehe eines ``H`` ueber der Grundlinie — das Mass aus der Masstabelle.

    Nicht die Schriftgroesse: die enthaelt Ober- und Unterlaengen und faellt je
    nach Schriftschnitt um bis zu 30 % anders aus.
    """
    return float(-font.getbbox("H", anchor="ls")[1]) or font.size * 0.7


def _zeilenbreite(font, text: str, spacing: float) -> float:
    """Breite einer gesperrten Zeile.

    Summiert die Einzelvorschuebe, weil auch gezeichnet wird, Zeichen fuer
    Zeichen — Pillow kennt keine Sperrung, und ein gesperrter Lauf haette
    ohnehin kein Kerning mehr, gegen das sich das aufrechnen liesse.
    """
    if not text:
        return 0.0
    return sum(font.getlength(c) for c in text) + spacing * (len(text) - 1)


def _zeilenkasten(font, text: str, x: float, baseline: float,
                  breite: float) -> tuple[float, float, float, float]:
    """Tintenkasten einer Zeile. Die Hoehe kommt aus der Metrik des ganzen
    Laufs — Umlautpunkte oben und Unterlaengen unten gehoeren dazu, sonst
    meldet die Safe-Area-Pruefung ein Ö als passend, das oben herausragt."""
    _x0, y0, _x1, y1 = font.getbbox(text, anchor="ls")
    return (x, baseline + y0, x + breite, baseline + y1)


def _passt(box, rahmen) -> bool:
    return (box[0] >= rahmen[0] and box[1] >= rahmen[1]
            and box[2] <= rahmen[2] and box[3] <= rahmen[3])


def _text_layer(size: tuple[int, int], plan: dict):
    """Die Textebene mit Alpha — ohne jede Kenntnis des Hintergrunds."""
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for zeile in plan["zeilen"]:
        x = zeile["x"]
        for zeichen in zeile["text"]:
            draw.text((x, zeile["baseline"]), zeichen, font=zeile["font"],
                      fill=TEXT_RGB + (255,), anchor="ls")
            x += zeile["font"].getlength(zeichen) + zeile["spacing"]
    x0, y0, x1, y1 = plan["linie"]
    draw.rectangle((round(x0), round(y0), round(x1), round(y1)),
                   fill=TEXT_RGB + (255,))
    return layer


def _messbox(text, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Der tatsaechliche Tintenkasten der gezeichneten Ebene.

    Gemessen wird an den Pixeln, nicht am Satzplan: der Plan rechnet mit
    Vorschubbreiten und ist damit geringfuegig grosszuegiger.
    """
    w, h = size
    box = text.getbbox()
    if not box:
        return (0, 0, w, h)
    x0, y0, x1, y1 = box
    return (max(0, x0), max(0, y0), min(w, max(x0 + 1, x1)),
            min(h, max(y0 + 1, y1)))


# --- Hintergrund ----------------------------------------------------------

def _background(seg: TitleSegment, t: TitleDefaults, bg_source: Path | None,
                size: tuple[int, int]) -> tuple[object, list[str], str]:
    """Die Hintergrundebene, noch **ohne** Abdunklung.

    Die Abdunklung haengt an der Messung und wird deshalb erst danach
    angewandt; sonst muesste der Blur je Messschritt neu laufen.

    Gibt zusaetzlich zurueck, woraus der Grund tatsaechlich entstanden ist —
    ein fehlendes Hintergrundbild wird zur Schwarzflaeche, und die vertraegt
    keine Anfangsabdunklung mehr.
    """
    from PIL import Image, ImageCms, ImageFilter, ImageOps

    from .preprocess import LONG_EDGE, _cover_crop

    w, h = size
    art = bg_kind(seg.bg)
    if art == "color":
        return (Image.new("RGB", size, _hex_rgb(seg.bg)), [], "color")
    if art == "none":
        return (Image.new("RGB", size, (0, 0, 0)), [], "none")
    if bg_source is None:
        # Kein Absturz: `_titel_hintergrund` hat den fehlenden Pfad bereits
        # gemeldet, und eine Folie auf Schwarz ist ein brauchbares Ergebnis.
        return (Image.new("RGB", size, (0, 0, 0)), [], "none")

    warnungen: list[str] = []
    try:
        with Image.open(bg_source) as roh:
            im = ImageOps.exif_transpose(roh)
            icc = roh.info.get("icc_profile")
    except (OSError, ValueError) as exc:
        return (Image.new("RGB", size, (0, 0, 0)),
                [f"Hintergrundbild {bg_source.name} ist nicht lesbar "
                 f"({type(exc).__name__}) — die Folie bekommt eine Schwarzflaeche."],
                "none")

    if icc:
        try:
            import io
            src_prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, src_prof,
                                           ImageCms.createProfile("sRGB"),
                                           outputMode="RGB")
        except Exception as exc:                        # noqa: BLE001
            warnungen.append(f"ICC-Konvertierung fehlgeschlagen ({exc}), das "
                             f"Profil wird als sRGB gelesen.")
    if im.mode != "RGB":
        im = im.convert("RGB")

    # Shrink-8 wie im Hochformat-Komposit (`preprocess._portrait_composite`):
    # sigma 60 direkt auf 7680 px zu blurren kostet spuerbar Zeit, und Gauss und
    # Skalierung kommutieren naeherungsweise. Hier ist der Trick keine Kuer —
    # die Leinwand ist die groesste im ganzen Werkzeug.
    sigma = t.blur * w / LONG_EDGE          # blur gilt auf 7680er Basis
    klein_w, klein_h = max(1, w // BLUR_SHRINK), max(1, h // BLUR_SHRINK)
    klein = _cover_crop(im, klein_w, klein_h, Image=Image)
    klein = klein.filter(ImageFilter.GaussianBlur(radius=sigma / BLUR_SHRINK))
    return (klein.resize((w, h), Image.BICUBIC), warnungen, "image")


def _hex_rgb(bg: str) -> tuple[int, int, int]:
    return (int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16))


# --- Kontrast (Anhang A des Briefings) ------------------------------------

def _relative_luminance(rgb) -> float:
    """WCAG 2.1, Kanaele auf 0..1 normiert."""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v / 255.0) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _fit_darkening(bg, box, *, start: float, minimum: float,
                   floor: float = DARKEN_FLOOR,
                   step: float = DARKEN_STEP) -> tuple[float, float]:
    """Abdunklungsfaktor, der **unter der Textflaeche** den Kontrast traegt.

    Deterministisch: festes Raster, feste Schrittweite, feste Untergrenze. Zwei
    Laeufe auf demselben Bild liefern denselben Wert — Voraussetzung fuer den
    Cache (T2). Gemessen wird nur unter dem Text; ein heller Himmel am Bildrand
    darf die ganze Folie nicht schwarz ziehen.

    Getragen werden muss der Kontrast am **hellen Ende** der Flaeche
    (``MEASURE_PERCENTILE``), nicht im Mittel. Die Begruendung steht dort.

    Gerechnet wird je Feld: erst die Abdunklung auf die sRGB-Werte, dann die
    Leuchtdichte. Die Reihenfolge ist nicht beliebig — die Transferkurve ist
    nicht linear, und ein Perzentil ueber Mittelwerte waere kein Perzentil der
    Leuchtdichte mehr.
    """
    raster = bg.crop(box).resize((MEASURE_GRID, MEASURE_GRID)).convert("RGB")
    # ``tobytes`` statt ``getdata``: Letzteres ist in Pillow 12 als veraltet
    # markiert, und drei Bytes je Feld sind hier ohnehin die direktere Form.
    roh = raster.tobytes()
    felder = [roh[i:i + 3] for i in range(0, len(roh), 3)]
    lt = _relative_luminance(TEXT_RGB)
    rang = min(len(felder) - 1, int(len(felder) * MEASURE_PERCENTILE))

    def kontrast_bei(faktor: float) -> float:
        hell = sorted(_relative_luminance([c * faktor for c in feld])
                      for feld in felder)[rang]
        return _contrast(lt, hell)

    faktor = start
    while faktor > floor:
        k = kontrast_bei(faktor)
        if k >= minimum:
            return (round(faktor, 3), k)
        faktor = round(faktor - step, 3)
    return (floor, kontrast_bei(floor))
