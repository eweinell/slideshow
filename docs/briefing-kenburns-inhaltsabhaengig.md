# Briefing: Inhaltsabhängige Ken-Burns-Effekte

**Status:** Konzept, offen · **Betrifft:** neu `vision.py` + `kbplan.py`, dazu
`kenburns.py`, `build.py`, `models.py`, `cli.py` · **Vorbedingung:**
`slideshow preprocess` (die Analyse läuft auf den normalisierten Cache-Bildern)

Die Ken-Burns-Bewegung ist heute rein positionsabhängig: Zoomrichtung nach
Segmentindex, Schwenkrichtung reihum durch acht Himmelsrichtungen. Das Bild
selbst geht nicht ein. Folge: in ein Gruppenfoto wird auf den Bildrand
zugeschwenkt, ein Makro wird um 30 % vergrößert bis es weich wird, und ein
Panorama bekommt einen vertikalen Schwenk gegen die Bildachse.

Dieses Briefing beschreibt, wie der Bildinhalt in die Bewegungswahl eingeht —
**ohne** die Abwechslung zu verlieren, die die heutige Rotation garantiert, und
ohne Determinismus und Segment-Cache aufzugeben.

---

## 1. Ausgangslage

`plan_motion` (`kenburns.py:62`) leitet die komplette Bewegung aus zwei Zahlen
ab: `index` und `duration`.

```python
zoom_in = (index % 2 == 0) or not defaults.alternate      # kenburns.py:79
dx, dy  = _DIRECTIONS[index % len(_DIRECTIONS)]           # kenburns.py:82
c0 = (0.5 - dx * a, 0.5 - dy * a)                         # a = pan_amount = 0.06
```

Was bereits vorhanden ist und getragen werden kann:

| Baustein | Zustand |
|---|---|
| `KBSpec` (`models.py:260`) — `z`, `c`, `ease`, `engine` je Segment | vorhanden, validiert |
| `plan_motion(..., spec)` überschreibt die Defaults | vorhanden |
| `render.py:72` reicht `slot.intent.kb` bis in den Filtergraph durch | vorhanden |
| Cache-Key enthält `vf` **und** `motion.fingerprint()` (`render.py:137`) | vorhanden |
| `build._segment_from_slot` schreibt `kb:` | **fehlt** — schreibt nie ein `kb` |

Das ist die wichtigste Feststellung des Briefings: **der Renderpfad ist
fertig.** Eine inhaltsabhängige Bewegung muss nichts weiter tun, als in
`edit.yaml` ein `kb:` je Standbild-Segment zu hinterlegen. `render` und der
Segment-Cache verhalten sich dann von selbst richtig.

### 1.1 Zwei Randbedingungen, die das Konzept vorformen

**(a) Die Normalform vereinfacht die Geometrie.** Jedes Cache-Bild hat exakt
das Ausgabeseitenverhältnis (`preprocess.py:7-13`). Das sichtbare Fenster bei
Zoom `z` ist damit in normalisierten Koordinaten ein Rechteck der relativen
Breite **und** Höhe `1/z` um die Mitte `c`. Bildkoordinaten aus der Analyse
lassen sich also direkt gegen das Fenster prüfen — keine
Seitenverhältnis-Umrechnung, keine Sonderfälle.

**(b) Bei `z = 1.0` gibt es keinen Schwenk.** `zoompan_filter`
(`kenburns.py:149`) klemmt die Fensterposition an den Bildrand:

```
x = max(0, min(iw - iw/zoom, cx*iw - iw/zoom/2))
```

Unbeschnitten bleibt `c` nur, solange

    |cx − 0.5| ≤ (1 − 1/z) / 2

gilt. Bei `z = 1.0` ist die rechte Seite **null**: die Mitte wird zwangsweise
auf 0.5 geklemmt. Der heutige Default `z0 = 1.0 → z1 = 1.0 + …` hat am
Zoom-1.0-Ende also gar keinen Schwenk; `pan_amount` wirkt dort nur als
Richtungstendenz des Zooms, nicht als Kamerafahrt.

Das ist keine Randnotiz. **Ein Schwenk auf ein Motiv verlangt Zoom-Vorrat auf
beiden Enden der Bewegung.** Wer um `a` schwenken will, braucht durchgehend

    z ≥ 1 / (1 − 2a)

