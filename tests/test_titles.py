"""Titel- und Zwischenfolien (``docs/briefing-titelfolien.md``).

Geprueft wird hier die **Einbettung**: Lage auf dem Raster, Verhalten in Stille,
Rundlauf durch die Edit-List, Deckungsrechnung. Das Aussehen der Folie ist nicht
Gegenstand dieser Datei — es entsteht im Generator und braucht Pillow und eine
Schriftdatei.

Alles laeuft ohne ffmpeg: die Regionenkarte wird von Hand gestellt, damit der
Beat-Fahrplan *bekannt* ist. Nur so ist eine Aussage wie "der Titel beginnt auf
Beat 32" ueberhaupt pruefbar statt bloss plausibel.
"""

from __future__ import annotations

import pytest

from slideshow.build import (build_edit_list, check_title_phrases, plan_from_edit,
                             validate_edit)
from slideshow.errors import SchemaError
from slideshow.models import (BeatMap, Chapter, Defaults, EditList, Manifest,
                              MediaItem, Region, StillSegment, TitleSegment,
                              dump_edit_yaml)
from slideshow.planner import to_time
from slideshow.titles import reading_seconds, title_asset

FPS = 60.0

#: 120 BPM = 0,5 s je Beat. Alle Beat-Aussagen in dieser Datei rechnen damit.
BEAT = 0.5


# --------------------------------------------------------------------------
# Material von Hand
# --------------------------------------------------------------------------

def _manifest(n: int = 12, *, ton: str = "cache/mix.flac",
              dauer: float = 90.0) -> Manifest:
    media = [MediaItem(id=f"img_{i:03d}", path=f"src/img_{i:03d}.jpg", kind="image",
                       cache_path=f"cache/img_{i:03d}.jpg", time_source="exif",
                       # 2 h Abstand: Bild 0 und 11 liegen damit auf verschiedenen
                       # Tagen, und der Tageszaehler in `subtitle: auto` ist pruefbar.
                       capture_time=1_753_000_000 + i * 7200)
             for i in range(n)]
    m = Manifest(media=media, fps_suggestion=FPS)
    m.audio.file = ton
    m.audio.duration = dauer if ton else 0.0
    return m


def _beat_region(start: float = 0.0, end: float = 90.0) -> Region:
    return Region(type="beat", start=start, end=end, bpm=120.0, offset=start, conf=0.9)


def _bauen(manifest: Manifest, regions: list[Region], chapters: list[Chapter], *,
           dauer: float = 90.0, defaults: Defaults | None = None, project=None):
    """Ohne ``project`` misst ``bg: auto`` nicht — es bleibt bei der reinen
    Positionsregel, und genau die ist hier Gegenstand. Die Messwahl steht in
    ``test_titles_hintergrund.py``."""
    beatmap = BeatMap(audio={"file": manifest.audio.file, "duration": dauer},
                      regions=regions)
    return build_edit_list(project, manifest, beatmap, defaults=defaults or Defaults(),
                           fps=FPS, size=(1280, 720), chapters=chapters)


def _titelslots(plan):
    return [(i, s) for i, s in enumerate(plan.slots) if s.intent.title is not None]


# --------------------------------------------------------------------------
# T4 — Phrasenlage
# --------------------------------------------------------------------------

def test_titelfolie_beginnt_auf_einer_phrasengrenze():
    """Der Kern von Entscheidung 3: eine Zaesur gehoert auf die Eins.

    Mit ``beats_per_still: 7`` liegt der Titel ohne Zutun auf Beat 35 — mitten
    in der Phrase. Genau dieser Fall soll korrigiert werden; bei einem Vielfachen
    von 8 wuerde der Test auch dann gruen, wenn die Rechnung gar nicht liefe.
    """
    defaults = Defaults(beats_per_still=7)
    defaults.title.phrase_beats = 8
    _edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe")],
                               defaults=defaults)

    (i, slot), = _titelslots(plan)
    beat_nr = to_time(slot.start_f, FPS) / BEAT
    assert beat_nr == pytest.approx(round(beat_nr), abs=1.0 / FPS / BEAT)
    assert round(beat_nr) % defaults.title.phrase_beats == 0, \
        f"Titel beginnt auf Beat {beat_nr}, das ist keine Phrasengrenze"
    # Die Ausrichtung ist als Absicht des *Vorgaengers* materialisiert und damit
    # in der Datei sichtbar — nicht als Sonderregel im Planer versteckt.
    assert plan.slots[i - 1].intent.beats == 4


def test_phrasenlage_wird_als_beats_des_vorgaengers_berichtet():
    defaults = Defaults(beats_per_still=7)
    _edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe")],
                               defaults=defaults)
    passend = [w for w in plan.warnings if "Phrasengrenze" in w and "Malmoe" in w]
    assert passend, "die Korrektur muss im Bericht stehen, sonst ist sie unerklaerlich"
    assert "von 7 auf 4" in passend[0]


def test_ohne_korrekturbedarf_wird_nichts_berichtet():
    """Liegt der Titel ohnehin richtig, ist Schweigen die richtige Antwort."""
    # beats_per_still 8 und phrase_beats 8: jeder Slot endet auf einer Phrase.
    _edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe")])
    assert not [w for w in plan.warnings if "Phrasengrenze" in w]


