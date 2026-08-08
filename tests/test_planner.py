"""Abnahmekriterien 8 und 10 — Timeline, Drift und ``snap_back``.

8.  Schnittzeitpunkte liegen exakt auf Framegrenzen; die Abweichung zum
    Beat-Raster ist ueber die volle Laufzeit < 1 Frame (kein akkumulierender
    Drift) — auch bei gemischten ``beat``- und ``free``-Regionen.
10. Ein ``dur:``-Override mitten in einer Beat-Region bringt die
    *nachfolgenden* Schnitte nicht dauerhaft aus dem Takt.
"""

from __future__ import annotations

import pytest

from slideshow.models import Defaults, Region
from slideshow.planner import (Intent, apply_transitions, coverage, plan_slots,
                               resolve, slot_capacity, to_frame, to_time,
                               validate_continuity, visible_span)

FPS = 60.0


def _regions_two_songs():
    """Nachbau der Fixture-Struktur, ohne Audio-Analyse."""
    return [
        Region(type="beat", start=0.0, end=16.412, bpm=120.0, offset=0.412),
        Region(type="free", start=16.412, end=22.412, reason="stille"),
        Region(type="beat", start=22.412, end=34.412, bpm=90.0, offset=22.412),
    ]


def _stills(n: int) -> list[Intent]:
    return [Intent(kind="still", src=f"cache/img_{i:03d}.jpg", index=i) for i in range(n)]


def _plan(regions=None, n=40, defaults=None, total=None):
    regions = regions or _regions_two_songs()
    defaults = defaults or Defaults()
    total = total if total is not None else int(round(regions[-1].end * FPS))
    return plan_slots(regions, _stills(n), defaults, fps=FPS, total_frames=total)


# --------------------------------------------------------------------------
# Kriterium 8
# --------------------------------------------------------------------------

def test_grenzen_sind_ganzzahlige_frames():
    plan = _plan()
    for slot in plan.slots:
        assert isinstance(slot.start_f, int) and isinstance(slot.end_f, int)
        assert slot.end_f > slot.start_f


def test_timeline_ist_lueckenlos():
    plan = _plan()
    assert plan.slots[0].start_f == 0
    for a, b in zip(plan.slots, plan.slots[1:]):
        assert a.end_f == b.start_f
    assert plan.slots[-1].end_f == plan.total_frames


def test_kein_drift_gegen_das_beat_raster():
    """Der eigentliche Kern von 6.4.

    Geprueft wird die *letzte* Region: wuerden Einzeldauern aufaddiert, waere
    der Versatz genau dort am groessten.
    """
    regions = _regions_two_songs()
    plan = _plan(regions)
    frame = 1.0 / FPS

    for i, slot in enumerate(plan.slots[:-1]):
        region = plan.regions[slot.region_index]
        if region.type != "beat":
            continue
        cut = to_time(slot.end_f, FPS)
        if not (region.start - 1e-6 <= cut <= region.end + 1e-6):
            continue
        beat = region.beat_duration()
        k = round((cut - region.offset) / beat)
        delta = abs(cut - (region.offset + k * beat))
        assert delta < frame, (f"Schnitt {i} bei {cut:.4f} s liegt {delta * 1000:.1f} ms "
                               f"neben dem Raster (> 1 Frame)")


def test_drift_waechst_nicht_ueber_die_laufzeit():
    """Ein akkumulierender Fehler zeigt sich als wachsende Abweichung."""
    regions = [Region(type="beat", start=0.0, end=600.0, bpm=118.0, offset=0.412)]
    plan = _plan(regions, n=200, total=int(600 * FPS))
    beat = regions[0].beat_duration()
    deltas = []
    for slot in plan.slots[:-1]:
        cut = to_time(slot.end_f, FPS)
        k = round((cut - 0.412) / beat)
        deltas.append(abs(cut - (0.412 + k * beat)))
    assert max(deltas) < 1.0 / FPS
    # Erste und letzte Haelfte muessen gleich gut sein.
    half = len(deltas) // 2
    assert max(deltas[half:]) <= max(deltas[:half]) + 1e-9


def test_free_region_wird_exakt_gefuellt():
    """6.3: die Anzahl der Bilder fuellt die Region exakt."""
    plan = _plan()
    for i, region in enumerate(plan.regions):
        if region.type != "free":
            continue
        members = [s for s in plan.slots if s.region_index == i]
        assert members, "free-Region ohne Bilder"
        span = members[-1].end_f - members[0].start_f
        expected = int(round(region.end * FPS)) - int(round(region.start * FPS))
        assert abs(span - expected) <= 1


