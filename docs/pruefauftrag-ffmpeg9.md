# Prüfauftrag: läuft `slideshow` unter ffmpeg 9?

**An:** eine KI mit freiem Zugriff auf eine ffmpeg-9-Installation
**Von:** Analyse vom 23.08.2026, erstellt ohne ffmpeg-9-Binary
**Umfang:** 11 Messungen, ~30 Minuten ohne Repo, ~2 Stunden mit Repo

---

## Worum es geht

`slideshow` ist ein musiksynchroner 4K-Slideshow-Renderer (Python-CLI), der
ffmpeg für alles Schwere benutzt: Bildnormalisierung, Clip-Intermediates,
Ken-Burns-Fahrten über `zoompan`, Überblendungen über `xfade`, Tonmischung über
`loudnorm`/`amix`, und einen verlustfreien Concat mit `-c copy`.

Entwickelt wird gegen **ffmpeg 8.1.2**. Eine Quelltextanalyse von
`release/9.0` hat ergeben, dass alle 34 benutzten Filter weiter registriert
sind und die entfernten NVENC-Altoptionen nicht verwendet werden. **Offen sind
elf Punkte, die sich nur an einer laufenden Binary klären lassen.** Genau die
sind dieser Auftrag.

## Regeln

1. **Nicht raten.** Jede Aussage wird durch ein ausgeführtes Kommando und
   dessen Ausgabe belegt. „Sollte funktionieren" ist kein Ergebnis.
2. **Nichts reparieren.** Dieser Auftrag misst. Wer einen Defekt findet, meldet
   ihn — er ändert keinen Code und schlägt auch keinen Patch vor, solange nicht
   ausdrücklich danach gefragt wird.
3. **Rohausgaben beilegen**, mindestens für jeden Punkt, der von der Erwartung
   abweicht. Gekürzt wird nur mit ausgewiesener Auslassung.
4. **Fehlschläge wörtlich zitieren.** Eine ffmpeg-Fehlermeldung ist das
   eigentliche Ergebnis, nicht ihre Zusammenfassung.
5. **Nicht Durchführbares als solches melden.** Ohne NVIDIA-GPU ist P10 nicht
   durchführbar — das ist ein legitimes Ergebnis, eine Schätzung wäre keines.
6. Jede Messung, deren Ergebnis vom **Referenzwert aus ffmpeg 6.1.1** (unten
   jeweils angegeben, real gemessen) abweicht, ist ein Befund und gehört in die
   Zusammenfassung, auch wenn sie „harmlos" wirkt.

## Vorbereitung

```bash
mkdir -p /tmp/ff9 && cd /tmp/ff9
ffmpeg -version | head -3          # in den Bericht, vollständig
ffmpeg -hide_banner -buildconf | head -40   # in den Bericht, gekürzt erlaubt
python3 -c "import numpy; print(numpy.__version__)"   # für P1b und P2 nötig
```

Melde vorab: exakte ffmpeg- und ffprobe-Version, Betriebssystem, ob eine
NVIDIA-GPU verfügbar ist, ob `libzimg` (zscale) und `libplacebo` einkompiliert
sind.

---

## P1 — Die `zoompan`-Bittiefensonde (höchste Priorität)

Das Programm ermittelt in `doctor.py`, ob `zoompan` mit mehr als 8 Bit
rechnet. Davon hängt die Empfehlung ab, bei Banding auf den langsameren
`--kb-engine scale16` zu wechseln. Die Sonde liest dafür **Logtext**:

```python
r"auto-inserting filter '(auto_scale_\d+)' between the filter '[^']+' "
r"and the filter 'Parsed_zoompan_\d+'"
```

Die Quelltextanalyse von `release/9.0` sagt, dass diese Formulierung in
`libavfilter/avfiltergraph.c` **nicht mehr existiert**. Ist das so, misst die
Sonde nichts mehr — und liefert je nach Restausgabe eine falsch-negative
(harmlos) oder eine falsch-positive Antwort (schädlich: sie behauptet dann
10-Bit-Tauglichkeit, ohne sie geprüft zu haben).

