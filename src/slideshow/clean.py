"""Cache aufraeumen (Pruefbericht 2026-11, AP2 Schritt d).

Bis hierhin loeschte kein Kommando eine Cache-Datei, ausser im Fehlerpfad.
Jeder geaenderte Segment-Key liess das alte Segment liegen, jede verworfene
Auswahl ihre normalisierten Bilder — ``london-2026`` hielt 9,4 GB Segmente neben
einem 8,6-GB-Master.

Zwei Wege:

* **automatisch** nach einem vollstaendigen Render: die Segmente, die der
  vorige volle Lauf *desselben Profils* brauchte und dieser nicht mehr,
  werden geloescht (:func:`remember_segments`). Grundlage ist eine Liste je
  Profil in ``cache/segments/``; ein Segment, das ein anderes Profil fuehrt
  (Vorschau neben Master), bleibt.
* **ausdruecklich** mit ``slideshow clean``: alles, was die aktuelle
  ``edit.yaml`` nicht referenziert (:func:`plan_clean`).

Was ``edit.yaml`` referenziert, wird nie geloescht. Ein Clip-Intermediate, das
groesser ist als sein Bereich, gehoert dazu — das schrumpft der naechste
Render von selbst.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .encoders import EncoderProfile
from .models import EditList, Manifest
from .paths import Project

log = logging.getLogger("slideshow.clean")

#: Endung der Segmentlisten in ``cache/segments/``.
LISTE = ".liste"

#: Die Arten, nach denen ``clean`` zaehlt und die sich einzeln waehlen lassen.
ARTEN = ("bilder", "clips", "ton", "titel", "segmente", "thumbs")


# --------------------------------------------------------------------------
# Segmentlisten je Profil
# --------------------------------------------------------------------------

def profile_list(project: Project, profile: EncoderProfile) -> Path:
    """Liste der Segmente, die der letzte volle Lauf dieses Profils brauchte.

    Groesse und Rate gehoeren in den Namen: ein Master in 1080p und einer in
    4K mit demselben Encoder sind zwei Profile, und keiner darf die Segmente
    des anderen fuer verwaist halten.
    """
    w, h = profile.size
    return project.segments / f"{profile.name}_{w}x{h}_{profile.fps:g}{LISTE}"


def _lies_liste(pfad: Path) -> set[str]:
    try:
        return {z.strip() for z in pfad.read_text(encoding="utf-8").splitlines()
                if z.strip()}
    except OSError:
        return set()


def all_listed(project: Project) -> set[str] | None:
    """Alle Segmentnamen aus allen Profillisten — ``None``, wenn es keine gibt.

    ``None`` heisst: kein Lauf hat je eine Liste geschrieben (Projekt aus der
    Zeit davor). Dann laesst sich nicht sagen, was verwaist ist, und ``clean``
    laesst die Segmente stehen, statt einen 90-Minuten-Render zu verwerfen.
    """
    if not project.segments.exists():
        return None
    listen = sorted(project.segments.glob(f"*{LISTE}"))
    if not listen:
        return None
    namen: set[str] = set()
    for liste in listen:
        namen |= _lies_liste(liste)
    return namen


def remember_segments(project: Project, profile: EncoderProfile, jobs, *,
                      complete: bool) -> tuple[int, int]:
    """Profilliste fortschreiben und verwaiste Segmente loeschen.

    ``complete`` nur fuer einen Lauf ueber die ganze Timeline: dann ersetzt die
    neue Liste die alte, und was nur in der alten stand und in keiner anderen
    Profilliste, wird geloescht. Ein Teillauf (``--range``) ergaenzt die Liste
    lediglich — er sieht die uebrigen Segmente nicht.

    Gibt Anzahl und Bytes der geloeschten Segmente zurueck.
    """
    pfad = profile_list(project, profile)
    alt = _lies_liste(pfad)
    neu = {j.out.name for j in jobs}
    if not complete:
        neu |= alt
    project.segments.mkdir(parents=True, exist_ok=True)
    pfad.write_text("".join(f"{n}\n" for n in sorted(neu)), encoding="utf-8")
    if not complete:
        return (0, 0)

    andere: set[str] = set()
    for liste in project.segments.glob(f"*{LISTE}"):
        if liste != pfad:
            andere |= _lies_liste(liste)
    anzahl = groesse = 0
    for name in sorted(alt - neu - andere):
        datei = project.segments / name
        try:
            groesse += datei.stat().st_size
            datei.unlink()
            anzahl += 1
        except OSError:
            continue
    if anzahl:
        log.info("%d verwaiste Segmente geloescht (%.2f GB)", anzahl, groesse / 1e9)
    return (anzahl, groesse)


# --------------------------------------------------------------------------
# slideshow clean
# --------------------------------------------------------------------------

@dataclass
class CleanPlan:
    """Was ``clean`` loeschen wuerde, je Art."""

    dateien: dict[str, list[Path]] = field(default_factory=lambda: {a: [] for a in ARTEN})
    #: Bilder, deren ``cache_path`` im Manifest danach geleert werden muss.
    bilder_ids: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)

    def anzahl(self, art: str) -> int:
        """Gezaehlt wird die Sache, nicht ihre ``.key``-Datei."""
        return sum(1 for p in self.dateien[art] if p.suffix != ".key")

    def bytes(self, art: str) -> int:
        summe = 0
        for p in self.dateien[art]:
            try:
                summe += p.stat().st_size
            except OSError:
                pass
        return summe


def _mit_key(pfad: Path) -> list[Path]:
    """Die Datei samt ihrer ``.key``-Datei, soweit vorhanden."""
    key = pfad.with_suffix(pfad.suffix + ".key")
    return [p for p in (pfad, key) if p.exists()]


def referenced(edit: EditList) -> tuple[set[str], set[str]]:
    """Die ``cache/``-Pfade, die ``edit.yaml`` nennt: Medien und Titelassets.

    Zu den Medien zaehlen die Hintergruende der Titelfolien — die Folie wird
    aus dem Original gebacken, aber ``bg_kind`` und ``build`` gehen ueber den
    Cache-Pfad, und eine geaenderte Ueberschrift backt neu.
    """
    from .models import TitleSegment
    from .preprocess import title_canvas
    from .titles import bg_kind, resolved, title_asset

    medien: set[str] = set()
    titel: set[str] = set()
    canvas = title_canvas(tuple(edit.size))
    for idx, seg in enumerate(edit.segments):
        if isinstance(seg, TitleSegment):
            r = resolved(edit.segments, idx)
            titel.add(title_asset(r, edit.defaults, canvas))
            if bg_kind(r.bg) == "image":
                medien.add(str(r.bg).replace("\\", "/"))
            continue
        src = getattr(seg, "src", None)
        if src:
            medien.add(str(src).replace("\\", "/"))
    return (medien, titel)


def plan_clean(project: Project, edit: EditList, manifest: Manifest, *,
               arten: tuple[str, ...] = ARTEN) -> CleanPlan:
    """Bestimmt, was weg kann. Loescht nichts."""
    from .sheet import THUMB_DIR

    plan = CleanPlan()
    medien, titel = referenced(edit)
    cache = project.cache

    for m in manifest.media:
        if m.kind == "image":
            if "bilder" in arten and m.cache_path and m.cache_path not in medien:
                dateien = _mit_key(project.abs(m.cache_path))
                if dateien:
                    plan.dateien["bilder"] += dateien
                    plan.bilder_ids.append(m.id)
            continue
        im_film = bool(m.cache_path) and m.cache_path in medien
        if "clips" in arten and m.cache_path and not im_film:
            plan.dateien["clips"] += _mit_key(project.abs(m.cache_path))
        if "ton" in arten and not im_film:
            plan.dateien["ton"] += _mit_key(cache / f"{m.id}.audio.flac")

    if "titel" in arten:
        for datei in sorted(cache.glob("title_*.jpg")):
            if project.rel(datei) not in titel:
                plan.dateien["titel"] += _mit_key(datei)

    if "thumbs" in arten and (cache / THUMB_DIR).exists():
        im_film_ids = {m.id for m in manifest.media if m.cache_path in medien}
        for datei in sorted((cache / THUMB_DIR).glob("*")):
            if datei.is_file() and datei.stem not in im_film_ids:
                plan.dateien["thumbs"].append(datei)

    if "segmente" in arten and project.segments.exists():
        gelistet = all_listed(project)
        if gelistet is None:
            plan.hinweise.append(
                "Segmente bleiben stehen: es gibt noch keine Segmentliste. Sie "
                "entsteht beim naechsten vollstaendigen `render`; danach weiss "
                "`clean`, welche Segmente ein Profil noch braucht.")
        else:
            for datei in sorted(project.segments.glob("seg_*")):
                if datei.name not in gelistet:
                    plan.dateien["segmente"].append(datei)
    return plan


def apply_clean(project: Project, plan: CleanPlan, manifest: Manifest) -> int:
    """Loescht die Dateien des Plans und leert ``cache_path`` der Bilder.

    Ohne das Leeren plante ``build`` ein Bild ein, dessen Datei fehlt, und
    ``render`` scheiterte an "Datei fehlt". So meldet ``build`` es als nicht
    vorverarbeitet, und ``preprocess`` holt es nach, falls es zurueckkommt.
    """
    geloescht = 0
    for dateien in plan.dateien.values():
        for datei in dateien:
            try:
                datei.unlink()
                geloescht += 1
            except FileNotFoundError:
                continue
    for mid in plan.bilder_ids:
        item = manifest.by_id(mid)
        if item is not None:
            item.cache_path = ""
    return geloescht
