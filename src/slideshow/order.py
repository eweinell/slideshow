"""Manuelle Reihenfolge — ``order.yaml`` aufloesen
(``docs/briefing-manuelle-reihenfolge.md``, Stufe 1).

Die Abfolge des Films kommt sonst aus :func:`slideshow.probe.chronological`.
Fuer einen Film, der **thematisch** erzaehlt, ist das falsch, und die Handarbeit
in ``edit.yaml`` stirbt beim naechsten ``build``. ``order.yaml`` ist deshalb
eine eigene *Eingabe*-Datei nach dem Muster von ``chapters.yaml``: an
Medien-IDs verankert, von ``build`` gelesen und nie geschrieben.

Dieses Modul ist reine Rechnung ohne Datei-I/O bis auf :func:`load_order` —
dieselbe Aufteilung wie in :mod:`slideshow.titles`.

Die Aufloesung ist im Kern eine Zeile (``[by_id[i] for i in ids]``); alles
andere hier sind die drei Faelle, in denen genau das stillschweigend das
Falsche taete:

* eine **unbekannte** ID — der Tippfehler oder die umbenannte Datei,
* eine **doppelte** ID — sie macht die Kapitelverankerung mehrdeutig
  (Entscheidung 4),
* **nicht genanntes** Material — der teuerste Fehler dieser Datei, weil man
  eine Stunde rendert und hinterher nachzaehlt (Entscheidung 3).
"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import SchemaError
from .models import Manifest, OrderList
from .probe import chronological

#: Wie viele IDs eine Meldung auffuehrt, bevor sie zaehlt statt aufzulisten.
#: Bei 90 Bildern ist eine vollstaendige Liste keine Fehlermeldung mehr,
#: sondern eine zweite Datei.
MAX_GENANNT = 8


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
