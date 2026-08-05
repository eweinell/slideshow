"""Auswahl aus grossem Material — ``slideshow select``
(``docs/briefing-auswahl.md``, Stufe 1).

Aus einem Sammelbecken von tausend und mehr Aufnahmen die zweihundert
herausholen, die in den Film passen. Ohne einen einzigen Bildpunkt anzusehen:
gerechnet wird auf Zeitstempeln und EXIF, alles andere ist ausdrueckliches
Nicht-Ziel.

Geschrieben wird eine ``order.yaml`` mit ``rest: drop``. Das ist keine
Verlegenheitsloesung, sondern das vorhandene Format fuer genau diesen Fall —
und es bringt mit, was eine Auswahl braucht: das Abgewaehlte bleibt als
Kommentar an seinem zeitlichen Platz stehen, und
:func:`slideshow.order.mentioned_ids` sorgt dafuer, dass ein spaeteres
``order --update`` es nie wieder als "neu" anbietet.

Dieses Modul ist reine Rechnung ohne Datei-I/O — dieselbe Aufteilung wie in
:mod:`slideshow.order` und :mod:`slideshow.titles`.

Vier Stufen, in dieser Reihenfolge, und jede fuer sich erklaerbar:

**Trauben** (:func:`bursts`). Aufnahmen dicht beieinander zeigen fast immer
dasselbe. Aus einer Traube kommt hoechstens ein Bild in den Film — die einzige
Regel hier, die ohne Quote und ohne Zufall gilt.

**Quote** (:func:`day_quota`). Wie viele Bilder ein Tag stellen darf. Gerechnet
wird auf der Zahl seiner *Trauben*, nicht seiner Bilder: wer zweihundert
Serienbilder von zwei Motiven macht, hat zwei Motive.

**Spreizung** (:func:`spread`). Welche Trauben des Tages. Nicht zufaellig,
sonst kommen alle acht Bilder vom Abendessen.

**Wahl in der Traube** (:func:`pick_in_burst`). Welches Bild. Hier sitzt das
bisschen Zufall, das ein zweites ``--seed`` sichtbar macht.
"""

from __future__ import annotations

import datetime as _dt
import math
import random
from dataclasses import dataclass, field

from .chapters import JUMP_KM, day_label, distance_km, first_day
from .errors import SlideshowError
from .models import Manifest, MediaItem
from .probe import chronological, effective_capture_time

# --------------------------------------------------------------------------
# Vorgaben
#
# Alles hier sind Anfangswerte, keine Messergebnisse. Der Traubenabstand ist
# der einzige, an dem wirklich etwas haengt — er gehoert nach dem ersten Lauf
# ueber echtes Material nachgemessen.
# --------------------------------------------------------------------------

#: Abstand, unter dem zwei Aufnahmen als dasselbe Motiv gelten (Sekunden).
BURST_GAP = 90.0
#: Hoechstdauer einer Traube. Ohne diesen Deckel verschmilzt eine Stunde
#: regelmaessigen Fotografierens im Minutentakt zu *einem* Eintrag, und der
#: ganze Vormittag stellt ein Bild.
BURST_MAX = 600.0
#: Daempfung der Tagesquote. 0 = gleich viele je Tag, 1 = proportional zum
#: Material. Bei 0,5 bekommt ein Tag mit vierfachem Material doppelt so viele.
DAY_ALPHA = 0.5
#: Kein Tag mit Material geht leer aus.
MIN_PER_DAY = 1
#: Kein Tag stellt mehr als diesen Anteil des Films.
MAX_SHARE = 0.25
#: Mindestlangkante. Der Master ist 4K, und Ken Burns zoomt hinein.
MIN_LONG_EDGE = 2160
#: Ab hier gilt ein Bild als Panorama — in 16:9 wird daraus eine Briefmarke.
PANORAMA_RATIO = 2.5
#: Hoechstanteil Hochformat. Darueber wird nachkorrigiert (:func:`balance_portrait`).
MAX_PORTRAIT = 0.3

#: Gruppierungsachse der Quote.
BY_CHOICES = ("day", "place", "none")

#: Wie viele IDs eine Meldung auffuehrt, bevor sie zaehlt statt aufzulisten.
MAX_GENANNT = 8


# --------------------------------------------------------------------------
# Trauben
# --------------------------------------------------------------------------

@dataclass
class Burst:
    """Eine Gruppe von Aufnahmen, die vermutlich dasselbe zeigen."""

    items: list[MediaItem] = field(default_factory=list)
    #: Aufnahmezeiten inklusive Uhren-Offset, gleiche Reihenfolge wie ``items``.
    times: list[float] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.times[0]

    @property
    def end(self) -> float:
        return self.times[-1]

    @property
    def mitte(self) -> float:
        return 0.5 * (self.start + self.end)

    @property
    def tag(self) -> _dt.date:
        return _dt.datetime.fromtimestamp(self.mitte).date()

    def __len__(self) -> int:
        return len(self.items)


def bursts(media: list[MediaItem], offsets: dict[str, float], *,
           gap: float = BURST_GAP, max_span: float = BURST_MAX) -> list[Burst]:
    """Zerlegt die chronologische Folge in Trauben.

    Getrennt wird an drei Signalen, und alle drei bedeuten dasselbe: *hier
    faengt etwas Neues an*.

    - **Zeitluecke** groesser ``gap``. Das Hauptsignal, und ohne inhaltliche
      Analyse das einzige, das es fuer Aehnlichkeit gibt.
    - **Ortssprung** ueber ``JUMP_KM``. Nur wo GPS vorliegt, also selten; wo es
      vorliegt, schlaegt es die Zeit.

    Gerechnet wird **je Kamera getrennt**, nicht auf der gemischten Folge. Zwei
    Kameras, die gleichzeitig laufen, machen keine Serie, sondern zwei
    Blickwinkel — beide duerfen bleiben. Ein blosses "Geraetewechsel trennt"
    auf der gemischten Folge waere dabei zu wenig: bei der Reihenfolge Sony,
    Pixel, Sony zerrisse der Wechsel die Kette, und die beiden Sony-Aufnahmen
    landeten in *verschiedenen* Trauben, obwohl sie zwoelf Sekunden
    auseinanderliegen. Beide koennten dann gewaehlt werden, und genau das soll
    die Traubenregel verhindern. (Gemessen an einem Testbestand von 1570
    Aufnahmen: drei solcher Paare.)

    Material ohne verwertbaren Aufnahmezeitpunkt gehoert **nicht** hierher: es
    hat keine Position auf der Zeitachse, und eine Traube waere geraten. Der
    Aufrufer sortiert es vorher aus.
    """
    nach_kamera: dict[str, list[tuple[float, MediaItem]]] = {}
    for m in media:
        ts = effective_capture_time(m, offsets)
        if ts is not None and m.time_source != "none":
            nach_kamera.setdefault(m.camera, []).append((ts, m))

    out: list[Burst] = []
    for reihe in nach_kamera.values():
        reihe.sort(key=lambda x: (x[0], x[1].path))
        roh: list[Burst] = []
        for ts, m in reihe:
            if roh and _gehoert_dazu(roh[-1], ts, m, gap):
                roh[-1].items.append(m)
                roh[-1].times.append(ts)
            else:
                roh.append(Burst(items=[m], times=[ts]))
        for b in roh:
            out.extend(_teile_lange(b, max_span))
    return sorted(out, key=lambda b: (b.start, b.items[0].path))


