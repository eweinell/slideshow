# Briefing: Titelfolien-Hintergrund nach Tragfähigkeit wählen

**Status:** Konzept, offen · geprüft gegen 360ef0b (07.08.2026) ·
**Betrifft:** `build.py` (`insert_titles`), `titles.py` (Messfunktion
herausziehen), `models.py` (`TitleDefaults`) ·
**Herkunft:** Abschnitt 14.1 des Ken-Burns-Briefings
([`briefing-kenburns-inhaltsabhaengig.md`](briefing-kenburns-inhaltsabhaengig.md))
nennt diesen Teil „den billigsten echten Gewinn im ganzen Abschnitt 14 — und
ein Argument dafür, ihn unabhängig von diesem Briefing zu bauen." Genau das
tut dieses Briefing: **der Kontrast-Teil, ohne API.** Der API-Teil (Gesichter
unter der Textfläche, Szenenpräferenz) bleibt dort.

---

## 1. Ausgangslage

`bg: auto` nimmt heute **das erste Bild des neuen Abschnitts** — eine reine
Positionsentscheidung (`insert_titles`, `build.py:288`; für handgeschriebene
Edit-Lists dieselbe Regel in `resolve_bg`, `titles.py:146`). Erst viel später,
beim Backen des Assets, misst `render_title` (`titles.py:389`) die
Leuchtdichte unter der Textfläche und dunkelt nach, bis 4,5:1 Kontrast steht
(`_fit_darkening`, `titles.py:668`): Startwert `darken` 0,55, Schrittweite
0,05, harte Untergrenze `DARKEN_FLOOR` 0,25. Reicht auch die nicht, gibt es
eine Warnung — die Folie ist dann schon gebaut.

Das Fehlerbild: das erste Bild des Abschnitts ist ein heller Himmel. Die
Abdunklung läuft an ihre Grenze, die Folie wird matschig oder trägt den Text
gar nicht — während das dritte Bild des Abschnitts eine dunkle Wasserfläche
hätte, die den Text mit 0,55 trüge. Das Werkzeug hat alle Zutaten, diese Wahl
selbst zu treffen; es fehlt nur die Iteration.

### 1.1 Die Nebenbedingung, die die Wahl teuer macht: die Fokusblende

