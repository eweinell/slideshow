"""Phase 4 — Rendering (Abschnitt 8).

Segmente sind unabhaengig (Prinzip 2): jedes wird einzeln encodiert, per
Content-Hash gecacht und nur bei Aenderung neu gerendert. Ein korrigiertes Bild
an Position 47 loest deshalb genau drei Neurenderungen aus — das Still-Segment
selbst und die zwei angrenzenden ``xfade``-Segmente, weil deren Hash die
Nachbarn einschliesst (8.2).
"""

from __future__ import annotations

import concurrent.futures as _fut
import logging
import os
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cache import HashIndex, cache_key
from .doctor import Capabilities
from .encoders import EncoderProfile, master_profile, preview_profile
from .errors import SchemaError, SlideshowError
from .kenburns import (KBMotion, clip_input_args, frames_arg, kb_filter, plan_motion,
                       still_input_args, xfade_expr)
from .logging_setup import console
from .models import EditList, Manifest
from .paths import Project
from .planner import Plan, RenderSegment, Slot, resolve, to_time, validate_continuity
from .proc import DryRun, ffprobe_json, run

log = logging.getLogger("slideshow.render")

#: Version der Render-Logik — Teil des Parameter-Hashes, damit eine Aenderung
#: hier alle Segmente ungueltig macht.
RENDER_VERSION = 4


@dataclass
class SegmentJob:
    index: int
    kind: str
    key: str
    out: Path
    cmd: list[str]
    frames: int
    label: str
    cached: bool = False


@dataclass
class RenderStats:
    total: int = 0
    from_cache: int = 0
    rendered: int = 0
    seconds: float = 0.0
    out_bytes: int = 0
    timeline_seconds: float = 0.0
    music_seconds: float = 0.0
    failures: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Kommandos je Segmenttyp
# --------------------------------------------------------------------------

