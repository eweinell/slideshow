# Briefing: Titel- und Zwischenfolien

**Status:** Stufe 2 (Einbettung) **umgesetzt**, Stufe 1 (Generator) und Stufe 3
(Kapitelerkennung) offen · **Betrifft:** neues Modul `src/slideshow/titles.py`,
`models.py`, `build.py`, `planner.py`, `mlt.py`, `cli.py`,
`docs/edit-yaml.md` · **Vorbedingung:** keine

> **Was heute läuft.** `slideshow build --chapters chapters.yaml` erzeugt eine
> vollständige Edit-List mit `type: title`-Segmenten: Phrasenlage ausgerichtet,
> Stille-Regel angewandt, Fokusblende mit gekoppelter `kb:` gesetzt, Rundlauf
> und Deckungsrechnung stimmen. Was fehlt, ist das **Bild**:
> `titles.render_title` wirft noch einen `SlideshowError`, `render` kann eine
> Titelfolie also nicht encodieren. Die Nahtstelle dorthin — Schriftfindung,
> Layoutparameter, Assetpfad, Frische-Schlüssel — steht bereits und ist
> beschrieben in Abschnitt 5.
>
> **Eine Abweichung vom Plan:** Schrift- und Hintergrund-Hash gehen nicht in
> den *Dateinamen* des Assets ein, sondern in den *Frische-Schlüssel* daneben
> (`.key`-Datei, Muster aus `preprocess.py`). Damit lässt sich der Pfad ohne
> Datei-I/O berechnen, `plan_from_edit` bleibt eine reine Funktion über
> `edit.yaml`, und die Zusage aus Abschnitt 8 bleibt trotzdem erhalten: eine
> unter WSL erzeugte Folie wird unter Windows neu gebacken. Begründung im
> Modul-Docstring von `titles.py`.

Eine Urlaubs-Slideshow über drei Wochen und vier Städte ist ohne Gliederung ein
Strom aus 100 gleichwertigen Bildern. Was fehlt, ist die Zäsur: *hier endet
Kopenhagen, hier beginnt Malmö.* Dieses Briefing beschreibt Folien mit
**Überschrift, zweiter Zeile und einem Hintergrund**, die genau diese Zäsur
setzen — und zwar so, dass sie sich in den Fluss einfügen statt ihn zu
zerschneiden.

Der Anspruch ist damit höher als „Bild mit Text drauf". Eine Titelfolie muss:

1. **auf dem Raster liegen** — und zwar nicht auf irgendeinem Beat, sondern auf
   einem musikalisch sinnvollen (Entscheidung 3); und dort, wo es kein Raster
   gibt — in `free`-Regionen und in Stille — schlicht so lange stehen wie jedes
   andere Bild (Entscheidung 3b);
2. **aus dem Material kommen** — der Hintergrund ist das erste Bild der neuen
   Stadt, unscharf; nicht ein Fremdkörper aus einer Vorlagensammlung;
3. **wieder auflösen** — der Übergang zurück ins Material ist Teil der Idee,
   nicht ein Nachgedanke (Entscheidung 5);
4. **die Zusagen des Werkzeugs nicht brechen** — Einzelsegment-Caching,
   deterministische Timeline, Rundlauf durch `edit.yaml`.

---

## 1. Was bereits da ist

Der größte Teil der nötigen Mechanik existiert und wird nur noch verbunden.
Das ist der Grund, warum dieses Vorhaben klein bleibt — vorausgesetzt, man
widersteht der Versuchung, einen zweiten Renderpfad aufzumachen.

| Vorhanden | Ort | Was es hier beiträgt |
|---|---|---|
| Blur-Komposit für Hochformat | `preprocess.py:141` `_portrait_composite` | genau der gesuchte Hintergrund: formatfüllend, `gblur` σ≈60, ~25 % abgedunkelt — inklusive des Shrink-8-Tricks, der den Blur ~50× billiger macht |
| Normalisierung eines Fotos | `preprocess.py:79` `process_image` | EXIF-Orientation, ICC → sRGB, Lanczos auf Normalform. Ein Titelhintergrund braucht exakt dasselbe |
| Standbild-Renderpfad | `render.py:75` `_still_stream` | eine Titelfolie *ist* ein Standbild. Ken Burns, Framegrenzen, Encoder-Profil unverändert |
| Übergänge als eigene Segmente | `planner.py:469` `resolve` | die Blende in die Folie hinein und aus ihr heraus ist ohne Zutun schon ein eigenständig cachebares Segment |
| Bewegung über die volle sichtbare Spanne | `render.py:67` `_motion_for` | die Kamerafahrt läuft durch die Blende hindurch weiter — Voraussetzung für die Fokusblende aus Entscheidung 5 |
| Regionsraster | `planner.py:60` `RegionGrid` | `beat_index_at_or_after` und `snap_up` sind bereits die Werkzeuge für die Phrasenlage |
| Content-Hash-Cache | `cache.py` `HashIndex`, `cache_key` | eine generierte Folie ist eine Datei wie jede andere und cacht sich mit |

Nicht vorhanden: eine Textausgabe. `doctor` prüft heute `zoompan`, `xfade`,
`scale`, `format` (`doctor.py:561`) — `drawtext` steht dort nicht, und keine
Phase kennt eine Schriftdatei.

---

## 2. Gestalt

Bevor über Code zu reden ist: wie sieht die Folie aus? Die Zahlen sind
Vorschläge, aber sie sind gerechnet und gehören als Defaults in den Code, nicht
in die Fantasie des Anwenders.

```
┌──────────────────────────────────────────────────────────┐  ← 3840 × 2160
│                                                          │
│   ░░░ erstes Foto der neuen Stadt, σ 60 blur, −45 % ░░░  │
│                                                          │
│                     M A L M Ö                            │  Versalhöhe 7,5 % H
│                    ───────────                           │  Linie 0,12 × Höhe
│                 Tag 11 · 24. Juli                        │  0,42 × Überschrift
│                                                          │
│  ← 10 % Safe Area ─────────────────────── 10 % Safe →    │
└──────────────────────────────────────────────────────────┘
```

| Größe | Vorschlag | Begründung |
|---|---|---|
| Versalhöhe Überschrift | 0,075 × Bildhöhe (162 px @ 2160) | auf einem 55″-Fernseher aus 3 m so groß wie eine Zeitungsschlagzeile; auf dem Handy noch lesbar |
| Zweite Zeile | 0,42 × Überschrift (68 px) | deutlich untergeordnet, aber über der Grenze, ab der Kompression sie auffrisst |
| Sperrung | Überschrift +0,04 em, zweite Zeile +0,10 em | gesperrte Versalien wirken ruhig; ungesperrt wirken sie gedrängt |
| Zeilenabstand | 0,55 × Versalhöhe zwischen Linie und Zeilen | |
| Safe Area | 10 % ringsum | überlebt TV-Overscan und einen 4:5-Beschnitt für Social Media |
| Satzachse | optisch mittig, Textblock auf 0,52 × H zentriert | geometrisch mittig wirkt zu tief; 2 % nach oben korrigiert das |
| Blur | σ 60 auf 7680er Basis (= σ 30 auf 4K) | derselbe Wert wie das Hochformat-Komposit — die beiden Bildsprachen müssen zusammenpassen |
| Abdunklung | Startwert 0,55, gemessen nachgeführt | siehe unten |

