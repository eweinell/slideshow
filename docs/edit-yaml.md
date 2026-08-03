# `edit.yaml` — Referenz

Die Edit-List ist die Single Source of Truth. Jeder Renderpfad leitet sich aus
ihr ab, nie umgekehrt; `render` liest ausschließlich diese Datei plus das
Manifest. Sie ist bewusst menschenlesbar und von Hand editierbar.

Sie hält **Absicht**, keine aufgelösten Zeitstempel: `beats: 8` statt
`start: 12.334`. Die absoluten Framegrenzen entstehen bei jedem Lauf neu aus
derselben deterministischen Funktion, damit `build` und `render` garantiert
dasselbe sehen. Deshalb steht in der Datei auch nirgends ein Startzeitpunkt
eines Segments — er ergibt sich aus allen vorhergehenden.

> **`build` überschreibt die Datei.** Von Hand Geändertes geht bei einem
> erneuten `slideshow build` verloren. Wer die Edit-List anfasst, arbeitet
> danach direkt mit `slideshow render` weiter — oder legt eine Kopie an und
> übergibt sie explizit: `slideshow render meine-fassung.yaml`.

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
| `pan_rate` | float | `0.03` | Schwenkweg **pro Sekunde**, normalisiert auf die Bildkante. Dieselbe Regel wie beim Zoom. |
| `pan_total` | [float, float] | `[0.05, 0.18]` | Klemmung des Gesamt-Schwenkwegs. |
| `ease` | enum | `smoothstep` | `smoothstep` (weich an- und abbremsend) oder `linear`. |
| `alternate` | bool | `true` | Zoomrichtung wechseln lassen. Hundertmal hineinzoomen ermüdet. Der Wechsel ist **statistisch, nicht streng abwechselnd** — siehe unten. `false` zoomt immer hinein. |
| `engine` | enum | `zoompan` | `zoompan` ist schnell, rechnet aber **8-bittig** — ffmpeg schiebt eine Konvertierung davor. `scale16` rechnet durchgehend in 16 Bit, kostet mehr CPU. Bei sichtbarem Banding in Himmelsverläufen umstellen. |

#### Rate und Klemmung — wo die Rate wirklich gilt

Beide Bewegungen folgen derselben Formel:

```
Betrag = clamp(rate × Dauer, min, max)
```

Die Rate ist damit ein **Sollwert innerhalb eines Dauerfensters**, kein
Festwert. Außerhalb gewinnt die Klemmung, und die effektive Geschwindigkeit
weicht ab. Mit den Vorgaben reicht das Fenster von **1,6 s** (Zoom) bzw.
**1,7 s** (Schwenk) **bis 6,0 s**:

| Dauer | Gesamtzoom | eff. Zoomrate | Schwenkweg | eff. Schwenkrate |
|---|---|---|---|---|
| 1,0 s | 8,0 % | 0,080 (1,6×) | 0,050 | 0,050 (1,7×) |
| 2,0 s | 10,0 % | 0,050 (1,0×) | 0,060 | 0,030 (1,0×) |
| 4,0 s | 20,0 % | 0,050 (1,0×) | 0,120 | 0,030 (1,0×) |
| 6,0 s | 30,0 % | 0,050 (1,0×) | 0,180 | 0,030 (1,0×) |
| 12,0 s | 30,0 % | 0,025 (0,5×) | 0,180 | 0,015 (0,5×) |
| 28,0 s | 30,0 % | 0,011 (0,2×) | 0,180 | 0,006 (0,2×) |

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

Der Schwenk läuft symmetrisch um die Bildmitte: von `0.5 − a` nach `0.5 + a`
mit `a = Schwenkweg / 2`. Die Richtung wird deterministisch aus acht auf Länge 1
normierten Vektoren gewählt — alle acht legen denselben Weg zurück.

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

