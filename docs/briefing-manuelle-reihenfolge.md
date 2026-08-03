# Briefing: Manuelle Reihenfolge

**Status:** Entwurf, nicht umgesetzt · **Betrifft:** neues Modul
`src/slideshow/order.py`, `models.py`, `build.py`, `chapters.py`, `cli.py`,
`docs/edit-yaml.md` · **Vorbedingung:** keine

Die Abfolge des Films kommt heute aus `chronological(manifest)` — Fotos in der
Reihenfolge ihrer Aufnahmezeit. Für eine Reisedokumentation ist das richtig.
Für einen Film, der **thematisch** erzählt — erst alle Küstenbilder, dann die
Abende, dann die Menschen — ist es genau falsch, und es gibt keinen Weg daran
vorbei. `docs/edit-yaml.md` benennt die Lücke im Klartext:

> Für eine freie Reihenfolge, die sich nicht aus Zeitstempeln ergibt, führt
> derzeit kein Weg daran vorbei \[an der Handarbeit in `edit.yaml`]. Bei vielen
> Segmenten ist das mühsam; ein Kommando, das eine bestehende Reihenfolge über
> einen Neubau rettet, gibt es noch nicht.

„Mühsam" ist untertrieben. Wer 90 Bilder in `edit.yaml` umsortiert, muss die
`from`/`to` von 89 Übergängen mitziehen — Indizes in ein Array, das die Blenden
selbst mitzählt. Und der nächste `slideshow build` wirft alles weg.

Dieses Briefing beschreibt eine **eigene Eingabedatei `order.yaml`**, die die
Reihenfolge festhält und einen Neubau überlebt — dasselbe Muster, das
`chapters.yaml` für die Kapitel bereits erfolgreich anwendet.

Der Anspruch:

1. **Sortiert wird an Medien-IDs**, nicht an Positionen oder Zeiten — sonst
   verrutscht die Handarbeit beim nächsten `probe`.
2. **Kein Bild verschwindet still.** Wer 90 Bilder sortiert und 87 auflistet,
   bekommt eine Meldung, keinen um drei Bilder kürzeren Film.
3. **Sortieren heißt Zeilen verschieben**, nicht IDs abtippen. Die Datei wird
   erzeugt, vorbelegt und mit dem Kontext kommentiert, den man zum Sortieren
   braucht.
4. **Die Zusagen des Werkzeugs bleiben.** Einzelsegment-Caching,
   deterministische Timeline, Rundlauf durch `edit.yaml`.

---

## 1. Was bereits da ist

Der Eingriff ist klein, weil die Reihenfolge im Code schon heute an genau
**einer** Stelle festgelegt wird und alles Nachfolgende sie nur noch entgegennimmt.

| Vorhanden | Ort | Was es hier beiträgt |
|---|---|---|
| **Der `order`-Parameter selbst** | `build.py:52` `build_edit_list(order=…)` | existiert bereits — nur ohne Aufrufer, ohne Dateiformat und mit falschem Verhalten (siehe unten) |
| Genau eine Stelle für die Abfolge | `build.py:62` `media = chronological(manifest)` | danach ist die Reihenfolge nur noch eine Liste; Planer, Renderer und MLT-Export bleiben unberührt |
| Medien-IDs als stabiler Anker | `probe.py` `_make_id`, `docs/edit-yaml.md` „Medien-IDs" | hängen nur am Dateinamen — gegen Umsortieren und nachgereichtes Material immun. Genau die Eigenschaft, die eine Sortierdatei braucht |
| Kapitel als eigene Eingabedatei | `models.py:647` `ChapterList`, `cli.py:572` `_load_chapters` | das komplette Muster: laden, an IDs auflösen, Neubau überleben, ausdrücklich genannter Pfad ist Pflicht, gefundene Datei ist Bequemlichkeit |
| YAML mit Zeilennummern | `models.py:681` `_LineLoader`, `_to_schema_error` | ein Tippfehler in `order.yaml` meldet Datei und Zeile, ohne dass dafür etwas Neues entsteht |
| Das Formular-Muster | `chapters.py:188` `dump_chapters_yaml` | von Hand geschrieben statt `yaml.dump`, weil die Kommentare die halbe Miete sind. Hier gilt dasselbe |
| Übergänge erzeugt `build` | `build.py:702` `_segments_from_plan` | die `from`/`to`-Buchhaltung entfällt komplett — der eigentliche Gewinn gegenüber Handarbeit in `edit.yaml` |
| Ken-Burns-Richtung hängt an `src` | `kenburns.py:91` `plan_motion` | Umsortieren kostet beim Rendern fast nichts: die Bilder behalten ihre Bewegung, nur die angrenzenden Blenden rechnen neu (Entscheidung 7 des Titelfolien-Briefings) |
| Signale für Tag- und Ortsgrenzen | `chapters.py:92` `suggest`, `chapters.py:48` `distance_km` | liefern die Vorbelegung des Generators, ohne dass etwas Neues gemessen werden muss |
| Deckungsrechnung | `planner.py:571` `coverage`, `planner.py:686` `coverage_advice` | rechnet über `len(intents)`; eine kürzere Auswahl meldet sich von selbst als solche |