def _motion_for(plan: Plan, slot_index: int, slot: Slot, edit: EditList) -> KBMotion:
    """Die Bewegung eines Stills — ueber seine **volle sichtbare Dauer**."""
    vs, ve = (slot.start_f - plan.transitions[slot_index] // 2,
              slot.end_f + plan.transitions[slot_index + 1] // 2)
    duration = (ve - vs) / plan.fps
    return plan_motion(slot_index, duration, edit.defaults.kb, slot.intent.kb)


def _still_stream(project: Project, plan: Plan, edit: EditList, slot_index: int,
                  slot: Slot, *, seg_start: int, seg_frames: int,
                  profile: EncoderProfile) -> tuple[list[str], str, dict]:
    """Eingabe-Argumente und Filterkette fuer einen Standbild-Strom."""
    vs = slot.start_f - plan.transitions[slot_index] // 2
    ve = slot.end_f + plan.transitions[slot_index + 1] // 2
    total = max(1, ve - vs)
    offset = seg_start - vs
    motion = _motion_for(plan, slot_index, slot, edit)
    src = project.abs(slot.intent.src)
    args = still_input_args(str(src), fps=plan.fps, frames=seg_frames)
    vf = kb_filter(motion, total_frames=total, offset=offset,
                   size=profile.size, fps=plan.fps)
    meta = {"motion": motion.fingerprint(), "total": total, "offset": offset}
    return (args, vf, meta)


def _clip_stream(project: Project, plan: Plan, slot: Slot, *, seg_start: int,
                 seg_frames: int, profile: EncoderProfile,
                 manifest: Manifest | None) -> tuple[list[str], str, dict]:
    """Aus dem Intermediate nur noch schneiden (8.3).

    Das Intermediate liegt bereits in Zielgroesse, Zielrate und Ziel-Zeitbasis
    vor; hier bleibt Formatangleichung und Trim.
    """
    src = project.abs(slot.intent.src)
    file_offset = 0.0
    if manifest is not None:
        item = manifest.by_cache_path(slot.intent.src)
        if item and item.clip:
            file_offset = item.clip.cache_offset
    # Zeitpunkt im Intermediate, an dem dieses Segment beginnt.
    start = slot.clip_in + (seg_start - slot.start_f) / plan.fps - file_offset
    start = max(0.0, start)
    args = clip_input_args(str(src), start=start, frames=seg_frames, fps=plan.fps)
    w, h = profile.size
    vf = (f"scale={w}:{h}:flags=lanczos:force_original_aspect_ratio=increase,"
          f"crop={w}:{h},setsar=1")
    return (args, vf, {"start": round(start, 6)})


def fade_frames(plan: Plan, edit: EditList, segment_frames: int) -> int:
    """Laenge der Ausblende in Frames, begrenzt auf das letzte Segment.

    Die Blende sitzt bewusst *im Segment*, nicht im Mux: der Mux haengt die
    Segmente mit ``-c:v copy`` aneinander, ein Filter dort wuerde den ganzen
    Master neu encodieren und die verlustfreie Concat-Kette aufgeben (8.3).
    Das letzte Segment wird ohnehin encodiert, dort kostet die Blende nichts.

    Der Preis dieser Entscheidung: die Blende kann nicht laenger sein als das
    letzte Segment. Bei den ueblichen Standzeiten von mehreren Sekunden faellt
    das nicht ins Gewicht; ist das Segment kuerzer, wird die Blende gekuerzt
    statt ueber die Segmentgrenze hinweg gestueckelt.
    """
    wunsch = float(getattr(edit.defaults, "fade_out", 0.0) or 0.0)
    if wunsch <= 0 or segment_frames <= 0:
        return 0
    return max(0, min(int(round(wunsch * plan.fps)), segment_frames))


def _fade_suffix(plan: Plan, edit: EditList, seg: RenderSegment) -> tuple[str, dict]:
    """Filterzusatz fuer das *letzte* Segment der Timeline — sonst leer."""
    if seg.end_f != plan.total_frames:
        return ("", {})
    n = fade_frames(plan, edit, seg.frames)
    if n <= 0:
        return ("", {})
    st = (seg.frames - n) / plan.fps
    return (f",fade=t=out:st={st:.6f}:d={n / plan.fps:.6f}", {"fade": n})


def _segment_command(project: Project, plan: Plan, edit: EditList, seg: RenderSegment, *,
                     profile: EncoderProfile, out: Path,
                     manifest: Manifest | None) -> tuple[list[str], dict, list[str]]:
    """Baut das ffmpeg-Kommando und den Parametersatz fuer den Cache-Key."""
    fmt = f"format={profile.pix_fmt}"
    slots = plan.slots
    fade, fade_param = _fade_suffix(plan, edit, seg)

    if seg.kind in ("still", "clip"):
        i = slots.index(seg.slot)                     # type: ignore[arg-type]
        if seg.kind == "still":
            args, vf, meta = _still_stream(project, plan, edit, i, seg.slot,
                                           seg_start=seg.start_f, seg_frames=seg.frames,
                                           profile=profile)
        else:
            args, vf, meta = _clip_stream(project, plan, seg.slot, seg_start=seg.start_f,
                                          seg_frames=seg.frames, profile=profile,
                                          manifest=manifest)
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", *args,
               "-vf", f"{vf},{fmt}{fade}", *frames_arg(seg.frames),
               "-an", *profile.video_args(), str(out)]
        sources = [seg.slot.intent.src]                # type: ignore[union-attr]
        params = {"kind": seg.kind, "v": RENDER_VERSION, "frames": seg.frames,
                  "vf": vf, **fade_param, **meta}
        return (cmd, params, sources)

    # --- Uebergangs-Segment (8.2) -------------------------------------
    ia = slots.index(seg.a)                            # type: ignore[arg-type]
    ib = slots.index(seg.b)                            # type: ignore[arg-type]
    parts_in: list[str] = []
    metas: list[dict] = []
    filters: list[str] = []

    for n, (idx, slot) in enumerate(((ia, seg.a), (ib, seg.b))):
        if slot.intent.kind == "still":                # type: ignore[union-attr]
            args, vf, meta = _still_stream(project, plan, edit, idx, slot,
                                           seg_start=seg.start_f, seg_frames=seg.frames,
                                           profile=profile)
        else:
            args, vf, meta = _clip_stream(project, plan, slot, seg_start=seg.start_f,
                                          seg_frames=seg.frames, profile=profile,
                                          manifest=manifest)
        parts_in += args
        metas.append(meta)
        filters.append(f"[{n}:v]{vf},{fmt},setpts=PTS-STARTPTS[s{n}]")

    xf = xfade_expr(seg.mode, seg.frames, plan.fps)
    graph = ";".join([*filters, f"[s0][s1]{xf},{fmt}{fade}[v]"])
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", *parts_in,
           "-filter_complex", graph, "-map", "[v]", *frames_arg(seg.frames),
           "-an", *profile.video_args(), str(out)]
    sources = [seg.a.intent.src, seg.b.intent.src]     # type: ignore[union-attr]
    params = {"kind": "xfade", "v": RENDER_VERSION, "frames": seg.frames,
              "mode": seg.mode, "a": metas[0], "b": metas[1], **fade_param}
    return (cmd, params, sources)


