"""Feinschliff — ``overrides.yaml`` (``docs/edit-yaml.md``, Abschnitt "Der
Feinschliff").

Geprueft wird die eine Zusage, um die es bei dieser Datei geht: **ein
nachgereichtes Bild kostet die Handarbeit nicht mehr.** Bis hierher hatte man
die Wahl zwischen einem Neubau, der jede Standzeit und jede Fahrt verwarf, und
einem Film ohne das neue Bild.

Alles ohne ffmpeg: gearbeitet wird auf einem von Hand gestellten Manifest und
einer von Hand gestellten Regionenkarte — dieselbe Anlage wie in
``test_order.py``, damit die Abfolge *bekannt* ist statt bloss plausibel.
"""

from __future__ import annotations

import copy

import pytest

from slideshow.build import build_edit_list
from slideshow.errors import SchemaError
from slideshow.models import (BeatMap, Chapter, ClipInfo, ClipSegment, Defaults,
                              ImageInfo, KBSpec, Manifest, MediaItem,
                              MediaOverride, Overrides, Region, StillSegment,
                              TitleSegment, XfadeSegment, dump_edit_yaml,
                              hand_edited)
from slideshow.overrides import (diff_edit, dump_overrides_yaml, merge_overrides,
                                 resolve_media)

FPS = 60.0
T0 = 1_753_000_000                   # 20. Juli 2025, lokale Zeit
DAUER = 90.0


# --------------------------------------------------------------------------
# Material von Hand
# --------------------------------------------------------------------------

def _manifest(n: int = 8, *, mit_clip: bool = False) -> Manifest:
    media = [MediaItem(id=f"img_{i:03d}", path=f"src/img_{i:03d}.jpg", kind="image",
                       cache_path=f"cache/img_{i:03d}.jpg", time_source="exif",
                       capture_time=T0 + i * 3600,
                       image=ImageInfo(width=6000, height=4000))
             for i in range(n)]
    if mit_clip:
        media.append(MediaItem(
            id="clip_001", path="src/clip_001.mp4", kind="clip",
            cache_path="cache/clip_001.mov", time_source="container",
            capture_time=T0 + n * 3600,
            clip=ClipInfo(duration=12.0, effective_duration=12.0,
                          cache_offset=0.0, cache_duration=12.0)))
    m = Manifest(media=media, fps_suggestion=FPS)
    m.audio.file = "cache/mix.flac"
    m.audio.duration = DAUER
    return m


def _bauen(manifest: Manifest, *, overrides: Overrides | None = None,
           chapters=None, defaults: Defaults | None = None):
    regions = [Region(type="beat", start=0.0, end=DAUER, bpm=120.0, offset=0.0,
                      conf=0.9)]
    beatmap = BeatMap(audio={"file": manifest.audio.file, "duration": DAUER},
                      regions=regions)
    if defaults is None:
        defaults = overrides.merged_defaults() if overrides else Defaults()
    return build_edit_list(None, manifest, beatmap, defaults=defaults, fps=FPS,
                           size=(1280, 720), chapters=chapters, overrides=overrides)


def _still(edit, mid: str) -> StillSegment:
    treffer = [s for s in edit.segments
               if isinstance(s, StillSegment) and s.src.endswith(f"{mid}.jpg")]
    assert treffer, f"{mid} steht nicht in der Edit-List"
    return treffer[0]


def _blende_vor(edit, mid: str) -> XfadeSegment | None:
    """Der Uebergang unmittelbar vor dem Segment von ``mid`` — oder ``None``."""
    for i, seg in enumerate(edit.segments):
        if getattr(seg, "src", "").endswith(f"{mid}.jpg") and i:
            vor = edit.segments[i - 1]
            return vor if isinstance(vor, XfadeSegment) else None
    return None


# --------------------------------------------------------------------------
# Hin: die Datei wirkt
# --------------------------------------------------------------------------