def test_free_region_dauern_liegen_im_toleranzband():
    defaults = Defaults()
    plan = _plan(defaults=defaults)
    lo, hi = defaults.still_tolerance
    for i, region in enumerate(plan.regions):
        if region.type != "free":
            continue
        for slot in (s for s in plan.slots if s.region_index == i):
            dur = slot.frames / FPS
            assert lo - 0.01 <= dur <= hi + 0.01, f"{dur:.2f} s ausserhalb [{lo}, {hi}]"


def test_erstes_bild_beginnt_bei_null_trotz_vorlauf():
    """6.0: das erste Bild beginnt bei 0, die Schnitte bleiben auf dem Raster."""
    plan = _plan()
    assert plan.slots[0].start_f == 0
    region = plan.regions[0]
    cut = to_time(plan.slots[0].end_f, FPS)
    beat = region.beat_duration()
    k = round((cut - region.offset) / beat)
    assert abs(cut - (region.offset + k * beat)) < 1.0 / FPS


# --------------------------------------------------------------------------
# Kriterium 10
# --------------------------------------------------------------------------

def test_dur_override_gewinnt_immer():
    """6.3, Praezedenz 1."""
    regions = [Region(type="beat", start=0.0, end=60.0, bpm=120.0, offset=0.0)]
    intents = _stills(10)
    intents[3].dur = 2.7
    intents[3].snap_back = False
    plan = plan_slots(regions, intents, Defaults(), fps=FPS, total_frames=int(60 * FPS))
    assert plan.slots[3].frames / FPS == pytest.approx(2.7, abs=1.0 / FPS)


def test_snap_back_holt_den_takt_zurueck():
    """Kriterium 10: nach einem Override stehen die *nachfolgenden* Schnitte
    wieder auf dem Raster."""
    regions = [Region(type="beat", start=0.0, end=120.0, bpm=120.0, offset=0.0)]
    intents = _stills(12)
    intents[3].dur = 2.7                     # bewusst kein Vielfaches eines Beats
    plan = plan_slots(regions, intents, Defaults(snap_back=True), fps=FPS,
                      total_frames=int(120 * FPS))
    beat = 0.5
    for i, slot in enumerate(plan.slots[3:-1], start=3):
        cut = to_time(slot.end_f, FPS)
        k = round(cut / beat)
        assert abs(cut - k * beat) < 1.0 / FPS, \
            f"Schnitt {i} bei {cut:.4f} s ist nach dem Override aus dem Takt"


def test_ohne_snap_back_liegt_genau_ein_schnitt_daneben():
    """``snap_back: false`` — die Verschiebung, die man bewusst in Kauf nimmt.

    Sie beschraenkt sich auf *einen* Schnitt: das Bild mit dem Override endet
    off-grid, das folgende Bild faengt den Rest auf. Siehe die Anmerkung zu
    ``snap_back`` in :mod:`slideshow.planner`.
    """
    regions = [Region(type="beat", start=0.0, end=120.0, bpm=120.0, offset=0.0)]
    intents = _stills(12)
    intents[3].dur = 2.7
    intents[3].snap_back = False
    plan = plan_slots(regions, intents, Defaults(snap_back=False), fps=FPS,
                      total_frames=int(120 * FPS))

    daneben = to_time(plan.slots[3].end_f, FPS)
    assert abs(daneben - round(daneben / 0.5) * 0.5) > 1.0 / FPS, \
        "ohne snap_back darf der Override-Schnitt gerade nicht gerundet werden"
    for i, slot in enumerate(plan.slots[4:-1], start=4):
        cut = to_time(slot.end_f, FPS)
        assert abs(cut - round(cut / 0.5) * 0.5) < 1.0 / FPS, \
            f"Schnitt {i} haette wieder auf dem Raster liegen muessen"


def test_mit_snap_back_liegt_auch_der_override_auf_dem_raster():
    regions = [Region(type="beat", start=0.0, end=120.0, bpm=120.0, offset=0.0)]
    intents = _stills(12)
    intents[3].dur = 2.7
    plan = plan_slots(regions, intents, Defaults(snap_back=True), fps=FPS,
                      total_frames=int(120 * FPS))
    cut = to_time(plan.slots[3].end_f, FPS)
    assert abs(cut - round(cut / 0.5) * 0.5) < 1.0 / FPS