# --------------------------------------------------------------------------
# Planung der Jobs
# --------------------------------------------------------------------------

def plan_jobs(project: Project, plan: Plan, edit: EditList, segments: list[RenderSegment],
              *, profile: EncoderProfile, caps: Capabilities,
              manifest: Manifest | None, index: HashIndex) -> list[SegmentJob]:
    project.segments.mkdir(parents=True, exist_ok=True)
    jobs: list[SegmentJob] = []
    for seg in segments:
        out_tmp = project.segments / "pending"
        cmd, params, sources = _segment_command(project, plan, edit, seg,
                                                profile=profile, out=out_tmp,
                                                manifest=manifest)
        try:
            hashes = [index.file_hash(project.abs(s)) for s in sources]
        except FileNotFoundError as exc:
            raise SchemaError(str(exc), path=f"segments[{seg.index}].src") from exc

        # Der Parameter-Hash schliesst den vollstaendigen Filtergraph, alle
        # Encoder-Parameter und die ffmpeg-Major-Version ein (Abschnitt 11).
        key = cache_key(hashes, {**params, "enc": profile.fingerprint(),
                                 "ffmpeg": caps.ffmpeg_major})
        out = project.segments / f"seg_{key}.{profile.container}"
        cmd = [str(c) if c != str(out_tmp) else str(out) for c in cmd]
        label = _label(seg)
        jobs.append(SegmentJob(index=seg.index, kind=seg.kind, key=key, out=out,
                               cmd=cmd, frames=seg.frames, label=label,
                               cached=out.exists() and out.stat().st_size > 0))
    return jobs


def _label(seg: RenderSegment) -> str:
    if seg.kind == "xfade" and seg.a and seg.b:
        return f"xfade {Path(seg.a.intent.src).stem} -> {Path(seg.b.intent.src).stem}"
    if seg.slot:
        return f"{seg.kind} {Path(seg.slot.intent.src).stem}"
    return seg.kind


# --------------------------------------------------------------------------
# Ausfuehrung
# --------------------------------------------------------------------------

def _run_job(job: SegmentJob) -> tuple[int, str | None, float]:
    t0 = time.time()
    try:
        run(job.cmd, timeout=3600)
        return (job.index, None, time.time() - t0)
    except SlideshowError as exc:
        return (job.index, str(exc), time.time() - t0)