def _gehoert_dazu(b: Burst, ts: float, m: MediaItem, gap: float) -> bool:
    if ts - b.end > gap:
        return False
    letzte_gps = next((x.gps for x in reversed(b.items) if x.gps), None)
    if m.gps and letzte_gps and distance_km(letzte_gps, m.gps) >= JUMP_KM:
        return False
    return True


def _teile_lange(b: Burst, max_span: float) -> list[Burst]:
    """Teilt eine zu lange Traube an ihrer groessten inneren Luecke.

    Geteilt wird an der groessten Luecke und nicht in der zeitlichen Mitte: die
    groesste Luecke ist die Stelle, an der am ehesten wirklich etwas anderes
    anfing.

    **Bei Gleichstand gewinnt die mittigste Luecke**, und daran haengt mehr als
    Geschmack. Eine Intervallaufnahme — Zeitraffer, Serienbild im festen Takt —
    hat lauter *gleich grosse* Luecken; die erste davon zu nehmen spaltet dann
    jedes Mal ein einziges Bild ab. Der Aufwand waere quadratisch, und die
    urspruenglich rekursive Fassung lief bei 1240 gleichmaessig verteilten
    Aufnahmen in einen ``RecursionError``. Mit der Mitte halbiert sich die
    Traube stattdessen.

    Iterativ statt rekursiv aus demselben Grund: die Tiefe haenge sonst an den
    Daten, und die kommen hier von aussen.
    """
    fertig: list[Burst] = []
    stapel = [b]
    while stapel:
        akt = stapel.pop()
        if len(akt) < 2 or (akt.end - akt.start) <= max_span:
            fertig.append(akt)
            continue
        mitte = (len(akt) - 1) / 2.0
        _, _, schnitt = max((akt.times[i + 1] - akt.times[i], -abs(i - mitte), i)
                            for i in range(len(akt) - 1))
        # Rechts zuerst auf den Stapel, damit links zuerst wieder herunterkommt
        # und ``fertig`` chronologisch bleibt.
        stapel.append(Burst(items=akt.items[schnitt + 1:],
                            times=akt.times[schnitt + 1:]))
        stapel.append(Burst(items=akt.items[:schnitt + 1],
                            times=akt.times[:schnitt + 1]))
    return fertig


# --------------------------------------------------------------------------
# Quote
# --------------------------------------------------------------------------

def day_quota(counts: dict, total: int, *, alpha: float = DAY_ALPHA,
              floor: int = MIN_PER_DAY, max_share: float = MAX_SHARE) -> dict:
    """Verteilt ``total`` Plaetze auf die Gruppen in ``counts``.

    ``counts`` bildet den Gruppenschluessel (Kalendertag, Ortscluster) auf die
    Zahl seiner **Trauben** ab — die Zahl seiner Bilder waere das falsche Mass
    (Entscheidung 6 des Briefings).

    Gleich viele je Tag kippt in die eine Richtung, proportional zum Material in
    die andere. Dazwischen liegt::

        n_j  proportional zu  c_j ** alpha

    Danach greifen Boden und Deckel. Und ueber allem steht ``n_j <= c_j``: mehr
    Bilder als Trauben kann ein Tag nicht stellen, ohne die Traubenregel zu
    brechen — die ist wichtiger als jede Quote.

    Die Ganzzahligkeit ist eine **Sitzverteilung**, kein Runden: erst abrunden,
    dann die verbleibenden Plaetze nach groesstem Rest. Sonst summieren sich 187
    gerundete Quoten auf 183 oder 191, und die Zielzahl waere nur ungefaehr
    getroffen.
    """
    aktiv = {k: c for k, c in counts.items() if c > 0}
    if not aktiv or total <= 0:
        return {k: 0 for k in counts}

    # Obergrenze je Gruppe. `max_share` soll den einen Tag baendigen, an dem
    # vierhundert Bilder entstanden — nicht die Verteilung ersetzen. Bei vier
    # Gruppen *sind* 25 Prozent aber schon der Gleichanteil: der Deckel traefe
    # dann jede Gruppe, erzwaenge exakte Gleichverteilung und machte `alpha`
    # wirkungslos. Deshalb liegt er nie unter dem Doppelten des Gleichanteils —
    # darunter waere er kein Deckel mehr, sondern die Quote selbst.
    gleichanteil = total / len(aktiv)
    deckel_abs = max(int(math.floor(max_share * total)),
                     int(math.ceil(2 * gleichanteil)), 1)
    obergrenze = {k: min(c, deckel_abs) for k, c in aktiv.items()}
    untergrenze = {k: min(c, max(0, floor)) for k, c in aktiv.items()}

    gewichte = {k: float(c) ** alpha for k, c in aktiv.items()}
    summe = sum(gewichte.values())
    roh = {k: total * g / summe for k, g in gewichte.items()}

    sitze = {k: int(math.floor(v)) for k, v in roh.items()}
    reste = sorted(aktiv, key=lambda k: (-(roh[k] - sitze[k]), str(k)))
    offen = total - sum(sitze.values())
    for k in reste[:max(0, offen)]:
        sitze[k] += 1

    sitze = {k: min(obergrenze[k], max(untergrenze[k], v)) for k, v in sitze.items()}
    sitze = _ausgleichen(sitze, obergrenze, untergrenze, gewichte, total)

    out = {k: 0 for k in counts}
    out.update(sitze)
    return out


