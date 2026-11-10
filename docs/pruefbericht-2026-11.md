# Prüfbericht November 2026: Workflow, Cache, Beat-Ausrichtung, Codequalität

Stand: 2026-11-10, gemessen auf `main` (Commit `2696d76`) an den Projekten
`kenia-2026`, `london-2026` und `sommer26`.

Der Bericht ist zugleich **Arbeitsgrundlage**: Abschnitt 7 zerlegt die
Vorschläge in Arbeitspakete mit Abnahmekriterien. Ein späterer Lauf beginnt
dort, liest das Paket und den zugehörigen Befund und trägt den Stand in der
Tabelle nach.

Nicht im Detail geprüft: `select`, `sheet`, `order`, `titles`, `kbplan`, `mlt`,
`probe`, `doctor`. Aussagen über diese Module stützen sich auf Schnittstellen
und Messwerte, nicht auf eine Zeilenlektüre.

Die Messungen lassen sich wiederholen:

```powershell
.\.venv\Scripts\python.exe scripts\cache_bilanz.py kenia-2026 london-2026
.\.venv\Scripts\python.exe scripts\beat_drift.py london-2026
```

---

## 1. Kurzfassung

| # | Befund | Gewicht |
|---|---|---|
| 1 | Ohne librosa saß jeder Beat rund 50 ms zu früh; 3 von 628 Tests rot | Fehler, behoben (AP1); vorhandene Karten nicht betroffen |
| 2 | Die Motiverkennung hat nie eine `vision.yaml` erzeugt | Merkmal ungenutzt |
| 3 | Clip-Intermediates belegen 182 MB je Sekunde, immer für den ganzen Clip | 192 von 210 GB in `kenia-2026` |
| 4 | Der Cache wird nie aufgeräumt | wächst mit jedem Bau |
| 5 | `edit.yaml` ist dem Namen nach die Wahrheit, tatsächlich ein Bauprodukt | Ursache des umständlichen Ablaufs |
| 6 | Die Beat-Erkennung liefert Messfenster als Regionen; die Standzeitregeln kompensieren das | Ursache der verborgenen Komplexität |
| 7 | Die Frage „wie viele Bilder passen" ist dreimal implementiert | drei dokumentierte Folgefehler |

---

## 2. Akute Befunde

### 2.1 Onset-Erkennung ohne librosa (behoben, siehe AP1)

- `beats.onset_envelope` nimmt librosa, wenn es importierbar ist, und fällt
  sonst still auf `_spectral_flux` zurück.
- `_spectral_flux` legte Frame `n` auf die Samples `[n·hop, n·hop + win)` und
  ordnete ihm die Zeit `n·hop / sr` zu, also den **Fensteranfang**. Ein
  Anschlag erschien dadurch um rund die halbe Fensterlänge zu früh (gemessen an
  einem Klick: −48 bis −51 ms bei 46 ms halbem Fenster).
- `refine_offset` sucht nur ±30 ms um den Rasterpunkt und holt das nicht zurück.
- Folge: Offset und damit jeder Schnitt etwa 2,5 Frames (50 fps) vor dem Beat.
- Sichtbar wurde es, weil die `.venv` am 2026-11-10 neu angelegt wurde und
  seither weder `librosa` noch `anthropic` enthält. `docs/pruefbericht-ffmpeg9.md`
  vermerkt für August noch „librosa: ja (0.11.0, im .venv)".
- Rote Tests vor der Korrektur: `test_offsets_treffen_den_ersten_schlag`,
  `test_schnitte_liegen_auf_echten_beats`,
  `test_abweichung_waechst_nicht_zum_ende_hin`. Mit librosa in einer
  Wegwerfumgebung (`uv run --with librosa`) liefen alle 38 Beat- und Sync-Tests
  durch.
