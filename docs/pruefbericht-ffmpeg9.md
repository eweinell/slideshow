# ffmpeg-9-Prüfbericht

Antwort auf [`docs/pruefauftrag-ffmpeg9.md`](pruefauftrag-ffmpeg9.md).
Gemessen am 23.08.2026 auf einer Maschine mit echter ffmpeg-9-Binary.
Alle elf Punkte wurden durchgeführt, keiner musste ausgelassen werden.

## Umgebung

```
ffmpeg:   ffmpeg version 9.0.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
          built with gcc 16.1.0 (Rev2, Built by MSYS2 project), libavformat 63.1.101, libavcodec 63.1.101
ffprobe:  ffprobe version 9.0.1-full_build-www.gyan.dev Copyright (c) 2007-2026 the FFmpeg developers
OS:       Windows 11 Pro 10.0.26200 (Messungen über MSYS2/Git-Bash und PowerShell 5.1)
GPU:      NVIDIA GeForce RTX 4080 SUPER, 16376 MB VRAM, Treiber 610.88
          (zusätzlich AMD Radeon(TM) Graphics, integriert)
zscale:   ja (--enable-libzimg)     libplacebo: ja (--enable-libplacebo, --enable-vulkan)
exiftool: ja (13.59)                librosa:    ja (0.11.0, im .venv)
numpy:    ja (2.4.6, im .venv — das System-`python3` hat kein numpy)
```

Weitere für die Prüfung erhebliche Buildflags: `--enable-nvenc --enable-nvdec
--enable-ffnvcodec --enable-cuvid --enable-libx264 --enable-libx265
--enable-libsvtav1 --enable-librav1e --enable-libvpl --enable-amf`.

> **Zur Einordnung:** Der Auftrag erwartete unter P10 „keine NVIDIA-GPU, nicht
> durchführbar". Auf dieser Maschine steckt eine RTX 4080 SUPER, `nvidia-smi`
> und alle NVENC-Praxistests laufen — P10 war also durchführbar. Ebenso ist ein
> Vulkan-Gerät vorhanden, weshalb P9 anders ausfällt als die Referenz. Beides
> ist ein Merkmal *dieser* Messung, keine Eigenschaft des Projekts: wer die
> Prüfung wiederholt, liest den eigenen Stand aus `slideshow doctor` ab, statt
> ihn von hier zu übernehmen.

## Ergebnis in einem Satz

**Zehn von elf Prüfungen unauffällig; ffmpeg 9.0.1 ist für dieses Programm
freigabefähig** — der einzige Befund (P2, fehlende `color_trc`- und
`color_primaries`-Tags) bricht keinen Renderlauf ab, sondern liefert einen
unvollständig getaggten Master, und er ist mit einer Zeile Filtergraph zu
beheben.

Die sechs Leitfragen des Auftrags:

1. **Liefert die `zoompan`-Sonde noch eine richtige Antwort?** Ja, **Fall C**.
   Die Logformulierung ist in 9.0.1 unverändert vorhanden, die Sonde trifft, und
   ihr Ergebnis deckt sich mit der unabhängigen Wirkungsmessung P1b. Die
   Quelltextanalyse von `release/9.0` hat sich hier geirrt.
2. **Trägt der Master seine BT.709-Tags und sein `hvc1`?** `hvc1` ja,
   `colorspace=bt709` und `color_range=tv` ja — **`color_transfer` und
   `color_primaries` nein**. Das ist Befund B1.
3. **Läuft `slideshow audio`?** Ja. Muster trifft, beide Pässe laufen, im Labor
   und im echten Kommando.
4. **Läuft `slideshow beats`?** Ja. `silence_start:`/`silence_end:` unverändert,
   die Beat-Erkennung findet auf dem Fixture-Track 120,00 und 90,00 bpm bei
   Konfidenz 0,93.
5. **Stimmt die Framezahl eines Standbildsegments exakt?** Ja, `60/1,60` —
   identisch zur Referenz.
6. **Gibt es einen Fehlschlag, der einen Renderlauf abbricht?** **Nein.**
   616 Tests grün, und die vollständige Kette `probe → audio → preprocess →
   beats → build → render --preview` endet in jeder Stufe mit Exitcode 0.

---

## Befunde