also z ≥ 1,064 für a = 0,03 und z ≥ 1,136 für a = 0,06. Bei
`zoom_total = (0.08, 0.30)` frisst ein 6-%-Schwenk damit die halbe zulässige
Zoomspanne. Zoomweite und Schwenkweite sind ein Budget, kein
Parameterpaar — der Planer muss sie gemeinsam vergeben (Abschnitt 6.3).

---

## 2. Was „inhaltsabhängig" heißen soll

Drei Ebenen, in dieser Reihenfolge der Wichtigkeit:

1. **Schutz (hart).** Was nicht angeschnitten werden darf, wird zu keinem
   Zeitpunkt angeschnitten: Gesichter, der Kopf über dem Horizont, Schrift.
   Das ist der Teil, der heute sichtbar schiefgeht, und er ist als
   geometrische Nebenbedingung exakt prüfbar.
2. **Passung (weich).** Die Bewegung folgt der Bildaussage: Panorama →
   Schwenk entlang der Bildachse; Porträt → langsames Heranfahren aufs
   Gesicht; Makro → nahezu Stillstand; Architektur → vertikale Fahrt.
3. **Abwechslung (global).** Über die Bildfolge hinweg wiederholen sich
   Bewegungen nicht. Diese Ebene steht **über** der Passung: wenn 30 Fotos
   hintereinander Weitwinkel-Landschaft sind, darf nicht 30-mal derselbe
   Links-Rechts-Schwenk laufen.

Ebene 1 und 2 brauchen Bildverständnis. Ebene 3 ist reine Kombinatorik und
gehört ausdrücklich **nicht** ins Modell.

---

## 3. Architektur: das Modell liefert Fakten, der Planer entscheidet

```
preprocess ──► cache/*.jpg
                  │
                  ▼
          slideshow analyze      (Claude API, einmalig je Bild)
                  │
                  ▼
             vision.yaml         ← ansehen und korrigierbar
                  │
   beats.yaml ────┤
                  ▼
          slideshow build ──► edit.yaml  (mit kb: je Standbild)
                  │
                  ▼
          slideshow render       (unverändert)
```

Die Claude-API wird **nicht** gefragt, welcher Ken-Burns-Effekt passt. Sie
wird gefragt, was auf dem Bild zu sehen ist. Gründe:

- Das Modell kennt die Segmentdauer nicht, kennt die Nachbarbilder nicht und
  kann die Abwechslungsbedingung nicht erfüllen — die ist global.
- Eine gelieferte Bewegung wäre nicht nachvollziehbar prüfbar. Eine gelieferte
  Bounding-Box ist es: sie steht in `vision.yaml`, sie lässt sich mit einem
  Blick aufs Bild verifizieren und von Hand korrigieren.
- Bildfakten sind über Prompt- und Modellwechsel hinweg stabiler als
  Bewegungsurteile. Ein Modellwechsel darf nicht den kompletten
  Segment-Cache invalidieren (Abschnitt 9).
- Die Regeln „Gesicht nicht anschneiden" und „Makro nicht überzoomen" sind
  zehn Zeilen Code. Dafür braucht es kein Sprachmodell.

Das Modell darf einen unverbindlichen Vorschlag mitgeben (`suggest`), den der
Planer nur als Stichentscheid bei gleichwertigen Kandidaten verwendet.

---

## 4. `vision.yaml`

Ein Eintrag je Bild, Koordinaten normalisiert **auf das Cache-Bild** (also auf
die 16:9-Normalform, nicht auf das Original).

```yaml
version: 1
model: claude-opus-5
prompt: 1                       # Prompt-Version, geht in den Analyse-Cache-Key
images:
  cache/img_0042.jpg:
    hash: 3f1c8a9e2b7d4055      # blake2b des Cache-Bilds (HashIndex)
    scene: landscape_wide       # Enum, s. u.
    axis: horizontal            # horizontal | vertical | none
    horizon: 0.61               # oder null
    focus: [0.38, 0.47]         # wohin gezoomt werden darf
    subjects:
      - {box: [0.30, 0.34, 0.46, 0.72], kind: person, weight: 0.9}
    protect:                    # darf zu keinem Zeitpunkt angeschnitten werden
      - [0.30, 0.30, 0.48, 0.74]
    detail: 0.35                # Detaildichte 0..1 → Obergrenze für Zoom
    depth: into                 # into | out | flat
    suggest: pan_right          # unverbindlich
    conf: 0.88
    note: "Fjord im Weitwinkel, Wanderer links im Vordergrund"
```

`scene`-Enum (Startmenge, bewusst klein — jede Klasse muss eine *andere*
Bewegungsregel nach sich ziehen, sonst gehört sie nicht ins Enum):

