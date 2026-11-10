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

from .cache import HashIndex, cache_key, hash_file
from .clipaudio import SAMPLE_RATE as CLIP_AUDIO_RATE, atempo_chain
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


def title_canvas(size: tuple[int, int]) -> tuple[int, int]:
    """Leinwand, auf der eine Titelfolie gebacken wird.

    Dieselbe Normalform wie jedes andere Zwischenprodukt: das
    Ausgabe-Seitenverhaeltnis bei ``LONG_EDGE`` Breite. Entscheidend ist das
    wegen des eingebrannten Textes — die Folie wird von der Ken-Burns-Fahrt bis
    zu 1,3-fach vergroessert, und nur mit dem Subpixel-Vorrat bleibt der Text
    dabei durchgehend mindestens zweifach ueberabgetastet. In Ausgabegroesse
    gebacken wuerde er weich, und zwar genau dort, wo er am meisten auffaellt.

    Nebeneffekt, der ebenfalls erwuenscht ist: die Folie haengt damit nicht mehr
    an ``edit.size``. Ein Projekt in 4K und dasselbe in 1080p teilen sich
    dieselbe Datei, weil die Pixel dieselben sind.
    """
    w, h = size
    return (LONG_EDGE, int(round(LONG_EDGE * h / w)))


@dataclass
class PreprocessStats:
    images_done: int = 0
    images_cached: int = 0
    #: Clips, die eine Kennung bekommen haben. Umgerechnet werden sie erst
    #: beim Rendern (:func:`ensure_clip_intermediates`).
    clips: int = 0
    #: Medien, die die Auswahl ausgelassen hat (``only``).
    skipped: int = 0
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
# Titelfolien
# --------------------------------------------------------------------------

@dataclass
class TitleAssetStats:
    erzeugt: int = 0
    aus_cache: int = 0
    warnungen: list[str] = None

    def __post_init__(self):
        if self.warnungen is None:
            self.warnungen = []


def ensure_title_assets(project: Project, edit, manifest: Manifest | None = None, *,
                        dry: DryRun | None = None) -> TitleAssetStats:
    """Erzeugt die Assets aller Titelfolien, soweit sie fehlen oder veraltet sind.

    ``render`` darf sich nicht darauf verlassen, dass ``build`` gelaufen ist:
    ``slideshow render edit.yaml`` mit von Hand geaenderter Ueberschrift ist ein
    unterstuetzter Weg, und dann existiert das Asset unter dem neuen Namen noch
    gar nicht. Die Funktion ist deshalb idempotent und kostet bei unveraendertem
    Text nichts ausser einem Hash je Folie.

    Die Frische haengt an mehr als am Dateinamen: der Name ergibt sich aus der
    *Absicht* (Text, Hintergrundangabe, Layout), der Schluessel zusaetzlich aus
    dem *Inhalt* von Hintergrundbild und Schriftdatei. Nur so erkennt ein
    zweiter Rechner mit anderer Schrift, dass die vorhandene Datei nicht seine
    ist — die Folie saehe sonst anders aus, und der Cache waere zufrieden.
    """
    from .models import TitleSegment
    from .titles import find_font, freshness_key, render_title, resolved, title_asset

    stats = TitleAssetStats()
    folien = [(idx, resolved(edit.segments, idx))
              for idx, seg in enumerate(edit.segments) if isinstance(seg, TitleSegment)]
    if not folien:
        return stats

    project.ensure_dirs()
    # Erst hier, nicht beim Import: ein Projekt ohne Titelfolien braucht keine
    # Schrift und soll auch nicht daran scheitern.
    font = find_font(edit.defaults.title.font)
    font_hash = hash_file(font)
    canvas = title_canvas(tuple(edit.size))

    for idx, seg in folien:
        rel = title_asset(seg, edit.defaults, canvas)
        out = project.abs(rel)
        quelle, bg_hash, hinweis = _titel_hintergrund(project, manifest, seg)
        if hinweis:
            stats.warnungen.append(f"segments[{idx}]: {hinweis}")

        key = freshness_key(seg, edit.defaults, canvas, bg_hash=bg_hash,
                            font_hash=font_hash)
        if _is_fresh(out, key):
            stats.aus_cache += 1
            continue
        if dry is not None and dry.enabled:
            dry.record(["slideshow", "titel", seg.title, "->", str(out)])
            continue

        info = render_title(seg, edit.defaults, bg_source=quelle, out=out,
                            size=canvas, font=font)
        _mark_fresh(out, key)
        stats.erzeugt += 1
        for w in (info or {}).get("warnungen", []):
            stats.warnungen.append(f"segments[{idx}] {seg.title!r}: {w}")
    return stats


