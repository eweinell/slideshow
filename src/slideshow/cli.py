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
# Nur Konstanten — die Vorgaben stehen dort, wo sie wirken, und nicht ein
# zweites Mal in den `--help`-Texten.
from .select import (BURST_GAP, BY_CHOICES, DAY_ALPHA, MAX_PORTRAIT, MAX_SHARE,
                     MIN_LONG_EDGE, MIN_PER_DAY)
from .sheet import THUMB_SIZE

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
               "  slideshow order                       # optional: Reihenfolge sortieren\n"
               "  slideshow chapters                    # optional: Titelfolien\n"
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
    pp.add_argument("--order", default=None, metavar="order.yaml",
                    help="nur die dort genannten Medien normalisieren "
                         "(Default: order.yaml im Projekt, falls vorhanden). "
                         "Nach `slideshow select` spart das den Grossteil der "
                         "Arbeit — es wird nur aufbereitet, was in den Film kommt")
    pp.add_argument("--all", action="store_true",
                    help="alles aus dem Manifest normalisieren, auch was die "
                         "Auswahl auslaesst — fuer den Fall, dass die Auswahl "
                         "noch mehrfach umgeworfen wird")

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
    bu.add_argument("--fade-out", type=float, default=None, metavar="S",
                    help="Ausblende am Filmende in Sekunden (0 = keine)")
    ch = sub.add_parser("chapters", help="Kapitelgrenzen vorschlagen -> chapters.yaml")
    ch.add_argument("-o", "--output", default=None, metavar="chapters.yaml")
    ch.add_argument("--manifest", default=None)
    ch.add_argument("--min-gap", type=float, default=None, metavar="H",
                    help="Zeitluecke in Stunden, ab der ein Kapitel vorgeschlagen "
                         "wird (Default 20)")
    ch.add_argument("--min-jump", type=float, default=None, metavar="KM",
                    help="Ortssprung in km, ab dem ein Kapitel vorgeschlagen wird "
                         "(Default 30)")
    ch.add_argument("--from-groups", action="store_true",
                    help="ein Kapitel je Block aus order.yaml statt aus "
                         "Zeitluecken — der Weg, wenn die Abschnitte beim "
                         "Sortieren von Hand gezogen wurden")
    ch.add_argument("--no-auftakt", action="store_true",
                    help="keinen Titel vor dem Material vorschlagen")
    ch.add_argument("--force", action="store_true",
                    help="vorhandene Datei ueberschreiben")

    od = sub.add_parser(
        "order", help="Reihenfolge zum Sortieren erzeugen -> order.yaml",
        description="Erzeugt order.yaml: die Medien chronologisch vorbelegt, "
                    "gruppiert und je Zeile mit Tag, Uhrzeit und Format "
                    "kommentiert. Sortiert wird danach von Hand, indem man "
                    "Zeilen verschiebt. `slideshow build` liest die Datei von "
                    "selbst, wenn sie im Projekt liegt.")
    od.add_argument("-o", "--output", default=None, metavar="order.yaml",
                    help="Zieldatei (Default: order.yaml im Projektverzeichnis)")
    od.add_argument("--manifest", default=None,
                    help="abweichendes Manifest (Default: manifest.json im Projekt)")
    od.add_argument("--by", choices=("day", "place", "none"), default="day",
                    help="Vorgruppierung: 'day' ein Block je Kalendertag (Default), "
                         "'place' ein Block je Ortscluster aus GPS, 'none' ein "
                         "einziger Block")
    od.add_argument("--update", action="store_true",
                    help="neu hinzugekommenes Material einpflegen und dabei "
                         "Sortierung, Gruppennamen und Kommentare behalten — der "
                         "Weg nach einem erneuten `probe`")
    od.add_argument("--apply", default=None, metavar="auswahl.txt",
                    help="Aenderungen aus dem Kontaktbogen einspielen: markierte "
                         "Medien hereinnehmen, andere auskommentieren. `-` liest "
                         "die Zeilen von der Eingabe, zum Einfuegen aus der "
                         "Zwischenablage")
    od.add_argument("--force", action="store_true",
                    help="vorhandene Datei ueberschreiben und damit die Sortierung "
                         "verwerfen (globales --dry-run zeigt sie nur an)")

    se = sub.add_parser(
        "select", help="Teilmenge aus grossem Material waehlen -> order.yaml",
        description="Waehlt aus einem Sammelbecken die Bilder aus, die in den "
                    "Film passen: keine zwei Aufnahmen desselben Motivs, von "
                    "jedem Tag etwa gleich viele, ueber den Tag verteilt. "
                    "Geschrieben wird eine order.yaml mit `rest: drop` — alles "
                    "Uebrige bleibt als Kommentar darin stehen und laesst sich "
                    "durch Zeilentausch hereinholen. Ohne inhaltliche Analyse: "
                    "gerechnet wird auf Zeitstempeln und EXIF.")
    se.add_argument("-o", "--output", default=None, metavar="order.yaml",
                    help="Zieldatei (Default: order.yaml im Projektverzeichnis)")
    se.add_argument("--manifest", default=None,
                    help="abweichendes Manifest (Default: manifest.json im Projekt)")
    se.add_argument("--count", default="auto", metavar="N|auto",
                    help="wie viele Medien (Default: auto — so viele, wie die "
                         "Regionenkarte aus beats.yaml hergibt)")
    se.add_argument("--seed", type=int, default=None,
                    help="Zufallszahl der Auswahl. Ohne Angabe wird eine gezogen "
                         "und in die Datei geschrieben — derselbe Seed liefert "
                         "denselben Vorschlag noch einmal")
    se.add_argument("--by", choices=BY_CHOICES, default="day",
                    help="worauf sich die Quote bezieht: 'day' je Kalendertag "
                         "(Default), 'place' je Ortscluster aus GPS, 'none' gar "
                         "nicht — dann wirkt nur die Spreizung")
    se.add_argument("--burst-gap", type=float, default=BURST_GAP, metavar="SEK",
                    help=f"Abstand, unter dem zwei Aufnahmen als dasselbe Motiv "
                         f"gelten (Default: {BURST_GAP:g}). Aus jeder solchen "
                         f"Traube kommt hoechstens ein Bild in den Film")
    se.add_argument("--day-weight", type=float, default=DAY_ALPHA, metavar="ALPHA",
                    help=f"Daempfung der Quote (Default: {DAY_ALPHA:g}). 0 = gleich "
                         f"viele je Tag, 1 = proportional zum Material")
    se.add_argument("--min-per-day", type=int, default=MIN_PER_DAY,
                    help=f"Mindestzahl je Tag mit Material (Default: {MIN_PER_DAY})")
    se.add_argument("--max-share", type=float, default=MAX_SHARE,
                    help=f"Hoechstanteil eines Tages am Film (Default: {MAX_SHARE:g})")
    se.add_argument("--min-long-edge", type=int, default=MIN_LONG_EDGE, metavar="PX",
                    help=f"Bilder mit kuerzerer Langkante kommen nicht in Frage "
                         f"(Default: {MIN_LONG_EDGE}) — der Master ist 4K, und Ken "
                         f"Burns zoomt hinein")
    se.add_argument("--rating-min", type=int, default=0, metavar="STERNE",
                    help="nur Bilder ab dieser Bewertung (Default: aus). Wo "
                         "Sterne vergeben wurden, ist das das beste Signal, das "
                         "diesem Verfahren zur Verfuegung steht")
    se.add_argument("--max-portrait", type=float, default=MAX_PORTRAIT,
                    help=f"Hoechstanteil Hochformat (Default: {MAX_PORTRAIT:g}); "
                         f"darueber wird innerhalb der Traube getauscht")
    se.add_argument("--no-keep-clips", action="store_true",
                    help="Clips wie Bilder behandeln statt sie alle zu nehmen")
    se.add_argument("--sheet", action="store_true",
                    help="danach gleich den Kontaktbogen erzeugen "
                         "(`slideshow sheet`) — die Auswahl kennt keine "
                         "Bildinhalte, angesehen werden muss sie trotzdem")
    se.add_argument("--force", action="store_true",
                    help="vorhandene order.yaml ueberschreiben und damit Auswahl "
                         "und Sortierung verwerfen")

    sh = sub.add_parser(
        "sheet", help="Kontaktbogen zur Auswahl erzeugen -> contact.html",
        description="Erzeugt eine HTML-Seite, auf der die Auswahl aus order.yaml "
                    "zu sehen ist: nach Tagen gegliedert, jede Traube als "
                    "Kachelgruppe, das gewaehlte Bild gross, seine Geschwister "
                    "klein daneben. Der Bogen liest order.yaml und schreibt "
                    "nichts — ein Klick markiert einen Tausch, der Knopf legt "
                    "die YAML-Zeilen in die Zwischenablage, eintragen macht der "
                    "Mensch.")
    sh.add_argument("-o", "--output", default=None, metavar="contact.html",
                    help="Zieldatei (Default: contact.html im Projektverzeichnis)")
    sh.add_argument("--manifest", default=None,
                    help="abweichendes Manifest (Default: manifest.json im Projekt)")
    sh.add_argument("--order", default=None, metavar="order.yaml",
                    help="Auswahl, die gezeigt wird (Default: order.yaml im "
                         "Projekt). Was dort in `items:` steht, ist gewaehlt — "
                         "auch nach Handarbeit")
    sh.add_argument("--thumb", type=int, default=THUMB_SIZE, metavar="PX",
                    help=f"Kachelgroesse in Bildpunkten (Default: {THUMB_SIZE})")
    sh.add_argument("--jobs", type=int, default=None,
                    help="parallele ffmpeg-Laeufe fuer Bilder ohne eingebettete "
                         "Vorschau")
    auswahl = sh.add_mutually_exclusive_group()
    auswahl.add_argument("--all", action="store_true", dest="alles",
                         help="alles zeigen: Auswahl, Geschwister, ausgelassene "
                              "Trauben, technische Ausschluesse (Default)")
    auswahl.add_argument("--selected", action="store_true",
                         help="nur die gewaehlten Medien — der schnelle Blick auf "
                              "den Film, ohne die Alternativen")
    # Bewusst nicht `--force`. Der heisst bei `select`, `order` und `chapters`
    # "ueberschreib meine Handarbeit"; hier gibt es keine — contact.html ist ein
    # Erzeugnis und wird immer neu geschrieben. Wer den Schalter aus Gewohnheit
    # setzte, verwuerfe nur den Bildcache und wartete bei 1240 Bildern eine
    # halbe Stunde auf dasselbe Ergebnis.
    sh.add_argument("--refresh-thumbs", action="store_true", dest="force",
                    help="Thumbnails neu erzeugen, statt vorhandene "
                         "wiederzuverwenden — noetig, wenn ein Bild unter "
                         "gleichem Namen neu entwickelt wurde")

    bu.add_argument("--chapters", default=None, metavar="chapters.yaml",
                    help="Titel- und Zwischenfolien einsetzen "
                         "(Default: chapters.yaml im Projekt, falls vorhanden)")
    bu.add_argument("--order", default=None, metavar="order.yaml",
                    help="Medien in der dort festgelegten Reihenfolge statt "
                         "chronologisch; die Datei bestimmt ueber `rest:` auch, "
                         "was mit nicht genanntem Material geschieht "
                         "(Default: order.yaml im Projekt, falls vorhanden)")
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
    rd.add_argument("--range", dest="range_spec", default=None, metavar="A:B",
                    help="nur die Segmente [A, B) rendern — Segmentnummern, "
                         "keine Sekunden")
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

    # Fehlende Aufnahmezeitpunkte einzeln zu melden reicht nicht: bei 1000
    # Bildern sind das 1000 gleichlautende Zeilen, von denen der Bericht 20
    # zeigt. Dass die *Zeitstruktur des Materials* fehlt — und damit die
    # Grundlage von Reihenfolge, Kapiteln und Auswahl —, sieht man erst an der
    # Summe.
    ohne_zeit = [x for x in m.media if x.time_source in ("mtime", "none")]
    if len(ohne_zeit) > 20 or (m.media and len(ohne_zeit) > len(m.media) // 5):
        anteil = 100.0 * len(ohne_zeit) / len(m.media)
        con.print(f"\n[yellow]{len(ohne_zeit)} von {len(m.media)} Medien "
                  f"({anteil:.0f} %) haben keinen verwertbaren Aufnahmezeitpunkt.[/yellow] "
                  f"Die Reihenfolge kommt fuer sie aus der Dateizeit und ist "
                  f"vermutlich falsch. Ist exiftool installiert und liest es "
                  f"dieses Format? `slideshow doctor` sagt es.")

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


def _auswahl(project: Project, angabe: str | None, manifest) -> set[str] | None:
    """Die Medien-IDs aus ``order.yaml`` — oder ``None`` fuer "alles".

    Dieselbe Fundregel wie bei ``build`` (:func:`_load_order`): ein
    ausdruecklich genannter Pfad, den es nicht gibt, ist ein Fehler; eine
    gefundene ``order.yaml`` ist Bequemlichkeit.

    **``rest:`` gilt hier genauso wie beim Bauen.** Ein ``rest: error`` bricht
    also auch ``preprocess`` ab — sonst normalisiert man eine Stunde lang
    Material, das ``build`` fuenf Minuten spaeter verweigert.
    """
    ids, meldungen, _olist = _load_order(project, angabe, manifest,
                                         titel="Auswahl")
    if ids is None:
        return None
    for m in meldungen:
        console().print(f"  [dim]{m}[/dim]")
    return set(ids)


def cmd_preprocess(args, project: Project) -> int:
    from .doctor import estimate_space, check_space, preflight
    from .models import Manifest
    from .preprocess import preprocess
    caps = preflight(project, "preprocess")

    path = Path(args.manifest) if args.manifest else project.manifest
    manifest = Manifest.load(path)
    _merge_audio_info(project, manifest)

    only = None if args.all else _auswahl(project, args.order, manifest)
    bilder = [m for m in manifest.images if only is None or m.id in only]
    filme = [m for m in manifest.clips if only is None or m.id in only]

    # Geschaetzt wird ueber das, was wirklich verarbeitet wird. Sonst verlangt
    # die Platzpruefung bei tausend erfassten und zweihundert gewaehlten
    # Bildern das Fuenffache und schlaegt Alarm, wo nichts ist.
    clip_seconds = sum((m.clip.duration if m.clip else 0.0) for m in filme)
    est = estimate_space(images=len(bilder), clip_seconds=clip_seconds,
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
                       dry=dry, spans=spans, only=only)
    if args.dry_run:
        console().print(dry.as_text())
        return 0

    manifest.save(path)
    console().print(f"Bilder: {stats.images_done} neu, {stats.images_cached} aus Cache | "
                    f"Clips: {stats.clips_done} neu, {stats.clips_cached} aus Cache")
    if stats.skipped:
        console().print(f"  [dim]{stats.skipped} Medien ausgelassen — sie stehen "
                        f"nicht in der Auswahl. `--all` verarbeitet trotzdem "
                        f"alles.[/dim]")
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
    ohne_ton = not audio.exists()

    bounds: list[tuple[float, float]] = []
    if project.manifest.exists() and not ohne_ton:
        manifest = Manifest.load(project.manifest)
        _merge_audio_info(project, manifest)
        bounds = [(t.start, t.end) for t in manifest.audio.tracks]

    if ohne_ton:
        bm = _beatmap_ohne_tonspur(project, still_seconds=args.still_seconds)
    elif args.bpm:
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


def _beatmap_ohne_tonspur(project: Project, *, still_seconds: float):
    """Regionenkarte fuer ein Projekt ganz ohne Tonspur.

    Ohne Musik gibt es nichts zu rastern. Die Karte besteht deshalb aus einer
    einzigen free-Region, deren Laenge sich aus der Zahl der Medien und dem
    Standardtakt ergibt — der uebliche Weg `beats` -> `build` -> `render` laeuft
    damit auch hier durch, statt in einer Sackgasse zu enden.
    """
    from .models import BeatMap, Manifest, Region
    if not project.manifest.exists():
        raise SlideshowError(
            f"Weder Tonspur noch Manifest gefunden ({project.manifest}). "
            f"`slideshow probe <material>` zuerst laufen lassen — und fuer eine "
            f"vertonte Slideshow zusaetzlich `slideshow audio <track>`.")
    manifest = Manifest.load(project.manifest)
    n = len(manifest.media)
    if n == 0:
        raise SlideshowError("Das Manifest enthaelt keine Medien — `slideshow probe` "
                             "zuerst laufen lassen.")
    dauer = round(n * still_seconds, 6)
    log.info("Keine Tonspur — Karte aus %d Medien x %.1f s = %.1f s",
             n, still_seconds, dauer)
    return BeatMap(audio={"file": "", "duration": dauer},
                   regions=[Region(type="free", start=0.0, end=dauer,
                                   reason="ohne Tonspur")])


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
    from .order import anchor_chapters
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
    if args.fade_out is not None:
        defaults.fade_out = args.fade_out
    if args.xfade_beats is not None:
        defaults.xfade.beats = args.xfade_beats
    defaults.xfade.auto = not args.no_xfade

    chapters = _load_chapters(project, args.chapters, defaults)
    order, order_notes, olist = _load_order(project, args.order, manifest)
    # `group:` zeigt auf einen Block in order.yaml; ``build`` kennt nur noch
    # eine Liste. Aufgeloest wird das hier, an der einzigen Stelle, die beide
    # Dateien vor sich hat.
    chapters = anchor_chapters(chapters, olist, order)

    size = _parse_size(args.size) if args.size else (3840, 2160)
    edit, plan, cov = build_edit_list(project, manifest, beatmap, defaults=defaults,
                                      fps=args.fps, size=size, chapters=chapters,
                                      order=order, order_notes=order_notes)
    _print_coverage(cov, defaults, plan)

    tips = coverage_advice(cov, defaults)
    if tips and not args.force:
        for line in tips:
            console().print(f"  [yellow]{line}[/]")
        # Kein Abbruch: die Edit-List wird in jedem Fall geschrieben. Frueher
        # stand hier "Mit --force trotzdem schreiben", was eine Aktion
        # verlangte, die es nie gab — --force unterdrueckt nur diese Hinweise.
        console().print("[dim]Hinweis, kein Fehler — die Edit-List wird geschrieben "
                        "(`--force` blendet das aus).[/dim]")

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


def cmd_chapters(args, project: Project) -> int:
    from .models import Manifest

    manifest = Manifest.load(Path(args.manifest) if args.manifest else project.manifest)
    text, bericht, signal = (_chapters_aus_gruppen(args, project, manifest)
                             if args.from_groups
                             else _chapters_aus_luecken(args, project, manifest))

    out = Path(args.output) if args.output else (project.root / "chapters.yaml")
    con = console()
    if args.dry_run:
        con.print(text)
        return 0
    # Die Datei ist Handarbeit, sobald sie einmal ausgefuellt wurde. Sie
    # kommentarlos zu ueberschreiben hiesse, zwoelf Ortsnamen zu loeschen.
    if out.exists() and not args.force:
        raise SlideshowError(
            f"{out} gibt es bereits. Die Datei enthaelt von Hand eingetragene "
            f"Ueberschriften — `--force` ueberschreibt sie, `--dry-run` zeigt den "
            f"Vorschlag nur an.")
    out.write_text(text, encoding="utf-8")

    con.print(f"Kapitelvorschlaege: {out}  ({bericht})")
    con.print(f"[dim]{signal}[/dim]")
    con.print("[yellow]Die Ueberschriften sind leer und muessen ausgefuellt "
              "werden.[/yellow] Danach: `slideshow build`")
    return 0


def _chapters_aus_luecken(args, project: Project, manifest) -> tuple[str, str, str]:
    """Grenzen aus Zeitluecken und Ortsspruengen — der Normalfall."""
    from .chapters import (GAP_PLACE_HOURS, JUMP_KM, ORDER_VORBEHALT,
                           coverage_note, dump_chapters_yaml, first_image_id,
                           suggest)
    from .order import is_chronological

    vorschlaege = suggest(manifest,
                          min_gap_hours=(args.min_gap if args.min_gap is not None
                                         else GAP_PLACE_HOURS),
                          min_jump_km=(args.min_jump if args.min_jump is not None
                                       else JUMP_KM))
    # Ohne die Reihenfolge nennte der Auftakt-Kommentar das chronologisch erste
    # Bild — bei manueller Sortierung schlicht das falsche. Ein Fehler in der
    # Datei darf hier aber nicht abbrechen: gebraucht wird sie fuer einen
    # *Kommentar*, und wer gerade sortiert, hat regelmaessig einen Zwischenstand
    # liegen, den `build` zu Recht ablehnen wuerde.
    try:
        reihenfolge, _notes, _olist = _load_order(project, None, manifest)
    except SlideshowError as exc:
        reihenfolge = None
        console().print(f"[yellow]order.yaml bleibt aussen vor:[/] {exc}")
    # Bei manueller Sortierung sind die Nachbarn im Film thematisch benachbart,
    # nicht zeitlich — die Zeitluecken-Heuristik unten rechnet dann an der
    # fertigen Abfolge vorbei. Das muss in der Datei stehen, nicht nur hier.
    vorbehalt = (ORDER_VORBEHALT if reihenfolge
                 and not is_chronological(manifest, reihenfolge) else "")
    text = dump_chapters_yaml(vorschlaege, hinweis=coverage_note(manifest),
                              auftakt=not args.no_auftakt,
                              auftakt_bild=first_image_id(manifest, reihenfolge),
                              vorbehalt=vorbehalt)

    stark = sum(1 for v in vorschlaege if v.staerke == "stark")
    schwach = len(vorschlaege) - stark
    bericht = (f"{stark} Grenzen"
               + (f", {schwach} schwaechere als Kommentar" if schwach else ""))
    return (text, bericht, coverage_note(manifest))


def _chapters_aus_gruppen(args, project: Project, manifest) -> tuple[str, str, str]:
    """Ein Kapitel je Block aus ``order.yaml``.

    Fuer den Film, dessen Abschnitte beim Sortieren gezogen wurden — ein Kapitel
    je Reiseabschnitt ueber mehrere Tage. Die Zeitluecken-Heuristik hat dort
    nichts beizutragen: sie misst zwischen *zeitlichen* Nachbarn, im Film stehen
    aber thematische, und ihre Vorschlaege wirft man hinterher weg.

    Anders als beim Auftakt-Kommentar in :func:`_chapters_aus_luecken` ist ein
    Fehler in ``order.yaml`` hier kein Schoenheitsfehler, sondern nimmt der Datei
    den Inhalt — er wird deshalb durchgereicht statt gemeldet.
    """
    from .chapters import dump_group_chapters_yaml, first_image_id
    from .order import group_anchors

    if args.min_gap is not None or args.min_jump is not None:
        raise SlideshowError(
            "--from-groups nimmt die Grenzen aus order.yaml; --min-gap und "
            "--min-jump stellen die Zeitluecken-Erkennung ein und blieben dabei "
            "wirkungslos. Entweder das eine oder das andere.")

    ids, _notes, olist = _load_order(project, None, manifest)
    if olist is None:
        raise SlideshowError(
            f"--from-groups braucht order.yaml — daraus werden die Kapitel, und in "
            f"{project.root} liegt keine. Erzeugen: `slideshow order --by place` "
            f"(ein Block je Ort, meist mehrere Tage) oder `--by day`.")
    anker = group_anchors(olist, manifest, ids)
    if not anker:
        raise SlideshowError(
            "order.yaml nennt keine benannten Bloecke. Die flache Form "
            "`order: [...]` kennt keine Gruppen, und ohne Gruppen gibt es nichts "
            "zu verankern — `slideshow order --by day` erzeugt die gruppierte "
            "Form. Ohne Gruppen bleibt `slideshow chapters` ohne den Schalter.")

    text = dump_group_chapters_yaml(anker, auftakt=not args.no_auftakt,
                                    auftakt_bild=first_image_id(manifest, ids))
    mehrtaegig = sum(1 for a in anker if a.mehrtaegig)
    bericht = (f"{len(anker)} Bloecke aus order.yaml"
               + (f", davon {mehrtaegig} ueber mehrere Tage" if mehrtaegig else ""))
    signal = ("Anker `group:` — sie ueberleben jedes weitere Umsortieren "
              "innerhalb der Bloecke"
              + (f"; {mehrtaegig} Kapitel bekommen `subtitle: null`, weil `auto` "
                 f"dort nur den ersten Tag naehme" if mehrtaegig else ""))
    return (text, bericht, signal)


def cmd_order(args, project: Project) -> int:
    from .models import Manifest
    from .order import (dump_order_yaml, group_media, load_order,
                        update_order_text)

    manifest = Manifest.load(Path(args.manifest) if args.manifest else project.manifest)
    out = Path(args.output) if args.output else (project.root / "order.yaml")
    con = console()

    if args.apply:
        if not out.exists():
            raise SlideshowError(
                f"{out} gibt es noch nicht — `--apply` aendert eine bestehende "
                f"Auswahl. Erst `slideshow select`, dann den Bogen ansehen.")
        text, meldungen = _apply_changes(args, out, manifest)
    elif args.update:
        if not out.exists():
            raise SlideshowError(f"{out} gibt es noch nicht — `--update` pflegt eine "
                                 f"bestehende Datei nach. Ohne den Schalter wird sie "
                                 f"neu erzeugt.")
        olist, _zeilen = load_order(out)
        text, meldungen = update_order_text(out.read_text(encoding="utf-8"),
                                            olist, manifest)
    else:
        bloecke = group_media(manifest, by=args.by)
        text = dump_order_yaml(bloecke, manifest, by=args.by)
        meldungen = [f"{len(bloecke)} Gruppen, "
                     f"{sum(len(b.items) for b in bloecke)} Medien"]

    if args.dry_run:
        con.print(text)
        return 0
    # Wie bei den Kapiteln: die Datei ist Handarbeit, sobald einmal sortiert
    # wurde. Sie kommentarlos zu ueberschreiben hiesse, die Sortierung zu
    # loeschen — dafuer gibt es `--update`, und erst danach `--force`.
    if out.exists() and not (args.force or args.update or args.apply):
        raise SlideshowError(
            f"{out} gibt es bereits und enthaelt die Sortierung. `--update` pflegt "
            f"neues Material ein und behaelt sie, `--force` wirft sie weg, "
            f"`--dry-run` zeigt den Vorschlag nur an.")
    out.write_text(text, encoding="utf-8")

    con.print(f"Reihenfolge: {out}")
    for m in meldungen:
        con.print(f"  [dim]{m}[/dim]")
    if args.apply:
        con.print("Den Bogen neu erzeugen, um den Stand zu sehen: "
                  "`slideshow sheet`")
    elif not args.update:
        con.print("[yellow]Die Reihenfolge ist noch chronologisch.[/yellow] Zum "
                  "Sortieren die Zeilen verschieben. Danach: `slideshow build`")
    return 0


def _apply_changes(args, out: Path, manifest) -> tuple[str, list[str]]:
    """Die Aenderungsliste aus dem Kontaktbogen einlesen und anwenden.

    Zwei Wege hinein, weil zwei Arbeitsweisen: ``--apply auswahl.txt`` fuer die
    Datei, die der Knopf "Aenderungen speichern" ablegt, und ``--apply -`` fuer
    das, was in der Zwischenablage liegt. Der zweite Weg ist der bequemere bei
    einer Handvoll Tausche, der erste der einzige bei hundertsechzig.
    """
    from .order import apply_changes
    from .sheet import parse_changes

    con = console()
    if args.apply == "-":
        con.print("[dim]Zeilen aus dem Kontaktbogen einfuegen, dann Strg+Z und "
                  "Enter (Windows) bzw. Strg+D (Linux).[/dim]")
        roh = sys.stdin.read()
    else:
        pfad = Path(args.apply)
        if not pfad.exists():
            raise SlideshowError(
                f"Aenderungsliste fehlt: {pfad}. Sie entsteht im Kontaktbogen "
                f"ueber den Knopf 'Aenderungen speichern' — oder `--apply -` "
                f"nimmt sie aus der Zwischenablage entgegen.")
        roh = pfad.read_text(encoding="utf-8")

    rein, raus, unklar = parse_changes(roh)
    if unklar:
        stellen = "; ".join(f"Zeile {nr}: {z!r}" for nr, z in unklar[:5])
        mehr = f" (+{len(unklar) - 5} weitere)" if len(unklar) > 5 else ""
        raise SlideshowError(
            f"{len(unklar)} Zeilen der Aenderungsliste sind weder ein Eintrag "
            f"noch ein Kommentar: {stellen}{mehr}. Erwartet wird, was der "
            f"Kontaktbogen ausgibt — `      - img_042` zum Hereinnehmen, "
            f"`      #  raus: img_042` zum Herausnehmen.")
    if not (rein or raus):
        raise SlideshowError(
            "Die Aenderungsliste nennt kein einziges Medium. Im Bogen wird durch "
            "Anklicken markiert; erst dann fuellt sich der Zettel.")

    con.print(f"Aenderungen: {len(rein)} herein, {len(raus)} hinaus")
    return apply_changes(out.read_text(encoding="utf-8"), manifest, rein, raus,
                         quelle=str(out))


def cmd_select(args, project: Project) -> int:
    from .models import Manifest
    from .select import dump_selection_yaml, select_media

    manifest = Manifest.load(Path(args.manifest) if args.manifest else project.manifest)
    out = Path(args.output) if args.output else (project.root / "order.yaml")
    con = console()

    ziel, herkunft = _zielzahl(args, project, manifest)
    sel = select_media(
        manifest, count=ziel, seed=args.seed, by=args.by, gap=args.burst_gap,
        alpha=args.day_weight, min_per_day=args.min_per_day,
        max_share=args.max_share, min_long_edge=args.min_long_edge,
        rating_min=args.rating_min, keep_clips=not args.no_keep_clips,
        max_portrait=args.max_portrait)
    text = dump_selection_yaml(sel, manifest)

    if args.dry_run:
        # Anders als bei `order --dry-run` steht hier nicht nur der Dateiinhalt.
        # Ein Vorschlag wird angesehen, um ihn zu beurteilen, und die Zahlen und
        # Hinweise sind genau das Urteil — sie erst beim Schreiben zu zeigen
        # hiesse, sie ausgerechnet dann zu verschweigen, wenn man sie braucht.
        _print_quote(sel, manifest)
        for m in sel.meldungen:
            con.print(f"  [yellow]{m}[/yellow]")
        con.print("")
        con.print(text)
        return 0
    # Dieselbe Vorsicht wie bei `order`: sobald einmal ausgewaehlt oder
    # sortiert wurde, ist die Datei Handarbeit. Ein zweites `select` wuerfelt
    # neu und wirft beides weg — das darf nicht beilaeufig passieren.
    if out.exists() and not args.force:
        raise SlideshowError(
            f"{out} gibt es bereits und enthaelt Auswahl und Sortierung. "
            f"`--force` wuerfelt neu und wirft sie weg, `--dry-run` zeigt den "
            f"Vorschlag nur an. Um beim selben Vorschlag zu bleiben und nur "
            f"neues Material einzupflegen: `slideshow order --update`.")
    out.write_text(text, encoding="utf-8")

    con.print(f"Auswahl: {out}")
    con.print(f"  [dim]{len(sel.ids)} von {sel.gesamt} Medien · Zielzahl {ziel} "
              f"({herkunft}) · Seed {sel.seed}[/dim]")
    _print_quote(sel, manifest)
    for m in sel.meldungen:
        con.print(f"  [yellow]{m}[/yellow]")
    con.print("[yellow]Die Auswahl ist ein Vorschlag.[/yellow] Sie kennt keine "
              "Bildinhalte — ansehen mit `slideshow sheet`, tauschen durch "
              "Zeilentausch in der Datei. Danach: `slideshow preprocess`")
    if args.sheet:
        # Der Bogen wird bewusst nicht aus `sel` gebaut, sondern aus der eben
        # geschriebenen Datei: sonst gaebe es zwei Wege zum selben Bild, und
        # nur einer davon liefe je wieder. `--sheet` ist eine Abkuerzung fuer
        # den zweiten Aufruf, nicht ein zweiter Codepfad.
        con.print("")
        return cmd_sheet(_SheetArgs(args, order=str(out)), project)
    return 0


class _SheetArgs:
    """Die Schalter, die ``select --sheet`` an ``cmd_sheet`` weiterreicht.

    Argparse-Namespaces zweier Subkommandos zu mischen ginge auch, faellt aber
    beim naechsten neuen Schalter um. Hier steht schwarz auf weiss, was der
    Bogen aus dem `select`-Aufruf uebernimmt (Projekt, Manifest, `--dry-run`)
    und was seine Vorgaben sind.
    """

    def __init__(self, args, *, order: str) -> None:
        self.order = order
        self.manifest = args.manifest
        self.output = None
        self.thumb = THUMB_SIZE
        self.jobs = None
        self.alles = True
        self.selected = False
        self.force = False
        self.dry_run = args.dry_run
        self.quiet = args.quiet


def cmd_sheet(args, project: Project) -> int:
    from .models import Manifest
    from .order import load_order, resolve_order
    from .sheet import (dump_sheet_html, selection_from_order, sheet_media,
                        thumbnails)

    manifest = Manifest.load(Path(args.manifest) if args.manifest else project.manifest)
    con = console()

    pfad = Path(args.order) if args.order else (project.root / "order.yaml")
    if not pfad.exists():
        raise SlideshowError(
            f"{pfad} gibt es nicht. Der Kontaktbogen zeigt eine *Auswahl*, und "
            f"die steht in order.yaml — `slideshow select` erzeugt sie, "
            f"`slideshow order` eine Reihenfolge ohne Abwahl.")

    text = pfad.read_text(encoding="utf-8")
    olist, zeilen = load_order(pfad)
    ids, meldungen = resolve_order(manifest, olist, quelle=str(pfad), zeilen=zeilen)
    sel = selection_from_order(manifest, olist, text, ids=ids)

    out = Path(args.output) if args.output else (project.root / "contact.html")
    medien = sheet_media(sel, manifest, nur_auswahl=args.selected)
    thumbs, stats = thumbnails(project, medien, size=args.thumb, force=args.force,
                               jobs=args.jobs, dry=DryRun(enabled=args.dry_run))
    html = dump_sheet_html(sel, thumbs, manifest, base=out.parent,
                           nur_auswahl=args.selected, thumb=args.thumb,
                           quelle=pfad.name)

    if args.dry_run:
        con.print(f"Kontaktbogen: {out}  ({len(medien)} Kacheln, "
                  f"{len(html) / 1024:.0f} KB)")
        return 0
    out.write_text(html, encoding="utf-8")

    con.print(f"Kontaktbogen: {out}")
    con.print(f"  [dim]{len(sel.ids)} gewaehlt, {len(medien)} Kacheln, "
              f"{len(html) / 1024:.0f} KB[/dim]")
    con.print(f"  [dim]Thumbnails: {stats.aus_vorschau} aus der EXIF-Vorschau, "
              f"{stats.skaliert} skaliert, {stats.aus_cache} aus dem Cache "
              f"({stats.sekunden:.1f} s)[/dim]")
    if stats.fehlend:
        con.print(f"  [yellow]{len(stats.fehlend)} ohne Thumbnail: "
                  f"{', '.join(stats.fehlend[:8])}[/yellow]")
    for m in meldungen + sel.meldungen:
        con.print(f"  [yellow]{m}[/yellow]")
    con.print(f"[yellow]Der Bogen schreibt nichts.[/yellow] Ein Klick markiert "
              f"einen Tausch, der Knopf legt die Zeilen in die Zwischenablage — "
              f"eintragen von Hand in {pfad.name}.")
    return 0


def _zielzahl(args, project: Project, manifest) -> tuple[int, str]:
    """Wie viele Medien gewaehlt werden — und woher die Zahl kommt.

    ``auto`` liest sie aus der Regionenkarte: die Summe der Slots *ist* die
    Zahl der Bilder, die die Musik traegt. Sie einzutippen hiesse, eine
    Rechnung im Kopf zu machen, die ``slot_capacity`` schon kann.

    Abgezogen wird, was keine Standbilder belegen: jede Titelfolie nimmt einen
    Slot weg, und ein Clip, der laenger laeuft als ein Standbild, mehrere.
    Ohne diese Reserve waehlte man genau so viele Bilder, wie Slots da sind,
    und jede Kapitelfolie schoebe eines wieder heraus.
    """
    import yaml
    from .models import BeatMap, Defaults, Region
    from .planner import slot_capacity

    if str(args.count).lower() != "auto":
        try:
            n = int(args.count)
        except ValueError:
            raise SlideshowError(f"--count erwartet eine Zahl oder 'auto', "
                                 f"nicht {args.count!r}") from None
        return (n, "angegeben")

    bpath = project.root / "beats.yaml"
    if not bpath.exists():
        raise SlideshowError(
            f"Ohne {bpath} weiss `--count auto` nicht, wie viele Bilder die Musik "
            f"traegt. Entweder `slideshow beats` zuerst laufen lassen oder die "
            f"Zahl angeben: `slideshow select --count 200`.")
    raw = yaml.safe_load(bpath.read_text(encoding="utf-8")) or {}
    regions = [Region.model_validate(r) for r in raw.get("regions", [])]
    beatmap = BeatMap(version=raw.get("version", 1), regions=regions)
    defaults = Defaults()

    reserve, teile = 0, []
    kapitel = project.root / "chapters.yaml"
    if kapitel.exists():
        from .models import ChapterList
        n = len(ChapterList.load(kapitel).chapters)
        if n:
            reserve += n
            teile.append(f"{n} Titelfolien")
    mehrbedarf = _clip_mehrbedarf(manifest, beatmap, defaults)
    if mehrbedarf:
        reserve += mehrbedarf
        teile.append(f"{mehrbedarf} Slots fuer laengere Clips")

    gesamt = slot_capacity(beatmap.regions, defaults)
    ziel = slot_capacity(beatmap.regions, defaults, reserve=reserve)
    herkunft = f"{gesamt} Slots aus beats.yaml"
    if teile:
        herkunft += " abzueglich " + " und ".join(teile)
    if ziel <= 0:
        raise SlideshowError(
            f"Die Regionenkarte gibt {gesamt} Slots her, und {reserve} davon sind "
            f"schon vergeben. Fuer Bilder bleibt nichts uebrig — laengere Musik, "
            f"weniger Kapitel oder ein kleineres `beats_per_still`.")
    return (ziel, herkunft)


def _clip_mehrbedarf(manifest, beatmap, defaults) -> int:
    """Wie viele *zusaetzliche* Slots die Clips ueber ihre Standlaenge hinaus
    belegen. Ein 30-Sekunden-Clip frisst bei 4 s Standzeit sieben Bilder."""
    from .planner import standard_slot

    takt = standard_slot(beatmap.regions, defaults)
    if takt <= 0:
        return 0
    mehr = 0.0
    for m in manifest.media:
        if m.kind != "clip" or m.clip is None:
            continue
        dauer = m.clip.cache_duration or m.clip.effective_duration or m.clip.duration
        mehr += max(0.0, (dauer - takt) / takt)
    return int(round(mehr))


def _print_quote(sel, manifest) -> None:
    """Die Verteilung als Tabelle — die eigentliche Aussage der Auswahl.

    Ohne sie ist das Ergebnis eine Zahl, mit ihr sieht man, ob die Daempfung
    passt: welcher Tag wie viel Material hatte und wie viel davon in den Film
    kommt.
    """
    from rich.table import Table
    if not sel.quote:
        return
    t = Table(title="Auswahl je Gruppe", title_justify="left")
    t.add_column("Gruppe"); t.add_column("Aufnahmen", justify="right")
    t.add_column("Trauben", justify="right"); t.add_column("gewaehlt", justify="right")
    t.add_column("Quote", justify="right")
    for schluessel, (n, trauben, aufnahmen) in sel.quote.items():
        anteil = f"{100.0 * n / aufnahmen:.0f} %" if aufnahmen else "-"
        t.add_row(str(schluessel), str(aufnahmen), str(trauben), str(n), anteil)
    console().print(t)


def _load_chapters(project: Project, angabe: str | None, defaults) -> list:
    """Kapitel laden — ausdruecklich angegeben oder als Projektdatei gefunden.

    Ein *ausdruecklich* genannter Pfad, den es nicht gibt, ist ein Fehler; die
    stillschweigend gefundene ``chapters.yaml`` ist eine Bequemlichkeit und
    darf fehlen.
    """
    from .models import ChapterList
    from .titles import find_font

    if angabe:
        pfad = Path(angabe)
        if not pfad.exists():
            raise SlideshowError(f"Kapiteldatei fehlt: {pfad}")
    else:
        pfad = project.root / "chapters.yaml"
        if not pfad.exists():
            return []

    kapitel = ChapterList.load(pfad).chapters
    if kapitel:
        # Ohne Schrift gibt es keine Folie. Das jetzt zu melden ist der
        # Unterschied zwischen einer Zeile mit Installationsbefehl und einem
        # Traceback nach dem halben Rendern (Abnahmekriterium T8).
        schrift = find_font(defaults.title.font)
        console().print(f"Kapitel: {pfad}  ({len(kapitel)} Titelfolien, "
                        f"Schrift {schrift})")
    return kapitel


def _load_order(project: Project, angabe: str | None, manifest, *,
                titel: str = "Reihenfolge"
                ) -> tuple[list[str] | None, list[str], object | None]:
    """Reihenfolge laden und aufloesen — dieselbe Regel wie bei den Kapiteln.

    Ein *ausdruecklich* genannter Pfad, den es nicht gibt, ist ein Fehler; die
    stillschweigend gefundene ``order.yaml`` ist eine Bequemlichkeit und darf
    fehlen. Ohne Datei bleibt es bei der chronologischen Abfolge.
    """
    from .order import load_order, resolve_order

    if angabe:
        pfad = Path(angabe)
        if not pfad.exists():
            raise SlideshowError(f"Reihenfolgedatei fehlt: {pfad}")
    else:
        pfad = project.root / "order.yaml"
        if not pfad.exists():
            return (None, [], None)

    olist, zeilen = load_order(pfad)
    ids, meldungen = resolve_order(manifest, olist, quelle=str(pfad), zeilen=zeilen)
    gruppen = len([g for g in olist.blocks if g.items])
    console().print(f"{titel}: {pfad}  ({len(ids)} von {len(manifest.media)} "
                    f"Medien" + (f", {gruppen} Gruppen" if olist.groups else "") + ")")
    return (ids, meldungen, olist)


def _print_coverage(cov, defaults, plan) -> None:
    """Laufzeit-Vorabpruefung nach 6.5."""
    from rich.table import Table
    t = Table(title="Laufzeit-Vorabpruefung (6.5)", title_justify="left")
    spalten = ["#", "Typ", "Start", "Dauer", "BPM", "Kapazitaet", "Bilder", "Clips"]
    # Die Titelspalte nur zeigen, wenn es Titel gibt — sonst steht in jeder
    # Zeile eine Null, die nichts erklaert.
    mit_titeln = bool(cov.titles)
    if mit_titeln:
        spalten.append("Titel")
    for c in spalten:
        t.add_column(c, justify="right" if c != "Typ" else "left")
    for r in cov.per_region:
        zeile = [str(r["index"]), r["type"], f"{r['start']:.2f}", f"{r['seconds']:.2f}",
                 f"{r['bpm']:.1f}" if r["bpm"] else "-", str(r["capacity"]),
                 str(r["stills"]), str(r["clips"])]
        if mit_titeln:
            zeile.append(str(r.get("titles", 0)))
        t.add_row(*zeile)
    con = console()
    con.print(t)
    if cov.audio_seconds <= 0:
        ton = "ohne Tonspur"
    elif abs(cov.audio_seconds - cov.music_seconds) < 0.05:
        ton = f"Musik {cov.audio_seconds:.2f} s"
    else:
        wie = "gekuerzt" if cov.audio_seconds > cov.music_seconds else "stumm verlaengert"
        ton = f"Musik {cov.audio_seconds:.2f} s -> {wie}"
    con.print(f"Laufzeit {cov.music_seconds:.2f} s | {ton} | "
              f"geplant {cov.planned_seconds:.2f} s | "
              f"{cov.stills} Bilder, {cov.clips} Clips"
              + (f", {cov.titles} Titelfolien" if cov.titles else ""))
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

    _titelassets(project, edit, manifest, dry=DryRun(enabled=args.dry_run))
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


def _titelassets(project: Project, edit, manifest, *, dry=None) -> None:
    """Titelfolien backen, bevor irgendetwas sie zu lesen versucht.

    Steht vor ``check_sources_exist``: das Asset ist ein Erzeugnis, kein
    Material, und "Datei fehlt" waere hier die falsche Diagnose.
    """
    from .preprocess import ensure_title_assets

    stats = ensure_title_assets(project, edit, manifest, dry=dry)
    if stats.erzeugt or stats.aus_cache:
        console().print(f"Titelfolien: {stats.erzeugt} erzeugt, "
                        f"{stats.aus_cache} aus Cache")
    for w in stats.warnungen:
        console().print(f"  [yellow]{w}[/]")


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

    _titelassets(project, edit, manifest, dry=DryRun(enabled=args.dry_run))
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
    "preprocess": cmd_preprocess, "beats": cmd_beats, "chapters": cmd_chapters,
    "order": cmd_order, "select": cmd_select, "sheet": cmd_sheet,
    "build": cmd_build,
    "render": cmd_render, "export-mlt": cmd_export_mlt, "selftest": cmd_selftest,
}


