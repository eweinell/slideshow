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
import math
import subprocess
from pathlib import Path

import pytest

from slideshow.kenburns import (KBMotion, frames_arg, kb_filter, motion_key,
                                plan_motion, still_input_args, zoom_from_duration)
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


# --------------------------------------------------------------------------
# Schwenk
#
# Derselbe Grundsatz wie beim Zoom: aus der Dauer abgeleitet, nicht fest.
# --------------------------------------------------------------------------

def _weg(m) -> float:
    """Laenge der Schwenkstrecke von c0 nach c1."""
    return math.dist(m.c0, m.c1)


def test_schwenk_ergibt_sich_aus_der_dauer():
    """Die Rate in Reinform — deshalb ``through``.

    Unter ``center`` deckelt der Zoom den Weg (siehe
    ``test_der_zoom_deckelt_den_schwenk``), und dann misst dieser Test die
    Klemmung statt der Rate.
    """
    d = KBDefaults(pan_rate=0.03, pan_total=(0.05, 0.18), pan_anchor="through")

    assert _weg(plan_motion(0, 2.0, d)) == pytest.approx(0.06)
    assert _weg(plan_motion(0, 4.0, d)) == pytest.approx(0.12)
    assert _weg(plan_motion(0, 0.5, d)) == pytest.approx(0.05), "untere Klemmung"
    assert _weg(plan_motion(0, 60.0, d)) == pytest.approx(0.18), "obere Klemmung"


def test_schwenkgeschwindigkeit_ist_im_fenster_konstant():
    """Genau dafür ist die Rate da: 2 s und 6 s müssen gleich schnell wirken."""
    d = KBDefaults(pan_anchor="through")
    lo, hi = d.pan_total
    for dauer in (lo / d.pan_rate, 3.0, 4.0, hi / d.pan_rate):
        assert _weg(plan_motion(0, dauer, d)) / dauer == pytest.approx(d.pan_rate)


def test_alle_acht_richtungen_schwenken_gleich_weit():
    """Regression: unnormierte Diagonalen liefen 41 % weiter als die Geraden.

    Eine ganzzahlige Kennung wird unveraendert durchgereicht; das unterste Bit
    steuert den Zoom, die darueber die Schwenkrichtung. ``2 * i`` spricht damit
    Richtung ``i`` an — die Bitaufteilung steht hier ausgeschrieben, weil dieser
    Test der einzige ist, der sie braucht.
    """
    d = KBDefaults()
    wege = [_weg(plan_motion(2 * i, 4.0, d)) for i in range(8)]

    assert max(wege) == pytest.approx(min(wege)), \
        "keine Richtung darf schneller sein als die andern"
    assert len({(round(m.c1[0] - m.c0[0], 6), round(m.c1[1] - m.c0[1], 6))
                for m in (plan_motion(2 * i, 4.0, d) for i in range(8))}) == 8, \
        "trotzdem acht verschiedene Richtungen"


def test_schwenk_bleibt_im_bild():
    """Die Mitte darf nicht so weit wandern, dass der Ausschnitt herausläuft.

    Zulässig ist bei Zoom ``z`` eine Auslenkung von ``0.5 - 1/(2z)``; darüber
    klemmt der Filter, und der eingestellte Weg käme gar nicht an.
    """
    d = KBDefaults()
    for dauer in (2.0, 4.0, 6.0, 28.05):
        m = plan_motion(0, dauer, d)
        z_max = max(m.z0, m.z1)
        erlaubt = 0.5 - 1.0 / (2.0 * z_max)
        auslenkung = max(abs(m.c1[0] - 0.5), abs(m.c1[1] - 0.5))
        assert auslenkung <= erlaubt + 1e-9, \
            f"bei {dauer} s wird gegen den Bildrand geklemmt"