### P1a — Existiert die Logzeile noch?

```bash
ffmpeg -hide_banner -v verbose -f lavfi -i testsrc2=s=64x64:r=25 -t 0.1 \
  -vf "format=yuv420p10le,zoompan=z=1.1:d=1:s=64x64:fps=25" \
  -f null - 2> zoompan.log

echo "--- Treffer 'auto-inserting': $(grep -c 'auto-inserting' zoompan.log)"
echo "--- Zeilen mit 'Parsed_zoompan':"
grep -n "Parsed_zoompan" zoompan.log
echo "--- Zeilen mit 'auto_scale':"
grep -n "auto_scale" zoompan.log
```

**Referenz ffmpeg 6.1.1 (gemessen):** `auto-inserting` kommt 2× vor; genau
**eine** Zeile enthält `Parsed_zoompan`, und zwar die auto-inserting-Zeile:

```
[Parsed_zoompan_1 @ 0x...] auto-inserting filter 'auto_scale_1' between the filter 'Parsed_format_0' and the filter 'Parsed_zoompan_1'
```

**Zu melden:** die drei Zählungen, die vollständige `zoompan.log` (sie ist
kurz), und die Einordnung nach dieser Tabelle:

| Fall | Bedingung in der Ausgabe | Verhalten der Sonde | Bewertung |
|---|---|---|---|
| A | keine Zeile enthält `Parsed_zoompan` | liefert `False` | falsch-negativ, harmlos |
| B | `Parsed_zoompan` kommt vor, die alte Formulierung nicht | liefert `True` | **falsch-positiv, schädlich** |
| C | alte Formulierung unverändert vorhanden | korrekt | kein Handlungsbedarf |

### P1b — Rechnet `zoompan` tatsächlich in mehr als 8 Bit?

Unabhängig vom Logtext, durch Messung der Wirkung. Eine 10-Bit-Rampe mit 1024
unterscheidbaren Luma-Werten läuft durch `zoompan`; wird intern auf 8 Bit
gekürzt, bleiben höchstens 256 übrig.

```bash
python3 - <<'PY'
import numpy as np
w, h = 1024, 64
y = np.tile(np.linspace(0, 1023, w).astype('<u2'), (h, 1))
uv = np.full((h // 2, w // 2), 512, dtype='<u2')
open('ramp10.yuv', 'wb').write(y.tobytes() + uv.tobytes() + uv.tobytes())
PY

IN="-f rawvideo -pix_fmt yuv420p10le -s 1024x64 -framerate 25 -i ramp10.yuv"
ffmpeg -hide_banner -v error -y $IN -vf "null" \
  -frames:v 1 -f rawvideo -pix_fmt yuv420p10le ref.yuv
ffmpeg -hide_banner -v error -y $IN -vf "zoompan=z=1:d=1:s=1024x64:fps=25" \
  -frames:v 1 -f rawvideo -pix_fmt yuv420p10le zp.yuv

python3 - <<'PY'
import numpy as np
for n in ('ref.yuv', 'zp.yuv'):
    a = np.fromfile(n, dtype='<u2')[:1024 * 64]
    print('%-8s unterscheidbare Y-Werte: %d' % (n, len(np.unique(a))))
PY
```

**Referenz ffmpeg 6.1.1 (gemessen):** `ref.yuv` 1024, `zp.yuv` **256** —
`zoompan` kürzt dort also auf 8 Bit.

**Zu melden:** beide Zahlen. Wenn `zp.yuv` deutlich über 256 liegt, hat sich
das Verhalten von `zoompan` in 9 geändert — das wäre eine gute Nachricht mit
Folgen für die Dokumentation des Projekts, und ebenso berichtenswert wie das
Gegenteil.

---

## P2 — Farb-Tags durch die ganze Produktionskette