def render_segments(jobs: list[SegmentJob], *, workers: int,
                    dry: DryRun | None = None) -> RenderStats:
    """Rendert alle nicht gecachten Segmente parallel.

    ``zoompan`` laeuft auf der CPU; GPU-Beschleunigung gibt es dafuer nicht.
    Der Weg ist Parallelisierung: N unabhaengige ffmpeg-Prozesse, N begrenzt
    durch min(CPU-Kerne, NVENC-Session-Limit aus Phase 0).
    """
    stats = RenderStats(total=len(jobs))
    todo = [j for j in jobs if not j.cached]
    stats.from_cache = len(jobs) - len(todo)

    if dry and dry.enabled:
        for j in todo:
            dry.record(j.cmd)
        return stats

    if not todo:
        log.info("Alle %d Segmente aus dem Cache.", len(jobs))
        return stats

    log.info("%d Segmente neu rendern, %d aus dem Cache (%d Worker)",
             len(todo), stats.from_cache, workers)
    t0 = time.time()
    done = 0
    con = console()
    with _fut.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_job, j): j for j in todo}
        for fut in _fut.as_completed(futures):
            job = futures[fut]
            idx, err, secs = fut.result()
            done += 1
            if err:
                stats.failures.append(f"Segment {idx} ({job.label}): {err}")
                log.error("Segment %d (%s) fehlgeschlagen:\n%s", idx, job.label, err)
                continue
            stats.rendered += 1
            elapsed = time.time() - t0
            eta = elapsed / done * (len(todo) - done)
            con.print(f"  [{done:>4}/{len(todo)}] {job.label:<44} "
                      f"{job.frames:>5} Frames  {secs:5.1f}s  ETA {_hms(eta)}")
    stats.seconds = time.time() - t0
    return stats


def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


# --------------------------------------------------------------------------
# Concat und Muxing (8.4)
# --------------------------------------------------------------------------

_UNIFORM_KEYS = ("codec_name", "width", "height", "pix_fmt", "sample_aspect_ratio",
                 "r_frame_rate")


def verify_uniform(jobs: list[SegmentJob]) -> None:
    """Vor dem Concat verifizieren, dass alle Segmente uebereinstimmen.

    Der Concat-Demuxer mit ``-c copy`` ist verlustfrei und schnell — aber nur,
    wenn 8.3 eingehalten wurde. Sonst entsteht ein kaputter Master, und zwar
    ohne Fehlermeldung. Deshalb wird hier lieber praezise abgebrochen.
    """
    reference: dict | None = None
    ref_job: SegmentJob | None = None
    for job in jobs:
        if not job.out.exists():
            raise SlideshowError(f"Segment {job.index} fehlt: {job.out}")
        data = ffprobe_json(job.out)
        stream = next((s for s in data.get("streams", [])
                       if s.get("codec_type") == "video"), None)
        if stream is None:
            raise SlideshowError(f"Segment {job.index} enthaelt keinen Videostream: "
                                 f"{job.out}")
        props = {k: stream.get(k) for k in _UNIFORM_KEYS}
        if reference is None:
            reference, ref_job = props, job
            continue
        diff = {k: (reference[k], props[k]) for k in _UNIFORM_KEYS
                if reference[k] != props[k]}
        if diff:
            lines = [f"Segment {job.index} ({job.label}) passt nicht zu Segment "
                     f"{ref_job.index} ({ref_job.label}):"]
            lines += [f"  {k}: {a!r} vs {b!r}" for k, (a, b) in diff.items()]
            lines.append("Der Concat-Demuxer verlangt identische Parameter (8.3).")
            raise SlideshowError("\n".join(lines))