### B1 — `-color_trc` und `-color_primaries` als Ausgabeoption wirkungslos  [schädlich]

**Prüfung:** P2
**Erwartet (6.1.1):** `tv,bt709,bt709,bt709` in allen drei Stufen
**Gemessen (9.0.1):** `tv,bt709,unknown,unknown` in allen drei Stufen

Rohausgabe der Hauptkette:

```
seg1.mkv   [0][0][0][0],tv,bt709,unknown,unknown
cat.mkv    [0][0][0][0],tv,bt709,unknown,unknown
m.mp4      hvc1,tv,bt709,unknown,unknown
```

Zusatz, DNxHR-Intermediate in MOV (`color_space,color_transfer,color_primaries`):

```
bt709,unknown,unknown
```

**Wo es entsteht.** Nicht beim `concat` und nicht beim Mux — schon im ersten
Encode. `cat.mkv` und `m.mp4` reichen nur weiter, was `seg1.mkv` bereits nicht
hat. Der verlustfreie Concat mit `-c copy` und der Mux mit `-c:v copy` arbeiten
korrekt: sie erhalten `colorspace`, `color_range` und das `hvc1`-Tag
vollständig.

**Es ist weder encoder- noch containerspezifisch.** Gegenproben ohne
`setparams`:

```
libx264 / mp4 :  tv,bt709,unknown,unknown
ffv1    / mkv :  tv,bt709,unknown,unknown
libx265 / mkv :  tv,bt709,unknown,unknown
dnxhd   / mov :     bt709,unknown,unknown
```

`-colorspace` und `-color_range` kommen in jeder Kombination durch,
`-color_trc` und `-color_primaries` in keiner.

**Der `setparams`-Weg rettet es vollständig**, in jeder Stufe. Mit
`-vf "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"`
vor dem Encoder:

```
sp1.mkv    [0][0][0][0],tv,bt709,bt709,bt709
spcat.mkv  [0][0][0][0],tv,bt709,bt709,bt709
spm.mp4    hvc1,tv,bt709,bt709,bt709
```

und für das Intermediate:

```
bt709,bt709,bt709
```

**Am echten Produkt bestätigt.** Das ist kein Laborartefakt. Der Gesamtlauf aus
P11 liefert für `out/preview.mp4` und für das erste Cache-Segment dasselbe Bild:

```
preview.mp4                              avc1,tv,bt709,unknown,unknown
cache/segments/seg_0698017a….mkv         h264,tv,bt709,unknown,unknown
```

**Wirkung auf das Programm.** `encoders.COLOR_ARGS` setzt die Tags als
Ausgabeoptionen; zwei der vier landen nicht in der Datei. Der Master ist damit
unvollständig getaggt, und weil `verify_master` nicht auf Farb-Tags schaut,
läuft er stillschweigend durch. Praktisch fällt es meist nicht auf — Player und
Browser nehmen für HD-Material ohne Transfer-Tag bt709 an —, aber die Zusicherung
ist weg: bei Weiterverarbeitung in einem farbmanagenden Werkzeug oder bei
Auslieferung an eine Plattform, die auf die Tags schaut, entscheidet dann die
Vermutung des Empfängers.

**Attribution offen.** Der Auftrag selbst hält fest, dass das Projekt dieselbe
Beobachtung am 07.08.2026 bereits unter **ffmpeg 8.1.2** gemacht hat (in einem
anderen Zusammenhang — `make_clips` taggt deshalb schon über `setparams`, siehe
[`docs/briefing-hlg-ffmpeg8.md`](briefing-hlg-ffmpeg8.md)). Es ist daher
**wahrscheinlich keine Neuerung von 9**, sondern mit 7 oder 8 gekommen. Auf
dieser Maschine ließ sich das nicht gegenprüfen, weil hier keine ältere Binary
liegt. Für die Freigabeentscheidung ist es gleichgültig, für einen späteren
Patch nicht: es heißt, dass der Master **auch heute schon**, unter 8.1.2, ohne
diese beiden Tags ausgeliefert wird.

### Kein Befund, aber berichtenswert: die Quelltextanalyse zu P1 war falsch

