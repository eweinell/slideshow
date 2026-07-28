"""Phase 5 — MLT/Kdenlive-Export und Reimport (Abschnitt 9).

Geprueft wird, was ohne installiertes Kdenlive pruefbar ist: dass das XML
wohlgeformt ist, die Geometrie der Timeline traegt und der Reimport-Weg
zurueck in die Edit-List funktioniert. Ob Kdenlive die ``rect``-Syntax der
jeweiligen Version akzeptiert, bleibt eine manuelle Pruefung — siehe
``docs/manuelle-checks.md``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from slideshow.kenburns import KBMotion
from slideshow.mlt import export_mlt, kenburns_rect, reimport_mlt
from slideshow.models import StillSegment
from slideshow.planner import visible_span

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


@pytest.fixture
def xml(built):
    return export_mlt(built["project"], built["edit"], built["manifest"])


def test_xml_ist_wohlgeformt(xml):
    root = ET.fromstring(xml)
    assert root.tag == "mlt"
    assert root.find("profile") is not None


def test_profil_traegt_groesse_und_framerate(xml, built):
    prof = ET.fromstring(xml).find("profile")
    w, h = built["edit"].size
    assert prof.get("width") == str(w)
    assert prof.get("height") == str(h)
    num = int(prof.get("frame_rate_num")) / int(prof.get("frame_rate_den"))
    assert num == pytest.approx(built["edit"].fps)


def test_zwei_videospuren_fuer_die_blenden(xml):
    """A/B-Roll: ohne zweite Spur koennen sich die Uebergaenge nicht ueberlappen."""
    root = ET.fromstring(xml)
    assert root.find(".//playlist[@id='playlist0']") is not None
    assert root.find(".//playlist[@id='playlist1']") is not None


def test_eintraege_ueberlappen_sich_um_die_blendendauer(xml, built):
    """Dieselbe Geometrie wie im ffmpeg-Pfad: Fenster ``[t - T/2, t + T/2]``."""
    root = ET.fromstring(xml)
    plan = built["plan"]

    spans: list[tuple[int, int]] = []
    for lane in (0, 1):
        pl = root.find(f".//playlist[@id='playlist{lane}']")
        cursor = 0
        for child in pl:
            if child.tag == "blank":
                cursor += int(child.get("length"))
            else:
                laenge = int(child.get("out")) - int(child.get("in")) + 1
                spans.append((cursor, cursor + laenge))
                cursor += laenge
    spans.sort()

    erwartet = [visible_span(plan, i) for i in range(len(plan.slots))]
    assert len(spans) == len(erwartet)
    for (a, b), (ea, eb) in zip(spans, erwartet):
        assert (a, b) == (max(0, ea), eb)


def test_stills_bekommen_eine_qtblend_transform(xml):
    root = ET.fromstring(xml)
    rects = [p.text for f in root.iter("filter")
             for p in f.iter("property") if p.get("name") == "rect"]
    assert rects, "kein Ken-Burns-Keyframe im Export"
    for rect in rects:
        assert ";" in rect, "eine Transform braucht mehr als einen Keyframe"


def test_tonspur_liegt_auf_eigener_spur(xml):
    root = ET.fromstring(xml)
    assert root.find(".//playlist[@id='playlist_audio']") is not None


def test_rect_keyframes_sind_monoton_im_zoom():
    m = KBMotion(1.0, 1.25, (0.5, 0.5), (0.5, 0.5), "smoothstep", "zoompan")
    rect = kenburns_rect(m, total_frames=120, size=(1920, 1080), stride=10)
    breiten = [float(k.split("=")[1].split()[2]) for k in rect.split(";")]
    assert breiten == sorted(breiten)
    assert breiten[0] == pytest.approx(1920.0)
    assert breiten[-1] == pytest.approx(1920 * 1.25)


def test_rect_bleibt_im_bild():
    """Die Quelle darf nie so verschoben werden, dass ein Rand frei liegt."""
    m = KBMotion(1.0, 1.3, (0.0, 0.0), (1.0, 1.0), "linear", "zoompan")
    rect = kenburns_rect(m, total_frames=60, size=(1920, 1080), stride=5)
    for key in rect.split(";"):
        x, y, w, h, _op = key.split("=")[1].split()
        assert float(x) <= 0.001 and float(y) <= 0.001
        assert float(x) + float(w) >= 1920 - 0.001
        assert float(y) + float(h) >= 1080 - 0.001


def test_reimport_uebernimmt_geaenderte_zeiten(built, tmp_path):
    """Der Rueckweg: in Kdenlive korrigierte Zeiten fliessen in die Edit-List."""
    project, edit, manifest = built["project"], built["edit"], built["manifest"]
    pfad = tmp_path / "projekt.kdenlive"
    pfad.write_text(export_mlt(project, edit, manifest), encoding="utf-8")

    # Ein Clip in Kdenlive verlaengern.
    tree = ET.parse(pfad)
    eintrag = tree.getroot().find(".//playlist[@id='playlist0']/entry")
    eintrag.set("out", str(int(eintrag.get("out")) + 120))
    tree.write(pfad, encoding="utf-8")

    changes = reimport_mlt(project, pfad, edit, manifest)
    assert changes, "die Aenderung haette auffallen muessen"
    geaendert = changes[0]
    assert geaendert["neu"] > geaendert["alt"]
    ziel = edit.segments[geaendert["segment"]]
    assert isinstance(ziel, StillSegment)
    assert ziel.dur == pytest.approx(geaendert["neu"])
    assert ziel.beats is None, "dur gewinnt immer, beats muss weichen"


def test_reimport_ohne_aenderung_meldet_nichts(built, tmp_path):
    project, edit, manifest = built["project"], built["edit"], built["manifest"]
    pfad = tmp_path / "projekt.kdenlive"
    pfad.write_text(export_mlt(project, edit, manifest), encoding="utf-8")
    assert reimport_mlt(project, pfad, edit, manifest) == []