def test_altes_pan_amount_rendert_unveraendert_weiter():
    """``pan_amount`` war ein fester Weg — als Klemmung mit gleichen Grenzen
    ist genau das wieder herstellbar, unabhängig von der Dauer.

    Dazu gehört die alte Auslegung: eine Datei, die diesen Schlüssel noch
    nennt, ist älter als `pan_anchor` und meint `through`. Sonst wäre
    „bitgleich" nur die halbe Wahrheit.
    """
    alt = KBDefaults.model_validate({"pan_amount": 0.06})

    assert alt.pan_total == (0.12, 0.12)
    assert alt.pan_anchor == "through"
    for dauer in (2.0, 4.0, 12.0):
        m = plan_motion(0, dauer, alt)
        assert m.c0 == pytest.approx((0.44, 0.5))
        assert m.c1 == pytest.approx((0.56, 0.5))


def test_wer_beides_schreibt_bekommt_beides():
    """Die Übersetzung ist eine Vorbelegung, kein Zwang."""
    d = KBDefaults.model_validate({"pan_amount": 0.06, "pan_anchor": "center"})
    assert d.pan_anchor == "center"


# --------------------------------------------------------------------------
# Wo der Schwenk die Mitte beruehrt (`pan_anchor`)
#
# Der Richtungswechsel steckt nicht im Plan, sondern in der Klemmung des
# Filters. Geprueft wird deshalb die *sichtbare* Bahn: dieselbe Rechnung, die
# `zoompan_filter` als Ausdruck hinschreibt.
# --------------------------------------------------------------------------

def _sichtbare_mitte(m, achse: int = 0, schritte: int = 41) -> list[float]:
    """Die Bildmitte, die im Bild ankommt — nach ``max(0, min(1-1/z, …))``."""
    bahn = []
    for i in range(schritte):
        p = i / (schritte - 1)
        e = p * p * (3 - 2 * p) if m.ease == "smoothstep" else p
        zoom = m.z0 + (m.z1 - m.z0) * e
        c = m.c0[achse] + (m.c1[achse] - m.c0[achse]) * e
        breite = 1.0 / zoom
        bahn.append(max(0.0, min(1.0 - breite, c - breite / 2)) + breite / 2)
    return bahn


def _richtungswechsel(bahn: list[float]) -> int:
    return sum(1 for a, b, c in zip(bahn, bahn[1:], bahn[2:])
               if (b - a) * (c - b) < -1e-12)


def test_die_sichtbare_mitte_wechselt_die_richtung_nicht():
    """Die eigentliche Zusage — und sie gilt in beide Zoomrichtungen.

    ``2 * i`` spricht Schwenkrichtung ``i`` mit Hineinzoom an, ``2 * i + 1``
    dieselbe Richtung mit Herauszoom (das unterste Bit steuert den Zoom).
    """
    d = KBDefaults()
    for dauer in (2.0, 4.0, 6.0, 12.0):
        for key in range(16):
            m = plan_motion(key, dauer, d)
            for achse in (0, 1):
                assert _richtungswechsel(_sichtbare_mitte(m, achse)) == 0, \
                    f"Kennung {key}, {dauer} s, Achse {achse}"


def test_die_alte_auslegung_hatte_genau_diesen_wechsel():
    """Die Gegenprobe — sonst prüft der Test oben nur sich selbst."""
    m = plan_motion(0, 5.0, KBDefaults(pan_anchor="through"))
    assert _richtungswechsel(_sichtbare_mitte(m)) == 1


def test_der_schwenk_kommt_ganz_im_bild_an():
    """Nichts wird geklemmt: der geplante Weg ist auch der sichtbare."""
    d = KBDefaults()
    for dauer in (2.0, 4.0, 6.0, 12.0):
        m = plan_motion(0, dauer, d)
        bahn = _sichtbare_mitte(m)
        assert abs(bahn[-1] - bahn[0]) == pytest.approx(_weg(m), abs=1e-6)


def test_der_zoom_deckelt_den_schwenk():
    """Ein Zoom, der bei 1,0 anfängt, gibt nur ``0.5 - 1/(2z)`` her — dort ist
    der Ausschnitt das ganze Bild, und die Mitte kann sich nicht bewegen."""
    d = KBDefaults()
    for dauer in (2.0, 4.0, 6.0):
        m = plan_motion(0, dauer, d)
        erlaubt = 0.5 - 1.0 / (2.0 * max(m.z0, m.z1))
        assert _weg(m) == pytest.approx(min(d.pan_rate * dauer, erlaubt))