Der Master wird aus einzeln encodierten Segmenten per `concat` mit `-c copy`
zusammengesetzt und danach mit `-c:v copy` gemuxt. Die BT.709-Tags werden dabei
als **Ausgabeoptionen** gesetzt (`encoders.COLOR_ARGS`). Das Projekt hat am
07.08.2026 festgestellt, dass ffmpeg 8.1.2 diese Optionen in einem anderen
Zusammenhang *nicht* mehr in die Datei übernimmt. Ob das für diese Kette gilt,
ist offen — und die Endprüfung `verify_master` schaut nicht auf Farb-Tags, ein
ungetaggter Master liefe also stillschweigend durch.

```bash
for i in 1 2; do
  ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=128x72:r=25 -t 0.4 \
    -c:v libx265 -preset ultrafast -crf 30 -x265-params log-level=error \
    -pix_fmt yuv420p10le \
    -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv \
    -fps_mode cfr -r 25 seg$i.mkv
done
printf 'ffconcat version 1.0\nfile seg1.mkv\nfile seg2.mkv\n' > segs.txt
ffmpeg -hide_banner -v error -y -f concat -safe 0 -i segs.txt \
  -c copy -fflags +genpts cat.mkv
ffmpeg -hide_banner -v error -y -i cat.mkv -c:v copy -tag:v hvc1 \
  -movflags +faststart m.mp4

for f in seg1.mkv cat.mkv m.mp4; do
  printf "%-10s " $f
  ffprobe -v error -select_streams v:0 -show_entries \
    stream=codec_tag_string,color_range,color_space,color_transfer,color_primaries \
    -of csv=p=0 $f
done
```

**Referenz ffmpeg 6.1.1 (gemessen):**

```
seg1.mkv   [0][0][0][0],tv,bt709,bt709,bt709
cat.mkv    [0][0][0][0],tv,bt709,bt709,bt709
m.mp4      hvc1,tv,bt709,bt709,bt709
```

**Zu melden:** die drei Zeilen. Entscheidend ist, ob in **jeder** Stufe
`bt709` dreimal und `tv` erscheinen und ob `m.mp4` das Tag `hvc1` trägt. Fehlt
etwas, bitte zusätzlich prüfen, ob `-vf setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709`
vor dem Encoder die Tags rettet — das ist der Weg, den das Projekt an anderer
Stelle bereits benutzt.

Zusatz, gleiche Frage für das Clip-Intermediate (DNxHR in MOV):

```bash
ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=1280x720:r=25 -t 0.4 \
  -c:v dnxhd -profile:v dnxhr_hqx -pix_fmt yuv422p10le \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
  -metadata:s:v rotate=0 inter.mov
ffprobe -v error -select_streams v:0 -show_entries \
  stream=color_space,color_transfer,color_primaries -of csv=p=0 inter.mov
```

---

## P3 — Die geparsten Listen und die Versionszeile

`doctor.py` liest die Fähigkeiten aus `-filters` und `-encoders` und zerlegt
die Zeilen mit festen Mustern. Die Flagspalte hat sich zwischen 7.x (drei
Zeichen) und 8.x (zwei) schon einmal geändert; die Muster sind seither
tolerant, aber das ist zu bestätigen.

```bash
ffmpeg -hide_banner -filters  | sed -n '1,12p'
ffmpeg -hide_banner -encoders | sed -n '1,12p'

ffmpeg -hide_banner -filters  > f.txt
ffmpeg -hide_banner -encoders > e.txt
python3 - <<'PY'
import re
f = open('f.txt').read(); e = open('e.txt').read()
nf = re.findall(r"^\s*[A-Z.]{2,3}\s+(\S+)", f, re.M)
ne = re.findall(r"^\s*[A-Z.]{6}\s+(\S+)", e, re.M)
print("Filter erkannt :", len(nf))
print("Encoder erkannt:", len(ne))
for name in ("zoompan","xfade","scale","format","zscale","libplacebo",
             "loudnorm","atempo","amix","adelay","atrim","apad","afade",
             "aresample","aformat","setparams","silencedetect","volume",
             "asetpts","acrossfade","tonemap","gblur","overlay","split",
             "pad","crop","setsar","setpts","eq","fade"):
    print("  FEHLT:", name) if name not in nf else None
for name in ("libx264","libx265","dnxhd","ffv1","aac","flac"):
    print("  ENCODER FEHLT:", name) if name not in ne else None
PY

ffmpeg -version | head -1 | python3 -c "
import re, sys
m = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', sys.stdin.readline())
print('geparste Version:', m.groups() if m else 'KEIN TREFFER')"
```

