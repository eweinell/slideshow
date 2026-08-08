"""Feinschliff — ``overrides.yaml``.

``build`` erzeugt ``edit.yaml`` bei jedem Lauf neu. Die Reihenfolge ueberlebt
das in ``order.yaml``, die Kapitel in ``chapters.yaml``; alles uebrige — eine
laengere Standzeit, eine abgeschaltete Fahrt, ein getrimmter Clip, ein harter
Schnitt — hatte bis hierher keinen Ort ausser dem Erzeugnis. Wer ein Bild
nachreichte (``order --update``) und neu baute, verlor es; wer nicht neu baute,
bekam das Bild nicht in den Film. ``overrides.yaml`` loest genau diese Klemme.

Zwei Richtungen, und beide stehen hier:

**Hin** — :func:`resolve_media` und :func:`cut_seconds` uebersetzen die Datei in
das, was der Planer ohnehin versteht. ``build`` bekommt dadurch keine zweite
Sprache, nur fruehere Werte in denselben Feldern.

**Zurueck** — :func:`diff_edit` vergleicht eine von Hand geaenderte
``edit.yaml`` mit dem, was ``build`` gerade erzeugt haette, und schreibt die
Unterschiede als Eintraege heraus. Das ist der Weg fuer den, der schon in der
Edit-List gearbeitet hat, und der Grund, warum diese Datei niemand von Hand
anlegen muss.

Verankert wird ausschliesslich an **Medien-IDs** — nie an Segmentindizes. Ein
Index verschiebt sich beim naechsten eingefuegten Bild, eine Kennung nicht; es
ist dieselbe Entscheidung, die ``chapters.yaml`` (``before:``) und die
Ken-Burns-Richtung (``motion_key(src)``) schon getroffen haben.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import yaml

from .errors import SchemaError
# ``_Dumper`` ist privat und wird hier trotzdem gebraucht: die Werte des
# Feinschliffs sind dieselben verschachtelten Mappings wie in ``edit.yaml``
# (``kb: {z: [1.0, 1.2]}``), und sie sollen genauso aussehen.
from .models import (CutOverride, EditList, Manifest, MediaOverride, Overrides,
                     StillSegment, TitleSegment, XfadeSegment, _Dumper,
                     tief_verschmelzen, tiefe_differenz)

#: Der uebliche Ort. Wie ``chapters.yaml`` und ``order.yaml`` eine Datei, die
#: ``build`` von selbst findet, wenn sie im Projekt liegt.
OVERRIDES_NAME = "overrides.yaml"

#: Welche Felder an welchem Segmenttyp verglichen und uebertragen werden.
#: ``portrait`` steht nur beim Standbild, ``in``/``out``/``snap`` nur beim
#: Clip; die Zuordnung hier ist die einzige Stelle, die das weiss.
FELDER_STILL = ("beats", "dur", "hold", "snap_back", "portrait", "motion", "kb")
FELDER_CLIP = ("in_", "out", "snap", "snap_back")

_EPS = 1e-6


# --------------------------------------------------------------------------
# Hin: die Datei in Absicht uebersetzen
# --------------------------------------------------------------------------

def resolve_media(ov: Overrides, manifest: Manifest, *,
                  quelle: str = OVERRIDES_NAME) -> dict[str, MediaOverride]:
    """Prueft die Kennungen und liefert den Feinschliff je Medien-ID.

    Eine unbekannte ID ist ein **Fehler**, kein stiller Ignorierfall: sie ist
    ein Tippfehler oder eine umbenannte Datei, und in beiden Faellen bliebe die
    Absicht sonst wirkungslos in der Datei stehen. Dieselbe Regel wie in
    ``order.yaml`` (Prinzip 4).
    """
    bekannt = {m.id for m in manifest.media}
    unbekannt = [mid for mid in ov.media if mid not in bekannt]
    if unbekannt:
        gezeigt = ", ".join(sorted(unbekannt)[:8])
        rest = len(unbekannt) - 8
        beispiel = next((m.id for m in manifest.media), "img_001")
        raise SchemaError(
            f"{len(unbekannt)} Kennungen unter `media:` stehen nicht im Manifest: "
            f"{gezeigt}" + (f" (+{rest} weitere)" if rest > 0 else "")
            + f". IDs haengen am Dateinamen und stehen in manifest.json unter "
              f"media[].id, z. B. {beispiel!r} — wurde die Quelldatei umbenannt?",
            file=quelle)
    fehlend = [c.before for c in ov.cuts if c.before not in bekannt]
    if fehlend:
        raise SchemaError(
            f"{len(fehlend)} Kennungen unter `cuts:` stehen nicht im Manifest: "
            f"{', '.join(sorted(set(fehlend))[:8])}. Eine Blende wird an dem "
            f"Medium verankert, das *hinter* ihr steht.", file=quelle)
    return {mid: e for mid, e in ov.media.items() if not e.leer}


def cut_seconds(cut: CutOverride, beat_dauer: float | None, defaults) -> float | None:
    """Die gewuenschte Blendendauer in Sekunden — oder ``None`` fuer "unveraendert".

    ``beats`` gilt nur, wo ein Raster liegt; sonst dieselbe Ersatzrechnung wie
    in :func:`slideshow.build.plan_from_edit`, damit ein ``beats:`` in einer
    free-Region nicht wirkungslos bleibt.
    """
    if cut.dur is not None:
        return float(cut.dur)
    if cut.beats is None:
        return None
    if beat_dauer:
        return float(cut.beats) * beat_dauer
    return float(cut.beats) * defaults.still_seconds / max(1, defaults.beats_per_still)


# --------------------------------------------------------------------------
# Zurueck: aus einer von Hand geaenderten Edit-List lesen
# --------------------------------------------------------------------------

def diff_edit(frisch: EditList, hand: EditList,
              manifest: Manifest) -> tuple[Overrides, list[str]]:
    """Was steht in ``hand`` anders als in ``frisch``?

    ``frisch`` ist der Bau, den ``build`` **jetzt** liefern wuerde — mit allen
    Eingabedateien, die im Projekt liegen, den bereits vorhandenen Feinschliff
    eingeschlossen. Was danach noch abweicht, ist Handarbeit und wird zu einem
    Eintrag.

    Verglichen wird nach **Kennung**, nicht nach Position: zwischen den beiden
    Fassungen kann ein Bild dazugekommen oder weggefallen sein, und gerade dann
    braucht man diese Funktion. Was sich so nicht ausdruecken laesst — eine
    andere Reihenfolge, ein geaenderter Titeltext —, kommt als Meldung zurueck
    und nennt die Datei, in die es gehoert.
    """
    id_von_pfad = {m.cache_path: m.id for m in manifest.media if m.cache_path}
    meldungen: list[str] = []

    if abs(hand.fps - frisch.fps) > _EPS:
        meldungen.append(
            f"Die Handfassung laeuft mit {hand.fps:g} fps statt {frisch.fps:g} — das "
            f"gehoert an `build --fps {hand.fps:g}`, nicht in den Feinschliff.")
    if tuple(hand.size) != tuple(frisch.size):
        w, h = hand.size
        meldungen.append(
            f"Die Handfassung misst {w}x{h} statt "
            f"{frisch.size[0]}x{frisch.size[1]} — das gehoert an `build --size "
            f"{w}x{h}`.")

    h_folge, h_blende = _spuren(hand.segments, id_von_pfad)
    f_folge, f_blende = _spuren(frisch.segments, id_von_pfad)
    h_map, h_doppelt = _nach_schluessel(h_folge)
    f_map, _ = _nach_schluessel(f_folge)

    for schluessel in h_doppelt:
        meldungen.append(
            f"{_nennung(schluessel)} steht in der Handfassung mehrfach. Der "
            f"Feinschliff kennt ein Medium nur einmal — verglichen wird das erste "
            f"Vorkommen.")

    meldungen += _folgen_vergleichen([s for s, _ in h_folge], [s for s, _ in f_folge],
                                     h_map, f_map)

    media: dict[str, MediaOverride] = {}
    for schluessel, hs in h_map.items():
        fs = f_map.get(schluessel)
        if fs is None:
            continue
        if isinstance(hs, TitleSegment) or isinstance(fs, TitleSegment):
            meldungen += _titel_vergleichen(hs, fs)
            continue
        if type(hs) is not type(fs):
            continue
        felder = _felder_vergleichen(hs, fs)
        if felder:
            media[schluessel[1]] = MediaOverride.model_validate(felder)

    cuts: list[CutOverride] = []
    for schluessel in h_map:
        if schluessel not in f_map:
            continue
        cut, meldung = _blende_vergleichen(schluessel, h_blende.get(schluessel),
                                           f_blende.get(schluessel))
        if cut is not None:
            cuts.append(cut)
        if meldung:
            meldungen.append(meldung)

    defaults = tiefe_differenz(hand.defaults.model_dump(mode="json"),
                               frisch.defaults.model_dump(mode="json"))
    return (Overrides(defaults=defaults, media=media, cuts=cuts), meldungen)


def _spuren(segments: list, id_von_pfad: dict[str, str]
            ) -> tuple[list[tuple[tuple, object]], dict[tuple, XfadeSegment]]:
    """Die Segmentliste als Folge von Kennungen — und die Blende vor jeder.

    Die Uebergaenge sind eigene Segmente mit ``from``/``to`` als Indizes; hier
    interessiert nur, *vor welchem Medium* eine Blende sitzt. Genau das ueberlebt
    ein eingefuegtes Bild, waehrend die Indizes es nicht tun.
    """
    folge: list[tuple[tuple, object]] = []
    blende: dict[tuple, XfadeSegment] = {}
    offen: XfadeSegment | None = None
    for seg in segments:
        if isinstance(seg, XfadeSegment):
            offen = seg
            continue
        schluessel = _schluessel(seg, id_von_pfad)
        folge.append((schluessel, seg))
        if offen is not None and schluessel not in blende:
            blende[schluessel] = offen
        offen = None
    return (folge, blende)


def _schluessel(seg, id_von_pfad: dict[str, str]) -> tuple[str, str]:
    if isinstance(seg, TitleSegment):
        # Eine Folie hat kein ``src`` (der Pfad des Assets ergibt sich aus dem
        # Inhalt); ihre Ueberschrift ist das einzige Stabile an ihr.
        return ("titel", seg.title)
    return ("medium", id_von_pfad.get(seg.src, seg.src))


def _nennung(schluessel: tuple[str, str]) -> str:
    return (f"Die Titelfolie {schluessel[1]!r}" if schluessel[0] == "titel"
            else f"Das Medium {schluessel[1]!r}")


def _nach_schluessel(folge: list[tuple[tuple, object]]) -> tuple[dict, list[tuple]]:
    out: dict[tuple, object] = {}
    doppelt: list[tuple] = []
    for schluessel, seg in folge:
        if schluessel in out:
            if schluessel not in doppelt:
                doppelt.append(schluessel)
            continue
        out[schluessel] = seg
    return (out, doppelt)


def _folgen_vergleichen(hand: list[tuple], frisch: list[tuple],
                        h_map: dict, f_map: dict) -> list[str]:
    """Meldet, was sich mit dem Feinschliff **nicht** ausdruecken laesst.

    Reihenfolge, hinzugefuegte und entfernte Segmente gehoeren nach
    ``order.yaml`` bzw. ``chapters.yaml``. Sie hier stillschweigend zu
    uebergehen waere der teure Fehler: man sicherte den Feinschliff, baute neu
    und faende die Umsortierung von gestern nicht wieder.
    """
    meldungen: list[str] = []
    nur_hand = [s for s in h_map if s not in f_map]
    nur_frisch = [s for s in f_map if s not in h_map]
    if nur_hand:
        titel = [s for s in nur_hand if s[0] == "titel"]
        medien = [s for s in nur_hand if s[0] == "medium"]
        if medien:
            meldungen.append(
                f"{len(medien)} Segmente stehen nur in der Handfassung "
                f"({', '.join(s[1] for s in medien[:6])}). Ein von Hand eingesetztes "
                f"Medium gehoert nach order.yaml — dort ueberlebt es den Neubau.")
        if titel:
            meldungen.append(
                f"{len(titel)} Titelfolien stehen nur in der Handfassung "
                f"({', '.join(repr(s[1]) for s in titel[:4])}). Titel gehoeren nach "
                f"chapters.yaml.")
    if nur_frisch:
        medien = [s[1] for s in nur_frisch if s[0] == "medium"]
        if medien:
            meldungen.append(
                f"{len(medien)} Segmente stehen nur im Neubau "
                f"({', '.join(medien[:6])}) — in der Handfassung geloescht? "
                f"Weglassen ist eine Sache von order.yaml (`rest: drop`).")

    # Nur wenn beide dieselbe Menge tragen, ist ein Reihenfolgevergleich
    # ueberhaupt aussagekraeftig; sonst hat die Meldung oben es schon gesagt.
    if not nur_hand and not nur_frisch and hand != frisch:
        meldungen.append(
            "Die Reihenfolge weicht ab. Der Feinschliff kann sie nicht tragen — "
            "dafuer gibt es order.yaml (`slideshow order`), und sie ueberlebt "
            "dort jeden Neubau.")
    return meldungen


def _felder_vergleichen(hand, frisch) -> dict:
    """Die abweichenden Felder eines Segments als Eintrag.

    Nur Werte, die in der Handfassung **dastehen**: ein geloeschtes ``beats: 8``
    laesst sich als Feinschliff nicht ausdruecken (und meint ohnehin denselben
    Wert, den der Neubau von selbst einsetzt).
    """
    namen = FELDER_STILL if isinstance(hand, StillSegment) else FELDER_CLIP
    out: dict = {}
    for name in namen:
        h = getattr(hand, name, None)
        f = getattr(frisch, name, None)
        if h is None or _gleich(h, f):
            continue
        out[name] = h
    return out


def _gleich(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, (int, float)):
        return abs(a - float(b)) <= _EPS
    if hasattr(a, "model_dump") and hasattr(b, "model_dump"):
        return a.model_dump() == b.model_dump()
    return a == b


def _titel_vergleichen(hand, frisch) -> list[str]:
    if not isinstance(hand, TitleSegment) or not isinstance(frisch, TitleSegment):
        return []
    h = hand.model_dump(exclude_none=True)
    f = frisch.model_dump(exclude_none=True)
    anders = sorted({k for k in set(h) | set(f) if h.get(k) != f.get(k)})
    if not anders:
        return []
    return [f"Titelfolie {hand.title!r}: {', '.join(anders)} weicht ab. Titelfolien "
            f"kennt der Feinschliff nicht — sie stehen vollstaendig in "
            f"chapters.yaml und ueberleben dort den Neubau."]


def _blende_vergleichen(schluessel: tuple[str, str], hand: XfadeSegment | None,
                        frisch: XfadeSegment | None
                        ) -> tuple[CutOverride | None, str | None]:
    """Der Uebergang **vor** diesem Segment."""
    if hand is None and frisch is None:
        return (None, None)
    gleich_dauer = (hand is not None and frisch is not None
                    and _gleich(hand.dur, frisch.dur) and hand.beats == frisch.beats)
    gleich_modus = (hand.mode if hand else None) == (frisch.mode if frisch else None)
    if gleich_dauer and gleich_modus:
        return (None, None)

    if schluessel[0] == "titel":
        return (None,
                f"Die Blende vor der Titelfolie {schluessel[1]!r} weicht ab. Um eine "
                f"Folie herum stellen `defaults.title.xfade_in`, `xfade_out` und "
                f"`xfade_focus` die Laenge — als Faktor auf die uebliche Blende.")

    felder: dict = {"before": schluessel[1]}
    if hand is None:
        # In der Handfassung geloescht: der harte Schnitt ist eine Blende der
        # Laenge null, und nur so ueberlebt er den Neubau — ein fehlender
        # Eintrag hiesse "wie ueblich".
        felder["dur"] = 0.0
    else:
        if hand.dur is not None:
            felder["dur"] = round(float(hand.dur), 6)
        elif hand.beats is not None:
            felder["beats"] = hand.beats
        # Der Modus nur, wenn er wirklich etwas sagt: eine neu eingesetzte
        # Blende traegt ``dissolve``, weil das die Vorgabe ist, nicht weil
        # jemand sie gewaehlt haette.
        if not gleich_modus and (frisch is not None or hand.mode != "dissolve"):
            felder["mode"] = hand.mode
    return (CutOverride.model_validate(felder), None)


# --------------------------------------------------------------------------
# Zusammenlegen und schreiben
# --------------------------------------------------------------------------

def merge_overrides(alt: Overrides, neu: Overrides) -> Overrides:
    """``neu`` ueber ``alt`` legen, Eintrag fuer Eintrag und Feld fuer Feld.

    Zusammengelegt statt ersetzt, weil ``alt`` bereits in den Vergleich
    eingegangen ist: was dort steht, taucht in ``neu`` gar nicht mehr auf und
    duerfte nicht deshalb verschwinden.
    """
    media = dict(alt.media)
    for mid, eintrag in neu.media.items():
        vorher = media.get(mid)
        daten = ({} if vorher is None
                 else vorher.model_dump(by_alias=True, exclude_none=True))
        daten.update(eintrag.model_dump(by_alias=True, exclude_none=True))
        media[mid] = MediaOverride.model_validate(daten)

    cuts = {c.before: c for c in alt.cuts}
    cuts.update({c.before: c for c in neu.cuts})
    return Overrides(version=alt.version,
                     defaults=tief_verschmelzen(alt.defaults, neu.defaults),
                     media=media,
                     cuts=[cuts[k] for k in sorted(cuts)])


def dump_overrides_yaml(ov: Overrides, manifest: Manifest | None = None) -> str:
    """Die Datei schreiben — von Hand, nicht ueber ``yaml.dump``.

    Derselbe Grund wie bei ``order.yaml`` und ``chapters.yaml``: sie wird
    gelesen und weiterbearbeitet, und dafuer muss neben jeder Zeile stehen, um
    welches Bild es geht. Ein Dumper wirft die Kommentare weg.
    """
    def block(daten) -> str:
        return yaml.dump(daten, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                         width=200, default_flow_style=True).strip()

    quelle = {m.id: Path(m.path).name for m in manifest.media} if manifest else {}
    zeilen = [
        "# overrides.yaml — der Feinschliff. Wird von `slideshow build` eingelesen.",
        "#",
        "# `build` erzeugt edit.yaml bei jedem Lauf neu. Was hier steht, ueberlebt",
        "# das — wie die Reihenfolge in order.yaml und die Kapitel in chapters.yaml.",
        "# Verankert an Medien-IDs (manifest.json, media[].id) und nicht an",
        "# Segmentindizes: ein eingefuegtes Bild verschiebt jeden Index, keine ID.",
        "#",
        "# Erzeugt von `slideshow overrides` aus dem Vergleich einer von Hand",
        f"# geaenderten edit.yaml mit dem Neubau  ({_dt.date.today().isoformat()}).",
        "",
        "version: 1",
    ]

    if ov.defaults:
        zeilen += ["",
                   "# Fuer den ganzen Film. Dieselben Schluessel wie `defaults:` in",
                   "# edit.yaml; genannt wird nur, was abweicht.",
                   yaml.dump({"defaults": ov.defaults}, Dumper=_Dumper, sort_keys=False,
                             allow_unicode=True).rstrip()]

    if ov.media:
        zeilen += ["",
                   "# Einzelne Medien. Dieselben Schluessel wie am Segment in edit.yaml:",
                   "# `dur:`/`beats:` (Standzeit), `motion: none` (Bild steht still),",
                   "# `kb:` (Kamerafahrt im Einzelnen), `hold:`, `snap_back:`,",
                   "# `portrait:` — beim Clip `in:`/`out:`/`snap:`.",
                   "media:"]
        for mid in sorted(ov.media):
            daten = ov.media[mid].model_dump(by_alias=True, exclude_none=True)
            kommentar = f"   # {quelle[mid]}" if mid in quelle else ""
            zeilen.append(f"  {mid}: {block(daten)}{kommentar}")

    if ov.cuts:
        zeilen += ["",
                   "# Blenden, verankert am folgenden Medium. `dur: 0` ist der harte",
                   "# Schnitt; ohne Eintrag gilt die uebliche Laenge.",
                   "cuts:"]
        for cut in ov.cuts:
            daten = cut.model_dump(exclude_none=True)
            zeilen.append(f"  - {block(daten)}")

    if ov.leer:
        zeilen += ["",
                   "# Noch kein Feinschliff. Was in edit.yaml von Hand geaendert wird,",
                   "# holt `slideshow overrides` hier herein.",
                   "media: {}"]
    return "\n".join(zeilen) + "\n"
