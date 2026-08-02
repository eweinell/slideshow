"""Anbindung der Titelfolien an den Renderpfad.

Geprueft wird, was **ohne** den Generator schon feststeht: dass die Folie auf
der Normalform gebacken wird, dass ein Projekt ohne Titel keine Schrift
braucht, dass der Hintergrund aus dem Original kommt und nicht aus dem
Zwischenprodukt, und dass ein fehlendes Asset die richtige Diagnose bekommt.

``render_title`` selbst wirft derzeit noch (Stufe 1 des Briefings ist offen).
Die Tests hier fassen es deshalb nur ueber ``--dry-run`` an oder pruefen die
Wege davor und danach.
"""

from __future__ import annotations

import pytest

from slideshow.build import check_sources_exist
from slideshow.errors import SchemaError, SlideshowError
from slideshow.models import Chapter, TitleSegment
from slideshow.paths import Project
from slideshow.preprocess import (LONG_EDGE, ensure_title_assets, title_canvas,
                                  _titel_hintergrund)
from slideshow.proc import DryRun
from slideshow.titles import title_asset

from .test_titles import _bauen, _beat_region, _manifest


@pytest.fixture
def titelprojekt(tmp_path):
    """Ein gebautes Projekt mit einer Titelfolie, samt Dateien auf der Platte."""
    project = Project.open(tmp_path / "proj", create=True)
    project.ensure_dirs()
    manifest = _manifest()
    for m in manifest.media:
        (project.root / m.path).parent.mkdir(parents=True, exist_ok=True)
        (project.root / m.path).write_bytes(b"original")
        project.abs(m.cache_path).write_bytes(b"zwischenprodukt")
    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")])
    return {"project": project, "edit": edit, "manifest": manifest, "plan": plan}


# --------------------------------------------------------------------------
# Normalform
# --------------------------------------------------------------------------

def test_die_folie_wird_auf_der_normalform_gebacken():
    """Nicht in Ausgabegroesse.

    Der Text ist in die Pixel eingebrannt und wird von der Ken-Burns-Fahrt bis
    zu 1,3-fach vergroessert. Nur mit dem Subpixel-Vorrat bleibt er dabei
    ueberabgetastet; in 4K gebacken wuerde er weich.
    """
    assert title_canvas((3840, 2160)) == (LONG_EDGE, 4320)
    assert title_canvas((1920, 1080)) == (LONG_EDGE, 4320)


def test_assetpfad_haengt_nicht_an_der_ausgabegroesse():
    """Dasselbe Projekt in 4K und in 1080p teilt sich die Datei — die Pixel
    sind dieselben, nur die Skalierung beim Rendern unterscheidet sich."""
    vierk, _p, _c = _bauen(_manifest(), [_beat_region()],
                           [Chapter(before="img_005", title="Malmoe")])
    folie = next(s for s in vierk.segments if isinstance(s, TitleSegment))
    assert title_asset(folie, vierk.defaults, title_canvas((3840, 2160))) == \
        title_asset(folie, vierk.defaults, title_canvas((1920, 1080)))


# --------------------------------------------------------------------------
# ensure_title_assets
# --------------------------------------------------------------------------

def test_ein_projekt_ohne_titel_braucht_keine_schrift(tmp_path, monkeypatch):
    """Sonst scheiterte jedes gewoehnliche Projekt an einer Abhaengigkeit, die
    es gar nicht benutzt."""
    monkeypatch.setenv("SLIDESHOW_FONT", str(tmp_path / "gibtsnicht.ttf"))
    project = Project.open(tmp_path / "proj", create=True)
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()], [])

    stats = ensure_title_assets(project, edit, None)
    assert (stats.erzeugt, stats.aus_cache) == (0, 0)