`landscape_wide` · `portrait_person` · `group` · `architecture` · `detail_macro`
· `action` · `interior` · `document` · `other`

Das Format ist bewusst wie `beats.yaml` gebaut: eine Zeile je Objekt, von Hand
editierbar, zur Sichtprüfung gedacht. Wer eine falsche Box sieht, korrigiert
sie; ein erneuter `analyze`-Lauf überschreibt sie nicht, solange Bild-Hash,
Prompt-Version und Modell gleich bleiben.

---

## 5. Der API-Aufruf

| Aspekt | Festlegung |
|---|---|
| Modell | `claude-opus-5` als Default, `--model` überschreibt |
| Bild | Cache-Bild auf 1024×576 herunterskaliert, JPEG q80, base64 |
| Format | `output_config.format` mit JSON-Schema → garantiert parsebare Antwort |
| Aufwand | `output_config: {"effort": "low"}` — die Aufgabe ist Klassifikation, kein Denksport |
| Caching | Systemprompt + Schema als gecachter Präfix (`cache_control`), volatile Teile ans Ende |
| Durchsatz | ein Bild je Request, synchron und parallel; Batch-API nur auf Wunsch (`--batch`, s. E4) |
| Übertragung | Bild als base64 im Request — **nicht** über die Files-API (E6) |
| Wiederholung | SDK-Default (2 Retries), dazu Ausfall-Rückfall aus Abschnitt 8 |

Zwei Details, die leicht Geld kosten, wenn man sie übersieht:

- **Thinking ist auf `claude-opus-5` per Default an** und wird als Ausgabe
  abgerechnet. Bei ~100 Bildern ist das der größte Kostenposten, nicht das
  Bild. Deshalb `effort: "low"` und ein knappes `max_tokens`.
- **Der Prompt-Cache wird erst nach der ersten Antwort lesbar.** Bei
  parallelem Fan-out zahlen sonst alle Requests den vollen Präfix. Also: einen
  Request absetzen, erste Antwort abwarten, dann die übrigen 99 parallel.

Die Analyse läuft auf dem **normalisierten** Bild, nicht auf dem Original.
Damit gelten die gelieferten Koordinaten unverändert im Koordinatensystem des
Ken-Burns-Filters — keine Transformation, kein Versatz. Der Preis: bei
`portrait: blur` sieht das Modell auch die unscharfen Seitenbalken. Das ist
kein Nachteil, sondern der Punkt — es ist genau das Bild, das gerendert wird,
und daraus folgt unmittelbar eine Regel (Abschnitt 6.2).

---

## 6. Von Fakten zu Bewegung

### 6.1 Regeltabelle

| `scene` | Grundbewegung | Zoom | Schwenk |
|---|---|---|---|
| `landscape_wide` | langsame Fahrt entlang `axis` | klein (0,08–0,15) | groß, Richtung = `axis` |
| `portrait_person` | Heranfahren auf `focus` | mittel | klein, auf `focus` zu |
| `group` | leichtes Herausfahren | klein | sehr klein oder keiner |
| `architecture` | vertikale Fahrt bei `axis: vertical` | mittel | groß, vertikal |
| `detail_macro` | nahezu Stillstand | sehr klein (≤ 0,08) | keiner |
| `action` | Herausfahren, Schwenk in Bewegungsrichtung | mittel | mittel |
| `interior` | Heranfahren auf `focus` | mittel | klein |
| `document` | statisch, formatfüllend | keiner | keiner |
| `other` | heutige Rotation | nach Dauer | Default |

Die Tabelle liefert je Bild **eine Kandidatenmenge** mit Passungsnoten, nicht
eine Bewegung. Das ist die Voraussetzung dafür, dass Abschnitt 7 noch etwas zu
wählen hat.

### 6.2 Hochformat

Ein `portrait: blur`-Komposit hat links und rechts unscharfe Balken. Ein
horizontaler Schwenk fährt in die Balken hinein und macht sie sichtbar
größer — der schlechteste Fall. Regel: **Hochformat-Komposite bekommen nur
vertikale Schwenks oder reinen Zoom.** Bei `portrait: crop` gilt die Regel
nicht, dort ist das Komposit ein normales Vollbild.

### 6.3 Die Klemmkette

Der Planer rechnet in dieser Reihenfolge, jeder Schritt kann nur verkleinern:

