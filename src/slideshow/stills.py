"""Bildersammlung: die Standbilder des Films als JPEGs zum Durchblaettern.

Ein zweiter Ausgabeweg neben ``master.mp4``. Was hineinkommt und in welcher
Reihenfolge, entscheidet allein ``edit.yaml`` (Invariante 1) — nicht
``order.yaml``: nur die Edit-List weiss, welche Bilder tatsaechlich im Film
stehen. Die Reihenfolge traegt der Dateiname (``0001_DSC06273.jpg``).

**Quelle ist das Original, nicht ``cache/``.** Das Zwischenprodukt ist auf
die 16:9-Normalform beschnitten oder als Hochformat-Komposit mit unscharfem
Rand gebaut; die Sammlung soll aber das Seitenverhaeltnis der Aufnahme
behalten. Aufrichten und Farbraum laufen ueber denselben Codepfad wie das
Preprocessing (:func:`slideshow.preprocess.load_upright_srgb`).

**Metadaten** kopiert exiftool aus dem Original und korrigiert, was nach dem
Umrechnen falsch waere (:func:`exif_argumente`): Orientation, Pixelmasse,
Farbraum, eingebettete Vorschauen. Die Aufnahmezeit wird um den Uhren-Offset
der Kamera korrigiert — dieselbe Zeit, nach der die Slideshow sortiert.

**Idempotent.** Der Inhaltsschluessel einer Datei haengt an Original und
Parametern, nicht an ihrer Position. Rueckt ein Bild nur einen Platz weiter,
wird es umbenannt, nicht neu kodiert. Der Index ``.slideshow-bilder.json``
im Ausgabeordner haelt fest, welche Dateien dieses Werkzeug geschrieben hat;
nur die werden je umbenannt oder geloescht.
"""

from __future__ import annotations

import concurrent.futures as _fut
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .cache import HashIndex, cache_key
from .errors import SlideshowError
from .models import EditList, Manifest, MediaItem, StillSegment, TitleSegment
from .paths import Project
from .proc import DryRun, have, run

log = logging.getLogger("slideshow.stills")

#: Version der Exportlogik — geht in jeden Inhaltsschluessel ein. Hochzaehlen,
#: wenn sich Pixel oder Metadaten bei gleichen Parametern aendern.
STILLS_VERSION = 1

#: Name des Index im Ausgabeordner.
INDEX_NAME = ".slideshow-bilder.json"

#: Praefix halbfertiger Dateien. Ein abgebrochener Lauf hinterlaesst sie; der
#: naechste raeumt sie weg, bevor er plant.
_TEMP_PRAEFIX = ".~"

QUALITY = 92

#: Software-Tag der erzeugten Dateien.
SOFTWARE = "slideshow export-jpg"


# --------------------------------------------------------------------------
# Was in die Sammlung gehoert
# --------------------------------------------------------------------------

@dataclass
class Eintrag:
    """Eine Datei der Sammlung, noch ohne Nummer im Namen."""

    #: ``bild`` | ``titel``
    art: str
    #: Datei, aus der die Pixel kommen: das Original bzw. das Titelasset.
    quelle: Path
    #: Namensteil hinter der Nummer, ohne Endung.
    stamm: str
    item: MediaItem | None = None
    #: Uhren-Offset der Kamera in Sekunden (nur Bilder).
    offset: float = 0.0
    #: Wird von :func:`benenne` gesetzt.
    name: str = ""


