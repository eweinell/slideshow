# Hinweise für Claude Code

Musiksynchroner 4K-Slideshow-Renderer, Python-CLI. Fachliche Einführung steht in
der [README](README.md); die Schlüssel der Edit-List in
[`docs/edit-yaml.md`](docs/edit-yaml.md).

Diese Datei sammelt nur, was man beim Arbeiten *im* Repo sonst jedes Mal neu
herausfindet.

## Umgebung

**Diese Datei schreibt die Maschine nicht fest.** Hardware und Werkzeugstände
wechseln zwischen Sessions; was zählt, ist der Messwert von jetzt.
`slideshow doctor` gibt ihn vollständig aus — Binaries samt Version, GPU,
NVENC-Fähigkeiten, Tonemapping-Pfade. Einmal laufen lassen schlägt jede Annahme.

- **Windows, PowerShell** ist der verlässliche Weg. Das Bash-Tool ist
  *manchmal* verfügbar und meldet sonst `No suitable shell found`. Nicht darauf
  bauen, sondern kurz prüfen — die POSIX-Skripte aus `docs/` laufen damit
  unverändert, statt sie nach PowerShell übersetzen zu müssen.
- **`.venv` enthält kein pytest.** Tests laufen über `uv`:
  ```powershell
  uv run --extra dev pytest -q
  ```
  Das CLI dagegen aus dem venv: `.\.venv\Scripts\slideshow.exe`
- **`librosa` und `anthropic` sind Extras und überleben kein neu angelegtes
  venv.** Nachinstallieren:
  ```powershell
  uv sync --inexact --extra dev --extra librosa --extra vision
  ```
  Ohne `--inexact` wirft `uv sync` alles hinaus, was nicht zu den genannten
  Extras gehört. `uv run --extra dev …` lässt Vorhandenes dagegen stehen.
  **librosa entscheidet mit über die Regionenkarte**: ohne es rechnet
  `beats` die Onset-Kurve mit numpy. Beide Pfade liegen zeitlich richtig, die
  Karten unterscheiden sich trotzdem. Welcher lief, steht in `beats.yaml`
  unter `audio.onset` und in der `doctor`-Zeile „Beat-Analyse". Ein Test
  erzwingt den numpy-Pfad (`test_ohne_librosa_treffen_die_offsets_den_ersten_schlag`);
  den librosa-Pfad prüft die Suite nur, wenn es installiert ist.
- **ffmpeg, exiftool, ImageMagick und `melt`** sind installiert. Entwickelt
  wurde gegen ffmpeg 8.1.2; **9.0.1 ist geprüft und freigegeben**
  ([`docs/pruefbericht-ffmpeg9.md`](docs/pruefbericht-ffmpeg9.md)). Die
  Versionsnummer hier nicht raten — `doctor` fragt sie ab.
- **Ob eine NVIDIA-GPU da ist, entscheidet die Maschine, nicht diese Datei.**
  `doctor` meldet die NVENC-Encoder immer als vorhanden (ffmpeg hat sie
  einkompiliert); erst der Praxistest darunter — Zeilen „GPU", „NVENC
  10-Bit-HEVC", „NVENC-Sessions" — sagt, ob sie wirklich laufen. Ohne GPU prüft
  die Codec-Auswahl praktisch nach und fällt korrekt auf libx265 zurück;
  Vorschläge wie `--codec av1_nvenc` aus dem `probe`-Report schlagen dann fehl.
  **Ist eine da, lohnt sie sich deutlich**: NVENC verkürzt den 4K-Render um ein
  Vielfaches gegenüber libx265, NVDEC nimmt 4:2:2-10-Bit-Clips ohne CPU-Decode
  an, und `--codec av1_nvenc` wird zur echten Option. Der Preis ist die
  NVENC-Sessiongrenze — sie deckelt den Render-Pool (oft auf 4), unabhängig von
  `--jobs` und der Kernzahl.
- **`libplacebo` ist nutzbar, wo ein Vulkan-Gerät läuft** — auch eine
  integrierte GPU genügt. `tonemap_chain` bevorzugt es dann, und `zscale` kommt
  nie zum Zug. Ein Test, der `zscale_usable=True` setzt und `libplacebo_usable`
  auf dem Maschinenwert lässt, prüft dort den anderen Pfad als gedacht (Fall
  `test_tonemapping_steht_vor_dem_scale`, siehe
  [`docs/briefing-hlg-ffmpeg8.md`](docs/briefing-hlg-ffmpeg8.md), Abschnitt 8).
  Welcher Pfad hier greift, steht in der `doctor`-Zeile „HDR-Tonemapping".

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