def test_beats_in_einer_free_region_warnt_und_nimmt_die_standardlaenge():
    """Kein Abbruch — der Fall entsteht auch ohne Zutun.

    ``build`` setzt ``beats:`` in der Lagekorrektur und plant danach neu; wandert
    das Segment dabei ueber eine Regionsgrenze, waere ein Fehler nicht
    zuzuordnen. Die Kachelung der free-Region muss dabei unberuehrt bleiben.
    """
    regions = [Region(type="free", start=0.0, end=12.0, reason="stille")]
    ohne = plan_slots(regions, _stills(3), Defaults(), fps=FPS,
                      total_frames=int(12 * FPS))
    intents = _stills(3)
    intents[1].beats = 8
    plan = plan_slots(regions, intents, Defaults(), fps=FPS, total_frames=int(12 * FPS))

    assert [(s.start_f, s.end_f) for s in plan.slots] == \
        [(s.start_f, s.end_f) for s in ohne.slots], \
        "`beats:` darf die driftfreie Kachelung der free-Region nicht verschieben"
    warnung = " ".join(plan.warnings)
    assert "beat-Region" in warnung
    assert "segments[1].beats" in warnung, "die Warnung muss auf die Zeile zeigen"


def test_sehr_lange_stille_bekommt_ein_hold_bild():
    """6.3: > 12 s Stille traegt bewusst ein einzelnes ruhiges Bild."""
    regions = [Region(type="free", start=0.0, end=20.0, reason="stille")]
    plan = plan_slots(regions, _stills(5), Defaults(), fps=FPS,
                      total_frames=int(20 * FPS))
    in_region = [s for s in plan.slots if s.region_index == 0]
    assert len(in_region) == 1
    assert in_region[0].hold


# --------------------------------------------------------------------------
# Die Framerundung darf keinen Beat verschenken
# --------------------------------------------------------------------------

#: 117 bpm auf 50 fps: ein Beat ist 0,5128 s lang und trifft praktisch nie eine
#: Framegrenze. Genau dort zeigt sich der Fehler — bei 120 bpm auf 60 fps
#: (0,5 s = 30 Frames) faellt er nie auf.
def _krummes_raster():
    return [Region(type="beat", start=0.0, end=180.0, bpm=117.0, offset=0.317),
            Region(type="free", start=180.0, end=210.0, reason="stille"),
            Region(type="beat", start=210.0, end=390.0, bpm=103.5, offset=210.21)]


def test_ein_slot_steht_genau_beats_per_still_beats():
    """Der Cursor ist framegerundet.

    Landet er eine halbe Frame *hinter* einem Beat, sitzt er auf demselben Frame
    und *ist* dieser Beat. Eine rein numerische Toleranz in
    ``beat_index_at_or_after`` sah ihn als "danach" und gab dem Slot 13 statt 12
    Beats. Das traf rund jeden zweiten Slot; an ``sommer26`` gemessen waren es
    102 von 293 Slots und zusammen 53 s — Kapazitaet, die am Filmende als
    ungenutztes Material wieder auftauchte.
    """
    fps = 50.0
    defaults = Defaults(beats_per_still=12)
    plan = plan_slots(_krummes_raster(), _stills(60), defaults, fps=fps,
                      total_frames=int(390 * fps))
    # Das letzte Bild einer Region traegt deren Rest (Restplatz-Regel) und ist
    # damit planmaessig laenger.
    letztes = {s.region_index: i for i, s in enumerate(plan.slots)}
    geprueft = 0
    for i, slot in enumerate(plan.slots[:-1]):
        region = plan.regions[slot.region_index]
        if region.type != "beat" or letztes[slot.region_index] == i:
            continue
        beat = region.beat_duration()
        offset = float(region.offset if region.offset is not None else region.start)
        # Das *erste* Bild einer Region faengt am Regionsanfang an, nicht auf
        # dem Raster (6.0, Vorlaufregel) — es ist von sich aus laenger.
        start = slot.start_f / fps - offset
        if abs(start - round(start / beat) * beat) > 1.0 / fps:
            continue
        # Gemessen wird in *Frames*, nicht in Beats: beide Slotgrenzen sind
        # gerundet, also ist eine Frame Spiel. In Beats ausgedrueckt haengt
        # dieselbe Toleranz am Tempo und waere bei 194 bpm zu eng.
        soll = round(12 * beat * fps)
        assert abs(slot.frames - soll) <= 1, \
            f"Slot {i} steht {slot.frames} Frames statt {soll} (12 Beats)"
        geprueft += 1
    assert geprueft >= 40, f"nur {geprueft} Slots geprueft"


