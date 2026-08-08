"""Synthetische Test-Fixtures (Abschnitt 12).

Die Abnahmekriterien sind ohne Material nicht pruefbar. ``slideshow selftest
--make-fixtures`` erzeugt deshalb Testmaterial mit *bekannter* Wahrheit —
insbesondere einen Klick-Track, dessen Beat-Zeitplan exakt bekannt ist, sodass
der Sync framegenau per ffprobe der Paket-Timestamps geprueft werden kann.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .errors import ExternalToolError
from .proc import ffprobe_json, have, run

log = logging.getLogger("slideshow.fixtures")

SR = 48000


# --------------------------------------------------------------------------
# Klick-Track
# --------------------------------------------------------------------------

@dataclass
class TrackSpec:
    bpm: float
    beats: int

    @property
    def beat_dur(self) -> float:
        return 60.0 / self.bpm

    @property
    def duration(self) -> float:
        return self.beats * self.beat_dur


@dataclass
class ClickTrack:
    """Der bekannte Zeitplan — Grundlage jeder Sync-Pruefung."""

    path: str
    lead_in: float
    gap: float
    tracks: list[dict] = field(default_factory=list)
    beat_times: list[float] = field(default_factory=list)
    duration: float = 0.0


def _click(dur: float, freq: float, sr: int = SR) -> np.ndarray:
    n = int(dur * sr)
    t = np.arange(n) / sr
    env = np.exp(-t * 45.0)
    tone = np.sin(2 * math.pi * freq * t) * env
    thump = np.sin(2 * math.pi * 62.0 * t) * np.exp(-t * 30.0) * 0.8
    return (tone * 0.6 + thump * 0.4).astype(np.float32)


def make_click_track(path: Path, *, tracks: list[TrackSpec], gap: float,
                     lead_in: float = 0.412, tail: float = 2.0) -> ClickTrack:
    """Zwei "Songs" mit unterschiedlichem Tempo, dazwischen echte Stille.

    Der Vorlauf ist bewusst < 1 s: damit greift die Ausnahme aus 6.0 (das erste
    Bild beginnt bei 0, es entsteht keine eigene free-Region).
    """
    import soundfile as sf

    total = lead_in + sum(t.duration for t in tracks) + gap * (len(tracks) - 1) + tail
    buf = np.zeros(int(total * SR), dtype=np.float32)

    beat_times: list[float] = []
    meta: list[dict] = []
    cursor = lead_in
    for i, spec in enumerate(tracks):
        freq = 1000.0 if i % 2 == 0 else 1400.0
        start = cursor
        for b in range(spec.beats):
            t = cursor + b * spec.beat_dur
            beat_times.append(round(t, 6))
            # Der erste Schlag im Takt lauter — das gibt der Downbeat-Erkennung
            # ueberhaupt eine Chance.
            amp = 0.95 if b % 4 == 0 else 0.55
            click = _click(min(0.12, spec.beat_dur * 0.9), freq) * amp
            s = int(round(t * SR))
            buf[s:s + len(click)] += click[:max(0, len(buf) - s)]
        cursor += spec.duration
        meta.append({"index": i, "bpm": spec.bpm, "beats": spec.beats,
                     "start": round(start, 6), "end": round(cursor, 6),
                     "beat_dur": round(spec.beat_dur, 9)})
        if i < len(tracks) - 1:
            cursor += gap

    stereo = np.stack([buf, buf], axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), stereo, SR, subtype="PCM_24")

    return ClickTrack(path=str(path), lead_in=lead_in, gap=gap, tracks=meta,
                      beat_times=beat_times, duration=round(total, 6))


# --------------------------------------------------------------------------
# Bilder
# --------------------------------------------------------------------------

def make_images(outdir: Path, *, count: int = 12, width: int = 1600,
                height: int = 1000) -> list[Path]:
    """Testbilder inkl. Portrait-JPEG mit EXIF-Orientation 6 und Verlaufsbild."""
    from PIL import Image, ImageDraw

    outdir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []

    for i in range(count):
        hue = int(255 * i / max(1, count - 1))
        img = Image.new("RGB", (width, height), (hue, 90, 255 - hue))
        d = ImageDraw.Draw(img)
        # Ein hartes Raster: Zittern und Positionsspruenge werden daran sichtbar.
        for x in range(0, width, 100):
            d.line([(x, 0), (x, height)], fill=(255, 255, 255), width=2)
        for y in range(0, height, 100):
            d.line([(0, y), (width, y)], fill=(255, 255, 255), width=2)
        d.rectangle([40, 40, 360, 200], fill=(0, 0, 0))
        d.text((60, 60), f"IMG {i:03d}", fill=(255, 255, 0))
        p = outdir / f"img_{i:03d}.jpg"
        img.save(p, quality=95)
        made.append(p)

    # Portrait mit EXIF-Orientation 6: gespeichert quer, angezeigt hoch.
    port = Image.new("RGB", (width, height), (30, 30, 30))
    d = ImageDraw.Draw(port)
    d.rectangle([0, 0, width, 80], fill=(220, 40, 40))       # "oben" nach Drehung
    d.text((60, 400), "PORTRAIT (orientation 6)", fill=(255, 255, 255))
    pp = outdir / "portrait_o6.jpg"
    port.save(pp, quality=95)
    if have("exiftool"):
        run(["exiftool", "-overwrite_original", "-q", "-Orientation=6", "-n", str(pp)],
            check=False)
    else:
        log.warning("exiftool fehlt — Portrait-Fixture ohne EXIF-Orientation")
    made.append(pp)

    # Verlaufsbild fuer den Banding-Test (Himmel).
    grad = Image.new("RGB", (width, height))
    px = grad.load()
    for y in range(height):
        v = y / (height - 1)
        px_row = (int(20 + 40 * v), int(60 + 90 * v), int(150 + 100 * v))
        for x in range(width):
            px[x, y] = px_row
    gp = outdir / "gradient_sky.png"
    grad.save(gp)
    made.append(gp)

    return made


# --------------------------------------------------------------------------
# Clips
# --------------------------------------------------------------------------

def _ffmpeg(args: list[str], *, desc: str) -> bool:
    res = run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=False, timeout=600)
    if not res.ok:
        log.warning("Fixture %s fehlgeschlagen: %s", desc, res.stderr_tail(4))
    return res.ok


def hat_transfer(path: Path, erwartet: str) -> bool:
    """Traegt die erzeugte Datei die Transferfunktion wirklich?

    Eine Fixture, die zusichert, was sie erzeugt, ist die Vorbedingung dafuer,
    dass ein roter Test etwas ueber das *Werkzeug* sagt. Genau hier fehlte die
    Zusicherung: unter ffmpeg 8.1.2 kamen die Ausgabeoptionen
    ``-color_trc``/``-color_primaries`` nicht mehr in der Datei an, der
    HLG-Clip war ungetaggt — und drei Tests zeigten monatelang auf
    ``detect_hdr`` statt auf die Fixture
    (``docs/briefing-hlg-ffmpeg8.md``, Befund 07.08.2026).

    Gemessen wird mit demselben Leser, den ``probe`` benutzt
    (:func:`slideshow.probe.color_transfer`) — eine Fixture, die sich mit
    anderen Augen prueft als der Produktionscode, prueft sich selbst. Der
    Import steht im Funktionskoerper, weil ``probe`` die halbe Toolchain
    nachzieht und ``make_click_track`` sie nicht braucht.
    """
    from .probe import color_transfer

    try:
        data = ffprobe_json(path)
    except (ExternalToolError, OSError) as exc:
        log.warning("Fixture %s: Farbtags nicht pruefbar (%s)", path.name, exc)
        return False
    stream = next((s for s in data.get("streams", [])
                   if s.get("codec_type") == "video"), {})
    ist = color_transfer(stream)
    if ist == erwartet:
        return True
    log.warning(
        "Fixture %s traegt color_transfer=%r statt %r — dieses ffmpeg schreibt "
        "die Farbtags nicht wie erwartet. Die Tests, die darauf bauen, pruefen "
        "sonst die Fixture statt das Werkzeug (docs/briefing-hlg-ffmpeg8.md).",
        path.name, ist or "(leer)", erwartet)
    return False


def make_clips(outdir: Path, *, seconds: float = 4.0) -> dict[str, Path]:
    """Clips via ``testsrc2`` — deckt die Faelle aus Abschnitt 12 ab."""
    outdir.mkdir(parents=True, exist_ok=True)
    made: dict[str, Path] = {}

    def src(fps: float, size: str = "1280x720") -> list[str]:
        return ["-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps:g}", "-t", str(seconds)]

    # --- schlichte CFR-Clips in den drei relevanten Raten ----------------
    for fps in (30, 60, 50):
        p = outdir / f"clip_{fps}p.mp4"
        if _ffmpeg([*src(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", str(p)], desc=f"{fps}p"):
            made[f"{fps}p"] = p

    # --- mit Rotations-Metadatum ----------------------------------------
    # ffmpeg 6 ignoriert `-metadata:s:v rotate=90` beim mov/mp4-Muxer. Die
    # Display-Matrix entsteht stattdessen ueber `-display_rotation` auf der
    # *Eingabe*seite, das per `-c copy` in den Output durchgereicht wird.
    if "30p" in made:
        p = outdir / "clip_rot90.mp4"
        if _ffmpeg(["-display_rotation", "90", "-i", str(made["30p"]), "-c", "copy", str(p)],
                   desc="rot90"):
            made["rot90"] = p

    # --- kuenstlich VFR: Frames unregelmaessig droppen, PTS behalten -----
    # Das simuliert, was Handys bei wenig Licht tun. -fps_mode passthrough ist
    # entscheidend, sonst regularisiert ffmpeg die Timestamps wieder.
    p = outdir / "clip_vfr.mp4"
    if _ffmpeg([*src(30), "-vf", "select='not(between(mod(n\\,30)\\,8\\,13))'",
                "-fps_mode", "passthrough", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", str(p)], desc="vfr"):
        made["vfr"] = p

    # --- HLG-getaggt ------------------------------------------------------
    # Getaggt wird ueber `setparams` an den *Frames*, nicht ueber die
    # Ausgabeoptionen `-color_trc`/`-color_primaries`: ffmpeg 8.1.2 uebernimmt
    # die nicht mehr in die Datei, und der Clip war dadurch still ungetaggt
    # (docs/briefing-hlg-ffmpeg8.md). `setparams` ist ausserdem derselbe Weg,
    # den `doctor` fuer seine zscale-Probe seit je benutzt — ein Idiom im Repo
    # statt zwei — und im Gegensatz zu `-x264-params` encoderunabhaengig.
    p = outdir / "clip_hlg.mp4"
    if _ffmpeg([*src(30), "-vf", "setparams=color_primaries=bt2020:"
                "color_trc=arib-std-b67:colorspace=bt2020nc",
                "-c:v", "libx264", "-pix_fmt", "yuv420p10le", "-preset", "veryfast",
                str(p)], desc="hlg"):
        hat_transfer(p, "arib-std-b67")
        made["hlg"] = p

    # --- HEVC 4:2:2 10 Bit (XAVC-HS-artig) --------------------------------
    p = outdir / "clip_hevc422.mp4"
    if _ffmpeg([*src(50), "-c:v", "libx265", "-pix_fmt", "yuv422p10le",
                "-x265-params", "log-level=error", "-preset", "ultrafast",
                "-tag:v", "hvc1", str(p)], desc="hevc422"):
        made["hevc422"] = p

    return made


# --------------------------------------------------------------------------
# Gesamtes Fixture-Set
# --------------------------------------------------------------------------

def make_fixtures(root: Path, *, with_clips: bool = True) -> dict:
    """Erzeugt das komplette Set und schreibt ``expected.json`` daneben.

    ``expected.json`` ist die Wahrheit, gegen die die pytest-Suite prueft.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    audio_dir = root / "audio"
    # Kriterien 9-10: zwei Songs mit 6 s Stille dazwischen.
    main = make_click_track(audio_dir / "click_two_songs.wav",
                            tracks=[TrackSpec(120.0, 32), TrackSpec(90.0, 18)], gap=6.0)
    # Kriterium 11: dieselbe Struktur, aber nur 1,2 s Luecke.
    short = make_click_track(audio_dir / "click_short_gap.wav",
                             tracks=[TrackSpec(120.0, 32), TrackSpec(90.0, 18)], gap=1.2)

    images = make_images(root / "images")
    clips = make_clips(root / "clips") if with_clips and have("ffmpeg") else {}

    expected = {
        "audio": {"two_songs": asdict(main), "short_gap": asdict(short)},
        "images": [str(p.relative_to(root)) for p in images],
        "clips": {k: str(v.relative_to(root)) for k, v in clips.items()},
    }
    (root / "expected.json").write_text(json.dumps(expected, indent=1), encoding="utf-8")
    log.info("Fixtures erzeugt unter %s (%d Bilder, %d Clips)", root, len(images), len(clips))
    return expected


def load_expected(root: Path) -> dict:
    p = Path(root) / "expected.json"
    if not p.exists():
        raise FileNotFoundError(f"Fixtures fehlen: {p}. Erst `slideshow selftest "
                                f"--make-fixtures` laufen lassen.")
    return json.loads(p.read_text(encoding="utf-8"))
