"""Kontaktbogen — ``slideshow sheet``
(``docs/briefing-auswahl.md``, Abschnitt 5 und Stufe 3).

:mod:`slideshow.select` waehlt ohne einen einzigen Bildpunkt anzusehen. Das ist
Absicht und zugleich die Grenze des Verfahrens: eine Liste aus 187 Kennungen
kann niemand beurteilen. Der Kontaktbogen ist die Gegenbewegung — er liest
Bildpunkte, aber nur zum *Anzeigen*, und macht aus der Durchsicht von 1240
Aufnahmen zehn Minuten.

Drei Teile, und sie haengen nur lose zusammen:

**Rekonstruktion** (:func:`selection_from_order`). Woher der Bogen weiss, was
gewaehlt ist. Siehe den Docstring dort — das ist die eigentliche Denkarbeit.

**Thumbnails** (:func:`thumbnails`). Der Bogen darf nicht das werden, was er
verhindern soll. Eingebettete EXIF-Vorschau zuerst, ffmpeg nur, wo keine liegt.

**HTML** (:func:`dump_sheet_html`). Eine Datei, kein Server, kein Framework,
kein Netzzugriff. Sie muss in fuenf Jahren noch aufgehen.

**Der Bogen schreibt nichts** (Entscheidung 7 des Briefings). Ein Klick
markiert einen Tausch, ein Knopf legt die fertigen YAML-Zeilen in die
Zwischenablage — eintragen macht der Mensch. ``order.yaml`` ist die Wahrheit;
ein Browser, der sie im Ruecken der Kommandozeile aendert, erzeugt die zweite.
"""

from __future__ import annotations

import base64
import concurrent.futures as _fut
import datetime as _dt
import html
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .chapters import day_label, first_day
from .models import Manifest, MediaItem, OrderList
from .paths import Project
from .probe import chronological, effective_capture_time
from .proc import DryRun, have, run
from .select import (BURST_GAP, BURST_MAX, DAY_ALPHA, MIN_LONG_EDGE, Burst,
                     Selection, bursts, hard_filter, panorama)

log = logging.getLogger("slideshow.sheet")

#: Kantenlaenge der Kacheln in Bildpunkten. 320 ist die Groesse, bei der man
#: auf einem 1440p-Schirm sechs Trauben nebeneinander sieht und trotzdem
#: erkennt, ob jemand die Augen zu hat.
THUMB_SIZE = 320

#: Wie viele Dateien ein exiftool-Lauf auf einmal bekommt. Nicht wegen der
#: Kommandozeile — die geht ueber ``-@`` —, sondern wegen des Speichers: mit
#: ``-b -j`` steht jede Vorschau base64-kodiert in *einer* JSON-Antwort, und
#: 1240 Vorschauen waeren dreistellige Megabyte am Stueck.
EXIF_CHUNK = 50

#: JPEG-Qualitaet der skalierten Rueckfall-Thumbnails (ffmpeg ``-q:v``,
#: kleiner ist besser).
THUMB_QUALITY = 4

#: Unterverzeichnis im Cache. Die Thumbnails gehoeren dorthin und nicht neben
#: die HTML-Datei: sie sind abgeleitet, wegwerfbar und werden beim naechsten
#: Lauf wiederverwendet.
THUMB_DIR = "thumbs"


# --------------------------------------------------------------------------
# Zustand aus order.yaml rekonstruieren
# --------------------------------------------------------------------------

#: Die Parameter aus dem Dateikopf, den `slideshow select` schreibt.
_KOPF = {
    "ziel": re.compile(r"Zielzahl\s+(\d+)"),
    "seed": re.compile(r"Seed\s+(\d+)"),
    "gap": re.compile(r"Traubenabstand\s+([\d,.]+)\s*s"),
    "alpha": re.compile(r"Tagesgewicht\s+([\d,.]+)"),
    "min_long_edge": re.compile(r"Mindestlangkante\s+(\d+)\s*px"),
    "rating_min": re.compile(r"ab\s+(\d+)\s+Sternen"),
}


def read_params(text: str) -> dict:
    """Liest die Auswahlparameter aus dem Kopf einer ``order.yaml``.

    ``slideshow select`` protokolliert sie dort, weil sie nirgends sonst
    ueberleben (Entscheidung 4 des Briefings: Auswahlparameter sind
    Generatorparameter, keine ``Defaults``). Genau deshalb muss der Bogen sie
    von dort zurueckholen: mit einem anderen ``--burst-gap`` zerfaellt das
    Material in andere Trauben, und der Bogen zeigte Geschwister, die die
    Auswahl nie als Geschwister gesehen hat.

    Was fehlt, fehlt — eine von Hand oder von ``slideshow order`` geschriebene
    Datei hat diesen Kopf nicht. Dann gelten die Vorgaben, und der Aufrufer
    sagt es dazu.
    """
    out: dict = {}
    kopf = "\n".join(z for z in text.splitlines() if z.lstrip().startswith("#"))
    for name, regex in _KOPF.items():
        treffer = regex.search(kopf)
        if not treffer:
            continue
        roh = treffer.group(1).replace(",", ".")
        out[name] = float(roh) if name in ("gap", "alpha") else int(float(roh))
    return out