def test_eine_laengere_standzeit_aus_dem_feinschliff_wirkt():
    manifest = _manifest()
    edit, _plan, _cov = _bauen(manifest, overrides=Overrides(
        media={"img_003": MediaOverride(dur=8.0)}))
    assert _still(edit, "img_003").dur == 8.0
    # und nur dort: die Nachbarn stehen weiter im Takt der Region
    assert _still(edit, "img_002").beats == 8
    assert _still(edit, "img_004").beats == 8


def test_eine_eigene_kamerafahrt_landet_am_genannten_bild():
    manifest = _manifest()
    steht = KBSpec(z=(1.0, 1.0), c=(0.5, 0.5, 0.5, 0.5))
    edit, _plan, _cov = _bauen(manifest, overrides=Overrides(
        media={"img_005": MediaOverride(kb=steht)}))
    assert _still(edit, "img_005").kb == steht
    assert _still(edit, "img_004").kb is None


def test_ein_hochformat_findet_in_die_edit_list_zurueck():
    """Ohne das waere die Absicht gesetzt und das Erzeugnis wuesste nichts davon."""
    manifest = _manifest()
    edit, _plan, _cov = _bauen(manifest, overrides=Overrides(
        media={"img_002": MediaOverride(portrait="crop")}))
    assert _still(edit, "img_002").portrait == "crop"
    assert _still(edit, "img_003").portrait is None


def test_ein_getrimmter_clip_uebernimmt_in_und_out():
    manifest = _manifest(n=4, mit_clip=True)
    edit, _plan, _cov = _bauen(manifest, overrides=Overrides(
        media={"clip_001": MediaOverride.model_validate(
            {"in": 2.0, "out": 6.0, "snap": "none"})}))
    clip = next(s for s in edit.segments if isinstance(s, ClipSegment))
    assert clip.in_ == 2.0
    assert clip.out == pytest.approx(6.0, abs=1 / FPS)
    assert clip.snap == "none"


def test_dur_null_macht_aus_einer_blende_einen_harten_schnitt():
    from slideshow.models import CutOverride

    manifest = _manifest()
    ohne, _plan, _cov = _bauen(manifest)
    assert _blende_vor(ohne, "img_005") is not None

    edit, _plan, _cov = _bauen(manifest, overrides=Overrides(
        cuts=[CutOverride(before="img_005", dur=0.0)]))
    assert _blende_vor(edit, "img_005") is None
    # Der Schnitt daneben bleibt unberuehrt — es ist *eine* Blende, nicht alle.
    assert _blende_vor(edit, "img_006") is not None


def test_eine_einzelne_blende_bekommt_laenge_und_modus():
    from slideshow.models import CutOverride

    manifest = _manifest()
    edit, _plan, _cov = _bauen(manifest, overrides=Overrides(
        cuts=[CutOverride(before="img_004", dur=1.5, mode="fade")]))
    blende = _blende_vor(edit, "img_004")
    assert blende.dur == pytest.approx(1.5, abs=1 / FPS)
    assert blende.mode == "fade"
    assert _blende_vor(edit, "img_005").mode == "dissolve"


def test_eigene_vorgaben_verschmelzen_nur_das_genannte():
    """Ein Zweig darf nicht als Ganzes ersetzt werden — sonst faellt jede nicht
    genannte Ken-Burns-Vorgabe auf nichts zurueck."""
    ov = Overrides(defaults={"kb": {"engine": "scale16"}})
    d = ov.merged_defaults()
    assert d.kb.engine == "scale16"
    assert d.kb.zoom_rate == Defaults().kb.zoom_rate
    assert d.still_seconds == Defaults().still_seconds


def test_ein_tippfehler_in_den_vorgaben_faellt_auf():
    ov = Overrides(defaults={"still_second": 5.0})
    with pytest.raises(SchemaError) as exc:
        ov.merged_defaults(quelle="overrides.yaml")
    assert "defaults" in str(exc.value.path or "")