def _ausgleichen(sitze: dict, obergrenze: dict, untergrenze: dict,
                 gewichte: dict, total: int) -> dict:
    """Bringt die Summe nach dem Klemmen wieder auf ``total``.

    Boden und Deckel koennen sich widersprechen — bei dreissig Tagen und einem
    Mindestbild je Tag ist eine Zielzahl von zwanzig nicht erfuellbar. Dann
    gewinnt der Boden, und die Summe bleibt darueber; der Aufrufer meldet das.
    Es gibt hier keine Loesung, nur eine Entscheidung: lieber ein paar Bilder
    mehr als ein Tag, der aus dem Film verschwindet.
    """
    sitze = dict(sitze)
    for _ in range(10000):
        diff = total - sum(sitze.values())
        if diff == 0:
            break
        if diff > 0:
            kandidaten = [k for k in sitze if sitze[k] < obergrenze[k]]
            if not kandidaten:
                break
            # Wer relativ zu seinem Gewicht am schlechtesten wegkommt, bekommt
            # den naechsten Platz — das haelt die Verteilung bei der Form, die
            # `alpha` vorgibt.
            k = min(kandidaten, key=lambda x: ((sitze[x] + 1) / gewichte[x], str(x)))
            sitze[k] += 1
        else:
            kandidaten = [k for k in sitze if sitze[k] > untergrenze[k]]
            if not kandidaten:
                break
            k = max(kandidaten, key=lambda x: (sitze[x] / gewichte[x], str(x)))
            sitze[k] -= 1
    return sitze


# --------------------------------------------------------------------------
# Spreizung
# --------------------------------------------------------------------------

def spread(gruppe: list[Burst], n: int, rng: random.Random) -> list[Burst]:
    """Waehlt ``n`` Trauben aus ``gruppe``, moeglichst gleichmaessig verteilt.

    Zufaellig zu ziehen waere einfacher und falsch: bei acht Bildern aus einem
    Tag kaemen regelmaessig fuenf vom Abendessen, weil dort am meisten
    fotografiert wurde. Gezogen wird deshalb ueber **Zielzeitpunkte** —
    gleichmaessig zwischen erster und letzter Aufnahme des Tages, und zu jedem
    die naechstgelegene noch freie Traube.

    Echte Uhrzeitluecken bleiben dabei respektiert: ein Zielzeitpunkt, der in
    die Mittagspause faellt, wandert auf deren Rand, und der Rand ist Ende oder
    Anfang einer Aktivitaet — genau die richtige Stelle.

    Der Zufall sitzt im **Jitter** der Zielzeitpunkte. Ohne ihn liefe jeder
    Seed auf dieselbe Auswahl hinaus, und ein zweiter Vorschlag waere nicht zu
    bekommen.
    """
    if n <= 0:
        return []
    if n >= len(gruppe):
        return list(gruppe)

    sortiert = sorted(gruppe, key=lambda b: b.mitte)
    t0, t1 = sortiert[0].mitte, sortiert[-1].mitte
    if t1 - t0 < 1e-9:
        return rng.sample(sortiert, n)

    schritt = (t1 - t0) / n
    frei = list(sortiert)
    gewaehlt: list[Burst] = []
    for i in range(n):
        ziel = t0 + (i + 0.5 + rng.uniform(-0.5, 0.5)) * schritt
        treffer = min(frei, key=lambda b: abs(b.mitte - ziel))
        frei.remove(treffer)
        gewaehlt.append(treffer)
    return sorted(gewaehlt, key=lambda b: b.mitte)


# --------------------------------------------------------------------------
# Wahl innerhalb der Traube
# --------------------------------------------------------------------------

def verwackelt(m: MediaItem) -> bool:
    """Freihandregel: Belichtungszeit laenger als 1/Brennweite.

    Kennt weder Stativ noch Stabilisator und liegt deshalb oft falsch — als
    Abwertung *innerhalb* einer Traube ist das verschmerzbar, als
    Ausschlussgrund waere es das nicht.

    Fehlt die Kleinbild-Brennweite (viele Kameras schreiben sie nicht, siehe
    :func:`slideshow.probe._kb_brennweite`), wird die reale genommen. Bei einem
    Crop-Sensor ist die Regel damit um den Cropfaktor zu lasch — sie schlaegt
    dann seltener an, und das ist die harmlose Richtung.
    """
    i = m.image
    if i is None or i.exposure_time <= 0:
        return False
    brennweite = i.focal_35 or i.focal
    if brennweite <= 0:
        return False
    return i.exposure_time > 1.0 / brennweite


def _punkte(m: MediaItem, b: Burst, groessen: dict[str, float]) -> float:
    """Punktzahl eines Bildes innerhalb seiner Traube."""
    p = 0.0
    p += 3.0 * min(5, max(0, m.rating))          # Sterne schlagen alles andere
    if len(b) >= 3:
        # Bei einer Serie ist meist das letzte das gemeinte: man drueckt, bis
        # es sitzt. Das erste ist oft der Testschuss.
        if m is b.items[-1]:
            p += 1.0
        elif m is b.items[0]:
            p -= 1.0
    p += groessen.get(m.id, 0.0)
    if verwackelt(m):
        p -= 1.0
    return p


def _groessen_z(b: Burst) -> dict[str, float]:
    """z-Wert der Dateigroesse je Bild, **getrennt nach Kamera**.

    Als globales Schaerfemass waere die Dateigroesse Unfug — ein Bild mit viel
    Himmel ist klein und trotzdem scharf. Innerhalb einer Traube ist der
    Vergleich aber fair: gleiches Motiv, gleiche Belichtung, gleiche
    JPEG-Einstellung, und eine verwackelte Aufnahme hat weniger hohe
    Ortsfrequenzen und komprimiert kleiner.

    Fair ist er allerdings **nur bei gleicher Kamera**. Fotografieren zwei
    Leute gleichzeitig, gewaenne sonst stillschweigend immer das Geraet mit der
    hoeheren JPEG-Qualitaet. Deshalb wird je Kamera getrennt gerechnet, und wo
    eine Kamera nur ein Bild beitraegt, faellt das Signal weg.
    """
    out: dict[str, float] = {}
    nach_kamera: dict[str, list[MediaItem]] = {}
    for m in b.items:
        nach_kamera.setdefault(m.camera, []).append(m)
    for gruppe in nach_kamera.values():
        if len(gruppe) < 2:
            continue
        werte = [float(m.size_bytes) for m in gruppe]
        mittel = sum(werte) / len(werte)
        streuung = math.sqrt(sum((w - mittel) ** 2 for w in werte) / len(werte))
        if streuung < 1e-9:
            continue
        for m, w in zip(gruppe, werte):
            # Geklemmt: ein einzelner Ausreisser soll die Sterne nicht schlagen.
            out[m.id] = max(-1.0, min(1.0, (w - mittel) / streuung))
    return out