def sammlung(project: Project, edit: EditList, manifest: Manifest, *,
             titles: bool = False) -> tuple[list[Eintrag], list[str]]:
    """Die Eintraege der Sammlung in Filmreihenfolge, dazu Hinweise.

    Ein Bild, das der Film zweimal zeigt, steht nur beim ersten Vorkommen in
    der Sammlung — beim Blaettern wirkt die Wiederholung wie ein Fehler, im
    Film ist sie gewollt. Clips bleiben draussen.

    Fehlt das Original eines Bildes, faellt es heraus und wird gemeldet. Das
    Zwischenprodukt aus ``cache/`` waere zwar da, hat aber das Format des
    Films statt das der Aufnahme — genau das, was diese Ausgabe nicht ist.
    """
    from .preprocess import title_canvas
    from .titles import _slug, resolved, title_asset

    eintraege: list[Eintrag] = []
    hinweise: list[str] = []
    gesehen: set[str] = set()
    canvas = title_canvas(tuple(edit.size))

    for idx, seg in enumerate(edit.segments):
        if isinstance(seg, StillSegment):
            if seg.src in gesehen:
                continue
            gesehen.add(seg.src)
            item = manifest.by_cache_path(seg.src)
            if item is None:
                hinweise.append(f"segments[{idx}]: {seg.src} steht nicht im Manifest "
                                f"— ausgelassen.")
                continue
            original = project.abs(item.path)
            if not original.exists():
                hinweise.append(f"segments[{idx}]: Original zu {item.id} fehlt "
                                f"({original}) — ausgelassen.")
                continue
            eintraege.append(Eintrag(
                art="bild", quelle=original, stamm=original.stem, item=item,
                offset=manifest.clock_offsets.get(item.camera, 0.0)))
        elif isinstance(seg, TitleSegment) and titles:
            asset = project.abs(title_asset(resolved(edit.segments, idx),
                                            edit.defaults, canvas))
            if not asset.exists():
                hinweise.append(f"segments[{idx}]: Titelfolie {seg.title!r} ist nicht "
                                f"gebacken ({asset}) — ausgelassen.")
                continue
            eintraege.append(Eintrag(art="titel", quelle=asset,
                                     stamm=f"titel_{_slug(seg.title)}"))
    benenne(eintraege)
    return eintraege, hinweise


def benenne(eintraege: list[Eintrag]) -> None:
    """Laufende Nummer plus Stamm, mindestens dreistellig.

    Die Stellenzahl richtet sich nach der Anzahl, damit die Namen in jedem
    Dateibrowser auch alphabetisch richtig sortiert stehen — ``10_`` vor
    ``2_`` ist genau der Fehler, den die Benennung verhindern soll.
    """
    stellen = max(3, len(str(len(eintraege))))
    for nr, e in enumerate(eintraege, start=1):
        e.name = f"{nr:0{stellen}d}_{e.stamm}.jpg"


# --------------------------------------------------------------------------
# Groesse und Metadaten
# --------------------------------------------------------------------------

def zielgroesse(breite: int, hoehe: int, langkante: int) -> tuple[int, int]:
    """Auf ``langkante`` skaliert, Seitenverhaeltnis erhalten, nie vergroessert.

    Langkante statt Einpassen in die 16:9-Box: ein Hochformat bekaeme sonst
    nur 1440 x 2160 Pixel — auf dem Fernseher dasselbe, auf Tablet und Handy
    sichtbar duenner. Hochgerechnet wird nie; das ergaebe nur groessere
    Dateien ohne ein Detail mehr.
    """
    lang = max(breite, hoehe)
    if lang <= langkante:
        return (breite, hoehe)
    f = langkante / lang
    return (max(1, round(breite * f)), max(1, round(hoehe * f)))