Die Analyse von `release/9.0` sagte, die Formulierung
`auto-inserting filter '…' between the filter '…' and the filter '…'` existiere
in `libavfilter/avfiltergraph.c` nicht mehr. **Sie existiert unverändert.** Die
befürchtete falsch-positive Antwort der Sonde (Fall B) tritt nicht ein. Wer die
Analyse als Grundlage für weitere Schlüsse benutzt hat, sollte diese Schlüsse
nachprüfen.

---

## Prüfungstabelle

| Prüfung | Ergebnis | Referenz 6.1.1 | Gemessen 9.0.1 | Bewertung |
|---|---|---|---|---|
| P1a | Logzeile vorhanden | 2 Treffer, 1 Zeile mit `Parsed_zoompan` | 2 Treffer, 1 Zeile | **Fall C** — kein Handlungsbedarf |
| P1b | `zoompan` weiter 8 Bit | 1024 / 256 | 1024 / 256 | identisch |
| P2 | Tags unvollständig | 3× `tv,bt709,bt709,bt709`, `hvc1` | 3× `tv,bt709,unknown,unknown`, `hvc1` | **Befund B1** |
| P3 | Parser trifft | 558 / 231, keine `FEHLT`, `('6','1','1')` | 581 / 249, keine `FEHLT`, `('9','0','1')` | unauffällig (Zahlen buildabhängig) |
| P4 | Muster trifft, beide Pässe | — | `Muster trifft: True`, kein Schlüssel fehlt, 2. Pass OK | unauffällig |
| P5 | Format unverändert | — | `silence_start:` / `silence_end:` + Zahl | unauffällig |
| P6 | alle Modi vorhanden | — | 15× `OK`, 0× `FEHLT` | unauffällig |
| P7 | Bereich unverändert | akzeptiert 0.5–100.0, abgelehnt 0.49 / 101 | identisch | unauffällig |
| P8 | Framezahl exakt | `60/1,60` | `60/1,60` | identisch |
| P9 | beide Ketten laufen | `zscale OK`, `libplacebo FEHLER` (kein Vulkan) | `zscale OK`, **`libplacebo OK`** | besser als Referenz, siehe unten |
| P10 | alle drei NVENC-Formen | nicht durchführbar | 3× `OK` | unauffällig |
| P11 | Suite grün, Kette läuft | grün auf 8.1.2 | **616 passed**, alle Stufen Exitcode 0 | unauffällig |

## Nicht durchführbar

Keine. Insbesondere P10 war entgegen der Erwartung des Auftrags durchführbar,
weil auf dieser Maschine eine NVIDIA-GPU steckt.

---

## Rohausgaben

### P1a — die `zoompan`-Bittiefensonde

```
--- Treffer 'auto-inserting': 2
--- Zeilen mit 'Parsed_zoompan':
18:[Parsed_zoompan_1 @ 000002499bc318c0] auto-inserting filter 'auto_scale_1' between the filter 'Parsed_format_0' and the filter 'Parsed_zoompan_1'
--- Zeilen mit 'auto_scale':
15:[auto_scale_0 @ 000002499dc21c80] w:iw h:ih flags:'' interl:0
16:[Parsed_format_0 @ 000002499bc1b340] auto-inserting filter 'auto_scale_0' between the filter 'graph -1 input from stream 0:0' and the filter 'Parsed_format_0'
17:[auto_scale_1 @ 000002499dc20e80] w:iw h:ih flags:'' interl:0
18:[Parsed_zoompan_1 @ 000002499bc318c0] auto-inserting filter 'auto_scale_1' between the filter 'Parsed_format_0' and the filter 'Parsed_zoompan_1'
19:[auto_scale_0 @ 000002499dc21c80] w:64 h:64 fmt:yuv420p csp:unknown range:unknown sar:1/1 -> w:64 h:64 fmt:yuv420p10le csp:unknown range:unknown sar:1/1 flags:0x00000004
22:[auto_scale_1 @ 000002499dc20e80] w:64 h:64 fmt:yuv420p10le csp:unknown range:unknown sar:1/1 -> w:64 h:64 fmt:yuv420p csp:unknown range:unknown sar:1/1 flags:0x00000004
```

(Auslassung: Zeilen 20/21 und 23/24 sind `framesync`-Meldungen ohne Bezug zur
Prüfung; die vollständige Logdatei ist ansonsten wiedergegeben.)