def pick_in_burst(b: Burst, rng: random.Random) -> MediaItem:
    """Waehlt ein Bild aus der Traube — gewichtet gezogen, nicht bestbewertet.

    Ohne inhaltliche Analyse ist "das beste Bild" nicht bestimmbar. Ein
    deterministisches Verfahren macht dann nicht keinen Fehler, sondern immer
    denselben; die gewichtete Ziehung erlaubt stattdessen einen zweiten
    Vorschlag zum Vergleichen.
    """
    if len(b) == 1:
        return b.items[0]
    groessen = _groessen_z(b)
    punkte = [_punkte(m, b, groessen) for m in b.items]
    hoechster = max(punkte)
    gewichte = [math.exp(p - hoechster) for p in punkte]
    return rng.choices(b.items, weights=gewichte, k=1)[0]


# --------------------------------------------------------------------------
# Harte Filter
# --------------------------------------------------------------------------

def hard_filter(media: list[MediaItem], *, min_long_edge: int = MIN_LONG_EDGE,
                rating_min: int = 0) -> tuple[list[MediaItem], dict[str, str]]:
    """Trennt, was gar nicht erst in Frage kommt — mit Grund je Kennung.

    Der Grund wandert spaeter in die Kommentarzeile der ``order.yaml``. Ein
    stillschweigend verschwundenes Bild waere hier besonders aergerlich: es
    sieht aus wie eine Geschmacksentscheidung und ist eine technische.
    """
    ok: list[MediaItem] = []
    gruende: dict[str, str] = {}
    for m in media:
        if m.kind == "clip":
            ok.append(m)
            continue
        i = m.image
        if i is not None and min_long_edge > 0:
            lang = max(i.width, i.height)
            if 0 < lang < min_long_edge:
                gruende[m.id] = f"zu klein: {i.width}x{i.height}"
                continue
        if rating_min > 0 and m.rating < rating_min:
            gruende[m.id] = (f"{m.rating} statt {rating_min} Sterne"
                             if m.rating else "ohne Bewertung")
            continue
        ok.append(m)
    return (ok, gruende)


def panorama(m: MediaItem, *, ratio: float = PANORAMA_RATIO) -> bool:
    """Extremes Seitenverhaeltnis — in 16:9 wird daraus eine Briefmarke.

    Kein Ausschlussgrund: ein Panorama kann das beste Bild des Films sein, und
    Ken Burns kann darin fahren. Nur eine Markierung, damit man es sieht.
    """
    i = m.image
    if i is None or min(i.width, i.height) <= 0:
        return False
    return max(i.width, i.height) / min(i.width, i.height) >= ratio


# --------------------------------------------------------------------------
# Vielfalt als Nachkorrektur
# --------------------------------------------------------------------------

def balance_portrait(gewaehlt: dict[str, MediaItem], trauben: dict[str, Burst],
                     rng: random.Random, *, max_portrait: float = MAX_PORTRAIT
                     ) -> tuple[dict[str, MediaItem], list[str]]:
    """Tauscht Hochformate gegen Querformate, wenn ihr Anteil den Deckel reisst.

    Hochformatanteil ist eine Eigenschaft der *fertigen* Auswahl, nicht eines
    einzelnen Bildes. Deshalb steht das hier und nicht in :func:`_punkte`: vier
    gewichtete Ziele in einer Schleife kann hinterher niemand mehr erklaeren,
    und eine Nachkorrektur laesst sich melden.

    Getauscht wird nur **innerhalb derselben Traube** — ein anderes Motiv waere
    kein Tausch, sondern eine zweite Auswahl mit anderem Ergebnis. Findet sich
    dort kein Querformat, bleibt es, wie es ist, und die Meldung sagt es.
    """
    bilder = [m for m in gewaehlt.values() if m.kind == "image"]
    if not bilder:
        return (gewaehlt, [])
    hoch = [m for m in bilder if m.image is not None and m.image.portrait]
    erlaubt = int(math.floor(max_portrait * len(bilder)))
    zuviel = len(hoch) - erlaubt
    if zuviel <= 0:
        return (gewaehlt, [])

    out = dict(gewaehlt)
    getauscht = 0
    # Die schwaechsten zuerst: wer Sterne hat, bleibt.
    for m in sorted(hoch, key=lambda x: (x.rating, x.size_bytes)):
        if getauscht >= zuviel:
            break
        b = trauben.get(m.id)
        if b is None:
            continue
        quer = [x for x in b.items
                if x.image is not None and not x.image.portrait]
        if not quer:
            continue
        groessen = _groessen_z(b)
        ersatz = max(quer, key=lambda x: (_punkte(x, b, groessen), x.id))
        del out[m.id]
        out[ersatz.id] = ersatz
        trauben[ersatz.id] = b
        getauscht += 1

    meldungen: list[str] = []
    if getauscht:
        meldungen.append(f"{getauscht} Hochformate gegen Querformate aus derselben "
                         f"Traube getauscht (Anteil lag ueber "
                         f"{max_portrait:.0%})".replace("%", " Prozent"))
    if getauscht < zuviel:
        meldungen.append(f"{zuviel - getauscht} Hochformate bleiben — in ihrer Traube "
                         f"gibt es kein Querformat. Der Anteil liegt damit ueber "
                         f"{max_portrait:.0%}".replace("%", " Prozent"))
    return (out, meldungen)


# --------------------------------------------------------------------------
# Die Klammer
# --------------------------------------------------------------------------

