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


def test_handarbeit_haelt_den_neubau_an_und_laesst_sich_sichern(tmp_path, quelle,
                                                                capsys):
    """Die Schleife aus Rezept 8: aendern -> `build` haelt an -> sichern -> bauen.

    Der Fall, um den es geht: ohne diese Kette kostet ein nachgereichtes Bild
    entweder die Handarbeit (Neubau) oder das Bild (kein Neubau).
    """
    src, audio = quelle
    proj = str(tmp_path / "proj")
    _run("--project", proj, "probe", str(src), "--fps", "60")
    _run("--project", proj, "audio", str(audio))
    _run("--project", proj, "preprocess", "--intermediate", "hevc_intra_cpu")
    _run("--project", proj, "beats")
    assert _run("--project", proj, "build") == 0

    edit = tmp_path / "proj" / "edit.yaml"
    text = edit.read_text(encoding="utf-8")
    assert "beats: 8" in text, "Vorbedingung: ein Standbild mit dem Regeltakt"
    edit.write_text(text.replace("beats: 8", "beats: 12", 1), encoding="utf-8")
    capsys.readouterr()

    # Der Neubau haelt an, statt die Aenderung kommentarlos zu verwerfen.
    assert _run("--project", proj, "build") != 0
    assert "overrides" in capsys.readouterr().out

    assert _run("--project", proj, "overrides") == 0
    ov = tmp_path / "proj" / "overrides.yaml"
    assert ov.exists() and "beats: 12" in ov.read_text(encoding="utf-8")

    # Danach baut es wieder ohne Nachfrage — und traegt denselben Wert.
    assert _run("--project", proj, "build") == 0
    assert "beats: 12" in edit.read_text(encoding="utf-8")


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


# --------------------------------------------------------------------------
# Wegweiser
#
# Der Vorschlag kommt aus dem Zustand des Projekts, nicht aus dem zuletzt
# gelaufenen Kommando — sonst führt er in die Irre, sobald jemand eine Phase
# wiederholt oder überspringt.
# --------------------------------------------------------------------------

class _Args:
    def __init__(self, project=None):
        self.project = project
        self.quiet = False
        self.dry_run = False


def _vorschlag_zeilen(project, project_arg="/anderswo"):
    from slideshow.cli import _naechster_schritt
    return _naechster_schritt(project, _Args(project_arg))


def _vorschlag(project, project_arg="/anderswo"):
    return " ".join(_vorschlag_zeilen(project, project_arg))


@pytest.fixture
def leer(tmp_path):
    from slideshow.paths import Project
    p = Project.open(tmp_path / "proj", create=True)
    p.ensure_dirs()
    return p


def _schreibe_manifest(project, *, cache_path="", medien=2, audio_file=""):
    from slideshow.models import AudioInfo, ImageInfo, Manifest, MediaItem
    m = Manifest(
        media=[MediaItem(id=f"img_{i}", path=f"src/DSC{i}.jpg", kind="image",
                         cache_path=(f"cache/img_{i}.jpg" if cache_path else ""),
                         image=ImageInfo(width=6000, height=4000))
               for i in range(medien)],
        audio=AudioInfo(file=audio_file))
    m.save(project.manifest)
    return m


def test_ohne_manifest_wird_probe_vorgeschlagen(leer):
    assert "probe" in _vorschlag(leer)


def test_ohne_zwischenprodukte_wird_preprocess_vorgeschlagen(leer):
    _schreibe_manifest(leer)
    assert _vorschlag(leer).endswith("preprocess")


def test_tonspur_im_materialordner_wird_erkannt(leer):
    """Der Vorschlag nennt die gefundene Datei, statt nur `audio` zu sagen."""
    _schreibe_manifest(leer, cache_path="ja")
    (leer.root / "src").mkdir(exist_ok=True)
    for name in ("DSC0.jpg", "Mein Lied.mp3"):
        (leer.root / "src" / name).write_bytes(b"x")

    vorschlag = _vorschlag(leer)
    assert 'audio "Mein Lied.mp3"' in vorschlag, "Leerzeichen müssen gequotet sein"
    assert "ohne Musik weiter" in vorschlag, "der stumme Weg gehört daneben"


def test_ohne_tonspurkandidat_geht_es_direkt_zu_beats(leer):
    _schreibe_manifest(leer, cache_path="ja")
    (leer.root / "src").mkdir(exist_ok=True)
    (leer.root / "src" / "DSC0.jpg").write_bytes(b"x")

    assert _vorschlag(leer).endswith("beats")


def _schreibe_beatmap(project, dauer):
    (project.root / "beats.yaml").write_text(
        f"version: 1\naudio: {{file: cache/mix.flac, duration: {dauer}}}\n"
        f"regions:\n- {{type: free, start: 0.0, end: {dauer}, reason: x}}\n",
        encoding="utf-8")


def test_build_vorschlag_nennt_die_passende_standzeit(leer):
    _schreibe_manifest(leer, cache_path="ja", medien=14, audio_file="cache/mix.flac")
    _schreibe_beatmap(leer, 392.68)

    assert "--still-seconds 28" in _vorschlag(leer)


def test_unsinnige_standzeit_wird_nicht_vorgeschlagen(leer):
    """392 s auf 3 Bilder wären 131 s je Bild — richtig gerechnet, trotzdem Unsinn."""
    _schreibe_manifest(leer, cache_path="ja", medien=3, audio_file="cache/mix.flac")
    _schreibe_beatmap(leer, 392.68)

    # Auf der ersten Zeile, nicht auf der ganzen Ausgabe: darunter kann der
    # Hinweis auf `chapters` stehen, und der ist eine andere Zusage.
    zeilen = _vorschlag_zeilen(leer)
    assert zeilen[0].endswith("build"), "dann lieber nackt — build erklärt die Optionen"
    assert "still-seconds" not in " ".join(zeilen)


def test_projekt_schalter_entfaellt_im_eigenen_verzeichnis(leer, monkeypatch):
    """Sonst steht in der Zeile ein --project, das auf das Hier zeigt."""
    _schreibe_manifest(leer)
    monkeypatch.chdir(leer.root)

    assert _vorschlag(leer, str(leer.root)) == "slideshow preprocess"


def test_am_ende_wird_nichts_mehr_verlangt(leer):
    _schreibe_manifest(leer, cache_path="ja", audio_file="cache/mix.flac")
    _schreibe_beatmap(leer, 20.0)
    (leer.root / "edit.yaml").write_text("x", encoding="utf-8")
    (leer.out / "master.mp4").write_bytes(b"x")

    assert "Fertig" in _vorschlag(leer)


def test_kaputtes_manifest_kippt_den_wegweiser_nicht(leer):
    """Der Hinweis ist Beiwerk — er darf nie das eigentliche Kommando stören."""
    leer.manifest.write_text("{kein json", encoding="utf-8")

    assert _vorschlag(leer) == ""