# --------------------------------------------------------------------------
# Kennungen: kein stiller Ignorierfall
# --------------------------------------------------------------------------

def test_eine_unbekannte_kennung_bricht_ab():
    manifest = _manifest()
    ov = Overrides(media={"img_042": MediaOverride(dur=8.0)})
    with pytest.raises(SchemaError, match="img_042"):
        resolve_media(ov, manifest)


def test_eine_unbekannte_kennung_an_einer_blende_bricht_ab():
    from slideshow.models import CutOverride

    manifest = _manifest()
    ov = Overrides(cuts=[CutOverride(before="img_042", dur=0.0)])
    with pytest.raises(SchemaError, match="img_042"):
        resolve_media(ov, manifest)


def test_ein_eintrag_fuer_ein_ausgelassenes_bild_meldet_sich():
    """Die Datei ueberdauert mehrere Auswahlrunden — ein Eintrag darf auf ein
    gerade nicht verwendetes Bild zeigen. Still wirkungslos bleiben darf er
    nicht: dann sucht man den Fehler im Renderer."""
    manifest = _manifest()
    ov = Overrides(media={"img_007": MediaOverride(dur=8.0)})
    _edit, plan, _cov = _bauen(manifest, overrides=ov)
    assert not any("nicht im Film" in w for w in plan.warnings)

    # Dieselbe Datei, aber die Reihenfolge laesst img_007 weg.
    regions = [Region(type="beat", start=0.0, end=DAUER, bpm=120.0, offset=0.0)]
    beatmap = BeatMap(audio={"file": manifest.audio.file, "duration": DAUER},
                      regions=regions)
    _edit2, plan2, _cov2 = build_edit_list(
        None, manifest, beatmap, defaults=Defaults(), fps=FPS, size=(1280, 720),
        order=[f"img_{i:03d}" for i in range(4)], overrides=ov)
    assert any("nicht im Film" in w for w in plan2.warnings)