def _titel_hintergrund(project: Project, manifest: Manifest | None,
                       seg) -> tuple[Path | None, str, str]:
    """Quelldatei und Inhaltshash des Hintergrunds."""
    from .titles import bg_kind

    if bg_kind(seg.bg) != "image":
        return (None, "", "")
    quelle, hinweis = titel_bildquelle(project, manifest, seg.bg)
    return (quelle, hash_file(quelle) if quelle is not None else "", hinweis)


def titel_bildquelle(project: Project, manifest: Manifest | None,
                     bg: str) -> tuple[Path | None, str]:
    """Die Datei, aus der der Hintergrund einer Titelfolie entsteht.

    Als Quelle dient das **Original** aus dem Manifest, nicht das
    Zwischenprodukt: bei einem Hochformat ist Letzteres bereits ein
    Blur-Komposit, und ein zweiter Blur darueber ergaebe einen verwaschenen
    Rahmen um ein leicht verwaschenes Hochformat. In ``edit.yaml`` steht
    trotzdem der ``cache/``-Pfad — das ist die Kennung, unter der ein Bild im
    ganzen Projekt auftritt.

    Ohne Inhaltshash, weil ``build`` bei der Wahl des Hintergrunds nur den
    Pfad braucht (``docs/briefing-titelfolien-hintergrund.md``) und ueber
    fuenf Kandidaten je Kapitel nicht fuenf 20-MP-Dateien hashen soll. Dass
    Wahl und Backen dieselbe Datei sehen, haengt daran, dass beide durch
    diese Funktion gehen.
    """
    if manifest is not None:
        item = manifest.by_cache_path(bg)
        if item is not None:
            original = project.abs(item.path)
            if original.exists():
                return (original, "")

    # Ohne Manifest oder ohne Original bleibt das Zwischenprodukt. Das ist
    # brauchbar, aber bei einem Hochformat sichtbar schlechter — deshalb gesagt.
    ersatz = project.abs(bg)
    if ersatz.exists():
        return (ersatz,
                f"Original zu {bg} nicht auffindbar, der Hintergrund entsteht aus "
                f"dem Zwischenprodukt. Bei einem Hochformat wird er dadurch doppelt "
                f"unscharf.")
    return (None, f"Hintergrundbild {bg} fehlt — die Folie bekommt eine Farbflaeche.")


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


@dataclass(frozen=True)
class ClipSpan:
    """Der Ausschnitt eines Clips in beiden Zeitbasen.

    ``out_*`` liegt in der retimten Zeitbasis — darauf beziehen sich in/out
    der Edit-List (Prinzip 3). ``src_*`` ist dieselbe Stelle im Original und
    geht an ``-ss``/``-t``. Bild und Ton lesen denselben Ausschnitt, weil sie
    dieselbe Rechnung benutzen.
    """

    out_start: float
    out_duration: float
    src_start: float
    src_duration: float
    #: Geschwindigkeit relativ zum Original (> 1 = schneller).
    speed: float