- **Die vorhandenen Karten sind nicht betroffen.** Die Datei hielt den Pfad
  bisher nicht fest, die Messung beantwortet es aber: ein zu frühes Raster
  zeigte sich als Anschlag bei etwa +50 ms. `scripts/beat_drift.py` misst über
  alle Beat-Regionen im Median +0 ms (`london-2026`, Karte vom 22.08.) und
  −10 ms (`kenia-2026`, Karte vom 10.11. um 14:43, also 16 Minuten vor dem
  neuen venv). Beide sind demnach mit librosa entstanden. `sommer26` ließ sich
  nicht messen, dort fehlt `cache/mix.flac`.
- Betroffen gewesen wäre jeder `slideshow beats` seit dem 10.11., 14:59.

### 2.2 Motiverkennung ohne echten Lauf

- In keinem der drei Projekte liegt eine `vision.yaml`; in den `edit.yaml`
  tragen 0, 1 und 8 Standbilder ein `kb:` (alle von Hand).
- Alle 22 `analyze`-Läufe stammen vom 2026-08-13 zwischen 17:39 und 18:57 und
  waren `--count-tokens` oder `--dry-run`.
- Fehlerbild: viermal fehlende Zugangsdaten (`Could not resolve authentication
  method`), danach Amazon Bedrock: 403 „not available" in `eu-west-1`, 404
  „does not exist" in `eu-central-1` für `anthropic.claude-opus-5`, ebenso für
  die Schreibweisen mit `eu.` und `global.` davor.
- Planer (`kbplan`) und Tests stehen. Es fehlt der Zugang, nicht die Rechnung.
- `vision.PREISE` kennt `claude-opus-5-5` und `claude-haiku-5-5` nicht; die
  Kostenschätzung meldet dort „unbekannt".

### 2.3 Kleinere Funde

- `cli._size_of` (`cli.py:647`) gibt konstant `(3840, 2160)` zurück. `preprocess`
  rechnet Clip-Intermediates also immer in 4K, unabhängig von `edit.size`.
- `build --size` und `--fps` stehen in keiner Eingabedatei. `slideshow overrides`
  baut zum Vergleich ohne sie.
- Vier Standbilder in `london-2026` stehen unter 3 s. Eines ist das letzte Bild
  (0,44 s; die Restplatz-Regel gilt am Filmende bewusst nicht). Die drei anderen
  stehen mitten in einer Region rund 2 s. Vermutete Ursache, nicht
  nachverfolgt: `build._phrasenlage` lässt den Vorgänger einer Titelfolie bis
  auf einen Beat schrumpfen (`build.py:788`, Bedingung `neu < 1.0`) und prüft
  `min_still` nicht.
- 62 Standbilder in `london-2026` stehen über 7 s bei `still_tolerance: [3, 7]`.
  11 davon enden an einer Regionsgrenze (Restzuschlag); der Rest kommt aus
  `beats_per_still: 12`.

---

## 3. Cache

### 3.1 Messwerte

| Posten | kenia-2026 | london-2026 |
|---|---|---|
| Cache gesamt | 210,5 GB | 39,2 GB |
| Clip-Intermediates (`clip_*.mov`) | 192,2 GB, 64 Clips, 1108 s | 26,0 GB, 11 Dateien |
| Normalisierte Bilder (`img_*.jpg`) | 16,7 GB, 1306 Bilder | 2,8 GB, 270 Bilder |
| Segmente | 0,25 GB, 618 Dateien | 9,4 GB, 687 Dateien |
| `mix.flac` | 1,2 GB | 0,5 GB, dazu 0,5 GB Einzeltracks |
| Standbilder im Film | 287 | 266 |
| Clips im Film | 28 mit 373 s (34 % des Intermediate-Materials) | 11 mit 132 s (60 %) |

### 3.2 Ursachen

- **DNxHR HQX in UHD/50p kostet konstant etwa 182 MB/s.** Fünf Sekunden Clip
  sind knapp 1 GB. Ein 25-s-Handyclip in 1080p30 wird auf UHD50 hochgerechnet
  und belegt 4,5 GB.
