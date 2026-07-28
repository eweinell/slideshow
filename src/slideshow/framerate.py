"""Framerate-Politik (Abschnitt 7).

Der Kern: weil der Originalton verworfen wird, entfaellt beim Umkonformieren
die uebliche Huerde (keine Tonhoehenverschiebung). Material darf deshalb
*reinterpretiert* werden — jeder Quellframe wird genau ein Ausgabeframe.
Das ist deutlich besser als ``minterpolate`` (teuer, Artefakte an
Bewegungskanten) und besser als Frame-Duplikation (Judder in Schwenks).

Mechanik: wir waehlen eine ganzzahlige Frameverdopplung ``dup`` so, dass
``src_fps * dup`` moeglichst nahe an der Zielrate liegt, und konformieren den
Rest 1:1 per ``setpts``. Weil ``src_fps * dup / setpts == target`` gilt, ist
die Zwischenrate immer ein ganzzahliger Teiler der Zielrate — die
CFR-Konformierung dupliziert danach exakt und ohne Judder.

    setpts_factor = (src_fps * dup) / target_fps
    speed         = 1 / setpts_factor

Die Tabelle aus Abschnitt 7 faellt daraus von selbst heraus:

===========  =====  =============  =======================================
Quelle       dup    setpts         Ergebnis
===========  =====  =============  =======================================
Android 30p  2      1.000          exakt x2, verlustfrei, Tempo unveraendert
Android 60p  1      1.000          1:1
Sony 50p     1      0.8333         glattes 60p, 1 Frame je Frame
Sony 25p     2      0.8333         x2 -> 50p, dann wie oben
Sony 100p    1      1.6667         Zeitlupe, ohnehin beabsichtigt
===========  =====  =============  =======================================

.. note::
   Abweichung vom Briefing, bewusst: dort steht fuer 50p -> 60p
   ``setpts=1.2*PTS`` bei gleichzeitig "jeder Frame ein Ausgabeframe". Beides
   zusammen geht nicht auf. Der Faktor 1,2 *streckt* die Zeitbasis; das
   Material laeuft dann mit 41,7 fps in ein 60p-Raster und muss dupliziert
   werden — genau der Judder, den der Trick vermeiden soll. Fuer "1 Frame =
   1 Frame" ist der Faktor ``50/60 = 0.8333``. Damit laeuft Sony-Material
   20 % *schneller* statt in leichter Zeitlupe; die Bewegung ist dafuer
   vollkommen glatt, was das erklaerte Ziel war.

   Wer die Zeitlupe statt des Zeitraffers will, waehlt 50p als Zielrate — dann
   bleibt Sony-Material unveraendert. Der Preis: Android-30p hat zu 50p kein
   ganzzahliges Verhaeltnis und wird seinerseits konformiert. Bei gemischtem
   Material gibt es keine verlustfreie Wahl; :func:`suggest_target_fps`
   quantifiziert den Kompromiss, entschieden wird er vom Nutzer (Abschnitt 15).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Uebliche Zielraten, die zur Auswahl stehen.
CANDIDATE_RATES = (24.0, 25.0, 30.0, 50.0, 60.0)

#: Groesste erlaubte Frameverdopplung, bevor wir es lassen.
_MAX_DUP = 4

#: Ab hier gilt ein Verhaeltnis als "exakt".
_EPS = 1e-6


@dataclass(frozen=True)
class Retime:
    """Das Ergebnis der Konformierung eines Clips auf die Zielrate."""

    src_fps: float
    target_fps: float
    dup: int
    #: Multiplikator fuer ``setpts``. 1.0 bedeutet: kein Retiming noetig.
    setpts: float
    note: str

    @property
    def speed(self) -> float:
        """Wiedergabegeschwindigkeit relativ zum Original."""
        return 1.0 / self.setpts if self.setpts else 1.0

    @property
    def lossless(self) -> bool:
        """True, wenn Tempo und Bewegung unveraendert bleiben."""
        return abs(self.setpts - 1.0) < 1e-9

    def effective_duration(self, source_duration: float) -> float:
        """Dauer im Intermediate — die Zeitbasis, auf die sich in/out beziehen."""
        return source_duration * self.setpts

    def filter_expr(self) -> str | None:
        """``setpts``-Ausdruck fuer die Filterkette, oder None."""
        if self.lossless:
            return None
        return f"setpts={self.setpts:.9f}*PTS"


def plan_retime(src_fps: float, target_fps: float) -> Retime:
    """Bestimmt Frameverdopplung und ``setpts`` fuer einen Clip."""
    if src_fps <= 0:
        return Retime(src_fps, target_fps, 1, 1.0, "Framerate unbekannt, 1:1 uebernommen")

    best_dup, best_cost = 1, math.inf
    for dup in range(1, _MAX_DUP + 1):
        ratio = (src_fps * dup) / target_fps
        cost = abs(math.log(ratio))
        # Exakte Verhaeltnisse deutlich bevorzugen: sie sind verlustfrei.
        if abs(ratio - round(ratio)) < _EPS or abs(1 / ratio - round(1 / ratio)) < _EPS:
            cost -= 1e-3
        if cost < best_cost - 1e-12:
            best_dup, best_cost = dup, cost

    setpts = (src_fps * best_dup) / target_fps
    if abs(setpts - 1.0) < 1e-9:
        setpts = 1.0

    note = _describe(src_fps, target_fps, best_dup, setpts)
    return Retime(src_fps, target_fps, best_dup, setpts, note)


def _describe(src: float, target: float, dup: int, setpts: float) -> str:
    if abs(setpts - 1.0) < 1e-9:
        if dup > 1:
            return f"{src:g}p -> {target:g}p: exakt x{dup}, Frameverdopplung, verlustfrei"
        return f"{src:g}p -> {target:g}p: 1:1"
    speed = 1.0 / setpts
    kind = "Zeitraffer" if speed > 1 else "Zeitlupe"
    prefix = f"x{dup} -> {src * dup:g}p, dann " if dup > 1 else ""
    return (f"{src:g}p -> {target:g}p: {prefix}1 Frame je Frame per setpts={setpts:.4f} "
            f"({speed * 100:.1f} % Geschwindigkeit, milde {kind})")


def suggest_target_fps(clip_fps: dict[float, float],
                       candidates: tuple[float, ...] = CANDIDATE_RATES) -> tuple[float, str]:
    """Schlaegt eine Zielrate vor, statt 60p blind anzunehmen.

    ``clip_fps`` bildet Quellframerate auf Gesamtdauer in Sekunden ab. Die
    Standbilder sind framerate-agnostisch und rendern in jeder Zielrate nativ —
    die Zielrate wird also allein vom Clipmaterial bestimmt.
    """
    if not clip_fps:
        return (60.0, "kein Clipmaterial — die Standbilder sind framerate-agnostisch, "
                      "60p als unauffaelliger Default")

    scored: list[tuple[float, float, float]] = []
    for target in candidates:
        penalty = 0.0
        exact = 0.0
        for fps, weight in clip_fps.items():
            rt = plan_retime(fps, target)
            if rt.lossless:
                exact += weight
            else:
                penalty += weight * abs(math.log(rt.speed))
        scored.append((penalty, -exact, target))
    scored.sort()
    penalty, neg_exact, best = scored[0]

    total = sum(clip_fps.values()) or 1.0
    exact_share = -neg_exact / total
    parts = [f"{fps:g}p: {dur:.1f}s" for fps, dur in sorted(clip_fps.items())]
    reason = (f"Verteilung {{{', '.join(parts)}}} -> {best:g}p "
              f"({exact_share * 100:.0f} % des Materials laufen unveraendert")
    if penalty > 0:
        worst = max(clip_fps, key=lambda f: abs(math.log(plan_retime(f, best).speed or 1)))
        reason += f", staerkste Anpassung bei {worst:g}p"
    reason += ")"
    return (best, reason)
