"""Phase 3c — Edit-List erzeugen und aufloesen (Abschnitt 6.6).

Die Edit-List haelt **Absicht** (``beats:``, ``dur:``, ``snap:``), nicht
aufgeloeste Zeitstempel. Die absoluten Framegrenzen entstehen bei jedem Lauf
neu aus derselben deterministischen Funktion (:func:`plan_from_edit`). Das ist
Absicht:

* Prinzip 1 bleibt gewahrt — die Datei ist von Hand editierbar, und eine
  geaenderte ``beats:``-Zahl wirkt sich auch tatsaechlich aus. Waeren die
  Framegrenzen mitgespeichert, gaebe es zwei Wahrheiten, die auseinanderlaufen.
* Prinzip 3 bleibt gewahrt — *gerechnet* wird ausschliesslich in absoluten
  Frames auf der Master-Timeline (siehe :mod:`slideshow.planner`).

Wer die aufgeloeste Timeline sehen will, bekommt sie als Report bzw. in
``out/timeline.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import EDIT_VERSION
from .errors import SchemaError, SlideshowError
from .kenburns import plan_motion
from .models import (BeatMap, ClipSegment, Defaults, EditList, KBSpec, Manifest,
                     Region, StillSegment, XfadeSegment)
from .paths import Project
from .planner import (Coverage, Intent, Plan, RenderSegment, apply_transitions,
                      coverage, plan_slots, resolve, to_frame, to_time,
                      validate_continuity, visible_span)
from .probe import chronological

log = logging.getLogger("slideshow.build")


# --------------------------------------------------------------------------
# Erzeugen
# --------------------------------------------------------------------------

def build_edit_list(project: Project, manifest: Manifest, beatmap: BeatMap, *,
                    defaults: Defaults | None = None, fps: float | None = None,
                    size: tuple[int, int] = (3840, 2160),
                    order: list[str] | None = None) -> tuple[EditList, Plan, Coverage]:
    """Erzeugt ``edit.yaml`` aus Manifest und Regionenkarte."""
    defaults = defaults or Defaults()
    fps = float(fps or manifest.fps_suggestion)
    regions = beatmap.regions
    if not regions:
        raise SlideshowError("Regionenkarte ist leer — `slideshow beats` zuerst laufen lassen.")

    duration = float(beatmap.audio.get("duration") or manifest.audio.duration or 0.0)
    if duration <= 0:
        duration = regions[-1].end
    total_frames = to_frame(duration, fps)

    media = chronological(manifest)
    if order:
        by_id = {m.id: m for m in manifest.media}
        media = [by_id[i] for i in order if i in by_id]

    intents: list[Intent] = []
    for m in media:
        if not m.cache_path:
            log.warning("%s hat kein Zwischenprodukt — `slideshow preprocess` fehlt?", m.id)
            continue
        if m.kind == "image":
            intents.append(Intent(kind="still", src=m.cache_path, index=len(intents)))
        else:
            info = m.clip
            available = (info.cache_duration or info.effective_duration) if info else 0.0
            intents.append(Intent(kind="clip", src=m.cache_path, index=len(intents),
                                  clip_in=(info.cache_offset if info else 0.0),
                                  clip_available=(info.cache_offset if info else 0.0) + available,
                                  snap="out"))
    if not intents:
        raise SlideshowError("Keine vorverarbeiteten Medien gefunden. "
                             "`slideshow preprocess` zuerst laufen lassen.")

    plan = plan_slots(regions, intents, defaults, fps=fps, total_frames=total_frames)
    explicit = None if defaults.xfade.auto else {}
    apply_transitions(plan, defaults, explicit=explicit)
    clamp_transitions_for_handles(plan, manifest)
    cov = coverage(plan, defaults)

    edit = EditList(
        version=EDIT_VERSION, fps=fps, size=tuple(size),
        audio={"file": manifest.audio.file or beatmap.audio.get("file", ""),
               "duration": round(duration, 6),
               "regions": [_region_dict(r) for r in regions]},
        defaults=defaults,
        segments=_segments_from_plan(plan, defaults))
    return (edit, plan, cov)


def _region_dict(r: Region) -> dict:
    d = {"type": r.type, "start": round(r.start, 6), "end": round(r.end, 6)}
    if r.type == "beat":
        d.update(bpm=r.bpm, offset=round(r.offset or 0.0, 6))
        if r.conf is not None:
            d["conf"] = round(r.conf, 4)
    elif r.reason:
        d["reason"] = r.reason
    return d


def _segments_from_plan(plan: Plan, defaults: Defaults) -> list:
    """Baut die Segmentliste inklusive der Uebergaenge als *eigene* Segmente.

    ``from``/``to`` sind Indizes in genau diese Liste. Der Uebergang steht
    zwischen seinen beiden Nachbarn, sodass die Datei auch beim Lesen von oben
    nach unten Sinn ergibt.
    """
    segments: list = []
    slot_to_index: list[int] = []

    for i, slot in enumerate(plan.slots):
        if i > 0 and plan.transitions[i] > 0:
            segments.append(XfadeSegment(**{
                "from": slot_to_index[i - 1], "to": len(segments) + 1,
                "dur": round(plan.transitions[i] / plan.fps, 6),
                "mode": plan.transition_modes.get(i, defaults.xfade.mode)}))
        slot_to_index.append(len(segments))
        segments.append(_segment_from_slot(plan, i, slot, defaults))
    return segments


def _segment_from_slot(plan: Plan, i: int, slot, defaults: Defaults):
    """Schreibt die **Absicht** eines Slots, nicht seine gemessene Dauer.

    Das ist der Unterschied zwischen einer Edit-List, die sich reproduzierbar
    laden laesst, und einer, die bei jedem Lauf wandert. Die tatsaechliche
    Dauer eines Slots weicht regelmaessig von der Absicht ab, weil der Planer
    Regeln anwendet: das erste Bild schluckt den Vorlauf (6.0), das letzte
    Bild einer Region wird an deren Ende geklemmt, das letzte Bild ueberhaupt
    wird auf die Timelinelaenge gestreckt. Schriebe man diese Dauern als
    ``beats:`` zurueck, wuerden die Regeln beim naechsten Laden ein *zweites*
    Mal angewandt — und die Timeline verschoebe sich bei jedem Roundtrip.
    """
    region = plan.regions[slot.region_index]
    intent = slot.intent
    if intent.kind == "clip":
        return ClipSegment(**{
            "src": intent.src, "in": round(slot.clip_in, 6),
            "out": round(slot.clip_out, 6), "snap": intent.snap})

    seg = StillSegment(src=intent.src)
    if intent.dur is not None:
        seg.dur = round(intent.dur, 6)
    elif region.type == "beat" and region.bpm:
        # Explizit ausschreiben: so ist beim Lesen sofort klar, warum ein Bild
        # so lang steht, und die Zahl laesst sich direkt anfassen.
        beats = intent.beats if intent.beats is not None else \
            (region.beats_per_still or defaults.beats_per_still)
        seg.beats = beats
    if intent.snap_back is not None:
        seg.snap_back = intent.snap_back
    if slot.hold:
        seg.hold = True
    return seg


# --------------------------------------------------------------------------
# Aufloesen (von der Edit-List zurueck zur Timeline)
# --------------------------------------------------------------------------

def plan_from_edit(edit: EditList, manifest: Manifest | None = None) -> Plan:
    """Rekonstruiert die Timeline aus ``edit.yaml``.

    ``build`` und ``render`` gehen durch dieselbe Funktion; nur so liefern
    beide garantiert dieselben Framegrenzen.
    """
    regions = edit.regions
    if not regions:
        raise SchemaError("keine Regionen definiert", path="audio.regions")

    duration = float(edit.audio.get("duration") or regions[-1].end)
    total_frames = to_frame(duration, edit.fps)

    intents: list[Intent] = []
    slot_of_segment: dict[int, int] = {}
    explicit: dict[int, float] = {}
    modes: dict[int, str] = {}
    pending: list[tuple[int, XfadeSegment]] = []

    for idx, seg in enumerate(edit.segments):
        if isinstance(seg, XfadeSegment):
            pending.append((idx, seg))
            continue
        slot_of_segment[idx] = len(intents)
        if isinstance(seg, StillSegment):
            intents.append(Intent(
                kind="still", src=seg.src, index=idx, beats=seg.beats, dur=seg.dur,
                hold=seg.hold, snap_back=seg.snap_back, kb=seg.kb,
                portrait=seg.portrait))
        else:
            available, offset = _clip_bounds(seg, manifest)
            intents.append(Intent(
                kind="clip", src=seg.src, index=idx, snap=seg.snap,
                snap_back=seg.snap_back, clip_in=seg.in_, clip_out=seg.out,
                clip_available=available if available else (seg.out or 0.0),
                dur=(seg.out - seg.in_) if seg.out is not None else None))
            if offset is not None and seg.in_ < offset - 1e-4:
                raise SchemaError(
                    f"in={seg.in_:.3f} s liegt vor dem Anfang des Intermediates "
                    f"({offset:.3f} s). Clip neu vorverarbeiten oder in erhoehen.",
                    path=f"segments[{idx}].in")

    plan = plan_slots(regions, intents, edit.defaults, fps=edit.fps,
                      total_frames=total_frames)

    for idx, seg in pending:
        a, b = seg.from_, seg.to
        for name, ref in (("from", a), ("to", b)):
            if ref not in slot_of_segment:
                raise SchemaError(
                    f"verweist auf Segment {ref}, das kein still/clip ist "
                    f"(oder nicht existiert)", path=f"segments[{idx}].{name}")
        sa, sb = slot_of_segment[a], slot_of_segment[b]
        if sb != sa + 1:
            raise SchemaError(
                f"Uebergang verbindet die Segmente {a} und {b}, die auf der "
                f"Timeline nicht benachbart sind", path=f"segments[{idx}]")
        if sb >= len(plan.slots):
            continue                     # Nachbar fiel aus der Timeline (Ueberdeckung)
        seconds = seg.dur
        if seconds is None:
            beats = seg.beats if seg.beats is not None else edit.defaults.xfade.beats
            region = plan.regions[plan.slots[sb].region_index]
            if region.type == "beat" and region.bpm:
                seconds = beats * region.beat_duration()
            else:
                seconds = beats * edit.defaults.still_seconds / \
                    max(1, edit.defaults.beats_per_still)
        explicit[sb] = float(seconds)
        modes[sb] = seg.mode

    plan.transition_modes = modes
    apply_transitions(plan, edit.defaults, explicit=explicit)
    if manifest is not None:
        clamp_transitions_for_handles(plan, manifest)
    return plan


def _clip_bounds(seg: ClipSegment, manifest: Manifest | None) -> tuple[float, float | None]:
    if manifest is None:
        return (0.0, None)
    item = manifest.by_cache_path(seg.src)
    if item is None or item.clip is None:
        return (0.0, None)
    info = item.clip
    return (info.cache_offset + (info.cache_duration or info.effective_duration),
            info.cache_offset)


# --------------------------------------------------------------------------
# Semantische Validierung (Abschnitt 11)
# --------------------------------------------------------------------------

def clamp_transitions_for_handles(plan: Plan, manifest: Manifest) -> None:
    """``build`` muss validieren, dass ``T/2`` <= Handle ist (8.2).

    Fuer Clip-Nachbarn liefert das Intermediate die Frames ausserhalb des
    exklusiven Anteils — aber nur, soweit die 1-s-Handles aus 5.2 reichen.
    Statt hart abzubrechen wird die Blende auf das Moegliche gekuerzt und
    gewarnt: eine kuerzere Blende ist immer noch ein brauchbarer Film.
    """
    fps = plan.fps
    for cut in range(1, len(plan.slots)):
        t = plan.transitions[cut]
        if t <= 0:
            continue
        half = t / 2 / fps
        limit = half
        for slot, side in ((plan.slots[cut - 1], "after"), (plan.slots[cut], "before")):
            if slot.intent.kind != "clip":
                continue
            item = manifest.by_cache_path(slot.intent.src)
            info = item.clip if item else None
            if info is None:
                continue
            start = info.cache_offset
            end = info.cache_offset + (info.cache_duration or info.effective_duration)
            handle = (end - slot.clip_out) if side == "after" else (slot.clip_in - start)
            limit = min(limit, max(0.0, handle))
        if limit < half - 1e-6:
            # Abrunden, damit die Blende sicher in die Handles passt, und auf
            # eine gerade Framezahl, damit T/2 exakt aufgeht.
            plan.transitions[cut] = max(0, min(t, int(limit * fps) * 2))
            plan.warnings.append(
                f"Blende an Schnitt {cut} von {t / fps:.3f} s auf "
                f"{plan.transitions[cut] / fps:.3f} s gekuerzt — die Handles des "
                f"angrenzenden Clips reichen nicht weiter (5.2: 1 s).")


def validate_edit(edit: EditList, manifest: Manifest | None = None) -> Plan:
    """Vollstaendige semantische Pruefung *vor* dem ersten Renderaufruf.

    Deckt Abnahmekriterium 14 ab: eine fehlerhafte ``edit.yaml`` fuehrt zu
    einer Fehlermeldung mit YAML-Pfad, bevor ffmpeg auch nur gestartet wird.
    """
    from .beats import validate_tiling

    regions = edit.regions
    duration = float(edit.audio.get("duration") or (regions[-1].end if regions else 0.0))
    validate_tiling(regions, duration)

    # `beats:` nur in Beat-Regionen — geprueft beim Planen, weil erst dort
    # feststeht, in welcher Region ein Segment landet.
    plan = plan_from_edit(edit, manifest)
    segments = resolve(plan)
    validate_continuity(segments, plan.total_frames)

    if manifest is not None:
        _validate_sources(edit, manifest)
    return plan


def _validate_sources(edit: EditList, manifest: Manifest) -> None:
    """Referenzierte Cache-Dateien muessen vorhanden sein."""
    known = {m.cache_path for m in manifest.media if m.cache_path}
    for idx, seg in enumerate(edit.segments):
        src = getattr(seg, "src", None)
        if src and src not in known:
            raise SchemaError(
                f"{src!r} steht nicht im Manifest. `slideshow preprocess` erneut "
                f"laufen lassen oder den Pfad korrigieren.",
                path=f"segments[{idx}].src")


def check_sources_exist(project: Project, edit: EditList) -> None:
    missing: list[tuple[int, str]] = []
    for idx, seg in enumerate(edit.segments):
        src = getattr(seg, "src", None)
        if src and not project.abs(src).exists():
            missing.append((idx, src))
    if missing:
        idx, src = missing[0]
        raise SchemaError(
            f"Datei fehlt: {src}" + (f" (+{len(missing) - 1} weitere)"
                                     if len(missing) > 1 else ""),
            path=f"segments[{idx}].src")


# --------------------------------------------------------------------------
# Timeline-Export fuer Inspektion
# --------------------------------------------------------------------------

def timeline_json(plan: Plan, segments: list[RenderSegment]) -> dict:
    return {
        "fps": plan.fps,
        "total_frames": plan.total_frames,
        "duration": round(to_time(plan.total_frames, plan.fps), 6),
        "slots": [
            {"index": i, "src": s.intent.src, "kind": s.intent.kind,
             "region": s.region_index, "start_f": s.start_f, "end_f": s.end_f,
             "start": round(to_time(s.start_f, plan.fps), 6),
             "end": round(to_time(s.end_f, plan.fps), 6),
             "visible": list(visible_span(plan, i)),
             "hold": s.hold}
            for i, s in enumerate(plan.slots)],
        "transitions": plan.transitions,
        "segments": [
            {"index": s.index, "kind": s.kind, "start_f": s.start_f, "end_f": s.end_f,
             "frames": s.frames,
             "src": (s.slot.intent.src if s.slot else
                     f"{s.a.intent.src} -> {s.b.intent.src}" if s.a and s.b else "")}
            for s in segments],
    }


def write_timeline(project: Project, plan: Plan, segments: list[RenderSegment]) -> Path:
    project.out.mkdir(parents=True, exist_ok=True)
    p = project.out / "timeline.json"
    p.write_text(json.dumps(timeline_json(plan, segments), indent=1), encoding="utf-8")
    return p
