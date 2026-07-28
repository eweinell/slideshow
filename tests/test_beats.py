"""Abnahmekriterien 9 und 11 — Regionenerkennung.

9.  Eine Tonspur aus zwei Tracks mit 6 s Stille dazwischen ergibt zwei
    ``beat``-Regionen mit *unterschiedlichem* erkannten BPM und eine
    ``free``-Region.
11. Eine 1,2-s-Luecke erzeugt kein 1,2-s-Bild, sondern wird in die
    Nachbarregion aufgenommen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slideshow.beats import (detect_regions, merge_short_regions, validate_tiling)
from slideshow.errors import SlideshowError
from slideshow.models import Region

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


@pytest.fixture(scope="module")
def regions_two_songs(click_two_songs):
    return detect_regions(Path(click_two_songs.path))


@pytest.fixture(scope="module")
def regions_short_gap(click_short_gap):
    return detect_regions(Path(click_short_gap.path))


# --------------------------------------------------------------------------
# Kriterium 9
# --------------------------------------------------------------------------

def test_zwei_beat_regionen_und_eine_free_region(regions_two_songs):
    regions = regions_two_songs.regions
    beat = [r for r in regions if r.type == "beat"]
    free = [r for r in regions if r.type == "free"]
    assert len(beat) == 2, f"erwartet 2 beat-Regionen, bekommen: {[r.type for r in regions]}"
    assert len(free) == 1


def test_beat_regionen_haben_unterschiedliches_tempo(regions_two_songs, click_two_songs):
    """Der Kern von 6.1: eine Tempo-Erkennung ueber den ganzen Mix wuerde einen
    Mittelwert liefern, der zu keinem der beiden Tracks passt."""
    beat = [r for r in regions_two_songs.regions if r.type == "beat"]
    erkannt = sorted(r.bpm for r in beat)
    erwartet = sorted(t["bpm"] for t in click_two_songs.tracks)
    assert erkannt != [erkannt[0], erkannt[0]], "beide Regionen mit gleichem BPM"
    for got, want in zip(erkannt, erwartet):
        assert got == pytest.approx(want, abs=1.0), f"{got} statt {want} BPM"


def test_offsets_treffen_den_ersten_schlag(regions_two_songs, click_two_songs):
    """Die Phase entscheidet ueber den ganzen Schnitt — sie muss deutlich
    unter einem Frame liegen."""
    beat = sorted((r for r in regions_two_songs.regions if r.type == "beat"),
                  key=lambda r: r.start)
    for region, track in zip(beat, click_two_songs.tracks):
        delta = abs(region.offset - track["start"])
        assert delta < 1 / 60, (f"Offset {region.offset:.4f} vs {track['start']:.4f} "
                                f"= {delta * 1000:.1f} ms, mehr als ein Frame bei 60p")


def test_vorlauf_unter_einer_sekunde_wird_absorbiert(regions_two_songs):
    """6.0: das erste Bild beginnt bei 0, es entsteht keine eigene free-Region."""
    first = regions_two_songs.regions[0]
    assert first.start == 0.0
    assert first.type == "beat", "der Vorlauf von 0,412 s haette absorbiert werden muessen"


def test_karte_kachelt_lueckenlos(regions_two_songs, click_two_songs):
    validate_tiling(regions_two_songs.regions, click_two_songs.duration)


# --------------------------------------------------------------------------
# Kriterium 11
# --------------------------------------------------------------------------

def test_kurze_luecke_erzeugt_keine_eigene_region(regions_short_gap):
    """Kriterium 11: die 1,2-s-Luecke geht in der Nachbarregion auf."""
    kurz = [r for r in regions_short_gap.regions
            if r.type == "free" and r.duration < 3.0]
    assert not kurz, f"1,2-s-Luecke wurde zur eigenen Region: {kurz}"


def test_kurze_luecke_zerstoert_das_tempo_nicht(regions_short_gap, click_short_gap):
    beat = [r for r in regions_short_gap.regions if r.type == "beat"]
    assert len(beat) == 2
    erkannt = sorted(r.bpm for r in beat)
    erwartet = sorted(t["bpm"] for t in click_short_gap.tracks)
    for got, want in zip(erkannt, erwartet):
        assert got == pytest.approx(want, abs=1.0)


def test_merge_short_regions_verschmilzt_nach_vorn():
    """Die Verschmelzung selbst, unabhaengig von der Erkennung."""
    regions = [
        Region(type="beat", start=0.0, end=16.0, bpm=120.0, offset=0.0),
        Region(type="free", start=16.0, end=17.2, reason="luecke"),
        Region(type="beat", start=17.2, end=30.0, bpm=90.0, offset=17.2),
    ]
    out = merge_short_regions(regions, still_seconds=4.0, tolerance=(3.0, 6.0))
    assert len(out) == 2
    assert out[0].end == pytest.approx(17.2), "die Luecke gehoert an die Vorgaengerregion"
    assert out[1].start == pytest.approx(17.2)


def test_lange_stille_bleibt_eigene_region():
    regions = [
        Region(type="beat", start=0.0, end=16.0, bpm=120.0, offset=0.0),
        Region(type="free", start=16.0, end=24.0, reason="stille"),
        Region(type="beat", start=24.0, end=40.0, bpm=90.0, offset=24.0),
    ]
    out = merge_short_regions(list(regions), still_seconds=4.0, tolerance=(3.0, 6.0))
    assert len(out) == 3, "8 s tragen zwei Bilder zu 4 s und bleiben eigenstaendig"


# --------------------------------------------------------------------------
# Validierung
# --------------------------------------------------------------------------

def test_luecke_in_der_karte_wird_erkannt():
    regions = [Region(type="beat", start=0.0, end=10.0, bpm=120.0, offset=0.0),
               Region(type="free", start=11.0, end=20.0)]
    with pytest.raises(SlideshowError, match="Luecke"):
        validate_tiling(regions, 20.0)


def test_karte_muss_bei_null_beginnen():
    regions = [Region(type="beat", start=0.5, end=20.0, bpm=120.0, offset=0.5)]
    with pytest.raises(SlideshowError, match="Nullpunkt|beginnt"):
        validate_tiling(regions, 20.0)


def test_beat_region_ohne_bpm_ist_ungueltig():
    regions = [Region(type="beat", start=0.0, end=20.0)]
    with pytest.raises(SlideshowError, match="ohne bpm"):
        validate_tiling(regions, 20.0)
