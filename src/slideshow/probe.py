"""Phase 1 — Ingest & Probe (Abschnitt 4).

``slideshow probe <quellverzeichnis>`` -> ``manifest.json``.

EXIF wird fuer alle Bilder in *einem* ``exiftool -j -r``-Batchlauf gelesen:
100 Prozessstarts sind unter Windows spuerbar langsam.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import statistics
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import MANIFEST_VERSION
from .doctor import Capabilities
from .errors import SlideshowError
from .framerate import plan_retime, suggest_target_fps
from .models import ClipInfo, ImageInfo, Manifest, MediaItem
from .paths import Project
from .proc import ffprobe_json, ffprobe_packets, have, run

log = logging.getLogger("slideshow.probe")

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp",
             ".dng", ".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2"}
VIDEO_EXT = {".mp4", ".mov", ".mts", ".m2ts", ".avi", ".mkv", ".m4v", ".3gp", ".webm"}
#: Kein Quellmaterial im Sinne von `probe` — die Tonspur kommt ueber
#: `slideshow audio`. Die Liste dient dazu, sie im Materialordner zu *erkennen*
#: und den passenden naechsten Schritt vorzuschlagen.
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma",
             ".aif", ".aiff"}

#: Verdachtsschwelle fuer VFR aus dem Vergleich r_frame_rate / avg_frame_rate.
_VFR_SUSPECT = Fraction(1, 100)
#: Ab dieser relativen Streuung der Paket-Deltas gilt VFR als bestaetigt.
_VFR_JITTER = 0.02


# --------------------------------------------------------------------------
# Klassifikation und Erkennung
# --------------------------------------------------------------------------

def classify(s: dict) -> str:
    """Grobklassifikation des Clipmaterials (Abschnitt 4)."""
    codec, fmt = s.get("codec_name", ""), s.get("pix_fmt", "")
    if codec == "hevc" and fmt in ("yuv420p10le", "yuv422p10le"):
        return "xavc_hs"          # Sony HEVC, Long-GOP, 10 bit
    if codec == "h264" and str(s.get("profile", "")).startswith("High"):
        return "xavc_s"           # Sony AVC 8 bit 4:2:0
    return "generic"              # Android u. a.


def color_transfer(s: dict) -> str:
    """Die Transferfunktion eines Streams.

    ffprobe nennt das Feld ``color_transfer``; ``color_trc`` ist nur der Name
    der gleichnamigen *ffmpeg-Option*. Wer nach ``color_trc`` greift, bekommt
    stillschweigend einen leeren String — und damit sieht jeder HDR-Clip wie
    SDR aus und landet ungetonemappt im Master.
    """
    return str(s.get("color_transfer") or s.get("color_trc") or "")


def detect_hdr(s: dict) -> str:
    """HLG (Sony HLG-Profil, Pixel) bzw. PQ/HDR10 (Samsung HDR10+)."""
    trc = color_transfer(s)
    if trc == "arib-std-b67":
        return "hlg"
    if trc == "smpte2084":
        return "pq"
    return ""


def _fraction(value: str | None) -> Fraction:
    try:
        f = Fraction(value or "0/1")
    except (ValueError, ZeroDivisionError):
        return Fraction(0)
    return f


def vfr_suspect(s: dict) -> bool:
    """Verdachtstest — kritisch fuer Android-Material."""
    r, a = _fraction(s.get("r_frame_rate")), _fraction(s.get("avg_frame_rate"))
    if r == 0 or a == 0:
        return False
    return abs(r - a) / r > _VFR_SUSPECT


def confirm_vfr(path: Path, ffprobe: str = "ffprobe") -> tuple[bool, float]:
    """Bestaetigung ueber die tatsaechlichen Paket-Timestamps.

    ``avg_frame_rate`` allein ist bei Android unzuverlassig; deshalb messen wir
    die Streuung der Deltas eines Ausschnitts.
    """
    packets = ffprobe_packets(path, count=300, ffprobe=ffprobe)
    times: list[float] = []
    for p in packets:
        t = p.get("pts_time") or p.get("dts_time")
        try:
            times.append(float(t))
        except (TypeError, ValueError):
            continue
    times.sort()
    deltas = [b - a for a, b in zip(times, times[1:]) if b > a]
    if len(deltas) < 10:
        return (False, 0.0)
    med = statistics.median(deltas)
    if med <= 0:
        return (False, 0.0)
    # Robuste Streuung: mittlere absolute Abweichung relativ zum Median.
    jitter = sum(abs(d - med) for d in deltas) / len(deltas) / med
    return (jitter > _VFR_JITTER, round(jitter, 5))


def rotation_of(stream: dict) -> int:
    """Rotation aus der Display-Matrix (bzw. dem alten rotate-Tag)."""
    for sd in stream.get("side_data_list", []) or []:
        if sd.get("side_data_type") == "Display Matrix" and "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag:
        try:
            return int(float(tag)) % 360
        except (TypeError, ValueError):
            pass
    return 0


# --------------------------------------------------------------------------
# Zeitstempel
# --------------------------------------------------------------------------

_EXIF_DT = re.compile(r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_NAME_DT = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_ T]?(\d{2})[-_.:]?(\d{2})"
                      r"[-_.:]?(\d{2})?")


def _parse_exif_datetime(value: str | None) -> float | None:
    if not value or not isinstance(value, str):
        return None
    m = _EXIF_DT.match(value.strip())
    if not m:
        return None
    try:
        parts = [int(x) for x in m.groups()]
        return _dt.datetime(*parts).timestamp()
    except ValueError:
        return None


def _parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        txt = value.replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(txt).timestamp()
    except (ValueError, TypeError):
        return None


def _parse_from_name(name: str) -> float | None:
    m = _NAME_DT.search(name)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    try:
        return _dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0)).timestamp()
    except ValueError:
        return None


_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def gps_from_exif(exif: dict) -> tuple[float, float] | None:
    """Aufnahmeort aus den EXIF-Daten, als ``(lat, lon)`` in Grad.

    ``exiftool -n`` liefert je nach Tag-Gruppe entweder den vorzeichenbehafteten
    Composite-Wert oder den rohen Betrag samt ``Ref``. Deshalb wird der Betrag
    genommen und das Vorzeichen aus ``Ref`` gesetzt, wo es eines gibt — sonst
    kippte ein Suedhalbkugel-Foto je nach exiftool-Version einmal zu viel.
    """
    lat, lon = exif.get("GPSLatitude"), exif.get("GPSLongitude")
    if lat is None or lon is None:
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    ref_lat = str(exif.get("GPSLatitudeRef") or "").strip().upper()[:1]
    ref_lon = str(exif.get("GPSLongitudeRef") or "").strip().upper()[:1]
    if ref_lat in ("N", "S"):
        lat = -abs(lat) if ref_lat == "S" else abs(lat)
    if ref_lon in ("E", "W"):
        lon = -abs(lon) if ref_lon == "W" else abs(lon)
    return _gps_plausibel(lat, lon)


def gps_from_tags(tags: dict) -> tuple[float, float] | None:
    """Aufnahmeort aus Container-Tags (``ISO 6709``, wie Handys ihn schreiben).

    ``+52.5200+013.4050+031.000/`` — Breite, Laenge, optional Hoehe.
    """
    for key in ("com.apple.quicktime.location.ISO6709", "location",
                "location-eng"):
        wert = (tags or {}).get(key)
        if not wert:
            continue
        m = _ISO6709.match(str(wert).strip())
        if m:
            try:
                return _gps_plausibel(float(m.group(1)), float(m.group(2)))
            except ValueError:
                continue
    return None


def _gps_plausibel(lat: float, lon: float) -> tuple[float, float] | None:
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    # Genau 0/0 ist die "Nullinsel" im Golf von Guinea — praktisch immer ein
    # leerer Fix, kein Aufenthaltsort.
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return None
    # Auf sechs Nachkommastellen (~0,1 m) runden: mehr ist Messrauschen und
    # laesst das Manifest zwischen zwei Laeufen wackeln.
    return (round(lat, 6), round(lon, 6))


def capture_time(*, exif: dict, container_tags: dict, path: Path) -> tuple[float | None, str]:
    """Fallback-Kaskade aus 4.4: EXIF -> container creation_time -> mtime -> Name."""
    for key in ("DateTimeOriginal", "CreateDate", "MediaCreateDate"):
        ts = _parse_exif_datetime(exif.get(key))
        if ts:
            return (ts, "exif")
    ts = _parse_iso((container_tags or {}).get("creation_time"))
    if ts:
        return (ts, "container")
    ts = _parse_from_name(path.name)
    if ts:
        return (ts, "filename")
    try:
        return (path.stat().st_mtime, "mtime")
    except OSError:
        return (None, "none")


# --------------------------------------------------------------------------
# Uhren-Offsets
# --------------------------------------------------------------------------

_OFFSET_RE = re.compile(r"^\s*(?P<model>.+?)\s*=\s*(?P<sign>[+-])?"
                        r"(?P<h>\d+):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*$")


def parse_clock_offset(spec: str) -> tuple[str, float]:
    """``"ILCE-7M4=+01:00:00"`` -> ``("ILCE-7M4", 3600.0)``."""
    m = _OFFSET_RE.match(spec)
    if not m:
        raise SlideshowError(
            f"Unlesbarer Uhren-Offset {spec!r}. Erwartet: \"MODELL=+HH:MM:SS\" "
            f"(z. B. --clock-offset \"ILCE-7M4=+01:00:00\")")
    sign = -1.0 if m.group("sign") == "-" else 1.0
    secs = int(m.group("h")) * 3600 + int(m.group("m")) * 60 + int(m.group("s") or 0)
    return (m.group("model"), sign * secs)


# --------------------------------------------------------------------------
# EXIF-Batchlauf
# --------------------------------------------------------------------------

_EXIF_TAGS = ["-DateTimeOriginal", "-CreateDate", "-Model", "-Make", "-Orientation",
              "-ProfileDescription", "-ColorSpace", "-ImageWidth", "-ImageHeight",
              "-FileType", "-GPSLatitude", "-GPSLongitude", "-GPSLatitudeRef",
              "-GPSLongitudeRef"]


def read_exif_batch(paths: list[Path]) -> dict[str, dict]:
    """Ein einziger exiftool-Lauf fuer alle Bilder."""
    if not paths or not have("exiftool"):
        if paths:
            log.warning("exiftool fehlt — EXIF-Orientation und ICC-Profile werden "
                        "nicht ausgewertet. Bilder koennen quer liegen.")
        return {}
    cmd = ["exiftool", "-j", "-n", "-q", *_EXIF_TAGS, *[str(p) for p in paths]]
    res = run(cmd, check=False, timeout=900)
    if not res.stdout.strip():
        log.warning("exiftool lieferte keine Daten: %s", res.stderr_tail(5))
        return {}
    try:
        entries = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        log.warning("exiftool-Ausgabe unlesbar (%s), EXIF wird uebersprungen", exc)
        return {}
    out: dict[str, dict] = {}
    for e in entries:
        src = e.get("SourceFile")
        if src:
            out[str(Path(src).resolve())] = e
    return out


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------

def discover(sources: list[Path]) -> tuple[list[Path], list[Path], list[Path]]:
    """Findet Bilder, Clips und ignorierte Dateien."""
    images, clips, other = [], [], []
    for src in sources:
        src = Path(src)
        if src.is_file():
            candidates = [src]
        elif src.is_dir():
            candidates = sorted(p for p in src.rglob("*") if p.is_file())
        else:
            raise SlideshowError(f"Quelle existiert nicht: {src}")
        for p in candidates:
            ext = p.suffix.lower()
            if ext in IMAGE_EXT:
                images.append(p)
            elif ext in VIDEO_EXT:
                clips.append(p)
            elif not p.name.startswith("."):
                other.append(p)
    return (images, clips, other)


def _make_id(prefix: str, path: Path, taken: set[str]) -> str:
    """Stabile, lesbare ID. Stabil heisst: haengt nur am Dateinamen, damit ein
    zusaetzliches Foto nicht alle anderen IDs verschiebt."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_") or "x"
    base = f"{prefix}_{stem}"
    cand, n = base, 1
    while cand in taken:
        n += 1
        cand = f"{base}_{n}"
    taken.add(cand)
    return cand