Wie weit der Schwenk tatsächlich sichtbar wird, begrenzt der Bildrand: der
Ausschnitt hat bei Zoom `z` die Breite `1/z`, seine Mitte darf sich also nur
innerhalb von `0.5 ± (0.5 − 1/(2z))` bewegen. **Bei `z = 1,0` ist das exakt
null** — der Ausschnitt *ist* das ganze Bild. Da jedes Hineinzoom-Segment bei
`z = 1,0` beginnt, ist der Schwenk dort festgenagelt und öffnet sich erst mit
wachsendem Zoom; von der geplanten Strecke wird rund die Hälfte sichtbar. Wer
mehr Schwenk will, hebt deshalb `pan_total` **und** `zoom_total[0]` an — mehr
Grundzoom schafft erst den Spielraum, in dem der Schwenk stattfinden kann.

> **`pan_amount` (veraltet).** Der frühere Schlüssel war eine *feste*
> Auslenkung ohne Dauerbezug. Er wird weiterhin gelesen und verlustfrei nach
> `pan_total: [2 × pan_amount, 2 × pan_amount]` übersetzt — eine Klemmung mit
> gleichen Grenzen liefert immer denselben Weg, bestehende Projekte rendern
> also unverändert. Für dauerabhängige Schwenks den Schlüssel durch
> `pan_rate`/`pan_total` ersetzen.

### `defaults.xfade` — Übergänge