def test_die_karte_haelt_was_slot_capacity_verspricht():
    """``slot_capacity`` ist die Zielzahl fuer ``slideshow select``.

    Vergibt der Planer weniger Slots, waehlt man Bilder aus, fuer die es keinen
    Platz gibt — und merkt es erst am fertigen Film als Ueberdeckung.
    """
    fps = 50.0
    defaults = Defaults(beats_per_still=12)
    regions = _krummes_raster()
    kap = slot_capacity(regions, defaults)
    plan = plan_slots(regions, _stills(kap + 10), defaults, fps=fps,
                      total_frames=int(390 * fps))
    assert len(plan.slots) == kap


# --------------------------------------------------------------------------
# Mindeststandzeit — der Rest am Regionsende
#
# Eine beat-Region ist so gut wie nie ein ganzzahliges Vielfaches der
# Slotlaenge. Der Bruchteil bekam frueher ein eigenes Bild: an echtem Material
# regelmaessig unter 1 s, unter den Blenden praktisch ein Aufblitzen.
# --------------------------------------------------------------------------

#: 3 Slots zu 4 s, dann 2,5 s Rest — unter der Mindeststandzeit von 3 s.
#: Dahinter noch eine Region, damit nicht die Ausnahme am Filmende greift.
def _regions_mit_rest(ende: float = 14.5):
    return [Region(type="beat", start=0.0, end=ende, bpm=120.0, offset=0.0),
            Region(type="free", start=ende, end=ende + 16.0, reason="stille")]


def test_der_rest_am_regionsende_bekommt_kein_eigenes_bild():
    regions = _regions_mit_rest()
    defaults = Defaults()
    plan = plan_slots(regions, _stills(10), defaults, fps=FPS,
                      total_frames=int(round(30.5 * FPS)))

    in_region = [s for s in plan.slots if s.region_index == 0]
    assert len(in_region) == 3, "der 2,5-s-Rest darf kein viertes Bild werden"
    for slot in in_region:
        assert slot.frames / FPS >= defaults.min_still - 1e-9, \
            f"{slot.frames / FPS:.3f} s unter der Mindeststandzeit"


def test_der_rest_faellt_dem_vorgaenger_zu_und_schliesst_die_region():
    """Er kann nur an einen Nachbarn — und zwar an den *vorherigen*.

    Der Anfang des naechsten Bildes ist der erste Beat der neuen Region und
    soll dort bleiben; verschoebe man ihn, waere der Sync fuer eine ganze
    Region dahin.
    """
    plan = plan_slots(_regions_mit_rest(), _stills(10), Defaults(), fps=FPS,
                      total_frames=int(round(30.5 * FPS)))
    letzter = [s for s in plan.slots if s.region_index == 0][-1]
    assert letzter.frames / FPS == pytest.approx(6.5), "4 s Slot + 2,5 s Rest"
    assert letzter.end_f == to_frame(14.5, FPS)


def test_das_verdraengte_bild_rutscht_weiter_statt_wegzufallen():
    """Kein Medium geht verloren — es wird in der naechsten Region geplant."""
    plan = plan_slots(_regions_mit_rest(), _stills(10), Defaults(), fps=FPS,
                      total_frames=int(round(30.5 * FPS)))
    reihe = [s.intent.src for s in plan.slots]
    assert reihe[:4] == [f"cache/img_{i:03d}.jpg" for i in range(4)], \
        "die Reihenfolge bleibt unberuehrt"
    assert plan.slots[3].region_index == 1, \
        "das vierte Bild gehoert jetzt in die free-Region, nicht an deren Rand"
    assert "cache/img_003.jpg" not in plan.unused, \
        "verdraengt heisst verschoben, nicht weggeworfen"


def test_ein_tragfaehiger_rest_bekommt_weiterhin_sein_eigenes_bild():
    """Die Regel greift nur unterhalb der Schwelle — sonst waere sie ein
    Rundungsfehler mit anderem Vorzeichen."""
    regions = _regions_mit_rest(15.5)          # 3 Slots zu 4 s, dann 3,5 s Rest
    plan = plan_slots(regions, _stills(10), Defaults(), fps=FPS,
                      total_frames=int(round(31.5 * FPS)))
    in_region = [s for s in plan.slots if s.region_index == 0]
    assert len(in_region) == 4
    assert in_region[-1].frames / FPS == pytest.approx(3.5)


