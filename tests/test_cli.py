"""CLI-Oberflaeche (Abschnitt 10) — Rauchtest der ganzen Kette.

Geprueft wird, dass jedes Subkommando aufrufbar ist, ``--dry-run`` wirklich
nichts schreibt und Fehler als Meldung statt als Traceback herauskommen.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from slideshow.cli import main

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def _run(*args: str) -> int:
    return main(list(args))


@pytest.fixture
def quelle(tmp_path, images, click_two_songs):
    """Ein Quellverzeichnis mit wenigen Bildern und der Tonspur."""
    src = tmp_path / "material"
    src.mkdir()
    for p in images[:5]:
        shutil.copy(p, src / p.name)
    return (src, Path(click_two_songs.path))


def test_hilfe_und_version(capsys):
    with pytest.raises(SystemExit) as exc:
        _run("--version")
    assert exc.value.code == 0


def test_doctor_liefert_exitcode(tmp_path):
    code = _run("--project", str(tmp_path), "doctor", "--quick")
    assert code in (0, 2)


def test_ganze_kette(tmp_path, quelle):
    """probe -> audio -> preprocess -> beats -> build: muss durchlaufen."""
    src, audio = quelle
    proj = str(tmp_path / "proj")

    assert _run("--project", proj, "probe", str(src), "--fps", "60") == 0
    assert (tmp_path / "proj" / "manifest.json").exists()

    assert _run("--project", proj, "audio", str(audio)) == 0
    assert (tmp_path / "proj" / "cache" / "mix.flac").exists()

    assert _run("--project", proj, "preprocess", "--intermediate", "hevc_intra_cpu") == 0
    assert list((tmp_path / "proj" / "cache").glob("img_*.jpg"))

    assert _run("--project", proj, "beats") == 0
    assert (tmp_path / "proj" / "beats.yaml").exists()

    assert _run("--project", proj, "build", "--force") == 0
    edit = tmp_path / "proj" / "edit.yaml"
    assert edit.exists()
    assert (tmp_path / "proj" / "out" / "timeline.json").exists()

    assert _run("--project", proj, "export-mlt") == 0
    assert (tmp_path / "proj" / "out" / "project.kdenlive").exists()

    # Jedes Subkommando schreibt ein Logfile nach logs/ (Abschnitt 2).
    logs = list((tmp_path / "proj" / "logs").glob("*.log"))
    assert {p.name.split("-")[0] for p in logs} >= {
        "probe", "audio", "preprocess", "beats", "build"}


def test_dry_run_schreibt_nichts(tmp_path, quelle):
    src, _audio = quelle
    proj = tmp_path / "proj"
    assert _run("--project", str(proj), "--dry-run", "probe", str(src)) == 0
    assert not (proj / "manifest.json").exists()


def test_render_dry_run_zeigt_kommandos(tmp_path, quelle, capsys):
    src, audio = quelle
    proj = str(tmp_path / "proj")
    _run("--project", proj, "probe", str(src), "--fps", "60")
    _run("--project", proj, "audio", str(audio))
    _run("--project", proj, "preprocess", "--intermediate", "hevc_intra_cpu")
    _run("--project", proj, "beats")
    _run("--project", proj, "build", "--force")
    capsys.readouterr()

    assert _run("--project", proj, "--dry-run", "render", "--codec", "libx264") == 0
    out = capsys.readouterr().out
    assert "ffmpeg" in out
    assert not (tmp_path / "proj" / "out" / "master.mp4").exists()


def test_fehlendes_manifest_meldet_klartext(tmp_path, capsys):
    code = _run("--project", str(tmp_path), "build")
    assert code != 0
    text = capsys.readouterr().out
    assert "Traceback" not in text
    assert "slideshow" in text.lower()


def test_unlesbarer_clock_offset_meldet_das_format(tmp_path, quelle, capsys):
    src, _audio = quelle
    code = _run("--project", str(tmp_path / "p"), "probe", str(src),
                "--clock-offset", "voellig-kaputt")
    assert code != 0
    text = capsys.readouterr().out
    assert "MODELL" in text or "clock-offset" in text


def test_clock_offset_landet_im_manifest(tmp_path, quelle):
    from slideshow.models import Manifest
    src, _audio = quelle
    proj = tmp_path / "proj"
    assert _run("--project", str(proj), "probe", str(src),
                "--clock-offset", "TestCam=+01:30:00") == 0
    manifest = Manifest.load(proj / "manifest.json")
    assert manifest.clock_offsets["TestCam"] == pytest.approx(5400.0)


def test_beats_mit_manuellem_tempo(tmp_path, quelle):
    import yaml
    src, audio = quelle
    proj = tmp_path / "proj"
    _run("--project", str(proj), "probe", str(src), "--fps", "60")
    _run("--project", str(proj), "audio", str(audio))
    assert _run("--project", str(proj), "beats", "--bpm", "118", "--offset", "0.4") == 0
    karte = yaml.safe_load((proj / "beats.yaml").read_text(encoding="utf-8"))
    assert len(karte["regions"]) == 1
    assert karte["regions"][0]["bpm"] == 118.0
    assert karte["regions"][0]["offset"] == pytest.approx(0.4)


def test_clock_offset_parser():
    from slideshow.probe import parse_clock_offset
    assert parse_clock_offset("ILCE-7M4=+01:00:00") == ("ILCE-7M4", 3600.0)
    assert parse_clock_offset("Pixel 7=-00:05:30") == ("Pixel 7", -330.0)
    assert parse_clock_offset("Cam=02:00") == ("Cam", 7200.0)


def test_range_parser():
    from slideshow.errors import SlideshowError
    from slideshow.render import parse_range
    assert parse_range(None, 10) == (0, 10)
    assert parse_range("2:5", 10) == (2, 5)
    assert parse_range("7:", 10) == (7, 10)
    assert parse_range(":3", 10) == (0, 3)
    with pytest.raises(SlideshowError):
        parse_range("5:5", 10)
