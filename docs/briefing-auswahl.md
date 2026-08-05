# Briefing: Auswahl aus großem Material

**Status:** **Stufen 0–2 umgesetzt** · **Betrifft:** neues Modul
`src/slideshow/select.py`, neues Modul `src/slideshow/sheet.py`, `probe.py`,
`proc.py`, `planner.py`, `preprocess.py`, `cli.py`, `docs/edit-yaml.md` ·
**Vorbedingung:** `order.yaml` (Briefing „Manuelle Reihenfolge", Stufen 1–2,
umgesetzt)

> **Was heute läuft.**
>
> ```
> slideshow probe D:\fotos\island        # 1240 Bilder erfasst
> slideshow audio track.mp3
> slideshow beats                        # -> beats.yaml, 187 Slots
> slideshow select                       # -> order.yaml: 187 gewaehlt, Rest als Kommentar
> slideshow preprocess                   # nur die Auswahl, nicht alle 1240
> slideshow build && slideshow render
> ```
>
> Stufe 0 (Argumentdatei für exiftool, eigene Meldung für zu lange
> Kommandozeilen, lauter EXIF-Ausfall), Stufe 1 (Trauben, gedämpfte
> Tagesquote, Spreizung, Wahl in der Traube, harte Filter,
> Hochformat-Nachkorrektur, das Formular, `slideshow select`) und Stufe 2
> (`preprocess --order`/`--all`) sind umgesetzt und getestet —
> `tests/test_select.py`, 29 Tests.
>
> **Offen:** Stufe 3, der Kontaktbogen. Der Dateikopf verweist bereits auf
> `slideshow sheet`.

---

## 0. Ergebnis der Umsetzung

Fünf Abweichungen vom Vorschlag, alle in der Umsetzung gefunden:

**1. Die exiftool-Grenze ist schärfer und das Symptom ein anderes.** Der
Vorschlag nahm einen stillen `mtime`-Rückfall an. Gemessen: `read_exif_batch`
läuft bei **192 Dateien** (Pfadlänge 168 Zeichen, Kommandozeile 32648 Zeichen)
und bricht bei 193 ab — aber laut, mit `Programm nicht gefunden: exiftool
([WinError 206] …)`. Windows meldet die zu lange Argumentliste als
`FileNotFoundError`, und `proc.run` deutete jeden `OSError` zu einem fehlenden
Programm um. Deshalb kam Punkt 2 in Stufe 0 dazu: die Meldung gehört korrigiert,
und zwar für alle Aufrufer — `ffmpeg` mit langer Concat-Liste läuft in dieselbe
Falle.

**2. `MANIFEST_VERSION` wurde nicht angehoben.** Der Vorschlag verlangte es und
zugleich, dass alte Manifeste weiter laden. Beides geht nicht: `_check_version`
lehnt jede Version ab, die nicht die aktuelle ist, und ein Anheben hätte
`testset1/manifest.json` unbrauchbar gemacht. Da `ImageInfo` und `MediaItem`
`extra="allow"` tragen und alle neuen Felder Vorgaben haben, ist das Anheben
unnötig. Ein altes Manifest liefert für die neuen Felder Nullen; wer Sterne
auswerten will, lässt `probe` erneut laufen.