**Kontrast wird gemessen, nicht gehofft.** Ein Sonnenuntergang bleibt auch
unscharf hell; 25 % Abdunklung wie beim Hochformat-Komposit reichen dort nicht.
Der Generator misst die Leuchtdichte des Hintergrunds **unter der
Textbounding-Box** und zieht die Abdunklung (bzw. einen Verlaufs-Scrim von
unten) nach, bis das Kontrastverhältnis ≥ 4,5:1 liegt — deterministisch, in
festen Schritten, mit Obergrenze und Warnung, wenn die Grenze nicht erreicht
wird. Das ist billig (Pillow, ein `resize` auf 64 px genügt) und macht aus einer
Geschmacksfrage ein Abnahmekriterium (T6). Der Messcode steht im Anhang.

**Zweite Zeile automatisch.** `subtitle: auto` formatiert den
Aufnahmezeitpunkt des folgenden Bildes aus dem Manifest
(`capture_time`, `probe.py:543` liefert die Chronologie ohnehin schon):
*„Tag 11 · 24. Juli"*. Der Tageszähler ergibt sich aus dem ersten
Aufnahmedatum des Projekts. Die Überschrift bleibt Handarbeit — einen
Ortsnamen kann das Werkzeug nicht erfinden, und es soll es auch nicht
versuchen.

---

## 3. Vorgeschlagene Syntax

```yaml
segments:
  - {type: still, src: cache/img_041.jpg, beats: 8}
  - {type: xfade, from: 40, to: 42, beats: 1.5}
  - {type: title, title: Malmö, subtitle: auto, bg: auto, beats: 12}
  - {type: xfade, from: 41, to: 43, beats: 2, mode: dissolve}
  - {type: still, src: cache/img_042.jpg, beats: 8}
```

Felder:

| Feld | Werte | Bedeutung |
|---|---|---|
| `title` | Text | Überschrift. Pflicht — eine Folie ohne Überschrift ist ein Fehler, kein Sonderfall |
| `subtitle` | Text \| `auto` \| entfällt | zweite Zeile |
| `bg` | `auto` (Default) \| Pfad \| `#rrggbb` \| `none` | Hintergrundquelle, siehe Entscheidung 4 |
| `beats` / `dur` | Zahl | wie beim Still; `beats` nur in Beat-Regionen, `dur` in `free`-Regionen (Entscheidung 3) |
| `snap_back` | `true` \| `false` | wie beim Still; in langer Stille zwingend `false`, siehe Entscheidung 3 |
| `style` | `card` (Default) \| `lower-third` | Reserviert für Stufe 2, siehe Entscheidung 1 |
| `kb` | `KBSpec` | wie beim Still; `{z: [1, 1]}` erzeugt eine ruhende Karte |

Und die Defaults, neben `kb:` und `xfade:` in `defaults:`:

```yaml
defaults:
  title:
    beats: 12            # Standzeit in Beat-Regionen
                         # in free-Regionen gilt still_seconds — die
                         # Standardlänge der Bildanzeige, ohne Sonderregel
    phrase_beats: 8      # Titel beginnen auf Phrasengrenzen (Entscheidung 3)
    font: auto           # Pfad; SLIDESHOW_FONT gewinnt (analog SLIDESHOW_MELT)
    size: 0.075          # Versalhöhe der Überschrift, Anteil der Bildhöhe
    subtitle_scale: 0.42
    blur: 60             # Sigma auf 7680er Basis
    darken: 0.55         # Startwert; wird nach Messung nachgeführt
    min_contrast: 4.5
    safe: 0.10
```

---

## 4. Entscheidungen

**Alle sieben Entscheidungen sind gefallen, jeweils der Empfehlung folgend.**
Die verworfenen Alternativen bleiben mit ihrer Begründung stehen — wer später
an einer Stelle zweifelt, soll nachlesen können, was dagegen sprach, statt die
Abwägung neu zu führen.

### Entscheidung 1 — Wo entsteht die Folie?

**(a) Gebackenes Bild aus der Absicht** *(Empfehlung)*
Ein Generator erzeugt aus `title`/`subtitle`/`bg` + Layoutparametern ein
fertiges Bild in `cache/title_<key>.jpg`, inhaltsadressiert wie jedes andere
Zwischenprodukt. Von da an ist die Folie ein Standbild wie jedes andere.

- **dafür:** `render.py`, `planner.py`, `mlt.py`, Concat und Encoderprofile
  bleiben **unangetastet**. Ken Burns, Blenden, Segment-Caching und der
  MLT-Export funktionieren, ohne dass sie von Titeln wissen. Typografie in
  Pillow ist der ffmpeg-Textausgabe deutlich überlegen: echte Sperrung,
  Umlaute ohne Escaping-Fallen, mehrzeiliger Satz, Verlaufs-Scrim,
  gemessener Kontrast. Keine neue ffmpeg-Abhängigkeit.
- **dagegen:** Der Text ist in die Pixel eingebrannt und zoomt mit der
  Ken-Burns-Bewegung mit. Textänderung heißt Neugenerierung — aber die ist
  automatisch und dauert unter einer Sekunde.

Der eingebrannte Text ist weniger schlimm, als er klingt: die Folie wird auf
7680 × 4320 erzeugt (`preprocess.LONG_EDGE`) und bei ≤ 1,3× Zoom auf 4K
heruntergerechnet — der Text ist also durchgehend mindestens zweifach
überabgetastet und bleibt scharf.

**(b) `drawtext` im Filtergraph**
Der Text entsteht beim Rendern, das Bild darunter bewegt sich.

- **dafür:** rasiermesserscharfer, stehender Text über bewegtem Hintergrund —
  der klassische Dokumentarfilm-Look. Text und Bild können unabhängig ein-
  und ausblenden.
- **dagegen:** braucht `libfreetype` im ffmpeg-Build (heute nicht geprüft),
  eine Schriftdatei mit plattformabhängigem Pfad, und das
  `drawtext`-Escaping ist unter Windows berüchtigt (`C\:/Windows/fonts/…`,
  Doppelpunkte und Apostrophe im Text). Der MLT-Export bräuchte eine
  **zweite**, unabhängige Implementierung über Kdenlive-Titelclips — zwei
  Wahrheiten für dasselbe Bild.

**(c) Zwei Ebenen: Hintergrund gebacken, Text als Overlay**
Der Generator legt zwei Dateien ab (Hintergrund als JPEG, Text als PNG mit
Alpha); der Renderer legt sie mit `overlay` übereinander.

- **dafür:** derselbe stehende, scharfe Text wie (b), aber ohne
  `drawtext`-Abhängigkeit. Ermöglicht die `lower-third`-Variante: Ortsname über
  dem *laufenden* ersten Foto, ganz ohne eigene Folie.