def test_die_phrasenlage_haelt_auch_bei_krummem_raster():
    """Lagekorrektur und Planer muessen dieselbe Beatrechnung fuehren.

    ``_phrasenlage`` hatte ``beat_index_at_or_after`` ein zweites Mal
    ausgeschrieben — mit einer rein numerischen Toleranz, waehrend der Planer
    auf eine halbe Frame prueft. Beide liefen dadurch um genau einen Beat
    auseinander, und die Folie landete daneben (an ``sommer26`` gemessen:
    0,52 s, also exakt ein Beat bei 117,25 bpm).

    Sichtbar wird das nur bei einem Raster, das keine Framegrenzen trifft: 120
    bpm auf 60 fps sind glatte 30 Frames je Beat, dort faellt es nie auf. 117
    bpm sind 30,77 Frames.
    """
    region = Region(type="beat", start=0.0, end=90.0, bpm=117.0, offset=0.317,
                    conf=0.9)
    defaults = Defaults(beats_per_still=12)
    _edit, plan, _cov = _bauen(_manifest(), [region],
                               [Chapter(before="img_005", title="Malmoe")],
                               defaults=defaults)
    (_i, slot), = _titelslots(plan)
    beat = region.beat_duration()
    phrase = defaults.title.phrase_beats * beat
    start = to_time(slot.start_f, FPS)
    versatz = start - (region.offset + round((start - region.offset) / phrase) * phrase)
    assert abs(versatz) <= 1.0 / FPS, \
        f"Titel beginnt {versatz * 1000:+.0f} ms neben der Phrasengrenze"
    assert not check_title_phrases(plan, defaults), \
        "und die Nachpruefung darf dann auch nichts zu melden haben"


def test_regionsgrenze_gilt_als_phrasengrenze():
    """Eine Regionsgrenze ist per Konstruktion eine musikalische Grenze.

    Dort ist die Phrasenrechnung gegenstandslos — und der Vorgaenger liegt in
    einer anderen Region, darf also nicht angetastet werden.
    """
    regions = [_beat_region(0.0, 40.0),
               Region(type="beat", start=40.0, end=90.0, bpm=90.0, offset=40.0, conf=0.8)]
    _edit, plan, _cov = _bauen(_manifest(), regions,
                               [Chapter(at=10, title="Malmoe")])
    (i, slot), = _titelslots(plan)
    if plan.slots[i - 1].region_index != slot.region_index:
        assert plan.slots[i - 1].intent.beats is None


# --------------------------------------------------------------------------
# T5 — Stille
# --------------------------------------------------------------------------

def _stille_projekt(defaults: Defaults | None = None):
    """40 s Takt, dann 24 s echte Stille — laenger als ``hold_seconds`` (12 s)."""
    regions = [_beat_region(0.0, 40.0),
               Region(type="free", start=40.0, end=64.0, reason="stille", quiet=True)]
    return _bauen(_manifest(), regions, [Chapter(at=10, title="Malmoe")],
                  dauer=64.0, defaults=defaults)


def test_titel_in_langer_stille_steht_die_standardlaenge():
    """Die unangenehmste Falle des ganzen Vorhabens (Entscheidung 3b).

    Eine stille Region ueber ``hold_seconds`` ist **ein** Slot. Ohne Gegenmittel
    bekaeme die Folie die gesamte Stille: 24 Sekunden Standbild mit "Malmoe"
    darauf, ohne Fehlermeldung.
    """
    defaults = Defaults()
    _edit, plan, _cov = _stille_projekt(defaults)
    (i, slot), = _titelslots(plan)

    assert plan.regions[slot.region_index].type == "free"
    assert slot.hold, "die Region muss die hold-Falle ueberhaupt aufspannen"
    assert to_time(slot.frames, FPS) == pytest.approx(defaults.still_seconds,
                                                      abs=1.0 / FPS)
    # Der Rest der Stille faellt an das folgende Bild, das ruhig stehen bleibt.
    folge = plan.slots[i + 1]
    assert folge.hold
    assert to_time(folge.frames, FPS) == pytest.approx(20.0, abs=1.0 / FPS)


def test_in_langer_stille_wird_snap_back_abgeschaltet():
    """Die Gegenprobe zu T5.

    ``dur:`` allein rettet nichts: ``snap_back`` ist per Default an, und die
    einzige Kante einer hold-Region ist ihr Ende — der Override wuerde also
    wieder auf die volle Stille aufgerundet. Ein Test, der nur die Dauer prueft,
    bliebe auch mit ``snap_back: true`` gruen und pruefte die Falle gar nicht.
    """
    edit, plan, _cov = _stille_projekt()
    (_i, slot), = _titelslots(plan)
    assert slot.intent.snap_back is False

    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    assert folie.snap_back is False
    assert folie.dur is not None and folie.beats is None

    # Und der Beweis, dass es ohne diese Zeile schiefginge.
    with_snap = folie.model_copy(update={"snap_back": True})
    segmente = [with_snap if isinstance(s, TitleSegment) else s for s in edit.segments]
    verdorben = edit.model_copy(update={"segments": segmente})
    slot2 = next(s for s in plan_from_edit(verdorben).slots if s.intent.title)
    assert to_time(slot2.frames, FPS) > 20.0, \
        "ohne snap_back: false frisst die Folie die ganze Stille"


