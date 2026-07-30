"""Abnahmekriterien 5, 6 und 13 — Caching, Teil-Neurenderung, Master.

5.  Zweiter ``render``-Lauf ohne Aenderungen: alle Segmente aus dem Cache.
6.  Aenderung eines einzelnen Bildes: genau ein Still-Segment plus die zwei
    angrenzenden ``xfade``-Segmente werden neu gerendert.
13. Der Master (``hvc1``, faststart) spielt ohne Nachkonvertierung.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from slideshow.build import plan_from_edit
from slideshow.cache import HashIndex
from slideshow.doctor import Capabilities
from slideshow.encoders import master_profile
from slideshow.planner import resolve
from slideshow.proc import ffprobe_json
from slideshow.render import (concat_and_mux, plan_jobs, render, render_segments,
                              verify_master, verify_uniform)

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def _profile(edit):
    """CPU-Encoder in Testaufloesung — schnell und ueberall verfuegbar."""
    w, h = edit.size
    return master_profile(Capabilities().encoder_choice(), width=w, height=h,
                          fps=edit.fps, codec="libx264")


def _jobs(built, caps):
    project, edit, plan = built["project"], built["edit"], built["plan"]
    segments = resolve(plan)
    index = HashIndex(project.cache / "hashindex.json")
    return plan_jobs(project, plan, edit, segments, profile=_profile(edit),
                     caps=caps, manifest=built["manifest"], index=index)


# --------------------------------------------------------------------------
# Cache-Keys — ohne Rendern pruefbar
# --------------------------------------------------------------------------

def test_keys_sind_stabil(built, caps):
    """Kriterium 5, strukturell: identische Eingaben -> identische Keys."""
    a = [j.key for j in _jobs(built, caps)]
    b = [j.key for j in _jobs(built, caps)]
    assert a == b
    assert len(set(a)) == len(a), "kollidierende Cache-Keys"


def test_bildaenderung_trifft_genau_drei_segmente(built, caps):
    """Kriterium 6: ein Still-Segment plus die zwei angrenzenden xfades."""
    project = built["project"]
    vorher = {j.index: j.key for j in _jobs(built, caps)}

    segments = resolve(built["plan"])
    nachbarn = {}
    for s in segments:
        if s.kind == "xfade":
            nachbarn.setdefault(id(s.a), []).append(s.index)
            nachbarn.setdefault(id(s.b), []).append(s.index)
    # Ein Still *mitten* in der Timeline — nur dort sind es zwei Blenden.
    ziel = next(s for s in segments
                if s.kind == "still" and len(nachbarn.get(id(s.slot), [])) == 2)
    quelle = project.abs(ziel.slot.intent.src)

    # Inhalt aendern (wie es `preprocess` nach einem korrigierten Original taete).
    data = bytearray(quelle.read_bytes())
    data[-1] = (data[-1] + 1) % 256
    quelle.write_bytes(bytes(data))

    nachher = {j.index: j.key for j in _jobs(built, caps)}
    geaendert = {i for i in vorher if vorher[i] != nachher[i]}

    betroffen = {ziel.index}
    for s in segments:
        if s.kind == "xfade" and (s.a is ziel.slot or s.b is ziel.slot):
            betroffen.add(s.index)

    assert geaendert == betroffen, (
        f"neu zu rendern: {sorted(geaendert)}, erwartet: {sorted(betroffen)}")
    assert len(betroffen) == 3, "in der Mitte der Timeline sind es genau drei"


def test_encoder_wechsel_invalidiert_alles(built, caps):
    """Abschnitt 11: sonst ueberleben stale Segmente eine Default-Aenderung."""
    project, edit, plan = built["project"], built["edit"], built["plan"]
    segments = resolve(plan)
    index = HashIndex(project.cache / "hashindex.json")
    a = [j.key for j in plan_jobs(project, plan, edit, segments, profile=_profile(edit),
                                  caps=caps, manifest=built["manifest"], index=index)]
    anderer = master_profile(Capabilities().encoder_choice(), width=edit.size[0],
                             height=edit.size[1], fps=edit.fps, codec="libx265")
    b = [j.key for j in plan_jobs(project, plan, edit, segments, profile=anderer,
                                  caps=caps, manifest=built["manifest"], index=index)]
    assert not set(a) & set(b)


def test_ffmpeg_version_geht_in_den_key_ein(built, caps):
    """Sonst ueberleben stale Segmente ein ffmpeg-Update unbemerkt."""
    project, edit, plan = built["project"], built["edit"], built["plan"]
    segments = resolve(plan)
    index = HashIndex(project.cache / "hashindex.json")
    a = [j.key for j in plan_jobs(project, plan, edit, segments, profile=_profile(edit),
                                  caps=caps, manifest=built["manifest"], index=index)]
    andere = Capabilities(**{**caps.__dict__, "ffmpeg_version": [99, 0]})
    b = [j.key for j in plan_jobs(project, plan, edit, segments, profile=_profile(edit),
                                  caps=andere, manifest=built["manifest"], index=index)]
    assert not set(a) & set(b)


# --------------------------------------------------------------------------
# Tatsaechliches Rendern
# --------------------------------------------------------------------------

@pytest.mark.slow
def test_voller_lauf_und_zweiter_lauf_aus_dem_cache(built, caps):
    """Kriterium 5, vollstaendig."""
    project, edit, plan = built["project"], built["edit"], built["plan"]
    out = project.out / "master.mp4"

    erst = render(project, edit, plan, caps=caps, manifest=built["manifest"], out=out,
                  codec="libx264", jobs_limit=2)
    assert erst.rendered > 0
    assert not erst.failures
    assert out.exists()

    zweit = render(project, edit, plan, caps=caps, manifest=built["manifest"], out=out,
                   codec="libx264", jobs_limit=2)
    assert zweit.rendered == 0, "der zweite Lauf haette nichts neu rendern duerfen"
    assert zweit.from_cache == zweit.total


@pytest.mark.slow
def test_master_hat_die_laenge_der_timeline(built, caps):
    """8.4: Abweichung Video<->Soll > 1 Frame ist ein Fehler."""
    project, edit, plan = built["project"], built["edit"], built["plan"]
    out = project.out / "master.mp4"
    stats = render(project, edit, plan, caps=caps, manifest=built["manifest"], out=out,
                   codec="libx264", jobs_limit=2)
    info = verify_master(out, expected_seconds=stats.timeline_seconds, fps=edit.fps)
    assert info["delta_frames"] <= 1.0


@pytest.mark.slow
def test_master_traegt_die_tonspur(built, caps):
    project, edit, plan = built["project"], built["edit"], built["plan"]
    out = project.out / "master.mp4"
    render(project, edit, plan, caps=caps, manifest=built["manifest"], out=out,
           codec="libx264", jobs_limit=2)
    streams = ffprobe_json(out).get("streams", [])
    assert any(s.get("codec_type") == "audio" for s in streams), "keine Tonspur im Master"
    assert any(s.get("codec_type") == "video" for s in streams)


@pytest.mark.slow
def test_range_rendert_nur_einen_ausschnitt(built, caps):
    project, edit, plan = built["project"], built["edit"], built["plan"]
    voll = len(resolve(plan))
    out = project.out / "ausschnitt.mp4"
    stats = render(project, edit, plan, caps=caps, manifest=built["manifest"], out=out,
                   codec="libx264", jobs_limit=2, range_spec="0:3")
    assert stats.total == 3 < voll
    assert out.exists()


@pytest.mark.slow
def test_hevc_master_traegt_hvc1_und_faststart(built, caps):
    """Kriterium 13: ohne hvc1 spielt HEVC-in-MP4 auf Apple-Geraeten nicht."""
    project, edit, plan = built["project"], built["edit"], built["plan"]
    out = project.out / "hevc.mp4"
    render(project, edit, plan, caps=caps, manifest=built["manifest"], out=out,
           codec="libx265", jobs_limit=2, range_spec="0:2")

    stream = next(s for s in ffprobe_json(out)["streams"] if s.get("codec_type") == "video")
    assert stream.get("codec_tag_string") == "hvc1", \
        f"Codec-Tag ist {stream.get('codec_tag_string')!r} statt 'hvc1'"
    assert _moov_vor_mdat(out), "moov liegt hinter mdat — faststart hat nicht gegriffen"


def _moov_vor_mdat(path: Path) -> bool:
    """Liest die Top-Level-Atome und prueft die Reihenfolge."""
    with open(path, "rb") as fh:
        pos = 0
        for _ in range(20):
            fh.seek(pos)
            head = fh.read(8)
            if len(head) < 8:
                return False
            size, kind = struct.unpack(">I4s", head)
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            if size == 1:                       # 64-Bit-Groesse
                size = struct.unpack(">Q", fh.read(8))[0]
            if size < 8:
                return False
            pos += size
    return False


# --------------------------------------------------------------------------
# Ausblende am Filmende
#
# Sie sitzt im letzten Segment, nicht im Mux — der haengt die Segmente mit
# `-c:v copy` aneinander, ein Filter dort wuerde den ganzen Master neu
# encodieren.
# --------------------------------------------------------------------------

def test_ausblende_trifft_nur_das_letzte_segment(built):
    from slideshow.render import _fade_suffix
    edit, plan = built["edit"], built["plan"]
    edit.defaults.fade_out = 1.5
    segments = resolve(plan)

    filter_je_segment = [_fade_suffix(plan, edit, s)[0] for s in segments]

    assert filter_je_segment[-1], "das letzte Segment blendet aus"
    assert not any(filter_je_segment[:-1]), "alle anderen bleiben unberuehrt"
    assert "fade=t=out" in filter_je_segment[-1]


def test_ausblende_wird_auf_das_letzte_segment_begrenzt(built):
    """Lieber eine kuerzere Blende als eine ueber die Segmentgrenze hinweg."""
    from slideshow.render import fade_frames
    edit, plan = built["edit"], built["plan"]
    edit.defaults.fade_out = 999.0

    assert fade_frames(plan, edit, segment_frames=30) == 30


def test_ausblende_laesst_sich_abschalten(built):
    from slideshow.render import _fade_suffix, fade_frames
    edit, plan = built["edit"], built["plan"]
    edit.defaults.fade_out = 0.0
    segments = resolve(plan)

    assert fade_frames(plan, edit, segment_frames=600) == 0
    assert _fade_suffix(plan, edit, segments[-1])[0] == ""


def test_ausblende_geht_in_den_cache_key_ein(built, caps):
    """Sonst liefert ein zweiter Lauf das alte, ungeblendete Segment aus."""
    project, edit, plan = built["project"], built["edit"], built["plan"]
    letztes = resolve(plan)[-1:]
    index = HashIndex(project.cache / "hashindex.json")

    edit.defaults.fade_out = 0.0
    ohne = plan_jobs(project, plan, edit, letztes, profile=_profile(edit), caps=caps,
                     manifest=built["manifest"], index=index)[0].key
    edit.defaults.fade_out = 1.5
    mit = plan_jobs(project, plan, edit, letztes, profile=_profile(edit), caps=caps,
                    manifest=built["manifest"], index=index)[0].key

    assert ohne != mit


@pytest.mark.slow
def test_uneinheitliche_segmente_brechen_vor_dem_concat_ab(built, caps, tmp_path):
    """8.4: lieber praezise abbrechen als einen kaputten Master produzieren."""
    from slideshow.errors import SlideshowError
    project, edit, plan = built["project"], built["edit"], built["plan"]
    segments = resolve(plan)[:2]
    index = HashIndex(project.cache / "hashindex.json")
    jobs = plan_jobs(project, plan, edit, segments, profile=_profile(edit), caps=caps,
                     manifest=built["manifest"], index=index)
    render_segments(jobs, workers=2)

    # Zweites Segment absichtlich in anderer Aufloesung ueberschreiben.
    from slideshow.proc import run
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=s=160x90:r=60", "-t", "0.2", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(jobs[1].out)])

    with pytest.raises(SlideshowError) as exc:
        verify_uniform(jobs)
    assert "width" in str(exc.value) or "height" in str(exc.value)