Die Wahl ist **nicht frei**. `_ist_fokusblende` (`build.py:608`) koppelt
Folie und Folgebild zum Schärfezug genau dann, wenn `title.bg` das nächste
Standbild *ist*. Wählt die Messung Bild 3 statt Bild 1, entfällt die
Fokusblende — derselbe Effekt wie heute bei einem von Hand gesetzten
`bg: img_075`, aber eben automatisch und damit potenziell unbemerkt.
[Rezept 4](rezepte.md#4-kapitelweise-erzählen) verkauft die Kopplung
ausdrücklich als Gestaltungsmittel („das vorgezogene Bild wird zum
Hintergrund seiner eigenen Kapitelfolie, und die Blende danach löst es scharf
auf").

Daraus folgt die Grundhaltung dieses Briefings: **das erste Bild hat Vorrang,
solange es trägt.** Die Messwahl ist ein Rettungsweg für den hellen Himmel,
kein Optimierer, der für 5 % weniger Abdunklung eine Choreografie opfert.

## 2. Festlegung

Bei `bg: auto` läuft in `insert_titles` je Kapitel diese Kette:

1. **Kandidaten** sind die ersten `auto_candidates` (Vorgabe 5) *Standbilder*
   des Abschnitts — vom Kapitelanfang bis zur nächsten Kapitelgrenze, Clips
   übersprungen wie heute.
2. **Das erste Bild wird zuerst gemessen.** Trägt es den Text mit einem
   Abdunklungsfaktor ≥ `auto_darken_min` (Vorgabe 0,40), bleibt es —
   Fokusblende erhalten, keine weitere Messung, das Ergebnis ist
   byte-identisch zu heute.
3. Sonst werden die übrigen Kandidaten gemessen und es gewinnt der mit der
   **geringsten nötigen Abdunklung** (größter Faktor). Bei Gleichstand — die
   Faktoren sind durch die Schrittweite 0,05 diskret, Gleichstand ist der
   Normalfall, kein Randfall — gewinnt der **früheste**. Liegt damit wieder
   das erste Bild vorn, ist nichts passiert.
4. Fällt die Wahl nicht auf das erste Bild, **meldet der Bericht das mitsamt
   den Zahlen und der Folge**: „Kapitel 'Malmö': Hintergrund `img_101`
   (Abdunklung 0,55) statt `img_098` (0,30) — die Fokusblende entfällt."
5. Trägt kein Kandidat den Text (alle unter `min_contrast` am Boden), bleibt
   das erste Bild und die heutige Warnung — keine Verschlimmbesserung, und
   die Fokusblende bleibt wenigstens erhalten.

Der gewählte Hintergrund landet wie heute als konkreter Wert in `edit.yaml`
(Entscheidung 4 des Titelfolien-Briefings: sichtbar und von Hand
korrigierbar). Ein ausdrückliches `bg:` (Medien-ID, Farbe, `none`) umgeht die
Kette vollständig — der Vorschlag ersetzt nicht die Wahl, er verbessert die
Vorgabe.

### 2.1 Die Messung gehört in eine gemeinsame Funktion

Die Wahl braucht die Rechnung aus `render_title` zur `build`-Zeit: Satz →
Messbox → Hintergrund (Original laden, auf die Leinwand skalieren, Blur auf
1/8) → `_fit_darkening`. Diese Kette wird aus `render_title` in eine eigene
Funktion gezogen (Arbeitstitel `measure_darkening(seg, defaults, bg_source,
size, font) -> (faktor, kontrast)`), die **beide** Aufrufer verwenden —
`insert_titles` für die Wahl, `render_title` beim Backen. Zwei
Implementierungen derselben Rechnung driften; eine Folie, deren Wahl mit
anderen Zahlen begründet wurde als denen, mit denen sie gebacken wird, wäre
genau der stille Fehler, den dieses Repo sonst überall ausschließt.

Zwei Konsequenzen daraus:

- `build` muss die Schrift auflösen (`find_font`), was es heute nicht tut.
  Findet sich keine, **entfällt die Wahl mit einer Warnung** und das erste
  Bild bleibt — `build` bricht deshalb nicht ab; das Backen warnt später
  ohnehin.
- Die Messung braucht Titel und Untertitel (die Textfläche hängt daran).
  `subtitle: auto` muss daher **vor** der Hintergrundwahl aufgelöst sein —
  im heutigen Code steht die bg-Auflösung vor der subtitle-Auflösung
  (`build.py:288` vor `:315`), die Reihenfolge dreht sich um.

### 2.2 Kosten

Eine Messung lädt das Original (20 MP), skaliert und blurt auf 1/8 — grob
eine halbe bis eine Sekunde. Durch die Faulheit aus Schritt 2 ist der
Normalfall **eine Messung je Kapitel**; nur der helle Himmel kostet bis zu
`auto_candidates`. Bei acht Kapiteln also Sekunden, im schlechtesten Fall
eine halbe Minute — spürbar, aber `build` bleibt der billige Schritt. Ein
Messwert-Cache (Schlüssel: Bildhash + Text + Layoutparameter) ist möglich,
aber nicht Teil dieses Umfangs.

## 3. Entscheidungen

### E1 — Wann darf die Wahl vom ersten Bild abweichen?

**(a) Nur im harten Fehlerfall** (Kontrast schafft es auch am Boden 0,25
nicht) — zu zaghaft: „matschig" beginnt weit vor dem Boden, und der harte
Fall ist selten. Der Gewinn bliebe Theorie.

**(b) Unterhalb einer Schwelle `auto_darken_min`** *(Empfehlung, Vorgabe
0,40)* — der Normalfall (Startwert 0,55 trägt) bleibt unangetastet und damit
auch die Fokusblende; gewechselt wird erst, wenn das erste Bild deutlich
über den Startwert hinaus abgedunkelt werden müsste. 0,40 liegt drei
Messschritte unter dem Startwert und deutlich über dem Boden 0,25.

**(c) Immer das beste Bild** — opfert die Fokusblende für marginale Gewinne
und macht die Wahl nervös: jedes nachgereichte Bild kann den Hintergrund
aller folgenden Kapitel kippen.

> **Empfehlung: (b).** Beide Werte sind `TitleDefaults`-Schlüssel
> (`auto_candidates: 5`, `auto_darken_min: 0.40`), **keine CLI-Argumente** —
> Architektur-Invariante 1. Wichtig: beide gehören **nicht** in
> `layout_params` (`titles.py:222`) — sie ändern das gebackene Bild nicht
> und dürfen keine Assets invalidieren; genau davor warnt der dortige
> Docstring.

### E2 — Was macht der Handpfad (`bg: auto` in einer handgeschriebenen Edit-List)?

**(a) `resolve_bg` bleibt positional** *(Empfehlung)* — die Messwahl ist eine
`build`-Leistung, deren Ergebnis sichtbar in `edit.yaml` steht. Beim Backen
(`ensure_title_assets`) gäbe es keinen Ort, an dem die Wahl sichtbar würde —
unsichtbare Magie, das Gegenteil von Entscheidung 4. Der Docstring von
`resolve_bg` begründet heute die Gleichheit beider Wege; er wird
umgeschrieben: der Handweg bekommt die *einfache* Regel, und wer die Messwahl
will, geht über `build`.

**(b) Auch beim Backen messen** — verworfen: `ensure_title_assets` schreibt
`edit.yaml` nicht, die Wahl wäre unsichtbar und bei jedem Backen neu.

### E3 — Meldet der Bericht auch den Normalfall?

Nur Abweichungen und Fehlschläge (Schritt 4 und 5). Ein Bericht, der bei
jedem Bau acht Zeilen „Kapitel X: erstes Bild trägt" ausgibt, trainiert das
Überlesen — gemeldet wird, was eine Entscheidung war.

## 4. Abnahmekriterien

- **A1** — Trägt das erste Bild mit Faktor ≥ `auto_darken_min`, ist
  `edit.yaml` byte-identisch zum Stand vor diesem Briefing (inklusive
  gesetzter Fokusblende).
- **A2** — Erstes Bild unter der Schwelle, ein späterer Kandidat trägt
  besser: `bg:` zeigt auf den Kandidaten, der Bericht nennt beide Faktoren
  und den Entfall der Fokusblende; `_couple_focus_motion` koppelt nicht.
- **A3** — Kein Kandidat trägt: erstes Bild bleibt, die heutige Warnung
  bleibt, keine Endlosmessung.
- **A4** — Ein ausdrückliches `bg:` (ID, Farbe, `none`) löst keine Messung
  und keine Meldung aus.
- **A5** — Determinismus: zweimal `build` auf demselben Material ergibt
  byte-identische Dateien; die Messfunktion ist dieselbe, die das Backen
  verwendet (ein gemeinsamer Codepfad, per Test erzwungen: Wahlfaktor ==
  Backfaktor für dieselbe Folie).
- **A6** — Ohne auffindbare Schrift läuft `build` mit Warnung durch, Wahl
  entfällt.
- **A7** — Suite grün. Vorbestehende Ausnahme: die drei HDR-Tests, bis
  [`briefing-hlg-ffmpeg8.md`](briefing-hlg-ffmpeg8.md) umgesetzt ist.
- **A8** — Sichtprüfung an echtem Material mit mindestens einem hellen
  Kapitelanfang: ist die Folie lesbarer, und fällt der Entfall der
  Fokusblende dort auf?

## 5. Betroffene Stellen

| Ort | Änderung |
|---|---|
| `build.py` `insert_titles` (`:288`) | Kandidatenkette statt „erstes Bild"; Reihenfolge subtitle ↔ bg drehen; Berichtszeilen |
| `titles.py` | Messkette aus `render_title` als `measure_darkening` herausziehen; `render_title` ruft sie; `resolve_bg`-Docstring (E2) |
| `models.py` `TitleDefaults` | `auto_candidates`, `auto_darken_min`; **nicht** in `layout_params` |
| Tests | `tests/test_titles_generator.py` oder neu: A1–A6; Fixtures mit hellem/dunklem Kandidaten (einfarbige Testbilder genügen) |

## 6. Doku-Anpassungen (Teil der Umsetzung)

- `docs/rezepte.md`, Rezept 2: „Jede Folie hat den unscharfen, abgedunkelten
  Hintergrund des ersten Bildes ihres Abschnitts" — ergänzen: *sofern es den
  Text trägt; sonst nimmt `build` das tragfähigste der ersten fünf und
  meldet es.* Rezept 4 („wird damit zum Hintergrund seiner eigenen
  Kapitelfolie") bekommt denselben Vorbehalt in einem Halbsatz.
- `docs/briefing-titelfolien.md`, Entscheidung 4: Nachtrag mit Verweis
  hierher — die Empfehlung (a) „erstes Bild" gilt weiterhin als Vorrang,
  die Messwahl ist ihr Rettungsweg.
- `docs/briefing-kenburns-inhaltsabhaengig.md`, 14.1: der Kontrast-Teil ist
  ausgekoppelt (Nachtrag steht bereits drin); nach der Umsetzung dort als
  **umgesetzt** markieren, der API-Teil bleibt offen.
- `docs/edit-yaml.md`: `bg: auto` unter dem Titel-Segment und die beiden
  neuen `TitleDefaults`-Schlüssel dokumentieren.
- `CLAUDE.md`: Baustellen-Zeile aktualisieren.

## 7. Risiken

- **Die Wahl kippt bei Materialnachschub.** Ein nachgereichtes Bild am
  Kapitelanfang kann Hintergrund *und* Fokusblende ändern — das tut es heute
  auch (das „erste Bild" ist dann ein anderes), aber die Messwahl macht den
  Effekt weniger vorhersagbar. Der Bericht (Schritt 4) ist die Antwort;
  wer Stabilität will, schreibt `bg:` fest.
- **Schwellenwert ist Geschmack.** 0,40 ist ein Vorschlag, kein Messwert.
  Die Sichtprüfung (A8) muss ihn bestätigen; er ist bewusst ein
  Defaults-Schlüssel.
- **`build` wird langsamer.** Begrenzt durch die Faulheit aus 2.2; wenn es
  stört, ist der Messwert-Cache der nächste Schritt, nicht das Weglassen der
  Messung.

## 8. Nicht in diesem Umfang

Der API-Teil von 14.1 (Gesichter unter der Textfläche, `quiet`-Flächen,
Szenenpräferenz) — er setzt später als weiterer Filter auf derselben
Kandidatenkette auf. Die Textlage (unten/Mitte/oben statt Abdunkeln) — laut
14.1 ein eigenes Briefing. Ein Messwert-Cache. Jede Form von Umsortierung
des Materials.