def selection_from_order(manifest: Manifest, olist: OrderList, text: str = "", *,
                         ids: list[str] | None = None) -> Selection:
    """Baut die :class:`~slideshow.select.Selection` aus einer ``order.yaml``.

    **Warum nicht einfach** :func:`~slideshow.select.select_media` **noch
    einmal?** Weil der Bogen dann etwas anderes zeigte als die Datei. ``sheet``
    laeuft eigenstaendig und meist *nach* der Handarbeit: ein Bild getauscht,
    eine Traube hereingeholt, drei Zeilen auskommentiert. Ein zweiter Wurf
    wuerfelte in :func:`~slideshow.select.pick_in_burst` neu, und selbst mit
    demselben Seed traefe er die Handarbeit nicht — die steht ja gerade nicht
    im Seed. Der Nutzer saehe einen Vorschlag, den es nirgends gibt, und
    tauschte gegen Bilder, die schon drin sind. ``order.yaml`` ist die
    Wahrheit; der Bogen liest sie und rechnet nur das Drumherum dazu.

    Rekonstruiert wird aus zwei Quellen:

    - **gewaehlt** ist, was in ``items:`` steht — nichts anderes. Auskommentiert
      heisst draussen, und das gilt auch fuer eine Zeile, die von Hand
      auskommentiert wurde.
    - **die Trauben** kommen aus :func:`~slideshow.select.bursts`, mit den
      Parametern aus dem Dateikopf (:func:`read_params`). Die Traubenbildung
      haengt nur am Material und am Abstand, nicht am Zufall — sie ist
      reproduzierbar, und deshalb darf sie nachgerechnet werden.

    Daraus folgt der Rest von selbst: eine Traube mit genau einem gelisteten
    Bild ist eine getroffene Wahl, ihre uebrigen Aufnahmen sind die
    Alternativen; eine Traube ohne gelistetes Bild ist ausgelassen. Zwei
    gelistete Bilder in einer Traube sind kein Fehler, sondern Handarbeit
    (jemand wollte beide) — sie stehen beide gross da, und die Meldung sagt es.
    """
    offsets = manifest.clock_offsets
    by_id = {m.id: m for m in manifest.media}
    p = read_params(text)

    sel = Selection(seed=p.get("seed", 0), gesamt=len(manifest.media))
    sel.params = {
        "gap": p.get("gap", BURST_GAP), "alpha": p.get("alpha", DAY_ALPHA),
        "min_long_edge": p.get("min_long_edge", MIN_LONG_EDGE),
        "rating_min": p.get("rating_min", 0), "by": "day",
    }
    if not p:
        sel.meldungen.append(
            f"Diese order.yaml stammt nicht von `slideshow select` — im Kopf "
            f"stehen keine Auswahlparameter. Der Bogen rechnet mit den Vorgaben "
            f"(Traubenabstand {BURST_GAP:g} s).")

    gewaehlte_ids = list(ids) if ids is not None else [
        mid for g in olist.blocks for mid in g.items]
    gewaehlt = {mid for mid in gewaehlte_ids if mid in by_id}
    sel.ids = [mid for mid in gewaehlte_ids if mid in by_id]
    sel.ziel = p.get("ziel", len(sel.ids))

    # -- ohne Zeitstempel: eigener Topf, wie in `select_media` ----------
    datiert: list[MediaItem] = []
    for m in manifest.media:
        ts = effective_capture_time(m, offsets)
        (sel.ohne_datum if ts is None or m.time_source == "none"
         else datiert).append(m)

    # Die harten Ausschluesse werden nachgerechnet, nicht ausgelesen: ihr Grund
    # steht in der Datei nur als Freitext hinter einer Kommentarzeile, und ihn
    # dort zurueckzuparsen hiesse, eine Formatierung zur Schnittstelle zu
    # machen. Die Rechnung ist billig und liefert denselben Text.
    tauglich, sel.gruende = hard_filter(
        datiert, min_long_edge=int(sel.params["min_long_edge"]),
        rating_min=int(sel.params["rating_min"]))

    bilder = [m for m in tauglich if m.kind == "image"]
    alle = bursts(bilder, offsets, gap=float(sel.params["gap"]), max_span=BURST_MAX)

    mehrfach: list[str] = []
    for b in alle:
        drin = [m for m in b.items if m.id in gewaehlt]
        if not drin:
            sel.ausgelassen.append(b)
            continue
        draussen = [m for m in b.items if m.id not in gewaehlt]
        for m in drin:
            sel.alternativen[m.id] = list(draussen)
        if len(drin) > 1:
            mehrfach.append("/".join(m.id for m in drin))

    if mehrfach:
        sel.meldungen.append(
            f"{len(mehrfach)} Trauben stellen mehr als ein Bild — das kann "
            f"`slideshow select` nicht erzeugt haben, es ist Handarbeit: "
            f"{', '.join(mehrfach[:8])}"
            + (f" (+{len(mehrfach) - 8} weitere)" if len(mehrfach) > 8 else ""))

    hart_gewaehlt = sorted(sel.gruende.keys() & gewaehlt)
    if hart_gewaehlt:
        sel.meldungen.append(
            f"{len(hart_gewaehlt)} gewaehlte Medien fallen eigentlich technisch "
            f"heraus und stehen trotzdem in der Datei: "
            f"{', '.join(hart_gewaehlt[:8])}")

    sel.quote = _quote(alle, gewaehlt, by_id, offsets)
    return sel


def _quote(alle: list[Burst], gewaehlt: set[str], by_id: dict[str, MediaItem],
           offsets: dict[str, float]) -> dict:
    """Tag -> (gewaehlt, Trauben, Aufnahmen) — dieselbe Form wie in ``select``.

    Gezaehlt werden die *Trauben* je Tag und nicht die Bilder, weil die Quote
    genau darauf rechnet (Entscheidung 6). Die gewaehlten kommen aus der Datei
    und nicht aus den Trauben: ein von Hand hereingeholtes Bild ohne Traube —
    ein Clip etwa — soll in seinem Tag mitgezaehlt werden.
    """
    zahlen: dict[_dt.date, list[int]] = {}
    for b in alle:
        eintrag = zahlen.setdefault(b.tag, [0, 0, 0])
        eintrag[1] += 1
        eintrag[2] += len(b)
    for mid in gewaehlt:
        m = by_id.get(mid)
        ts = effective_capture_time(m, offsets) if m else None
        if ts is None:
            continue
        zahlen.setdefault(_dt.datetime.fromtimestamp(ts).date(), [0, 0, 0])[0] += 1
    return {tag: tuple(werte) for tag, werte in sorted(zahlen.items())}


# --------------------------------------------------------------------------
# Thumbnails
# --------------------------------------------------------------------------