Die entscheidende Zeile 18 passt Zeichen für Zeichen auf das Muster aus
`doctor.py`. Zeile 22 zeigt zusätzlich die Wirkung: `yuv420p10le → yuv420p`
direkt vor `zoompan`.

### P1b — Wirkungsmessung

```
ref.yuv  unterscheidbare Y-Werte: 1024
zp.yuv   unterscheidbare Y-Werte: 256
```

### P3 — geparste Listen

```
Filter erkannt : 581
Encoder erkannt: 249
geparste Version: ('9', '0', '1')
```

Keine `FEHLT`- und keine `ENCODER FEHLT`-Zeile. Die Flagspalte ist gegenüber 8.x
unverändert zweistellig bei den Filtern (` TS aap`, ` .. abench`) und
sechsstellig bei den Encodern (` V....D a64multi`); beide Muster greifen.

### P4 — `loudnorm`, erster Pass

```json
{
	"input_i" : "-21.75",
	"input_tp" : "-18.06",
	"input_lra" : "0.00",
	"input_thresh" : "-31.75",
	"output_i" : "-16.03",
	"output_tp" : "-12.27",
	"output_lra" : "0.00",
	"output_thresh" : "-26.03",
	"normalization_type" : "dynamic",
	"target_offset" : "0.03"
}
```

`Muster trifft: True`, keine fehlende Schlüsselmeldung. Der Block enthält keine
verschachtelten Klammern, das Muster `\{[^{}]*"input_i"[^{}]*\}` ist also
weiterhin ausreichend. Zweiter Pass: `Zweiter Pass OK`.

Gegenprobe im echten Codepfad — `slideshow audio` auf dem Fixture-Track:

```
INFO     click_two_songs.wav: -22.5 LUFS / -2.4 dBTP -> -16.0 LUFS
Mix: …\proj\cache\mix.flac (36.41 s, 1 Tracks)
```

### P5 — `silencedetect`

```
[Parsed_silencedetect_0 @ 000001e8b4412dc0] silence_start: 2
[Parsed_silencedetect_0 @ 000001e8b4412dc0] silence_end: 4.000021 | silence_duration: 2.000021
```

### P6 — `xfade`-Modi

```
OK    fade          OK    slideleft
OK    fadeblack     OK    slideright
OK    fadewhite     OK    smoothleft
OK    wipeleft      OK    smoothright
OK    wiperight     OK    circleopen
OK    wipeup        OK    circleclose
OK    wipedown      OK    pixelize
              OK    hblur
```

Zur im Auftrag genannten Zahl: `_XFADE_MODES` in `kenburns.py` hat sechzehn
Schlüssel, aber `dissolve` bildet auf `fade` ab. Es sind fünfzehn verschiedene
ffmpeg-Übergänge, und die Prüfliste deckt sie vollständig ab — keine Lücke.

### P7 — `atempo`

```
abgelehnt   atempo=0.49
akzeptiert  atempo=0.5
akzeptiert  atempo=0.8333333
akzeptiert  atempo=2.0
akzeptiert  atempo=2.5
akzeptiert  atempo=4.0
akzeptiert  atempo=100
abgelehnt   atempo=101
```

Bereich weiterhin **[0.5, 100.0]**, unverändert gegenüber 6.1.1. Der bereits
bekannte Befund — Kettung nach oben überflüssig, nach unten nötig — ist
inzwischen im Repo korrigiert (Commit `6cfc1f9`).

### P8 — Standbild

```
60/1,60
```

### P9 — Tonemapping

```
zscale OK
libplacebo OK
```

`libplacebo` läuft hier, weil ein Vulkan-Gerät vorhanden ist — die Referenz
6.1.1 scheiterte nur am fehlenden Gerät, nicht am Filter. Beide Ketten sind
damit erstmals gemeinsam belegt; die grobe `eq`-Näherung kommt nicht zum Zug.
`doctor` meldet unabhängig davon `nutzbar: libplacebo, zscale (Praxistest
bestanden)`.

### P10 — NVENC

```
hevc_nvenc constqp OK
hevc_nvenc vbr/multipass OK
av1_nvenc OK
```