def test_unbekannte_felder_bricht_das_laden_ab(tmp_path):
    pfad = tmp_path / "overrides.yaml"
    pfad.write_text("version: 1\nmedia:\n  img_001: {duer: 8}\n", encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        Overrides.load(pfad)
    assert exc.value.line == 3


# --------------------------------------------------------------------------
# Zurueck: die Handarbeit einlesen
# --------------------------------------------------------------------------

def _handfassung(frisch):
    return copy.deepcopy(frisch)


def test_der_feinschliff_wird_aus_der_handfassung_gelesen():
    manifest = _manifest()
    frisch, _plan, _cov = _bauen(manifest)
    hand = _handfassung(frisch)
    _still(hand, "img_003").dur = 8.0
    _still(hand, "img_003").beats = None
    _still(hand, "img_005").kb = KBSpec(z=(1.0, 1.0))
    hand.defaults.kb.engine = "scale16"

    neu, meldungen = diff_edit(frisch, hand, manifest)
    assert meldungen == []
    assert neu.media["img_003"].dur == 8.0
    assert neu.media["img_005"].kb.z == (1.0, 1.0)
    assert neu.defaults == {"kb": {"engine": "scale16"}}
    assert "img_004" not in neu.media


def test_ein_getrimmter_clip_wird_gelesen():
    manifest = _manifest(n=4, mit_clip=True)
    frisch, _plan, _cov = _bauen(manifest)
    hand = _handfassung(frisch)
    clip = next(s for s in hand.segments if isinstance(s, ClipSegment))
    clip.in_, clip.out, clip.snap = 2.0, 6.0, "none"

    neu, meldungen = diff_edit(frisch, hand, manifest)
    assert meldungen == []
    assert neu.media["clip_001"].in_ == 2.0
    assert neu.media["clip_001"].out == 6.0
    assert neu.media["clip_001"].snap == "none"


def test_eine_geloeschte_blende_wird_zur_null():
    manifest = _manifest()
    frisch, _plan, _cov = _bauen(manifest)
    hand = _handfassung(frisch)
    i = hand.segments.index(_still(hand, "img_005"))
    hand.segments.pop(i - 1)

    neu, _meldungen = diff_edit(frisch, hand, manifest)
    assert [(c.before, c.dur) for c in neu.cuts] == [("img_005", 0.0)]


def test_der_feinschliff_ueberlebt_ein_eingefuegtes_bild():
    """Die Zusage dieser Datei, in einem Test.

    Handarbeit an Bild 3 und 5, dann kommt ein neuntes Bild dazu und es wird neu
    gebaut: beide Handgriffe sitzen weiterhin an *ihrem* Bild, und das neue ist
    im Film.
    """
    manifest = _manifest()
    frisch, _plan, _cov = _bauen(manifest)
    hand = _handfassung(frisch)
    _still(hand, "img_003").dur = 8.0
    _still(hand, "img_003").beats = None
    _still(hand, "img_005").kb = KBSpec(z=(1.0, 1.0), c=(0.5, 0.5, 0.5, 0.5))
    neu, _meldungen = diff_edit(frisch, hand, manifest)

    nachschub = _manifest(n=9)
    gebaut, _plan2, _cov2 = _bauen(nachschub, overrides=neu)
    assert _still(gebaut, "img_003").dur == 8.0
    assert _still(gebaut, "img_005").kb.z == (1.0, 1.0)
    assert _still(gebaut, "img_008") is not None


def test_eine_geaenderte_reihenfolge_wird_gemeldet_statt_uebernommen():
    manifest = _manifest()
    frisch, _plan, _cov = _bauen(manifest)
    hand = _handfassung(frisch)
    a, b = hand.segments.index(_still(hand, "img_002")), \
        hand.segments.index(_still(hand, "img_004"))
    hand.segments[a], hand.segments[b] = hand.segments[b], hand.segments[a]

    neu, meldungen = diff_edit(frisch, hand, manifest)
    assert neu.leer
    assert any("order.yaml" in m for m in meldungen)


def test_ein_geloeschtes_bild_verweist_auf_order_yaml():
    manifest = _manifest()
    frisch, _plan, _cov = _bauen(manifest)
    hand = _handfassung(frisch)
    hand.segments.remove(_still(hand, "img_006"))

    _neu, meldungen = diff_edit(frisch, hand, manifest)
    assert any("order.yaml" in m and "img_006" in m for m in meldungen)


def test_ein_geaenderter_titel_verweist_auf_chapters_yaml():
    manifest = _manifest()
    kapitel = [Chapter(before="img_004", title="Am Wasser", subtitle=None)]
    frisch, _plan, _cov = _bauen(manifest, chapters=kapitel)
    hand = _handfassung(frisch)
    folie = next(s for s in hand.segments if isinstance(s, TitleSegment))
    folie.beats = 24.0

    neu, meldungen = diff_edit(frisch, hand, manifest)
    assert neu.leer
    assert any("chapters.yaml" in m for m in meldungen)


def test_eine_andere_framerate_gehoert_an_build():
    manifest = _manifest()
    frisch, _plan, _cov = _bauen(manifest)
    hand = _handfassung(frisch)
    hand.fps = 30.0

    _neu, meldungen = diff_edit(frisch, hand, manifest)
    assert any("--fps" in m for m in meldungen)


def test_zweimal_lesen_findet_beim_zweiten_mal_nichts_mehr():
    """Idempotenz: nach dem Sichern und Neubauen ist der Unterschied weg."""
    manifest = _manifest()
    frisch, _plan, _cov = _bauen(manifest)
    hand = _handfassung(frisch)
    _still(hand, "img_003").dur = 8.0
    _still(hand, "img_003").beats = None
    neu, _meldungen = diff_edit(frisch, hand, manifest)

    zweiter_bau, _plan2, _cov2 = _bauen(manifest, overrides=neu)
    nochmal, meldungen = diff_edit(zweiter_bau, zweiter_bau, manifest)
    assert nochmal.leer
    assert meldungen == []


def test_zusammenlegen_behaelt_den_alten_eintrag():
    alt = Overrides(media={"img_001": MediaOverride(dur=8.0),
                           "img_002": MediaOverride(hold=True)})
    neu = Overrides(media={"img_002": MediaOverride(dur=6.0)})
    zusammen = merge_overrides(alt, neu)
    assert zusammen.media["img_001"].dur == 8.0
    assert zusammen.media["img_002"].dur == 6.0
    assert zusammen.media["img_002"].hold is True


def test_die_geschriebene_datei_laesst_sich_wieder_lesen(tmp_path):
    from slideshow.models import CutOverride

    manifest = _manifest()
    ov = Overrides(defaults={"kb": {"engine": "scale16"}},
                   media={"img_001": MediaOverride(dur=8.0, kb=KBSpec(z=(1.0, 1.2)))},
                   cuts=[CutOverride(before="img_003", dur=0.0)])
    pfad = tmp_path / "overrides.yaml"
    pfad.write_text(dump_overrides_yaml(ov, manifest), encoding="utf-8")

    gelesen = Overrides.load(pfad)
    assert gelesen.media["img_001"].dur == 8.0
    assert gelesen.media["img_001"].kb.z == (1.0, 1.2)
    assert gelesen.cuts[0].before == "img_003"
    assert gelesen.defaults == {"kb": {"engine": "scale16"}}


# --------------------------------------------------------------------------
# Die Reissleine: `build` erkennt Handarbeit
# --------------------------------------------------------------------------

def test_die_frisch_geschriebene_edit_list_gilt_nicht_als_handarbeit():
    manifest = _manifest()
    edit, _plan, _cov = _bauen(manifest)
    assert not hand_edited(dump_edit_yaml(edit))


def test_eine_geaenderte_zeile_faellt_auf():
    manifest = _manifest()
    edit, _plan, _cov = _bauen(manifest)
    text = dump_edit_yaml(edit)
    assert hand_edited(text.replace("beats: 8", "beats: 12", 1))


def test_ohne_stempel_gilt_die_datei_als_handarbeit():
    """Eine Edit-List aus einer aelteren Fassung: der Irrtum in diese Richtung
    kostet ein `--force`, der in die andere die Handarbeit."""
    manifest = _manifest()
    edit, _plan, _cov = _bauen(manifest)
    ohne_kopf = dump_edit_yaml(edit).split("\n", 1)[1]
    assert hand_edited(ohne_kopf)


# --------------------------------------------------------------------------
# Fokusblende: ein eigenes `kb:` schaltet sie ab — sichtbar
# --------------------------------------------------------------------------

def test_ein_eigenes_kb_nach_der_folie_meldet_die_verlorene_fokusblende():
    manifest = _manifest()
    kapitel = [Chapter(before="img_004", title="Am Wasser", subtitle=None)]
    _edit, plan, _cov = _bauen(manifest, chapters=kapitel, overrides=Overrides(
        media={"img_004": MediaOverride(kb=KBSpec(z=(1.0, 1.1)))}))
    assert any("Fokusblende" in w for w in plan.warnings)


def test_ohne_eigenes_kb_bleibt_die_fokusblende_gekoppelt():
    manifest = _manifest()
    kapitel = [Chapter(before="img_004", title="Am Wasser", subtitle=None)]
    edit, plan, _cov = _bauen(manifest, chapters=kapitel)
    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    assert folie.kb is not None
    assert _still(edit, "img_004").kb.z[0] == folie.kb.z[1]
    assert not any("Fokusblende" in w for w in plan.warnings)
