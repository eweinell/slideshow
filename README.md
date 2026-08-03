# slideshow

Ein Python-CLI, das aus ~100 Fotos (20 MP) und einigen Videoclips eine
musiksynchrone 4K-Slideshow rendert.

```bash
slideshow doctor                              # Umgebung prüfen
slideshow probe /material/urlaub              # → manifest.json
slideshow audio track1.mp3 track2.mp3 --gap 6 # → cache/mix.flac
slideshow preprocess                          # → cache/
slideshow beats                               # → beats.yaml  ← ansehen!
slideshow order                               # → order.yaml  ← optional, zum Sortieren
slideshow build                               # → edit.yaml
slideshow render edit.yaml -o out/master.mp4
```

Diese Reihenfolge muss man sich nicht merken: jeder Schritt schließt mit dem
nächsten sinnvollen Aufruf ab, fertig zum Kopieren und mit passenden
Parametern.

```
Nächster Schritt:
  slideshow build --still-seconds 28
```

Der Vorschlag kommt aus dem *Zustand* des Projekts, nicht aus dem zuletzt
gelaufenen Kommando — wer eine Phase wiederholt oder überspringt, bekommt
trotzdem den richtigen. `-q` schaltet ihn ab.

Der `audio`-Schritt ist optional — ohne Tonspur entsteht eine stumme
Slideshow mit fester Bilddauer, siehe [Ohne Musik, mit zu wenig oder zu
viel](#ohne-musik-mit-zu-wenig-oder-zu-viel).

> **Fertige Abläufe für die Fälle, die wirklich vorkommen** — Rohschnitt,
> Kapitel, thematisch sortieren, auswählen, Nachschub einpflegen — stehen in
> [`docs/rezepte.md`](docs/rezepte.md). Wer nicht wissen will, *warum* das
> Werkzeug so gebaut ist, sondern nur, *welche* fünf Befehle sein Fall braucht,
> fängt dort an.

## Grundprinzipien

1. **Die Edit-List ist die Single Source of Truth.** `edit.yaml` ist
   menschenlesbar, versionierbar, von Hand editierbar. Jeder Renderpfad
   (ffmpeg direkt, MLT) leitet sich daraus ab, nie umgekehrt. Sie hält
   *Absicht* (`beats: 8`), nicht aufgelöste Zeitstempel — die absoluten
   Framegrenzen entstehen bei jedem Lauf neu aus derselben deterministischen
   Funktion, damit `build` und `render` garantiert dasselbe sehen.
2. **Segmente sind unabhängig.** Jedes wird einzeln encodiert, per Content-Hash
   gecacht und nur bei Änderung neu gerendert. Ein korrigiertes Bild an
   Position 47 löst genau drei Neurenderungen aus: das Still-Segment und die
   zwei angrenzenden Überblendungen.
3. **Eine Zeitbasis.** Alle Zeiten sind absolute Zeitpunkte auf der
   Master-Timeline; Nullpunkt ist Sample 0 der Tonspur. Gerechnet wird
   durchgehend in *Framenummern*; jede Dauer ist eine Differenz zweier davon.
   Nie werden Einzeldauern aufaddiert — sonst läuft der Sync gegen Ende weg.
4. **Fail loud, fail early.** Jede Phase validiert gegen ein Schema und bricht
   mit YAML-Pfad und Zeile ab, statt später einen kaputten Master zu bauen.

## Ohne Musik, mit zu wenig oder zu viel

Material und Musik passen selten von allein zusammen. Keiner dieser Fälle
bricht ab — jeder meldet sich und läuft durch:

| Fall | Verhalten |
|---|---|
| **keine Tonspur** | `beats` erzeugt eine Karte aus *Anzahl Medien × `still_seconds`*, der Master bekommt gar keine Tonspur |
| **Ton kürzer als das Material** | die restlichen Bilder laufen im festen Takt weiter, der Ton wird stumm verlängert |
| **Ton länger als das Material** | der Ton wird auf die Filmlänge abgeschnitten |

Die Laufzeit bestimmt normalerweise die **Musik**: solange das Material sie bis
auf eine Bildlänge genau füllt, fängt die Streckung des letzten Bildes den Rest
ab, und der Film endet mit dem Stück. Passt es nicht, gewinnt das **Material**.
Sonst stünde bei 14 Fotos unter einem 6:32-Stück das letzte Bild über fünf
Minuten still, nur damit der Ton aufgeht.

Der Takt ohne Beat-Raster ist `still_seconds` (Vorgabe 4 s) und gilt für
`beats` wie `build`:

```bash
slideshow build --still-seconds 28    # 14 Fotos füllen ein 6:32-Stück
```

Passt das Material nicht zur Musik, nennt `build` die nötige Standzeit selbst —
der Vorschlag ist so gerechnet, dass er die Lücke wirklich schließt.

### Ausblende am Ende

Bild und Ton blenden gemeinsam aus, 1,5 s per Vorgabe, abschaltbar mit
`--fade-out 0`. Bei gekürzter Tonspur bricht die Musik sonst mitten im Stück
ab.

Die Blende sitzt bewusst **im letzten Segment** und nicht im Mux: der Mux hängt
die Segmente mit `-c:v copy` aneinander, ein Filter dort würde den ganzen
Master neu encodieren und die verlustfreie Concat-Kette aufgeben. Ein zweiter
Lauf rendert deshalb genau *ein* Segment neu. Der Preis: die Blende kann nicht
länger sein als das letzte Segment — ist es kürzer, wird sie gekürzt statt über
die Segmentgrenze hinweg gestückelt.

## Ausführungsmodell

Analyse und Generierung laufen in WSL, das **Rendering nativ unter Windows** —
der 9p-Durchgriff auf `/mnt/c` ist bei 100 × 20 MP deutlich langsamer, und
natives ffmpeg hat vollen NVENC/NVDEC-Support. Das Tool erkennt, wo es läuft,
und `doctor` benennt im Report, welche Seite geprüft wurde. Alle Pfade in
Manifest und Edit-List sind relativ zum Projektroot.

Ohne NVIDIA-GPU läuft alles auf der CPU (libx265/libx264) — langsamer, aber
vollständig. Das ist kein Notnagel, sondern der Normalfall in der
Analyseumgebung und in der Testsuite.

## Projektlayout

```
manifest.json   was für Material vorliegt (probe)
beats.yaml      Regionenkarte der Tonspur (beats) — vor dem Bauen ansehen
chapters.yaml   Kapitel der Reise, Eingabe für build — optional
order.yaml      Reihenfolge der Medien, Eingabe für build — optional
edit.yaml       die Edit-List (build)
cache/          normalisierte Bilder, Clip-Intermediates, Segment-Cache
out/            master.mp4, timeline.json, project.kdenlive
logs/           ein Logfile je Subkommando, mit den exakten ffmpeg-Aufrufen
```

Sämtliche Schlüssel der Edit-List — Takt, Ken Burns, Übergänge, Präzedenz der
Dauerangaben und die üblichen Eingriffe von Hand — stehen in
[`docs/edit-yaml.md`](docs/edit-yaml.md); dort auch die beiden optionalen
Eingabedateien `chapters.yaml` (Titelfolien) und `order.yaml` (Reihenfolge und
Auswahl). Fertige Abläufe dazu: [`docs/rezepte.md`](docs/rezepte.md).

## Installation

```bash
pip install -e .
```

`slideshow doctor` prüft den Rest und gibt zu jedem Fehlschlag einen
kopierbaren Installationsbefehl aus — plattformabhängig (`winget` unter
Windows, `apt` unter WSL). Es läuft auch auf einem System ganz ohne ffmpeg
durch, statt mit einem Traceback abzustürzen.

Benötigt: ffmpeg ≥ 6.0, Python ≥ 3.11. Optional: exiftool (EXIF/ICC — ohne das
können Hochformat-Fotos quer liegen), ImageMagick, `melt` für den MLT-Pfad,
`librosa` für etwas bessere Onset-Erkennung (es gibt einen gleichwertigen
numpy-Fallback für perkussisches Material).

Werkzeuge werden nicht nur im `PATH` gesucht. `melt` etwa wird praktisch nie
einzeln installiert, sondern kommt mit Kdenlive oder Shotcut mit — und beide
legen nur ihre Haupt-Exe in den `PATH` (scoop shimt `kdenlive.exe`, während
`melt.exe` in `bin/` liegen bleibt). Die üblichen Installationsorte werden
deshalb zusätzlich abgesucht; der Report zeigt dann den gefundenen Pfad an.
Liegt ein Werkzeug woanders, setzt `SLIDESHOW_<NAME>` es fest — etwa
`SLIDESHOW_MELT=C:\Pfad\zu\melt.exe`. Der Override gewinnt auch gegen den
`PATH`, falls dort die falsche Version steht.

## Tests

```bash
slideshow selftest --make-fixtures    # synthetisches Material erzeugen
pytest                                # Abnahmekriterien dagegen prüfen
```

Der Fixture-Generator baut einen Klick-Track mit **bekanntem** Beat-Zeitplan
(zwei Songs, 120 und 90 BPM, 6 s Stille dazwischen) — nur deshalb ist der Sync
framegenau automatisiert prüfbar. Dazu Clips in 30p/50p/60p, einer mit
Rotations-Metadatum, einer künstlich VFR, einer HLG-getaggt, einer als
4:2:2-10-Bit-HEVC; Bilder inklusive Portrait-JPEG mit EXIF-Orientation 6 und
einem Verlaufsbild für den Banding-Test.

Automatisiert abgedeckt sind die Abnahmekriterien 1, 2, 3, 5–12 und 14. Die
Kriterien mit visueller Komponente stehen in
[`docs/manuelle-checks.md`](docs/manuelle-checks.md).

## Bewusste Abweichungen vom Briefing

Vier Stellen sind anders umgesetzt als spezifiziert. Jede ist im Code an der
betroffenen Funktion begründet:

| Stelle | Briefing | Umgesetzt | Grund |
|---|---|---|---|
| **7** Retiming 50p → 60p | `setpts=1.2*PTS`, „leichte Zeitlupe" bei „jeder Frame ein Ausgabeframe" | `setpts=0.8333*PTS` | Beides zusammen geht nicht auf. 1,2 streckt die Zeitbasis, das Material landet mit 41,7 fps im 60p-Raster und muss dupliziert werden — genau der Judder, den der Trick vermeiden soll. Der Faktor für 1:1 ist `50/60`. Das Material läuft dadurch 20 % schneller statt langsamer; wer die Zeitlupe will, wählt `--fps 50`. |
| **6.3** `snap_back` | Ein `dur:`-Override verschiebe „alle nachfolgenden Schnitte" | Der Versatz bleibt auf **einen** Schnitt begrenzt | Der Standard-Slot einer Beat-Region ist absolut definiert (`beats_per_still` Beats ab dem nächsten Rasterbeat), nicht relativ zum Cursor. Damit findet schon das folgende Bild von selbst aufs Raster zurück. `snap_back` entscheidet nur noch über den Override-Schnitt selbst. Ein Tippfehler in `dur:` kann den Rest des Films nicht mehr aus dem Takt bringen. |
| **5.2** Tonemapping | `zscale=t=linear:npl=100,…` | `npl=1000` für HLG, Eingangs-Charakteristik explizit (`tin`/`min`/`pin`) | Ohne explizite Eingangstags bricht `zscale` mit `code 3074: no path between colorspaces` ab, sobald ein Tag am Quellstream fehlt — bei Handymaterial die Regel. Und HLG referenziert 1000 nits; mit `npl=100` bliebe das Bild flau, also genau der Fehler, den Kriterium 4 ausschließt. |
| **5.1** Normalform | Hochformat wird ins 16:9-Komposit gerendert | **Jedes** Bild wird auf 7680×4320 normalisiert | `zoompan` schneidet immer einen Bereich im Seitenverhältnis der *Eingabe* aus und skaliert ihn auf die Ausgabegröße. Bei 3:2-Quelle und 16:9-Ausgabe verzerrt das. Querformat wird deshalb formatfüllend beschnitten, damit der Ken-Burns-Renderer wirklich für alle Bilder identisch bleibt. |

## Verifizierte technische Annahmen

Der als Risiko markierte Punkt aus 8.1 ist geklärt:

**`zoompan` rechnet in 8 Bit.** In ffmpeg 6.1.1 hängt der Filtergraph
nachweislich ein `auto_scale` von `yuv420p10le` nach `yuv420p` *vor* den
Filter. 16-Bit-PNGs nützen der Kette also nichts; der 10-Bit-Gewinn entsteht
nur noch im Encoder. `doctor` prüft das auf der jeweiligen Maschine empirisch
und meldet es.

Deshalb gibt es einen zweiten Ken-Burns-Pfad, `--kb-engine scale16`: per-Frame
`scale`-Expressions in `yuv444p16le` plus festes `crop`. Er rechnet durchgehend
in 16 Bit, kostet mehr CPU und ist als Option gedacht, nicht als Default — zu
wählen, wenn der Banding-Test Stufen in Himmelsverläufen zeigt.

Beide Pfade sind nachweislich **bitgleich fortsetzbar**: rendert man die Frames
`[k, N)` einer Bewegung als eigenes Segment mit Frame-Offset `k`, sind sie
identisch zum durchgehenden Lauf. Das ist die harte Fassung von
Abnahmekriterium 12 — an der Fenstergrenze einer Blende kann es per
Konstruktion keinen Positionssprung geben.

Zwei Fallen dabei, beide im Code dokumentiert: `scale`/`crop` kennen den
Framezähler als `n`, nicht als `on` (ein falscher Name wirft keinen Fehler, die
Bewegung steht einfach still), und die Crop-Position darf nicht über `iw`/`ih`
formuliert werden, weil `crop` die Eingangsmaße an die Filterkonfiguration
bindet.

## Beat-Erkennung bei langen Stücken

Ein Song driftet. Kein reales Stück hält sein Tempo über sechs Minuten so
genau, dass ein *starres* Raster darüber passt — an einem gemessenen 6:32-Mix
läuft der Puls von 150 auf 157 BPM und wieder zurück, gegenüber einem festen
Raster sind das über zwei Sekunden Versatz. Ein einzelnes `bpm` für die ganze
Tonspur beschreibt solches Material nicht.

Die Analyse legt deshalb kein globales Raster an, sondern zerlegt lange
Abschnitte in Fenster von `MAX_FIT_WINDOW` (20 s) und passt jedem Fenster ein
eigenes Raster an. Anschließend werden benachbarte Fenster wieder verschmolzen,
wo Tempo *und* Phase durchtragen — die Fensterung ist ein Mittel der Messung
und soll in der Karte nicht sichtbar werden. Bleibt eine Grenze stehen, hat
sich dort tatsächlich das Tempo geändert.

Für den gemessenen Mix ergibt das zwölf Beat-Regionen zwischen 149,5 und 156,75
BPM, die jeweils auf unter 1 % dem lokal tatsächlich gespielten Tempo
entsprechen; die Rasterpunkte liegen im Median 15,6 ms neben den Referenz-Beats.
Vorher war das Ergebnis *eine* `free`-Region über die volle Länge — kein
einziger Schnitt lag auf einem Beat.

Was bewusst `free` bleibt: Passagen, in denen sich wirklich kein Puls finden
lässt — Ambient, Sprachaufnahmen, ausgedünnte Intros und Ausklänge. Dort takten
die Bildwechsel im Standardintervall `still_seconds` weiter. Messwerte und
Diagnose stehen in
[`docs/briefing-beat-detection.md`](docs/briefing-beat-detection.md).

Wer das Tempo kennt, setzt es weiterhin direkt — eine handgeschriebene Karte
mit einer einzigen Beat-Region über den ganzen Track bleibt gültig:

```bash
slideshow beats --bpm 152 --offset 0.35
```

## Übergangs-Mechanik

Der Schnittpunkt `t` liegt auf dem Beat. Ein Übergang der Dauer `T` belegt
`[t − T/2, t + T/2]` — der Schnitt bleibt auf dem Raster, die Blende ist
darüber zentriert.

```
Bild A  ├──────── exklusiv ────────┤
                                   ├── xfade ──┤
Bild B                             ├───────── exklusiv ─────────┤
                                   ↑
                              Schnitt auf dem Beat
```

Die Ken-Burns-Bewegung eines Stills ist über seine **volle sichtbare Spanne**
definiert (exklusiver Anteil plus angrenzende Übergangs-Hälften). Das
xfade-Segment wertet dieselben Ausdrücke beider Nachbarn mit passendem
Frame-Offset aus — die Bewegung läuft durch die Blende hindurch weiter.

Übergänge sind **eigene Segmente**. Das ist der Trick, der die Unabhängigkeit
und damit das Caching rettet: der Hash eines xfade-Segments schließt die Quell-
und Bewegungs-Hashes beider Nachbarn ein, `T` und den Modus.