- **Es wird der ganze Clip umgerechnet.** `preprocess` läuft vor `build` und
  kennt den Schnitt nicht. `--ranges-from edit.yaml` existiert, ist aber nur auf
  Wunsch und setzt eine fertige `edit.yaml` voraus. In `kenia-2026` gehören
  93 GB zu Clips, die im Film gar nicht vorkommen; einschließlich 2 s Handles
  sind rund 115 GB entbehrlich.
- **Nichts wird aufgeräumt.** Außer im Fehlerpfad löscht kein Kommando eine
  Cache-Datei. Jeder geänderte Segment-Key lässt das alte Segment liegen; die
  Segmente halten den Master ein zweites Mal vor (London: 9,4 GB neben 8,6 GB
  in `out/`).
- **Bilder liegen größer als die Originale vor**: JPEG q95 in 4:4:4 auf
  7680 × 4320, im Mittel 12,8 MB. `zoompan` rechnet laut `kenburns.py` ohnehin
  in yuv420p; das volle Chroma nützt nur der Engine `scale16`.
- **Die Auswahl wirkt nur vorwärts.** In `kenia-2026` sind 1306 Bilder
  normalisiert, 287 stehen im Film.

### 3.3 Vorschläge

1. Clip-Intermediates erst beim Rendern erzeugen, nur für den verwendeten
   Bereich plus Handles. Vorbild ist `preprocess.ensure_title_assets`.
2. `slideshow clean`: entfernt, was die aktuelle `edit.yaml` nicht referenziert
   (Bilder, Clips, Originalton, Segmente, Thumbnails), mit `--dry-run` und
   Größenangabe. Nach erfolgreichem Render verwaiste Segmente desselben Profils
   automatisch löschen.
3. Kleineren Intermediate-Codec als Vorgabe. `hevc_intra_nvenc` und
   `hevc_intra_cpu` sind schon wählbar. Die Ersparnis ist **nicht gemessen**.
4. Bilder in 4:2:0 ablegen, solange die Engine `zoompan` ist. Ersparnis
   **nicht gemessen**.
5. Weitergehend: gar kein Intermediate. Das Segment liest die Quelle direkt,
   Tonemapping, Retiming und Skalierung wandern in den Segmentgraph. Offen sind
   die framegenaue Suche in Long-GOP- und VFR-Quellen und ein Ersatz für den
   Kdenlive-Export, der heute auf die Intermediates zeigt.

---

## 4. Workflow

### 4.1 Ist-Zustand

- Datendateien: `manifest.json`, `cache/mix.json`, `beats.yaml`, `order.yaml`,
  `chapters.yaml`, `overrides.yaml`, `vision.yaml`, `edit.yaml`,
  `out/timeline.json`, dazu `contact.html` und die Änderungsliste des Bogens.
- Kommandos: 15. Aufrufe laut Logs in `sommer26`: `build` 32, `preprocess` 25,
  `analyze` 22, `order` 15, `sheet` 15, `render` 13, `probe` 9, `select` 8.
- Spuren von Handsicherung: `edit copy.yaml`, `order-select-20260822.yaml`,
  `order-e1.txt`.
- Von Hand bearbeitet werden `order.yaml` (Zeilen tauschen), `chapters.yaml`
  (Überschriften), `overrides.yaml` (Clipschnitte, Vorgaben) und gelegentlich
  `beats.yaml`.

### 4.2 Der Widerspruch

Invariante 1 nennt `edit.yaml` die einzige Wahrheit und von Hand editierbar.
Tatsächlich ist sie ein Bauprodukt:

- `build` schreibt sie bei jedem Lauf neu,
- eine Prüfsumme in der Kopfzeile schützt sie vor dem Überschreiben,
- Handarbeit muss `slideshow overrides` per Vergleich mit einem frischen Bau
  wieder herausrechnen (`overrides.py`, 437 Zeilen, dazu 559 Zeilen Tests),
- `render` plant aus ihr ein zweites Mal (`plan_from_edit`), und ein Rundlauf
  muss stabil sein. Daraus stammen `check_title_phrases` und die Regel, die
  Phrasenlage als `beats:` des Vorgängers zu materialisieren.