@dataclass
class ProbeResult:
    manifest: Manifest
    ignored: list[Path]
    device_spans: dict[str, tuple[float, float, int]]


def probe_sources(project: Project, sources: list[Path], *, caps: Capabilities,
                  clock_offsets: dict[str, float] | None = None,
                  target_fps: float | None = None) -> ProbeResult:
    images, clips, other = discover(sources)
    if not images and not clips:
        raise SlideshowError(f"Kein verwertbares Material gefunden unter: "
                             f"{', '.join(str(s) for s in sources)}")
    log.info("Gefunden: %d Bilder, %d Clips, %d ignoriert", len(images), len(clips), len(other))

    exif = read_exif_batch(images)
    taken: set[str] = set()
    media: list[MediaItem] = []

    for p in images:
        media.append(_probe_image(project, p, exif.get(str(p.resolve()), {}), taken))
    for p in clips:
        media.append(_probe_clip(project, p, taken, caps=caps))

    # Zielrate aus der tatsaechlichen Verteilung ableiten (Abschnitt 7).
    hist: dict[float, float] = {}
    for m in media:
        if m.clip and m.clip.fps > 0:
            hist[round(m.clip.fps, 3)] = hist.get(round(m.clip.fps, 3), 0.0) + m.clip.duration
    suggested, rationale = suggest_target_fps(hist)
    fps = float(target_fps or suggested)

    # Retiming planen — die effektive Dauer landet im Manifest (5.2).
    for m in media:
        if not m.clip:
            continue
        rt = plan_retime(m.clip.fps, fps)
        m.clip.retime = rt.setpts
        m.clip.retime_note = rt.note
        m.clip.effective_duration = round(rt.effective_duration(m.clip.duration), 6)
        if not rt.lossless:
            m.warnings.append(rt.note)

    manifest = Manifest(
        version=MANIFEST_VERSION,
        created=_dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        clock_offsets=dict(clock_offsets or {}),
        fps_histogram={f"{k:g}": int(round(v)) for k, v in sorted(hist.items())},
        fps_suggestion=fps,
        fps_rationale=rationale,
        media=media,
    )
    _verify_paths(project, manifest)
    return ProbeResult(manifest, other, device_spans(manifest))