**Referenz ffmpeg 6.1.1 (gemessen):** 558 Filter, 231 Encoder erkannt, keine
`FEHLT`-Zeile, geparste Version `('6', '1', '1')`. Die beiden Zahlen sind
buildabhängig und deshalb kein Kriterium — entscheidend ist, dass sie deutlich
über null liegen (bei null hat der Parser die Spalte verfehlt) und dass keine
`FEHLT`-Zeile erscheint.

**Zu melden:** die beiden Zählungen, jede `FEHLT`-Zeile (jede einzelne ist ein
Befund), und die geparste Version. Erwartet wird ein Tupel, dessen erste Zahl
`9` ist. Ergibt der Parser `KEIN TREFFER`, ist die Versionsprüfung des
Programms wirkungslos — bitte dann die vollständige erste Zeile zitieren.

---

## P4 — `loudnorm`, Zweipass über stderr

Das Kommando `slideshow audio` liest die Messwerte des ersten Passes als JSON
von stderr, mit dem Muster `\{[^{}]*"input_i"[^{}]*\}`. Das Muster verträgt
**keine verschachtelten Klammern** im JSON-Block.

```bash
ffmpeg -hide_banner -v error -y -f lavfi \
  -i "sine=frequency=440:sample_rate=48000:duration=5" -c:a flac ton.flac

ffmpeg -hide_banner -nostats -i ton.flac \
  -af "loudnorm=I=-16.0:TP=-1.5:LRA=11.0:print_format=json" \
  -f null - 2> loud.log
sed -n '/{/,/}/p' loud.log

python3 - <<'PY'
import re
err = open('loud.log').read()
m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', err, re.S)
print("Muster trifft:", bool(m))
for k in ("input_i","input_tp","input_lra","input_thresh","target_offset"):
    print("  fehlt:", k) if (not m or k not in m.group(0)) else None
PY
```

Danach der zweite Pass mit den gemessenen Werten (Platzhalter einsetzen) —
er muss ohne Fehler durchlaufen:

```bash
ffmpeg -hide_banner -v error -y -i ton.flac -af \
 "loudnorm=I=-16.0:TP=-1.5:LRA=11.0:measured_I=<I>:measured_TP=<TP>:measured_LRA=<LRA>:measured_thresh=<TH>:offset=<OFF>:linear=true:print_format=summary" \
 -ar 48000 -ac 2 -c:a flac norm.flac && echo "Zweiter Pass OK"
```

**Zu melden:** ob das Muster trifft, jede fehlende Schlüsselmeldung, der
JSON-Block im Original, und ob der zweite Pass durchläuft. Schlägt das Muster
fehl, bricht `slideshow audio` mit einer klaren Meldung ab — das Kommando wäre
dann unbenutzbar, also ein blockierender Befund.

---

## P5 — `silencedetect`

`slideshow beats` liest `silence_start:` und `silence_end:` aus stderr.

```bash
ffmpeg -hide_banner -v error -y -f lavfi \
  -i "sine=frequency=440:sample_rate=48000:duration=2" -c:a flac a1.flac
ffmpeg -hide_banner -v error -y -f lavfi \
  -i "anullsrc=r=48000:cl=mono:duration=2" -c:a flac a2.flac
printf "file a1.flac\nfile a2.flac\nfile a1.flac\n" > al.txt
ffmpeg -hide_banner -v error -y -f concat -safe 0 -i al.txt -c:a flac mix.flac

ffmpeg -hide_banner -nostats -i mix.flac \
  -af "silencedetect=noise=-45.0dB:d=0.4" -f null - 2>&1 | grep -i silence
```

**Erwartet:** mindestens eine Zeile mit `silence_start:` und eine mit
`silence_end:`, jeweils gefolgt von einer Zahl. **Zu melden:** die Zeilen
wörtlich.

---