@dataclass
class Selection:
    """Das Ergebnis einer Auswahl — alles, was die Datei und der Bericht brauchen."""

    #: Gewaehlte Medien-IDs in chronologischer Folge.
    ids: list[str] = field(default_factory=list)
    #: Gewaehlte ID -> die Geschwister derselben Traube, die draussen bleiben.
    alternativen: dict[str, list[MediaItem]] = field(default_factory=dict)
    #: Vollstaendig ausgelassene Trauben, chronologisch.
    ausgelassen: list[Burst] = field(default_factory=list)
    #: ID -> Grund eines harten Ausschlusses.
    gruende: dict[str, str] = field(default_factory=dict)
    #: Medien ohne verwertbaren Aufnahmezeitpunkt.
    ohne_datum: list[MediaItem] = field(default_factory=list)
    #: Gruppenschluessel -> (gewaehlt, Trauben, Aufnahmen) fuer den Bericht.
    quote: dict = field(default_factory=dict)
    meldungen: list[str] = field(default_factory=list)
    seed: int = 0
    ziel: int = 0
    params: dict = field(default_factory=dict)
    gesamt: int = 0


def select_media(manifest: Manifest, *, count: int, seed: int | None = None,
                 by: str = "day", gap: float = BURST_GAP,
                 max_span: float = BURST_MAX, alpha: float = DAY_ALPHA,
                 min_per_day: int = MIN_PER_DAY, max_share: float = MAX_SHARE,
                 min_long_edge: int = MIN_LONG_EDGE, rating_min: int = 0,
                 keep_clips: bool = True,
                 max_portrait: float = MAX_PORTRAIT) -> Selection:
    """Waehlt ``count`` Medien aus dem Manifest.

    Die Reihenfolge der Stufen ist nicht beliebig: harte Filter zuerst (sie
    aendern die Traubenbildung nicht, aber die Auswahl darin), dann Trauben,
    dann Quote, dann Spreizung, dann die Wahl im Einzelnen. Die
    Hochformat-Korrektur kommt zuletzt, weil sie die fertige Auswahl braucht.
    """
    if by not in BY_CHOICES:
        raise SlideshowError(f"unbekannte Gruppierung {by!r} (erwartet: "
                             f"{', '.join(BY_CHOICES)})")
    if count <= 0:
        raise SlideshowError(f"Zielzahl muss positiv sein, nicht {count}")

    if seed is None:
        seed = random.randrange(1, 1_000_000)
    rng = random.Random(seed)
    offsets = manifest.clock_offsets
    sel = Selection(seed=seed, ziel=count, gesamt=len(manifest.media))
    sel.params = {"by": by, "gap": gap, "alpha": alpha, "min_long_edge": min_long_edge,
                  "rating_min": rating_min, "max_share": max_share,
                  "min_per_day": min_per_day, "max_portrait": max_portrait}

    # -- ohne Zeitstempel: eigener Topf ---------------------------------
    # Diese Medien haben keine Position auf der Zeitachse. Traube und Quote
    # waeren fuer sie geraten, und einsortieren ist Handarbeit.
    datiert, sel.ohne_datum = [], []
    for m in manifest.media:
        ts = effective_capture_time(m, offsets)
        (sel.ohne_datum if ts is None or m.time_source == "none"
         else datiert).append(m)
    if sel.ohne_datum:
        sel.meldungen.append(
            f"{len(sel.ohne_datum)} Medien ohne Aufnahmezeitpunkt bleiben "
            f"draussen — sie haben keinen Platz auf der Zeitachse und wollen von "
            f"Hand einsortiert werden: {_liste([m.id for m in sel.ohne_datum])}")

    # -- harte Filter ---------------------------------------------------
    tauglich, sel.gruende = hard_filter(datiert, min_long_edge=min_long_edge,
                                        rating_min=rating_min)
    if sel.gruende:
        nach_grund: dict[str, int] = {}
        for grund in sel.gruende.values():
            schluessel = grund.split(":")[0]
            nach_grund[schluessel] = nach_grund.get(schluessel, 0) + 1
        teile = ", ".join(f"{n}x {g}" for g, n in sorted(nach_grund.items()))
        sel.meldungen.append(f"{len(sel.gruende)} Medien fallen technisch heraus "
                             f"({teile})")

    # -- Clips vorab ----------------------------------------------------
    # Clips sind wenige, gewollt und belegen mehr als einen Standardslot. Sie
    # gegen Bilder um dieselben Plaetze antreten zu lassen hiesse, sie dem
    # Zufall zu ueberlassen — bei drei Clips auf tausend Bildern faellt dann
    # regelmaessig gar keiner an.
    clips = [m for m in tauglich if m.kind == "clip"] if keep_clips else []
    bilder = [m for m in tauglich if m.kind == "image"]
    rest = max(0, count - len(clips))
    if clips and rest == 0:
        sel.meldungen.append(f"{len(clips)} Clips fuellen die Zielzahl bereits aus — "
                             f"es bleibt kein Platz fuer Bilder")

    # -- Trauben --------------------------------------------------------
    alle = bursts(bilder, offsets, gap=gap, max_span=max_span)
    gruppen = _gruppieren(alle, offsets, by=by)

    # -- Quote und Spreizung --------------------------------------------
    counts = {k: len(v) for k, v in gruppen.items()}
    quoten = day_quota(counts, rest, alpha=alpha, floor=min_per_day,
                       max_share=max_share)
    vergeben = sum(quoten.values())
    if vergeben > rest:
        sel.meldungen.append(
            f"{vergeben} statt {rest} Bilder: bei {len(counts)} Gruppen und "
            f"mindestens {min_per_day} Bild je Gruppe geht die Zielzahl nicht auf. "
            f"Entweder mehr Bilder zulassen oder `--min-per-day 0` setzen.")

    gewaehlt: dict[str, MediaItem] = {}
    traube_von: dict[str, Burst] = {}
    genommen: set[int] = set()
    for schluessel, gruppe in gruppen.items():
        treffer = spread(gruppe, quoten.get(schluessel, 0), rng)
        for b in treffer:
            m = pick_in_burst(b, rng)
            gewaehlt[m.id] = m
            traube_von[m.id] = b
            genommen.add(id(b))
        sel.quote[schluessel] = (len(treffer), len(gruppe),
                                 sum(len(b) for b in gruppe))

    # -- Vielfalt -------------------------------------------------------
    gewaehlt, hinweise = balance_portrait(gewaehlt, traube_von, rng,
                                          max_portrait=max_portrait)
    sel.meldungen += hinweise

    # -- Ergebnis zusammensetzen ----------------------------------------
    for mid, m in gewaehlt.items():
        b = traube_von[mid]
        geschwister = [x for x in b.items if x.id != mid]
        if geschwister:
            sel.alternativen[mid] = geschwister
    sel.ausgelassen = [b for b in alle if id(b) not in genommen]

    reihe = [m for m in chronological(manifest)
             if m.id in gewaehlt or (keep_clips and m in clips)]
    sel.ids = [m.id for m in reihe]
    sel.meldungen += _pruefe_abstaende(reihe, offsets, gap)
    sel.meldungen += _pruefe_deckung(sel, alle, count, gap)
    return sel