def _verify_paths(project: Project, manifest: Manifest) -> None:
    """Jeder gespeicherte Pfad muss vom Projektroot aus wieder auf seine
    Quelldatei zeigen (Grundprinzip 4).

    Gelesen wird das Material ueber die Pfade, wie sie auf der Kommandozeile
    stehen — abgelegt wird projektrelativ. Stimmen die beiden Bezugssysteme
    nicht ueberein, ist der Report trotzdem vollstaendig und korrekt, und der
    Fehler faellt erst in preprocess auf. Hier ist er noch erklaerbar.
    """
    broken = [m for m in manifest.media if not project.abs(m.path).exists()]
    if not broken:
        return
    lines = "\n".join(f"  {m.id}: {m.path} -> {project.abs(m.path)}" for m in broken[:10])
    more = f"\n  ... und {len(broken) - 10} weitere" if len(broken) > 10 else ""
    raise SlideshowError(
        f"{len(broken)} von {len(manifest.media)} Medienpfaden zeigen vom Projektroot "
        f"({project.root}) aus ins Leere:\n{lines}{more}")


def _probe_image(project: Project, path: Path, exif: dict, taken: set[str]) -> MediaItem:
    width = int(exif.get("ImageWidth") or 0)
    height = int(exif.get("ImageHeight") or 0)
    orientation = int(exif.get("Orientation") or 1)

    if not width or not height:
        width, height = _image_size_fallback(path)

    # Bei Orientation 5-8 sind die gespeicherten Masse gedreht.
    disp_w, disp_h = (height, width) if orientation in (5, 6, 7, 8) else (width, height)

    icc = str(exif.get("ProfileDescription") or "")
    if not icc:
        cs = exif.get("ColorSpace")
        icc = {1: "sRGB", 2: "AdobeRGB", 65535: "Uncalibrated"}.get(cs, "") if cs else ""

    ts, source = capture_time(exif=exif, container_tags={}, path=path)
    camera = str(exif.get("Model") or exif.get("Make") or "unbekannt").strip()

    item = MediaItem(
        id=_make_id("img", path, taken),
        path=project.rel(path),
        kind="image",
        size_bytes=_size(path),
        camera=camera,
        capture_time=ts,
        time_source=source,
        gps=gps_from_exif(exif),
        image=ImageInfo(width=disp_w, height=disp_h, orientation=orientation, icc=icc,
                        portrait=bool(disp_h > disp_w)),
    )
    if source in ("mtime", "none"):
        item.warnings.append(
            f"kein verwertbarer Aufnahmezeitpunkt (Quelle: {source}) — landet am Ende "
            f"und will von Hand platziert werden")
    if icc and icc not in ("sRGB", "sRGB IEC61966-2.1"):
        item.warnings.append(f"ICC-Profil {icc!r} wird nach sRGB konvertiert")
    return item


