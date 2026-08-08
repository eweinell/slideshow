"""Phase 3b — Timeline-Planung (Abschnitte 6.0, 6.3–6.6 und 8.2).

Hier faellt die eine Entscheidung, die ueber die ganze Laufzeit traegt:

    **Segmentgrenzen werden als absolute Zeitpunkte berechnet und erst dann
    differenziert. Nie Einzeldauern aufaddieren.**

Bei 100 Segmenten und gerundeten Framegrenzen akkumuliert sich sonst ein
Versatz von mehreren Frames, und der Sync laeuft gegen Ende sichtbar weg —
genau dort, wo die Musik meist am dichtesten ist. Deshalb rechnet dieses Modul
durchgehend in *absoluten Framenummern* auf der Master-Timeline, und jede Dauer
ist eine Differenz zweier solcher Nummern.

.. note::
   **Reichweite von ``snap_back``.** Das Briefing beschreibt in 6.3, ein
   ``dur:``-Override verschiebe "alle nachfolgenden Schnitte gegen das Raster",
   und ``snap_back`` hole den Sync zurueck. Hier ist der Standard-Slot einer
   Beat-Region jedoch nicht relativ zum Cursor definiert, sondern absolut:
   ``beats_per_still`` Beats *ab dem naechsten Beat auf dem Raster*. Damit
   findet schon das folgende Bild von selbst aufs Raster zurueck, und der
   Versatz bleibt auf genau einen Schnitt begrenzt statt bis zum Ende
   mitzulaufen.

   ``snap_back`` entscheidet deshalb nur noch ueber diesen einen Schnitt: ob
   das Bild mit dem Override selbst auf den naechsten Beat aufgerundet wird
   (``true``, Default) oder exakt seine Sekunden behaelt (``false``). Das ist
   die konservativere Auslegung — ein Tippfehler in ``dur:`` kann den Rest des
   Films nicht mehr aus dem Takt bringen.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from .errors import SchemaError
from .models import Defaults, KBSpec, Region, TitleSegment

log = logging.getLogger("slideshow.planner")

_EPS = 1e-6


def to_frame(t: float, fps: float) -> int:
    """Absoluter Zeitpunkt -> absolute Framenummer."""
    return int(round(t * fps))


def to_time(frame: int, fps: float) -> float:
    return frame / fps


# --------------------------------------------------------------------------
# Regionsraster
# --------------------------------------------------------------------------

class RegionGrid:
    """Die zulaessigen Schnittpunkte einer Region.

    Beat-Regionen rastern auf ``offset + k * beat``; free-Regionen auf eine
    driftfreie ``linspace``-Kachelung, die die Region *exakt* fuellt.
    """

    def __init__(self, region: Region, defaults: Defaults, fps: float, index: int):
        self.region = region
        self.index = index
        self.fps = fps
        self.defaults = defaults
        self.is_beat = region.type == "beat"
        self.hold = False

        if self.is_beat:
            self.beat = region.beat_duration()
            self.offset = float(region.offset if region.offset is not None else region.start)
            self.bps = int(region.beats_per_still or defaults.beats_per_still)
            self.edges: np.ndarray | None = None
        else:
            self.beat = 0.0
            still_seconds = float(region.still_seconds or defaults.still_seconds)
            n, self.hold = _free_count(region.duration, still_seconds,
                                       defaults.still_tolerance, defaults.hold_seconds,
                                       quiet=region.quiet)
            # Driftfrei: Kanten in Frames rechnen, Dauern daraus differenzieren.
            self.edges = np.rint(
                np.linspace(region.start, region.end, n + 1) * fps).astype(np.int64)

    # -- Kandidaten ----------------------------------------------------
    def beat_time(self, k: float) -> float:
        """``k`` darf gebrochen sein — ``beats: 1.5`` ist eine legitime Angabe."""
        return self.offset + k * self.beat

    def beat_index_at_or_after(self, t: float) -> int:
        return math.ceil((t - self.offset) / self.beat - _EPS)

    def default_end(self, cursor: float) -> float:
        """Das Ende des naechsten Standard-Slots ab ``cursor``."""
        if self.is_beat:
            k0 = self.beat_index_at_or_after(cursor)
            return self.beat_time(k0 + self.bps)
        assert self.edges is not None
        cf = cursor * self.fps
        for e in self.edges[1:]:
            if e > cf + 0.5:
                return float(e) / self.fps
        return self.region.end

    def snap_nearest(self, t: float) -> float:
        """Naechstliegender Schnittpunkt — fuer das Clip-Out-Snapping (6.6)."""
        if self.is_beat:
            k = round((t - self.offset) / self.beat)
            return self.beat_time(int(k))
        assert self.edges is not None
        arr = self.edges / self.fps
        return float(arr[int(np.argmin(np.abs(arr - t)))])

    def snap_up(self, t: float) -> float:
        """Auf den *naechsten* Schnittpunkt aufrunden — ``snap_back`` (6.3)."""
        if self.is_beat:
            k = self.beat_index_at_or_after(t)
            return self.beat_time(k)
        assert self.edges is not None
        for e in self.edges[1:]:
            if e / self.fps > t + _EPS:
                return float(e) / self.fps
        return self.region.end

    def distance_in_beats(self, a: float, b: float) -> float:
        if self.is_beat and self.beat:
            return abs(a - b) / self.beat
        return abs(a - b)


def _free_count(duration: float, still_seconds: float,
                tolerance: tuple[float, float], hold_seconds: float,
                *, quiet: bool = False) -> tuple[int, bool]:
    """Anzahl Bilder, die eine free-Region **exakt** fuellen (6.3).

    Das ``hold``-Flag gilt nur fuer *stille* Regionen. Eine free-Region
    entsteht auch dann, wenn Musik laeuft, sich aber kein Raster fitten liess
    — bei einem durchgehenden Song ueber mehrere Minuten ist das der
    Normalfall, denn ein starres Raster driftet. Ohne die ``quiet``-Bedingung
    bekaeme genau der einen einzigen Standbild-Slot fuer den ganzen Film.
    Hier greift stattdessen ``still_seconds`` als Standardtakt.
    """
    lo, hi = tolerance
    if quiet and duration > hold_seconds:
        # Sehr lange Stille bekommt ein hold-Flag, damit dort bewusst ein
        # einzelnes ruhiges Bild stehen bleiben kann.
        return (1, True)
    base = max(1, int(round(duration / still_seconds)))
    for n in (base, base + 1, max(1, base - 1)):
        if n >= 1 and lo <= duration / n <= hi:
            return (n, False)
    return (max(1, base), False)


# --------------------------------------------------------------------------
# Eingabe des Planers
# --------------------------------------------------------------------------

@dataclass
class Intent:
    """Was ein Segment *will* — die Absicht aus der Edit-List.

    Der Planer uebersetzt Absicht in absolute Framegrenzen. Sowohl ``build``
    (aus dem Manifest) als auch ``render`` (aus ``edit.yaml``) gehen durch
    dieselbe Funktion; nur so liefern beide garantiert dieselbe Timeline.
    """

    kind: str                       # "still" | "clip"
    src: str
    index: int = 0
    beats: float | None = None
    dur: float | None = None
    hold: bool = False
    snap: str = "out"
    snap_back: bool | None = None
    kb: KBSpec | None = None
    #: Nur Standbilder: das ``motion:`` aus der Datei bzw. dem Feinschliff.
    #: Der Planer liest es nicht — es steht schon als ``kb:`` daneben. Getragen
    #: wird es, damit ``build`` es beim Rueckschreiben wiederfindet und die
    #: Zeile in ``edit.yaml`` erklaert, warum das Bild stillsteht.
    motion: str | None = None
    portrait: str | None = None
    #: Titelfolien: die Absicht aus ``edit.yaml``. Der Planer behandelt sie wie
    #: jedes andere Standbild — ``kind`` bleibt ``"still"``, ``src`` zeigt auf
    #: das gebackene Asset. Das Feld traegt nur, was ``build`` beim Rueckschreiben
    #: braucht, und wofuer die Deckungsrechnung Titel getrennt zaehlen muss.
    title: TitleSegment | None = None
    #: Nur Clips: verfuegbare Laenge und Startpunkt im Intermediate.
    clip_in: float = 0.0
    clip_available: float = 0.0
    clip_out: float | None = None


@dataclass
class Slot:
    """Ein Medium auf seinem Rasterplatz. Grenzen sind absolute Frames."""

    intent: Intent
    region_index: int
    start_f: int
    end_f: int
    hold: bool = False
    #: Nur Clips: der tatsaechlich verwendete Bereich im Intermediate.
    clip_in: float = 0.0
    clip_out: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return self.end_f - self.start_f


@dataclass
class Plan:
    fps: float
    slots: list[Slot]
    #: Uebergangsdauern in Frames an jedem Schnitt. ``len == len(slots) + 1``;
    #: der erste und der letzte Eintrag sind immer 0.
    transitions: list[int]
    total_frames: int
    regions: list[Region]
    warnings: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)
    #: Frames, um die das letzte Segment gestreckt werden musste (Unterdeckung).
    stretched: int = 0
    #: Blendenmodus je Schnitt, sofern die Edit-List einen vorgibt.
    transition_modes: dict[int, str] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Slot-Planung
# --------------------------------------------------------------------------

def plan_slots(regions: list[Region], intents: list[Intent], defaults: Defaults, *,
               fps: float, total_frames: int) -> Plan:
    """Weist jedem Medium seinen Platz auf der Timeline zu."""
    grids = [RegionGrid(r, defaults, fps, i) for i, r in enumerate(regions)]
    if not grids:
        raise SchemaError("Keine Regionen — `slideshow beats` zuerst laufen lassen.",
                          path="audio.regions")

    slots: list[Slot] = []
    warnings: list[str] = []
    # Der Cursor laeuft in *Frames*, nicht in Sekunden. Sonst entstehen an
    # Regionsgrenzen entartete Segmente: eine Region, die bei 16.022 s endet,
    # liegt auf Frame 961.3; nach dem Runden steht der Cursor bei 16.0167 s und
    # damit rechnerisch noch *vor* dem Regionsende — die Region gilt als offen
    # und bekommt ein 1-Frame-Bild angehaengt.
    cursor_f = 0
    ri = 0

    def region_end_frame(i: int) -> int:
        return min(to_frame(grids[i].region.end, fps), total_frames)

    for intent in intents:
        while ri < len(grids) and cursor_f >= region_end_frame(ri):
            ri += 1
        if ri >= len(grids) or cursor_f >= total_frames:
            break

        grid = grids[ri]
        region = grid.region
        cursor = to_time(cursor_f, fps)
        end_time = to_time(total_frames, fps)
        snap_back = defaults.snap_back if intent.snap_back is None else intent.snap_back
        local: list[str] = []
        clip_in = intent.clip_in
        clip_out = intent.clip_out or 0.0

        if intent.kind == "clip":
            available = max(0.0, intent.clip_available - clip_in)
            wanted = (intent.dur if intent.dur is not None
                      else (clip_out - clip_in if intent.clip_out else available))
            wanted = max(1.0 / fps, min(wanted, available))
            end = cursor + wanted
            if intent.snap == "out":
                # Out-Punkt aufs Raster ziehen, indem der *Trim* angepasst wird
                # — nie die Geschwindigkeit (6.6).
                snapped = grid.snap_nearest(end)
                dist = grid.distance_in_beats(snapped, end)
                tol = defaults.clip_snap_tol
                if snapped <= cursor + 1e-4:
                    snapped = grid.snap_up(cursor + 1e-4)
                    dist = grid.distance_in_beats(snapped, end)
                if dist > tol:
                    local.append(
                        f"Out-Punkt liegt {dist:.2f} Beats vom Raster entfernt "
                        f"(Toleranz {tol:g}). Vorschlag: Clip auf "
                        f"{grid.snap_nearest(end) - cursor:.3f} s trimmen oder "
                        f"snap: none setzen.")
                    end = snapped
                elif snapped - cursor > available + 1e-4:
                    # Nach unten snappen, wenn der Clip fuer den naechsten Beat
                    # zu kurz ist — lieber frueher schneiden als strecken.
                    end = grid.snap_nearest(cursor + available)
                    if end <= cursor:
                        end = cursor + available
                    local.append("Clip zu kurz fuer den naechsten Beat, Out-Punkt "
                                 "auf den vorherigen gezogen.")
                else:
                    end = snapped
            elif snap_back:
                end = grid.snap_up(end)
            clip_out = clip_in + (end - cursor)
            if clip_out - clip_in > available + 1e-4:
                local.append("Clip reicht nicht bis zum Rasterpunkt, letztes Bild "
                             "wird gehalten.")
        else:
            if intent.dur is not None:
                # Praezedenz 1: explizite Sekunden gewinnen immer.
                end = cursor + intent.dur
                if snap_back:
                    # Nach einem Override auf den naechsten Beat aufrunden,
                    # damit der Sync danach wieder steht (6.3).
                    end = grid.snap_up(end)
            elif intent.beats is not None and not grid.is_beat:
                # Kein Abbruch: dieser Fall entsteht auch ohne Zutun. ``build``
                # setzt ``beats:`` waehrend der Lagekorrektur (``_adjust_titles``)
                # und plant danach neu — verschiebt sich das Segment dabei ueber
                # eine Regionsgrenze, stand hier ein Fehler, den niemand
                # verursacht hat und den auch niemand beheben konnte. Dasselbe
                # gilt fuer ein ``beats:`` aus ``overrides.yaml``, das an einem
                # Medium haengt und nicht an einer Position.
                #
                # Der Ersatzwert ist der **Standard-Slot der free-Region**, nicht
                # etwa ``beats`` in Sekunden umgerechnet: eine free-Region ist
                # driftfrei gekachelt (siehe ``_free_count``) und wird von ihren
                # Kanten exakt gefuellt. Eine freie Dauer mittendrin verschoebe
                # jeden folgenden Schnitt gegen diese Kachelung.
                local.append(
                    f"segments[{intent.index}].beats: `beats: {intent.beats:g}` gilt "
                    f"nur in einer beat-Region; dieses Segment liegt in der "
                    f"free-Region [{region.start:.3f}, {region.end:.3f}] und steht "
                    f"die Standardlaenge. Fuer eine feste Standzeit `dur:` in "
                    f"Sekunden angeben.")
                end = grid.default_end(cursor)
            elif intent.beats is not None:
                k0 = grid.beat_index_at_or_after(cursor)
                end = grid.beat_time(k0 + float(intent.beats))
            else:
                end = grid.default_end(cursor)

        start_f = cursor_f
        end_f = min(to_frame(end, fps), region_end_frame(ri), total_frames)
        if end_f <= start_f:
            # Kein Platz mehr fuer einen sinnvollen Slot: den Rest der Region
            # zuschlagen, statt ein Ein-Frame-Bild zu erzeugen.
            end_f = region_end_frame(ri)
        if end_f <= start_f:
            continue

        slots.append(Slot(intent=intent, region_index=ri, start_f=start_f, end_f=end_f,
                          hold=intent.hold or (grid.hold and intent.kind == "still"),
                          clip_in=clip_in, clip_out=clip_out, warnings=local))
        for w in local:
            warnings.append(f"{intent.src}: {w}")
        cursor_f = end_f

    unused = [i.src for i in intents[len(slots):]]
    stretched = 0
    if slots and slots[-1].end_f < total_frames:
        stretched = total_frames - slots[-1].end_f
        slots[-1].end_f = total_frames
    if not slots:
        raise SchemaError("Die Timeline enthaelt keine Segmente.", path="segments")

    # Lueckenlosigkeit erzwingen: jede Grenze ist zugleich Ende und Anfang.
    for a, b in zip(slots, slots[1:]):
        b.start_f = a.end_f

    return Plan(fps=fps, slots=slots, transitions=[0] * (len(slots) + 1),
                total_frames=total_frames, regions=regions, warnings=warnings,
                unused=unused, stretched=stretched)


# --------------------------------------------------------------------------
# Uebergaenge (8.2)
# --------------------------------------------------------------------------

def apply_transitions(plan: Plan, defaults: Defaults, *,
                      explicit: dict[int, float] | None = None) -> None:
    """Setzt die Uebergangsdauern an den Schnittpunkten.

    Zeitmodell aus 8.2: der Schnittpunkt ``t`` liegt auf dem Beat, ein Uebergang
    der Dauer ``T`` belegt das Fenster ``[t - T/2, t + T/2]``. Der Schnitt bleibt
    also auf dem Raster, die Blende ist darueber zentriert.

    ``T`` wird auf eine **gerade** Framezahl gerundet, damit ``T/2`` exakt
    aufgeht und die exklusiven Anteile ganzzahlig bleiben.
    """
    fps = plan.fps
    n = len(plan.slots)
    trans = [0] * (n + 1)

    for cut in range(1, n):
        if explicit is not None and cut not in explicit:
            continue
        seconds = (explicit or {}).get(cut)
        if seconds is None:
            seconds = _default_transition_seconds(plan, cut, defaults)
        if seconds <= 0:
            continue
        frames = int(round(seconds * fps / 2.0)) * 2
        trans[cut] = max(0, frames)

    _clamp_transitions(plan, trans)
    plan.transitions = trans


def default_transition_seconds(plan: Plan, cut: int, defaults: Defaults) -> float:
    """Die Blendendauer, die :func:`apply_transitions` an diesem Schnitt saehe.

    Oeffentlich, weil ``build`` einzelne Schnitte abweichend setzt (die
    Choreografie um eine Titelfolie) und dafuer die uebrigen unveraendert
    mitgeben muss — ``explicit`` ist alles-oder-nichts.
    """
    return _default_transition_seconds(plan, cut, defaults)


def _default_transition_seconds(plan: Plan, cut: int, defaults: Defaults) -> float:
    """Blendendauer am Schnitt ``cut``.

    In Beats gerechnet, wenn eine der beiden angrenzenden Regionen ein
    Beat-Raster hat — sonst in Sekunden.
    """
    xf = defaults.xfade
    if xf.dur is not None:
        return float(xf.dur)
    for slot in (plan.slots[cut], plan.slots[cut - 1]):
        region = plan.regions[slot.region_index]
        if region.type == "beat" and region.bpm:
            return float(xf.beats) * region.beat_duration()
    return float(xf.beats) * defaults.still_seconds / max(1, defaults.beats_per_still)


def _clamp_transitions(plan: Plan, trans: list[int]) -> None:
    """Verhindert, dass eine Blende den exklusiven Anteil eines Nachbarn auffrisst.

    Zusaetzlich muss ``T/2`` in die 1-s-Handles eines Clips passen (8.2); der
    Handle-Test selbst steht in :func:`validate_plan`, weil er die Manifestdaten
    braucht.
    """
    for _ in range(4):
        changed = False
        for i, slot in enumerate(plan.slots):
            room = slot.frames - 2                      # mindestens 2 Frames exklusiv
            need = trans[i] // 2 + trans[i + 1] // 2
            if need <= room:
                continue
            scale = room / need if need else 0.0
            for cut in (i, i + 1):
                if trans[cut]:
                    trans[cut] = max(0, int(trans[cut] * scale / 2) * 2)
                    changed = True
        if not changed:
            break


# --------------------------------------------------------------------------
# Aufloesung in Render-Segmente
# --------------------------------------------------------------------------

@dataclass
class RenderSegment:
    """Ein unabhaengig encodierbares Segment (Prinzip 2)."""

    index: int
    kind: str                       # "still" | "clip" | "xfade"
    start_f: int
    end_f: int
    #: Fuer still/clip: der Slot. Fuer xfade: die beiden Nachbarslots.
    slot: Slot | None = None
    a: Slot | None = None
    b: Slot | None = None
    #: Volle sichtbare Spanne des Stills (exklusiver Anteil + halbe Blenden).
    visible_start: int = 0
    visible_end: int = 0
    a_visible: tuple[int, int] = (0, 0)
    b_visible: tuple[int, int] = (0, 0)
    mode: str = "dissolve"

    @property
    def frames(self) -> int:
        return self.end_f - self.start_f


def visible_span(plan: Plan, i: int) -> tuple[int, int]:
    """Die volle sichtbare Spanne eines Slots — exklusiver Anteil plus die
    angrenzenden Uebergangs-*Haelften*.

    Die Ken-Burns-Bewegung ist ueber genau diese Spanne definiert, damit sie
    durch die Blende hindurch weiterlaeuft (8.2 / Kriterium 12).
    """
    slot = plan.slots[i]
    return (slot.start_f - plan.transitions[i] // 2,
            slot.end_f + plan.transitions[i + 1] // 2)


def resolve(plan: Plan) -> list[RenderSegment]:
    """Zerlegt den Plan in die tatsaechlich zu rendernden Segmente."""
    out: list[RenderSegment] = []
    n = len(plan.slots)
    for i, slot in enumerate(plan.slots):
        t_in, t_out = plan.transitions[i], plan.transitions[i + 1]
        vs, ve = visible_span(plan, i)

        # Exklusiver Anteil: [start + T_in/2, end - T_out/2]
        ex_start = slot.start_f + t_in // 2
        ex_end = slot.end_f - t_out // 2
        if ex_end > ex_start:
            out.append(RenderSegment(
                index=len(out), kind=slot.intent.kind, start_f=ex_start, end_f=ex_end,
                slot=slot, visible_start=vs, visible_end=ve))

        if i + 1 < n and plan.transitions[i + 1] > 0:
            nxt = plan.slots[i + 1]
            t = plan.transitions[i + 1]
            cut = slot.end_f
            out.append(RenderSegment(
                index=len(out), kind="xfade",
                start_f=cut - t // 2, end_f=cut + t // 2,
                a=slot, b=nxt,
                a_visible=(vs, ve), b_visible=visible_span(plan, i + 1),
                mode=plan.transition_modes.get(i + 1, "dissolve")))
    return out


def validate_continuity(segments: list[RenderSegment], total_frames: int) -> None:
    """Die Segmente muessen die Timeline lueckenlos und ueberlappungsfrei kacheln."""
    if not segments:
        raise SchemaError("Keine Segmente zu rendern.", path="segments")
    if segments[0].start_f != 0:
        raise SchemaError(
            f"Erstes Segment beginnt bei Frame {segments[0].start_f} statt 0. "
            f"Der Nullpunkt der Master-Timeline ist Sample 0 der Tonspur.",
            path="segments[0]")
    for a, b in zip(segments, segments[1:]):
        if a.end_f != b.start_f:
            kind = "Luecke" if b.start_f > a.end_f else "Ueberlappung"
            raise SchemaError(
                f"{kind} zwischen Segment {a.index} (endet Frame {a.end_f}) und "
                f"Segment {b.index} (beginnt Frame {b.start_f}).",
                path=f"segments[{b.index}]")
    if segments[-1].end_f != total_frames:
        raise SchemaError(
            f"Letztes Segment endet bei Frame {segments[-1].end_f}, die Timeline "
            f"hat {total_frames} Frames.", path=f"segments[{segments[-1].index}]")
    for s in segments:
        if s.frames <= 0:
            raise SchemaError(f"Segment {s.index} hat {s.frames} Frames.",
                              path=f"segments[{s.index}]")


# --------------------------------------------------------------------------
# Laufzeit-Vorabpruefung (6.5)
# --------------------------------------------------------------------------

@dataclass
class Coverage:
    music_seconds: float
    planned_seconds: float
    stills: int
    clips: int
    unused: list[str]
    stretched_seconds: float
    per_region: list[dict]
    #: Titelfolien, getrennt gezaehlt. Sie belegen einen Slot wie ein Foto,
    #: sind aber keines: "5 Medien passen nicht mehr in die Musik" ist
    #: irrefuehrend, wenn drei der Slots Kapitelanfaenge sind, die man nicht
    #: einfach weglassen moechte.
    titles: int = 0
    #: Laenge der *Tonspur*. Weicht von ``music_seconds`` (der Laenge der
    #: Timeline) ab, sobald das Material die Laenge bestimmt: dann wird die
    #: Tonspur beim Muxen gekuerzt oder mit Stille aufgefuellt.
    audio_seconds: float = 0.0

    @property
    def underrun(self) -> bool:
        return self.stretched_seconds > 0.5

    @property
    def overrun(self) -> bool:
        return bool(self.unused)


def coverage(plan: Plan, defaults: Defaults) -> Coverage:
    """Aufgeschluesselt nach Region: verfuegbare Musikdauer, Bilder- und
    Clipbedarf, Ueber- oder Unterdeckung — **vor** jedem Rendern (6.5)."""
    per_region: list[dict] = []
    for i, r in enumerate(plan.regions):
        members = [s for s in plan.slots if s.region_index == i]
        titles = sum(1 for s in members if s.intent.title is not None)
        stills = sum(1 for s in members
                     if s.intent.kind == "still" and s.intent.title is None)
        clips = sum(1 for s in members if s.intent.kind == "clip")
        capacity = _region_capacity(r, defaults)
        per_region.append({
            "index": i, "type": r.type, "start": r.start, "end": r.end,
            "seconds": r.duration, "bpm": r.bpm, "stills": stills, "clips": clips,
            "titles": titles, "capacity": capacity,
        })
    return Coverage(
        music_seconds=to_time(plan.total_frames, plan.fps),
        planned_seconds=to_time(sum(s.frames for s in plan.slots), plan.fps),
        stills=sum(1 for s in plan.slots
                   if s.intent.kind == "still" and s.intent.title is None),
        clips=sum(1 for s in plan.slots if s.intent.kind == "clip"),
        titles=sum(1 for s in plan.slots if s.intent.title is not None),
        unused=plan.unused,
        stretched_seconds=to_time(plan.stretched, plan.fps),
        per_region=per_region)


def material_seconds(regions: list[Region], n_media: int, defaults: Defaults) -> float:
    """Wie lang das Material bei Standardtaktung von sich aus laeuft.

    Gegenstueck zur Tonspurlaenge: erst der Vergleich beider Zahlen sagt, ob
    Musik und Material ueberhaupt zueinander passen. Gerechnet wird mit
    denselben Slotlaengen, die der Planer spaeter vergibt — in einer
    beat-Region ``beats_per_still`` Beats, in einer free-Region der
    Standardtakt.
    """
    rest = n_media
    total = 0.0
    for r in regions:
        if rest <= 0:
            break
        cap = _region_capacity(r, defaults)
        if cap <= 0:
            continue
        nehmen = min(rest, cap)
        total += nehmen * (r.duration / cap)
        rest -= nehmen
    if rest > 0:
        # Material ueber die Karte hinaus: dort gibt es kein Raster mehr, also
        # gilt der Standardtakt.
        total += rest * defaults.still_seconds
    return total


def standard_slot(regions: list[Region], defaults: Defaults) -> float:
    """Laenge *eines* Standardbildes — das Mass fuer die Toleranz."""
    for r in regions:
        if r.type == "beat" and r.bpm:
            return (r.beats_per_still or defaults.beats_per_still) * r.beat_duration()
        return float(r.still_seconds or defaults.still_seconds)
    return float(defaults.still_seconds)


def fit_regions_to(regions: list[Region], duration: float, *,
                   eps: float = 0.02) -> list[Region]:
    """Die Regionenkarte auf eine neue Gesamtlaenge zuschneiden oder verlaengern.

    Die Karte beschreibt die *Tonspur*. Bestimmt ausnahmsweise das Material die
    Laenge, muss sie mitwandern — sonst deckt sie die Timeline nicht mehr
    lueckenlos ab, und der Planer bekommt Slots ohne Region.
    """
    out: list[Region] = []
    for r in regions:
        if r.start >= duration - eps:
            break
        r2 = r.model_copy(deep=True)
        if r2.end > duration:
            r2.end = duration
        if r2.duration <= eps:
            continue
        out.append(r2)

    if not out:
        return [Region(type="free", start=0.0, end=duration,
                       reason="Materiallaenge")]

    letzte = out[-1]
    if letzte.end < duration - eps:
        # Der Schwanz bekommt *immer* eine eigene Region, statt die letzte zu
        # verlaengern. Zwei Gruende: ein Beat-Raster liesse sich dort nicht
        # fortschreiben, wo nichts mehr zu treffen ist — und eine als `quiet`
        # markierte Region wuerde ihre hold-Eigenschaft mitnehmen und den
        # ganzen Schwanz auf ein einziges Standbild zusammenziehen. Hinter dem
        # Tonende ist aber keine Stille, sondern gar kein Ton; dort laeuft die
        # Bildfolge im Standardtakt weiter.
        out.append(Region(type="free", start=letzte.end, end=duration,
                          quiet=False,
                          reason="Material laeuft ueber die Tonspur hinaus"))
    else:
        letzte.end = duration
    return out


def _region_capacity(r: Region, defaults: Defaults) -> int:
    """Wie viele Standardbilder die Region fassen wuerde."""
    if r.type == "beat" and r.bpm:
        per_still = (r.beats_per_still or defaults.beats_per_still) * r.beat_duration()
        return max(1, int(round(r.duration / per_still)))
    n, _hold = _free_count(r.duration, r.still_seconds or defaults.still_seconds,
                           defaults.still_tolerance, defaults.hold_seconds,
                           quiet=r.quiet)
    return n


def slot_capacity(regions: list[Region], defaults: Defaults, *, reserve: int = 0) -> int:
    """Wie viele Standardbilder die ganze Karte fasst.

    Die Zielzahl fuer ``slideshow select`` (docs/briefing-auswahl.md, 4.1) —
    und damit die Antwort auf die Frage, wie viele Bilder man aus einem
    Sammelbecken herausholen soll. Sie steht in der Regionenkarte und muss
    nicht geraten werden.

    ``reserve`` nimmt vorweg, was keine Standbilder belegen: Titelfolien und
    der Mehrbedarf laengerer Clips. Ohne die Reserve waehlte man genau so viele
    Bilder aus, wie Slots da sind, und jede Kapitelfolie schoebe eines wieder
    heraus.

    Gegenstueck zu :func:`material_seconds`, die dieselbe Rechnung in der
    anderen Richtung fuehrt.
    """
    gesamt = sum(_region_capacity(r, defaults) for r in regions)
    return max(0, gesamt - max(0, reserve))


def coverage_advice(cov: Coverage, defaults: Defaults) -> list[str]:
    """Bei Unter- oder Ueberdeckung Optionen vorschlagen, statt stumm
    abzuschneiden.

    Die Richtung des Vorschlags ist das Entscheidende: zu *wenig* Material
    verlangt **laengere** Standzeiten, zu *viel* verlangt kuerzere. Und der
    Hebel haengt von der Regionsart ab — in free-Regionen taktet
    ``still_seconds``, ``beats_per_still`` bleibt dort ohne Wirkung.
    """
    tips: list[str] = []
    stellen = _stellschraube(cov, defaults)
    # Titelfolien zaehlen mit: sie belegen einen Slot und tragen damit zur
    # Standzeit bei, aus der die Vorschlaege gerechnet werden.
    benutzt = max(1, cov.stills + cov.clips + cov.titles)
    # ``planned_seconds`` enthaelt den gestreckten Schwanz bereits — als Basis
    # fuer einen Faktor taugt es deshalb nicht. Die *natuerliche* Laenge des
    # Materials ist das, was ohne die Streckung stehen bliebe.
    natuerlich = max(1e-6, cov.planned_seconds - cov.stretched_seconds)
    ist_standzeit = natuerlich / benutzt

    if cov.underrun:
        needed = cov.stretched_seconds
        ziel = cov.music_seconds / benutzt
        tips.append(f"Das Material deckt {needed:.1f} s der Musik nicht ab. Optionen:")
        tips.append(f"  1. {stellen(ziel / ist_standzeit)}")
        tips.append(f"  2. mehr Bilder aufnehmen (ca. "
                    f"{math.ceil(needed / max(1e-6, ist_standzeit))} zusaetzlich)")
        tips.append(f"  3. Musik um {needed:.1f} s kuerzen")
    if cov.overrun:
        ziel = cov.music_seconds / (benutzt + len(cov.unused))
        grund = (f" — {cov.titles} der belegten Slots sind Titelfolien"
                 if cov.titles else "")
        tips.append(f"{len(cov.unused)} Medien passen nicht mehr in die Musik und "
                    f"bleiben ungenutzt{grund}. Optionen:")
        tips.append(f"  1. {stellen(ziel / ist_standzeit)}")
        tips.append(f"  2. diese Medien entfernen")
        tips.append(f"  3. Musik verlaengern (`slideshow audio` mit weiterem Track)")
    return tips


def _stellschraube(cov: Coverage, defaults: Defaults):
    """Den passenden Regler benennen — je nachdem, was die Karte hergibt.

    ``faktor`` ist der Streckungsfaktor der Standzeit: > 1 laenger, < 1 kuerzer.
    """
    hat_beat = any(r["type"] == "beat" for r in cov.per_region)

    def formuliere(faktor: float) -> str:
        richtung = "erhoehen" if faktor > 1 else "reduzieren"
        if hat_beat:
            bps = defaults.beats_per_still
            return (f"beats_per_still von {bps} auf ~{max(1, round(bps * faktor))} "
                    f"{richtung} (`build --beats-per-still`)")
        s = defaults.still_seconds
        return (f"Standzeit still_seconds von {s:.1f} s auf ~{max(0.5, s * faktor):.1f} s "
                f"{richtung} (`build --still-seconds`)")

    return formuliere