def clip_span(item: MediaItem, *, fps: float, span: tuple[float, float] | None,
              handle: float = 1.0) -> ClipSpan:
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
    return ClipSpan(out_start=out_start, out_duration=out_end - out_start,
                    src_start=src_start, src_duration=src_dur, speed=rt.speed)


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
    cs = clip_span(item, fps=fps, span=span, handle=handle)
    out_start, src_start, src_dur = cs.out_start, cs.src_start, cs.src_duration

    args = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    # 4:2:2-10-Bit-HEVC ohne passendes NVDEC: hwaccel gezielt deaktivieren,
    # statt mit kryptischem NVDEC-Fehler abzubrechen (3.3 / Kriterium 3).
    if info.force_cpu_decode:
        args += ["-hwaccel", "none"]
        log.info("%s: 4:2:2 10 Bit ohne NVDEC-Unterstuetzung -> CPU-Decode", item.id)
    elif caps.gpu_name and "cuda" in caps.hwaccels:
        args += ["-hwaccel", "cuda"]

    # -ss und -t gehoeren beide vor -i: als Eingabeoptionen begrenzen sie das
    # gelesene Quellfenster, und -t zaehlt ab dem Seek-Ziel (gemessen). Steht
    # -t dagegen dahinter, ist es eine Ausgabeoption und kappt das per setpts
    # gestreckte Ergebnis wieder auf die ungestreckte Quelldauer — Zeitlupen-
    # Clips (setpts > 1) kamen zu kurz an, waehrend Manifest und Planer weiter
    # mit der vollen Dauer rechneten.
    if src_start > 0:
        args += ["-ss", f"{src_start:.6f}"]
    if src_dur > 0:
        args += ["-t", f"{src_dur:.6f}"]
    args += ["-i", str(project.abs(item.path))]

    vf = build_clip_filter(item, size=size, fps=fps, caps=caps, portrait_mode=portrait_mode)
    enc, _container = intermediate_args(codec)
    args += [
        "-vf", vf,
        "-fps_mode", "cfr", "-r", f"{fps:g}",
        # Das Bildsegment bleibt stumm. Der Originalton wird — wenn er
        # ueberhaupt verlangt ist — als eigene Datei extrahiert und erst beim
        # Muxen untergemischt (5.5), damit das Umschalten kein einziges
        # gerendertes Segment ungueltig macht.
        "-an",
        *enc,
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        # Im Output darf kein Rotations-Tag zurueckbleiben; die Rotation ist
        # beim Decode bereits angewandt.
        "-metadata:s:v", "rotate=0",
        str(dst),
    ]
    return (args, out_start, cs.out_duration)


# --------------------------------------------------------------------------
# 5.5 Originalton der Clips
# --------------------------------------------------------------------------

def clip_audio_cmd(project: Project, item: MediaItem, dst: Path, *, fps: float,
                   span: tuple[float, float] | None = None,
                   handle: float = 1.0) -> list[str]:
    """Extrahiert den Originalton eines Clips in die Zeitbasis des Intermediates.

    Derselbe Ausschnitt wie beim Bild (:func:`clip_span`) und dieselbe
    Streckung: wurde das Bild per ``setpts`` konformiert, muss der Ton per
    ``atempo`` mit — sonst laeuft er dem Clip davon. ``atempo`` aendert dabei
    das Tempo, nicht die Tonhoehe.

    Die Datei entsteht unabhaengig davon, ob der Ton spaeter verwendet wird.
    Sie ist klein gegen das Videointermediate, und so kostet das Einschalten
    von ``audio: true`` an einem Segment keinen zweiten Preprocessing-Lauf.
    """
    cs = clip_span(item, fps=fps, span=span, handle=handle)
    chain = [*atempo_chain(cs.speed), f"aresample={CLIP_AUDIO_RATE}",
             "aformat=channel_layouts=stereo"]

    # Dasselbe wie im Bildpfad: -ss/-t vor -i, sonst kappt die Ausgabeoption
    # -t den per atempo gestreckten Ton auf die Quelldauer zurueck.
    args = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    if cs.src_start > 0:
        args += ["-ss", f"{cs.src_start:.6f}"]
    if cs.src_duration > 0:
        args += ["-t", f"{cs.src_duration:.6f}"]
    args += ["-i", str(project.abs(item.path))]
    args += ["-vn", "-map", "0:a:0", "-af", ",".join(chain),
             "-c:a", "flac", str(dst)]
    return args