Die tatsächlichen Quellen sind `order.yaml`, `chapters.yaml`, `overrides.yaml`
und `beats.yaml`.

### 4.3 Vorschlag A (kompatibel): `slideshow make`

- Führt `probe → audio → beats → preprocess → build → render` aus und
  überspringt, was aktuell ist. Die Zustandslogik steht in
  `cli._naechster_schritt`; sie druckt den nächsten Befehl bisher nur.
- Hält an, wo ein Mensch gebraucht wird: leere Kapitelüberschriften,
  ungeprüfte Regionenkarte, frische Auswahl.
- Voraussetzung: Materialordner, Tracks, Lücke, Größe und Framerate stehen in
  einer Datei. Heute sind sie Kommandozeilenargumente von `probe`, `audio` und
  `build`.
- `--preview` reicht bis zum 720p-Render.
- Clips werden nach dem Planen aufbereitet. Das erledigt Cache-Vorschlag 1 mit.

### 4.4 Vorschlag B (nicht kompatibel): eine Handdatei `film.yaml`

```yaml
film: {size: 3840x2160, fps: 50, music: [a.mp3, b.mp3], gap: 6}
defaults: {still_tolerance: [4, 6]}
kapitel:
  - title: "Tower Hill & Bridge"
    bg: auto
    items:
      - img_DSC08059
      - clip_C0196: {in: 3, out: 13, audio: true}
      - img_DSC08117: {beats: 12, motion: none}
      # - img_DSC07997   # 20:32 · quer
```

- Ein Kapitel ist eine Gruppe, eine Gruppe ohne `title:` bekommt keine Folie.
- Feinschliff steht am Eintrag, eine Blende als `cut:` am Eintrag.
- Es entfallen: die drei Kapitelanker (`at`, `before`, `group`),
  `order.anchor_chapters`, `chapters --from-groups`, verwaiste Overrides.
- `edit.yaml` wird zur Lock-Datei mit aufgelösten Framegrenzen in einem
  Bauordner. `render` plant nicht mehr selbst.
- Es entfallen damit: Stempel und `hand_edited`, `slideshow overrides`,
  `plan_from_edit`, die Rundlauf-Garantie, `check_title_phrases`.
- `beats.yaml` und `vision.yaml` bleiben eigene Dateien: erzeugt, korrigierbar,
  mit demselben Überschreibschutz, den `edit.yaml` heute hat.
- Migration: `slideshow migrate` liest die drei Dateien und schreibt
  `film.yaml`. Mechanisch machbar, weil alle drei an Medien-IDs hängen. Sie
  muss auf Textebene arbeiten (wie `order.update_order_text`), sonst gehen die
  Alternativen-Kommentare der Auswahl verloren.
- Der Kontaktbogen liest die Kopfzeilen der `order.yaml` als Schnittstelle
  (`sheet._KOPF`). Die Parameter der Auswahl brauchen in `film.yaml` einen
  festen Schlüssel statt eines Kommentars.

Empfehlung: erst A, dann B.

---

## 5. Beat-Ausrichtung und Standzeiten

### 5.1 Messwerte an `london-2026`

- 53 Regionen, davon 43 Beat-Regionen (1735 s) und 10 free (254 s).
- Beat-Regionen dauern im Median 19,7 s, also genau ein `MAX_FIT_WINDOW`;
  25 liegen unter 25 s.
- 32 Grenzen liegen zwischen zwei Beat-Regionen. Bei allen 32 weicht das Tempo
  um höchstens 1,5 % ab (`BEAT_MERGE_BPM_TOL`). Keine ist ein Tempowechsel.
- Phasensprung an diesen Grenzen: Median 118 ms (0,21 Beat), Maximum 287 ms,
  13-mal über einem Viertelbeat.
- Drift in den zwölf Regionen über 40 s (Versatz Raster zu Anschlag in
  10-s-Fenstern): größter Wert je Region zwischen 18 und 62 ms, also bis
  0,14 Beat oder drei Frames bei 50 fps.
