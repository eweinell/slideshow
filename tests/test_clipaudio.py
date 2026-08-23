"""Nachtrag 5.5 — Originalton der Videoclips (docs/originalton-clips.md).

Die drei Zusagen, die dieses Modul absichert:

1. **Nur auf Anforderung, und die steht in** ``overrides.yaml``. Ohne sie
   traegt der Master nur die Musik; *welche* Clips ihren Ton behalten und mit
   welchen Pegeln, steht dort unter ``defaults:`` und ``media:``. Dass die
   Datei wirkt, prueft ``test_overrides.py`` — hier geht es um das, was der
   Ton danach im Master tut.
2. **Die Musik geht dort weg.** Waehrend eines Clips mit Originalton faellt
   der Musikpegel auf ``defaults.clip_audio.music`` (Default 0 = stumm).
3. **Es kostet keine Neurenderung.** Der Ton wird beim Muxen untergemischt,
   nicht in die Segmente encodiert — die Cache-Keys aendern sich nicht.

Gemessen wird im fertigen Master, nicht an Kommandozeilen: der Clipton liegt
bei 300 Hz, die Musikklicks bei 1000/1400 Hz, und beide lassen sich pro
Zeitfenster getrennt auswerten.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from slideshow.cache import HashIndex
from slideshow.clipaudio import (atempo_chain, collect_cues, filtergraph,
                                 music_volume_expr, wants_audio)
from slideshow.doctor import Capabilities
from slideshow.encoders import master_profile
from slideshow.models import ClipSegment, EditList, MediaOverride, Overrides
from slideshow.planner import Intent, resolve, to_time
from slideshow.proc import run
from slideshow.render import plan_jobs, render

from .conftest import build_project, requires_ffmpeg

#: Frequenz des Clip-Fixtures und der Musikklicks.
TON_HZ = 300.0
MUSIK_HZ = 1000.0


# --------------------------------------------------------------------------
# Ohne ffmpeg pruefbar
# --------------------------------------------------------------------------

def test_atempo_kette_trifft_die_geschwindigkeit():
    """Der Ton muss dieselbe Streckung mitmachen wie das Bild (Abschnitt 7)."""
    assert atempo_chain(1.0) == []
    for speed in (0.8333333, 1.2, 2.5, 0.3, 4.0):
        faktoren = [float(f.split("=")[1]) for f in atempo_chain(speed)]
        assert faktoren, f"{speed} ergab keine Kette"
        assert all(0.5 <= f <= 2.0 for f in faktoren), \
            f"{faktoren} verlaesst den gueltigen Bereich von atempo"
        assert np.prod(faktoren) == pytest.approx(speed, rel=1e-9)


def test_ohne_anforderung_kein_originalton():
    """Zusage 1, an der Praezedenz: Segment schlaegt Default."""
    from slideshow.models import ClipAudioDefaults
    aus, an = ClipAudioDefaults(), ClipAudioDefaults(enabled=True)
    clip = Intent(kind="clip", src="cache/c.mov")

    assert not wants_audio(clip, aus)
    assert wants_audio(clip, an)
    # Das Segment darf den globalen Schalter in beide Richtungen ueberstimmen.
    assert wants_audio(Intent(kind="clip", src="c", audio=True), aus)
    assert not wants_audio(Intent(kind="clip", src="c", audio=False), an)
    # Ein Standbild hat nie einen Originalton.
    assert not wants_audio(Intent(kind="still", src="cache/i.jpg"), an)


def test_musikpegel_bleibt_ausserhalb_der_clips_unangetastet():
    """Ohne Clipton gibt es keinen Grund, an der Musik zu drehen."""
    assert music_volume_expr([]) is None


def test_filtergraph_adressiert_die_richtigen_eingaenge():
    from slideshow.clipaudio import ClipAudioCue
    cues = [ClipAudioCue(0, "cache/a.audio.flac", start=2.0, duration=3.0, offset=1.0),
            ClipAudioCue(3, "cache/b.audio.flac", start=9.0, duration=2.0, offset=0.0)]
    graph, label = filtergraph(cues, first_input=2, music_input=1)
    assert label == "[aout]"
    assert "[1:a]" in graph and "[2:a]" in graph and "[3:a]" in graph
    assert "adelay=2000:all=1" in graph and "adelay=9000:all=1" in graph
    assert "amix=inputs=3" in graph


def test_jeder_cue_senkt_die_musik_um_seinen_eigenen_betrag():
    """Der eine Clip verdient Stille, der naechste laesst die Musik stehen.

    Die Absenkungen werden summiert statt einer globalen Zahl gefolgt — das
    geht nur, weil die Cues auf der Timeline disjunkt sind (Slots ueberlappen
    nicht).
    """
    from slideshow.clipaudio import ClipAudioCue
    stumm = ClipAudioCue(0, "a", start=2.0, duration=4.0, offset=0.0, music=0.0)
    leise = ClipAudioCue(2, "b", start=10.0, duration=4.0, offset=0.0, music=0.25)
    steht = ClipAudioCue(4, "c", start=20.0, duration=4.0, offset=0.0, music=1.0)

    expr = music_volume_expr([stumm, leise, steht])
    assert "1.000000*" in expr and "0.750000*" in expr
    # Ein Clip, unter dem die Musik unveraendert weiterlaeuft, taucht gar
    # nicht auf — sonst stuende dort eine Absenkung um null.
    assert expr.count("max(0,min(1,") == 2
    assert music_volume_expr([steht]) is None


def test_die_schlussblende_gilt_fuer_den_fertigen_mix():
    """Sonst liefe ein Clip am Filmende weiter, waehrend das Bild schwarz ist."""
    from slideshow.clipaudio import ClipAudioCue
    cue = ClipAudioCue(0, "a", start=8.0, duration=4.0, offset=0.0)
    graph, _ = filtergraph([cue], first_input=2, music_input=1,
                           fade_out=(10.5, 1.5))
    # Hinter amix, nicht in einem einzelnen Zweig — und hinter apad, sonst
    # liefe die Blende ins Leere.
    assert graph.endswith("apad,afade=t=out:st=10.500000:d=1.500000[aout]")


def test_blende_passt_immer_in_den_cue():
    """Ein 0,2-s-Schnipsel vertraegt keine 0,25-s-Blende."""
    from slideshow.clipaudio import ClipAudioCue
    kurz = ClipAudioCue(0, "a", start=0.0, duration=0.2, offset=0.0, fade=0.25)
    assert kurz.effective_fade() == pytest.approx(0.1)
    graph, _ = filtergraph([kurz], first_input=1, music_input=None)
    assert "afade=t=in:st=0:d=0.100000" in graph


# --------------------------------------------------------------------------
# Gegen echtes Material
# --------------------------------------------------------------------------

def _mit_ton(project, images, audio, caps, clip: Path, **kwargs):
    """Projekt aus einem Bilderstapel und *einem* Clip mit Originalton.

    Der Clip steht bewusst an zweiter Stelle: davor und dahinter laeuft Musik,
    und nur so ist ueberhaupt messbar, ob sie an genau der richtigen Stelle
    weggeht.
    """
    def reihenfolge(manifest):
        clips = [m.id for m in manifest.media if m.kind == "clip"]
        bilder = [m.id for m in manifest.media if m.kind == "image"]
        return [bilder[0], *clips, *bilder[1:]]

    return build_project(project, images=images, audio=audio, caps=caps,
                         clips={"ton": clip}, order=reihenfolge, **kwargs)


def _feinschliff(**felder):
    """Ein ``overrides.yaml``-Eintrag fuer den einen Clip des Projekts."""
    def bauen(manifest):
        clip = next(m for m in manifest.media if m.kind == "clip")
        return Overrides(media={clip.id: MediaOverride.model_validate(felder)})
    return bauen


@pytest.fixture
def mit_ton(project, images, click_two_songs, caps, clip_with_audio):
    """Der Clip behaelt seinen Ton — angefordert wie im echten Projekt, ueber
    einen Eintrag unter ``media:``."""
    if clip_with_audio is None:
        pytest.skip("Clip-Fixture mit Ton konnte nicht erzeugt werden")
    manifest, edit, plan, cov = _mit_ton(
        project, images, click_two_songs, caps, clip_with_audio,
        overrides=_feinschliff(audio=True))
    return {"project": project, "manifest": manifest, "edit": edit, "plan": plan}


@requires_ffmpeg
def test_preprocess_legt_die_tonspur_neben_das_intermediate(mit_ton):
    """Ohne extrahierte Tonspur gibt es spaeter nichts zu mischen."""
    project, manifest = mit_ton["project"], mit_ton["manifest"]
    clip = manifest.clips[0]
    assert clip.clip.has_audio, "das Fixture hat keinen Ton — Test wertlos"
    assert clip.audio_cache_path, "preprocess hat keine Tonspur abgelegt"
    assert project.abs(clip.audio_cache_path).exists()


@requires_ffmpeg
def test_build_schreibt_die_absicht_in_die_edit_list(mit_ton, tmp_path):
    """Prinzip 1: der Wunsch steht in der Datei und ueberlebt den Roundtrip."""
    edit = mit_ton["edit"]
    clips = [s for s in edit.segments if isinstance(s, ClipSegment)]
    assert clips and all(s.audio is True for s in clips)

    pfad = tmp_path / "edit.yaml"
    edit.save(pfad)
    assert "audio: true" in pfad.read_text(encoding="utf-8")
    assert all(s.audio is True for s in EditList.load(pfad).segments
               if isinstance(s, ClipSegment))


@requires_ffmpeg
def test_pegel_je_clip_landen_am_cue(project, images, click_two_songs, caps,
                                     clip_with_audio):
    """Jeder Wert gilt auch je Clip — der eine verdient Stille, der naechste
    laesst die Musik stehen."""
    if clip_with_audio is None:
        pytest.skip("Clip-Fixture mit Ton konnte nicht erzeugt werden")
    manifest, edit, plan, _c = _mit_ton(
        project, images, click_two_songs, caps, clip_with_audio,
        overrides=_feinschliff(audio=True, gain=0.6, music=0.25, fade=0.4))

    cue = collect_cues(plan, edit, manifest)[0][0]
    assert (cue.gain, cue.music, cue.fade) == (0.6, 0.25, 0.4)
    # Und der Musikpegel folgt dem Cue, nicht einer globalen Zahl.
    assert "0.750000" in music_volume_expr([cue])


@requires_ffmpeg
def test_ohne_eintrag_bleibt_die_edit_list_stumm(project, images, click_two_songs,
                                                 caps, clip_with_audio):
    """Zusage 1: der Originalton ist kein Default."""
    if clip_with_audio is None:
        pytest.skip("Clip-Fixture mit Ton konnte nicht erzeugt werden")
    _m, edit, plan, _c = _mit_ton(project, images, click_two_songs, caps,
                                  clip_with_audio)
    clips = [s for s in edit.segments if isinstance(s, ClipSegment)]
    assert clips and all(s.audio is None for s in clips)
    cues, warnungen = collect_cues(plan, edit, _m)
    assert cues == [] and warnungen == []


@requires_ffmpeg
def test_cues_liegen_auf_ihren_slots(mit_ton):
    """Der Ton muss dort stehen, wo das Bild steht — sonst laeuft er weg."""
    plan, edit, manifest = mit_ton["plan"], mit_ton["edit"], mit_ton["manifest"]
    cues, warnungen = collect_cues(plan, edit, manifest)
    assert warnungen == []
    assert len(cues) == 1

    cue = cues[0]
    slot = plan.slots[cue.slot_index]
    assert slot.intent.kind == "clip"
    assert cue.start == pytest.approx(to_time(slot.start_f, plan.fps))
    assert cue.duration == pytest.approx(to_time(slot.frames, plan.fps))
    # Startpunkt in der Tonspur: dieselbe Zeitbasis wie das Bild-Intermediate.
    versatz = manifest.clips[0].clip.cache_offset
    assert cue.offset == pytest.approx(max(0.0, slot.clip_in - versatz))


@requires_ffmpeg
def test_gestreckter_clip_dueckt_die_musik_nicht_ueber_sein_ende_hinaus(mit_ton):
    """Der Planer streckt das letzte Segment auf die Timelinelaenge; das Bild
    haelt dann sein letztes Frame. Der Ton kann das nicht — er ist zu Ende, und
    die Musik muss zurueckkommen."""
    plan, edit, manifest = mit_ton["plan"], mit_ton["edit"], mit_ton["manifest"]
    slot = next(s for s in plan.slots if s.intent.kind == "clip")
    verfuegbar = manifest.clips[0].clip.cache_duration

    slot.end_f = slot.start_f + int(round((verfuegbar + 20.0) * plan.fps))
    cue = collect_cues(plan, edit, manifest)[0][0]
    assert cue.duration == pytest.approx(verfuegbar - cue.offset, abs=1e-6)
    assert cue.duration < to_time(slot.frames, plan.fps)


@requires_ffmpeg
def test_fehlende_tonspur_wird_gemeldet_statt_verschluckt(mit_ton):
    """Ein angeforderter, aber nicht vorhandener Ton ist eine Warnung wert."""
    plan, edit, manifest = mit_ton["plan"], mit_ton["edit"], mit_ton["manifest"]
    manifest.clips[0].audio_cache_path = ""
    cues, warnungen = collect_cues(plan, edit, manifest)
    assert cues == []
    assert len(warnungen) == 1 and "preprocess" in warnungen[0]


@requires_ffmpeg
def test_originalton_macht_kein_segment_ungueltig(mit_ton, caps):
    """Zusage 3: die Segmente sind stumm, also aendert der Ton ihre Keys nicht.

    Das ist der Grund, den Ton erst beim Muxen unterzumischen: die Entscheidung
    laesst sich nach einem vier Stunden langen Renderlauf noch umwerfen.
    """
    project, edit, plan = mit_ton["project"], mit_ton["edit"], mit_ton["plan"]
    segments = resolve(plan)
    w, h = edit.size
    profil = master_profile(Capabilities().encoder_choice(), width=w, height=h,
                            fps=edit.fps, codec="libx264")
    index = HashIndex(project.cache / "hashindex.json")

    def keys(e):
        return [j.key for j in plan_jobs(project, plan, e, segments, profile=profil,
                                         caps=caps, manifest=mit_ton["manifest"],
                                         index=index)]

    mit = keys(edit)
    stumm = edit.model_copy(deep=True)
    stumm.defaults.clip_audio.enabled = False
    for seg in stumm.segments:
        if isinstance(seg, ClipSegment):
            seg.audio = None
    assert keys(stumm) == mit, "der Originalton hat einen Segment-Key veraendert"
    assert all("-an" in j.cmd for j in plan_jobs(
        project, plan, edit, segments, profile=profil, caps=caps,
        manifest=mit_ton["manifest"], index=index))


# --------------------------------------------------------------------------
# Der Master selbst
# --------------------------------------------------------------------------

def _master_audio(path: Path, tmp: Path) -> tuple[np.ndarray, int]:
    """Tonspur des Masters als Monosignal."""
    import soundfile as sf
    wav = tmp / "master_audio.wav"
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)],
        timeout=600)
    data, sr = sf.read(str(wav), dtype="float32", always_2d=False)
    return (np.asarray(data), sr)


def _pegel(samples: np.ndarray, sr: int, freq: float, start: float, end: float) -> float:
    """Amplitude bei ``freq`` im Fenster ``[start, end)`` (Goertzel von Hand)."""
    a, b = int(max(0.0, start) * sr), int(end * sr)
    seg = samples[a:min(b, len(samples))]
    if len(seg) < 256:
        return 0.0
    t = np.arange(len(seg)) / sr
    return float(abs(np.dot(seg, np.exp(-2j * np.pi * freq * t))) / len(seg))


@pytest.mark.slow
@requires_ffmpeg
def test_master_traegt_den_originalton_und_dueckt_die_musik(mit_ton, caps, tmp_path):
    """Zusage 1 und 2, gemessen im fertigen Master."""
    project, edit, plan = mit_ton["project"], mit_ton["edit"], mit_ton["plan"]
    out = project.out / "mit_ton.mp4"
    stats = render(project, edit, plan, caps=caps, manifest=mit_ton["manifest"],
                   out=out, codec="libx264", jobs_limit=2)
    assert stats.clip_audio == 1
    assert not stats.failures

    cue = collect_cues(plan, edit, mit_ton["manifest"])[0][0]
    samples, sr = _master_audio(out, tmp_path)

    # Fenster *innerhalb* der Blenden, damit die Rampen nicht mitgemessen werden.
    rand = cue.effective_fade() + 0.05
    innen = (cue.start + rand, cue.end - rand)
    davor = (max(0.0, cue.start - 2.0), cue.start - rand)

    ton_innen = _pegel(samples, sr, TON_HZ, *innen)
    ton_davor = _pegel(samples, sr, TON_HZ, *davor)
    assert ton_innen > 10 * max(ton_davor, 1e-6), \
        f"Originalton nicht im Master: {ton_innen:.5f} innen, {ton_davor:.5f} davor"

    musik_innen = _pegel(samples, sr, MUSIK_HZ, *innen)
    musik_davor = _pegel(samples, sr, MUSIK_HZ, *davor)
    assert musik_davor > 1e-4, "die Musik fehlt schon ausserhalb des Clips"
    assert musik_innen < musik_davor / 5, \
        f"Musik laeuft unter dem Clip weiter: {musik_innen:.5f} vs {musik_davor:.5f}"


@pytest.mark.slow
@requires_ffmpeg
def test_ohne_anforderung_kein_ton_im_master(project, images, click_two_songs, caps,
                                             clip_with_audio, tmp_path):
    """Der Gegenprobe wegen: derselbe Clip, nur ohne ``audio: true``."""
    if clip_with_audio is None:
        pytest.skip("Clip-Fixture mit Ton konnte nicht erzeugt werden")
    manifest, edit, plan, _c = _mit_ton(project, images, click_two_songs, caps,
                                        clip_with_audio)
    out = project.out / "stumm.mp4"
    stats = render(project, edit, plan, caps=caps, manifest=manifest, out=out,
                   codec="libx264", jobs_limit=2)
    assert stats.clip_audio == 0

    slot = next(s for s in plan.slots if s.intent.kind == "clip")
    start, end = (to_time(slot.start_f, plan.fps), to_time(slot.end_f, plan.fps))
    samples, sr = _master_audio(out, tmp_path)
    assert _pegel(samples, sr, TON_HZ, start + 0.3, end - 0.3) < 1e-3
    assert _pegel(samples, sr, MUSIK_HZ, start + 0.3, end - 0.3) > 1e-4, \
        "die Musik haette hier unveraendert weiterlaufen muessen"


# --------------------------------------------------------------------------
# Kdenlive-Export
# --------------------------------------------------------------------------

@requires_ffmpeg
def test_mlt_export_zeigt_den_originalton_als_eigene_spur(mit_ton):
    """Wer in Kdenlive nachschneidet, muss den Ton sehen — sonst schneidet er
    ihn versehentlich weg."""
    import xml.etree.ElementTree as ET
    from slideshow.mlt import export_mlt

    root = ET.fromstring(export_mlt(mit_ton["project"], mit_ton["edit"],
                                    mit_ton["manifest"]))
    pl = root.find(".//playlist[@id='playlist_clipaudio']")
    assert pl is not None, "keine Spur mit Originalton im Projekt"
    assert len(pl.findall("entry")) == 1
    assert root.find(".//tractor/track[@producer='playlist_clipaudio']") is not None


@requires_ffmpeg
def test_mlt_export_ohne_originalton_bleibt_unveraendert(project, images,
                                                        click_two_songs, caps,
                                                        clip_with_audio):
    import xml.etree.ElementTree as ET
    from slideshow.mlt import export_mlt
    if clip_with_audio is None:
        pytest.skip("Clip-Fixture mit Ton konnte nicht erzeugt werden")
    manifest, edit, _p, _c = _mit_ton(project, images, click_two_songs, caps,
                                      clip_with_audio)
    root = ET.fromstring(export_mlt(project, edit, manifest))
    assert root.find(".//playlist[@id='playlist_clipaudio']") is None