## P6 — Alle benutzten `xfade`-Modi

Das Programm lässt sechzehn Übergangsarten zu. Jede muss der Filter kennen.

```bash
for m in fade fadeblack fadewhite wipeleft wiperight wipeup wipedown \
         slideleft slideright smoothleft smoothright circleopen circleclose \
         pixelize hblur; do
  if ffmpeg -hide_banner -v error -y \
      -f lavfi -i testsrc2=s=64x64:r=25 -t 1 \
      -f lavfi -i testsrc2=s=64x64:r=25 -t 1 \
      -filter_complex "[0:v][1:v]xfade=transition=$m:duration=0.5:offset=0,format=yuv420p[v]" \
      -map "[v]" -frames:v 5 -f null - 2>/dev/null; then
    echo "OK    $m"
  else
    echo "FEHLT $m"
  fi
done
```

**Zu melden:** jede `FEHLT`-Zeile mitsamt der Fehlermeldung des betreffenden
Laufs (ohne `2>/dev/null` wiederholen).

---

## P7 — Zulässiger Bereich von `atempo`

Der Originalton eines Clips wird per `atempo` mitgestreckt; das Programm
kettet Faktoren außerhalb von [0.5, 2.0], weil ein einzelnes `atempo` sie
historisch ablehnt. Ändert 9 den Bereich, ist die Kettung überflüssig
(harmlos) — es ist trotzdem gut, es zu wissen.

```bash
for f in 0.49 0.5 0.8333333 2.0 2.5 4.0 100 101; do
  if ffmpeg -hide_banner -v error -y -f lavfi \
      -i "sine=frequency=440:sample_rate=48000:duration=0.2" \
      -af "atempo=$f" -f null - 2>/dev/null; then
    echo "akzeptiert  atempo=$f"
  else
    echo "abgelehnt   atempo=$f"
  fi
done
```

**Referenz ffmpeg 6.1.1 (gemessen):** akzeptiert werden 0.5 bis 100.0;
abgelehnt werden 0.49 und 101. Der tatsächliche Bereich ist also **[0.5,
100.0]**, nicht [0.5, 2.0] — der Kommentar im Programm behauptet an dieser
Stelle etwas Falsches, und die Kettung nach oben ist überflüssig (nach unten
ist sie nötig). Das ist bereits ohne ffmpeg 9 ein Befund; hier interessiert
nur, ob 9 die Grenzen erneut verschiebt.

**Zu melden:** die acht Zeilen.

---

## P8 — Standbild mit exakter Framezahl

Jedes Ken-Burns-Segment entsteht aus einem Standbild mit `-loop 1` und wird
über `-frames:v` auf eine **exakte** Framezahl gezwungen. Weicht das ab,
verschiebt sich der Sync — der Kern des Programms.

```bash
ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=320x180:r=1 \
  -frames:v 1 bild.png
ffmpeg -hide_banner -v error -y -loop 1 -framerate 60 -t 1.000000 -i bild.png \
  -vf "zoompan=z='1+0.1*on/59':d=1:s=320x180:fps=60,format=yuv420p" \
  -frames:v 60 -c:v libx264 -preset veryfast -crf 20 \
  -fps_mode cfr -r 60 still.mp4
ffprobe -v error -select_streams v:0 -count_frames \
  -show_entries stream=nb_read_frames,r_frame_rate -of csv=p=0 still.mp4
```

**Referenz ffmpeg 6.1.1 (gemessen):** genau `60/1,60` — ffprobe gibt die Felder in der Reihenfolge des Streams aus, nicht in der der Abfrage: erst `r_frame_rate`, dann `nb_read_frames`. **Zu melden:** die Zeile. Jede Abweichung ist
ein blockierender Befund.

---

## P9 — Tonemapping-Ketten für HDR-Clips

Das Programm bevorzugt `libplacebo` und fällt auf `zscale` zurück. Beide
Ketten stehen hier wörtlich so, wie das Programm sie baut.