- **dagegen:** Der Standbild-Pfad in `render.py` müsste von `-vf` auf
  `-filter_complex` umgestellt werden, und zwar auch im xfade-Pfad, der bereits
  zwei Ströme mischt (`render.py:172 ff.`). Das ist der einzige Vorschlag hier,
  der echten Umbau am Renderer bedeutet.

> **Empfehlung: (a) — aber der Generator wird von Anfang an zweischichtig
> gebaut.** Er erzeugt intern eine Hintergrundebene und eine Textebene mit
> Alpha und flacht sie in Stufe 1 beim Schreiben zusammen. Zeigt die
> Sichtprüfung, dass mitzoomender Text stört, entfällt in Stufe 2 nur das
> Zusammenflachen — Layout, Kontrastmessung, Schriftfindung und Cache-Keys
> bleiben identisch. Die Entscheidung ist damit umkehrbar, ohne dass etwas
> weggeworfen wird. (b) ist die einzige Variante, die diesen Weg verbaut.

### Entscheidung 2 — Eigener Segmenttyp oder Feld am Still?

**(a) `type: title` als eigener Typ** *(Empfehlung)* — neue Klasse
`TitleSegment` in der `Segment`-Union (`models.py:421`).

- **dafür:** liest sich in der YAML-Datei als das, was es ist. `extra="forbid"`
  erzwingt saubere Felder; eine Folie ohne `title` scheitert beim Laden mit
  Pfad und Zeile statt beim Rendern mit einem leeren Bild.
- **dagegen:** drei Stellen müssen mitwandern, sonst wird es unangenehm:
  `_DISCRIMINATORS` (`models.py:535`, sonst steht `segments[41].title.title`
  im Fehlertext), `_segment_from_slot` (`build.py:169`, sonst wird eine
  Titelfolie beim Rundlauf zum gewöhnlichen Still degradiert — das fängt
  `tests/test_roundtrip.py` ab) und der `StillSegment`-Zweig im MLT-Export
  (`mlt.py:299`).

**(b) `title:`-Block am `StillSegment`** — weniger Berührungspunkte, aber `src`
wird optional und damit die Bedeutung des Feldes unscharf. Zwei Zustände
desselben Typs, die sich gegenseitig ausschließen, sind genau die Art
Modellierung, die später Sonderfälle nach sich zieht.

> **Empfehlung: (a).** Im Planer bleibt der Titel trotzdem ein
> `Intent(kind="still")` mit generiertem `src` — der Planer muss von Titeln
> gar nichts wissen. Die Unterscheidung lebt ausschließlich im Schema und in
> `build.py`.

### Entscheidung 3 — Wann darf eine Titelfolie beginnen?

Das ist die eigentliche Frage hinter „harmonisch einbetten".

Ein Schnitt auf *irgendeinem* Beat ist synchron, aber nicht musikalisch. Musik
ist in Phrasen gegliedert — typischerweise 4 oder 8 Beats. Ein Bildwechsel
mitten in der Phrase fällt kaum auf; eine **Zäsur** mitten in der Phrase fällt
sofort auf, und zwar als Fehler. Eine Titelfolie gehört auf die Eins.

**(a) Wie jedes andere Bild** — nächster Rasterbeat.
Nichts zu tun, aber die Folie landet mit 7/8 Wahrscheinlichkeit neben der
Phrasengrenze.

**(b) Phrasenlage, im Planer erzwungen** — `plan_slots` zieht den Startpunkt
einer Titelfolie auf das nächste Vielfache von `phrase_beats` hoch.

