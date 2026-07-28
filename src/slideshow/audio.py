"""Phase 2b — Audio (Abschnitte 5.3 und 5.4).

``slideshow audio TRACK1 TRACK2 ... -o cache/mix.flac [--gap 6] [--xfade 3]``

Baut den Mix, den alle folgenden Phasen voraussetzen. Zweipass-``loudnorm``
laeuft *pro Track vor* dem Mischen, damit ein leiser Track nicht leise bleibt.
Die Track-Grenzen landen im Manifest — ``beats`` nutzt sie als Seeds fuer die
Regionsgrenzen, statt sie rein akustisch raten zu muessen.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import SlideshowError
from .models import AudioInfo, TrackBound
from .proc import DryRun, ffprobe_json, run

log = logging.getLogger("slideshow.audio")

#: Zielpegel nach 5.3.
TARGET_I = -16.0
TARGET_TP = -1.5
TARGET_LRA = 11.0

SAMPLE_RATE = 48000


@dataclass
class LoudnormMeasurement:
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float

    def second_pass_filter(self) -> str:
        return (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
                f":measured_I={self.input_i}:measured_TP={self.input_tp}"
                f":measured_LRA={self.input_lra}:measured_thresh={self.input_thresh}"
                f":offset={self.target_offset}:linear=true:print_format=summary")


def measure_loudness(path: Path) -> LoudnormMeasurement:
    """Erster loudnorm-Pass. Die Messwerte kommen als JSON auf stderr."""
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
               "-af", f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
                      f":print_format=json",
               "-f", "null", "-"], check=False, timeout=1800)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr or "", re.S)
    if not m:
        raise SlideshowError(
            f"loudnorm-Messung fuer {path.name} lieferte keine Werte.\n"
            f"{res.stderr_tail(10)}")
    data = json.loads(m.group(0))

    def num(key: str, default: float) -> float:
        try:
            v = float(data[key])
        except (KeyError, TypeError, ValueError):
            return default
        # Bei sehr leisem oder stillem Material liefert loudnorm -inf.
        return default if v != v or v in (float("inf"), float("-inf")) else v

    return LoudnormMeasurement(
        input_i=num("input_i", TARGET_I), input_tp=num("input_tp", TARGET_TP),
        input_lra=num("input_lra", TARGET_LRA), input_thresh=num("input_thresh", -34.0),
        target_offset=num("target_offset", 0.0))


def normalize_track(src: Path, dst: Path, *, dry: DryRun | None = None) -> Path:
    """Zweipass-``loudnorm`` auf 48 kHz / Stereo / FLAC (5.3)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dry and dry.enabled:
        dry.record(["ffmpeg", "-i", str(src), "-af", "loudnorm(2-pass)", str(dst)])
        return dst
    meas = measure_loudness(src)
    log.info("%s: %.1f LUFS / %.1f dBTP -> %.1f LUFS", src.name,
             meas.input_i, meas.input_tp, TARGET_I)
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(src),
         "-af", meas.second_pass_filter(),
         "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "flac", str(dst)], timeout=1800)
    return dst


def audio_duration(path: Path) -> float:
    data = ffprobe_json(path)
    dur = (data.get("format") or {}).get("duration")
    if dur is None:
        stream = next((s for s in data.get("streams", [])
                       if s.get("codec_type") == "audio"), {})
        dur = stream.get("duration")
    try:
        return float(dur)
    except (TypeError, ValueError):
        raise SlideshowError(f"Dauer von {path} nicht ermittelbar") from None