- 33 von 290 Slots enden an einer Regionsgrenze.

Gegenprobe an `kenia-2026` (139 Regionen, 98 davon Beat, 4632 s): 57 Grenzen
zwischen zwei Beat-Regionen, 40 davon mit Tempo innerhalb 1,5 %; Phasensprung
dort im Median 69 ms (0,17 Beat), zehnmal über einem Viertelbeat. Größter
Versatz Raster zu Anschlag in einer langen Region: 107 ms, also 5,4 Frames.

### 5.2 Ursache

- `fit_grid` passt je Fenster ein starres Raster an, das Tempo in Schritten von
  0,25 bpm (`beats.py`, Schleife über `np.arange(…, 0.25)`). Bei 96 bpm sind
  das bis zu 0,13 % Periodenfehler, über 20 s rund 26 ms.
- Jedes Fenster bekommt eine eigene Phase. `merge_adjacent_beats` verschmilzt
  nur, wenn Anfang und Ende des Folgefensters innerhalb eines Viertelbeats auf
  dem alten Raster liegen. Die Toleranz ist zugleich die Ungenauigkeit, die
  eine verschmolzene Region still mitnimmt.
- Jede stehengebliebene Grenze ist ein erzwungener Schnitt mit Rest. Daraus
  folgen `_rest_zuschlagen`, `min_still_seconds`, die Halbframe-Toleranz in
  `beat_index_at_or_after` und die Spiegelung derselben Regel in
  `_region_capacity`.
- `standard_beats` (Boden unter `beats_per_still`) kompensiert Oktavfehler der
  Erkennung (190 bis 200 bpm statt des halben Tempos).

### 5.3 Dreifache Kapazitätsrechnung

„Wie viele Bilder passen" beantworten `planner.plan_slots` (maßgeblich),
`planner._region_capacity` (geschlossene Form) und
`planner._material_sequenziell` (Cursor-Simulation). `CLAUDE.md` dokumentiert
drei Fehler aus dem Auseinanderlaufen solcher Kopien: die Formel in
`_phrasenlage`, die Restregel in `_region_capacity`, die Clipdauern in
`material_seconds`. `build_edit_list` schätzt die Länge, plant, misst nach und
plant bis zu viermal neu (`_MAX_LAENGE_PASSES`), jeweils mit bis zu vier
Durchgängen für die Titellage.

### 5.4 Vorschläge

1. Numpy-Onset zentrieren, verwendeten Pfad in `beats.yaml` vermerken (AP1).
2. Beat-Liste statt Raster: die Region trägt verfolgte Beatzeiten, der Planer
   liest `beat_time(k)` aus der Liste. Fenster, Verschmelzen und Phasensprünge
   entfallen; Regionsgrenzen gibt es nur noch an Stille und Trackwechseln.
   `(bpm, offset)` bleibt als Handeingabe erlaubt und wird zur Liste
   ausgerollt. Neues Kartenformat (Version 2); Migration ist ein neuer
   `beats`-Lauf, handkorrigierte Karten werden ausgerollt.
3. Zwischenlösung, falls 2 zu groß ist: Tempo je Fenster unquantisiert
   nachschärfen (Ausgleichsgerade durch die verfeinerten Anschläge) und die
   Phase des Folgefensters an das Vorgängerraster binden.
4. Eine Kapazitätsrechnung: Kapazität und Filmlänge aus einem Probelauf von
   `plan_slots` ablesen. Ersetzt `material_seconds`, `_material_sequenziell`,
   `_region_capacity`, die Toleranz in `_timeline_length` und die
   Nachmess-Schleife.
5. Eigenschaftstests mit krummen Rastern (zufälliges Tempo mal Framerate):
   kein Standbild unter `min_still` ohne ausdrückliche Angabe, jeder
   Standard-Slot hat genau `standard_beats` Beats, `slot_capacity` gleich der
   Zahl geplanter Slots.

---

## 6. Codequalität