def test_ein_auftakt_mit_beats_in_einer_free_region_bricht_nicht_ab():
    """Regression aus dem ersten Smoketest.

    `slideshow chapters` schrieb dem Auftakt ein `beats:` mit, und ein Film
    beginnt haeufig mit einer free-Region — die ersten Sekunden eines Stuecks
    lassen sich selten rastern. ``beats`` wurde damals schon beim Einsetzen auf
    den Intent gelegt, also **bevor** feststand, in welcher Region die Folie
    landet: der erste ``plan_slots``-Lauf scheiterte an "`beats:` ist nur in
    einer beat-Region gueltig", noch bevor die Lagekorrektur zum Zug kam.
    """
    regions = [Region(type="free", start=0.0, end=4.015,
                      reason="niedrige Rhythmus-Konfidenz"),
               _beat_region(4.015, 90.0)]
    edit, plan, _cov = _bauen(_manifest(), regions,
                              [Chapter(at=0, title="Skandinavien", beats=16)])

    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    assert folie.beats is None, "in einer free-Region gibt es keine Beats"
    assert plan.slots[0].intent.title is not None
    # Wirkungslos, aber nicht stillschweigend: die Zahl steht sichtbar in
    # chapters.yaml und taete offenbar etwas.
    assert any("wirkungslos" in w for w in plan.warnings)


def test_eine_folie_die_erst_beim_nachplanen_in_die_stille_rutscht_bricht_nicht_ab():
    """Derselbe Fehler, eine Stufe spaeter — und deshalb lange unentdeckt.

    Der Auftakt-Fall oben behandelt nur den *ersten* Planungslauf. ``beats``
    kommt aber ein zweites Mal an den Intent: ``_titel_in_beatregion`` setzt es,
    und ``plan_with_titles`` plant danach neu. Liegt die Phrasengrenze genau auf
    dem Regionsende — hier bei 16 s, waehrend die Folie bei 15 s beginnt —, dann
    dehnt die Lagekorrektur den Vorgaenger, die Folie rutscht in die free-Region
    und traegt das ``beats`` aus dem vorigen Durchgang noch mit sich.

    Der Ausweg (``_titel_in_freeregion`` raeumt es im naechsten Durchgang weg)
    kam nie zum Zug, weil ``plan_slots`` davor abbrach.
    """
    regions = [_beat_region(0.0, 16.0),
               Region(type="free", start=16.0, end=90.0,
                      reason="niedrige Rhythmus-Konfidenz")]
    edit, plan, _cov = _bauen(_manifest(), regions,
                              [Chapter(before="img_005", title="Malmoe")],
                              defaults=Defaults(beats_per_still=6))

    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    assert folie.beats is None, "in der free-Region gibt es keine Beats"
    i, slot = _titelslots(plan)[0]
    assert plan.regions[slot.region_index].type == "free"
    assert to_time(slot.start_f, FPS) == pytest.approx(16.0), \
        "die Lagekorrektur soll die Folie trotzdem auf die Phrasengrenze ziehen"

    # Der Vorgaenger traegt die Dehnung sichtbar (Entscheidung 3c) — hier
    # allerdings nicht mehr als materialisiertes `beats:`: die Restplatz-Regel
    # in ``plan_slots`` schlaegt ihm die letzte Sekunde der Beat-Region schon
    # von selbst zu, weil sie unter der Mindeststandzeit liegt. Fuer die
    # Lagekorrektur bleibt danach nichts mehr zu tun.
    vorgaenger = plan.slots[i - 1]
    standard = 6 * plan.regions[0].beat_duration()
    assert vorgaenger.frames / FPS > standard, \
        "der Vorgaenger muss laenger stehen als ein Standardbild"
    assert to_time(vorgaenger.end_f, FPS) == pytest.approx(16.0)
    assert not any("beat-Region" in w for w in plan.warnings), \
        "aufgeraeumt, also auch nichts mehr zu melden"

    # Der eigentliche Grund fuer Entscheidung 3c: die Lage muss den Weg ueber
    # die Datei ueberstehen. Sie tut es auch ohne `beats:`, weil die
    # Restplatz-Regel aus denselben Regionen dasselbe Ergebnis rechnet.
    zurueck = plan_from_edit(edit)
    assert [s.start_f for s in zurueck.slots] == [s.start_f for s in plan.slots]


def test_der_auftakt_bekommt_keinen_verschiebevorschlag():
    """Er gehoert an den Anfang, nicht auf eine Zaesur — davor ist nichts."""
    regions = [Region(type="free", start=0.0, end=4.015, reason="Intro"),
               _beat_region(4.015, 90.0)]
    _edit, plan, _cov = _bauen(_manifest(), regions,
                               [Chapter(at=0, title="Skandinavien")])
    assert not [w for w in plan.warnings if "Regionsgrenze" in w
                and "Skandinavien" in w]