def write_concat_list(project: Project, jobs: list[SegmentJob]) -> Path:
    path = project.cache / "segments.txt"
    lines = ["ffconcat version 1.0"]
    for job in jobs:
        # Relativ zum Listenverzeichnis: haelt das Projekt zwischen WSL und
        # Windows portabel.
        rel = os.path.relpath(job.out, path.parent).replace("\\", "/")
        lines.append(f"file '{rel}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def concat_and_mux(project: Project, jobs: list[SegmentJob], *, audio: Path | None,
                   out: Path, profile: EncoderProfile, timeline_seconds: float,
                   audio_start: float = 0.0, fade_seconds: float = 0.0,
                   dry: DryRun | None = None) -> None:
    """Segmente verlustfrei aneinanderhaengen und mit der Tonspur muxen."""
    listfile = write_concat_list(project, jobs)
    video = project.cache / f"video_concat.{profile.container}"

    concat_cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
                  "-f", "concat", "-safe", "0", "-i", str(listfile),
                  "-c", "copy", "-fflags", "+genpts", str(video)]

    mux_cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(video)]
    if audio and audio.exists():
        if audio_start > 0:
            mux_cmd += ["-ss", f"{audio_start:.6f}"]
        mux_cmd += ["-i", str(audio)]
    mux_cmd += ["-c:v", "copy"]
    if profile.codec in ("hevc_nvenc", "libx265"):
        # Ohne dieses Tag spielt HEVC-in-MP4 auf Apple-Geraeten nicht.
        mux_cmd += ["-tag:v", "hvc1"]
    if audio and audio.exists():
        # Kein -shortest: die Ziellaenge steht in der Edit-List. Audio wird
        # exakt darauf getrimmt bzw. mit apad aufgefuellt.
        af = "apad"
        if fade_seconds > 0:
            # Erst auffuellen, dann ausblenden — in dieser Reihenfolge blendet
            # auch eine kuerzere Tonspur sauber aus, statt vorher zu enden und
            # die Blende ins Leere laufen zu lassen.
            af += (f",afade=t=out:st={max(0.0, timeline_seconds - fade_seconds):.6f}"
                   f":d={fade_seconds:.6f}")
        mux_cmd += ["-c:a", "aac", "-b:a", "320k", "-af", af,
                    "-map", "0:v:0", "-map", "1:a:0"]
    else:
        mux_cmd += ["-map", "0:v:0"]
    mux_cmd += ["-t", f"{timeline_seconds:.6f}", "-movflags", "+faststart", str(out)]

    if dry and dry.enabled:
        dry.record(concat_cmd)
        dry.record(mux_cmd)
        return

    out.parent.mkdir(parents=True, exist_ok=True)
    run(concat_cmd, timeout=3600)
    run(mux_cmd, timeout=3600)
    video.unlink(missing_ok=True)


def verify_master(path: Path, *, expected_seconds: float, fps: float) -> dict:
    """Abweichung Video<->Soll > 1 Frame ist ein Fehler, kein Rundungsproblem (8.4)."""
    data = ffprobe_json(path)
    stream = next((s for s in data.get("streams", [])
                   if s.get("codec_type") == "video"), {})
    fmt = data.get("format", {})
    actual = float(fmt.get("duration") or 0.0)
    delta_frames = abs(actual - expected_seconds) * fps
    info = {"duration": actual, "expected": expected_seconds,
            "delta_frames": round(delta_frames, 3),
            "codec": stream.get("codec_name"), "pix_fmt": stream.get("pix_fmt"),
            "width": stream.get("width"), "height": stream.get("height"),
            "size_bytes": int(fmt.get("size") or 0)}
    if delta_frames > 1.0:
        raise SlideshowError(
            f"Der Master ist {actual:.3f} s lang, die Timeline verlangt "
            f"{expected_seconds:.3f} s — das sind {delta_frames:.1f} Frames "
            f"Abweichung. Mehr als ein Frame ist ein Fehler, kein Rundungsproblem.")
    return info


# --------------------------------------------------------------------------
# Orchestrierung
# --------------------------------------------------------------------------

def choose_profile(caps: Capabilities, edit: EditList, *, preview: bool,
                   codec: str = "auto") -> EncoderProfile:
    if preview:
        return preview_profile(edit.fps)
    w, h = edit.size
    return master_profile(caps.encoder_choice(), width=w, height=h, fps=edit.fps,
                          codec=codec)