# --------------------------------------------------------------------------
# Wegweiser: was als naechstes sinnvoll ist
#
# Abgeleitet wird das aus dem *Zustand* des Projekts, nicht aus dem gerade
# gelaufenen Kommando. Wer eine Phase wiederholt oder ueberspringt, bekommt
# trotzdem den passenden Vorschlag, und nach einem `doctor` mitten im Projekt
# steht nicht wieder `probe` da.
# --------------------------------------------------------------------------

def _praefix(args, project: Project) -> str:
    """Der Aufruf so, wie ihn der Nutzer wiederholen kann — inklusive
    ``--project``, sonst zeigt der Vorschlag auf das falsche Verzeichnis.

    Zeigt es ohnehin aufs aktuelle Verzeichnis, bleibt es weg: der Vorschlag
    soll kurz genug sein, dass man ihn ohne Zeilenumbruch liest.
    """
    if not args.project:
        return "slideshow"
    try:
        if project.root == Path.cwd().resolve():
            return "slideshow"
    except OSError:
        pass
    p = str(args.project)
    return f'slideshow --project "{p}"' if " " in p else f"slideshow --project {p}"


def _zitiere(pfad: Path) -> str:
    name = pfad.name
    return f'"{name}"' if " " in name else name


def _tonspur_kandidaten(project: Project, manifest) -> list[Path]:
    """Audiodateien, die neben dem Material liegen."""
    from .probe import AUDIO_EXT
    for m in manifest.media:
        p = project.abs(m.path)
        if not p.exists():
            continue
        return sorted(q for q in p.parent.iterdir()
                      if q.is_file() and q.suffix.lower() in AUDIO_EXT)
    return []


