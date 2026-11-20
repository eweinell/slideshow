"""Bildersammlung (``slideshow export-jpg``).

Geprueft wird, was die Sammlung von einem einfachen Export unterscheidet: sie
folgt der Edit-List, behaelt das Seitenverhaeltnis der Aufnahme, steht aufrecht,
traegt korrigierte Metadaten, und ein zweiter Lauf kodiert nur, was sich am
Inhalt geaendert hat — eine verschobene Position ist eine Umbenennung.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from slideshow.errors import SlideshowError
from slideshow.models import ClipSegment, StillSegment, TitleSegment
from slideshow.proc import DryRun, have, run
from slideshow.stills import (INDEX_NAME, Eintrag, benenne, export_stills,
                              sammlung, verschiebung, zielgroesse)

from .conftest import requires_ffmpeg

requires_exiftool = pytest.mark.skipif(not have("exiftool"), reason="exiftool fehlt")


def _eintrag(pfad: Path, **kw) -> Eintrag:
    return Eintrag(art="bild", quelle=pfad, stamm=pfad.stem, **kw)


def _export(project, eintraege, ordner, **kw):
    benenne(eintraege)
    kw.setdefault("langkante", 640)
    return export_stills(project, eintraege, ordner, **kw)


def _tags(pfad: Path, *tags: str) -> dict:
    res = run(["exiftool", "-j", "-n", "-a", "-G1", *tags, str(pfad)])
    daten = json.loads(res.stdout)[0]
    daten.pop("SourceFile", None)
    return daten


@pytest.fixture
def quer(images, tmp_path) -> list[Path]:
    """Drei Querformate als eigene Kopien — die Session-Fixture bleibt unberuehrt."""
    ziel = tmp_path / "orig"
    ziel.mkdir()
    return [Path(shutil.copy(p, ziel / p.name)) for p in images[:3]]


# --------------------------------------------------------------------------
# Groesse und Benennung
# --------------------------------------------------------------------------

def test_die_langkante_wird_skaliert_und_das_seitenverhaeltnis_bleibt():
    assert zielgroesse(6000, 4000, 3840) == (3840, 2560)
    assert zielgroesse(4000, 6000, 3840) == (2560, 3840)
    assert zielgroesse(12000, 3000, 3840) == (3840, 960)


def test_kleine_originale_werden_nicht_vergroessert():
    assert zielgroesse(1600, 1200, 3840) == (1600, 1200)
    assert zielgroesse(3840, 2160, 3840) == (3840, 2160)


def test_die_nummer_hat_genug_stellen_fuer_die_alphabetische_sortierung(tmp_path):
    wenige = [_eintrag(tmp_path / f"b{i}.jpg") for i in range(12)]
    benenne(wenige)
    assert wenige[0].name == "001_b0.jpg" and wenige[11].name == "012_b11.jpg"

    viele = [_eintrag(tmp_path / f"b{i}.jpg") for i in range(1200)]
    benenne(viele)
    assert viele[0].name == "0001_b0.jpg"
    assert sorted(e.name for e in viele) == [e.name for e in viele]


def test_der_uhren_offset_wird_zur_exiftool_verschiebung():
    assert verschiebung(3600) == "+0:0:0 1:0:0"
    assert verschiebung(-3725) == "-0:0:0 1:2:5"
    assert verschiebung(90061) == "+0:0:1 1:1:1"


# --------------------------------------------------------------------------
# Auswahl aus der Edit-List
# --------------------------------------------------------------------------

@requires_ffmpeg
def test_die_sammlung_folgt_der_edit_list(built):
    project, edit, manifest = built["project"], built["edit"], built["manifest"]
    eintraege, hinweise = sammlung(project, edit, manifest)
    assert not hinweise

    erwartet = [manifest.by_cache_path(s.src).id for s in edit.segments
                if isinstance(s, StillSegment)]
    assert [e.item.id for e in eintraege] == erwartet
    assert all(e.quelle == project.abs(e.item.path) for e in eintraege), \
        "Quelle muss das Original sein, nicht das 16:9-Zwischenprodukt"


@requires_ffmpeg
def test_ein_doppelt_gezeigtes_bild_steht_nur_einmal_darin_und_clips_fehlen(built):
    project, edit, manifest = built["project"], built["edit"], built["manifest"]
    stills = [s for s in edit.segments if isinstance(s, StillSegment)]
    segmente = [stills[0], stills[1], stills[0],
                ClipSegment(src="cache/clip_x.mov"), stills[2]]
    eintraege, _ = sammlung(project, edit.model_copy(update={"segments": segmente}),
                            manifest)
    assert [e.item.cache_path for e in eintraege] == [s.src for s in stills[:3]]
    assert [e.name.split("_")[0] for e in eintraege] == ["001", "002", "003"]


@requires_ffmpeg
def test_titelfolien_kommen_nur_mit_dem_schalter_hinein(built):
    from slideshow.preprocess import ensure_title_assets

    project, edit, manifest = built["project"], built["edit"], built["manifest"]
    folie = TitleSegment(title="Malmö", subtitle=None, bg="#203040")
    edit = edit.model_copy(update={"segments": [folie, *edit.segments]})
    ensure_title_assets(project, edit, manifest)

    ohne, _ = sammlung(project, edit, manifest)
    assert all(e.art == "bild" for e in ohne)

    mit, hinweise = sammlung(project, edit, manifest, titles=True)
    assert not hinweise
    # Ohne Umlaut im Dateinamen: FAT32-Sticks und TV-Mediaplayer danken es.
    assert mit[0].art == "titel" and mit[0].name == "001_titel_malmo.jpg"
    assert len(mit) == len(ohne) + 1

    stats = export_stills(project, mit[:1], project.out / "bilder", langkante=640)
    assert not stats.fehler
    with Image.open(project.out / "bilder" / mit[0].name) as img:
        assert img.size == (640, 360), "die Folie hat das Format des Films"


# --------------------------------------------------------------------------
# Pixel
# --------------------------------------------------------------------------

def test_hochformat_steht_aufrecht_und_behaelt_sein_format(project, images, tmp_path):
    quelle = next(p for p in images if "portrait" in p.name)
    ordner = tmp_path / "bilder"
    stats = _export(project, [_eintrag(quelle)], ordner)
    assert not stats.fehler, stats.fehler

    with Image.open(ordner / "001_portrait_o6.jpg") as img:
        if have("exiftool"):
            # Die Fixture ist quer gespeichert und per Orientation 6 hochkant.
            assert img.size == (400, 640)
        assert img.getexif().get(0x0112, 1) == 1
        assert img.info.get("icc_profile"), "ohne sRGB-Profil raet der Betrachter"


def test_querformat_wird_nicht_auf_16_zu_9_beschnitten(project, quer, tmp_path):
    ordner = tmp_path / "bilder"
    _export(project, [_eintrag(quer[0])], ordner)
    with Image.open(ordner / f"001_{quer[0].stem}.jpg") as img:
        assert img.size == (640, 400)     # 800x500 der Fixture, also 16:10


# --------------------------------------------------------------------------
# Metadaten
# --------------------------------------------------------------------------

@pytest.fixture
def mit_exif(quer):
    """Ein Original mit allem, was korrigiert oder entfernt werden muss."""
    pfad = quer[0]
    run(["exiftool", "-q", "-overwrite_original",
         "-Make=SONY", "-Model=ILCE-6700", "-Artist=Erika Muster",
         "-DateTimeOriginal=2026:07:24 23:30:00", "-CreateDate=2026:07:24 23:30:00",
         "-XMP-photoshop:DateCreated=2026:07:24 23:30:00",
         "-GPSLatitude=59.33", "-GPSLatitudeRef=N", "-GPSLongitude=18.06",
         "-GPSLongitudeRef=E", "-XMP-exif:GPSLatitude=59.33N",
         "-SerialNumber=4711", "-ExifImageWidth=6192", "-ExifImageHeight=4128",
         "-ColorSpace#=65535", "-XMP-tiff:Orientation#=1", str(pfad)])
    return pfad


@requires_exiftool
def test_metadaten_werden_uebernommen_und_korrigiert(project, mit_exif, tmp_path):
    ordner = tmp_path / "bilder"
    stats = _export(project, [_eintrag(mit_exif, offset=4500.0)], ordner)
    assert not stats.fehler, stats.fehler

    t = _tags(ordner / f"001_{mit_exif.stem}.jpg")
    assert t["IFD0:Make"] == "SONY" and t["IFD0:Model"] == "ILCE-6700"
    assert t["IFD0:Artist"] == "Erika Muster"
    # Uhren-Offset +1:15 ueber Mitternacht, in EXIF und XMP gleichermassen.
    assert t["ExifIFD:DateTimeOriginal"] == "2026:07:25 00:45:00"
    assert t["ExifIFD:CreateDate"] == "2026:07:25 00:45:00"
    assert t["XMP-photoshop:DateCreated"].startswith("2026:07:25 00:45:00")
    assert (t["ExifIFD:ExifImageWidth"], t["ExifIFD:ExifImageHeight"]) == (640, 400)
    assert t["ExifIFD:ColorSpace"] == 1
    assert t["IFD0:Orientation"] == 1
    assert "XMP-tiff:Orientation" not in t
    assert "ExifIFD:SerialNumber" not in t
    assert t["IFD0:Software"] == "slideshow export-jpg"
    assert not any(k.startswith("IFD1:") for k in t), "Vorschau des Originals"
    assert abs(t["GPS:GPSLatitude"] - 59.33) < 1e-6


@requires_exiftool
def test_ohne_offset_bleibt_die_aufnahmezeit_stehen(project, mit_exif, tmp_path):
    ordner = tmp_path / "bilder"
    _export(project, [_eintrag(mit_exif)], ordner)
    t = _tags(ordner / f"001_{mit_exif.stem}.jpg", "-DateTimeOriginal")
    assert t["ExifIFD:DateTimeOriginal"] == "2026:07:24 23:30:00"


@requires_exiftool
def test_no_gps_entfernt_die_koordinaten_aus_exif_und_xmp(project, mit_exif, tmp_path):
    ordner = tmp_path / "bilder"
    stats = _export(project, [_eintrag(mit_exif)], ordner, gps=False)
    assert not stats.fehler, stats.fehler
    t = _tags(ordner / f"001_{mit_exif.stem}.jpg")
    assert not [k for k in t if "GPS" in k], t
    assert t["IFD0:Make"] == "SONY", "nur GPS faellt weg, nicht der Rest"


# --------------------------------------------------------------------------
# Abgleich mit dem Ausgabeordner
# --------------------------------------------------------------------------

def _inhalt(ordner: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(ordner.glob("*.jpg"))}


def test_eine_neue_reihenfolge_benennt_um_statt_neu_zu_kodieren(project, quer, tmp_path):
    ordner = tmp_path / "bilder"
    a, b, c = quer
    _export(project, [_eintrag(a), _eintrag(b), _eintrag(c)], ordner)
    vorher = _inhalt(ordner)

    stats = _export(project, [_eintrag(c), _eintrag(a), _eintrag(b)], ordner)
    assert (stats.neu, stats.umbenannt, stats.geloescht) == (0, 3, 0)
    nachher = _inhalt(ordner)
    assert nachher[f"001_{c.stem}.jpg"] == vorher[f"003_{c.stem}.jpg"]
    assert nachher[f"002_{a.stem}.jpg"] == vorher[f"001_{a.stem}.jpg"]
    assert len(nachher) == 3


def test_ein_tausch_zweier_bilder_ueberschreibt_nichts(project, quer, tmp_path):
    """Gleiche Staemme, damit die Zielnamen genau die alten Namen sind."""
    ordner = tmp_path / "bilder"
    a, b = quer[:2]
    ea, eb = Eintrag("bild", a, "x"), Eintrag("bild", b, "x")
    benenne([ea, eb])
    export_stills(project, [ea, eb], ordner, langkante=640)
    vorher = _inhalt(ordner)

    ea2, eb2 = Eintrag("bild", a, "x"), Eintrag("bild", b, "x")
    benenne([eb2, ea2])
    stats = export_stills(project, [eb2, ea2], ordner, langkante=640)
    assert (stats.neu, stats.umbenannt) == (0, 2)
    nachher = _inhalt(ordner)
    assert nachher["001_x.jpg"] == vorher["002_x.jpg"]
    assert nachher["002_x.jpg"] == vorher["001_x.jpg"]


def test_ein_herausgenommenes_bild_verschwindet_aus_dem_ordner(project, quer, tmp_path):
    ordner = tmp_path / "bilder"
    a, b, c = quer
    _export(project, [_eintrag(a), _eintrag(b), _eintrag(c)], ordner)
    stats = _export(project, [_eintrag(a), _eintrag(c)], ordner)
    assert (stats.unveraendert, stats.umbenannt, stats.geloescht, stats.neu) == (1, 1, 1, 0)
    assert set(_inhalt(ordner)) == {f"001_{a.stem}.jpg", f"002_{c.stem}.jpg"}


def test_ein_geaenderter_parameter_kodiert_neu(project, quer, tmp_path):
    ordner = tmp_path / "bilder"
    _export(project, [_eintrag(quer[0])], ordner)
    stats = _export(project, [_eintrag(quer[0])], ordner, langkante=320)
    assert (stats.neu, stats.unveraendert) == (1, 0)
    with Image.open(ordner / f"001_{quer[0].stem}.jpg") as img:
        assert img.size == (320, 200)


def test_fremde_dateien_bleiben_unberuehrt(project, quer, tmp_path):
    ordner = tmp_path / "bilder"
    ordner.mkdir()
    (ordner / "notizen.txt").write_text("von Hand", encoding="utf-8")
    _export(project, [_eintrag(quer[0])], ordner)
    _export(project, [_eintrag(quer[1])], ordner)
    assert (ordner / "notizen.txt").read_text(encoding="utf-8") == "von Hand"
    assert set(_inhalt(ordner)) == {f"001_{quer[1].stem}.jpg"}


def test_eine_fremde_datei_unter_dem_zielnamen_bricht_ab(project, quer, tmp_path):
    ordner = tmp_path / "bilder"
    ordner.mkdir()
    fremd = ordner / f"001_{quer[0].stem}.jpg"
    fremd.write_bytes(b"nicht von uns")
    with pytest.raises(SlideshowError, match="nicht von ihm"):
        _export(project, [_eintrag(quer[0])], ordner)
    assert fremd.read_bytes() == b"nicht von uns"


def test_ein_abgebrochener_lauf_hinterlaesst_keinen_muell(project, quer, tmp_path):
    ordner = tmp_path / "bilder"
    ordner.mkdir()
    (ordner / f".~{quer[0].stem}.jpg").write_bytes(b"halb")
    _export(project, [_eintrag(quer[0])], ordner)
    assert not list(ordner.glob(".~*"))
    assert json.loads((ordner / INDEX_NAME).read_text(encoding="utf-8"))["dateien"]


def test_dry_run_schreibt_nichts(project, quer, tmp_path):
    ordner = tmp_path / "bilder"
    dry = DryRun(enabled=True)
    stats = _export(project, [_eintrag(quer[0])], ordner, dry=dry)
    assert stats.neu == 1 and dry.commands
    assert not ordner.exists()
