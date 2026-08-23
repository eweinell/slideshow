"""Originalton der Clips (Nachtrag 5.5, ``docs/originalton-clips.md``).

Standardmaessig traegt der Film nur die Musik; der Ton der Videoclips wird
verworfen. Auf Wunsch laeuft er stattdessen mit, und die Musik geht darunter
weg.

**Eingestellt wird das in** ``overrides.yaml``, dem Ort fuer alles, was einen
Neubau ueberleben soll — global unter ``defaults:`` und je Clip unter
``media:``, verankert an der Medien-ID:

.. code-block:: yaml

   defaults:
     clip_audio: {enabled: true, music: 0.0, gain: 1.0, fade: 0.25}
   media:
     clip_kinder_lachen: {audio: true, music: 0.2}
     clip_wind_am_kliff: {audio: false}

``edit.yaml`` haelt das Ergebnis als ``audio:``/``gain:``/``music:``/``fade:``
je Clip-Segment fest — sie bleibt lesbar und bleibt die Single Source of Truth,
aber niemand muss sie dafuer anfassen. Der Weg dorthin ist derselbe wie fuer
Standzeit und Clipschnitt: :mod:`slideshow.overrides` uebersetzt den Eintrag in
gewoehnliche Absicht am Intent, und dieses Modul sieht nur noch Werte.

Drei Entscheidungen tragen dieses Modul:

**1. Der Ton bleibt aus den Videosegmenten heraus.** Die Segmente sind
stumm (``-an``) und bleiben es. Der Originalton wird beim Muxen als eigene
Spur untergemischt, nicht in die Segmente encodiert. Damit bleibt Prinzip 2
unangetastet: das Ein- und Ausschalten des Originaltons macht **kein**
einziges Segment im Cache ungueltig, es kostet nur einen neuen Mux-Lauf.

**2. Die Musik wird gedueckt, nicht angehalten.** Ein Pausieren waere das
Naheliegende, ist hier aber ausgeschlossen: die gesamte Timeline rechnet in
absoluten Zeitpunkten auf der Tonspur, Sample 0 ist ihr Nullpunkt (Prinzip 3).
Hielte die Musik fuer die Dauer eines Clips an, verschoebe sich jeder folgende
Beat gegen jeden folgenden Schnitt. ``defaults.clip_audio.music`` bestimmt
deshalb den *Pegel*, auf den die Musik zurueckgeht — 0.0 (Default) ist stumm,
0.15 laesst sie leise darunter stehen.

**3. Jeder Wert gilt auch je Clip.** ``gain``, ``music`` und ``fade`` stehen
nicht nur global, sondern an jedem Cue: der eine Clip verdient Stille unter
sich, der naechste laesst die Musik leise stehen. Der Musikpegel entsteht
deshalb als *Summe* der Absenkungen ueber disjunkten Fenstern, nicht als eine
Zahl fuer den ganzen Film.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .models import ClipAudioDefaults, EditList
from .planner import Intent, Plan, to_time

log = logging.getLogger("slideshow.clipaudio")

SAMPLE_RATE = 48000

#: Kuerzer als das lohnt kein eigener Tonschnipsel — ein paar Samples Rest
#: waeren nur ein Knacksen.
_MIN_CUE = 0.05

#: ``atempo`` akzeptiert je Instanz nur [0.5, 2.0]; groessere Faktoren werden
#: gekettet.
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0


def wants_audio_flag(flag: bool | None, defaults: ClipAudioDefaults) -> bool:
    """Praezedenz: Segment schlaegt Default (wie ueberall in der Edit-List)."""
    return defaults.enabled if flag is None else bool(flag)


def wants_audio(intent: Intent, defaults: ClipAudioDefaults) -> bool:
    if intent.kind != "clip":
        return False
    return wants_audio_flag(intent.audio, defaults)


def atempo_chain(speed: float) -> list[str]:
    """``atempo``-Kette fuer eine Geschwindigkeit ausserhalb von [0.5, 2.0].

    Der Ton muss dieselbe Streckung mitmachen wie das Bild (Abschnitt 7),
    sonst laeuft er dem Clip davon. ``atempo`` aendert das Tempo ohne die
    Tonhoehe zu verschieben — bei den hier ueblichen Faktoren (0.83 fuer
    50p -> 60p) ist das Ergebnis unauffaellig.
    """
    if abs(speed - 1.0) < 1e-9:
        return []
    if speed <= 0:
        raise ValueError(f"unplausible Geschwindigkeit: {speed}")
    parts: list[float] = []
    rest = speed
    while rest > _ATEMPO_MAX:
        parts.append(_ATEMPO_MAX)
        rest /= _ATEMPO_MAX
    while rest < _ATEMPO_MIN:
        parts.append(_ATEMPO_MIN)
        rest /= _ATEMPO_MIN
    parts.append(rest)
    return [f"atempo={p:.9f}" for p in parts]


# --------------------------------------------------------------------------
# Was wann klingt
# --------------------------------------------------------------------------

@dataclass
class ClipAudioCue:
    """Ein Stueck Originalton an seinem Platz auf der Ausgabetimeline."""

    slot_index: int
    #: Projektrelativer Pfad der extrahierten Tonspur.
    src: str
    #: Startzeitpunkt auf der **Ausgabe**timeline (bei ``--range`` also
    #: relativ zum Anfang des gerenderten Ausschnitts).
    start: float
    duration: float
    #: Startpunkt innerhalb der Tonspur.
    offset: float
    gain: float = 1.0
    #: Pegel, auf den die Musik *unter diesem* Cue zurueckgeht. Je Cue und
    #: nicht global, weil ``overrides.yaml`` ihn je Medium setzen kann: der
    #: eine Clip verdient Stille, der naechste laesst die Musik stehen.
    music: float = 0.0
    fade: float = 0.25
    label: str = ""

    @property
    def end(self) -> float:
        return self.start + self.duration

    def effective_fade(self) -> float:
        """Blende, die in den Cue passt — nie laenger als seine halbe Dauer."""
        return max(0.0, min(self.fade, self.duration / 2.0))


def collect_cues(plan: Plan, edit: EditList, manifest, *, start_f: int = 0,
                 end_f: int | None = None) -> tuple[list[ClipAudioCue], list[str]]:
    """Sammelt die Tonschnipsel fuer den gerenderten Ausschnitt.

    Gibt zusaetzlich die Warnungen zurueck: ein Clip, dessen Originalton
    angefordert wurde, den es aber nicht gibt, wird uebersprungen — aber
    niemals stillschweigend.
    """
    cfg = edit.defaults.clip_audio
    fps = plan.fps
    end_f = plan.total_frames if end_f is None else end_f
    cues: list[ClipAudioCue] = []
    warnings: list[str] = []

    for i, slot in enumerate(plan.slots):
        if not wants_audio(slot.intent, cfg):
            continue
        src, offset0, available, note = _audio_source(slot.intent.src, manifest)
        if src is None:
            warnings.append(f"{slot.intent.src}: {note}")
            continue

        # Auf den gerenderten Ausschnitt beschneiden.
        s_f = max(slot.start_f, start_f)
        e_f = min(slot.end_f, end_f)
        if e_f <= s_f:
            continue

        # Startpunkt in der Tonspur — dieselbe Rechnung wie fuer das Bild
        # (``render._clip_stream``), damit Ton und Bild denselben Ausschnitt
        # zeigen.
        offset = max(0.0, slot.clip_in + to_time(s_f - slot.start_f, fps) - offset0)
        duration = to_time(e_f - s_f, fps)
        # Reicht der Slot ueber den Clip hinaus, haelt das Bild sein letztes
        # Frame (der Planer sagt das an). Der Ton kann das nicht: er waere
        # schlicht zu Ende. Die Musik darf deshalb nur so lange unten bleiben,
        # wie tatsaechlich etwas zu hoeren ist — sonst dueckt ein gestreckter
        # Schlussclip die Musik ueber eine halbe Minute Standbild weg.
        rest = max(0.0, available - offset)
        if rest <= _MIN_CUE:
            warnings.append(f"{slot.intent.src}: Originalton angefordert, aber ab "
                            f"{offset:.2f} s ist die Tonspur zu Ende")
            continue
        duration = min(duration, rest)

        cues.append(ClipAudioCue(
            slot_index=i, src=src, start=to_time(s_f - start_f, fps),
            duration=duration, offset=offset,
            gain=_wert(slot.intent.audio_gain, cfg.gain),
            music=_wert(slot.intent.audio_music, cfg.music),
            fade=_wert(slot.intent.audio_fade, cfg.fade),
            label=slot.intent.src))

    return (cues, warnings)


def _wert(eigen: float | None, vorgabe: float) -> float:
    """Praezedenz wie ueberall: was am Medium steht, schlaegt den Default."""
    return vorgabe if eigen is None else float(eigen)


def _audio_source(cache_path: str, manifest) -> tuple[str | None, float, float, str]:
    """Tonspur, Zeitbasis-Offset und Laenge zu einem Clip-Intermediate."""
    if manifest is None:
        return (None, 0.0, 0.0, "ohne Manifest ist die Tonspur nicht auffindbar — "
                                "`render --manifest` angeben")
    item = manifest.by_cache_path(cache_path)
    if item is None:
        return (None, 0.0, 0.0, "steht nicht im Manifest")
    if not item.audio_cache_path:
        if item.clip is not None and not item.clip.has_audio:
            return (None, 0.0, 0.0, "Originalton angefordert, aber der Clip hat "
                                    "keine Tonspur")
        return (None, 0.0, 0.0, "Originalton angefordert, aber nicht extrahiert — "
                                "`slideshow preprocess` erneut laufen lassen")
    info = item.clip
    available = (info.cache_duration or info.effective_duration) if info else 0.0
    return (item.audio_cache_path, (info.cache_offset if info else 0.0), available, "")


# --------------------------------------------------------------------------
# Filtergraph
# --------------------------------------------------------------------------

def music_volume_expr(cues: list[ClipAudioCue]) -> str | None:
    """Lautstaerkeverlauf der Musik als ``volume``-Ausdruck.

    Ueber jedem Cue liegt ein Trapez: in ``fade`` Sekunden auf 1 hoch, am Ende
    wieder herunter, gewichtet mit dem Absenkbetrag ``1 - music`` *dieses*
    Cues. Der Musikpegel ist ``1 - Summe der Absenkungen``. Die Summe ist
    zulaessig, weil die Cues auf der Timeline disjunkt sind — Slots
    ueberlappen nicht; das ``max(0, ...)`` ist nur die Sicherheitsklemmung.
    """
    terms = []
    for c in cues:
        senkung = 1.0 - c.music
        if senkung <= 0:
            continue                    # dieser Clip laesst die Musik stehen
        f = c.effective_fade()
        if f <= 0:
            trapez = f"between(t,{c.start:.6f},{c.end:.6f})"
        else:
            trapez = (f"max(0,min(1,min((t-{c.start:.6f})/{f:.6f},"
                      f"({c.end:.6f}-t)/{f:.6f})))")
        terms.append(f"{senkung:.6f}*{trapez}")
    if not terms:
        return None
    return f"max(0,1-({'+'.join(terms)}))"


def filtergraph(cues: list[ClipAudioCue], *, first_input: int,
                music_input: int | None,
                fade_out: tuple[float, float] | None = None,
                sample_rate: int = SAMPLE_RATE) -> tuple[str, str]:
    """Baut den Audio-Filtergraph fuer den Mux-Lauf.

    ``first_input`` ist der Index des ersten Cue-Eingangs in der
    ffmpeg-Kommandozeile; die Cues liegen danach in der Reihenfolge von
    ``cues``. ``fade_out`` ist ``(Startzeitpunkt, Dauer)`` der Ausblende am
    Filmende und gilt fuer den **fertigen Mix** — ein Clip, der bis zuletzt
    laeuft, blendet damit zusammen mit dem Bild aus. Gibt ``(graph, label)``
    zurueck.
    """
    if not cues and music_input is None:
        raise ValueError("weder Musik noch Clipton — nichts zu mischen")

    common = f"aresample={sample_rate},aformat=channel_layouts=stereo"
    parts: list[str] = []
    labels: list[str] = []

    if music_input is not None:
        expr = music_volume_expr(cues)
        chain = common
        if expr:
            # eval=frame: der Ausdruck wird je Frame ausgewertet, nur so
            # entsteht ueberhaupt ein Verlauf.
            chain += f",volume=volume='{expr}':eval=frame"
        parts.append(f"[{music_input}:a]{chain}[music]")
        labels.append("[music]")

    for n, c in enumerate(cues):
        chain = [f"atrim=start={c.offset:.6f}:duration={c.duration:.6f}",
                 "asetpts=PTS-STARTPTS", common]
        f = c.effective_fade()
        if f > 0:
            chain.append(f"afade=t=in:st=0:d={f:.6f}")
            chain.append(f"afade=t=out:st={c.duration - f:.6f}:d={f:.6f}")
        if abs(c.gain - 1.0) > 1e-9:
            chain.append(f"volume={c.gain:.6f}")
        delay = int(round(c.start * 1000))
        if delay > 0:
            # all=1: sonst verzoegert adelay nur den linken Kanal.
            chain.append(f"adelay={delay}:all=1")
        parts.append(f"[{first_input + n}:a]{','.join(chain)}[ca{n}]")
        labels.append(f"[ca{n}]")

    # apad haelt die Spur bis zum ``-t`` des Muxers durch; ohne sie endet der
    # Ton frueh, wenn die Musik kuerzer ist als die Timeline. Erst auffuellen,
    # dann ausblenden — dieselbe Reihenfolge wie im Musik-Pfad des Muxers,
    # sonst liefe die Blende ins Leere.
    schluss = "apad"
    if fade_out:
        st, dauer = fade_out
        schluss += f",afade=t=out:st={st:.6f}:d={dauer:.6f}"
    if len(labels) == 1:
        parts.append(f"{labels[0]}{schluss}[aout]")
    else:
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0"
                     f":dropout_transition=0,{schluss}[aout]")
    return (";".join(parts), "[aout]")