def _extract_clip_audio(project: Project, item: MediaItem, *, fps: float,
                        span: tuple[float, float] | None, index: HashIndex,
                        stats: "ClipStats", dry: DryRun | None,
                        handle: float = 1.0) -> None:
    """Legt ``cache/<id>.audio.flac`` an, sofern der Clip ueberhaupt Ton hat.

    Derselbe Bereich wie das Bild-Intermediate, damit beide denselben
    ``cache_offset`` haben. Die Datei entsteht auch dann, wenn der Ton nicht
    angefordert ist: sie ist klein, und so kostet ``audio: true`` nachtraeglich
    nur einen Mux.
    """
    if item.clip is None or not item.clip.has_audio:
        item.audio_cache_path = ""
        return
    dst = project.cache / f"{item.id}.audio.flac"
    cmd = clip_audio_cmd(project, item, dst, fps=fps, span=span, handle=handle)
    try:
        key = cache_key([index.file_hash(project.abs(item.path))],
                        {"op": "clip-audio", "v": PREPROC_VERSION, "cmd": cmd[3:]})
    except FileNotFoundError as exc:
        stats.failures.append(str(exc))
        return

    item.audio_cache_path = project.rel(dst)
    if _is_fresh(dst, key):
        stats.clip_audio_cached += 1
        return
    if dry and dry.enabled:
        dry.record(cmd)
        return
    try:
        run(cmd, timeout=3600)
        if not dst.exists() or dst.stat().st_size == 0:
            raise SlideshowError(f"ffmpeg schrieb keine Tonspur: {dst}")
        _mark_fresh(dst, key)
        stats.clip_audio_done += 1
    except SlideshowError as exc:
        # Eine fehlende Tonspur darf den Clip nicht mitreissen: das Bild ist
        # das Wesentliche, der Originalton die Zugabe. Gemeldet wird sie
        # trotzdem, sonst fehlt sie spaeter unerklaerlich.
        dst.unlink(missing_ok=True)
        _keyfile(dst).unlink(missing_ok=True)
        item.audio_cache_path = ""
        stats.failures.append(f"{item.id} (Originalton): {exc}")
        log.warning("Originalton von %s nicht extrahierbar: %s", item.id, exc)


# --------------------------------------------------------------------------
# Clip-Intermediates beim Rendern (Pruefbericht 2026-11, AP2)
# --------------------------------------------------------------------------

#: Vor- und Nachlauf um den genutzten Bereich, bevor auf ganze Sekunden
#: gerundet wird. Die Blenden sind darin schon enthalten (``clip_bereiche``
#: rechnet ueber die sichtbare Spanne); der Rand ist fuer Kdenlive und dafuer,
#: dass ein um ein paar Frames korrigierter Schnitt den Cache-Key nicht aendert.
CLIP_HANDLE = 1.0

#: Bytes je Sekunde fuer DNxHR HQX in UHD/50p, gemessen an ``kenia-2026``
#: (Pruefbericht 2026-11, 3.2). Fuer die anderen Codecs gibt es keine Messung;
#: sie werden mit demselben Wert geschaetzt, das ist eine Obergrenze.
INTERMEDIATE_BPS_UHD50 = 182e6

#: Codecs, deren Intermediate im ``.mov`` landet — dem Container, den die
#: Clip-Kennung ``cache/<id>.mov`` traegt.
INTERMEDIATE_CODECS = ("dnxhr_hqx", "hevc_intra_nvenc", "hevc_intra_cpu")


@dataclass
class ClipStats:
    erzeugt: int = 0
    aus_cache: int = 0
    #: Sekunden Intermediate (retimte Zeitbasis), die dieser Lauf neu schreibt.
    sekunden_neu: float = 0.0
    clip_audio_done: int = 0
    clip_audio_cached: int = 0
    failures: list[str] = None

    def __post_init__(self):
        if self.failures is None:
            self.failures = []


