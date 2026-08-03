# Rezepte

Fertige Abläufe für die Fälle, die tatsächlich vorkommen. Jedes Rezept sagt,
**wofür** es gut ist, welche Befehle in welcher Reihenfolge laufen und **was
dabei herauskommt** — die Bedeutung der einzelnen Schlüssel steht in
[`edit-yaml.md`](edit-yaml.md), hier geht es um den Weg.

## Welches Rezept?

| Was du willst | Rezept |
|---|---|
| Einmal sehen, ob das Material trägt — schnell, ohne Feinschliff | [1. Rohschnitt](#1-rohschnitt) |
| Die Reise von vorn nach hinten, mit Kapiteln wie „Malmö" | [2. Die Reise chronologisch](#2-die-reise-chronologisch) |
| Erst alle Küstenbilder, dann die Abende — thematisch statt nach Uhrzeit | [3. Thematisch erzählen](#3-thematisch-erzählen) |
| 90 Fotos, aber die Musik trägt nur 50 — auswählen statt alles zeigen | [4. Auswählen statt kürzen](#4-auswählen-statt-kürzen) |
| Nachträglich sind Fotos dazugekommen | [5. Nachschub einpflegen](#5-nachschub-einpflegen) |
| Kein Ton, nur Bilder | [6. Stummer Film](#6-stummer-film) |
| Ein einzelnes Bild soll länger stehen oder stillstehen | [7. Feinschliff ohne Neubau](#7-feinschliff-ohne-neubau) |
| Den Schnitt in Kdenlive fertig machen | [8. Weiter in Kdenlive](#8-weiter-in-kdenlive) |

**Erst Reihenfolge, dann Kapitel** — wenn du beides brauchst. Der Grund steht
bei [Rezept 3](#3-thematisch-erzählen).

---

## Die Schritte, die immer davorstehen

Vier Befehle, die jedes Rezept voraussetzt. Sie kosten einmal Zeit und danach
nichts mehr — alles Weitere arbeitet auf ihren Ergebnissen.

```bash
slideshow doctor                                # läuft ffmpeg? welche Encoder?
slideshow probe /material/urlaub                # was liegt vor?
slideshow audio track1.mp3 track2.mp3 --gap 6   # Musik normalisieren und mischen
slideshow preprocess                            # Bilder und Clips aufbereiten
slideshow beats                                 # wo ist der Takt?
```

| Schritt | Wofür | Wann wiederholen |
|---|---|---|
| `doctor` | Prüft die Umgebung und gibt zu jedem Fehlschlag einen kopierbaren Installationsbefehl aus. | einmal, und wenn etwas kaputtgeht |
| `probe` | Liest Aufnahmezeit, Kamera, GPS, Auflösung und vergibt jedem Foto seine **Medien-ID** — den Namen, unter dem es überall sonst auftaucht. | wenn Material dazukommt |
| `audio` | Mischt die Tracks zu einer Tonspur. `--gap 6` legt 6 s Stille dazwischen, `--xfade 3` blendet stattdessen über. Optional — ohne Musik siehe [Rezept 6](#6-stummer-film). | wenn sich die Musik ändert |
| `preprocess` | Normalisiert jedes Foto und schneidet Clips zu. Der langsame Schritt. | wenn Material dazukommt |
| `beats` | Findet den Takt und schreibt die Regionenkarte. **Diese Datei vorher ansehen** — sie entscheidet, wo geschnitten wird. | wenn sich die Musik ändert |

Die Reihenfolge muss man sich nicht merken: jeder Schritt schließt mit dem
nächsten sinnvollen Aufruf ab, fertig zum Kopieren.

> **Kennt das Werkzeug das Tempo schon**, spart `slideshow beats --bpm 152
> --offset 0.35` die Analyse und legt eine einzige Beat-Region über den ganzen
> Track.

> **Gehen zwei Kameras verschieden**, korrigiert das `probe`:
> `slideshow probe /material --clock-offset "ILCE-7M4=+01:00:00"`. Das ist der
> richtige Weg für einen *systematischen* Versatz — dafür braucht es keine
> Sortierung von Hand.

---

## 1. Rohschnitt

**Wofür.** Der erste Blick. Trägt das Material die Musik? Sitzen die Schnitte?
Ohne Titel, ohne Sortierung, in 720p statt 4K.

```bash
slideshow build
slideshow render --preview
```

**Was herauskommt.** Ein `out/master.mp4` in 1280 × 720, in Minuten statt
Stunden gerendert. Derselbe Schnitt, dieselbe Tonspur, dieselben Blenden wie im
großen Lauf — nur kleiner und schneller.

**Worauf achten.** `build` gibt vorher eine Laufzeit-Vorabprüfung aus: wie viele
Bilder in welche Region passen und ob Material und Musik zusammengehen. Passt es
nicht, nennt es die nötige Standzeit selbst.

Nur einen Ausschnitt ansehen: `slideshow render --preview --range 40:60` — das
sind **Segmentnummern**, nicht Sekunden. Welches Segment wo liegt, steht in
`out/timeline.json`.

---

## 2. Die Reise chronologisch

**Wofür.** Der Normalfall. Der Film läuft von vorn nach hinten, und Titelfolien
setzen die Zäsuren: *hier endet Kopenhagen, hier beginnt Malmö.*

```bash
slideshow chapters           # → chapters.yaml, Überschriften noch leer
# Ortsnamen eintragen
slideshow build
slideshow render
```

**Was herauskommt.** Ein Film in Aufnahmereihenfolge mit ganzseitigen
Titelfolien an den erkannten Abschnittsgrenzen. Jede Folie hat den unscharfen,
abgedunkelten Hintergrund des ersten Bildes ihres Abschnitts und löst sich in
genau dieses Bild scharf auf.

**Worauf achten.** `chapters` findet die Grenzen, **nicht** die Namen — einen
Ortsnamen kann das Werkzeug nicht erfinden, und eine leere Überschrift bricht
den Bau ab. Schwächere Kandidaten stehen auskommentiert darunter, ein Handgriff
macht daraus einen Eintrag.

Die Erkennung nutzt zwei Signale: einen **Ortssprung** über 30 km (das
treffsicherste, aber nur wo GPS vorliegt) und eine **Zeitlücke** über 20 Stunden
(immer da, aber grob). Wo Koordinaten vorliegen, gewinnen sie auch gegen die
Uhr — sonst bekäme eine Nacht im selben Hotel ein eigenes Kapitel. Zum
Nachjustieren:

```bash
slideshow chapters --min-jump 20 --min-gap 12    # empfindlicher
slideshow chapters --no-auftakt                  # ohne Titel vor dem Film
```

`chapters.yaml` wird **nicht** überschrieben, sobald sie einmal ausgefüllt ist —
sie enthält Handarbeit. `slideshow --dry-run chapters` zeigt den Vorschlag nur
an.

**`subtitle: auto`** setzt die zweite Zeile aus dem Aufnahmedatum („Tag 11 ·
24. Juli"). Hier stimmt das immer; bei [Rezept 3](#3-thematisch-erzählen) nicht
mehr.

---

## 3. Thematisch erzählen

**Wofür.** Der Film soll nicht die Chronologie nacherzählen, sondern gruppieren:
erst alle Küstenbilder, dann die Abende, dann die Menschen.

```bash
slideshow order              # → order.yaml, chronologisch vorbelegt
# Zeilen verschieben, Gruppen umbenennen (tag-3 → am-wasser)
slideshow chapters           # → chapters.yaml
# Überschriften eintragen, Anker auf `group:` umstellen
slideshow build
slideshow render
```

**Was herauskommt.** Ein Film in deiner Reihenfolge. Die Blenden zwischen den
Bildern erzeugt `build` von selbst — du verschiebst Zeilen, nicht Indizes.

**Erst `order`, dann `chapters`.** Die Abhängigkeit läuft nur in eine Richtung,
und andersherum arbeitest du doppelt:

- `group:` als Kapitelanker setzt voraus, dass es die Gruppen schon gibt.
- Die Kapitelvorschläge sind aus Zeitlücken zwischen *chronologischen* Nachbarn
  gerechnet. In einer thematisch sortierten Folge sitzen sie an Stellen, die es
  dort nicht mehr gibt — du wirfst sie hinterher weg. Ist die Reihenfolge
  bereits von Hand gesetzt, schreibt `chapters` diesen Vorbehalt in den Kopf der
  erzeugten Datei.
- Ein `before:`-Anker auf ein Bild, das du beim Sortieren abwählst, bricht den
  Bau ab.

**So sieht die Datei aus.** Erzeugt wird sie nach Kalendertagen vorgruppiert,
jede Zeile mit dem Kontext zum Sortieren:

```yaml
groups:
  - name: tag-1
    items:
      - img_DSC06273   # Tag 1 · 24. Juli 10:14 · quer
      - img_DSC06280   # Tag 1 · 24. Juli 10:22 · hoch
```

`--by place` gruppiert stattdessen nach Ortsclustern aus GPS, `--by none` legt
alles in einen Block.

**Gruppennamen erscheinen nicht im Film.** Sie sind die Arbeitseinheit beim
Sortieren, keine Überschrift. Wer an einer Blockgrenze eine Zäsur will, schreibt
sie in `chapters.yaml`:

```yaml
chapters:
  - {group: am-wasser, title: "Am Wasser", subtitle: null}
```

Dieser Anker ist der robustere: er überlebt jedes weitere Umsortieren
*innerhalb* des Blocks, ein `before: img_042` nicht.

**Worauf achten.** `subtitle: auto` nimmt das Datum des folgenden Bildes. Über
einem Block aus fünf Reisetagen führt das in die Irre — `build` meldet genau
diesen Fall je Kapitel. Setz die zweite Zeile von Hand oder lass sie mit
`subtitle: null` weg.

Es muss **jedes** Medium in der Datei stehen, sonst bricht der Bau ab und nennt
die fehlenden. Das ist Absicht: der teuerste Fehler wäre ein Film, dem
stillschweigend drei Bilder fehlen.

---

## 4. Auswählen statt kürzen

**Wofür.** Es ist mehr Material da, als die Musik trägt. Ein Stück von 6:32
fasst bei acht Beats je Bild rund 50 Fotos — bei 90 muss ausgewählt werden.

```bash
slideshow order
# in order.yaml: `rest: error` → `rest: drop`
# Verworfene Zeilen auskommentieren, Grund dahinterschreiben
slideshow build
```

**Was herauskommt.** Ein Film aus genau den Bildern, die stehen geblieben sind.
Die verworfenen bleiben als auskommentierte Zeilen in der Datei — mit dem
Grund daneben, und jederzeit durch Entfernen des `#` wieder dabei.

```yaml
rest: drop

groups:
  - name: am-wasser
    items:
      - img_DSC06401   # Tag 3 · 26. Juli 18:02 · quer
    # - img_DSC06402   # zu dunkel
```

**Worauf achten.** `rest:` sagt, was mit nicht genanntem Material geschieht:

| | |
|---|---|
| `error` (Vorgabe) | Abbruch mit Nennung der fehlenden IDs |
| `drop` | weglassen — die Auswahl |
| `append` | hinten chronologisch anhängen — der Zwischenstand beim Sortieren |

Beide letzteren melden sich bei **jedem** Bau im Bericht, und `--force` blendet
das nicht aus. Ein vergessenes `append` hängt sonst unsortiertes Material ans
Filmende.

Die Gegenrichtung — zu **wenig** Material — löst keine Auswahl, sondern eine
längere Standzeit: `slideshow build --still-seconds 28`. Den passenden Wert
nennt `build` selbst.

---

## 5. Nachschub einpflegen

**Wofür.** Die Karte der Zweitkamera ist aufgetaucht. Zwölf Fotos mehr — und
die Sortierung von gestern soll bleiben.

```bash
slideshow probe /material/urlaub /material/nachschub
slideshow preprocess
slideshow order --update
slideshow build
```

**Was herauskommt.** `order.yaml` mit unveränderter Reihenfolge, unveränderten
Gruppennamen und unveränderten Kommentaren — und einer neuen Gruppe `neu` am
Ende, in die das neue Material einsortiert werden will.

**Worauf achten.** `--update` ist der Weg zurück, wenn `build` nach einem
erneuten `probe` fehlendes Material meldet. **Nicht** `--force` nehmen: das
schreibt die Datei chronologisch neu und wirft die Sortierung weg.

Drei Dinge macht `--update` still richtig:

- Was du bewusst abgewählt hast (auskommentierte Zeilen), wird **nicht** erneut
  als „neu" angeboten.
- Einträge, deren Datei es nicht mehr gibt, werden an Ort und Stelle
  auskommentiert statt gelöscht — die Zeile steht dort, wo das Bild einsortiert
  war.
- Ein zweiter Lauf ändert nichts mehr.

`chapters.yaml` hat kein `--update`; neue Kapitel trägst du von Hand nach.

---

## 6. Stummer Film

**Wofür.** Kein passender Track, oder der Ton kommt später dazu.

```bash
slideshow probe /material/urlaub
slideshow preprocess
slideshow beats --still-seconds 5
slideshow build --still-seconds 5      # ← noch einmal, siehe unten
slideshow render
```

**Was herauskommt.** Ein Master **ohne** Tonspur, jedes Bild steht 5 Sekunden.
Die Laufzeit ergibt sich aus dem Material statt aus der Musik — bei 50 Fotos
also 250 Sekunden.

**Worauf achten.** Die Standzeit muss an **beide** Befehle. `beats` bemisst
damit nur die Karte; `build` hat seine eigene Vorgabe von 4 s und teilt die
Karte sonst neu ein. Vergisst man es, stehen die Bilder 4,2 s statt 5 s — kein
Fehler, aber auch nicht das, was dasteht.

Der `audio`-Schritt entfällt ersatzlos; `beats` erzeugt dann eine Karte aus
*Anzahl Medien × Standzeit*. Titelfolien, Sortierung und Kapitel funktionieren
unverändert.

---

## 7. Feinschliff ohne Neubau

**Wofür.** Der Schnitt steht, aber ein Bild soll länger stehen bleiben, ein
anderes stillstehen.

```bash
cp edit.yaml meine-fassung.yaml
# von Hand ändern
slideshow render meine-fassung.yaml
```

**Was herauskommt.** Ein Master mit genau diesen Änderungen — und im
Renderbericht die Zahl der wirklich neu berechneten Segmente. Ein geändertes
Bild kostet drei davon, nicht den halben Film.

```yaml
- {type: still, src: cache/img_DSC06300.jpg, dur: 8}          # länger stehen
- type: still                                                  # stillstehen
  src: cache/img_DSC06301.jpg
  kb: {z: [1.0, 1.0], c: [0.5, 0.5, 0.5, 0.5]}
```

**Worauf achten.** `slideshow build` **überschreibt `edit.yaml`**. Wer von Hand
ändert, arbeitet danach mit `render` weiter oder legt wie oben eine Kopie an.

Alles, was sich systematisch ergibt — Reihenfolge, Kapitel, Takt —, gehört
**nicht** hierher, sondern in `order.yaml`, `chapters.yaml` oder die Parameter
von `build`. Sonst ist es beim nächsten Bau weg. Die vollständige
Schlüsselreferenz steht in [`edit-yaml.md`](edit-yaml.md).

---

## 8. Weiter in Kdenlive

**Wofür.** Die Grobstruktur steht, der Rest soll im Schnittprogramm passieren —
oder die Zeiten sollen dort korrigiert und wieder zurückgeholt werden.

```bash
slideshow export-mlt                              # → out/project.kdenlive
# in Kdenlive schneiden
slideshow export-mlt --reimport out/project.kdenlive
slideshow render
```

**Was herauskommt.** Ein Kdenlive-Projekt mit allen Bildern, Clips, Titelfolien
und Blenden an ihren Positionen. Der Rückweg führt korrigierte Zeiten in die
Edit-List zurück, sodass der eigentliche Master weiterhin aus derselben Quelle
entsteht.

**Worauf achten.** Braucht `melt`, das meist mit Kdenlive oder Shotcut
mitkommt. `doctor` sagt, ob es gefunden wurde; liegt es woanders, setzt
`SLIDESHOW_MELT` es fest.

---

## Der große Lauf

Am Ende jedes Rezepts steht derselbe Befehl:

```bash
slideshow render -o out/master.mp4
```

**Ein voller 4K-Lauf dauert 45–90 Minuten.** Was nur das Verhalten prüft — Takt,
Reihenfolge, Blenden —, prüfst du besser mit `--preview`. `--jobs` begrenzt die
parallelen Encodes, wenn wenig Arbeitsspeicher frei ist.

Ein zweiter Lauf rendert nur, was sich geändert hat: jedes Segment ist einzeln
gecacht. Umsortieren kostet deshalb fast nichts — die Bilder behalten ihre
Kamerafahrt, nur die angrenzenden Blenden werden neu berechnet.
