"""Manuelle Reihenfolge — ``order.yaml``
(``docs/briefing-manuelle-reihenfolge.md``, Stufen 1–2).

Die Abfolge des Films kommt sonst aus :func:`slideshow.probe.chronological`.
Fuer einen Film, der **thematisch** erzaehlt, ist das falsch, und die Handarbeit
in ``edit.yaml`` stirbt beim naechsten ``build``. ``order.yaml`` ist deshalb
eine eigene *Eingabe*-Datei nach dem Muster von ``chapters.yaml``: an
Medien-IDs verankert, von ``build`` gelesen und nie geschrieben.

Dieses Modul ist reine Rechnung ohne Datei-I/O bis auf :func:`load_order` —
dieselbe Aufteilung wie in :mod:`slideshow.titles`.

Drei Aufgaben, in dieser Reihenfolge im Text:

**Aufloesen.** Im Kern eine Zeile (``[by_id[i] for i in ids]``); alles andere
sind die drei Faelle, in denen genau das stillschweigend das Falsche taete —
eine **unbekannte** ID (Tippfehler oder umbenannte Datei), eine **doppelte**
(sie macht die Kapitelverankerung mehrdeutig, Entscheidung 4) und **nicht
genanntes** Material (der teuerste Fehler dieser Datei, weil man eine Stunde
rendert und hinterher nachzaehlt, Entscheidung 3).

**Erzeugen und Nachpflegen.** Niemand tippt 90 Medien-IDs ab. Die Datei ist ein
*Formular* — vorbelegt, gruppiert und mit dem Kontext kommentiert, den man zum
Sortieren braucht (Entscheidung 7). ``update_order_text`` arbeitet deshalb auf
dem Quelltext und nicht auf dem Modell: die eigenen Kommentare sind hier keine
Zierde, sondern die Auswahl.

**Verankern.** ``group:`` in ``chapters.yaml`` zeigt auf einen Block dieser
Datei und wird hier zu einem ``before:`` aufgeloest (Entscheidung 5) — die
einzige Stelle, die von der Kopplung der beiden Dateien weiss.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from .chapters import (JUMP_KM, GruppenAnker, day_label, distance_km,
                       first_day)
from .errors import SchemaError
from .models import Chapter, Manifest, MediaItem, OrderList
from .probe import chronological, effective_capture_time

#: Wie viele IDs eine Meldung auffuehrt, bevor sie zaehlt statt aufzulisten.
#: Bei 90 Bildern ist eine vollstaendige Liste keine Fehlermeldung mehr,
#: sondern eine zweite Datei.
MAX_GENANNT = 8

#: Gruppierung der erzeugten Datei.
BY_CHOICES = ("day", "place", "none")


# --------------------------------------------------------------------------
# Zeilennummern
# --------------------------------------------------------------------------

#: Eine Medien-ID als eigenstaendiges Wort in einer Listenzeile — ``- img_042``
#: ebenso wie ``[img_042, img_043]``. Ein ``name: ankunft`` faellt nicht
#: darunter, weil dort ein Doppelpunkt folgt.
_ITEM_RE = re.compile(r"(?:^|[-\[,\s])\s*([A-Za-z0-9_-]+)\s*(?=[,\]\s]|$)")


def item_lines(text: str) -> dict[str, list[int]]:
    """Bildet jede Medien-ID auf die Zeilen ab, in denen sie steht.

    Der ``_LineLoader`` in :mod:`slideshow.models` merkt sich Zeilen nur fuer
    *Mappings*; die Eintraege hier sind blosse Strings in einer Liste und haben
    deshalb keine. Fuer eine flache Liste aus 90 IDs waere die Zeile des
    umschliessenden Mappings aber wertlos — sie zeigte auf Zeile 1.

    Gelesen wird darum der Quelltext. Das ist eine **Anreicherung**, keine
    Wahrheit: die Meldung stimmt auch ohne Treffer, sie zeigt dann nur nicht
    auf die Zeile. Kommentare bleiben aussen vor, damit ein erlaeuterndes
    ``# statt img_042`` nicht die falsche Stelle nennt.
    """
    out: dict[str, list[int]] = {}
    for nr, zeile in enumerate(text.splitlines(), start=1):
        for treffer in _ITEM_RE.finditer(zeile.split("#", 1)[0]):
            out.setdefault(treffer.group(1), []).append(nr)
    return out


def mentioned_ids(text: str) -> set[str]:
    """Alle IDs, die im Quelltext **irgendwo** stehen — auch in Kommentaren.

    Die Gegenfrage zu :func:`item_lines`, und sie wird fuer ``--update``
    gebraucht: eine auskommentierte Zeile ist bei ``rest: drop`` kein Ueberbleibsel,
    sondern die Auswahl — dieses Bild bleibt bewusst draussen. Wuerde ``--update``
    nur die *gelisteten* IDs kennen, boete es jedes abgewaehlte Foto bei jedem Lauf
    erneut als "neu" an, und die Abwahl waere nach dem dritten Mal unauffindbar.
    """
    return {t.group(1) for zeile in text.splitlines()
            for t in _ITEM_RE.finditer(zeile.replace("#", " "))}


def load_order(path: Path) -> tuple[OrderList, dict[str, list[int]]]:
    """``order.yaml`` laden — samt der Zeilen, in denen die IDs stehen."""
    p = Path(path)
    olist = OrderList.load(p)
    return (olist, item_lines(p.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# Aufloesen
# --------------------------------------------------------------------------

def resolve_order(manifest: Manifest, olist: OrderList, *, quelle: str = "order.yaml",
                  zeilen: dict[str, list[int]] | None = None
                  ) -> tuple[list[str], list[str]]:
    """Die Datei zur endgueltigen ID-Folge aufloesen.

    Liefert die Folge und die **Meldungen** dazu — die gehen als Warnungen in
    den Bericht, damit ein ``rest: append`` oder ``rest: drop`` nicht nur in der
    Datei steht, sondern bei jedem Lauf sichtbar wird.
    """
    zeilen = zeilen or {}
    bekannt = {m.id for m in manifest.media}
    # Im Fliesstext nur der Dateiname: ``quelle`` ist der volle Pfad, damit die
    # Fehlermeldung ihn als Ort fuehren kann — zweimal ausgeschrieben macht er
    # aus einer Warnung im Bericht drei Zeilen Pfad.
    name = Path(quelle).name

    ids: list[str] = []
    gesehen: dict[str, str] = {}                 # ID -> Gruppe des ersten Vorkommens
    for gruppe in olist.blocks:
        for mid in gruppe.items:
            if mid not in bekannt:
                raise SchemaError(_unbekannt(mid, gruppe.name, manifest), file=quelle,
                                  line=_zeile(zeilen, mid))
            if mid in gesehen:
                raise SchemaError(_doppelt(mid, gesehen[mid], gruppe.name, zeilen),
                                  file=quelle, line=_zeile(zeilen, mid, letzte=True))
            gesehen[mid] = gruppe.name
            ids.append(mid)

    meldungen: list[str] = []
    fehlend = [m.id for m in chronological(manifest) if m.id not in gesehen]
    if fehlend:
        if olist.rest == "error":
            raise SchemaError(_fehlend(fehlend, name), file=quelle)
        # Nicht in die Hinweise, die `--force` ausblendet: ein vergessenes
        # `append` haengt unsortiertes Material ans Filmende, und diese Zeile
        # ist die einzige Warnung davor.
        wohin = ("laufen hinten chronologisch mit" if olist.rest == "append"
                 else "bleiben weg")
        meldungen.append(f"{len(fehlend)} Medien stehen nicht in {name} und {wohin} "
                         f"(`rest: {olist.rest}`): {_liste(fehlend)}")
        if olist.rest == "append":
            ids.extend(fehlend)

    if not ids:
        raise SchemaError(
            f"{name} nennt kein einziges Medium. Ohne Eintraege gaebe es keinen "
            f"Film — die Datei loeschen, um chronologisch zu bauen, oder mit "
            f"`slideshow order` neu erzeugen.", file=quelle)
    return (ids, meldungen)


def _zeile(zeilen: dict[str, list[int]], mid: str, *, letzte: bool = False) -> int | None:
    treffer = zeilen.get(mid)
    if not treffer:
        return None
    return treffer[-1] if letzte else treffer[0]


def _liste(ids: list[str]) -> str:
    gezeigt = ", ".join(ids[:MAX_GENANNT])
    rest = len(ids) - MAX_GENANNT
    return gezeigt + (f" (+{rest} weitere)" if rest > 0 else "")


def _unbekannt(mid: str, gruppe: str, manifest: Manifest) -> str:
    wo = f" (Gruppe {gruppe!r})" if gruppe else ""
    beispiel = next((m.id for m in manifest.media), "img_001")
    return (f"{mid!r}{wo} ist keine Medien-ID aus dem Manifest. IDs haengen am "
            f"Dateinamen und stehen in manifest.json unter media[].id, z. B. "
            f"{beispiel!r} — wurde die Quelldatei umbenannt oder das Material neu "
            f"erfasst? `slideshow order --update` pflegt die Datei nach, ohne die "
            f"Sortierung zu verlieren.")


def _doppelt(mid: str, erste_gruppe: str, gruppe: str,
             zeilen: dict[str, list[int]]) -> str:
    treffer = zeilen.get(mid) or []
    wo = f" (Zeilen {', '.join(str(z) for z in treffer)})" if len(treffer) > 1 else ""
    gruppen = (f" — erst in {erste_gruppe!r}, dann in {gruppe!r}"
               if erste_gruppe and gruppe and erste_gruppe != gruppe else "")
    return (f"{mid!r} steht zweimal in der Reihenfolge{wo}{gruppen}. Die Datei "
            f"beschreibt eine Permutation des Materials, und die kennt kein Bild "
            f"zweimal: `before:` in chapters.yaml traefe sonst stillschweigend das "
            f"erste Vorkommen. Eine bewusste Wiederholung — dasselbe Bild als "
            f"Klammer am Anfang und am Ende — bleibt als Handgriff in edit.yaml "
            f"moeglich.")


def _fehlend(fehlend: list[str], quelle: str) -> str:
    eintraege = "\n".join(f"  - {mid}" for mid in fehlend[:MAX_GENANNT])
    rest = len(fehlend) - MAX_GENANNT
    if rest > 0:
        eintraege += f"\n  … (+{rest} weitere)"
    return (f"{len(fehlend)} Medien stehen nicht in {quelle}. Entweder eintragen, "
            f"oder `rest: append` (hinten anhaengen) bzw. `rest: drop` (weglassen) "
            f"setzen:\n{eintraege}")


# --------------------------------------------------------------------------
# Gruppieren
#
# Vorbelegt wird nicht flach, sondern nach Tagen bzw. Orten. Eine
# vorgruppierte Datei ist der brauchbarste Ausgangspunkt fuer eine thematische
# Umsortierung: man sieht, woher ein Bild kommt, waehrend man es woandershin
# schiebt. Die Signale sind dieselben wie bei ``slideshow chapters`` — hier
# werden sie nur anders ausgegeben.
# --------------------------------------------------------------------------

class Block:
    """Ein vorgeschlagener Block: Name, Medien, Begruendung der Grenze."""

    def __init__(self, name: str, grund: str = "") -> None:
        self.name = name
        self.grund = grund
        self.items: list[MediaItem] = []


def group_media(manifest: Manifest, *, by: str = "day") -> list[Block]:
    """Teilt die chronologische Folge in Bloecke."""
    if by not in BY_CHOICES:
        raise SchemaError(f"unbekannte Gruppierung {by!r} (erwartet: "
                          f"{', '.join(BY_CHOICES)})")
    reihe = chronological(manifest)
    if not reihe:
        return []
    offsets = manifest.clock_offsets
    if by == "none":
        block = Block("alle")
        block.items = list(reihe)
        return [block]
    return _nach_tagen(reihe, offsets) if by == "day" else _nach_orten(reihe, offsets)


def _nach_tagen(reihe: list[MediaItem], offsets: dict[str, float]) -> list[Block]:
    """Kalendertag, nicht Zeitluecke.

    Eine Aufnahme um 23:50 und eine um 00:10 liegen 20 Minuten auseinander und
    trotzdem an verschiedenen Tagen; eine Luecken-Heuristik traefe das nicht.
    Und nur mit dem Kalendertag heisst der Block ``tag-2`` auch wirklich, was
    ``subtitle: auto`` spaeter als "Tag 2" ausschreibt.
    """
    tag_eins = first_day(reihe, offsets)
    bloecke: list[Block] = []
    ohne = Block("ohne-datum", "kein Aufnahmezeitpunkt — von Hand einsortieren")
    letzter: _dt.date | None = None
    for m in reihe:
        ts = effective_capture_time(m, offsets)
        if ts is None or m.time_source == "none":
            ohne.items.append(m)
            continue
        tag = _dt.datetime.fromtimestamp(ts).date()
        if tag != letzter:
            nummer = (tag - tag_eins).days + 1 if tag_eins else len(bloecke) + 1
            bloecke.append(Block(f"tag-{nummer}", day_label(ts, tag_eins)))
            letzter = tag
        bloecke[-1].items.append(m)
    return bloecke + ([ohne] if ohne.items else [])


def _nach_orten(reihe: list[MediaItem], offsets: dict[str, float]) -> list[Block]:
    """Ortscluster ueber GPS. Material ohne Fix bleibt beim laufenden Block —
    es wurde zwischen zwei verorteten Aufnahmen gemacht und gehoert dorthin."""
    tag_eins = first_day(reihe, offsets)
    bloecke: list[Block] = []
    anker: tuple[float, float] | None = None
    for m in reihe:
        sprung = distance_km(anker, m.gps) if (anker and m.gps) else None
        if not bloecke or (sprung is not None and sprung >= JUMP_KM):
            ts = effective_capture_time(m, offsets)
            grund = f"{sprung:.0f} km weiter" if sprung is not None else ""
            if ts is not None:
                grund = f"{grund} · {day_label(ts, tag_eins)}" if grund \
                    else day_label(ts, tag_eins)
            bloecke.append(Block(f"ort-{len(bloecke) + 1}", grund))
            anker = m.gps or anker
        elif m.gps and anker is None:
            anker = m.gps
        bloecke[-1].items.append(m)
    return bloecke


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------

def dump_order_yaml(bloecke: list[Block], manifest: Manifest, *, by: str = "day") -> str:
    """Schreibt die Datei — von Hand, nicht ueber ``yaml.dump``.

    Derselbe Grund wie bei ``chapters.yaml``: die Datei ist ein **Formular**.
    Niemand tippt 90 Medien-IDs ab; sortiert wird, indem man Zeilen verschiebt,
    und dafuer muss neben jeder Zeile stehen, *was* dort steht. Ein YAML-Dumper
    wirft die Kommentare weg.
    """
    offsets = manifest.clock_offsets
    tag_eins = first_day(chronological(manifest), offsets)
    zeilen = [
        "# order.yaml — Reihenfolge der Medien. Wird von `slideshow build` eingelesen.",
        "#",
        "# Erzeugt von `slideshow order`, chronologisch vorbelegt. Sortieren heisst",
        "# Zeilen verschieben: ein Bild gehoert dorthin, wo es im *Film* stehen soll,",
        "# nicht dorthin, wo es aufgenommen wurde. Die Kommentare rechts sagen, was",
        "# man da gerade verschiebt.",
        "#",
        "# Die Gruppennamen erscheinen NICHT im Film — sie sind die Arbeitseinheit,",
        "# keine Ueberschrift. Wer an einer Blockgrenze eine Zaesur will, schreibt sie",
        "# in chapters.yaml: `- {group: am-wasser, title: \"Am Wasser\"}`.",
        "#",
        "# Nach einem erneuten `slideshow probe`: `slideshow order --update` pflegt",
        "# neues Material ein und behaelt die Sortierung.",
        "",
        "version: 1",
        "",
        "# Was hier nicht steht, bricht den Build ab. `append` haengt es hinten",
        "# chronologisch an, `drop` laesst es weg — eine Auswahl statt einer",
        "# Sortierung, fuer den Fall, dass mehr Material da ist als Musik.",
        "rest: error",
        "",
        "groups:",
    ]

    if not bloecke:
        return "\n".join(zeilen + ["  # Kein Material im Manifest."]) + "\n"

    for nr, block in enumerate(bloecke):
        if nr:
            zeilen.append("")
        if block.grund:
            zeilen.append(f"  # {block.grund}")
        zeilen.append(f"  - name: {block.name}")
        zeilen.append("    items:")
        for m in block.items:
            zeilen.append(f"      - {m.id}{_kontext(m, tag_eins, offsets)}")
    if by != "none":
        zeilen += ["", "# Zum Umsortieren die Zeilen zwischen den Gruppen verschieben und die",
                   "# Gruppennamen umbenennen (`tag-3` -> `am-wasser`). Eine Gruppe darf leer",
                   "# bleiben; ein Bild darf nur einmal vorkommen."]
    return "\n".join(zeilen) + "\n"


def _kontext(m: MediaItem, tag_eins: _dt.date | None, offsets: dict[str, float],
             *, knapp: bool = False) -> str:
    """Der Kommentar hinter einer Zeile — das, was man zum Sortieren braucht.

    ``knapp`` laesst den Tag weg und nennt nur die Uhrzeit. Das ist die Form,
    die ``slideshow select`` schreibt: dort steht der Tag schon ueber dem Block,
    und bei zweihundert Zeilen zaehlt jede Wiederholung. Eine eingefuegte Zeile
    muss aussehen wie ihre Nachbarn, sonst sieht die Datei nach zwei Werkzeugen
    aus.
    """
    teile: list[str] = []
    ts = effective_capture_time(m, offsets)
    if ts is not None and m.time_source != "none":
        wann = _dt.datetime.fromtimestamp(ts)
        teile.append(f"{wann:%H:%M}" if knapp
                     else f"{day_label(ts, tag_eins)} {wann:%H:%M}")
    if m.kind == "clip":
        info = m.clip
        dauer = (info.effective_duration or info.duration) if info else 0.0
        teile.append(f"Clip {dauer:.1f} s".replace(".", ","))
    elif m.image is not None:
        teile.append("hoch" if m.image.portrait else "quer")
    return f"   # {' · '.join(teile)}" if teile else ""


# --------------------------------------------------------------------------
# Nachpflegen
# --------------------------------------------------------------------------

def update_order_text(text: str, olist: OrderList, manifest: Manifest) -> tuple[str, list[str]]:
    """Neues Material einpflegen, ohne die Handarbeit anzufassen.

    Gearbeitet wird auf dem **Quelltext**, nicht auf dem Modell. Ein Neuschreiben
    aus ``dump_order_yaml`` verloere die eigenen Kommentare — und die sind hier
    keine Zierde: bei ``rest: drop`` ist eine auskommentierte Zeile die Auswahl,
    und ihr Kommentar sagt, warum das Bild draussen bleibt. Genau das darf ein
    Nachpflegen nicht wegwerfen.

    Zurueck kommt der neue Text und die Liste der Meldungen.
    """
    bekannt = {m.id: m for m in manifest.media}
    genannt = [mid for g in olist.blocks for mid in g.items]
    erwaehnt = mentioned_ids(text)
    gelistet = set(genannt)
    neu = [m for m in chronological(manifest) if m.id not in erwaehnt]
    abgewaehlt = [m.id for m in chronological(manifest)
                  if m.id not in gelistet and m.id in erwaehnt]
    verschwunden = [mid for mid in genannt if mid not in bekannt]

    zeilen = text.splitlines()
    meldungen: list[str] = []
    if abgewaehlt:
        meldungen.append(
            f"{len(abgewaehlt)} Medien stehen nur als Kommentar in der Datei und "
            f"bleiben es — bewusst abgewaehlt: {_liste(abgewaehlt)}")

    # Verschwundenes auskommentieren statt loeschen: die Zeile steht an der
    # Stelle, an die das Bild einsortiert war, und wer die Datei umbenannt hat,
    # findet die Stelle so wieder.
    if verschwunden:
        stellen = item_lines(text)
        for mid in verschwunden:
            for nr in stellen.get(mid, []):
                roh = zeilen[nr - 1]
                einzug = len(roh) - len(roh.lstrip())
                zeilen[nr - 1] = (f"{' ' * einzug}# {roh.strip()}"
                                  f"   # nicht mehr im Manifest")
        meldungen.append(
            f"{len(verschwunden)} Eintraege stehen nicht mehr im Manifest und wurden "
            f"auskommentiert: {_liste(verschwunden)}")

    if neu:
        zeilen = _einfuegen(zeilen, text, olist, manifest, neu)
        meldungen.append(f"{len(neu)} neue Medien angehaengt — einsortieren: "
                         f"{_liste([m.id for m in neu])}")
    if not (neu or verschwunden):
        meldungen.append("nichts nachzupflegen — die Datei ist auf dem Stand des "
                         "Manifests")
    return ("\n".join(zeilen) + "\n", meldungen)


def _einfuegen(zeilen: list[str], text: str, olist: OrderList, manifest: Manifest,
               neu: list[MediaItem]) -> list[str]:
    """Das neue Material hinter dem letzten vorhandenen Eintrag anfuegen."""
    offsets = manifest.clock_offsets
    tag_eins = first_day(chronological(manifest), offsets)
    heute = _dt.date.today().isoformat()
    stellen = item_lines(text)
    genannt = [mid for g in olist.blocks for mid in g.items]
    gelistet = max((stellen[mid][-1] for mid in genannt if mid in stellen), default=0)
    # Eingefuegt wird hinter der letzten Zeile, die *irgendeine* ID nennt —
    # auch in einem Kommentar. Sonst ueberholt der neue Block eine
    # auskommentierte Abwahl am Ende einer Gruppe, und die steht danach
    # zusammenhanglos unter einer fremden Ueberschrift.
    letzte = max(_letzte_erwaehnung(text, manifest), gelistet)

    if letzte == 0:
        # Leere oder unlesbar geratene Datei: dann ist Anhaengen ans Ende die
        # einzige Stelle, die es gibt.
        letzte = len(zeilen)

    if olist.groups is not None:
        block = [f"  # neu seit {heute} — einsortieren", "  - name: neu", "    items:"]
        block += [f"      - {m.id}{_kontext(m, tag_eins, offsets)}" for m in neu]
    elif gelistet and "[" in zeilen[gelistet - 1]:
        # Flache Liste im Flow-Stil: eine Blockzeile daneben waere kein gueltiges
        # YAML mehr, also wandert das Neue in dieselbe Zeile.
        kopf, klammer, rest = zeilen[gelistet - 1].rpartition("]")
        zeilen[gelistet - 1] = (kopf + ", " + ", ".join(m.id for m in neu)
                                + klammer + rest + f"   # neu seit {heute}")
        return zeilen
    else:
        block = [f"  # neu seit {heute} — einsortieren"]
        block += [f"  - {m.id}{_kontext(m, tag_eins, offsets)}" for m in neu]
    return zeilen[:letzte] + [""] + block + zeilen[letzte:]


def _letzte_erwaehnung(text: str, manifest: Manifest) -> int:
    """Letzte Zeile, in der eine Medien-ID vorkommt — Kommentare eingeschlossen."""
    bekannt = {m.id for m in manifest.media}
    letzte = 0
    for nr, zeile in enumerate(text.splitlines(), start=1):
        if any(t.group(1) in bekannt for t in _ITEM_RE.finditer(zeile.replace("#", " "))):
            letzte = nr
    return letzte


# --------------------------------------------------------------------------
# Aenderungen aus dem Kontaktbogen einspielen
# --------------------------------------------------------------------------

def apply_changes(text: str, manifest: Manifest, rein: list[str], raus: list[str],
                  *, quelle: str = "order.yaml") -> tuple[str, list[str]]:
    """Nimmt Medien herein und heraus — auf dem Quelltext, nicht auf dem Modell.

    Der Rueckweg vom Kontaktbogen. Bei einer Handvoll Tausche traegt man sie
    selbst ein; bei hundertsechzig ist das ein Nachmittag, und genau dafuer gibt
    es diese Funktion.

    Gearbeitet wird wie in :func:`update_order_text` auf dem **Text**: die
    Kommentare sind in dieser Datei die halbe Miete — sie tragen die
    Alternativen, die Begruendungen und bei ``rest: drop`` die Abwahl selbst.
    Ein Neuschreiben aus dem Modell verloere sie.

    **Wohin ein hereingenommenes Bild kommt:** vor den ersten Eintrag, der
    *spaeter* aufgenommen wurde. Solange die Bloecke chronologisch liegen — und
    das tun sie, wenn die Datei aus ``slideshow select`` stammt —, landet es
    damit von selbst im richtigen Tagesblock, weil die Blockkoepfe zwischen den
    Eintraegen stehen. Nach einer thematischen Umsortierung stimmt nur noch die
    Zeit, nicht mehr der Block; darauf weist die Meldung hin.

    Idempotent: ein Bild, das schon gelistet ist, wird nicht doppelt
    eingetragen, und eines, das gar nicht drinsteht, laesst sich nicht
    herausnehmen. Beides ist eine Meldung, kein Abbruch — bei hundertsechzig
    Aenderungen ist die halb angewandte Liste der schlechtere Ausgang.
    """
    by_id = {m.id: m for m in manifest.media}
    offsets = manifest.clock_offsets
    name = Path(quelle).name

    unbekannt = [mid for mid in [*rein, *raus] if mid not in by_id]
    if unbekannt:
        raise SchemaError(
            f"{len(unbekannt)} Kennungen aus der Aenderungsliste stehen nicht im "
            f"Manifest: {_liste(unbekannt)}. Stammt die Liste von einem anderen "
            f"Projekt, oder wurde seither neu erfasst?", file=quelle)

    beides = sorted(set(rein) & set(raus))
    if beides:
        raise SchemaError(
            f"{len(beides)} Medien sollen zugleich herein und hinaus: "
            f"{_liste(beides)}. Die Liste widerspricht sich — im Bogen noch "
            f"einmal ansehen.", file=quelle)

    zeilen = text.splitlines()
    stellen = item_lines(text)
    gelistet = {mid for mid in stellen if mid in by_id}
    meldungen: list[str] = []

    # -- herausnehmen ---------------------------------------------------
    # Auskommentiert, nicht geloescht: die Zeile steht an der Stelle, an die das
    # Bild einsortiert war, und ihr Kommentar sagt, was es zeigte. Wer den
    # Tausch bereut, macht ihn mit einem Handgriff rueckgaengig.
    heute = _dt.date.today().isoformat()
    nicht_drin = [mid for mid in raus if mid not in gelistet]
    getan_raus = 0
    for mid in raus:
        for nr in stellen.get(mid, []):
            roh = zeilen[nr - 1]
            einzug = len(roh) - len(roh.lstrip())
            zeilen[nr - 1] = (f"{' ' * einzug}# {roh.strip()}"
                              f"   # heraus {heute}")
            getan_raus += 1

    # -- hereinnehmen ---------------------------------------------------
    schon_drin = [mid for mid in rein if mid in gelistet]
    offen = [mid for mid in rein if mid not in gelistet]
    tag_eins = first_day(chronological(manifest), offsets)
    # Alle Einfuegungen vorher bestimmen und erst danach anwenden, von hinten
    # nach vorn — sonst verschiebt die erste Einfuegung alle folgenden Zeilen.
    # Knapper Kontext: die Datei kommt aus `select`, und dort steht der Tag
    # ueber dem Block. Eine eingefuegte Zeile soll aussehen wie ihre Nachbarn.
    knapp = _knappe_zeilen(text)
    nach_stelle: dict[int, list[str]] = {}
    for mid in sorted(offen, key=lambda x: _zeitschluessel(by_id[x], offsets)):
        idx = _einfuegepunkt(by_id[mid], zeilen, by_id, offsets)
        neu = (f"      - {mid}"
               f"{_kontext(by_id[mid], tag_eins, offsets, knapp=knapp)}")
        nach_stelle.setdefault(idx, []).append(neu)
    for idx in sorted(nach_stelle, reverse=True):
        zeilen[idx:idx] = nach_stelle[idx]

    # -- Meldungen ------------------------------------------------------
    if offen:
        meldungen.append(f"{len(offen)} Medien hereingenommen")
    if getan_raus:
        meldungen.append(f"{getan_raus} Zeilen auskommentiert")
    if schon_drin:
        meldungen.append(f"{len(schon_drin)} standen bereits in {name} und "
                         f"bleiben unveraendert: {_liste(schon_drin)}")
    if nicht_drin:
        meldungen.append(f"{len(nicht_drin)} sollten heraus, standen aber gar "
                         f"nicht in {name}: {_liste(nicht_drin)}")
    if not (offen or getan_raus):
        meldungen.append(f"nichts zu tun — {name} steht bereits so da")

    # Die Folge *nach* der Aenderung, in Dateireihenfolge — aus dem fertigen
    # Text gelesen und nicht aus `gelistet` zusammengesetzt: das ist eine Menge
    # und hat gar keine Reihenfolge. Daraus gebaut meldete die Pruefung eine
    # unsortierte Datei, wo eine tadellos sortierte stand.
    neuer_text = "\n".join(zeilen) + "\n"
    danach = item_lines(neuer_text)
    ids_danach = [mid for mid, nrs in sorted(danach.items(), key=lambda kv: kv[1][0])
                  if mid in by_id]
    if offen and not is_chronological(manifest, ids_danach):
        meldungen.append(
            "Die Reihenfolge ist nicht chronologisch. Hereingenommene Medien "
            "stehen an der ersten zeitlich passenden Stelle — das ist nicht "
            "unbedingt die Gruppe, in die sie thematisch gehoeren.")
    return (neuer_text, meldungen)


# --------------------------------------------------------------------------
# Umsortieren aus dem Kontaktbogen (Ansicht "Reihenfolge")
# --------------------------------------------------------------------------

def order_fingerprint(olist: OrderList) -> str:
    """Kurze Pruefsumme ueber Bloecke und gelistete IDs, in Dateireihenfolge.

    Der Bogen traegt sie mit, und ``--apply`` vergleicht sie mit der Datei.
    Eine Umsortierung ist — anders als ein Tausch — eine *vollstaendige* Folge:
    wer nach dem Erzeugen des Bogens von Hand drei Zeilen verschoben hat und
    danach die Liste aus dem Bogen einspielt, verloere diese drei
    Verschiebungen kommentarlos. Die Mengenpruefung in :func:`apply_reorder`
    faengt nur hinzugekommene oder verschwundene IDs, keine geaenderte
    Reihenfolge — das tut erst diese Summe.
    """
    import hashlib
    h = hashlib.blake2b(digest_size=6)
    for g in olist.blocks:
        h.update(f"\x00{g.name}\x01".encode("utf-8"))
        h.update("\x02".join(g.items).encode("utf-8"))
    return h.hexdigest()


#: ``- name: tag-3`` — ein Blockkopf; der Name darf in Anfuehrungszeichen stehen.
_NAME_RE = re.compile(r"^(\s*)-\s*name:\s*(.*?)\s*(?:#.*)?$")
#: ``items:`` bzw. ``order:`` (flache Form), leer oder ``[]``, ggf. mit Kommentar.
_LISTE_RE = re.compile(r"^(\s*)(items|order):\s*(\[\s*\])?\s*(#.*)?$")
#: Die Alternativen, die ``select`` unter eine gewaehlte Zeile schreibt
#: (``#  statt: …``), und ihre Fortsetzungszeilen (``#         …``).
_STATT_RE = re.compile(r"^\s*#\s*statt:")
_FORTSETZUNG_RE = re.compile(r"^\s*#\s{3,}\S")


def _gepaeck(zeilen: list[str], i: int) -> int:
    """Index hinter der letzten Zeile, die mit dem Eintrag in Zeile ``i`` wandert.

    Das sind nur die ``statt:``-Zeilen. Sie beschreiben *dieses* Bild — seine
    Geschwister —, und an einer anderen Stelle stehengelassen zeigten sie auf
    das falsche. Alles andere (ausgelassene Trauben, eigene Kommentare,
    auskommentierte Abwahl) bleibt, wo es steht: es beschreibt eine *Stelle*
    in der Zeitachse, nicht ein Bild.
    """
    j = i + 1
    if j < len(zeilen) and _STATT_RE.match(zeilen[j]):
        j += 1
        while j < len(zeilen) and _FORTSETZUNG_RE.match(zeilen[j]):
            j += 1
    return j


def _listenzeilen(text: str, olist: OrderList, quelle: str) -> list[int]:
    """Index der ``items:``- bzw. ``order:``-Zeile je Block, in Blockreihenfolge.

    Gebraucht fuer zwei Dinge: als Einfuegestelle in einen leeren Block und um
    ``items: []`` umzuschreiben, sobald ein Block leer wird oder es nicht mehr
    ist — ``items:`` ohne Eintrag waere YAML-``null`` und keine leere Liste.
    """
    zeilen = text.splitlines()
    if olist.groups is None:
        treffer = [i for i, z in enumerate(zeilen)
                   if (m := _LISTE_RE.match(z)) and m.group(2) == "order"
                   and not m.group(1)]
        if len(treffer) != 1:
            raise SchemaError("Die flache Liste `order:` steht nicht als eigene "
                              "Zeile da — umsortieren laesst sich nur die "
                              "Blockform mit einem Eintrag je Zeile.", file=quelle)
        return treffer

    koepfe = [i for i, z in enumerate(zeilen)
              if _NAME_RE.match(z) and not z.lstrip().startswith("#")]
    if len(koepfe) != len(olist.blocks):
        raise SchemaError(
            f"{len(olist.blocks)} Bloecke in der Datei, aber {len(koepfe)} Zeilen "
            f"`- name:` — die Blockkoepfe muessen je eine eigene Zeile haben.",
            file=quelle)
    out: list[int] = []
    for nr, (kopf, block) in enumerate(zip(koepfe, olist.blocks)):
        name = _NAME_RE.match(zeilen[kopf]).group(2).strip("'\"")
        if name != block.name:
            raise SchemaError(f"Blockkopf in Zeile {kopf + 1} heisst {name!r}, "
                              f"erwartet war {block.name!r}.", file=quelle,
                              line=kopf + 1)
        ende = koepfe[nr + 1] if nr + 1 < len(koepfe) else len(zeilen)
        liste = next((i for i in range(kopf, ende)
                      if (m := _LISTE_RE.match(zeilen[i])) and m.group(2) == "items"),
                     None)
        if liste is None:
            raise SchemaError(
                f"Block {block.name!r}: `items:` steht nicht als eigene Zeile da "
                f"(Flow-Stil `items: [a, b]`?). Umsortieren aus dem Bogen braucht "
                f"einen Eintrag je Zeile.", file=quelle, line=kopf + 1)
        out.append(liste)
    return out


def _lis(folge: list[int]) -> set[int]:
    """Positionen einer laengsten aufsteigenden Teilfolge.

    Was *nicht* darin liegt, ist die kleinste Menge an Eintraegen, die man
    verschieben muss, um von der alten zur neuen Folge zu kommen. Ein Bild nach
    vorn gezogen heisst so "1 verschoben" und nicht "40 verschoben" — der
    naive Positionsvergleich zaehlte jedes Bild dazwischen mit.
    """
    import bisect
    enden: list[int] = []
    wo: list[int] = []
    vorher = [-1] * len(folge)
    for i, x in enumerate(folge):
        k = bisect.bisect_left(enden, x)
        if k == len(enden):
            enden.append(x)
            wo.append(i)
        else:
            enden[k] = x
            wo[k] = i
        vorher[i] = wo[k - 1] if k else -1
    out: set[int] = set()
    i = wo[-1] if wo else -1
    while i >= 0:
        out.add(i)
        i = vorher[i]
    return out


def apply_reorder(text: str, manifest: Manifest, olist: OrderList,
                  bloecke: list[tuple[str, list[tuple[str, bool]]]], *,
                  stand: str | None = None,
                  quelle: str = "order.yaml") -> tuple[str, list[str]]:
    """Schreibt eine neue Reihenfolge aus dem Kontaktbogen in die Datei.

    ``bloecke`` ist die **vollstaendige** Folge, wie sie die Ansicht
    "Reihenfolge" zeigt: je Block die Eintraege in Filmreihenfolge, jeder mit
    der Angabe, ob er hinaus soll. Vollstaendig und nicht als Liste einzelner
    Zuege, weil das Ergebnis dann nicht davon abhaengt, in welcher Reihenfolge
    jemand im Browser gezogen hat — und weil sich so pruefen laesst, ob der
    Bogen zur Datei passt (:func:`order_fingerprint`).

    Gearbeitet wird wie in :func:`apply_changes` **auf dem Text**, und zwar so,
    wie man es im Editor von Hand taete: **nur die bewegten Eintraege
    wandern.** Welche das sind, sagt die laengste aufsteigende Teilfolge der
    alten Positionen (:func:`_lis`) — alles darin bleibt Zeichen fuer Zeichen
    stehen. Ein bewegter Eintrag (seine Zeile samt den ``statt:``-Zeilen
    darunter, :func:`_gepaeck`) wird an seiner alten Stelle entfernt und direkt
    hinter seinem neuen Vorgaenger eingesetzt; ohne Vorgaenger im Block direkt
    hinter ``items:``.

    Die naheliegende Alternative — die neue Folge der Reihe nach in die alten
    Zeilenplaetze zu fuellen — sieht einfacher aus und ist an echten Dateien
    falsch: ein Bild an den Blockanfang gezogen schiebt dann *jeden* Eintrag
    des Blocks um einen Platz, und die Kommentare dazwischen (Abwahl,
    ausgelassene Trauben) stehen danach neben fremden Bildern. An
    ``london-2026`` waren das 20 geaenderte Zeilen fuer drei Handgriffe.

    Kommentare zwischen den Eintraegen gehoeren dabei zum *folgenden* Eintrag
    und bleiben vor ihm stehen — so schreibt ``select`` die ausgelassenen
    Trauben (vor das naechste gewaehlte Bild), und deshalb setzt sich ein
    bewegter Eintrag hinter seinen Vorgaenger und nicht vor seinen Nachfolger.

    IDs, die die Folge nennt und die Datei nicht listet, sind
    **hereingenommen** (im Bogen angeklickt) und stehen dort, wohin der Bogen
    sie gesetzt hat. Hinausgenommenes wird auskommentiert wie in
    :func:`apply_changes`.

    Bloecke werden nicht umbenannt, neu angelegt oder umgestellt — ihre Namen
    sind Anker fuer ``chapters.yaml`` (``group:``), und ein Kapitel, dessen
    Gruppe still verschwindet, faellt erst beim naechsten ``build`` auf.
    """
    by_id = {m.id: m for m in manifest.media}
    offsets = manifest.clock_offsets
    name = Path(quelle).name

    # -- passt der Bogen zur Datei? ---------------------------------------
    if stand is not None and stand != order_fingerprint(olist):
        raise SchemaError(
            f"{name} wurde geaendert, nachdem der Kontaktbogen erzeugt wurde. "
            f"Die Liste aus dem Bogen ist eine vollstaendige Reihenfolge und "
            f"ueberschriebe diese Aenderungen — erst `slideshow sheet` neu "
            f"erzeugen, dann noch einmal sortieren.", file=quelle)

    soll_namen = [g.name for g in olist.blocks]
    ist_namen = [n for n, _ in bloecke]
    if ist_namen != soll_namen:
        raise SchemaError(
            f"Die Bloecke der Liste ({', '.join(ist_namen) or '—'}) passen nicht "
            f"zu {name} ({', '.join(soll_namen) or '—'}). Stammt der Bogen von "
            f"einem aelteren Stand? `slideshow sheet` erzeugt ihn neu.",
            file=quelle)

    alle = [mid for _n, eintraege in bloecke for mid, _r in eintraege]
    unbekannt = [mid for mid in alle if mid not in by_id]
    if unbekannt:
        raise SchemaError(
            f"{len(unbekannt)} Kennungen aus der Liste stehen nicht im Manifest: "
            f"{_liste(unbekannt)}.", file=quelle)
    doppelt = sorted({mid for mid in alle if alle.count(mid) > 1})
    if doppelt:
        raise SchemaError(f"{len(doppelt)} Medien stehen mehrfach in der Liste: "
                          f"{_liste(doppelt)}.", file=quelle)

    gelistet = [mid for g in olist.blocks for mid in g.items if mid in by_id]
    fehlend = [mid for mid in gelistet if mid not in set(alle)]
    if fehlend:
        raise SchemaError(
            f"{len(fehlend)} Medien aus {name} fehlen in der Liste: "
            f"{_liste(fehlend)}. Eine Umsortierung nennt jedes gelistete Medium — "
            f"der Bogen ist aelter als die Datei. `slideshow sheet` erzeugt ihn "
            f"neu.", file=quelle)

    # -- Plaetze und Listenzeilen bestimmen --------------------------------
    zeilen = text.splitlines()
    stellen = item_lines(text)
    plaetze: dict[str, tuple[int, int]] = {}
    for mid in gelistet:
        nrs = stellen.get(mid, [])
        if len(nrs) != 1:
            raise SchemaError(f"{mid!r} steht nicht genau einmal als Eintrag in "
                              f"{name}.", file=quelle)
        i = nrs[0] - 1
        vorn = zeilen[i].split("#", 1)[0]
        if not re.fullmatch(rf"\s*-\s*{re.escape(mid)}\s*", vorn):
            raise SchemaError(
                f"Zeile {i + 1} enthaelt mehr als einen Eintrag oder ist im "
                f"Flow-Stil geschrieben (`[a, b]`). Umsortieren aus dem Bogen "
                f"braucht einen Eintrag je Zeile.", file=quelle, line=i + 1)
        plaetze[mid] = (i, _gepaeck(zeilen, i))
    listenzeilen = _listenzeilen(text, olist, quelle)

    heute = _dt.date.today().isoformat()
    knapp = _knappe_zeilen(text)
    tag_eins = first_day(chronological(manifest), offsets)

    def stueck(mid: str, raus: bool, einzug: str) -> list[str]:
        if mid not in plaetze:                      # hereingenommen
            if raus:
                return []
            return [f"{einzug}- {mid}"
                    f"{_kontext(by_id[mid], tag_eins, offsets, knapp=knapp)}"]
        a, b = plaetze[mid]
        teil = list(zeilen[a:b])
        if raus:
            roh = teil[0]
            ein = len(roh) - len(roh.lstrip())
            teil[0] = f"{' ' * ein}# {roh.strip()}   # heraus {heute}"
        return teil

    # -- was bleibt stehen? -------------------------------------------------
    alt_index = {mid: i for i, mid in enumerate(gelistet)}
    alt_block = {mid: g.name for g in olist.blocks for mid in g.items}
    # Kandidaten sind gelistete Eintraege, die ihren Block behalten. Ein
    # Blockwechsel ist immer eine Bewegung, auch wenn die globale Folge gleich
    # bleibt (letztes Bild von Tag 1 wird erstes von Tag 2).
    kandidaten = [mid for n, e in bloecke for mid, _r in e
                  if mid in alt_index and alt_block[mid] == n]
    stabil = {kandidaten[p] for p in _lis([alt_index[m] for m in kandidaten])}

    # Einzug neuer Zeilen: wie die vorhandenen Eintraege, sonst die Vorgabe.
    erster = next(iter(plaetze.values()), None)
    vorgabe = ("      " if olist.groups is not None else "  ")
    if erster is not None:
        roh = zeilen[erster[0]]
        vorgabe = roh[:len(roh) - len(roh.lstrip())]

    ersatz: dict[int, str] = {}             # Zeile -> neue Zeile (Abwahl)
    weg: set[int] = set()                   # Zeilen bewegter Eintraege
    danach: dict[int, list[str]] = {}       # Zeile -> dahinter einfuegen
    listen_neu: dict[int, str] = {}

    for mid in plaetze:
        if mid not in stabil:
            weg.update(range(*plaetze[mid]))

    for (bname, eintraege), lz in zip(bloecke, listenzeilen):
        anker = lz
        for mid, raus in eintraege:
            if mid in stabil:
                a, b = plaetze[mid]
                if raus:
                    ersatz[a] = stueck(mid, True, vorgabe)[0]
                anker = b - 1
                continue
            danach.setdefault(anker, []).extend(stueck(mid, raus, vorgabe))

        # `items:` ohne einen einzigen Eintrag darunter ist YAML-null, und
        # `items: []` mit Eintraegen darunter ist kaputt. Beides kann eine
        # Umsortierung erzeugen — ein Block wird leergezogen oder gefuellt.
        leer = not any(not raus for _mid, raus in eintraege)
        m = _LISTE_RE.match(zeilen[lz])
        war_leer = bool(m.group(3))
        if leer != war_leer:
            kommentar = f"  {m.group(4)}" if m.group(4) else ""
            listen_neu[lz] = (f"{m.group(1)}{m.group(2)}:"
                              f"{' []' if leer else ''}{kommentar}")

    out: list[str] = []
    for i, z in enumerate(zeilen):
        if i in weg:
            pass
        elif i in ersatz:
            out.append(ersatz[i])
        else:
            out.append(listen_neu.get(i, z))
        # Auch hinter einer entfallenen Zeile: der Anker ist immer ein stehender
        # Eintrag oder die `items:`-Zeile, nie eine bewegte — aber falls doch,
        # geht so nichts verloren, und die Gegenprobe unten saehe es.
        if i in danach:
            out.extend(danach[i])
    neuer_text = "\n".join(out) + "\n"

    # -- Gegenprobe ---------------------------------------------------------
    # Die Textchirurgie ist die riskante Stelle dieses Moduls. Bevor sie eine
    # Datei schreibt, die `build` erst eine Stunde spaeter liest, wird das
    # Ergebnis hier noch einmal geladen und mit der verlangten Folge verglichen.
    import yaml
    try:
        probe = OrderList.model_validate(yaml.safe_load(neuer_text) or {})
    except Exception as exc:                            # noqa: BLE001
        raise SchemaError(f"Die umsortierte Datei waere kein gueltiges "
                          f"{name} mehr ({exc}). Nichts geschrieben.",
                          file=quelle) from exc
    soll = [(n, [mid for mid, raus in e if not raus]) for n, e in bloecke]
    ist = [(g.name, list(g.items)) for g in probe.blocks]
    if ist != soll:
        raise SchemaError(f"Die umsortierte Datei ergibt nicht die Folge aus dem "
                          f"Bogen. Nichts geschrieben — bitte melden.", file=quelle)

    # -- Meldungen ----------------------------------------------------------
    # Gezaehlt wie im Bogen (`pruefen()` im Skript): ohne Hinausgenommenes,
    # damit die Zahl auf dem Bildschirm und die im Bericht dieselbe ist.
    raus_von = {mid: raus for _n, e in bloecke for mid, raus in e}
    bleiben = [mid for mid in kandidaten if not raus_von[mid]]
    drin = sum(1 for mid in alle if mid in alt_index and not raus_von[mid])
    verschoben = drin - len(_lis([alt_index[m] for m in bleiben]))
    herein = [mid for mid in alle if mid not in alt_index and not raus_von[mid]]
    hinaus = [mid for mid in alle if mid in alt_index and raus_von[mid]]
    meldungen: list[str] = []
    if verschoben:
        meldungen.append(f"{verschoben} Medien an neuer Stelle")
    if herein:
        meldungen.append(f"{len(herein)} Medien hereingenommen")
    if hinaus:
        meldungen.append(f"{len(hinaus)} Zeilen auskommentiert")
    if not (verschoben or herein or hinaus):
        meldungen.append(f"nichts zu tun — {name} steht bereits so da")
    return (neuer_text, meldungen)


#: Eine Eintragszeile, deren Kommentar mit der Uhrzeit beginnt — die Form aus
#: ``slideshow select``. ``slideshow order`` schreibt stattdessen "Tag 3 · …".
_KNAPP = re.compile(r"^\s*-\s*[A-Za-z0-9_-]+\s+#\s*\d{1,2}:\d{2}\b", re.M)


def _knappe_zeilen(text: str) -> bool:
    """Schreibt diese Datei die kurze Kommentarform?

    Geraten wird an dem, was dasteht, statt an der Herkunft: eine Datei kann
    von Hand entstanden oder umgeschrieben worden sein, und der Dateikopf
    ueberlebt nicht jede Bearbeitung.
    """
    return bool(_KNAPP.search(text))


def _zeitschluessel(m: MediaItem, offsets: dict[str, float]) -> tuple[float, str]:
    """Aufnahmezeit als Sortierschluessel; Datumsloses ans Ende."""
    ts = effective_capture_time(m, offsets)
    return (float("inf") if ts is None else ts, m.path)


def _einfuegepunkt(m: MediaItem, zeilen: list[str], by_id: dict[str, MediaItem],
                   offsets: dict[str, float]) -> int:
    """Index, **vor** dem die neue Zeile steht.

    Gesucht ist die erste Eintragszeile mit spaeterer Aufnahmezeit. Gibt es
    keine, kommt das Bild hinter den letzten Eintrag — nicht ans Dateiende, wo
    es unter die Schlusskommentare rutschte.
    """
    ziel = _zeitschluessel(m, offsets)
    letzter = 0
    for nr, zeile in enumerate(zeilen):
        vor_kommentar = zeile.split("#", 1)[0]
        treffer = [t.group(1) for t in _ITEM_RE.finditer(vor_kommentar)]
        gefunden = next((t for t in treffer if t in by_id), None)
        if gefunden is None:
            continue
        letzter = nr + 1
        if _zeitschluessel(by_id[gefunden], offsets) > ziel:
            return nr
    return letzter


# --------------------------------------------------------------------------
# Kapitel an Gruppen verankern (Entscheidung 5)
# --------------------------------------------------------------------------

def is_chronological(manifest: Manifest, ids: list[str]) -> bool:
    """Steigen die Aufnahmezeiten der Folge monoton?"""
    by_id = {m.id: m for m in manifest.media}
    offsets = manifest.clock_offsets
    zeiten = [t for t in (effective_capture_time(by_id[i], offsets)
                          for i in ids if i in by_id) if t is not None]
    return all(a <= b for a, b in zip(zeiten, zeiten[1:]))


def group_anchors(olist: OrderList, manifest: Manifest,
                  ids: list[str] | None = None) -> list[GruppenAnker]:
    """Beschreibt jeden Block als Kapitelkandidaten — die Vorlage fuer
    ``slideshow chapters --from-groups``.

    Die Gegenrichtung zu :func:`anchor_chapters`: dort wird ein ``group:``
    aufgeloest, hier wird eines *vorgeschlagen*. Beides steht in diesem Modul,
    weil beides von der Kopplung der zwei Dateien weiss.

    Gerechnet wird auf den Bloecken, nicht auf der aufgeloesten Folge: die
    Blockgrenze ist die Kapitelgrenze, und sie steht auch dann fest, wenn
    innerhalb des Blocks noch umsortiert wird. Der zeitliche Umfang dagegen
    kommt aus den Aufnahmezeiten und ist deshalb gegen ein Vorziehen
    unempfindlich — ``min``/``max`` statt erstem und letztem Eintrag.

    ``ids`` ist die aufgeloeste Folge; fehlt ein Block darin vollstaendig
    (``rest: drop`` hat ihn geleert), gaebe es fuer seine Folie keinen Platz und
    :func:`anchor_chapters` braeche spaeter ab — er wird hier weggelassen.
    """
    by_id = {m.id: m for m in manifest.media}
    offsets = manifest.clock_offsets
    tag_eins = first_day(chronological(manifest), offsets)
    ueberlebt = set(ids) if ids is not None else None

    anker: list[GruppenAnker] = []
    for gruppe in olist.blocks:
        # Die flache Form ``order:`` ergibt einen namenlosen Block. Ein
        # ``group: ""`` waere kein Anker, sondern ein Tippfehler mit Wirkung.
        if not gruppe.name:
            continue
        items = [i for i in gruppe.items
                 if ueberlebt is None or i in ueberlebt]
        if not items:
            continue
        zeiten = sorted(t for t in (effective_capture_time(by_id[i], offsets)
                                    for i in items if i in by_id) if t is not None)
        tage = {_dt.datetime.fromtimestamp(t).date() for t in zeiten}
        if not zeiten:
            # Kein Zeitstempel im ganzen Block: ``subtitle: auto`` haette nichts
            # zu nehmen und liefe in die Warnung aus ``insert_titles``.
            spanne, mehrtaegig = ("ohne Aufnahmezeitpunkt", True)
        elif len(tage) == 1:
            spanne, mehrtaegig = (day_label(zeiten[0], tag_eins), False)
        else:
            spanne = (f"{day_label(zeiten[0], tag_eins)} bis "
                      f"{day_label(zeiten[-1], tag_eins)}")
            mehrtaegig = True
        anker.append(GruppenAnker(name=gruppe.name, anzahl=len(items),
                                  spanne=spanne, mehrtaegig=mehrtaegig))
    return anker


def anchor_chapters(chapters: list[Chapter], olist: OrderList | None,
                    ids: list[str] | None) -> list[Chapter]:
    """Loest ``group:`` in ``chapters.yaml`` zu einem ``before:`` auf.

    Der Anker existiert, weil ``before: img_042`` in dem Moment bricht, in dem
    man img_042 innerhalb seines Blocks nach hinten schiebt — er zeigt dann
    kommentarlos mitten in den Block hinein. ``group:`` meint die Blockgrenze
    und ueberlebt jedes Umsortieren *innerhalb* des Blocks.

    Aufgeloest wird hier und nicht in ``insert_titles``: dort ist die
    Reihenfolge nur noch eine Liste, und die Gruppen sind schon vergessen. So
    gibt es genau eine Stelle, die von der Kopplung der beiden Dateien weiss —
    und ``build.py`` bekommt keine Zeile ueber Gruppen.
    """
    if not any(k.group for k in chapters):
        return chapters

    if olist is None:
        namen = ", ".join(sorted({k.group for k in chapters if k.group}))
        raise SchemaError(
            f"Kapitel verweisen auf die Gruppen {namen} — es gibt aber keine "
            f"order.yaml, in der Gruppen stehen koennten. Entweder eine anlegen "
            f"(`slideshow order`) oder die Kapitel an eine Medien-ID haengen "
            f"(`before:`).", path="chapters")

    # Nur was die Aufloesung ueberlebt hat: ein `rest: drop` kann das erste Bild
    # einer Gruppe weggenommen haben, und die Folie gehoert dann vor das erste
    # noch vorhandene.
    ueberlebt = set(ids or [])
    erste: dict[str, str] = {}
    for gruppe in olist.blocks:
        if not gruppe.name:
            continue
        treffer = next((mid for mid in gruppe.items if mid in ueberlebt), None)
        if treffer is not None:
            erste[gruppe.name] = treffer

    out: list[Chapter] = []
    for kap in chapters:
        if not kap.group:
            out.append(kap)
            continue
        if kap.group not in erste:
            leer = any(g.name == kap.group for g in olist.blocks)
            raise SchemaError(
                (f"Kapitel {kap.title!r} verweist auf die Gruppe {kap.group!r}, die "
                 f"kein Medium mehr enthaelt — `rest: drop` hat sie geleert."
                 if leer else
                 f"Kapitel {kap.title!r} verweist auf die Gruppe {kap.group!r}, die es "
                 f"in order.yaml nicht gibt. Vorhanden: "
                 f"{', '.join(g.name for g in olist.blocks if g.name) or '(keine)'}."),
                path="chapters")
        out.append(kap.model_copy(update={"group": None, "before": erste[kap.group]}))
    return out