### Die teurere Falle: Kodierung

**`Get-Content`/`Set-Content` zerstören die Umlaute in jeder Datei dieses
Repos.** In PowerShell 5.1 liest `Get-Content -Raw` mit der ANSI-Codepage und
`Set-Content -Encoding utf8` schreibt ein BOM. Ein Textersatz über diesen Weg
macht aus `großem` ein `groÃŸem`, aus `–` ein `â€“` — in *allen* Zeilen, nicht
nur der bearbeiteten. Der Schaden fällt erst im `git diff` auf (400 geänderte
Zeilen statt 5) und sieht dort nach einem Zeilenenden-Problem aus.

Für Textänderungen das Edit-Werkzeug nehmen. Muss es PowerShell sein, dann über
.NET mit ausdrücklicher Kodierung:

```powershell
$t = [System.IO.File]::ReadAllText($pfad, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($pfad, $t, (New-Object System.Text.UTF8Encoding($false)))
```

Ist es schon passiert, lässt es sich umkehren — die UTF-8-Bytes wurden als
CP1252 gelesen:

```powershell
$b = [System.Text.Encoding]::GetEncoding(1252).GetBytes($kaputt)
$heil = [System.Text.Encoding]::UTF8.GetString($b)
```

## Tests

**Die Suite ist grün** — es gibt keine geduldeten Fehlschläge mehr. Wer einen
sieht, hat ihn verursacht (oder die Umgebung hat sich geändert).

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
- **Ein voller 4K-Render dauert auf der CPU 45–90 Minuten** (mit NVENC
  deutlich weniger — wie viel, sagt nur die Messung). Für alles, was nur das
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
| **Prüfbericht November 2026** | [`docs/pruefbericht-2026-11.md`](docs/pruefbericht-2026-11.md) ist die **Arbeitsgrundlage für die nächsten Umbauten**: Befunde mit Messwerten (Cache, Workflow, Beat-Ausrichtung, Codequalität) und sechs Arbeitspakete mit Abnahmekriterien in Abschnitt 7. **AP1 (Onset-Erkennung) und AP2 (Clip-Intermediates beim Rendern, `slideshow clean`) sind umgesetzt**, AP3 (`slideshow make`) ist das nächste. Seit AP2 gilt: `preprocess` vergibt Clips nur die Kennung `cache/<id>.mov`; das Intermediate samt Originalton entsteht in `render`/`export-mlt` über `preprocess.ensure_clip_intermediates`, nur im Bereich aus `clip_bereiche(plan)`. **Falle:** `cache_offset`/`cache_duration` sind danach die Zeitbasis der *Datei* und werden nur im Speicher gesetzt — Grenzen für `in`/`out` liefert `build.clip_laenge` (`effective_duration`). Wer dort wieder `cache_duration` liest, verengt den Spielraum mit jedem Lauf. Nach einem vollen Render löscht `clean.remember_segments` verwaiste Segmente desselben Profils. Wer ein Paket abschließt, trägt Stand und Messwerte dort in Abschnitt 7 und 8 nach. Nachmessen: `scripts/cache_bilanz.py`, `scripts/beat_drift.py`. |
| **Beat-Erkennung bei langen Stücken** | **Umgesetzt** ([`docs/briefing-beat-detection.md`](docs/briefing-beat-detection.md)). Lange Abschnitte werden in `MAX_FIT_WINDOW`-Fenster (20 s) zerlegt, einzeln gefittet und über `merge_adjacent_beats` wieder verschmolzen. Offen bleibt nur die Abdeckung: 88,2 % statt der angepeilten 90 %, weil der Ausklang des Testtracks (die letzten ~40 s) rhythmisch wirklich zu dünn ist. |
| **`fit_grid` misst lokal, nicht global** | Die Konfidenz ist nur für Fenster bis ~30 s kalibriert. `MAX_FIT_WINDOW` und `CONF_THRESHOLD` hängen zusammen — wer an einem dreht, muss den anderen nachmessen. Unter 16 s zerfallen zusätzlich die Fixture-Songs, und die konstruktive Garantie hinter Abnahmekriterium A5 fällt. |
| **Titel- und Zwischenfolien** | **Vollständig** ([`docs/briefing-titelfolien.md`](docs/briefing-titelfolien.md), Stufen 1–3). `slideshow chapters` → `chapters.yaml` → `build` → `render`. Generator (`titles.py`), Phrasenlage, Stille-Regel, Fokusblende, Rundlauf, Deckungsrechnung, MLT. Offen ist nur die Sichtprüfung in Bewegung — sie braucht ffmpeg und eine echte Tonspur. |
| **Manuelle Reihenfolge** | **Vollständig** ([`docs/briefing-manuelle-reihenfolge.md`](docs/briefing-manuelle-reihenfolge.md), Stufen 1–2). `slideshow order` → `order.yaml` → `build` → `render`. Generator (`order.py`) mit `--by day\|place\|none` und `--update`, Auflösung mit allen drei Fehlerfällen samt Zeile, `rest: error\|append\|drop`, `group:` als dritter Kapitelanker. Dazu `slideshow chapters --from-groups` (`order.group_anchors` → `chapters.dump_group_chapters_yaml`): ein Kapitel je Block statt aus Zeitlücken geraten — [Rezept 4](docs/rezepte.md#4-kapitelweise-erzählen). Offen ist nur Stufe 3 (wiederholtes Material, `--from edit.yaml`, Kontaktbogen) — bewusst zurückgestellt. |
| **Auswahl aus großem Material** | **Vollständig** ([`docs/briefing-auswahl.md`](docs/briefing-auswahl.md), Stufen 0–3). `slideshow select` wählt nach Zeitstruktur (Trauben, gedämpfte Tagesquote, Spreizung) und schreibt `order.yaml` mit `rest: drop`; `preprocess` folgt der Auswahl (`--order`/`--all`); `slideshow sheet` (auch `select --sheet`) erzeugt den Kontaktbogen `contact.html` — [Rezept 5b](docs/rezepte.md#5b-aus-tausend-bildern-auswählen-lassen). Dazu Stufe 0: `read_exif_batch` läuft über `-@ argfile` (brach vorher **ab 193 Dateien** ab und meldete dabei „Programm nicht gefunden: exiftool"). Offen ist nur A10, die Sichtprüfung an einem Sammelbecken über 1000 Aufnahmen. |
| **Handarbeit in `edit.yaml`** | **Umgesetzt.** `overrides.yaml` ist die dritte Eingabedatei neben `chapters.yaml` und `order.yaml`: `defaults:`, `media:` (nach Medien-ID) und `cuts:` (Blende, verankert am *folgenden* Medium). `slideshow overrides` erzeugt sie, indem es die vorhandene `edit.yaml` gegen einen frischen Bau hält — Reihenfolge, Titel und `fps`/`size` werden dabei **gemeldet** statt übernommen, weil sie anderswohin gehören. Dazu die Reißleine: `dump_edit_yaml` schreibt eine Prüfsumme in die Kopfzeile, und `build` bricht ab, statt eine abweichende Datei zu überschreiben (`--force` ist umgedeutet und schreibt trotzdem). Nebenbefund mit erledigt: die `Defaults` lebten bisher nur zur Laufzeit — was kein CLI-Argument hat, ließ sich gar nicht dauerhaft setzen. Offen ist nur die Sichtprüfung an einem echten Projekt. |
| **Rückweg aus dem Bogen** | `slideshow order --apply auswahl.txt` (oder `-` für die Zwischenablage) spielt die markierten Änderungen ein: `sheet.parse_changes` liest das Format, `order.apply_changes` schreibt. **Der Bogen schreibt weiterhin nichts** — Entscheidung 7 gilt, nur der Rückweg ist jetzt ein Kommando statt Handarbeit (an echtem Material waren es ~160 Tausche). Zwei Fallen dabei: eine Chronologieprüfung über eine `set` meldet jede sortierte Datei als unsortiert, und PowerShell setzt vor jede Pipe ein BOM. |
| **Bogen: Ansicht „Reihenfolge“** | **Umgesetzt** ([Rezept 5b](docs/rezepte.md#5b-aus-tausend-bildern-auswählen-lassen)). Zweite Ansicht im selben `contact.html`, umschaltbar unten links, `sheet --sequence` öffnet damit: nur die gelisteten Medien, in Dateireihenfolge, nach `order.yaml`-Blöcken (`sheet.folge_bloecke`), Ziehen verschiebt, Klick nimmt hinaus. Markierungen gelten je Medium in beiden Ansichten. Nach einem Zug gibt der Bogen die **ganze Folge** aus (`# reihenfolge`, `# stand: <order_fingerprint>`, `# block: …`). `sheet.parse_reorder` erkennt sie an der Kennzeile, sonst bliebe es `parse_changes`, und `order.apply_reorder` schreibt sie. Drei Dinge zum Merken. **(1)** In der Datei wandern **nur die bewegten Zeilen**: was in der längsten aufsteigenden Teilfolge (`order._lis`) liegt, bleibt stehen. Ein Bewegter setzt sich hinter seinen neuen Vorgänger, die `statt:`-Zeilen reisen mit (`_gepaeck`). Die naheliegende Platz-für-Platz-Füllung schob an `london-2026` 20 Zeilen für drei Handgriffe und stellte Abwahl-Kommentare neben fremde Bilder. **(2)** Die Prüfsumme ist Pflicht, nicht Zierde: eine vollständige Folge überschriebe sonst Handarbeit, die nach dem Bogen kam. Die Mengenprüfung allein fängt nur hinzugekommene oder verschwundene IDs. **(3)** Ein Tausch in der Auswahl setzt das Geschwister in der Reihenfolge **hinter seinen Traubenchef**. Weicht das von der Zeitregel aus `apply_changes` ab, gilt es als Zug (`bewegt`), damit Ausgabe und Anzeige übereinstimmen. Das JS darf kein `data-id="…"` als Text enthalten (Selektoren laufen über `JSON.stringify`), sonst zählt `test_nur_die_auswahl_laesst_die_geschwister_weg` es als Kachel. Geprüft ist das JS mit jsdom (Scratch), **nicht im echten Browser**; offen ist die Sichtprüfung des Ziehens. |
| **Kontaktbogen: zwei Fallen** | `sheet` **würfelt nicht neu**, es rekonstruiert aus `order.yaml` (`selection_from_order`) — gewählt ist, was in `items:` steht. Damit ist der **Kopf der `order.yaml` eine Schnittstelle**: `read_params` liest Seed, Traubenabstand und Mindestlangkante aus den Kommentarzeilen zurück, die `select` dort hinterlegt. Wer die Kopfzeilen in `select._kopf` umformuliert, muss `_KOPF` in `sheet.py` mitziehen, sonst rechnet der Bogen still mit den Vorgaben. Zweitens: die JPEGs in `testset1/` haben **keine eingebettete EXIF-Vorschau** (RawTherapee-Export), der Bogen skaliert dort also über ffmpeg — der schnelle Pfad lässt sich daran nicht messen. |
| **HLG unter ffmpeg 8.1.2** | **Umgesetzt** ([`docs/briefing-hlg-ffmpeg8.md`](docs/briefing-hlg-ffmpeg8.md)). Es war eine **Fixture-, keine Produktionsregression**: ffmpeg 8 übernimmt `-color_trc`/`-color_primaries` als Ausgabeoption nicht mehr. `make_clips` taggt jetzt über `setparams` (derselbe Weg wie `doctor`) und prüft mit `fixtures.hat_transfer` nach dem Encode nach — die fehlende Zusicherung war die eigentliche Ursache der Fehldiagnose. `test_tonemapping_steht_vor_dem_scale` war aus einem **zweiten**, unabhängigen Grund rot: es verlangte `zscale`, während `tonemap_chain` libplacebo bevorzugt, wo Vulkan läuft — der Test prüft jetzt die Reihenfolge statt den Tonemapper. Offen bleibt nur A4, der Beleg an echtem HLG-Material. |
| **Titelfolien-Hintergrund nach Tragfähigkeit** | **Umgesetzt** ([`docs/briefing-titelfolien-hintergrund.md`](docs/briefing-titelfolien-hintergrund.md)). `bg: auto` misst beim Bauen die nötige Abdunklung des ersten Abschnittsbildes (`build._bg_nach_tragfaehigkeit` über `titles.measure_darkening` — derselbe Codepfad wie das Backen); erst unterhalb von `auto_darken_min` gewinnt das tragfähigste der ersten fünf. **Die Schwelle ist 0,50, nicht die im Briefing empfohlenen 0,40**: die Abdunklung ist ein Faktor auf sRGB, Reinweiß trägt den Text bei 0,45, und mit `min_contrast: 4.5` hat die Skala nur die Stufen 0,55/0,50/0,45 — bei 0,40 hätte das Merkmal nie ausgelöst. Offen ist nur A8, die Sichtprüfung an einem hellen Kapitelanfang. |
| **Ken-Burns-Richtung** | **Umgesetzt** (Entscheidung 7 des Titelfolien-Briefings). `plan_motion` nimmt die Kennung (`src`) statt des Slot-Index, `motion_key` hasht sie mit `blake2b`. Einfügen und Umsortieren sind damit dauerhaft billig; der Preis ist ein nur noch statistischer Zoomwechsel und identische Bewegung bei einem doppelt verwendeten Bild. |
| **Inhaltsabhängige Ken-Burns-Effekte** | **Umgesetzt** ([`docs/briefing-kenburns-inhaltsabhaengig.md`](docs/briefing-kenburns-inhaltsabhaengig.md), Rev. 2). `slideshow analyze` → `vision.yaml` (Bildfakten, nicht Bewegungen) → `kbplan.plan_kb` → `kb:` je Standbild. Alle drei Fallen sind adressiert: die Abwechslung läuft über den **Kennungs-Hash über eine Kandidatenliste**, nicht über die Sequenz (A3b); der Planer läuft **vor** `_couple_focus_motion` und überspringt das Folgebild einer Fokusblende (A9); die Klemmung `\|c−0,5\| ≤ 0,5 − 1/(2z)` führt `kbplan` selbst (A1b). Vier Dinge, die beim Weiterbauen zu wissen sind: **(1)** geprüft wird gegen die **gerundete** `KBSpec` — auf vier Nachkommastellen zu runden hat A1b einmal gebrochen, dafür gibt es `RUNDUNGSMARGE` und `haelt_gerundet`. **(2)** Die Zoomrichtung steht in der *innersten* Kandidatenschleife: bei Passungsgleichstand entscheidet die Erzeugungsreihenfolge, und mit ihr außen bekamen achslose Szenen immer einen Hineinzoom (A2 lief über 65 %). **(3)** `landscape_wide` hat bewusst *keinen* `zoom_total`-Deckel, obwohl die Tabelle in 6.1 „Zoom klein" sagt — der Zoom ist das Zahlungsmittel für den Schwenk, ein Deckel von 0,15 begrenzte den „großen Schwenk" derselben Zeile auf 0,065. **(4)** `effort` ist auf Haiku 4.5 ein 400, siehe `vision.OHNE_EFFORT`. Offen ist A11, die Sichtprüfung an echtem Material — und Abschnitt 14 (Titelfolien-Hintergrund per API, Kontaktbogen-Etiketten, Wahl in der Traube), jeder Punkt ein eigenes Briefing. |
| **Verschenkte Beats durch Framerundung** | **Umgesetzt.** `RegionGrid.beat_index_at_or_after` verglich mit `_EPS = 1e-6`, während der Cursor **framegerundet** ist. Lag er eine halbe Frame hinter einem Beat — auf demselben Frame, also *auf* dem Beat —, galt er als „danach", und der Slot bekam `beats_per_still + 1` Beats. Das traf **102 von 293 Slots in `sommer26`, zusammen 53 s = 8,6 Standardbilder**. Auf den Sync wirkte es nie (die Schnitte lagen so oder so auf dem Raster), es war die *Kapazität*, die still verlorenging: `material_seconds` versprach 1807,9 s gegen 1816,7 s Ton, und trotzdem fielen sieben Medien hinten heraus. Die Toleranz ist jetzt eine halbe Frame in Beats (`0,5 / (fps · beat)`) statt einer numerischen Konstante — bei 117 bpm auf 50 fps sind das 0,0196 Beats gegen vorher 1e-6, also Faktor 20 000. **Sichtbar wird der Fehler nur bei krummen Rastern**: 120 bpm auf 60 fps ergibt genau 30 Frames pro Beat, dort tritt er nie auf — deshalb hat ihn keine Fixture gefunden. Gegenprobe im Test: `test_die_karte_haelt_was_slot_capacity_verspricht`. **Die Formel stand zweimal da**: `build._phrasenlage` hatte sie ausgeschrieben, lief nach der Korrektur um einen Beat auseinander und setzte Titelfolien 0,52 s neben die Phrasengrenze. Sie ist jetzt eine freie Funktion `planner.beat_index_at_or_after`, die beide aufrufen. Wer an der Toleranz dreht, prüft `test_die_phrasenlage_haelt_auch_bei_krummem_raster` — der Test schlägt fehl, sobald die Kopie zurückkehrt. |
| **Mindeststandzeit am Regionsende** | **Umgesetzt.** Eine Beat-Region ist nie ein ganzzahliges Vielfaches der Slotlänge; der Rest bekam ein eigenes Bild — in `sommer26` viermal unter 1 s, im schlimmsten Fall 0,30 s mit 0,06 s freier Sicht. `planner._rest_zuschlagen` schlägt ihn stattdessen dem Vorgänger derselben Region zu, sobald er unter `defaults.min_still_seconds` liegt (Vorgabe: `still_tolerance[0]`, also 3,0 s). **Zwei Ausnahmen, beide notwendig:** am Filmende greift die Regel nicht (dort ist die Grenze kein Schnitt, und `stretched` erledigt es schon — sonst kostet sie bei materialbestimmter Länge grundlos ein Medium), und hinter einem Clip lässt sich nicht verlängern. Was durchfällt, wird gemeldet. `_region_capacity` führt dieselbe Rechnung, sonst wählte `select` gegen eine andere Regel. **Folge: pro betroffener Region ein Bild weniger** — in `sommer26` schloss das nebenbei die 52,5 s Unterdeckung. Verstärkt wird der Effekt von der Beat-Erkennung: nicht verschmolzene `MAX_FIT_WINDOW`-Fenster (dBPM knapp über `BEAT_MERGE_BPM_TOL`) liefern zusätzliche Regionsgrenzen und damit zusätzliche Reste. |
| **Boden unter `beats_per_still`** | **Umgesetzt** ([`docs/edit-yaml.md`](docs/edit-yaml.md#der-boden-unter-beats_per_still)). `planner.standard_beats` hebt den *globalen* `beats_per_still` pro Beat-Region an, bis `Beats × Beat ≥ still_tolerance[0]` gilt (bei 200 bpm 8 → 10, in Beatpaaren — ein Schritt von 4 überschoss: bei Boden 4,0 s wurden aus 123 bpm 5,9 s). Anlass: `kenia-2026` — die Beat-Erkennung verdoppelt dort mehrfach auf 190–200 bpm (`_BPM_RANGE` endet bei 200, `_octave_up` verdoppelt aktiv), und 59 von 168 Standbildern standen 2,4 s. **Die Regel steht an einer Stelle**, und `build` schreibt den angehobenen Wert als `beats:` in `edit.yaml` — ein dort stehender Wert gilt ausdrücklich und liefe sonst am Boden vorbei. Ein `beats_per_still` an der Region bleibt unangetastet. **Offen:** die Ursache, die Oktavfehler selbst (190–200 bpm sind meist doppeltes Tempo), und der `coverage_advice`-Vorschlag „`beats_per_still` senken", der in schnellen Regionen jetzt wirkungslos ist. |
| **Filmlänge bei Clips** | **Umgesetzt** ([`docs/edit-yaml.md`](docs/edit-yaml.md#die-filmlänge-wenn-das-material-sie-bestimmt)). `material_seconds` zählte jedes Medium als *einen* Standard-Slot; ein Clip belegt aber seine Länge. Bestimmt das Material die Laufzeit, fiel sie um den Mehrbedarf der Clips zu kurz aus — an `kenia-2026` 173 s, **36 Bilder hinten „ungenutzt"**, obwohl 4800 s Musik übrig waren. Jetzt: `material_seconds(…, fixed=)` mit den Dauern je Medium (`build._feste_dauern`), danach **Nachmessung am Plan** (`_MAX_LAENGE_PASSES`): Material übrig → verlängern, letztes Bild gestreckt → auf das natürliche Ende kürzen. **Zwei Fallen:** `plan_with_titles` verändert die `Intent`s (`_setze`), die Durchgänge planen deshalb auf `copy.deepcopy(intents)`. Und `select` (`slot_capacity`) kennt Clipdauern weiterhin nur über `reserve` — die Zielzahl dort ist unverändert eine Zählung. |
| **Schwenk am Zoomanfang** | **Umgesetzt.** Der Schwenk hat jetzt ein ruhendes Ende in der Bildmitte (`defaults.kb.pan_anchor: center`) und wird auf `0,5 − 1/(2z)` des größten Zooms gedeckelt — der geplante Weg ist damit auch der sichtbare. Vorher lief er symmetrisch durch die Mitte und kippte sichtbar die Richtung, sobald die Klemmung bei `z = 1,0` aufging (gemessen über 5 s: 0,500 → 0,526 → 0,447). Die alte Auslegung bleibt als `pan_anchor: through` erreichbar; `pan_amount` in einer alten Datei setzt sie selbst. **Folge: `pan_rate`/`pan_total` greifen mit den Vorgaben nicht mehr** — es gewinnt der Deckel. Mehr Schwenk heißt jetzt mehr `zoom_total`. |
| **Originalton der Videoclips** | **Umgesetzt** ([`docs/originalton-clips.md`](docs/originalton-clips.md)). Nachtrag 5.5 — steht nicht im Briefing, das den Originalton ausnahmslos verwirft (Abschnitt 7 baut die Framerate-Politik darauf auf). Angefordert wird er in `overrides.yaml`: `defaults: clip_audio:` filmweit, `media: {clip_x: {audio: true, music: 0.2}}` je Clip. **Der Ton bleibt aus den Segmenten heraus** — sie bleiben `-an`, untergemischt wird beim Muxen, und deshalb ändert das Umschalten keinen einzigen Cache-Key (Invariante 3/4): die Entscheidung lässt sich nach einem 90-Minuten-Render noch umwerfen. **Die Musik wird gedückt, nicht angehalten** — ein Pausieren bräche Invariante 2, jeder folgende Beat verschöbe sich; der Pegel gilt je Clip, der Verlauf ist die Summe der Absenkungen über disjunkten Fenstern. Drei Fallen, die Messungen an echtem Material gezeigt haben: **(1)** der Planer streckt das letzte Segment auf die Timelinelänge — ohne Klemmung auf die vorhandene Tonlänge dückte ein 4-s-Clip die Musik über 17,5 s Standbild weg. **(2)** Die Ausblende am Filmende muss hinter dem `amix` sitzen, nicht nur auf der Musik, sonst läuft ein Schlussclip weiter, während das Bild schwarz ist. **(3)** Rich frisst `[aout]` als Stil-Tag und `0:v:0` als Emoji — der Trockenlauf gab bis dahin nicht kopierbare Kommandozeilen aus. Offen ist nur die akustische Sichtprüfung (`docs/manuelle-checks.md`, Punkt 7). |
| **ffmpeg 9** | **Geprüft und freigegeben** ([`docs/pruefbericht-ffmpeg9.md`](docs/pruefbericht-ffmpeg9.md) beantwortet [`docs/pruefauftrag-ffmpeg9.md`](docs/pruefauftrag-ffmpeg9.md)). Elf Messungen an 9.0.1: Suite grün, die volle Kette `probe → audio → preprocess → beats → build → render` in jeder Stufe Exitcode 0, Framezahl exakt, Sync auf die Millisekunde. Zwei Dinge zum Merken. **(1)** Die `zoompan`-Bittiefensonde in `doctor.py` liest Logtext, und eine Quelltextanalyse hatte behauptet, die Formulierung sei in 9 verschwunden — **sie ist unverändert da**, die Sonde antwortet richtig (`zoompan` kürzt weiterhin auf 8 Bit, unabhängig nachgemessen mit einer 10-Bit-Rampe: 256 statt 1024 unterscheidbare Werte). **(2)** Der einzige Befund: `-color_trc` und `-color_primaries` als *Ausgabeoption* landen nicht mehr in der Datei — der Master trägt `colorspace=bt709`, `color_range=tv` und `hvc1`, aber `color_transfer=unknown` und `color_primaries=unknown`. Das entsteht schon im ersten Encode (nicht beim Concat oder Mux) und gilt für jeden Encoder und Container; `setparams` vor dem Encoder rettet es vollständig — derselbe Weg, den `make_clips` seit dem HLG-Briefing schon geht. Vermutlich kein 9er-Regress, sondern mit 7 oder 8 gekommen: `encoders.COLOR_ARGS` liefert also **auch unter 8.1.2** einen unvollständig getaggten Master, und `verify_master` schaut nicht auf Farb-Tags. Nicht repariert (der Prüfauftrag misst nur). |