1. **Zoom-Obergrenze aus dem Schutz.** Eine Schutzbox der Breite `bw` und Höhe
   `bh` passt nur bis `z ≤ 1 / max(bw, bh)`.
2. **Zoom-Obergrenze aus der Detaildichte.** `z_max ≤ 1 + (1 − detail) · 0,30`.
   Ein detailarmer Himmel verträgt Zoom, ein Makro nicht. Zusätzlich gilt
   weiterhin die harte Grenze aus `sanity_check` (`kenburns.py:276`): über 2×
   reicht der Subpixel-Vorrat nicht.
3. **Zoomspanne aus der Dauer**, wie heute (`zoom_rate`, `zoom_total`), dann
   gegen 1. und 2. geklemmt.
4. **Schwenkweite aus dem Zoom-Vorrat.** Mit `z_min = min(z0, z1)` gilt
   `a ≤ (1 − 1/z_min) / 2`. Reicht das nicht für den gewünschten Schwenk, hat
   der Planer zwei Hebel: `z_min` anheben (Zoom-Vorrat schaffen, kostet
   Zoomspanne) oder `a` senken. **Vorschlag: bis `z_min = 1,10` anheben,
   darüber `a` senken.** Der Schwenk ist die auffälligere Bewegung; ein Bild,
   das durchgehend um 10 % vergrößert steht, fällt niemandem auf.
5. **Schutzprüfung über die volle Bewegung.** Für `t ∈ {0, 0.25, 0.5, 0.75, 1}`
   und die extremen `z`-Werte: liegt jede Schutzbox vollständig im Fenster
   `[cx(t) ± 1/(2z(t))] × [cy(t) ± 1/(2z(t))]`? Sonst `a` halbieren und
   wiederholen, maximal dreimal, danach reiner Zoom auf `focus`.

Schritt 5 ist mit fünf Stützstellen ausreichend, weil `z` und `c` beide
monoton in `t` sind (Smoothstep ist monoton) — das Fenster wandert ohne
Umkehr, die Extrema liegen an den Rändern.

---

## 7. Abwechslung

Ohne Gegenmaßnahme erzeugt Abschnitt 6 bei homogenem Material Monotonie: 30
Landschaftsbilder → 30 gleiche Schwenks. Die Abwechslung ist deshalb ein
eigener, dem Regelwerk **nachgeschalteter** Schritt.

**Bewegungssignatur** eines Kandidaten:

    (zoom ∈ {ein, aus}, schwenk ∈ {0..7, keiner}, weite ∈ {S, M, L})

**Abstand** zweier Signaturen: 2 Punkte für unterschiedliche Zoomrichtung,
0–2 Punkte nach Winkelabstand der Schwenkrichtung, 1 Punkt für
unterschiedliche Weite. Maximum 5.

**Auswahl** — gierig, sequentiell, deterministisch:

```
punktzahl(k) = passung(k) − λ · Σ_{j=1..W} gewicht(j) · (5 − abstand(k, gewählt[i−j]))
```

mit Fenster `W = 3`, `gewicht = (1.0, 0.6, 0.3)` und `λ` als einzigem Regler
(Vorschlag: `λ = 0.25`, per `--variety` einstellbar; `0` = reine Passung).

Dazu zwei harte Regeln, die der Strafterm allein nicht sicher erzwingt:

- **Kein Signatur-Duplikat innerhalb von drei aufeinanderfolgenden Stills.**
- **Höchstens zwei gleiche Zoomrichtungen in Folge** — das ist die heutige
  `alternate`-Zusage, nur weicher formuliert.

Gieriges Vorgehen genügt: die Bildreihenfolge steht fest (chronologisch), es
gibt keinen Rückwärtseffekt, und eine globale Optimierung würde ein
Zuordnungsproblem für einen kaum wahrnehmbaren Gewinn lösen.

---

## 8. Einbettung, Determinismus, Ausfall

**Wo das Ergebnis landet.** `build` liest `vision.yaml`, ruft den Planer und
schreibt je Standbild ein aufgelöstes `kb:` nach `edit.yaml`:

```yaml
- {type: still, src: cache/img_0042.jpg, beats: 8,
   kb: {z: [1.1, 1.24], c: [0.44, 0.5, 0.34, 0.5]}}
```

Das bleibt Absicht, nicht gemessene Dauer — der Konflikt aus
`build._segment_from_slot` entsteht nicht. Und es bleibt von Hand
überschreibbar: wer eine Bewegung nicht mag, ändert vier Zahlen.