| Schlüssel | Typ | Vorgabe | Bedeutung |
|---|---|---|---|
| `auto` | bool | `true` | Automatisch zwischen alle benachbarten Segmente Blenden setzen. Harte Schnitte zwischen hundert Standbildern wirken abgehackt. **Wirkt nur in `build`**, nicht beim Laden — siehe Kasten unten. |
| `beats` | float | `1.0` | Standarddauer einer Blende in Beats (Beat-Region). |
| `dur` | float | – | Standarddauer in Sekunden. In `free`-Regionen der einzig sinnvolle Weg. |
| `mode` | string | `dissolve` | Siehe [Blendenmodi](#blendenmodi). |

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
| `motion` | enum | `kenburns` | `kenburns` fährt über die Folie wie über jedes Standbild, `none` lässt sie stillstehen. Der Text ist in die Pixel eingebrannt und fährt sonst mit — er flimmert dabei bei dünnen Schriften und liest sich stehend ruhiger. Aufgelöst wird das als gewöhnliches `kb:` am Segment, nicht im Renderer. |
| `font` | string | `auto` | Pfad zur Schriftdatei. `auto` sucht plattformabhängig (Windows: Segoe UI, Arial; Linux: DejaVu Sans, Noto Sans; macOS: Helvetica). Die Umgebungsvariable **`SLIDESHOW_FONT` gewinnt immer** — dieselbe Regel wie bei `SLIDESHOW_MELT`. |
| `size` | float | `0.075` | Versalhöhe der Überschrift als Anteil der Bildhöhe. 162 px bei 2160 — auf einem 55″-Fernseher aus 3 m so groß wie eine Zeitungsschlagzeile. |
| `subtitle_scale` | float | `0.42` | Größe der zweiten Zeile, Anteil der Überschrift. |
| `blur` | float | `60.0` | Blur-Sigma des Hintergrunds, auf 7680er Basis. Derselbe Wert wie das Hochformat-Komposit — die beiden Bildsprachen müssen zusammenpassen. |
| `darken` | float | `0.55` | **Startwert** der Abdunklung. Der Generator misst die Leuchtdichte unter der Textfläche und führt den Wert in festen Schritten nach, bis der Kontrast trägt. |
| `min_contrast` | float | `4.5` | Gefordertes Kontrastverhältnis zwischen Text und Hintergrund (WCAG 2.1). Gemessen wird das **95. Perzentil** der Leuchtdichte unter der Textfläche, nicht ihr Mittel — sonst bleibt die Folie im Durchschnitt lesbar und über ihrer hellsten Stelle trotzdem nicht. Wird der Wert bis zur Untergrenze nicht erreicht, folgt eine Warnung statt stiller Unlesbarkeit. |
| `safe` | float | `0.10` | Safe Area ringsum, Anteil der Kante. Überlebt TV-Overscan und einen 4:5-Beschnitt. |
| `xfade_in` | float | `1.5` | Blende **in** die Folie hinein, als Faktor auf die Standardblende. Der Film atmet in die Zäsur ein. |
| `xfade_out` | float | `1.0` | Blende **aus** der Folie heraus, ohne Fokusblende. |
| `xfade_focus` | float | `2.0` | Blende heraus, wenn der Hintergrund das Folgebild ist (Fokusblende). Länger, weil der Schärfezug Zeit braucht. |

Die drei `xfade_*`-Faktoren ändern nur die Choreografie, nicht das Bild — sie
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
| `beats` | float | – | Dauer in Beats. Nur in einer Beat-Region gültig — in einer `free`-Region ist es ein Fehler mit Angabe der Region. Gebrochene Werte (`1.5`) sind erlaubt. |
| `hold` | bool | `false` | Ruhiges Bild über eine lange Stille. Wird von `build` gesetzt, lässt sich aber erzwingen. |
| `snap_back` | bool | von `defaults` | Nur für dieses Segment. |
| `portrait` | enum | von `defaults` | Nur für dieses Bild. |
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
| `bg` | string | `auto` | Hintergrund: `auto` (erstes Bild des neuen Abschnitts, unscharf), ein Pfad, `#rrggbb` als Farbfläche oder `none` für Text auf Schwarz. `build` schreibt den aufgelösten Wert zurück. |
| `dur` | float | – | Wie beim Standbild. Gewinnt immer. |
| `beats` | float | von `defaults.title.beats` | Wie beim Standbild — nur in einer Beat-Region gültig. |
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

#### Fokusblende

Steht der Hintergrund einer Folie auf `auto`, ist er das erste Bild des neuen
Abschnitts. Die Blende *aus* der Folie heraus führt dann auf **dasselbe Bild,
scharf**: der Hintergrund löst sich vor den Augen des Zuschauers auf.

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

Für alle Folien auf einmal: `defaults.title.motion: none`.

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
| `mode` | string | Siehe unten. |

Einen Übergang **entfernen** heißt: das `xfade`-Segment löschen. Die Nachbarn
stoßen dann hart aneinander; ihre Indizes in den übrigen `from`/`to` müssen
angepasst werden.

#### Blendenmodi

`dissolve` (= `fade`), `fadeblack`, `fadewhite`, `wipeleft`, `wiperight`,
`wipeup`, `wipedown`, `slideleft`, `slideright`, `smoothleft`, `smoothright`,
`circleopen`, `circleclose`, `pixelize`, `hblur`.

> **Vorbehalt:** Ein unbekannter Modus wird derzeit *stillschweigend* zu einer
> normalen Blende (`kenburns.py`, `_XFADE_MODES.get(mode, "fade")`). Ein
> Tippfehler wie `dissovle` fällt also nicht auf. Anders als bei allen übrigen
> Schlüsseln gibt es hier keine Prüfung.

### `kb` am Segment

Überschreibt `defaults.kb` für ein einzelnes Bild. Alle Felder optional.

| Schlüssel | Typ | Bedeutung |
|---|---|---|
| `z` | [float, float] | Start- und Ziel-Zoom, z. B. `[1.0, 1.2]`. Beide > 0. Ohne Angabe aus `zoom_rate` und Dauer gerechnet. |
| `c` | [float, float, float, float] | Start- und Ziel-Bildmitte als `[x0, y0, x1, y1]`, normalisiert auf `[0, 1]`. `[0.5, 0.5, 0.5, 0.5]` steht still. |
| `ease` | enum | `smoothstep` oder `linear`. |
| `engine` | enum | `zoompan` oder `scale16`, nur für dieses Bild. |

Die Bewegung ist über die **volle sichtbare Spanne** definiert — exklusiver
Anteil plus die angrenzenden Übergangshälften. Das xfade-Segment wertet
dieselben Ausdrücke beider Nachbarn mit passendem Frame-Offset aus, damit die
Bewegung durch die Blende hindurch weiterläuft.

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
| `before` | string | – | [Medien-ID](#medien-ids), **vor** der die Folie steht. Genau eines von `before` und `at`. |
| `at` | int | – | Position in der Medienfolge. `at: 0` ist der Auftakt vor allem Material. |
| `title` | string | – | Überschrift. Pflicht und nicht leer. |
| `subtitle` | string | `auto` | Zweite Zeile. `auto` bildet `Tag 11 · 24. Juli` aus dem Aufnahmezeitpunkt des folgenden Bildes; Tag 1 ist das früheste Aufnahmedatum des Projekts. Weglassen mit `subtitle: null`. |
| `bg` | string | `auto` | Wie am Segment, **zusätzlich als [Medien-ID](#medien-ids)** (`bg: img_075`). In dieser Datei stehen IDs; einen Cache-Pfad müsste man erst nachschlagen. `build` löst sie auf und schreibt den Pfad nach `edit.yaml`. Was weder ID noch bekannter Pfad, Farbfläche oder `none` ist, bricht mit Nennung des Kapitels ab. |
| `beats`, `dur` | float | von `defaults.title` | Standzeit. |
| `style` | enum | `card` | Wie am Segment. |
| `motion` | enum | von `defaults.title.motion` | Wie am Segment. `none` lässt die Folie stillstehen. |
| `kb` | Objekt | – | Wie am Segment. Setzt die Fokusblenden-Kopplung außer Kraft. |

Verankert wird an **Medien-IDs**, nicht an Segmentindizes oder Zeiten: IDs sind
gegen Umsortieren und gegen zusätzliche Bilder stabil, alles andere verrutscht
beim nächsten `build`. Eine ID, die es nicht gibt, ist ein Fehler mit Nennung
des Kapitels — kein stilles Überspringen.

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

### Die Grenzen finden lassen

```
slideshow chapters                 # -> chapters.yaml mit leeren Überschriften
slideshow chapters --min-jump 20   # Ortssprung-Schwelle in km (Default 30)
slideshow chapters --min-gap 12    # Zeitlücke in Stunden (Default 20)
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

## Präzedenz der Dauer

Für ein Standbild gilt, von oben nach unten, die erste zutreffende Regel:

1. **`dur:` am Segment** — explizite Sekunden gewinnen immer. Bei
   `snap_back: true` wird danach auf den nächsten Beat aufgerundet.
2. **`beats:` am Segment** — nur in einer Beat-Region.
3. **`beats_per_still` / `still_seconds` an der Region** — der regionale Takt.
4. **`defaults.beats_per_still` / `defaults.still_seconds`** — der globale Takt.

Der Standard-Slot einer Beat-Region ist **absolut** definiert
(`beats_per_still` Beats ab dem nächsten Rasterbeat), nicht relativ zum
Cursor. Deshalb findet schon das folgende Bild nach einem Override von selbst
aufs Raster zurück — ein Tippfehler in `dur:` kann den Rest des Films nicht aus
dem Takt bringen.

---

## Häufige Eingriffe

**Ein Bild länger stehen lassen**

```yaml
- {type: still, src: cache/img_DSC06300.jpg, dur: 8}
```

**Bewegung für ein Bild abschalten**

```yaml
- type: still
  src: cache/img_DSC06300.jpg
  kb: {z: [1.0, 1.0], c: [0.5, 0.5, 0.5, 0.5]}
```

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
- **Von Hand nachziehen.** Für eine freie Reihenfolge, die sich nicht aus
  Zeitstempeln ergibt, führt derzeit kein Weg daran vorbei. Bei vielen
  Segmenten ist das mühsam; ein Kommando, das eine bestehende Reihenfolge über
  einen Neubau rettet, gibt es noch nicht.

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
| `edit.yaml` | **Diese Datei** (`build`). |
| `out/timeline.json` | Die *aufgelöste* Timeline mit absoluten Framenummern. Erzeugnis, kein Eingabeformat — zum Nachrechnen, nicht zum Editieren. |
