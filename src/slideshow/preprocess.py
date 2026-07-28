"""Phase 2 — Preprocessing (Abschnitt 5). Schreibt nach ``cache/``, idempotent.

Nebeneffekt und groesster einzelner Geschwindigkeitsgewinn im ganzen Tool:
alle folgenden Renderdurchlaeufe lesen kleine, gleichfoermige Dateien statt
100 x 20 MP mit gemischten Profilen.

**Normalform.** Jedes zwischengespeicherte Bild hat exakt das Seitenverhaeltnis
der Ausgabe (7680x4320) — nicht nur die Hochformate. Sonst muesste der
Ken-Burns-Renderer je nach Quellformat unterschiedlich rechnen, denn
``zoompan`` schneidet immer einen Bereich *im Seitenverhaeltnis der Eingabe*
aus und skaliert ihn auf die Ausgabegroesse; bei 3:2-Eingabe und 16:9-Ausgabe
verzerrt das. Querformat wird deshalb formatfuellend beschnitten, Hochformat
bekommt das Komposit aus 5.1.
"""

from __future__ import annotations

import concurrent.futures as _fut
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from .cache import HashIndex, cache_key
from .doctor import Capabilities
from .encoders import intermediate_args
from .errors import SlideshowError
from .framerate import plan_retime
from .models import Manifest, MediaItem
from .paths import Project
from .proc import DryRun, run

log = logging.getLogger("slideshow.preprocess")

#: Subpixel-Vorrat fuer Ken Burns. Das Original mit 5472 px waere bei
#: 4K-Ausgabe schon grenzwertig.
LONG_EDGE = 7680

#: Version der Preprocessing-Logik — geht in den Cache-Key ein, damit eine
#: Aenderung hier alte Zwischenprodukte ungueltig macht.
PREPROC_VERSION = 3


@dataclass
class PreprocessStats:
    images_done: int = 0
    images_cached: int = 0
    clips_done: int = 0
    clips_cached: int = 0
    failures: list[str] = None

    def __post_init__(self):
        if self.failures is None:
            self.failures = []


def _keyfile(out: Path) -> Path:
    return out.with_suffix(out.suffix + ".key")


def _is_fresh(out: Path, key: str) -> bool:
    kf = _keyfile(out)
    if not out.exists() or not kf.exists():
        return False
    try:
        return kf.read_text(encoding="utf-8").strip() == key
    except OSError:
        return False


def _mark_fresh(out: Path, key: str) -> None:
    _keyfile(out).write_text(key, encoding="utf-8")


# --------------------------------------------------------------------------
# 5.1 Bilder
# --------------------------------------------------------------------------

