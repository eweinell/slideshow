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
| Kapitel über je einige Tage, innen chronologisch mit ein paar Ausreißern | [4. Kapitelweise erzählen](#4-kapitelweise-erzählen) |
| 90 Fotos, aber die Musik trägt nur 50 — auswählen statt alles zeigen | [5. Auswählen statt kürzen](#5-auswählen-statt-kürzen) |
| Ein Sammelbecken aus über tausend Bildern, davon sollen 200 in den Film | [5b. Aus tausend Bildern auswählen lassen](#5b-aus-tausend-bildern-auswählen-lassen) |
| Nachträglich sind Fotos dazugekommen | [6. Nachschub einpflegen](#6-nachschub-einpflegen) |
| Kein Ton, nur Bilder | [7. Stummer Film](#7-stummer-film) |
| Ein einzelnes Bild soll länger stehen oder stillstehen | [8. Feinschliff ohne Neubau](#8-feinschliff-ohne-neubau) |
| Den Schnitt in Kdenlive fertig machen | [9. Weiter in Kdenlive](#9-weiter-in-kdenlive) |

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
| `audio` | Mischt die Tracks zu einer Tonspur. `--gap 6` legt 6 s Stille dazwischen, `--xfade 3` blendet stattdessen über. Optional — ohne Musik siehe [Rezept 7](#7-stummer-film). | wenn sich die Musik ändert |
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
24. Juli"). Hier stimmt das immer; sobald von Hand sortiert wird, nicht mehr —
siehe [Rezept 3](#3-thematisch-erzählen) und
[Rezept 4](#4-kapitelweise-erzählen).

---

## 3. Thematisch erzählen

**Wofür.** Der Film soll nicht die Chronologie nacherzählen, sondern gruppieren:
erst alle Küstenbilder, dann die Abende, dann die Menschen.

```bash
slideshow order                    # → order.yaml, chronologisch vorbelegt
# Zeilen verschieben, Gruppen umbenennen (tag-3 → am-wasser)
slideshow chapters --from-groups   # → chapters.yaml, ein Eintrag je Gruppe
# Überschriften eintragen
slideshow build
slideshow render
```

**Was herauskommt.** Ein Film in deiner Reihenfolge. Die Blenden zwischen den
Bildern erzeugt `build` von selbst — du verschiebst Zeilen, nicht Indizes.

**Erst `order`, dann `chapters`.** Die Abhängigkeit läuft nur in eine Richtung,
und andersherum arbeitest du doppelt:

- `--from-groups` schreibt einen Eintrag je Gruppe und setzt den Anker `group:`.
  Beides setzt voraus, dass es die Gruppen schon gibt.
- Ohne den Schalter kommen die Vorschläge aus Zeitlücken zwischen
  *chronologischen* Nachbarn. In einer thematisch sortierten Folge sitzen sie an
  Stellen, die es dort nicht mehr gibt — du wirfst sie hinterher weg. Ist die
  Reihenfolge bereits von Hand gesetzt, schreibt `chapters` diesen Vorbehalt in
  den Kopf der erzeugten Datei.
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
Sortieren, keine Überschrift — den Text einer Folie schreibt `chapters.yaml`.
Genau das erzeugt `chapters --from-groups`: eine Zeile je Block, Anker gesetzt,
Überschrift leer.

```yaml
chapters:
  - {group: am-wasser, title: "Am Wasser", subtitle: null}
```

Dieser Anker ist der robustere: er überlebt jedes weitere Umsortieren
*innerhalb* des Blocks, ein `before: img_042` nicht.

**Worauf achten.** `subtitle: auto` nimmt das Datum des folgenden Bildes. Über
einem Block aus fünf Reisetagen führt das in die Irre — `--from-groups` setzt
dort deshalb `subtitle: null`, und `build` meldet den Fall auch dann, wenn die
Datei von Hand entstanden ist.

Es muss **jedes** Medium in der Datei stehen, sonst bricht der Bau ab und nennt
die fehlenden. Das ist Absicht: der teuerste Fehler wäre ein Film, dem
stillschweigend drei Bilder fehlen.

---

## 4. Kapitelweise erzählen

**Wofür.** Der Mischfall, und wahrscheinlich der häufigste: Die Erzählung
zerfällt in Abschnitte über je einige Tage — von Hand gesetzt, thematisch, nicht
gefunden. *Innerhalb* eines Abschnitts bleibt es chronologisch, nur ein paar
Bilder wandern nach vorn, damit man gleich erkennt, wo man ist.

```bash
slideshow order --by place         # → order.yaml, ein Block je Ort
# Blöcke zusammenfassen und benennen, einzelne Zeilen nach vorn ziehen
slideshow chapters --from-groups   # → chapters.yaml, ein Eintrag je Block
# Überschriften eintragen
slideshow build
slideshow render --preview
```

**Was herauskommt.** Ein Film, dessen Kapitelgrenzen genau dort sitzen, wo du
beim Sortieren die Blockgrenzen gezogen hast. Innerhalb der Kapitel läuft die
Uhr weiter, mit deinen Ausreißern.

**`--by place` statt `--by day`.** Ein mehrtägiger Aufenthalt wird damit *ein*
Block statt drei — die Vorbelegung ist dann schon fast die Gliederung. Ohne GPS
bleibt `--by day`, und Tagesblöcke fasst man zusammen, indem man die beiden
Kopfzeilen der Folgetage löscht:

```text
  - name: schaeren       ← war tag-4, umbenannt
    items:
      - img_DSC06401   # Tag 4 · 27. Juli 09:12 · quer
                       ← hier standen `- name: tag-5` und `items:`
      - img_DSC06455   # Tag 5 · 28. Juli 08:40 · quer
```

**Ein Bild nach vorn ziehen** heißt: Zeile hochschieben. Zwei Dinge greifen dann
ineinander — `group:` zeigt immer auf das *erste* Medium des Blocks, und `bg:
auto` der Folie nimmt genau dieses Bild als unscharfen Grund. Das Bild, das du
der Erkennbarkeit wegen vorziehst, wird damit zum Hintergrund seiner eigenen
Kapitelfolie, und die Blende danach löst es scharf auf.

**Worauf achten.** Was `--from-groups` schreibt, ist eine Vorlage mit drei
bereits getroffenen Entscheidungen:

| | |
|---|---|
| Der erste Block | steht **auskommentiert** — er säße an derselben Stelle wie der Auftakt, und zwei Titelfolien hintereinander fallen erst im fertigen Film auf. Entweder den Auftakt löschen oder diese Zeile. |
| Blöcke über mehrere Tage | bekommen `subtitle: null`. `auto` nähme davon nur den ersten Tag. |
| Blöcke aus einem Tag | bekommen `subtitle: auto` — dort stimmt es. |

Der Schalter verträgt sich nicht mit `--min-gap`/`--min-jump`: die stellen die
Zeitlücken-Erkennung ein, die hier gar nicht läuft. Der Aufruf bricht deshalb
ab, statt sie stillschweigend zu ignorieren.

`chapters.yaml` hat kein `--update`. Kommt später ein Block dazu, trägst du die
eine Zeile von Hand nach — oder du löschst die Datei und lässt sie neu
erzeugen, solange die Überschriften noch nicht drinstehen.

Für `order.yaml` gilt unverändert, was bei [Rezept 3](#3-thematisch-erzählen)
steht: es muss **jedes** Medium darin stehen, sonst bricht der Bau ab. Wer
auswählen will, setzt `rest: drop` — [Rezept 5](#5-auswählen-statt-kürzen).

**Umsortieren ist billig.** Die Kamerafahrt hängt an der Kennung des Bildes,
nicht an seiner Position: ein vorgezogenes Bild behält seine Bewegung, neu
gerechnet werden nur die angrenzenden Blenden.

---

## 5. Auswählen statt kürzen

**Wofür.** Es ist mehr Material da, als die Musik trägt. Ein Stück von 6:32
fasst bei acht Beats je Bild rund 50 Fotos — bei 90 muss ausgewählt werden.

Bei **90 Fotos** geht das von Hand. Bei tausend nicht mehr — dafür gibt es
[Rezept 5b](#5b-aus-tausend-bildern-auswählen-lassen).

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

## 5b. Aus tausend Bildern auswählen lassen

**Wofür.** Ein Sammelbecken statt eines vorsortierten Ordners: 1200 Aufnahmen
aus zwei Wochen, davon sollen 200 in den Film. Von Hand auszukommentieren wäre
ein Nachmittag, und `preprocess` würde vorher alle 1200 auf 4K normalisieren —
Stunden Rechenzeit für Material, das nie zu sehen ist.

**Die Reihenfolge kehrt sich um:** erst auswählen, dann normalisieren. Und weil
die Zielzahl aus der Regionenkarte kommt, muss `beats` davor.

```bash
slideshow probe /material/island     # 1240 Bilder
slideshow audio track.mp3
slideshow beats                      # → beats.yaml, 187 Slots
slideshow select                     # → order.yaml: 187 gewählt, Rest als Kommentar
slideshow preprocess                 # nur die Auswahl, nicht alle 1240
slideshow build && slideshow render
```

**Was herauskommt.** Eine `order.yaml` mit `rest: drop`, in der die gewählten
Bilder stehen und alle übrigen als Kommentar an ihrem zeitlichen Platz — nach
Tagen gegliedert, also sofort als Kapitelanker brauchbar
(`slideshow chapters --from-groups`).

**Wonach ausgewählt wird.** Nach Zeitstruktur, ohne einen Bildpunkt anzusehen:

| | |
|---|---|
| **Trauben** | Aufnahmen unter 90 s Abstand zeigen fast immer dasselbe. Aus jeder solchen Traube kommt **höchstens ein** Bild in den Film. |
| **Quote je Tag** | Gedämpft: vierfaches Material ergibt doppelt so viele Bilder, nicht viermal so viele. Gerechnet auf der Zahl der *Trauben* — wer 200 Serienbilder von zwei Motiven macht, hat zwei Motive. |
| **Spreizung** | Innerhalb des Tages gleichmäßig über die Zeit verteilt, damit nicht alle acht Bilder vom Abendessen kommen. |
| **Wahl in der Traube** | Sterne schlagen alles; sonst zählen Position in der Serie, Dateigröße als Schärfeindiz und die Freihandregel. |

Ausgeschlossen wird nur, was technisch nicht geht: eine Langkante unter 2160 px
(der Master ist 4K, und Ken Burns zoomt hinein). Der Grund steht in der Datei.

**Ein anderes Bild nehmen** heißt Zeilentausch — die Geschwister derselben
Traube stehen direkt darunter:

```yaml
      - img_DSC06273   # 10:14 · quer · Traube mit 4
      #  statt: img_DSC06271 (10:13) · img_DSC06272 (10:14) · img_DSC06274 (10:15)
```

**Worauf achten.**

- **Die Auswahl ist ein Vorschlag.** Sie kennt keine Bildinhalte. Ein technisch
  tadelloses Ergebnis kann ausgerechnet die drei Bilder auslassen, um die es in
  dem Film geht — durchsehen lohnt.
- **Ein zweiter Vorschlag** kostet nichts: `slideshow select --force` würfelt
  neu, `--seed 4711` holt einen bestimmten zurück. Der Seed steht im Dateikopf.
- **`--dry-run` zeigt, ohne zu schreiben** — mitsamt der Tabelle, welcher Tag
  wie viel stellt. Der schnellste Weg, `--day-weight` einzustellen.
- **Nicht zweimal `select`.** Der zweite Lauf wirft Auswahl *und* Sortierung
  weg und verlangt deshalb `--force`. Um nur nachgereichtes Material
  einzupflegen: `slideshow order --update` — das behält beides.
- **`preprocess` folgt der Auswahl von selbst**, sobald eine `order.yaml` im
  Projekt liegt. Wer später eine Zeile tauscht, lässt es einfach noch einmal
  laufen: das neue Bild wird nachgeholt, die fertigen bleiben liegen.
  `preprocess --all` normalisiert trotzdem alles — sinnvoll, wenn die Auswahl
  noch mehrfach umgeworfen werden soll und der Cache warm sein darf.

```bash
slideshow select --dry-run                 # ansehen, nichts schreiben
slideshow select --count 150               # Zielzahl selbst setzen
slideshow select --day-weight 0            # von jedem Tag gleich viele
slideshow select --burst-gap 30            # engere Trauben, mehr Auswahl
slideshow select --rating-min 1            # nur, was in Lightroom Sterne hat
```

Wo Sterne vergeben wurden, ist `--rating-min` das mit Abstand beste Signal in
diesem ganzen Verfahren — es ist das einzige, das den Bildinhalt kennt.

---

## 6. Nachschub einpflegen

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

## 7. Stummer Film

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

## 8. Feinschliff ohne Neubau

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

## 9. Weiter in Kdenlive

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
