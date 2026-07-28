"""Abnahmekriterium 14 — Schema- und Semantikfehler mit YAML-Pfad.

    Eine fehlerhafte ``edit.yaml`` fuehrt zu einer Fehlermeldung mit YAML-Pfad
    **vor** dem ersten Renderaufruf.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from slideshow import EDIT_VERSION
from slideshow.build import validate_edit
from slideshow.errors import SchemaError
from slideshow.models import EditList, Manifest, parse_time

BASIS = """
version: 2
fps: 60
size: [640, 360]
audio:
  file: cache/mix.flac
  duration: 28.0
  regions:
    - {type: beat, start: 0.0, end: 8.0, bpm: 120.0, offset: 0.0}
    - {type: free, start: 8.0, end: 28.0, reason: stille}
defaults:
  beats_per_still: 8
  still_seconds: 4.0
segments:
  - {type: still, src: cache/a.jpg, beats: 8}
  - {type: still, src: cache/b.jpg, beats: 8}
  - {type: still, src: cache/c.jpg}
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "edit.yaml"
    p.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")
    return p


def test_gueltige_liste_laedt(tmp_path):
    edit = EditList.load(_write(tmp_path, BASIS))
    assert edit.version == EDIT_VERSION
    assert len(edit.segments) == 3


def test_unbekannte_version_wird_abgelehnt(tmp_path):
    with pytest.raises(SchemaError) as exc:
        EditList.load(_write(tmp_path, BASIS.replace("version: 2", "version: 99")))
    assert "Version" in str(exc.value)
    assert exc.value.path == "version"


def test_unbekanntes_feld_nennt_den_pfad(tmp_path):
    text = BASIS.replace("{type: still, src: cache/b.jpg, beats: 8}",
                         "{type: still, src: cache/b.jpg, beats: 8, zoomlevel: 3}")
    with pytest.raises(SchemaError) as exc:
        EditList.load(_write(tmp_path, text))
    assert exc.value.path == "segments[1].zoomlevel"
    assert "unbekanntes Feld" in str(exc.value)


def test_fehlerhafter_kb_wert_nennt_den_pfad(tmp_path):
    text = BASIS.replace("{type: still, src: cache/a.jpg, beats: 8}",
                         "{type: still, src: cache/a.jpg, beats: 8, kb: {z: [0, 1.2]}}")
    with pytest.raises(SchemaError) as exc:
        EditList.load(_write(tmp_path, text))
    assert exc.value.path == "segments[0].kb.z"


def test_fehler_nennt_datei_und_zeile(tmp_path):
    text = BASIS.replace("{type: still, src: cache/c.jpg}",
                         "{type: still, src: cache/c.jpg, quatsch: 1}")
    with pytest.raises(SchemaError) as exc:
        EditList.load(_write(tmp_path, text))
    assert exc.value.file and exc.value.file.endswith("edit.yaml")
    assert exc.value.line, "die Meldung soll auf die Zeile zeigen"


def test_beats_in_free_region_wird_vor_dem_rendern_erkannt(tmp_path):
    """Der Fall aus Kriterium 14 — semantisch, nicht syntaktisch."""
    text = BASIS.replace("{type: still, src: cache/c.jpg}",
                         "{type: still, src: cache/c.jpg, beats: 8}")
    edit = EditList.load(_write(tmp_path, text))
    with pytest.raises(SchemaError) as exc:
        validate_edit(edit)
    assert "beat-Region" in str(exc.value)
    assert exc.value.path and exc.value.path.startswith("segments[2]")


def test_regionsluecke_wird_vor_dem_rendern_erkannt(tmp_path):
    text = BASIS.replace("{type: free, start: 8.0, end: 28.0, reason: stille}",
                         "{type: free, start: 10.0, end: 28.0, reason: stille}")
    edit = EditList.load(_write(tmp_path, text))
    with pytest.raises(Exception) as exc:
        validate_edit(edit)
    assert "Luecke" in str(exc.value)


def test_region_beginnt_nicht_bei_null(tmp_path):
    text = BASIS.replace("{type: beat, start: 0.0, end: 8.0, bpm: 120.0, offset: 0.0}",
                         "{type: beat, start: 1.0, end: 8.0, bpm: 120.0, offset: 1.0}")
    edit = EditList.load(_write(tmp_path, text))
    with pytest.raises(Exception, match="Nullpunkt|beginnt"):
        validate_edit(edit)


def test_xfade_auf_nicht_benachbarte_segmente(tmp_path):
    text = BASIS + "  - {type: xfade, from: 0, to: 2, dur: 0.5}\n"
    edit = EditList.load(_write(tmp_path, text))
    with pytest.raises(SchemaError) as exc:
        validate_edit(edit)
    assert "benachbart" in str(exc.value)


def test_xfade_auf_unbekanntes_segment(tmp_path):
    text = BASIS + "  - {type: xfade, from: 0, to: 99, dur: 0.5}\n"
    edit = EditList.load(_write(tmp_path, text))
    with pytest.raises(SchemaError) as exc:
        validate_edit(edit)
    assert exc.value.path and "to" in exc.value.path


def test_fehlende_quelldatei_nennt_den_pfad(tmp_path):
    from slideshow.build import check_sources_exist
    from slideshow.paths import Project
    edit = EditList.load(_write(tmp_path, BASIS))
    project = Project.open(tmp_path, create=True)
    with pytest.raises(SchemaError) as exc:
        check_sources_exist(project, edit)
    assert exc.value.path and exc.value.path.startswith("segments[")
    assert "src" in exc.value.path


def test_kaputtes_yaml_nennt_die_zeile(tmp_path):
    p = tmp_path / "edit.yaml"
    p.write_text("version: 2\nsegments:\n  - {type: still,\n", encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        EditList.load(p)
    assert exc.value.line


def test_manifest_version_wird_geprueft(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text('{"version": 99, "media": []}', encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        Manifest.load(p)
    assert "Version" in str(exc.value)


# --------------------------------------------------------------------------
# Zeitangaben
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,erwartet", [
    (6.5, 6.5), ("6.5", 6.5), ("00:08.500", 8.5), ("01:00:00", 3600.0),
    ("00:01.000", 1.0),
])
def test_zeitformate(text, erwartet):
    assert parse_time(text) == pytest.approx(erwartet)


def test_unlesbare_zeit_nennt_den_pfad():
    with pytest.raises(SchemaError) as exc:
        parse_time("morgen", path="segments[4].in")
    assert exc.value.path == "segments[4].in"


def test_clip_zeiten_werden_geparst(tmp_path):
    text = BASIS + '  - {type: clip, src: cache/k.mov, in: "00:01.000", out: "00:08.500"}\n'
    edit = EditList.load(_write(tmp_path, text))
    clip = edit.segments[-1]
    assert clip.in_ == pytest.approx(1.0)
    assert clip.out == pytest.approx(8.5)


def test_roundtrip_ueber_yaml(tmp_path):
    """Was `build` schreibt, muss `render` wieder lesen koennen."""
    edit = EditList.load(_write(tmp_path, BASIS))
    out = tmp_path / "wieder.yaml"
    edit.save(out)
    erneut = EditList.load(out)
    assert erneut.model_dump() == edit.model_dump()