def test_fehlende_schrift_meldet_sich_mit_installationsbefehl(titelprojekt, monkeypatch):
    monkeypatch.setenv("SLIDESHOW_FONT", str(titelprojekt["project"].root / "keine.ttf"))
    with pytest.raises(SlideshowError) as exc:
        ensure_title_assets(titelprojekt["project"], titelprojekt["edit"],
                            titelprojekt["manifest"])
    assert "SLIDESHOW_FONT" in str(exc.value)


def test_dry_run_erzeugt_nichts(titelprojekt):
    dry = DryRun(enabled=True)
    stats = ensure_title_assets(titelprojekt["project"], titelprojekt["edit"],
                                titelprojekt["manifest"], dry=dry)
    assert stats.erzeugt == 0
    assert dry.commands, "der geplante Schritt muss im Dry-Run auftauchen"
    assert "Malmoe" in " ".join(dry.commands[0])


def test_der_generator_meldet_sich_verstaendlich(titelprojekt):
    """Solange Stufe 1 offen ist, ist das die ehrlichste Antwort — kein
    Traceback und kein leeres Bild."""
    with pytest.raises(SlideshowError) as exc:
        ensure_title_assets(titelprojekt["project"], titelprojekt["edit"],
                            titelprojekt["manifest"])
    assert "Titelgenerator" in str(exc.value)


# --------------------------------------------------------------------------
# Hintergrundquelle
# --------------------------------------------------------------------------

def test_hintergrund_kommt_aus_dem_original(titelprojekt):
    """Nicht aus ``cache/``: dort ist ein Hochformat bereits ein Blur-Komposit,
    und ein zweiter Blur ergaebe einen verwaschenen Rahmen um ein leicht
    verwaschenes Hochformat."""
    p, manifest = titelprojekt["project"], titelprojekt["manifest"]
    seg = TitleSegment(title="Malmoe", bg="cache/img_005.jpg")
    quelle, digest, hinweis = _titel_hintergrund(p, manifest, seg)

    assert quelle == p.abs("src/img_005.jpg")
    assert quelle.read_bytes() == b"original"
    assert digest and not hinweis


def test_ohne_manifest_bleibt_das_zwischenprodukt_mit_hinweis(titelprojekt):
    p = titelprojekt["project"]
    seg = TitleSegment(title="Malmoe", bg="cache/img_005.jpg")
    quelle, digest, hinweis = _titel_hintergrund(p, None, seg)

    assert quelle == p.abs("cache/img_005.jpg")
    assert digest
    assert "doppelt unscharf" in hinweis


def test_farbflaeche_braucht_keine_quelle(titelprojekt):
    quelle, digest, hinweis = _titel_hintergrund(
        titelprojekt["project"], titelprojekt["manifest"],
        TitleSegment(title="Auftakt", bg="#1b2a3a"))
    assert (quelle, digest, hinweis) == (None, "", "")


def test_fehlendes_hintergrundbild_ist_kein_absturz(titelprojekt):
    quelle, _digest, hinweis = _titel_hintergrund(
        titelprojekt["project"], None, TitleSegment(title="X", bg="cache/weg.jpg"))
    assert quelle is None
    assert "fehlt" in hinweis


# --------------------------------------------------------------------------
# Diagnose
# --------------------------------------------------------------------------

def test_fehlendes_asset_wird_als_erzeugnis_gemeldet(titelprojekt):
    """"Datei fehlt" waere die falsche Diagnose — man suchte nach etwas, das es
    nie gab. Der Schritt ist nicht gelaufen, das ist die Aussage."""
    with pytest.raises(SchemaError) as exc:
        check_sources_exist(titelprojekt["project"], titelprojekt["edit"])
    assert "Titelasset fehlt" in str(exc.value)
    assert exc.value.path.endswith(".title")


def test_vorhandenes_asset_laesst_die_pruefung_durch(titelprojekt):
    p, edit = titelprojekt["project"], titelprojekt["edit"]
    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    p.abs(title_asset(folie, edit.defaults,
                      title_canvas(tuple(edit.size)))).write_bytes(b"jpeg")
    check_sources_exist(p, edit)
