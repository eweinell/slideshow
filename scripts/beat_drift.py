"""Wie genau sitzt die Regionenkarte auf der Musik?

Aufruf:  python scripts/beat_drift.py <projekt>

Zwei Messungen an beats.yaml und cache/mix.flac:

1. Grenzen zwischen zwei Beat-Regionen: Tempounterschied und Phasensprung.
   Liegt das Tempo innerhalb der Verschmelztoleranz, ist die Grenze ein
   Messfenster und kein Tempowechsel.
2. Drift innerhalb langer Beat-Regionen: Versatz zwischen Rasterpunkt und
   tatsaechlichem Anschlag, in 10-s-Fenstern.

Messgrundlage zu docs/pruefbericht-2026-11.md, Abschnitt 5 und AP5. Liest nur.
"""

from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from slideshow.beats import (ANALYSIS_SR, BEAT_MERGE_BPM_TOL, _FINE_HOP,  # noqa: E402
                             load_mono)

FENSTER = 10.0      # Sekunden je Messfenster
LANG = 40.0         # ab dieser Dauer wird die Drift gemessen


def grenzen(regionen: list[dict]) -> None:
    paare = []
    for a, b in zip(regionen, regionen[1:]):
        if a["type"] == b["type"] == "beat":
            periode = 60.0 / a["bpm"]
            d = (b["offset"] - a["offset"]) % periode
            sprung = min(d, periode - d)
            paare.append((abs(b["bpm"] - a["bpm"]) / a["bpm"], sprung, sprung / periode))
    gleich = [p for p in paare if p[0] <= BEAT_MERGE_BPM_TOL]
    print(f"Grenzen beat->beat: {len(paare)}, davon Tempo innerhalb "
          f"{100 * BEAT_MERGE_BPM_TOL:g} %: {len(gleich)}")
    if gleich:
        ms = np.array([p[1] for p in gleich]) * 1000
        anteil = np.array([p[2] for p in gleich])
        print(f"  Phasensprung dort: Median {np.median(ms):.0f} ms, Max {ms.max():.0f} ms; "
              f"Median {np.median(anteil):.2f} Beat, ueber 0,25 Beat: "
              f"{int((anteil > 0.25).sum())}, ueber 0,10 Beat: {int((anteil > 0.10).sum())}")


def anstieg(y: np.ndarray) -> tuple[np.ndarray, float]:
    """Energieanstieg in feiner Rasterung — dieselbe Groesse wie in refine_offset."""
    n = 1 + (len(y) - 1) // _FINE_HOP
    pad = np.pad(y, (0, max(0, n * _FINE_HOP - len(y))))
    rms = np.sqrt((pad[:n * _FINE_HOP].reshape(n, _FINE_HOP) ** 2).mean(axis=1) + 1e-12)
    return np.maximum(np.diff(rms, prepend=rms[:1]), 0.0), ANALYSIS_SR / _FINE_HOP


def versatz(region: dict, t0: float, t1: float, flux: np.ndarray,
            rate: float) -> float | None:
    """Medianer Versatz Raster -> Anschlag in [t0, t1], Suchfenster +-0,25 Beat."""
    periode = 60.0 / region["bpm"]
    w = int(round(0.25 * periode * rate))
    t = region["offset"] + math.ceil((t0 - region["offset"]) / periode) * periode
    werte = []
    while t < t1:
        mitte = int(round(t * rate))
        lo, hi = max(0, mitte - w), min(len(flux), mitte + w + 1)
        seg = flux[lo:hi]
        if len(seg) > 3 and seg.max() > 0:
            werte.append((lo + int(np.argmax(seg))) / rate - t)
        t += periode
    return float(np.median(werte)) if len(werte) >= 6 else None


def lage(regionen: list[dict], flux: np.ndarray, rate: float, fps: float) -> None:
    """Sitzt das Raster im Mittel auf dem Anschlag — oder systematisch daneben?

    Ein gemeinsamer Versatz aller Regionen in dieselbe Richtung ist kein
    Tempofehler, sondern ein Fehler der Onset-Erkennung: der unzentrierte
    numpy-Pfad legte jedes Raster rund 50 ms zu frueh, der Anschlag erscheint
    hier dann bei etwa +50 ms.
    """
    werte = []
    for r in regionen:
        if r["type"] != "beat" or r["end"] - r["start"] < FENSTER:
            continue
        v = versatz(r, r["start"], r["end"], flux, rate)
        if v is not None:
            werte.append(v * 1000)
    if not werte:
        return
    q1, med, q3 = np.percentile(werte, [25, 50, 75])
    print(f"\nLage ueber {len(werte)} Beat-Regionen (Anschlag minus Raster): "
          f"Median {med:+.0f} ms, Quartile {q1:+.0f} / {q3:+.0f} ms "
          f"= {med * fps / 1000:+.1f} Frames bei {fps:g} fps")


def drift(regionen: list[dict], flux: np.ndarray, rate: float, fps: float) -> None:
    print(f"\nDrift in Beat-Regionen ab {LANG:g} s (Versatz Raster -> Anschlag):")
    schlimmste = []
    for r in regionen:
        if r["type"] != "beat" or r["end"] - r["start"] < LANG:
            continue
        werte, t = [], r["start"]
        while t + FENSTER <= r["end"] + 1e-6:
            v = versatz(r, t, t + FENSTER, flux, rate)
            if v is not None:
                werte.append(v)
            t += FENSTER
        if not werte:
            continue
        periode = 60.0 / r["bpm"]
        gross = max(werte, key=abs)
        schlimmste.append(abs(gross))
        print(f"  [{r['start']:7.1f}-{r['end']:7.1f}] {r['end'] - r['start']:5.0f} s "
              f"@ {r['bpm']:6.2f} bpm: Anfang {werte[0] * 1000:+5.0f} ms, Ende "
              f"{werte[-1] * 1000:+5.0f} ms, groesster {gross * 1000:+5.0f} ms "
              f"({abs(gross) / periode:.2f} Beat, {abs(gross) * fps:.1f} Frames)")
    if schlimmste:
        print(f"  groesster Versatz ueberhaupt: {max(schlimmste) * 1000:.0f} ms "
              f"= {max(schlimmste) * fps:.1f} Frames bei {fps:g} fps")


def main(root: pathlib.Path) -> None:
    karte = yaml.safe_load((root / "beats.yaml").read_text(encoding="utf-8"))
    regionen = karte["regions"]
    beat = [r for r in regionen if r["type"] == "beat"]
    dauern = np.array([r["end"] - r["start"] for r in beat]) if beat else np.array([0.0])
    print(f"{root.name}: {len(regionen)} Regionen, {len(beat)} beat "
          f"({dauern.sum():.0f} s, Median {np.median(dauern):.1f} s), "
          f"Onset-Pfad laut Karte: {karte.get('audio', {}).get('onset', 'nicht vermerkt')}")
    grenzen(regionen)

    fps = 50.0
    edit = root / "edit.yaml"
    if edit.exists():
        fps = float(yaml.safe_load(edit.read_text(encoding="utf-8")).get("fps") or fps)
    flux, rate = anstieg(load_mono(root / "cache" / "mix.flac"))
    lage(regionen, flux, rate, fps)
    drift(regionen, flux, rate, fps)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(pathlib.Path(sys.argv[1]))