def parse_range(spec: str | None, count: int) -> tuple[int, int]:
    """``--range 40:60`` -> Segmentbereich [40, 60)."""
    if not spec:
        return (0, count)
    try:
        a, _, b = spec.partition(":")
        start = int(a) if a else 0
        end = int(b) if b else count
    except ValueError:
        raise SlideshowError(f"Unlesbarer Bereich {spec!r}. Erwartet: --range 40:60") from None
    start = max(0, min(start, count))
    end = max(start, min(end, count))
    if start == end:
        raise SlideshowError(f"Der Bereich {spec!r} enthaelt keine Segmente "
                             f"(insgesamt {count}).")
    return (start, end)


def render(project: Project, edit: EditList, plan: Plan, *, caps: Capabilities,
           manifest: Manifest | None, out: Path, jobs_limit: int | None = None,
           preview: bool = False, codec: str = "auto", range_spec: str | None = None,
           dry: DryRun | None = None) -> RenderStats:
    segments = resolve(plan)
    validate_continuity(segments, plan.total_frames)

    lo, hi = parse_range(range_spec, len(segments))
    selected = segments[lo:hi]
    profile = choose_profile(caps, edit, preview=preview, codec=codec)
    log.info("Encoder: %s (%s, %dx%d @ %g fps)", profile.name, profile.pix_fmt,
             profile.width, profile.height, profile.fps)

    index = HashIndex(project.cache / "hashindex.json")
    jobs = plan_jobs(project, plan, edit, selected, profile=profile, caps=caps,
                     manifest=manifest, index=index)
    index.save()

    stats = render_segments(jobs, workers=caps.max_workers(jobs_limit), dry=dry)
    if stats.failures:
        raise SlideshowError("Rendering fehlgeschlagen:\n" + "\n".join(stats.failures))

    if not (dry and dry.enabled):
        verify_uniform(jobs)

    start_f, end_f = selected[0].start_f, selected[-1].end_f
    timeline_seconds = to_time(end_f - start_f, plan.fps)
    audio = project.abs(edit.audio_file) if edit.audio_file else None
    # Der Ton blendet genau so lang aus wie das Bild — und nur, wenn das Ende
    # des Films ueberhaupt im gerenderten Bereich liegt (`--range`).
    letzte = selected[-1]
    fade_s = (to_time(fade_frames(plan, edit, letzte.frames), plan.fps)
              if letzte.end_f == plan.total_frames else 0.0)
    concat_and_mux(project, jobs, audio=audio, out=out, profile=profile,
                   timeline_seconds=timeline_seconds,
                   audio_start=to_time(start_f, plan.fps),
                   fade_seconds=fade_s, dry=dry)

    stats.timeline_seconds = timeline_seconds
    stats.music_seconds = to_time(plan.total_frames, plan.fps)
    if not (dry and dry.enabled):
        info = verify_master(out, expected_seconds=timeline_seconds, fps=plan.fps)
        stats.out_bytes = info["size_bytes"]
    return stats


def print_report(stats: RenderStats, out: Path) -> None:
    """Abschlusstabelle nach 8.5."""
    from rich.table import Table
    con = console()
    t = Table(title="Render-Report", title_justify="left")
    t.add_column("Kennzahl", style="bold")
    t.add_column("Wert", justify="right")
    t.add_row("Segmente gesamt", str(stats.total))
    t.add_row("aus Cache", str(stats.from_cache))
    t.add_row("neu gerendert", str(stats.rendered))
    t.add_row("Renderzeit", _hms(stats.seconds))
    t.add_row("Ausgabegroesse", f"{stats.out_bytes / 1e6:.1f} MB" if stats.out_bytes else "-")
    t.add_row("Laufzeit", f"{stats.timeline_seconds:.2f} s")
    t.add_row("Musiklaenge", f"{stats.music_seconds:.2f} s")
    t.add_row("Ausgabe", str(out))
    con.print(t)


def dump_commands(jobs: list[SegmentJob]) -> str:
    return "\n".join(shlex.join(j.cmd) for j in jobs)
