"""``bg: auto`` waehlt nach Tragfaehigkeit
(``docs/briefing-titelfolien-hintergrund.md``).

Geprueft wird die **Wahl**, nicht das Aussehen: welches Bild hinter der Folie
landet, wann das erste seinen Vorrang behaelt, und was der Bericht darueber
sagt. Das Aussehen steht in ``test_titles_generator.py``, die Einbettung in
``test_titles.py``.

Die Hintergruende sind einfarbige Flaechen statt Fotos: die Abdunklung ist eine
Funktion der Leuchtdichte unter dem Text, und eine Flaeche legt sie exakt fest.
Mit den Vorgaben hat die Skala genau drei Stufen — Reinweiss traegt den Text bei
0,45, alles ab Grau 200 abwaerts beim Startwert 0,55. Ein Test, der sich eine
Zwischenstufe wuenscht, prueft eine Rechnung, die es nicht gibt.

Die Leinwand wird kleingestellt (``LONG_EDGE``): gemessen wird auf der
Normalform, und 7680 px waeren je Kandidat reine Wartezeit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from slideshow.models import Chapter, Defaults, Manifest, TitleSegment
from slideshow.paths import Project
from slideshow.preprocess import title_canvas, titel_bildquelle
from slideshow.titles import find_font, measure_darkening

from .test_titles import _bauen, _beat_region, _manifest

#: Grauwerte, deren Abdunklung feststeht. Nachgemessen mit ``_fit_darkening``:
#: 255 -> 0,45 (der Rettungsfall), 100 -> 0,55 (der Startwert traegt).
HELL, DUNKEL = 255, 100


@pytest.fixture(autouse=True)
def kleine_leinwand(monkeypatch):
    monkeypatch.setattr("slideshow.preprocess.LONG_EDGE", 1280)


def _projekt(tmp_path: Path, grauwerte: dict[int, int], *,
             n: int = 12) -> tuple[Project, Manifest]:
    """Ein Projekt mit echten Bilddateien; ``grauwerte`` setzt einzelne hell.

    Alles nicht Genannte ist dunkel — die Wahl soll an dem haengen, was der
    Test ausdruecklich sagt.
    """
    project = Project.open(tmp_path / "proj", create=True)
    project.ensure_dirs()
    manifest = _manifest(n)
    for i, m in enumerate(manifest.media):
        v = grauwerte.get(i, DUNKEL)
        original = project.abs(m.path)
        original.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 500), (v, v, v)).save(original, format="JPEG",
                                                     quality=95)
        # Das Zwischenprodukt existiert im echten Projekt auch; die Messung
        # muss trotzdem das Original nehmen (``titel_bildquelle``).
        Image.new("RGB", (400, 250), (0, 0, 0)).save(project.abs(m.cache_path),
                                                     format="JPEG")
    return (project, manifest)


def _folie(edit) -> TitleSegment:
    return next(s for s in edit.segments if isinstance(s, TitleSegment))


def _meldungen(plan, stichwort: str = "Hintergrund") -> list[str]:
    return [w for w in plan.warnings if stichwort in w]


# --------------------------------------------------------------------------
# A1 — der Normalfall bleibt, wie er war
# --------------------------------------------------------------------------

def test_ein_tragfaehiges_erstes_bild_behaelt_seinen_vorrang(tmp_path):
    """Der Kern von Entscheidung E1: die Messwahl ist ein Rettungsweg.

    Traegt das erste Bild des Abschnitts, ist das Ergebnis dasselbe wie ohne
    dieses Briefing — samt Fokusblende, die eine andere Wahl kosten wuerde.
    """
    project, manifest = _projekt(tmp_path, {})
    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")],
                              project=project)

    assert _folie(edit).bg == "cache/img_005.jpg"
    assert not _meldungen(plan)


def test_die_wahl_aendert_die_datei_nicht_gegenueber_dem_stand_davor(tmp_path):
    """A1 woertlich: byte-identisch zum Lauf ohne Messung.

    Gegenprobe zum vorigen Test — dort koennte die Wahl auch zufaellig wieder
    beim ersten Bild landen und dabei etwas anderes verschoben haben.
    """
    from slideshow.models import dump_edit_yaml

    project, manifest = _projekt(tmp_path, {})
    kapitel = [Chapter(before="img_005", title="Malmoe")]
    mit, _p, _c = _bauen(manifest, [_beat_region()], kapitel, project=project)
    ohne, _p, _c = _bauen(_manifest(), [_beat_region()], kapitel)

    assert dump_edit_yaml(mit) == dump_edit_yaml(ohne)


# --------------------------------------------------------------------------
# A2 — der helle Himmel
# --------------------------------------------------------------------------

def test_ein_zu_helles_erstes_bild_verliert_gegen_einen_spaeteren_kandidaten(tmp_path):
    project, manifest = _projekt(tmp_path, {5: HELL})
    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")],
                              project=project)

    assert _folie(edit).bg == "cache/img_006.jpg"
    (meldung,) = _meldungen(plan)
    assert "img_006" in meldung and "img_005" in meldung
    assert "0.55" in meldung and "0.45" in meldung, \
        "beide Faktoren gehoeren in den Bericht, sonst ist die Wahl unbegruendet"
    assert "Fokusblende" in meldung


def test_ohne_die_kopplung_wird_die_fokusblende_nicht_erwaehnt(tmp_path):
    """Steht an der Kapitelstelle ein Clip, gaebe es ohnehin keine Fokusblende
    (``_ist_fokusblende`` verlangt ein Standbild als Folgesegment). Sie als
    Verlust zu melden hiesse, einen Schaden zu behaupten, den es nicht gibt.

    Der Wechsel muss trotzdem stattfinden — sonst prueft der Test nur, dass
    ueberhaupt nichts passiert ist.
    """
    from slideshow.models import ClipInfo

    project, manifest = _projekt(tmp_path, {6: HELL})
    manifest.media[5].kind = "clip"
    manifest.media[5].clip = ClipInfo(duration=4.0, fps=30.0)

    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")],
                              project=project)

    assert _folie(edit).bg == "cache/img_007.jpg", \
        "img_006 ist hell, der Wechsel muss stattfinden"
    (meldung,) = _meldungen(plan)
    assert "Fokusblende" not in meldung


def test_die_fokusblende_wird_tatsaechlich_abgeschaltet(tmp_path):
    """Die Meldung darf keine Behauptung bleiben: bei abweichender Wahl darf
    ``_couple_focus_motion`` die Folie und das Folgebild nicht koppeln."""
    from slideshow.build import _ist_fokusblende

    project, manifest = _projekt(tmp_path, {5: HELL})
    _edit, plan, _cov = _bauen(manifest, [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe")],
                               project=project)

    (i, _slot), = [(i, s) for i, s in enumerate(plan.slots)
                   if s.intent.title is not None]
    assert not _ist_fokusblende(plan, i)
    assert plan.slots[i + 1].intent.kb is None, \
        "ohne Fokusblende darf das Folgebild keine gekoppelte Fahrt bekommen"


def test_gesucht_wird_nur_im_eigenen_abschnitt(tmp_path):
    """Ein Kapitel darf sich nicht am Material des naechsten bedienen — sonst
    stuende hinter 'Malmoe' ein Bild aus Stockholm."""
    project, manifest = _projekt(tmp_path, {5: HELL, 6: HELL})
    edit, _plan, _cov = _bauen(
        manifest, [_beat_region()],
        [Chapter(before="img_005", title="Malmoe"),
         Chapter(before="img_007", title="Stockholm")],
        project=project)

    malmoe = next(s for s in edit.segments
                  if isinstance(s, TitleSegment) and s.title == "Malmoe")
    assert malmoe.bg == "cache/img_005.jpg", \
        "der Abschnitt hat nur helle Bilder — img_007 gehoert dem naechsten"


def test_die_zahl_der_kandidaten_ist_einstellbar(tmp_path):
    """``auto_candidates: 1`` schaltet die Wahl praktisch ab: nur das erste
    Bild kommt in Frage, und mehr Kandidaten gibt es nicht zu messen."""
    project, manifest = _projekt(tmp_path, {5: HELL})
    defaults = Defaults()
    defaults.title.auto_candidates = 1
    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")],
                              defaults=defaults, project=project)

    assert _folie(edit).bg == "cache/img_005.jpg"
    assert not _meldungen(plan)


# --------------------------------------------------------------------------
# A3 — wenn kein Kandidat traegt
# --------------------------------------------------------------------------

def test_traegt_kein_kandidat_bleibt_es_beim_ersten_bild(tmp_path):
    """Keine Verschlimmbesserung: ein anderes Bild waere genauso unlesbar, und
    die Fokusblende waere zusaetzlich weg. Gemeldet wird es trotzdem — das
    Backen warnt spaeter noch einmal an der Folie selbst.
    """
    project, manifest = _projekt(tmp_path, dict.fromkeys(range(12), HELL))
    defaults = Defaults()
    defaults.title.min_contrast = 12.0        # von keiner Flaeche zu schaffen
    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")],
                              defaults=defaults, project=project)

    assert _folie(edit).bg == "cache/img_005.jpg"
    (meldung,) = _meldungen(plan, "traegt")
    assert "img_005" in meldung and "Kandidaten" in meldung


# --------------------------------------------------------------------------
# A4 — ein ausdrueckliches bg: umgeht die Kette
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bg,erwartet", [("img_009", "cache/img_009.jpg"),
                                         ("#1b2a3a", "#1b2a3a"),
                                         ("none", "none")])
def test_ein_ausdrueckliches_bg_wird_nicht_gemessen(tmp_path, bg, erwartet):
    """Der Vorschlag ersetzt nicht die Wahl, er verbessert die Vorgabe."""
    project, manifest = _projekt(tmp_path, {5: HELL, 9: HELL})
    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe", bg=bg)],
                              project=project)

    assert _folie(edit).bg == erwartet
    assert not _meldungen(plan)


# --------------------------------------------------------------------------
# A5 — Determinismus und *ein* Codepfad
# --------------------------------------------------------------------------

def test_zweimal_bauen_ergibt_dieselbe_datei(tmp_path):
    from slideshow.models import dump_edit_yaml

    project, manifest = _projekt(tmp_path, {5: HELL})
    kapitel = [Chapter(before="img_005", title="Malmoe")]
    a, _p, _c = _bauen(manifest, [_beat_region()], kapitel, project=project)
    b, _p, _c = _bauen(manifest, [_beat_region()], kapitel, project=project)

    assert dump_edit_yaml(a) == dump_edit_yaml(b)


def test_der_gewaehlte_faktor_ist_der_faktor_des_backens(tmp_path):
    """Der eigentliche Riegel aus Abschnitt 2.1: Wahl und Backen gehen durch
    dieselbe Rechnung. Zwei Implementierungen driften, und eine Folie, deren
    Wahl mit anderen Zahlen begruendet wurde als denen, mit denen sie gebacken
    wird, waere genau der stille Fehler, den dieses Repo ausschliesst.
    """
    from slideshow.titles import render_title

    project, manifest = _projekt(tmp_path, {5: HELL})
    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")],
                              project=project)
    folie = _folie(edit)

    (meldung,) = _meldungen(plan)
    quelle, _hinweis = titel_bildquelle(project, manifest, folie.bg)
    canvas = title_canvas(tuple(edit.size))
    gewaehlt, _kontrast = measure_darkening(folie, edit.defaults, bg_source=quelle,
                                            size=canvas, font=find_font())
    gebacken = render_title(folie, edit.defaults, bg_source=quelle,
                            out=tmp_path / "folie.jpg", size=canvas,
                            font=find_font())

    assert gewaehlt == gebacken["abdunklung"]
    assert f"{gewaehlt:.2f}" in meldung


# --------------------------------------------------------------------------
# A6 — ohne Schrift laeuft build durch
# --------------------------------------------------------------------------

def test_ohne_schrift_entfaellt_die_wahl_mit_warnung(tmp_path, monkeypatch):
    """``build`` bricht nicht ab: die Positionsregel liefert weiterhin ein
    Ergebnis, und das Backen meldet die fehlende Schrift ohnehin."""
    monkeypatch.setenv("SLIDESHOW_FONT", str(tmp_path / "gibtsnicht.ttf"))
    project, manifest = _projekt(tmp_path, {5: HELL})
    edit, plan, _cov = _bauen(manifest, [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")],
                              project=project)

    assert _folie(edit).bg == "cache/img_005.jpg"
    assert [w for w in plan.warnings if "Schriftdatei" in w]


def test_ohne_bg_auto_wird_keine_schrift_gesucht(tmp_path, monkeypatch):
    """Ein Projekt mit lauter festen Hintergruenden soll nicht an einer
    Abhaengigkeit scheitern, die es nicht benutzt."""
    monkeypatch.setenv("SLIDESHOW_FONT", str(tmp_path / "gibtsnicht.ttf"))
    project, manifest = _projekt(tmp_path, {})
    _edit, plan, _cov = _bauen(
        manifest, [_beat_region()],
        [Chapter(before="img_005", title="Malmoe", bg="#1b2a3a")],
        project=project)

    assert not [w for w in plan.warnings if "Schriftdatei" in w]


# --------------------------------------------------------------------------
# Die Quelle der Messung
# --------------------------------------------------------------------------

def test_gemessen_wird_das_original_nicht_das_zwischenprodukt(tmp_path):
    """Die Zwischenprodukte in ``_projekt`` sind schwarz, die Originale hell.

    Naehme die Messung das Zwischenprodukt, truege jeder Kandidat den Text mit
    dem Startwert, und die Wahl fiele nie. Derselbe Grund wie beim Backen: ein
    Hochformat ist in ``cache/`` bereits ein Blur-Komposit.
    """
    project, manifest = _projekt(tmp_path, {5: HELL})
    edit, _plan, _cov = _bauen(manifest, [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe")],
                               project=project)
    assert _folie(edit).bg == "cache/img_006.jpg"


def test_ein_fehlendes_original_faellt_nicht_als_bester_kandidat_an(tmp_path):
    """Ohne Datei gaebe es eine Schwarzflaeche zu messen — und die truege jeden
    Text. Ein geloeschtes Bild waere sonst der tragfaehigste Hintergrund."""
    project, manifest = _projekt(tmp_path, {5: HELL})
    for pfad in (manifest.media[6].path, manifest.media[6].cache_path):
        project.abs(pfad).unlink()

    edit, _plan, _cov = _bauen(manifest, [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe")],
                               project=project)
    assert _folie(edit).bg == "cache/img_007.jpg"