@dataclass
class ThumbStats:
    """Woher die Thumbnails kamen — die Zahl, die Abnahmekriterium A9 meint."""

    #: Schon vorhanden, kein Zugriff auf die Quelldatei.
    aus_cache: int = 0
    #: Aus der eingebetteten EXIF-Vorschau, ohne jede Decodierung.
    aus_vorschau: int = 0
    #: Ueber ffmpeg skaliert, weil keine Vorschau vorlag oder sie zu klein war.
    skaliert: int = 0
    #: Medien ohne Thumbnail (Quelldatei weg, ffmpeg gescheitert).
    fehlend: list[str] = field(default_factory=list)
    sekunden: float = 0.0

    @property
    def erzeugt(self) -> int:
        return self.aus_vorschau + self.skaliert


def thumbnails(project: Project, media: list[MediaItem], *, size: int = THUMB_SIZE,
               force: bool = False, jobs: int | None = None,
               dry: DryRun | None = None) -> tuple[dict[str, Path], ThumbStats]:
    """Erzeugt Vorschaubilder nach ``cache/thumbs/<id>.jpg``.

    **Eingebettete Vorschau zuerst.** JPEG und praktisch jedes RAW tragen ein
    fertiges Vorschaubild im Header, und ``exiftool -b`` holt es heraus, ohne
    einen einzigen Bildpunkt zu decodieren — im Batchlauf ueber alle Dateien.
    Das ist der Kern der Sache: 1240 Aufnahmen von 6232 px herunterzurechnen,
    nur um sie anzusehen, waere genau der Aufwand, den der Bogen vermeiden
    soll.

    Nur wo keine Vorschau liegt (oder sie kleiner ist als die Kachel), wird
    ueber ffmpeg skaliert — ein Prozess je Datei, und das ist der teure Weg.
    Die Zaehler in :class:`ThumbStats` sagen hinterher, welcher Weg wie oft
    gegangen wurde; ohne sie sieht ein Lauf, der eine halbe Stunde skaliert,
    genauso aus wie einer, der zwanzig Sekunden Header liest.

    **Inkrementell**: ein zweiter Lauf erzeugt nur Fehlendes. Die Thumbnails
    haengen an der Medien-ID und nicht am Dateiinhalt — ein neu belichtetes
    Bild unter altem Namen braucht ``force``. Das ist die richtige Abwaegung:
    einen Content-Hash ueber 1240 Dateien zu bilden kostet mehr als der ganze
    Rest dieses Kommandos.

    Die eingebettete Vorschau wird **uebernommen, wie sie ist** — auch wenn sie
    mit 1616 px groesser ausfaellt als die Kachel. Sie herunterzurechnen kostete
    je Datei einen Prozessstart und damit genau das, was die Vorschau einspart;
    die Kachelgroesse macht ohnehin das CSS.
    """
    ziel = project.cache / THUMB_DIR
    beginn = time.monotonic()
    stats = ThumbStats()
    pfade: dict[str, Path] = {}

    offen: list[tuple[MediaItem, Path]] = []
    for m in media:
        dst = ziel / f"{m.id}.jpg"
        if dst.exists() and not force:
            pfade[m.id] = dst
            stats.aus_cache += 1
            continue
        offen.append((m, dst))

    if not offen:
        stats.sekunden = time.monotonic() - beginn
        return (pfade, stats)

    if dry is not None and dry.enabled:
        dry.record(["exiftool", "-b", "-PreviewImage", f"({len(offen)} Dateien)"])
        for m, dst in offen:
            pfade[m.id] = dst
        stats.sekunden = time.monotonic() - beginn
        return (pfade, stats)

    ziel.mkdir(parents=True, exist_ok=True)

    # Clips tragen keine EXIF-Vorschau; fuer sie gibt es nur den Standbildweg.
    kandidaten = [(m, dst) for m, dst in offen if m.kind == "image"]
    fertig = _aus_vorschau(project, kandidaten, size=size)
    stats.aus_vorschau = len(fertig)
    for mid in fertig:
        pfade[mid] = ziel / f"{mid}.jpg"

    rest = [(m, dst) for m, dst in offen if m.id not in fertig]
    if rest:
        log.info("%d Thumbnails aus der EXIF-Vorschau, %d werden skaliert",
                 stats.aus_vorschau, len(rest))
        for mid, ok in _skalieren(project, rest, size=size, jobs=jobs):
            if ok:
                stats.skaliert += 1
                pfade[mid] = ziel / f"{mid}.jpg"
            else:
                stats.fehlend.append(mid)

    stats.sekunden = time.monotonic() - beginn
    return (pfade, stats)


def _aus_vorschau(project: Project, kandidaten: list[tuple[MediaItem, Path]], *,
                  size: int) -> set[str]:
    """Holt die eingebetteten Vorschaubilder heraus. Liefert die erledigten IDs.

    In Haeppchen, und zwar wegen des Speichers: ``exiftool -b -j`` liefert jede
    Vorschau base64-kodiert in *einer* JSON-Antwort. Ueber die Laenge der
    Kommandozeile muss man sich dagegen keine Gedanken machen — die Pfade gehen
    wie in :func:`slideshow.probe.read_exif_batch` ueber eine Argumentdatei.
    """
    if not kandidaten or not have("exiftool"):
        if kandidaten:
            log.info("exiftool fehlt — alle Thumbnails werden skaliert")
        return set()

    quellen = {str(project.abs(m.path).resolve()): (m, dst)
               for m, dst in kandidaten}
    erledigt: set[str] = set()
    pfadliste = list(quellen)

    for i in range(0, len(pfadliste), EXIF_CHUNK):
        teil = pfadliste[i:i + EXIF_CHUNK]
        for eintrag in _exiftool_binaer(teil):
            treffer = quellen.get(str(Path(eintrag.get("SourceFile", "")).resolve()))
            if treffer is None:
                continue
            m, dst = treffer
            roh = _beste_vorschau(eintrag, size=size)
            if roh is None:
                continue
            try:
                dst.write_bytes(roh)
            except OSError as exc:
                log.warning("%s: Vorschau nicht schreibbar (%s)", m.id, exc)
                continue
            erledigt.add(m.id)
    return erledigt