#: Ab wie vielen Medien der Wegweiser zur Auswahl raet, statt gleich alles zu
#: normalisieren. Grob gegriffen: die genaue Grenze kennt erst `select --count
#: auto` aus der Regionenkarte, und die gibt es hier noch nicht.
SELECT_HINWEIS_AB = 300


def _tonspur_schritt(project: Project, manifest, ruf: str) -> list[str]:
    """Vorschlag, die Musik zu setzen — oder nichts, wenn sie schon da ist."""
    if manifest.audio.file or (project.cache / "mix.flac").exists():
        return []
    kandidaten = _tonspur_kandidaten(project, manifest)
    if not kandidaten:
        return []
    dateien = " ".join(_zitiere(k) for k in kandidaten[:3])
    return [f"{ruf} audio {dateien}",
            f"[dim]oder ohne Musik weiter mit: {ruf} beats[/dim]"]


def _naechster_schritt(project: Project, args) -> list[str]:
    """Erste Phase, deren Voraussetzung noch fehlt — als fertige Kommandozeile."""
    from .models import Manifest
    ruf = _praefix(args, project)

    if not project.manifest.exists():
        return [f"{ruf} probe <material-verzeichnis>"]

    try:
        manifest = Manifest.load(project.manifest)
    except Exception:                              # noqa: BLE001 - der Wegweiser
        return []                                  # darf nie das Kommando kippen

    beats = project.root / "beats.yaml"
    unbearbeitet = manifest.media and not any(m.cache_path for m in manifest.media)

    # Bei viel Material kehrt sich die uebliche Reihenfolge um: erst auswaehlen,
    # dann normalisieren. `preprocess` verarbeitet sonst tausend Bilder auf 7680
    # px Langkante, von denen zweihundert im Film landen — Stunden Rechenzeit
    # fuer Material, das niemand sieht. Und `select --count auto` braucht die
    # Regionenkarte, also muss `beats` davor.
    zuviel = len(manifest.media) > SELECT_HINWEIS_AB
    if zuviel and unbearbeitet and not (project.root / "order.yaml").exists():
        if beats.exists():
            return [f"{ruf} select",
                    f"[dim]{len(manifest.media)} Medien sind mehr, als ein Film "
                    f"traegt — auswaehlen, bevor `preprocess` alle normalisiert[/dim]"]
        return _tonspur_schritt(project, manifest, ruf) or [
            f"{ruf} beats",
            f"[dim]danach `{ruf} select`: {len(manifest.media)} Medien sind mehr, "
            f"als ein Film traegt[/dim]"]

    if unbearbeitet:
        return [f"{ruf} preprocess"]

    if not beats.exists():
        return _tonspur_schritt(project, manifest, ruf) or [f"{ruf} beats"]

    if not project.edit.exists():
        schritte = [f"{ruf} build{_build_parameter(project, manifest, beats)}"]
        # Kapitel sind optional; der Hinweis kommt nur, solange es weder eine
        # chapters.yaml noch eine Edit-List gibt — also genau einmal, an der
        # Stelle, an der er noch etwas aendert.
        if not (project.root / "chapters.yaml").exists():
            schritte.append(f"[dim]oder vorher Kapitel vorschlagen lassen: "
                            f"{ruf} chapters[/dim]")
        return schritte

    if not (project.out / "master.mp4").exists():
        return [f"{ruf} render"]

    return [f"[dim]Fertig. Feinschliff von Hand: {ruf} export-mlt[/dim]"]


