"""Abnahmekriterium 12 — Bewegungskontinuitaet durch die Blende.

    Waehrend einer Ueberblendung laeuft die Ken-Burns-Bewegung beider Bilder
    sichtbar weiter — kein Einfrieren, kein Positionssprung an den
    Fenstergrenzen ``t ± T/2``.

Die harte Fassung dieses Kriteriums: rendert man die Frames ``[k, N)`` einer
Bewegung als eigenes Segment mit Frame-Offset ``k``, muessen sie **bitgleich**
zu den Frames ``[k, N)`` des durchgehenden Laufs sein. Ist das erfuellt, kann
es an der Fenstergrenze per Konstruktion keinen Sprung geben.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from slideshow.kenburns import (KBMotion, frames_arg, kb_filter, plan_motion,
                                still_input_args, zoom_from_duration)
from slideshow.models import Defaults, KBDefaults, KBSpec, Region
from slideshow.planner import Intent, apply_transitions, plan_slots, resolve

from .conftest import requires_ffmpeg

SIZE = (320, 180)
FPS = 60.0


# --------------------------------------------------------------------------
# Bewegungsplanung
# --------------------------------------------------------------------------

def test_zoom_ergibt_sich_aus_der_dauer():
    """8.1: der Zoom-Betrag muss sich aus der Dauer ergeben, nicht umgekehrt."""
    d = KBDefaults(zoom_rate=0.05, zoom_total=(0.08, 0.30))
    kurz = zoom_from_duration(2.0, d)
    lang = zoom_from_duration(10.0, d)
    assert kurz < lang, "ein laengeres Bild braucht mehr Zoomweg"
    assert zoom_from_duration(0.5, d) == pytest.approx(1.08), "untere Klemmung"
    assert zoom_from_duration(60.0, d) == pytest.approx(1.30), "obere Klemmung"


def test_zoomrichtung_alterniert():
    """Hundertmal hineinzuzoomen ermuedet."""
    d = KBDefaults(alternate=True)
    richtungen = [plan_motion(i, 4.0, d).z1 > plan_motion(i, 4.0, d).z0 for i in range(6)]
    assert richtungen == [True, False, True, False, True, False]


def test_explizites_kb_ueberschreibt_die_ableitung():
    d = KBDefaults()
    m = plan_motion(0, 4.0, d, KBSpec(z=(1.0, 1.5), c=(0.1, 0.2, 0.9, 0.8)))
    assert (m.z0, m.z1) == (1.0, 1.5)
    assert m.c0 == (0.1, 0.2) and m.c1 == (0.9, 0.8)


def test_bewegung_ist_deterministisch():
    """Sonst aendert sich der Cache-Key bei jedem Lauf."""
    d = KBDefaults()
    a = plan_motion(7, 4.0, d).fingerprint()
    b = plan_motion(7, 4.0, d).fingerprint()
    assert a == b


# --------------------------------------------------------------------------
# Offsets in der Timeline
# --------------------------------------------------------------------------

def test_xfade_segment_setzt_die_bewegung_beider_nachbarn_fort():
    """Der Offset jedes Nachbarn muss auf seine *volle sichtbare Spanne*
    bezogen sein, nicht auf den exklusiven Anteil."""
    regions = [Region(type="beat", start=0.0, end=40.0, bpm=120.0, offset=0.0)]
    intents = [Intent(kind="still", src=f"img{i}.jpg", index=i) for i in range(6)]
    defaults = Defaults()
    plan = plan_slots(regions, intents, defaults, fps=FPS, total_frames=int(40 * FPS))
    apply_transitions(plan, defaults)
    segments = resolve(plan)

    xfades = [s for s in segments if s.kind == "xfade"]
    assert xfades, "ohne Uebergaenge ist das Kriterium nicht pruefbar"
    for seg in xfades:
        a_off = seg.start_f - seg.a_visible[0]
        b_off = seg.start_f - seg.b_visible[0]
        assert a_off > 0, "der abgehende Nachbar ist mitten in seiner Bewegung"
        assert b_off == 0, "der ankommende Nachbar beginnt genau hier"
        # Die Blende liegt zentriert ueber dem Schnitt.
        cut = seg.a.end_f
        assert seg.start_f == cut - seg.frames // 2
        assert seg.end_f == cut + seg.frames // 2


def test_exklusiver_anteil_und_blende_ueberlappen_sich_nicht():
    regions = [Region(type="beat", start=0.0, end=40.0, bpm=120.0, offset=0.0)]
    intents = [Intent(kind="still", src=f"img{i}.jpg", index=i) for i in range(6)]
    defaults = Defaults()
    plan = plan_slots(regions, intents, defaults, fps=FPS, total_frames=int(40 * FPS))
    apply_transitions(plan, defaults)
    segments = resolve(plan)
    for a, b in zip(segments, segments[1:]):
        assert a.end_f == b.start_f


# --------------------------------------------------------------------------
# Der eigentliche Nachweis: bitgleiche Fortsetzung
# --------------------------------------------------------------------------

def _render_hashes(src: Path, *, engine: str, total: int, offset: int,
                   frames: int, size: tuple[int, int] = SIZE) -> list[str]:
    m = plan_motion(0, total / FPS, KBDefaults(engine=engine))
    m = KBMotion(m.z0, m.z1, m.c0, m.c1, m.ease, engine)
    vf = kb_filter(m, total_frames=total, offset=offset, size=size, fps=FPS)
    cmd = ["ffmpeg", "-hide_banner", "-v", "error",
           *still_input_args(str(src), fps=FPS, frames=frames),
           "-vf", vf, *frames_arg(frames),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    px = size[0] * size[1] * 3
    return [hashlib.md5(raw[i * px:(i + 1) * px]).hexdigest()
            for i in range(len(raw) // px)]


@requires_ffmpeg
@pytest.mark.slow
@pytest.mark.parametrize("engine", ["zoompan", "scale16"])
def test_offset_setzt_die_bewegung_bitgleich_fort(images, engine):
    """Kriterium 12, harte Fassung: kein Positionssprung an der Fenstergrenze."""
    src = images[0]
    total, cut = 120, 60
    voll = _render_hashes(src, engine=engine, total=total, offset=0, frames=total)
    rest = _render_hashes(src, engine=engine, total=total, offset=cut, frames=total - cut)

    assert len(voll) == total and len(rest) == total - cut
    unterschiede = [i for i in range(total - cut) if voll[cut + i] != rest[i]]
    assert not unterschiede, (
        f"{len(unterschiede)} von {total - cut} Frames weichen ab — die Bewegung "
        f"springt an der Fenstergrenze (Engine {engine})")


@requires_ffmpeg
@pytest.mark.slow
@pytest.mark.parametrize("engine", ["zoompan", "scale16"])
def test_bewegung_friert_nicht_ein(images, engine):
    """Eine Blende zwischen zwei Standframes ist ein sichtbarer Fehler und
    explizit nicht zulaessig (8.2).

    Geprueft wird die *mittlere* Haelfte der Bewegung. An den Raendern ist die
    Geschwindigkeit durch den Smoothstep gewollt nahe null, dort sind
    Wiederholungen kein Fehler, sondern die Absicht.

    Die Breite ist bewusst 1280 und nicht 320: ``scale16`` skaliert auf
    ganzzahlige Pixelmasse, und bei 320 px Breite gibt ein 10-%-Zoom nur rund
    32 unterscheidbare Stufen her. Der Test wuerde dann die Testaufloesung
    messen statt der Bewegung.
    """
    n = 120
    voll = _render_hashes(images[0], engine=engine, total=n, offset=0, frames=n,
                          size=(1280, 720))
    mitte = voll[n // 4:3 * n // 4]

    # Nicht "jeder Frame verschieden", sondern "kein Stillstand": ``zoompan``
    # schneidet x/y auf ganze Pixel (das im Briefing genannte Zittern), sodass
    # vereinzelt zwei gleiche Frames aufeinander folgen. Sichtbar wird das erst
    # als *Kette*. ``scale16`` erreicht hier null Wiederholungen.
    laengster, lauf = 1, 1
    for a, b in zip(mitte, mitte[1:]):
        lauf = lauf + 1 if a == b else 1
        laengster = max(laengster, lauf)
    assert laengster <= 2, (
        f"bis zu {laengster} gleiche Frames hintereinander in der schnellen "
        f"Bewegungsmitte — die Bewegung stockt sichtbar (Engine {engine})")