def _exiftool_binaer(pfade: list[str]) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="slideshow-thumbs-") as tmp:
        argfile = Path(tmp) / "dateien.args"
        argfile.write_text("\n".join(pfade) + "\n", encoding="utf-8")
        # ``-b`` liefert unter ``-j`` einen ``base64:``-Praefix statt Rohbytes;
        # anders liesse sich Binaeres nicht in JSON transportieren.
        cmd = ["exiftool", "-j", "-b", "-q", "-charset", "filename=utf8",
               "-PreviewImage", "-ThumbnailImage", "-@", str(argfile)]
        res = run(cmd, check=False, timeout=900)
    if not res.stdout.strip():
        return []
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        log.warning("exiftool-Ausgabe unlesbar (%s)", exc)
        return []


def _beste_vorschau(eintrag: dict, *, size: int) -> bytes | None:
    """Die groesste brauchbare eingebettete Vorschau, oder None.

    ``ThumbnailImage`` ist bei vielen Kameras 160x120 gross. Das reicht fuer
    eine 320-px-Kachel nicht — ein matschiges Thumbnail ist schlimmer als
    keines, weil man ihm nicht ansieht, dass die Unschaerfe von der Vorschau
    kommt und nicht vom Bild. Deshalb wird die Groesse aus dem JPEG-Kopf
    gelesen (ohne Decodierung) und zu Kleines verworfen.
    """
    beste: bytes | None = None
    for tag in ("PreviewImage", "ThumbnailImage"):
        wert = eintrag.get(tag)
        if not isinstance(wert, str) or not wert.startswith("base64:"):
            continue
        try:
            roh = base64.b64decode(wert[7:], validate=False)
        except (ValueError, TypeError):
            continue
        masse = _jpeg_groesse(roh)
        if masse is None or max(masse) < size:
            continue
        if beste is None or len(roh) > len(beste):
            beste = roh
    return beste


def _jpeg_groesse(data: bytes) -> tuple[int, int] | None:
    """Bildgroesse aus dem JPEG-Kopf — ohne einen Bildpunkt zu decodieren.

    Zwanzig Zeilen Markerlauf statt einer Bildbibliothek: gebraucht wird nur
    die Frage "gross genug?", und dafuer eine Abhaengigkeit einzufuehren waere
    nicht verhaeltnismaessig.
    """
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    i, n = 2, len(data)
    while i + 3 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0x01, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        laenge = int.from_bytes(data[i + 2:i + 4], "big")
        # SOF0..SOF15 tragen die Groesse; die Huffman-/Arithmetik-Tabellen
        # (C4, C8, CC) liegen im selben Bereich und tun es nicht.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 > n:
                return None
            hoehe = int.from_bytes(data[i + 5:i + 7], "big")
            breite = int.from_bytes(data[i + 7:i + 9], "big")
            return (breite, hoehe)
        if laenge < 2:
            return None
        i += 2 + laenge
    return None


def _skalieren(project: Project, rest: list[tuple[MediaItem, Path]], *, size: int,
               jobs: int | None) -> list[tuple[str, bool]]:
    """Der Rueckfall: ffmpeg, ein Prozess je Datei.

    Parallel ueber Threads und nicht ueber Prozesse: die Arbeit steckt
    vollstaendig in ffmpeg, der eigene Prozess wartet nur. Ein
    ``ProcessPoolExecutor`` wie in :mod:`slideshow.preprocess` brauchte hier
    nichts als Startzeit.
    """
    arbeiter = max(1, jobs or min(8, (os.cpu_count() or 4)))

    def einer(paar: tuple[MediaItem, Path]) -> tuple[str, bool]:
        m, dst = paar
        src = project.abs(m.path)
        if not src.exists():
            log.warning("%s: Quelldatei fehlt (%s)", m.id, src)
            return (m.id, False)
        cmd = ["ffmpeg", "-v", "error", "-y"]
        if m.kind == "clip":
            # Ein Standbild aus der ersten Sekunde: Bild 0 ist bei vielen
            # Kameras noch dunkel, weil die Belichtung nachregelt.
            cmd += ["-ss", "1"]
        cmd += ["-i", str(src), "-frames:v", "1", "-vf",
                f"scale=w={size}:h={size}:force_original_aspect_ratio=decrease"
                f":flags=lanczos",
                "-q:v", str(THUMB_QUALITY), str(dst)]
        res = run(cmd, check=False, timeout=300)
        if not res.ok or not dst.exists():
            log.warning("%s: kein Thumbnail (%s)", m.id, res.stderr_tail(3))
            return (m.id, False)
        return (m.id, True)

    with _fut.ThreadPoolExecutor(max_workers=arbeiter) as pool:
        return list(pool.map(einer, rest))


# --------------------------------------------------------------------------
# Aufbau des Bogens
# --------------------------------------------------------------------------

@dataclass
class Kachel:
    """Ein Bild auf dem Bogen."""

    m: MediaItem
    gewaehlt: bool = False
    #: Grund eines harten Ausschlusses, als Badge.
    grund: str = ""
    zeit: str = ""


@dataclass
class Gruppe:
    """Eine Traube — zusammenhaengende Kachelgruppe.

    ``art`` ist ``gewaehlt`` (mit grosser Kachel), ``ausgelassen`` (nur kleine)
    oder ``hart`` (technisch ausgeschlossen).
    """

    art: str
    kacheln: list[Kachel] = field(default_factory=list)
    zeit: str = ""


@dataclass
class Abschnitt:
    """Ein Tag."""

    titel: str
    info: str = ""
    gruppen: list[Gruppe] = field(default_factory=list)


def _uhr(ts: float | None) -> str:
    return f"{_dt.datetime.fromtimestamp(ts):%H:%M}" if ts is not None else "?"