def verschiebung(offset: float) -> str:
    """Uhren-Offset in Sekunden als exiftool-Verschiebung ``+0:0:D H:M:S``."""
    vorzeichen = "-" if offset < 0 else "+"
    rest = int(round(abs(offset)))
    tage, rest = divmod(rest, 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    return f"{vorzeichen}0:0:{tage} {h}:{m}:{s}"


#: Datumsfelder, die der Uhren-Offset korrigiert. ``ModifyDate`` bewusst
#: nicht: es ist der Zeitpunkt der Bearbeitung, nicht der Aufnahme.
_DATUMSFELDER = ("ExifIFD:DateTimeOriginal", "ExifIFD:CreateDate",
                 "XMP-exif:DateTimeOriginal", "XMP-xmp:CreateDate",
                 "XMP-photoshop:DateCreated")

#: Was aus dem Original *nicht* mitkommt. Jede Zeile hat einen Grund:
_NICHT_KOPIEREN = (
    # Herstellerdaten: gross, enthalten bei Sony eigene Vorschaubilder samt
    # Offsets und nach dem Umschreiben keine verwertbare Information.
    "--MakerNotes:all", "--MakerNotes",
    # Eingebettete Vorschauen zeigen das Original in alter Ausrichtung und
    # altem Format — ein Betrachter, der die Vorschau zeigt, zeigt Falsches.
    "--IFD1:all", "--ThumbnailImage", "--PreviewImage", "--JpgFromRaw",
    "--OtherImage",
    # Eingebrannt: ein stehengebliebenes Tag dreht ein zweites Mal.
    "--Orientation",
    # Das Bild ist nach sRGB umgerechnet; das Profil bettet Pillow ein.
    "--ICC_Profile:all", "--ColorSpace", "--InteropIFD:all",
    # Masse des Originals.
    "--ExifImageWidth", "--ExifImageHeight", "--ImageWidth", "--ImageHeight",
    # Bearbeitungsverlauf und Gesichtsregionen. Die Regionen beziehen sich
    # auf die ungedrehte Ausrichtung des Originals.
    "--XMP-crs:all", "--XMP-xmpMM:all", "--XMP-mwg-rs:all", "--XMP-MP:all",
    # Seriennummern und Besitzer: die Sammlung ist zum Weitergeben gedacht.
    "--SerialNumber", "--InternalSerialNumber", "--LensSerialNumber",
    "--OwnerName", "--CameraOwnerName",
    "--Software",
)


def exif_argumente(e: Eintrag, ziel: Path, breite: int, hoehe: int, *,
                   gps: bool = True) -> list[str]:
    """Ein exiftool-Kommando (ohne ``-execute``) fuer eine Datei der Sammlung."""
    args = ["-tagsFromFile", str(e.quelle), "-all:all", *_NICHT_KOPIEREN]
    if not gps:
        # Der Platzhalter ueber den Namen erfasst EXIF-GPS und XMP-exif:GPS*
        # zugleich. ``--GPS:all`` liess in der Messung ``GPSVersionID`` stehen.
        args.append("--GPS*")
    if e.offset:
        v = verschiebung(e.offset)
        for feld in _DATUMSFELDER:
            args.append(f'-{feld}<${{{feld};ShiftTime("{v}")}}')
    args += ["-IFD0:Orientation#=1",
             f"-ExifIFD:ExifImageWidth={breite}",
             f"-ExifIFD:ExifImageHeight={hoehe}",
             "-ExifIFD:ColorSpace#=1",
             f"-Software={SOFTWARE}",
             str(ziel)]
    return args


def schreibe_metadaten(kommandos: list[list[str]]) -> list[str]:
    """Alle Kommandos in *einem* exiftool-Lauf. Gibt Fehlermeldungen zurueck.

    Ueber eine Argumentdatei mit ``-execute`` zwischen den Bloecken: ein
    Prozess je Bild kostete bei tausend Bildern Minuten, und die Kommandozeile
    reisst unter Windows ab 193 Dateien (siehe ``probe.read_exif_batch``).
    """
    if not kommandos:
        return []
    zeilen: list[str] = []
    for i, k in enumerate(kommandos):
        if i:
            zeilen.append("-execute")
        zeilen += k
    with tempfile.TemporaryDirectory(prefix="slideshow-stills-") as tmp:
        argfile = Path(tmp) / "metadaten.args"
        argfile.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        # -q -q unterdrueckt die Warnungen, die ein fehlendes Datumsfeld in
        # ShiftTime ausloest — die meisten Bilder haben nur einen Teil davon.
        # Fehler bleiben sichtbar. -charset gehoert in die common_args: dort
        # gilt es fuer jeden Block, davor nur fuer den ersten.
        res = run(["exiftool", "-@", str(argfile), "-common_args",
                   "-charset", "filename=utf8", "-overwrite_original", "-m",
                   "-q", "-q"], check=False, timeout=1800)
    fehler = [z for z in res.stderr.splitlines() if z.strip()]
    if res.returncode != 0 and not fehler:
        fehler = [f"exiftool endete mit Code {res.returncode}"]
    return fehler


# --------------------------------------------------------------------------
# Pixel
# --------------------------------------------------------------------------

def _srgb_profil() -> bytes:
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def erzeuge_jpeg(quelle: Path, ziel: Path, *, langkante: int,
                 quality: int = QUALITY) -> tuple[int, int]:
    """Aufrecht, sRGB, auf die Langkante, als JPEG mit eingebettetem Profil."""
    from PIL import Image

    from .preprocess import load_upright_srgb

    im = load_upright_srgb(quelle)
    w, h = zielgroesse(im.width, im.height, langkante)
    if (w, h) != (im.width, im.height):
        im = im.resize((w, h), Image.LANCZOS)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    # 4:4:4 wie im Cache: Farbkanten (rote Schrift, Laub vor Himmel) bleiben
    # sonst sichtbar weich, und die Datei waechst nur um gut ein Zehntel.
    im.save(ziel, format="JPEG", quality=quality, subsampling=0, optimize=True,
            progressive=False, icc_profile=_srgb_profil())
    return (w, h)


def _erzeuge_einen(args) -> tuple[str, tuple[int, int] | None, str | None]:
    """Worker fuer den Prozesspool (Pillow gibt das GIL nicht her)."""
    name, quelle, ziel, langkante, quality = args
    try:
        return (name, erzeuge_jpeg(Path(quelle), Path(ziel), langkante=langkante,
                                   quality=quality), None)
    except Exception as exc:                        # noqa: BLE001
        return (name, None, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Abgleich mit dem Ausgabeordner
# --------------------------------------------------------------------------

@dataclass
class ExportStats:
    neu: int = 0
    umbenannt: int = 0
    unveraendert: int = 0
    geloescht: int = 0
    fehler: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)


def _lies_index(ordner: Path) -> dict[str, str]:
    pfad = ordner / INDEX_NAME
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    dateien = daten.get("dateien", {}) if isinstance(daten, dict) else {}
    # Was der Index kennt, aber nicht mehr da ist, hat jemand von Hand geloescht.
    return {n: k for n, k in dateien.items() if (ordner / n).exists()}


def _schreibe_index(ordner: Path, dateien: dict[str, str]) -> None:
    pfad = ordner / INDEX_NAME
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": STILLS_VERSION,
                               "dateien": dict(sorted(dateien.items()))},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, pfad)