def test_ein_kapitel_mit_beats_in_einer_beat_region_wirkt():
    """Die Gegenprobe: dort ist `beats` genau richtig und wird uebernommen."""
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe", beats=16)])
    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    assert folie.beats == 16


def test_kurze_stille_braucht_keinen_override():
    """Der Normalfall: unter ``hold_seconds`` kachelt die Region von selbst."""
    regions = [_beat_region(0.0, 40.0),
               Region(type="free", start=40.0, end=48.0, reason="stille", quiet=True)]
    _edit, plan, _cov = _bauen(_manifest(), regions, [Chapter(at=10, title="Malmoe")],
                               dauer=48.0)
    (_i, slot), = _titelslots(plan)
    assert not slot.hold
    assert slot.intent.dur is None and slot.intent.beats is None


# --------------------------------------------------------------------------
# T9 — Rundlauf
# --------------------------------------------------------------------------

def test_titelfolie_bleibt_beim_rundlauf_eine_titelfolie():
    """Sonst kippt sie zum Standbild — und in der Stille faellt dabei die
    Regel aus Entscheidung 3b weg, ohne dass etwas protestiert."""
    edit, _plan, _cov = _stille_projekt()
    erneut = EditList.model_validate(edit.model_dump(mode="json", by_alias=True))
    folie = next(s for s in erneut.segments if s.type == "title")
    assert isinstance(folie, TitleSegment)
    assert folie.title == "Malmoe"
    assert folie.snap_back is False


def test_plan_ueberlebt_den_weg_durch_die_edit_list_mit_titeln(tmp_path):
    edit, plan, _cov = _stille_projekt()
    pfad = tmp_path / "edit.yaml"
    pfad.write_text(dump_edit_yaml(edit), encoding="utf-8")
    erneut = plan_from_edit(EditList.load(pfad))

    assert [(s.start_f, s.end_f) for s in erneut.slots] == \
           [(s.start_f, s.end_f) for s in plan.slots]
    assert erneut.transitions == plan.transitions
    assert [s.intent.src for s in erneut.slots] == [s.intent.src for s in plan.slots]


def test_mehrfaches_schreiben_wandert_nicht(tmp_path):
    edit, _plan, _cov = _stille_projekt()
    vorher = None
    for _ in range(3):
        pfad = tmp_path / "edit.yaml"
        pfad.write_text(dump_edit_yaml(edit), encoding="utf-8")
        edit = EditList.load(pfad)
        jetzt = dump_edit_yaml(edit)
        if vorher is not None:
            assert jetzt == vorher
        vorher = jetzt


# --------------------------------------------------------------------------
# T11 — ohne Tonspur
# --------------------------------------------------------------------------

def test_ohne_tonspur_verlaengern_titel_die_timeline():
    """``material_seconds`` rechnet mit ``n_media`` — und eine Titelfolie ist
    kein Medium, belegt aber einen Slot.

    Zaehlt man sie nicht mit, fehlt dem Film je Titel dessen Standzeit, und die
    zugeschnittene Regionenkarte deckt die Timeline nicht mehr ab.
    """
    manifest = _manifest(ton="")
    regions = [Region(type="free", start=0.0, end=48.0, reason="ohne Ton")]

    ohne, plan_ohne, _ = _bauen(manifest, regions, [], dauer=0.0)
    mit, plan_mit, _ = _bauen(manifest, regions,
                              [Chapter(at=4, title="Malmoe"),
                               Chapter(at=8, title="Stockholm")], dauer=0.0)

    (_i, slot), *rest = _titelslots(plan_mit)
    standzeit = sum(to_time(s.frames, FPS) for _i, s in _titelslots(plan_mit))
    assert len(rest) == 1
    assert mit.audio["duration"] == pytest.approx(
        ohne.audio["duration"] + standzeit, abs=2.0 / FPS)
    # Und die Karte deckt weiterhin lueckenlos ab.
    validate_edit(mit)


# --------------------------------------------------------------------------
# Deckungsrechnung
# --------------------------------------------------------------------------

def test_titel_werden_getrennt_gezaehlt():
    """"12 Medien passen nicht mehr in die Musik" ist irrefuehrend, wenn drei
    der Slots Kapitelanfaenge sind, die man nicht einfach weglassen moechte."""
    _edit, _plan, cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe"),
                                Chapter(at=0, title="Skandinavien")])
    assert cov.titles == 2
    assert cov.stills == 12
    assert sum(r["titles"] for r in cov.per_region) == 2


# --------------------------------------------------------------------------
# Tonpausen-Vorschlag (Entscheidung 6c)
# --------------------------------------------------------------------------

def _mit_stille(position: int):
    """44 s Takt, 12 s echte Stille, dann wieder Takt."""
    regions = [_beat_region(0.0, 44.0),
               Region(type="free", start=44.0, end=56.0, reason="stille", quiet=True),
               _beat_region(56.0, 90.0)]
    return _bauen(_manifest(), regions, [Chapter(at=position, title="Malmoe")])