Alle drei modernen Formen laufen: `-preset p7 -tune hq -rc constqp`,
`-rc vbr -cq -multipass fullres -temporal-aq` und `av1_nvenc`. Keine der in 9
entfernten Legacy-Optionen wird berührt. Bestätigt die Aussage der Analyse.

---

## P11 — Gesamtlauf

### Testsuite

Gemessen auf dem Branch `claude/video-clips-original-audio-rxp0a0`, Commit
`6cfc1f9` (der Auftrag nennt `main`; dieser Branch ist ein Superset und enthält
zusätzlich den Originalton-Nachtrag). `exiftool` und `librosa` waren beide
installiert — die vier bzw. fünf im Auftrag genannten Tests konnten also real
laufen und sind nicht ausgefallen.

```
........................................................................ [ 11%]
........................................................................ [ 23%]
........................................................................ [ 35%]
........................................................................ [ 46%]
........................................................................ [ 58%]
........................................................................ [ 70%]
........................................................................ [ 81%]
........................................................................ [ 93%]
........................................                                 [100%]
616 passed in 250.00s (0:04:10)
```

**Kein einziger Fehlschlag.**

### Kette mit synthetischem Material

Jede Stufe mit Exitcode 0:

| Stufe | Exitcode | Bemerkung |
|---|---|---|
| `selftest --make-fixtures` | 0 | 14 Bilder, 8 Clips |
| `probe … --fps 60` | 0 | Manifest geschrieben |
| `audio click_two_songs.wav` | 0 | `mix.flac`, 36,41 s |
| `preprocess --intermediate hevc_intra_cpu` | 0 | 14 Bilder, 8 Clips, 1 Originalton |
| `beats` | 0 | siehe Regionenkarte |
| `build --force` | 0 | 35 Segmente, 13 Übergänge |
| `render --preview` | 0 | siehe Render-Report |
| `doctor` | 0 | 1 Warnung (die erwartete) |

Regionenkarte aus `beats` — librosa findet auf dem Zweisong-Fixture beide Tempi
sauber:

```
┌───┬──────┬────────┬────────┬────────┬────────┬────────┬───────────┐
│ # │ Typ  │  Start │   Ende │  Dauer │    BPM │ Offset │ Konfidenz │
├───┼──────┼────────┼────────┼────────┼────────┼────────┼───────────┤
│ 0 │ beat │  0.000 │ 16.022 │ 16.022 │ 120.00 │  0.411 │      0.93 │
│ 1 │ free │ 16.022 │ 22.411 │  6.389 │      - │      - │         - │
│ 2 │ beat │ 22.411 │ 36.412 │ 14.001 │  90.00 │ 22.411 │      0.93 │
└───┴──────┴────────┴────────┴────────┴────────┴────────┴───────────┘
```

`build` meldet fünf gekürzte Blenden und vier Clip-Warnungen. Die sind
**materialbedingt**, nicht ffmpeg-bedingt: die Fixture-Clips sind kürzer als die
Handles, die eine 0,5-s-Blende braucht.

Render-Report:

```
┌─────────────────┬──────────┐
│ Kennzahl        │     Wert │
├─────────────────┼──────────┤
│ Segmente gesamt │       35 │
│ aus Cache       │        0 │
│ neu gerendert   │       35 │
│ Renderzeit      │     1:38 │
│ Ausgabegroesse  │   9.8 MB │
│ Laufzeit        │  88.42 s │
│ Musiklaenge     │  88.42 s │
└─────────────────┴──────────┘
```

`ffprobe` auf das Ergebnis:

```
codec_name=h264
width=1280
height=720
pix_fmt=yuv420p
codec_name=aac
duration=88.416667
```

Laufzeit und Musiklänge stimmen auf die Millisekunde — der Sync, um den es in
P8 geht, hält auch über die ganze Kette.

### `doctor`-Report

Vollständig, wie im Auftrag verlangt:

```
┌────────┬──────────────────────┬───────────────────────────────────────────────────────────────────────────┐
│ Status │ Pruefung             │ Detail                                                                    │
├────────┼──────────────────────┼───────────────────────────────────────────────────────────────────────────┤
│ OK     │ Umgebung             │ Windows (Render-Toolchain), slideshow 0.1.0                               │
│ OK     │ ffmpeg               │ ffmpeg version 9.0.1-full_build-www.gyan.dev … (getestete Basis; 6.x      │
│        │                      │ bringt xfade-/zoompan-Fixes)                                              │
│ OK     │ ffprobe              │ ffprobe version 9.0.1-full_build-www.gyan.dev …                           │
│ OK     │ python3              │ Python 3.14.6                                                             │
│ OK     │ exiftool             │ 13.59  (fuer EXIF/ICC-Auswertung)                                         │
│ OK     │ magick               │ ImageMagick 7.1.2-29 Q16-HDRI x64                                         │
│ OK     │ melt                 │ melt.exe 7.39.0  (nur fuer den MLT-Renderpfad)                            │
│ OK     │ Schrift              │ C:\Windows\Fonts\segoeui.ttf  (fuer Titelfolien)                          │
│ OK     │ nvidia-smi           │ NVIDIA-SMI version  : 610.88  (Treiber >= 550 fuer NVENC/NVDEC)            │
│ OK     │ Python-Pakete        │ numpy, pyyaml, pydantic, pillow, rich, soundfile                          │
│ OK     │ Beat-Analyse         │ librosa (mel-basierte Onset-Erkennung)                                    │
│ OK     │ ffmpeg-Filter        │ zoompan, xfade, scale, format                                             │
│ OK     │ HDR-Tonemapping      │ nutzbar: libplacebo, zscale (Praxistest bestanden)                        │
│ OK     │ NVENC-Encoder        │ hevc_nvenc, h264_nvenc, av1_nvenc                                         │
│ WARN   │ zoompan-Bittiefe     │ zoompan ist 8-Bit-only; ffmpeg konvertiert still herunter. Der            │
│        │                      │ 10-Bit-Gewinn entsteht nur im Encoder. Bei sichtbarem Banding:            │
│        │                      │ kb.engine=scale16 setzen (16-Bit-Pfad ohne zoompan)                       │
│ OK     │ GPU                  │ NVIDIA GeForce RTX 4080 SUPER, 16376 MB VRAM                              │
│ OK     │ NVENC 10-Bit-HEVC    │ Praxistest bestanden                                                      │
│ OK     │ NVENC-Ratensteuerung │ VBR mit Multipass und Temporal-AQ                                         │
│ OK     │ NVENC-Sessions       │ 4 parallel (begrenzt den Render-Pool)                                     │
│ OK     │ NVDEC 4:2:2 10 Bit   │ vorhanden                                                                 │
│ OK     │ AV1-NVENC            │ verfuegbar (optionales Ausgabeformat)                                     │
│ OK     │ CPU-Kerne            │ 24 (Worker-Pool: 4)                                                       │
│ OK     │ Speicherplatz        │ 1867.1 GB frei …                                                          │
└────────┴──────────────────────┴───────────────────────────────────────────────────────────────────────────┘

1 Warnung(en), aber lauffaehig.
```

Die Zeile „zoompan-Bittiefe" ist die aus P1. Sie steht auf `WARN` und sagt
„8-Bit-only" — **das ist die richtige Antwort**, und sie deckt sich mit der
unabhängigen Messung P1b (256 statt 1024 unterscheidbare Werte). Die Sonde
funktioniert unter ffmpeg 9 unverändert.

Nebenbei belegt der Report, dass die Versionsprüfung greift: sie zerlegt
`9.0.1` korrekt und stuft die Version als brauchbar ein. Der Klammerzusatz
„getestete Basis; 6.x bringt xfade-/zoompan-Fixes" ist inzwischen erkennbar
veraltet — keine Fehlfunktion, nur ein alter Text.

---

## Freigabeempfehlung

**ffmpeg 9.0.1 kann für dieses Programm freigegeben werden.** Punkt 5 und 6 der
Leitfragen — exakte Framezahl und abbrechende Fehlschläge — sind beide positiv
beantwortet: der Sync stimmt auf die Millisekunde, und weder die 616 Tests noch
die vollständige Renderkette zeigen einen Fehler.

Als Nacharbeit bleibt genau ein Punkt, B1. Er ist **nicht ffmpeg-9-spezifisch**
und trifft die 8.1.2-Entwicklungsmaschine mit hoher Wahrscheinlichkeit genauso.
Ein Patch wurde auftragsgemäß (Regel 2) nicht erstellt.