def clip_kennung(item: MediaItem) -> str:
    """Der ``cache/``-Pfad, unter dem ein Clip in ``edit.yaml`` steht.

    Er ist zugleich der Ort des Intermediates. Die Datei entsteht aber erst
    beim Rendern; ``preprocess`` vergibt nur die Kennung, damit ``build`` den
    Clip ueberhaupt einplant.
    """
    return f"cache/{item.id}.mov"


def clip_bereiche(plan, manifest: Manifest) -> dict[str, tuple[float, float]]:
    """Je Clip-Kennung der Bereich, den das Intermediate abdecken muss.

    In der retimten Zeitbasis, ueber die volle **sichtbare** Spanne jedes Slots
    (also einschliesslich der halben Blenden), um ``CLIP_HANDLE`` erweitert und
    nach aussen auf ganze Sekunden gerundet. Die Rundung ist der Punkt: der
    Bereich geht ueber das Kommando in den Cache-Key ein, und ohne sie loeste
    jede Korrektur um ein paar Frames eine neue Umrechnung aus.

    Ein mehrfach verwendeter Clip bekommt die Huelle seiner Bereiche.
    """
    from .planner import visible_span

    bereiche: dict[str, tuple[float, float]] = {}
    for i, slot in enumerate(plan.slots):
        if slot.intent.kind != "clip":
            continue
        item = manifest.by_cache_path(slot.intent.src)
        if item is None or item.clip is None:
            continue
        vs, ve = visible_span(plan, i)
        lo = slot.clip_in - (slot.start_f - vs) / plan.fps
        hi = slot.clip_in + (ve - slot.start_f) / plan.fps
        lo = max(0.0, float(math.floor(lo - CLIP_HANDLE + 1e-6)))
        hi = float(math.ceil(hi + CLIP_HANDLE - 1e-6))
        ende = item.clip.effective_duration or item.clip.duration
        if ende > 0:
            hi = min(hi, ende)
        alt = bereiche.get(slot.intent.src)
        bereiche[slot.intent.src] = (min(lo, alt[0]), max(hi, alt[1])) if alt else (lo, hi)
    return bereiche


def intermediate_bps(size: tuple[int, int], fps: float) -> float:
    """Geschaetzte Bytes je Sekunde Intermediate, linear in Pixeln mal Rate."""
    w, h = size
    return INTERMEDIATE_BPS_UHD50 * (w * h * fps) / (3840 * 2160 * 50)


def ensure_clip_intermediates(project: Project, plan, manifest: Manifest, *,
                              size: tuple[int, int], caps: Capabilities,
                              codec: str = "dnxhr_hqx", portrait_mode: str = "blur",
                              nur: set[str] | None = None, clip_audio: bool = True,
                              dry: DryRun | None = None) -> ClipStats:
    """Erzeugt die Intermediates der Clips im Plan, nur im verwendeten Bereich.

    Gegenstueck zu :func:`ensure_title_assets` und aus demselben Grund beim
    Rendern statt beim Vorverarbeiten: erst der Plan weiss, welcher Teil eines
    Clips im Film steht. Vorher wurde jeder Clip ganz umgerechnet, in
    ``kenia-2026`` 192 GB, von denen ein Drittel im Film vorkam.

    Setzt ``cache_offset``, ``cache_duration`` und ``audio_cache_path`` am
    Manifest-Eintrag **im Speicher** — danach lesen Segmentkommandos, Ton und
    Kdenlive-Export die Datei in ihrer tatsaechlichen Zeitbasis. Das Manifest
    auf der Platte bleibt unberuehrt: der Bereich ist eine Eigenschaft dieses
    Plans, nicht des Materials.

    ``nur`` beschraenkt die Erzeugung auf eine Menge von Kennungen (``render
    --range``). Der Bereich wird trotzdem aus dem ganzen Plan bestimmt, damit
    ein Teilrender dieselbe Datei erzeugt wie der volle.
    """
    bereiche = clip_bereiche(plan, manifest)
    if nur is not None:
        bereiche = {k: v for k, v in bereiche.items() if k in nur}
    return make_clip_intermediates(project, manifest, bereiche, size=size, caps=caps,
                                   codec=codec, portrait_mode=portrait_mode,
                                   clip_audio=clip_audio, dry=dry)


