"""Bildanalyse mit mehreren Modellen gegen eine Referenz — Kosten und Genauigkeit.

Die offene Frage aus dem Briefing (``docs/briefing-kenburns-inhaltsabhaengig.md``,
Nachtraege 12.11.2026): reicht ein billigeres Modell der aktuellen Generation
(Haiku 5.5, Sonnet 5.5) fuer die Bildfakten, oder setzt es die Schutzboxen so
ungenau, dass die Kamerafahrt leidet? Referenz ist die Vorgabe,
``vision.DEFAULT_MODEL`` (Opus 5.5). Der Preis ist bei 500 Bildern kein
Argument (Cent-Betraege), die Koordinaten sind es.

Gemessen wird grundsaetzlich die **aktuelle** Modellgeneration; aeltere
Modelle lassen sich mit ``--modelle`` nennen, sind aber nicht der Massstab.

Das Skript analysiert eine kleine, ueber das Material gestreute Stichprobe mit
jedem Modell und schreibt nach ``<projekt>/vision-vergleich/``:

- ``<modell>.yaml``  — die Analyse je Modell, im Format von ``vision.yaml``;
- ``vergleich.html`` — jedes Bild mit den Boxen aller Modelle nebeneinander,
  zur Sichtpruefung (rot: Schutzbox, blau: Motiv, gruen: ruhige Flaeche,
  gelb: Fokus, orange: Horizont);
- ``vergleich.md``   — Kennzahlen je Kandidat gegen die Referenz:
  Szenen-Uebereinstimmung, Abstand der Fokuspunkte, Deckung der Schutzboxen,
  geplante Kamerafahrt; dazu gemessene Tokens und Kosten samt Hochrechnung.

**Die ``vision.yaml`` des Projekts wird nicht angefasst.** Der Vergleich ist
ein Messlauf, keine Analyse fuer den Film.

Die Referenz ist kein Ground Truth, sondern der Massstab der heutigen Vorgabe:
eine Abweichung heisst "anders als Opus", nicht "falsch". Ob anders auch
schlechter ist, zeigt erst ``vergleich.html``.

Der API-Schluessel kommt aus ``ANTHROPIC_API_KEY`` oder aus ``--key-file``
(eine Textdatei mit nur dem Schluessel, **ausserhalb** des Repos). Er wird
nie ausgegeben.

Aufruf::

    # erst ohne Anfragen: Stichprobe und Kostenschaetzung
    python scripts/vision_vergleich.py kenia-2026 --dry-run

    # dann der Lauf (schickt die Stichprobe an die API)
    python scripts/vision_vergleich.py kenia-2026 --key-file C:/pfad/key.txt --yes
"""

from __future__ import annotations

import argparse
import html
import math
import os
import statistics
import sys
import time
from pathlib import Path

from slideshow import vision as v
from slideshow.kbplan import plan_kb
from slideshow.models import KBDefaults, Manifest, VisionDoc, VisionEntry
from slideshow.paths import Project

#: Die billigeren Modelle der aktuellen Generation — sie muessen sich messen.
KANDIDATEN = ("claude-haiku-5-5", "claude-sonnet-5-5")
#: Woran gemessen wird: die Vorgabe von ``slideshow analyze``.
REFERENZ = v.DEFAULT_MODEL
#: Standzeit, mit der die Kamerafahrt zum Vergleich geplant wird — die
#: Vorgabe-Standzeit eines Bildes liegt bei rund fuenf Sekunden.
DAUER = 5.0
#: Ab dieser Deckung gilt eine Schutzbox als "dieselbe" wie die der Referenz.
IOU_GLEICH = 0.5


# --------------------------------------------------------------------------
# Stichprobe
# --------------------------------------------------------------------------

def stichprobe(project: Project, manifest: Manifest, n: int
               ) -> tuple[dict[str, Path], dict[str, bool]]:
    """``n`` Cache-Bilder, gleichmaessig ueber die Aufnahmefolge verteilt.

    Gestreut statt zufaellig: aufeinanderfolgende Aufnahmen sind oft dasselbe
    Motiv, und zwanzig Bilder aus einer Stunde sagten wenig ueber die Reise.
    Liefert die Pfade und je Bild, ob es ein Hochformat-Komposit ist.
    """
    kandidaten = [m for m in manifest.media
                  if m.kind == "image" and m.cache_path
                  and project.abs(m.cache_path).exists()]
    if not kandidaten:
        sys.exit("Keine vorverarbeiteten Bilder — `slideshow preprocess` fehlt?")
    n = min(n, len(kandidaten))
    schritt = len(kandidaten) / n
    auswahl = [kandidaten[int(i * schritt + schritt / 2)] for i in range(n)]
    pfade = {m.cache_path: project.abs(m.cache_path) for m in auswahl}
    hoch = {m.cache_path: bool(m.image and m.image.portrait) for m in auswahl}
    return pfade, hoch


