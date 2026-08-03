"""Phase 3c — Edit-List erzeugen und aufloesen (Abschnitt 6.6).

Die Edit-List haelt **Absicht** (``beats:``, ``dur:``, ``snap:``), nicht
aufgeloeste Zeitstempel. Die absoluten Framegrenzen entstehen bei jedem Lauf
neu aus derselben deterministischen Funktion (:func:`plan_from_edit`). Das ist
Absicht:

* Prinzip 1 bleibt gewahrt — die Datei ist von Hand editierbar, und eine
  geaenderte ``beats:``-Zahl wirkt sich auch tatsaechlich aus. Waeren die
  Framegrenzen mitgespeichert, gaebe es zwei Wahrheiten, die auseinanderlaufen.
* Prinzip 3 bleibt gewahrt — *gerechnet* wird ausschliesslich in absoluten
  Frames auf der Master-Timeline (siehe :mod:`slideshow.planner`).

Wer die aufgeloeste Timeline sehen will, bekommt sie als Report bzw. in
``out/timeline.json``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import EDIT_VERSION
from .errors import SchemaError, SlideshowError
from .kenburns import plan_motion
from .models import (BeatMap, Chapter, ClipSegment, Defaults, EditList, KBSpec,
                     Manifest, MediaItem, Region, StillSegment, TitleSegment,
                     XfadeSegment)
from .paths import Project
from .planner import (Coverage, Intent, Plan, RenderSegment, apply_transitions,
                      coverage, default_transition_seconds, fit_regions_to,
                      material_seconds, plan_slots, resolve, standard_slot,
                      to_frame, to_time, validate_continuity, visible_span)
from .preprocess import title_canvas
from .probe import chronological
from .titles import bg_kind, reading_seconds, resolved, title_asset, title_kb

log = logging.getLogger("slideshow.build")


# --------------------------------------------------------------------------
# Erzeugen
# --------------------------------------------------------------------------

def build_edit_list(project: Project, manifest: Manifest, beatmap: BeatMap, *,
                    defaults: Defaults | None = None, fps: float | None = None,
                    size: tuple[int, int] = (3840, 2160),
                    order: list[str] | None = None,
                    order_notes: list[str] | None = None,
                    chapters: list[Chapter] | None = None
                    ) -> tuple[EditList, Plan, Coverage]:
    """Erzeugt ``edit.yaml`` aus Manifest und Regionenkarte.

    ``order`` ist die bereits **aufgeloeste** ID-Folge aus ``order.yaml``
    (:func:`slideshow.order.resolve_order`), ``order_notes`` sind deren
    Meldungen. Beides kommt fertig von aussen, weil nur dort Dateipfad und
    Zeilennummern bekannt sind — hier gaebe es fuer ein `rest: append` keine
    Stelle, auf die die Meldung zeigen koennte.
    """
    defaults = defaults or Defaults()
    fps = float(fps or manifest.fps_suggestion)
    regions = beatmap.regions
    if not regions:
        raise SlideshowError("Regionenkarte ist leer — `slideshow beats` zuerst laufen lassen.")

    media = chronological(manifest)
    if order:
        by_id = {m.id: m for m in manifest.media}
        # Frueher stand hier ``[by_id[i] for i in order if i in by_id]``. Das
        # uebersprang eine unbekannte ID wortlos — genau der stille
        # Ignorierfall, den Prinzip 4 ausschliesst. Die Pruefung steht auch in
        # ``resolve_order``; hier bleibt sie, weil ``build_edit_list`` direkt
        # aufrufbar ist und dann an keiner Datei haengt.
        unbekannt = [i for i in order if i not in by_id]
        if unbekannt:
            raise SchemaError(
                f"Die Reihenfolge nennt {len(unbekannt)} Medien-IDs, die es im "
                f"Manifest nicht gibt: {', '.join(unbekannt[:8])}"
                + (f" (+{len(unbekannt) - 8} weitere)" if len(unbekannt) > 8 else ""),
                path="order")
        media = [by_id[i] for i in order]

    intents: list[Intent] = []
    for m in media:
        if not m.cache_path:
            log.warning("%s hat kein Zwischenprodukt — `slideshow preprocess` fehlt?", m.id)
            continue
        if m.kind == "image":
            intents.append(Intent(kind="still", src=m.cache_path, index=len(intents)))
        else:
            info = m.clip
            available = (info.cache_duration or info.effective_duration) if info else 0.0
            intents.append(Intent(kind="clip", src=m.cache_path, index=len(intents),
                                  clip_in=(info.cache_offset if info else 0.0),
                                  clip_available=(info.cache_offset if info else 0.0) + available,
                                  snap="out"))
    if not intents:
        raise SlideshowError("Keine vorverarbeiteten Medien gefunden. "
                             "`slideshow preprocess` zuerst laufen lassen.")

    kapitel_warnungen = insert_titles(intents, chapters or [], media, defaults, size)

    # Ob eine Tonspur da ist, entscheidet die *Datei*, nicht die Dauer: die
    # Karte fuer ein Projekt ohne Ton traegt zwar eine Laenge, aber keinen Pfad.
    audio_file = manifest.audio.file or beatmap.audio.get("file", "")
    audio_seconds = float(beatmap.audio.get("duration") or manifest.audio.duration or 0.0) \
        if audio_file else 0.0
    # ``len(intents)`` schliesst die Titelfolien ein — genau deshalb werden sie
    # *vor* dieser Zeile eingesetzt. Eine Titelfolie ist kein Medium, belegt
    # aber einen Slot; zaehlte man sie nicht mit, waere die Materiallaenge je
    # Titel um einen Standard-Slot zu kurz, und ohne Tonspur deckte die
    # zugeschnittene Regionenkarte die Timeline nicht mehr ab.
    duration, hinweis = _timeline_length(regions, len(intents), defaults,
                                         audio_seconds=audio_seconds)
    regions = fit_regions_to(regions, duration)
    total_frames = to_frame(duration, fps)

    plan, lage_warnungen = plan_with_titles(regions, intents, defaults, fps=fps,
                                            total_frames=total_frames)
    explicit = None if defaults.xfade.auto else {}
    apply_transitions(plan, defaults, explicit=explicit)
    if defaults.xfade.auto:
        apply_transitions(plan, defaults, explicit=_title_transitions(plan, defaults))
    clamp_transitions_for_handles(plan, manifest)
    plan.warnings.extend(kapitel_warnungen + lage_warnungen
                         + chapter_placement_hints(plan))
    if hinweis:
        plan.warnings.insert(0, hinweis)
    for meldung in reversed(order_notes or []):
        # Ganz nach oben: dass Material hinten angehaengt wurde oder fehlt,
        # erklaert die Zahlen der Deckungsrechnung darunter.
        plan.warnings.insert(0, meldung)
    cov = coverage(plan, defaults)
    cov.audio_seconds = audio_seconds

    edit = EditList(
        version=EDIT_VERSION, fps=fps, size=tuple(size),
        audio={"file": audio_file,
               "duration": round(duration, 6),
               "regions": [_region_dict(r) for r in regions]},
        defaults=defaults,
        segments=_segments_from_plan(plan, defaults))
    return (edit, plan, cov)


def _timeline_length(regions: list[Region], n_media: int, defaults: Defaults, *,
                     audio_seconds: float) -> tuple[float, str]:
    """Laenge der Timeline bestimmen — und melden, wenn die Tonspur nachgibt.

    Die Musik gibt die Laufzeit vor, solange das Material sie bis auf eine
    Bildlaenge genau fuellt; dann faengt die uebliche Streckung des letzten
    Bildes den Rest ab, und der Film endet mit der Musik.

    Passt es *nicht*, gewinnt das Material. Sonst bleibt bei 14 Fotos unter
    einem 6:32-Stueck das letzte Bild ueber fuenf Minuten stehen, nur damit der
    Ton aufgeht — oder es fallen Bilder hinten herunter. Beides ist schlechter
    als eine gekuerzte bzw. stumm auslaufende Tonspur, und beides bricht nicht
    ab, sondern meldet sich.
    """
    if audio_seconds <= 0:
        laenge = material_seconds(regions, n_media, defaults) or regions[-1].end
        return (laenge, f"keine Tonspur — die Laufzeit ergibt sich aus dem Material "
                        f"({n_media} Medien, {laenge:.1f} s)")

    material = material_seconds(regions, n_media, defaults)
    if abs(material - audio_seconds) <= standard_slot(regions, defaults):
        return (audio_seconds, "")

    if material < audio_seconds:
        return (material, f"Material ({material:.1f} s) ist kuerzer als die Tonspur "
                          f"({audio_seconds:.1f} s) — der Ton wird auf die Filmlaenge "
                          f"abgeschnitten")
    return (material, f"Material ({material:.1f} s) ist laenger als die Tonspur "
                      f"({audio_seconds:.1f} s) — die restlichen Bilder laufen ohne Ton")


def _region_dict(r: Region) -> dict:
    d = {"type": r.type, "start": round(r.start, 6), "end": round(r.end, 6)}
    if r.type == "beat":
        d.update(bpm=r.bpm, offset=round(r.offset or 0.0, 6))
        if r.conf is not None:
            d["conf"] = round(r.conf, 4)
    elif r.reason:
        d["reason"] = r.reason
    return d


# --------------------------------------------------------------------------
# Titelfolien (docs/briefing-titelfolien.md)
#
# Der Planer weiss von Titeln nichts: eine Titelfolie ist fuer ihn ein Still
# mit generiertem ``src``. Alles, was sie besonders macht — Phrasenlage,
# Verhalten in langer Stille, Fokusblende —, wird hier in *gewoehnliche
# Absicht* uebersetzt und steht danach sichtbar in ``edit.yaml``. Wer eine
# dieser Rechnungen nicht mag, ueberschreibt sie in der Datei von Hand.
# --------------------------------------------------------------------------

#: Wie oft die Lage der Titel nachgerechnet wird. Jede Korrektur verschiebt
#: alles Folgende, also auch den naechsten Titel — deshalb ueberhaupt eine
#: Schleife. Zwei Durchgaenge genuegen in der Praxis; die Grenze verhindert
#: nur, dass zwei Titel sich gegenseitig hin- und herschieben.
_MAX_LAGE_PASSES = 4

_MONATE = ["Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"]


@dataclass
class _Lage:
    """Buchhaltung waehrend der Lagekorrektur.

    Zwei Dinge muessen ueber die Durchgaenge hinweg ueberleben. **Meldungen**
    sind nach (Thema, Titel) verschluesselt, damit ein spaeterer Durchgang seine
    eigene Aussage ueberschreibt statt sie zu verdoppeln — und damit die Meldung
    ueber eine Korrektur stehen bleibt, auch wenn der letzte Durchgang nichts
    mehr zu tun findet. **Ausgangswerte** braucht es, weil der Bericht sonst die
    letzte Zwischenstufe meldet ("von 9 auf 7") statt der tatsaechlichen
    Aenderung — und bei einer Korrektur, die sich unterwegs selbst aufhebt,
    ueberhaupt keine Aenderung zu melden ist.
    """

    meldungen: dict[tuple[str, str], str] = field(default_factory=dict)
    ausgang: dict[int, float | None] = field(default_factory=dict)

    def merke(self, intent: Intent) -> None:
        self.ausgang.setdefault(intent.index, intent.beats)

    def vorher(self, intent: Intent) -> float | None:
        return self.ausgang.get(intent.index, intent.beats)


def insert_titles(intents: list[Intent], chapters: list[Chapter],
                  media: list[MediaItem], defaults: Defaults,
                  size: tuple[int, int]) -> list[str]:
    """Setzt die Kapitel als Titel-Intents in die Medienfolge ein.

    Aufgeloest wird hier alles, was spaeter nicht mehr zu ermitteln ist:
    ``bg: auto`` auf das erste Bild des neuen Abschnitts, eine Medien-ID im
    ``bg:`` auf deren Cache-Pfad und ``subtitle: auto`` auf das Aufnahmedatum.
    Alles landet als konkreter Wert in ``edit.yaml`` — sichtbar und von Hand
    korrigierbar statt als Zauberei im Code.
    """
    warnungen: list[str] = []
    if not chapters:
        return warnungen

    pos_von_src = {i.src: p for p, i in enumerate(intents)}
    verwendet = [m for m in media if m.cache_path in pos_von_src]
    erster_tag = _erster_aufnahmetag(verwendet)
    # Nur Bilder: ein Clip taugt nicht als Standhintergrund — dieselbe Regel,
    # nach der ``resolve_bg`` bei ``auto`` das naechste *Standbild* sucht.
    bild_von_id = {m.id: m.cache_path for m in media
                   if m.cache_path and m.kind == "image"}

    aufgeloest: list[tuple[int, Chapter]] = []
    for kap in chapters:
        if kap.group is not None:
            # Hier ist die Reihenfolge nur noch eine Liste, die Gruppen sind
            # vergessen. Ein `group:` muss deshalb vorher aufgeloest sein
            # (`order.anchor_chapters`); kaeme es bis hierher durch, faende die
            # Folie stillschweigend keinen Platz.
            raise SchemaError(
                f"Kapitel {kap.title!r} traegt einen unaufgeloesten `group:`-Anker "
                f"({kap.group!r}). `slideshow.order.anchor_chapters` muss vor dem "
                f"Bauen laufen.", path="chapters")
        if kap.at is not None:
            aufgeloest.append((max(0, min(int(kap.at), len(verwendet))), kap))
            continue
        treffer = next((p for p, m in enumerate(verwendet) if m.id == kap.before), None)
        if treffer is None:
            raise SchemaError(
                f"Kapitel {kap.title!r} verweist auf die Medien-ID {kap.before!r}, "
                f"die es nicht gibt (oder die kein Zwischenprodukt hat). "
                f"IDs stehen im Manifest.", path="chapters")
        aufgeloest.append((treffer, kap))
    # Stabil nach Position: sonst haengt bei zwei Kapiteln an derselben Stelle
    # die Reihenfolge davon ab, wie die Datei geschrieben wurde.
    aufgeloest.sort(key=lambda x: x[0])
    warnungen += _chronologie_hinweise(aufgeloest, verwendet, erster_tag)

    for versatz, (pos, kap) in enumerate(aufgeloest):
        folgebild = next((m for m in verwendet[pos:] if m.kind == "image"), None)
        bg = kap.bg
        if bg == "auto":
            if folgebild is not None:
                bg = folgebild.cache_path
            else:
                bg = "none"
                warnungen.append(
                    f"Titel {kap.title!r} steht hinter allem Material — es gibt kein "
                    f"'naechstes Bild' als Hintergrund. Faellt auf `bg: none` "
                    f"(Text auf Schwarz) zurueck.")
        elif bg in bild_von_id:
            # In dieser Datei stehen Medien-IDs, keine Cache-Pfade — ein
            # bestimmtes Bild waehlt man deshalb ueber seine ID. In ``edit.yaml``
            # steht danach der Pfad: das ist die Kennung, unter der ein Bild im
            # ganzen Projekt auftritt, und die Fokusblende vergleicht sie.
            bg = bild_von_id[bg]
        elif bg_kind(bg) == "image" and bg not in bild_von_id.values():
            clip = any(m.kind == "clip" and bg in (m.id, m.cache_path) for m in media)
            beispiel = next(iter(bild_von_id), "img_001")
            raise SchemaError(
                (f"Kapitel {kap.title!r}: {bg!r} ist ein Clip. Als Hintergrund "
                 f"taugt nur ein Standbild — ein Standbild steht still."
                 if clip else
                 f"Kapitel {kap.title!r}: {bg!r} ist als Hintergrund weder eine "
                 f"Medien-ID noch der Cache-Pfad eines Bildes aus dem Manifest.")
                + f"  IDs stehen im Manifest, z. B. `bg: {beispiel}`; eine "
                  f'Farbflaeche waere `bg: "#1b2a3a"`.', path="chapters")
        subtitle = kap.subtitle
        if subtitle == "auto":
            subtitle = _auto_subtitle(folgebild, erster_tag)
            if subtitle is None:
                warnungen.append(
                    f"Titel {kap.title!r}: `subtitle: auto` braucht einen "
                    f"Aufnahmezeitpunkt, das folgende Bild hat keinen. Zweite Zeile "
                    f"bleibt leer.")

        seg = TitleSegment(title=kap.title, subtitle=subtitle, bg=bg,
                           beats=kap.beats, dur=kap.dur, style=kap.style,
                           motion=kap.motion, kb=kap.kb)
        # ``beats``/``dur`` bleiben hier bewusst **leer**. Welches der beiden
        # gilt, haengt an der Region, in der die Folie landet — und die steht
        # erst nach dem ersten Planen fest. Gaebe man ``beats`` schon jetzt
        # mit, scheiterte ``plan_slots`` an einem Titel, der in einer
        # free-Region beginnt ("`beats:` ist nur in einer beat-Region
        # gueltig"), bevor die Lagekorrektur ueberhaupt zum Zug kaeme. Der
        # Wunsch des Kapitels steht in ``seg`` und wird dort abgeholt.
        intents.insert(pos + versatz,
                       Intent(kind="still", src=title_asset(seg, defaults, title_canvas(size)),
                              kb=title_kb(seg, defaults), title=seg))

    # Die Positionen der nachfolgenden Intents haben sich verschoben; ``index``
    # zeigt in Fehlermeldungen auf das Segment und muss stimmen.
    for p, intent in enumerate(intents):
        intent.index = p
    return warnungen


def _erster_aufnahmetag(media: list[MediaItem]) -> _dt.date | None:
    """Tag 1 der Reise — Bezugspunkt des Tageszaehlers in ``subtitle: auto``."""
    zeiten = [m.capture_time for m in media if m.capture_time]
    return _dt.datetime.fromtimestamp(min(zeiten)).date() if zeiten else None


def _tag_nummer(ts: float, erster_tag: _dt.date) -> int:
    return (_dt.datetime.fromtimestamp(ts).date() - erster_tag).days + 1


def _chronologie_hinweise(aufgeloest: list[tuple[int, Chapter]],
                          verwendet: list[MediaItem],
                          erster_tag: _dt.date | None) -> list[str]:
    """Meldet ``subtitle: auto`` ueber einem Abschnitt aus mehreren Tagen.

    ``subtitle: auto`` bildet "Tag 11 · 24. Juli" aus dem Aufnahmezeitpunkt des
    *folgenden* Bildes. Solange die Abfolge chronologisch laeuft, ist das der
    Beginn des Abschnitts und damit richtig. Bei manueller Sortierung
    (``order.yaml``) steht ueber einem Block aus fuenf Reisetagen dann das Datum
    eines einzelnen davon — technisch korrekt, inhaltlich irrefuehrend.

    Gemeldet, nicht korrigiert: welches Datum ein thematischer Block tragen
    soll, weiss das Werkzeug nicht. Und gemessen wird die **Monotonie**, nicht
    das blosse Vorhandensein von ``order.yaml`` — wer die Datei nur benutzt, um
    drei Bilder zu tauschen, bekommt keine Warnung ueber etwas, das er nicht
    getan hat.
    """
    if erster_tag is None:
        return []
    zeiten = [m.capture_time for m in verwendet if m.capture_time]
    if len(zeiten) < 2 or all(a <= b for a, b in zip(zeiten, zeiten[1:])):
        return []

    hinweise: list[str] = []
    grenzen = [p for p, _ in aufgeloest] + [len(verwendet)]
    for k, (pos, kap) in enumerate(aufgeloest):
        if kap.subtitle != "auto":
            continue
        folgebild = next((m for m in verwendet[pos:] if m.kind == "image"), None)
        if folgebild is None or not folgebild.capture_time:
            continue
        tage = sorted({_tag_nummer(m.capture_time, erster_tag)
                       for m in verwendet[pos:grenzen[k + 1]] if m.capture_time})
        if len(tage) < 2:
            continue
        hinweise.append(
            f"Titel {kap.title!r}: die Reihenfolge ist nicht chronologisch, "
            f"`subtitle: auto` nimmt aber das Datum des folgenden Bildes "
            f"(Tag {_tag_nummer(folgebild.capture_time, erster_tag)}). Der Abschnitt "
            f"enthaelt Bilder von Tag {tage[0]} bis Tag {tage[-1]} — zweite Zeile von "
            f"Hand setzen oder mit `subtitle: null` weglassen.")
    return hinweise


def _auto_subtitle(item: MediaItem | None, erster_tag: _dt.date | None) -> str | None:
    """"Tag 11 · 24. Juli" aus dem Aufnahmezeitpunkt des folgenden Bildes."""
    if item is None or not item.capture_time:
        return None
    wann = _dt.datetime.fromtimestamp(item.capture_time)
    datum = f"{wann.day}. {_MONATE[wann.month - 1]}"
    if erster_tag is None:
        return datum
    tag = (wann.date() - erster_tag).days + 1
    return f"Tag {tag} · {datum}" if tag >= 1 else datum


def plan_with_titles(regions: list[Region], intents: list[Intent], defaults: Defaults,
                     *, fps: float, total_frames: int) -> tuple[Plan, list[str]]:
    """Planen, die Lage der Titel korrigieren, neu planen — bis es steht.

    Die Korrektur ist gewoehnliche Absicht (``beats:`` des Vorgaengers,
    ``dur:``/``snap_back:`` an der Folie selbst), keine Sonderregel im Planer.
    Nur deshalb bleibt ``planner.py`` von diesem Vorhaben unberuehrt.
    """
    def planen() -> Plan:
        return plan_slots(regions, intents, defaults, fps=fps, total_frames=total_frames)

    plan = planen()
    if not any(i.title is not None for i in intents):
        return (plan, [])

    lage = _Lage()
    for _ in range(_MAX_LAGE_PASSES):
        if not _adjust_titles(plan, defaults, lage):
            break
        plan = planen()
    else:
        lage.meldungen[("instabil", "")] = (
            f"Die Lage der Titelfolien ist nach {_MAX_LAGE_PASSES} Durchgaengen nicht "
            f"stabil — zwei Kapitel schieben sich gegenseitig. Die Folien stehen "
            f"trotzdem; `beats:` der Vorgaenger in edit.yaml von Hand nachziehen.")
    return (plan, list(lage.meldungen.values()))


def _adjust_titles(plan: Plan, defaults: Defaults,
                   lage: _Lage) -> bool:
    """Standzeit und Lage jeder Titelfolie an ihre Region anpassen."""
    veraendert = False
    for i, slot in enumerate(plan.slots):
        if slot.intent.title is None:
            continue
        region = plan.regions[slot.region_index]
        if region.type == "beat" and region.bpm:
            veraendert |= _titel_in_beatregion(plan, i, defaults, lage)
        else:
            veraendert |= _titel_in_freeregion(plan, i, defaults, lage)
    return veraendert


def _setze(intent: Intent, **felder) -> bool:
    """Absicht setzen und melden, ob sich etwas geaendert hat."""
    veraendert = False
    for name, wert in felder.items():
        alt = getattr(intent, name)
        gleich = (abs(alt - wert) < 1e-6 if isinstance(alt, float)
                  and isinstance(wert, float) else alt == wert)
        if not gleich:
            setattr(intent, name, wert)
            veraendert = True
    return veraendert


def _titel_in_beatregion(plan: Plan, i: int, defaults: Defaults,
                         lage: _Lage) -> bool:
    """Standzeit in Beats, und der Anfang auf einer Phrasengrenze."""
    slot = plan.slots[i]
    seg = slot.intent.title
    region = plan.regions[slot.region_index]
    beat = region.beat_duration()

    if seg.dur is not None:
        # Explizite Sekunden aus dem Kapitel gewinnen auch hier (Praezedenz 1).
        veraendert = _setze(slot.intent, beats=None, dur=seg.dur)
    else:
        wunsch = float(seg.beats if seg.beats is not None else defaults.title.beats)
        veraendert = _setze(slot.intent, beats=wunsch, dur=None)
        lese = reading_seconds(seg)
        if wunsch * beat < lese - 1e-6:
            lage.meldungen[("lesezeit", seg.title)] = (
                f"Titel {seg.title!r} steht {wunsch:g} Beats ({wunsch * beat:.1f} s), "
                f"die Lesezeit liegt bei {lese:.1f} s. `beats:` erhoehen.")

    return _phrasenlage(plan, i, defaults, lage) or veraendert


def _phrasenlage(plan: Plan, i: int, defaults: Defaults,
                 lage: _Lage) -> bool:
    """Den **Vorgaenger** so dehnen oder stauchen, dass der Titel auf die Eins faellt.

    Entscheidung 3c: die Ausrichtung wird als ``beats:`` des vorangehenden
    Bildes materialisiert. Der Planer fuehrt sie danach ohne jede Sonderregel
    aus, und in der Datei steht sichtbar, warum dieses eine Bild laenger steht.
    """
    slot = plan.slots[i]
    seg = slot.intent.title
    region = plan.regions[slot.region_index]
    if i == 0:
        return False                    # der Auftakt liegt per Definition richtig
    vor = plan.slots[i - 1]
    if vor.region_index != slot.region_index:
        # Eine Regionsgrenze ist per Konstruktion eine musikalische Grenze —
        # dort ist die Phrasenrechnung gegenstandslos, nicht fehlgeschlagen.
        return False
    if vor.intent.kind != "still":
        lage.meldungen[("phrase", seg.title)] = (
            f"Titel {seg.title!r} folgt auf einen Clip; dessen Laenge haengt am "
            f"Material und wird fuer die Phrasenlage nicht angetastet.")
        return False

    beat = region.beat_duration()
    phrase = float(defaults.title.phrase_beats) * beat
    offset = float(region.offset if region.offset is not None else region.start)
    start = to_time(slot.start_f, plan.fps)

    k = round((start - offset) / phrase)
    ziel = offset + k * phrase
    if abs(ziel - start) <= 0.5 / plan.fps:
        return False                    # liegt bereits auf der Eins
    if not (region.start - 1e-6 <= ziel <= region.end + 1e-6):
        lage.meldungen[("phrase", seg.title)] = (
            f"Titel {seg.title!r} hat keine Phrasengrenze in Reichweite — die "
            f"naechste laege ausserhalb der Region. Bleibt beim Standardverhalten.")
        return False

    vor_start = to_time(vor.start_f, plan.fps)
    k0 = math.ceil((vor_start - offset) / beat - 1e-6)
    neu = (ziel - (offset + k0 * beat)) / beat
    if neu < 1.0:
        lage.meldungen[("phrase", seg.title)] = (
            f"Titel {seg.title!r} liesse sich nur auf die Phrasengrenze ziehen, wenn "
            f"das Bild davor auf {neu:.1f} Beats schrumpfte. Bleibt beim "
            f"Standardverhalten; Kapitel ein Bild frueher oder spaeter ansetzen.")
        return False

    lage.merke(vor.intent)
    if not _setze(vor.intent, beats=round(neu, 6)):
        return False

    # Gemeldet wird der Weg vom **Ausgangswert**, nicht von der letzten
    # Zwischenstufe. Hebt ein spaeterer Durchgang die Korrektur wieder auf —
    # weil ein Titel davor bereits alles verschoben hat —, gibt es nichts zu
    # melden, und der stehengebliebene Satz aus dem ersten Durchgang muss weg.
    vorher = lage.vorher(vor.intent)
    ausgang = float(vorher if vorher is not None
                    else (region.beats_per_still or defaults.beats_per_still))
    if abs(ausgang - neu) < 1e-6:
        lage.meldungen.pop(("phrase", seg.title), None)
        return True
    lage.meldungen[("phrase", seg.title)] = (
        f"Titel {seg.title!r} beginnt bei {start:.2f} s, die Phrasengrenze liegt bei "
        f"{ziel:.2f} s — `beats:` des Vorgaengers von {ausgang:g} auf {neu:g} gesetzt.")
    return True


def _titel_in_freeregion(plan: Plan, i: int, defaults: Defaults,
                         lage: _Lage) -> bool:
    """In ``free``-Regionen gilt die **Standardlaenge der Bildanzeige**.

    Ob dort Musik laeuft, die sich nur nicht rastern liess, oder wirklich nichts
    zu hoeren ist, aendert daran nichts: die Folie steht so lange wie die Bilder
    um sie herum. Phrasen gibt es hier nicht.

    Genau einen Fall gibt es, in dem das ohne Zutun schiefgeht: eine *stille*
    Region ueber ``hold_seconds`` ist **ein** Slot (``_free_count`` liefert
    ``n = 1``), damit dort bewusst ein ruhiges Einzelbild stehen bleiben kann.
    Eine Titelfolie bekaeme dort die ganze Stille — zwanzig Sekunden Standbild
    mit "Malmoe" darauf. Und der naheliegende Rettungsweg ueber ``dur:`` fuehrt
    in dieselbe Falle zurueck, weil ``snap_back`` per Default aufrundet und die
    einzige Kante einer ``hold``-Region das Regionsende ist.
    """
    slot = plan.slots[i]
    seg = slot.intent.title
    region = plan.regions[slot.region_index]
    lese = reading_seconds(seg)

    if seg.dur is not None:
        return _setze(slot.intent, beats=None, dur=seg.dur)

    if seg.beats is not None:
        # `beats` gilt hier nicht, und stillschweigend zu ignorieren waere die
        # schlechteste Antwort: die Zahl steht sichtbar in chapters.yaml und
        # taete offenbar etwas.
        lage.meldungen[("beats-frei", seg.title)] = (
            f"Titel {seg.title!r} liegt in einer free-Region ohne Beat-Raster — "
            f"`beats: {seg.beats:g}` bleibt dort wirkungslos. Fuer eine feste "
            f"Standzeit `dur:` in Sekunden angeben.")

    if not slot.hold:
        if to_time(slot.frames, plan.fps) < lese - 1e-6:
            lage.meldungen[("kurz", seg.title)] = (
                f"Titel {seg.title!r} steht nur {to_time(slot.frames, plan.fps):.1f} s "
                f"(Region zu kurz), die Lesezeit liegt bei {lese:.1f} s. Kapitel eine "
                f"Region weiter ansetzen.")
        return _setze(slot.intent, beats=None, dur=None)

    standard = float(region.still_seconds or defaults.still_seconds)
    dauer = max(standard, lese)
    if dauer > standard + 1e-6:
        lage.meldungen[("stille", seg.title)] = (
            f"Titel {seg.title!r}: Lesezeit {lese:.1f} s liegt ueber der Standzeit "
            f"{standard:.1f} s — die Folie steht {dauer:.1f} s.")
    return _setze(slot.intent, beats=None, dur=dauer, snap_back=False)


def _ist_fokusblende(plan: Plan, i: int) -> bool:
    """Loest die Blende aus der Folie heraus auf **dasselbe Bild, scharf** auf?"""
    slot = plan.slots[i]
    if slot.intent.title is None or i + 1 >= len(plan.slots):
        return False
    folge = plan.slots[i + 1]
    return (folge.intent.title is None and folge.intent.kind == "still"
            and slot.intent.title.bg == folge.intent.src)


def _title_transitions(plan: Plan, defaults: Defaults) -> dict[int, float]:
    """Blendendauern aller Schnitte, mit eigener Choreografie um jede Folie.

    ``apply_transitions`` kennt nur alles-oder-nichts: ein ``explicit``-Eintrag
    fehlt heisst *keine Blende*, nicht *die uebliche*. Deshalb werden hier alle
    Schnitte aufgefuehrt und nur die um eine Titelfolie skaliert.
    """
    t = defaults.title
    explicit = {cut: default_transition_seconds(plan, cut, defaults)
                for cut in range(1, len(plan.slots))}
    for i, slot in enumerate(plan.slots):
        if slot.intent.title is None:
            continue
        if i in explicit:
            explicit[i] *= t.xfade_in
        if i + 1 in explicit:
            explicit[i + 1] *= t.xfade_focus if _ist_fokusblende(plan, i) else t.xfade_out
    return explicit


def _sichtbare_dauer(plan: Plan, i: int) -> float:
    vs, ve = visible_span(plan, i)
    return (ve - vs) / plan.fps


def _couple_focus_motion(plan: Plan, defaults: Defaults) -> None:
    """Die Fokusblende braucht eine ueber die Blende hinweg **stetige** Fahrt.

    Ohne sie wirkt die Aufloesung nicht wie ein Schaerfezug, sondern wie ein
    Schnitt zwischen zwei aehnlichen Bildern — das Schlechteste aus beiden
    Welten. Zoom und Bildmitte der Folie enden deshalb dort, wo die des
    Folgebildes beginnen; geschrieben wird beides explizit in die Datei, damit
    es sichtbar und korrigierbar bleibt.

    Die Folie zoomt dabei immer *hinein*. Ein Hinauszoom endete bei ``z = 1,0``,
    und das Folgebild muesste darunter weitermachen — dort ist der Ausschnitt
    aber bereits das ganze Bild. Nebengewinn: das Folgebild beginnt oberhalb von
    ``z = 1,0`` und hat damit von der ersten Sekunde an den vollen Spielraum des
    Bildrands, statt ihn sich erst zu erzoomen.
    """
    from .kenburns import zoom_from_duration

    kb = defaults.kb
    for i, slot in enumerate(plan.slots):
        if not _ist_fokusblende(plan, i):
            continue
        folge = plan.slots[i + 1]
        if slot.intent.kb is not None or folge.intent.kb is not None:
            # Von Hand gesetzt gewinnt — und ``motion: none`` ist genau das,
            # nur bequemer geschrieben: eine stillstehende Folie soll nicht
            # nachtraeglich eine gekoppelte Fahrt bekommen. Die laengere
            # Fokusblende bleibt, der Schaerfezug findet ohne Fahrt statt.
            continue

        d_titel = _sichtbare_dauer(plan, i)
        d_folge = _sichtbare_dauer(plan, i + 1)
        # Nur fuer die Schwenkrichtung; Zoom wird unten ohnehin erzwungen.
        m = plan_motion(slot.intent.src, d_titel, kb)
        z_titel = zoom_from_duration(d_titel, kb)
        z_folge = z_titel + (zoom_from_duration(d_folge, kb) - 1.0)

        weg = math.dist(m.c0, m.c1)
        richtung = ((m.c1[0] - m.c0[0]) / weg, (m.c1[1] - m.c0[1]) / weg) \
            if weg > 1e-9 else (0.0, 0.0)
        weg_folge = min(max(kb.pan_rate * d_folge, kb.pan_total[0]), kb.pan_total[1])
        # Derselbe Deckel wie in ``plan_motion``, nur ueber beide Segmente
        # gerechnet: die Fahrt faengt bereits ausgelenkt an, und was der
        # groesste Zoom hergibt, teilen sich Folie und Folgebild. Ohne das
        # stuende der Schwenk des Folgebilds gegen Ende in der Klemmung, waehrend
        # der Zoom weiterlaeuft — sichtbar als Fahrt, die auf halber Strecke
        # anhaelt.
        weg_folge = min(weg_folge, max(0.0, 0.5 - 1.0 / (2.0 * z_folge) - weg))
        ziel = (_klemme(m.c1[0] + richtung[0] * weg_folge),
                _klemme(m.c1[1] + richtung[1] * weg_folge))

        slot.intent.kb = KBSpec(z=(1.0, round(z_titel, 4)),
                                c=(round(m.c0[0], 4), round(m.c0[1], 4),
                                   round(m.c1[0], 4), round(m.c1[1], 4)))
        folge.intent.kb = KBSpec(z=(round(z_titel, 4), round(z_folge, 4)),
                                 c=(round(m.c1[0], 4), round(m.c1[1], 4),
                                    round(ziel[0], 4), round(ziel[1], 4)))


def _klemme(v: float) -> float:
    return max(0.0, min(1.0, v))


def chapter_placement_hints(plan: Plan) -> list[str]:
    """Schlaegt vor, ein Kapitel zu verschieben, wenn nebenan eine Zaesur liegt.

    Die Kapitel haengen an Medien-IDs, die Stille aber an der Zeitachse — beides
    trifft erst nach dem Planen aufeinander. Faellt ein Titel *knapp* neben eine
    Pause zwischen zwei Tracks oder eine Regionsgrenze, ist das schade: dort
    waere die Zaesur ohnehin, und die Folie muesste den Fluss gar nicht erst
    unterbrechen.

    Ein **Vorschlag im Bericht**, keine automatische Verschiebung. Welches Foto
    zu welcher Stadt gehoert, weiss das Werkzeug nicht — es verschoebe sonst
    eine Kapitelgrenze mitten in einen Ort hinein.
    """
    hinweise: list[str] = []
    kanten = [(r.start, r.quiet) for r in plan.regions[1:]]
    if not kanten:
        return hinweise

    for i, slot in enumerate(plan.slots):
        seg = slot.intent.title
        if seg is None:
            continue
        if i == 0:
            # Der Auftakt gehoert an den Anfang, nicht auf eine Zaesur — davor
            # gibt es nichts, wovon er sich absetzen koennte.
            continue
        if plan.regions[slot.region_index].quiet:
            continue                    # steht bereits in der Stille
        start = to_time(slot.start_f, plan.fps)
        # Bezug ist die Laenge eines *Bildes*, nicht die der Folie: eine
        # Titelfolie steht laenger als ein Foto, und "zwei Bilder spaeter" soll
        # heissen, dass zwei Fotos die Grenze wechseln.
        bezug = to_time(plan.slots[i - 1].frames if i else slot.frames, plan.fps)
        # Fenster: rund zwei Bildlaengen in jede Richtung. Weiter zu schauen
        # hiesse, eine Verschiebung vorzuschlagen, die den Abschnitt zerreisst.
        fenster = 2.0 * bezug
        nah = [(abs(k - start), k, quiet) for k, quiet in kanten
               if abs(k - start) <= fenster]
        if not nah:
            continue
        # Eine echte Stille ist der bessere Platz als eine blosse Regionsgrenze.
        nah.sort(key=lambda x: (not x[2], x[0]))
        abstand, kante, quiet = nah[0]
        if abstand <= 1.0 / plan.fps:
            continue                    # sitzt schon darauf
        bilder = max(1, round(abstand / max(1e-6, bezug)))
        richtung = "spaeter" if kante > start else "frueher"
        was = "eine Pause im Ton" if quiet else "eine Regionsgrenze"
        hinweise.append(
            f"Titel {seg.title!r} beginnt bei {start:.1f} s; bei {kante:.1f} s liegt "
            f"{was} — dort faellt die Zaesur mit dem Ton zusammen. Kapitel etwa "
            f"{bilder} {'Bild' if bilder == 1 else 'Bilder'} {richtung} ansetzen.")
    return hinweise


def check_title_phrases(plan: Plan, defaults: Defaults) -> list[str]:
    """Liegt noch jede Titelfolie auf ihrer Phrasengrenze?

    Die Ausrichtung ist als ``beats:`` des Vorgaengers materialisiert
    (Entscheidung 3c) und zerfaellt still, sobald jemand davor etwas aendert.
    Diese Pruefung ist deshalb nicht optional, sondern der Preis fuer den
    einfachen Planer.
    """
    hinweise: list[str] = []
    phrase_beats = float(defaults.title.phrase_beats)
    for i, slot in enumerate(plan.slots):
        seg = slot.intent.title
        if seg is None or i == 0:
            continue
        region = plan.regions[slot.region_index]
        if region.type != "beat" or not region.bpm:
            continue
        if plan.slots[i - 1].region_index != slot.region_index:
            continue
        phrase = phrase_beats * region.beat_duration()
        offset = float(region.offset if region.offset is not None else region.start)
        start = to_time(slot.start_f, plan.fps)
        versatz = start - (offset + round((start - offset) / phrase) * phrase)
        if abs(versatz) > 0.5 / plan.fps:
            hinweise.append(
                f"Titel {seg.title!r} beginnt {abs(versatz):.2f} s neben der "
                f"Phrasengrenze. Wurde davor etwas geaendert? `slideshow build` "
                f"richtet die Lage neu aus.")
    return hinweise


def _segments_from_plan(plan: Plan, defaults: Defaults) -> list:
    """Baut die Segmentliste inklusive der Uebergaenge als *eigene* Segmente.

    ``from``/``to`` sind Indizes in genau diese Liste. Der Uebergang steht
    zwischen seinen beiden Nachbarn, sodass die Datei auch beim Lesen von oben
    nach unten Sinn ergibt.
    """
    segments: list = []
    slot_to_index: list[int] = []

    # Erst hier, weil die Kopplung die *endgueltigen* Blendendauern braucht:
    # die Bewegung ist ueber die volle sichtbare Spanne definiert, und die
    # schliesst die halben Blenden ein.
    _couple_focus_motion(plan, defaults)

    for i, slot in enumerate(plan.slots):
        if i > 0 and plan.transitions[i] > 0:
            segments.append(XfadeSegment(**{
                "from": slot_to_index[i - 1], "to": len(segments) + 1,
                "dur": round(plan.transitions[i] / plan.fps, 6),
                "mode": plan.transition_modes.get(i, defaults.xfade.mode)}))
        slot_to_index.append(len(segments))
        segments.append(_segment_from_slot(plan, i, slot, defaults))
    return segments


def _segment_from_slot(plan: Plan, i: int, slot, defaults: Defaults):
    """Schreibt die **Absicht** eines Slots, nicht seine gemessene Dauer.

    Das ist der Unterschied zwischen einer Edit-List, die sich reproduzierbar
    laden laesst, und einer, die bei jedem Lauf wandert. Die tatsaechliche
    Dauer eines Slots weicht regelmaessig von der Absicht ab, weil der Planer
    Regeln anwendet: das erste Bild schluckt den Vorlauf (6.0), das letzte
    Bild einer Region wird an deren Ende geklemmt, das letzte Bild ueberhaupt
    wird auf die Timelinelaenge gestreckt. Schriebe man diese Dauern als
    ``beats:`` zurueck, wuerden die Regeln beim naechsten Laden ein *zweites*
    Mal angewandt — und die Timeline verschoebe sich bei jedem Roundtrip.
    """
    region = plan.regions[slot.region_index]
    intent = slot.intent
    if intent.kind == "clip":
        return ClipSegment(**{
            "src": intent.src, "in": round(slot.clip_in, 6),
            "out": round(slot.clip_out, 6), "snap": intent.snap})

    felder: dict = {"beats": None, "dur": None, "snap_back": None,
                    "hold": bool(slot.hold), "kb": intent.kb}
    if intent.dur is not None:
        felder["dur"] = round(intent.dur, 6)
    elif region.type == "beat" and region.bpm:
        # Explizit ausschreiben: so ist beim Lesen sofort klar, warum ein Bild
        # so lang steht, und die Zahl laesst sich direkt anfassen.
        felder["beats"] = intent.beats if intent.beats is not None else \
            (region.beats_per_still or defaults.beats_per_still)
    if intent.snap_back is not None:
        felder["snap_back"] = intent.snap_back

    if intent.title is not None:
        # Eine Titelfolie muss als Titelfolie zurueckkommen — sonst degradiert
        # sie beim Rundlauf zum gewoehnlichen Standbild, und in einer langen
        # Stille faellt genau dabei die Regel aus Entscheidung 3b weg.
        # ``update`` setzt *alle* Dauerfelder neu, damit ein ``beats:`` aus dem
        # Kapitel nicht stehen bleibt, wenn die Folie in einer free-Region
        # gelandet ist.
        return intent.title.model_copy(update=felder)
    return StillSegment(src=intent.src, **felder)


# --------------------------------------------------------------------------
# Aufloesen (von der Edit-List zurueck zur Timeline)
# --------------------------------------------------------------------------

def plan_from_edit(edit: EditList, manifest: Manifest | None = None) -> Plan:
    """Rekonstruiert die Timeline aus ``edit.yaml``.

    ``build`` und ``render`` gehen durch dieselbe Funktion; nur so liefern
    beide garantiert dieselben Framegrenzen.
    """
    regions = edit.regions
    if not regions:
        raise SchemaError("keine Regionen definiert", path="audio.regions")

    duration = float(edit.audio.get("duration") or regions[-1].end)
    total_frames = to_frame(duration, edit.fps)

    intents: list[Intent] = []
    slot_of_segment: dict[int, int] = {}
    explicit: dict[int, float] = {}
    modes: dict[int, str] = {}
    pending: list[tuple[int, XfadeSegment]] = []

    for idx, seg in enumerate(edit.segments):
        if isinstance(seg, XfadeSegment):
            pending.append((idx, seg))
            continue
        slot_of_segment[idx] = len(intents)
        if isinstance(seg, TitleSegment):
            # Der Planer sieht ein Standbild wie jedes andere; ``src`` ergibt
            # sich aus dem Inhalt der Folie, ohne Datei anzufassen.
            titel = resolved(edit.segments, idx)
            intents.append(Intent(
                kind="still", src=title_asset(titel, edit.defaults,
                                              title_canvas(tuple(edit.size))),
                index=idx, beats=seg.beats, dur=seg.dur, hold=seg.hold,
                snap_back=seg.snap_back, kb=title_kb(titel, edit.defaults),
                title=titel))
        elif isinstance(seg, StillSegment):
            intents.append(Intent(
                kind="still", src=seg.src, index=idx, beats=seg.beats, dur=seg.dur,
                hold=seg.hold, snap_back=seg.snap_back, kb=seg.kb,
                portrait=seg.portrait))
        else:
            available, offset = _clip_bounds(seg, manifest)
            intents.append(Intent(
                kind="clip", src=seg.src, index=idx, snap=seg.snap,
                snap_back=seg.snap_back, clip_in=seg.in_, clip_out=seg.out,
                clip_available=available if available else (seg.out or 0.0),
                dur=(seg.out - seg.in_) if seg.out is not None else None))
            if offset is not None and seg.in_ < offset - 1e-4:
                raise SchemaError(
                    f"in={seg.in_:.3f} s liegt vor dem Anfang des Intermediates "
                    f"({offset:.3f} s). Clip neu vorverarbeiten oder in erhoehen.",
                    path=f"segments[{idx}].in")

    plan = plan_slots(regions, intents, edit.defaults, fps=edit.fps,
                      total_frames=total_frames)

    for idx, seg in pending:
        a, b = seg.from_, seg.to
        for name, ref in (("from", a), ("to", b)):
            if ref not in slot_of_segment:
                raise SchemaError(
                    f"verweist auf Segment {ref}, das kein still/clip ist "
                    f"(oder nicht existiert)", path=f"segments[{idx}].{name}")
        sa, sb = slot_of_segment[a], slot_of_segment[b]
        if sb != sa + 1:
            raise SchemaError(
                f"Uebergang verbindet die Segmente {a} und {b}, die auf der "
                f"Timeline nicht benachbart sind", path=f"segments[{idx}]")
        if sb >= len(plan.slots):
            continue                     # Nachbar fiel aus der Timeline (Ueberdeckung)
        seconds = seg.dur
        if seconds is None:
            beats = seg.beats if seg.beats is not None else edit.defaults.xfade.beats
            region = plan.regions[plan.slots[sb].region_index]
            if region.type == "beat" and region.bpm:
                seconds = beats * region.beat_duration()
            else:
                seconds = beats * edit.defaults.still_seconds / \
                    max(1, edit.defaults.beats_per_still)
        explicit[sb] = float(seconds)
        modes[sb] = seg.mode

    plan.transition_modes = modes
    apply_transitions(plan, edit.defaults, explicit=explicit)
    if manifest is not None:
        clamp_transitions_for_handles(plan, manifest)
    return plan


def _clip_bounds(seg: ClipSegment, manifest: Manifest | None) -> tuple[float, float | None]:
    if manifest is None:
        return (0.0, None)
    item = manifest.by_cache_path(seg.src)
    if item is None or item.clip is None:
        return (0.0, None)
    info = item.clip
    return (info.cache_offset + (info.cache_duration or info.effective_duration),
            info.cache_offset)


# --------------------------------------------------------------------------
# Semantische Validierung (Abschnitt 11)
# --------------------------------------------------------------------------

def clamp_transitions_for_handles(plan: Plan, manifest: Manifest) -> None:
    """``build`` muss validieren, dass ``T/2`` <= Handle ist (8.2).

    Fuer Clip-Nachbarn liefert das Intermediate die Frames ausserhalb des
    exklusiven Anteils — aber nur, soweit die 1-s-Handles aus 5.2 reichen.
    Statt hart abzubrechen wird die Blende auf das Moegliche gekuerzt und
    gewarnt: eine kuerzere Blende ist immer noch ein brauchbarer Film.
    """
    fps = plan.fps
    for cut in range(1, len(plan.slots)):
        t = plan.transitions[cut]
        if t <= 0:
            continue
        half = t / 2 / fps
        limit = half
        for slot, side in ((plan.slots[cut - 1], "after"), (plan.slots[cut], "before")):
            if slot.intent.kind != "clip":
                continue
            item = manifest.by_cache_path(slot.intent.src)
            info = item.clip if item else None
            if info is None:
                continue
            start = info.cache_offset
            end = info.cache_offset + (info.cache_duration or info.effective_duration)
            handle = (end - slot.clip_out) if side == "after" else (slot.clip_in - start)
            limit = min(limit, max(0.0, handle))
        if limit < half - 1e-6:
            # Abrunden, damit die Blende sicher in die Handles passt, und auf
            # eine gerade Framezahl, damit T/2 exakt aufgeht.
            plan.transitions[cut] = max(0, min(t, int(limit * fps) * 2))
            plan.warnings.append(
                f"Blende an Schnitt {cut} von {t / fps:.3f} s auf "
                f"{plan.transitions[cut] / fps:.3f} s gekuerzt — die Handles des "
                f"angrenzenden Clips reichen nicht weiter (5.2: 1 s).")


def validate_edit(edit: EditList, manifest: Manifest | None = None) -> Plan:
    """Vollstaendige semantische Pruefung *vor* dem ersten Renderaufruf.

    Deckt Abnahmekriterium 14 ab: eine fehlerhafte ``edit.yaml`` fuehrt zu
    einer Fehlermeldung mit YAML-Pfad, bevor ffmpeg auch nur gestartet wird.
    """
    from .beats import validate_tiling

    regions = edit.regions
    duration = float(edit.audio.get("duration") or (regions[-1].end if regions else 0.0))
    validate_tiling(regions, duration)

    # `beats:` nur in Beat-Regionen — geprueft beim Planen, weil erst dort
    # feststeht, in welcher Region ein Segment landet.
    plan = plan_from_edit(edit, manifest)
    segments = resolve(plan)
    validate_continuity(segments, plan.total_frames)
    plan.warnings.extend(check_title_phrases(plan, edit.defaults))

    if manifest is not None:
        _validate_sources(edit, manifest)
    return plan


def _validate_sources(edit: EditList, manifest: Manifest) -> None:
    """Referenzierte Cache-Dateien muessen vorhanden sein."""
    known = {m.cache_path for m in manifest.media if m.cache_path}
    for idx, seg in enumerate(edit.segments):
        if isinstance(seg, TitleSegment):
            # Das Asset einer Titelfolie ist ein Erzeugnis, kein Material — es
            # steht nicht im Manifest. Der Hintergrund dagegen schon.
            bg = resolved(edit.segments, idx).bg
            if bg not in ("none", "") and not bg.startswith("#") and bg not in known:
                raise SchemaError(
                    f"{bg!r} steht nicht im Manifest. `slideshow preprocess` erneut "
                    f"laufen lassen oder den Pfad korrigieren.",
                    path=f"segments[{idx}].bg")
            continue
        src = getattr(seg, "src", None)
        if src and src not in known:
            raise SchemaError(
                f"{src!r} steht nicht im Manifest. `slideshow preprocess` erneut "
                f"laufen lassen oder den Pfad korrigieren.",
                path=f"segments[{idx}].src")


def check_sources_exist(project: Project, edit: EditList) -> None:
    missing: list[tuple[int, str]] = []
    for idx, seg in enumerate(edit.segments):
        if isinstance(seg, TitleSegment):
            # Ein fehlendes Titelasset ist kein fehlendes Material, sondern ein
            # nicht gelaufener Erzeugungsschritt — die Diagnose muss das sagen,
            # sonst sucht man nach einer Datei, die es nie gab.
            rel = title_asset(resolved(edit.segments, idx), edit.defaults,
                              title_canvas(tuple(edit.size)))
            if not project.abs(rel).exists():
                raise SchemaError(
                    f"Titelasset fehlt: {rel}. Es entsteht beim Rendern von selbst "
                    f"(`ensure_title_assets`); wer `render` umgeht, muss es "
                    f"erzeugen lassen.", path=f"segments[{idx}].title")
            continue
        src = getattr(seg, "src", None)
        if src and not project.abs(src).exists():
            missing.append((idx, src))
    if missing:
        idx, src = missing[0]
        raise SchemaError(
            f"Datei fehlt: {src}" + (f" (+{len(missing) - 1} weitere)"
                                     if len(missing) > 1 else ""),
            path=f"segments[{idx}].src")


# --------------------------------------------------------------------------
# Timeline-Export fuer Inspektion
# --------------------------------------------------------------------------

def timeline_json(plan: Plan, segments: list[RenderSegment]) -> dict:
    return {
        "fps": plan.fps,
        "total_frames": plan.total_frames,
        "duration": round(to_time(plan.total_frames, plan.fps), 6),
        "slots": [
            {"index": i, "src": s.intent.src, "kind": s.intent.kind,
             "region": s.region_index, "start_f": s.start_f, "end_f": s.end_f,
             "start": round(to_time(s.start_f, plan.fps), 6),
             "end": round(to_time(s.end_f, plan.fps), 6),
             "visible": list(visible_span(plan, i)),
             "hold": s.hold}
            for i, s in enumerate(plan.slots)],
        "transitions": plan.transitions,
        "segments": [
            {"index": s.index, "kind": s.kind, "start_f": s.start_f, "end_f": s.end_f,
             "frames": s.frames,
             "src": (s.slot.intent.src if s.slot else
                     f"{s.a.intent.src} -> {s.b.intent.src}" if s.a and s.b else "")}
            for s in segments],
    }


def write_timeline(project: Project, plan: Plan, segments: list[RenderSegment]) -> Path:
    project.out.mkdir(parents=True, exist_ok=True)
    p = project.out / "timeline.json"
    p.write_text(json.dumps(timeline_json(plan, segments), indent=1), encoding="utf-8")
    return p
