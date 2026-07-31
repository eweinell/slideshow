"""Abnahmekriterien 9 und 11 — Regionenerkennung.

9.  Eine Tonspur aus zwei Tracks mit 6 s Stille dazwischen ergibt zwei
    ``beat``-Regionen mit *unterschiedlichem* erkannten BPM und eine
    ``free``-Region.
11. Eine 1,2-s-Luecke erzeugt kein 1,2-s-Bild, sondern wird in die
    Nachbarregion aufgenommen.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from slideshow.beats import (ANALYSIS_SR, CONF_THRESHOLD, HOP, MAX_FIT_WINDOW,
                             _split_long, _stability, detect_regions, fit_grid,
                             merge_adjacent_beats, merge_short_regions,
                             validate_tiling)
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
# Durchgehender Track — docs/briefing-beat-detection.md
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def regions_long_track(click_long_track):
    return detect_regions(Path(click_long_track.path))


def test_durchgehender_track_wird_nicht_komplett_free(regions_long_track):
    """Der Kern des Briefings: 70 s Musik am Stueck ergaben frueher *eine*
    free-Region ueber die volle Laenge — kein einziger Schnitt auf einem Beat.
    """
    beat = [r for r in regions_long_track.regions if r.type == "beat"]
    assert beat, "durchgehender Song ohne eine einzige Beat-Region"
    anteil = sum(r.duration for r in beat) / regions_long_track.audio["duration"]
    assert anteil > 0.9, f"nur {anteil:.0%} des Tracks als beat erkannt"


def test_durchgehender_track_behaelt_sein_tempo(regions_long_track):
    for r in regions_long_track.regions:
        if r.type == "beat":
            assert r.bpm == pytest.approx(150.0, abs=1.5), \
                f"{r.bpm} statt 150 BPM — Oktavfehler waere 75 oder 300"


def test_jedes_fenster_liegt_ueber_der_schwelle(regions_long_track):
    """Abnahmekriterium A1: die Zerlegung existiert genau dafuer."""
    for r in regions_long_track.regions:
        if r.type == "beat":
            assert r.conf >= CONF_THRESHOLD


def test_gleichbleibendes_tempo_ergibt_eine_region(regions_long_track):
    """Die Fensterung ist ein Mittel der Messung, keine Aussage ueber die
    Musik: bei konstantem Tempo darf sie nicht sichtbar werden."""
    beat = [r for r in regions_long_track.regions if r.type == "beat"]
    assert len(beat) == 1, \
        f"in {len(beat)} Fenster zerfallen: {[(r.start, r.end, r.bpm) for r in beat]}"


def test_karte_des_langen_tracks_kachelt_lueckenlos(regions_long_track,
                                                    click_long_track):
    validate_tiling(regions_long_track.regions, click_long_track.duration)


# --------------------------------------------------------------------------
# Fensterbildung
# --------------------------------------------------------------------------

def test_lange_abschnitte_werden_in_gleiche_fenster_zerlegt():
    out = _split_long([(0.0, 70.0)], 20.0)
    dauern = [e - s for s, e in out]
    assert len(out) == 4
    assert max(dauern) <= 20.0
    assert max(dauern) == pytest.approx(min(dauern)), "ungleiche Fenster"
    assert out[0][0] == 0.0 and out[-1][1] == 70.0, "Abschnitt nicht vollstaendig"


def test_zerlegung_laesst_keinen_reststummel():
    """41 s werden zu 3x13,7 s — nicht zu 20+20+1. Ein Stummel bekaeme kein
    verwertbares Raster und fiele als free-Insel auf."""
    out = _split_long([(0.0, 41.0)], 20.0)
    assert len(out) == 3
    assert min(e - s for s, e in out) > 10.0


def test_kurze_abschnitte_bleiben_unangetastet():
    spans = [(0.0, 16.0), (20.0, 30.0)]
    assert _split_long(spans, 20.0) == spans


def test_fixture_songs_bleiben_unter_der_fenstergrenze():
    """A5 haengt daran: die Klick-Songs (16,0 und 12,0 s) duerfen gar nicht
    erst zerlegt werden, sonst gilt die Garantie von genau zwei Regionen
    nicht mehr."""
    assert MAX_FIT_WINDOW > 16.0


# --------------------------------------------------------------------------
# Wiederverschmelzen
# --------------------------------------------------------------------------

def _fenster(start: float, end: float, bpm: float, offset: float, conf: float = 0.8):
    return Region(type="beat", start=start, end=end, bpm=bpm, offset=offset,
                  conf=conf)


def test_benachbarte_fenster_gleichen_tempos_werden_verschmolzen():
    out = merge_adjacent_beats([_fenster(0.0, 20.0, 150.0, 0.0, conf=0.8),
                                _fenster(20.0, 40.0, 150.0, 20.0, conf=0.7)])
    assert len(out) == 1
    assert out[0].end == pytest.approx(40.0)
    assert out[0].conf == pytest.approx(0.7), \
        "die verschmolzene Region ist nur so verlaesslich wie ihr schwaechster Teil"


def test_tempowechsel_haelt_die_grenze():
    out = merge_adjacent_beats([_fenster(0.0, 20.0, 150.0, 0.0),
                                _fenster(20.0, 40.0, 90.0, 20.0)])
    assert len(out) == 2, "an einem echten Tempowechsel gehoert eine Grenze hin"


def test_phasenversatz_haelt_die_grenze():
    """Gleiches Tempo, aber der zweite Song setzt auf der Gegenzeit ein."""
    out = merge_adjacent_beats([_fenster(0.0, 20.0, 150.0, 0.0),
                                _fenster(20.0, 40.0, 150.0, 20.2)])
    assert len(out) == 2


def test_leichter_tempoversatz_haelt_die_grenze():
    """1 % Abweichung liegt unter der Tempotoleranz und die Phase stimmt am
    *Anfang* — bis zum Ende der zweiten Region ist das Raster trotzdem um
    eine halbe Beat-Laenge verrutscht. Genau dafuer wird auch am Ende geprueft.
    """
    out = merge_adjacent_beats([_fenster(0.0, 20.0, 150.0, 0.0),
                                _fenster(20.0, 40.0, 151.5, 20.0)])
    assert len(out) == 2


def test_handgeschriebene_karte_mit_einer_region_bleibt_gueltig():
    """Abnahmekriterium A7. Die Zerlegung aendert nur, *wie viele* Regionen
    erzeugt werden — das Format bleibt gleich, und eine von Hand gepflegte
    Karte mit einer einzigen Beat-Region ueber den ganzen Track wird
    weiterhin respektiert. Neu erzeugt wuerde sie nicht mehr.
    """
    import yaml

    from slideshow.models import BeatMap

    roh = yaml.safe_load("""
    version: 1
    audio: {file: mix.flac, duration: 392.68}
    regions:
      - {type: beat, start: 0.0, end: 392.68, bpm: 152.0, offset: 0.35}
    """)
    karte = BeatMap(version=roh["version"], audio=roh["audio"],
                    regions=[Region.model_validate(r) for r in roh["regions"]])

    validate_tiling(karte.regions, 392.68)
    assert karte.regions[0].beat_duration() == pytest.approx(60.0 / 152.0)


def test_free_regionen_verschmelzen_nicht_mit_beat_regionen():
    regions = [_fenster(0.0, 20.0, 150.0, 0.0),
               Region(type="free", start=20.0, end=26.0, reason="stille"),
               _fenster(26.0, 46.0, 150.0, 26.0)]
    assert len(merge_adjacent_beats(regions)) == 3


# --------------------------------------------------------------------------
# Oktavfehler und Stabilitaet
# --------------------------------------------------------------------------

def _klick_huellkurve(bpm: float, seconds: float, *, schwach: float) -> np.ndarray:
    """Onset-Kurve mit abwechselnd starken und schwachen Schlaegen."""
    fps = ANALYSIS_SR / HOP
    env = np.zeros(int(seconds * fps))
    period = 60.0 / bpm * fps
    for k in range(int((len(env) - 1) / period) + 1):
        env[int(round(k * period))] = 1.0 if k % 2 == 0 else schwach
    return env


def test_bassdrum_auf_jedem_zweiten_schlag_ergibt_nicht_das_halbe_tempo():
    """Ohne Korrektur gewinnt hier das halbe Raster: es sammelt pro
    Rasterpunkt mehr Energie ein, und der Prior kann das nicht auffangen."""
    env = _klick_huellkurve(152.0, 20.0, schwach=0.45)
    an = fit_grid(env, start=0.0, sr=ANALYSIS_SR, hop=HOP)
    assert an.bpm == pytest.approx(152.0, abs=1.5), f"{an.bpm} statt 152 BPM"


def test_gleichmaessige_schlaege_werden_nicht_verdoppelt():
    """Die Gegenprobe: liegt zwischen den Schlaegen nichts, bleibt es beim
    gefundenen Tempo. Sonst wuerde jeder Klick-Track doppelt so schnell."""
    env = _klick_huellkurve(76.0, 20.0, schwach=1.0)
    an = fit_grid(env, start=0.0, sr=ANALYSIS_SR, hop=HOP)
    assert an.bpm == pytest.approx(76.0, abs=1.5), f"{an.bpm} statt 76 BPM"


def test_stabilitaet_bestraft_den_backbeat_nicht():
    wechsel = np.array([1.0, 0.4] * 12)
    assert _stability(wechsel) > 0.95, \
        "der regelmaessige Wechsel stark/schwach ist Regelmaessigkeit, keine Stoerung"


def test_stabilitaet_faellt_bei_unregelmaessiger_energie():
    unregelmaessig = np.array([1.0, 0.1, 0.55, 0.95, 0.2, 0.75] * 4)
    assert _stability(unregelmaessig) < 0.7


def test_stabilitaet_entspricht_ohne_wechsel_der_streuung():
    """Sind beide Haelften gleich stark, geht die Formel exakt in
    ``1 - std/mean`` ueber — Material ohne Backbeat wird unveraendert
    bewertet, die Schwelle bleibt dort kalibriert."""
    vals = np.array([1.0, 0.6, 0.6, 1.0] * 6)
    assert _stability(vals) == pytest.approx(1.0 - vals.std() / vals.mean())


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