# --------------------------------------------------------------------------
# Kennzahlen
# --------------------------------------------------------------------------

def iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    schnitt = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    flaeche = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
               - schnitt)
    return schnitt / flaeche if flaeche > 0 else 0.0


def boxen_paaren(a: list, b: list) -> list[float]:
    """Gierige Zuordnung nach groesster Deckung; je Paar die IoU.

    Unpaarige Boxen (das eine Modell schuetzt etwas, das andere nicht)
    zaehlen als 0 — genau der Fall, der den Zoom am staerksten veraendert.
    """
    paare = sorted(((iou(x, y), i, j) for i, x in enumerate(a)
                    for j, y in enumerate(b)), reverse=True)
    frei_a, frei_b, werte = set(range(len(a))), set(range(len(b))), []
    for wert, i, j in paare:
        if i in frei_a and j in frei_b and wert > 0:
            werte.append(wert)
            frei_a.discard(i)
            frei_b.discard(j)
    werte += [0.0] * (len(frei_a) + len(frei_b))
    return werte


def fahrt(e: VisionEntry, key: str, hoch: bool):
    """Die Kamerafahrt, die der Planer aus diesen Fakten macht."""
    return plan_kb(e, key=key, duration=DAUER, defaults=KBDefaults(),
                   portrait_komposit=hoch)


def vergleich(kand: VisionDoc, ref: VisionDoc, hoch: dict[str, bool]) -> dict:
    """Ein Kandidat gegen die Referenz, Bild fuer Bild."""
    gemeinsam = sorted(kand.images.keys() & ref.images.keys())
    k: dict = {"n": len(gemeinsam), "szene": 0, "achse": 0, "fokus": [],
               "horizont": [], "detail": [], "schutz_iou": [], "schutz_k": 0,
               "schutz_r": 0, "fahrt_gleich": 0, "fahrt_zoom": [],
               "fahrt_ziel": []}
    for rel in gemeinsam:
        x, y = kand.images[rel], ref.images[rel]
        k["szene"] += x.scene == y.scene
        k["achse"] += x.axis == y.axis
        if x.focus and y.focus:
            k["fokus"].append(math.dist(x.focus, y.focus))
        if x.horizon is not None and y.horizon is not None:
            k["horizont"].append(abs(x.horizon - y.horizon))
        k["detail"].append(abs(x.detail - y.detail))
        k["schutz_k"] += len(x.protect)
        k["schutz_r"] += len(y.protect)
        k["schutz_iou"] += boxen_paaren(list(x.protect), list(y.protect))
        fx, fy = fahrt(x, rel, hoch[rel]), fahrt(y, rel, hoch[rel])
        # Beide ohne Fahrt heisst: beide fallen auf die heutige Rotation
        # zurueck — das ist Uebereinstimmung, keine Abweichung.
        if fx is None and fy is None:
            k["fahrt_gleich"] += 1
        if fx is not None and fy is not None:
            k["fahrt_gleich"] += fx.signatur == fy.signatur
            k["fahrt_zoom"].append(abs(fx.spec.z[1] - fy.spec.z[1]))
            k["fahrt_ziel"].append(math.dist(fx.spec.c[2:], fy.spec.c[2:]))
    return k


def _mittel(werte: list[float]) -> str:
    return f"{statistics.mean(werte):.3f}" if werte else "—"


def _median(werte: list[float]) -> str:
    return f"{statistics.median(werte):.3f}" if werte else "—"


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------