def _build_parameter(project: Project, manifest, beats: Path) -> str:
    """``--still-seconds`` vorschlagen, wenn der Standardtakt nicht aufgeht.

    Nur fuer Karten ganz ohne Beat-Raster: dort taktet ``still_seconds``. Wo
    ein Raster liegt, ist ``beats_per_still`` der Hebel, und den kennt der
    Nutzer erst nach der Deckungspruefung in `build`.
    """
    import yaml
    from .models import Defaults
    try:
        roh = yaml.safe_load(beats.read_text(encoding="utf-8")) or {}
        regionen = roh.get("regions") or []
        if not regionen or any(r.get("type") == "beat" for r in regionen):
            return ""
        dauer = float(roh.get("audio", {}).get("duration") or 0.0)
    except Exception:                              # noqa: BLE001
        return ""

    n = sum(1 for m in manifest.media if m.cache_path)
    if n < 1 or dauer <= 0:
        return ""
    passend = dauer / n
    if abs(passend - Defaults().still_seconds) <= Defaults().still_seconds:
        return ""
    # Nur vorschlagen, was auch ein Mensch vorschlagen wuerde. Drei Fotos unter
    # einem 6:32-Stueck ergaeben rechnerisch 131 s je Bild — richtig gerechnet
    # und trotzdem Unsinn. In solchen Faellen bleibt der Vorschlag beim nackten
    # `build`; dessen Deckungspruefung nennt dann alle drei Auswege.
    if not 2.0 <= passend <= 30.0:
        return ""
    return f" --still-seconds {passend:.0f}"


def _zeige_naechsten_schritt(project: Project, args) -> None:
    if args.quiet or args.dry_run:
        return
    try:
        zeilen = _naechster_schritt(project, args)
    except Exception:                              # noqa: BLE001
        return
    if not zeilen:
        return
    con = console()
    con.print("\n[bold]Nächster Schritt:[/bold]")
    for z in zeilen:
        # soft_wrap: ein Vorschlag, den die Konsole hart umbricht, laesst sich
        # nicht mehr als eine Zeile kopieren — genau dafuer steht er da.
        con.print(f"  {z}" if z.startswith("[dim]") else f"  [cyan]{z}[/cyan]",
                  soft_wrap=True)


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
        rc = _COMMANDS[args.command](args, project)
        if rc == 0:
            _zeige_naechsten_schritt(project, args)
        return rc
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