def _kachel(m: MediaItem, sel: Selection, offsets: dict[str, float], *,
            gewaehlt: bool) -> Kachel:
    return Kachel(m=m, gewaehlt=gewaehlt, grund=sel.gruende.get(m.id, ""),
                  zeit=_uhr(effective_capture_time(m, offsets)))


def build_sections(sel: Selection, manifest: Manifest, *,
                   nur_auswahl: bool = False) -> list[Abschnitt]:
    """Ordnet alles, was der Bogen zeigt, nach Tagen und Trauben.

    Gegliedert wird nach **Kalendertag**, nicht nach den Bloecken der
    ``order.yaml``. Das ist eine bewusste Abweichung vom Briefing (5.1), und
    zwar aus dem Zweck des Bogens: verglichen wird mit dem Nachbarn, und
    Nachbar ist, was zur selben Zeit entstand. Nach einer thematischen
    Umsortierung stuenden die Geschwister einer Traube sonst in drei
    verschiedenen Abschnitten — genau dort, wo man sie nicht nebeneinander
    sieht. Solange die Datei von ``slideshow select`` kommt, ist beides
    dasselbe: dessen Bloecke *sind* Kalendertage.
    """
    offsets = manifest.clock_offsets
    by_id = {m.id: m for m in manifest.media}
    tag_eins = first_day(chronological(manifest), offsets)
    gewaehlt = set(sel.ids)

    # Jede Gruppe bekommt einen Zeitpunkt und wird danach einsortiert. Das ist
    # der einzige Schluessel, den alle drei Arten gemeinsam haben.
    eintraege: list[tuple[float, Gruppe]] = []
    verbraucht: set[str] = set()

    for mid in sel.ids:
        m = by_id.get(mid)
        if m is None:
            continue
        geschwister = sel.alternativen.get(mid, [])
        g = Gruppe(art="gewaehlt")
        g.kacheln.append(_kachel(m, sel, offsets, gewaehlt=True))
        verbraucht.add(mid)
        if not nur_auswahl:
            for x in sorted(geschwister,
                            key=lambda y: (effective_capture_time(y, offsets) or 0.0)):
                if x.id in gewaehlt or x.id in verbraucht:
                    continue
                g.kacheln.append(_kachel(x, sel, offsets, gewaehlt=False))
                verbraucht.add(x.id)
        ts = effective_capture_time(m, offsets)
        g.zeit = _uhr(ts)
        eintraege.append((ts if ts is not None else 0.0, g))

    if not nur_auswahl:
        for b in sel.ausgelassen:
            g = Gruppe(art="ausgelassen", zeit=_uhr(b.start))
            for x in b.items:
                if x.id in verbraucht:
                    continue
                g.kacheln.append(_kachel(x, sel, offsets, gewaehlt=False))
                verbraucht.add(x.id)
            if g.kacheln:
                eintraege.append((b.start, g))

        # Harte Ausschluesse stehen an ihrem zeitlichen Platz und nicht am
        # Ende: man sucht sie nicht, man stolpert ueber sie — "hier fehlt was"
        # ist die Frage, auf die der Badge die Antwort ist.
        for mid, grund in sorted(sel.gruende.items()):
            m = by_id.get(mid)
            if m is None or mid in verbraucht:
                continue
            ts = effective_capture_time(m, offsets)
            g = Gruppe(art="hart", zeit=_uhr(ts))
            g.kacheln.append(Kachel(m=m, grund=grund, zeit=_uhr(ts)))
            verbraucht.add(mid)
            eintraege.append((ts if ts is not None else 0.0, g))

    eintraege.sort(key=lambda x: (x[0], x[1].kacheln[0].m.id))

    abschnitte: list[Abschnitt] = []
    letzter: _dt.date | None = None
    for ts, g in eintraege:
        tag = _dt.datetime.fromtimestamp(ts).date()
        if tag != letzter:
            zahlen = sel.quote.get(tag)
            info = ""
            if zahlen:
                n, trauben, aufnahmen = zahlen
                info = (f"{aufnahmen} Aufnahmen · {trauben} Trauben · "
                        f"{n} gewaehlt")
            abschnitte.append(Abschnitt(titel=day_label(ts, tag_eins), info=info))
            letzter = tag
        abschnitte[-1].gruppen.append(g)

    ohne = [m for m in sel.ohne_datum if m.id not in verbraucht]
    if ohne and not nur_auswahl:
        a = Abschnitt(titel="Ohne Aufnahmezeitpunkt",
                      info=f"{len(ohne)} Medien · von Hand einsortieren")
        for m in ohne:
            g = Gruppe(art="hart" if m.id not in gewaehlt else "gewaehlt")
            g.kacheln.append(_kachel(m, sel, offsets, gewaehlt=m.id in gewaehlt))
            a.gruppen.append(g)
        abschnitte.append(a)
    return abschnitte


# --------------------------------------------------------------------------
# HTML
#
# Von Hand geschrieben, ohne Vorlagenmaschine — aus demselben Grund, aus dem
# `order.yaml` von Hand geschrieben wird: die Datei soll in fuenf Jahren noch
# aufgehen. Kein Framework, kein CDN, kein Netzzugriff, keine `data:`-URI (1240
# base64-Thumbnails waeren ~27 MB in einer Datei, die kein Editor mehr oeffnet).
# --------------------------------------------------------------------------