- **dagegen:** Der Planer bekommt eine Vorausschau („das *nächste* Intent ist
  ein Titel"), die er heute nicht hat, und das Strecken des Vorgängers
  kollidiert mit Regionsenden und `_clamp_transitions`.

**(c) Phrasenlage, in `build` materialisiert** *(Empfehlung)*
`build` rechnet die Phrasengrenze aus und schreibt dem **vorangehenden** Bild
ein explizites `beats:` — die Ausrichtung wird damit zu gewöhnlicher Absicht in
der Edit-List, die der Planer ohne jede Sonderregel ausführt.

- **dafür:** `planner.py` bleibt vollständig unberührt. Das Ergebnis steht
  sichtbar in `edit.yaml` („dieses Bild steht 11 Beats, damit der Titel auf die
  Eins fällt") und lässt sich von Hand überstimmen — Prinzip 1.
- **dagegen:** Wer später ein früheres Bild verlängert, verschiebt die
  Phrasenlage, ohne dass etwas protestiert. Gegenmittel: `build` und
  `validate_edit` prüfen die Lage jeder Titelfolie und warnen mit konkretem
  Vorschlag („Titel *Malmö* beginnt auf Beat 37, nächste Phrasengrenze wäre 40 —
  `beats:` des Vorgängers von 8 auf 11").

Regel für die Auswahl der Grenze: die **nächstgelegene** Phrasengrenze
innerhalb von ± ½ Phrase; liegt keine in Reichweite, bleibt es beim
Standardverhalten und es wird gewarnt. So wird der Vorgänger nie mehr als vier
Beats gedehnt oder gestaucht.

**Standzeit in Beat-Regionen.** `beats: 12` sind bei 152 BPM 4,7 s. Als
Untergrenze gilt eine Lesezeitregel: `1,8 s + 0,25 s je Wort`, aufgerundet auf
ein Vielfaches der Phrase. „Malmö / Tag 11 · 24. Juli" sind fünf Wörter →
3,05 s → 8 Beats (3,2 s) genügen; 12 Beats geben der Zäsur mehr Ruhe. Der
Default bleibt 12, der Generator warnt nur, wenn die Untergrenze unterschritten
wird.

### Entscheidung 3b — Titelfolien in `free`-Regionen und in Stille

Eine `free`-Region entsteht aus zwei sehr verschiedenen Gründen
(`models.py:215`): da läuft Musik, die sich nur nicht rastern ließ
(`quiet: false`), oder da ist tatsächlich nichts zu hören (`quiet: true`). Für
Titelfolien gilt in beiden Fällen dieselbe Zusage: **es gilt die Standardlänge
der Bildanzeige.** Eine Titelfolie steht dort genauso lange wie die Bilder um
sie herum, mit derselben Präzedenz wie im `RegionGrid` (`planner.py:82`) —
`Region.still_seconds` vor `Defaults.still_seconds`. Phrasen gibt es hier
nicht; die Ausrichtung aus Entscheidung 3 entfällt ersatzlos.

Im Regelfall stellt sich das von selbst ein: `default_end` (`planner.py:98`)
liefert die nächste Kante der `linspace`-Kachelung, und die **ist** die
Standardlänge. Es gibt aber genau einen Fall, in dem es still schiefgeht:

> **Lange Stille kachelt nicht.** Ist eine Region `quiet` und länger als
> `hold_seconds` (12 s), liefert `_free_count` (`planner.py:136`) **`n = 1`** —
> die ganze Stille ist *ein* Slot, damit dort bewusst ein ruhiges Einzelbild
> stehen bleiben kann. Eine Titelfolie an dieser Stelle bekäme die **gesamte**
> Stille: zwanzig Sekunden Standbild mit „Malmö" darauf.

Der naheliegende Rettungsweg über `dur:` führt ohne Zutun in dieselbe Falle:
`snap_back` ist per Default an, und `snap_up` (`planner.py:119`) sucht die
nächste Kante — in einer `hold`-Region gibt es genau eine, nämlich das
Regionsende. Der Override würde also wieder auf die volle Länge aufgerundet.

**Regel, die `build` deshalb umsetzt.** Beides sind vorhandene Felder; der
Planer bleibt unangetastet:

```yaml
- {type: title, title: Malmö, subtitle: auto, bg: auto,
   dur: 4.0, snap_back: false}     # lange Stille: Standardlänge, nicht aufrunden
```

| Lage | Was `build` schreibt |
|---|---|
| `free`, ohne `hold` | nichts — die Kachelung liefert die Standardlänge von selbst |
| `free`, mit `hold` (lange Stille) | `dur: still_seconds` **und** `snap_back: false` |
| Region kürzer als die Standardlänge | Folie wird am Regionsende geklemmt (`plan_slots`, `region_end_frame`); unterschreitet das die Lesezeit, folgt eine Warnung mit dem Vorschlag, das Kapitel eine Region weiter zu setzen |

Der Rest der Stille fällt damit an das folgende Bild, das seinen `hold`-Status
behält und ruhig stehen bleibt — genau die Aufteilung, die gemeint ist: vier
Sekunden Titel, sechzehn Sekunden Ruhe. Die Lesezeitregel bleibt auch hier die
Untergrenze: liegt `1,8 s + 0,25 s je Wort` über `still_seconds`, schreibt
`build` den größeren Wert und vermerkt es im Bericht. Sichtbar in der Datei und
von Hand korrigierbar, nicht stillschweigend.

**Umgekehrt ist die Stille der beste Platz für eine Zäsur, den es gibt.** Die
Pause zwischen zwei Tracks ist bereits eine musikalische Kapitelgrenze; die
Track-Grenzen stehen als `manifest.audio.tracks` (`models.py:127`) ohnehin im
Manifest und sind Seed der Regionserkennung. Dort fällt der Ortswechsel mit
einem Wechsel im Ton zusammen, und die Folie muss den Fluss nicht
unterbrechen — an dieser Stelle ist ohnehin einer. `slideshow chapters`
(Entscheidung 6) schlägt solche Punkte deshalb mit vor.

**Ohne Tonspur — und warum `material_seconds` Titel mitzählen muss.** Seit
der Musik-optional-Unterstützung bestimmt bei fehlender Tonspur das *Material*
die Laufzeit: `_timeline_length` (`build.py:106`) ruft `material_seconds`
(`planner.py:575`), und `fit_regions_to` (`planner.py:611`) zieht die
Regionenkarte auf diese Länge nach. Die Karte besteht dann aus einer einzigen
`free`-Region mit `quiet: false` — für Titelfolien also der unkomplizierte
Fall aus der Tabelle oben, Standardlänge ohne jeden Override.

Eine Falle steckt trotzdem darin: `material_seconds` rechnet mit `n_media` —
der Zahl der Medien aus dem Manifest. **Eine Titelfolie ist kein Medium, belegt
aber einen Slot.** Zählt man sie nicht mit, ist die Materiallänge je Titel um
einen Standard-Slot zu kurz. Mit Tonspur kippt dadurch die Abwägung „Musik oder
Material gibt die Laufzeit vor" an der Toleranzgrenze `standard_slot`
(`planner.py:602`); ohne Tonspur fehlt dem Film schlicht je Titel dessen
Standzeit, und die zugeschnittene Karte deckt die Timeline nicht mehr. `build`
muss `n_media` deshalb um die Kapitel erhöhen, bevor es `_timeline_length`
ruft — eine Zeile, die man genau einmal übersieht (Abnahmekriterium T11).

Zwei Randbemerkungen zur Wechselwirkung mit der Beat-Erkennung:

- Seit `briefing-beat-detection.md` umgesetzt ist, zerfällt ein durchgehender
  Track in viele kurze Beat-Regionen (`MAX_FIT_WINDOW`, 20 s, danach
  `merge_adjacent_beats`). Eine **Regionsgrenze ist immer eine zulässige
  Titelposition** — sie ist per Konstruktion eine musikalische Grenze, und die
  Phrasenrechnung entfällt dort. Das macht die Platzierung *einfacher*, nicht
  schwerer.
- Die Abdeckung liegt bei 88,2 %; der Rest sind `free`-Regionen *mit* Musik
  (`quiet: false`) — beim Testtrack der rhythmisch dünne Ausklang. Auch dort
  greift die Standardlänge; Phrasenlage ist mangels Raster nicht bestimmbar,
  und `build` sagt das im Bericht, statt eine Genauigkeit vorzutäuschen, die es
  nicht gibt.

### Entscheidung 4 — Woher kommt der Hintergrund?

**(a) Erstes Bild des neuen Abschnitts, unscharf** *(Empfehlung, `bg: auto`)*
Der Titel kündigt an, was kommt; der Hintergrund zeigt es bereits, nur noch
nicht lesbar. Und er ermöglicht die Fokusblende aus Entscheidung 5.

**(b) Letztes Bild des vorigen Abschnitts** — Rückblickslogik. Wirkt wie ein
Abspann statt wie ein Kapitelanfang, und das Bild war eine Sekunde zuvor
scharf zu sehen; unscharf wiederholt wirkt es wie ein Fehler.

**(c) Explizites Bild** (`bg: cache/img_042.jpg` oder ein Originalpfad) — muss
es geben, für den Fall, dass das automatisch gewählte Bild ein Nahaufnahme
eines Tellers ist.

**(d) Farbfläche** (`bg: "#1b2a3a"`) — eine ruhige, gesetzte Variante. Sinnvoll
als Auftakt-Titel des ganzen Films, wo es noch kein „nächstes Bild" gibt. Die
Farbe lässt sich beim Generieren aus der Dominanzfarbe des Folgebildes
vorschlagen (Pillow: `resize((1,1))` auf einer entsättigten, abgedunkelten
Variante) und wird als Hex-Wert in `edit.yaml` geschrieben — damit bleibt sie
sichtbar und editierbar statt als Zauberei im Code.

**(e) `bg: none`** — Text auf Schwarz. Der harte Kapitelschnitt. Gehört ins
Repertoire, ist aber nicht der Default, weil er den Fluss ausdrücklich bricht.

> **Empfehlung: (a) als Default, (c)/(d)/(e) als Überschreibung.**

**Wichtig für die Umsetzung:** Als Quelle für den Hintergrund dient das
**Original** (`MediaItem.path`), nicht das Zwischenprodukt aus `cache/`. Bei
einem Hochformat-Foto ist das Zwischenprodukt bereits ein Blur-Komposit; ein
zweiter Blur darüber ergäbe einen verwaschenen Rahmen um ein leicht
verwaschenes Hochformat. `process_image` (`preprocess.py:79`) leistet
Orientation, ICC und Beschnitt ohnehin — für den Titelhintergrund wird es mit
`portrait_mode="crop"` plus Blur- und Abdunklungsparametern aufgerufen. Beim
vollständig unscharfen Hintergrund fällt der Beschnitt eines Hochformats nicht
auf.

### Entscheidung 5 — Wie kommt die Folie in den Fluss hinein und wieder heraus?

Die Standardblenden greifen automatisch (`XfadeDefaults.auto = true`), aber die
Zäsur verdient eine eigene Choreografie.

**(a) Symmetrisch, Standarddauer** — funktioniert, ist unauffällig.

**(b) Asymmetrisch** — länger hinein (1,5–2 Beats), kürzer heraus (1 Beat).
Der Film „atmet ein" in die Zäsur und setzt danach neu an.

**(c) `fadeblack`** — bewusster Aktschluss. Bricht den Fluss, gehört aber ins
Repertoire (`mode: fadeblack` steht in `_XFADE_MODES` bereits bereit).

**(d) Fokusblende** *(Empfehlung, in Verbindung mit `bg: auto`)*
Die Blende *aus* der Titelfolie heraus führt auf **dasselbe Bild, scharf**. Der
Hintergrund löst sich vor den Augen des Zuschauers auf; aus der Ahnung wird das
Foto.

```
   Kopenhagen                Titelfolie „Malmö"            Malmö
├── Bild 41 ───┤├─ xfade ─┤├──── unscharf ────┤├─ xfade ─┤├── Bild 42 ───┤
                                                    ↑            ↑
                             Phrasenbeginn     derselbe Hintergrund,
                            (Beat 8k)          jetzt scharf
```

Das kostet **keine neue Mechanik**: es ist eine gewöhnliche `xfade` zwischen
zwei Standbildern, die zufällig aus derselben Quelle stammen. Der Cache-Hash
des Übergangssegments schließt beide Nachbarn ein — das stimmt weiterhin.

Damit die Auflösung wirklich wie ein Schärfezug wirkt und nicht wie ein
Bildwechsel, muss die **Kamerafahrt über die Blende hinweg stetig sein**: die
Ken-Burns-Bewegung der Titelfolie endet dort, wo die des Folgebildes beginnt
(gleiche Bildmitte, stetiger Zoom). Das Werkzeug definiert die Bewegung ohnehin
über die volle sichtbare Spanne und wertet im xfade-Segment beide Ausdrücke mit
passendem Frame-Offset aus (`render.py:67`, README „Übergangs-Mechanik") — es
fehlt nur die Vorgabe, die die beiden `KBSpec` aneinander koppelt. `build`
schreibt sie beim Erzeugen der Fokusblende explizit in beide Segmente, damit sie
sichtbar und korrigierbar bleibt.

Ein Nebengewinn, der die Kopplung zusätzlich rechtfertigt: `CLAUDE.md` führt
unter den offenen Baustellen den **Schwenk am Zoomanfang** — ein Hineinzoom
beginnt bei `z = 1,0`, dort ist der Ausschnitt das ganze Bild, und die
Bildmitte kann sich nicht bewegen; rund die Hälfte des geplanten Schwenks
bleibt unsichtbar. Setzt die Fokusblende das Folgebild auf den Endzoom der
Titelfolie (im Beispiel 1,06 statt 1,0), fängt es *oberhalb* der Klemmung an
und schwenkt von der ersten Sekunde an sichtbar. Für die Bilder direkt nach
einem Kapitelanfang ist das Problem damit nebenbei behoben.

> **Empfehlung: (d) für `bg: auto`, sonst (b).** Beides erzeugt `build`
> automatisch; beides steht danach als gewöhnliche `xfade`-Segmente in der
> Datei und lässt sich löschen oder ändern.

### Entscheidung 6 — Wie kommen die Folien in die Edit-List?

Zwölf Städte von Hand einzupflegen ist zumutbar; zwölf Städte nach jedem
`build`-Lauf **erneut** einzupflegen nicht. `build` erzeugt `edit.yaml` neu.

**(a) Nur von Hand** — geht immer, überlebt aber kein `build`.

**(b) `chapters.yaml` als Eingabe für `build`** *(Empfehlung)*

```yaml
# chapters.yaml — Kapitel der Reise. Wird von `slideshow build` eingelesen.
chapters:
  - {before: img_042, title: Malmö,      subtitle: auto}
  - {before: img_071, title: Stockholm,  subtitle: auto, bg: cache/img_075.jpg}
  - {at: 0,           title: Skandinavien 2026, subtitle: "Drei Wochen, vier Städte",
     bg: "#1b2a3a", beats: 16}
```

Verankert wird an **Medien-IDs**, nicht an Segmentindizes oder Zeiten — IDs
sind gegen Umsortieren und gegen zusätzliche Bilder stabil. `at: 0` ist der
Auftakt vor allem Material.

**(c) Kapitelvorschläge aus dem Material** — `slideshow chapters` schreibt eine
`chapters.yaml` mit gefundenen Grenzen und leeren Überschriften, die der
Anwender ausfüllt. Zwei Signale liegen bereits im Manifest oder sind billig zu
holen:

- **Zeitlücke.** `capture_time` steht im Manifest, `chronological()`
  (`probe.py:543`) sortiert danach. Eine Lücke > 8 h ist eine Tagesgrenze,
  eine Lücke > 20 h fast immer ein Ortswechsel. Zehn Zeilen Code.
- **Ortssprung.** GPS steht in den EXIF-Daten der meisten Handyfotos; `probe`
  liest EXIF ohnehin (optional über exiftool). Ein Sprung > 30 km zwischen
  zwei aufeinanderfolgenden Aufnahmen *ist* der neue Ort — das treffsicherste
  verfügbare Signal. Erfordert eine kleine Erweiterung in `probe.py`
  (`gps: [lat, lon]` am `MediaItem`).
- **Pause im Ton.** Die Kapitel hängen an Medien-IDs, die Stille aber an der
  Zeitachse — beides trifft erst nach dem Planen aufeinander. `build` prüft
  deshalb im Nachgang, wo ein Kapitel landet, und schlägt vor, es um ein oder
  zwei Bilder zu verschieben, wenn dadurch die Zäsur in eine `quiet`-Region
  oder auf eine Regionsgrenze fällt (Entscheidung 3b). Das ist ein Vorschlag im
  Bericht, keine automatische Verschiebung: welches Foto zu welcher Stadt
  gehört, weiß das Werkzeug nicht.

Die **Überschrift bleibt leer** und muss ausgefüllt werden: ein Ortsname lässt
sich ohne Netz nicht aus Koordinaten gewinnen, und ein geratener Name ist
schlimmer als kein Name. `build` bricht mit klarer Meldung ab, wenn ein Kapitel
ohne `title` dasteht.

*Nebenbei:* Liegt für die Reise eine Planung mit Stationen und Daten vor, ist
`chapters.yaml` daraus direkt erzeugbar — die Stationsnamen sind exakt die
gesuchten Überschriften. Das ist eine Zeile Konvertierung und keine
Abhängigkeit; erwähnt, weil es die lästigste Handarbeit vollständig erledigt.

> **Empfehlung: (b) umsetzen, (c) mit der Zeitlücke als erste Ausbaustufe.**
> GPS ist die deutlich bessere Erkennung, hängt aber an einer
> Manifest-Erweiterung und gehört deshalb in einen eigenen Schritt.

### Entscheidung 7 — Ken-Burns-Richtung hängt am Slot-Index

Ein Fund, der ohne diese Arbeit nicht auffällt, mit ihr aber sofort:

`plan_motion` (`kenburns.py:73`) leitet Zoomrichtung und Schwenkrichtung aus
`index % 2` bzw. `index % 8` ab — dem **Slot-Index**. Der Umbau des Schwenks
auf `pan_rate`/`pan_total` hat daran nichts geändert; er hat die Kopplung nur
ausdrücklich gemacht („deterministisch nach Segmentindex durchgereicht",
`_DIRECTIONS`). Wird eine Titelfolie an
Position 41 eingefügt, verschiebt sich der Index **jedes folgenden Bildes** um
eins. Damit ändert sich jede folgende Bewegung, damit jeder folgende Cache-Key,
damit rendert der halbe Film neu. Die Zusage aus Prinzip 2 — „ein korrigiertes
Bild löst genau drei Neurenderungen aus" — gilt fürs Einfügen also nicht.

**(a) Hinnehmen.** Titel werden meist vor dem großen Rendern gesetzt. Der
Schaden trifft nur den, der spät noch ein Kapitel einschiebt — und der wartet
dann eben einmal.

**(b) Richtung an einen stabilen Schlüssel binden** *(Empfehlung)*
Statt des Index ein deterministischer Hash über `src` (bzw. über
`title` bei Titelfolien). Die Bewegung bleibt reproduzierbar, alterniert
weiterhin sichtbar, hängt aber nicht mehr an der Position.

- **dafür:** Einfügen, Löschen und Umsortieren werden dauerhaft billig — nicht
  nur für Titel. Die Änderung betrifft eine Funktion.
- **dagegen:** Einmalig wird jedes Segment ungültig, weil sich jede Bewegung
  ändert. Das ist der Grund, es **jetzt** zu tun, zusammen mit einer Änderung,
  die ohnehin neu rendert, und nicht später einzeln.

> **Empfehlung: (b), im selben Zug.** Die Alternierung „hundertmal
> hineinzoomen ermüdet" bleibt erhalten, wenn die unterste Hash-Bit die
> Zoomrichtung bestimmt — statistisch ausgeglichen, nur nicht mehr streng
> abwechselnd. Wer strenge Alternierung will, behält den Index für die
> Zoomrichtung und nimmt den Hash nur für die Schwenkrichtung; dann bleibt
> die halbe Invalidierung. Das ist eine bewusste Abwägung und gehört als
> Kommentar an die Funktion.

---

## 5. Vorgeschlagene Umsetzung

Abgehakt ist, was in Stufe 2 steht — mit den unten vermerkten Abweichungen.
Stufe 1 und 3 stehen noch aus.

**Stufe 1 — die Folie** *(offen; Punkt 2 und 5 sind bereits da)*

1. **`titles.py`** mit `TITLE_VERSION = 1`, einer Layout-Dataclass (die Werte
   aus Abschnitt 2) und `render_title(spec, bg_source, out) -> dict`. Intern
   zweischichtig: `_background()` und `_text_layer()` mit Alpha, am Ende
   zusammengeflacht (Entscheidung 1).
2. **Schriftfindung** analog zur `melt`-Suche (`README`, „Werkzeuge werden
   nicht nur im `PATH` gesucht"): `SLIDESHOW_FONT` gewinnt, danach eine
   Kandidatenliste je Plattform (WSL: DejaVu Sans, Noto Sans; Windows: Segoe
   UI, Arial). Kein Fund → `SlideshowError` mit kopierbarem
   Installationsbefehl, kein Traceback.
3. **Kontrastschleife** wie im Anhang, mit Obergrenze und Warnung.
4. **`doctor`** bekommt eine Zeile „Schrift" mit dem gefundenen Pfad — genau
   wie bei `melt`.
5. **Cache-Key.** `cache_key([hash(bg-Quelle), hash(Schriftdatei)], {op:
   "title", v: TITLE_VERSION, ...alle Layoutparameter, Text})`. **Die
   Schriftdatei gehört in den Hash.** Sonst sieht eine unter WSL erzeugte
   Folie anders aus als dieselbe unter Windows, und der Cache merkt es nicht.

**Stufe 2 — die Einbettung** *(umgesetzt)*

6. **`TitleSegment`** in `models.py`, `_DISCRIMINATORS` erweitern,
   `Defaults.title` ergänzen.
7. **`build.py`:** `chapters.yaml` einlesen, Kapitel an Medien-IDs auflösen,
   Titel-Intents an der richtigen Stelle in die Intent-Liste einsetzen,
   Phrasenlage rechnen und als `beats:` des Vorgängers materialisieren
   (Entscheidung 3c), in `free`-Regionen stattdessen `dur:` und — bei `hold` —
   `snap_back: false` schreiben (Entscheidung 3b), Fokusblende und gekoppelte
   `KBSpec` erzeugen (Entscheidung 5d). `_segment_from_slot` muss Titel als
   Titel zurückschreiben, samt `dur`/`snap_back`; sonst kippt die Folie beim
   nächsten Rundlauf zurück in die volle Stille.
8. **Assets sicherstellen.** `render` darf nicht darauf vertrauen, dass `build`
   gelaufen ist — `slideshow render edit.yaml` mit von Hand geändertem Text ist
   ein unterstützter Weg. Also eine idempotente `ensure_title_assets(edit)` zu
   Beginn von `render` und `export-mlt`, nach dem `_is_fresh`/`_mark_fresh`-
   Muster aus `preprocess.py:61`. Kostet bei unverändertem Text nichts.
9. **`coverage`** (`planner.py:551`) zählt Titel getrennt aus. Sonst meldet der
   Bericht „12 Medien passen nicht mehr in die Musik", ohne zu sagen, dass drei
   davon Titelfolien sind, die man nicht einfach weglassen möchte.
10. **`mlt.py`:** Titelfolien als gewöhnliche Standbild-Producer exportieren
    (bei Umsetzungsvariante (a) fällt das von selbst an, es ist ein JPEG).
11. **Laufzeitrechnung.** `n_media` in `_timeline_length` um die Kapitel
    erhöhen (Entscheidung 3b, Abschnitt „Ohne Tonspur").
12. **Dokumentation.** `docs/edit-yaml.md` beschreibt die Schlüssel der
    Edit-List und ist damit Teil der Umsetzung, nicht Nacharbeit: `type: title`
    mit allen Feldern und der `defaults.title`-Block. Dazu die Zeile in der
    Tabelle „Offene Baustellen" in `CLAUDE.md`.

**Stufe 3 — die Kapitelerkennung** *(offen)*

13. `slideshow chapters` mit Zeitlücken-Heuristik, schreibt `chapters.yaml`
    mit leeren Überschriften und vorbelegten Untertiteln.
14. GPS im Manifest (`probe.py`) und Ortssprung als zweites Signal.

Nicht anzufassen: `planner.py` (außer der Zählung in `coverage`), `beats.py`,
`encoders.py`, die Concat- und Muxing-Kette.

**Abweichungen in der Umsetzung von Stufe 2**, jeweils mit Begründung im Code:

| Geplant | Umgesetzt |
|---|---|
| Schrift- und Hintergrund-Hash im Cache-Key des Dateinamens (Punkt 5) | Beide im Frische-Schlüssel daneben; der Name hängt nur an der Absicht. Sonst bräuchte `plan_from_edit` Datei-I/O. |
| `n_media` in `_timeline_length` erhöhen (Punkt 11) | Die Titel-Intents werden *vor* dem Aufruf eingesetzt, damit `len(intents)` von selbst stimmt. Eine Stelle statt zwei, die auseinanderlaufen können. |
| `plan_slots` nur einmal aufrufen | Die Lagekorrektur braucht das Ergebnis des Planens (in welcher Region landet die Folie?) und ändert danach die Absicht. `plan_with_titles` iteriert deshalb bis zu viermal; der Planer selbst bleibt unberührt. |
| — | `mlt.py:299` nimmt Titelfolien im `--reimport` bereits mit; ohne das wäre der Reimport eines Projekts mit Titeln abgestürzt. Der Export selbst brauchte keine Änderung — er geht über `plan.slots`, und dort ist eine Folie ein Standbild. |

---

## 6. Betroffene Stellen

| Ort | Rolle | Änderung |
|---|---|---|
| `titles.py` (neu) | Folienerzeugung | Layout, Schriftfindung, Kontrastmessung, Cache-Key |
| `models.py:371` | `StillSegment` | Vorbild für `TitleSegment` |
| `models.py:421` | `Segment`-Union | `TitleSegment` aufnehmen |
| `models.py:535` | `_DISCRIMINATORS` | `"title"` ergänzen, sonst falsche Fehlerpfade |
| `models.py:345` | `Defaults` | `title:`-Block |
| `preprocess.py:79` | `process_image` | Blur/Abdunklung als Parameter, Wiederverwendung für den Hintergrund |
| `preprocess.py:141` | `_portrait_composite` | Vorlage für die Blur-Ebene (Shrink-8-Trick) |
| `build.py:59` | Intent-Erzeugung | Kapitel einsetzen |
| `build.py:148` | `_segments_from_plan` | Fokusblende, gekoppelte `KBSpec` |
| `build.py:169` | `_segment_from_slot` | Titel als Titel zurückschreiben (Rundlauf!) |
| `build.py:337` | `validate_edit` | Phrasenlage prüfen und warnen |
| `render.py:460` | `render` | `ensure_title_assets` voranstellen |
| `planner.py:551` | `coverage` | Titel getrennt zählen |
| `planner.py:575` | `material_seconds` | Titel zählen als Slot; Aufrufer korrigieren |
| `build.py:106` | `_timeline_length` | `n_media` um die Kapitel erhöhen |
| `docs/edit-yaml.md` | Schlüsselreferenz | `type: title`, `defaults.title` |
| `CLAUDE.md` | Baustellentabelle | Zeile für dieses Vorhaben |
| `kenburns.py:73` | `plan_motion` | Entscheidung 7 |
| `mlt.py:299` | `StillSegment`-Zweig | Titel mitnehmen |
| `doctor.py:561` | Fähigkeitsprüfung | Zeile „Schrift" |
| `cli.py` | Unterkommandos | `chapters`, `build --chapters` |

---

## 7. Abnahmekriterien

- **T1 — Timeline bleibt heil.** Mit drei Titelfolien in einem Fixture-Projekt
  bleibt `validate_continuity` grün, und `verify_master` meldet ≤ 1 Frame
  Abweichung. Titel verschieben keine Musik, sie belegen Slots.
- **T2 — Determinismus.** Zwei `build`+`render`-Läufe auf demselben
  `chapters.yaml` erzeugen ein **bitgleiches** Titelasset; der zweite
  Renderlauf meldet alle Segmente „aus Cache".
- **T3 — Lokalität der Änderung.** Wird nur die Überschrift einer Folie
  geändert, rendert der Report genau **drei** Segmente neu (die Folie und die
  zwei angrenzenden Blenden) — und nach Entscheidung 7(b) auch dann, wenn eine
  Folie neu **eingefügt** wird, drei plus die Nachbarschaft, nicht der halbe
  Film.
- **T4 — Phrasenlage.** Jede Titelfolie in einer Beat-Region beginnt auf einem
  Vielfachen von `phrase_beats` ab `Region.offset`, ± 1 Frame. Gemessen an der
  Fixture mit bekanntem Beat-Fahrplan (120 und 90 BPM).
- **T5 — Stille.** Eine Titelfolie in einer `quiet`-Region von 20 s steht
  `still_seconds` lang (± 1 Frame), **nicht** 20 s; das folgende Bild erhält
  den Rest und behält `hold`. Geprüft an der Fixture, deren 6-s-Lücke
  (`hold_seconds` = 12 s, also *ohne* `hold`) zusätzlich den Normalfall
  abdeckt: dort greift die Kachelung ohne jeden Override. Gegenprobe: mit
  `snap_back: true` **muss** der Test fehlschlagen — sonst prüft er die Falle
  aus Entscheidung 3b gar nicht.
- **T6 — Lesbarkeit.** Auf dem erzeugten Asset liegt das Kontrastverhältnis
  zwischen Textfarbe und der gemessenen mittleren Leuchtdichte unter der
  Textbounding-Box bei ≥ 4,5:1 — geprüft gegen ein absichtlich helles
  Testbild (weißer Verlauf) und ein dunkles.
- **T7 — Safe Area.** Die Bounding-Box beider Zeilen liegt vollständig
  innerhalb der um `safe` eingerückten Fläche; Überlauf einer langen
  Überschrift führt zu automatischer Verkleinerung bis 0,7 × und danach zu
  einer Warnung — nicht zu abgeschnittenem Text.
- **T8 — Fehlende Schrift.** Ohne auffindbare Schriftdatei bricht `build` mit
  einer Meldung samt Installationsbefehl ab, nicht mit einem Traceback; und
  `doctor` hat es vorher gemeldet.
- **T9 — Rundlauf.** `build` → `edit.yaml` → `plan_from_edit` → erneutes
  Schreiben liefert dieselbe Datei. Eine Titelfolie darf beim Rundlauf nicht zum
  gewöhnlichen Standbild werden (Erweiterung von `tests/test_roundtrip.py`).
- **T10 — MLT.** Der Kdenlive-Export enthält die Titelfolie als Clip mit
  identischer Länge und Position; `--reimport` bleibt heil.
- **T11 — Ohne Tonspur.** Ein Projekt ohne Musik mit drei Titelfolien ergibt
  eine Timeline, die um genau die drei Titelstandzeiten länger ist als dasselbe
  Projekt ohne Titel — und `validate_continuity` bleibt grün, die Karte deckt
  also weiterhin lückenlos ab.
- **T12 — Suite.** `pytest` bleibt grün bis auf die bekannte Vorbelastung:
  `test_hdr_wird_erkannt`, `test_tonemapping_steht_vor_dem_scale` und
  `test_ohne_tonemapper_greift_die_naeherung` in `tests/test_media.py`
  scheitern bereits vor dieser Arbeit unter ffmpeg 8.1.2. Keine **neuen**
  Fehlschläge; die neuen Tests kommen obendrauf.

---

## 8. Risiken

- **Cache-Key unvollständig.** Fehlen Schrift, Layoutversion oder ein
  Layoutparameter im Hash, sehen zwei Maschinen verschieden aus, und niemand
  merkt es, weil der Cache zufrieden ist. `TITLE_VERSION` muss bei jeder
  Layoutänderung hoch — dieselbe Disziplin wie bei `PREPROC_VERSION` und
  `RENDER_VERSION`.
- **Deckungsrechnung.** Jede Titelfolie kostet einen Slot Musik. Bei fünf
  Kapiteln fallen fünf Fotos hinten herunter, und der Hinweis „5 Medien passen
  nicht mehr" nennt heute nicht den Grund. Schritt 9 der Umsetzung adressiert
  das; wird es vergessen, ist der Bericht irreführend.
- **Stille verschluckt die Folie.** Der Fehler aus Entscheidung 3b ist der
  unangenehmste im ganzen Vorhaben: er tritt nur bei `quiet`-Regionen über
  `hold_seconds` auf, wirft keine Fehlermeldung, und das Ergebnis ist ein
  zwanzig Sekunden stehender Titel mitten im Film. Wer `dur:` setzt und
  `snap_back: false` vergisst, bekommt exakt dasselbe. Deshalb ist T5 mit
  Gegenprobe formuliert — ein Test, der auch mit `snap_back: true` grün bleibt,
  prüft die Falle nicht.
- **Phrasenlage veraltet still.** Materialisiert man die Ausrichtung als
  `beats:` (Entscheidung 3c), zerfällt sie bei jeder späteren Änderung davor.
  Die Prüfung in `validate_edit` ist deshalb nicht optional, sondern der
  Preis für den einfachen Planer.
- **Fokusblende ohne stetige Bewegung** wirkt nicht wie ein Schärfezug,
  sondern wie ein Schnitt zwischen zwei ähnlichen Bildern — das Schlechteste
  aus beiden Welten. Die Kopplung der `KBSpec` aus Entscheidung 5 ist der
  entscheidende Teil und gehört in die Sichtprüfung
  (`docs/manuelle-checks.md`).
- **Text im Bild bei mitzoomender Bewegung** kann bei sehr dünnen Schriften
  flimmern. Gegenmittel in Stufe 1: keine Haarlinien-Schriften als Default,
  und `kb: {z: [1, 1]}` steht jederzeit zur Verfügung. Zeigt die Sichtprüfung
  Flimmern, ist Entscheidung 1(c) der Ausweg — deshalb der zweischichtige
  Generator.
- **Einmalige Vollinvalidierung** durch Entscheidung 7(b). Bekannt, gewollt,
  einmalig — aber der Renderlauf danach dauert so lange wie der erste.

---

## Anhang A — Kontrastmessung

Läuft im Generator, bevor der Text gesetzt wird, und ist zugleich der Kern von
Abnahmekriterium T6.

```python
def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG 2.1, Kanäle auf 0..1 normiert."""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v / 255.0) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def fit_darkening(bg, box, text_rgb=(255, 255, 255), *, start=0.55,
                  minimum=4.5, floor=0.25, step=0.05) -> float:
    """Abdunklungsfaktor, der unter der Textfläche den Kontrast trägt.

    Deterministisch: feste Schrittweite, feste Untergrenze. Zwei Läufe auf
    demselben Bild liefern denselben Wert — Voraussetzung für den Cache.
    """
    patch = bg.crop(box).resize((16, 16))          # 256 Pixel genügen völlig
    px = list(patch.getdata())
    mean = tuple(sum(c[i] for c in px) / len(px) for i in range(3))
    lt = _relative_luminance(text_rgb)

    factor = start
    while factor > floor:
        lb = _relative_luminance(tuple(v * factor for v in mean))
        if _contrast(lt, lb) >= minimum:
            return round(factor, 3)
        factor -= step
    return floor        # + Warnung: Motiv trägt keinen hellen Text
```

## Anhang B — Beispiel, vollständig

`chapters.yaml`:

```yaml
chapters:
  - {at: 0, title: Skandinavien 2026, subtitle: "Drei Wochen, vier Städte",
     bg: "#1b2a3a", beats: 16}
  - {before: img_042, title: Malmö,     subtitle: auto}
  - {before: img_071, title: Stockholm, subtitle: auto}
```

Ergebnis in `edit.yaml`, Ausschnitt um das zweite Kapitel — 152 BPM,
`phrase_beats: 8`, Bild 41 endet regulär auf Beat 37 und wird auf 40 gedehnt:

```yaml
  - {type: still, src: cache/img_041.jpg, beats: 11}   # 8 -> 11: Phrasenlage
  - {type: xfade, from: 78, to: 80, beats: 1.5}
  - {type: title, title: Malmö, subtitle: 'Tag 11 · 24. Juli',
     bg: cache/img_042.jpg, beats: 12,
     kb: {z: [1.0, 1.06], c: [0.5, 0.5, 0.53, 0.5]}}
  - {type: xfade, from: 79, to: 81, beats: 2}          # Fokusblende
  - {type: still, src: cache/img_042.jpg, beats: 8,
     kb: {z: [1.06, 1.14], c: [0.53, 0.5, 0.58, 0.5]}} # setzt die Fahrt fort
```

Die beiden `kb:`-Blöcke sind der Kern der Fokusblende: Zoom und Bildmitte der
Titelfolie enden dort, wo die des Folgebildes beginnen. Über die Blende hinweg
steht die Kamera also nie still und springt nie — es wird nur scharf.

## Anhang C — Sichtprüfung

Ergänzend zu `docs/manuelle-checks.md`, weil der entscheidende Teil dieses
Vorhabens sich nicht automatisiert prüfen lässt:

1. **Zäsurwirkung.** Läuft der Film ab 15 s vor dem Titel: fühlt sich der
   Einsatz gesetzt an oder verfrüht? (Prüft Entscheidung 3.)
2. **Fokusblende.** Einzelbildweise über den Übergang: springt die Bildmitte?
   (Prüft die Kopplung der `KBSpec`.)
3. **Lesbarkeit in Bewegung.** Auf dem Handy bei 50 % Helligkeit ansehen — der
   gemessene Kontrast gilt für ein Standbild, nicht für ein komprimiertes
   Video in einer hellen Küche.
4. **Bildsprache.** Titelhintergrund und Hochformat-Komposit nebeneinander:
   dieselbe Unschärfe, dieselbe Abdunklung? Wenn nicht, wirken sie wie zwei
   verschiedene Filme.
5. **Titel in der Stille.** Ein Kapitel bewusst in eine Pause zwischen zwei
   Tracks legen und die Stelle im Ganzen ansehen: Steht die Folie so lange wie
   die Bilder ringsum, und übernimmt danach das ruhige Einzelbild? Das ist die
   Sichtprobe zu T5 — die Zahl allein sagt nicht, ob die Ruhe nach dem Titel
   trägt oder ob der Film an dieser Stelle stehenbleibt.
