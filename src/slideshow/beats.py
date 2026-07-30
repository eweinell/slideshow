"""Phase 3a — Audio-Segmentierung und Beats (Abschnitte 6.1 und 6.2).

Kein globales BPM-Raster: ein einzelnes ``bpm`` + ``offset`` fuer die gesamte
Tonspur ist nur bei genau einem durchlaufenden Track korrekt. Die Tonspur wird
deshalb zuerst in **Regionen** zerlegt, und erst danach wird jede Region
*einzeln* analysiert. Diese Reihenfolge ist wichtig: eine Tempo-Erkennung ueber
einen Mix aus zwei Tracks liefert einen Mittelwert, der zu keinem der beiden
passt.

``free`` ist der Fallback-Modus — nicht nur fuer echte Stille, sondern fuer
jeden Abschnitt, in dem sich kein verlaessliches Raster finden laesst.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import BEATS_VERSION
from .errors import SlideshowError
from .models import BeatMap, Region
from .proc import run

log = logging.getLogger("slideshow.beats")

ANALYSIS_SR = 22050
HOP = 512

#: Kurze Ausreisser glaetten, sonst zerfaellt die Tonspur in Dutzende
#: Mikroregionen (6.2).
SMOOTH_SECONDS = 1.5

#: Schwellen der Erkennung (6.2).
SILENCE_DB = -45.0
SILENCE_MIN = 0.4
QUIET_DB = -35.0

#: Ab hier gilt eine Region als rhythmisch verlaesslich.
CONF_THRESHOLD = 0.55
#: Kuerzer als das lohnt kein eigenes Beat-Raster.
MIN_BEAT_REGION = 5.0

_BPM_RANGE = (55.0, 200.0)
#: Prior gegen Oktavfehler (halbes/doppeltes Tempo), zentriert auf 120 BPM.
_BPM_PRIOR_CENTER = 120.0
_BPM_PRIOR_WIDTH = 0.9


# --------------------------------------------------------------------------
# Audio laden
# --------------------------------------------------------------------------

def load_mono(path: Path, sr: int = ANALYSIS_SR) -> np.ndarray:
    """Dekodiert ueber ffmpeg nach mono float32.

    Bewusst ueber ffmpeg statt ueber librosa/soundfile: ffmpeg ist ohnehin
    Pflicht, versteht jedes Eingangsformat und macht die Analyse unabhaengig
    davon, welche Audio-Bibliotheken installiert sind.
    """
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le",
           "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(sr), "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-5:]
        raise SlideshowError(f"Audio nicht dekodierbar: {path}\n" + "\n".join(tail))
    y = np.frombuffer(proc.stdout, dtype=np.float32).astype(np.float64)
    if y.size == 0:
        raise SlideshowError(f"Audio ist leer: {path}")
    return y


# --------------------------------------------------------------------------
# Signal 1: harte Stille
# --------------------------------------------------------------------------

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silence(path: Path, *, noise_db: float = SILENCE_DB,
                   min_dur: float = SILENCE_MIN) -> list[tuple[float, float]]:
    """Harte Stille (Luecken zwischen Tracks, Vorlauf, Ausklang)."""
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
               "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
               "-f", "null", "-"], check=False, timeout=1800)
    err = res.stderr or ""
    starts = [float(m) for m in _SIL_START.findall(err)]
    ends = [float(m) for m in _SIL_END.findall(err)]
    spans: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        spans.append((max(0.0, s), e if e is not None else math.inf))
    return spans


# --------------------------------------------------------------------------
# Signal 2 und 3: Pegel und Rhythmus
# --------------------------------------------------------------------------

def rms_db(y: np.ndarray, hop: int = HOP) -> np.ndarray:
    """Pegelverlauf in dB relativ zum Maximum — findet Fades und Intros, die
    nicht digital still sind."""
    n = 1 + (len(y) - 1) // hop
    pad = np.pad(y, (0, max(0, n * hop - len(y))))
    frames = pad[:n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    ref = max(rms.max(), 1e-12)
    return 20.0 * np.log10(rms / ref)


def onset_envelope(y: np.ndarray, sr: int = ANALYSIS_SR, hop: int = HOP) -> np.ndarray:
    """Onset-Staerke. Nutzt librosa, wenn vorhanden, sonst Spectral Flux.

    Der numpy-Pfad ist kein Notnagel: er macht die ganze Analyse unabhaengig
    von einer schwergewichtigen Abhaengigkeit (librosa zieht numba/llvmlite
    nach) und ist auf perkussivem Material praktisch gleichwertig.
    """
    try:
        import librosa
        env = librosa.onset.onset_strength(y=y.astype(np.float32), sr=sr, hop_length=hop)
        return np.asarray(env, dtype=np.float64)
    except Exception:                               # noqa: BLE001
        return _spectral_flux(y, hop=hop)


def _spectral_flux(y: np.ndarray, *, hop: int = HOP, win: int = 2048) -> np.ndarray:
    n = 1 + max(0, (len(y) - win)) // hop
    if n < 2:
        return np.zeros(max(1, n))
    window = np.hanning(win)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = y[idx] * window
    mag = np.abs(np.fft.rfft(frames, axis=1))
    diff = np.diff(mag, axis=0, prepend=mag[:1])
    flux = np.maximum(diff, 0.0).sum(axis=1)
    return flux


@dataclass
class Analysis:
    """Ergebnis der Rasteranpassung an eine Kandidatenregion."""

    bpm: float
    #: Absoluter Zeitpunkt des ersten Beats der Region.
    offset: float
    conf: float
    stability: float


def _prior(bpm: float) -> float:
    return math.exp(-0.5 * (math.log2(bpm / _BPM_PRIOR_CENTER) / _BPM_PRIOR_WIDTH) ** 2)


def fit_grid(env: np.ndarray, *, start: float, sr: int = ANALYSIS_SR,
             hop: int = HOP) -> Analysis:
    """Passt ein Beat-Raster (Tempo *und* Phase) an die Onset-Kurve an.

    Statt Tempo und Downbeat getrennt zu schaetzen, wird direkt das Raster
    gesucht, dessen Beat-Positionen die groesste Onset-Energie einsammeln. Der
    Prior gegen Oktavfehler verhindert, dass 120 BPM als 60 BPM durchgeht —
    beide sammeln pro Beat gleich viel Energie ein.
    """
    if env.size < 8:
        return Analysis(0.0, start, 0.0, 0.0)
    e = env - env.min()
    peak = e.max()
    if peak <= 0:
        return Analysis(0.0, start, 0.0, 0.0)
    e = e / peak
    fps = sr / hop
    mean_e = float(e.mean()) or 1e-9

    best: tuple[float, float, float] = (-1.0, 0.0, 0.0)   # (score, bpm, phase_frames)
    for bpm in np.arange(_BPM_RANGE[0], _BPM_RANGE[1] + 0.01, 0.25):
        period = 60.0 / bpm * fps
        if period < 2 or period > len(e):
            continue
        n_beats = int((len(e) - 1) // period) + 1
        if n_beats < 3:
            continue
        k = np.arange(n_beats)
        for phase in np.arange(0.0, period, 0.5):
            pos = phase + k * period
            pos = pos[pos <= len(e) - 1]
            if pos.size < 3:
                continue
            score = float(np.interp(pos, np.arange(len(e)), e).mean()) * _prior(bpm)
            if score > best[0]:
                best = (score, float(bpm), float(phase))

    score, bpm, phase = best
    if bpm <= 0:
        return Analysis(0.0, start, 0.0, 0.0)

    period = 60.0 / bpm * fps
    positions = phase + np.arange(int((len(e) - 1 - phase) // period) + 1) * period
    vals = np.interp(positions, np.arange(len(e)), e)

    # Konfidenz: wie stark heben sich die Rasterpunkte vom Mittel ab?
    ratio = float(vals.mean()) / mean_e
    conf = max(0.0, min(1.0, (ratio - 1.0) / 2.0))
    # Stabilitaet: wie gleichmaessig ist die Energie ueber die Beats verteilt?
    stability = 0.0
    if vals.size > 2 and vals.mean() > 0:
        stability = max(0.0, min(1.0, 1.0 - float(vals.std()) / float(vals.mean())))

    return Analysis(bpm=round(bpm, 2), offset=round(start + phase / fps, 6),
                    conf=round(conf * 0.6 + stability * 0.4, 4),
                    stability=round(stability, 4))


#: Feine Rasterung fuer die Offset-Verfeinerung: 64 Samples bei 22050 Hz sind
#: 2,9 ms, also rund ein Sechstel Frame bei 60 fps.
_FINE_HOP = 64


def refine_offset(y: np.ndarray, an: Analysis, *, start: float, end: float,
                  sr: int = ANALYSIS_SR) -> Analysis:
    """Korrigiert die Phase direkt auf der Wellenform.

    Die Onset-Kurve wird mit Hop 512 berechnet — 23 ms Rasterung, deutlich
    groeber als ein Videoframe. Fuer den Schnitt reicht das nicht: die Blende
    saesse systematisch hinter dem Schlag. Deshalb wird der gefundene Offset
    nachtraeglich am tatsaechlichen Energieanstieg ausgerichtet.
    """
    if an.bpm <= 0:
        return an
    period = 60.0 / an.bpm
    fine_fps = sr / _FINE_HOP

    n = 1 + (len(y) - 1) // _FINE_HOP
    pad = np.pad(y, (0, max(0, n * _FINE_HOP - len(y))))
    rms = np.sqrt((pad[:n * _FINE_HOP].reshape(n, _FINE_HOP) ** 2).mean(axis=1) + 1e-12)
    flux = np.maximum(np.diff(rms, prepend=rms[:1]), 0.0)
    if flux.max() <= 0:
        return an

    # Suchfenster: eine halbe Onset-Rasterbreite in jede Richtung.
    window = int(round(0.030 * fine_fps))
    corrections: list[float] = []
    t = an.offset
    while t < end - 1e-9:
        centre = int(round(t * fine_fps))
        lo, hi = max(0, centre - window), min(len(flux), centre + window + 1)
        if hi - lo >= 3:
            seg = flux[lo:hi]
            if seg.max() > 0:
                corrections.append((lo + int(np.argmax(seg))) / fine_fps - t)
        t += period

    if len(corrections) < 3:
        return an
    delta = float(np.median(corrections))
    if abs(delta) > 0.5 * period:
        return an
    # Bewusst *ohne* Klemmung auf ``start``: die Regionsgrenze stammt aus der
    # Stilleerkennung und liegt oft ein paar Millisekunden hinter dem ersten
    # Schlag. ``offset`` ist eine Phasenreferenz, kein Zeitpunkt innerhalb der
    # Region — ein Wert knapp davor ist korrekt und wird von
    # :func:`snap_region_starts` genutzt, um die Grenze auf den Beat zu ziehen.
    return Analysis(bpm=an.bpm, offset=round(an.offset + delta, 6),
                    conf=an.conf, stability=an.stability)


def snap_region_starts(regions: list[Region]) -> list[Region]:
    """Zieht den Anfang jeder Beat-Region auf den naechstliegenden Beat.

    Der Uebergang von Stille zu Musik ist selbst ein Schnitt. Wenn die
    Regionsgrenze aus der Stilleerkennung ein paar Millisekunden neben dem
    Schlag liegt, liegt dieser Schnitt daneben — und zwar dauerhaft, weil alle
    folgenden Schnitte der Region darauf aufsetzen.
    """
    for i, r in enumerate(regions):
        if r.type != "beat" or i == 0 or not r.bpm or r.offset is None:
            continue
        period = r.beat_duration()
        k = round((r.start - r.offset) / period)
        target = r.offset + k * period
        prev = regions[i - 1]
        if abs(target - r.start) > 0.5 * period:
            continue
        if target <= prev.start + 1e-6 or target >= r.end - 1e-6:
            continue
        prev.end = round(target, 6)
        r.start = round(target, 6)
    # ``offset`` als ersten Beat *innerhalb* der Region normalisieren.
    for r in regions:
        if r.type == "beat" and r.bpm and r.offset is not None:
            period = r.beat_duration()
            k = math.ceil((r.start - r.offset) / period - 1e-9)
            r.offset = round(r.offset + k * period, 6)
    return regions


# --------------------------------------------------------------------------
# Regionenkarte
# --------------------------------------------------------------------------

def _mask_to_spans(mask: np.ndarray, fps: float) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    if mask.size == 0:
        return spans
    edges = np.diff(mask.astype(np.int8))
    starts = list(np.flatnonzero(edges == 1) + 1)
    ends = list(np.flatnonzero(edges == -1) + 1)
    if mask[0]:
        starts.insert(0, 0)
    if mask[-1]:
        ends.append(len(mask))
    for s, e in zip(starts, ends):
        spans.append((s / fps, e / fps))
    return spans


def _smooth(mask: np.ndarray, fps: float, seconds: float = SMOOTH_SECONDS) -> np.ndarray:
    """Laeufe unter ``seconds`` verschlucken — in beide Richtungen."""
    out = mask.copy()
    min_len = max(1, int(round(seconds * fps)))
    for value in (True, False):
        i = 0
        while i < len(out):
            if out[i] != value:
                i += 1
                continue
            j = i
            while j < len(out) and out[j] == value:
                j += 1
            if (j - i) < min_len and not (i == 0 and j == len(out)):
                out[i:j] = not value
            i = j
    return out


def detect_regions(path: Path, *, track_bounds: list[tuple[float, float]] | None = None,
                   still_seconds: float = 4.0,
                   tolerance: tuple[float, float] = (3.0, 6.0)) -> BeatMap:
    """Zerlegt die Tonspur in ``beat``- und ``free``-Regionen.

    Drei Signale werden kombiniert (6.2) — harte Stille, leise Passagen und
    Rhythmus-Konfidenz — plus die Track-Grenzen aus 5.4 als Seeds.
    """
    y = load_mono(path)
    sr = ANALYSIS_SR
    duration = len(y) / sr
    fps = sr / HOP

    db = rms_db(y)
    quiet = db < QUIET_DB

    # Signal 1 in dieselbe Rasterung uebertragen.
    for s, e in detect_silence(path):
        e = min(e, duration)
        i0, i1 = int(s * fps), min(len(quiet), int(math.ceil(e * fps)))
        if i1 > i0:
            quiet[i0:i1] = True

    quiet = _smooth(quiet, fps)
    loud_spans = _mask_to_spans(~quiet, fps)
    quiet_spans = _mask_to_spans(quiet, fps)

    # Track-Grenzen als Seeds: an ihnen wird auf jeden Fall getrennt, statt sie
    # rein akustisch raten zu muessen.
    seeds = sorted({round(b, 3) for pair in (track_bounds or []) for b in pair
                    if 0.0 < b < duration})
    loud_spans = _split_at(loud_spans, seeds)

    env = onset_envelope(y, sr=sr, hop=HOP)
    regions: list[Region] = []

    for s, e in loud_spans:
        if e - s <= 0.05:
            continue
        i0, i1 = int(s * fps), min(len(env), int(math.ceil(e * fps)))
        seg = env[i0:i1]
        an = fit_grid(seg, start=s, sr=sr, hop=HOP)
        an = refine_offset(y, an, start=s, end=e, sr=sr)
        if an.conf >= CONF_THRESHOLD and an.bpm > 0 and (e - s) >= MIN_BEAT_REGION:
            regions.append(Region(type="beat", start=round(s, 6), end=round(e, 6),
                                  bpm=an.bpm, offset=an.offset, conf=an.conf))
        else:
            reason = "niedrige Rhythmus-Konfidenz" if (e - s) >= MIN_BEAT_REGION \
                else "zu kurz fuer ein eigenes Raster"
            regions.append(Region(type="free", start=round(s, 6), end=round(e, 6),
                                  reason=reason))

    for s, e in quiet_spans:
        reason = "stille" if e - s >= 2.0 else "kurze luecke"
        regions.append(Region(type="free", start=round(s, 6), end=round(e, 6), reason=reason,
                              quiet=True))

    regions.sort(key=lambda r: r.start)
    regions = _tile(regions, duration)
    regions = merge_adjacent_free(regions)
    regions = apply_preroll_rule(regions)
    regions = merge_short_regions(regions, still_seconds=still_seconds, tolerance=tolerance)
    regions = snap_region_starts(regions)
    validate_tiling(regions, duration)

    return BeatMap(version=BEATS_VERSION,
                   audio={"file": path.name, "duration": round(duration, 6)},
                   regions=regions)


def _split_at(spans: list[tuple[float, float]], seeds: list[float]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, e in spans:
        cuts = [c for c in seeds if s + 0.5 < c < e - 0.5]
        prev = s
        for c in cuts:
            out.append((prev, c))
            prev = c
        out.append((prev, e))
    return out


def _tile(regions: list[Region], duration: float) -> list[Region]:
    """Die Regionenkarte muss ``[0, audio_ende]`` lueckenlos kacheln (6.0).

    Fuehrende und abschliessende ``free``-Regionen entstehen hier automatisch.
    """
    out: list[Region] = []
    cursor = 0.0
    for r in regions:
        if r.start > cursor + 1e-6:
            out.append(Region(type="free", start=round(cursor, 6), end=round(r.start, 6),
                              reason="luecke"))
        out.append(r)
        cursor = max(cursor, r.end)
    if cursor < duration - 1e-6:
        out.append(Region(type="free", start=round(cursor, 6), end=round(duration, 6),
                          reason="ausklang"))
    if out:
        out[0].start = 0.0
        out[-1].end = round(duration, 6)
    return out


def merge_adjacent_free(regions: list[Region]) -> list[Region]:
    out: list[Region] = []
    for r in regions:
        if out and out[-1].type == "free" and r.type == "free":
            out[-1].end = r.end
            if r.reason and r.reason not in out[-1].reason:
                out[-1].reason = f"{out[-1].reason}+{r.reason}" if out[-1].reason else r.reason
            # Nur wer durchgehend still ist, bleibt still: eine Stille, an die
            # ein rastherloses Musikstueck anschliesst, ist zusammen Musik.
            out[-1].quiet = out[-1].quiet and r.quiet
            continue
        out.append(r)
    return out


def apply_preroll_rule(regions: list[Region]) -> list[Region]:
    """Ausnahme aus 6.0: ein Vorlauf < 1 s vor dem ersten Beat wird der ersten
    Beat-Region zugeschlagen.

    Das erste Bild beginnt damit bei 0; die Schnitte liegen weiterhin auf dem
    Beat-Raster, weil ``offset`` unveraendert bleibt.
    """
    if len(regions) >= 2 and regions[0].type == "free" and regions[1].type == "beat" \
            and regions[0].duration < 1.0:
        regions[1].start = regions[0].start
        return regions[1:]
    return regions


def merge_short_regions(regions: list[Region], *, still_seconds: float,
                        tolerance: tuple[float, float]) -> list[Region]:
    """Regionen verschmelzen, die kein sinnvolles Bild tragen koennen (6.3).

    Eine 1,2-s-Luecke soll kein 1,2-s-Bild erzeugen, sondern in der
    Nachbarregion aufgehen (Abnahmekriterium 11).
    """
    lo, hi = tolerance
    changed = True
    while changed and len(regions) > 1:
        changed = False
        for i, r in enumerate(regions):
            if r.type != "free" or _free_fits(r, still_seconds, lo, hi):
                continue
            # Bevorzugt in die vorhergehende Region, sonst in die folgende.
            if i > 0:
                regions[i - 1].end = r.end
            else:
                regions[i + 1].start = r.start
            regions.pop(i)
            changed = True
            break
    return regions


def _free_fits(r: Region, still_seconds: float, lo: float, hi: float) -> bool:
    """Laesst sich die Region mit n Bildern innerhalb des Toleranzbands fuellen?"""
    base = max(1, int(round(r.duration / still_seconds)))
    for n in (base, base + 1, max(1, base - 1)):
        if n >= 1 and lo <= r.duration / n <= hi:
            return True
    # Sehr lange Stille bekommt ein hold-Bild und ist damit immer zulaessig.
    return r.duration > hi


def validate_tiling(regions: list[Region], duration: float, *, eps: float = 1e-3) -> None:
    """``build`` validiert die Lueckenlosigkeit und bricht sonst ab (6.0)."""
    if not regions:
        raise SlideshowError("Regionenkarte ist leer.")
    if abs(regions[0].start) > eps:
        raise SlideshowError(
            f"Regionenkarte beginnt bei {regions[0].start:.3f} s statt bei 0. "
            f"Der Nullpunkt der Master-Timeline ist Sample 0 der Tonspur.")
    for a, b in zip(regions, regions[1:]):
        if abs(a.end - b.start) > eps:
            kind = "Luecke" if b.start > a.end else "Ueberlappung"
            raise SlideshowError(
                f"{kind} in der Regionenkarte: Region endet bei {a.end:.3f} s, "
                f"die naechste beginnt bei {b.start:.3f} s. "
                f"Die Karte muss [0, {duration:.3f}] lueckenlos kacheln.")
    if abs(regions[-1].end - duration) > max(eps, 0.05):
        raise SlideshowError(
            f"Regionenkarte endet bei {regions[-1].end:.3f} s, die Tonspur bei "
            f"{duration:.3f} s.")
    for r in regions:
        if r.type == "beat" and not r.bpm:
            raise SlideshowError(f"Beat-Region [{r.start:.3f}, {r.end:.3f}] ohne bpm.")