def test_am_filmende_bleibt_der_rest_ein_bild():
    """Dort ist die Grenze kein Schnitt, sondern das Ende.

    Bestimmt das Material die Laenge, endet die Regionenkarte genau da, wo das
    letzte Bild anfangen wollte. Wuerde die Regel auch hier greifen, kostete sie
    ohne Not ein Medium — und der Rest faellt dem letzten Bild ohnehin zu.
    """
    regions = [Region(type="beat", start=0.0, end=14.5, bpm=120.0, offset=0.0)]
    plan = plan_slots(regions, _stills(4), Defaults(), fps=FPS,
                      total_frames=to_frame(14.5, FPS))
    assert len(plan.slots) == 4
    assert plan.slots[-1].frames / FPS == pytest.approx(2.5)
    assert not plan.unused


def test_ein_nicht_auffangbarer_rest_wird_gemeldet():
    """Hinter einem Clip laesst sich nicht verlaengern: seine Laenge kommt aus
    dem Intermediate. Dann bleibt das kurze Bild — aber nicht stillschweigend.
    """
    regions = [Region(type="beat", start=0.0, end=12.5, bpm=120.0, offset=0.0),
               Region(type="free", start=12.5, end=28.5, reason="stille")]
    intents = [
        Intent(kind="still", src="cache/img_000.jpg", index=0),
        Intent(kind="still", src="cache/img_001.jpg", index=1),
        # Endet auf dem Beat bei 12,0 s und laesst 0,5 s bis zur Regionsgrenze.
        Intent(kind="clip", src="cache/clip_000.mov", index=2, clip_available=4.0),
        Intent(kind="still", src="cache/img_002.jpg", index=3),
        Intent(kind="still", src="cache/img_003.jpg", index=4),
    ]
    plan = plan_slots(regions, intents, Defaults(), fps=FPS,
                      total_frames=int(round(28.5 * FPS)))

    kurz = [s for s in plan.slots if s.region_index == 0][-1]
    assert kurz.intent.kind == "still"
    assert kurz.frames / FPS == pytest.approx(0.5)
    assert any("Mindeststandzeit" in w for w in plan.warnings), \
        "ein Bild unter der Mindeststandzeit gehoert gemeldet"


def test_ein_ausdrueckliches_dur_darf_kuerzer_sein_und_bleibt_unkommentiert():
    """Wer 1,5 s hinschreibt, meint 1,5 s. Die Schranke ist gegen den *geplanten*
    Rest gerichtet, nicht gegen Handarbeit."""
    regions = [Region(type="beat", start=0.0, end=60.0, bpm=120.0, offset=0.0)]
    intents = _stills(10)
    intents[3].dur = 1.5
    intents[3].snap_back = False
    plan = plan_slots(regions, intents, Defaults(), fps=FPS, total_frames=int(60 * FPS))
    assert plan.slots[3].frames / FPS == pytest.approx(1.5, abs=1.0 / FPS)
    assert not any("Mindeststandzeit" in w for w in plan.warnings)


def test_die_mindeststandzeit_folgt_dem_toleranzband():
    """Eine Zahl, nicht zwei: free- und beat-Regionen teilen dieselbe Untergrenze."""
    assert Defaults().min_still == pytest.approx(3.0)
    assert Defaults(still_tolerance=(2.0, 5.0)).min_still == pytest.approx(2.0)
    assert Defaults(min_still_seconds=1.0).min_still == pytest.approx(1.0)


def test_die_mindeststandzeit_laesst_sich_abschalten():
    """``0`` stellt das alte Verhalten wieder her — die Reissleine, falls jemand
    den Rest ausdruecklich als Bild haben will."""
    plan = plan_slots(_regions_mit_rest(), _stills(10), Defaults(min_still_seconds=0.0),
                      fps=FPS, total_frames=int(round(30.5 * FPS)))
    in_region = [s for s in plan.slots if s.region_index == 0]
    assert len(in_region) == 4
    assert in_region[-1].frames / FPS == pytest.approx(2.5)