def make_clip_intermediates(project: Project, manifest: Manifest,
                            bereiche: dict[str, tuple[float, float] | None], *,
                            size: tuple[int, int], caps: Capabilities,
                            codec: str = "dnxhr_hqx", portrait_mode: str = "blur",
                            clip_audio: bool = True,
                            dry: DryRun | None = None) -> ClipStats:
    """Erzeugt Intermediates fuer ausdrueckliche Bereiche je Clip-Kennung.

    ``None`` als Bereich heisst: der ganze Clip. Normalerweise kommen die
    Bereiche aus dem Plan (:func:`ensure_clip_intermediates`); direkt
    aufgerufen wird diese Funktion von den Tests, die das Intermediate selbst
    pruefen.
    """
    if codec not in INTERMEDIATE_CODECS:
        raise SlideshowError(
            f"Intermediate-Codec {codec!r} wird nicht unterstuetzt. Moeglich: "
            f"{', '.join(INTERMEDIATE_CODECS)}.")
    stats = ClipStats()
    if not bereiche:
        return stats

    project.ensure_dirs()
    index = HashIndex(project.cache / "hashindex.json")
    fps = manifest.fps_suggestion
    auftraege = []
    for kennung, span in bereiche.items():
        item = manifest.by_cache_path(kennung)
        if item is None or item.clip is None:
            stats.failures.append(f"{kennung}: steht nicht als Clip im Manifest")
            continue
        dst = project.abs(kennung)
        cmd, offset, dur = clip_intermediate_cmd(
            project, item, dst, size=size, fps=fps, caps=caps, codec=codec,
            portrait_mode=portrait_mode, span=span, handle=0.0)
        try:
            key = cache_key([index.file_hash(project.abs(item.path))],
                            {"op": "clip", "v": PREPROC_VERSION, "cmd": cmd[3:],
                             "fps": fps, "size": list(size)})
        except FileNotFoundError as exc:
            stats.failures.append(str(exc))
            continue
        item.clip.cache_offset = round(offset, 6)
        item.clip.cache_duration = round(dur, 6)
        if clip_audio:
            _extract_clip_audio(project, item, fps=fps, span=span, handle=0.0,
                                index=index, stats=stats, dry=dry)
        if _is_fresh(dst, key):
            stats.aus_cache += 1
            continue
        auftraege.append((item, dst, cmd, key, dur))

    stats.sekunden_neu = sum(a[4] for a in auftraege)
    if auftraege and not (dry and dry.enabled):
        _platz_fuer_clips(project, stats.sekunden_neu * intermediate_bps(size, fps))

    for item, dst, cmd, key, _dur in auftraege:
        if dry and dry.enabled:
            dry.record(cmd)
            continue
        log.info("Clip %s -> %s (%.1f s ab %.1f s, %s)", item.id, dst.name,
                 item.clip.cache_duration, item.clip.cache_offset, item.clip.retime_note)
        try:
            run(cmd, timeout=3600)
            if not dst.exists() or dst.stat().st_size == 0:
                raise SlideshowError(
                    f"ffmpeg meldete Erfolg, hat aber nichts geschrieben: {dst}")
            _mark_fresh(dst, key)
            stats.erzeugt += 1
        except SlideshowError as exc:
            # Eine leere Datei ist gefaehrlicher als gar keine: sie sieht fuer
            # spaetere Phasen wie ein gueltiges Intermediate aus.
            dst.unlink(missing_ok=True)
            _keyfile(dst).unlink(missing_ok=True)
            stats.failures.append(f"{item.id}: {exc}")
            log.error("Clip %s fehlgeschlagen: %s", item.id, exc)
    index.save()
    return stats