**Determinismus.** Modellantworten sind nicht bit-reproduzierbar. Der
Determinismus entsteht dadurch, dass die Antwort **einmal** in `vision.yaml`
festgeschrieben wird; ab da ist die ganze Kette rein deterministisch.
`analyze` ruft die API nur, wenn `hash + prompt + model` sich geändert haben —
ein zweiter Lauf ohne neue Bilder macht null Requests. (`temperature` steht auf
`claude-opus-5` ohnehin nicht zur Verfügung.)

**Ausfall.** Fehlender Schlüssel, kein Netz, Rate-Limit, Refusal, unlesbares
JSON, `conf < 0.5` — jeder dieser Fälle fällt auf das heutige Verhalten
zurück, je Bild einzeln. `build` läuft immer durch; im Report steht, wie viele
Bilder analysiert wurden und wie viele auf die Rotation zurückgefallen sind.
`--no-vision` schaltet die Analyse komplett ab.

**Cache-Wirkung.** `motion.fingerprint()` steckt im Segment-Cache-Key
(`render.py:137`). Eine geänderte Analyse invalidiert genau die betroffenen
Segmente und ihre zwei Nachbar-Blenden — das ist Prinzip 2 und funktioniert
ohne Zutun. Aber: **eine Neuanalyse aller Bilder (neues Modell, neuer Prompt)
invalidiert den kompletten Renderlauf.** `analyze` muss davor warnen und die
Zahl der betroffenen Segmente nennen, bevor es losläuft.

---

## 9. Kosten

### Annahmen

| Posten | Wert |
|---|---|
| Analysebild 1024×576 | 786 Bildtokens (`(1024·576)/750`) |
| Systemprompt + JSON-Schema | 1 200 Tokens, gecacht |
| Nutzertext je Bild | 40 Tokens |
| Ausgabe, sparsam (`effort: low`) | 250 Tokens |
| Ausgabe, denkfreudig | 700 Tokens |

Gecacheter Präfix zählt mit 0,1× Eingabepreis. Der Cache-Mindestpräfix ist
modellabhängig: 512 Tokens auf Opus 5, 1 024 auf Sonnet 5 — **4 096 auf
Haiku 4.5**, unser 1 200-Token-Präfix cached dort also gar nicht.

### Kosten je 100 Bilder (USD)

| Modell | Preis in/out je M | synchron, sparsam | synchron, denkfreudig | Batch-API (−50 %) |
|---|---|---|---|---|
| **Opus 5** | 5 / 25 | **1,10** | 2,22 | 0,55 – 1,11 |
| **Sonnet 5** | 3 / 15 | **0,66** | 1,33 | 0,33 – 0,67 |
| **Haiku 4.5** | 1 / 5 | **0,33** | 0,55 | 0,16 – 0,28 |

Sonnet 5 liegt bis 31.08.2026 auf einem Einführungspreis von 2/10 — dann
etwa ein Drittel günstiger als oben. Für die Planung ist der Regelpreis
angesetzt.

### Was daraus folgt

- **Die Ausgabetokens dominieren, nicht das Bild.** Auf Opus 5 macht die
  Eingabe 0,47 ct je Bild aus, die Ausgabe 0,63 bis 1,75 ct. Das Analysebild
  von 1024×576 auf 768×432 zu verkleinern spart 17 ct je 100 Bilder — der
  falsche Hebel. Der richtige ist `effort` und ein knappes `max_tokens`.
- **Die Absolutkosten sind vernachlässigbar.** Ein Urlaubsprojekt mit 100
  Fotos kostet einmalig ein bis zwei Dollar und wird bei Wiederholungsläufen
  aus `vision.yaml` bedient. Dagegen stehen Stunden Renderzeit. Es gibt keinen
  Kostengrund, am Modell zu sparen — deshalb Opus 5 als Default.
- **Die Batch-API halbiert den Preis, kostet aber 29 Tage Aufbewahrung.** Ein
  Analyselauf ist zwar kein interaktiver Vorgang, aber die Batch-API ist nicht
  ZDR-fähig und speichert die Aufträge 29 Tage. Bei privaten Fotos ist das der
  falsche Tausch für 0,55 USD je 100 Bilder; siehe E4.
- Vor dem ersten großen Lauf gehört `count_tokens` gegen fünf echte Bilder
  ausgeführt und mit der Tabelle verglichen. Die Ist-Kosten meldet `analyze`
  am Ende aus `usage` (Abnahmekriterium A6).

---