```bash
HDR="setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc"

# Kein `| tail` und kein Pipe hinter dem ffmpeg-Aufruf: der Exitcode einer
# Pipeline ist der des *letzten* Glieds, und ein gescheitertes ffmpeg meldete
# so ein "OK". Die Logdatei wird nur im Fehlerfall ausgegeben.
if ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=128x72:r=25 -t 0.2 \
  -vf "$HDR,zscale=tin=arib-std-b67:min=bt2020nc:pin=bt2020:t=linear:npl=1000,tonemap=hable:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p" \
  -f null - 2> zs.log
then echo "zscale OK"; else echo "zscale FEHLER"; cat zs.log; fi

if ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=128x72:r=25 -t 0.2 \
  -vf "$HDR,libplacebo=tonemapping=bt.2390:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p10le" \
  -f null - 2> lp.log
then echo "libplacebo OK"; else echo "libplacebo FEHLER"; cat lp.log; fi
```

**Referenz ffmpeg 6.1.1 (gemessen):** `zscale OK`; `libplacebo FEHLER` mit
`Error while filtering: Generic error in an external library` — auf der
Messmaschine fehlt ein Vulkan-Gerät.

**Zu melden:** beide Ergebnisse. Ein `libplacebo`-Fehler ist nur dann ein
Befund, wenn ein Vulkan-Gerät vorhanden ist — sonst bitte vermerken, dass
keines da war. Scheitern **beide**, fällt das Programm auf eine grobe
`eq`-Näherung zurück; das ist dokumentiert, aber berichtenswert.

---

## P10 — NVENC (nur mit NVIDIA-GPU)

ffmpeg 9 hat die alten NVENC-Presetnamen und die Legacy-Ratensteuerungen
entfernt. Die Analyse sagt, dass das Programm ausschließlich die moderne Form
benutzt. Zu bestätigen:

```bash
ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=256x144:r=30 -t 1 \
  -c:v hevc_nvenc -preset p7 -tune hq -rc constqp -qp 16 \
  -pix_fmt p010le -f null - && echo "hevc_nvenc constqp OK" || echo "FEHLER"

ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=256x144:r=30 -t 1 \
  -c:v hevc_nvenc -rc vbr -cq 24 -b:v 0 -multipass fullres -temporal-aq 1 \
  -pix_fmt p010le -f null - && echo "hevc_nvenc vbr/multipass OK" || echo "FEHLER"

ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=256x144:r=30 -t 1 \
  -c:v av1_nvenc -preset p7 -rc constqp -qp 24 \
  -pix_fmt p010le -f null - && echo "av1_nvenc OK" || echo "FEHLER"
```

**Zu melden:** die drei Ergebnisse, oder „keine NVIDIA-GPU, nicht durchführbar".

---

## P11 — Gesamtlauf (nur mit dem Repo, sehr aussagekräftig)

Wenn das Repository verfügbar ist, ist das der stärkste Einzelbeleg. Der
Branch heißt `main`.

```bash
uv run --extra dev pytest -q 2>&1 | tail -30
```

Die Suite ist auf der Entwicklungsmaschine grün. **Zu melden:** die
Zusammenfassungszeile und jeder Fehlschlag mit Testnamen und Assertion.

Wichtig bei der Einordnung: einige Tests brauchen `exiftool` und `librosa`.
Fehlen die, schlagen unabhängig von ffmpeg fehl:
`test_offsets_treffen_den_ersten_schlag`, `test_hochformat_*`,
`test_schnitte_liegen_auf_echten_beats`,
`test_abweichung_waechst_nicht_zum_ende_hin`. Bitte diese vier bzw. fünf von
echten ffmpeg-9-Befunden trennen und dazu melden, ob `exiftool` und `librosa`
installiert waren.

Danach ein echter Durchlauf mit synthetischem Material:

```bash
slideshow selftest --make-fixtures --fixtures-dir /tmp/ff9/fix
cd /tmp/ff9/proj 2>/dev/null || mkdir -p /tmp/ff9/proj && cd /tmp/ff9/proj
slideshow --project . probe /tmp/ff9/fix/images /tmp/ff9/fix/clips --fps 60
slideshow --project . audio /tmp/ff9/fix/audio/click_two_songs.wav
slideshow --project . preprocess --intermediate hevc_intra_cpu
slideshow --project . beats
slideshow --project . build --force
slideshow --project . render --preview -o out/preview.mp4
ffprobe -v error -show_entries format=duration -show_entries \
  stream=codec_name,pix_fmt,width,height -of default=nw=1 out/preview.mp4
```

