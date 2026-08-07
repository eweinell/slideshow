# Briefing: Blendenmodus validieren

**Status:** Konzept, offen · geprüft gegen 360ef0b (07.08.2026) ·
**Betrifft:** `models.py`, ein Test; `kenburns.py` bleibt unverändert ·
**Aufwand:** klein — bewusst als Briefing festgehalten, weil die Entscheidung
*wo* geprüft wird, einen Import-Zyklus berührt.

---

## 1. Ausgangslage

Das Schema der Edit-List validiert überall hart: unbekannte Schlüssel sind ein
Fehler mit Pfad und Zeile (`docs/edit-yaml.md`, Kopf), `version` wird
abgelehnt statt geraten, ein `before:`-Anker auf ein fehlendes Bild bricht den
Bau ab. Eine einzige Stelle fällt aus dieser Reihe:

```python
transition = _XFADE_MODES.get(mode, "fade")        # kenburns.py:348
```

Ein vertipptes `mode: disolve` wird stillschweigend zu einer normalen Blende.
Kein Fehler, keine Warnung — der Film rendert und sieht nur anders aus als
bestellt. `known_modes()` (`kenburns.py:362`) existiert seit jeher, wird aber
nirgends zur Prüfung aufgerufen.

Wo der Wert herkommt, beides Freitext:

| Feld | Stelle | Default |
|---|---|---|
| `XfadeDefaults.mode` | `models.py:387` | `dissolve` |
| `XfadeSegment.mode` | `models.py:566` | `dissolve` |

Drei Feststellungen, die den Zuschnitt klein halten:

- **Der Cache ist nicht das Problem.** Der Segment-Hash enthält den Modus
  (`render.py:216`); wer den Tippfehler korrigiert, invalidiert korrekt. Das
  Problem ist allein die stille Fehlleitung davor.
- **`dissolve` ist ein Alias, kein Fehler.** Die Tabelle `_XFADE_MODES`
  (`kenburns.py:352`, 16 Einträge) bildet `dissolve` auf ffmpegs `fade` ab.
  Gültig ist, was als *Schlüssel* in der Tabelle steht — die Validierung ist
  ihr Schlüsselvergleich, nicht mehr.
- **Der MLT-Export ist unberührt.** `mlt.py` liest `mode` gar nicht; die
  Übergänge landen dort als Standard-Blende. Das bleibt so.

## 2. Festlegung

Beide `mode`-Felder werden im Schema gegen `known_modes()` geprüft. Die
Fehlermeldung nennt den vertippten Wert und die gültigen Modi; YAML-Pfad und
Zeile kommen über die vorhandene Schema-Maschinerie von selbst dazu.

`_XFADE_MODES.get(mode, "fade")` in `xfade_expr` bleibt stehen — nach der
Validierung ist der Default unerreichbar, aber eine Funktion, die auch mit
rohen Strings aufrufbar ist, soll nicht mit `KeyError` antworten. Ein
Kommentar an der Stelle sagt, warum der Rückfall bleibt.

## 3. Entscheidung — wo läuft die Prüfung?

Der Haken ist die Importrichtung: `kenburns.py` importiert `models.py`
(`kenburns.py:34`). Ein Import in der Gegenrichtung auf Modulebene wäre ein
Zyklus.

**(a) `field_validator` in `models.py`, Import von `known_modes` im
Funktionskörper** *(Empfehlung)* — der Zyklus entsteht nur auf Modulebene; zur
Validierungszeit ist `kenburns` längst geladen oder problemlos ladbar.
Kleinster Eingriff, eine Stelle, Fehlermeldung mit Pfad und Zeile gratis.

**(b) Tabelle nach `models.py` umziehen** — löst den Zyklus grundsätzlich,
trägt aber Render-Wissen (die ffmpeg-Namen der xfade-Transitionen) in die
Schemadatei. Die Tabelle gehört zu dem Code, der sie in einen Filterausdruck
übersetzt.

**(c) Prüfung an den Verwendungsstellen in `build`/`render`** — zwei Stellen
statt einer, und eine von Hand geschriebene Edit-List erreicht `render` ohne
`build`. Genau für diesen Weg gibt es die Schemavalidierung.

> **Empfehlung: (a).**

## 4. Abnahmekriterien

- **A1** — Ein `mode: disolve` in einem xfade-Segment bricht bei `render`
  (und bei jedem anderen Leser der Datei) mit einer Meldung ab, die Pfad,
  Zeile, den vertippten Wert und die gültigen Modi nennt.
- **A2** — Dasselbe für `defaults.xfade.mode`.
- **A3** — Alle 16 Schlüssel aus `known_modes()` validieren; `dissolve`
  bleibt gültig und bedeutet weiterhin `fade`.
- **A4** — Suite grün. Vorbestehende Ausnahme: die drei HDR-Tests, bis
  [`briefing-hlg-ffmpeg8.md`](briefing-hlg-ffmpeg8.md) umgesetzt ist.

## 5. Doku-Anpassungen (Teil der Umsetzung)

- `docs/edit-yaml.md`: beim Segmenttyp `xfade` und bei `defaults.xfade` je
  ein Satz, dass ein unbekannter Modus ein Fehler ist, plus die Liste der
  gültigen Werte (aus `known_modes()` abgeschrieben, mit dem Hinweis, dass
  `dissolve` = `fade`).
- `CLAUDE.md`: Baustellen-Zeile „Blendenmodus wird nicht geprüft" entfernen.
- `README.md`/`docs/rezepte.md`: erwähnen den Modus nicht — vor dem Abschluss
  per Suche gegenprüfen, dann nichts zu tun.

## 6. Risiken

Eine bestehende `edit.yaml` mit Tippfehler, die bisher stillschweigend als
`fade` lief, bricht nach der Umsetzung ab. Das ist gewollt (Grundprinzip 4,
„fail loud"), aber die Fehlermeldung muss den Übergang tragen: wer den alten
stillen Zustand wiederhaben will, schreibt den genannten gültigen Modus hin.

## 7. Nicht in diesem Umfang

Neue Übergangsarten, die Übertragung des Modus in den MLT-Export, und die
Validierung weiterer Felder außerhalb der beiden `mode`-Felder.