**3. Die Kleinbild-Brennweite fehlt öfter als gedacht.** Gemessen an der Sony
ILCE-6700: `FocalLengthIn35mmFormat` schreibt sie nicht, und `FocalLength35efl`
liefert exiftool dann unverändert die reale Brennweite zurück — 114 mm statt der
171 mm, die der Crop ergäbe. `_kb_brennweite` erkennt den Gleichstand und
schreibt lieber eine 0 („unbekannt") als eine Zahl, die wie eine Messung
aussieht. Die Freihandregel fällt dann auf die reale Brennweite zurück und ist
um den Cropfaktor zu lasch — die harmlose Richtung.

**4. Trauben entstehen je Kamera, nicht sequenziell.** Der Vorschlag sagte
„Gerätewechsel trennt". Auf der gemischten Folge ist das zu wenig: bei der
Reihenfolge Sony, Pixel, Sony zerreißt der Wechsel die Kette, und die beiden
Sony-Aufnahmen landen in *verschiedenen* Trauben — zwölf Sekunden auseinander
und beide wählbar. Gemessen an 1570 Aufnahmen: drei solcher Paare. Jetzt wird je
Kamera geclustert; `_pruefe_abstaende` zählt nach und meldet, was übrig bleibt.

**5. Der Deckel `max_share` durfte nicht unter den Gleichanteil fallen.** Bei
vier Gruppen *sind* 25 Prozent schon der Gleichanteil: der Deckel traf jede
Gruppe, erzwang exakte Gleichverteilung und machte `alpha` wirkungslos. Er liegt
jetzt nie unter dem Doppelten des Gleichanteils.

Dazu zwei Ergänzungen, die im Vorschlag fehlten: **abgewählte Geschwister
ausgelassener Trauben werden vollständig genannt** (sonst bietet
`order --update` sie erneut als neu an — Prinzip 2 war sonst verletzt), und
**`select` meldet Unterdeckung** samt Grund. Ohne diese Meldung sah ein Lauf mit
Zielzahl 117 und 19 gewählten Medien wortlos richtig aus.

### Abnahme

| | Kriterium | Ergebnis |
|---|---|---|
| A1 | Kommandozeilenlänge | 1200 Pfade, 1200 EXIF-Treffer in 16,6 s ✓ |
| A2 | keine benachbarten Aufnahmen | 0 Paare derselben Kamera; 7 Geräteausnahmen, gemeldet ✓ |
| A3 | Quote trifft | Summe exakt 187 über 30 Gruppen; 5→2, 252→16 Aufnahmen ✓ |
| A4 | Spreizung | Variationskoeffizient 0,32 gegen 1,01 bei Zufallsstichprobe ✓ |
| A5 | reproduzierbar | gleicher Seed zeichengleich, anderer Seed 54 % verschieden ✓ |
| A6 | Rundlauf | `select` → `preprocess` → `build` an echtem Material durch; Edit-List aus 6 von 21 Medien ✓ · **voller Render offen** |
| A7 | Nachpflegen | jede abgewählte ID im Text erwähnt ✓ |
| A8 | `preprocess` folgt | frisches Projekt: 6 von 21 Bildern normalisiert; `--all` holt die 15 nach ✓ |
| A9 | Kontaktbogen | **offen — Stufe 3** |
| A10 | Sichtprüfung | **offen** — braucht echtes Material und den Kontaktbogen |

Laufzeit: 1570 Bilder in 0,01 s, erzeugte Datei 1436 Zeilen / 54 KB.
Gesamtsuite 412 bestanden, 3 rot (die bekannten HLG-Tests, unverändert).

Zu Stufe 2 kam eine Kleinigkeit dazu, die im Vorschlag fehlte: die
**Speicherschätzung** in `cmd_preprocess` rechnet jetzt über die Auswahl statt
über das Manifest. Sonst verlangt sie bei tausend erfassten und zweihundert
gewählten Bildern das Fünffache und schlägt Alarm, wo nichts ist.

---

## 1. Ausgangslage

Der heutige Ablauf setzt voraus, dass das Material *bereits* die Auswahl ist:
`probe` erfasst einen Ordner, `preprocess` normalisiert alles darin, `build`
legt alles in die Timeline. Bei 90 vorsortierten Bildern stimmt das.

Bei einem Sammelbecken von über 1000 Bildern stimmt keine der drei Annahmen
mehr:

1. **`preprocess` normalisiert alles** (`preprocess.py:442`, „Verarbeitet alle
   Medien des Manifests"). 1240 Bilder auf 7680 px Langkante sind Stunden
   Rechenzeit und zweistellige Gigabyte Cache für Material, das nie im Film
   landet.
2. **Der Planer meldet die Überdeckung erst nach dem Bauen.** `coverage`
   (`planner.py:571`) rechnet über `len(intents)` und sagt korrekt „1053 Medien
   ungenutzt" — aber erst, wenn `preprocess` längst gelaufen ist, und ohne einen
   Vorschlag, *welche* 187 bleiben sollen.
3. **Die einzige Handhabe ist `rest: drop`.** Das Format kann eine Auswahl
   ausdrücken; erzeugen kann sie niemand. Wer 1053 Zeilen von Hand
   auskommentiert, hat den Film nicht gemacht.

Dieses Briefing beschreibt ein Kommando, das die Auswahl **vorschlägt** — nach
Zeitstruktur, ohne einen einzigen Bildpunkt anzusehen — und einen
**Kontaktbogen**, der sie zum Ansehen bringt, damit der Mensch die Entscheidung
trifft, die eine Heuristik nicht treffen kann.

Der Anspruch:

1. **Die Auswahl ist eine Datei, kein Zustand.** Was gewählt ist, steht in
   `order.yaml` und überlebt jeden weiteren Lauf.
2. **Nichts verschwindet unsichtbar.** Die 1053 nicht gewählten Bilder stehen
   in derselben Datei — auskommentiert, mit Begründung, an ihrem zeitlichen
   Platz.
3. **Tauschen ist ein Zeilentausch**, kein neuer Lauf.
4. **Keine inhaltliche Analyse.** Kein Bild wird zum *Entscheiden* geöffnet.
   Alles kommt aus Zeitstempeln und EXIF. Der Kontaktbogen liest Bildpunkte,
   aber nur zum Anzeigen.

---

## 2. Was bereits da ist

Der Eingriff ist überraschend klein, weil das Zielformat bereits existiert und
bereits das Richtige tut.

| Vorhanden | Ort | Was es hier beiträgt |
|---|---|---|
| **`rest: drop`** | `models.py:720`, `order.py:140` | die Auswahlsemantik ist fertig und getestet — der Kommentar im Generator nennt sie wörtlich: „eine Auswahl statt einer Sortierung" |
| **Auskommentierte Zeile = Abwahl** | `order.py:84` `mentioned_ids` | der entscheidende Baustein. `--update` bietet ein abgewähltes Bild nie wieder als „neu" an. Ohne das wären 1053 Kommentarzeilen nach dem zweiten `probe` wertlos |
| Slot-Kapazität je Region | `planner.py:675` `_region_capacity` | die Zielzahl steht in `beats.yaml` und muss nicht geraten werden |
| Gegenrechnung Material↔Musik | `planner.py:599` `material_seconds` | dieselbe Rechnung in der anderen Richtung; die Zielzahl ist ihre Umkehrung |
| Tagesgrenzen | `order.py:242` `_nach_tagen` | inklusive der Feinheit, dass 23:50 und 00:10 verschiedene Tage sind — eine Lückenheuristik träfe das nicht |
| Ortscluster | `chapters.py` `distance_km`, `JUMP_KM` | die zweite Quotierungsachse, falls GPS vorliegt |
| Uhren-Offsets je Gerät | `probe.py:597` `effective_capture_time` | bei zwei Kameras mit falsch gestellter Uhr ist das die Voraussetzung dafür, dass Trauben überhaupt stimmen |
| EXIF-Batchlauf | `probe.py:288` `read_exif_batch` | ein Lauf für alles; neue Tags kosten eine Zeile in `_EXIF_TAGS` (`probe.py:282`) |
| `size_bytes` im Manifest | `models.py:114` | schon erfasst, bisher ungenutzt — der Tiebreak innerhalb einer Traube |
| Formular-Muster | `order.py:294` `dump_order_yaml`, `order.py:348` `_kontext` | von Hand geschrieben statt `yaml.dump`, damit neben jeder Zeile steht, was dort steht. Die Auswahl braucht genau das, nur mit mehr Kontext |
| Nachpflegen ohne Verlust | `order.py:368` `update_order_text` | arbeitet auf dem Quelltext; die Handarbeit an der Auswahl überlebt ein `probe` |
| Kapitel je Block | `order.py:479` `group_anchors` | die erzeugten Tagesblöcke sind sofort Kapitelanker — `slideshow chapters --from-groups` funktioniert ohne Änderung |

**Zwei Stellen tragen die Last nicht.** Beide sind Vorbedingung, nicht Kür:

- **`read_exif_batch` bricht, lange bevor es 1000 Bilder sieht.** `probe.py:295`
  baut eine Kommandozeile aus allen Pfaden; Windows begrenzt `CreateProcess` auf
  32767 Zeichen. **Gemessen** (1200 Kopien eines Testbildes, mittlere Pfadlänge
  168 Zeichen): bei **192 Dateien** läuft es noch, bei 193 nicht mehr. Die
  Kommandozeile ist an der Kippe 32648 Zeichen lang — das Limit exakt. Bei
  kürzeren Pfaden liegt die Grenze entsprechend höher (~460 Dateien bei 70
  Zeichen), aber niemals bei 1000.

  Das `check=False` fängt den Fall **nicht** ab: `CreateProcess` wirft einen
  `FileNotFoundError` (WinError 206), der landet in `proc.py:72` im
  `except OSError`-Zweig und wird dort zu

  ```
  Programm nicht gefunden: exiftool ([WinError 206] Der Dateiname oder die
  Erweiterung ist zu lang)
  ```

  Das ist die zweitschlechteste Meldung, die dieser Fehler haben kann — laut
  ist er immerhin, aber er zeigt auf das falsche Problem. Wer sie liest,
  installiert exiftool neu. Der Nachsatz in der Klammer ist der einzige Hinweis
  darauf, dass exiftool sehr wohl da ist und nur die Argumentliste zu lang war.
- **`preprocess` kennt keine Auswahl** (`preprocess.py:442`). Ohne Filter
  entfällt der halbe Gewinn.

---

## 3. Gestalt

### 3.1 Die erzeugte `order.yaml`

```yaml
# order.yaml — Reihenfolge der Medien. Wird von `slideshow build` eingelesen.
#
# Erzeugt von `slideshow select`: 187 von 1240 Medien gewaehlt.
#   Zielzahl 187 aus beats.yaml (6:32 Musik, 8 Beats je Bild)
#   Seed 4711 · Traubenabstand 90 s · Tagesgewicht 0,50
#   1053 Medien stehen als Kommentar in dieser Datei — sie bleiben draussen.
#
# Ein anderes Bild nehmen heisst: die Kommentarzeile eintragen und die
# gewaehlte auskommentieren. `slideshow sheet` zeigt beide nebeneinander.
#
# Ein erneutes `slideshow select` wirft die Auswahl weg (`--force`).
# `slideshow order --update` pflegt neues Material ein und behaelt sie.

version: 1
rest: drop

groups:
  # Tag 1 · 24. Juli · 112 Aufnahmen in 38 Trauben, 14 gewaehlt
  - name: tag-1
    items:
      - img_DSC06273     # 10:14 · quer · Traube 3/4
      #  statt: img_DSC06271 (10:13) · img_DSC06272 (10:14) · img_DSC06274 (10:15)
      - img_DSC06288     # 10:41 · quer
      - clip_MVI_1234    # 10:52 · Clip 8,2 s
      - img_DSC06301     # 11:30 · hoch · Traube 1/2
      #  statt: img_DSC06302 (11:30)
      #
      #  ausgelassen 11:31–12:58 — 21 Aufnahmen in 6 Trauben:
      #  img_DSC06303 (11:31) · img_DSC06309 (11:44) · img_DSC06318 (12:02)
      #  img_DSC06327 (12:20) · img_DSC06340 (12:41) · img_DSC06351 (12:58)
      - img_DSC06362     # 13:12 · quer
```

Drei Arten von Kommentarzeilen, und alle drei sind dieselbe Sache — abgewähltes
Material an seinem zeitlichen Platz:

- **`statt:`** — die Geschwister derselben Traube. Fast immer dasselbe Motiv;
  hier tauscht man, wenn das gewählte unscharf ist oder jemand die Augen zu hat.
- **`ausgelassen`** — ganze Trauben, die die Quote nicht mehr hergab. Hier
  tauscht man, wenn ein Motiv fehlt.
- **`— …`** hinter einer Zeile — der Grund eines *harten* Ausschlusses
  (`zu klein: 1024x768`), siehe 4.5.

Aufgeführt wird je Traube nur ein Vertreter, wenn eine Traube ausgelassen wird
— sonst stehen 1053 IDs in der Datei und niemand liest sie. Die vollständige
Liste ist der Kontaktbogen.

### 3.2 Die Kommandos

```
slideshow select [--count auto|N] [--seed N] [--by day|place]
                 [--burst-gap 90] [--day-weight 0.5]
                 [--min-per-day 1] [--max-share 0.25]
                 [--min-long-edge 2160] [--rating-min N]
                 [--keep-clips|--no-keep-clips] [--sheet]
                 [-o order.yaml] [--force]

slideshow sheet  [--order order.yaml] [--all|--selected]
                 [-o contact.html] [--thumb 320] [--force]
```

`--dry-run` ist global und zeigt bei beiden nur an.

---

## 4. Das Auswahlverfahren

Vier Stufen, in dieser Reihenfolge. Jede ist für sich erklärbar und einzeln
prüfbar — das ist wichtiger als ein besseres Gesamtergebnis, denn wer der
Auswahl nicht traut, benutzt sie nicht.

### 4.1 Zielzahl

```python
def slot_capacity(regions, defaults, *, reserve=0) -> int
```

Summe von `_region_capacity` über alle Regionen, abzüglich Reserve. Reserviert
werden Slots für Titelfolien (aus `chapters.yaml`, falls vorhanden) und für
Clips, die mehr als einen Standardslot belegen.

`--count auto` ist die Vorgabe. Eine getippte Zahl gewinnt, wird aber gegen die
Kapazität geprüft und meldet Über- oder Unterdeckung mit derselben Sprache wie
`_print_coverage` (`cli.py:792`) — die Auswahl ist der Ort, an dem diese Meldung
etwas nützt, denn hier lässt sie sich noch befolgen.

### 4.2 Trauben — die Ähnlichkeitsvermutung

Aufnahmen mit weniger als `burst_gap` (Vorgabe **90 s**) Abstand bilden eine
**Traube**. Serienbilder, Belichtungsreihen, dreimal dasselbe Motiv, bis es
sitzt.

**Aus einer Traube kommt höchstens ein Bild in den Film.** Das ist die einzige
Regel des Verfahrens, die ohne Quote und ohne Zufall gilt.

Der Zeitabstand ist ohne inhaltliche Analyse das einzige verfügbare
Ähnlichkeitssignal — und ein gutes: zwei Aufnahmen innerhalb von 90 Sekunden
zeigen fast immer dasselbe. Die Gegenrichtung stimmt nicht (dasselbe Motiv am
nächsten Tag ist eine eigene Traube), und das ist in Ordnung: die Regel soll
Wiederholung *vermeiden*, nicht sie *finden*.

Verfeinerungen, wo Signale vorliegen:

- **Ortssprung trennt.** Liegt GPS vor und beträgt der Abstand mehr als
  `JUMP_KM`, endet die Traube, egal wie eng die Zeit liegt. (Bei diesem Material
  praktisch nie — GPS haben die wenigsten Bilder.)
- **Gerätewechsel trennt.** Zwei Kameras, die gleichzeitig laufen, machen keine
  Serie, sondern zwei Blickwinkel. Beide dürfen bleiben.
- **Trauben werden gedeckelt.** Eine Traube, die über `burst_max` (Vorgabe
  10 min) läuft, wird an der größten inneren Lücke geteilt. Sonst verschmilzt
  eine Stunde regelmäßigen Fotografierens im Minutentakt zu einem einzigen
  Eintrag, und der ganze Vormittag stellt ein Bild.

### 4.3 Quote je Tag — gedämpft

Gleich viele Bilder je Kalendertag kippt in die eine Richtung (der Anreisetag
mit 5 Fotos bekommt so viel wie der Wandertag mit 400); proportional zum
Material kippt in die andere (der Tag, an dem viel geknipst wurde, frisst den
Film). Der Vorschlag liegt dazwischen:

```
    n_j  ∝  c_j ^ α
```

`c_j` ist die Zahl der **Trauben** an Tag j — nicht der Bilder. Das ist der
Punkt: wer 200 Serienbilder von zwei Motiven macht, hat zwei Motive, und die
Quote soll ihn dafür nicht belohnen.

| α | Wirkung |
|---|---|
| 0 | exakt gleich viele je Tag |
| **0,5** (Vorgabe) | vierfaches Material → doppelt so viele Bilder |
| 1 | proportional zum Material |

Danach **Boden und Deckel**: `--min-per-day` (Vorgabe 1) stellt sicher, dass
kein Tag mit Material leer ausgeht; `--max-share` (Vorgabe 0,25) verhindert,
dass ein Tag den halben Film stellt. Beides kann sich widersprechen — bei 30
Tagen und `min-per-day: 1` ist die Zielzahl 187 knapp; dann gewinnt der Boden,
und die Meldung sagt es.

Die Ganzzahligkeit ist eine Sitzverteilung, kein Runden: erst
`floor`, dann die verbleibenden Sitze nach größtem Rest — sonst summieren sich
187 gerundete Quoten auf 183 oder 191.

`--by place` ersetzt die Achse durch Ortscluster, `--by none` verzichtet auf
die Quote (dann wirkt nur noch 4.4 über das gesamte Material).

### 4.4 Spreizung innerhalb des Tages

Aus den `c_j` Trauben eines Tages werden `n_j` gezogen — **nicht zufällig**,
sonst kommen alle acht Bilder vom Abendessen.

`n_j` Zielzeitpunkte gleichmäßig zwischen erster und letzter Aufnahme des
Tages, jeweils die nächstgelegene noch freie Traube. Echte Uhrzeitlücken
(Mittagspause, Autofahrt) bleiben damit respektiert: die Zielzeitpunkte, die in
ein Loch fallen, wandern an dessen Ränder — und die sind Ende und Anfang einer
Aktivität, also genau richtig.

Der Zufall sitzt im **Jitter** der Zielzeitpunkte (±½ Rasterbreite). Damit
liefert ein zweiter Seed eine sichtbar andere, gleich gut gespreizte Auswahl.

### 4.5 Wahl innerhalb der Traube

Punktzahl je Bild, gewichtete Ziehung statt Argmax:

| Signal | Wirkung | Woher |
|---|---|---|
| **Rating / Label** | dominant — ein Stern schlägt alles andere | EXIF, neu in `_EXIF_TAGS` |
| **Position in der Traube** | letztes +1, erstes −1 (ab 3 Bildern) | Aufnahmezeit |
| **`size_bytes`** | z-Wert **innerhalb** der Traube | Manifest, `models.py:114` |
| **Verwacklungsverdacht** | −1, wenn Belichtungszeit > 1/Brennweite(KB) | EXIF, neu |

Zu `size_bytes`: als globales Schärfemaß wäre das Unfug — ein Bild mit viel
Himmel ist klein und trotzdem scharf. **Innerhalb einer Traube** ist der
Vergleich aber fair: gleiche Kamera, gleiches Motiv, gleiche Belichtung,
gleiche JPEG-Einstellung. Eine verwackelte Aufnahme hat weniger hohe
Ortsfrequenzen und komprimiert messbar kleiner. Das ist kein Schärfemaß,
sondern ein Tiebreak zwischen fünf Versuchen desselben Bildes — und es kostet
keinen einzigen Dateizugriff.

Zum Verwacklungsverdacht: `ExposureTime > 1 / FocalLengthIn35mmFormat` ist die
Freihandregel. Sie kennt keinen Stabilisator und kein Stativ und liegt deshalb
oft falsch — als Abwertung *innerhalb* einer Traube ist das verschmerzbar, als
Ausschlussgrund wäre es nicht hinnehmbar. Deshalb steht sie hier und nicht in
4.6.

### 4.6 Harte Filter

Vor allem anderen, mit Grund in der Kommentarzeile:

- **Mindestlangkante** (`--min-long-edge`, Vorgabe **2160**). Der Master ist
  4K, und Ken Burns zoomt hinein. Ein 1024 px breites Weiterleitungsbild ist
  unbrauchbar, egal wie schön es ist. Bilder unter 3840 px kommen mit, bekommen
  aber eine Warnung.
- **Seitenverhältnis** jenseits 2,5:1 — Panoramen, die in 16:9 zu
  Briefmarken werden. Nicht ausgeschlossen, aber abgewertet und markiert.
- **`--rating-min`** — falls in Lightroom oder digiKam Sterne vergeben wurden,
  ist das das mit Abstand beste Signal im ganzen Verfahren. Vorgabe: aus.
- **Kein verwertbarer Zeitstempel** (`time_source` in `mtime`, `none`) — diese
  Bilder haben keine Position auf der Zeitachse, Traube und Tagesquote sind für
  sie sinnlos. Eigener Block `ohne-datum` am Ende, vollständig auskommentiert,
  mit Hinweis. Sie einzureihen ist Handarbeit und soll es bleiben.

### 4.7 Vielfalt als Nachkorrektur

Hochformatanteil und Brennweitenverteilung sind Eigenschaften der *fertigen*
Auswahl, nicht einzelner Bilder. Sie gehören deshalb nicht in die Punktzahl
(vier gewichtete Ziele in einer Schleife kann hinterher niemand mehr erklären),
sondern hinterher:

Reißt der Hochformatanteil `--max-portrait` (Vorgabe 0,3), wird gezielt
getauscht — das schwächste Hochformatbild gegen das beste Querformat derselben
Traube, sonst desselben Tages. Der Tausch wird gemeldet und ist damit
nachvollziehbar. Kommt kein Ersatz zustande, bleibt es, wie es ist, und die
Meldung sagt es.

Dasselbe Muster ließe sich auf Brennweiten anwenden (200 Bilder bei 24 mm sind
monoton). **Zurückgestellt** bis Stufe 3 — erst zeigen, dass es stört.

### 4.8 Zufall und Reproduzierbarkeit

Ohne inhaltliche Analyse ist „das beste Bild" nicht bestimmbar. Ein
deterministisches Verfahren macht dann nicht keinen Fehler, sondern immer
denselben. Die gewichtete Ziehung erlaubt stattdessen zwei Vorschläge zum
Vergleichen.

- `--seed` steuert alles Zufällige. Ohne Angabe wird einer gezogen und **in den
  Dateikopf geschrieben** — sonst ist ein gefallener Vorschlag unwiederbringlich.
- Derselbe Seed auf demselben Manifest ergibt dieselbe Auswahl, auch nach einem
  `probe`, der weitere Bilder hinzufügt? **Nein**, und das darf auch nicht
  behauptet werden: neue Bilder ändern Trauben und Quoten. Für Stabilität gibt
  es `order --update`, das die Datei nachpflegt statt neu zu würfeln.
- Der Zufallsgenerator ist ein eigener `random.Random(seed)`, nie das
  Modul-globale `random` — sonst hängt das Ergebnis daran, wer vorher gezogen
  hat.

---

## 5. Der Kontaktbogen

`slideshow sheet` erzeugt `contact.html` — die Antwort auf das Nicht-Ziel
„keine inhaltliche Analyse". Die macht der Mensch, und dafür muss er die Bilder
sehen.

Ohne ihn ist die Auswahl eine Liste von 187 IDs, die niemand beurteilen kann.
Mit ihm dauert die Durchsicht von 1240 Bildern zehn Minuten.

### 5.1 Gestalt

Nach Tagen gegliedert, ein Abschnitt je Block aus `order.yaml`. Innerhalb des
Tages die Trauben als zusammenhängende Kachelgruppen in zeitlicher Folge:

```
  Tag 1 · 24. Juli · 112 Aufnahmen · 38 Trauben · 14 gewaehlt
  ┌──────────┐ ┌────┐┌────┐┌────┐   ┌──────────┐  ┌────┐┌────┐
  │  gewählt │ │    ││    ││    │   │  gewählt │  │    ││    │
  │          │ │    ││    ││    │   │          │  │    ││    │
  └──────────┘ └────┘└────┘└────┘   └──────────┘  └────┘└────┘
   img_DSC06273  ·06271 ·06272 ·06274  img_DSC06288   ·06289 ·06290
   10:14                                10:41
```

- **Gewählt** groß und in voller Sättigung, die Geschwister klein und matt.
- **Unter jeder Kachel die Medien-ID** — sie ist der Griff, mit dem man in
  `order.yaml` tauscht, und deshalb muss sie lesbar dastehen, nicht im Tooltip.
- Ausgelassene Trauben in derselben Reihe, aber ohne großes Bild — man sieht,
  *dass* dort etwas übersprungen wurde, und was.
- Harte Ausschlüsse mit ihrem Grund als Badge (`1024×768`).
- Kopfzeile: Zielzahl und woher sie kommt, Seed, Parameter, Quote je Tag als
  kleines Balkenbild.

### 5.2 Thumbnails ohne Vollbild-Decode

Der Bogen darf nicht das werden, was er verhindern soll — 1240 Bilder auf 4K zu
skalieren, um sie anzusehen, wäre absurd.

**Eingebettete EXIF-Vorschau zuerst.** JPEG und praktisch jedes RAW tragen ein
fertiges Vorschaubild im Header; `exiftool -b -PreviewImage` bzw.
`-ThumbnailImage` holt es ohne jede Decodierung heraus, in einem Batchlauf über
alle Dateien. Größenordnungen schneller als Skalieren, und für 320 px reicht es
allemal. Nur wo keine Vorschau liegt, wird über ffmpeg skaliert.

Ablage in `cache/thumbs/<id>.jpg`, inkrementell — ein zweiter Lauf erzeugt nur
Fehlendes. Das HTML verweist relativ dorthin. **Keine `data:`-URIs**: 1240
Thumbnails wären ~27 MB Base64 in einer Datei, die kein Editor mehr öffnet.
Der Bogen ist eine Projektdatei, keine E-Mail-Anlage.

`loading="lazy"` auf jeder Kachel — sonst dekodiert der Browser 1240 Bilder
beim Öffnen.

### 5.3 Was der Bogen nicht tut

**Er schreibt nichts.** Kein Klick verändert `order.yaml`. Die Datei ist die
Wahrheit; ein Browser, der sie im Rücken der Kommandozeile ändert, erzeugt die
zweite. Stattdessen: ein Klick markiert einen Tausch, und ein Knopf legt die
fertigen YAML-Zeilen in die Zwischenablage — einfügen macht der Mensch.

Das ist bewusst eine Handbewegung mehr. Sie ist der Preis dafür, dass man den
Zustand des Projekts immer in `order.yaml` ablesen kann.

Kein Server, kein Build-Schritt, keine externe Bibliothek: eine HTML-Datei mit
eingebettetem CSS und ~50 Zeilen JavaScript für Markieren und Kopieren. Der
Bogen muss in fünf Jahren noch aufgehen.

---

## 6. Entscheidungen

### Entscheidung 1 — `order.yaml`, keine eigene `select.yaml`

Eine Auswahl *ist* eine Reihenfolge mit `rest: drop`. Eine eigene Datei
bräuchte ein zweites Format, eine zweite Auflösung, eine zweite
Fehlerbehandlung — und vor allem eine Regel dafür, was gilt, wenn beide Dateien
sich widersprechen. Die gibt es dann nicht, sondern die Widersprüche.

Der Preis: `select` und `order` schreiben dieselbe Datei und müssen sich beim
Überschreiben genauso vorsichtig verhalten wie `order` heute (`cli.py:715`:
Datei da → `--update` oder `--force`).

### Entscheidung 2 — Abgewähltes bleibt in der Datei, nicht nur im Bericht

1053 Kommentarzeilen sind viel. Die Alternative — nur die 187 in die Datei, der
Rest in einen Bericht — wäre kürzer und falsch: ein anderes Bild zu nehmen
hieße dann, in einer zweiten Datei nachzuschlagen und eine ID abzutippen. Der
ganze Nutzen des Formular-Musters entfällt.

Deshalb: alle Geschwister der gewählten Trauben vollständig, ausgelassene
Trauben mit einem Vertreter je Traube. Bei 1240 Bildern sind das rund 500
Zeilen — lesbar, weil sie an ihrem zeitlichen Platz stehen.

### Entscheidung 3 — Die Zielzahl kommt aus `beats.yaml`

`--count 200` einzutippen heißt, die Rechnung im Kopf zu machen, die
`_region_capacity` schon kann. `auto` ist die Vorgabe; fehlt `beats.yaml`, ist
`--count` Pflicht und der Fehler sagt das.

Folge: `select` steht **nach** `beats` im Ablauf. Das ist eine echte
Reihenfolgeabhängigkeit, und `_naechster_schritt` (`cli.py:982`) muss sie
kennen.

### Entscheidung 4 — Auswahlparameter sind Generatorparameter, nicht `Defaults`

Architektur-Invariante 1 sagt: neue Einstellungen gehören nach `Defaults`, nicht
in CLI-Argumente. Sie gilt hier nicht, und zwar aus demselben Grund, aus dem sie
für `order --by` und `chapters --gap` nicht gilt: **diese Werte wirken einmal
und materialisieren sich in einer Datei.** Nach dem Lauf ist die Auswahl die
Wahrheit, nicht der Parameter, der zu ihr geführt hat. Ein `--day-weight` in
`Defaults` würde behaupten, es wirke beim Rendern — das tut es nie.

Protokolliert werden sie trotzdem, im Dateikopf. Ohne das ist ein Lauf nicht
wiederholbar.

### Entscheidung 5 — Trauben trennen an Zeit, nicht an Ähnlichkeit

Das ist die Kernannahme, und sie ist angreifbar: zwei völlig verschiedene
Motive innerhalb einer Minute werden zu einer Traube, und eines fällt heraus.

Der Gegenentwurf wäre Bildähnlichkeit (Perceptual Hash, Histogramme). Er ist
ausgeschlossen — ausdrückliches Nicht-Ziel, und er kostet 1240 Volldecodes.

Der Schaden ist außerdem beschränkt: das verlorene Motiv steht als `statt:`-Zeile
direkt daneben und ist im Kontaktbogen sichtbar. Wer `--burst-gap 30` setzt,
dreht die Annahme herunter. Die Vorgabe 90 s ist ein Anfangswert und kein
Messergebnis — sie gehört nach dem ersten echten Durchlauf überprüft.

### Entscheidung 6 — Quote auf Trauben, nicht auf Bilder

Siehe 4.3. Der Unterschied ist groß und leicht zu übersehen: auf Bildern
gerechnet gewinnt der Serienknipser zweimal — einmal, weil er viele Bilder hat,
und einmal, weil die Traubenregel ihm die Auswahl bereits abgenommen hat.

### Entscheidung 7 — Der Kontaktbogen schreibt nicht

Siehe 5.3. Erklärt sich aus Grundprinzip „eine Wahrheit". Der Rückweg über die
Zwischenablage ist die Handbewegung, die den Zustand nachvollziehbar hält.

### Entscheidung 8 — `preprocess` liest `order.yaml`

Ohne das entfällt der halbe Gewinn (Punkt 1 der Ausgangslage). Umgesetzt nach
dem Muster von `build`: ausdrücklich genannter Pfad ist Pflicht, gefundene Datei
ist Bequemlichkeit (`cli.py:765` `_load_order`).

**`rest:` gilt dabei genauso.** Ein `rest: error` lässt `preprocess` genauso
abbrechen wie `build` — sonst normalisiert man eine Stunde lang Material, das
`build` fünf Minuten später verweigert.

`--all` normalisiert trotzdem alles, für den Fall, dass man die Auswahl noch
mehrfach umwerfen will und den Cache warm haben möchte.

### Entscheidung 9 — Was ausdrücklich nicht dazugehört

- **Keine Schärfe-, Gesichts- oder Inhaltserkennung.** Nicht-Ziel, ausdrücklich
  bestätigt. `size_bytes` ist die Grenze dessen, was ohne Dateizugriff geht, und
  es wird nur als Tiebreak innerhalb einer Traube benutzt.
- **Kein Nachziehen der Zielzahl beim Rendern.** Ist die Musik nachher doch
  länger, wird `select` erneut gerufen — nicht heimlich nachgeholt.
- **Kein Lernen aus früheren Auswahlen.** Verlockend („er nimmt immer die
  Hochformate raus"), aber es macht das Verfahren unerklärbar und den Lauf
  nicht mehr reproduzierbar.
- **Keine Brennweiten-Vielfalt in Stufe 1** (4.7).

---

## 7. Vorgeschlagene Umsetzung

**Stufe 0 — Vorbedingungen** *(ohne die trägt nichts)*

1. **`read_exif_batch` auf `-@ argfile`** (`probe.py:288`). Pfade in eine
   temporäre UTF-8-Datei, `exiftool -@ liste.txt` statt der Pfade auf der
   Zeile. Dazu `-charset filename=utf8`, sonst fallen Umlaute in Ordnernamen
   heraus. Test: 1200 erzeugte Pfade laufen durch, die Kommandozeile bleibt
   unter 4000 Zeichen (heute: Abbruch ab 193 Dateien, siehe Abschnitt 2).
2. **`proc.run` darf WinError 206 nicht als „Programm nicht gefunden" ausgeben**
   (`proc.py:72`). Der `except OSError`-Zweig deutet jeden `OSError` zu einem
   fehlenden Programm um. Eine zu lange Argumentliste (`errno.E2BIG`, unter
   Windows `winerror == 206`) bekommt eine eigene Meldung, die sagt, was los
   ist. Das kostet vier Zeilen und wirkt für jeden Aufrufer, nicht nur für
   exiftool — `ffmpeg` mit einer langen Concat-Liste läuft in dieselbe Falle.
3. **Leeres exiftool-Ergebnis ist kein Normalfall.** Heute nur `log.warning`
   (`probe.py:298`), und `capture_time` fällt danach still auf `mtime` zurück —
   ein Manifest ohne die Zeitstruktur, auf der dieses ganze Briefing aufbaut.
   Nach Punkt 1 tritt der Fall zwar nicht mehr aus diesem Grund ein, aber er
   bleibt möglich (kaputte Installation, Rechte). Bei mehr als, sagen wir, 20
   Bildern ohne einen einzigen EXIF-Treffer soll `probe` abbrechen.

**Stufe 1 — Auswahl**

4. **`_EXIF_TAGS` erweitern** (`probe.py:282`): `-Rating`, `-Label`,
   `-ExposureTime`, `-FocalLengthIn35mmFormat`, `-FocalLength`, `-ISO`. Dazu
   die Felder in `MediaItem`/`ImageInfo` (`models.py:57`, `models.py:108`).
   Manifest-Version anheben. Alle Felder optional mit Vorgabe — ein altes
   Manifest muss weiter laden.
5. **`planner.py`: `slot_capacity(regions, defaults, *, reserve=0) -> int`** —
   öffentliche Summe über `_region_capacity` (`planner.py:675`). Keine neue
   Rechnung, nur ein Name für eine vorhandene.
6. **`select.py` (neu), reine Rechnung ohne Datei-I/O** — dieselbe Aufteilung
   wie `order.py` und `titles.py`:
   - `bursts(media, offsets, *, gap, max_span) -> list[Burst]` (4.2)
   - `day_quota(bursts_je_tag, total, *, alpha, floor, cap) -> dict` (4.3),
     Sitzverteilung nach größtem Rest
   - `spread(bursts, n, rng) -> list[Burst]` (4.4)
   - `pick_in_burst(burst, rng) -> MediaItem` (4.5)
   - `hard_filter(media, ...) -> tuple[list, dict[str, str]]` (4.6), der Grund
     je ausgeschlossener ID
   - `balance_portrait(auswahl, ...) -> tuple[list, list[str]]` (4.7)
   - `select_media(manifest, ...) -> Selection` — die Klammer; `Selection` hält
     Auswahl, Trauben, Gründe und Meldungen
   - `dump_selection_yaml(selection, manifest) -> str` (3.1) — von Hand
     geschrieben, wie `dump_order_yaml`
7. **`cli.py`: `slideshow select`** mit den Schaltern aus 3.2, `cmd_select` nach
   dem Muster von `cmd_order` (`cli.py:690`). Bericht: gewählt/gesamt, Quote je
   Tag, harte Ausschlüsse gezählt nach Grund, Seed.
8. **`_naechster_schritt`** (`cli.py:982`) kennt `select` — nach `beats`, vor
   `preprocess`, und nur dann vorgeschlagen, wenn das Material die Kapazität
   deutlich übersteigt.

**Stufe 2 — `preprocess` folgt der Auswahl**

9. **`preprocess(project, manifest, *, only=None)`** (`preprocess.py:442`):
   `only` ist eine ID-Menge. Fehlt sie, ändert sich nichts.
10. **`cmd_preprocess`** (`cli.py:329`) lädt `order.yaml` über `_load_order`,
    löst über `resolve_order` auf, reicht die IDs durch. `--all` hebt es auf.
    Berichtszeile: `Auswahl: order.yaml (187 von 1240 Medien)`.

**Stufe 3 — Kontaktbogen**

11. **`sheet.py` (neu):** `thumbnails(project, media, *, size) -> dict[str, Path]`
    — EXIF-Vorschau im Batch, ffmpeg nur als Rückfall, inkrementell nach
    `cache/thumbs/`. Dazu `dump_sheet_html(selection, thumbs, manifest) -> str`.
12. **`slideshow sheet`** in `cli.py`, plus `select --sheet` als Abkürzung.
13. **Markieren und Kopieren** im HTML (5.3) — ~50 Zeilen JavaScript, kein
    Framework.

**Stufe 4 — nur wenn es sich zeigt**

14. Brennweiten-Vielfalt (4.7).
15. `--by place` mit echter Clusterung statt der Sprungheuristik.
16. Ähnlichkeit über eingebettete Vorschaubilder — die liegen für den
    Kontaktbogen ohnehin in `cache/thumbs/`, ein Histogrammvergleich darauf
    wäre billig. Berührt aber das Nicht-Ziel und braucht eine eigene
    Entscheidung.

**Nicht anzufassen:** `planner.py` außer der einen neuen Funktion, `render.py`,
`kenburns.py`, `beats.py`, `encoders.py`, `build.py`, die Concat- und
Muxing-Kette. Die Auswahl endet in `order.yaml`; alles danach kennt sie nicht.

---

## 8. Betroffene Stellen

| Ort | Rolle | Änderung |
|---|---|---|
| `select.py` (neu) | Auswahlverfahren | Trauben, Quote, Spreizung, Formular |
| `sheet.py` (neu) | Kontaktbogen | Thumbnails, HTML |
| `probe.py:288` | `read_exif_batch` | `-@ argfile` (Stufe 0) |
| `proc.py:72` | `except OSError` | WinError 206 / `E2BIG` nicht als „Programm nicht gefunden" melden |
| `probe.py:298` | leeres Ergebnis | Abbruch statt Warnung |
| `probe.py:282` | `_EXIF_TAGS` | Rating, Belichtung, Brennweite, ISO |
| `models.py:57` | `ImageInfo` | neue EXIF-Felder |
| `models.py:108` | `MediaItem` | dito; Manifest-Version |
| `planner.py:675` | `_region_capacity` | `slot_capacity` als öffentliche Summe |
| `preprocess.py:442` | `preprocess` | `only=`-Filter |
| `cli.py:329` | `cmd_preprocess` | `order.yaml` laden, `--all` |
| `cli.py:690` | `cmd_order` | Vorbild für `cmd_select` |
| `cli.py:765` | `_load_order` | wird von `preprocess` mitbenutzt |
| `cli.py:792` | `_print_coverage` | Sprache für die Deckungsmeldung |
| `cli.py:982` | `_naechster_schritt` | `select` einreihen |
| `order.py:84` | `mentioned_ids` | unverändert — trägt die Abwahl |
| `order.py:368` | `update_order_text` | unverändert — trägt das Nachpflegen |
| `docs/edit-yaml.md` | `order.yaml` | Abschnitt „Auswahl statt Sortierung" |
| `docs/rezepte.md` | | Rezept „Aus 1200 Bildern einen Film" |
| `README.md`, `CLAUDE.md` | | Ablauf, Baustellenzeile |

Tests: `tests/test_select.py`, `tests/test_sheet.py`. Fixtures mit erfundenen
Zeitstempeln — das Verfahren braucht keine echten Bilder, nur ein Manifest.

---

## 9. Abnahmekriterien

**A1 — Kommandozeilenlänge.** `read_exif_batch` mit 1200 Pfaden à 168 Zeichen
läuft durch und liefert für jeden einen EXIF-Eintrag; die tatsächliche
Kommandozeile bleibt unter 4000 Zeichen. Gegenprobe vor der Änderung: derselbe
Aufruf bricht heute ab 193 Dateien ab (gemessen). Zusätzlich: ein `run()` mit
absichtlich überlanger Argumentliste meldet die Länge, nicht ein fehlendes
Programm.

**A2 — Keine benachbarten Aufnahmen.** In der Auswahl liegen keine zwei Medien
weniger als `burst-gap` auseinander. Ausnahmen nur bei Gerätewechsel, und die
werden gezählt gemeldet.

**A3 — Quote trifft.** Bei 30 Tagen mit stark ungleichem Material (5 bis 400
Aufnahmen) und α = 0,5 bekommt kein Tag mit Material null Bilder, keiner mehr
als `max-share`, und die Summe ist exakt `--count`.

**A4 — Spreizung.** Innerhalb eines Tages liegt der Variationskoeffizient der
Abstände zwischen gewählten Aufnahmen unter dem einer gleich großen
Zufallsstichprobe — gemessen über 100 Seeds auf einer Fixture.

**A5 — Reproduzierbar.** Zweimal `select --seed 4711` auf demselben Manifest
ergibt zeichengleiche Dateien. Zwei verschiedene Seeds ergeben Auswahlen, die
sich in mindestens 20 % der IDs unterscheiden — sonst wirkt der Zufall nicht.

**A6 — Rundlauf.** `select` → `build` → `render --preview` läuft ohne Warnung
über Über- oder Unterdeckung durch. Das ist der eigentliche Test: die Zielzahl
aus 4.1 muss zu dem passen, was der Planer später wirklich vergibt.

**A7 — Nachpflegen.** `probe` mit 50 zusätzlichen Bildern, dann
`order --update`: die Auswahl bleibt unverändert, die abgewählten Bilder werden
nicht erneut angeboten, die 50 neuen stehen im Block `neu`.

**A8 — `preprocess` folgt.** Bei 1240 Medien und 187 gewählten entstehen 187
Cache-Einträge. Mit `--all` 1240.

**A9 — Kontaktbogen.** 1240 Thumbnails aus einem Manifest ohne einen einzigen
Volldecode, wenn EXIF-Vorschauen vorliegen. Die HTML-Datei bleibt unter 2 MB.
Sie öffnet ohne Netzzugriff und ohne lokalen Server.

**A10 — Sichtprüfung.** Ein realer Durchlauf über einen echten Bilderordner mit
mehr als 1000 Aufnahmen, Kontaktbogen angesehen, Urteil notiert: Wirkt die
Auswahl wie eine Auswahl oder wie ein Zufallsgriff? Dieses Kriterium lässt sich
nicht automatisieren und ist trotzdem das wichtigste.

---

## 10. Risiken

**Der `burst-gap` ist geraten.** 90 Sekunden sind ein Anfangswert ohne
Messgrundlage. Zu klein: Serienbilder überleben als eigene Trauben und die
Wiederholung landet im Film. Zu groß: eine Stunde Fotografieren wird zu einer
Traube. Der Deckel `burst_max` (4.2) fängt den zweiten Fall ab, der erste
bleibt. **Nach dem ersten echten Durchlauf nachmessen** — die Verteilung der
Zeitabstände über ein reales Sammelbecken sagt mehr als jede Überlegung, und
`sheet` zeigt sie ohnehin an.

**Die Tagesquote passt nicht zu jeder Reise.** Ein Film über einen einzigen Tag
hat genau einen Tag, und dann wirkt nur noch die Spreizung. Das ist richtig, aber
`--by day` heißt dort nichts mehr — der Fall muss gemeldet und `--by none`
vorgeschlagen werden, sonst sieht es nach einem kaputten Schalter aus.

**`size_bytes` täuscht bei gemischten Kameras.** Ein Handy-JPEG und ein
Kamera-JPEG sind nicht vergleichbar. Innerhalb einer Traube kommt beides selten
zusammen — aber wenn zwei Leute gleichzeitig fotografieren, doch. Abhilfe:
z-Wert nur über Bilder derselben Kamera *innerhalb* der Traube; bei gemischter
Traube fällt das Signal weg. Muss in der Umsetzung stehen, sonst gewinnt
stillschweigend immer die Kamera mit der höheren JPEG-Qualität.

**Der Kontaktbogen wird zur zweiten Wahrheit.** Sobald er anklickbar ist, will
man, dass er speichert. Entscheidung 7 hält dagegen; der Druck bleibt. Wenn er
irgendwann nachgibt, muss er in `order.yaml` schreiben und nirgendwo sonst.

**Manifest-Version.** Die neuen EXIF-Felder heben sie an. Ein bestehendes
`testset1/manifest.json` muss weiter laden (alle Felder optional) — sonst ist
das reale Testprojekt nach dem Merge kaputt, und das fällt erst beim nächsten
Render auf.

**Die Auswahl kann gut aussehen und trotzdem falsch sein.** Sie kennt keine
Bildinhalte. Ein technisch tadelloses, gleichmäßig gespreiztes Ergebnis kann
ausgerechnet die drei Bilder auslassen, um die es in dem Film geht. Deshalb ist
der Kontaktbogen Teil dieses Briefings und nicht ein späteres Extra: das
Verfahren ist ein **Vorschlag**, und der Mensch muss ihn in vertretbarer Zeit
prüfen können.