Gut gelöst und beizubehalten: absolute Framenummern, Cache-Keys über den
vollständigen Filtergraph, Segmentunabhängigkeit, Fehlermeldungen mit YAML-Pfad
und Ausweg, 628 Tests mit sprechenden Namen.

| Thema | Befund | Vorschlag |
|---|---|---|
| Kommentare | 31 % der Zeilen sind Prosa (4866 von 15 854 ohne Leerzeilen). Viel davon ist Vorgeschichte: „früher stand hier", Messwerte einzelner Projekte | Geschichte in Commits und Briefings; im Code bleibt die Begründung der geltenden Entscheidung |
| `CLAUDE.md` | Die Tabelle „Offene Baustellen" ist ein Änderungsprotokoll mit 20 langen Zellen und wird in jede Session geladen | auf offene Punkte und Fallen kürzen, Erledigtes in ein Änderungsprotokoll |
| `cli.py` | 2096 Zeilen; Fachlogik in `_edit_bauen`, `_zielzahl`, `_clip_mehrbedarf`, `_spans_from_edit`, `_carry_over_audio`; vier fast gleiche `_load_*`; `build_parser` 334 Zeilen | Fachlogik in die Module, ein gemeinsamer Lader für Projektdateien, Parser je Kommando |
| `build_edit_list` | 202 Zeilen, 12 Parameter; `apply_transitions` bis zu dreimal; Intents werden beim Planen verändert und deshalb mit `deepcopy` übergeben | in Schritte mit unveränderlichen Zwischenständen zerlegen; fällt mit Vorschlag 5.4 Nr. 4 großenteils weg |
| `models.py` | 1591 Zeilen: Schema, YAML-Ein- und -Ausgabe, Stempel | Schema und Serialisierung trennen |
| Umgebungsweichen | `onset_envelope` fing jede Exception und wechselte den Algorithmus; `doctor` meldete das als Hinweis | ausdrücklich wählen, Pfad im Ergebnis vermerken (AP1) |
| `render.py:163` | `slots.index(seg.slot)` je Segment, quadratisch | Index am Segment mitführen |

---

## 7. Arbeitspakete

Reihenfolge nach Nutzen und Abhängigkeit. Jedes Paket ist für sich lieferbar.

| AP | Inhalt | Stand |
|---|---|---|
| 1 | Onset-Fehler beheben, Suite grün, Pfad in `beats.yaml` | **umgesetzt 2026-11-10** |
| 2 | Clip-Intermediates beim Rendern und nur im verwendeten Bereich; `slideshow clean` | offen |
| 3 | `slideshow make` | offen |
| 4 | Erster echter `analyze`-Lauf | offen |
| 5 | Beat-Liste und eine Kapazitätsrechnung | offen |
| 6 | `film.yaml` mit Migration | offen |

### AP1: Onset-Erkennung

- Dateien: `src/slideshow/beats.py`, `src/slideshow/cli.py` (`cmd_beats`),
  `src/slideshow/doctor.py`, `tests/test_beats.py`.
- Abnahme:
  - A1: `uv run --extra dev pytest -q` ist ohne librosa grün.
  - A2: dieselben Beat- und Sync-Tests sind mit librosa grün.
  - A3: ein Test erzwingt den numpy-Pfad und misst die Lage eines Anschlags;
    er schlägt fehl, wenn die Zentrierung entfällt.
  - A4: `beats.yaml` nennt den Pfad, der sie erzeugt hat.
- Ergebnis und Messwerte: Abschnitt 8.

### AP2: Cache

- Dateien: `preprocess.py` (Clip-Teil), `render.py`, `cli.py`, `doctor.py`
  (`estimate_space`), `mlt.py`.
- Schritte: (a) `ensure_clip_intermediates(project, edit, plan, manifest)`
  analog zu `ensure_title_assets`, aufgerufen in `cmd_render` und
  `cmd_export_mlt`; der Bereich kommt aus dem Plan (`slot.clip_in`,
  `slot.clip_out`, Handles aus den Blenden). (b) `preprocess` normalisiert
  Clips nicht mehr, es extrahiert höchstens den Originalton. (c) `_size_of`
  durch `edit.size` ersetzen. (d) `slideshow clean`.
