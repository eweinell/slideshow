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
| `alternate` | bool | `true` | Zoomrichtung von Bild zu Bild wechseln. Hundertmal hineinzoomen ermüdet. |
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
mit `a = Schwenkweg / 2`. Die Richtung rotiert deterministisch über acht auf
Länge 1 normierte Vektoren nach Segmentindex — alle acht legen denselben Weg
zurück.

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
| `auto` | bool | `true` | Automatisch zwischen alle benachbarten Segmente Blenden setzen. Harte Schnitte zwischen hundert Standbildern wirken abgehackt. |
| `beats` | float | `1.0` | Standarddauer einer Blende in Beats (Beat-Region). |
| `dur` | float | – | Standarddauer in Sekunden. In `free`-Regionen der einzig sinnvolle Weg. |
| `mode` | string | `dissolve` | Siehe [Blendenmodi](#blendenmodi). |

Der Schnittpunkt liegt **auf** dem Beat; eine Blende der Dauer `T` belegt
`[t − T/2, t + T/2]`. Der Schnitt bleibt also auf dem Raster, die Blende ist
darüber zentriert.

---

## `segments`

Die Abfolge. Drei Typen, unterschieden über `type`.

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
| `src` | string | – | Projektrelativer Pfad, üblicherweise nach `cache/`. Pflicht. |
| `dur` | float\|string | – | Explizite Dauer in Sekunden. **Gewinnt immer.** |
| `beats` | float | – | Dauer in Beats. Nur in einer Beat-Region gültig — in einer `free`-Region ist es ein Fehler mit Angabe der Region. Gebrochene Werte (`1.5`) sind erlaubt. |
| `hold` | bool | `false` | Ruhiges Bild über eine lange Stille. Wird von `build` gesetzt, lässt sich aber erzwingen. |
| `snap_back` | bool | von `defaults` | Nur für dieses Segment. |
| `portrait` | enum | von `defaults` | Nur für dieses Bild. |
| `kb` | Objekt | von `defaults.kb` | Siehe unten. |

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

**Alle Übergänge weg, nur harte Schnitte**

`defaults.xfade.auto: false` setzen und neu bauen (`slideshow build --no-xfade`)
— das Löschen von Hand ist bei hundert Segmenten Sisyphusarbeit, weil alle
`from`/`to` nachziehen müssen.

**Banding im Himmel bekämpfen**

`defaults.kb.engine: scale16` (oder `slideshow build --kb-engine scale16`).

**Ohne Ausblende enden**

`defaults.fade_out: 0`.

**Reihenfolge ändern**

Die Segmente umsortieren. Die `from`/`to` der Übergänge müssen mitwandern —
einfacher ist es, die Blenden zu entfernen und mit `auto: true` neu setzen zu
lassen.

---

## Verwandte Dateien

| Datei | Rolle |
|---|---|
| `manifest.json` | Was für Material vorliegt (`probe`, `preprocess`). |
| `beats.yaml` | Regionenkarte der Tonspur (`beats`) — vor dem Bauen ansehen. |
| `edit.yaml` | **Diese Datei** (`build`). |
| `out/timeline.json` | Die *aufgelöste* Timeline mit absoluten Framenummern. Erzeugnis, kein Eingabeformat — zum Nachrechnen, nicht zum Editieren. |