def schluessel(e: Eintrag, quell_hash: str, *, langkante: int, quality: int,
               gps: bool) -> str:
    """Inhaltsschluessel einer Datei — ohne ihre Position in der Folge."""
    params = {"op": "still", "v": STILLS_VERSION, "art": e.art,
              "langkante": langkante, "quality": quality}
    if e.art == "bild":
        params.update(gps=gps, offset=e.offset)
    return cache_key([quell_hash], params)


def export_stills(project: Project, eintraege: list[Eintrag], ordner: Path, *,
                  langkante: int, quality: int = QUALITY, gps: bool = True,
                  jobs: int | None = None, dry: DryRun | None = None) -> ExportStats:
    """Gleicht den Ausgabeordner mit der Sammlung ab.

    Reihenfolge der Schritte: Uebriggebliebenes loeschen, Bewegtes ueber
    Zwischennamen umbenennen, Neues erzeugen. Die Zwischennamen sind noetig,
    weil ein Tausch zweier Bilder sonst eine Datei ueberschreibt, die noch
    gebraucht wird.
    """
    stats = ExportStats()
    trocken = dry is not None and dry.enabled
    hashes = HashIndex(project.cache / "hashindex.json")

    soll: dict[str, str] = {}
    nach_name: dict[str, Eintrag] = {}
    for e in eintraege:
        soll[e.name] = schluessel(e, hashes.file_hash(e.quelle), langkante=langkante,
                                  quality=quality, gps=gps)
        nach_name[e.name] = e
    hashes.save()

    if not trocken:
        ordner.mkdir(parents=True, exist_ok=True)
        for rest in ordner.glob(f"{_TEMP_PRAEFIX}*"):
            rest.unlink(missing_ok=True)
    ist = _lies_index(ordner) if ordner.exists() else {}

    bleibt = {n for n, k in soll.items() if ist.get(n) == k}
    frei = {n: k for n, k in ist.items() if n not in bleibt}
    nach_schluessel: dict[str, list[str]] = {}
    for n, k in sorted(frei.items()):
        nach_schluessel.setdefault(k, []).append(n)

    umbenennen: list[tuple[str, str]] = []
    erzeugen: list[str] = []
    for n, k in soll.items():
        if n in bleibt:
            continue
        kandidaten = nach_schluessel.get(k)
        if kandidaten:
            umbenennen.append((kandidaten.pop(0), n))
        else:
            erzeugen.append(n)
    verwendet = {alt for alt, _ in umbenennen}
    loeschen = [n for n in frei if n not in verwendet]

    # Eine fremde Datei unter einem Zielnamen wird nicht ueberschrieben: der
    # Ordner kann von Hand befuellt sein, und was nicht im Index steht, hat
    # dieses Werkzeug nicht geschrieben.
    belegt = set(loeschen) | verwendet
    fremd = [n for n in soll if n not in bleibt and n not in belegt
             and (ordner / n).exists()]
    if fremd:
        raise SlideshowError(
            f"{len(fremd)} Datei(en) in {ordner} stehen dem Export im Weg und "
            f"stammen nicht von ihm, z. B. {fremd[0]}. Verschieben oder einen "
            f"anderen Ordner waehlen (-o).")

    stats.unveraendert = len(bleibt)
    if trocken:
        for alt in loeschen:
            dry.record(["loeschen", str(ordner / alt)])
        for alt, neu in umbenennen:
            dry.record(["umbenennen", str(ordner / alt), "->", str(ordner / neu)])
        for n in erzeugen:
            dry.record(["<pillow+exiftool>", str(nach_name[n].quelle), "->",
                        str(ordner / n)])
        stats.geloescht, stats.umbenannt, stats.neu = (len(loeschen), len(umbenennen),
                                                       len(erzeugen))
        return stats

    stand = {n: ist[n] for n in bleibt}
    try:
        for alt in loeschen:
            (ordner / alt).unlink(missing_ok=True)
            stats.geloescht += 1
        zwischen = []
        for i, (alt, neu) in enumerate(umbenennen):
            tmp = ordner / f"{_TEMP_PRAEFIX}mv{i}_{neu}"
            os.replace(ordner / alt, tmp)
            zwischen.append((tmp, neu))
        for tmp, neu in zwischen:
            os.replace(tmp, ordner / neu)
            stand[neu] = soll[neu]
            stats.umbenannt += 1

        _erzeuge_alle(ordner, [nach_name[n] for n in erzeugen], soll, stand, stats,
                      langkante=langkante, quality=quality, gps=gps, jobs=jobs)
    finally:
        # Auch nach einem Abbruch: was fertig ist, gehoert ins Verzeichnis,
        # sonst haelt der naechste Lauf es fuer eine fremde Datei.
        _schreibe_index(ordner, stand)
    return stats