## 10. Offene Fragen und Entscheidungen

### E1 — Liefert das Modell Fakten oder Bewegung?

**(a) Fakten, lokaler Planer entscheidet** *(Empfehlung)* — begründet in
Abschnitt 3: prüfbar, korrigierbar, deterministisch, und die
Abwechslungsbedingung ist ohnehin nicht modellierbar, weil sie global ist.

**(b) Das Modell liefert fertige `KBSpec`-Blöcke** — weniger Code, aber jede
Bewegung wird zum Vertrauensakt, `vision.yaml` verliert seinen Zweck als
Sichtprüfung, und die Kosten steigen (längere Ausgabe).

> **Empfehlung: (a)**, mit dem unverbindlichen `suggest`-Feld als
> Stichentscheid. Damit bleibt (b) als spätere Option offen, ohne (a)
> wegzuwerfen.

### E2 — Analysebild: Cache-Bild oder Original?

**(a) Normalisiertes Cache-Bild** *(Empfehlung)* — Koordinaten gelten 1:1 im
Filterkoordinatensystem, keine Transformation, und das Modell sieht genau das
Bild, das gerendert wird (inklusive der Hochformat-Balken, woraus Regel 6.2
folgt).

**(b) Original** — mehr Bildinhalt sichtbar, aber jede Koordinate muss durch
`_cover_crop`/`_portrait_composite` zurückgerechnet werden. Ein Fehler dort
verschiebt Schutzboxen unbemerkt, und genau die sollen ja schützen.

> **Empfehlung: (a).** Der einzige Nachteil sind ein paar Tokens für
> unscharfe Balken.

### E3 — Wo werden die Ergebnisse abgelegt?

**(a) Eigenes `vision.yaml`** *(Empfehlung)* — gleiche Rolle wie `beats.yaml`:
eigenes Kommando, eigener Zeitpunkt, zur Sichtprüfung gedacht, von Hand
korrigierbar. `build` liest es und schreibt aufgelöste `kb:`-Blöcke.

**(b) `manifest.json` erweitern** — Manifest ist maschinenerzeugtes JSON aus
`probe` und ausdrücklich nicht zum Anfassen gedacht; eine Neuanalyse müsste
das Manifest neu schreiben.

**(c) Direkt `kb:` nach `edit.yaml`, kein Zwischenformat** — spart eine Datei,
verliert aber die Trennung zwischen „was ist auf dem Bild" und „wie bewegt
sich die Kamera". Ein `--variety`-Wechsel erzwingt dann eine Neuanalyse.

> **Empfehlung: (a).** Zwei Artefakte mit je einem klaren Besitzer.

### E4 — Ein Bild je Request oder Bündel?

**(a) Ein Bild je Request, synchron parallel** *(Empfehlung)* — je Bild
cachebar, ein Fehlschlag betrifft ein Bild, und der Messages-Endpunkt ist der
datensparsamste Weg: ZDR-fähig, Bild und Antwort werden nach der Antwort nicht
abgelegt (E6). 100 Bilder sind in wenigen Minuten durch.

**(b) Ein Bild je Request, Batch-API** — halbiert den Preis, ist aber
**nicht ZDR-fähig und speichert die Aufträge 29 Tage**. Für 0,55 USD je 100
Bilder ist das bei privaten Fotos der falsche Tausch.

**(c) N Bilder je Request** — weniger Requests, aber das Modell muss Indizes
sauber halten (erfahrungsgemäß die Fehlerquelle bei Mehrbild-Prompts), ein
Fehlschlag kostet N Bilder, und die Ersparnis ist gering, weil der geteilte
Präfix ohnehin gecacht ist.

> **Empfehlung: (a)**, mit `--batch` als bewusst zu wählender Option für
> Anwender, denen die 29 Tage gleichgültig sind (etwa bei Landschafts- oder
> Sachaufnahmen ohne Personen).

### E5 — Welches Modell als Default?

Bei 1,10 USD je 100 Bilder ist Kosten kein Argument. **Empfehlung:
`claude-opus-5` als Default**, `--model` für Sonnet 5 (Massenläufe,
Prompt-Iteration) und Haiku 4.5 (Rauchtest). Wichtig: das Modell steht in
`vision.yaml` und geht in den Analyse-Cache-Key ein — ein Wechsel ist eine
bewusste Entscheidung mit Renderkosten (Abschnitt 8).

### E6 — Datenschutz