def test_ein_titel_neben_der_stille_bekommt_einen_verschiebevorschlag():
    """Die Pause zwischen zwei Tracks ist bereits eine musikalische
    Kapitelgrenze — landet die Folie knapp daneben, ist das schade.

    Ein Vorschlag im Bericht, keine automatische Verschiebung: welches Foto zu
    welcher Stadt gehoert, weiss das Werkzeug nicht.
    """
    _edit, plan, _cov = _mit_stille(10)
    passend = [w for w in plan.warnings if "Pause im Ton" in w]
    assert passend, "der Hinweis fehlt"
    assert "44.0 s" in passend[0]
    assert "1 Bild spaeter" in passend[0]


def test_ein_titel_in_der_stille_bekommt_keinen_vorschlag():
    """Die Gegenprobe — sonst schlaegt der Bericht vor, was schon gilt."""
    _edit, plan, _cov = _mit_stille(11)
    assert not [w for w in plan.warnings if "Pause im Ton" in w]


def test_weit_entfernte_kanten_bleiben_unerwaehnt():
    """Drei Bilder zu verschieben ist keine Feinkorrektur mehr, sondern eine
    inhaltliche Aenderung — dazu schweigt das Werkzeug."""
    _edit, plan, _cov = _mit_stille(8)
    assert not [w for w in plan.warnings if "Pause im Ton" in w]


# --------------------------------------------------------------------------
# T3 — Lokalitaet der Aenderung
# --------------------------------------------------------------------------

def test_ein_eingefuegtes_kapitel_laesst_die_folgenden_bilder_in_ruhe():
    """Abnahmekriterium T3, und der Grund fuer Entscheidung 7.

    Solange die Bewegung am Slot-Index hing, verschob eine eingefuegte
    Titelfolie die Richtung **jedes** folgenden Bildes — damit dessen
    Cache-Key, damit rendert der halbe Film neu. Die Zusage aus Prinzip 2,
    dass eine Korrektur genau drei Neurenderungen ausloest, galt fuers
    Einfuegen gar nicht.

    Geprueft wird an der Bewegung selbst, nicht am Index: gleiche Kennung,
    gleiche Richtung. Die *Dauer* darf sich sehr wohl aendern — der Titel
    verschiebt die Nachbarn auf dem Raster, und daraus leitet sich der
    Zoombetrag ab. Verglichen wird deshalb die Richtung, nicht der Betrag.
    """
    from slideshow.kenburns import plan_motion

    def richtungen(chapters):
        _edit, plan, _cov = _bauen(_manifest(), [_beat_region()], chapters)
        d = _edit.defaults.kb
        return {s.intent.src: (plan_motion(s.intent.src, 4.0, d).z1
                               > plan_motion(s.intent.src, 4.0, d).z0,
                               plan_motion(s.intent.src, 4.0, d).c1)
                for s in plan.slots if s.intent.title is None}

    ohne = richtungen([])
    mit = richtungen([Chapter(before="img_005", title="Malmoe")])

    gemeinsam = set(ohne) & set(mit)
    assert len(gemeinsam) >= 10, "die Bilder muessen in beiden Laeufen vorkommen"
    for src in gemeinsam:
        assert mit[src] == ohne[src], f"{src} bewegt sich nur wegen der Einfuegung anders"


# --------------------------------------------------------------------------
# Fokusblende (Entscheidung 5d)
# --------------------------------------------------------------------------

def test_fokusblende_koppelt_die_kamerafahrt():
    """Zoom und Bildmitte der Folie enden dort, wo die des Folgebildes beginnen.

    Ohne das wirkt die Aufloesung nicht wie ein Schaerfezug, sondern wie ein
    Schnitt zwischen zwei aehnlichen Bildern.
    """
    edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")])
    (i, slot), = _titelslots(plan)
    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    folge = plan.slots[i + 1].intent

    assert folie.bg == folge.src, "bg: auto zeigt auf das erste Bild des Abschnitts"
    assert folie.kb is not None and folge.kb is not None
    assert folie.kb.z[1] == folge.kb.z[0]
    assert folie.kb.c[2:] == folge.kb.c[:2]
    # Die Folie zoomt hinein, damit das Folgebild oberhalb von z = 1,0 anfaengt
    # und dort schon den vollen Spielraum des Bildrands hat.
    assert folie.kb.z[0] == 1.0 and folie.kb.z[1] > 1.0
    assert folge.kb.z[1] > folge.kb.z[0]


def test_die_gekoppelte_fahrt_bleibt_im_bild():
    """Der Deckel aus ``plan_motion`` gilt ueber beide Segmente zusammen.

    Die Fahrt faengt beim Folgebild bereits ausgelenkt an; was der groesste
    Zoom hergibt, teilen sich Folie und Folgebild. Ohne die gemeinsame Rechnung
    liefe der Schwenk gegen Ende in die Klemmung und stuende still, waehrend der
    Zoom weiterlaeuft.
    """
    edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")])
    (i, _slot), = _titelslots(plan)
    folge = plan.slots[i + 1].intent

    erlaubt = 0.5 - 1.0 / (2.0 * folge.kb.z[1])
    for achse in (0, 1):
        assert abs(folge.kb.c[2 + achse] - 0.5) <= erlaubt + 1e-6