- Fallen: `clip.cache_offset` und `cache_duration` im Manifest sind heute die
  Grundlage von `clamp_transitions_for_handles` und `_clip_bounds`; wird der
  Bereich enger, müssen Handles weiterhin mindestens die halbe Blende decken.
  Der Cache-Key des Intermediates enthält das Kommando und damit den Bereich:
  ein geänderter Schnitt rechnet den Clip neu. Die Handles deshalb großzügig
  runden (ganze Sekunden), damit kleine Korrekturen den Key nicht ändern.
- Abnahme:
  - A1: `kenia-2026` neu aufbereitet belegt für Clips höchstens das 1,3-Fache
    von „verwendete Sekunden + 2 s je Clip" mal Bitrate.
  - A2: Framezahl und Sync des Masters unverändert (`verify_master`,
    `test_sync.py`).
  - A3: `clean --dry-run` nennt je Art Anzahl und Größe; `clean` löscht nichts,
    was die aktuelle `edit.yaml` referenziert.
  - A4: ein zweiter `render` ohne Änderung rendert nichts neu.

### AP3: `slideshow make`

- Dateien: `cli.py`, neues Modul für die Ablaufsteuerung, `paths.py`.
- Vorarbeit: Projektdatei mit Material, Tracks, Lücke, Größe, Framerate. Ihr
  Format so wählen, dass es der Kopf von `film.yaml` aus AP6 werden kann.
- Abnahme:
  - A1: in einem leeren Projekt mit Projektdatei führt `make --preview` bis zum
    720p-Film, mit einem Halt an der Regionenkarte.
  - A2: ein zweiter `make` ohne Änderung führt kein Kommando aus.
  - A3: eine geänderte Zeile in `order.yaml` löst `build` und `render` aus,
    nicht `probe` oder `beats`.

### AP4: Motiverkennung

- Zugang klären: Erstanbieter-API (`ANTHROPIC_API_KEY` oder `ant auth login`)
  oder Bedrock mit einer Modell-ID, die in der gewählten Region freigeschaltet
  ist.
- `vision.DEFAULT_MODEL` und `vision.PREISE` gegen die aktuelle Modellliste
  prüfen. Vor einem Wechsel auf `claude-opus-5-5` die Anfrageform prüfen:
  Thinking lässt sich dort nicht abschalten, die Vorgabe für `effort` ist
  `medium`, erzwungenes `tool_choice` wird abgelehnt.
- `uv sync --extra dev --extra librosa --extra vision`, sonst fehlt das SDK.
- Abnahme: A11 des Briefings (`docs/briefing-kenburns-inhaltsabhaengig.md`),
  also ein realer Lauf an einem Projekt, angesehen.

### AP5: Beat-Liste und Kapazität

- Dateien: `beats.py`, `planner.py`, `build.py`, `models.py` (`Region`),
  `docs/edit-yaml.md`.
- Erst die Kapazitätsrechnung (Vorschlag 5.4 Nr. 4), weil sie ohne
  Formatänderung auskommt und die Eigenschaftstests (Nr. 5) als Netz liefert.
  Dann die Beat-Liste.
- Abnahme:
  - A1: `scripts/beat_drift.py london-2026` meldet keine Grenze zwischen zwei
    Beat-Regionen mit Tempo innerhalb 1,5 % und Phasensprung über 0,1 Beat.
  - A2: größter Versatz Raster zu Anschlag in jeder Region unter einem Frame.
  - A3: `slot_capacity` gleich der Zahl der Slots aus `plan_slots`, als
    Eigenschaftstest über zufällige Karten.
  - A4: `material_seconds`, `_material_sequenziell` und `_region_capacity`
    existieren nicht mehr.

### AP6: `film.yaml`