Die Analyse schickt private Urlaubsfotos mit erkennbaren Personen an einen
externen Dienst. **Das darf kein stiller Schritt in `preprocess` werden.**

Was Anthropic für die Claude-API zusagt (Stand 08/2026, Quellen unten):

| Punkt | Zusage |
|---|---|
| Training | „Retained data is never used for model training without your express permission." Gilt für die API, **nicht** für die Consumer-Produkte (Free/Pro/Max) — deren Regeln sind andere. |
| Aufbewahrung | Standard: Inhalte werden nicht dauerhaft vorgehalten und innerhalb von 30 Tagen gelöscht. |
| Zero Data Retention | Vertraglich über den Vertrieb, nicht selbst einschaltbar — für ein privates Projekt praktisch nicht erreichbar. |
| Auffälligkeiten | **Unabhängig von jeder Vereinbarung:** von den automatischen Trust-&-Safety-Systemen markierte Inhalte können bis zu **2 Jahren** aufbewahrt werden. |
| Modellwahl | `claude-opus-5` ist kein „Covered Model"; nur Fable 5 und Mythos 5 erzwingen 30-Tage-Retention und sind von ZDR ausgeschlossen. Die Wahl aus E5 ist damit auch die datensparsamste. |

Daraus folgen drei technische Festlegungen:

1. **Bilder als base64 im Messages-Request, nie über die Files-API.** Der
   Messages-Endpunkt ist ZDR-fähig; die Files-API ist es nicht und hält
   Dateien *„until explicitly deleted"*.
2. **Batch-API nicht als Default** — 29 Tage Aufbewahrung, siehe E4.
3. **Strukturierte Ausgabe ist unkritisch.** Vom JSON-Schema wird eine
   Grammatik bis zu 24 h zwischengespeichert, Prompt und Antwort nicht. Unser
   Schema enthält keine Bilddaten — es darf auch künftig keine bekommen.

> **Empfehlung:** eigenes, ausdrücklich aufzurufendes Kommando
> `slideshow analyze`; beim ersten Lauf in einem Projekt eine einmalige
> Bestätigung, die Modell, Bildanzahl **und die Tabelle oben in zwei Sätzen**
> nennt — insbesondere die 2-Jahres-Frist bei markierten Inhalten, denn das
> ist der Punkt, den ein Nutzer nicht erwartet; `--yes` für Skripte;
> `--no-vision` als dauerhafte Abschaltung in `build`. Der Punkt gehört
> zusätzlich in die README, nicht nur in den `--help`-Text.

