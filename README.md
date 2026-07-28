# slideshow

Ein Python-CLI, das aus ~100 Fotos (20 MP) und einigen Videoclips eine
musiksynchrone 4K-Slideshow rendert.

```bash
slideshow doctor                              # Umgebung prüfen
slideshow probe /material/urlaub              # → manifest.json
slideshow audio track1.mp3 track2.mp3 --gap 6 # → cache/mix.flac
slideshow preprocess                          # → cache/
slideshow beats                               # → beats.yaml  ← ansehen!
slideshow build                               # → edit.yaml
slideshow render edit.yaml -o out/master.mp4
```

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
edit.yaml       die Edit-List (build)
cache/          normalisierte Bilder, Clip-Intermediates, Segment-Cache
out/            master.mp4, timeline.json, project.kdenlive
logs/           ein Logfile je Subkommando, mit den exakten ffmpeg-Aufrufen
```

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
