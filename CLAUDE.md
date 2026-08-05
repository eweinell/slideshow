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
| **Beat-Erkennung bei langen Stücken** | **Umgesetzt** ([`docs/briefing-beat-detection.md`](docs/briefing-beat-detection.md)). Lange Abschnitte werden in `MAX_FIT_WINDOW`-Fenster (20 s) zerlegt, einzeln gefittet und über `merge_adjacent_beats` wieder verschmolzen. Offen bleibt nur die Abdeckung: 88,2 % statt der angepeilten 90 %, weil der Ausklang des Testtracks (die letzten ~40 s) rhythmisch wirklich zu dünn ist. |
| **`fit_grid` misst lokal, nicht global** | Die Konfidenz ist nur für Fenster bis ~30 s kalibriert. `MAX_FIT_WINDOW` und `CONF_THRESHOLD` hängen zusammen — wer an einem dreht, muss den anderen nachmessen. Unter 16 s zerfallen zusätzlich die Fixture-Songs, und die konstruktive Garantie hinter Abnahmekriterium A5 fällt. |
| **Titel- und Zwischenfolien** | **Vollständig** ([`docs/briefing-titelfolien.md`](docs/briefing-titelfolien.md), Stufen 1–3). `slideshow chapters` → `chapters.yaml` → `build` → `render`. Generator (`titles.py`), Phrasenlage, Stille-Regel, Fokusblende, Rundlauf, Deckungsrechnung, MLT. Offen ist nur die Sichtprüfung in Bewegung — sie braucht ffmpeg und eine echte Tonspur. |
| **Manuelle Reihenfolge** | **Vollständig** ([`docs/briefing-manuelle-reihenfolge.md`](docs/briefing-manuelle-reihenfolge.md), Stufen 1–2). `slideshow order` → `order.yaml` → `build` → `render`. Generator (`order.py`) mit `--by day\|place\|none` und `--update`, Auflösung mit allen drei Fehlerfällen samt Zeile, `rest: error\|append\|drop`, `group:` als dritter Kapitelanker. Dazu `slideshow chapters --from-groups` (`order.group_anchors` → `chapters.dump_group_chapters_yaml`): ein Kapitel je Block statt aus Zeitlücken geraten — [Rezept 4](docs/rezepte.md#4-kapitelweise-erzählen). Offen ist nur Stufe 3 (wiederholtes Material, `--from edit.yaml`, Kontaktbogen) — bewusst zurückgestellt. |
| **Auswahl aus großem Material** | **Stufen 0–2 umgesetzt** ([`docs/briefing-auswahl.md`](docs/briefing-auswahl.md)). `slideshow select` wählt nach Zeitstruktur (Trauben, gedämpfte Tagesquote, Spreizung) und schreibt `order.yaml` mit `rest: drop`; `preprocess` folgt der Auswahl (`--order`/`--all`) — [Rezept 5b](docs/rezepte.md#5b-aus-tausend-bildern-auswählen-lassen). Dazu Stufe 0: `read_exif_batch` läuft über `-@ argfile` (brach vorher **ab 193 Dateien** ab und meldete dabei „Programm nicht gefunden: exiftool"). Offen ist Stufe 3 (Kontaktbogen `slideshow sheet`; der erzeugte Dateikopf verweist bereits darauf). |
| **HLG unter ffmpeg 8.1.2** | Siehe oben, die drei roten Tests. Kein Briefing vorhanden. |
| **Blendenmodus wird nicht geprüft** | `_XFADE_MODES.get(mode, "fade")` in `kenburns.py` macht aus einem vertippten Modus stillschweigend eine normale Blende — die einzige Stelle im Schema ohne Validierung. `known_modes()` gibt es bereits. |
| **Ken-Burns-Richtung** | **Umgesetzt** (Entscheidung 7 des Titelfolien-Briefings). `plan_motion` nimmt die Kennung (`src`) statt des Slot-Index, `motion_key` hasht sie mit `blake2b`. Einfügen und Umsortieren sind damit dauerhaft billig; der Preis ist ein nur noch statistischer Zoomwechsel und identische Bewegung bei einem doppelt verwendeten Bild. |
| **Schwenk am Zoomanfang** | **Umgesetzt.** Der Schwenk hat jetzt ein ruhendes Ende in der Bildmitte (`defaults.kb.pan_anchor: center`) und wird auf `0,5 − 1/(2z)` des größten Zooms gedeckelt — der geplante Weg ist damit auch der sichtbare. Vorher lief er symmetrisch durch die Mitte und kippte sichtbar die Richtung, sobald die Klemmung bei `z = 1,0` aufging (gemessen über 5 s: 0,500 → 0,526 → 0,447). Die alte Auslegung bleibt als `pan_anchor: through` erreichbar; `pan_amount` in einer alten Datei setzt sie selbst. **Folge: `pan_rate`/`pan_total` greifen mit den Vorgaben nicht mehr** — es gewinnt der Deckel. Mehr Schwenk heißt jetzt mehr `zoom_total`. |
