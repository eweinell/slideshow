"""Phase 5 — MLT/Kdenlive-Export (Abschnitt 9).

Zweck: visuelle Kontrolle und manuelle Nachkorrektur der Stellen, die
algorithmisch nicht passen, ohne die Pipeline zu verlassen. Die Edit-List
bleibt dabei die Single Source of Truth — der Export leitet sich aus ihr ab,
und :func:`reimport_mlt` fuehrt in Kdenlive korrigierte Zeiten zurueck.

Ken Burns laeuft in MLT ueber keyframed ``qtblend``-Transformen —
subpixelgenau, ohne das ``zoompan``-Problem aus 8.1.

Layout: die Stills/Clips liegen abwechselnd auf zwei Videospuren und
ueberlappen sich an jedem Schnitt um genau die Blendendauer ``T``. Genau diese
Ueberlappung traegt die ``luma``-Transition. Das ist dieselbe Geometrie wie im
ffmpeg-Pfad (8.2: Fenster ``[t - T/2, t + T/2]``), nur anders ausgedrueckt.

.. warning::
   Die ``rect``-Keyframe-Syntax der Transform-Effekte unterscheidet sich
   zwischen Kdenlive-Versionen. Dieser Export folgt der ``qtblend``-Schreibweise
   ``frame=x y w h opacity``. Wenn Kdenlive das Projekt oeffnet, aber die
   Bewegung nicht zeigt: ein Segment von Hand in Kdenlive bauen, speichern und
   die erzeugte ``rect``-Zeile mit :func:`kenburns_rect` vergleichen — das ist
   schneller als jede Rateschleife.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

from .build import plan_from_edit
from .errors import SlideshowError
from .kenburns import plan_motion
from .models import EditList, Manifest, StillSegment, TitleSegment, XfadeSegment
from .paths import Project
from .planner import Plan, visible_span

log = logging.getLogger("slideshow.mlt")

#: Keyframe-Dichte. MLT interpoliert linear; unsere Bewegung ist ein
#: Smoothstep, deshalb wird sie gestuetzt statt nur an den Endpunkten gesetzt.
KEYFRAME_STRIDE = 8


# --------------------------------------------------------------------------
# Ken Burns als rect-Keyframes
# --------------------------------------------------------------------------

def kenburns_rect(motion, *, total_frames: int, size: tuple[int, int],
                  stride: int = KEYFRAME_STRIDE) -> str:
    """Uebersetzt eine Ken-Burns-Bewegung in eine ``rect``-Keyframeliste.

    Im ffmpeg-Pfad wird ein Fenster *aus* der Quelle geschnitten; in MLT wird
    die Quelle *auf* die Leinwand gelegt. Das ist dieselbe Transformation von
    der anderen Seite: ein Zoom von ``z`` entspricht einer Quelle, die mit
    ``z``-facher Leinwandbreite platziert und entsprechend verschoben wird.
    """
    w, h = size
    keys: list[str] = []
    last = max(1, total_frames - 1)
    frames = list(range(0, total_frames, max(1, stride)))
    if frames[-1] != last:
        frames.append(last)

    for f in frames:
        p = f / last
        e = p if motion.ease == "linear" else p * p * (3 - 2 * p)
        z = motion.z0 + (motion.z1 - motion.z0) * e
        cx = motion.c0[0] + (motion.c1[0] - motion.c0[0]) * e
        cy = motion.c0[1] + (motion.c1[1] - motion.c0[1]) * e
        sw, sh = w * z, h * z
        x = min(0.0, max(w - sw, -(cx * sw - w / 2)))
        y = min(0.0, max(h - sh, -(cy * sh - h / 2)))
        keys.append(f"{f}={x:.2f} {y:.2f} {sw:.2f} {sh:.2f} 1")
    return ";".join(keys)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def export_mlt(project: Project, edit: EditList, manifest: Manifest | None = None) -> str:
    plan = plan_from_edit(edit, manifest)
    w, h = edit.size
    fps = edit.fps

    root = ET.Element("mlt", {
        "LC_NUMERIC": "C", "version": "7.0.0",
        "title": "slideshow", "producer": "main_bin",
    })
    ET.SubElement(root, "profile", {
        "description": f"slideshow {w}x{h} {fps:g}fps",
        "width": str(w), "height": str(h),
        "progressive": "1", "sample_aspect_num": "1", "sample_aspect_den": "1",
        "display_aspect_num": str(w), "display_aspect_den": str(h),
        "frame_rate_num": str(int(round(fps * 1000))), "frame_rate_den": "1000",
        "colorspace": "709",
    })

    counter = _Counter()
    producers: dict[str, str] = {}
    # Zwei Videospuren: A/B-Roll, damit sich die Blenden ueberlappen koennen.
    tracks: list[list] = [[], []]
    cursor = [0, 0]

    for i, slot in enumerate(plan.slots):
        vs, ve = visible_span(plan, i)
        vs = max(0, vs)
        length = max(1, ve - vs)
        lane = i % 2

        if vs > cursor[lane]:
            tracks[lane].append(("blank", vs - cursor[lane]))
        cursor[lane] = vs + length

        src = project.abs(slot.intent.src)
        pid = producers.get(str(src))
        if pid is None:
            pid = counter.next("producer")
            producers[str(src)] = pid
            _producer(root, pid, src, kind=slot.intent.kind, length=plan.total_frames + 600)

        filters: list[ET.Element] = []
        if slot.intent.kind == "still":
            motion = plan_motion(i, length / fps, edit.defaults.kb, slot.intent.kb)
            f = ET.Element("filter", {"id": counter.next("filter")})
            _prop(f, "mlt_service", "qtblend")
            _prop(f, "kdenlive_id", "qtblend")
            _prop(f, "rect", kenburns_rect(motion, total_frames=length, size=(w, h)))
            _prop(f, "compositing", "0")
            _prop(f, "distort", "0")
            filters.append(f)
            entry_in, entry_out = 0, length - 1
        else:
            file_offset = 0.0
            if manifest is not None:
                item = manifest.by_cache_path(slot.intent.src)
                if item and item.clip:
                    file_offset = item.clip.cache_offset
            start = max(0.0, slot.clip_in - file_offset - (slot.start_f - vs) / fps)
            entry_in = int(round(start * fps))
            entry_out = entry_in + length - 1
        tracks[lane].append(("entry", pid, entry_in, entry_out, filters))

    playlists = []
    for lane, items in enumerate(tracks):
        pl = ET.SubElement(root, "playlist", {"id": f"playlist{lane}"})
        for item in items:
            if item[0] == "blank":
                ET.SubElement(pl, "blank", {"length": str(item[1])})
            else:
                _, pid, a, b, filters = item
                entry = ET.SubElement(pl, "entry",
                                      {"producer": pid, "in": str(a), "out": str(b)})
                for f in filters:
                    entry.append(f)
        playlists.append(pl)

    audio_pl = None
    if edit.audio_file:
        apath = project.abs(edit.audio_file)
        aid = counter.next("producer")
        _producer(root, aid, apath, kind="audio", length=plan.total_frames + 600)
        audio_pl = ET.SubElement(root, "playlist", {"id": "playlist_audio"})
        ET.SubElement(audio_pl, "entry",
                      {"producer": aid, "in": "0", "out": str(plan.total_frames - 1)})

    tractor = ET.SubElement(root, "tractor", {
        "id": "tractor0", "title": "slideshow",
        "in": "0", "out": str(plan.total_frames - 1)})
    ET.SubElement(tractor, "track", {"producer": "background"})
    _background(root, plan.total_frames)
    for pl in playlists:
        ET.SubElement(tractor, "track", {"producer": pl.get("id")})
    if audio_pl is not None:
        ET.SubElement(tractor, "track", {"producer": "playlist_audio", "hide": "video"})

    # Spur 2 ueber Spur 1 blenden. Ohne Ueberlappung ist die Transition ein
    # harter Schnitt, mit Ueberlappung genau unsere Blende.
    tr = ET.SubElement(tractor, "transition", {"id": "transition_mix"})
    _prop(tr, "a_track", "1")
    _prop(tr, "b_track", "2")
    _prop(tr, "mlt_service", "luma")
    _prop(tr, "kdenlive_id", "luma")
    for cut, frames in enumerate(plan.transitions):
        if frames:
            log.debug("Schnitt %d: Blende ueber %d Frames", cut, frames)

    comp = ET.SubElement(tractor, "transition", {"id": "transition_comp"})
    _prop(comp, "a_track", "0")
    _prop(comp, "b_track", "1")
    _prop(comp, "mlt_service", "frei0r.cairoblend")

    return _pretty(root)


class _Counter:
    def __init__(self):
        self._n: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        i = self._n.get(prefix, 0)
        self._n[prefix] = i + 1
        return f"{prefix}{i}"


def _prop(parent: ET.Element, name: str, value: str) -> ET.Element:
    e = ET.SubElement(parent, "property", {"name": name})
    e.text = str(value)
    return e


def _producer(root: ET.Element, pid: str, path: Path, *, kind: str, length: int) -> None:
    p = ET.SubElement(root, "producer",
                      {"id": pid, "in": "0", "out": str(length)})
    _prop(p, "length", str(length))
    _prop(p, "resource", str(path))
    if kind == "still":
        _prop(p, "mlt_service", "qimage")
        _prop(p, "ttl", "1")
    elif kind == "audio":
        _prop(p, "mlt_service", "avformat")
        _prop(p, "video_index", "-1")
    else:
        _prop(p, "mlt_service", "avformat")
    _prop(p, "kdenlive:clipname", path.name)


def _background(root: ET.Element, frames: int) -> None:
    p = ET.SubElement(root, "producer", {"id": "background", "in": "0", "out": str(frames)})
    _prop(p, "length", str(frames))
    _prop(p, "mlt_service", "color")
    _prop(p, "resource", "black")
    _prop(p, "aspect_ratio", "1")


def _pretty(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="utf-8")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


# --------------------------------------------------------------------------
# Reimport
# --------------------------------------------------------------------------

def reimport_mlt(project: Project, path: Path, edit: EditList,
                 manifest: Manifest | None = None) -> list[dict]:
    """Fuehrt in Kdenlive korrigierte Zeiten in die Edit-List zurueck.

    Gelesen werden die Laengen der Eintraege auf den beiden Videospuren; daraus
    ergibt sich die sichtbare Spanne jedes Slots und damit dessen Dauer. Die
    Dauer wird als ``dur:`` am jeweiligen Segment gesetzt — explizite Sekunden
    gewinnen immer (6.3, Praezedenz 1), die Korrektur ueberlebt also den
    naechsten ``build``-Lauf nicht ungewollt, sondern bewusst.
    """
    if not path.exists():
        raise SlideshowError(f"Kdenlive-Projekt nicht gefunden: {path}")
    tree = ET.parse(path)
    root = tree.getroot()
    fps = _profile_fps(root, edit.fps)

    spans: list[tuple[int, int]] = []
    for lane in (0, 1):
        pl = root.find(f".//playlist[@id='playlist{lane}']")
        if pl is None:
            continue
        cursor = 0
        for child in pl:
            if child.tag == "blank":
                cursor += int(child.get("length", "0"))
            elif child.tag == "entry":
                a, b = int(child.get("in", "0")), int(child.get("out", "0"))
                length = b - a + 1
                spans.append((cursor, cursor + length))
                cursor += length
    spans.sort()
    if not spans:
        raise SlideshowError(f"{path} enthaelt keine verwertbaren Spuren.")

    media_segments = [(i, s) for i, s in enumerate(edit.segments)
                      if not isinstance(s, XfadeSegment)]
    if len(spans) != len(media_segments):
        log.warning("Kdenlive liefert %d Clips, die Edit-List hat %d Medien-Segmente "
                    "— es werden nur die ersten %d uebernommen.",
                    len(spans), len(media_segments), min(len(spans), len(media_segments)))

    plan = plan_from_edit(edit, manifest)
    changes: list[dict] = []
    for (idx, seg), (vs, ve), slot_i in zip(media_segments, spans, range(len(plan.slots))):
        # Sichtbare Spanne -> exklusive Slot-Dauer zurueckrechnen.
        t_in = plan.transitions[slot_i] if slot_i < len(plan.transitions) else 0
        t_out = plan.transitions[slot_i + 1] if slot_i + 1 < len(plan.transitions) else 0
        slot_frames = (ve - vs) - (t_in // 2) - (t_out // 2)
        new_dur = round(slot_frames / fps, 6)
        old = plan.slots[slot_i].frames / fps if slot_i < len(plan.slots) else None
        if old is not None and abs(new_dur - old) < 1.0 / fps:
            continue
        if isinstance(seg, (StillSegment, TitleSegment)):
            # Eine Titelfolie ist beim Reimport ein Standbild wie jedes andere:
            # sie traegt dieselben Dauerfelder, nur keinen `src`.
            seg.dur = new_dur
            seg.beats = None
        else:
            seg.out = round(seg.in_ + new_dur, 6)
        changes.append({"segment": idx, "src": getattr(seg, "src", ""),
                        "alt": round(old or 0.0, 6), "neu": new_dur})
    return changes


def _profile_fps(root: ET.Element, fallback: float) -> float:
    prof = root.find("profile")
    if prof is None:
        return fallback
    try:
        num = float(prof.get("frame_rate_num", "0"))
        den = float(prof.get("frame_rate_den", "1"))
        return num / den if num and den else fallback
    except (TypeError, ValueError):
        return fallback