def _pruefe_deckung(sel: Selection, alle: list[Burst], count: int,
                    gap: float) -> list[str]:
    """Meldet, wenn die Zielzahl nicht erreicht wurde — und warum.

    Der Fall sieht harmlos aus und ist es nicht: wer ``--count auto`` ruft und
    zweihundert Slots bekommt, aber nur neunzehn Bilder, hat kein
    Auswahlproblem, sondern zu wenig Material. Das faellt sonst erst bei
    ``build`` auf, nach dem Normalisieren — und dann ist eine Stunde weg.

    Unterschieden wird, woran es liegt, denn die Auswege sind
    entgegengesetzte: an der Traubenregel (mehr Material laege da, aber es
    zeigt dasselbe) oder schlicht am Bestand.
    """
    fehlend = count - len(sel.ids)
    if fehlend <= 0 or fehlend < max(1, count // 20):
        return []

    if len(alle) <= len(sel.ids):
        return [f"{len(sel.ids)} statt {count} Medien — mehr gibt das Material "
                f"nicht her, ohne zwei Aufnahmen derselben Traube zu nehmen. "
                f"Entweder mehr Material, kuerzere Musik, laengere Standzeiten "
                f"(`build --still-seconds`/`--beats-per-still`) oder ein "
                f"kleineres `--burst-gap` als {gap:g} s."]
    return [f"{len(sel.ids)} statt {count} Medien — die Quote hat nicht alles "
            f"vergeben, obwohl {len(alle)} Trauben da sind. Das liegt am Deckel: "
            f"`--max-share` oder `--min-per-day` anpassen."]


def _pruefe_abstaende(reihe: list[MediaItem], offsets: dict[str, float],
                      gap: float) -> list[str]:
    """Zaehlt, was trotz Traubenregel dicht beieinander liegt.

    Uebrig bleiben darf nur die Geraeteausnahme: zwei Kameras, die gleichzeitig
    laufen, haben getrennte Trauben, und aus beiden darf gewaehlt werden. Bleibt
    ein Paar *derselben* Kamera uebrig, ist die Traubenbildung kaputt — und
    zwar auf eine Art, die im fertigen Film als Doppelung auffaellt und sonst
    nirgends. Deshalb wird hier nachgezaehlt statt darauf zu vertrauen.
    """
    paare: list[tuple[MediaItem, MediaItem]] = []
    letzter: tuple[float, MediaItem] | None = None
    for m in reihe:
        ts = effective_capture_time(m, offsets)
        if ts is None:
            continue
        if letzter is not None and ts - letzter[0] < gap:
            paare.append((letzter[1], m))
        letzter = (ts, m)

    gleiche = [(a, b) for a, b in paare if a.camera == b.camera]
    out: list[str] = []
    if gleiche:
        out.append(f"{len(gleiche)} Paare derselben Kamera liegen enger als "
                   f"{gap:g} s beieinander — das sollte die Traubenregel "
                   f"ausschliessen: "
                   f"{_liste([f'{a.id}/{b.id}' for a, b in gleiche])}")
    if len(paare) > len(gleiche):
        out.append(f"{len(paare) - len(gleiche)} Paare liegen enger als {gap:g} s, "
                   f"stammen aber von verschiedenen Geraeten — zwei Blickwinkel "
                   f"auf dieselbe Szene, beide bleiben")
    return out


def _gruppieren(trauben: list[Burst], offsets: dict[str, float], *,
                by: str) -> dict:
    """Ordnet die Trauben der Quotierungsachse zu.

    ``day`` nimmt den **Kalendertag**, nicht eine Zeitluecke: eine Aufnahme um
    23:50 und eine um 00:10 liegen zwanzig Minuten auseinander und trotzdem an
    verschiedenen Tagen. Dieselbe Entscheidung wie in
    :func:`slideshow.order._nach_tagen`.
    """
    out: dict = {}
    if by == "none":
        out["alle"] = list(trauben)
        return out
    if by == "day":
        for b in trauben:
            out.setdefault(b.tag, []).append(b)
        return dict(sorted(out.items()))

    # ``place``: Ortscluster ueber GPS. Trauben ohne Fix bleiben beim laufenden
    # Cluster — sie entstanden zwischen zwei verorteten Aufnahmen.
    anker: tuple[float, float] | None = None
    nummer = 0
    for b in sorted(trauben, key=lambda x: x.mitte):
        gps = next((m.gps for m in b.items if m.gps), None)
        sprung = distance_km(anker, gps) if (anker and gps) else None
        if nummer == 0 or (sprung is not None and sprung >= JUMP_KM):
            nummer += 1
            anker = gps or anker
        elif gps and anker is None:
            anker = gps
        out.setdefault(f"ort-{nummer}", []).append(b)
    return out


def _liste(ids: list[str]) -> str:
    gezeigt = ", ".join(ids[:MAX_GENANNT])
    rest = len(ids) - MAX_GENANNT
    return gezeigt + (f" (+{rest} weitere)" if rest > 0 else "")


# --------------------------------------------------------------------------
# Ausgabe
#
# Geschrieben wird von Hand, nicht ueber ``yaml.dump`` — derselbe Grund wie bei
# ``order.yaml`` und ``chapters.yaml``: die Datei ist ein **Formular**. Ein
# anderes Bild zu nehmen heisst, zwei Zeilen zu tauschen, und dafuer muss neben
# jeder Zeile stehen, was dort steht. Ein YAML-Dumper wirft die Kommentare weg,
# und damit die halbe Datei.
# --------------------------------------------------------------------------

def dump_selection_yaml(sel: Selection, manifest: Manifest) -> str:
    """Schreibt die Auswahl als ``order.yaml`` mit ``rest: drop``."""
    offsets = manifest.clock_offsets
    by_id = {m.id: m for m in manifest.media}
    tag_eins = first_day(chronological(manifest), offsets)

    zeilen = _kopf(sel)
    gewaehlt = [by_id[i] for i in sel.ids if i in by_id]
    if not gewaehlt:
        return "\n".join(zeilen + ["  # Nichts ausgewaehlt."]) + "\n"

    # Die ausgelassenen Trauben werden zwischen die gewaehlten Zeilen
    # eingeflochten, nicht ans Ende gehaengt: nur an ihrem zeitlichen Platz
    # sagen sie, *was* dort uebersprungen wurde. Am Ende waeren sie eine
    # zweite Datei.
    offen = sorted(sel.ausgelassen, key=lambda b: b.mitte)
    idx = 0
    letzter_tag: _dt.date | None = None

    for m in gewaehlt:
        ts = effective_capture_time(m, offsets) or 0.0
        tag = _dt.datetime.fromtimestamp(ts).date()
        if tag != letzter_tag:
            # Vor dem Tageswechsel noch alles ausgeben, was am alten Tag liegt.
            idx = _ausgelassene(zeilen, offen, idx, bis=_tagesbeginn(tag),
                                offsets=offsets)
            zeilen += _blockkopf(tag, tag_eins, sel, gewaehlt, offsets)
            letzter_tag = tag
        idx = _ausgelassene(zeilen, offen, idx, bis=ts, offsets=offsets)
        zeilen.append(f"      - {m.id}{_zeilenkontext(m, sel, offsets)}")
        zeilen += _alternativen(m, sel, offsets)

    idx = _ausgelassene(zeilen, offen, idx, bis=float("inf"), offsets=offsets)
    zeilen += _restbloecke(sel, by_id, offsets)
    zeilen += [
        "",
        "# Ein anderes Bild nehmen: die Kommentarzeile eintragen und die",
        "# gewaehlte auskommentieren. Was hier als Kommentar steht, bietet",
        "# `slideshow order --update` nie wieder als neu an.",
    ]
    return "\n".join(zeilen) + "\n"


def _kopf(sel: Selection) -> list[str]:
    p = sel.params
    komma = lambda x: f"{x:g}".replace(".", ",")            # noqa: E731
    return [
        "# order.yaml — Reihenfolge der Medien. Wird von `slideshow build` eingelesen.",
        "#",
        f"# Erzeugt von `slideshow select`: {len(sel.ids)} von {sel.gesamt} Medien",
        f"# gewaehlt. Alles Uebrige steht als Kommentar in dieser Datei und bleibt",
        f"# draussen (`rest: drop`).",
        "#",
        f"#   Zielzahl {sel.ziel} · Seed {sel.seed} · Traubenabstand "
        f"{komma(p.get('gap', BURST_GAP))} s · Tagesgewicht "
        f"{komma(p.get('alpha', DAY_ALPHA))}",
        f"#   Mindestlangkante {p.get('min_long_edge', MIN_LONG_EDGE)} px"
        + (f" · ab {p['rating_min']} Sternen" if p.get("rating_min") else ""),
        "#",
        "# Denselben Vorschlag noch einmal:  slideshow select --seed "
        f"{sel.seed} --force",
        "# Einen anderen Vorschlag:          slideshow select --force",
        "#",
        "# Die Auswahl ansehen:              slideshow sheet",
        "",
        "version: 1",
        "",
        "# `drop` laesst weg, was hier nicht steht — das ist bei einer Auswahl der",
        "# Sinn der Sache. `append` haenge alles Uebrige hinten an, `error` bricht ab.",
        "rest: drop",
        "",
        "groups:",
    ]


def _tagesbeginn(tag: _dt.date) -> float:
    return _dt.datetime.combine(tag, _dt.time.min).timestamp()


def _blockkopf(tag: _dt.date, tag_eins: _dt.date | None, sel: Selection,
               gewaehlt: list[MediaItem], offsets: dict[str, float]) -> list[str]:
    ts = _dt.datetime.combine(tag, _dt.time(12, 0)).timestamp()
    nummer = (tag - tag_eins).days + 1 if tag_eins else 1
    zahlen = sel.quote.get(tag)
    if zahlen:
        n, trauben, aufnahmen = zahlen
        info = (f"{aufnahmen} Aufnahmen in {trauben} Trauben, {n} gewaehlt")
    else:
        info = f"{sum(1 for m in gewaehlt if _tag_von(m, offsets) == tag)} gewaehlt"
    return ["", f"  # {day_label(ts, tag_eins)} · {info}",
            f"  - name: tag-{nummer}", "    items:"]


def _tag_von(m: MediaItem, offsets: dict[str, float]) -> _dt.date | None:
    ts = effective_capture_time(m, offsets)
    return _dt.datetime.fromtimestamp(ts).date() if ts is not None else None


def _zeilenkontext(m: MediaItem, sel: Selection, offsets: dict[str, float]) -> str:
    """Der Kommentar hinter einer gewaehlten Zeile.

    Bewusst knapper als :func:`slideshow.order._kontext`: der Tag steht schon
    ueber dem Block, und bei zweihundert Zeilen zaehlt jede Wiederholung. Dafuer
    steht hier, was dort niemanden interessiert — wie viele Aufnahmen hinter
    diesem einen Bild stehen.
    """
    teile: list[str] = []
    ts = effective_capture_time(m, offsets)
    if ts is not None:
        teile.append(f"{_dt.datetime.fromtimestamp(ts):%H:%M}")
    if m.kind == "clip":
        info = m.clip
        dauer = (info.cache_duration or info.effective_duration) if info else 0.0
        teile.append(f"Clip {dauer:.1f} s".replace(".", ","))
    elif m.image is not None:
        teile.append("hoch" if m.image.portrait else "quer")
    if panorama(m):
        teile.append("Panorama")
    if m.rating:
        teile.append("*" * m.rating)
    geschwister = sel.alternativen.get(m.id)
    if geschwister:
        teile.append(f"Traube mit {len(geschwister) + 1}")
    return f"   # {' · '.join(teile)}" if teile else ""


def _alternativen(m: MediaItem, sel: Selection,
                  offsets: dict[str, float]) -> list[str]:
    """Die Geschwister derselben Traube — vollstaendig.

    Sie zeigen fast immer dasselbe Motiv. Genau hier tauscht man, wenn das
    gewaehlte Bild unscharf ist oder jemand die Augen zu hat, und deshalb
    stehen sie vollstaendig da und nicht gezaehlt.
    """
    geschwister = sel.alternativen.get(m.id)
    if not geschwister:
        return []
    out: list[str] = []
    for teil in _haeppchen(geschwister, 3):
        namen = " · ".join(f"{x.id} ({_uhr(x, offsets)})" for x in teil)
        out.append(f"      #  statt: {namen}" if not out
                   else f"      #         {namen}")
    return out


def _ausgelassene(zeilen: list[str], offen: list[Burst], idx: int, *,
                  bis: float, offsets: dict[str, float]) -> int:
    """Gibt alle ausgelassenen Trauben aus, die vor ``bis`` liegen.

    Zusammengefasst, mit **einem Vertreter je Traube** statt aller Aufnahmen:
    bei tausend Bildern stuenden sonst tausend IDs in der Datei, und niemand
    liest sie. Der Vertreter ist das bestbewertete Bild seiner Traube — also
    das, was man beim Reinnehmen ohnehin naehme. Die vollstaendige Liste zeigt
    der Kontaktbogen.
    """
    genommen: list[Burst] = []
    while idx < len(offen) and offen[idx].mitte < bis:
        genommen.append(offen[idx])
        idx += 1
    if not genommen:
        return idx

    aufnahmen = sum(len(b) for b in genommen)
    zeilen.append("      #")
    zeilen.append(f"      #  ausgelassen {_spanne(genommen)} — "
                  f"{_zahlwort(aufnahmen, 'Aufnahme', 'Aufnahmen')} in "
                  f"{_zahlwort(len(genommen), 'Traube', 'Trauben')}:")
    vertreter = {id(b): _vertreter(b) for b in genommen}
    for teil in _haeppchen([vertreter[id(b)] for b in genommen], 3):
        namen = " · ".join(f"{x.id} ({_uhr(x, offsets)})" for x in teil)
        zeilen.append(f"      #  {namen}")

    # Die uebrigen Aufnahmen derselben Trauben, nur als Namen und dicht
    # gepackt. Sie muessen genannt werden, auch wenn sie niemand liest:
    # `slideshow order --update` bietet jede ID, die *nirgends* in der Datei
    # steht, beim naechsten Lauf erneut als "neu" an — eine Abwahl waere nach
    # dem dritten Mal unauffindbar. Genannt heisst hier abgewaehlt.
    uebrige = [m.id for b in genommen for m in b.items
               if m.id != vertreter[id(b)].id]
    if uebrige:
        zeilen.append(f"      #  dieselben Trauben, {len(uebrige)} weitere:")
        for teil in _haeppchen(uebrige, 6):
            zeilen.append(f"      #  {' · '.join(teil)}")
    zeilen.append("      #")
    return idx


def _vertreter(b: Burst) -> MediaItem:
    """Das beste Bild einer ausgelassenen Traube — deterministisch.

    Hier wird nicht gezogen, sondern genommen: die Zeile ist ein Vorschlag zum
    Nachschlagen, kein Teil des Films. Ein Zufall darin waere nur verwirrend.
    """
    if len(b) == 1:
        return b.items[0]
    groessen = _groessen_z(b)
    return max(b.items, key=lambda m: (_punkte(m, b, groessen), m.id))


def _restbloecke(sel: Selection, by_id: dict[str, MediaItem],
                 offsets: dict[str, float]) -> list[str]:
    """Was keinen zeitlichen Platz hat: technische Ausschluesse und Datumslose.

    Beide stehen am Ende und **nicht** zwischen den anderen. Ein zu kleines
    Bild ist kein Kandidat fuer einen Tausch — es bleibt zu klein, egal wie
    schoen es ist —, und zwischen den Alternativen stuende es nur im Weg.
    """
    out: list[str] = []
    if sel.gruende:
        out += ["", f"  # {len(sel.gruende)} Medien kommen technisch nicht in Frage.",
                "  # Sie stehen hier, damit man sieht, dass es sie gibt — nicht zum",
                "  # Einsortieren. Ein zu kleines Bild bleibt zu klein.",
                "  - name: technisch-ausgeschlossen", "    items: []"]
        for mid, grund in sorted(sel.gruende.items()):
            out.append(f"      # - {mid}   # {grund}")
    if sel.ohne_datum:
        out += ["", f"  # {len(sel.ohne_datum)} Medien ohne Aufnahmezeitpunkt. Sie haben",
                "  # keinen Platz auf der Zeitachse — wer sie will, traegt sie dort ein,",
                "  # wo sie hingehoeren.",
                "  - name: ohne-datum", "    items: []"]
        for m in sel.ohne_datum:
            out.append(f"      # - {m.id}   # {m.time_source}")
    return out


def _haeppchen(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def _uhr(m: MediaItem, offsets: dict[str, float]) -> str:
    ts = effective_capture_time(m, offsets)
    return _uhrzeit(ts) if ts is not None else "?"


def _uhrzeit(ts: float) -> str:
    return f"{_dt.datetime.fromtimestamp(ts):%H:%M}"


def _zahlwort(n: int, einzahl: str, mehrzahl: str) -> str:
    return f"{n} {einzahl if n == 1 else mehrzahl}"


def _spanne(genommen: list[Burst]) -> str:
    """Zeitspanne einer Folge ausgelassener Trauben.

    Innerhalb eines Tages reicht die Uhrzeit — das Datum steht ueber dem Block.
    Ueber eine Tagesgrenze hinweg nicht: eine Spanne "22:17 bis 09:04" laese
    sich sonst als Nacht *oder* als anderthalb Tage lesen. Das kommt vor, wenn
    ein Tag gar kein Bild bekommen hat.
    """
    a, b = genommen[0].start, genommen[-1].end
    tag_a = _dt.datetime.fromtimestamp(a).date()
    tag_b = _dt.datetime.fromtimestamp(b).date()
    if tag_a != tag_b:
        return (f"{_dt.datetime.fromtimestamp(a):%d.%m. %H:%M} bis "
                f"{_dt.datetime.fromtimestamp(b):%d.%m. %H:%M}")
    von, nach = _uhrzeit(a), _uhrzeit(b)
    return von if von == nach else f"{von} bis {nach}"