def process_image(src: Path, dst: Path, *, portrait_mode: str = "blur",
                  fmt: str = "jpeg", quality: int = 95,
                  size: tuple[int, int] | None = None) -> dict:
    """Normalisiert ein Foto (Pillow-Pfad, 5.1).

    1. EXIF-Orientation hart einbrennen und das Tag entfernen — ffmpeg rotiert
       Standbilder nicht zuverlaessig anhand von EXIF; das ist die haeufigste
       Ursache fuer "warum liegt Bild 63 quer".
    2. ICC -> sRGB.
    3. Auf die Normalform skalieren (Lanczos).
    4. Als JPEG q=95 bzw. 16-Bit-PNG ablegen.
    """
    from PIL import Image, ImageCms, ImageOps, ImageFilter

    tw, th = size or (LONG_EDGE, int(round(LONG_EDGE * 9 / 16)))

    with Image.open(src) as im:
        # 1. Orientation einbrennen. exif_transpose entfernt das Tag dabei.
        im = ImageOps.exif_transpose(im)
        orig_aspect = im.width / im.height if im.height else 1.0

        # 2. ICC -> sRGB. Handys liefern haeufig Display-P3, Kameras oft
        #    AdobeRGB — beides muss nach sRGB.
        icc = im.info.get("icc_profile")
        if icc:
            try:
                import io
                src_prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                im = ImageCms.profileToProfile(
                    im, src_prof, ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception as exc:                # noqa: BLE001
                log.warning("%s: ICC-Konvertierung fehlgeschlagen (%s), Profil wird "
                            "als sRGB interpretiert", src.name, exc)
        if im.mode != "RGB":
            im = im.convert("RGB")

        portrait = orig_aspect < 1.0
        if portrait and portrait_mode != "crop":
            out = _portrait_composite(im, tw, th, mode=portrait_mode,
                                      Image=Image, ImageFilter=ImageFilter)
        else:
            out = _cover_crop(im, tw, th, Image=Image)

        dst.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "png16":
            # 16 Bit nuetzt nur dem scale16-Renderpfad; zoompan rechnet in 8 Bit.
            out.convert("RGB").save(dst, format="PNG", compress_level=4)
        else:
            out.save(dst, format="JPEG", quality=quality, subsampling=0,
                     optimize=True, progressive=False)
        return {"width": out.width, "height": out.height, "portrait": portrait}


def _cover_crop(im, tw: int, th: int, *, Image):
    """Formatfuellend skalieren und mittig beschneiden."""
    scale = max(tw / im.width, th / im.height)
    nw, nh = max(tw, int(math.ceil(im.width * scale))), max(th, int(math.ceil(im.height * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def _portrait_composite(im, tw: int, th: int, *, mode: str, Image, ImageFilter):
    """16:9-Komposit fuer Hochformat (5.1).

    Hintergrund = dasselbe Bild formatfuellend, ``gblur`` (sigma ~ 60), ~25 %
    abgedunkelt; Vordergrund hoehenbuendig zentriert. Das passiert *hier*, nicht
    erst beim Rendern — damit beziehen sich alle KB-Koordinaten auf das
    Komposit und der Renderer bleibt fuer alle Bilder identisch.
    """
    fg_h = th
    fg_w = max(1, int(round(im.width * fg_h / im.height)))
    fg = im.resize((fg_w, fg_h), Image.LANCZOS)

    if mode == "black":
        canvas = Image.new("RGB", (tw, th), (0, 0, 0))
    else:
        # Sigma 60 auf 7680x4320 direkt zu blurren ist unnoetig teuer. Klein
        # rechnen, blurren, hochskalieren ist visuell identisch und ~50x
        # schneller — Gauss und Skalierung kommutieren naeherungsweise.
        shrink = 8
        small_w, small_h = max(1, tw // shrink), max(1, th // shrink)
        bg = _cover_crop(im, small_w, small_h, Image=Image)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=60 / shrink))
        bg = bg.resize((tw, th), Image.BICUBIC)
        canvas = bg.point(lambda v: int(v * 0.75))   # ~25 % abdunkeln

    canvas.paste(fg, ((tw - fg_w) // 2, 0))
    return canvas


def _image_params(portrait_mode: str, fmt: str, quality: int,
                  size: tuple[int, int]) -> dict:
    return {"op": "image", "v": PREPROC_VERSION, "portrait": portrait_mode,
            "format": fmt, "quality": quality, "size": list(size)}


def _preprocess_one_image(args) -> tuple[str, str, dict | None, str | None]:
    """Worker fuer den Prozesspool (Pillow gibt das GIL nicht her)."""
    mid, src, dst, params, key = args
    try:
        info = process_image(Path(src), Path(dst), portrait_mode=params["portrait"],
                             fmt=params["format"], quality=params["quality"],
                             size=tuple(params["size"]))
        _mark_fresh(Path(dst), key)
        return (mid, dst, info, None)
    except Exception as exc:                        # noqa: BLE001
        return (mid, dst, None, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# 5.2 Clips
# --------------------------------------------------------------------------

def build_clip_filter(item: MediaItem, *, size: tuple[int, int], fps: float,
                      caps: Capabilities, portrait_mode: str = "blur") -> str:
    """Filterkette fuer das Intermediate.

    Reihenfolge nach 5.2: Tonemapping und Retiming *vor* dem Scale.
    """
    info = item.clip
    assert info is not None
    parts: list[str] = []

    if info.hdr:
        chain = caps.tonemap_chain(info.hdr)
        if chain:
            parts.append(chain)
        else:
            # Ohne zscale/libplacebo bleibt nur eine Naeherung. Die ist
            # ausdruecklich keine Farbwissenschaft, aber besser als ein
            # ausgewaschener Clip mitten im SDR-Material.
            parts.append("eq=contrast=1.18:saturation=1.12:gamma=0.92")

    rt = plan_retime(info.fps, fps)
    expr = rt.filter_expr()
    if expr:
        parts.append(expr)

    w, h = size
    src_aspect = _display_aspect(info)
    if src_aspect and src_aspect < 1.0 and portrait_mode != "crop":
        parts.append(_blur_pad_chain(w, h, mode=portrait_mode))
    else:
        parts.append(f"scale={w}:{h}:flags=lanczos:force_original_aspect_ratio=increase")
        parts.append(f"crop={w}:{h}")
    parts.append("setsar=1")
    return ",".join(parts)


def _display_aspect(info) -> float | None:
    """Seitenverhaeltnis *nach* der Rotation aus der Display-Matrix."""
    w = getattr(info, "width", 0) or 0
    h = getattr(info, "height", 0) or 0
    if not w or not h:
        return None
    if info.rotation in (90, 270, -90, -270):
        w, h = h, w
    return w / h


def _blur_pad_chain(w: int, h: int, *, mode: str) -> str:
    """Hochformat-Clip in ein 16:9-Bett setzen — analog zu 5.1, damit auch
    hier keine schwarzen Balken entstehen."""
    if mode == "black":
        return (f"scale={w}:{h}:flags=lanczos:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black")
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={w}:{h}:flags=bicubic:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},gblur=sigma=60,eq=brightness=-0.12[bgb];"
        f"[fg]scale={w}:{h}:flags=lanczos:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
    )


def clip_intermediate_cmd(project: Project, item: MediaItem, dst: Path, *,
                          size: tuple[int, int], fps: float, caps: Capabilities,
                          codec: str, portrait_mode: str,
                          span: tuple[float, float] | None = None,
                          handle: float = 1.0) -> tuple[list[str], float, float]:
    """Baut das ffmpeg-Kommando fuer ein Clip-Intermediate.

    Gibt zusaetzlich Offset und Dauer des Intermediates *in der retimten
    Zeitbasis* zurueck — darauf beziehen sich in/out der Edit-List (Prinzip 3).
    """
    info = item.clip
    assert info is not None
    rt = plan_retime(info.fps, fps)
    eff_total = rt.effective_duration(info.duration)

    if span is None:
        out_start, out_end = 0.0, eff_total
    else:
        # Handles: Voraussetzung fuer Ueberblendungen an Clip-Grenzen (8.2).
        out_start = max(0.0, span[0] - handle)
        out_end = min(eff_total, span[1] + handle)

    # Quellzeiten: die Zeitbasis vor dem Retiming.
    src_start = out_start / rt.setpts if rt.setpts else out_start
    src_dur = (out_end - out_start) / rt.setpts if rt.setpts else (out_end - out_start)

    args = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    # 4:2:2-10-Bit-HEVC ohne passendes NVDEC: hwaccel gezielt deaktivieren,
    # statt mit kryptischem NVDEC-Fehler abzubrechen (3.3 / Kriterium 3).
    if info.force_cpu_decode:
        args += ["-hwaccel", "none"]
        log.info("%s: 4:2:2 10 Bit ohne NVDEC-Unterstuetzung -> CPU-Decode", item.id)
    elif caps.gpu_name and "cuda" in caps.hwaccels:
        args += ["-hwaccel", "cuda"]

    if src_start > 0:
        # -ss vor -i fuer schnelles Keyframe-Seeking, danach exakt trimmen.
        args += ["-ss", f"{src_start:.6f}"]
    args += ["-i", str(project.abs(item.path))]
    if src_dur > 0:
        args += ["-t", f"{src_dur:.6f}"]

    vf = build_clip_filter(item, size=size, fps=fps, caps=caps, portrait_mode=portrait_mode)
    enc, _container = intermediate_args(codec)
    args += [
        "-vf", vf,
        "-fps_mode", "cfr", "-r", f"{fps:g}",
        "-an",                                      # Originalton wird nie verwendet
        *enc,
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        # Im Output darf kein Rotations-Tag zurueckbleiben; die Rotation ist
        # beim Decode bereits angewandt.
        "-metadata:s:v", "rotate=0",
        str(dst),
    ]
    return (args, out_start, out_end - out_start)


# --------------------------------------------------------------------------
# Orchestrierung
# --------------------------------------------------------------------------

def preprocess(project: Project, manifest: Manifest, *, caps: Capabilities,
               portrait_mode: str = "blur", image_format: str = "jpeg",
               quality: int = 95, size: tuple[int, int] = (3840, 2160),
               intermediate_codec: str = "dnxhr_hqx", jobs: int | None = None,
               dry: DryRun | None = None,
               spans: dict[str, tuple[float, float]] | None = None,
               long_edge: int = LONG_EDGE) -> PreprocessStats:
    """Verarbeitet alle Medien des Manifests nach ``cache/``.

    ``long_edge`` ist der Subpixel-Vorrat fuer Ken Burns und nur fuer die
    Testsuite gedacht — dort waeren 7680 px reine Wartezeit.
    """
    project.ensure_dirs()
    stats = PreprocessStats()
    index = HashIndex(project.cache / "hashindex.json")
    fps = manifest.fps_suggestion
    img_size = (long_edge, int(round(long_edge * size[1] / size[0])))
    workers = caps.max_workers(jobs)

    # --- Bilder -------------------------------------------------------
    tasks = []
    for item in manifest.images:
        src = project.abs(item.path)
        ext = "png" if image_format == "png16" else "jpg"
        dst = project.cache / f"{item.id}.{ext}"
        params = _image_params(portrait_mode, image_format, quality, img_size)
        try:
            key = cache_key([index.file_hash(src)], params)
        except FileNotFoundError as exc:
            stats.failures.append(str(exc))
            continue
        item.cache_path = project.rel(dst)
        if _is_fresh(dst, key):
            stats.images_cached += 1
            continue
        if dry and dry.enabled:
            dry.record(["<pillow>", "normalize-image", str(src), "->", str(dst)])
            continue
        tasks.append((item.id, str(src), str(dst), params, key))

    if tasks:
        log.info("Bilder: %d neu, %d aus Cache (%d Worker)",
                 len(tasks), stats.images_cached, workers)
        with _fut.ProcessPoolExecutor(max_workers=workers) as pool:
            for mid, dst, info, err in pool.map(_preprocess_one_image, tasks):
                if err:
                    stats.failures.append(f"{mid}: {err}")
                    log.error("%s: %s", mid, err)
                    continue
                stats.images_done += 1
                item = manifest.by_id(mid)
                if item and item.image and info:
                    item.image.portrait = info["portrait"]

    # --- Clips --------------------------------------------------------
    for item in manifest.clips:
        src = project.abs(item.path)
        _enc, container = intermediate_args(intermediate_codec)
        dst = project.cache / f"{item.id}.{container}"
        span = (spans or {}).get(item.id)
        cmd, offset, dur = clip_intermediate_cmd(
            project, item, dst, size=size, fps=fps, caps=caps,
            codec=intermediate_codec, portrait_mode=portrait_mode, span=span)
        try:
            key = cache_key([index.file_hash(src)],
                            {"op": "clip", "v": PREPROC_VERSION, "cmd": cmd[3:],
                             "fps": fps, "size": list(size)})
        except FileNotFoundError as exc:
            stats.failures.append(str(exc))
            continue
        item.cache_path = project.rel(dst)
        if item.clip:
            item.clip.cache_offset = round(offset, 6)
            item.clip.cache_duration = round(dur, 6)
        if _is_fresh(dst, key):
            stats.clips_cached += 1
            continue
        if dry and dry.enabled:
            dry.record(cmd)
            continue
        log.info("Clip %s -> %s (%s)", item.id, dst.name,
                 item.clip.retime_note if item.clip else "")
        try:
            run(cmd, timeout=3600)
            if not dst.exists() or dst.stat().st_size == 0:
                raise SlideshowError(
                    f"ffmpeg meldete Erfolg, hat aber nichts geschrieben: {dst}")
            _mark_fresh(dst, key)
            stats.clips_done += 1
        except SlideshowError as exc:
            # Eine leere Datei ist gefaehrlicher als gar keine: sie sieht fuer
            # spaetere Phasen wie ein gueltiges Intermediate aus.
            dst.unlink(missing_ok=True)
            _keyfile(dst).unlink(missing_ok=True)
            stats.failures.append(f"{item.id}: {exc}")
            log.error("Clip %s fehlgeschlagen: %s", item.id, exc)

    index.save()
    return stats