def test_die_kapazitaetsrechnung_kennt_die_regel():
    """Sonst waehlte ``select`` Bilder aus, fuer die es keinen Slot gibt."""
    defaults = Defaults()
    kurz = coverage(plan_slots(_regions_mit_rest(), _stills(10), defaults, fps=FPS,
                               total_frames=int(round(30.5 * FPS))), defaults)
    beat = next(r for r in kurz.per_region if r["type"] == "beat")
    assert beat["capacity"] == 3, "die 2,5 s Rest fassen kein Bild"


# --------------------------------------------------------------------------
# Uebergaenge (8.2)
# --------------------------------------------------------------------------

def test_uebergang_liegt_zentriert_ueber_dem_schnitt():
    plan = _plan()
    apply_transitions(plan, Defaults())
    for cut in range(1, len(plan.slots)):
        t = plan.transitions[cut]
        if not t:
            continue
        assert t % 2 == 0, "T muss gerade sein, damit T/2 exakt aufgeht"
        schnitt = plan.slots[cut].start_f
        assert plan.slots[cut - 1].end_f == schnitt


def test_segmente_kacheln_die_timeline():
    plan = _plan()
    apply_transitions(plan, Defaults())
    segments = resolve(plan)
    validate_continuity(segments, plan.total_frames)


def test_sichtbare_spanne_umfasst_die_blendenhaelften():
    """8.2: die Ken-Burns-Bewegung ist ueber exklusiven Anteil *plus*
    angrenzende Uebergangs-Haelften definiert."""
    plan = _plan()
    apply_transitions(plan, Defaults())
    for i, slot in enumerate(plan.slots):
        vs, ve = visible_span(plan, i)
        assert vs == slot.start_f - plan.transitions[i] // 2
        assert ve == slot.end_f + plan.transitions[i + 1] // 2
        assert ve - vs >= slot.frames


def test_blende_frisst_den_exklusiven_anteil_nicht_auf():
    regions = [Region(type="beat", start=0.0, end=40.0, bpm=120.0, offset=0.0)]
    defaults = Defaults()
    defaults.xfade.beats = 8            # absurd lang: so lang wie ein ganzes Bild
    plan = plan_slots(regions, _stills(10), defaults, fps=FPS, total_frames=int(40 * FPS))
    apply_transitions(plan, defaults)
    segments = resolve(plan)
    validate_continuity(segments, plan.total_frames)
    for s in segments:
        assert s.frames > 0


# --------------------------------------------------------------------------
# Laufzeitdeckung (6.5)
# --------------------------------------------------------------------------

def test_ueberdeckung_meldet_ungenutzte_medien():
    plan = _plan(n=200)
    cov = coverage(plan, Defaults())
    assert cov.overrun
    assert cov.unused


def test_unterdeckung_wird_gemeldet_statt_stumm_abzuschneiden():
    plan = _plan(n=2)
    cov = coverage(plan, Defaults())
    assert cov.underrun
    from slideshow.planner import coverage_advice
    tips = coverage_advice(cov, Defaults())
    assert any("beats_per_still" in t for t in tips)


# --------------------------------------------------------------------------
# Free-Regionen ohne Beat-Raster
#
# Ein durchgehender Song ohne erkennbares Raster ist *eine* lange
# free-Region. Fiele die auf ein einziges Standbild zusammen, waere der Film
# ein Foto mit Musik.
# --------------------------------------------------------------------------

def _langer_song(dauer: float = 392.68) -> list[Region]:
    return [Region(type="free", start=0.0, end=dauer,
                   reason="niedrige Rhythmus-Konfidenz")]


def test_langer_song_ohne_raster_wechselt_im_standardtakt():
    defaults = Defaults()                      # still_seconds = 4.0
    regions = _langer_song()
    plan = plan_slots(regions, _stills(200), defaults, fps=FPS,
                      total_frames=int(round(392.68 * FPS)))
    cov = coverage(plan, defaults)

    assert cov.stills > 1, "die Region darf nicht auf ein Standbild zusammenfallen"
    assert cov.stills == pytest.approx(392.68 / 4.0, abs=1), \
        "gewechselt wird im Standardtakt still_seconds"


