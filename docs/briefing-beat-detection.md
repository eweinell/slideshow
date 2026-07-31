# Briefing: Beat-Erkennung für durchgehende Tracks

**Status:** umgesetzt nach Entscheidung 1(a), 2(a), 3 · **Betrifft:**
`src/slideshow/beats.py` · **Ergebnis:** [Abschnitt 9](#9-ergebnis-der-umsetzung)

> Die Abschnitte 1–8 beschreiben den **Zustand vor der Umsetzung** und bleiben
> als Messgrundlage unverändert stehen. Was tatsächlich gebaut wurde und wo die
> Abnahme abweicht, steht in Abschnitt 9.

Die Beat-Erkennung gibt bei einem durchgehenden Musikstück vollständig auf und
stuft die gesamte Tonspur als `free` ein. Damit liegt kein einziger Schnitt auf
einem Beat — die Kernzusage des Werkzeugs greift genau im häufigsten Fall
nicht: *ein* Song unter der Slideshow.

Der Befund stammt aus einem Durchlauf mit echtem Material (`testset1/`:
14 Fotos, ein Track von 6:32). Er ist kein Randfall und keine Eigenheit dieses
Stücks — er tritt bei jedem Track auf, der länger als etwa eine Minute ist.

---

## 1. Ausgangslage

`slideshow beats` liefert für den 392,68 s langen Mix genau eine Region:

```yaml
regions:
- {type: free, start: 0.0, end: 392.68, reason: stille+niedrige Rhythmus-Konfidenz}
```

Das Material ist dabei eindeutig rhythmisch. Gegenmessung mit librosa auf
derselben Datei (`cache/mix.flac`):

| Messgröße | Wert |
|---|---|
| Tempo (`beat_track`) | **152,00 BPM** |
| erkannte Beats | 957 |
| Beat-Abstand | 0,3947 s (Median), σ = 0,0126 s |
| Autokorrelations-Peak der Onset-Kurve | **0,578 bei 152,0 BPM** |
| Abweichung der Beats von einem *starren* Raster | rms **1,121 s**, max **2,090 s** |

Die letzte Zeile ist der Kern. Die Schläge sitzen **lokal** sehr eng
(σ = 12,6 ms von Beat zu Beat), aber über 6½ Minuten läuft das Stück gegen ein
starres Raster um bis zu 2,09 s aus dem Takt — mehr als fünf Beat-Längen.

Fährt man `fit_grid` aus `beats.py` über verschieden lange Fenster desselben
Tracks, fällt die Konfidenz monoton mit der Fensterlänge:

| Fensterlänge | BPM (Median) | Konfidenz (Median) | über `CONF_THRESHOLD` (0,55) |
|---|---|---|---|
| 10 s | 77,75 | **0,818** | 38/39 |
| 20 s | 77,50 | 0,780 | 19/19 |
| 30 s | 78,25 | **0,731** | 12/13 |
| 60 s | 150,25 | 0,443 | 2/6 |
| 120 s | 150,00 | 0,330 | 0/3 |
| 240 s | 150,25 | 0,155 | 0/1 |
| **393 s** (was das Werkzeug tut) | 156,75 | **0,130** | **0/1** |

Der Schätzer ist also nicht defekt. Er meldet zutreffend, dass sich über 392 s
kein *starres* Raster legen lässt. Nur zieht das Werkzeug daraus den
denkbar ungünstigsten Schluss.

---

## 2. Diagnose

Zwei Mechanismen greifen ineinander.

### 2.1 Regionen werden nur an Stille getrennt

`detect_regions` (`beats.py:340`) bildet die Regionen aus `loud_spans` — den
Abschnitten zwischen erkannten Stillen (`beats.py:364`). Zusätzlich wird an den
Track-Grenzen aus dem Manifest getrennt (`_split_at`, `beats.py:371`).

Ein einzelner, durchgehend abgemischter Song hat **weder Stille noch innere
Track-Grenzen**. Er ist damit per Konstruktion *eine* Region über die volle
Länge, und `fit_grid` bekommt 392 s am Stück vorgelegt.

Die Ironie steht im Modul-Docstring (`beats.py:3-8`): dort wird ein globales
Raster ausdrücklich verworfen, weil es „nur bei genau einem durchlaufenden
Track korrekt" sei. Die Messung zeigt, dass es **auch dann nicht** korrekt ist.
Die Annahme, die dem Regionenmodell zugrunde liegt, trifft nicht zu.

### 2.2 Warum die Konfidenz rechnerisch zusammenbricht

`fit_grid` (`beats.py:160`) sucht Tempo und Phase gemeinsam: für jedes BPM aus
`_BPM_RANGE` und jede Phase wird die mittlere Onset-Energie an den
Rasterpunkten gescort. Die Konfidenz entsteht aus zwei Anteilen
(`beats.py:205-214`):

```python
ratio = float(vals.mean()) / mean_e          # Überhöhung an den Rasterpunkten
conf  = max(0.0, min(1.0, (ratio - 1.0) / 2.0))
stability = 1.0 - vals.std() / vals.mean()   # Gleichmäßigkeit über die Beats
return ... conf=round(conf * 0.6 + stability * 0.4, 4)
```

Driftet das Stück gegen das starre Raster, wandern die Rasterpunkte
fortschreitend in die Lücken zwischen den Schlägen. Dann gilt:

- `vals.mean()` nähert sich dem globalen Mittel `mean_e` → `ratio → 1` →
  der erste Anteil geht gegen **0**;
- ein Teil der Punkte sitzt noch auf Schlägen, der Rest nicht → `vals.std()`
  steigt → `stability` fällt.

Beide Terme fallen gemeinsam. Genau das misst die Tabelle in Abschnitt 1.

`refine_offset` (`beats.py:223`) kann das nicht auffangen: die Funktion
korrigiert über den **Median** aller Abweichungen ausschließlich die *Phase*,
nicht das Tempo — eine starre Verschiebung des ganzen Rasters. Gegen Drift ist
sie wirkungslos.

### 2.3 Nebenbefund: Oktavfehler

Bei kurzen Fenstern liefert `fit_grid` 77,75 BPM, bei langen 150–157 BPM. Die
Referenz liegt bei 152,00. Kurze Fenster landen also auf dem **halben** Tempo,
obwohl der Prior gegen Oktavfehler (`_prior`, `beats.py:156`) bei 155 BPM
sogar höher gewichtet (0,917) als bei 77,75 BPM (0,785). Der Energie-Score
überstimmt den Prior.

Das ist ein eigenständiges Problem und wird durch die Umstellung auf kurze
Fenster **relevanter**, nicht kleiner: wer künftig je Fenster fittet, fittet
künftig im Bereich, in dem der Oktavfehler auftritt. Es gehört mit in die
Abnahme (Kriterium A4).

---

## 3. Was bereits erledigt ist — und was nicht

Bereits umgesetzt und getestet ist ausschließlich der **Rückfall**: eine
`free`-Region ohne Raster taktet jetzt im konfigurierbaren Standardintervall
`still_seconds`, statt auf ein einziges Standbild zusammenzufallen. Dafür
unterscheidet `Region.quiet` echte Stille von „Musik ohne Raster"
(`models.py`), und `_free_count` (`planner.py`) wertet das aus.

**Das ist Schadensbegrenzung, keine Lösung.** Die Bildwechsel fallen nicht mehr
aus, aber sie liegen weiterhin auf keinem Beat. Dieses Briefing beschreibt die
eigentliche Behebung.

Der Rückfall bleibt in jedem Fall erhalten — er wird weiter gebraucht für
Material, das wirklich kein Raster hat (Ambient, Sprachaufnahmen, Naturton).

---

## 4. Betroffene Stellen

| Ort | Rolle | Änderung |
|---|---|---|
| `beats.py:340` `detect_regions` | erzeugt Regionen aus `loud_spans` | lange Spans zusätzlich unterteilen |
| `beats.py:160` `fit_grid` | fittet Tempo + Phase | unverändert nutzbar, künftig je Fenster |
| `beats.py:223` `refine_offset` | Phasenkorrektur | je Fenster anzuwenden |
| `beats.py:444` `merge_adjacent_free` | verschmilzt `free`-Regionen | Pendant für `beat`-Regionen gleichen Tempos nötig |
| `beats.py:473` `merge_short_regions` | entfernt zu kurze Regionen | muss die neuen Grenzen vertragen |
| `beats.py:508` `validate_tiling` | lückenlose Überdeckung | **muss weiter gelten** |
| `beats.py:271` `snap_region_starts` | zieht Grenzen auf den Beat | an neuen Grenzen prüfen |
| `models.py` `Region` | Datenmodell der Karte | ggf. Schemaerweiterung (Entscheidung 1) |
| `planner.py` `RegionGrid` | rastert Schnittpunkte | je nach Entscheidung 1 betroffen |

---

## 5. Offene Entscheidungen

### Entscheidung 1 — Stückweises Raster oder Tempokurve

**(a) Stückweiser Fit** *(Empfehlung)*
Lange `loud_spans` vor dem Fit in Fenster von 20–30 s unterteilen, je Fenster
`fit_grid` + `refine_offset`, anschließend benachbarte Fenster mit gleichem
Tempo wieder verschmelzen.

- **dafür:** Das Datenmodell bleibt exakt wie es ist — eine Region trägt
  weiterhin genau ein `bpm` und ein `offset`. `beats.yaml`, `RegionGrid`,
  `beat_duration()` und der gesamte Planner bleiben unberührt. Die Messung
  belegt, dass 20–30 s zuverlässig über der Schwelle liegen (0,78 / 0,73).
- **dagegen:** Die Karte bekommt mehr Regionen (grob 13–20 statt 1). An jeder
  Regionsgrenze kann das Tempo minimal springen.

**(b) Tempokurve je Region**
Eine Region trägt eine Stützstellenfolge `(zeit, bpm)` statt eines Skalars.

- **dafür:** physikalisch genauer, keine Sprünge, eine Region bleibt eine Region.
- **dagegen:** Schemaänderung an `beats.yaml`; `beat_duration()` und jede
  Stelle, die ein konstantes Tempo annimmt (`RegionGrid.beat_time`,
  `beat_index_at_or_after`, `distance_in_beats`), müssen umgebaut werden. Die
  handschriftliche Korrigierbarkeit der Karte — laut README ausdrücklich
  gewollt — leidet erheblich.

> **Empfehlung: (a).** Der Nutzen von (b) ist gering, solange (a) die
> Konfidenz nachweislich über die Schwelle hebt; der Eingriff in Schema und
> Planner ist ein Vielfaches größer. (b) bleibt später möglich, ohne (a)
> wegzuwerfen.

### Entscheidung 2 — Umgang mit `CONF_THRESHOLD`

Der Wert 0,55 ist für kurze Fenster richtig kalibriert. Über etwa 30 s hinaus
misst er faktisch Drift statt Rhythmus, ist als Kriterium dort also
bedeutungslos.

**(a) Schwelle unverändert lassen** *(Empfehlung)* — wenn nach Entscheidung 1(a)
ohnehin nur noch Fenster von 20–30 s gefittet werden, arbeitet die Schwelle
durchgehend in dem Bereich, für den sie kalibriert ist. Kein Grund, an ihr zu
drehen.

**(b) Längenabhängige Schwelle** — nur nötig, falls die Fensterlänge doch
variabel wird.

> **Empfehlung: (a)**, mit einer Ergänzung: die Fensterlänge gehört als
> benannte Konstante neben `CONF_THRESHOLD`, mit einem Kommentar, der die
> Kopplung festhält. Wer künftig an einem der beiden Werte dreht, muss den
> anderen mitdenken.

### Entscheidung 3 — Rückwärtskompatibilität

Bestehende `beats.yaml` tragen ein `bpm` je Region und müssen weiter bauen.

Bei Entscheidung 1(a) ist das **geschenkt** — das Format ändert sich nicht,
nur die Anzahl der erzeugten Regionen. Eine von Hand gepflegte Karte mit einer
einzigen Beat-Region über den ganzen Track bleibt gültig und wird respektiert;
neu erzeugt würde sie nicht mehr.

Bei 1(b) wäre eine Migration nötig (Skalar → einelementige Stützstellenfolge).
Das ist ein zusätzliches Argument für (a).

---

## 6. Vorgeschlagene Umsetzung (bei Entscheidung 1a)

1. **Konstante einführen:** `MAX_FIT_WINDOW = 30.0` neben `CONF_THRESHOLD`
   (`beats.py:45`), mit Kommentar zur Kopplung an die Schwelle.
2. **Fenster bilden:** In `detect_regions` jeden `loud_span` länger als
   `MAX_FIT_WINDOW` in möglichst gleich lange Fenster ≤ `MAX_FIT_WINDOW`
   zerlegen (kein Rest-Stummel: `ceil(dauer / MAX_FIT_WINDOW)` gleiche Teile).
   Kürzere Spans bleiben wie bisher unangetastet.
3. **Je Fenster fitten:** `fit_grid` + `refine_offset` unverändert je Fenster
   aufrufen. Die bestehende Verzweigung nach `conf >= CONF_THRESHOLD` bleibt
   wie sie ist — ein Fenster ohne Raster wird `free` und fällt korrekt auf
   `still_seconds` zurück.
4. **Wieder verschmelzen:** Ein `merge_adjacent_beats`-Pendant zu
   `merge_adjacent_free` (`beats.py:444`): benachbarte `beat`-Regionen
   zusammenfassen, wenn ihr Tempo sich um weniger als eine Toleranz (Vorschlag:
   1,5 %) unterscheidet **und** die Phase des zweiten Fensters auf dem
   fortgesetzten Raster des ersten liegt (Vorschlag: < ¼ Beat Abweichung).
   Andernfalls bleibt die Grenze bestehen — dort *hat* sich das Tempo geändert.
5. **Reihenfolge einhalten:** Das Verschmelzen gehört vor
   `merge_short_regions` und `snap_region_starts`; `validate_tiling` bleibt der
   letzte Schritt und muss weiter grün sein.
6. **Bericht:** Die Regionentabelle von `slideshow beats` zeigt künftig mehrere
   Zeilen. Das ist erwünscht — sie ist ausdrücklich zur Sichtprüfung da.

Nicht anzufassen: `Region.quiet`, `_free_count`, der `still_seconds`-Rückfall.
Der bleibt für Material ohne jedes Raster zuständig.

---

## 7. Abnahmekriterien

Messbar, gegen `testset1/cache/mix.flac` und die vorhandenen Fixtures.
Das Skript im Anhang erzeugt A1 und A4 direkt.

- **A1 — Konfidenz:** Über die volle Länge des Tracks liegt die Konfidenz
  **jeder** erzeugten `beat`-Region ≥ `CONF_THRESHOLD` (0,55).
  *Ausgangswert: 0,130 bei einer einzigen Region.*
- **A2 — Abdeckung:** Mindestens **90 %** der 392,68 s werden als `beat`
  eingestuft (heute: 0 %).
- **A3 — Rasterlage:** Die erzeugten Rasterpunkte liegen im Median **≤ 25 ms**
  von den librosa-Beatpositionen entfernt, im Maximum ≤ ½ Beat.
  *Ausgangswert gegen ein starres Raster: rms 1,121 s.*
- **A4 — kein Oktavfehler:** Das Tempo jeder `beat`-Region liegt innerhalb
  **±2 %** von 152,0 BPM — nicht bei 76 und nicht bei 304.
- **A5 — keine Regression an der Fixture:** Der Klick-Track aus
  `slideshow selftest --make-fixtures` (zwei Songs, 120 und 90 BPM, 6 s Stille
  dazwischen) ergibt weiterhin genau **zwei** Beat-Regionen mit korrektem
  Tempo, getrennt durch eine `free`-Region.
  Das ist bei `MAX_FIT_WINDOW = 30.0` **konstruktiv erfüllt**: die Songs sind
  `TrackSpec(120.0, 32)` = 16,0 s und `TrackSpec(90.0, 18)` = 12,0 s lang
  (`fixtures.py:239-240`), liegen also beide unter der Fenstergrenze und werden
  gar nicht erst zerlegt. Wird `MAX_FIT_WINDOW` je unter 16 s gesenkt, fällt
  diese Garantie — dann greift das Verschmelzen aus Schritt 4, und A5 wird zum
  echten Test.
- **A6 — Stille bleibt Stille:** Eine `free`-Region aus echter Stille behält
  `quiet: true` und damit das ruhige Einzelbild
  (`test_stille_bekommt_weiterhin_ein_ruhiges_standbild`).
- **A7 — Kompatibilität:** Eine bestehende `beats.yaml` mit einer einzigen
  Beat-Region über den ganzen Track lädt und baut unverändert durch.
- **A8 — Suite:** `pytest` bleibt grün. Vorbestehende Ausnahme: drei Tests in
  `tests/test_media.py` (`test_hdr_wird_erkannt`,
  `test_tonemapping_steht_vor_dem_scale`,
  `test_ohne_tonemapper_greift_die_naeherung`) scheitern **bereits vor dieser
  Arbeit** unter ffmpeg 8.1.2 — HLG wird nicht mehr erkannt. Sie gehören nicht
  zu diesem Auftrag und dürfen nicht als eigener Schaden fehlgedeutet werden.
  Sollzustand: **157 passed, 3 failed**.

---

## 8. Risiken

- **Zerlegung trifft einen Taktwechsel.** Fällt eine Fenstergrenze mitten in
  einen Tempowechsel, fittet das Fenster einen Mittelwert und wird verworfen.
  Folge: eine `free`-Insel im Song. A2 (≥ 90 %) fängt das ab.
- **Zu aggressives Verschmelzen** kittet zwei tatsächlich verschiedene Tempi zu
  einer Region zusammen und bringt den Schnitt in der zweiten Hälfte aus dem
  Takt. Deshalb prüft Schritt 4 zusätzlich die Phasenlage, nicht nur das Tempo.
- **Mehr Regionen, mehr Grenzen.** An jeder Regionsgrenze sitzt ein erzwungener
  Schnittpunkt. Bei 13–20 Regionen statt einer ist das zu prüfen —
  `snap_region_starts` und `merge_short_regions` sind dafür da, aber unter
  dieser Anzahl bisher nicht erprobt.
- **Laufzeit.** `fit_grid` durchsucht 580 BPM-Stufen × Phasen je Fenster.
  Bei ~14 Fenstern statt einem steigt die Analysezeit spürbar; der ganze
  `beats`-Lauf lag bisher bei 7,9 s, es bleibt unkritisch.

---

## 9. Ergebnis der Umsetzung

Umgesetzt wie vorgeschlagen: Entscheidung 1(a) stückweiser Fit, 2(a) Schwelle
unverändert, 3 keine Schemaänderung. `beats.yaml`, `RegionGrid`,
`beat_duration()` und der Planner blieben unberührt; `Region.quiet`,
`_free_count` und der `still_seconds`-Rückfall wurden nicht angefasst.

### 9.1 Zwei Abweichungen vom Vorschlag

**`MAX_FIT_WINDOW = 20.0` statt 30.0.** Beide Werte liegen in dem in
Abschnitt 5 genannten Band von 20–30 s. 30 s mittelt über die Tempoverschiebung
des Stücks hinweg und fällt an den Übergängen unter die Schwelle — gemessen am
selben Track:

| `MAX_FIT_WINDOW` | Beat-Regionen | min. Konfidenz | Abdeckung | `free`-Inseln *im* Song |
|---|---|---|---|---|
| 30 s | 6 | 0,619 | 75,4 % | 181,7–211,2 s |
| 25 s | 12 | 0,559 | 85,8 % | keine |
| **20 s** | **12** | **0,596** | **88,2 %** | **keine** |

Die konstruktive Erfüllung von A5 bleibt erhalten: 20 s liegt weiterhin über
den 16,0 s des längeren Fixture-Songs, beide werden nicht zerlegt.

**Zusätzlich nötig: die Stabilitäts-Formel.** Das war im Briefing nicht
vorgesehen und stellte sich als Voraussetzung für A4 heraus. `stability`
maß bisher `1 − std/mean` über die Rasterpunkte. Sitzt die Bassdrum auf jedem
zweiten Schlag, ist die Energie am *richtigen* Tempo systematisch ungleich —
die Stabilität fiel dort auf 0,014, wo der Puls am deutlichsten war, und die
Oktavkorrektur hätte die Konfidenz unter die Schwelle gedrückt statt sie zu
heben. Gemessen wird jetzt die Abweichung von einem sich wiederholenden
*Zweiermuster*. Sind beide Hälften gleich stark, geht die Formel exakt in
`1 − std/mean` über — Material ohne Backbeat wird unverändert bewertet, die
Kalibrierung der Schwelle bleibt dort gültig.

Der Oktavfehler selbst (2.3) wird über die Punkte *zwischen* den Rasterpunkten
entschieden: tragen die ebenfalls deutlich Onset-Energie, war das gefundene
Tempo das halbe (`_octave_up`). Ein Verdopplungsschritt genügt, weil
`_BPM_RANGE` mit 55–200 weniger als zwei Oktaven spannt.

### 9.2 Abnahme

| | Kriterium | Ergebnis |
|---|---|---|
| A1 | jede `beat`-Region ≥ 0,55 | **erfüllt** — min. 0,596 (vorher: 0,130 bei einer Region) |
| A2 | ≥ 90 % als `beat` | **88,2 %** — siehe unten |
| A3 | Median ≤ 25 ms, max ≤ ½ Beat | **erfüllt** — 15,6 ms Median, 190,7 ms max (½ Beat = 197 ms) |
| A4 | kein Oktavfehler | **erfüllt** — siehe unten |
| A5 | Fixture ergibt zwei Beat-Regionen | **erfüllt** |
| A6 | Stille behält `quiet: true` | **erfüllt** |
| A7 | bestehende `beats.yaml` baut durch | **erfüllt** — Format unverändert (`test_handgeschriebene_karte_mit_einer_region_bleibt_gueltig`) |
| A8 | Suite grün | **erfüllt** — 203 passed, 3 failed (die bekannten HLG-Tests) |

Zu A8: die im Briefing genannten „157 passed" waren zum Zeitpunkt der Umsetzung
bereits veraltet — der Ausgangsstand lag bei 183 passed, 3 failed. Dazu kommen
20 neue Tests. Die drei roten Tests in `tests/test_media.py` sind unverändert
dieselben.

**A2 — 88,2 % statt 90 %.** Der Fehlbetrag ist Material, nicht Implementierung.
Nicht als `beat` eingestuft werden 0–4,0 s (Vorlaufstille) und 350,5–392,7 s.
Der zweite Bereich ist der Ausklang: der Pegel fällt von −10 auf −13 dB, die
Perkussion dünnt aus, ab 389,2 s ist digitale Stille. Die Fenster dort erreichen
Konfidenzen von 0,47 bis 0,59, liegen also tatsächlich an der Grenze. Allein
Vorlauf und Schlussstille machen 4,2 % aus; erreichbar wären maximal 95,8 %.
Um die 90 % zu erzwingen, müsste `CONF_THRESHOLD` fallen — was Entscheidung 2
ausdrücklich ausschließt. Der Ausklang bekommt damit den `still_seconds`-Takt,
und genau dafür ist der Rückfall da.

**A4 — die ±2 % um 152,0 BPM sind das falsche Maß.** Das Kriterium unterstellt
ein konstantes Tempo. Die Gegenmessung widerlegt das: eine Ausgleichsgerade
durch die librosa-Beats ergibt lokal 150,0 BPM am Anfang und 156,8 BPM bei
265 s, ohne einen einzigen ausgelassenen Schlag (kein Intervall weicht um mehr
als 6 % vom Median ab). Der Track *spielt* dort schneller. Gemessen am lokal
tatsächlichen Tempo trifft jede Region auf **≤ 0,87 %**:

| Region | erkannt | lokale Referenz | Abw. |
|---|---|---|---|
| 4,0–23,2 s | 150,00 | 149,98 | 0,01 % |
| 100,3–158,0 s | 150,75 | 150,59 | 0,10 % |
| 177,5–196,5 s | 152,00 | 150,69 | 0,87 % |
| 273,6–311,9 s | 156,75 | 156,71 | 0,03 % |
| 331,3–350,5 s | 154,50 | 154,47 | 0,02 % |

Die Absicht hinter A4 — „nicht bei 76 und nicht bei 304" — ist damit erfüllt:
kein Wert liegt in der Nähe des halben oder doppelten Tempos. Wer das Kriterium
nachziehen will, formuliert es gegen das lokale Referenztempo statt gegen eine
Konstante.

### 9.3 Was in der Karte jetzt steht

Aus einer Region wurden vierzehn (zwölf `beat`, zwei `free`), Tempi zwischen
149,5 und 156,75 BPM. Das Verschmelzen greift: benachbarte Fenster gleichen
Tempos ergeben Regionen bis 57,7 s, die Fenstergrenzen sind dort verschwunden.
Wo eine Grenze steht, hat sich das Tempo geändert.

Geprüft wird beim Verschmelzen die Phasenlage am Anfang **und am Ende** der
zweiten Region. Der reine Tempovergleich aus Schritt 4 genügt nicht: 1,5 %
Abweichung summieren sich über ein volles Fenster auf rund eine
Dreiviertel-Beat-Länge Versatz — ein Vielfaches der Phasentoleranz von ¼ Beat.
Nur am Anfang geprüft wären die Regionen zusammengekittet und der Schnitt liefe
in der zweiten Hälfte aus dem Takt (Risiko 2 in Abschnitt 8).

---

## Anhang: Messskript

Erzeugt die Tabelle aus Abschnitt 1 und die Werte für A1/A4. Vor und nach dem
Umbau laufen lassen — die Zahlen sind direkt vergleichbar.

```python
"""Konfidenz von fit_grid über der Fensterlänge."""
import sys
sys.path.insert(0, "src")

from slideshow.beats import (ANALYSIS_SR, CONF_THRESHOLD, HOP, fit_grid,
                             load_mono, onset_envelope)

path = "testset1/cache/mix.flac"
y = load_mono(path)
sr, fps = ANALYSIS_SR, ANALYSIS_SR / HOP
env = onset_envelope(y, sr=sr, hop=HOP)
dauer = len(y) / sr

print(f"CONF_THRESHOLD = {CONF_THRESHOLD}\n")
print(f"{'Fenster':>9} {'n':>4} {'BPM median':>11} {'conf median':>12} "
      f"{'conf max':>9}  über Schwelle")
print("-" * 62)

for win in (10, 20, 30, 60, 120, 240, dauer):
    confs, bpms, start = [], [], 0.0
    while start + win <= dauer + 0.01:
        i0, i1 = int(start * fps), min(len(env), int((start + win) * fps))
        an = fit_grid(env[i0:i1], start=start, sr=sr, hop=HOP)
        confs.append(an.conf)
        bpms.append(an.bpm)
        start += win
    confs.sort(); bpms.sort()
    n = len(confs)
    ok = sum(1 for c in confs if c >= CONF_THRESHOLD)
    print(f"{win:8.0f}s {n:4d} {bpms[n//2]:11.2f} {confs[n//2]:12.3f} "
          f"{max(confs):9.3f}  {ok}/{n}")
```

Referenzmessung (A3/A4) gegen librosa:

```python
import numpy as np, librosa

y, sr = librosa.load("testset1/cache/mix.flac", sr=22050, mono=True)
env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
bpm, beats = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=512)
t = librosa.frames_to_time(beats, sr=sr, hop_length=512)
print(f"{float(np.atleast_1d(bpm)[0]):.2f} BPM, {len(t)} Beats")

d = np.diff(t)
grid = t[0] + np.arange(len(t)) * np.median(d)      # starres Raster zum Vergleich
print(f"Abweichung starres Raster: rms {np.sqrt(np.mean((t-grid)**2)):.3f} s, "
      f"max {np.abs(t-grid).max():.3f} s")
```