_CSS = """
:root { --bg:#fbfaf8; --fg:#1d1c1a; --matt:#6d6a66; --linie:#ded9d2;
        --gut:#1f7a4d; --raus:#b03636; --drin:#2f6f9f; --karte:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#17181a; --fg:#eceae7; --matt:#9a9691; --linie:#33353a;
          --gut:#4ec98a; --raus:#e8776f; --drin:#6fb0dd; --karte:#1f2124; }
}
* { box-sizing: border-box; }
body { margin:0; padding:0 0 5rem; background:var(--bg); color:var(--fg);
       font:14px/1.45 "Segoe UI", system-ui, sans-serif; }
header { padding:1.2rem 1.5rem; border-bottom:1px solid var(--linie); }
h1 { margin:0 0 .3rem; font-size:1.25rem; letter-spacing:.01em; }
.meta { color:var(--matt); font-size:.85rem; }
.meta code { background:var(--karte); padding:.05em .35em; border-radius:3px; }
.hinweis { color:var(--raus); margin:.15rem 0 0; font-size:.85rem; }
.balken { margin:.9rem 0 0; display:grid;
          grid-template-columns:max-content 1fr max-content; gap:.2rem .6rem;
          align-items:center; max-width:44rem; font-size:.78rem; }
.balken .tag { font-weight:600; color:var(--matt); }
.spur { position:relative; height:.6rem; }
.spur span { position:absolute; left:0; top:0; height:100%; min-width:1px;
             border-radius:2px; }
.spur .alle { background:var(--matt); opacity:.25; }
.spur .drin { background:var(--gut); opacity:.9; }
.balken .zahl { color:var(--matt); }
section { padding:1.1rem 1.5rem .4rem; }
h2 { margin:0 0 .1rem; font-size:1rem; }
h2 span { color:var(--matt); font-weight:400; font-size:.82rem; margin-left:.6rem; }
.reihe { display:flex; flex-wrap:wrap; gap:1.4rem; padding:.7rem 0; }
.traube { display:flex; align-items:flex-start; gap:.35rem;
          padding:.35rem .5rem; border-radius:7px; background:var(--karte);
          border:1px solid var(--linie); }
.traube.ausgelassen { background:transparent; border-style:dashed; opacity:.85; }
.traube.hart { background:transparent; border-color:var(--raus); }
figure { margin:0; width:var(--klein); cursor:pointer; }
figure.gross { width:var(--gross); }
figure img { display:block; width:100%; height:auto; border-radius:4px;
             background:var(--linie); filter:grayscale(.55) opacity(.72); }
/* Das gewaehlte Bild bekommt eine eigene Farbe, nicht nur mehr Flaeche.
   Groesse allein traegt beim Scrollen nicht: neben einer Traube aus fuenf
   kleinen Kacheln sieht man sie, neben einer einzelnen nicht mehr. Und sobald
   markiert wird, konkurriert sie mit Gruen und Rot — drei Zustaende brauchen
   drei Sprachen. Blau ist die dritte: der Ausgangszustand, den keiner
   angefasst hat. */
figure.gross img { filter:none; outline:3px solid var(--drin);
                   outline-offset:1px; }
figure.gross figcaption strong { color:var(--drin); }
figure:hover img { outline:2px solid var(--matt); }
/* Nur solange nichts markiert ist — sonst schluege der Hover mit seiner
   hoeheren Spezifitaet das Gruen bzw. Rot der Markierung. */
figure.gross:not(.rein):not(.raus):hover img { outline:3px solid var(--drin); }
figcaption { font-size:.68rem; color:var(--matt); word-break:break-all;
             margin-top:.15rem; line-height:1.25; }
figure.gross figcaption { font-size:.74rem; color:var(--fg); }
.leer { display:flex; align-items:center; justify-content:center;
        aspect-ratio:3/2; border:1px dashed var(--linie); border-radius:4px;
        color:var(--matt); font-size:.7rem; }
.badge { display:inline-block; background:var(--raus); color:#fff;
         border-radius:3px; padding:0 .3em; font-size:.65rem; }
.stern { color:#c99a15; }
figure.rein img { outline:3px solid var(--gut); filter:none; }
figure.raus img { outline:3px solid var(--raus); opacity:.45; }
footer { position:fixed; left:0; right:0; bottom:0; background:var(--karte);
         border-top:1px solid var(--linie); padding:.6rem 1.5rem;
         display:flex; gap:1rem; align-items:center; font-size:.85rem; }
button { font:inherit; padding:.35rem .9rem; border-radius:5px;
         border:1px solid var(--linie); background:var(--bg); color:var(--fg);
         cursor:pointer; }
button:hover { border-color:var(--matt); }
#zettel { position:fixed; right:1.5rem; bottom:4rem; max-width:32rem;
          max-height:50vh; overflow:auto; background:var(--karte);
          border:1px solid var(--linie); border-radius:7px; padding:.8rem 1rem;
          white-space:pre; font-family:Consolas, monospace; font-size:.78rem;
          display:none; }
"""

