# Manuelle Abnahme

Die Kriterien mit visueller Komponente lassen sich nicht automatisiert prüfen —
synthetisches Testmaterial sagt nichts über Farbeindruck, Banding oder Judder.
Diese Liste ist der Rest, der mit **echtem** Material von Hand durchzugehen ist.

Alles andere läuft automatisiert: `slideshow selftest --make-fixtures` erzeugt
das Material, `pytest` prüft die Kriterien 1, 2, 3, 5–12 und 14 dagegen.

---

## 1. Banding in Himmelsverläufen (Abschnitt 8.1)

**Warum manuell:** Die Bittiefen-Frage ist verifiziert — `zoompan` rechnet in
ffmpeg 6.1 nachweislich in 8 Bit (siehe `doctor`-Report, Zeile
„zoompan-Bittiefe"). Ob das *sichtbar* stört, hängt vom Motiv ab.

**Vorgehen:**

1. Ein Foto mit großem, glattem Himmelsverlauf auswählen.
2. `slideshow render edit.yaml -o out/test.mp4 --range 0:3`
3. Auf einem guten Monitor im Vollbild ansehen, besonders bei langsamem Zoom.

**Wenn Stufen sichtbar sind:**

```bash
slideshow build --kb-engine scale16      # 16-Bit-Pfad ohne zoompan
```

Kostet spürbar mehr CPU. Der Pfad ist verifiziert bitgleich-kontinuierlich
(siehe `tests/test_kenburns.py`), also gefahrlos umschaltbar.

---

## 2. Zittern bei langsamen Schwenks (Abschnitt 8.1)

**Warum manuell:** `zoompan` schneidet x/y auf ganze Pixel. Die Testsuite prüft,
dass nie mehr als zwei gleiche Frames aufeinanderfolgen — ob das als Ruckeln
auffällt, entscheidet das Auge.

**Vorgehen:** Ein Segment mit langer Standzeit (≥ 8 s) und wenig Zoom rendern
und den Bildrand beobachten. Bei sichtbarem Zittern: `--kb-engine scale16`.

---

## 3. HLG/PQ-Clip im SDR-Master (Abnahmekriterium 4)

> Ein HLG-Clip sieht im SDR-Master farblich zu den umgebenden SDR-Clips passend
> aus, nicht flau.

**Warum manuell:** Automatisiert geprüft wird nur, dass die Tonemapping-Kette
greift und BT.709-getaggtes Material herauskommt. Der Farbeindruck ist eine
Beurteilung.

**Vorgehen:**

1. Einen HLG-Clip (Sony HLG-Profil oder Pixel-HDR-Video) direkt neben einen
   SDR-Clip derselben Szene schneiden.
2. Rendern und beide im direkten Übergang vergleichen.

**Stellschrauben,** falls der Clip flau oder zu kontrastreich wirkt:

- `doctor` zeigt unter „HDR-Tonemapping", welcher Pfad benutzt wird.
  `libplacebo` (`tonemapping=bt.2390`) ist die bessere Variante, braucht aber
  ein Vulkan-Gerät; sonst greift `zscale` + `tonemap=hable`.
- Der `npl`-Wert steht in `Capabilities.tonemap_chain()`: 1000 für HLG, 100 für
  PQ. **Abweichung vom Briefing:** dort steht `npl=100` für beide. Bei HLG ist
  1000 der Referenz-Peak; mit 100 bleibt das Bild flau — genau der Fehler, den
  dieses Kriterium ausschließt.
- Ohne `zscale` **und** `libplacebo` greift nur eine Näherung
  (`eq=contrast=1.18:saturation=1.12:gamma=0.92`). Das ist keine Farbwissenschaft.
  Dann besser einen ffmpeg-Full-Build installieren.

---

## 4. Judder in Schwenks bei konformiertem Material (Abschnitt 7)

**Warum manuell:** Die Testsuite prüft die Retiming-*Rechnung* (Kadenz,
Framezahl, CFR). Ob eine Kameraschwenkbewegung nach der Konformierung glatt
läuft, sieht man nur.

**Vorgehen:** Einen Sony-50p-Clip mit deutlichem Schwenk in einem 60p-Master
rendern und den Schwenk beobachten.

**Zu beachten — bewusste Abweichung vom Briefing:** Das Briefing nennt für
50p → 60p `setpts=1.2*PTS` und beschreibt das Ergebnis als „leichte Zeitlupe"
bei gleichzeitig „jeder Frame ein Ausgabeframe". Beides zusammen geht nicht auf.
Umgesetzt ist die Variante, die glatt läuft: Faktor `50/60 = 0.8333`, jeder
Quellframe wird genau ein Ausgabeframe. Das Material läuft dadurch 20 %
**schneller** statt langsamer.

Wer die Zeitlupe will, wählt 50p als Zielrate:

```bash
slideshow probe ... --fps 50
```

Dann bleibt Sony-Material 1:1 — der Preis ist, dass Android-30p zu 50p kein
ganzzahliges Verhältnis hat und seinerseits konformiert wird. Bei gemischtem
Material gibt es keine verlustfreie Wahl; `probe` beziffert den Kompromiss.

---

## 5. Wiedergabe auf den Zielgeräten (Abnahmekriterium 13)

> Der Master (`hvc1`, faststart) spielt in Windows „Filme & TV", VLC und auf
> einem Apple-Gerät ohne Nachkonvertierung.

Automatisiert geprüft sind das Codec-Tag `hvc1` und die Atom-Reihenfolge
(`moov` vor `mdat`). Die tatsächliche Wiedergabe muss auf den Geräten selbst
getestet werden:

- [ ] Windows „Filme & TV"
- [ ] VLC
- [ ] iPhone/iPad oder macOS QuickTime
- [ ] Netzwerk-/Streaming-Wiedergabe (faststart wirkt erst dort)

---

## 6. MLT/Kdenlive-Export (Abschnitt 9)

**Warum manuell:** Die `rect`-Keyframe-Syntax der Transform-Effekte
unterscheidet sich zwischen Kdenlive-Versionen genug, dass Raten Zeitverschwendung
ist.

**Vorgehen:**

1. `slideshow export-mlt edit.yaml -o out/projekt.kdenlive`
2. In Kdenlive öffnen. Erwartet: zwei Videospuren im A/B-Roll, die sich an
   jedem Schnitt um die Blendendauer überlappen, plus eine Tonspur.
3. Prüfen, ob die Ken-Burns-Bewegung auf den Standbildern läuft.

**Wenn die Bewegung fehlt:** Ein Segment von Hand in Kdenlive bauen, speichern,
das erzeugte XML öffnen und die `rect`-Zeile mit der Ausgabe von
`slideshow.mlt.kenburns_rect()` vergleichen. Das ist schneller als jede
Rateschleife. Der Export schreibt `qtblend` mit `rect` im Format
`frame=x y w h opacity`.

**Rückweg:** In Kdenlive korrigierte Zeiten fließen mit

```bash
slideshow export-mlt edit.yaml --reimport out/projekt.kdenlive
```

als `dur:` in die Edit-List zurück — explizite Sekunden gewinnen immer (6.3).

---

## 7. Nach dem ersten echten Durchlauf prüfen

- [ ] Liegt Bild 63 aufrecht? (EXIF-Orientation wird im Preprocessing hart
      eingebrannt — wenn nicht, fehlt `exiftool`; `doctor` warnt davor.)
- [ ] Sind die Uhren der Geräte plausibel? `probe` gibt pro Kameramodell die
      Zeitspanne aus. Ohne Korrektur verschränkt die chronologische Sortierung
      Sony- und Android-Material systematisch.
- [ ] Stimmt die Downbeat-Phase? `slideshow beats` schreibt die Regionenkarte
      zur **Sichtprüfung**, bevor gebaut wird. Die automatische Phase ist der
      unzuverlässigste Teil der ganzen Analyse; sie von Hand zu setzen dauert
      dreißig Sekunden:

      ```yaml
      - {type: beat, start: 0.0, end: 184.9, bpm: 118.0, offset: 0.412}
      ```