def bericht_md(kandidaten: list[str], referenz: str, laeufe: dict,
               docs: dict[str, VisionDoc], hoch: dict[str, bool],
               anzahl_hoch: int) -> str:
    alle = [*kandidaten, referenz]
    n_bilder = len(hoch)
    z = [f"# Bildanalyse: {', '.join(kandidaten)} gegen {referenz}", "",
         f"Stichprobe: {n_bilder} Bilder. Kamerafahrt geplant mit {DAUER:g} s "
         f"Standzeit und den Vorgaben. Die Referenz ist der Massstab der "
         f"heutigen Vorgabe, kein Ground Truth — eine Abweichung heisst "
         f"\"anders als {referenz}\", nicht \"falsch\".", "",
         "## Kosten (gemessen)", "",
         "| Modell | Anfragen | ausgefallen | Eingabe/Bild | Ausgabe/Bild | "
         "Cache gelesen | Kosten | je "
         f"{anzahl_hoch} Bilder | je {anzahl_hoch} (Batch) | Laufzeit |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for m in alle:
        bericht, sekunden = laeufe[m]
        vb = bericht.verbrauch
        n = max(1, vb.requests)
        ist = vb.kosten(m)
        hochrechnung = (ist / n * anzahl_hoch) if ist is not None else None
        z.append(
            f"| {m} | {vb.requests} | {bericht.ausgefallen} | "
            f"{vb.eingabe / n:.0f} | {vb.ausgabe / n:.0f} | {vb.cache_gelesen} | "
            + (f"{ist:.4f} $ | {hochrechnung:.2f} $ | {hochrechnung / 2:.2f} $"
               if ist is not None else "? | ? | ?")
            + f" | {sekunden:.0f} s |")
    z += ["", f"Eingabe/Bild ohne Cache; die Schaetzung rechnet mit "
          f"{v.TOKENS_BILD + v.TOKENS_TEXT} (Bild + Text) und "
          f"{v.TOKENS_AUSGABE} Ausgabetokens. Ausgabe/Bild enthaelt die "
          f"Denk-Tokens.", "",
          "Hochrechnung linear aus den gemessenen Kosten je Bild; der "
          "einmalige Cache-Schreibvorgang ist darin anteilig enthalten.", "",
          f"## Uebereinstimmung mit {referenz}", "",
          "| Kennzahl | " + " | ".join(kandidaten) + " |",
          "|---|" + "---|" * len(kandidaten)]
    ks = [vergleich(docs[m], docs[referenz], hoch) for m in kandidaten]

    def zeile(titel: str, wert) -> None:
        z.append(f"| {titel} | " + " | ".join(wert(k) for k in ks) + " |")

    zeile("Gleiche Szene", lambda k: f"{k['szene']} von {k['n']}")
    zeile("Gleiche Achse", lambda k: f"{k['achse']} von {k['n']}")
    zeile("Abstand der Fokuspunkte (Median, Bildbreite = 1)",
          lambda k: _median(k["fokus"]))
    zeile("Horizont-Abweichung (Mittel)", lambda k: _mittel(k["horizont"]))
    zeile("Detaildichte-Abweichung (Mittel)", lambda k: _mittel(k["detail"]))
    # Die Referenzzahl je Spalte: faellt beim Kandidaten ein Bild aus, zaehlt
    # auch die Referenz nur ueber die gemeinsamen Bilder.
    zeile("Schutzboxen (Kandidat / Referenz)",
          lambda k: f"{k['schutz_k']} / {k['schutz_r']}")
    zeile("Deckung der Schutzboxen (IoU, Mittel; unpaarig = 0)",
          lambda k: _mittel(k["schutz_iou"]))
    zeile(f"davon praktisch gleich (IoU ≥ {IOU_GLEICH})",
          lambda k: f"{sum(1 for w in k['schutz_iou'] if w >= IOU_GLEICH)} von "
                    f"{len(k['schutz_iou'])}")
    zeile("Gleiche Fahrt (Zoomrichtung und Schwenk; beide Rotation = gleich)",
          lambda k: f"{k['fahrt_gleich']} von {k['n']}")
    zeile("Zoom am Fahrtende, Abweichung (Mittel)",
          lambda k: _mittel(k["fahrt_zoom"]))
    zeile("Bildmitte am Fahrtende, Abstand (Mittel)",
          lambda k: _mittel(k["fahrt_ziel"]))

    z += ["", "## Je Bild", "",
          "| Bild | " + " | ".join(alle) + " |",
          "|---|" + "---|" * len(alle)]
    for rel in sorted(hoch):
        zellen = []
        for m in alle:
            e = docs[m].images.get(rel)
            if e is None:
                zellen.append("ausgefallen")
                continue
            f = fahrt(e, rel, hoch[rel])
            zellen.append(f"{e.scene} ({e.conf:.2f}), {len(e.protect)} Schutz, "
                          f"Fahrt {f.signatur if f else '—'}")
        z.append(f"| {Path(rel).name} | " + " | ".join(zellen) + " |")
    z += ["", "Sichtpruefung: `vergleich.html` im selben Verzeichnis."]
    return "\n".join(z) + "\n"


_CSS = """
body { margin:0; padding:1rem 1.5rem; font:14px/1.4 "Segoe UI", system-ui,
       sans-serif; background:#17181a; color:#eceae7; }
h1 { font-size:1.2rem; } .legende span { margin-right:1.2rem; }
.zeile { display:grid; gap:1rem; margin:1.4rem 0; }
.zeile h2 { grid-column:1 / -1; margin:0; font-size:.95rem; color:#9a9691; }
.bild { position:relative; }
.bild img { display:block; width:100%; height:auto; }
.box { position:absolute; box-sizing:border-box; }
.schutz { border:2px solid #ff4d4d; }
.motiv { border:2px dashed #6fb0dd; }
.ruhe { border:2px dotted #4ec98a; }
.fokus { position:absolute; width:14px; height:14px; margin:-7px 0 0 -7px;
         border:3px solid #ffd23f; border-radius:50%; }
.horizont { position:absolute; left:0; right:0; border-top:2px solid #f0a43a; }
.text { font-size:.8rem; color:#c9c6c1; margin-top:.3rem; }
"""


def _box(b, klasse: str) -> str:
    x0, y0, x1, y1 = b
    return (f'<div class="box {klasse}" style="left:{x0 * 100:.2f}%;'
            f"top:{y0 * 100:.2f}%;width:{(x1 - x0) * 100:.2f}%;"
            f'height:{(y1 - y0) * 100:.2f}%"></div>')


def _panel(e: VisionEntry | None, modell: str, src: str, hoch: bool,
           rel: str) -> str:
    if e is None:
        return (f'<div><div class="text"><b>{html.escape(modell)}</b> · '
                f"ausgefallen</div></div>")
    teile = [f'<img src="{html.escape(src)}" alt="">']
    teile += [_box(s.box, "motiv") for s in e.subjects]
    teile += [_box(b, "schutz") for b in e.protect]
    if e.quiet:
        teile.append(_box(e.quiet, "ruhe"))
    if e.horizon is not None:
        teile.append(f'<div class="horizont" style="top:{e.horizon * 100:.2f}%"></div>')
    if e.focus:
        teile.append(f'<div class="fokus" style="left:{e.focus[0] * 100:.2f}%;'
                     f'top:{e.focus[1] * 100:.2f}%"></div>')
    f = fahrt(e, rel, hoch)
    text = (f"<b>{html.escape(modell)}</b> · {e.scene} ({e.conf:.2f}) · "
            f"Achse {e.axis} · Detail {e.detail:.2f} · Fahrt "
            f"{html.escape(str(f.signatur)) if f else '—'}<br>"
            f"{html.escape(e.note)}")
    return (f'<div><div class="bild">{"".join(teile)}</div>'
            f'<div class="text">{text}</div></div>')


def bericht_html(modelle: list[str], docs: dict[str, VisionDoc],
                 hoch: dict[str, bool], out: Path, project: Project) -> str:
    spalten = f"grid-template-columns:repeat({len(modelle)}, 1fr)"
    zeilen = []
    for rel in sorted(hoch):
        src = Path(os.path.relpath(project.abs(rel), out)).as_posix()
        panels = "".join(_panel(docs[m].images.get(rel), m, src, hoch[rel], rel)
                         for m in modelle)
        zeilen.append(f'<div class="zeile" style="{spalten}">'
                      f"<h2>{html.escape(Path(rel).name)}</h2>{panels}</div>")
    titel = " | ".join(html.escape(m) for m in modelle)
    return ("<!DOCTYPE html>\n<html lang=\"de\"><head><meta charset=\"utf-8\">"
            "<title>Bildanalyse im Vergleich</title>"
            f"<style>{_CSS}</style></head><body>"
            f"<h1>Bildanalyse: {titel}</h1>"
            '<p class="legende"><span style="color:#ff4d4d">■ Schutzbox</span>'
            '<span style="color:#6fb0dd">■ Motiv</span>'
            '<span style="color:#4ec98a">■ ruhige Flaeche</span>'
            '<span style="color:#ffd23f">● Fokus</span>'
            '<span style="color:#f0a43a">― Horizont</span></p>'
            + "".join(zeilen) + "</body></html>\n")


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------

def schluessel_laden(key_file: str | None, project: Project) -> None:
    if key_file:
        pfad = Path(key_file).resolve()
        repo = Path(__file__).resolve().parent.parent
        if repo in pfad.parents or project.root.resolve() in pfad.parents:
            sys.exit(f"Die Schluesseldatei liegt im Repo bzw. Projekt ({pfad}). "
                     f"Bitte ausserhalb ablegen — sonst landet sie beim naechsten "
                     f"`git add` im Verlauf.")
        os.environ["ANTHROPIC_API_KEY"] = pfad.read_text(encoding="utf-8").strip()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Kein API-Schluessel: ANTHROPIC_API_KEY setzen oder --key-file "
                 "angeben.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("projekt", help="Projektverzeichnis, z. B. kenia-2026")
    ap.add_argument("-n", type=int, default=20, help="Stichprobengroesse (20)")
    ap.add_argument("--modelle", nargs="+", default=list(KANDIDATEN),
                    metavar="MODELL",
                    help=f"Kandidaten (Vorgabe: {' '.join(KANDIDATEN)})")
    ap.add_argument("--referenz", default=REFERENZ, metavar="MODELL",
                    help=f"woran gemessen wird (Vorgabe: {REFERENZ})")
    ap.add_argument("--hochrechnen", type=int, default=500, metavar="N",
                    help="Kosten auf N Bilder hochrechnen (500)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--key-file", default=None,
                    help="Datei mit dem API-Schluessel (ausserhalb des Repos)")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur Stichprobe und Schaetzung, keine Anfragen")
    ap.add_argument("--yes", action="store_true",
                    help="Datenschutzhinweis bestaetigt — ohne den Schalter wird "
                         "nichts gesendet")
    ap.add_argument("--auswerten", action="store_true",
                    help="keine Anfragen: die gespeicherten <modell>.yaml eines "
                         "frueheren Laufs neu auswerten (Kosten bleiben leer)")
    args = ap.parse_args()

    kandidaten = [m for m in args.modelle if m != args.referenz]
    alle = [*kandidaten, args.referenz]
    project = Project.open(args.projekt)
    manifest = Manifest.load(project.manifest)
    pfade, hoch = stichprobe(project, manifest, args.n)
    out = project.root / "vision-vergleich"

    if args.auswerten:
        # Der Messlauf kostet Geld und schickt Fotos weg; die Auswertung darf
        # man beliebig oft wiederholen. Kosten stehen nur im ersten Bericht.
        docs = {m: v.laden(out / f"{m}.yaml") for m in alle}
        fehlt = [m for m, d in docs.items() if d is None]
        if fehlt:
            sys.exit(f"Keine gespeicherte Analyse fuer {', '.join(fehlt)} in {out}.")
        laeufe = {m: (v.Bericht(), 0.0) for m in alle}
        md = bericht_md(kandidaten, args.referenz, laeufe, docs, hoch,
                        args.hochrechnen)
        md = md.replace("## Kosten (gemessen)", "## Kosten\n\nNeu ausgewertet "
                        "ohne Anfragen — die gemessenen Kosten stehen im Bericht "
                        "des Messlaufs.", 1)
        (out / "vergleich-auswertung.md").write_text(md, encoding="utf-8")
        (out / "vergleich.html").write_text(
            bericht_html(alle, docs, hoch, out, project), encoding="utf-8")
        print(f"Neu ausgewertet: {out / 'vergleich-auswertung.md'}")
        return 0

    print(f"Stichprobe: {len(pfade)} Bilder aus {project.root.name}, "
          f"{len(alle)} Modelle, Referenz {args.referenz}")
    summe = 0.0
    for m in alle:
        s = v.kosten_schaetzung(len(pfade), m)
        summe += s or 0.0
        print(f"  {m}: geschaetzt {s:.4f} $" if s is not None
              else f"  {m}: Preis unbekannt")
    print(f"  zusammen: {summe:.4f} $")
    print("\nDatenschutz:")
    for zeile in v.DATENSCHUTZ:
        print(f"  - {zeile}")
    if args.dry_run:
        print("\n--dry-run: keine Anfragen gesendet.")
        for rel in pfade:
            print(f"  {rel}")
        return 0
    if not args.yes:
        print("\nNichts gesendet. Mit --yes bestaetigen, dass die Stichprobe an "
              "die API gehen darf.")
        return 1
    schluessel_laden(args.key_file, project)

    out.mkdir(exist_ok=True)
    docs: dict[str, VisionDoc] = {}
    laeufe: dict = {}
    for m in alle:
        print(f"\n{m} …")
        beginn = time.monotonic()
        doc, bericht = v.analysiere(
            pfade, modell=m, jobs=args.jobs,
            fortschritt=lambda f, g, rel: print(f"  [{f:>3}/{g}] {Path(rel).name}"))
        laeufe[m] = (bericht, time.monotonic() - beginn)
        docs[m] = doc
        v.speichern(doc, out / f"{m}.yaml")
        for w in bericht.warnungen:
            print(f"  ! {w}")

    (out / "vergleich.md").write_text(
        bericht_md(kandidaten, args.referenz, laeufe, docs, hoch, args.hochrechnen),
        encoding="utf-8")
    (out / "vergleich.html").write_text(
        bericht_html(alle, docs, hoch, out, project), encoding="utf-8")
    print(f"\nErgebnis: {out / 'vergleich.md'}\nSichtpruefung: {out / 'vergleich.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
