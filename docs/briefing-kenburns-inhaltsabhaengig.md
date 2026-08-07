# Briefing: Inhaltsabhängige Ken-Burns-Effekte

**Status:** Konzept, offen · **Rev. 2 — geprüft gegen `main` a8b25ad (06.08.2026)** ·
**Betrifft:** neu `vision.py` + `kbplan.py`, dazu `kenburns.py`, `build.py`,
`models.py`, `cli.py` · **Vorbedingung:** `slideshow preprocess` (die Analyse
läuft auf den normalisierten Cache-Bildern)

Die Ken-Burns-Bewegung ist heute rein kennungsabhängig: Zoomrichtung aus dem
untersten Bit eines Hashes über den Bildpfad, Schwenkrichtung aus den nächsten
drei Bits. Das Bild selbst geht nicht ein. Folge: in ein Gruppenfoto wird auf
den Bildrand zugeschwenkt, ein Makro wird um 30 % vergrößert bis es weich wird,
und ein Panorama bekommt einen vertikalen Schwenk gegen die Bildachse.

Dieses Briefing beschreibt, wie der Bildinhalt in die Bewegungswahl eingeht —
ohne die Abwechslung zu verlieren, ohne Determinismus und Segment-Cache
aufzugeben, und **ohne die Positionsunabhängigkeit wieder herzugeben**, die
`main` sich inzwischen erarbeitet hat (Abschnitt 0.1 und 7).

Abschnitt 14 beantwortet die zweite Frage: Die Bildfakten werden für die
Kamerafahrt erhoben — wo im Werkzeug tragen sie sonst noch etwas?

---

## 0. Stand gegen `main` — was die Zwischenzeit am Konzept geändert hat

Rev. 1 dieses Briefings entstand auf dem Stand 4f97144. Seither sind 25 Commits
nach `main` gegangen — Titelfolien, manuelle Reihenfolge, Auswahl,
Kontaktbogen, und zwei Änderungen direkt an der Kamerafahrt. Das Konzept
**trägt weiterhin**: Ziel, Architektur und alle neun Entscheidungen aus
Abschnitt 10 bleiben. Falsch geworden sind Zahlen, Zeilenverweise und —
inhaltlich schwerwiegend — zwei Rechenwege und eine Nebenbedingung.

| # | Was sich geändert hat | Wirkung aufs Konzept |
|---|---|---|
| 1 | `plan_motion(key, …)` statt `plan_motion(index, …)` (2a401f9) | Abschnitt 1 falsch zitiert; **Abschnitt 7 war architektonisch unverträglich** — siehe 0.1 |
| 2 | `pan_anchor: center` + Deckel `0,5 − 1/(2z)` (99b7943) | Abschnitt 1.1(b) und 6.3 Schritt 4 überholt — siehe 0.2 |
| 3 | `_segment_from_slot` schreibt `kb:` (63b25ba) | Die „wichtigste Feststellung" von Rev. 1 ist erledigt: der Schreibpfad ist **fertig** |
| 4 | `_couple_focus_motion` schreibt selbst `kb:` und weicht vorhandenen aus | **Neue Kollision** — siehe 0.3 |
| 5 | Titelfolien sind Standbilder ohne Manifest-Eintrag | `analyze` muss sie überspringen, `build` darf sie nicht als Ausfall zählen |
| 6 | `pan_amount` → `pan_rate`/`pan_total` | Alle Zahlenbeispiele in 1.1 und 6.3 neu |
| 7 | `StillSegment.portrait` je Segment | Regel 6.2 muss den **wirksamen** Modus lesen, nicht den Default |
| 8 | `preprocess --order` normalisiert nur die Auswahl | `analyze` sieht nur die Auswahl — gut für die Kosten, folgenreich für Abschnitt 14 |
| 9 | `probe` liest Rating, Belichtungszeit, Brennweite, ISO, GPS | Signale, die es zur Zeit von Rev. 1 noch nicht gab |
| 10 | API-Stand 08/2026 nachgeprüft | Preise, Cache-Mindestpräfixe und Modell-IDs in Abschnitt 9 stimmen; zwei Korrekturen in Abschnitt 5 |

Punkt 1 bis 4 sind keine Textpflege, sondern Konzeptarbeit. Sie stehen deshalb
ausgeschrieben.

### 0.1 Die Abwechslung darf nicht an die Position zurück

`main` hat die Bewegungsrichtung bewusst von der Position an die **Kennung des
Bildes** gebunden (`motion_key`, `kenburns.py:74`). Der Grund steht im
Docstring von `plan_motion`: eine an Position 41 eingefügte Titelfolie verschob
vorher den Index jedes folgenden Bildes, damit dessen Bewegung, damit dessen
Cache-Key — der halbe Film rendert neu. Bezahlt wurde das mit einer nur noch
*statistischen* Alternierung.