**Der vorhandene Parameter ist nicht brauchbar, wie er ist.** Zwei Zeilen:

```python
by_id = {m.id: m for m in manifest.media}
media = [by_id[i] for i in order if i in by_id]
```

Eine unbekannte ID wird stillschweigend übersprungen, und jedes Medium, das in
`order` fehlt, fällt aus dem Film — beides ohne ein Wort. Das ist genau der
stille Ignorierfall, den Prinzip 4 („fail loud, fail early") ausschließt.
Entscheidung 3 macht daraus explizites Verhalten.

---

## 2. Vorgeschlagene Syntax

`order.yaml` liegt neben `chapters.yaml` im Projektroot und wird von `build`
gefunden, ohne dass man sie nennen muss.

```yaml
# order.yaml — Reihenfolge der Medien. Wird von `slideshow build` eingelesen.
version: 1
rest: error                  # error (Vorgabe) | append | drop

groups:
  - name: ankunft
    items:
      - img_DSC06273         # Tag 1 · 24. Juli 10:14 · quer
      - img_DSC06280         # Tag 1 · 24. Juli 10:22 · hoch
      - clip_MVI_1234        # Tag 1 · 24. Juli 10:31 · Clip 8,2 s

  - name: am-wasser
    items:
      - img_DSC06401         # Tag 3 · 26. Juli 18:02 · quer
      - img_DSC06288         # Tag 1 · 24. Juli 11:40 · quer
```

Die flache Kurzform ohne Gruppen ist erlaubt und bedeutet dasselbe wie **eine**
namenlose Gruppe:

```yaml
order: [img_DSC06273, img_DSC06280, img_DSC06401]
```

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `version` | int | `1` | Schemaversion. Andere Werte werden abgelehnt, nicht geraten |
| `rest` | enum | `error` | Was mit Material geschieht, das die Datei nicht nennt — siehe Entscheidung 3 |
| `groups` | Liste | – | Blöcke. Genau eines von `groups:` und `order:` |
| `groups[].name` | string | – | Bezeichner des Blocks. **Erscheint nicht im Film** — siehe Entscheidung 2. Optional, aber Anker für `chapters.yaml` (Entscheidung 5) |
| `groups[].items` | Liste[string] | – | Medien-IDs in der gewünschten Reihenfolge. Darf leer sein |
| `order` | Liste[string] | – | Flache Kurzform. Genau eines von `groups:` und `order:` |

Aufgerufen wird das mit `slideshow build --order order.yaml`; liegt die Datei
unter diesem Namen im Projektverzeichnis, findet `build` sie von selbst. Ohne
Datei bleibt alles wie heute: chronologisch.

Erzeugt wird sie von einem neuen Unterkommando:

```
slideshow order                 # -> order.yaml, chronologisch vorbelegt, nach Tagen gruppiert
slideshow order --by day        # Gruppierung: day (Vorgabe) | place | none
slideshow order --update        # neues Material einpflegen, Handarbeit behalten
slideshow order --dry-run       # nur anzeigen
slideshow order --force         # bestehende Datei überschreiben
```

---

## 3. Entscheidungen

### Entscheidung 1 — Eigene Datei, nicht `edit.yaml` und nicht die Kommandozeile

Drei Wege stünden offen.

**(a) Handarbeit in `edit.yaml`** — der heutige Zustand. Scheidet aus: `build`
überschreibt die Datei, die `from`/`to` von 89 Blenden müssen mitgezogen
werden, und die Reihenfolge steht dann in einem Erzeugnis statt in einer
Eingabe.

**(b) Ein CLI-Argument** — `slideshow build --order img_a,img_b,…`. Scheidet
aus, und zwar an Architektur-Invariante 1: „Neue Einstellungen gehören nach
`Defaults`, nicht in CLI-Argumente, die nur zur Laufzeit existieren." Eine
Reihenfolge von 90 IDs ist keine Einstellung, sondern ein Dokument. Sie muss
versionierbar sein, kommentierbar, und man muss sie in mehreren Sitzungen
bearbeiten können.

**(c) Eine eigene Eingabedatei** — gewählt. Die Begründung steht bereits
geschrieben, für den Zwilling dieses Problems:

> `build` erzeugt `edit.yaml` **neu**. Zwölf Städte von Hand einzupflegen ist
> zumutbar, zwölf Städte nach jedem `build`-Lauf erneut einzupflegen nicht.

Für 90 sortierte Bilder gilt das mit Nachdruck. `order.yaml` verhält sich in
jeder Hinsicht wie `chapters.yaml`: Eingabe, nicht Erzeugnis; an Medien-IDs
verankert; von einem Generator vorbelegt; `build` schreibt sie nie.

**Nicht** gewählt: die Reihenfolge in `chapters.yaml` unterzubringen. Die eine
Datei sagt, *in welcher Reihenfolge* das Material läuft, die andere, *wo eine
Zäsur* liegt. Das sind zwei Fragen, und sie werden von verschiedenen Leuten zu
verschiedenen Zeitpunkten beantwortet.

### Entscheidung 2 — Gruppen, und was eine Gruppe nicht ist

Eine flache Liste aus 90 IDs ist schreibbar, aber nicht lesbar: man sieht ihr
nicht an, *warum* ein Bild dort steht. Thematisch sortieren heißt in der Praxis
**Blöcke bilden und Bilder zwischen ihnen verschieben** — die Gruppe ist nicht
Zierrat, sondern die Arbeitseinheit.

Gewählt: `groups:` mit `name:` und `items:`. Die flache Form `order:` bleibt
zulässig und normalisiert intern auf eine einzige namenlose Gruppe; das kostet
zehn Zeilen und erspart dem, der nur schnell drei Bilder tauschen will, eine
Verschachtelung, die er nicht braucht.

**Eine Gruppe ist keine Titelfolie.** Der Name erscheint nirgends im Film. Das
ist die wichtigere Hälfte der Entscheidung, und sie ist unbequem: es liegt
nahe, aus `name: am-wasser` automatisch eine Folie „Am Wasser" zu machen. Genau
das darf nicht passieren, denn dann gäbe es **zwei Wege**, eine Titelfolie zu
erklären — `chapters.yaml` und `order.yaml` —, die auseinanderlaufen können und
deren Vorrang man sich merken müsste. Der Text einer Folie wohnt in
`chapters.yaml`, dort und nur dort. Entscheidung 5 verbindet die beiden Dateien
statt sie zu vermischen.

Der Name ist deshalb bewusst technisch gehalten (kleingeschrieben, ohne
Leerzeichen, wie ein Bezeichner) — er sieht dann nicht aus wie eine Überschrift
und lädt nicht dazu ein, für eine gehalten zu werden.

### Entscheidung 3 — Was mit Material geschieht, das die Datei nicht nennt

Der heutige Code lässt es fallen, wortlos. Drei Verhalten sind vertretbar, und
welches richtig ist, hängt davon ab, was der Nutzer vorhat — deshalb steht die
Antwort **in der Datei**, nicht in einer Konvention:

| `rest:` | Verhalten | Wofür |
|---|---|---|
| `error` (Vorgabe) | Abbruch mit Nennung der fehlenden IDs | der Normalfall. Wer sortiert, will alle Bilder platziert haben |
| `append` | fehlendes Material chronologisch **hinten** anhängen, mit Meldung im Bericht | Zwischenstand: 40 von 90 sortiert, der Rest soll schon mitlaufen |
| `drop` | fehlendes Material weglassen, mit Meldung im Bericht | Auswahl statt Sortierung — siehe unten |

Die Vorgabe ist `error`, weil der teuerste Fehler dieser Datei das *stille*
Verschwinden von Bildern ist: Man rendert 70 Minuten und zählt hinterher nach.
Die Meldung nennt die ersten acht fehlenden IDs in einer Form, die sich direkt
in die Datei kopieren lässt, und dazu die Gesamtzahl:

```
14 Medien stehen nicht in order.yaml. Entweder eintragen, oder `rest: append`
(hinten anhängen) bzw. `rest: drop` (weglassen) setzen:
  - img_DSC06390
  - img_DSC06391
  … (+12 weitere)
```

**`drop` ist mehr als eine Notlösung.** Eine Slideshow gegen ein Stück von 6:32
fasst bei 8 Beats je Bild rund 50 Fotos; wer 90 hat, muss auswählen, und heute
gibt es dafür kein Werkzeug außer dem Dateisystem. `coverage_advice`
(`planner.py:686`) meldet die Diskrepanz bereits — mit `rest: drop` gibt es
zum ersten Mal einen Ort, an dem man darauf antworten kann, ohne Dateien zu
verschieben. Auskommentierte Zeilen sind dann die Auswahl, und der Kommentar
davor sagt, warum ein Bild draußen bleibt.

### Entscheidung 4 — Doppelte IDs sind ein Fehler

`order.yaml` beschreibt eine **Permutation** des Materials, und eine Permutation
hat keine Doppelten. Der Grund ist nicht Prinzipienreiterei, sondern ein
konkreter Schaden am Nachbarn: `insert_titles` (`build.py:212`) baut
`pos_von_src` als Abbildung `src -> Position` und sucht das Kapitel mit
`next((p for p, m in enumerate(verwendet) if m.id == kap.before))`. Steht ein
Bild zweimal in der Folge, trifft `before: img_042` stillschweigend das **erste**
Vorkommen — die Kapitelverankerung wird mehrdeutig, ohne dass es jemand merkt.

Also: Abbruch mit Nennung der ID und beider Zeilen. Eine **bewusste**
Wiederholung — dasselbe Bild als Klammer am Anfang und am Ende — bleibt
möglich, aber wie bisher als Handgriff in `edit.yaml`, wo sie sichtbar allein
steht. Sollte sich das als häufiger Wunsch erweisen, braucht `chapters.yaml`
zuerst einen Anker, der ein bestimmtes Vorkommen benennt; das ist Stufe 3
und kein Nebenbei.

### Entscheidung 5 — Wie `order.yaml` und `chapters.yaml` zusammenfinden

Beide Dateien verankern an Medien-IDs, und das genügt: `before: img_042` wirkt
in einer manuell sortierten Folge genauso wie in einer chronologischen — die
Auflösung in `insert_titles` läuft über `verwendet`, und das ist bereits die
sortierte Liste. **Für Stufe 1 ist damit nichts zu tun.**

Bequem ist es trotzdem nicht. Wer thematisch sortiert, will die Zäsur an der
**Blockgrenze**, nicht an einem bestimmten Bild — und `before: img_042` bricht
in dem Moment, in dem man img_042 innerhalb seines Blocks nach hinten schiebt.
Der Anker zeigt dann mitten in den Block hinein, kommentarlos.

Deshalb bekommt `chapters.yaml` in **Stufe 2** einen dritten Anker neben
`before:` und `at:`:

```yaml
chapters:
  - {group: am-wasser, title: "Am Wasser", subtitle: null}
```

`group:` setzt die Folie vor das erste Element der gleichnamigen Gruppe. Die
Reihenfolge wohnt weiter in `order.yaml`, der Text weiter in `chapters.yaml`,
und beide Dateien behalten genau eine Aufgabe. Ein `group:`, das es nicht gibt,
ist ein Fehler mit Nennung des Kapitels — dieselbe Regel wie bei `before:`.
Der Validator `_genau_ein_anker` wird zu *genau einem von dreien*.

### Entscheidung 6 — Chronologie-Annahmen, die brechen

Drei Stellen im Code setzen voraus, dass die Folge zeitlich aufsteigend läuft.
Bei thematischer Sortierung tut sie das nicht mehr. Alle drei müssen angefasst
werden, sonst liefern sie stillen Unsinn:

| Stelle | Annahme | Was zu tun ist |
|---|---|---|
| `chapters.py:150` `first_image_id` | „das erste Bild" = erstes chronologisch | muss die Reihenfolge aus `order.yaml` kennen, sonst nennt der Kommentar beim Auftakt das falsche Bild als `bg: auto`-Grund |
| `chapters.py:92` `suggest` | Abschnittsgrenzen = Zeitlücken zwischen **Nachbarn** | bei manueller Sortierung gegenstandslos: die Nachbarn sind thematisch, nicht zeitlich benachbart. `slideshow chapters` muss das melden, nicht rechnen |
| `build.py:316` `_auto_subtitle` | `subtitle: auto` = „Tag 11 · 24. Juli" des Folgebildes | bleibt technisch korrekt, wird aber inhaltlich irreführend: „Tag 3" über einem Block, der Bilder aus fünf Tagen enthält |

Für die letzten beiden gilt dieselbe Antwort: **melden, nicht korrigieren.**
`build` prüft, ob die Aufnahmezeiten der Folge monoton steigen, und warnt genau
dann, wenn ein Kapitel `subtitle: auto` verwendet:

```
Die Reihenfolge ist nicht chronologisch (order.yaml). `subtitle: auto` bildet
das Datum des folgenden Bildes ab — bei Kapitel 'Am Wasser' ist das Tag 3, der
Block enthält aber Bilder von Tag 1 bis Tag 5. Zweite Zeile von Hand setzen
oder mit `subtitle: null` weglassen.
```

Die Prüfung hängt an der gemessenen Monotonie, nicht am bloßen Vorhandensein
von `order.yaml` — wer die Datei nur benutzt, um drei Bilder zu tauschen,
bekommt keine Warnung über etwas, das er nicht getan hat.

`slideshow chapters` bekommt denselben Vorbehalt in den Kopf der erzeugten
Datei und in den Bericht, in derselben Sprache wie der bereits vorhandene
GPS-Hinweis aus `coverage_note`.

### Entscheidung 7 — Der Generator schreibt ein Formular, kein Erzeugnis

Niemand tippt 90 Medien-IDs ab. `slideshow order` schreibt die Datei
chronologisch vorbelegt, und Sortieren heißt danach **Zeilen verschieben** —
in jedem Editor eine Handbewegung.

Vorbelegt wird nicht flach, sondern nach **Tagen** gruppiert (`--by day`,
Vorgabe), wahlweise nach **Ortsclustern** (`--by place`, wo GPS vorliegt) oder
gar nicht (`--by none`). Die Signale dafür sind da: `chapters.py:92` `suggest`
rechnet Zeitlücken und Ortssprünge bereits aus, und sie werden hier nur anders
ausgegeben. Eine nach Tagen vorgruppierte Datei ist der brauchbarste
Ausgangspunkt für eine thematische Umsortierung — man sieht, woher ein Bild
kommt, während man es woandershin schiebt.

Jede Zeile trägt den Kontext, den man zum Sortieren braucht, als Kommentar:
Tag und Uhrzeit in der Form, die `subtitle: auto` erzeugt, Hoch- oder
Querformat, bei Clips die Länge. Zwischen zwei Gruppen steht die Begründung
(`# 42 km · 19 h Pause`). Ein `yaml.dump` kann das nicht — die Datei wird von
Hand geschrieben, nach dem Muster von `dump_chapters_yaml`.

Zwei Regeln von `slideshow chapters` gelten unverändert: eine vorhandene Datei
wird **nicht** überschrieben (`--force`, `--dry-run`), denn sie enthält
Handarbeit.

**`--update` ist der Grund, warum die Datei einen Generator braucht und nicht
nur eine Vorlage.** Nach einem erneuten `probe` mit 12 nachgereichten Fotos
liest `--update` die bestehende Datei ein, behält Reihenfolge, Gruppen und
Kommentare bei und hängt das neue Material als eigene Gruppe mit dem Kommentar
`# neu seit <Datum> — einsortieren` ans Ende. Ohne das steht man nach jedem
`probe` vor der Wahl zwischen `--force` (Handarbeit weg) und Handarbeit.

### Entscheidung 8 — Was ausdrücklich nicht dazugehört

- **Keine Sortierhilfe nach Bildinhalt.** Ähnlichkeitssuche, Farbclustering,
  Gesichtserkennung: alles denkbar, nichts davon gehört in dieses Vorhaben.
  Was „thematisch" heißt, weiß der Mensch.
- **Keine Kontaktbogen-Ansicht.** Sortieren geht leichter, wenn man die Bilder
  sieht; das ist ein eigenes Vorhaben (ein HTML-Bogen aus `cache/`), und es
  hängt nicht an diesem hier.
- **Kein Eingriff in den Planer.** `planner.py` bekommt eine Liste und weiß
  nicht, woher ihre Reihenfolge kommt. Das muss so bleiben.
- **Kein Rückschreiben aus `edit.yaml`.** Verlockend („ich habe schon in
  `edit.yaml` sortiert, mach mir daraus eine `order.yaml`") und als Einzeiler
  über `slideshow order --from edit.yaml` machbar. Aber es verankert eine
  Reihenfolge im Erzeugnis statt in der Eingabe und lädt dazu ein, weiter in
  `edit.yaml` zu sortieren. Zurückgestellt auf Stufe 3, wenn sich zeigt, dass
  es gebraucht wird.

---

## 4. Vorgeschlagene Umsetzung

**Stufe 1 — die Datei wirkt**

1. **`models.py`:** `OrderGroup` und `OrderList` nach dem Vorbild von
   `Chapter`/`ChapterList` (`models.py:601`), inklusive `load()` über den
   `_LineLoader` — damit ein Tippfehler Datei und Zeile nennt, ohne dass dafür
   etwas Neues entsteht. `model_validator`: genau eines von `groups:` und
   `order:`; `rest` als `Literal["error", "append", "drop"]`.
2. **`order.py` (neu), Auflösung:** `resolve_order(manifest, olist) ->
   tuple[list[str], list[str]]` — die aufgelöste ID-Folge und die Meldungen.
   Hier wohnen die Prüfungen aus Entscheidung 3 und 4: unbekannte ID
   (Abbruch, mit Zeile), doppelte ID (Abbruch, mit beiden Zeilen), fehlendes
   Material (`rest`). Reine Rechnung, kein Datei-I/O — wie `titles.py`.
3. **`build.py:63`:** die beiden Zeilen der `order`-Behandlung durch den Aufruf
   ersetzen. Die Signatur bleibt, wie sie ist. Die Meldungen gehen nach
   `plan.warnings`, wie alles andere auch.
4. **`build.py`, Chronologieprüfung:** Monotonie der `capture_time` über die
   aufgelöste Folge messen und die Warnung aus Entscheidung 6 setzen, wenn ein
   Kapitel `subtitle: auto` verwendet.
5. **`cli.py`:** `build --order`, plus `_load_order` nach dem Muster von
   `_load_chapters` (`cli.py:572`) — ausdrücklich genannter Pfad ist Pflicht,
   gefundene `order.yaml` ist Bequemlichkeit. Berichtszeile analog zur
   Kapitelzeile: `Reihenfolge: order.yaml (87 von 87 Medien, 5 Gruppen)`.
6. **`chapters.py:150` `first_image_id`** nimmt die aufgelöste Folge entgegen
   statt selbst `chronological` zu rufen.

**Stufe 2 — die Datei entsteht**

7. **`slideshow order`** in `cli.py` und `dump_order_yaml` in `order.py`:
   Formular mit Kontextkommentaren, Gruppierung nach `--by`, `--force`,
   `--dry-run`. Die Gruppierungssignale kommen aus `chapters.py` (`suggest`,
   `distance_km`) — nicht neu messen.
8. **`--update`:** bestehende Datei einlesen, neues Material als Gruppe
   `neu` anhängen, verschwundenes Material auskommentiert stehen lassen mit
   dem Grund (`# nicht mehr im Manifest`).
9. **`chapters.yaml` bekommt `group:`** als dritten Anker (Entscheidung 5):
   `Chapter.group`, `_genau_ein_anker` auf drei erweitern, Auflösung in
   `insert_titles`. Fehlt `order.yaml`, ist ein `group:`-Anker ein Fehler mit
   genau diesem Hinweis.
10. **`slideshow chapters`** meldet den Vorbehalt aus Entscheidung 6, wenn
    `order.yaml` vorliegt und die Folge nicht monoton ist — im Kopf der
    erzeugten Datei und im Bericht, neben `coverage_note`.
11. **Dokumentation.** `docs/edit-yaml.md`: der Abschnitt „Reihenfolge ändern"
    unter „Häufige Eingriffe" wird umgeschrieben — er beschreibt heute den
    Mangel. Dazu ein Abschnitt „`order.yaml`" neben dem zu `chapters.yaml`,
    die Zeile in „Verwandte Dateien", und die Baustellenzeile in `CLAUDE.md`.
    `README.md` bekommt `slideshow order` in den Ablauf am Kopf.

**Stufe 3 — nur wenn es sich zeigt**

12. Wiederholtes Material (Entscheidung 4) — braucht zuerst einen
    Kapitelanker, der ein Vorkommen benennt.
13. `slideshow order --from edit.yaml` (Entscheidung 8).
14. Kontaktbogen als HTML aus `cache/` — eigenes Vorhaben.

**Nicht anzufassen:** `planner.py`, `render.py`, `kenburns.py`, `beats.py`,
`encoders.py`, die Concat- und Muxing-Kette. Wenn eine dieser Dateien im Diff
auftaucht, ist etwas schiefgegangen.

---

## 5. Betroffene Stellen

| Ort | Rolle | Änderung |
|---|---|---|
| `order.py` (neu) | Auflösung und Formular | `resolve_order`, `dump_order_yaml`, `update_order` |
| `models.py:601` | `Chapter` | Vorbild für `OrderGroup`; in Stufe 2 der `group:`-Anker |
| `models.py:647` | `ChapterList` | Vorbild für `OrderList.load` |
| `build.py:52` | `build_edit_list` | Signatur bleibt; `order` wird zur aufgelösten Liste |
| `build.py:63` | Reihenfolge | die zwei stillen Zeilen ersetzen |
| `build.py:212` | `insert_titles` | Stufe 2: `group:`-Anker auflösen |
| `build.py:316` | `_auto_subtitle` | Warnung bei nicht-chronologischer Folge |
| `chapters.py:92` | `suggest` | Vorbehalt melden |
| `chapters.py:150` | `first_image_id` | aufgelöste Folge entgegennehmen |
| `cli.py:86` | `build`-Parser | `--order` |
| `cli.py:466` | `cmd_build` | `_load_order`, Berichtszeile |
| `cli.py:572` | `_load_chapters` | Vorbild für `_load_order` |
| `cli.py` | Unterkommandos | `order` mit `--by`, `--update`, `--force`, `--dry-run` |
| `docs/edit-yaml.md` | Referenz | `order.yaml`, „Reihenfolge ändern" neu |
| `README.md` | Ablauf | `slideshow order` |
| `CLAUDE.md` | Baustellentabelle | Zeile für dieses Vorhaben |

Kein neues externes Werkzeug, keine neue Abhängigkeit, keine `doctor`-Zeile.

---

## 6. Abnahmekriterien

- **O1 — Die Reihenfolge wirkt.** Ein Fixture-Projekt mit umgekehrter
  `order.yaml` erzeugt eine `edit.yaml`, deren `still`-Segmente exakt in dieser
  Reihenfolge stehen — und deren Timeline dieselbe Gesamtlänge hat wie die
  chronologische. Umsortieren verschiebt keine Musik.
- **O2 — Kein Bild verschwindet still.** Fehlt ein Medium in `order.yaml`,
  bricht `build` mit `rest: error` ab und nennt die ID. Mit `rest: append`
  läuft es durch, das Medium steht hinten, und der Bericht sagt es. Mit
  `rest: drop` fehlt es, und der Bericht sagt auch das. Alle drei Fälle
  geprüft.
- **O3 — Fehler melden sich mit Zeile.** Eine unbekannte ID und eine doppelte
  ID führen jeweils zu einem `SchemaError` mit Dateiname und Zeilennummer,
  nicht zu einem Traceback und nicht zu stillem Überspringen.
- **O4 — Lokalität der Änderung.** Werden zwei benachbarte Bilder getauscht,
  meldet der zweite Renderlauf genau die betroffenen Blenden als neu und alle
  Standbild-Segmente „aus Cache". Das ist die Probe auf Entscheidung 7 des
  Titelfolien-Briefings; bricht sie, hängt die Ken-Burns-Richtung wieder an der
  Position.
- **O5 — Rundlauf.** `build` mit `order.yaml` → `edit.yaml` →
  `plan_from_edit` → erneutes Schreiben liefert dieselbe Datei. `order.yaml`
  wird dabei kein zweites Mal gelesen und von `build` nie geschrieben.
- **O6 — Kapitel überleben das Umsortieren.** Eine `chapters.yaml` mit
  `before: img_042` setzt die Folie in der manuell sortierten Folge unmittelbar
  vor img_042 — an der neuen Position, nicht an der chronologischen. Mit
  `group:` (Stufe 2) vor das erste Element der Gruppe, auch nachdem innerhalb
  der Gruppe umsortiert wurde.
- **O7 — Die Chronologie-Warnung greift und schweigt richtig.** Bei
  nicht-monotoner Folge **und** `subtitle: auto` erscheint die Warnung aus
  Entscheidung 6. Bei monotoner Folge mit `order.yaml` erscheint sie nicht.
  Gegenprobe: ohne `subtitle: auto` erscheint sie auch bei nicht-monotoner
  Folge nicht.
- **O8 — Ohne `order.yaml` ändert sich nichts.** Die vorhandene Testsuite
  bleibt in dem Umfang grün, in dem sie es heute ist. Die erzeugte `edit.yaml`
  eines Projekts ohne `order.yaml` ist **byteweise** identisch zu der vor
  dieser Arbeit.
- **O9 — `--update` behält Handarbeit.** Eine von Hand sortierte Datei plus
  drei neue Medien im Manifest ergibt nach `slideshow order --update` dieselbe
  Reihenfolge, dieselben Gruppennamen und die drei neuen IDs in einer eigenen
  Gruppe am Ende.
- **O10 — Suite.** `pytest` bleibt grün bis auf die bekannte Vorbelastung
  (`test_hdr_wird_erkannt`, `test_tonemapping_steht_vor_dem_scale`,
  `test_ohne_tonemapper_greift_die_naeherung` in `tests/test_media.py`, die
  bereits vor dieser Arbeit unter ffmpeg 8.1.2 scheitern). Keine **neuen**
  Fehlschläge.

---

## 7. Risiken

- **Zwei Dateien, die beide an IDs hängen.** `order.yaml` und `chapters.yaml`
  brechen beide, wenn jemand eine Quelldatei umbenennt — und dann gleich
  doppelt. Der Schaden ist begrenzt (beide brechen laut, mit Nennung der ID),
  aber die Fehlermeldung sollte auf die gemeinsame Ursache zeigen und nicht
  zweimal dasselbe Rätsel aufgeben.
- **Der Sonderfall der Doppelnamen wird schlimmer.** Zwei Dateien mit demselben
  Stamm in verschiedenen Ordnern bekommen `_2`, `_3` … „in der Reihenfolge, in
  der `probe` sie findet". Kommt eine dritte hinzu, kann der Zähler die
  Zuordnung neu verteilen — und dann zeigt eine sortierte `order.yaml` still
  auf andere Bilder als gemeint. Das ist ein vorhandenes Risiko, aber es trifft
  hier 90 Anker statt zwölf. `slideshow order --update` sollte deshalb melden,
  wenn sich eine ID zwischen zwei Manifesten auf eine andere Quelldatei
  verschoben hat.
- **Die Chronologie-Warnung wird zum Rauschen.** Wer bewusst thematisch
  sortiert, will `subtitle: auto` ohnehin nicht und bekommt die Warnung bei
  jedem Lauf. Sie muss deshalb **je Kapitel** kommen und ausdrücklich sagen,
  wie man sie loswird (`subtitle: null`) — nicht als globaler Satz, den man
  wegzuklicken lernt.
- **`rest: append` als Falle.** Bequem beim Zwischenstand, gefährlich beim
  Abschluss: ein vergessenes `append` hängt fünf unsortierte Bilder ans
  Filmende, und der Bericht ist die einzige Warnung. Die Meldung gehört
  deshalb nicht zu den Hinweisen, die `--force` ausblendet.
- **Der Generator veraltet gegenüber dem Manifest.** Zwischen `slideshow order`
  und `slideshow build` kann ein `probe` liegen. `build` fängt das ab
  (unbekannte und fehlende IDs), aber der Weg zurück ist `--update`, und darauf
  muss die Fehlermeldung zeigen — sonst greift jemand zu `--force` und verliert
  die Sortierung.
