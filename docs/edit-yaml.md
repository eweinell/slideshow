# `edit.yaml` — Referenz

> Diese Seite erklärt die **Schlüssel**. Wer stattdessen einen fertigen Ablauf
> für seinen Fall sucht — thematisch sortieren, auswählen, Nachschub
> einpflegen —, findet ihn in [`rezepte.md`](rezepte.md).

Die Edit-List ist die Single Source of Truth. Jeder Renderpfad leitet sich aus
ihr ab, nie umgekehrt; `render` liest ausschließlich diese Datei plus das
Manifest. Sie ist bewusst menschenlesbar und von Hand editierbar.

Sie hält **Absicht**, keine aufgelösten Zeitstempel: `beats: 8` statt
`start: 12.334`. Die absoluten Framegrenzen entstehen bei jedem Lauf neu aus
derselben deterministischen Funktion, damit `build` und `render` garantiert
dasselbe sehen. Deshalb steht in der Datei auch nirgends ein Startzeitpunkt
eines Segments — er ergibt sich aus allen vorhergehenden.

> **`build` schreibt die Datei neu** — und merkt inzwischen, wenn dabei
> Handarbeit verlorenginge: es bricht ab und verweist auf
> [`slideshow overrides`](#overridesyaml--der-feinschliff), das den Feinschliff
> in eine Eingabedatei holt, die den Neubau übersteht. `--force` schreibt
> trotzdem. Wer nur diesen einen Schnitt sehen will, kommt weiterhin mit einer
> Kopie und `slideshow render meine-fassung.yaml` aus — sie ist dann aber ein
> Seitenzweig, in den kein nachgereichtes Bild mehr hineinfindet.

Unbekannte Schlüssel sind ein **Fehler**, kein stiller Ignorierfall: ein
vertipptes `still_secnods` bricht mit Pfad und Zeile ab, statt später einen
falsch getakteten Master zu bauen.

---

## Aufbau

```yaml
version: 2                    # Schemaversion, muss 2 sein
fps: 60.0                     # Zielframerate der Timeline
size: [3840, 2160]            # Ausgabeauflösung in Pixeln

audio: {...}                  # Tonspur und Regionenkarte
defaults: {...}               # gelten für alle Segmente
segments: [...]               # die Abfolge selbst
```

### Kopf

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `version` | int | `2` | Andere Versionen werden abgelehnt, nicht geraten. |
| `fps` | float | `60.0` | Muss zwischen 1 und 240 liegen. Alle Framegrenzen beziehen sich darauf; eine Änderung verschiebt sämtliche Schnitte. |
| `size` | [int, int] | `[3840, 2160]` | Breite × Höhe des Masters. |

---

## `audio`

```yaml
audio:
  file: cache/mix.flac
  duration: 56.097143
  regions:
    - {type: free, start: 0.0, end: 56.097143, reason: "niedrige Rhythmus-Konfidenz"}
    - {type: beat, start: 56.1, end: 92.0, bpm: 120.0, offset: 56.35, conf: 0.81}
```

| Schlüssel | Bedeutung |
|---|---|
| `file` | Projektrelativer Pfad zur Tonspur. **Leer = stummer Film**; der Master bekommt dann gar keine Tonspur. |
| `duration` | Länge der **Timeline**, nicht zwingend der Tonspur. Weichen beide ab, wird der Ton beim Muxen gekürzt oder mit Stille aufgefüllt. |
| `regions` | Die Regionenkarte aus `beats.yaml`, mitkopiert, damit die Edit-List für sich allein lesbar bleibt. |

### Regionen

Eine Region ist entweder `beat` (mit Raster) oder `free` (ohne). Sie müssen die
gesamte `duration` lückenlos und überlappungsfrei abdecken — `render` prüft das
und bricht sonst ab.

| Schlüssel | Gilt für | Bedeutung |
|---|---|---|
| `type` | beide | `beat` oder `free`. |
| `start`, `end` | beide | Absolute Zeitpunkte in Sekunden. |
| `bpm` | `beat` | Tempo. Pflicht in einer Beat-Region. |
| `offset` | `beat` | Phasenreferenz — Zeitpunkt eines Beats, nicht zwingend innerhalb der Region. Rasterpunkte liegen auf `offset + k × 60/bpm`. |
| `conf` | `beat` | Konfidenz der Erkennung, 0…1. Rein informativ. |
| `reason` | `free` | Warum kein Raster: `stille`, `niedrige Rhythmus-Konfidenz`, … |
| `quiet` | `free` | `true` nur bei **echter Stille**. Steuert die `hold_seconds`-Regel: nur eine stille Region bekommt ein einzelnes ruhiges Bild. Läuft Musik ohne erkanntes Raster, muss das `false` sein, sonst steht ein Bild über die ganze Region. Fehlt der Schlüssel, wird er aus `reason == "stille"` abgeleitet. |
| `beats_per_still` | `beat` | Überschreibt `defaults.beats_per_still` für **diese** Region. |
| `still_seconds` | `free` | Überschreibt `defaults.still_seconds` für **diese** Region. |

---

## `defaults`

Gelten für jedes Segment, das nichts Eigenes sagt.

### Takt und Dauer

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `beats_per_still` | int | `8` | Beats je Standbild in einer Beat-Region. Kleiner = schnellerer Schnitt. |
| `still_seconds` | float | `4.0` | Standzeit je Bild in einer `free`-Region — der Takt, wenn es kein Raster gibt. Auch der Takt für den stummen Teil hinter dem Tonende. |
| `still_tolerance` | [float, float] | `[3.0, 6.0]` | Erlaubtes Band, in dem eine `free`-Region **exakt** gefüllt werden darf. Die Region wird in gleich lange Slots geteilt; passt keine Anzahl ins Band, gewinnt die nächstbeste. `min` muss < `max` sein. |
| `min_still_seconds` | float | `still_tolerance[0]` | Kürzeste Standzeit, die ein eigenes Bild rechtfertigt — siehe [Der Rest am Regionsende](#der-rest-am-regionsende). `0` schaltet die Regel ab. |
| `hold_seconds` | float | `12.0` | Eine **stille** Region länger als das trägt bewusst *ein* ruhiges Bild statt vieler Wechsel. Greift nur bei `quiet: true`. |
| `snap_back` | bool | `true` | Nach einem `dur:`-Override auf den nächsten Beat aufrunden, damit der Sync danach wieder steht. Der Versatz bleibt so auf genau einen Schnitt begrenzt. |
| `clip_snap_tol` | float | `1.0` | Wie weit (in Beats) der Out-Punkt eines Clips maximal auf das Raster gezogen wird. |
| `fade_out` | float | `1.5` | Ausblende am Filmende in Sekunden, Bild nach Schwarz und Ton nach Stille, gleichzeitig. `0` schaltet sie ab. Kann nicht länger sein als das letzte Segment; darüber hinaus wird sie gekürzt. |

### Bild

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `portrait` | enum | `blur` | Behandlung von Hochformat: `blur` (weichgezeichneter Hintergrund), `black` (schwarze Balken), `crop` (formatfüllend beschnitten). |

### `defaults.kb` — Ken Burns

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `zoom_rate` | float | `0.05` | Zoom **pro Sekunde**. Der Gesamtzoom ergibt sich aus der Dauer, nicht umgekehrt — so bewegen sich lange und kurze Bilder gleich schnell. |
| `zoom_total` | [float, float] | `[0.08, 0.30]` | Klemmung des Gesamtzooms. Verhindert, dass ein sehr langes Bild bis zur Unkenntlichkeit hineinfährt. `min` muss ≤ `max` sein. |
| `pan_rate` | float | `0.03` | Schwenkweg **pro Sekunde**, normalisiert auf die Bildkante. Dieselbe Regel wie beim Zoom — aber nur eine Obergrenze: wirksam wird höchstens, was der Zoom hergibt (siehe unten). |
| `pan_total` | [float, float] | `[0.05, 0.18]` | Klemmung des Gesamt-Schwenkwegs. |
| `pan_anchor` | enum | `center` | Wo der Schwenk die Bildmitte berührt: `center` am ruhenden Ende (Hineinzoom fängt dort an, Herauszoom hört dort auf), `through` symmetrisch um die Mitte, also mittendurch. `through` ist die frühere Auslegung und hat einen sichtbaren Richtungswechsel — siehe unten. |
| `ease` | enum | `smoothstep` | `smoothstep` (weich an- und abbremsend) oder `linear`. |
| `alternate` | bool | `true` | Zoomrichtung wechseln lassen. Hundertmal hineinzoomen ermüdet. Der Wechsel ist **statistisch, nicht streng abwechselnd** — siehe unten. `false` zoomt immer hinein. |
| `engine` | enum | `zoompan` | `zoompan` ist schnell, rechnet aber **8-bittig** — ffmpeg schiebt eine Konvertierung davor. `scale16` rechnet durchgehend in 16 Bit, kostet mehr CPU. Bei sichtbarem Banding in Himmelsverläufen umstellen. |
| `motion` | enum | `kenburns` | Kamerafahrt überhaupt. `none` lässt **jedes** Bild stillstehen, Titelfolien eingeschlossen — der filmweite Schalter. Über die Raten ließe sich dasselbe ausdrücken, aber nur mit vier Werten, von denen zwei Klemmungen sind: `zoom_total: [0.08, …]` schaltete die Fahrt still wieder ein. |

#### Rate und Klemmung — wo die Rate wirklich gilt

Beide Bewegungen folgen derselben Formel:

```
Betrag = clamp(rate × Dauer, min, max)
```

Die Rate ist damit ein **Sollwert innerhalb eines Dauerfensters**, kein
Festwert. Außerhalb gewinnt die Klemmung, und die effektive Geschwindigkeit
weicht ab. Mit den Vorgaben reicht das Fenster von **1,6 s** (Zoom) bzw.
**1,7 s** (Schwenk) **bis 6,0 s**:

| Dauer | Gesamtzoom | eff. Zoomrate | Deckel `0,5 − 1/(2z)` | Schwenkweg | eff. Schwenkrate |
|---|---|---|---|---|---|
| 1,0 s | 8,0 % | 0,080 (1,6×) | 0,037 | 0,037 | 0,037 (1,2×) |
| 2,0 s | 10,0 % | 0,050 (1,0×) | 0,045 | 0,045 | 0,023 (0,8×) |
| 4,0 s | 20,0 % | 0,050 (1,0×) | 0,083 | 0,083 | 0,021 (0,7×) |
| 6,0 s | 30,0 % | 0,050 (1,0×) | 0,115 | 0,115 | 0,019 (0,6×) |
| 12,0 s | 30,0 % | 0,025 (0,5×) | 0,115 | 0,115 | 0,010 (0,3×) |
| 28,0 s | 30,0 % | 0,011 (0,2×) | 0,115 | 0,115 | 0,004 (0,1×) |

**Beim Schwenk gewinnt mit den Vorgaben durchgehend der Deckel**, nicht die
Rate: mehr als `0,5 − 1/(2z)` gibt der Bildrand nicht her, und der Zoom endet
bei 1,30. `pan_rate` und `pan_total` sind damit eine Obergrenze, die praktisch
nie greift — wer **mehr** Schwenk will, hebt `zoom_total`, wer **weniger** will,
senkt `pan_rate`. Dieselbe Rechnung ohne Deckel steht unter `pan_anchor:
through`; dort wären es 0,060 / 0,120 / 0,180 — sichtbar davon war aber auch
dort nur etwa die Hälfte.

Wer mit langen Standzeiten arbeitet — etwa `--still-seconds 28` —, dreht
deshalb an `zoom_total`/`pan_total`, **nicht** an den Raten: die haben dort
keine Wirkung mehr.

Zwei weitere Feinheiten:

- **Die Rate ist ein Mittelwert, kein Momentanwert.** Mit `ease: smoothstep`
  läuft der Fortschritt als `p²(3−2p)`; in der Bildmitte bewegt es sich
  **1,5×** so schnell wie im Mittel, an Anfang und Ende steht es still. Eine
  echte Konstante ist die Rate nur bei `ease: linear`.
- **`duration` ist die volle sichtbare Spanne**, exklusiver Anteil plus die
  angrenzenden Übergangshälften — nicht die Slotlänge aus `timeline.json`.

#### Schwenk: Richtung und Reichweite

Der Schwenk hat **ein ruhendes Ende in der Bildmitte**: beim Hineinzoomen fängt
er dort an, beim Herauszoomen hört er dort auf (`pan_anchor: center`). Die
Richtung wird deterministisch aus acht auf Länge 1 normierten Vektoren
gewählt — alle acht legen denselben Weg zurück.

Der Grund steht im Bildrand. Der Ausschnitt hat bei Zoom `z` die Breite `1/z`,
seine Mitte darf sich also nur innerhalb von `0.5 ± (0.5 − 1/(2z))` bewegen.
**Bei `z = 1,0` ist das exakt null** — der Ausschnitt *ist* das ganze Bild, und
der Filter zieht die Mitte dort auf 0,5. Wer den Schwenk dort anfangen lässt,
wo der Zoom ihn ohnehin festnagelt, verliert nichts.

Genau daran krankte die frühere Auslegung `through` (symmetrisch um die Mitte,
von `0.5 − a` nach `0.5 + a`): die sichtbare Mitte wanderte zuerst *mit der
aufgehenden Klemmung* nach außen, während der geplante Schwenk längst zur
Gegenseite unterwegs war — und kippte, sobald die Klemmung ihn freigab. Gemessen
an den Vorgaben über 5 s lief die sichtbare Mitte 0,500 → 0,526 → 0,447: **ein
Zoom, aber zwei Schwenks.** Der Schlüssel bleibt erhalten, damit bestehende
Projekte bitgleich weiterrendern.

Damit die ganze Bahn innerhalb der Klemmung liegt, wird der Weg auf
`0,5 − 1/(2z)` des größten Zooms gedeckelt. Dann ist der geplante Weg auch der
sichtbare — nichts verpufft mehr am Bildrand, und ein Zoom, der bei 1,0 anfängt,
gibt eben nur diese Strecke her. Mehr Schwenk heißt deshalb: mehr Grundzoom
(`zoom_total`), nicht mehr `pan_rate`.

Gedeckelt wird die **Strecke**, nicht die Auslenkung je Achse. Eine Diagonale
dürfte je Achse so weit ausschlagen wie eine Gerade und legte damit das
1,41-fache zurück — genau der Unterschied, den die normierten Richtungsvektoren
beseitigen sollen.

Einen reinen Schwenk **ohne** Zoom gibt es deshalb nur mit konstantem Zoom über
1,0, und den setzt man am Segment:

```yaml
- {type: still, src: cache/img_1.jpg, kb: {z: [1.20, 1.20], c: [0.425, 0.5, 0.575, 0.5]}}
```

Ein `kb: {z: …}` am Segment wird beim Deckeln mitgerechnet: die Bewegung plant
gegen den Zoom, den dieses Bild wirklich hat.

#### Woran die Richtung hängt

An der **Kennung des Bildes** (seinem `src`), nicht an seiner Position. Das ist
der Unterschied zwischen einem billigen und einem teuren Eingriff: hinge sie am
Segmentindex, verschöbe ein eingefügtes Segment die Bewegung jedes folgenden
Bildes, damit dessen Cache-Key, damit rendert der halbe Film neu — obwohl sich
an ihm nichts geändert hat. Über die Kennung sind Einfügen, Löschen und
Umsortieren dauerhaft billig.

Zwei Folgen, die man kennen sollte:

- **Der Zoomwechsel ist statistisch.** Das unterste Bit der Kennung entscheidet,
  ob hinein- oder herausgezoomt wird. Über eine ganze Bildmenge ist das
  ausgeglichen, aber es kommen ein paar gleiche Richtungen hintereinander vor —
  bei 40 Bildern typischerweise bis zu vier. Strenge Alternierung wäre nur über
  die Position zu haben, und die ist genau das, was hier aufgegeben wird.
- **Dasselbe Bild zweimal im Film bewegt sich beide Male gleich.** Bei einer
  bewussten Wiederholung ist das eher erwünscht; wer es anders will, setzt `kb:`
  am zweiten Vorkommen.

Die Kennung wird mit `blake2b` gehasht, nicht mit Pythons `hash()` — der ist für
Strings je Prozess gesalzen und lieferte bei jedem Lauf andere Bewegungen.

> **`pan_amount` (veraltet).** Der frühere Schlüssel war eine *feste*
> Auslenkung ohne Dauerbezug. Er wird weiterhin gelesen und verlustfrei nach
> `pan_total: [2 × pan_amount, 2 × pan_amount]` übersetzt — eine Klemmung mit
> gleichen Grenzen liefert immer denselben Weg. Dazu setzt er `pan_anchor:
> through`: eine Datei, die diesen Schlüssel noch nennt, ist älter als die neue
> Auslegung und meint die alte, und so rendert sie bitgleich weiter. Wer beides
> schreibt, bekommt beides. Für dauerabhängige Schwenks den Schlüssel durch
> `pan_rate`/`pan_total` ersetzen.

> **Ältere `edit.yaml` ohne `pan_amount`** kennen `pan_anchor` noch nicht und
> bekommen damit die neue Auslegung — die Bewegung geht über
> `KBMotion.fingerprint()` in den Cache-Key jedes Segments ein, ein solcher
> Lauf rendert also alles neu. Wer das nicht will, trägt `pan_anchor: through`
> in `defaults.kb` nach.

### `defaults.xfade` — Übergänge

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `auto` | bool | `true` | Automatisch zwischen alle benachbarten Segmente Blenden setzen. Harte Schnitte zwischen hundert Standbildern wirken abgehackt. **Wirkt nur in `build`**, nicht beim Laden — siehe Kasten unten. |
| `beats` | float | `1.0` | Standarddauer einer Blende in Beats (Beat-Region). |
| `dur` | float | – | Standarddauer in Sekunden. In `free`-Regionen der einzig sinnvolle Weg. |
| `mode` | string | `dissolve` | Siehe [Blendenmodi](#blendenmodi). Ein unbekannter Modus ist ein Fehler. |

Der Schnittpunkt liegt **auf** dem Beat; eine Blende der Dauer `T` belegt
`[t − T/2, t + T/2]`. Der Schnitt bleibt also auf dem Raster, die Blende ist
darüber zentriert.

> **`auto` ist eine Einstellung für `build`, keine Laufzeitregel.** Beim Laden
> einer bestehenden `edit.yaml` gilt ausschließlich, was als `xfade`-Segment in
> der Datei steht — `auto: true` fügt dort nichts nach. Das ist Absicht und die
> Kehrseite davon, dass ein gelöschtes `xfade`-Segment einen harten Schnitt
> bedeutet: würde `auto` beim Laden nachfüllen, ließe sich eine Blende nie
> entfernen. Wer alle Blenden aus der Datei löscht, bekommt also einen Film aus
> lauter harten Schnitten, nicht die Standardblenden zurück.

### `defaults.title` — Titelfolien

Gestalt und Choreografie der Titel- und Zwischenfolien. Die Zahlen sind
gerechnet, nicht geraten — die Begründungen stehen in
[`briefing-titelfolien.md`](briefing-titelfolien.md), Abschnitt 2.

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `beats` | float | `12.0` | Standzeit in einer Beat-Region. In einer `free`-Region gilt stattdessen `still_seconds` — dort steht eine Folie so lange wie die Bilder um sie herum, ohne Sonderregel. |
| `phrase_beats` | int | `8` | Länge einer musikalischen Phrase. Titel beginnen auf einem Vielfachen davon; `build` dehnt oder staucht dafür das **vorangehende** Bild. Muss ≥ 1 sein. |
| `motion` | enum | von `defaults.kb.motion` | `kenburns` fährt über die Folie wie über jedes Standbild, `none` lässt sie stillstehen. Der Text ist in die Pixel eingebrannt und fährt sonst mit — er flimmert dabei bei dünnen Schriften und liest sich stehend ruhiger. Aufgelöst wird das als gewöhnliches `kb:` am Segment, nicht im Renderer. Der Schlüssel ist für den häufigen Fall da: **Folien still, Bilder in Fahrt**. Für den umgekehrten reicht `defaults.kb.motion`. |
| `font` | string | `auto` | Pfad zur Schriftdatei. `auto` sucht plattformabhängig (Windows: Segoe UI, Arial; Linux: DejaVu Sans, Noto Sans; macOS: Helvetica). Die Umgebungsvariable **`SLIDESHOW_FONT` gewinnt immer** — dieselbe Regel wie bei `SLIDESHOW_MELT`. |
| `size` | float | `0.075` | Versalhöhe der Überschrift als Anteil der Bildhöhe. 162 px bei 2160 — auf einem 55″-Fernseher aus 3 m so groß wie eine Zeitungsschlagzeile. |
| `subtitle_scale` | float | `0.42` | Größe der zweiten Zeile, Anteil der Überschrift. |
| `blur` | float | `60.0` | Blur-Sigma des Hintergrunds, auf 7680er Basis. Derselbe Wert wie das Hochformat-Komposit — die beiden Bildsprachen müssen zusammenpassen. |
| `darken` | float | `0.55` | **Startwert** der Abdunklung. Der Generator misst die Leuchtdichte unter der Textfläche und führt den Wert in festen Schritten nach, bis der Kontrast trägt. |
| `min_contrast` | float | `4.5` | Gefordertes Kontrastverhältnis zwischen Text und Hintergrund (WCAG 2.1). Gemessen wird das **95. Perzentil** der Leuchtdichte unter der Textfläche, nicht ihr Mittel — sonst bleibt die Folie im Durchschnitt lesbar und über ihrer hellsten Stelle trotzdem nicht. Wird der Wert bis zur Untergrenze nicht erreicht, folgt eine Warnung statt stiller Unlesbarkeit. |
| `auto_candidates` | int | `5` | Wie viele Bilder am Anfang eines Abschnitts als `bg: auto` in Frage kommen. `1` schaltet die Wahl ab: dann gilt wieder allein die Position. Muss ≥ 1 sein. |
| `auto_darken_min` | float | `0.50` | Ab welcher Abdunklung das erste Bild als nicht mehr tragfähig gilt — siehe [`bg: auto` nach Tragfähigkeit](#bg-auto-nach-tragfähigkeit). |
| `safe` | float | `0.10` | Safe Area ringsum, Anteil der Kante. Überlebt TV-Overscan und einen 4:5-Beschnitt. |
| `xfade_in` | float | `1.5` | Blende **in** die Folie hinein, als Faktor auf die Standardblende. Der Film atmet in die Zäsur ein. |
| `xfade_out` | float | `1.0` | Blende **aus** der Folie heraus, ohne Fokusblende. |
| `xfade_focus` | float | `2.0` | Blende heraus, wenn der Hintergrund das Folgebild ist (Fokusblende). Länger, weil der Schärfezug Zeit braucht. |

Die drei `xfade_*`-Faktoren ändern nur die Choreografie, nicht das Bild, und
`auto_candidates`/`auto_darken_min` entscheiden nur, *welches* Bild
hinter die Folie kommt — das steht danach als `bg:` in der Datei. Alle fünf
gehen deshalb **nicht** in den Cache-Key des Titelassets ein. Alles andere
schon: eine Änderung an `size` oder `darken` erzeugt eine neue Datei.

> **Der Rechenweg steht nicht im Hash, nur die Parameter.** Ändert sich der
> Generator selbst, ohne dass ein Wert hier anders wird, merkt das niemand —
> das alte Asset gälte weiter als aktuell. Dafür gibt es `TITLE_VERSION` in
> `titles.py`, die bei jeder Änderung an Satz, Größen oder Kontrastregel
> hochgezählt wird und damit jeden Assetpfad neu vergibt. Die alte Datei bleibt
> als Waise in `cache/` liegen; das ist der Preis dafür, dass zwei Codestände
> nebeneinander bestehen können, ohne sich dieselbe Datei streitig zu machen.

---

## `segments`

Die Abfolge. Vier Typen, unterschieden über `type`.

### `still` — Standbild

```yaml
- {type: still, src: cache/img_DSC06273.jpg, hold: false}
- type: still
  src: cache/img_DSC06284.jpg
  dur: 6.5                        # dieses eine Bild länger stehen lassen
  kb: {z: [1.0, 1.25], ease: linear}
```

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `src` | string | – | Projektrelativer Pfad, üblicherweise nach `cache/`. Pflicht. Der Dateiname ohne Endung ist die [Medien-ID](#medien-ids) des Bildes. |
| `dur` | float\|string | – | Explizite Dauer in Sekunden. **Gewinnt immer.** |
| `beats` | float | – | Dauer in Beats. Nur in einer Beat-Region gültig — in einer `free`-Region **warnt** der Planer mit Angabe der Region und nimmt die Standardlänge (kein Abbruch, siehe [Präzedenz der Dauer](#präzedenz-der-dauer)). Gebrochene Werte (`1.5`) sind erlaubt. |
| `hold` | bool | `false` | Ruhiges Bild über eine lange Stille. Wird von `build` gesetzt, lässt sich aber erzwingen. |
| `snap_back` | bool | von `defaults` | Nur für dieses Segment. |
| `portrait` | enum | von `defaults` | Nur für dieses Bild. |
| `motion` | enum | von `defaults.kb.motion` | `none` lässt **dieses** Bild stillstehen — die kurze Schreibweise für das `kb:` darunter. Ein von Hand gesetztes `kb:` gewinnt. |
| `kb` | Objekt | von `defaults.kb` | Siehe unten. |

### `title` — Titel- und Zwischenfolie

Die Zäsur zwischen zwei Abschnitten: Überschrift, zweite Zeile, unscharfer
Hintergrund aus dem Material.

```yaml
- {type: title, title: Malmö, subtitle: 'Tag 11 · 24. Juli',
   bg: cache/img_042.jpg, beats: 12,
   kb: {z: [1.0, 1.06], c: [0.5, 0.5, 0.53, 0.5]}}
```

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `title` | string | – | Überschrift. **Pflicht** und nicht leer — eine Folie ohne Überschrift ist ein Fehler, kein Sonderfall. |
| `subtitle` | string | – | Zweite Zeile. `auto` in `chapters.yaml` wird beim Bauen zum Aufnahmedatum des folgenden Bildes aufgelöst und steht danach als Text hier. |
| `bg` | string | `auto` | Hintergrund: `auto` (ein Bild des neuen Abschnitts, unscharf — siehe [nach Tragfähigkeit](#bg-auto-nach-tragfähigkeit)), ein Pfad, `#rrggbb` als Farbfläche oder `none` für Text auf Schwarz. `build` schreibt den aufgelösten Wert zurück. |
| `dur` | float | – | Wie beim Standbild. Gewinnt immer. |
| `beats` | float | von `defaults.title.beats` | Wie beim Standbild — nur in einer Beat-Region gültig, in einer `free`-Region wirkungslos und gemeldet. |
| `hold` | bool | `false` | Wie beim Standbild. |
| `snap_back` | bool | von `defaults` | Wie beim Standbild. **In langer Stille setzt `build` hier `false`** — siehe unten. |
| `style` | enum | `card` | `card` ist die ganzseitige Folie. `lower-third` ist reserviert und rendert vorerst wie `card`. |
| `motion` | enum | von `defaults.title.motion` | `none` lässt **diese** Folie stillstehen. Siehe unten. |
| `kb` | Objekt | von `defaults.kb` | Wie beim Standbild. Bei einer Fokusblende schreibt `build` hier und am Folgebild gekoppelte Werte. |

Einen `src`-Schlüssel gibt es bewusst **nicht**. Der Pfad des gebackenen Assets
(`cache/title_malmoe_<hash>.jpg`) ergibt sich aus dem Inhalt des Segments;
stünde er zusätzlich in der Datei, gäbe es zwei Wahrheiten, und eine von Hand
geänderte Überschrift zeigte weiter auf das alte Bild.

Gebacken wird zu Beginn von `render` und `export-mlt`, nicht in `build` — auch
`slideshow render meine-fassung.yaml` mit von Hand geändertem Text ist ein
unterstützter Weg. Der Schritt ist idempotent und kostet bei unverändertem Text
nur einen Hash je Folie. Wer den Text ändert, bekommt automatisch ein neues
Asset, und genau drei Segmente rendern neu.

Die Folie entsteht auf der **Normalform** (7680 × 4320 bei 16:9), nicht in
Ausgabegröße: der Text ist in die Pixel eingebrannt und wird von der
Ken-Burns-Fahrt bis zu 1,3-fach vergrößert. Ein Projekt in 4K und dasselbe in
1080p teilen sich deshalb dieselbe Datei.

#### Wo eine Titelfolie beginnen darf

In einer **Beat-Region** auf einer Phrasengrenze. Ein Schnitt auf irgendeinem
Beat ist synchron, aber nicht musikalisch: ein Bildwechsel mitten in der Phrase
fällt kaum auf, eine *Zäsur* mitten in der Phrase fällt sofort auf — als
Fehler. `build` rechnet die nächstgelegene Grenze aus und materialisiert die
Ausrichtung als `beats:` des **vorangehenden** Bildes:

```yaml
- {type: still, src: cache/img_041.jpg, beats: 11}   # 8 -> 11: Phrasenlage
- {type: xfade, from: 78, to: 80, beats: 1.5}
- {type: title, title: Malmö, beats: 12}
```

Der Planer führt das ohne jede Sonderregel aus, und die Zahl lässt sich von
Hand überstimmen. Der Preis: wer später ein Bild *davor* verlängert, verschiebt
die Lage. `build` und `render` prüfen sie deshalb bei jedem Lauf und melden die
Abweichung mit konkretem Vorschlag.

An einer **Regionsgrenze** entfällt die Rechnung — die Grenze *ist* per
Konstruktion eine musikalische Zäsur.

In einer **`free`-Region** gibt es keine Phrasen; dort gilt die Standardlänge
der Bildanzeige (`still_seconds`, regional überschreibbar). Mit genau einer
Ausnahme:

> **Lange Stille kachelt nicht.** Eine `quiet`-Region über `hold_seconds`
> ist **ein** Slot, damit dort bewusst ein ruhiges Einzelbild stehen bleiben
> kann. Eine Titelfolie bekäme sonst die *ganze* Stille — zwanzig Sekunden
> Standbild mit „Malmö" darauf, ohne Fehlermeldung. `build` schreibt deshalb
> `dur: <still_seconds>` **und** `snap_back: false`. Beides zusammen: `dur:`
> allein rettet nichts, weil `snap_back` per Default aufrundet und die einzige
> Kante einer `hold`-Region ihr Ende ist. Der Rest der Stille fällt an das
> folgende Bild, das seinen `hold`-Status behält.

Als Untergrenze der Standzeit gilt eine Lesezeitregel — `1,8 s + 0,25 s je
Wort`. Sie begründet eine Warnung, keine stille Korrektur.

#### `bg: auto` nach Tragfähigkeit

`auto` nimmt **das erste Bild des neuen Abschnitts** — solange es den Text
trägt. Beim Bauen misst `build` dafür dieselbe Rechnung, die später das Asset
backt: Satz, Textfläche, Hintergrund unscharf, Abdunklung nachführen bis
`min_contrast` steht. Bleibt der nötige Faktor bei `auto_darken_min` oder
darüber, ist die Sache erledigt — eine Messung, dasselbe Ergebnis wie früher.

Erst darunter — der helle Himmel am Kapitelanfang — werden auch die übrigen
`auto_candidates` Bilder **des eigenen Abschnitts** gemessen, und es gewinnt
das mit der geringsten nötigen Abdunklung; bei Gleichstand das frühere. Der
gewählte Pfad steht danach als konkreter Wert in der Datei.

> **Eine abweichende Wahl kostet die Fokusblende** — die setzt voraus, dass
> Hintergrund und Folgebild dasselbe sind. Deshalb hat das erste Bild Vorrang,
> und deshalb steht die Wahl im Bericht:
>
> ```text
> Kapitel 'Malmö': Hintergrund img_101 (Abdunklung 0.55) statt img_098 (0.45)
> — das erste Bild des Abschnitts trägt den Text erst unterhalb von 0.50.
>   Die Fokusblende entfällt damit.
> ```
>
> Wer Stabilität will, schreibt `bg:` im Kapitel fest — ein ausdrücklicher Wert
> (Medien-ID, Farbfläche, `none`) umgeht die Messung vollständig.

Die Skala ist kürzer, als sie aussieht: die Abdunklung läuft in Schritten von
0,05 vom Startwert `darken` abwärts, und mit `min_contrast: 4.5` trägt selbst
Reinweiß den Text bei 0,45. Mit den Vorgaben gibt es also genau drei Stufen —
0,55, 0,50 und 0,45 —, und `auto_darken_min: 0.50` heißt: **nur die unterste
ist ein Rettungsfall.** Wer `min_contrast` anhebt, verlängert die Skala nach
unten, ohne dass die Schwelle etwas anderes bedeutet.

Trägt **kein** Kandidat den Text, bleibt es beim ersten Bild — ein anderes wäre
genauso unlesbar und die Fokusblende zusätzlich weg. Gemeldet wird es zweimal:
beim Bauen als Wahl, beim Backen an der Folie selbst.

In einer von Hand geschriebenen Edit-List gilt weiterhin die einfache Regel
(nächstes Standbild): die Messwahl ist eine Leistung von `build`, deren
Ergebnis sichtbar in der Datei steht. Beim Backen gäbe es keinen Ort, an dem
sie sichtbar würde.

#### Fokusblende

Steht der Hintergrund einer Folie auf `auto`, ist er in aller Regel das erste
Bild des neuen Abschnitts. Die Blende *aus* der Folie heraus führt dann auf
**dasselbe Bild, scharf**: der Hintergrund löst sich vor den Augen des
Zuschauers auf.

Damit das wie ein Schärfezug wirkt und nicht wie ein Schnitt zwischen zwei
ähnlichen Bildern, muss die Kamerafahrt über die Blende hinweg stetig sein.
`build` schreibt dafür gekoppelte `kb:`-Blöcke in beide Segmente — Zoom und
Bildmitte der Folie enden dort, wo die des Folgebildes beginnen:

```yaml
- {type: title, title: Malmö, bg: cache/img_042.jpg, beats: 12,
   kb: {z: [1.0, 1.06], c: [0.5, 0.5, 0.53, 0.5]}}
- {type: xfade, from: 79, to: 81, beats: 2}          # Fokusblende
- {type: still, src: cache/img_042.jpg, beats: 8,
   kb: {z: [1.06, 1.14], c: [0.53, 0.5, 0.58, 0.5]}} # setzt die Fahrt fort
```

Die Folie zoomt dabei immer **hinein**. Ein Hinauszoom endete bei `z = 1,0`,
und das Folgebild müsste darunter weitermachen — dort ist der Ausschnitt aber
bereits das ganze Bild. Nebengewinn: das Folgebild fängt oberhalb von `z = 1,0`
an und schwenkt damit von der ersten Sekunde an sichtbar, statt in der Klemmung
des Bildrands festzuhängen (siehe [Schwenk: Richtung und
Reichweite](#schwenk-richtung-und-reichweite)).

Ein von Hand gesetztes `kb:` gewinnt und wird nicht überschrieben.

#### Ohne Kamerafahrt

Der Text einer Folie ist in die Pixel eingebrannt und fährt deshalb mit. Das
ist gewollt — er gehört zum Bild und nicht darüber —, kostet aber Lesbarkeit:
ein stehender Satz liest sich ruhiger als ein wandernder, und dünne Schriften
flimmern in der Bewegung. `motion: none` lässt die Folie stillstehen:

```yaml
- {type: title, title: Malmö, subtitle: 'Tag 11 · 24. Juli',
   bg: cache/img_042.jpg, beats: 12, motion: none,
   kb: {z: [1.0, 1.0], c: [0.5, 0.5, 0.5, 0.5]}}   # von build materialisiert
```

Für alle Folien auf einmal: `defaults.title.motion: none`; für den ganzen Film
einschließlich der Bilder `defaults.kb.motion: none`. In `edit.yaml` überlebt
beides den nächsten Bau nicht — dauerhaft steht es im
[Feinschliff](#overridesyaml--der-feinschliff), der die globalen Vorgaben trägt:

```yaml
# overrides.yaml
defaults:
  title: {motion: none}         # nur die Folien; die Bilder fahren weiter
```

Ein `motion:` an der einzelnen Folie (in `chapters.yaml`) gewinnt darüber
weiterhin — es steht in `TitleSegment.motion`, die Vorgabe nur dahinter.

Dasselbe Wort steht am [Standbild](#still--standbild) und tut dort dasselbe.
`defaults.title.motion` gibt es trotzdem, weil „Folien still, Bilder in Fahrt"
der häufigere Wunsch ist als sein Gegenteil; ohne Angabe fällt es auf
`defaults.kb.motion` zurück, damit der filmweite Schalter die Folien mit
erwischt.

Aufgelöst wird das nicht im Renderer, sondern als **gewöhnliches `kb:`** am
Segment — genau der Block aus [Bewegung für ein Bild
abschalten](#häufige-eingriffe). Weder `planner.py` noch `render.py` bekommen
dadurch eine Zeile über Titel, und in der Datei steht sichtbar, warum diese
eine Folie stillsteht. Ein von Hand gesetztes `kb:` gewinnt gegen `motion:`;
wer beides schreibt, meint das `kb:`.

Zwei Folgen sind beabsichtigt:

- **Die Fokusblende bleibt, ihre Kopplung entfällt.** Der Schärfezug dauert
  weiter `xfade_focus` lang, aber `build` schreibt keine gekoppelte Fahrt mehr
  in Folie und Folgebild — sonst bekäme eine stillstehende Folie über die
  Hintertür doch eine Bewegung. Das Folgebild behält seine eigene.
- **Das Asset ändert sich nicht.** `motion` gehört zur Choreografie, nicht zu
  den Pixeln; ein Umschalten backt keine Folie neu. Neu gerendert werden nur die
  drei betroffenen Segmente.

### `clip` — Videoausschnitt

```yaml
- {type: clip, src: cache/clip_MVI_1234.mov, in: 2.5, out: "00:12.500", snap: out}
```

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `src` | string | – | Pfad zum **Intermediate**, nicht zur Originaldatei. |
| `in` | float\|string | `0.0` | In-Punkt im Intermediate. |
| `out` | float\|string | – | Out-Punkt. Ohne Angabe läuft der Clip bis zum Rasterpunkt. |
| `snap` | enum | `out` | `out` zieht den Out-Punkt auf das Beat-Raster (max. `clip_snap_tol` Beats), `none` lässt die Länge frei. |
| `snap_back` | bool | von `defaults` | Wie beim Standbild. |

`in` und `out` beziehen sich **immer** auf das (gegebenenfalls retimte)
Intermediate, nie auf die Originaldatei. Beide akzeptieren Sekunden (`6.5`)
oder `[[HH:]MM:]SS.mmm` als String (`"00:08.500"`, `"01:02:03.250"`).

### `xfade` — Übergang

```yaml
- {type: xfade, from: 0, to: 2, dur: 0.5, mode: dissolve}
```

Übergänge sind **eigene Segmente**. Das ist der Trick, der die Unabhängigkeit
der Segmente und damit das Caching rettet: der Hash eines xfade-Segments
schließt die Quell- und Bewegungs-Hashes beider Nachbarn ein.

| Schlüssel | Typ | Bedeutung |
|---|---|---|
| `from`, `to` | int | Indizes der Nachbarsegmente in `segments` (0-basiert, über das ganze Array gezählt — die Blende zwischen Segment 0 und 2 ist selbst Segment 1). |
| `dur` | float | Dauer in Sekunden. |
| `beats` | float | Dauer in Beats, nur in einer Beat-Region. |
| `mode` | string | Siehe unten. Ein unbekannter Modus ist ein Fehler. |

Einen Übergang **entfernen** heißt: das `xfade`-Segment löschen. Die Nachbarn
stoßen dann hart aneinander; ihre Indizes in den übrigen `from`/`to` müssen
angepasst werden.

#### Blendenmodi

`dissolve` (= `fade`), `fade`, `fadeblack`, `fadewhite`, `wipeleft`,
`wiperight`, `wipeup`, `wipedown`, `slideleft`, `slideright`, `smoothleft`,
`smoothright`, `circleopen`, `circleclose`, `pixelize`, `hblur`.

Ein Modus außerhalb dieser Liste ist ein **Fehler** — die Meldung nennt Pfad,
Zeile, den geschriebenen Wert und die gültigen Modi. Ein Tippfehler wie
`dissovle` lief früher stillschweigend als normale Blende durch; eine alte
Datei mit einem solchen Wert bricht jetzt ab und will den gemeinten Modus
ausgeschrieben haben.

### `kb` am Segment

Überschreibt `defaults.kb` für ein einzelnes Bild. Alle Felder optional.

| Schlüssel | Typ | Bedeutung |
|---|---|---|
| `z` | [float, float] | Start- und Ziel-Zoom, z. B. `[1.0, 1.2]`. Beide > 0. Ohne Angabe aus `zoom_rate` und Dauer gerechnet. |
| `c` | [float, float, float, float] | Start- und Ziel-Bildmitte als `[x0, y0, x1, y1]`, normalisiert auf `[0, 1]`. `[0.5, 0.5, 0.5, 0.5]` steht still. |
| `ease` | enum | `smoothstep` oder `linear`. |
| `engine` | enum | `zoompan` oder `scale16`, nur für dieses Bild. |

Soll das Bild einfach stillstehen, reicht `motion: none` am Segment — `build`
schreibt daraus genau den Block `kb: {z: [1.0, 1.0], c: [0.5, 0.5, 0.5, 0.5]}`.
Umgekehrt gilt: wer beides schreibt, meint das `kb:`.

Die Bewegung ist über die **volle sichtbare Spanne** definiert — exklusiver
Anteil plus die angrenzenden Übergangshälften. Das xfade-Segment wertet
dieselben Ausdrücke beider Nachbarn mit passendem Frame-Offset aus, damit die
Bewegung durch die Blende hindurch weiterläuft.

### Woher `kb:` kommt

Vier Stellen schreiben eine Kamerafahrt. Ihre Rangfolge liegt fest — sonst
gewönne die zufällige Reihenfolge im Code, und der häufigste Schaden wäre eine
still abgeschaltete Fokusblende:

| Rang | Quelle | Gilt für |
|---|---|---|
| 1 | **`kb:` von Hand** in `edit.yaml` oder `overrides.yaml` (auch `motion: none`) | immer |
| 2 | **Titelfolien** — `defaults.title.motion` bzw. `motion:` an der Folie | Folien |
| 3 | **Die Fokusblende** — die gekoppelte Fahrt aus Folie und Folgebild | das Folienpaar |
| 4 | **Der Vision-Planer** aus [`vision.yaml`](#visionyaml--was-auf-den-bildern-ist) | alle übrigen Standbilder |
| — | *keine davon* | dann die Rotation aus der Bildkennung |

Der Planer läuft **vor** der Fokusblende und lässt deren Folgebild in Ruhe. Ein
`kb:` an dieser Stelle schaltet den Schärfezug ab, mit dem sich eine Titelfolie
in das Foto auflöst — der Film rendert dann trotzdem, er sieht nur schlechter
aus. Wer dort selbst eingreift, bekommt eine Meldung.

---

## `vision.yaml` — was auf den Bildern ist

Ohne diese Datei hängt die Kamerafahrt allein an der Bildkennung: Zoomrichtung
aus einem Bit des Hashes, Schwenkrichtung aus den nächsten dreien. Das Bild
selbst geht nicht ein, und darum wird in ein Gruppenfoto schon mal auf den
Bildrand zugeschwenkt.

`slideshow analyze` fragt die Claude-API, **was auf dem Bild zu sehen ist** —
nicht, wie sich die Kamera bewegen soll. Aus den Fakten rechnet `build` die
Fahrt (`slideshow/kbplan.py`). Der Unterschied ist der ganze Entwurf: eine
gelieferte Bewegung wäre nicht prüfbar, eine gelieferte Bounding-Box ist es.

```yaml
version: 1
model: claude-opus-5
prompt: 1
images:
  cache/img_0042.jpg:
    hash: 9e2b7d4055f31c8a          # blake2b des Cache-Bildes
    scene: landscape_wide           # steuert die Bewegungsregel
    axis: horizontal                # Achse, entlang derer geschwenkt wird
    horizon: 0.61
    focus: [0.38, 0.47]             # wohin gefahren werden darf
    subjects:
      - {box: [0.3, 0.34, 0.46, 0.72], kind: person, weight: 0.9}
    protect:                        # darf zu keinem Zeitpunkt angeschnitten werden
      - [0.3, 0.3, 0.48, 0.74]
    detail: 0.35                    # Detaildichte → Obergrenze für den Zoom
    depth: into
    quiet: [0.05, 0.62, 0.55, 0.95] # ruhige Fläche
    suggest: pan_right              # unverbindlich
    conf: 0.88
    note: Fjord im Weitwinkel, Wanderer links im Vordergrund
```

**Alle Koordinaten sind auf das Cache-Bild normalisiert**, nicht auf das
Original. Nur so gelten sie unverändert im Koordinatensystem des
Ken-Burns-Filters — sonst müsste jede Box durch Beschnitt und
Hochformat-Komposit zurückgerechnet werden, und ein Fehler dort verschöbe genau
die Boxen, die schützen sollen.

| Schlüssel | Bedeutung |
|---|---|
| `hash` | Inhaltshash des Cache-Bildes. Ändert er sich, fragt `analyze` neu. |
| `stage` | `geometry` (Vorgabe) — Fakten mit Koordinaten vom Cache-Bild. `labels` ist für eine spätere, koordinatenfreie Auswahlanalyse reserviert. |
| `scene` | `landscape_wide` · `portrait_person` · `group` · `architecture` · `detail_macro` · `action` · `interior` · `document` · `other` |
| `axis` | `horizontal` · `vertical` · `none` |
| `focus` | Zielpunkt einer Fahrt, `[x, y]`. |
| `protect` | Höchstens vier Boxen, jede zwischen 1 % und 80 % der Fläche. Jede Box **deckelt den Zoom**. |
| `detail` | 0..1. Ein glatter Himmel verträgt Zoom, ein Makro nicht. |
| `conf` | Unter 0.5 gilt das Bild wie `scene: other` — die Schutzboxen bleiben trotzdem gültig. |

### Die Datei ist zum Ansehen da

Wie `beats.yaml`: eine Zeile je Objekt, von Hand korrigierbar. Wer eine falsche
Schutzbox sieht, ändert vier Zahlen; ein erneutes `analyze` überschreibt sie
nicht, solange Bild-Hash, Prompt-Version und Modell gleich bleiben. Eine
erfundene Box ist auch der wahrscheinlichste Fehler — sie klemmt den Zoom auf
1,05, und das Bild steht dann ohne erkennbaren Grund still.

### Was die Geometrie nicht hergibt

Zoomweite und Schwenkweite sind **ein Budget, kein Parameterpaar**. Der Filter
klemmt das sichtbare Fenster an den Bildrand; unbeschnitten bleibt eine
Bildmitte nur, solange

```
|c − 0,5| ≤ 0,5 − 1/(2·z)
```

gilt. Umgekehrt: eine Auslenkung von 0,12 verlangt `z ≥ 1,316`, und
`zoom_total` gibt mit den Vorgaben 1,30 her. Der Planer kürzt deshalb
regelmäßig und **meldet es**. Steht im Bericht eine große Zahl, ist der Hebel
`defaults.kb.zoom_total` und nicht der Planer.

### Ohne die Datei

`build` läuft in jedem Fall durch. Fehlt sie, fehlt ein Eintrag, oder ist eine
Analyse ausgefallen, bekommt das betroffene Bild kein `kb:` und läuft über die
heutige Rotation. `--no-vision` schaltet sie ab, auch wenn sie daliegt.

> **Ein Analyselauf schickt private Fotos an einen externen Dienst.** Das ist
> kein stiller Schritt: `analyze` ist ein eigenes Kommando und fragt vor dem
> ersten Senden nach. Siehe [README](../README.md#bildanalyse-und-datenschutz).

---

## `chapters.yaml` — woher die Titelfolien kommen

`build` erzeugt `edit.yaml` **neu**. Zwölf Städte von Hand einzupflegen ist
zumutbar, zwölf Städte nach jedem `build`-Lauf erneut einzupflegen nicht.
Deshalb sind die Kapitel eine eigene Eingabedatei:

```yaml
# chapters.yaml — Kapitel der Reise. Wird von `slideshow build` eingelesen.
chapters:
  - {at: 0,           title: Skandinavien 2026, subtitle: "Drei Wochen, vier Städte",
     bg: auto, motion: none, beats: 16}
  - {before: img_042, title: Malmö,     subtitle: auto}
  - {before: img_071, title: Stockholm, subtitle: auto, bg: img_075}
```

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `before` | string | – | [Medien-ID](#medien-ids), **vor** der die Folie steht. Genau eines von `before`, `at` und `group`. |
| `at` | int | – | Position in der Medienfolge. `at: 0` ist der Auftakt vor allem Material. |
| `group` | string | – | Name einer Gruppe aus [`order.yaml`](#orderyaml--die-reihenfolge-von-hand); die Folie steht vor deren erstem Medium. Siehe unten. |
| `title` | string | – | Überschrift. Pflicht und nicht leer. |
| `subtitle` | string | `auto` | Zweite Zeile. `auto` bildet `Tag 11 · 24. Juli` aus dem Aufnahmezeitpunkt des folgenden Bildes; Tag 1 ist das früheste Aufnahmedatum des Projekts. Weglassen mit `subtitle: null`. |
| `bg` | string | `auto` | Wie am Segment, **zusätzlich als [Medien-ID](#medien-ids)** (`bg: img_075`). Ein ausdrücklicher Wert umgeht die Messung aus [nach Tragfähigkeit](#bg-auto-nach-tragfähigkeit). In dieser Datei stehen IDs; einen Cache-Pfad müsste man erst nachschlagen. `build` löst sie auf und schreibt den Pfad nach `edit.yaml`. Was weder ID noch bekannter Pfad, Farbfläche oder `none` ist, bricht mit Nennung des Kapitels ab. |
| `beats`, `dur` | float | von `defaults.title` | Standzeit. |
| `style` | enum | `card` | Wie am Segment. |
| `motion` | enum | von `defaults.title.motion` | Wie am Segment. `none` lässt die Folie stillstehen. |
| `kb` | Objekt | – | Wie am Segment. Setzt die Fokusblenden-Kopplung außer Kraft. |

Verankert wird an **Medien-IDs**, nicht an Segmentindizes oder Zeiten: IDs sind
gegen Umsortieren und gegen zusätzliche Bilder stabil, alles andere verrutscht
beim nächsten `build`. Eine ID, die es nicht gibt, ist ein Fehler mit Nennung
des Kapitels — kein stilles Überspringen.

#### `group:` — der Anker für manuell Sortiertes

Wer thematisch sortiert, will die Zäsur an der **Blockgrenze**, nicht an einem
bestimmten Bild. `before: img_042` bricht in dem Moment, in dem man img_042
innerhalb seines Blocks nach hinten schiebt: der Anker zeigt dann kommentarlos
mitten in den Block hinein.

```yaml
chapters:
  - {group: am-wasser, title: "Am Wasser", subtitle: null}
```

`group:` meint den gleichnamigen Block aus [`order.yaml`](#orderyaml--die-reihenfolge-von-hand)
und überlebt jedes Umsortieren *innerhalb* des Blocks. Hat `rest: drop` das
erste Bild der Gruppe weggenommen, rückt die Folie vor das erste noch
vorhandene. Ein `group:` ohne `order.yaml`, eine unbekannte und eine leer
geräumte Gruppe sind jeweils ein Fehler mit Nennung des Kapitels.
`slideshow chapters --from-groups` schreibt diese Einträge fertig hin — einen je
Block, Überschrift leer.

#### Medien-IDs

Die ID ist der Name, unter dem ein Foto oder ein Clip im ganzen Projekt
auftritt. `probe` bildet sie aus dem Dateinamen und schreibt sie ins Manifest:

```
img_DSC06273        aus src/2026/DSC06273.JPG
clip_MVI_1234       aus src/MVI_1234.MOV
img_kopenhagen_2    zweite Datei mit dem Stamm „kopenhagen"
```

Gebildet aus **Art und Dateinamen**: `img_` für Bilder, `clip_` für Videos,
dahinter der Dateiname ohne Endung, alles außer `A–Z a–z 0–9 _ -` zu `_`
zusammengezogen. Zwei Dateien mit demselben Stamm in verschiedenen Ordnern
bekommen `_2`, `_3` … in der Reihenfolge, in der `probe` sie findet.

**Wo sie stehen:** in `manifest.json` unter `media[].id` — und, praktischer, in
jedem `src:` der Edit-List. Das Zwischenprodukt heißt exakt wie die ID:

```
cache/img_DSC06273.jpg   ->   ID  img_DSC06273
cache/clip_MVI_1234.mov  ->   ID  clip_MVI_1234
```

Wer also in `edit.yaml` sieht, welches Bild er meint, kann die ID direkt
ablesen — Ordner und Endung weg, der Rest ist sie.

Die ID hängt **nur am Dateinamen**, nicht an Position, Aufnahmezeit oder Anzahl
des Materials. Genau deshalb taugt sie als Anker: ein nachgereichtes Foto
verschiebt keine einzige andere ID, und eine `chapters.yaml` überlebt jedes
`probe` und jedes `build`. Die Kehrseite ist dieselbe Regel von hinten gelesen —
wer eine Datei **umbenennt**, ändert ihre ID, und die Kapitel darauf zeigen ins
Leere. Das bricht mit Nennung des Kapitels ab, bevor etwas gerendert wird.
Denselben Effekt hat der Sonderfall der Doppelnamen: kommt eine *dritte* Datei
mit dem Stamm „kopenhagen" hinzu, kann der `_2`-Zähler die Zuordnung neu
verteilen. Wer gleichnamige Dateien in mehreren Ordnern hat, prüft die Kapitel
nach einem `probe` besser noch einmal.

#### Der Auftakt

`at: 0` steht vor allem Material — und `bg: auto` bedeutet auch dort etwas: das
„nächste Bild" gibt es sehr wohl, es ist das erste des Films. Der Titel steht
also über dem ersten Foto, unscharf und abgedunkelt, und die Blende danach löst
ihn in genau dieses Bild scharf auf. Ein üblicher Filmanfang, und das, was
`slideshow chapters` vorschlägt.

Die Alternativen stehen als Handgriff im Kommentar der erzeugten Datei:

```yaml
  - {at: 0, title: "Skandinavien 2026", bg: "#1b2a3a"}   # ruhige Farbfläche
  - {at: 0, title: "Skandinavien 2026", bg: img_042}     # ein bestimmtes Bild
```

Ein bestimmtes Bild lohnt, wenn das erste Foto als Grund nichts hergibt — zu
dunkel, zu unruhig, zu wenig Himmel für zwei Zeilen Text. Welches Bild `auto`
hier trifft, nennt `slideshow chapters` im Kommentar, damit man es austauschen
kann, ohne es erst zu suchen. Zeigt `bg` auf ein Bild, das **nicht** das
folgende ist, entfällt die Fokusblende: es gibt dann nichts scharf aufzulösen.
Denselben Preis zahlt `auto`, wenn es dem ersten Bild den Text nicht zutraut —
den Fall nennt der Bericht ([nach
Tragfähigkeit](#bg-auto-nach-tragfähigkeit)). Nur „zu hell" misst das Werkzeug
allerdings; „zu unruhig" bleibt Handarbeit.

### Die Grenzen finden lassen

```
slideshow chapters                 # -> chapters.yaml mit leeren Überschriften
slideshow chapters --min-jump 20   # Ortssprung-Schwelle in km (Default 30)
slideshow chapters --min-gap 12    # Zeitlücke in Stunden (Default 20)
slideshow chapters --from-groups   # ein Eintrag je Block aus order.yaml
```

Zwei Signale, beide aus dem Manifest:

| Signal | Woher | Stärke |
|---|---|---|
| **Ortssprung** | GPS aus EXIF (Fotos) bzw. ISO-6709-Tag (Handyvideos) | Ein Sprung über 30 km *ist* der neue Ort — das treffsicherste Signal. |
| **Zeitlücke** | `capture_time` | Ab 8 h eine Tagesgrenze, ab 20 h fast immer ein Ortswechsel. Immer verfügbar, aber grob. |

**Wo Koordinaten vorliegen, entscheiden sie — auch *gegen* die Uhr.** Eine
24-Stunden-Pause bei unveränderten Koordinaten ist eine Nacht im selben Hotel
und kein neuer Abschnitt; ohne dieses Veto bekäme eine Reise so viele Kapitel
wie Tage. Fehlt GPS, bleibt nur die Zeitlücke, und der Bericht sagt das:
„kein Foto trägt Koordinaten — erkannt wird allein über Zeitlücken, und die
sind gegenüber einem Ortswechsel blind."

Die erzeugte Datei ist ein **Formular**: starke Grenzen stehen als Einträge
drin, schwächere Kandidaten darunter auskommentiert samt Begründung (`# 24 h
Pause, aber nur 0 km, gleicher Ort`). Ein Handgriff macht daraus einen Eintrag.
Eine vorhandene `chapters.yaml` wird **nicht** überschrieben — sie enthält
Handarbeit; dafür gibt es `--force`, und `--dry-run` zeigt den Vorschlag nur an.

**`--from-groups` sucht nicht, sondern übernimmt.** Wer die Abschnitte beim
Sortieren schon gezogen hat, bekommt einen Eintrag je Block aus `order.yaml` mit
`group:` als Anker — die beiden Schwellen oben sind dann gegenstandslos und
werden zurückgewiesen statt ignoriert. Vorentschieden wird dabei nur, was im
Material steht: ein Block aus einem einzigen Tag bekommt `subtitle: auto`, einer
über mehrere `subtitle: null`. Der erste Block steht auskommentiert, weil er
sonst mit dem Auftakt (`at: 0`) an derselben Stelle säße. Die Überschriften
bleiben auch hier leer.

`build` prüft im Nachgang noch, **wo** ein Kapitel auf der Timeline landet: fällt
die Zäsur knapp neben eine Pause zwischen zwei Tracks, schlägt der Bericht vor,
sie um ein oder zwei Bilder zu verschieben. Dort fiele sie mit dem Ton zusammen,
und die Folie müsste den Fluss gar nicht erst unterbrechen. Ein Vorschlag, keine
automatische Verschiebung — welches Foto zu welcher Stadt gehört, weiß das
Werkzeug nicht.

Aufgerufen wird das mit `slideshow build --chapters chapters.yaml`; liegt die
Datei unter diesem Namen im Projektverzeichnis, findet `build` sie von selbst.
Ohne auffindbare Schriftdatei bricht der Lauf sofort ab, mit
Installationsbefehl statt Traceback.

**Die Überschrift bleibt Handarbeit.** Einen Ortsnamen kann das Werkzeug nicht
erfinden, und ein geratener Name ist schlimmer als kein Name.

*Nebenbei:* Liegt für die Reise eine Planung mit Stationen und Daten vor, ist
`chapters.yaml` daraus direkt erzeugbar — die Stationsnamen sind exakt die
gesuchten Überschriften.

---

## `order.yaml` — die Reihenfolge von Hand

`build` leitet die Abfolge aus dem Aufnahmezeitpunkt ab. Für einen Film, der
**thematisch** erzählt — erst die Küste, dann die Abende —, ist das falsch, und
Handarbeit in `edit.yaml` stirbt beim nächsten `build`. Deshalb ist die
Reihenfolge, wie die Kapitel, eine eigene Eingabedatei:

```yaml
# order.yaml — Reihenfolge der Medien. Wird von `slideshow build` eingelesen.
version: 1
rest: error                  # error (Vorgabe) | append | drop

groups:
  - name: am-wasser
    items:
      - img_DSC06401
      - img_DSC06288
      - clip_MVI_1234

  - name: abende
    items: [img_DSC06273, img_DSC06280]
```

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `version` | int | `1` | Andere Versionen werden abgelehnt, nicht geraten. |
| `rest` | enum | `error` | Was mit Material geschieht, das die Datei nicht nennt — siehe unten. |
| `groups` | Liste | – | Blöcke. Genau eines von `groups:` und `order:`. |
| `groups[].name` | string | – | Bezeichner des Blocks. **Erscheint nicht im Film.** |
| `groups[].items` | Liste | – | [Medien-IDs](#medien-ids) in der gewünschten Reihenfolge. |
| `order` | Liste | – | Flache Kurzform, gleichbedeutend mit einer namenlosen Gruppe. |

Aufgerufen mit `slideshow build --order order.yaml`; liegt die Datei unter
diesem Namen im Projektverzeichnis, findet `build` sie von selbst. Ohne Datei
bleibt es chronologisch.

**Eine Gruppe ist keine Titelfolie.** Der Name steht nirgends im Bild — sonst
gäbe es zwei Wege, eine Überschrift zu erklären, und sie liefen auseinander.
Wer an einer Blockgrenze eine Zäsur will, schreibt sie in `chapters.yaml`, und
zwar mit `group:` als Anker: `{group: am-wasser, title: "Am Wasser"}`. Der Text
wohnt damit weiter in `chapters.yaml`, die Reihenfolge weiter hier, und beide
Dateien behalten genau eine Aufgabe.

### Die Datei erzeugen lassen

```
slideshow order                 # -> order.yaml, chronologisch vorbelegt, nach Tagen gruppiert
slideshow order --by place      # nach Ortsclustern aus GPS statt nach Tagen
slideshow order --by none       # ein einziger Block
slideshow order --update        # neues Material einpflegen, Sortierung behalten
slideshow --dry-run order       # nur anzeigen
slideshow order --force         # bestehende Datei überschreiben
```

Niemand tippt 90 Medien-IDs ab. Die erzeugte Datei ist ein **Formular**:
vorbelegt in chronologischer Reihenfolge, nach Kalendertagen gruppiert, und
jede Zeile trägt als Kommentar, was man zum Sortieren wissen muss — Tag,
Uhrzeit, Hoch- oder Querformat, bei Clips die Länge. Sortieren heißt dann
Zeilen verschieben.

Gruppiert wird nach **Kalendertag**, nicht nach Zeitlücke: eine Aufnahme um
23:50 und eine um 00:10 liegen 20 Minuten auseinander und trotzdem an
verschiedenen Tagen. Nur so heißt der Block `tag-2` auch das, was
`subtitle: auto` später als „Tag 2" ausschreibt. Material ohne Zeitstempel
landet in einer eigenen Gruppe `ohne-datum`.

Eine vorhandene Datei wird **nicht** überschrieben — sie enthält die Sortierung.

### Neues Material einpflegen

Nach einem erneuten `slideshow probe` kennt das Manifest Bilder, die
`order.yaml` nicht nennt — und `rest: error` bricht dann zu Recht ab.
`slideshow order --update` ist der Weg zurück:

- die bestehende Reihenfolge, die Gruppennamen und **alle Kommentare** bleiben,
- neues Material kommt als eigene Gruppe `neu` ans Ende, zum Einsortieren,
- Einträge, die es im Manifest nicht mehr gibt, werden an Ort und Stelle
  auskommentiert statt gelöscht — die Zeile steht dort, wo das Bild einsortiert
  war, und wer eine Datei umbenannt hat, findet die Stelle so wieder.

Gearbeitet wird dafür auf dem **Quelltext**, nicht auf dem Modell. Ein
Neuschreiben verlöre die eigenen Kommentare, und die sind hier keine Zierde:
bei `rest: drop` ist eine auskommentierte Zeile die Auswahl, und der Kommentar
davor sagt, warum das Bild draußen bleibt. Aus demselben Grund bietet
`--update` ein auskommentiertes Bild **nicht** erneut als „neu" an — sonst
stünde jedes verworfene Foto nach dem dritten Lauf dreimal in der Datei.

### Was mit nicht genanntem Material geschieht

| `rest:` | Verhalten |
|---|---|
| `error` (Vorgabe) | Abbruch mit Nennung der fehlenden IDs |
| `append` | fehlendes Material chronologisch **hinten** anhängen, mit Meldung im Bericht |
| `drop` | fehlendes Material weglassen, mit Meldung im Bericht |

Die Vorgabe ist `error`, weil der teuerste Fehler dieser Datei das *stille*
Verschwinden von Bildern ist: Man rendert eine Stunde und zählt hinterher nach.
Die Meldung nennt die fehlenden IDs in einer Form, die sich direkt in die Datei
kopieren lässt.

`rest: drop` ist dabei mehr als ein Notausgang. Eine Slideshow gegen ein Stück
von 6:32 fasst bei 8 Beats je Bild rund 50 Fotos; wer 90 hat, muss auswählen.
Auskommentierte Zeilen sind dann die Auswahl, und der Kommentar davor sagt,
warum ein Bild draußen bleibt. Beide Meldungen blendet `--force` **nicht** aus.

Bei tausend Bildern schreibt niemand diese Datei von Hand — dafür gibt es
`slideshow select`. Es erzeugt genau diese Form: die gewählten Medien als
Einträge, alle übrigen als Kommentar an ihrem zeitlichen Platz, nach Tagen
gegliedert. Ausgewählt wird nach Zeitstruktur und EXIF, ohne inhaltliche
Analyse — [Rezept 5b](rezepte.md#5b-aus-tausend-bildern-auswählen-lassen)
beschreibt das Verfahren, [`briefing-auswahl.md`](briefing-auswahl.md)
begründet es.

Angesehen wird das Ergebnis mit `slideshow sheet`: eine HTML-Seite, auf der
jede Traube als Kachelgruppe steht, das gewählte Bild groß, seine Geschwister
klein daneben. Der Bogen **liest** diese Datei und schreibt sie nie — was in
`items:` steht, ist gewählt, auch nach Handarbeit. Ein Klick markiert einen
Tausch, ein Knopf legt die YAML-Zeilen in die Zwischenablage; eintragen macht
der Mensch. Die Auswahlparameter, die `slideshow select` in den Dateikopf
schreibt, liest der Bogen von dort zurück — wer den Kopf umschreibt, nimmt ihm
den Traubenabstand, und er rechnet still mit der Vorgabe weiter.

Danach gilt für die Datei nichts Besonderes: sie ist eine gewöhnliche
`order.yaml`, und `order --update` pflegt sie nach, ohne die Auswahl zu
verlieren.

### Was die Datei nicht darf

Eine **unbekannte** ID bricht mit Zeile ab — kein stilles Überspringen. Eine
**doppelte** ID ebenso: die Datei beschreibt eine Permutation des Materials, und
`before:` in `chapters.yaml` träfe sonst stillschweigend das erste Vorkommen.
Eine bewusste Wiederholung — dasselbe Bild als Klammer am Anfang und am Ende —
bleibt als Handgriff in `edit.yaml` möglich.

### Was dabei nicht mehr stimmt

`subtitle: auto` bildet „Tag 11 · 24. Juli" aus dem Aufnahmezeitpunkt des
folgenden Bildes. Solange die Abfolge chronologisch läuft, ist das der Beginn
des Abschnitts. Steht über einem thematischen Block aus fünf Reisetagen das
Datum eines einzelnen davon, ist es technisch korrekt und inhaltlich
irreführend. `build` misst deshalb die Monotonie der Aufnahmezeiten und meldet
den Fall je Kapitel — wer nur zwei Bilder tauscht, bekommt keine Warnung über
etwas, das er nicht getan hat.

Aus demselben Grund taugt die Zeitlücken-Suche bei manueller Sortierung nur
noch bedingt: sie misst zwischen *Nachbarn*, und die sind dann thematisch
benachbart, nicht zeitlich. Die Reihenfolge zuerst festlegen, die Kapitel
danach — und dann `slideshow chapters --from-groups` nehmen, das die Blockgrenzen
übernimmt, statt neue zu raten. Wer die Datei doch ohne den Schalter erzeugt und
nicht chronologisch sortiert hat, findet diesen Vorbehalt in ihrem Kopf.

---

## `overrides.yaml` — der Feinschliff

Reihenfolge und Kapitel überleben den Neubau, weil sie eigene Eingabedateien
haben. Alles übrige hatte keinen Ort außer dem Erzeugnis: eine längere
Standzeit, eine abgeschaltete Fahrt, ein getrimmter Clip, ein harter Schnitt.
Wer ein Bild nachreichte und neu baute, verlor sie — wer nicht neu baute, bekam
das Bild nicht in den Film. Diese Datei löst genau die Klemme:

```yaml
# overrides.yaml — der Feinschliff. Wird von `slideshow build` eingelesen.
version: 1

defaults:                       # gilt für den ganzen Film
  kb: {engine: scale16}
  title: {motion: none}         # keine Kamerafahrt über Titelfolien

media:                          # einzelne Medien, nach Medien-ID
  img_DSC06300: {dur: 8}
  img_DSC06412: {motion: none}  # dieses Bild steht still
  clip_MVI_0042: {in: 3.2, out: 11.8, snap: none}

cuts:                           # Blenden, verankert am folgenden Medium
  - {before: img_DSC06413, dur: 0}
```

| Schlüssel | Typ | Bedeutung |
|---|---|---|
| `version` | int | `1`. Andere Versionen werden abgelehnt, nicht geraten. |
| `defaults` | Objekt | Teilbaum von [`defaults:`](#defaults). Nur das Genannte weicht ab — `kb: {engine: …}` lässt `zoom_rate` unberührt. Geprüft wird gegen das ganze Schema, ein `still_second: 5` bricht also ab. |
| `media` | Objekt | [Medien-ID](#medien-ids) → dieselben Schlüssel wie am Segment: `dur`, `beats`, `hold`, `snap_back`, `portrait`, `motion`, `kb` beim Standbild, `in`, `out`, `snap`, `snap_back` beim Clip. |
| `cuts` | Liste | `before:` ist die [Medien-ID](#medien-ids) **hinter** dem Schnitt, dazu `dur:`/`beats:` und `mode:`. `dur: 0` ist der harte Schnitt. |

**Verankert wird an Medien-IDs, nie an Segmentindizes.** `segments[41]` zeigt
nach dem nächsten eingefügten Bild auf ein anderes Segment; eine Kennung hängt
am Dateinamen und bleibt. Es ist dieselbe Entscheidung, die `chapters.yaml`
(`before:`) und die [Ken-Burns-Richtung](#woran-die-richtung-hängt) schon
getroffen haben — und aus demselben Grund gilt ein Eintrag für **beide**
Vorkommen, wenn ein Bild zweimal im Film steht.

Eine unbekannte ID bricht ab (Tippfehler oder umbenannte Datei). Eine bekannte,
die gerade nicht im Film steht, ist dagegen nur eine Meldung: die Datei soll
mehrere Auswahlrunden überdauern.

### Die Datei entstehen lassen

Niemand legt sie von Hand an. Man ändert wie bisher in `edit.yaml`, sieht sich
das Ergebnis an — und holt die Änderungen danach herüber:

```
slideshow overrides                     # edit.yaml gegen einen frischen Bau vergleichen
slideshow --dry-run overrides           # nur anzeigen
slideshow overrides --from andere.yaml  # eine andere Fassung als Quelle
```

Verglichen wird die vorhandene `edit.yaml` mit dem, was `build` **jetzt**
erzeugen würde — mit allen Eingabedateien des Projekts, den bereits gesicherten
Feinschliff eingeschlossen. Was danach noch abweicht, ist Handarbeit und wird
zum Eintrag; eine vorhandene `overrides.yaml` wird dabei ergänzt, nicht
überschrieben. Ein zweiter Lauf findet nichts mehr.

Was sich so **nicht** ausdrücken lässt, wird gemeldet statt geschluckt, mit der
Datei, in die es gehört:

| Abweichung | gehört nach |
|---|---|
| andere Reihenfolge, eingefügtes oder gelöschtes Segment | [`order.yaml`](#orderyaml--die-reihenfolge-von-hand) |
| geänderte Titelfolie, Blende um eine Folie herum | [`chapters.yaml`](#chaptersyaml--woher-die-titelfolien-kommen), `defaults.title.xfade_*` |
| `fps:`, `size:` | `build --fps`, `build --size` |

### Wenn `build` sich weigert

`build` schreibt eine Prüfsumme seines Ergebnisses in die Kopfzeile von
`edit.yaml` und vergleicht sie beim nächsten Lauf. Weicht die Datei ab, bricht
es ab und nennt beide Wege: `slideshow overrides` sichert die Handarbeit,
`--force` verwirft sie. Eine Edit-List aus einer älteren Fassung trägt die
Kopfzeile noch nicht und gilt deshalb einmalig als Handarbeit — der Irrtum in
diese Richtung kostet ein `--force`, der in die andere die Arbeit eines Abends.

Die Zeile ist ein **Kommentar**, kein Schlüssel: der Renderpfad weiß nichts von
ihr, und eine zweite Wahrheit im Schema wäre genau das, was
[`chapters.yaml`](#chaptersyaml--woher-die-titelfolien-kommen) an anderer Stelle
vermeidet.

Ist danach alles gesichert, setzt `slideshow overrides` den Stempel auf den
neuen Stand — sonst stünde man nach dem Sichern vor derselben Weigerung. Blieb
etwas unübertragbar (die Tabelle oben), bleibt auch der Schutz: die Edit-List
enthält dann weiterhin Handarbeit, die kein Neubau wiederherstellt.

Aus demselben Grund trägt eine Edit-List, in die `export-mlt --reimport` Zeiten
aus Kdenlive zurückgeschrieben hat, **keinen** Stempel: sie kommt nicht aus
`build`. Der nächste Bau hält daran an, und `slideshow overrides` trägt die
Zeiten als `dur:` bzw. `in`/`out` ein.

### Rangfolge

Für die Dauer gilt weiter die [Präzedenz](#präzedenz-der-dauer) — der
Feinschliff setzt einfach das, was sonst am Segment stünde. Für die Kamerafahrt
gibt es fünf Quellen, und ihre Rangfolge liegt fest:

| Rang | Quelle | Gilt |
|---|---|---|
| 1 | `kb:` am Segment oder in `overrides.yaml` | immer |
| 2 | `motion:` am Segment, an der Folie oder am Kapitel | für dieses eine Medium |
| 3 | `defaults.title.motion` | für alle Titelfolien |
| 4 | `defaults.kb.motion` | für den ganzen Film |
| 5 | die Kopplung der [Fokusblende](#fokusblende) | für das Folienpaar |
| 6 | Zoomrate und Kennungs-Hash | für alles übrige |

Die Ränge 2 bis 4 sind **eine** Sache: `build` übersetzt sie in genau das `kb:`
aus Rang 1 und schreibt es sichtbar in die Edit-List. Weder `planner.py` noch
`render.py` bekommen dadurch eine Zeile über den Schalter, und beim Lesen der
Datei ist zu erkennen, dass der Stillstand gewollt und nicht gerechnet ist.

Rang 1 hat einen Preis, den `build` auch meldet: ein eigenes `kb:` am Bild
**nach** einer Titelfolie schaltet die gekoppelte Fahrt ab, und die Folie löst
sich nicht mehr in das Bild auf, sondern schneidet darauf. Die längere Blende
bleibt. Für ein `motion: none` an dieser Stelle gilt dasselbe — es *ist* ein
`kb:`, nur kürzer geschrieben.

Und für die globalen Vorgaben: eingebaut, dann `overrides.yaml`, dann die
Kommandozeile. Die Datei überdauert, das Argument gilt für diesen Lauf — wer
beides setzt, meint das Argument. Wer eine Vorgabe dauerhaft will, für die es
kein Argument gibt (`still_tolerance`, `hold_seconds`, `title.size`), hat hier
den Ort dafür.

---

## Präzedenz der Dauer

Für ein Standbild gilt, von oben nach unten, die erste zutreffende Regel:

1. **`dur:` am Segment** — explizite Sekunden gewinnen immer. Bei
   `snap_back: true` wird danach auf den nächsten Beat aufgerundet.
2. **`beats:` am Segment** — nur in einer Beat-Region.
3. **`beats_per_still` / `still_seconds` an der Region** — der regionale Takt.
4. **`defaults.beats_per_still` / `defaults.still_seconds`** — der globale Takt.

Regel 2 in einer `free`-Region ist **kein Abbruch**, sondern eine Warnung mit
Regionsangabe und Segmentpfad; es gilt dann Regel 3. Das ist bewusst so, denn
dieser Zustand entsteht auch ohne Zutun: `build` legt `beats:` bei der
Lagekorrektur um eine Titelfolie an das vorangehende Bild und plant danach neu
— wandert ein Segment dabei über eine Regionsgrenze, gäbe es niemanden, der den
Fehler beheben könnte. Ein `beats:` aus [`overrides.yaml`](#overridesyaml--der-feinschliff)
hängt aus demselben Grund an einem Medium und nicht an einer Position.

Der Ersatzwert ist der Standard-Slot der Region, nicht `beats` in Sekunden
umgerechnet: eine `free`-Region ist driftfrei gekachelt und wird von ihren
Kanten exakt gefüllt. Eine freie Dauer mittendrin verschöbe jeden folgenden
Schnitt gegen diese Kachelung. Wer dort eine feste Standzeit braucht, nimmt
`dur:`.

Der Standard-Slot einer Beat-Region ist **absolut** definiert
(`beats_per_still` Beats ab dem nächsten Rasterbeat), nicht relativ zum
Cursor. Deshalb findet schon das folgende Bild nach einem Override von selbst
aufs Raster zurück — ein Tippfehler in `dur:` kann den Rest des Films nicht aus
dem Takt bringen.

### Der Rest am Regionsende

Die Dauer einer Beat-Region ist so gut wie nie ein ganzzahliges Vielfaches der
Slotlänge. Zwischen dem letzten vollen Slot und der Regionsgrenze bleibt ein
Rest, und ausgleichen lässt er sich nicht: die Slotgrenzen *sind* das
Beat-Raster, die Regionsgrenze liegt daneben.

```
Region [391,814 .. 411,597]  bpm 117,0  →  12 Beats = 6,154 s
  ├─ 6,66 s ─┼─ 6,16 s ─┼─ 6,66 s ─┤ 0,30 s │
                                     ^^^^^^ der Rest
```

Bekäme der Rest ein eigenes Bild, wäre das über viele Regionen regelmäßig
weniger als eine Sekunde — unter den beiden angrenzenden Blenden praktisch ein
Aufblitzen. Liegt er unter `min_still_seconds`, fällt er deshalb dem
**vorhergehenden** Bild derselben Region zu, und das verdrängte Medium wird in
der nächsten Region geplant. Nach vorn und nicht nach hinten, weil der Anfang
des nächsten Bildes der erste Beat der neuen Region ist und dort bleiben soll.

Zwei Ausnahmen:

- **Am Filmende** greift die Regel nicht. Dort ist die Grenze kein
  musikalischer Schnitt, und der Rest fällt dem letzten Bild ohnehin zu.
- **Hinter einem Clip** lässt sich nicht verlängern — dessen Länge kommt aus
  dem Intermediate. Dann bleibt das kurze Bild stehen, und `build` meldet es.

Die Regel kostet pro betroffener Region ein Bild. Bei knappem Material zeigt
sich das als Überdeckung („n Medien passen nicht mehr in die Musik"); der
Vorschlag, `beats_per_still` zu senken, steht dann im Bericht.

`free`-Regionen brauchen die Schranke nicht: sie sind über `still_tolerance`
exakt gekachelt und haben gar keinen Rest.

---

## Häufige Eingriffe

Alles Folgende steht so in `edit.yaml` — und `build` schreibt sie neu. Was
bleiben soll, holt [`slideshow overrides`](#overridesyaml--der-feinschliff)
danach in die Eingabedatei; dort steht dieselbe Angabe unter der Medien-ID.

**Ein Bild länger stehen lassen**

```yaml
- {type: still, src: cache/img_DSC06300.jpg, dur: 8}
```

**Bewegung für ein Bild abschalten**

```yaml
- {type: still, src: cache/img_DSC06300.jpg, motion: none}
```

`build` schreibt daraus `kb: {z: [1.0, 1.0], c: [0.5, 0.5, 0.5, 0.5]}` — den
Block kann man auch selbst hinschreiben, er gewinnt dann.

**Gar keine Kamerafahrt im ganzen Film**

`defaults.kb.motion: none`. Erwischt Bilder und Titelfolien; einzelne Bilder
dürfen mit `motion: kenburns` wieder ausscheren.

**Titelfolien ohne Kamerafahrt**

`defaults.title.motion: none` für alle, `motion: none` am einzelnen Segment
oder Kapitel. Der eingebrannte Text steht dann still und liest sich ruhiger.

**Alle Übergänge weg, nur harte Schnitte**

`defaults.xfade.auto: false` setzen und neu bauen (`slideshow build --no-xfade`)
— das Löschen von Hand ist bei hundert Segmenten Sisyphusarbeit, weil alle
`from`/`to` nachziehen müssen.

**Banding im Himmel bekämpfen**

`defaults.kb.engine: scale16` (oder `slideshow build --kb-engine scale16`).

**Ohne Ausblende enden**

`defaults.fade_out: 0`.

**Reihenfolge ändern**

Die `still`/`clip`-Segmente umsortieren — und die `from`/`to` der Übergänge
mitziehen. Sie sind Indizes in `segments`, gezählt über das ganze Array
einschließlich der Blenden selbst; die Blende zwischen Segment 0 und 2 ist
Segment 1, die nächste sitzt bei 3 und verbindet 2 und 4.

Die Blenden einfach zu löschen, hilft **nicht**: `auto: true` füllt beim Laden
nichts nach (siehe Kasten oben), das Ergebnis wären harte Schnitte. Wer
umsortiert und die Standardblenden behalten will, hat zwei Wege:

- **Neu bauen.** `slideshow build` erzeugt Blenden und Indizes von selbst,
  leitet die Reihenfolge aber wieder chronologisch aus dem Manifest ab. Für
  eine *systematische* Verschiebung — eine Kamera geht eine Stunde falsch —
  ist das der richtige Weg: `--clock-offset` setzen und neu bauen.
- **[`order.yaml`](#orderyaml--die-reihenfolge-von-hand) schreiben** und neu
  bauen. Für eine freie Reihenfolge, die sich nicht aus Zeitstempeln ergibt,
  ist das der Weg: die Datei ist eine *Eingabe* und überlebt den Neubau, die
  Übergänge und ihre `from`/`to` erzeugt `build` von selbst.
- **Von Hand nachziehen.** Bleibt für den Einzelfall — zwei Segmente tauschen,
  ohne eine Datei anzulegen. Bei vielen Segmenten ist es Sisyphusarbeit, weil
  alle `from`/`to` mitmüssen. Und der
  [Feinschliff](#overridesyaml--der-feinschliff) rettet die Reihenfolge
  ausdrücklich **nicht** — er meldet sie und verweist auf `order.yaml`.

Seit die Ken-Burns-Richtung an der Bildkennung hängt statt an der Position
(siehe [Woran die Richtung hängt](#woran-die-richtung-hängt)), kostet das
Umsortieren beim Rendern fast nichts: die Bilder behalten ihre Bewegung, nur
die angrenzenden Blenden werden neu berechnet.

---

## Verwandte Dateien

| Datei | Rolle |
|---|---|
| `manifest.json` | Was für Material vorliegt (`probe`, `preprocess`) — und unter `media[].id` die [Medien-IDs](#medien-ids), an denen `chapters.yaml` hängt. |
| `beats.yaml` | Regionenkarte der Tonspur (`beats`) — vor dem Bauen ansehen. |
| `chapters.yaml` | Kapitel der Reise, Eingabe für `build --chapters`. Überlebt das Neubauen der Edit-List. |
| `order.yaml` | [Reihenfolge der Medien](#orderyaml--die-reihenfolge-von-hand), Eingabe für `build --order`. Überlebt das Neubauen der Edit-List. |
| `overrides.yaml` | [Der Feinschliff](#overridesyaml--der-feinschliff) je Medium, Eingabe für `build --overrides`. Überlebt das Neubauen der Edit-List. |
| `vision.yaml` | [Was auf den Bildern ist](#visionyaml--was-auf-den-bildern-ist) (`analyze`) — Bildfakten, aus denen `build` die Kamerafahrt rechnet. Vor dem Bauen ansehen. |
| `edit.yaml` | **Diese Datei** (`build`). |
| `out/timeline.json` | Die *aufgelöste* Timeline mit absoluten Framenummern. Erzeugnis, kein Eingabeformat — zum Nachrechnen, nicht zum Editieren. |