def test_blende_in_die_zaesur_ist_laenger_als_die_uebrigen():
    """Der Film atmet in die Zaesur ein und setzt danach neu an."""
    defaults = Defaults()
    _edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe")],
                               defaults=defaults)
    (i, _slot), = _titelslots(plan)
    gewoehnlich = plan.transitions[2]
    assert plan.transitions[i] > gewoehnlich
    assert plan.transitions[i + 1] > gewoehnlich       # Fokusblende


def test_explizites_kb_gewinnt_gegen_die_kopplung():
    """Prinzip 1: was in der Datei steht, wird nicht ueberschrieben."""
    from slideshow.models import KBSpec
    _edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe",
                                        kb=KBSpec(z=(1.0, 1.0)))])
    (_i, slot), = _titelslots(plan)
    assert slot.intent.kb.z == (1.0, 1.0)


# --------------------------------------------------------------------------
# Bewegung (`motion`)
# --------------------------------------------------------------------------

#: Die Bewegung, die keine ist — Zoom 1,0 und eine Bildmitte, die sich nicht
#: ruehrt. Genau der Block, den `docs/edit-yaml.md` unter "Bewegung fuer ein
#: Bild abschalten" nennt.
STILLSTAND = ((1.0, 1.0), (0.5, 0.5, 0.5, 0.5))


def _folie(edit) -> TitleSegment:
    return next(s for s in edit.segments if isinstance(s, TitleSegment))


def test_eine_folie_ohne_bewegung_steht_still():
    """Der Text ist in die Pixel eingebrannt und faehrt sonst mit."""
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe",
                                        motion="none")])
    folie = _folie(edit)
    assert (folie.kb.z, folie.kb.c) == STILLSTAND


def test_der_stillstand_steht_als_gewoehnliches_kb_in_der_datei():
    """Uebersetzt in Absicht, nicht in eine Sonderregel: weder ``planner.py``
    noch ``render.py`` bekommen eine Zeile ueber Titel."""
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe",
                                        motion="none")])
    text = dump_edit_yaml(edit)
    assert "motion: none" in text
    assert "z: [1.0, 1.0]" in text


def test_motion_none_ueberlebt_den_rundlauf(tmp_path):
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe",
                                        motion="none")])
    p = tmp_path / "edit.yaml"
    p.write_text(dump_edit_yaml(edit), encoding="utf-8")
    folie = _folie(EditList.load(p))
    assert folie.motion == "none"
    assert (folie.kb.z, folie.kb.c) == STILLSTAND


def test_motion_gilt_auch_ohne_kb_in_der_datei(tmp_path):
    """Der Fall, an dem eine zweite Wahrheit entstuende.

    Kennte nur ``build`` die Regel, faehre eine von Hand geschriebene Folie mit
    ``motion: none`` beim Rendern trotzdem — die Datei saehe richtig aus und der
    Film waere es nicht.
    """
    p = tmp_path / "edit.yaml"
    p.write_text(
        "version: 2\nfps: 60.0\nsize: [1280, 720]\n"
        "audio: {file: '', duration: 20.0, regions: "
        "[{type: beat, start: 0.0, end: 20.0, bpm: 120.0, offset: 0.0}]}\n"
        "defaults: {}\n"
        "segments:\n  - {type: title, title: Malmoe, motion: none, beats: 8}\n",
        encoding="utf-8")
    slot = next(s for s in plan_from_edit(EditList.load(p)).slots if s.intent.title)
    assert (slot.intent.kb.z, slot.intent.kb.c) == STILLSTAND


def test_die_vorgabe_gilt_fuer_alle_folien():
    defaults = Defaults()
    defaults.title.motion = "none"
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(at=0, title="Skandinavien"),
                                Chapter(before="img_005", title="Malmoe")],
                               defaults=defaults)
    folien = [s for s in edit.segments if isinstance(s, TitleSegment)]
    assert len(folien) == 2
    assert all((f.kb.z, f.kb.c) == STILLSTAND for f in folien)


def test_handgesetztes_kb_gewinnt_gegen_motion_none():
    """``motion`` ist die bequeme Schreibweise, nicht die staerkere."""
    from slideshow.models import KBSpec
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe",
                                        motion="none", kb=KBSpec(z=(1.0, 1.2)))])
    assert _folie(edit).kb.z == (1.0, 1.2)


def test_eine_stillstehende_folie_bekommt_keine_gekoppelte_fahrt():
    """Die Fokusblende bleibt, ihre Kopplung entfaellt.

    Sonst bekaeme die Folie ueber die Hintertuer doch eine Fahrt — und das
    Folgebild muesste sie fortsetzen, statt seine eigene zu behalten.
    """
    edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe",
                                       motion="none")])
    (i, _slot), = _titelslots(plan)
    assert (_folie(edit).kb.z, _folie(edit).kb.c) == STILLSTAND
    assert plan.slots[i + 1].intent.kb is None, "das Folgebild behaelt seine Bewegung"
    assert plan.transitions[i + 1] > plan.transitions[2], "der Schaerfezug bleibt lang"


