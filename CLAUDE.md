# Hinweise für Claude Code

Musiksynchroner 4K-Slideshow-Renderer, Python-CLI. Fachliche Einführung steht in
der [README](README.md); die Schlüssel der Edit-List in
[`docs/edit-yaml.md`](docs/edit-yaml.md).

Diese Datei sammelt nur, was man beim Arbeiten *im* Repo sonst jedes Mal neu
herausfindet.

## Umgebung

- **Windows, PowerShell.** Das Bash-Tool ist in dieser Umgebung nicht verfügbar
  (`No suitable shell found`) — alles über PowerShell.
- **`.venv` enthält kein pytest.** Tests laufen über `uv`:
  ```powershell
  uv run --extra dev pytest -q
  ```
  Das CLI dagegen aus dem venv: `.\.venv\Scripts\slideshow.exe`
- **ffmpeg 8.1.2**, exiftool, ImageMagick und `melt` sind installiert;
  **keine NVIDIA-GPU**. `doctor` meldet NVENC-Encoder als vorhanden (ffmpeg hat
  sie einkompiliert), die Codec-Auswahl prüft aber praktisch nach und fällt
  korrekt auf libx265 zurück. Vorschläge wie `--codec av1_nvenc` aus dem
  `probe`-Report würden hier fehlschlagen.

### PowerShell-Falle

`Select-Object -First N` **bricht die Pipeline ab und beendet dabei den noch
laufenden Prozess.** Bei einem `slideshow probe | Select-String … | Select-Object
-First 1` wird das Manifest nicht mehr geschrieben, und der Fehler sieht aus wie
ein Werkzeugfehler. Ausgabe erst vollständig in eine Variable holen, dann
filtern:

```powershell
$o = & .\.venv\Scripts\slideshow.exe --project $p probe "$p\src" 2>&1
($o | Select-String "Manifest") -join ""
```

## Tests

**Drei Tests in `tests/test_media.py` schlagen dauerhaft fehl** —
`test_hdr_wird_erkannt`, `test_tonemapping_steht_vor_dem_scale`,
`test_ohne_tonemapper_greift_die_naeherung`. Ursache: HLG wird unter ffmpeg
8.1.2 nicht mehr erkannt (`detect_hdr` liefert `''`), die Tonemapping-Kette
greift deshalb nie. **Das ist der Ausgangszustand, kein selbst verursachter
Schaden.** Wer etwas ändert und danach genau diese drei rot sieht, hat nichts
kaputtgemacht.

Bei Zweifeln, ob ein Fehlschlag neu ist: eigene Änderungen wegstashen und
gegenprüfen —
`git stash push src/... ; uv run --extra dev pytest -k … ; git stash pop`.

Tracebacks zeigen manchmal `>   ???` statt Quellzeilen und Pfade unter
`…\slideshowbriefingumsetzung\…`. Das sind veraltete `.pyc` aus einem früheren
Repo-Pfad, kein echtes Problem.

## CLI-Eigenheiten

- **Globale Schalter stehen vor dem Subkommando**: `slideshow --project X
  --dry-run render edit.yaml`. Danach ergibt es einen Usage-Fehler.
- `testset1/` ist ein reales Testprojekt (14 Fotos einer Sony ILCE-6700, ein
  Track von 6:32). Nicht eingecheckt.
- **Ein voller 4K-Render dauert 45–90 Minuten.** Für alles, was nur das
  Verhalten prüft (Mux, Tonlänge, Ausblende, Segment-Cache), reicht
  `render --preview` — derselbe Mux-Pfad, nur 720p/x264. Für Teilbereiche
  `--range A:B`.
- `--jobs` begrenzen, wenn wenig RAM frei ist: 22 parallele 4K-Encodes brauchen
  mehr, als typischerweise verfügbar ist.

## Sprache und Stil

- Alles auf Deutsch: Code, Kommentare, Docstrings, Tests, Dokumentation,
  Commit-Messages, Konsolenausgaben.
- **Im Code keine Umlaute** (`ue`, `ae`, `oe`, `ss`) — in Markdown dagegen
  richtige Umlaute.
- Testnamen sind ganze Sätze: `test_zu_lange_tonspur_wird_abgeschnitten`.
- Kommentare begründen das *Warum*, nicht das Was — und stehen dort, wo eine
  Entscheidung gegen die naheliegende Alternative getroffen wurde.

## Architektur-Invarianten

Verletzt man eine davon, fällt es erst spät und woanders auf:

1. **`edit.yaml` ist die einzige Wahrheit.** Jeder Renderpfad leitet sich daraus
   ab. Neue Einstellungen gehören nach `Defaults`, nicht in CLI-Argumente, die
   nur zur Laufzeit existieren.
2. **Absolute Framenummern, nie Einzeldauern addieren** — sonst läuft der Sync
   gegen Ende weg.
3. **Segmente sind unabhängig und werden per Content-Hash gecacht.** Der
   Cache-Key umfasst den vollständigen Filtergraph über `params` in
   `plan_jobs`. Wer einen Filter ergänzt, muss ihn dort mitgeben, sonst liefert
   der zweite Lauf das alte Segment aus.
4. **Der Mux hängt mit `-c:v copy` zusammen.** Bildfilter gehören deshalb ins
   Segment, nicht in den Mux — sonst wird der ganze Master neu encodiert. Die
   Ausblende am Filmende sitzt aus genau diesem Grund im letzten Segment
   (`_fade_suffix` in `render.py`), nur der Ton wird beim Muxen geblendet.
5. **Zoom und Schwenk leiten sich aus der Dauer ab**, nach `clamp(rate × Dauer,
   min, max)`. Feste Beträge ergeben im kurzen Fall einen Ruck und im langen
   Stillstand.

## Offene Baustellen

| Thema | Stand |
|---|---|
| **Beat-Erkennung bei langen Stücken** | Ein durchgehender Song wird komplett als `free` eingestuft, kein Schnitt liegt auf einem Beat. Messwerte, Diagnose und Umbauvorschlag: [`docs/briefing-beat-detection.md`](docs/briefing-beat-detection.md). Nicht umgesetzt. |
| **HLG unter ffmpeg 8.1.2** | Siehe oben, die drei roten Tests. Kein Briefing vorhanden. |
| **Blendenmodus wird nicht geprüft** | `_XFADE_MODES.get(mode, "fade")` in `kenburns.py` macht aus einem vertippten Modus stillschweigend eine normale Blende — die einzige Stelle im Schema ohne Validierung. `known_modes()` gibt es bereits. |
| **Schwenk am Zoomanfang** | Ein Hineinzoom startet bei `z = 1,0`, dort ist der Ausschnitt das ganze Bild und die Mitte kann sich nicht bewegen. Rund die Hälfte des geplanten Schwenks bleibt deshalb unsichtbar. Bewusst so belassen, in `docs/edit-yaml.md` beschrieben. |
