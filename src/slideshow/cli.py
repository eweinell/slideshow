"""CLI-Oberflaeche (Abschnitt 10)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .errors import SlideshowError, UsageError
from .logging_setup import console, setup_logging
from .paths import Project, is_wsl
from .proc import DryRun

log = logging.getLogger("slideshow.cli")


# --------------------------------------------------------------------------
# Argumente
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slideshow",
        description="Musiksynchroner 4K-Slideshow-Renderer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Uebliche Reihenfolge:\n"
               "  slideshow doctor\n"
               "  slideshow probe /pfad/zum/material\n"
               "  slideshow audio track1.mp3 track2.mp3 --gap 6\n"
               "  slideshow preprocess\n"
               "  slideshow beats cache/mix.flac        # Regionenkarte pruefen!\n"
               "  slideshow build\n"
               "  slideshow render edit.yaml -o out/master.mp4\n")
    p.add_argument("--version", action="version", version=f"slideshow {__version__}")
    p.add_argument("--project", metavar="DIR", default=None,
                   help="Projektverzeichnis (Default: aktuelles Verzeichnis)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="geplante Aktionen und Kommandos ausgeben, nichts schreiben")

    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="Preflight, Installationsvorschlaege")
    d.add_argument("--quick", action="store_true",
                   help="GPU-Praxistests ueberspringen")

    pr = sub.add_parser("probe", help="Quellmaterial erfassen -> manifest.json")
    pr.add_argument("sources", nargs="+", metavar="SRC")
    pr.add_argument("-o", "--output", default=None, metavar="manifest.json")
    pr.add_argument("--clock-offset", action="append", default=[], metavar="MODELL=+HH:MM:SS",
                    help="Uhren-Offset je Kameramodell, mehrfach angebbar")
    pr.add_argument("--fps", type=float, default=None,
                    help="Zielframerate erzwingen statt sie vorzuschlagen")

    au = sub.add_parser("audio", help="Musik normalisieren und mischen")
    au.add_argument("tracks", nargs="+", metavar="TRACK")
    au.add_argument("-o", "--output", default=None, metavar="cache/mix.flac")
    g = au.add_mutually_exclusive_group()
    g.add_argument("--gap", type=float, metavar="S", help="Sekunden Stille zwischen Tracks")
    g.add_argument("--xfade", type=float, metavar="S", help="Sekunden Crossfade")

    pp = sub.add_parser("preprocess", help="Bilder und Clips normalisieren -> cache/")
    pp.add_argument("--manifest", default=None)
    pp.add_argument("--portrait", choices=("blur", "black", "crop"), default="blur")
    pp.add_argument("--image-format", choices=("jpeg", "png16"), default="jpeg",
                    help="png16 nur sinnvoll mit --kb-engine scale16")
    pp.add_argument("--intermediate", default="dnxhr_hqx",
                    choices=("dnxhr_hqx", "hevc_intra_nvenc", "hevc_intra_cpu", "ffv1"))
    pp.add_argument("--jobs", type=int, default=None)
    pp.add_argument("--ranges-from", default=None, metavar="edit.yaml",
                    help="nur die tatsaechlich verwendeten Clipbereiche extrahieren")

    be = sub.add_parser("beats", help="Regionenkarte erzeugen -> beats.yaml")
    be.add_argument("audio", nargs="?", default=None, metavar="AUDIO")
    be.add_argument("-o", "--output", default=None, metavar="beats.yaml")
    be.add_argument("--bpm", type=float, default=None,
                    help="Tempo manuell setzen (erzeugt eine einzige beat-Region)")
    be.add_argument("--offset", type=float, default=None, help="Phase manuell setzen")
    be.add_argument("--still-seconds", type=float, default=4.0)

    bu = sub.add_parser("build", help="Edit-List generieren -> edit.yaml")
    bu.add_argument("-o", "--output", default=None, metavar="edit.yaml")
    bu.add_argument("--manifest", default=None)
    bu.add_argument("--beats", dest="beatmap", default=None)
    bu.add_argument("--fps", type=float, default=None)
    bu.add_argument("--size", default=None, metavar="WxH")
    bu.add_argument("--beats-per-still", type=int, default=None)
    bu.add_argument("--still-seconds", type=float, default=None)
    bu.add_argument("--portrait", choices=("blur", "black", "crop"), default=None)
    bu.add_argument("--kb-engine", choices=("zoompan", "scale16"), default=None)
    bu.add_argument("--xfade-beats", type=float, default=None)
    bu.add_argument("--no-xfade", action="store_true",
                    help="keine automatischen Uebergaenge erzeugen")
    bu.add_argument("--force", action="store_true",
                    help="trotz Ueber-/Unterdeckung schreiben")

    rd = sub.add_parser("render", help="Edit-List rendern -> master.mp4")
    rd.add_argument("edit", nargs="?", default=None, metavar="edit.yaml")
    rd.add_argument("-o", "--output", default=None, metavar="out/master.mp4")
    rd.add_argument("--jobs", type=int, default=None)
    rd.add_argument("--preview", action="store_true",
                    help="1280x720, libx264 — schnell und ohne NVENC-Sessions")
    rd.add_argument("--range", dest="range_spec", default=None, metavar="A:B")
    rd.add_argument("--codec", default="auto",
                    choices=("auto", "hevc_nvenc", "av1_nvenc", "libx265", "libx264"))
    rd.add_argument("--manifest", default=None)

    mx = sub.add_parser("export-mlt", help="Kdenlive-Projekt aus der Edit-List")
    mx.add_argument("edit", nargs="?", default=None, metavar="edit.yaml")
    mx.add_argument("-o", "--output", default=None, metavar="project.kdenlive")
    mx.add_argument("--manifest", default=None)
    mx.add_argument("--reimport", default=None, metavar="project.kdenlive",
                    help="in Kdenlive korrigierte Zeiten in die Edit-List zurueckfuehren")

    st = sub.add_parser("selftest", help="Fixtures erzeugen und Testsuite laufen lassen")
    st.add_argument("--make-fixtures", action="store_true")
    st.add_argument("--fixtures-dir", default=None)
    st.add_argument("--no-clips", action="store_true")
    return p


# --------------------------------------------------------------------------
# Subkommandos
# --------------------------------------------------------------------------

def cmd_doctor(args, project: Project) -> int:
    from .doctor import build_report, print_report
    rep = build_report(project, deep=not args.quick, refresh=True)
    print_report(rep)
    return 0 if not rep.failures else 2


def cmd_probe(args, project: Project) -> int:
    from .doctor import preflight
    from .probe import parse_clock_offset, probe_sources
    caps = preflight(project, "probe")

    offsets: dict[str, float] = {}
    for spec in args.clock_offset:
        model, secs = parse_clock_offset(spec)
        offsets[model] = secs

    result = probe_sources(project, [Path(s) for s in args.sources], caps=caps,
                           clock_offsets=offsets, target_fps=args.fps)
    out = Path(args.output) if args.output else project.manifest
    _carry_over_audio(out, result.manifest)
    if args.dry_run:
        console().print(f"[dim]--dry-run: wuerde {out} schreiben[/dim]")
    else:
        result.manifest.save(out)
    _print_probe_report(result, out)
    return 0


def _carry_over_audio(existing: Path, fresh) -> None:
    """Den Tonteil eines vorhandenen Manifests in den neuen Scan uebernehmen.

    ``probe`` baut das Manifest neu auf und wuesste vom Mix nichts — ein
    zweiter Scan (neue Bilder dazugelegt, Uhren-Offset korrigiert) loeschte
    sonst den Tonteil, waehrend ``cache/mix.flac`` liegen bleibt. Das faellt
    erst in ``build`` auf, und dort sieht es aus, als haette ``audio`` nie
    gelaufen. ``cache_path`` wird bewusst *nicht* uebernommen: den leitet
    ``preprocess`` aus dem Inhalts-Hash der Quelle neu ab.
    """
    from .models import Manifest
    if not existing.exists() or fresh.audio.file:
        return
    try:
        alt = Manifest.load(existing)
    except Exception:                     # noqa: BLE001 - ein kaputtes Altmanifest
        return                            # darf den frischen Scan nicht aufhalten
    if alt.audio.file:
        fresh.audio = alt.audio
        log.info("Tonspur aus dem vorhandenen Manifest uebernommen: %s (%.2f s)",
                 alt.audio.file, alt.audio.duration)


def _print_probe_report(result, out: Path) -> None:
    from datetime import datetime
    from rich.table import Table
    con = console()
    m = result.manifest

    t = Table(title="Material", title_justify="left")
    t.add_column("Art"); t.add_column("Anzahl", justify="right")
    t.add_column("Hinweise", overflow="fold")
    warned = sum(1 for x in m.media if x.warnings)
    t.add_row("Bilder", str(len(m.images)), "")
    t.add_row("Clips", str(len(m.clips)), "")
    t.add_row("mit Warnung", str(warned), "")
    if result.ignored:
        t.add_row("ignoriert", str(len(result.ignored)),
                  ", ".join(p.name for p in result.ignored[:4]))
    con.print(t)

    if result.device_spans:
        # Kamera- und Handy-Uhren gehen typischerweise Minuten bis Stunden
        # auseinander (Zeitzone!). Ohne Korrektur verschraenkt die
        # chronologische Sortierung die Geraete systematisch.
        dt = Table(title="Geraete und Zeitspannen (4.4)", title_justify="left")
        dt.add_column("Kameramodell"); dt.add_column("Aufnahmen", justify="right")
        dt.add_column("von"); dt.add_column("bis")
        for cam, (lo, hi, n) in result.device_spans.items():
            dt.add_row(cam, str(n),
                       datetime.fromtimestamp(lo).strftime("%Y-%m-%d %H:%M:%S"),
                       datetime.fromtimestamp(hi).strftime("%Y-%m-%d %H:%M:%S"))
        con.print(dt)
        if len(result.device_spans) > 1:
            con.print("[yellow]Mehrere Geraete.[/yellow] Wenn die Zeitspannen "
                      "implausibel gegeneinander liegen, Uhren-Offset setzen:")
            con.print('  slideshow probe ... --clock-offset "MODELL=+01:00:00"')

    if m.fps_histogram:
        con.print(f"\n[bold]Framerate-Verteilung:[/bold] {m.fps_histogram} "
                  f"(Sekunden je Rate)")
    con.print(f"[bold]Zielrate:[/bold] {m.fps_suggestion:g}p — {m.fps_rationale}")

    warnings = [(x.id, w) for x in m.media for w in x.warnings]
    if warnings:
        con.print(f"\n[bold]Hinweise ({len(warnings)}):[/bold]")
        for mid, w in warnings[:20]:
            con.print(f"  [yellow]{mid}[/yellow]: {w}")
        if len(warnings) > 20:
            con.print(f"  [dim]... {len(warnings) - 20} weitere, vollstaendig im Log[/dim]")

    con.print("\n[bold]Offene Entscheidungen (Abschnitt 15)[/bold] — Defaults, "
              "die bestaetigt oder ueberschrieben werden wollen:")
    con.print(f"  Zielframerate      {m.fps_suggestion:g}p          "
              f"[dim]--fps 50[/dim]")
    con.print( "  Intermediate       dnxhr_hqx        "
               "[dim]preprocess --intermediate hevc_intra_cpu[/dim]")
    con.print( "  Ausgabecodec       HEVC             [dim]render --codec av1_nvenc[/dim]")
    con.print( "  Hochformat-Modus   blur             [dim]preprocess --portrait crop[/dim]")
    con.print( "  Beats pro Bild     8                [dim]build --beats-per-still 6[/dim]")
    con.print(f"\nManifest: {out}")


def cmd_audio(args, project: Project) -> int:
    from .audio import build_mix
    from .doctor import preflight
    from .models import Manifest
    preflight(project, "audio")
    project.ensure_dirs()

    out = Path(args.output) if args.output else (project.cache / "mix.flac")
    dry = DryRun(enabled=args.dry_run)
    gap = args.gap
    if gap is None and args.xfade is None and len(args.tracks) > 1:
        gap = 6.0
        log.info("Weder --gap noch --xfade angegeben, verwende --gap 6")

    info = build_mix([Path(t) for t in args.tracks], out, gap=gap, xfade=args.xfade,
                     workdir=project.cache, dry=dry)
    info.file = project.rel(out)

    if args.dry_run:
        console().print(dry.as_text())
        return 0

    (project.cache / "mix.json").write_text(
        json.dumps(info.model_dump(mode="json"), indent=1), encoding="utf-8")
    if project.manifest.exists():
        manifest = Manifest.load(project.manifest)
        manifest.audio = info
        manifest.save(project.manifest)
    console().print(f"Mix: {out} ({info.duration:.2f} s, {len(info.tracks)} Tracks)")
    return 0


def cmd_preprocess(args, project: Project) -> int:
    from .doctor import estimate_space, check_space, preflight
    from .models import Manifest
    from .preprocess import preprocess
    caps = preflight(project, "preprocess")

    path = Path(args.manifest) if args.manifest else project.manifest
    manifest = Manifest.load(path)
    _merge_audio_info(project, manifest)

    clip_seconds = sum((m.clip.duration if m.clip else 0.0) for m in manifest.clips)
    est = estimate_space(images=len(manifest.images), clip_seconds=clip_seconds,
                         timeline_seconds=manifest.audio.duration or 300.0)
    check = check_space(project, est)
    console().print(f"[{'green' if check.status == 'OK' else 'red'}]{check.status}[/] "
                    f"{check.detail}")
    if check.status == "FAIL":
        raise SlideshowError(check.fix or "Zu wenig Speicherplatz.")

    spans = None
    if args.ranges_from:
        spans = _spans_from_edit(project, Path(args.ranges_from), manifest)

    dry = DryRun(enabled=args.dry_run)
    stats = preprocess(project, manifest, caps=caps, portrait_mode=args.portrait,
                       image_format=args.image_format, size=tuple(_size_of(manifest)),
                       intermediate_codec=args.intermediate, jobs=args.jobs,
                       dry=dry, spans=spans)
    if args.dry_run:
        console().print(dry.as_text())
        return 0

    manifest.save(path)
    console().print(f"Bilder: {stats.images_done} neu, {stats.images_cached} aus Cache | "
                    f"Clips: {stats.clips_done} neu, {stats.clips_cached} aus Cache")
    if stats.failures:
        for f in stats.failures:
            console().print(f"  [red]FAIL[/] {f}")
        return 1
    return 0


def _size_of(manifest) -> tuple[int, int]:
    return (3840, 2160)


def _spans_from_edit(project: Project, edit_path: Path, manifest) -> dict:
    from .models import ClipSegment, EditList
    edit = EditList.load(edit_path)
    spans: dict[str, tuple[float, float]] = {}
    for seg in edit.segments:
        if not isinstance(seg, ClipSegment):
            continue
        item = manifest.by_cache_path(seg.src)
        if item is None:
            continue
        lo, hi = seg.in_, (seg.out if seg.out is not None else seg.in_)
        prev = spans.get(item.id)
        spans[item.id] = (min(lo, prev[0]), max(hi, prev[1])) if prev else (lo, hi)
    return spans


def _merge_audio_info(project: Project, manifest) -> None:
    """Track-Grenzen aus `slideshow audio` ins Manifest ziehen."""
    from .models import AudioInfo
    mixjson = project.cache / "mix.json"
    if manifest.audio.file or not mixjson.exists():
        return
    try:
        manifest.audio = AudioInfo.model_validate(json.loads(mixjson.read_text("utf-8")))
    except Exception as exc:                        # noqa: BLE001
        log.debug("mix.json nicht lesbar: %s", exc)


def cmd_beats(args, project: Project) -> int:
    import yaml
    from .beats import detect_regions, validate_tiling
    from .doctor import preflight
    from .models import BeatMap, Manifest, Region
    from .audio import audio_duration
    preflight(project, "beats")

    audio = Path(args.audio) if args.audio else (project.cache / "mix.flac")
    if not audio.exists():
        raise SlideshowError(f"Tonspur fehlt: {audio}. `slideshow audio` zuerst laufen "
                             f"lassen oder den Pfad angeben.")

    bounds: list[tuple[float, float]] = []
    if project.manifest.exists():
        manifest = Manifest.load(project.manifest)
        _merge_audio_info(project, manifest)
        bounds = [(t.start, t.end) for t in manifest.audio.tracks]

    if args.bpm:
        dur = audio_duration(audio)
        bm = BeatMap(audio={"file": project.rel(audio), "duration": round(dur, 6)},
                     regions=[Region(type="beat", start=0.0, end=round(dur, 6),
                                     bpm=args.bpm, offset=args.offset or 0.0, conf=1.0)])
        log.info("Tempo manuell gesetzt: %.2f BPM, Offset %.3f s",
                 args.bpm, args.offset or 0.0)
    else:
        bm = detect_regions(audio, track_bounds=bounds, still_seconds=args.still_seconds)
        bm.audio["file"] = project.rel(audio)

    validate_tiling(bm.regions, float(bm.audio["duration"]))
    out = Path(args.output) if args.output else (project.root / "beats.yaml")
    data = {"version": bm.version, "audio": bm.audio,
            "regions": [_region_yaml(r) for r in bm.regions]}
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=None)
    if args.dry_run:
        console().print(text)
        return 0
    out.write_text(
        "# Regionenkarte — zur Sichtpruefung und manuellen Korrektur, BEVOR gebaut wird.\n"
        "# Die automatische Downbeat-Phase ist der unzuverlaessigste Teil der ganzen\n"
        "# Analyse. Eine falsche Phase ruiniert den Schnitt; sie von Hand zu setzen\n"
        "# dauert dreissig Sekunden.\n" + text, encoding="utf-8")
    _print_beatmap(bm, out)
    return 0


def _region_yaml(r) -> dict:
    d = {"type": r.type, "start": round(r.start, 6), "end": round(r.end, 6)}
    if r.type == "beat":
        d.update(bpm=r.bpm, offset=round(r.offset or 0.0, 6), conf=round(r.conf or 0, 4))
    elif r.reason:
        d["reason"] = r.reason
    return d


def _print_beatmap(bm, out: Path) -> None:
    from rich.table import Table
    t = Table(title="Regionenkarte", title_justify="left")
    for c in ("#", "Typ", "Start", "Ende", "Dauer", "BPM", "Offset", "Konfidenz"):
        t.add_column(c, justify="right" if c not in ("Typ",) else "left")
    for i, r in enumerate(bm.regions):
        t.add_row(str(i), r.type, f"{r.start:.3f}", f"{r.end:.3f}", f"{r.duration:.3f}",
                  f"{r.bpm:.2f}" if r.bpm else "-",
                  f"{r.offset:.3f}" if r.offset is not None else "-",
                  f"{r.conf:.2f}" if r.conf is not None else "-")
    con = console()
    con.print(t)
    con.print(f"\n[bold]Bitte pruefen[/bold], dann `slideshow build`. Karte: {out}")


def cmd_build(args, project: Project) -> int:
    import yaml
    from .build import build_edit_list, validate_edit, write_timeline
    from .doctor import preflight
    from .models import BeatMap, Defaults, Manifest, Region
    from .planner import coverage_advice, resolve
    preflight(project, "build")

    manifest = Manifest.load(Path(args.manifest) if args.manifest else project.manifest)
    _merge_audio_info(project, manifest)

    bpath = Path(args.beatmap) if args.beatmap else (project.root / "beats.yaml")
    if not bpath.exists():
        raise SlideshowError(f"Regionenkarte fehlt: {bpath}. `slideshow beats` zuerst "
                             f"laufen lassen — und die Karte ansehen.")
    raw = yaml.safe_load(bpath.read_text(encoding="utf-8")) or {}
    beatmap = BeatMap(version=raw.get("version", 1), audio=raw.get("audio", {}),
                      regions=[Region.model_validate(r) for r in raw.get("regions", [])])

    defaults = Defaults()
    if args.beats_per_still is not None:
        defaults.beats_per_still = args.beats_per_still
    if args.still_seconds is not None:
        defaults.still_seconds = args.still_seconds
    if args.portrait:
        defaults.portrait = args.portrait
    if args.kb_engine:
        defaults.kb.engine = args.kb_engine
    if args.xfade_beats is not None:
        defaults.xfade.beats = args.xfade_beats
    defaults.xfade.auto = not args.no_xfade

    size = _parse_size(args.size) if args.size else (3840, 2160)
    edit, plan, cov = build_edit_list(project, manifest, beatmap, defaults=defaults,
                                      fps=args.fps, size=size)
    _print_coverage(cov, defaults, plan)

    tips = coverage_advice(cov, defaults)
    if tips and not args.force:
        for line in tips:
            console().print(f"  [yellow]{line}[/]")
    if (cov.underrun or cov.overrun) and not args.force:
        console().print("[yellow]Mit --force trotzdem schreiben.[/yellow]")

    out = Path(args.output) if args.output else project.edit
    if args.dry_run:
        from .models import dump_edit_yaml
        console().print(dump_edit_yaml(edit))
        return 0

    edit.save(out)
    plan2 = validate_edit(edit, manifest)
    segments = resolve(plan2)
    tpath = write_timeline(project, plan2, segments)
    console().print(f"\nEdit-List: {out}  ({len(edit.segments)} Segmente, "
                    f"davon {sum(1 for s in edit.segments if s.type == 'xfade')} Uebergaenge)")
    console().print(f"Aufgeloeste Timeline: {tpath}")
    return 0


def _print_coverage(cov, defaults, plan) -> None:
    """Laufzeit-Vorabpruefung nach 6.5."""
    from rich.table import Table
    t = Table(title="Laufzeit-Vorabpruefung (6.5)", title_justify="left")
    for c in ("#", "Typ", "Start", "Dauer", "BPM", "Kapazitaet", "Bilder", "Clips"):
        t.add_column(c, justify="right" if c != "Typ" else "left")
    for r in cov.per_region:
        t.add_row(str(r["index"]), r["type"], f"{r['start']:.2f}", f"{r['seconds']:.2f}",
                  f"{r['bpm']:.1f}" if r["bpm"] else "-", str(r["capacity"]),
                  str(r["stills"]), str(r["clips"]))
    con = console()
    con.print(t)
    con.print(f"Musik {cov.music_seconds:.2f} s | geplant {cov.planned_seconds:.2f} s | "
              f"{cov.stills} Bilder, {cov.clips} Clips")
    for w in plan.warnings[:10]:
        con.print(f"  [yellow]WARN[/] {w}")


def _parse_size(spec: str) -> tuple[int, int]:
    try:
        w, h = spec.lower().split("x")
        return (int(w), int(h))
    except ValueError:
        raise UsageError(f"Unlesbare Groesse {spec!r}. Erwartet: --size 3840x2160") from None


def cmd_render(args, project: Project) -> int:
    from .build import check_sources_exist, validate_edit
    from .doctor import preflight
    from .models import EditList, Manifest
    from .render import print_report, render
    caps = preflight(project, "render")

    epath = Path(args.edit) if args.edit else project.edit
    edit = EditList.load(epath)
    mpath = Path(args.manifest) if args.manifest else project.manifest
    manifest = Manifest.load(mpath) if mpath.exists() else None

    check_sources_exist(project, edit)
    plan = validate_edit(edit, manifest)

    out = Path(args.output) if args.output else (project.out / "master.mp4")
    dry = DryRun(enabled=args.dry_run)
    stats = render(project, edit, plan, caps=caps, manifest=manifest, out=out,
                   jobs_limit=args.jobs, preview=args.preview, codec=args.codec,
                   range_spec=args.range_spec, dry=dry)
    if args.dry_run:
        console().print(dry.as_text())
        return 0
    print_report(stats, out)
    return 0


def cmd_export_mlt(args, project: Project) -> int:
    from .models import EditList, Manifest
    from .mlt import export_mlt, reimport_mlt
    epath = Path(args.edit) if args.edit else project.edit
    edit = EditList.load(epath)
    mpath = Path(args.manifest) if args.manifest else project.manifest
    manifest = Manifest.load(mpath) if mpath.exists() else None

    if args.reimport:
        changes = reimport_mlt(project, Path(args.reimport), edit, manifest)
        if args.dry_run:
            console().print(json.dumps(changes, indent=1))
            return 0
        edit.save(epath)
        console().print(f"{len(changes)} Zeiten aus Kdenlive uebernommen -> {epath}")
        return 0

    out = Path(args.output) if args.output else (project.out / "project.kdenlive")
    xml = export_mlt(project, edit, manifest)
    if args.dry_run:
        console().print(xml[:4000])
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")
    console().print(f"Kdenlive-Projekt: {out}")
    return 0


def cmd_selftest(args, project: Project) -> int:
    from .fixtures import make_fixtures
    root = Path(args.fixtures_dir) if args.fixtures_dir else (project.root / "fixtures")
    if args.make_fixtures:
        if args.dry_run:
            console().print(f"[dim]--dry-run: wuerde Fixtures nach {root} schreiben[/dim]")
            return 0
        make_fixtures(root, with_clips=not args.no_clips)
        console().print(f"Fixtures: {root}")
        return 0

    try:
        import pytest
    except ImportError:
        raise SlideshowError("pytest fehlt. Installation: pip install pytest") from None
    tests = Path(__file__).resolve().parent.parent.parent / "tests"
    if not tests.exists():
        raise SlideshowError(f"Testsuite nicht gefunden: {tests}")
    return int(pytest.main([str(tests), "-q"]))


_COMMANDS = {
    "doctor": cmd_doctor, "probe": cmd_probe, "audio": cmd_audio,
    "preprocess": cmd_preprocess, "beats": cmd_beats, "build": cmd_build,
    "render": cmd_render, "export-mlt": cmd_export_mlt, "selftest": cmd_selftest,
}


# --------------------------------------------------------------------------
# Einstieg
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        project = Project.open(args.project, create=True)
    except SlideshowError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return exc.exit_code

    logfile = setup_logging(project, args.command, verbose=args.verbose, quiet=args.quiet)
    log.debug("slideshow %s | %s", __version__, " ".join(sys.argv[1:]))

    try:
        return _COMMANDS[args.command](args, project)
    except SlideshowError as exc:
        console().print(f"\n[red]Fehler:[/red] {exc}")
        console().print(f"[dim]Log: {logfile}[/dim]")
        return exc.exit_code
    except KeyboardInterrupt:
        console().print("\n[yellow]Abgebrochen.[/yellow]")
        return 130
    except Exception as exc:                        # noqa: BLE001
        log.exception("Unerwarteter Fehler")
        console().print(f"\n[red]Unerwarteter Fehler:[/red] {type(exc).__name__}: {exc}")
        console().print(f"[dim]Vollstaendiger Traceback im Log: {logfile}[/dim]")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
