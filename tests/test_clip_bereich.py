"""Clip-Intermediates beim Rendern, nur im verwendeten Bereich (Pruefbericht
2026-11, AP2).

Vorher rechnete ``preprocess`` jeden Clip ganz in DNxHR um, bevor der Schnitt
feststand — in ``kenia-2026`` 192 GB, von denen ein Drittel im Film vorkam.
Jetzt entsteht das Intermediate nach dem Planen, fuer die sichtbare Spanne
plus Rand, auf ganze Sekunden gerundet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slideshow.models import ClipInfo, Manifest, MediaItem, MediaOverride, Overrides
from slideshow.planner import Intent, Plan, Slot
from slideshow.preprocess import (CLIP_HANDLE, clip_bereiche, ensure_clip_intermediates,
                                  preprocess)
from slideshow.proc import ffprobe_json

from .conftest import TEST_INTERMEDIATE, TEST_SIZE, build_project, requires_ffmpeg

FPS = 50.0


def _manifest(dauer: float = 20.0) -> Manifest:
    return Manifest(fps_suggestion=FPS, media=[MediaItem(
        id="clip_a", path="src/a.mp4", kind="clip", cache_path="cache/clip_a.mov",
        clip=ClipInfo(duration=dauer, effective_duration=dauer, fps=FPS))])


def _plan(*clips: tuple[float, int, int], blende: int = 50) -> Plan:
    """Ein Standbild, dahinter die Clips als ``(clip_in, start_f, end_f)``."""
    slots = [Slot(intent=Intent(kind="still", src="cache/img.jpg"), region_index=0,
                  start_f=0, end_f=clips[0][1])]
    for clip_in, a, b in clips:
        slots.append(Slot(intent=Intent(kind="clip", src="cache/clip_a.mov"),
                          region_index=0, start_f=a, end_f=b, clip_in=clip_in,
                          clip_out=clip_in + (b - a) / FPS))
    trans = [0] + [blende] * (len(slots) - 1) + [0]
    return Plan(fps=FPS, slots=slots, transitions=trans,
                total_frames=slots[-1].end_f, regions=[])


# --------------------------------------------------------------------------
# Bereichsrechnung
# --------------------------------------------------------------------------

def test_der_bereich_deckt_die_sichtbare_spanne_samt_rand_in_ganzen_sekunden():
    # Clip ab 6,3 s, 4 s lang; davor eine 1-s-Blende, deren Haelfte vor
    # clip_in liegt: sichtbar 5,8 .. 10,3 s, mit Rand 4,8 .. 11,3 s.
    bereich = clip_bereiche(_plan((6.3, 200, 400)), _manifest())
    assert bereich == {"cache/clip_a.mov": (4.0, 12.0)}


def test_ein_korrigierter_schnitt_um_wenige_frames_aendert_den_bereich_nicht():
    """Der Bereich steht im Kommando und damit im Cache-Key. Ohne Rundung
    rechnete jede Feinkorrektur den Clip neu."""
    vorher = clip_bereiche(_plan((6.3, 200, 400)), _manifest())
    nachher = clip_bereiche(_plan((6.3 + 3 / FPS, 200, 400)), _manifest())
    assert vorher == nachher


def test_der_bereich_endet_an_den_grenzen_des_clips():
    bereich = clip_bereiche(_plan((0.2, 200, 400)), _manifest(dauer=4.5))
    assert bereich["cache/clip_a.mov"] == (0.0, 4.5)


def test_ein_doppelt_verwendeter_clip_bekommt_die_huelle_seiner_bereiche():
    bereich = clip_bereiche(_plan((2.0, 200, 300), (14.0, 500, 600)), _manifest())
    lo, hi = bereich["cache/clip_a.mov"]
    assert lo <= 2.0 - 0.5 - CLIP_HANDLE and hi >= 16.0 + CLIP_HANDLE
    assert lo == int(lo) and hi == int(hi)


def test_ohne_clip_im_plan_gibt_es_nichts_zu_tun():
    plan = Plan(fps=FPS, slots=[Slot(intent=Intent(kind="still", src="cache/img.jpg"),
                                     region_index=0, start_f=0, end_f=100)],
                transitions=[0, 0], total_frames=100, regions=[])
    assert clip_bereiche(plan, _manifest()) == {}


# --------------------------------------------------------------------------
# preprocess fasst Clips nicht mehr an
# --------------------------------------------------------------------------

@requires_ffmpeg
def test_preprocess_vergibt_nur_die_kennung(project, clip_with_audio, caps):
    from slideshow.probe import probe_sources
    if clip_with_audio is None:
        pytest.skip("Clip-Fixture mit Ton konnte nicht erzeugt werden")
    manifest = probe_sources(project, [clip_with_audio], caps=caps,
                             target_fps=FPS).manifest
    stats = preprocess(project, manifest, caps=caps, size=TEST_SIZE)
    item = manifest.clips[0]
    assert stats.clips == 1 and not stats.failures
    assert item.cache_path == f"cache/{item.id}.mov"
    assert not project.abs(item.cache_path).exists(), "das Intermediate entsteht beim Rendern"
    assert not item.audio_cache_path


# --------------------------------------------------------------------------
# Gegen echtes Material
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def langer_clip(fixture_root: Path) -> Path | None:
    from slideshow.fixtures import make_audio_clip
    from slideshow.proc import have
    if not have("ffmpeg"):
        return None
    return make_audio_clip(fixture_root / "clips" / "clip_lang.mp4", seconds=14.0)


@pytest.fixture
def ausschnitt(project, images, click_two_songs, caps, langer_clip):
    """Ein 14-s-Clip, von dem der Film nur 6,0 bis 8,0 s zeigt."""
    if langer_clip is None:
        pytest.skip("Clip-Fixture konnte nicht erzeugt werden")

    def reihenfolge(m):
        bilder = [x.id for x in m.media if x.kind == "image"]
        return [bilder[0], *[x.id for x in m.media if x.kind == "clip"], *bilder[1:4]]

    def schnitt(m):
        cid = next(x.id for x in m.media if x.kind == "clip")
        return Overrides(media={cid: MediaOverride.model_validate({"in": 6.0, "out": 8.0})})

    manifest, edit, plan, _c = build_project(
        project, images=images[:4], audio=click_two_songs, caps=caps, fps=FPS,
        clips={"lang": langer_clip}, order=reihenfolge, overrides=schnitt)
    return {"project": project, "manifest": manifest, "edit": edit, "plan": plan,
            "caps": caps}


@requires_ffmpeg
def test_das_intermediate_deckt_nur_den_verwendeten_bereich(ausschnitt):
    project, manifest = ausschnitt["project"], ausschnitt["manifest"]
    item = manifest.clips[0]
    slot = next(s for s in ausschnitt["plan"].slots if s.intent.kind == "clip")
    assert slot.clip_in == pytest.approx(6.0)

    offset, dauer = item.clip.cache_offset, item.clip.cache_duration
    assert offset <= slot.clip_in - CLIP_HANDLE and offset == int(offset)
    assert offset + dauer >= slot.clip_out + CLIP_HANDLE
    # A1 des Pruefberichts sinngemaess: hoechstens das 1,3-Fache von
    # "verwendete Sekunden + 2 s"; die ganzen Sekunden kosten hier mehr
    # Anteil als bei echten Clips, deshalb die Rundung obendrauf.
    verwendet = slot.clip_out - slot.clip_in
    assert dauer <= 1.3 * (verwendet + 2 * CLIP_HANDLE) + 2.0
    assert dauer < item.clip.effective_duration - 4.0, "der ganze Clip wurde umgerechnet"

    ist = float(ffprobe_json(project.abs(item.cache_path))["format"]["duration"])
    assert ist == pytest.approx(dauer, abs=1.5 / FPS)
    ton = float(ffprobe_json(project.abs(item.audio_cache_path))["format"]["duration"])
    assert ton == pytest.approx(dauer, abs=0.05)


@requires_ffmpeg
def test_ein_zweiter_lauf_ohne_aenderung_erzeugt_nichts(ausschnitt):
    """A4 des Pruefberichts, fuer die Clips."""
    stats = ensure_clip_intermediates(
        ausschnitt["project"], ausschnitt["plan"], ausschnitt["manifest"],
        size=TEST_SIZE, caps=ausschnitt["caps"], codec=TEST_INTERMEDIATE)
    assert (stats.erzeugt, stats.aus_cache) == (0, 1)


@requires_ffmpeg
def test_das_manifest_auf_der_platte_bleibt_ohne_bereich(ausschnitt):
    """Der Bereich gehoert zum Plan, nicht zum Material: ein ``build`` danach
    darf den Spielraum nicht auf den letzten Schnitt verengen."""
    gespeichert = Manifest.load(ausschnitt["project"].manifest)
    assert gespeichert.clips[0].clip.cache_offset == 0.0
    assert gespeichert.clips[0].clip.cache_duration == 0.0


@requires_ffmpeg
def test_render_findet_ein_fehlendes_intermediate_nicht_als_fehler(ausschnitt):
    """``check_sources_exist`` prueft bei Clips die Quelle, nicht das
    Intermediate — das entsteht ja erst beim Rendern."""
    from slideshow.build import check_sources_exist
    project, manifest, edit = (ausschnitt["project"], ausschnitt["manifest"],
                               ausschnitt["edit"])
    project.abs(manifest.clips[0].cache_path).unlink()
    check_sources_exist(project, edit, manifest)