def _platz_fuer_clips(project: Project, bedarf: float) -> None:
    """Bricht ab, bevor ein Intermediate die Platte fuellt.

    Ohne Reserve, anders als ``doctor.check_space``: der Bedarf ist bereits
    eine Obergrenze (DNxHR-Rate fuer jeden Codec), und ein ueberschriebenes
    Intermediate gibt seinen alten Platz ohnehin frei.
    """
    import shutil
    try:
        frei = shutil.disk_usage(project.cache).free
    except OSError:
        return
    if frei < bedarf:
        raise SlideshowError(
            f"Die Clip-Intermediates brauchen bis zu {bedarf / 1e9:.1f} GB, frei sind "
            f"{frei / 1e9:.1f} GB. Platz schaffen (`slideshow clean`) oder einen "
            f"kleineren Codec waehlen (`render --intermediate hevc_intra_nvenc`).")


# --------------------------------------------------------------------------
# Orchestrierung
# --------------------------------------------------------------------------

def preprocess(project: Project, manifest: Manifest, *, caps: Capabilities,
               portrait_mode: str = "blur", image_format: str = "jpeg",
               quality: int = 95, size: tuple[int, int] = (3840, 2160),
               jobs: int | None = None,
               dry: DryRun | None = None,
               long_edge: int = LONG_EDGE,
               only: set[str] | None = None) -> PreprocessStats:
    """Verarbeitet die Medien des Manifests nach ``cache/``.

    ``only`` beschraenkt den Lauf auf eine Menge von Medien-IDs — die Auswahl
    aus ``order.yaml`` (``docs/briefing-auswahl.md``, Stufe 2). Ohne den
    Parameter aendert sich nichts, und das ist der Normalfall: bei neunzig
    vorsortierten Bildern gibt es nichts zu sparen.

    Bei einem Sammelbecken schon: tausend Bilder auf 7680 px Langkante zu
    normalisieren kostet Stunden und zweistellige Gigabyte, und zweihundert
    davon landen im Film. Weggelassenes behaelt sein bisheriges ``cache_path``
    — ein spaeter doch hereingenommenes Bild wird beim naechsten Lauf
    nachgeholt, weil der Cache ohnehin inkrementell arbeitet.

    ``long_edge`` ist der Subpixel-Vorrat fuer Ken Burns und nur fuer die
    Testsuite gedacht — dort waeren 7680 px reine Wartezeit.

    **Clips werden hier nicht umgerechnet.** Sie bekommen nur ihre Kennung
    (:func:`clip_kennung`); Intermediate und Originalton entstehen beim
    Rendern, und zwar nur fuer den Bereich, den der Plan verwendet
    (:func:`ensure_clip_intermediates`). ``cache_offset``/``cache_duration``
    werden zurueckgesetzt: ``build`` plant gegen den ganzen Clip.
    """
    project.ensure_dirs()
    stats = PreprocessStats()
    index = HashIndex(project.cache / "hashindex.json")
    img_size = (long_edge, int(round(long_edge * size[1] / size[0])))
    workers = caps.max_workers(jobs)

    gewaehlt = (lambda m: True) if only is None else (lambda m: m.id in only)
    bilder = [m for m in manifest.images if gewaehlt(m)]
    filme = [m for m in manifest.clips if gewaehlt(m)]
    stats.skipped = len(manifest.media) - len(bilder) - len(filme)

    # --- Bilder -------------------------------------------------------
    tasks = []
    for item in bilder:
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

    # --- Clips: nur die Kennung ----------------------------------------
    for item in filme:
        if not project.abs(item.path).exists():
            stats.failures.append(f"Quelldatei fehlt: {project.abs(item.path)}")
            continue
        item.cache_path = clip_kennung(item)
        item.audio_cache_path = ""
        if item.clip:
            item.clip.cache_offset = 0.0
            item.clip.cache_duration = 0.0
        stats.clips += 1

    index.save()
    return stats