# Vanilla, ohne Abhaengigkeit und ohne Aufbaulauf. Der Bogen schreibt nichts —
# er markiert und legt Text in die Zwischenablage (Entscheidung 7).
_JS = """
var marken = Object.create(null);

function malen(el) {
  var m = marken[el.dataset.id];
  el.classList.toggle('rein', m === 'rein');
  el.classList.toggle('raus', m === 'raus');
}

function setzen(el, wert) {
  if (wert) { marken[el.dataset.id] = wert; } else { delete marken[el.dataset.id]; }
  malen(el);
}

document.addEventListener('click', function (ev) {
  var el = ev.target.closest ? ev.target.closest('figure[data-id]') : null;
  if (!el) { return; }
  var alt = marken[el.dataset.id];
  var gross = el.classList.contains('gross');
  setzen(el, alt ? null : (gross ? 'raus' : 'rein'));
  /* Ein Tausch hat zwei Seiten: wer ein Geschwister hereinholt, will das
     gewaehlte Bild derselben Traube hinaus. Automatisch mitmarkiert, aber
     einzeln wieder abwaehlbar. */
  if (!alt && !gross) {
    var chef = el.parentNode.querySelector('figure.gross[data-id]');
    if (chef && !marken[chef.dataset.id]) { setzen(chef, 'raus'); }
  }
  zaehlen();
});

function zeilen() {
  var rein = [], raus = [], out = [];
  document.querySelectorAll('figure[data-id]').forEach(function (el) {
    var m = marken[el.dataset.id];
    if (m === 'rein') { rein.push(el.dataset.id); }
    if (m === 'raus') { raus.push(el.dataset.id); }
  });
  if (!rein.length && !raus.length) { return ''; }
  out.push('# Kontaktbogen: ' + rein.length + ' herein, ' + raus.length + ' hinaus.');
  out.push('# In order.yaml eintragen, wo das getauschte Bild steht:');
  rein.forEach(function (id) { out.push('      - ' + id); });
  if (raus.length) {
    out.push('# und diese Zeilen auskommentieren:');
    raus.forEach(function (id) { out.push('      #  raus: ' + id); });
  }
  return out.join('\\n');
}

function zaehlen() {
  var n = Object.keys(marken).length;
  document.getElementById('stand').textContent =
    n ? n + ' markiert' : 'nichts markiert';
  var z = document.getElementById('zettel');
  z.textContent = zeilen();
  z.style.display = n ? 'block' : 'none';
}

function kopieren() {
  var text = zeilen();
  if (!text) { return; }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(gemeldet, gemeldet);
  } else {
    gemeldet();
  }
}

/* Der Weg fuer viele Aenderungen. Ueber die Zwischenablage 160 Zeilen von Hand
   in order.yaml einzusortieren ist Stunden Arbeit; die Datei nimmt
   `slideshow order --apply` in einem Zug entgegen. Ein Blob, keine Anfrage
   nach draussen — der Bogen bleibt ohne Netzzugriff. */
function herunterladen() {
  var text = zeilen();
  if (!text) { return; }
  var url = URL.createObjectURL(new Blob([text], {type: 'text/plain'}));
  var a = document.createElement('a');
  a.href = url;
  a.download = 'auswahl.txt';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  /* Erst nach dem Klick freigeben, sonst ist die URL beim Speichern schon
     ungueltig. */
  setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
}

function gemeldet() {
  var z = document.getElementById('zettel');
  z.style.display = 'block';
  var r = document.createRange();
  r.selectNodeContents(z);
  var s = window.getSelection();
  s.removeAllRanges();
  s.addRange(r);
}
"""


# --------------------------------------------------------------------------
# Der Rueckweg: was der Bogen ausgibt, wieder einlesen
#
# Erzeuger und Leser stehen bewusst in *einer* Datei. Das Format entsteht oben
# in ``zeilen()`` als JavaScript und wird hier in Python wieder zerlegt — zwei
# Module weit auseinander waeren zwei Wahrheiten, und die erste Aenderung am
# einen braeche das andere still. Dieselbe Ueberlegung wie bei
# ``select._kopf`` und :func:`read_params`.
# --------------------------------------------------------------------------

#: ``      - img_042`` — hereinnehmen.
_REIN = re.compile(r"^\s*-\s*([A-Za-z0-9_-]+)\s*$")
#: ``      #  raus: img_042`` — hinausnehmen.
_RAUS = re.compile(r"^\s*#\s*raus:\s*([A-Za-z0-9_-]+)\s*$")
#: Byte Order Mark. Als Escape und nicht als Zeichen: im Quelltext waere es
#: unsichtbar, und niemand fuende es beim Lesen wieder.
_BOM = "\ufeff"


def parse_changes(text: str) -> tuple[list[str], list[str], list[tuple[int, str]]]:
    """Zerlegt die Aenderungsliste aus dem Bogen.

    Liefert ``(hereinnehmen, hinausnehmen, unverstanden)``. Die dritte Liste
    ist der Grund, warum diese Funktion nicht einfach zwei Regexe ueber den Text
    laufen laesst: eine Zeile, die *wie* ein Eintrag aussieht und keiner ist —
    ein halb geloeschtes Stueck, eine von Hand danebengetippte ID — wuerde sonst
    stillschweigend verschwinden. Bei 160 Aenderungen faellt genau das nicht
    auf, und hinterher fehlen drei Bilder im Film.

    Reine Kommentarzeilen (die Ueberschriften, die der Bogen mitschreibt) und
    Leerzeilen sind keine Fehler.
    """
    rein: list[str] = []
    raus: list[str] = []
    unklar: list[tuple[int, str]] = []
    # Das BOM ist hier kein Randfall: PowerShell setzt es vor jede Pipe an ein
    # fremdes Programm, und ein Browser-Download traegt es je nach Editor auch.
    # Ohne diese Zeile scheitert ausgerechnet die erste Aenderung — und die
    # Meldung zeigte auf eine Zeile, die voellig richtig aussieht.
    for nr, zeile in enumerate(text.lstrip(_BOM).splitlines(), start=1):
        if not zeile.strip():
            continue
        # Die raus-Zeile ist selbst ein Kommentar und muss deshalb *vor* der
        # allgemeinen Kommentarpruefung stehen.
        m = _RAUS.match(zeile)
        if m:
            raus.append(m.group(1))
            continue
        if zeile.lstrip().startswith("#"):
            continue
        m = _REIN.match(zeile)
        if m:
            rein.append(m.group(1))
            continue
        unklar.append((nr, zeile.strip()))
    return (rein, raus, unklar)


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


def _rel(pfad: Path, base: Path | None) -> str:
    """Relativer, POSIX-getrennter Verweis von der HTML-Datei aus.

    Relativ und nicht absolut, damit der Bogen mitsamt Projektverzeichnis
    umziehen kann; und keine ``data:``-URI, weil 1240 base64-Thumbnails die
    Datei auf zweistellige Megabyte aufblaehen wuerden.
    """
    if base is None:
        return Path(pfad).as_posix()
    try:
        return Path(os.path.relpath(pfad, base)).as_posix()
    except ValueError:
        return Path(pfad).as_posix()


