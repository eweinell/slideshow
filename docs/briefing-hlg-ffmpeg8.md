# Briefing: HLG unter ffmpeg 8 — Befund und Abhilfe

**Status:** Umgesetzt am 08.08.2026 (A1–A3, A5); **A4 offen** — der Beleg an
echtem HLG-Material, siehe [`manuelle-checks.md`](manuelle-checks.md), Check 3 ·
Befund erhoben am 07.08.2026 gegen 360ef0b, ffmpeg 8.1.2 (Windows) ·
**Betrifft:** `fixtures.py`; `probe.py`, `preprocess.py` und `doctor.py` bleiben
unverändert · **Aufwand:** klein — der Wert dieses Briefings ist die Diagnose,
nicht der Patch.

> **Nachtrag zur Umsetzung.** Festlegung 3 („die drei Tests werden nicht
> angefasst") hat für zwei der drei gehalten. `test_tonemapping_steht_vor_dem_scale`
> war aus einem **zweiten, unabhängigen Grund** rot, den der Befund nicht
> erfassen konnte, weil er die Tags maß und nicht den Filtergraphen: der Test
> schaltet `zscale_usable` ein, lässt `libplacebo_usable` aber auf dem Wert der
> Maschine — und `tonemap_chain` bevorzugt libplacebo, wo Vulkan läuft. Der
> Test war damit nur dort grün, wo libplacebo *nicht* nutzbar ist. Er prüft
> jetzt die **Reihenfolge** (Tonemapper vor Scale) statt den Namen des
> Tonemappers; das ist seine Aussage laut Docstring, und sie gilt für beide
> Pfade. Siehe Abschnitt 8.

---

## 1. Symptom und bisherige Deutung

Drei Tests in `tests/test_media.py` schlagen unter ffmpeg 8.1.2 dauerhaft
fehl: `test_hdr_wird_erkannt`, `test_tonemapping_steht_vor_dem_scale`,
`test_ohne_tonemapper_greift_die_naeherung`. `CLAUDE.md` führt das bisher als
„HLG wird unter ffmpeg 8.1.2 nicht mehr erkannt (`detect_hdr` liefert `''`),
die Tonemapping-Kette greift deshalb nie" — also als **Produktionsregression**:
echte HLG-Clips landeten ungetonemappt und flau im SDR-Master, genau der
Fehler, den Abnahmekriterium 4 des Ursprungs-Briefings ausschließt.

Diese Deutung ist nach dem Befund unten **falsch**. Kaputt ist die
Testfixture, nicht die Erkennung.

## 2. Befund (Messprotokoll vom 07.08.2026)

Alle vier Punkte heute auf dieser Maschine nachgemessen:

1. **Die Fixture erzeugt keinen HLG-Clip mehr.** `make_clips`
   (`fixtures.py:209-213`) taggt über die *Ausgabeoptionen*
   `-color_trc arib-std-b67 -color_primaries bt2020 -colorspace bt2020nc`.
   Unter ffmpeg 8.1.2 kommt davon nur noch `-colorspace` in der Datei an —
   ffprobe meldet für den erzeugten Clip `color_space=bt2020nc`, aber
   **kein `color_transfer` und kein `color_primaries`**. Der Clip ist
   schlicht nicht mehr HLG-getaggt; `detect_hdr` liefert für ihn zu Recht
   `''`.
2. **`detect_hdr` (`probe.py:76`) funktioniert.** Eine Datei, die die Tags
   tatsächlich trägt, meldet ffprobe 8.1.2 vollständig
   (`color_transfer=arib-std-b67`), und die Erkennung liefert `hlg`.
3. **Zwei Schreibwege erzeugen unter 8.1.2 korrekt getaggte Dateien:**
   `-vf setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc`
   (Frame-Metadaten, der Encoder übernimmt sie) und
   `-x264-params colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc`
   (VUI direkt). Beide verifiziert.
4. **Die Tonemapping-Kette läuft.** Die zscale-Kette aus
   `doctor.tonemap_chain` (`doctor.py:140`) verarbeitet eine getaggte Datei
   unter 8.1.2 fehlerfrei.

Der Querbeweis steht seit jeher im Repo selbst: `doctor` benutzt für seine
zscale/libplacebo-Probe **bereits `setparams`** (`doctor.py:494`) — deshalb
war der Doctor-Report nie betroffen, während die Fixture daneben still ihre
Tags verlor.

**Folgerung:** Echtes HLG-Material trägt seine Tags im Bitstream (Sony
schreibt die VUI selbst) und wird von `probe` → `preprocess` weiterhin
erkannt und getonemappt. Es gibt nach heutigem Kenntnisstand **keine
Produktionsregression** — aber der Satz stützt sich auf synthetische Dateien
und verlangt einen Beleg an echtem Material (A4).

## 3. Festlegung

1. **Die Fixture taggt über `setparams`** statt über die Ausgabeoptionen —
   derselbe Weg, den `doctor` benutzt: ein Idiom im Repo statt zwei, und
   encoderunabhängig (die `-x264-params`-Variante bände die Fixture an
   libx264). `setparams` existiert seit ffmpeg 4; die Mindestanforderung
   ffmpeg ≥ 6.0 aus der README braucht keine Versionsweiche.
2. **`make_clips` prüft nach dem Encode nach**, dass `color_transfer` in der
   Datei angekommen ist, und warnt sonst — wie die vorhandene Warnung bei
   fehlgeschlagenem Encode (`fixtures.py:170`). Begründung: genau diese
   Lücke hat die Fehldiagnose erzeugt. Die Tests wurden korrekt rot, aber
   sie zeigten auf `detect_hdr` statt auf die Fixture; eine Prüfung an der
   Quelle hätte „Fixture verliert Tags" gemeldet statt „HLG wird nicht
   erkannt". Fixtures, die zusichern, was sie erzeugen, sind die
   Vorbedingung dafür, dass ein roter Test etwas über das Werkzeug sagt.
3. `probe.py`, `preprocess.py`, `doctor.py` und die drei Tests selbst werden
   **nicht angefasst** — die Tests sind richtig und werden durch die
   reparierte Fixture grün.

## 4. Abnahmekriterien

- **A1** — `slideshow selftest --make-fixtures` und danach die drei Tests:
  grün unter ffmpeg 8.1.2.
- **A2** — Voller Suitenlauf **ohne Ausnahmen** grün: der Absatz „drei Tests
  schlagen dauerhaft fehl" verschwindet aus `CLAUDE.md`, nicht nur aus der
  Baustellen-Tabelle.
- **A3** — Die Nach-Encode-Prüfung aus Festlegung 2 meldet sich, wenn man
  sie sabotiert (Test mit gestubbtem ffprobe-Ergebnis genügt).
- **A4** — Beleg an echtem Material: ein realer HLG-Clip (die ILCE-6700 kann
  HLG als Picture Profile, alternativ ein Referenz-Sample) durch `probe`:
  das Manifest trägt `hdr: hlg` und die Warnung
  „HLG-Material — wird nach BT.709 SDR getonemappt" (`probe.py:685`);
  `preprocess` baut die zscale-Kette ins Intermediate. Sichtprüfung: kein
  flaues Bild.
- **A5** — Die Doku-Anpassungen aus Abschnitt 5 sind vollzogen.

## 5. Doku-Anpassungen (Teil der Umsetzung)

- `CLAUDE.md`, Abschnitt „Tests": den Absatz über die drei dauerhaft roten
  Tests **ersatzlos streichen** (samt dem Hinweis „Wer genau diese drei rot
  sieht…"), sobald A1/A2 stehen. Baustellen-Zeile „HLG unter ffmpeg 8.1.2"
  entfernen.
- `docs/briefing-kenburns-inhaltsabhaengig.md`, Abnahmekriterium A10: die
  dort notierte „vorbestehende Ausnahme" (drei HDR-Tests) streichen.
  Dasselbe in den Abnahmekriterien der Briefings
  [`briefing-blendenmodus.md`](briefing-blendenmodus.md) und
  [`briefing-titelfolien-hintergrund.md`](briefing-titelfolien-hintergrund.md),
  falls diese früher umgesetzt wurden.
- `docs/manuelle-checks.md`: prüfen, ob dort ein HDR-Sichtcheck auf die
  roten Tests verweist; gegebenenfalls den Verweis aktualisieren.

## 6. Risiken

- **Material ohne Tags bleibt unerkannt.** Ein HLG-Clip, dessen Hersteller
  die VUI nicht schreibt (bei No-Name-Android denkbar), sieht für
  `detect_hdr` aus wie SDR — heute wie vor ffmpeg 8. Das ist eine bekannte
  Grenze der Tag-basierten Erkennung, keine Regression, und bleibt außerhalb
  dieses Briefings.
- **Das ffmpeg-Verhalten kann sich erneut ändern.** Ob das Ignorieren der
  Ausgabeoptionen in ffmpeg 8 Absicht oder Fehler ist, ist offen; `setparams`
  ist der robustere Weg, aber die Nach-Encode-Prüfung (Festlegung 2) ist die
  eigentliche Versicherung — sie macht die nächste Änderung dieser Art in
  Minuten statt in einer Fehldiagnose sichtbar.

## 7. Nicht in diesem Umfang

Eine inhaltliche Prüfung des Tonemapping-*Ergebnisses* (Farbmetrik statt
Filtergraph), PQ/HDR10+-Fixtures über den HLG-Fall hinaus, und jede Änderung
an der Erkennungslogik selbst.

---

## 8. Was tatsächlich geändert wurde (08.08.2026)

| Ort | Änderung |
|---|---|
| `fixtures.py` `make_clips` | HLG-Clip taggt über `-vf setparams=…` statt über die Ausgabeoptionen (Festlegung 1) |
| `fixtures.py` `hat_transfer` | Nach-Encode-Prüfung mit Warnung (Festlegung 2). Liest mit `probe.color_transfer` — dem Leser des Produktionscodes; eine Fixture, die sich mit anderen Augen prüft als das Werkzeug, prüft sich selbst |
| `tests/test_media.py` | zwei neue Tests für die Zusicherung (A3: einer am echten Clip, einer mit gestubbtem ffprobe). `test_tonemapping_steht_vor_dem_scale` prüft die Reihenfolge statt `zscale` — siehe Nachtrag oben |
| Doku | `CLAUDE.md` (Absatz „drei Tests" gestrichen, Baustellenzeile auf *umgesetzt*), Abnahmekriterien in drei Briefings, Verweis auf A4 in `manuelle-checks.md` |

`probe.py`, `preprocess.py` und `doctor.py` sind unverändert — die Diagnose hat
gehalten: es gab dort nichts zu reparieren.

**Nicht erledigt: A4.** Auf dieser Maschine liegt kein echtes HLG-Material. Der
Satz „echtes HLG trägt seine Tags im Bitstream" stützt sich weiterhin auf
synthetische Dateien und die Herstellerpraxis, nicht auf eine Messung. Das ist
die einzige verbliebene Lücke des Befunds und steht als Check 3 in
`manuelle-checks.md`.