def build_mix(tracks: list[Path], out: Path, *, gap: float | None = None,
              xfade: float | None = None, workdir: Path | None = None,
              dry: DryRun | None = None) -> AudioInfo:
    """Baut ``cache/mix.flac`` und liefert die Track-Grenzen (5.4)."""
    if not tracks:
        raise SlideshowError("Keine Tracks angegeben.")
    if gap is not None and xfade is not None:
        raise SlideshowError("--gap und --xfade schliessen einander aus.")
    if len(tracks) == 1:
        # Liefert der Nutzer einen fertigen Mix, wird dieser nur normalisiert.
        normalize_track(tracks[0], out, dry=dry)
        dur = 0.0 if (dry and dry.enabled) else audio_duration(out)
        return AudioInfo(file=out.name, duration=dur, sample_rate=SAMPLE_RATE,
                         tracks=[TrackBound(file=tracks[0].name, start=0.0, end=dur)])

    workdir = workdir or out.parent
    workdir.mkdir(parents=True, exist_ok=True)

    normalized: list[Path] = []
    for i, t in enumerate(tracks):
        n = workdir / f"_norm_{i:02d}.flac"
        normalize_track(t, n, dry=dry)
        normalized.append(n)

    if dry and dry.enabled:
        dry.record(["ffmpeg", "<mix>", *[str(n) for n in normalized], str(out)])
        return AudioInfo(file=out.name, sample_rate=SAMPLE_RATE)

    durations = [audio_duration(n) for n in normalized]
    if xfade is not None:
        bounds = _mix_crossfade(normalized, durations, out, xfade)
    else:
        bounds = _mix_gap(normalized, durations, out, gap or 0.0)

    total = audio_duration(out)
    info = AudioInfo(
        file=out.name, duration=total, sample_rate=SAMPLE_RATE,
        tracks=[TrackBound(file=tracks[i].name, start=round(s, 6), end=round(e, 6))
                for i, (s, e) in enumerate(bounds)])
    log.info("Mix: %d Tracks, %.2f s gesamt", len(tracks), total)
    for tb in info.tracks:
        log.info("  %-30s %8.2f - %8.2f s", tb.file, tb.start, tb.end)
    return info


def _mix_gap(files: list[Path], durations: list[float], out: Path,
             gap: float) -> list[tuple[float, float]]:
    """Tracks hintereinander, dazwischen echte Stille.

    Umgesetzt per ``adelay`` + ``amix`` statt ``concat``: die Offsets sind
    damit exakt und unabhaengig von Framing-Effekten des Concat-Filters.
    """
    offsets, cursor = [], 0.0
    for d in durations:
        offsets.append(cursor)
        cursor += d + gap

    parts, labels = [], []
    for i, off in enumerate(offsets):
        lbl = f"a{i}"
        delay = int(round(off * 1000))
        chain = f"[{i}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo"
        if delay > 0:
            chain += f",adelay={delay}:all=1"
        parts.append(f"{chain}[{lbl}]")
        labels.append(f"[{lbl}]")
    parts.append(f"{''.join(labels)}amix=inputs={len(files)}:normalize=0"
                 f":dropout_transition=0[out]")

    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    for f in files:
        cmd += ["-i", str(f)]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]",
            "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "flac", str(out)]
    run(cmd, timeout=3600)
    return [(off, off + d) for off, d in zip(offsets, durations)]


def _mix_crossfade(files: list[Path], durations: list[float], out: Path,
                   xfade: float) -> list[tuple[float, float]]:
    """Tracks mit Crossfade aneinander. Die Grenze liegt in der Fadenmitte."""
    for i, d in enumerate(durations):
        if d <= xfade:
            raise SlideshowError(f"Track {files[i].name} ist mit {d:.2f} s kuerzer als "
                                 f"der Crossfade ({xfade:.2f} s).")
    parts: list[str] = []
    prev = "[0:a]"
    parts.append(f"[0:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo[c0]")
    prev = "[c0]"
    for i in range(1, len(files)):
        parts.append(f"[{i}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo[n{i}]")
        label = "[out]" if i == len(files) - 1 else f"[c{i}]"
        parts.append(f"{prev}[n{i}]acrossfade=d={xfade}:c1=tri:c2=tri{label}")
        prev = label

    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    for f in files:
        cmd += ["-i", str(f)]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]",
            "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "flac", str(out)]
    run(cmd, timeout=3600)

    bounds, cursor = [], 0.0
    for i, d in enumerate(durations):
        start = cursor
        end = start + d
        bounds.append((start, end))
        cursor = end - xfade
    # Die Grenze zwischen zwei Tracks liegt in der Mitte der Blende.
    adjusted: list[tuple[float, float]] = []
    for i, (s, e) in enumerate(bounds):
        s2 = s if i == 0 else adjusted[i - 1][1]
        e2 = e - xfade / 2 if i < len(bounds) - 1 else e
        adjusted.append((s2, e2))
    return adjusted