def test_die_bewegung_aendert_das_asset_nicht():
    """``motion`` gehoert zur Choreografie, nicht zu den Pixeln — wie
    ``xfade_in``. Ginge es in den Hash ein, backte ein Umschalten alle Folien neu."""
    d = Defaults()
    ruhig = TitleSegment(title="Malmoe", motion="none")
    fahrend = TitleSegment(title="Malmoe", motion="kenburns")
    assert title_asset(ruhig, d, (3840, 2160)) == title_asset(fahrend, d, (3840, 2160))


# --------------------------------------------------------------------------
# Hintergrund und zweite Zeile
# --------------------------------------------------------------------------

def test_bg_darf_eine_medien_id_nennen():
    """In ``chapters.yaml`` stehen IDs; einen Cache-Pfad muesste man nachschlagen."""
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe",
                                        bg="img_009")])
    assert _folie(edit).bg == "cache/img_009.jpg"


def test_pfad_und_farbflaeche_bleiben_unangetastet():
    for wunsch, erwartet in (("cache/img_009.jpg", "cache/img_009.jpg"),
                             ("#1b2a3a", "#1b2a3a"), ("none", "none")):
        edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                                   [Chapter(before="img_005", title="Malmoe",
                                            bg=wunsch)])
        assert _folie(edit).bg == erwartet


def test_ein_clip_taugt_nicht_als_hintergrund():
    """Dieselbe Regel, nach der ``bg: auto`` das naechste *Standbild* sucht.

    Geprueft an ``insert_titles`` statt am ganzen Lauf: ein Clip im Manifest
    braucht ein Intermediate mit Laengenangabe, und darum geht es hier nicht.
    """
    from slideshow.build import insert_titles
    from slideshow.planner import Intent

    media = [MediaItem(id="clip_001", path="src/clip_001.mov", kind="clip",
                       cache_path="cache/clip_001.mov"),
             MediaItem(id="img_001", path="src/img_001.jpg", kind="image",
                       cache_path="cache/img_001.jpg")]
    intents = [Intent(kind="clip", src="cache/clip_001.mov", index=0),
               Intent(kind="still", src="cache/img_001.jpg", index=1)]
    with pytest.raises(SchemaError) as exc:
        insert_titles(intents, [Chapter(at=0, title="Malmoe", bg="clip_001")],
                      media, Defaults(), (1280, 720))
    assert "ist ein Clip" in str(exc.value)


def test_ein_unbekannter_hintergrund_nennt_das_kapitel():
    """Sonst kaeme die Meldung erst aus ``_validate_sources`` — mit einem
    Segmentindex, den man in ``chapters.yaml`` nicht wiederfindet."""
    with pytest.raises(SchemaError) as exc:
        _bauen(_manifest(), [_beat_region()],
               [Chapter(before="img_005", title="Malmoe", bg="img_999")])
    assert "img_999" in str(exc.value) and "Malmoe" in str(exc.value)


def test_bg_auto_wird_in_der_datei_materialisiert():
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe")])
    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    assert folie.bg == "cache/img_005.jpg"


def test_subtitle_auto_nennt_tag_und_datum():
    edit, _plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe",
                                        subtitle="auto")])
    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    assert folie.subtitle.startswith("Tag ")
    assert "·" in folie.subtitle


def test_titel_hinter_allem_material_faellt_auf_schwarz_zurueck():
    edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                              [Chapter(at=99, title="Ende", subtitle=None)])
    folie = next(s for s in edit.segments if isinstance(s, TitleSegment))
    assert folie.bg == "none"
    assert any("bg: none" in w for w in plan.warnings)


# --------------------------------------------------------------------------
# Assetpfad
# --------------------------------------------------------------------------

def test_assetpfad_haengt_am_inhalt_nicht_an_der_position():
    """Deterministisch und lokal: derselbe Text ergibt dieselbe Datei."""
    d = Defaults()
    a = TitleSegment(title="Malmoe", subtitle="Tag 11", bg="cache/img_042.jpg")
    b = TitleSegment(title="Malmoe", subtitle="Tag 11", bg="cache/img_042.jpg")
    assert title_asset(a, d, (3840, 2160)) == title_asset(b, d, (3840, 2160))
    assert title_asset(a, d, (3840, 2160)).startswith("cache/title_malmoe_")


@pytest.mark.parametrize("aenderung", [
    {"title": "Stockholm"}, {"subtitle": "Tag 12"}, {"bg": "cache/img_043.jpg"},
])
def test_jede_sichtbare_aenderung_ergibt_ein_neues_asset(aenderung):
    d = Defaults()
    a = TitleSegment(title="Malmoe", subtitle="Tag 11", bg="cache/img_042.jpg")
    assert title_asset(a, d, (3840, 2160)) != \
        title_asset(a.model_copy(update=aenderung), d, (3840, 2160))


def test_layoutaenderung_invalidiert_das_asset():
    a = TitleSegment(title="Malmoe")
    gross = Defaults()
    gross.title.size = 0.09
    assert title_asset(a, Defaults(), (3840, 2160)) != title_asset(a, gross, (3840, 2160))


def test_blendenlaenge_invalidiert_das_asset_nicht():
    """``xfade_in`` aendert die Choreografie, nicht das Bild."""
    a = TitleSegment(title="Malmoe")
    anders = Defaults()
    anders.title.xfade_in = 2.5
    assert title_asset(a, Defaults(), (3840, 2160)) == title_asset(a, anders, (3840, 2160))


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