Abschnitt 7 von Rev. 1 hat genau diesen Preis wieder eingekauft, ohne es zu
merken: eine gierige, sequentielle Auswahl mit Rückblick auf die drei Vorgänger
macht die Bewegung eines Bildes von seinen **Nachbarn** abhängig. Ein
eingefügtes Kapitel verschiebt das Fenster, ändert die Signaturen der
folgenden Stills, ändert deren `kb:`, ändert deren Cache-Key. Damit wäre auch
die Zusage aus Abschnitt 8 („eine geänderte Analyse invalidiert genau die
betroffenen Segmente") falsch gewesen — es wären alle nachfolgenden.

Abschnitt 7 ist deshalb neu geschrieben: **die Abwechslung kommt aus derselben
Quelle wie heute, dem Kennungs-Hash, nur wählt sie jetzt aus einer
inhaltsgeprüften Kandidatenmenge statt aus acht festen Himmelsrichtungen.**
Das ist strikt besser als heute (die Kandidaten sind passend *und* zulässig)
und kostet die Positionsunabhängigkeit nicht.

### 0.2 Der Schwenk hängt am größten Zoom, nicht am kleinsten

Rev. 1 rechnete: ein Schwenk um `a` verlangt durchgehend `z ≥ 1/(1 − 2a)`,
also Zoom-Vorrat am **kleineren** Ende der Bewegung, und schlug vor, `z_min`
bis 1,10 anzuheben.

`main` löst dasselbe Problem anders. `_pan` (`kenburns.py:167`) legt das
**ruhende Ende in die Bildmitte** — beim Hineinzoomen den Anfang, beim
Herauszoomen das Ende — und deckelt den Weg auf

    weg ≤ 0,5 − 1/(2·z_max)

Damit läuft der Schwenk mit der aufgehenden Klemmung statt gegen sie, und
`z_min` muss nicht angehoben werden. Schritt 4 der Klemmkette ist gegenstandslos
geworden; an seine Stelle tritt eine schärfere Rechnung (6.3).

Was **nicht** verschwunden ist, ist der Zielkonflikt — er ist nur zum Normalfall
geworden. Mit den Vorgaben (`zoom_total` bis 0,30, also `z_max` = 1,30) liegt
der Deckel bei **0,115**; `pan_rate` 0,03 über 5 s will 0,15. Der Deckel
gewinnt, und die Obergrenze von `pan_total` (0,18) ist mit den Vorgaben
überhaupt nicht erreichbar — dafür bräuchte es `z_max` ≥ 1,56. `CLAUDE.md`
sagt es in einer Zeile: *„Mehr Schwenk heißt jetzt mehr `zoom_total`."*

Für dieses Briefing folgt daraus etwas Unbequemes: **ein Schwenk, der ein Motiv
außerhalb der Mitte ansteuert, ist mit den heutigen Vorgaben nur schwach
möglich.** Ein Gesicht bei `c = (0,38 · 0,47)` liegt 0,12 von der Mitte
entfernt und verlangt `z ≥ 1,316` — knapp über dem, was `zoom_total` hergibt.
Der Planer muss das melden, nicht still zurechtstutzen (Abschnitt 6.3,
Schritt 5).

### 0.3 Wer `kb:` schreibt, schaltet die Fokusblende ab

`_couple_focus_motion` (`build.py:643`) koppelt Titelfolie und Folgebild zu
*einer* durchgehenden Fahrt, wenn der Hintergrund der Folie dasselbe Bild ist
— der Schärfezug, der die Folie in das Foto auflöst. Sie schreibt dafür `kb:`
auf **beide** Slots. Und sie tut es nur, wenn dort noch nichts steht:

```python
if slot.intent.kb is not None or folge.intent.kb is not None:
    continue                                    # build.py:665
```

Ein Vision-Planer, der stumpf jedes Standbild mit `kb:` versieht, schaltet
damit die Fokusblende jedes Kapitelanfangs ab — der Schärfezug wird zum
Schnitt zwischen zwei ähnlichen Bildern, genau dem Fall, den der Docstring
dort als „das Schlechteste aus beiden Welten" bezeichnet. Der Fehler wäre
still: der Film rendert, sieht nur schlechter aus.

Regel, neu in 6.4: **Der Vision-Planer läuft vor `_couple_focus_motion` und
lässt das Folgebild einer Fokusblende in Ruhe.** Der Bildinhalt geht dort
trotzdem ein — über die Schutzprüfung, die `_couple_focus_motion` nachgelagert
anwenden kann.

---

## 1. Ausgangslage

`plan_motion` (`kenburns.py:91`) leitet die komplette Bewegung aus zwei Zahlen
ab: einem Hash über die Kennung und der Dauer.

```python
n = motion_key(key)                                       # kenburns.py:116
zoom_in = (n & 1 == 0) or not defaults.alternate          # kenburns.py:129
weg     = min(max(defaults.pan_rate * duration, lo_p), hi_p)   # kenburns.py:144
dx, dy  = _DIRECTIONS[(n >> 1) % len(_DIRECTIONS)]        # kenburns.py:148
c0, c1  = _pan(weg, dx, dy, defaults.pan_anchor, …)       # kenburns.py:149
```

Was bereits vorhanden ist und getragen werden kann:

| Baustein | Zustand |
|---|---|
| `KBSpec` (`models.py:286`) — `z`, `c`, `ease`, `engine` je Segment | vorhanden, validiert |
| `plan_motion(…, spec)` überschreibt die Defaults vollständig | vorhanden |
| `render.py:77` reicht `slot.intent.kb` bis in den Filtergraph durch | vorhanden |
| `mlt.py:126` tut dasselbe für den Kdenlive-Export | vorhanden |
| Cache-Key enthält `vf` **und** `motion.fingerprint()` (`render.py:93`, `:242`) | vorhanden |
| `build._segment_from_slot` schreibt `kb:` nach `edit.yaml` (`build.py:837`) | **vorhanden** (war in Rev. 1 der offene Punkt) |
| `titles.title_kb` übersetzt `motion: none` in ein gewöhnliches `kb:` | vorhanden — Vorbild für den Planer |

Das ist die wichtigste Feststellung des Briefings, und sie ist seit Rev. 1 noch
stärker geworden: **der Rendervorgang ist fertig, und der Rückweg in die Datei
auch.** Eine inhaltsabhängige Bewegung muss nichts weiter tun, als vor dem
Schreiben der Edit-List ein `intent.kb` je Standbild zu setzen. `build`,
`render`, der Segment-Cache und der MLT-Export verhalten sich dann von selbst
richtig.

### 1.1 Zwei Randbedingungen, die das Konzept vorformen

**(a) Die Normalform vereinfacht die Geometrie.** Jedes Cache-Bild hat exakt
das Ausgabeseitenverhältnis (`preprocess.py:7-13`). Das sichtbare Fenster bei
Zoom `z` ist damit in normalisierten Koordinaten ein Rechteck der relativen
Breite **und** Höhe `1/z` um die Mitte `c`. Bildkoordinaten aus der Analyse
lassen sich also direkt gegen das Fenster prüfen — keine
Seitenverhältnis-Umrechnung, keine Sonderfälle.

**(b) Die Klemmung ist die eigentliche Nebenbedingung.** `zoompan_filter`
(`kenburns.py:258`) klemmt die Fensterposition an den Bildrand:

```
x = max(0, min(iw - iw/zoom, cx*iw - iw/zoom/2))
```

Unbeschnitten bleibt eine Bildmitte `c` zum Zeitpunkt `t` nur, solange

    |c(t) − 0,5| ≤ 0,5 − 1/(2·z(t))

gilt. Bei `z = 1,0` ist die rechte Seite **null**: die Mitte wird zwangsweise
auf 0,5 geklemmt.

Der Default-Pfad hält diese Bedingung heute von selbst ein — `_pan` legt das
ruhende Ende in die Mitte und deckelt die Strecke auf den Wert bei `z_max`
(0.2). **Ein Planer, der `c0` und `c1` selbst hinschreibt, umgeht `_pan`
vollständig** (`kenburns.py:156-158`) und damit auch den Deckel. Er muss die
Ungleichung deshalb selbst führen — sonst ist das geplante Fenster nicht das
sichtbare, und jede Schutzzusage aus Abschnitt 2 gilt für ein Rechteck, das
gar nicht auf dem Schirm ist.

Wie teuer das ist, zeigt die Umkehrung: eine Auslenkung `d` aus der Mitte
verlangt

    z ≥ 1 / (1 − 2d)

also z ≥ 1,064 für d = 0,03, z ≥ 1,136 für d = 0,06 und z ≥ 1,316 für d = 0,12.
Bei `zoom_total = (0,08 · 0,30)` ist bei d = 0,12 Schluss. **Zoomweite und
Schwenkweite sind ein Budget, kein Parameterpaar** — der Planer muss sie
gemeinsam vergeben (6.3), und wo das Budget nicht reicht, gehört das in den
Bericht (12).

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
   Bewegungen nicht auffällig. Diese Ebene steht **über** der Passung: wenn 30
   Fotos hintereinander Weitwinkel-Landschaft sind, darf nicht 30-mal derselbe
   Links-Rechts-Schwenk laufen.

Ebene 1 und 2 brauchen Bildverständnis. Ebene 3 ist reine Kombinatorik und
gehört ausdrücklich **nicht** ins Modell — und seit 0.1 auch nicht in eine
Sequenzbetrachtung.

---

## 3. Architektur: das Modell liefert Fakten, der Planer entscheidet

```
probe ──► manifest.json
             │
   beats ────┤        select ──► order.yaml  (Auswahl, optional)
             │           │
             ▼           ▼
        preprocess ──► cache/*.jpg          (nur die Auswahl)
                          │
                          ▼
                  slideshow analyze          (Claude API, einmalig je Bild)
                          │
                          ▼
                     vision.yaml             ← ansehen und korrigierbar
                          │
  beats.yaml ─────────────┤
  chapters.yaml ──────────┤
  order.yaml ─────────────┤
                          ▼
                  slideshow build ──► edit.yaml  (mit kb: je Standbild)
                          │
                          ▼
                  slideshow render            (unverändert)
```

`analyze` steht **nach `preprocess`** (es braucht die Cache-Bilder) und **vor
`build`**. Dass `preprocess` seit Stufe 2 des Auswahl-Briefings nur noch die
Auswahl normalisiert, ist dabei ein Geschenk: analysiert werden 187 Bilder,
nicht 1240. Für die Gegenrichtung — Analyse *für* die Auswahl — siehe 14.2.

Die Claude-API wird **nicht** gefragt, welcher Ken-Burns-Effekt passt. Sie
wird gefragt, was auf dem Bild zu sehen ist. Gründe:

- Das Modell kennt die Segmentdauer nicht, kennt die Nachbarbilder nicht und
  kann die Abwechslungsbedingung nicht erfüllen — die ist global.
- Eine gelieferte Bewegung wäre nicht nachvollziehbar prüfbar. Eine gelieferte
  Bounding-Box ist es: sie steht in `vision.yaml`, sie lässt sich mit einem
  Blick aufs Bild verifizieren und von Hand korrigieren.
- Bildfakten sind über Prompt- und Modellwechsel hinweg stabiler als
  Bewegungsurteile. Ein Modellwechsel darf nicht den kompletten
  Segment-Cache invalidieren (Abschnitt 8).
- Die Regeln „Gesicht nicht anschneiden" und „Makro nicht überzoomen" sind
  zehn Zeilen Code. Dafür braucht es kein Sprachmodell.
- **Neu als Argument:** Bildfakten tragen an fünf weiteren Stellen im Werkzeug
  (Abschnitt 14). Eine gelieferte `KBSpec` trägt an genau einer.

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
    quiet: [0.05, 0.62, 0.55, 0.95]   # ruhige Fläche, trägt Text (14.1)
    suggest: pan_right          # unverbindlich
    conf: 0.88
    note: "Fjord im Weitwinkel, Wanderer links im Vordergrund"
```

`scene`-Enum (Startmenge, bewusst klein — jede Klasse muss eine *andere*
Bewegungsregel nach sich ziehen, sonst gehört sie nicht ins Enum):

`landscape_wide` · `portrait_person` · `group` · `architecture` · `detail_macro`
· `action` · `interior` · `document` · `other`

Zwei Felder sind gegenüber Rev. 1 dazugekommen, beide für Abschnitt 14 und
beide praktisch gratis, weil das Modell das Bild ohnehin ansieht:

- **`quiet`** — die größte zusammenhängende Fläche ohne Motiv und ohne starke
  Struktur. Für den Titelfolien-Hintergrund (14.1) ist das die entscheidende
  Auskunft, und für den Planer ist sie ein guter Zielbereich, wenn `focus`
  fehlt.
- **`kind`** an den `subjects` — `person`, `face`, `text`, `animal`, `object`.
  Trägt 14.1 (kein Titel über einem Gesicht) und 14.4 (Vielfalt nach Motivart).

Das Format ist bewusst wie `beats.yaml` gebaut: eine Zeile je Objekt, von Hand
editierbar, zur Sichtprüfung gedacht. Wer eine falsche Box sieht, korrigiert
sie; ein erneuter `analyze`-Lauf überschreibt sie nicht, solange Bild-Hash,
Prompt-Version und Modell gleich bleiben.

**Titelfolien stehen nicht darin.** Ihr `src` (`title_asset`, `titles.py`) ist
ein erzeugtes Asset, kein Manifest-Medium; `analyze` überspringt sie, und
`build` darf sie nicht als „auf die Rotation zurückgefallen" zählen — sonst
meldet jeder Lauf mit Kapiteln einen Ausfall, den es nicht gibt.

---

## 5. Der API-Aufruf

| Aspekt | Festlegung |
|---|---|
| Modell | `claude-opus-5` als Default, `--model` überschreibt |
| Bild | Cache-Bild auf 1024×576 herunterskaliert, JPEG q80, base64 — eine Genauigkeits-, keine Kostenentscheidung (9) |
| Format | `output_config.format` mit JSON-Schema → garantiert parsebare Antwort |
| Aufwand | `output_config: {"effort": "low"}` — die Aufgabe ist Klassifikation, kein Denksport |
| `max_tokens` | **großzügig, nicht knapp** — siehe unten |
| Caching | Systemprompt + Schema als gecachter Präfix (`cache_control`), volatile Teile ans Ende |
| Durchsatz | ein Bild je Request, synchron und parallel; Batch-API nur auf Wunsch (`--batch`, s. E4) |
| Übertragung | Bild als base64 im Request — **nicht** über die Files-API (E6) |
| Wiederholung | SDK-Default (2 Retries), dazu Ausfall-Rückfall aus Abschnitt 8 |

Vier Details, die leicht Geld oder Korrektheit kosten, wenn man sie übersieht.
Die ersten beiden standen in Rev. 1, die letzten beiden sind Korrekturen aus
der Nachprüfung des API-Stands:

- **Thinking ist auf `claude-opus-5` per Default an** und wird als Ausgabe
  abgerechnet. Bei ~100 Bildern ist das der größte Kostenposten, nicht das
  Bild. Deshalb `effort: "low"`.
- **Der Prompt-Cache wird erst lesbar, wenn die erste Antwort zu streamen
  beginnt** — nicht erst, wenn sie fertig ist. Bei parallelem Fan-out zahlen
  sonst alle Requests den vollen Präfix. Also: einen Request absetzen, das
  erste Token abwarten, dann die übrigen 99 parallel.
- **Korrektur zu Rev. 1: `max_tokens` darf nicht knapp sein.** Auf
  `claude-opus-5` deckelt `max_tokens` **Thinking und Antworttext zusammen**.
  Ein knapper Wert schneidet nicht die Ausgabe, sondern erst das Denken und
  dann die Antwort ab — das Ergebnis ist kein sparsamer Lauf, sondern ein
  unbrauchbares JSON und ein bezahlter Request. Der Sparhebel ist `effort`,
  nicht `max_tokens`. Wer wirklich ohne Thinking arbeiten will, kann es auf
  `claude-opus-5` abschalten, aber nur bei `effort` ≤ `high` — und handelt
  sich dafür bekannte Nebenwirkungen ein. **Empfehlung: Thinking an lassen,
  `effort: "low"`, `max_tokens` mit Luft.**
- **Korrektur zu Rev. 1: das JSON-Schema kann die Wertebereiche nicht
  erzwingen.** Strukturierte Ausgabe unterstützt `enum`, `const`, `anyOf` und
  `additionalProperties: false`, aber **keine numerischen Schranken**
  (`minimum`, `maximum`) und keine Längenbegrenzungen. Eine Box mit `-0.3` oder
  eine `conf` von `1.7` ist schemakonform. Die Plausibilitätsprüfung aus
  Abschnitt 12 ist damit keine Vorsicht, sondern **Pflicht**; das Schema
  garantiert nur, dass sich die Antwort parsen lässt.

Die Analyse läuft auf dem **normalisierten** Bild, nicht auf dem Original.
Damit gelten die gelieferten Koordinaten unverändert im Koordinatensystem des
Ken-Burns-Filters — keine Transformation, kein Versatz. Der Preis: bei
`portrait: blur` sieht das Modell auch die unscharfen Seitenbalken. Das ist
kein Nachteil, sondern der Punkt — es ist genau das Bild, das gerendert wird,
und daraus folgt unmittelbar eine Regel (6.2).

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

Maßgeblich ist der **wirksame** Modus, nicht der Default: `StillSegment.portrait`
(`models.py:483`) überschreibt `defaults.portrait` je Segment. Der Planer liest
`seg.portrait or defaults.portrait`. Ob das Bild überhaupt hochkant ist, steht
im Manifest (`ImageInfo.portrait`, `models.py:64`) und muss nicht aus der
Analyse kommen.

### 6.3 Die Klemmkette

Der Planer rechnet in dieser Reihenfolge, jeder Schritt kann nur verkleinern:

1. **Zoom-Obergrenze aus dem Schutz.** Eine Schutzbox der Breite `bw` und Höhe
   `bh` passt nur bis `z ≤ 1 / max(bw, bh)`.
2. **Zoom-Obergrenze aus der Detaildichte.** `z_max ≤ 1 + (1 − detail) · 0,30`.
   Ein detailarmer Himmel verträgt Zoom, ein Makro nicht. Zusätzlich gilt
   weiterhin die harte Grenze aus `sanity_check` (`kenburns.py:382`): über 2×
   reicht der Subpixel-Vorrat nicht.
3. **Zoomspanne aus der Dauer**, wie heute (`zoom_rate`, `zoom_total`), dann
   gegen 1. und 2. geklemmt.
4. **Auslenkung je Endpunkt gegen die Klemmung.** Für beide Enden der Fahrt
   einzeln:

       |c_i − 0,5| ≤ 0,5 − 1/(2·z_i)         für i ∈ {0, 1}

   **Die Prüfung an den beiden Enden ist hier vollständig, nicht bloß eine
   Stichprobe.** Über den Fahrtparameter `e ∈ [0,1]` ist `|c(e) − 0,5|` konvex
   (Betrag einer Geraden) und `0,5 − 1/(2·z(e))` konkav (`z` linear in `e`,
   die Schranke konkav wachsend in `z`). Ihre Differenz ist konvex und nimmt
   ihr Maximum an einem Rand an. Wer beide Enden prüft, hat die ganze Bahn
   geprüft.

   Reicht es nicht, hat der Planer zwei Hebel: den betroffenen Endpunkt zur
   Mitte ziehen, oder `z` an diesem Ende anheben (kostet Zoomspanne).
   **Vorschlag: bis `z = 1,15` anheben, darüber die Auslenkung kürzen.** Der
   Schwenk ist die auffälligere Bewegung; ein Bild, das durchgehend um 15 %
   vergrößert steht, fällt niemandem auf. Kürzen ist zu **melden** — ein
   Motivschwenk, der auf halber Strecke endet, ist eine Entscheidung, keine
   Rundung (0.2 und 12).
5. **Schutzprüfung über die volle Bewegung.** Für `e ∈ {0, 0.125, …, 1}`
   (neun Stützstellen): liegt jede Schutzbox vollständig im Fenster
   `[c(e) ± 1/(2·z(e))]`?

   **Korrektur zu Rev. 1:** Der dortige Satz, fünf Stützstellen genügten, weil
   `z` und `c` monoton seien, trägt nicht. Für den Schutz lautet die Bedingung
   `|c(e) − b| + h ≤ 1/(2·z(e))`; links steht eine konvexe, rechts eine ebenfalls
   konvexe Funktion von `e`. Ein Randargument gibt es dafür nicht — die
   Verletzung kann in der Mitte liegen. Neun Stützstellen sind eine Stichprobe
   und bleiben eine; deshalb schließt sich daran: Auslenkung halbieren und
   erneut prüfen, höchstens dreimal, danach reiner Zoom auf `focus`.

### 6.4 Wer `kb:` schreibt — und wer nicht

Drei Stellen schreiben heute oder künftig ein `kb:`. Ihre Rangfolge muss
festliegen, sonst gewinnt die zufällige Reihenfolge im Code (0.3):

| Rang | Quelle | Gilt |
|---|---|---|
| 1 | **`kb:` von Hand in `edit.yaml`** | immer — A8 |
| 2 | **`titles.title_kb`** (`motion: none`) | für Titelfolien |
| 3 | **`_couple_focus_motion`** | für das Folienpaar einer Fokusblende |
| 4 | **Vision-Planer** | für alle übrigen Standbilder |

Daraus folgen drei Regeln:

- Der Vision-Planer läuft **vor** `_couple_focus_motion` und **überspringt**
  sowohl jede Titelfolie als auch das Folgebild einer Fokusblende
  (`_ist_fokusblende`, `build.py:608`). Ohne das fällt der Schärfezug aus.
- `_couple_focus_motion` darf die Schutzboxen des Folgebildes trotzdem
  auswerten: Zoom und Richtung stehen dort fest, aber der Endpunkt `ziel`
  (`build.py:690`) ist frei genug, um eine Schutzverletzung zu vermeiden. Das
  ist eine Verbesserung, kein Muss — sie gehört in Stufe 2.
- Ein Bild, das der Planer auslässt oder für das die Analyse fehlt, bekommt
  **kein** `kb:` und läuft damit über den heutigen Pfad. Das ist der
  Ausfallpfad aus Abschnitt 8, und er kostet keine Sonderbehandlung.

---

## 7. Abwechslung — ohne die Position zurückzuholen

Ohne Gegenmaßnahme erzeugt Abschnitt 6 bei homogenem Material Monotonie: 30
Landschaftsbilder → 30 gleiche Schwenks. Rev. 1 löste das mit einem gierigen
Durchlauf über die Bildfolge. Das ist verworfen (0.1): es macht die Bewegung
eines Bildes von seinen Nachbarn abhängig und damit von der Position.

**Der Ersatz nutzt dieselbe Quelle wie `main` heute — den Kennungs-Hash — nur
mit besserer Auswahl.**

**Bewegungssignatur** eines Kandidaten:

    (zoom ∈ {ein, aus}, schwenk ∈ {0..7, keiner}, weite ∈ {S, M, L})

**Kandidatenmenge.** Abschnitt 6 liefert je Bild nicht eine Bewegung, sondern
eine nach Passung sortierte Liste zulässiger Signaturen — zulässig heißt: alle
Schritte der Klemmkette bestanden. Für ein Landschaftsbild mit
`axis: horizontal` sind das typischerweise vier bis sechs (links/rechts × ein/aus
× zwei Weiten), für ein Makro genau eine.

**Auswahl:**

```python
kandidaten = sorted(zulaessig, key=passung, reverse=True)[:K]     # K = 4
gewaehlt   = kandidaten[motion_key(src) % len(kandidaten)]
```

Das ist deterministisch, hängt ausschließlich am Bild, und ist über eine
Bildmenge hinweg gleichverteilt — dieselbe Zusage wie heute für die acht
Himmelsrichtungen, nur dass jetzt keine der Möglichkeiten gegen den Bildinhalt
läuft. `K` ist der einzige Regler (`--variety`, Vorgabe 4; `1` = reine
Passung).

Was dabei verloren geht, ist ehrlich zu benennen: **eine harte Zusage „kein
Signatur-Duplikat innerhalb von drei aufeinanderfolgenden Stills" ist so nicht
zu haben.** Sie war nur über die Sequenz zu haben, und die Sequenz ist zu
teuer. Was bleibt, ist eine statistische Zusage — genau die, die `main` beim
Zoomwechsel bereits bewusst eingegangen ist (`docs/edit-yaml.md`: „bei 40
Bildern typischerweise bis zu vier gleiche Richtungen hintereinander").
Abnahmekriterium A2 ist entsprechend umformuliert.

**Wenn sich in der Sichtprüfung zeigt, dass das nicht reicht**, gibt es einen
positionsfreien Ausweg, der nicht in dieses Briefing gehört, aber genannt sein
soll: die Kennung des **Vorgängers in der `order.yaml`** mit in den Hash
nehmen. Das ist gegen Umsortieren nicht stabil, aber gegen Einfügen an anderer
Stelle sehr wohl — ein eingefügtes Kapitel ändert dann genau zwei Bewegungen
statt aller folgenden. Erst messen, dann bauen.

---

## 8. Einbettung, Determinismus, Ausfall

**Wo das Ergebnis landet.** `build` liest `vision.yaml`, ruft den Planer und
setzt je Standbild ein `intent.kb`; `_segment_from_slot` schreibt es nach
`edit.yaml` (`build.py:837`):

```yaml
- {type: still, src: cache/img_0042.jpg, beats: 8,
   kb: {z: [1.1, 1.24], c: [0.5, 0.5, 0.44, 0.34]}}
```

Das bleibt Absicht, nicht gemessene Dauer — der Konflikt aus
`_segment_from_slot` entsteht nicht. Und es bleibt von Hand überschreibbar:
wer eine Bewegung nicht mag, ändert vier Zahlen.

**Determinismus.** Modellantworten sind nicht bit-reproduzierbar. Der
Determinismus entsteht dadurch, dass die Antwort **einmal** in `vision.yaml`
festgeschrieben wird; ab da ist die ganze Kette rein deterministisch.
`analyze` ruft die API nur, wenn `hash + prompt + model` sich geändert haben —
ein zweiter Lauf ohne neue Bilder macht null Requests. (`temperature` steht auf
`claude-opus-5` ohnehin nicht zur Verfügung; ein Wert dort wird mit 400
abgewiesen.)

**Ausfall.** Fehlender Schlüssel, kein Netz, Rate-Limit, Refusal, unlesbares
JSON, `conf < 0.5` — jeder dieser Fälle fällt auf das heutige Verhalten
zurück, je Bild einzeln, indem schlicht kein `kb:` gesetzt wird (6.4). `build`
läuft immer durch; im Report steht, wie viele Bilder analysiert wurden und wie
viele auf die Rotation zurückgefallen sind. Titelfolien zählen dabei nicht mit
(Abschnitt 4). `--no-vision` schaltet die Analyse komplett ab.

**Cache-Wirkung.** `motion.fingerprint()` steckt im Segment-Cache-Key
(`render.py:93` → `:242`). Eine geänderte Analyse invalidiert genau die
betroffenen Segmente und ihre zwei Nachbar-Blenden — das ist Prinzip 2, und
seit Abschnitt 7 positionsfrei rechnet, stimmt die Zusage auch wirklich. Aber:
**eine Neuanalyse aller Bilder (neues Modell, neuer Prompt) invalidiert den
kompletten Renderlauf.** `analyze` muss davor warnen und die Zahl der
betroffenen Segmente nennen, bevor es losläuft.

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
modellabhängig und **nicht monoton über die Generationen**: 512 Tokens auf
Opus 5, 1 024 auf Sonnet 5 — **4 096 auf Haiku 4.5**, unser 1 200-Token-Präfix
cached dort also gar nicht.

### Kosten je 100 Bilder (USD)

| Modell | Preis in/out je M | synchron, sparsam | synchron, denkfreudig | Batch-API (−50 %) |
|---|---|---|---|---|
| **Opus 5** | 5 / 25 | **1,10** | 2,22 | 0,55 – 1,11 |
| **Sonnet 5** | 3 / 15 | **0,66** | 1,33 | 0,33 – 0,67 |
| **Haiku 4.5** | 1 / 5 | **0,33** | 0,55 | 0,16 – 0,28 |

Sonnet 5 liegt bis 31.08.2026 auf einem Einführungspreis von 2/10 — dann
etwa ein Drittel günstiger als oben. Für die Planung ist der Regelpreis
angesetzt.

### Erstanbieter-API oder Bedrock? Preise für europäische Regionen

Claude läuft auch auf Amazon Bedrock, und für ein Projekt, das private
Personenfotos verschickt, ist das nicht nur eine Abrechnungsfrage — siehe E11
und E6. Die Preise stehen hier, die Entscheidung in E11.

**Grundsatz.** Die Bedrock-Grundpreise entsprechen den Listenpreisen der
Erstanbieter-API. Bedrock kennt aber zwei Endpunktarten, und genau daran hängt
der Aufpreis:

- **Global** — dynamisches Routing über alle Regionen, **kein Aufpreis**.
- **Regional / EU-Inferenzprofil** — garantiertes Routing durch die gewählte
  Region beziehungsweise innerhalb der EU, **10 % Aufpreis auf alles**:
  Eingabe, Ausgabe, Cache-Schreiben, Cache-Lesen.

Wer Bedrock wegen der Datenresidenz nimmt, zahlt also die 10 % — sie *sind* die
Datenresidenz. Verfügbare EU-Regionen: Frankfurt (`eu-central-1`), Zürich
(`eu-central-2`), Stockholm (`eu-north-1`), Mailand (`eu-south-1`), Spanien
(`eu-south-2`), Irland (`eu-west-1`), London (`eu-west-2`), Paris
(`eu-west-3`) — alle mit EU-Inferenzprofil.

**Kosten je 100 Bilder**, gerechnet mit dem Tokenprofil von oben (786 Bild- +
40 Texttokens, 1200 Tokens gecachter Präfix, 250 Ausgabetokens):

| Modell (Bedrock-ID `anthropic.…`) | Preis in/out je M | Präfix cached? | Global | **EU (+10 %)** |
|---|---|---|---|---|
| `claude-fable-5` | 10 / 50 | ja (512) | 2,20 $ | **2,42 $** |
| `claude-opus-5` | 5 / 25 | ja (512) | 1,10 $ | **1,21 $** |
| `claude-opus-4-8` | 5 / 25 | ja (1024) | 1,10 $ | **1,21 $** |
| `claude-opus-4-7` | 5 / 25 | **nein (2048)** | 1,64 $ | **1,80 $** |
| `claude-sonnet-5` bis 31.08.2026 | 2 / 10 | ja (1024) | 0,44 $ | **0,48 $** |
| `claude-sonnet-5` ab 01.09.2026 | 3 / 15 | ja (1024) | 0,66 $ | **0,72 $** |
| `claude-haiku-4-5` | 1 / 5 | **nein (4096)** | 0,33 $ | **0,36 $** |

Zwei Zeilen darin sind kontraintuitiv und beide kommen vom
Cache-Mindestpräfix, nicht vom Listenpreis:

- **Opus 4.7 ist teurer als Opus 5**, obwohl beide 5/25 kosten: sein
  Mindestpräfix liegt bei 2048 Tokens, unsere 1200 cachen dort nicht.
- **Haiku 4.5 spart weniger, als der Listenpreis verspricht** (4096) — der
  Abstand zu Sonnet 5 schrumpft dadurch auf ein Drittel statt zwei Dritteln.

**Auf das reale Projekt hochgerechnet** (187 gewählte Bilder, EU-Endpunkt):
Opus 5 **2,26 $**, Sonnet 5 **1,35 $**, Haiku 4.5 **0,67 $**. Für die
Auswahlanalyse aus 14.2 über ein Sammelbecken von 1240 Bildern: 15,00 $ /
8,93 $ / 4,46 $.

**Was auf Bedrock fehlt und hier zählt:** die **Message-Batches-API gibt es
nicht**. Die −50 % aus der Tabelle weiter oben sind auf Bedrock also nicht zu
haben — was insofern schade ist, als der Grund, sie auf der Erstanbieter-API
abzulehnen (29 Tage Aufbewahrung, E4), auf Bedrock gerade entfiele. Ebenfalls
nicht vorhanden: Files-API und URL-Bildquellen (beides egal, wir schicken
base64) und der serverseitige Refusal-Rückfall (egal, Abschnitt 8 fängt je
Bild selbst ab). **Vorhanden und für dieses Briefing entscheidend sind
strukturierte Ausgabe, Prompt-Caching und Thinking.**

> **Der Vollständigkeit halber, ohne Empfehlung:** Bedrock führt auch
> bildfähige Fremdmodelle, und die sind dramatisch billiger — Amazon Nova Lite
> liegt bei etwa 0,06/0,24 $ je Million, was auf ungefähr 0,02 $ je 100 Bilder
> hinausliefe, Nova Pro auf etwa 0,24 $. Diese Zahlen stammen aus
> Sekundärquellen, nicht aus der AWS-Preisliste, und **die Rechnung trägt
> ohnehin nicht**: das Tokenmodell oben ist auf Claudes Bildzählung geeicht,
> andere Modellfamilien zählen Bilder anders, und vor allem hängt Abschnitt 5
> an einer **garantiert schemakonformen** Antwort und an brauchbaren
> Bounding-Box-Koordinaten. Wer hier wechseln will, misst das an fünf echten
> Bildern nach, statt einer Preistabelle zu glauben.

### Bildgröße — skaliert wird nur oberhalb des Deckels

Eine Frage, die beim Lesen sofort kommt: lohnt es, kleinere Bilder zu
schicken, oder rechnet die API das ohnehin herunter? Beides, je nach Größe.

**Oberhalb der Maximalkante des Modells wird skaliert und *dann* abgerechnet.**
Das Cache-Bild ungefragt zu schicken kostet also nicht die 44 000 Tokens, die
`(7680·4320)/750` ergäbe — es landet bei ~2576 px Langkante und damit beim
Deckel von ~4784 Tokens. „Vollformat" wird nie als Vollformat bezahlt.

**Unterhalb des Deckels wird nichts hochskaliert.** Dort zahlt man, was man
schickt, und die Tokenzahl geht linear mit der Fläche.

| Analysebild | Tokens ≈ w·h/750 | Bildanteil je 100 | **Gesamt je 100** |
|---|---|---|---|
| Cache-Bild ungefragt geschickt (→ 2576 px) | 4784 | 2,39 $ | 3,10 $ |
| EXIF-Vorschau, wie eingebettet (1616×1077) | 2320 | 1,16 $ | 1,87 $ |
| **1024×576 (Festlegung Abschnitt 5)** | **786** | **0,39 $** | **1,10 $** |
| 768×432 | 442 | 0,22 $ | 0,93 $ |
| 512×288 | 197 | 0,10 $ | 0,81 $ |
| 320×180 | 77 | 0,04 $ | 0,74 $ |

Die Formel ist eine Näherung und die Deckel sind modellabhängig — vor dem
ersten großen Lauf gehört das mit `count_tokens` an fünf echten Bildern in
beiden Größen nachgemessen (A6).

### Was daraus folgt

- **Die Bildgröße hat einen Boden, den sie nicht unterschreiten kann.** Der
  bildunabhängige Teil — 250 Ausgabetokens, gecachter Präfix, Nutzertext —
  sind **0,71 $ je 100 Bilder**, und den bekommt keine Auflösung weg. Selbst
  ein kostenloses Bild spart nur 36 %. Von 1024×576 auf Thumbnailgröße sind es
  35 ct je 100, von 1024×576 auf 768×432 nur 17 ct. Der Hebel ist `effort`
  (und **nicht** `max_tokens`, Abschnitt 5).
- **Über die Auflösung entscheidet trotzdem nicht der Preis, sondern die
  Aufgabe.** Die 1024×576 stehen hier, weil daraus **Koordinaten** kommen
  sollen. Eine Schutzbox auf einem 320er Bild ist auf ±3 Pixel genau,
  hochgerechnet auf 7680 sind das ±72 — und eine zu große Schutzbox klemmt den
  Zoom auf 1,05 und lässt das Bild stehen (Abschnitt 12). Für koordinatenfreie
  Etiketten (14.2) gilt das nicht; dort sind 512×288 die naheliegende Wahl,
  und dort lohnt das Sparen auch.
- **Die Absolutkosten sind vernachlässigbar — für die Kamerafahrt.** Ein
  Urlaubsprojekt mit 100 gewählten Fotos kostet einmalig ein bis zwei Dollar
  und wird bei Wiederholungsläufen aus `vision.yaml` bedient. Dagegen stehen
  Stunden Renderzeit. Es gibt keinen Kostengrund, am Modell zu sparen —
  deshalb Opus 5 als Default.
- **Für die Auswahl gilt das nicht mehr.** Ein Sammelbecken hat 1240 Bilder,
  nicht 187; auf Opus 5 wären das 13,60 USD statt 2,05. Das ist kein
  Ausschlussgrund, aber es ist die Größenordnung, ab der man über Modell und
  Bildmenge nachdenkt (14.2).
- **Die Batch-API halbiert den Preis, kostet aber 29 Tage Aufbewahrung.** Ein
  Analyselauf ist zwar kein interaktiver Vorgang, aber die Batch-API ist nicht
  ZDR-fähig und speichert die Aufträge 29 Tage. Bei privaten Fotos ist das der
  falsche Tausch für 0,55 USD je 100 Bilder; siehe E4.
- Vor dem ersten großen Lauf gehört `count_tokens` gegen fünf echte Bilder
  ausgeführt und mit der Tabelle verglichen. Die Ist-Kosten meldet `analyze`
  am Ende aus `usage` (Abnahmekriterium A6).

---

## 10. Offene Fragen und Entscheidungen

E1 bis E9 sind gegenüber Rev. 1 unverändert gültig; die Nachprüfung gegen
`main` hat keine davon umgestoßen. E10 ist neu und ersetzt die Sequenzfrage
aus Rev. 1.

### E1 — Liefert das Modell Fakten oder Bewegung?

**(a) Fakten, lokaler Planer entscheidet** *(Empfehlung)* — begründet in
Abschnitt 3: prüfbar, korrigierbar, deterministisch, die Abwechslungsbedingung
ist ohnehin nicht modellierbar, und die Fakten tragen an fünf weiteren Stellen
(14).

**(b) Das Modell liefert fertige `KBSpec`-Blöcke** — weniger Code, aber jede
Bewegung wird zum Vertrauensakt, `vision.yaml` verliert seinen Zweck als
Sichtprüfung, die Kosten steigen (längere Ausgabe), und das Modell kennt weder
die Klemmung noch den wirksamen `portrait`-Modus.

> **Empfehlung: (a)**, mit dem unverbindlichen `suggest`-Feld als
> Stichentscheid.

### E2 — Analysebild: Cache-Bild oder Original?

**(a) Normalisiertes Cache-Bild** *(Empfehlung)* — Koordinaten gelten 1:1 im
Filterkoordinatensystem, keine Transformation, und das Modell sieht genau das
Bild, das gerendert wird (inklusive der Hochformat-Balken, woraus Regel 6.2
folgt).

**(b) Original** — mehr Bildinhalt sichtbar, aber jede Koordinate muss durch
`_cover_crop`/`_portrait_composite` zurückgerechnet werden. Ein Fehler dort
verschiebt Schutzboxen unbemerkt, und genau die sollen ja schützen.

> **Empfehlung: (a).** Der einzige Nachteil sind ein paar Tokens für
> unscharfe Balken. **Nachtrag Rev. 2:** die Entscheidung gilt für die
> *Kamerafahrt*. Die koordinatenfreien Fakten aus 14.2 dürfen auf den
> Thumbnails laufen — siehe dort.

### E3 — Wo werden die Ergebnisse abgelegt?

**(a) Eigenes `vision.yaml`** *(Empfehlung)* — gleiche Rolle wie `beats.yaml`:
eigenes Kommando, eigener Zeitpunkt, zur Sichtprüfung gedacht, von Hand
korrigierbar. `build` liest es und schreibt aufgelöste `kb:`-Blöcke.

**(b) `manifest.json` erweitern** — Manifest ist maschinenerzeugtes JSON aus
`probe` und ausdrücklich nicht zum Anfassen gedacht; eine Neuanalyse müsste
das Manifest neu schreiben.

**(c) Direkt `kb:` nach `edit.yaml`, kein Zwischenformat** — spart eine Datei,
verliert aber die Trennung zwischen „was ist auf dem Bild" und „wie bewegt
sich die Kamera". Ein `--variety`-Wechsel erzwingt dann eine Neuanalyse — und
Abschnitt 14 wäre gar nicht möglich, weil `select` und `chapters` vor
`edit.yaml` laufen.

> **Empfehlung: (a).** Zwei Artefakte mit je einem klaren Besitzer. Rev. 2
> stärkt das: `vision.yaml` ist die gemeinsame Faktenbasis für vier Kommandos,
> nicht nur für `build`.

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
> Anwender, denen die 29 Tage gleichgültig sind.

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

**Die Tabelle oben ist nicht die einzige mögliche.** Über Amazon Bedrock
gelten andere Zusagen — die Inferenz läuft dort auf AWS-verwalteter
Infrastruktur ohne Zugriff von Anthropic-Personal, und ein regionaler Endpunkt
hält die Bilder in der gewählten EU-Region. Das ist genau der Punkt, an dem
E6 heute am schwächsten ist; siehe E11 und Abschnitt 9.

Quellen: [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention),
[How long do you store my organization's data?](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data),
[Data retention practices for Covered Models](https://support.claude.com/en/articles/15425996-data-retention-practices-for-covered-models).
Policies ändern sich — vor der Umsetzung neu prüfen.

### E7 — Geht der Musikkontext in die Bewegung ein?

Naheliegend: Beat-Regionen bekommen kräftigere Bewegung, `free`/stille
Regionen ruhigere. Technisch fehlt dafür nur die Durchreichung der Region an
den Planer — `plan_motion` bekommt heute nur Kennung und Dauer.

> **Empfehlung: v1 nein.** Die Dauer korreliert bereits mit der Regionsart
> (kurze Slots in dichten Beat-Regionen), und `zoom_rate` skaliert schon über
> die Dauer. Der Zusatznutzen ist gering, die Kopplung zwischen Planer und
> Regionenkarte teuer. Als Nachtrag jederzeit möglich.

### E8 — Reicht das Zwei-Punkt-Bewegungsmodell?

`KBSpec` kann genau `z0 → z1` und `c0 → c1`. Eine Fahrt, die erst schwenkt und
dann stehen bleibt, ist damit nicht ausdrückbar.

> **Empfehlung: v1 unverändert lassen.** Alle Regeln aus Abschnitt 6 kommen
> mit zwei Punkten aus. Eine Erweiterung auf Stützstellen wäre eine
> Schemaänderung an `edit.yaml` plus Umbau beider Renderpfade **und** des
> MLT-Exports — das gehört in ein eigenes Briefing, wenn der Bedarf belegt ist.

### E9 — Was passiert mit niedriger Konfidenz?

> **Empfehlung:** `conf < 0.5` → Bild wird wie `scene: other` behandelt
> (heutige Rotation), Schutzboxen werden aber **trotzdem** respektiert. Eine
> unsichere Klassifikation ist ein schwaches Signal für die Passung, aber
> immer noch besser als nichts für den Schutz.

### E10 — Wie wird die Abwechslung erzeugt? *(neu in Rev. 2)*

**(a) Kennungs-Hash wählt aus der Kandidatenliste** *(Empfehlung)* — Abschnitt
7. Positionsfrei, deterministisch, verträglich mit dem, was `main` in 2a401f9
entschieden hat. Preis: nur statistische Abwechslung, keine harte Zusage über
Nachbarschaften.

**(b) Gierige Sequenzauswahl mit Strafterm** — die Fassung aus Rev. 1. Liefert
eine harte Zusage („kein Duplikat in drei Folgen"), macht die Bewegung aber
positionsabhängig: ein eingefügtes Kapitel rendert den Rest des Films neu, und
die Cache-Zusage aus Abschnitt 8 fällt.

**(c) Hash über Kennung *und* Vorgänger-Kennung** — Mittelweg. Einfügen kostet
zwei Neurenderungen statt aller folgenden, Umsortieren kostet mehr. Nicht
unvernünftig, aber schwerer zu erklären.

> **Empfehlung: (a).** (c) bleibt als Nachtrag offen, wenn die Sichtprüfung
> Monotonie zeigt. (b) ist ausgeschlossen — sie kauft eine Zusage, die
> `main` bewusst verkauft hat.

### E11 — Erstanbieter-API oder Amazon Bedrock? *(neu in Rev. 2)*

Die Preise stehen in Abschnitt 9; sie entscheiden die Frage **nicht**. Auf
187 Bilder gerechnet trennt Opus 5 auf einem EU-Endpunkt (2,26 $) von Haiku
auf der Erstanbieter-API (0,62 $) weniger als ein Kaffee. Entschieden wird
über Datenhaltung und Betriebsaufwand.

**(a) Erstanbieter-API** *(Empfehlung für v1)* — ein Schlüssel, vorausbezahltes
Guthaben, alle Funktionen inklusive Batch-API, Modell-IDs ohne Präfix, `ant`
und `count_tokens` verfügbar. Die Datenlage ist die aus E6: 30 Tage Regel-
aufbewahrung, kein Training, aber bis zu 2 Jahre bei automatisch markierten
Inhalten, und ZDR praktisch nicht erreichbar.

**(b) Amazon Bedrock, EU-Inferenzprofil** — dieselbe Messages-API,
`AnthropicBedrockMantle`, Modell-IDs mit `anthropic.`-Präfix, 10 % Aufpreis.
Der Gewinn liegt genau dort, wo E6 heute am schwächsten ist: die Inferenz läuft
auf AWS-verwalteter Infrastruktur, **Anthropic-Personal hat keinen Zugriff
darauf**, die Datenhaltung richtet sich nach Amazon Bedrock statt nach den
Anthropic-Zusagen, und der regionale Endpunkt garantiert, dass die Bilder die
gewählte Region nicht verlassen. Für private Urlaubsfotos mit erkennbaren
Personen ist das der sachlich bessere Ort. Preis dafür: AWS-Konto und
IAM-Einrichtung, keine Batch-API, und die AWS-Datenschutzbedingungen sind
eigenständig zu lesen — sie sind nicht die aus E6, im Guten wie im
Ungewissen.

**(c) Fremdmodell auf Bedrock** — ausgeschlossen, solange Abschnitt 5 an
garantiert schemakonformer Ausgabe und brauchbaren Boxkoordinaten hängt. Der
Preisvorteil ist real und irrelevant: er spart zwei Dollar.

> **Empfehlung: (a) für v1, (b) als unterstützter Pfad.** Das kostet wenig,
> weil beide dieselbe Messages-API sprechen: `analyze` braucht eine
> Client-Weiche und einen Präfix am Modellnamen, sonst nichts. Wer die Fotos
> nicht aus der EU herauslassen will, soll das ohne Umbau tun können — und die
> Bestätigung beim ersten Lauf (E6) muss dann die **andere** Zusagentabelle
> zeigen, nicht die von Anthropic.

---

## 11. Abnahmekriterien

- **A1 — Schutz.** Für jedes Bild mit `protect`-Box liegt jede Box an allen
  neun Stützstellen vollständig im sichtbaren Fenster. Automatisiert prüfbar
  aus `edit.yaml` + `vision.yaml`, ohne zu rendern.
- **A1b — Klemmung.** Für jedes erzeugte `kb:` gilt an beiden Enden
  `|c_i − 0,5| ≤ 0,5 − 1/(2·z_i)`. Das geplante Fenster ist damit das
  sichtbare; ohne dieses Kriterium prüft A1 ein Rechteck, das der Filter nie
  zeigt.
- **A2 — Abwechslung** *(gegenüber Rev. 1 statistisch formuliert, siehe E10)*.
  Über einen Lauf mit ≥ 40 Standbildern: keine Zoomrichtung über 65 % Anteil;
  mindestens sechs der acht Schwenkrichtungen kommen vor; kein
  Signatur-Anteil über 30 %. Eine Nachbarschaftszusage wird **nicht** geprüft,
  weil sie nicht gegeben wird.
- **A3 — Determinismus.** Zweimal `build` auf demselben `vision.yaml` erzeugt
  eine byte-identische `edit.yaml`; ein anschließender `render` löst **null**
  Neurenderungen aus.
- **A3b — Positionsunabhängigkeit.** Ein Kapitel an Position 41 eingefügt und
  `build` erneut ausgeführt: die `kb:`-Blöcke aller übrigen Standbilder sind
  unverändert. Das ist die Zusage aus 2a401f9, und dieses Briefing darf sie
  nicht brechen.
- **A4 — Idempotenz der Analyse.** Ein zweiter `analyze`-Lauf ohne geänderte
  Bilder macht null API-Aufrufe.
- **A5 — Ausfall.** Ohne API-Schlüssel und ohne Netz laufen `analyze`
  (mit Warnung, ohne Fehlercode) und `build` (mit heutiger Rotation) durch.
  Die Testsuite läuft ohne Netzzugriff.
- **A6 — Kostenbericht.** `analyze` meldet Ist-Tokenverbrauch und -kosten aus
  `usage`; die Abweichung von Abschnitt 9 liegt unter 25 %.
- **A7 — Grenzen bleiben.** `sanity_check` (`kenburns.py:382`) meldet für
  keine erzeugte Bewegung „Zoom über 2×" oder „praktisch keine Bewegung",
  außer bei `scene: document`, wo Stillstand die Absicht ist.
- **A8 — Handkorrektur gewinnt.** Ein von Hand in `edit.yaml` gesetztes `kb:`
  überlebt einen erneuten `render`-Lauf unverändert.
- **A9 — Fokusblende überlebt.** In einem Projekt mit `chapters.yaml` und
  `bg: auto` ist für jedes Folienpaar mit Fokusblende die gekoppelte Fahrt
  weiterhin gesetzt: `z1` der Folie = `z0` des Folgebilds und `c1` = `c0`.
  Ohne dieses Kriterium fällt der Schärfezug still aus (0.3).
- **A10 — Suite.** `pytest` bleibt grün. Vorbestehende Ausnahme: die drei
  HDR-Tests in `tests/test_media.py`, die schon vor dieser Arbeit unter
  ffmpeg 8.1.2 scheitern (`CLAUDE.md`, Abschnitt „Tests").
- **A11 — Sichtprüfung.** Ein realer Lauf, angesehen: fährt die Kamera in die
  Bilder hinein statt an ihnen vorbei? Nicht automatisierbar und trotzdem das
  wichtigste Kriterium — dieselbe Rolle wie A10 im Auswahl-Briefing.

---

## 12. Risiken

- **Halluzinierte Schutzboxen.** Eine erfundene Box klemmt den Zoom auf 1,05
  und das Bild steht still. Gegenmaßnahmen: Plausibilitätsprüfung im Parser
  (Box in `[0,1]`, Fläche zwischen 1 % und 80 %, höchstens vier Boxen),
  Konfidenzschwelle aus E9, und `vision.yaml` als Sichtprüfung. **Die
  Parserprüfung ist Pflicht, nicht Kür** — das JSON-Schema kann numerische
  Schranken nicht ausdrücken (Abschnitt 5).
- **Der Schwenk aufs Motiv ist mit den Vorgaben kaum bezahlbar.** Ein Ziel
  0,12 von der Mitte verlangt `z ≥ 1,316`, `zoom_total` gibt 1,30 her (0.2).
  Der Planer wird also häufig kürzen. Das gehört gezählt in den Bericht
  („17 von 94 Schwenks gekürzt, davon 6 auf unter die Hälfte") — und wenn die
  Zahl groß ist, ist die Antwort nicht mehr Planerarbeit, sondern eine
  größere Vorgabe für `zoom_total`.
- **Monotonie trotz Analyse.** Bei sehr homogenem Material (30 Strandfotos)
  sind die Kandidatenmengen fast gleich. Abhilfe: Weite und Ease-Kurve als
  zusätzliche Abwechslungsachsen. A2 fängt den Fall messbar ab; E10(c) ist der
  Ausweg, wenn es nicht reicht.
- **Neuanalyse kostet den ganzen Renderlauf.** Modell- oder Prompt-Wechsel
  ändert alle Bewegungen und damit alle Segment-Hashes. Deshalb die Warnung
  aus Abschnitt 8 — und deshalb ist die Prompt-Version ein bewusst gepflegtes
  Feld, kein Nebeneffekt einer Textänderung.
- **Stille Abschaltung der Fokusblende.** Siehe 0.3. Der Fehler produziert
  keinen Fehler, nur einen schlechteren Film. A9 ist dagegen gerichtet.
- **Datenschutz.** Siehe E6. Das ist kein technisches Risiko, sondern eine
  Zusage an die Nutzer, die im Code sichtbar sein muss.

---

## 13. Betroffene Stellen

| Ort | Rolle | Änderung |
|---|---|---|
| `vision.py` (neu) | API-Aufruf, Schema, Parser, Cache | reine Analyse, kein Bewegungswissen |
| `kbplan.py` (neu) | Klemmkette, Kandidaten, Auswahl | reine Rechnung ohne Datei-I/O, wie `select.py` |
| `models.py:286` | `KBSpec` | unverändert — trägt bereits alles |
| `models.py` | `VisionDoc`, `VisionEntry` | neues Schema für `vision.yaml` |
| `build.py:643` | `_couple_focus_motion` | muss **nach** dem Planer laufen; Planer meidet das Folienpaar (6.4) |
| `build.py:837` | `_segment_from_slot` | unverändert — schreibt `kb:` bereits |
| `build.py` | Aufrufstelle des Planers | `vision.yaml` laden, `intent.kb` setzen, Ausfälle zählen |
| `cli.py` | `slideshow analyze` | Schalter, Bestätigung (E6), Kostenbericht |
| `cli.py` `_naechster_schritt` | Ablaufhinweis | `analyze` zwischen `preprocess` und `build` |
| `titles.py:196` | `title_kb` | unverändert — Vorbild und Rangstufe 2 (6.4) |
| `kenburns.py` | — | **nicht anfassen**; der Planer schreibt `KBSpec`, er ändert nicht `plan_motion` |
| `render.py`, `mlt.py` | — | **nicht anfassen** |
| `docs/edit-yaml.md` | `kb:` | Abschnitt „Woher `kb:` kommt" — vier Quellen, eine Rangfolge |
| `docs/rezepte.md` | | Rezept „Die Kamera aufs Motiv richten" |
| `README.md`, `CLAUDE.md` | | Ablauf, Datenschutzhinweis, Baustellenzeile |

Tests: `tests/test_vision.py` (Parser, Plausibilität, Cache-Schlüssel, Ausfall
ohne Netz), `tests/test_kbplan.py` (Klemmkette, A1/A1b als Eigenschaftstests,
Kandidatenwahl, A3b). Fixtures mit erfundenen `vision.yaml`-Einträgen — das
Verfahren braucht keine echten Bilder.

---

## 14. Was die Bildfakten sonst noch tragen

Die zweite Frage dieses Briefings: `vision.yaml` entsteht für die Kamerafahrt —
wo sonst im Werkzeug wird heute ohne Bildkenntnis entschieden, und wo hilft sie?

Seit Rev. 1 ist die Antwort deutlich länger geworden, weil `main` inzwischen
drei Kommandos hat, die es damals nicht gab. Das Auswahl-Briefing formuliert
sein viertes Grundprinzip als **„Keine inhaltliche Analyse. Kein Bild wird zum
*Entscheiden* geöffnet."** — und begründet an mehreren Stellen ausdrücklich,
was deshalb nicht geht (Entscheidung 5, Entscheidung 9, Risiko „Die Auswahl
kann gut aussehen und trotzdem falsch sein"). Sobald `vision.yaml` existiert,
ist dieses Prinzip keine Notwendigkeit mehr, sondern eine Wahl. Die folgende
Liste ist nach Nutzen je Aufwand sortiert; **keiner der Punkte gehört in dieses
Briefing** — sie sind der Grund, es zu bauen.

### 14.1 Titelfolien: welcher Hintergrund, und wo steht der Text · **größter Gewinn**

Heute wählt `bg: auto` **das erste Bild des neuen Abschnitts** (`build.py:242`,
`chapters.py:151`). Das ist eine reine Positionsentscheidung. Danach misst
`titles.py` die Leuchtdichte unter der Textfläche und dunkelt das ganze Bild
nach, bis 4,5:1 Kontrast steht (`_fit_darkening`, `titles.py:668`) — bis zu
`DARKEN_FLOOR`, und wenn es dann nicht reicht, gibt es eine Warnung.

Zwei Fehlerbilder folgen daraus, beide gut sichtbar:

- Der Titel steht über einem Gesicht. Das Werkzeug hat keinen Begriff davon.
- Das erste Bild des Abschnitts ist ein heller Himmel. Die Abdunklung läuft an
  ihre Grenze, und die Folie ist matschig — oder sie trägt den Text gar nicht
  und der Lauf warnt.

`vision.yaml` löst beides mit dem, was ohnehin erhoben wird:

- **Kandidatenwahl statt Positionswahl.** Aus den ersten `n` Bildern des
  Abschnitts das nehmen, das (a) `scene: landscape_wide` oder `architecture`
  ist — eine Totale eröffnet ein Kapitel besser als eine Nahaufnahme —, (b)
  eine `quiet`-Fläche unter der Textbox hat und (c) dort keine `subjects`-Box
  mit `kind: face` liegt.
- **Der zweite Teil ist sogar ohne API zu haben.** `_fit_darkening` gibt
  `kontrast` und `abdunklung` bereits zurück. Über die ersten fünf Bilder eines
  Kapitels zu iterieren und das mit der geringsten nötigen Abdunklung zu
  nehmen, kostet fünf Messungen und keinen einzigen Request. Das ist der
  billigste echte Gewinn im ganzen Abschnitt 14 — und ein Argument dafür, ihn
  unabhängig von diesem Briefing zu bauen.
- **Textlage.** `titles.py` setzt die Textfläche heute fest. Mit `quiet` ließe
  sich zwischen zwei, drei festen Lagen wählen (unten, Mitte, oben), statt das
  Bild zu verdunkeln, bis der feste Platz trägt. Das ist ein größerer Eingriff
  und gehört in ein eigenes Briefing.

`bg: auto` bleibt dabei überschreibbar wie heute (`bg: img_075`) — der
Vorschlag ersetzt nicht die Wahl, er verbessert die Vorgabe.

### 14.2 Auswahl in der Traube · **größter Nutzen, größte Kosten**

`pick_in_burst` (`select.py:417`) entscheidet zwischen zwei bis fünf Versuchen
desselben Motivs — und tut es mit Rating, Position in der Traube, `size_bytes`
und dem Verwacklungsverdacht aus der Freihandregel. Das Briefing nennt
`size_bytes` selbst „kein Schärfemaß, sondern ein Tiebreak", und Entscheidung 9
schließt Schärfe-, Gesichts- und Inhaltserkennung ausdrücklich aus. Genau
deshalb gibt es den Kontaktbogen: **der Mensch entscheidet, was die Heuristik
nicht kann.** An echtem Material waren das rund 160 Tausche in einem Durchgang
(Abweichung 12 im Auswahl-Briefing).

Bildfakten könnten hier ansetzen — geschlossene Augen, unscharfes Motiv,
angeschnittene Person, `conf` als Gesamtsignal. Drei Dinge sprechen dagegen,
es einfach anzuschließen:

1. **Die Reihenfolge stimmt nicht.** `select` läuft vor `preprocess`; die
   Cache-Bilder, auf denen E2 die Analyse festnagelt, gibt es dort noch nicht.
2. **Die Menge stimmt nicht.** Für die Auswahl zählen die *nicht* gewählten
   Bilder mit — 1240 statt 187, also 13,60 USD statt 2,05 auf Opus 5 (9).
3. **Das Nicht-Ziel ist ausdrücklich bestätigt**, nicht nur unterlassen.

Der Ausweg ist ein Zuschnitt, kein Umbau, und er löst alle drei Punkte — ganz
umsonst ist er allerdings nicht zu haben:

- **Nur die strittigen Bilder.** Analysiert werden die Mitglieder von Trauben
  mit ≥ 2 Bildern, also genau die Menge, über die `pick_in_burst` überhaupt
  entscheidet. Auf realem Material ist das ein Bruchteil des Bestands.
- **Auf den Thumbnails — aber normiert.** `sheet.thumbnails` (`sheet.py:259`)
  legt für den Kontaktbogen bereits Vorschauen nach `cache/thumbs/` — aus der
  eingebetteten EXIF-Vorschau, **ohne einen einzigen Volldecode**. Genau das
  Bildmaterial, das eine Auswahlanalyse braucht.

  **Was dabei nicht übersehen werden darf: diese Dateien haben keine
  einheitliche Größe.** Der Bogen übernimmt die Vorschau *so, wie sie ist* —
  genau das ist der Trick, der den Volldecode spart; `--thumb` setzt nur die
  CSS-Kachel und die Zielgröße des ffmpeg-Rückfalls, nicht die Pixelgröße der
  Vorschau (Abweichung 9 im Auswahl-Briefing). In `cache/thumbs/` liegen
  deshalb nebeneinander 1616-px-Vorschauen und 320-px-Rückfälle: **2320 gegen
  77 Bildtokens, Faktor 30** (Abschnitt 9). Eine Vorschau, die die Kamera
  großzügig eingebettet hat, wäre in der Analyse dreimal teurer als das
  geplante 1024×576-Bild — der vermeintlich billige Weg wäre der teuerste.

  Schlimmer als der Preis ist die Folge für das Urteil: die Analysequalität
  hinge daran, wie großzügig der Hersteller war. Bei zwei Kameras in einer
  Traube — dem Fall, den `bursts()` ausdrücklich zulässt — würden ausgerechnet
  die Geschwister ungleich beurteilt, zwischen denen zu entscheiden ist.

  **Also: vor dem Request auf eine feste Kante normieren (Vorschlag 512×288),
  nicht die Vorschau durchreichen.** Das kostet Rechenzeit, die der schnelle
  Pfad gerade eingespart hat — ein Skalierlauf über die strittigen Bilder,
  einmalig und cachebar. Wer 14.2 baut, sollte diesen Posten von Anfang an
  einplanen statt ihn zu entdecken.
- **Und deshalb ohne Koordinaten.** Die Thumbnails haben das Originalformat,
  nicht die Normalform; Boxen daraus gälten nicht im Filterkoordinatensystem
  (E2). Das ist kein Problem, weil die Auswahl **keine Koordinaten braucht** —
  sie braucht Etiketten und Noten: `eyes_open`, `sharp_on_subject`, `conf`.

Daraus fällt eine Strukturaussage ab, die über 14.2 hinausgeht: **`vision.yaml`
hat zwei Faktenmengen mit verschiedenen Anforderungen.** Die koordinatenfreie
darf früh und billig auf Thumbnails laufen, die koordinatengebundene muss spät
und genau auf den Cache-Bildern laufen. Zwei Prompt-Versionen, eine Datei, ein
Feld mehr im Kopf (`stage: labels | geometry`). Wer 14.2 später will, sollte
das Feld **jetzt** vorsehen.

Und die wichtigste Einschränkung bleibt: **der Kontaktbogen ersetzt sich damit
nicht.** Welches der drei Fotos vom Wasserfall *das* Foto der Reise ist, weiß
weder Heuristik noch Modell.

### 14.3 Der Kontaktbogen · **billigster Gewinn, kein Risiko**

`sheet.py` zeigt heute je Kachel Kennung, Uhrzeit, Format und den Grund eines
harten Ausschlusses. Ein Etikett aus `vision.yaml` daneben — `scene`, ein
Warnhinweis bei niedriger `conf`, ein Marker „Augen zu" — macht die Durchsicht
von 1240 Kacheln schneller, und **eine falsche Angabe kostet nichts**: der
Mensch sieht das Bild ja daneben.

Das ist der Punkt, an dem Bildanalyse am besten zur Philosophie des Werkzeugs
passt — Vorschlag statt Entscheidung, sichtbar statt still. Wenn 14.2 gebaut
wird, ist 14.3 dessen Sichtprüfung und sollte im selben Zug entstehen.

### 14.4 Vielfalt der Auswahl

`balance_portrait` (`select.py:482`) korrigiert den Hochformatanteil
nachträglich; Brennweitenvielfalt ist als Stufe 4 zurückgestellt („erst
zeigen, dass es stört"). `scene` macht eine dritte Achse messbar und
verständlich: *„23 von 30 gewählten Bildern sind `landscape_wide`"* — und der
Tausch läuft über genau dasselbe Muster wie beim Hochformat, also über
vorhandenen, erklärbaren Code.

Das ist eine kleine Erweiterung mit gutem Verhältnis, sobald 14.2 die Etiketten
ohnehin erhebt. Alleinstehend lohnt sie den Analyselauf nicht.

### 14.5 Trauben nach Ähnlichkeit statt nach Zeit

Entscheidung 5 des Auswahl-Briefings nennt die Kernannahme selbst angreifbar:
zwei völlig verschiedene Motive innerhalb einer Minute werden zu einer Traube,
und eines fällt heraus. Stufe 4 Punkt 16 sieht dafür bereits „Ähnlichkeit über
eingebettete Vorschaubilder" vor.

`scene` und `subjects[].kind` würden das trennen. **Empfehlung trotzdem: nicht
mit der API.** Ein Perceptual Hash oder ein Histogrammvergleich über die
ohnehin vorhandenen `cache/thumbs/` ist für „ist das dasselbe Motiv?" das
bessere und um Größenordnungen billigere Werkzeug. Das Sprachmodell soll
sagen, *was* auf dem Bild ist, nicht ob zwei Bilder gleich sind.

### 14.6 Standzeit nach Bildinhalt

Ein detailreiches Bild verträgt längere Standzeit als ein leerer Himmel;
`detail` liegt vor, `beats_per_still` ist heute einheitlich. Verlockend — und
teuer: die Slot-Zahl geht in `slot_capacity` ein, das wiederum die Zielzahl
von `select` bestimmt. Eine inhaltsabhängige Standzeit koppelt Analyse,
Planer und Auswahlrechnung aneinander. **Genannt, nicht empfohlen**; falls
überhaupt, dann als Vorschlag im Bericht statt als automatische Änderung.

### 14.7 Blendenmodus und Reihenfolge nach Inhalt

Beides stand schon in Rev. 1 unter „nicht in diesem Umfang" und bleibt dort.
Zur Reihenfolge kommt ein Argument dazu, das gegen sie spricht: Seit `order.py`
und `select.py` existieren, ist die Reihenfolge eine **Datei mit einem
Besitzer**, und der Film ist bewusst eine Chronik. Inhaltliche Umsortierung
würde diese Zusage angreifen. Was hineinpasst, ist kleiner und harmlos: eine
**Warnung**, wenn zwei benachbarte Bilder dieselbe `scene` und ähnliche
`subjects` haben — ein Hinweis im Bericht, keine Umsortierung.

### 14.8 Zusammenfassung

| # | Einsatz | Nutzen | Aufwand | Empfehlung |
|---|---|---|---|---|
| 14.1 | Titelfolien-Hintergrund + Textlage | hoch | klein | **bauen** — der Kontrast-Teil sogar ohne API |
| 14.3 | Etiketten im Kontaktbogen | mittel | sehr klein | **bauen**, zusammen mit 14.2 |
| 14.2 | Wahl in der Traube | hoch | groß | eigenes Briefing; `stage:`-Feld jetzt vorsehen |
| 14.4 | Vielfalt nach `scene` | mittel | klein | mitnehmen, wenn 14.2 kommt |
| 14.6 | Standzeit nach `detail` | gering | groß | nur als Berichtsvorschlag |
| 14.5 | Traubenbildung nach Ähnlichkeit | mittel | mittel | **nicht mit der API** — Perceptual Hash |
| 14.7 | Blende / Reihenfolge nach Inhalt | gering | mittel | nur als Warnung im Bericht |

---

## 15. Nicht in diesem Umfang

Alles aus Abschnitt 14 — jeder Punkt dort ist ein eigenes Briefing, und keiner
darf dieses aufhalten. Dazu: Ken Burns auf Clips, Gesichtserkennung ohne API
(lokales Modell), Stützstellen im Bewegungsmodell (E8), Musikkontext im
Planer (E7).