def test_der_schwenk_sieht_den_zoom_am_segment():
    """Ein `kb: {z: …}` muss die Deckelung mitbekommen — sonst plant der
    Schwenk gegen einen Zoom, den es gar nicht gibt."""
    d = KBDefaults()
    eng = plan_motion(0, 4.0, d, KBSpec(z=(1.0, 1.05)))
    weit = plan_motion(0, 4.0, d, KBSpec(z=(1.0, 1.30)))
    assert _weg(eng) < _weg(weit)
    assert _richtungswechsel(_sichtbare_mitte(eng)) == 0


def test_ein_ruhiges_ende_liegt_in_der_mitte():
    """Hineinzoom fängt in der Mitte an, Herauszoom hört dort auf."""
    d = KBDefaults()
    hinein = plan_motion(0, 4.0, d)          # unterstes Bit 0 -> hinein
    heraus = plan_motion(1, 4.0, d)
    assert hinein.z0 < hinein.z1 and heraus.z0 > heraus.z1
    assert hinein.c0 == pytest.approx((0.5, 0.5))
    assert heraus.c1 == pytest.approx((0.5, 0.5))


def test_verdrehte_grenzen_werden_abgewiesen():
    from pydantic import ValidationError
    for feld in ("pan_total", "zoom_total"):
        with pytest.raises(ValidationError):
            KBDefaults.model_validate({feld: (0.5, 0.1)})


def _bilder(n: int = 200) -> list[str]:
    return [f"cache/img_{i:03d}.jpg" for i in range(n)]


def test_zoomrichtung_wechselt_ueber_die_bildmenge():
    """Hundertmal hineinzuzoomen ermuedet.

    Seit die Richtung an der Kennung haengt statt an der Position, ist der
    Wechsel **statistisch** statt streng abwechselnd. Geprueft wird deshalb die
    Verteilung ueber eine realistische Bildmenge, nicht eine feste Reihenfolge:
    beide Richtungen muessen deutlich vorkommen.
    """
    d = KBDefaults(alternate=True)
    hinein = [plan_motion(src, 4.0, d).z1 > plan_motion(src, 4.0, d).z0
              for src in _bilder()]
    anteil = sum(hinein) / len(hinein)
    assert 0.35 < anteil < 0.65, f"einseitige Verteilung: {anteil:.0%} zoomen hinein"


def test_ohne_alternate_wird_immer_hineingezoomt():
    """Die Gegenprobe: der Schalter muss die Kennung ueberstimmen."""
    d = KBDefaults(alternate=False)
    assert all(plan_motion(src, 4.0, d).z1 > plan_motion(src, 4.0, d).z0
               for src in _bilder(40))


def test_die_bewegung_haengt_am_bild_nicht_an_seiner_position():
    """Der Kern von Entscheidung 7.

    Frueher leitete sich die Richtung aus dem Slot-Index ab. Ein an Position 41
    eingefuegtes Segment verschob damit die Bewegung **jedes** folgenden Bildes
    — und mit ihr dessen Cache-Key. Der halbe Film rendert neu, obwohl sich an
    ihm nichts geaendert hat.
    """
    d = KBDefaults()
    vorher = {src: plan_motion(src, 4.0, d).fingerprint() for src in _bilder(20)}

    # Umsortieren, Einfuegen, Loeschen — die Kennung bleibt, die Bewegung auch.
    nachher = {src: plan_motion(src, 4.0, d).fingerprint()
               for src in ["cache/title_malmoe_abc.jpg"] + list(reversed(_bilder(20)))}
    for src, fp in vorher.items():
        assert nachher[src] == fp


def test_die_kennung_ist_ueber_prozesse_hinweg_stabil():
    """Pythons ``hash()`` ist fuer Strings je Prozess gesalzen.

    Wer ihn hier einsetzte, bekaeme bei jedem Lauf andere Bewegungen und damit
    andere Cache-Keys — der Cache waere wertlos, ohne dass etwas auffiele. Der
    feste Wert nagelt das fest; er darf sich nur mit einer bewussten Aenderung
    an ``motion_key`` bewegen.
    """
    assert motion_key("cache/img_000.jpg") == 186163350704084497
    assert motion_key(7) == 7, "Ganzzahlen werden unveraendert durchgereicht"


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