**Zu melden:** ob jede Stufe mit Exitcode 0 endet, die Render-Report-Tabelle,
und die ffprobe-Ausgabe des Ergebnisses. `slideshow doctor` bitte ebenfalls
laufen lassen und den vollständigen Report beilegen — er enthält genau die
Zeile „zoompan-Bittiefe", um die es in P1 geht.

---

## Berichtsformat

```
# ffmpeg-9-Prüfbericht

## Umgebung
ffmpeg:   <erste Zeile von -version>
ffprobe:  <erste Zeile von -version>
OS:       <...>
GPU:      <Modell oder "keine">
zscale:   <ja/nein>   libplacebo: <ja/nein>
exiftool: <ja/nein>   librosa:    <ja/nein>

## Ergebnis in einem Satz
<z. B. "Neun von elf Prüfungen unauffällig; P1 und P2 zeigen Abweichungen.">

## Befunde (nur Abweichungen, schwerste zuerst)
### B1 — <Titel>  [blockierend | schädlich | harmlos]
Prüfung:   P<n>
Erwartet:  <Referenzwert>
Gemessen:  <Wert>
Rohausgabe:
```
<...>
```
Wirkung auf das Programm: <welche Funktion, welche Folge für den Nutzer>

## Prüfungstabelle
| Prüfung | Ergebnis | Referenz 6.1.1 | Gemessen 9.x | Bewertung |
|---|---|---|---|---|
| P1a | ... | 2 Treffer, 1 Zeile | ... | ... |
| P1b | ... | 1024 / 256 | ... | ... |
| ... | | | | |

## Nicht durchführbar
<Prüfung + Grund>
```

## Was am Ende beantwortet sein muss

1. Liefert die `zoompan`-Sonde auf ffmpeg 9 noch eine richtige Antwort — und
   wenn nein, in welche Richtung irrt sie? (P1a, P1b)
2. Trägt der fertige Master seine BT.709-Tags und sein `hvc1`? (P2)
3. Läuft `slideshow audio` — also trifft das loudnorm-Muster? (P4)
4. Läuft `slideshow beats` — also stimmt das silencedetect-Format? (P5)
5. Stimmt die Framezahl eines Standbildsegments exakt? (P8)
6. Gibt es einen Fehlschlag, der einen Renderlauf abbricht statt nur einen
   Report zu verfälschen?

Punkt 5 und 6 entscheiden, ob ffmpeg 9 für dieses Programm freigegeben werden
kann. Alles andere ist Nacharbeit.

---

## Anhang: Referenzwerte, gemessen auf ffmpeg 6.1.1

| Prüfung | Wert |
|---|---|
| P1a | `auto-inserting` 2×; genau eine Zeile mit `Parsed_zoompan` (die auto-inserting-Zeile) |
| P1b | Referenzrampe 1024 unterscheidbare Y-Werte, nach `zoompan` **256** |
| P2 | alle drei Stufen `tv,bt709,bt709,bt709`; `m.mp4` mit `hvc1` |
| P7 | akzeptiert 0.5–100.0, abgelehnt 0.49 und 101 |
| P8 | `60/1,60` |
| P9 | `zscale OK`, `libplacebo FEHLER` (kein Vulkan-Gerät) |

Diese Werte stammen aus einer echten Messung auf ffmpeg 6.1.1 (Ubuntu-Paket),
nicht aus der Dokumentation. Die Entwicklungsmaschine des Projekts läuft auf
8.1.2 — für sie liegen keine eigenen Messungen vor; wo ein Wert auf 9
abweicht, ist deshalb offen, ob die Änderung mit 7, 8 oder 9 kam. Für die
Freigabeentscheidung ist das gleichgültig, für einen späteren Patch nicht.