def _image_size_fallback(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:                              # noqa: BLE001 - Sonde darf nie werfen
        try:
            data = ffprobe_json(path)
            st = next((s for s in data.get("streams", [])
                       if s.get("codec_type") == "video"), {})
            return (int(st.get("width") or 0), int(st.get("height") or 0))
        except Exception:                          # noqa: BLE001
            return (0, 0)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _probe_clip(project: Project, path: Path, taken: set[str], *,
                caps: Capabilities) -> MediaItem:
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise SlideshowError(f"Datei enthaelt keinen Videostream: {path}")

    r_rate = str(video.get("r_frame_rate") or "0/1")
    a_rate = str(video.get("avg_frame_rate") or "0/1")
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)

    suspect = vfr_suspect(video)
    confirmed, jitter = (False, 0.0)
    if suspect:
        confirmed, jitter = confirm_vfr(path)

    # Bei bestaetigtem VFR ist ``avg_frame_rate`` *kleiner* als die nominelle
    # Rate, weil das Geraet Frames gedroppt hat. Wer darauf die Zielrate
    # bezieht, haelt ein 30p-Handyvideo faelschlich fuer 24p und retimt es in
    # Zeitlupe. Der Fix fuer Android-VFR ist ausschliesslich die
    # CFR-Konformierung (5.2), nicht eine Geschwindigkeitsaenderung — also
    # zaehlt hier die nominelle Rate.
    nominal = _fraction(r_rate)
    average = _fraction(a_rate)
    fps = float((nominal if confirmed and nominal else average) or nominal or 0)

    hdr = detect_hdr(video)
    cls = classify(video)
    rot = rotation_of(video)

    # 4:2:2-10-Bit-HEVC ohne passendes NVDEC muss auf der CPU decodiert werden (3.3).
    force_cpu = (video.get("codec_name") == "hevc"
                 and video.get("pix_fmt") == "yuv422p10le"
                 and not caps.nvdec_422_10bit)

    info = ClipInfo(
        codec=str(video.get("codec_name") or ""),
        profile=str(video.get("profile") or ""),
        level=int(video.get("level") or 0),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        pix_fmt=str(video.get("pix_fmt") or ""),
        r_frame_rate=r_rate,
        avg_frame_rate=a_rate,
        fps=round(fps, 6),
        color_primaries=str(video.get("color_primaries") or ""),
        color_trc=color_transfer(video),
        colorspace=str(video.get("color_space") or ""),
        rotation=rot,
        duration=round(duration, 6),
        bitrate=int(fmt.get("bit_rate") or 0),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        container=str(fmt.get("format_name") or ""),
        classification=cls,
        vfr_suspect=suspect,
        vfr_confirmed=confirmed,
        vfr_jitter=jitter,
        hdr=hdr,
        effective_duration=round(duration, 6),
        force_cpu_decode=force_cpu,
    )

    tags = fmt.get("tags") or {}
    ts, source = capture_time(exif={}, container_tags=tags, path=path)
    camera = str(tags.get("com.android.model") or tags.get("model")
                 or (video.get("tags") or {}).get("model") or "unbekannt").strip()

    item = MediaItem(
        id=_make_id("clip", path, taken),
        path=project.rel(path),
        kind="clip",
        size_bytes=_size(path),
        camera=camera,
        capture_time=ts,
        time_source=source,
        gps=gps_from_tags(tags),
        clip=info,
    )
    if confirmed:
        item.warnings.append(f"VFR bestaetigt (Jitter {jitter:.1%}) — wird zwingend "
                             f"zu CFR konformiert")
    elif suspect:
        item.warnings.append("VFR-Verdacht aus r/avg_frame_rate, per Paket-Timestamps "
                             "nicht bestaetigt — wird trotzdem CFR-konformiert")
    if rot:
        item.warnings.append(f"Rotation {rot} Grad wird beim Decode eingebrannt")
    if hdr:
        item.warnings.append(f"{hdr.upper()}-Material — wird nach BT.709 SDR getonemappt")
    if force_cpu:
        item.warnings.append("4:2:2 10 Bit HEVC ohne NVDEC-Unterstuetzung — CPU-Decode")
    if source in ("mtime", "none"):
        item.warnings.append(f"kein verwertbarer Aufnahmezeitpunkt (Quelle: {source})")
    return item