Quellen: [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention),
[How long do you store my organization's data?](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data),
[Data retention practices for Covered Models](https://support.claude.com/en/articles/15425996-data-retention-practices-for-covered-models).
Policies ändern sich — vor der Umsetzung neu prüfen.

### E7 — Geht der Musikkontext in die Bewegung ein?

Naheliegend: Beat-Regionen bekommen kräftigere Bewegung, `free`/stille
Regionen ruhigere. Technisch fehlt dafür nur die Durchreichung der Region an
den Planer — `plan_motion` bekommt heute nur `index` und `duration`.

> **Empfehlung: v1 nein.** Die Dauer korreliert bereits mit der Regionsart
> (kurze Slots in dichten Beat-Regionen), und `zoom_rate` skaliert schon über
> die Dauer. Der Zusatznutzen ist gering, die Kopplung zwischen Planer und
> Regionenkarte teuer. Als Nachtrag jederzeit möglich.

### E8 — Reicht das Zwei-Punkt-Bewegungsmodell?

`KBSpec` kann genau `z0 → z1` und `c0 → c1`. Eine Fahrt, die erst schwenkt und
dann stehen bleibt, ist damit nicht ausdrückbar.

> **Empfehlung: v1 unverändert lassen.** Alle Regeln aus Abschnitt 6 kommen
> mit zwei Punkten aus. Eine Erweiterung auf Stützstellen wäre eine
> Schemaänderung an `edit.yaml` plus Umbau beider Renderpfade — das gehört in
> ein eigenes Briefing, wenn der Bedarf belegt ist.

### E9 — Was passiert mit niedriger Konfidenz?

> **Empfehlung:** `conf < 0.5` → Bild wird wie `scene: other` behandelt
> (heutige Rotation), Schutzboxen werden aber **trotzdem** respektiert. Eine
> unsichere Klassifikation ist ein schwaches Signal für die Passung, aber
> immer noch besser als nichts für den Schutz.

---

## 11. Abnahmekriterien

- **A1 — Schutz.** Für jedes Bild mit `protect`-Box liegt jede Box an allen
  fünf Stützstellen vollständig im sichtbaren Fenster. Automatisiert prüfbar
  aus `edit.yaml` + `vision.yaml`, ohne zu rendern.
- **A2 — Abwechslung.** Über einen Lauf mit ≥ 40 Standbildern: kein
  Signatur-Duplikat innerhalb von drei aufeinanderfolgenden Stills; keine
  Zoomrichtung über 65 % Anteil; mindestens sechs der acht Schwenkrichtungen
  kommen vor.
- **A3 — Determinismus.** Zweimal `build` auf demselben `vision.yaml` erzeugt
  eine byte-identische `edit.yaml`; ein anschließender `render` löst **null**
  Neurenderungen aus.
- **A4 — Idempotenz der Analyse.** Ein zweiter `analyze`-Lauf ohne geänderte
  Bilder macht null API-Aufrufe.
- **A5 — Ausfall.** Ohne API-Schlüssel und ohne Netz laufen `analyze`
  (mit Warnung, ohne Fehlercode) und `build` (mit heutiger Rotation) durch.
  Die Testsuite läuft ohne Netzzugriff.
- **A6 — Kostenbericht.** `analyze` meldet Ist-Tokenverbrauch und -kosten aus
  `usage`; die Abweichung von Abschnitt 9 liegt unter 25 %.
- **A7 — Grenzen bleiben.** `sanity_check` (`kenburns.py:273`) meldet für
  keine erzeugte Bewegung „Zoom über 2×" oder „praktisch keine Bewegung",
  außer bei `scene: document`, wo Stillstand die Absicht ist.
- **A8 — Handkorrektur gewinnt.** Ein von Hand in `edit.yaml` gesetztes `kb:`
  überlebt einen erneuten `render`-Lauf unverändert.
- **A9 — Suite.** `pytest` bleibt grün. Vorbestehende Ausnahme: die drei
  HDR-Tests in `tests/test_media.py`, die schon vor dieser Arbeit unter
  ffmpeg 8.1.2 scheitern (siehe `docs/briefing-beat-detection.md`, A8).

---

## 12. Risiken

- **Halluzinierte Schutzboxen.** Eine erfundene Box klemmt den Zoom auf 1,05
  und das Bild steht still. Gegenmaßnahmen: Plausibilitätsprüfung im Parser
  (Box in `[0,1]`, Fläche zwischen 1 % und 80 %, höchstens vier Boxen),
  Konfidenzschwelle aus E9, und `vision.yaml` als Sichtprüfung.
- **Monotonie trotz Analyse.** Bei sehr homogenem Material (30 Strandfotos)
  sind die Kandidatenmengen fast gleich, und der Strafterm hat wenig
  Spielraum. Abhilfe: Weite und Ease-Kurve als zusätzliche
  Abwechslungsachsen, notfalls gelegentlich gegen die Passung entscheiden.
  A2 fängt den Fall messbar ab.
- **Neuanalyse kostet den ganzen Renderlauf.** Modell- oder Prompt-Wechsel
  ändert alle Bewegungen und damit alle Segment-Hashes. Deshalb die Warnung
  aus Abschnitt 8 — und deshalb ist die Prompt-Version ein bewusst gepflegtes
  Feld, kein Nebeneffekt einer Textänderung.
- **Zoom-Vorrat frisst Zoomspanne.** Ein kräftiger Schwenk zwingt `z_min`
  hoch (Abschnitt 1.1) und lässt innerhalb von `zoom_total` wenig Zoomweite
  übrig. Bei sehr kurzen Slots kann beides zusammen zu wenig sein; dann
  gewinnt der Schwenk und der Zoom entfällt praktisch. Das ist eine bewusste
  Wahl und gehört in den Bericht, nicht in eine stille Klemmung.
- **Datenschutz.** Siehe E6. Das ist kein technisches Risiko, sondern eine
  Zusage an die Nutzer, die im Code sichtbar sein muss.

---

## 13. Nicht in diesem Umfang

Blendenwahl nach Bildinhalt (`xfade`-Modus), Bildreihenfolge nach Inhalt statt
chronologisch, Ken Burns auf Clips, Gesichtserkennung ohne API (lokales
Modell), automatische Bildauswahl bei Überdeckung. Jedes davon ist ein eigenes
Briefing; die ersten beiden werden durch `vision.yaml` aber deutlich billiger,
weil die Bildfakten dann schon vorliegen.
