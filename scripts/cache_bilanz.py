"""Cache-Bilanz eines Projekts: was liegt in cache/, und was davon steht im Film.

Aufruf:  python scripts/cache_bilanz.py <projekt> [<projekt> ...]

Messgrundlage zu docs/pruefbericht-2026-11.md, Abschnitt 3 und AP2. Liest nur.
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

HANDLE = 1.0        # Sekunden je Seite, wie preprocess.clip_span


def bilanz(root: pathlib.Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    edit = yaml.safe_load((root / "edit.yaml").read_text(encoding="utf-8"))
    media = manifest["media"]
    segmente = edit["segments"]

    arten: dict[str, int] = {}
    for s in segmente:
        arten[s.get("type")] = arten.get(s.get("type"), 0) + 1
    print(f"== {root.name}: {len(media)} Medien, edit.yaml {arten}")

    # --- Clips ---------------------------------------------------------
    benutzt: dict[str, list[tuple[float, float | None]]] = {}
    for s in segmente:
        if s.get("type") == "clip":
            benutzt.setdefault(s["src"], []).append((s.get("in") or 0.0, s.get("out")))

    zeilen = []
    for m in media:
        if m.get("kind") != "clip" or not m.get("cache_path"):
            continue
        info = m["clip"]
        dauer = info.get("cache_duration") or info.get("duration") or 0.0
        datei = root / m["cache_path"]
        mb = datei.stat().st_size / 1e6 if datei.exists() else 0.0
        verwendet = sum((b if b is not None else dauer) - a
                        for a, b in benutzt.get(m["cache_path"], []))
        zeilen.append((m["id"], dauer, verwendet, mb))

    gesamt_mb = sum(z[3] for z in zeilen)
    gesamt_s = sum(z[1] for z in zeilen)
    film_s = sum(z[2] for z in zeilen)
    ungenutzt = sum(z[3] for z in zeilen if z[2] == 0)
    entbehrlich = sum(z[3] * (1 - min(1.0, (z[2] + 2 * HANDLE) / z[1]))
                      for z in zeilen if z[1] > 0 and z[2] > 0) + ungenutzt
    print(f"   Clips: {len(zeilen)} Intermediates, {gesamt_s:.0f} s, "
          f"{gesamt_mb / 1e3:.1f} GB ({gesamt_mb / max(gesamt_s, 1):.0f} MB/s)")
    print(f"   im Film: {sum(1 for z in zeilen if z[2] > 0)} Clips, {film_s:.0f} s "
          f"= {100 * film_s / max(gesamt_s, 1):.0f} % des Materials")
    print(f"   gar nicht im Film: {ungenutzt / 1e3:.1f} GB; entbehrlich mit "
          f"{HANDLE:g} s Handles: {entbehrlich / 1e3:.1f} GB")
    for z in sorted(zeilen, key=lambda z: -z[3])[:5]:
        print(f"     {z[0]:<34} {z[1]:6.1f} s, {z[2]:5.1f} s verwendet, {z[3] / 1e3:5.1f} GB")
    _bereiche_aus_dem_plan(root, gesamt_mb / max(gesamt_s, 1))

    # --- Bilder --------------------------------------------------------
    im_film = {s["src"] for s in segmente if s.get("type") == "still"}
    bilder = [(m["cache_path"], root / m["cache_path"]) for m in media
              if m.get("kind") == "image" and m.get("cache_path")]
    vorhanden = [(rel, p.stat().st_size / 1e6) for rel, p in bilder if p.exists()]
    b_mb = sum(mb for _rel, mb in vorhanden)
    b_frei = sum(mb for rel, mb in vorhanden if rel not in im_film)
    print(f"   Bilder: {len(vorhanden)} normalisiert, {b_mb / 1e3:.1f} GB "
          f"({b_mb / max(len(vorhanden), 1):.1f} MB je Bild); nicht im Film: "
          f"{sum(1 for rel, _ in vorhanden if rel not in im_film)} Bilder, "
          f"{b_frei / 1e3:.1f} GB")

    # --- Segmente und Rest ----------------------------------------------
    seg = list((root / "cache" / "segments").glob("*")) \
        if (root / "cache" / "segments").exists() else []
    s_mb = sum(p.stat().st_size for p in seg if p.is_file()) / 1e6
    alles = sum(p.stat().st_size for p in (root / "cache").rglob("*") if p.is_file()) / 1e6
    print(f"   Segmente: {len(seg)} Dateien, {s_mb / 1e3:.1f} GB "
          f"(Plan: {sum(1 for s in segmente)} Eintraege in edit.yaml)")
    print(f"   Cache gesamt: {alles / 1e3:.1f} GB")


def _bereiche_aus_dem_plan(root: pathlib.Path, mb_je_s: float) -> None:
    """Was die Intermediates seit AP2 belegen: die Bereiche, die
    ``ensure_clip_intermediates`` aus dem Plan der aktuellen edit.yaml waehlt.

    Gerechnet, nicht erzeugt — mit der gemessenen Rate der vorhandenen Dateien.
    Daneben die Schranke aus AP2/A1: das 1,3-Fache von "verwendete Sekunden +
    2 s je Clip".
    """
    try:
        from slideshow.build import plan_from_edit
        from slideshow.models import EditList, Manifest
        from slideshow.preprocess import clip_bereiche
    except ImportError:
        print("   (Bereiche nach AP2: slideshow nicht importierbar)")
        return
    edit = EditList.load(root / "edit.yaml")
    manifest = Manifest.load(root / "manifest.json")
    plan = plan_from_edit(edit, manifest)
    bereiche = clip_bereiche(plan, manifest)
    # Gezeigte Sekunden, nicht ``out - in``: ein Slot am Filmende oder vor
    # einem Raster kann kuerzer sein als sein ``out`` (london-2026, C0198:
    # out 34,4 s, gezeigt 4,1 s), und gelesen wird nur, was gezeigt wird.
    verwendet = sum(s.frames / plan.fps for s in plan.slots if s.intent.kind == "clip")
    bereich_s = sum(hi - lo for lo, hi in bereiche.values())
    schranke_s = 1.3 * (verwendet + 2.0 * len(bereiche))
    print(f"   nach AP2: {len(bereiche)} Intermediates, {bereich_s:.0f} s "
          f"(verwendet {verwendet:.0f} s) = {bereich_s * mb_je_s / 1e3:.1f} GB; "
          f"A1-Schranke {schranke_s:.0f} s = {schranke_s * mb_je_s / 1e3:.1f} GB "
          f"-> {'erfuellt' if bereich_s <= schranke_s else 'VERFEHLT'}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for arg in sys.argv[1:]:
        bilanz(pathlib.Path(arg))