def test_folie_ohne_ueberschrift_scheitert_beim_laden(tmp_path):
    p = tmp_path / "edit.yaml"
    p.write_text(
        "version: 2\nfps: 60.0\nsize: [1280, 720]\n"
        "audio: {file: '', duration: 10.0, regions: [{type: free, start: 0.0, end: 10.0}]}\n"
        "defaults: {}\n"
        "segments:\n  - {type: title, title: '', beats: 8}\n", encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        EditList.load(p)
    assert "Ueberschrift" in str(exc.value)


def test_fehlerpfad_nennt_nicht_den_discriminator(tmp_path):
    """Ohne ``_DISCRIMINATORS``-Eintrag stuende hier ``segments[0].title.kb.z``
    — ein Pfad, den es in der Datei nicht gibt."""
    p = tmp_path / "edit.yaml"
    p.write_text(
        "version: 2\nfps: 60.0\nsize: [1280, 720]\n"
        "audio: {file: '', duration: 10.0, regions: [{type: free, start: 0.0, end: 10.0}]}\n"
        "defaults: {}\n"
        "segments:\n  - {type: title, title: Malmoe, kb: {z: [-1, 2]}}\n", encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        EditList.load(p)
    assert exc.value.path == "segments[0].kb.z"


def test_unbekanntes_feld_an_der_folie_ist_ein_fehler(tmp_path):
    p = tmp_path / "edit.yaml"
    p.write_text(
        "version: 2\nfps: 60.0\nsize: [1280, 720]\n"
        "audio: {file: '', duration: 10.0, regions: [{type: free, start: 0.0, end: 10.0}]}\n"
        "defaults: {}\n"
        "segments:\n  - {type: title, title: Malmoe, untertitel: x}\n", encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        EditList.load(p)
    assert "unbekanntes Feld" in str(exc.value)


def test_unlesbare_farbangabe_wird_abgewiesen():
    with pytest.raises(ValueError):
        TitleSegment(title="Malmoe", bg="#1b2a3")


# --------------------------------------------------------------------------
# chapters.yaml
# --------------------------------------------------------------------------

def test_kapitel_braucht_genau_einen_anker():
    with pytest.raises(ValueError):
        Chapter(title="Malmoe")
    with pytest.raises(ValueError):
        Chapter(title="Malmoe", before="img_042", at=0)


def test_kapitel_ohne_ueberschrift_bricht_mit_klarer_meldung_ab():
    with pytest.raises(ValueError) as exc:
        Chapter(before="img_042", title="  ")
    assert "ausfuellen" in str(exc.value)


def test_fehlerpfad_nennt_das_feld_title(tmp_path):
    """``title`` heisst zufaellig wie sein eigener Typ.

    Die Regel, die den Discriminator aus dem Pfad wirft, darf den *Feldnamen*
    nicht mitnehmen — sonst meldet ein Kapitel ohne Ueberschrift nur
    ``chapters[1]``, und man sucht in einer Zeile mit fuenf Schluesseln.
    """
    from slideshow.models import ChapterList
    p = tmp_path / "chapters.yaml"
    p.write_text("chapters:\n  - {at: 0, title: Auftakt}\n  - {before: img_042}\n",
                 encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        ChapterList.load(p)
    assert exc.value.path == "chapters[1].title"
    assert exc.value.line == 3


def test_unbekannte_medien_id_nennt_das_kapitel():
    with pytest.raises(SchemaError) as exc:
        _bauen(_manifest(), [_beat_region()],
               [Chapter(before="img_999", title="Malmoe")])
    assert "img_999" in str(exc.value) and "Malmoe" in str(exc.value)


# --------------------------------------------------------------------------
# Lesezeit und Nachpruefung
# --------------------------------------------------------------------------

def test_lesezeit_waechst_mit_der_wortzahl():
    kurz = TitleSegment(title="Malmoe")
    lang = TitleSegment(title="Malmoe", subtitle="Tag 11 · 24. Juli")
    assert reading_seconds(lang) > reading_seconds(kurz)


def test_zu_kurze_standzeit_wird_gemeldet():
    defaults = Defaults()
    defaults.title.beats = 2                # 1,0 s bei 120 BPM
    _edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                               [Chapter(before="img_005", title="Malmoe",
                                        subtitle="Drei Wochen, vier Staedte")],
                               defaults=defaults)
    assert any("Lesezeit" in w for w in plan.warnings)


def test_verschobene_phrasenlage_faellt_bei_der_pruefung_auf():
    """Die Ausrichtung ist materialisiert und zerfaellt bei jeder Aenderung
    davor — deshalb ist die Nachpruefung nicht optional."""
    edit, plan, _cov = _bauen(_manifest(), [_beat_region()],
                              [Chapter(before="img_005", title="Malmoe")])
    assert check_title_phrases(plan, edit.defaults) == []

    erstes = next(s for s in edit.segments if isinstance(s, StillSegment))
    erstes.beats = (erstes.beats or 8) + 1
    verschoben = plan_from_edit(edit)
    assert any("Phrasengrenze" in h for h in
               check_title_phrases(verschoben, edit.defaults))