def test_standardtakt_ist_konfigurierbar():
    """Dieselbe Region, andere Standzeit — die Anzahl folgt der Einstellung."""
    regions = _langer_song()
    gesehen = {}
    for sekunden in (4.0, 8.0, 28.0):
        defaults = Defaults(still_seconds=sekunden)
        plan = plan_slots(regions, _stills(200), defaults, fps=FPS,
                          total_frames=int(round(392.68 * FPS)))
        gesehen[sekunden] = coverage(plan, defaults).stills

    assert gesehen[4.0] > gesehen[8.0] > gesehen[28.0]
    assert gesehen[28.0] == 14, "392.68 s / 28 s geht genau auf"


def test_stille_bekommt_weiterhin_ein_ruhiges_standbild():
    """Die hold-Regel bleibt — sie gilt nur nicht mehr fuer Musik."""
    defaults = Defaults()                      # hold_seconds = 12.0
    regions = [Region(type="free", start=0.0, end=40.0, reason="stille", quiet=True)]
    plan = plan_slots(regions, _stills(20), defaults, fps=FPS,
                      total_frames=int(40 * FPS))

    assert coverage(plan, defaults).stills == 1


def test_stille_die_in_musik_uebergeht_zaehlt_als_musik():
    """``merge_adjacent_free`` verodert nicht, es verundet."""
    from slideshow.beats import merge_adjacent_free
    verschmolzen = merge_adjacent_free([
        Region(type="free", start=0.0, end=3.0, reason="stille", quiet=True),
        Region(type="free", start=3.0, end=392.68, reason="niedrige Rhythmus-Konfidenz"),
    ])

    assert len(verschmolzen) == 1
    assert not verschmolzen[0].quiet, \
        "eine Region, in der ueberwiegend Musik laeuft, ist nicht still"


# --------------------------------------------------------------------------
# Tonspur-Rückfälle
#
# Drei Fälle müssen ohne Abbruch durchlaufen: gar keine Tonspur, eine zu
# kurze und eine zu lange. Die Musik gibt die Laufzeit vor, solange das
# Material sie bis auf eine Bildlänge füllt — darüber hinaus gewinnt das
# Material, und der Ton wird gekürzt bzw. stumm verlängert.
# --------------------------------------------------------------------------

def _tl(regions, n, defaults=None, *, audio):
    from slideshow.build import _timeline_length
    return _timeline_length(regions, n, defaults or Defaults(), audio_seconds=audio)


def test_material_laenge_folgt_dem_standardtakt():
    from slideshow.planner import material_seconds
    regions = [Region(type="free", start=0.0, end=40.0, reason="x")]

    # 10 Slots à 4 s in der Region, 3 davon belegt.
    assert material_seconds(regions, 3, Defaults()) == pytest.approx(12.0)
    # Mehr Medien als die Karte fasst: der Rest läuft im Standardtakt weiter.
    assert material_seconds(regions, 12, Defaults()) == pytest.approx(48.0)


def test_ohne_tonspur_bestimmt_das_material_die_laufzeit():
    regions = [Region(type="free", start=0.0, end=12.0, reason="ohne Tonspur")]
    dauer, hinweis = _tl(regions, 3, audio=0.0)

    assert dauer == pytest.approx(12.0)
    assert "keine Tonspur" in hinweis


def test_kleine_abweichung_laesst_die_musik_gewinnen():
    """Bis zu einer Bildlänge fängt die übliche Streckung den Rest ab."""
    regions = [Region(type="free", start=0.0, end=40.0, reason="x")]
    dauer, hinweis = _tl(regions, 10, audio=41.5)

    assert dauer == pytest.approx(41.5), "der Film soll mit der Musik enden"
    assert hinweis == ""


def test_zu_lange_tonspur_wird_abgeschnitten():
    regions = [Region(type="free", start=0.0, end=392.68, reason="x")]
    dauer, hinweis = _tl(regions, 14, audio=392.68)

    assert dauer == pytest.approx(56.0, abs=1.0), \
        "14 Bilder à 4 s — nicht 392 s mit einem Standbild am Ende"
    assert "abgeschnitten" in hinweis


def test_zu_kurze_tonspur_laesst_die_bilder_weiterlaufen():
    regions = [Region(type="free", start=0.0, end=5.0, reason="x")]
    dauer, hinweis = _tl(regions, 3, audio=5.0)

    assert dauer > 5.0
    assert "ohne Ton" in hinweis


