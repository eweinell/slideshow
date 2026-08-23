# Nachtrag 5.5 zum Briefing: Originalton der Videoclips

**Status:** umgesetzt · **Betrifft:** `clipaudio.py`, `preprocess.py`,
`build.py`, `render.py`, `mlt.py` · **Vorbedingung:** keine

Dieser Abschnitt steht **nicht** im ursprünglichen Briefing. Er erweitert
dessen Kapitel 5 (Preprocessing) und wird im Code als „5.5" zitiert, damit die
Verweise dieselbe Form haben wie überall sonst.

Das Briefing verwirft den Originalton ausnahmslos — Abschnitt 7 baut die ganze
Framerate-Politik sogar darauf auf („weil der Originalton verworfen wird,
entfällt beim Umkonformieren die übliche Hürde"). Das bleibt der Default. Nur
gibt es Clips, deren Ton der Film braucht: das Lachen der Kinder, die Brandung,
der Moment, in dem jemand etwas sagt.

---

## 1. Zusage

Auf Anforderung — nie von selbst — trägt ein Clip-Segment seinen Originalton
in den Master. Für seine Dauer geht die Musik zurück; wie weit, entscheidet
`defaults.clip_audio.music` (0.0 = stumm).

## 2. Bedienung

In `overrides.yaml` — dem Ort für alles, was einen Neubau überleben soll.
`edit.yaml` wird nicht von Hand editiert; sie hält nur das Ergebnis fest.

```yaml
version: 1

defaults:
  clip_audio: {enabled: true, music: 0.0, gain: 1.0, fade: 0.25}

media:
  clip_kinder_lachen: {audio: true, music: 0.2}   # Musik bleibt leise stehen
  clip_wind_am_kliff: {audio: false}              # dieser eine nicht
  clip_brandung:      {gain: 0.6, fade: 0.6}      # zu laut, weicher einblenden
```

| Schlüssel | Ort | Bedeutung |
|---|---|---|
| `enabled` | `defaults` | Originalton für alle Clips |
| `audio` | `media` | für dieses eine Medium, schlägt `enabled` |
| `music` | beide | Musikpegel *während* eines Clips mit Ton, `0` = stumm |
| `gain` | beide | Pegel des Originaltons |
| `fade` | beide | Ein-/Ausblende an den Clipgrenzen |

Die vier globalen Werte haben zusätzlich Schalter am `build`:
`--clip-audio`, `--no-clip-audio`, `--clip-audio-music`, `--clip-audio-gain`,
`--clip-audio-fade`. Sie gelten für einen Lauf.

**Präzedenz** — dieselbe wie für jedes andere Feld:

    media:-Eintrag  >  Kommandozeile  >  defaults: in overrides.yaml  >  eingebaut

Also: `--no-clip-audio` schaltet die filmweite Vorgabe ab, ein ausdrückliches
`audio: true` an einem Clip bleibt stehen — und `--clip-audio` überrennt
umgekehrt kein `audio: false`. Wer das nicht will, ändert den Eintrag.

**Verankert an der Medien-ID, nicht am Segmentindex.** Ein Index verschiebt
sich beim nächsten eingefügten Bild; eine Kennung nicht. Das ist dieselbe
Entscheidung, die `chapters.yaml` (`before:`) und die Ken-Burns-Richtung
(`motion_key(src)`) schon getroffen haben — und der Grund, warum es für die
Auswahl bewusst *kein* Kommandozeilenargument gibt.

**Der Rückweg steht auch offen.** Wer den Ton erst in `edit.yaml` ausprobiert,
lässt ihn von `slideshow overrides` sichern: `audio`, `gain`, `music` und
`fade` gehören zu den Feldern, die `diff_edit` erkennt und als `media:`-Eintrag
herausschreibt.

In der Edit-List sieht das Ergebnis so aus:

```yaml
segments:
  - {type: clip, src: cache/clip_007.mov, in: 0.0, out: 4.2, audio: true, music: 0.2}
```

## 3. Entwurfsentscheidungen

**Der Ton bleibt aus den Segmenten heraus.** Die Videosegmente sind stumm
(`-an`) und bleiben es; der Originalton wird beim Muxen als eigene Spur
untergemischt. Damit bleibt Prinzip 2 unangetastet: das Ein- und Ausschalten
macht **kein** gerendertes Segment ungültig — es läuft nur der Mux erneut.
`tests/test_clipaudio.py` prüft genau das an den Cache-Keys.

**Die Musik wird gedückt, nicht angehalten.** Ein Pausieren wäre das
Naheliegende und bricht Prinzip 3: die Timeline rechnet in absoluten
Zeitpunkten auf der Tonspur, Sample 0 ist ihr Nullpunkt. Hielte die Musik für
die Dauer eines Clips an, verschöbe sich jeder folgende Beat gegen jeden
folgenden Schnitt — der Sync wäre ab dem ersten Clip mit Ton hinüber.

Der Verlauf entsteht als `volume`-Ausdruck mit `eval=frame`: über jedem
Tonschnipsel liegt ein Trapez, das in `fade` Sekunden auf 1 steigt und wieder
fällt; der Musikpegel ist `1 − (1 − music) · Hüllkurve`.

**Der Ton macht das Retiming mit.** Wird ein Clip nach Abschnitt 7 per
`setpts` konformiert, läuft sein Ton per `atempo` im selben Verhältnis —
Tempo angepasst, Tonhöhe unverändert. `atempo` akzeptiert je Instanz nur
[0.5, 2.0]; größere Faktoren werden gekettet.

**Extrahiert wird vorsorglich, verwendet nur auf Anforderung.**
`preprocess` legt `cache/<id>.audio.flac` neben das Intermediate, sobald der
Clip überhaupt eine Tonspur hat — im selben Ausschnitt und derselben Zeitbasis
wie das Bild. Das kostet fast nichts und erspart einen zweiten
Preprocessing-Lauf, wenn die Entscheidung später fällt. `--no-audio-extract`
schaltet es ab.

**Die Ausblende am Filmende gilt für den Mix, nicht nur für die Musik.**
Ein Clip, der bis zuletzt läuft, blendet zusammen mit dem Bild aus — sonst
stünde das Bild in Schwarz, während der Originalton weiterläuft. Umgesetzt als
`afade` hinter dem `amix`, nicht in einem einzelnen Zweig.

**Der Ton endet mit dem Clip, nicht mit dem Slot.** Reicht ein Slot über den
Clip hinaus, hält das Bild sein letztes Frame (6.6). Der Ton kann das nicht.
Die Musik käme sonst über einem Standbild minutenlang nicht zurück — deshalb
wird der Schnipsel auf die tatsächlich vorhandene Tonlänge geklemmt.

**Kein `loudnorm` auf dem Clipton.** Die Musik ist auf −16 LUFS normalisiert
(5.3), der Originalton bewusst nicht: eine Zweipass-Normalisierung plättet
Raumeindruck und Dynamik gerade dort, wo sie den Wert ausmachen. Wer den Pegel
anpassen will, dreht an `gain`.

## 4. Grenzen

* Ein Clip ohne Tonspur wird übersprungen — mit Warnung, nie stillschweigend.
  Wer ihn ausdrücklich per Muster benannt hat, erfährt zusätzlich, warum.
* Der Kdenlive-Export legt die Schnipsel als eigene Tonspur ab, dückt die
  Musik dort aber nicht: wer das Projekt in einem Schnittprogramm öffnet, will
  von Hand mischen.
* Der Musikpegel ist je Clip einstellbar, weil die Cues auf der Timeline
  disjunkt sind. Überlappten sie je, addierten sich die Absenkungen — die
  Klemmung `max(0, …)` fängt das ab, statt negativ zu werden.
* Die akustische Beurteilung bleibt manuell — siehe
  [`manuelle-checks.md`](manuelle-checks.md), Punkt 7.
