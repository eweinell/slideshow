"""Ken-Burns-Filtergraphen (Abschnitt 8.1).

``zoompan`` hat zwei bekannte Schwaechen: es akkumuliert ``zoom+inc`` (Drift)
und schneidet x/y auf Integer (Zittern). Beides wird umgangen, indem der Zoom
*aus der Framenummer* berechnet und die Quelle vorher hochskaliert wird —
Letzteres erledigt bereits das Preprocessing.

Zwei Engines:

``zoompan``
    Der Default. Schnell, aber ffmpeg rechnet den Filter in 8 Bit (in
    ffmpeg 6.1 verifiziert: vor dem Filter wird still ein ``auto_scale`` nach
    ``yuv420p`` eingehaengt). Der 10-Bit-Gewinn entsteht dann nur noch im
    Encoder — das reduziert Banding messbar, loest es aber nicht vollstaendig.

``scale16``
    Der Ausweichpfad ohne ``zoompan``: per-Frame ``scale``-Expressions in
    ``yuv444p16le`` plus festes ``crop``. Rechnet durchgehend in 16 Bit und
    subpixelgenau, kostet aber spuerbar mehr CPU. Als Option, nicht als Default
    — zu waehlen, wenn der Banding-Test in Himmelsverlaeufen Stufen zeigt.

Beide bekommen dieselbe Signatur: die Bewegung ist ueber die **volle sichtbare
Spanne** eines Stills definiert (``total_frames``), und ein Segment rendert
daraus ab ``offset``. Genau das laesst die Bewegung durch eine Blende
hindurch weiterlaufen (8.2 / Abnahmekriterium 12).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import KBDefaults, KBSpec

#: Acht Schwenkrichtungen, deterministisch nach Segmentindex durchgereicht.
#: Deterministisch heisst: derselbe Index ergibt immer dieselbe Bewegung —
#: sonst aendert sich der Cache-Key bei jedem Lauf.
_DIRECTIONS = [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1), (-1, -1), (1, -1)]


@dataclass(frozen=True)
class KBMotion:
    """Eine vollstaendig bestimmte Ken-Burns-Bewegung."""

    z0: float
    z1: float
    c0: tuple[float, float]
    c1: tuple[float, float]
    ease: str = "smoothstep"
    engine: str = "zoompan"

    def as_spec(self) -> dict:
        return {"z": [round(self.z0, 4), round(self.z1, 4)],
                "c": [round(self.c0[0], 4), round(self.c0[1], 4),
                      round(self.c1[0], 4), round(self.c1[1], 4)]}

    def fingerprint(self) -> dict:
        """Was den Cache-Key beeinflusst."""
        return {**self.as_spec(), "ease": self.ease, "engine": self.engine}


def plan_motion(index: int, duration: float, defaults: KBDefaults,
                spec: KBSpec | None = None) -> KBMotion:
    """Leitet die Bewegung aus der Dauer ab.

    **Der Zoom-Betrag muss sich aus der Dauer ergeben, nicht umgekehrt** (8.1).
    Sobald Segmentlaengen zwischen ~2 s und ~12 s variieren, fuehrt ein fester
    Zoomfaktor im kurzen Fall zu einem hektischen Ruck und im langen Fall zu
    Stillstand.

    ``duration`` ist dabei die **volle sichtbare Dauer** des Bildes inklusive
    angrenzender Uebergangs-Haelften.
    """
    lo, hi = defaults.zoom_total
    z_end = min(1.0 + defaults.zoom_rate * duration, 1.0 + hi)
    z_end = max(z_end, 1.0 + lo)

    # Zoomrichtung alternieren. Hundertmal hineinzuzoomen ermuedet.
    zoom_in = (index % 2 == 0) or not defaults.alternate
    z0, z1 = (1.0, z_end) if zoom_in else (z_end, 1.0)

    dx, dy = _DIRECTIONS[index % len(_DIRECTIONS)]
    a = defaults.pan_amount
    c0 = (0.5 - dx * a, 0.5 - dy * a)
    c1 = (0.5 + dx * a, 0.5 + dy * a)

    ease = defaults.ease
    engine = defaults.engine

    if spec is not None:
        if spec.z is not None:
            z0, z1 = float(spec.z[0]), float(spec.z[1])
        if spec.c is not None:
            c0 = (float(spec.c[0]), float(spec.c[1]))
            c1 = (float(spec.c[2]), float(spec.c[3]))
        if spec.ease is not None:
            ease = spec.ease
        if spec.engine is not None:
            engine = spec.engine

    return KBMotion(z0=z0, z1=z1, c0=c0, c1=c1, ease=ease, engine=engine)


# --------------------------------------------------------------------------
# Ausdruecke
# --------------------------------------------------------------------------

def _progress(total_frames: int, offset: int, var: str) -> str:
    """Linearer Fortschritt 0..1 ueber die *volle sichtbare Spanne*.

    ``var`` zaehlt die Ausgabeframes des aktuellen Segments; ``offset``
    verschiebt es an seinen Platz innerhalb der Gesamtbewegung. Der Zaehler
    heisst je nach Filter anders: ``zoompan`` kennt ``on``, ``scale`` und
    ``crop`` kennen ``n``. Ein falscher Name faellt nicht als Fehler auf —
    ffmpeg wertet unbekannte Namen als 0 aus, und die Bewegung steht still.
    """
    n = max(1, total_frames - 1)
    return f"clip(({var}+{offset})/{n},0,1)"


def _eased(total_frames: int, offset: int, ease: str, var: str = "on") -> str:
    p = _progress(total_frames, offset, var)
    if ease == "linear":
        return p
    # Smoothstep. Linearer Zoom sieht mechanisch aus.
    return f"({p})*({p})*(3-2*({p}))"


def _lerp(a: float, b: float, e: str) -> str:
    if abs(a - b) < 1e-9:
        return f"{a:.6f}"
    return f"({a:.6f}+({b - a:.6f})*({e}))"


def zoompan_filter(m: KBMotion, *, total_frames: int, offset: int,
                   size: tuple[int, int], fps: float) -> str:
    """Der Default-Pfad.

    ``d=1`` bei geloopter Eingabe ist das saubere Idiom fuer "ein Ausgabeframe
    pro Eingabeframe". ``fps=`` im Filter ist zwingend — sonst resampled
    ``zoompan`` intern auf 25.
    """
    w, h = size
    e = _eased(total_frames, offset, m.ease)
    z = _lerp(m.z0, m.z1, e)
    cx = _lerp(m.c0[0], m.c1[0], e)
    cy = _lerp(m.c0[1], m.c1[1], e)
    # x/y sind die *linke obere Ecke* des Ausschnitts; c ist dessen Mitte.
    x = f"max(0,min(iw-iw/zoom,({cx})*iw-iw/zoom/2))"
    y = f"max(0,min(ih-ih/zoom,({cy})*ih-ih/zoom/2))"
    return (f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={_rate(fps)}")


def scale16_filter(m: KBMotion, *, total_frames: int, offset: int,
                   size: tuple[int, int], fps: float) -> str:
    """Der 16-Bit-Ausweichpfad ohne ``zoompan``.

    ``scale`` mit ``eval=frame`` skaliert die Quelle pro Frame auf
    ``(W*zoom, H*zoom)``; ``crop`` schneidet daraus das feste Ausgabefenster.
    Weil die Quelle doppelt so gross wie die Ausgabe ist, wird dabei immer
    *herunter*skaliert — das haelt die Kosten im Rahmen.

    ``setsar=1`` ist nicht optional: ``scale`` mit per-Frame-Ausdruecken laesst
    die Sample Aspect Ratio driften (verifiziert: ``sar:5792/5805``), und
    uneinheitliche SAR bringt spaeter das Concat zu Fall.

    .. warning::
       Die Crop-Position darf **nicht** ueber ``iw``/``ih`` formuliert werden.
       ``crop`` bindet die Eingangsmasse an die Filterkonfiguration; wenn die
       vorgelagerte ``scale`` ihre Ausgabegroesse pro Frame aendert, sieht
       ``crop`` je nach *erstem* Frame eines Segments einen anderen Wert.
       Zwei Segmente derselben Bewegung liefen dadurch auseinander — genau der
       Positionssprung an den Fenstergrenzen, den Kriterium 12 ausschliesst.
       Deshalb wird die skalierte Groesse hier geschlossen ausgerechnet und in
       den Ausdruck eingesetzt.
    """
    w, h = size
    e = _eased(total_frames, offset, m.ease, var="n")
    z = _lerp(m.z0, m.z1, e)
    cx = _lerp(m.c0[0], m.c1[0], e)
    cy = _lerp(m.c0[1], m.c1[1], e)
    # Ganzzahlig, aber nicht auf gerade Werte gerundet: in yuv444p16le gibt es
    # kein Chroma-Subsampling, und jede zusaetzliche Stufe ist Bewegungsaufloesung.
    sw = f"trunc({w}*({z}))"
    sh = f"trunc({h}*({z}))"
    x = f"max(0,min(({sw})-{w},({cx})*({sw})-{w}/2))"
    y = f"max(0,min(({sh})-{h},({cy})*({sh})-{h}/2))"
    return (f"format=yuv444p16le,"
            f"scale=w='{sw}':h='{sh}':eval=frame:flags=lanczos,"
            f"crop={w}:{h}:x='{x}':y='{y}',setsar=1")


def kb_filter(m: KBMotion, *, total_frames: int, offset: int,
              size: tuple[int, int], fps: float) -> str:
    if m.engine == "scale16":
        return scale16_filter(m, total_frames=total_frames, offset=offset,
                              size=size, fps=fps)
    return zoompan_filter(m, total_frames=total_frames, offset=offset,
                          size=size, fps=fps)


def _rate(fps: float) -> str:
    if abs(fps - round(fps)) < 1e-9:
        return str(int(round(fps)))
    for base in (24, 30, 60, 120):
        if abs(fps - base * 1000 / 1001) < 1e-4:
            return f"{base * 1000}/1001"
    return f"{fps:.6f}"


def still_input_args(src: str, *, fps: float, frames: int) -> list[str]:
    """Eingabe-Argumente fuer ein Standbild-Segment."""
    duration = frames / fps
    return ["-loop", "1", "-framerate", _rate(fps), "-t", f"{duration:.6f}", "-i", src]


def frames_arg(frames: int) -> list[str]:
    """Exakte Framezahl erzwingen — Rundung darf hier nichts kosten."""
    return ["-frames:v", str(int(frames))]


def clip_input_args(src: str, *, start: float, frames: int, fps: float) -> list[str]:
    """Eingabe-Argumente fuer einen Ausschnitt aus einem Clip-Intermediate.

    ``-ss`` steht auch hier vor ``-i``; das Intermediate ist All-Intra, das
    Seeking also framegenau und billig.
    """
    duration = frames / fps
    args: list[str] = []
    if start > 0:
        args += ["-ss", f"{start:.6f}"]
    args += ["-i", src, "-t", f"{duration + 1.0 / fps:.6f}"]
    return args


def xfade_expr(mode: str, duration_frames: int, fps: float) -> str:
    """``xfade``-Filterausdruck fuer ein Uebergangs-Segment."""
    d = duration_frames / fps
    transition = _XFADE_MODES.get(mode, "fade")
    return f"xfade=transition={transition}:duration={d:.6f}:offset=0"


_XFADE_MODES = {
    "dissolve": "fade", "fade": "fade", "fadeblack": "fadeblack",
    "fadewhite": "fadewhite", "wipeleft": "wipeleft", "wiperight": "wiperight",
    "wipeup": "wipeup", "wipedown": "wipedown", "slideleft": "slideleft",
    "slideright": "slideright", "smoothleft": "smoothleft",
    "smoothright": "smoothright", "circleopen": "circleopen",
    "circleclose": "circleclose", "pixelize": "pixelize", "hblur": "hblur",
}


def known_modes() -> list[str]:
    return sorted(_XFADE_MODES)


def zoom_from_duration(duration: float, defaults: KBDefaults) -> float:
    """Der Zoomfaktor, den ``plan_motion`` fuer diese Dauer waehlen wuerde."""
    lo, hi = defaults.zoom_total
    return max(min(1.0 + defaults.zoom_rate * duration, 1.0 + hi), 1.0 + lo)


def describe(m: KBMotion, duration: float) -> str:
    direction = "hinein" if m.z1 > m.z0 else "heraus"
    return (f"{direction}, {abs(m.z1 - m.z0) * 100:.0f} % ueber {duration:.2f} s, "
            f"{m.ease}, {m.engine}")


def clamp_unit(v: float) -> float:
    return max(0.0, min(1.0, v))


def sanity_check(m: KBMotion) -> list[str]:
    """Warnt vor Bewegungen, die sichtbar schlecht aussehen."""
    out: list[str] = []
    if max(m.z0, m.z1) > 2.0:
        out.append(f"Zoom bis {max(m.z0, m.z1):.2f}x — bei 2x Subpixel-Vorrat wird "
                   f"das Bild weich")
    if abs(m.z1 - m.z0) < 0.005 and math.dist(m.c0, m.c1) < 0.005:
        out.append("praktisch keine Bewegung — das Bild steht still")
    return out