def test_karte_wird_auf_die_neue_laenge_zugeschnitten():
    from slideshow.planner import fit_regions_to
    regions = [Region(type="beat", start=0.0, end=30.0, bpm=120.0, offset=0.0),
               Region(type="free", start=30.0, end=60.0, reason="x")]

    gekuerzt = fit_regions_to(regions, 20.0)
    assert len(gekuerzt) == 1
    assert gekuerzt[-1].end == pytest.approx(20.0)

    verlaengert = fit_regions_to(regions, 80.0)
    assert verlaengert[-1].end == pytest.approx(80.0)
    assert verlaengert[0].bpm == 120.0, "das Raster davor bleibt unangetastet"


def test_schwanz_hinter_dem_tonende_erbt_die_stille_nicht():
    """Regression: sonst kollabiert der stumme Teil auf ein einziges Bild.

    Beginnt die Tonspur leise, stuft ``beats`` die Region als ``stille`` ein.
    Wird *diese* Region über das Tonende hinaus verlängert, nimmt sie ihre
    hold-Eigenschaft mit — und ``hold`` heißt: ein Standbild für alles.
    Hinter dem Tonende ist aber keine Stille, sondern gar kein Ton.
    """
    from slideshow.planner import fit_regions_to
    still = [Region(type="free", start=0.0, end=5.017, reason="stille", quiet=True)]

    angepasst = fit_regions_to(still, 13.017)

    assert len(angepasst) == 2, "der Schwanz bekommt eine eigene Region"
    assert angepasst[0].quiet, "die echte Stille bleibt still"
    assert not angepasst[1].quiet, "der tonlose Teil ist keine Stille"

    plan = plan_slots(angepasst, _stills(3), Defaults(), fps=FPS,
                      total_frames=int(round(13.017 * FPS)))
    assert coverage(plan, Defaults()).stills == 3, \
        "alle drei Bilder müssen laufen, nicht eines über die volle Länge"


# --------------------------------------------------------------------------
# Richtung der Deckungs-Ratschlaege
# --------------------------------------------------------------------------

def test_bei_zu_wenig_material_werden_laengere_standzeiten_empfohlen():
    from slideshow.planner import coverage_advice
    tips = " ".join(coverage_advice(coverage(_plan(n=2), Defaults()), Defaults()))

    assert "erhoehen" in tips, \
        "zu wenig Material heisst: jedes Bild muss laenger stehen"
    assert "reduzieren" not in tips


def test_bei_zu_viel_material_werden_kuerzere_standzeiten_empfohlen():
    from slideshow.planner import coverage_advice
    tips = " ".join(coverage_advice(coverage(_plan(n=200), Defaults()), Defaults()))

    assert "reduzieren" in tips, \
        "uebrige Medien heissen: jedes Bild muss kuerzer stehen"
    assert "erhoehen" not in tips


def test_der_vorgeschlagene_wert_schliesst_die_luecke_wirklich():
    """Der Ratschlag wird befolgt und nachgerechnet.

    Ein Vorschlag, der die Unterdeckung nur verkleinert, ist so gut wie
    keiner — wer ihn befolgt, steht danach wieder vor derselben Meldung.
    """
    import re
    from slideshow.planner import coverage_advice

    defaults = Defaults()
    total = int(round(392.68 * FPS))
    plan = plan_slots(_langer_song(), _stills(14), defaults, fps=FPS, total_frames=total)
    cov = coverage(plan, defaults)
    assert cov.underrun

    tips = coverage_advice(cov, defaults)
    treffer = re.search(r"auf ~([\d.]+) s", " ".join(tips))
    assert treffer, "der Vorschlag muss eine konkrete Standzeit nennen"
    vorgeschlagen = float(treffer.group(1))
    assert vorgeschlagen == pytest.approx(392.68 / 14, abs=0.2)

    danach = Defaults(still_seconds=vorgeschlagen)
    cov2 = coverage(plan_slots(_langer_song(), _stills(14), danach, fps=FPS,
                               total_frames=total), danach)
    assert not cov2.underrun, "nach dem Befolgen darf keine Luecke mehr offen sein"
    assert not cov2.overrun


def test_free_region_bekommt_den_regler_der_dort_wirkt():
    """``beats_per_still`` ist in einer free-Region wirkungslos."""
    from slideshow.planner import coverage_advice
    defaults = Defaults()
    plan = plan_slots(_langer_song(), _stills(200), defaults, fps=FPS,
                      total_frames=int(round(392.68 * FPS)))
    tips = " ".join(coverage_advice(coverage(plan, defaults), defaults))

    assert "still_seconds" in tips
    assert "beats_per_still" not in tips