def device_spans(manifest: Manifest) -> dict[str, tuple[float, float, int]]:
    """Gruppiert nach Kameramodell und gibt pro Geraet die Zeitspanne aus (4.4)."""
    groups: dict[str, list[float]] = {}
    for m in manifest.media:
        if m.capture_time is None:
            continue
        groups.setdefault(m.camera or "unbekannt", []).append(m.capture_time)
    return {k: (min(v), max(v), len(v)) for k, v in sorted(groups.items())}


def effective_capture_time(item: MediaItem, offsets: dict[str, float]) -> float | None:
    """Aufnahmezeitpunkt inklusive Uhren-Offset (4.4)."""
    if item.capture_time is None:
        return None
    return item.capture_time + offsets.get(item.camera, 0.0)


def chronological(manifest: Manifest) -> list[MediaItem]:
    """Default-Reihenfolge: chronologisch nach Aufnahmezeitpunkt.

    Dateien ohne verwertbaren Zeitstempel landen mit WARN am Ende und werden
    von Hand in der Edit-List platziert (4.4).
    """
    offsets = manifest.clock_offsets
    dated, undated = [], []
    for m in manifest.media:
        ts = effective_capture_time(m, offsets)
        (undated if ts is None or m.time_source == "none" else dated).append((ts, m))
    dated.sort(key=lambda x: (x[0], x[1].path))
    undated.sort(key=lambda x: x[1].path)
    return [m for _, m in dated] + [m for _, m in undated]
