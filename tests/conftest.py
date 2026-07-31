"""Gemeinsame Fixtures fuer die Abnahme-Tests.

Alles laeuft gegen das synthetische Material aus :mod:`slideshow.fixtures`.
Der Klick-Track hat einen *bekannten* Beat-Zeitplan — nur deshalb ist der Sync
ueberhaupt automatisiert pruefbar (Abschnitt 12).

Die Tests arbeiten bewusst in kleinen Aufloesungen: geprueft wird Timing,
Struktur und Caching, nicht Bildqualitaet. Die visuellen Kriterien (4, Banding,
Judder) bleiben dokumentierte manuelle Checks — siehe ``docs/manuelle-checks.md``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from slideshow.fixtures import TrackSpec, make_click_track, make_clips, make_images
from slideshow.paths import Project
from slideshow.proc import have

#: Kleine Testaufloesung — 7680 px waeren reine Wartezeit.
TEST_SIZE = (640, 360)
TEST_LONG_EDGE = 1280

requires_ffmpeg = pytest.mark.skipif(not have("ffmpeg"), reason="ffmpeg fehlt")


# --------------------------------------------------------------------------
# Material
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fixture_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("fixtures")


@pytest.fixture(scope="session")
def click_two_songs(fixture_root: Path):
    """Zwei Songs (120 und 90 BPM) mit 6 s Stille dazwischen."""
    return make_click_track(fixture_root / "audio" / "two_songs.wav",
                            tracks=[TrackSpec(120.0, 32), TrackSpec(90.0, 18)], gap=6.0)


@pytest.fixture(scope="session")
def click_short_gap(fixture_root: Path):
    """Dieselbe Struktur mit nur 1,2 s Luecke — Abnahmekriterium 11."""
    return make_click_track(fixture_root / "audio" / "short_gap.wav",
                            tracks=[TrackSpec(120.0, 32), TrackSpec(90.0, 18)], gap=1.2)


@pytest.fixture(scope="session")
def click_long_track(fixture_root: Path):
    """*Ein* durchgehender Song, deutlich laenger als ``MAX_FIT_WINDOW``.

    Der Fall aus ``docs/briefing-beat-detection.md``: ohne innere Stille und
    ohne Track-Grenze gab es frueher genau eine Region ueber die volle Laenge,
    und die war mangels Konfidenz ``free``.
    """
    return make_click_track(fixture_root / "audio" / "long_track.wav",
                            tracks=[TrackSpec(150.0, 175)], gap=0.0)


@pytest.fixture(scope="session")
def images(fixture_root: Path) -> list[Path]:
    return make_images(fixture_root / "images", count=10, width=800, height=500)


@pytest.fixture(scope="session")
def clips(fixture_root: Path) -> dict[str, Path]:
    if not have("ffmpeg"):
        return {}
    return make_clips(fixture_root / "clips", seconds=3.0)


# --------------------------------------------------------------------------
# Projekt
# --------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path: Path) -> Project:
    p = Project.open(tmp_path / "proj", create=True)
    p.ensure_dirs()
    return p


@pytest.fixture(scope="session")
def caps():
    from slideshow.doctor import probe_capabilities
    return probe_capabilities(deep=False)


def build_project(project: Project, *, images: list[Path], audio, caps,
                  clips: dict[str, Path] | None = None, fps: float = 60.0,
                  size: tuple[int, int] = TEST_SIZE, **build_kwargs):
    """Faehrt die ganze Pipeline bis zur Edit-List durch.

    Gibt ``(manifest, edit, plan, coverage)`` zurueck.
    """
    from slideshow.audio import build_mix
    from slideshow.beats import detect_regions
    from slideshow.build import build_edit_list
    from slideshow.models import Defaults
    from slideshow.preprocess import preprocess
    from slideshow.probe import probe_sources

    sources = [p for p in images]
    if clips:
        sources += list(clips.values())

    result = probe_sources(project, sources, caps=caps, target_fps=fps)
    manifest = result.manifest

    mix = project.cache / "mix.flac"
    manifest.audio = build_mix([Path(audio.path)], mix)
    manifest.audio.file = project.rel(mix)

    preprocess(project, manifest, caps=caps, size=size,
               intermediate_codec="hevc_intra_cpu", long_edge=TEST_LONG_EDGE)
    manifest.save(project.manifest)

    beatmap = detect_regions(mix, track_bounds=[(t.start, t.end)
                                               for t in manifest.audio.tracks])
    defaults = Defaults(**build_kwargs) if build_kwargs else Defaults()
    edit, plan, cov = build_edit_list(project, manifest, beatmap,
                                      defaults=defaults, fps=fps, size=size)
    edit.save(project.edit)
    return (manifest, edit, plan, cov)


@pytest.fixture
def built(project, images, click_two_songs, caps):
    """Ein fertig gebautes Projekt ohne Clips — der Normalfall."""
    manifest, edit, plan, cov = build_project(project, images=images,
                                              audio=click_two_songs, caps=caps)
    return {"project": project, "manifest": manifest, "edit": edit, "plan": plan,
            "coverage": cov, "audio": click_two_songs}


def true_beat_times(track: dict) -> list[float]:
    return [round(track["start"] + i * track["beat_dur"], 9)
            for i in range(track["beats"])]


def nearest_beat_delta(t: float, beats: list[float]) -> float:
    """Abstand zum naechstliegenden echten Beat, in Sekunden."""
    return min(abs(t - b) for b in beats)