def _erzeuge_alle(ordner: Path, neu: list[Eintrag], soll: dict[str, str],
                  stand: dict[str, str], stats: ExportStats, *, langkante: int,
                  quality: int, gps: bool, jobs: int | None) -> None:
    if not neu:
        return
    aufgaben = [(e.name, str(e.quelle), str(ordner / f"{_TEMP_PRAEFIX}{e.name}"),
                 langkante, quality) for e in neu]
    arbeiter = max(1, min(jobs or (os.cpu_count() or 2), len(aufgaben), 8))
    fertig: dict[str, tuple[int, int]] = {}
    with _fut.ProcessPoolExecutor(max_workers=arbeiter) as pool:
        for name, masse, err in pool.map(_erzeuge_einen, aufgaben):
            if err:
                stats.fehler.append(f"{name}: {err}")
                log.error("%s: %s", name, err)
            else:
                fertig[name] = masse

    bilder = [e for e in neu if e.name in fertig and e.art == "bild"]
    if bilder and not have("exiftool"):
        stats.hinweise.append(
            "exiftool fehlt — die Bilder sind aufrecht und in sRGB, tragen aber "
            "keine Metadaten aus dem Original (Kamera, Aufnahmezeit, GPS).")
    elif bilder:
        kommandos = [exif_argumente(e, ordner / f"{_TEMP_PRAEFIX}{e.name}",
                                    *fertig[e.name], gps=gps) for e in bilder]
        for f in schreibe_metadaten(kommandos):
            stats.fehler.append(f"exiftool: {f}")

    for e in neu:
        if e.name not in fertig:
            continue
        os.replace(ordner / f"{_TEMP_PRAEFIX}{e.name}", ordner / e.name)
        stand[e.name] = soll[e.name]
        stats.neu += 1