def _kachel_html(k: Kachel, thumbs: dict[str, Path], base: Path | None) -> str:
    klasse = ' class="gross"' if k.gewaehlt else ""
    pfad = thumbs.get(k.m.id)
    if pfad is not None:
        # loading="lazy" ist hier kein Feinschliff: ohne das dekodiert der
        # Browser beim Oeffnen alle 1240 Bilder auf einmal.
        bild = (f'<img loading="lazy" decoding="async" alt="" '
                f'src="{_e(_rel(pfad, base))}">')
    else:
        bild = '<div class="leer">kein Thumbnail</div>'

    marken: list[str] = []
    if k.grund:
        marken.append(f'<span class="badge">{_e(k.grund)}</span>')
    if k.m.rating:
        marken.append(f'<span class="stern">{"*" * k.m.rating}</span>')
    if k.m.kind == "clip":
        marken.append("Clip")
    elif panorama(k.m):
        marken.append("Pano")
    zusatz = (" " + " ".join(marken)) if marken else ""
    # Beim gewaehlten Bild traegt die Kennung die Farbe mit. Die Kachel selbst
    # ist beim Scrollen oft halb aus dem Bild; die Zeile darunter nicht.
    kennung = f"<strong>{_e(k.m.id)}</strong>" if k.gewaehlt else _e(k.m.id)
    return (f"<figure{klasse} data-id=\"{_e(k.m.id)}\">{bild}"
            f'<figcaption>{kennung}<br>{_e(k.zeit)}{zusatz}</figcaption></figure>')


def _kopf_html(sel: Selection, quelle: str, gezeigt: int) -> str:
    p = sel.params
    komma = f"{float(p.get('gap', BURST_GAP)):g}".replace(".", ",")
    teile = [
        f"{len(sel.ids)} von {sel.gesamt} Medien gewaehlt",
        f"Zielzahl {sel.ziel}" if sel.ziel else "",
        f"Seed {sel.seed}" if sel.seed else "",
        f"Traubenabstand {komma}&nbsp;s",
        f"Tagesgewicht {float(p.get('alpha', DAY_ALPHA)):g}".replace(".", ","),
        f"Mindestlangkante {int(p.get('min_long_edge', MIN_LONG_EDGE))}&nbsp;px",
        f"{gezeigt} Kacheln",
    ]
    zeile = " · ".join(t for t in teile if t)
    hinweise = "".join(f'<p class="hinweis">{_e(m)}</p>' for m in sel.meldungen)
    return (f"<header><h1>Kontaktbogen</h1>"
            f'<p class="meta">Quelle <code>{_e(quelle)}</code> — {zeile}</p>'
            f"{hinweise}{_balken_html(sel)}</header>")


def _balken_html(sel: Selection) -> str:
    """Die Quote je Tag als Balken.

    Die eigentliche Aussage der Auswahl steht nicht in der Zahl 187, sondern in
    ihrer Verteilung: sieht man dem Bild an, dass der Wandertag mehr stellt als
    der Anreisetag, ohne ihn zu erschlagen, stimmt die Daempfung.
    """
    if not sel.quote:
        return ""
    hoechst = max((z[2] for z in sel.quote.values()), default=0) or 1
    zeilen = []
    for tag, (n, trauben, aufnahmen) in sel.quote.items():
        breite = max(1, int(round(100.0 * aufnahmen / hoechst)))
        anteil = max(1, int(round(100.0 * n / hoechst))) if n else 0
        zeilen.append(
            f'<div class="tag">{tag:%d.%m.}</div>'
            f'<div class="spur"><span class="alle" style="width:{breite}%"></span>'
            f'<span class="drin" style="width:{anteil}%"></span></div>'
            f'<div class="zahl">{n} von {trauben} Trauben · '
            f"{aufnahmen} Aufnahmen</div>")
    return f'<div class="balken">{"".join(zeilen)}</div>'


def dump_sheet_html(sel: Selection, thumbs: dict[str, Path], manifest: Manifest, *,
                    base: Path | None = None, nur_auswahl: bool = False,
                    thumb: int = THUMB_SIZE, quelle: str = "order.yaml") -> str:
    """Baut den Kontaktbogen als eine einzige, in sich geschlossene HTML-Datei.

    ``base`` ist das Verzeichnis, in dem die Datei landet — die Verweise auf
    ``cache/thumbs/`` werden relativ dazu gebildet.
    """
    abschnitte = build_sections(sel, manifest, nur_auswahl=nur_auswahl)
    teile: list[str] = []
    gezeigt = 0
    for a in abschnitte:
        kopf = f"<h2>{_e(a.titel)}"
        if a.info:
            kopf += f"<span>{_e(a.info)}</span>"
        kopf += "</h2>"
        reihe: list[str] = []
        for g in a.gruppen:
            kacheln = "".join(_kachel_html(k, thumbs, base) for k in g.kacheln)
            gezeigt += len(g.kacheln)
            reihe.append(f'<div class="traube {g.art}">{kacheln}</div>')
        teile.append(f'<section>{kopf}<div class="reihe">{"".join(reihe)}</div>'
                     f"</section>")

    gross = int(round(thumb * 0.85))
    klein = int(round(thumb * 0.42))
    stil = (f":root {{ --gross:{gross}px; --klein:{klein}px; }}" + _CSS)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Kontaktbogen</title>\n"
        f"<style>{stil}</style>\n</head>\n<body>\n"
        + _kopf_html(sel, quelle, gezeigt) + "\n"
        + "\n".join(teile)
        + "\n<footer><span id=\"stand\">nichts markiert</span>"
          '<button type="button" onclick="herunterladen()">Aenderungen '
          "speichern</button>"
          '<button type="button" onclick="kopieren()">Zeilen kopieren</button>'
          '<span class="meta">Einspielen mit <code>slideshow order --apply '
          "auswahl.txt</code> — der Bogen selbst schreibt nichts.</span>"
          "</footer>\n"
          '<pre id="zettel"></pre>\n'
        f"<script>{_JS}</script>\n</body>\n</html>\n")


def sheet_media(sel: Selection, manifest: Manifest, *,
                nur_auswahl: bool = False) -> list[MediaItem]:
    """Die Medien, die auf dem Bogen erscheinen — und nur die brauchen ein
    Thumbnail."""
    by_id = {m.id: m for m in manifest.media}
    out: dict[str, MediaItem] = {}
    for a in build_sections(sel, manifest, nur_auswahl=nur_auswahl):
        for g in a.gruppen:
            for k in g.kacheln:
                out.setdefault(k.m.id, by_id.get(k.m.id, k.m))
    return list(out.values())