- Setzt AP3 voraus (Projektdatei, Bauordner).
- Abnahme:
  - A1: `slideshow migrate` an `london-2026` und `kenia-2026`, danach derselbe
    Plan wie vorher (Slotgrenzen in Frames identisch).
  - A2: die auskommentierten Alternativen der Auswahl stehen unverändert in
    `film.yaml`.
  - A3: `slideshow sheet` und `order --apply` arbeiten auf `film.yaml`.

---

## 8. Umsetzungsprotokoll

### AP1, 2026-11-10

Geändert:

- `beats._spectral_flux` zentriert jedes Fenster auf `n · hop` (Auffüllen um
  eine halbe Fensterlänge an beiden Enden, wie librosa). Die Kurve ist dadurch
  vier Frames länger und hat dieselbe Länge wie die von librosa.
- `beats.onset_mit_pfad` gibt neben der Kurve den Pfad zurück (`librosa` oder
  `numpy`). Ein fehlendes librosa bleibt still; ein installiertes, das sich
  nicht laden lässt oder scheitert, wird jetzt gewarnt statt verschluckt.
- `detect_regions` schreibt den Pfad nach `audio.onset`; `slideshow beats`
  zeigt ihn unter der Regionentabelle. Eine Karte mit `--bpm` trägt keinen.
- `doctor` nennt als Abhilfe das Extra statt des nackten Pakets.
- Drei neue Tests in `tests/test_beats.py`:
  `test_spectral_flux_legt_den_anschlag_auf_seine_zeit`,
  `test_ohne_librosa_treffen_die_offsets_den_ersten_schlag` (erzwingt den
  numpy-Pfad über `sys.modules`), `test_die_karte_nennt_den_onset_pfad`.

Messwerte (Versatz Offset zu erstem Schlag, zwei Klick-Fixtures mit
120/90 und 117/133 bpm):

| Fassung | ohne `refine_offset` | mit `refine_offset` |
|---|---|---|
| vorher (Fensteranfang) | −52 bis −58 ms | −52 bis −58 ms |
| zentriert | −6 bis −11 ms | −0,4 bis −0,6 ms |

Die Konfidenz bleibt in beiden Fassungen gleich (0,89 bis 0,93); `fit_grid`
und `CONF_THRESHOLD` sind unberührt.

An echtem Material (`testset1`, Trockenlauf): der numpy-Pfad liefert Offset
0,189 s, die vorhandene Karte aus dem August (librosa) 0,182 s. Die Phase
stimmt auf 7 ms überein. Die Karten sind trotzdem nicht gleich: numpy
verschmilzt dort beide Fenster zu einer Region mit 118,5 bpm, librosa ließ
zwei stehen (118,25 und 118,5 bpm).

Abnahme:

- A1 (Suite ohne librosa grün): 631 von 631 in 241 s.
- A2 (Beat- und Sync-Tests mit librosa grün): 41 von 41, in einer
  Wegwerfumgebung (`uv run --with librosa`) und danach im venv mit
  librosa 0.11.0 und numba 0.66.0 auf Python 3.14. Die volle Suite ist mit
  librosa nicht noch einmal gelaufen.
- Gegenprobe: mit librosa liefert der Trockenlauf an `testset1` exakt die
  vorhandene Karte (Offset 0,182044 s, 118,25 bpm). Die Augustkarten stammen
  also vom librosa-Pfad.
- A3: erfüllt durch die ersten beiden neuen Tests. Der erste misst am selben
  Signal, an dem die alte Fassung −48 bis −51 ms lieferte; seine Grenze ist ein
  Kurvenframe (23 ms).
- A4: erfüllt, `audio: {file: …, duration: …, onset: numpy}`.

Offen geblieben:

- `sommer26` ließ sich nicht nachmessen (kein `cache/mix.flac`).
- `kenia-2026` liegt im Median 10 ms neben dem Anschlag (Raster zu spät,
  Quartile −20 und −2 ms). Das ist ein halber Frame und gehört zu AP5, nicht
  hierher.
- Der librosa-Pfad wird nur geprüft, wo librosa installiert ist. Soll die
  Suite beide Pfade immer prüfen, muss librosa in das Extra `dev`.
