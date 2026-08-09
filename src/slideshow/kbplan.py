"""Inhaltsabhaengige Kamerafahrt — von Bildfakten zur Bewegung
(``docs/briefing-kenburns-inhaltsabhaengig.md``, Abschnitt 6 und 7).

Reine Rechnung ohne Datei-I/O, dieselbe Aufteilung wie :mod:`slideshow.select`
und :mod:`slideshow.order`: hier steht, *wie* aus einem Eintrag in
``vision.yaml`` eine ``KBSpec`` wird, und nirgends, woher der Eintrag kommt.

Drei Ebenen, in dieser Reihenfolge der Wichtigkeit (Abschnitt 2):

**Schutz (hart).** Was nicht angeschnitten werden darf, wird zu keinem
Zeitpunkt angeschnitten. Das ist der Teil, der heute sichtbar schiefgeht, und
er ist als geometrische Nebenbedingung exakt pruefbar — :func:`schutz_haelt`.

**Passung (weich).** Die Bewegung folgt der Bildaussage: Panorama entlang der
Bildachse, Portraet aufs Gesicht zu, Makro nahezu Stillstand. Steht in
:data:`REGELN`.

**Abwechslung (global).** Ueber die Bildfolge hinweg wiederholen sich
Bewegungen nicht auffaellig — und zwar **ohne** die Position wieder
hereinzuholen. Abschnitt 6 liefert je Bild nicht *eine* Bewegung, sondern eine
nach Passung sortierte Liste zulaessiger Kandidaten; gewaehlt wird daraus ueber
denselben Kennungs-Hash, an dem schon :func:`slideshow.kenburns.plan_motion`
haengt. Das ist strikt besser als heute — die Kandidaten sind passend *und*
zulaessig — und kostet die Positionsunabhaengigkeit nicht.

Was dabei nicht zu haben ist, gehoert dazugesagt: eine harte Zusage "kein
Signatur-Duplikat unter drei aufeinanderfolgenden Bildern" gibt es nicht. Sie
waere nur ueber eine Sequenzbetrachtung zu haben, und die macht die Bewegung
eines Bildes von seinen Nachbarn abhaengig — ein eingefuegtes Kapitel rendert
dann den Rest des Films neu. Genau diesen Preis hat ``main`` in 2a401f9
bewusst verkauft; er wird hier nicht zurueckgekauft.

**Die Klemmung fuehrt dieser Planer selbst.** ``_pan`` in ``kenburns`` deckelt
den Schwenkweg auf das, was der groesste Zoom hergibt — aber nur, solange
niemand ``c`` selbst hinschreibt. Ein Planer, der ``c0``/``c1`` setzt, umgeht
``_pan`` vollstaendig und damit auch den Deckel. Ohne die Ungleichung aus
:func:`klemmung_haelt` waere das geplante Fenster nicht das sichtbare, und jede
Schutzzusage gaelte fuer ein Rechteck, das gar nicht auf dem Schirm ist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Die acht Richtungen kommen aus ``kenburns`` und werden hier **nicht** noch
# einmal hingeschrieben: eine Signatur ``richtung: 3`` muss dort denselben
# Vektor meinen wie hier, sonst laufen Planer und Renderpfad auseinander,
# sobald jemand an der Liste dreht.
from .kenburns import _DIRECTIONS, motion_key, plan_motion, zoom_from_duration
from .models import KBDefaults, KBSpec, VisionEntry

#: Wie viele der bestpassenden Kandidaten der Hash zur Auswahl bekommt.
#: ``1`` ist reine Passung (und damit maximale Monotonie), grosse Werte
#: erkaufen Abwechslung mit Bewegungen, die schlechter zum Bild passen.
VARIETY = 4

#: Harte Zoomgrenze aus :func:`slideshow.kenburns.sanity_check` — darueber
#: reicht der Subpixel-Vorrat nicht und das Bild wird weich.
Z_HART = 2.0

#: Spanne, ueber die die Detaildichte den Zoom deckelt: ein detailarmer Himmel
#: vertraegt die vollen 30 %, ein Makro nichts davon (6.3, Schritt 2).
DETAIL_SPANNE = 0.30

#: Bis hierher darf der Planer den Zoom *anheben*, um einen Motivschwenk zu
#: bezahlen (6.3, Schritt 4). Der Schwenk ist die auffaelligere Bewegung; ein
#: Bild, das durchgehend um 15 % vergroessert steht, faellt niemandem auf.
#: Darueber wird gekuerzt statt gezoomt — und das gehoert gemeldet.
Z_ANHEBEN = 1.15

#: Stuetzstellen der Schutzpruefung ueber die volle Fahrt (6.3, Schritt 5).
#: Neun, nicht fuenf: fuer den Schutz lautet die Bedingung
#: ``|c(e) - b| + h <= 1/(2 z(e))``, und dort stehen auf beiden Seiten konvexe
#: Funktionen von ``e``. Ein Randargument gibt es dafuer nicht — die Verletzung
#: kann in der Mitte liegen. Neun Stellen bleiben trotzdem eine Stichprobe;
#: deshalb schliesst sich das Halbieren aus :func:`plan_kb` daran an.
STUETZSTELLEN = 9

#: Hoechstens so oft wird der Schwenk halbiert, bevor der Planer auf reinen
#: Zoom zurueckfaellt.
HALBIERUNGEN = 3

#: Wegfaktoren der drei Weiten. Die Signatur unterscheidet sie, damit die
#: Abwechslung eine dritte Achse hat und nicht nur an Zoomrichtung und
#: Himmelsrichtung haengt.
WEITEN = {"S": 0.40, "M": 0.70, "L": 1.00}

#: Sicherheitsabstand zur Klemmgrenze, den das **Runden** verlangt.
#:
#: Geplant wird in Gleitkomma, geschrieben wird auf vier Nachkommastellen
#: (:meth:`Bewegung.as_spec`) — und was in ``edit.yaml`` steht, ist das, was
#: gerendert wird. Ein Schwenk exakt auf der Grenze rundet nach aussen und
#: verletzt damit Abnahmekriterium A1b, obwohl der Planer richtig gerechnet
#: hat. Der Abstand deckt beide Rundungen ab: die der Bildmitte (bis 5e-5) und
#: die des Zooms, ueber den die Grenze selbst wandert (bis 2e-5).
RUNDUNGSMARGE = 2e-4


# --------------------------------------------------------------------------
# Regeltabelle (6.1)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Regel:
    """Was eine Szenenklasse von der Bewegung will.

    Bewusst eine Praeferenz und keine Vorschrift: die Regel gewichtet die
    Kandidaten, sie waehlt nicht. Sonst haette Abschnitt 7 nichts mehr zu tun.
    """

    #: +1 hineinfahren, -1 herausfahren, 0 egal.
    zoom: int
    #: Zusaetzlicher Deckel auf den *Gesamtzoom* (nicht auf den Faktor).
    #: ``None`` heisst: es gilt nur, was Dauer, Detaildichte und Schutz sagen.
    zoom_total: float | None
    #: Woran sich der Schwenk ausrichtet.
    #: ``achse`` — an ``axis``, in beide Richtungen gleich gut.
    #: ``focus`` — auf den Zielpunkt zu, gerichtet.
    #: ``keiner`` — Stillstand ist die Absicht.
    #: ``frei`` — keine Praeferenz, alle Richtungen gleich.
    ziel: str
    #: Bevorzugte Weite; ``"0"`` heisst "am liebsten gar kein Schwenk".
    weite: str


#: Kleinste Zoomspanne, die noch als Bewegung durchgeht.
#: :func:`slideshow.kenburns.sanity_check` meldet "praktisch keine Bewegung"
#: unterhalb von 0,005 — und A7 laesst diese Meldung nur fuer ``document`` zu,
#: wo der Stillstand die Absicht ist. Bei ``detail: 1.0`` faellt der Deckel aus
#: Schritt 2 sonst genau auf 1,0, und ein Makro stuende ohne Absicht still.
Z_MIN_BEWEGUNG = 1.01

REGELN: dict[str, Regel] = {
    # Kein ``zoom_total``-Deckel, obwohl die Tabelle in 6.1 "Zoom klein"
    # sagt: der Zoom ist hier das *Zahlungsmittel* fuer den Schwenk
    # (``weg <= 0,5 - 1/(2z)``), und ein Deckel von 0,15 begrenzte den
    # "grossen Schwenk" derselben Zeile auf 0,065. Die Regel schluege sich
    # selbst. Klein bleibt der Zoom trotzdem, solange die Dauer nichts
    # anderes verlangt — ``zoom_rate`` deckelt ihn ohnehin.
    "landscape_wide":  Regel(zoom=0,  zoom_total=None, ziel="achse",  weite="L"),
    "portrait_person": Regel(zoom=+1, zoom_total=None, ziel="focus",  weite="S"),
    "group":           Regel(zoom=-1, zoom_total=0.15, ziel="focus",  weite="S"),
    "architecture":    Regel(zoom=0,  zoom_total=None, ziel="achse",  weite="L"),
    "detail_macro":    Regel(zoom=0,  zoom_total=0.08, ziel="keiner", weite="0"),
    "action":          Regel(zoom=-1, zoom_total=None, ziel="achse",  weite="M"),
    "interior":        Regel(zoom=+1, zoom_total=None, ziel="focus",  weite="S"),
    "document":        Regel(zoom=0,  zoom_total=0.0,  ziel="keiner", weite="0"),
}


# --------------------------------------------------------------------------
# Ergebnistypen
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Signatur:
    """Was zwei Bewegungen fuer das Auge unterscheidbar macht.

    Die Einheit, in der Abschnitt 7 zaehlt und Abnahmekriterium A2 misst.
    """

    zoom_in: bool
    #: Index in ``_DIRECTIONS``; ``None`` heisst: kein Schwenk.
    richtung: int | None
    #: ``S`` | ``M`` | ``L``, oder ``-`` wenn nicht geschwenkt wird.
    weite: str

    def __str__(self) -> str:
        z = "ein" if self.zoom_in else "aus"
        return f"{z}/{'-' if self.richtung is None else self.richtung}/{self.weite}"


@dataclass(frozen=True)
class Bewegung:
    """Ein zulaessiger Kandidat — Klemmkette und Schutzpruefung bestanden."""

    z0: float
    z1: float
    c0: tuple[float, float]
    c1: tuple[float, float]
    signatur: Signatur
    passung: float
    #: Anteil, um den der gewuenschte Schwenkweg gekuerzt werden musste.
    #: ``0.0`` heisst: voll bezahlt. Das ist eine Entscheidung, keine Rundung,
    #: und gehoert deshalb in den Bericht (Abschnitt 12).
    gekuerzt: float = 0.0

    def as_spec(self) -> KBSpec:
        return KBSpec(z=(round(self.z0, 4), round(self.z1, 4)),
                      c=(round(self.c0[0], 4), round(self.c0[1], 4),
                         round(self.c1[0], 4), round(self.c1[1], 4)))


@dataclass(frozen=True)
class Ergebnis:
    """Was der Planer je Bild liefert."""

    spec: KBSpec
    signatur: Signatur
    gekuerzt: float
    #: Wie viele Kandidaten der Hash zur Auswahl hatte. ``1`` heisst: das Bild
    #: liess genau eine Bewegung zu — dort gibt es keine Abwechslung zu holen.
    kandidaten: int
    #: Der Schwenk fiel einer **Schutzbox** zum Opfer, nicht der Klemmung.
    #: Zwei verschiedene Befunde mit demselben Symptom: das eine ist ein zu
    #: kleines ``zoom_total``, das andere ein Motiv, an das man nicht heranfahren
    #: kann, ohne es anzuschneiden. Im Bericht duerfen sie nicht zusammenfallen.
    schutz_erzwungen: bool = False


# --------------------------------------------------------------------------
# Geometrie
# --------------------------------------------------------------------------

def halbfenster(z: float) -> float:
    """Halbe Kantenlaenge des sichtbaren Fensters bei Zoom ``z``.

    In beiden Achsen dieselbe Zahl, und das ist kein Zufall: jedes Cache-Bild
    hat exakt das Ausgabeseitenverhaeltnis (Normalform aus dem Preprocessing).
    Damit ist das sichtbare Fenster in normalisierten Koordinaten ein Quadrat
    der relativen Breite **und** Hoehe ``1/z`` um die Mitte — keine
    Seitenverhaeltnis-Umrechnung, keine Sonderfaelle.
    """
    return 1.0 / (2.0 * max(z, 1e-9))


def klemm_grenze(z: float) -> float:
    """Wie weit die Bildmitte bei Zoom ``z`` aus der Bildmitte darf.

    ``zoompan`` klemmt die Fensterposition an den Bildrand
    (``max(0, min(iw - iw/zoom, ...))``). Unbeschnitten bleibt eine Mitte ``c``
    nur, solange ``|c - 0,5| <= 0,5 - 1/(2z)`` gilt. Bei ``z = 1,0`` ist die
    rechte Seite **null**: dort wird die Mitte zwangsweise auf 0,5 geklemmt.
    """
    return max(0.0, 0.5 - halbfenster(z))


def klemmung_haelt(z0: float, z1: float, c0, c1, *, eps: float = 1e-6) -> bool:
    """Liegt die ganze Bahn innerhalb der Klemmung? (Abnahmekriterium A1b)

    **Die Pruefung an den beiden Enden ist vollstaendig, nicht bloss eine
    Stichprobe.** Ueber den Fahrtparameter ``e`` ist ``|c(e) - 0,5|`` konvex
    (Betrag einer Geraden) und ``0,5 - 1/(2 z(e))`` konkav (``z`` linear in
    ``e``, die Schranke konkav wachsend in ``z``). Ihre Differenz ist konvex
    und nimmt ihr Maximum an einem Rand an. Wer beide Enden prueft, hat die
    ganze Bahn geprueft.
    """
    for z, c in ((z0, c0), (z1, c1)):
        grenze = klemm_grenze(z) + eps
        if abs(c[0] - 0.5) > grenze or abs(c[1] - 0.5) > grenze:
            return False
    return True


def _fenster(e: float, z0: float, z1: float, c0, c1):
    z = z0 + (z1 - z0) * e
    h = halbfenster(z)
    cx = c0[0] + (c1[0] - c0[0]) * e
    cy = c0[1] + (c1[1] - c0[1]) * e
    return (cx - h, cy - h, cx + h, cy + h)


def schutz_haelt(boxen, z0: float, z1: float, c0, c1, *,
                 stellen: int = STUETZSTELLEN, eps: float = 1e-6) -> bool:
    """Liegt jede Schutzbox an allen Stuetzstellen im Fenster? (A1)"""
    if not boxen:
        return True
    for i in range(stellen):
        e = i / (stellen - 1) if stellen > 1 else 0.0
        fx0, fy0, fx1, fy1 = _fenster(e, z0, z1, c0, c1)
        for bx0, by0, bx1, by1 in boxen:
            if (bx0 < fx0 - eps or by0 < fy0 - eps
                    or bx1 > fx1 + eps or by1 > fy1 + eps):
                return False
    return True


def zoom_deckel_mittig(boxen) -> float:
    """Groesster Zoom, bei dem ein **mittiges** Fenster alle Boxen umschliesst.

    Das ist der Rueckfall, der immer aufgeht: ``rmax`` ist der groesste Abstand
    von der Bildmitte zu einer Boxkante, und wegen ``rmax <= 0,5`` liegt die
    Schranke nie unter 1,0. Der Deckel aus Schritt 1 der Klemmkette
    (``1 / max(bw, bh)``) reicht dafuer nicht: er sagt nur, dass die Box in ein
    Fenster dieser *Groesse* passt, nicht dass sie in einem *mittigen* Fenster
    auch liegt.
    """
    if not boxen:
        return Z_HART
    rmax = max(max(abs(0.5 - x0), abs(x1 - 0.5), abs(0.5 - y0), abs(y1 - 0.5))
               for x0, y0, x1, y1 in boxen)
    return 1.0 / (2.0 * max(rmax, 1e-9))


def zoom_noetig(weg: float) -> float:
    """Der Zoom, den eine Auslenkung ``weg`` aus der Mitte verlangt.

    Die Umkehrung von :func:`klemm_grenze`: ``z >= 1 / (1 - 2 d)``. Also 1,064
    fuer d = 0,03, 1,136 fuer d = 0,06 und 1,316 fuer d = 0,12 — **Zoomweite
    und Schwenkweite sind ein Budget, kein Parameterpaar.**
    """
    if weg <= 0.0:
        return 1.0
    if weg >= 0.5:
        return math.inf
    return 1.0 / (1.0 - 2.0 * weg)


# --------------------------------------------------------------------------
# Klemmkette (6.3)
# --------------------------------------------------------------------------

def zoom_deckel(entry: VisionEntry, regel: Regel | None) -> float:
    """Schritte 1 und 2: was Schutz, Detaildichte und Szene hergeben.

    Jeder Schritt kann nur verkleinern.
    """
    grenzen = [Z_HART, 1.0 + (1.0 - entry.detail) * DETAIL_SPANNE]
    for x0, y0, x1, y1 in entry.protect:
        # Eine Box passt nur bis ``z <= 1 / max(bw, bh)`` ueberhaupt ins Fenster.
        seite = max(x1 - x0, y1 - y0)
        if seite > 0:
            grenzen.append(1.0 / seite)
    if regel is not None and regel.zoom_total is not None:
        grenzen.append(1.0 + regel.zoom_total)
    # Der Boden gilt fuer alles ausser ``document``: dort *soll* das Bild
    # stehen, ueberall sonst waere Stillstand ein unbeabsichtigter Ausfall der
    # Klemmkette (A7).
    boden = 1.0 if (regel is not None and regel.zoom_total == 0.0) else Z_MIN_BEWEGUNG
    return max(boden, min(grenzen))


def _zielrichtung(entry: VisionEntry, regel: Regel) -> tuple[float, float] | None:
    """Wohin die Bewegung laut Bildinhalt zeigen sollte — oder ``None``."""
    if regel.ziel == "focus":
        ziel = entry.focus
        if ziel is None and entry.quiet is not None:
            # Ohne Zielpunkt ist die ruhige Flaeche der beste Ersatz: dort ist
            # nichts, was der Schwenk anschneiden koennte.
            ziel = ((entry.quiet[0] + entry.quiet[2]) / 2.0,
                    (entry.quiet[1] + entry.quiet[3]) / 2.0)
        if ziel is None:
            return None
        dx, dy = ziel[0] - 0.5, ziel[1] - 0.5
        laenge = math.hypot(dx, dy)
        return (dx / laenge, dy / laenge) if laenge > 1e-6 else None
    if regel.ziel == "achse":
        if entry.axis == "horizontal":
            return (1.0, 0.0)
        if entry.axis == "vertical":
            return (0.0, 1.0)
    return None


def _wegwunsch(entry: VisionEntry, regel: Regel, duration: float,
               defaults: KBDefaults) -> float:
    """Wie weit die Fahrt laufen *will*, bevor die Geometrie mitredet."""
    if regel.ziel == "keiner":
        return 0.0
    if regel.ziel == "focus" and entry.focus is not None:
        # Auf das Motiv zu heisst: bis dorthin, nicht irgendwie weit.
        return math.dist(entry.focus, (0.5, 0.5))
    lo, hi = defaults.pan_total
    return min(max(defaults.pan_rate * duration, lo), hi)


def _fahrt(weg: float, richtung: tuple[float, float] | None, *, zoom_in: bool,
           z_klein: float, z_gross: float):
    """Legt die Strecke wie ``kenburns._pan`` mit ``pan_anchor: center`` aus.

    Das ruhende Ende liegt in der Bildmitte — beim Hineinzoomen der Anfang,
    beim Herauszoomen das Ende. Dann laufen Schwenk und Klemmgrenze in
    dieselbe Richtung, statt gegeneinander: die Klemmung geht mit wachsendem
    Zoom auf, und genau dorthin faehrt der Schwenk.
    """
    if richtung is None or weg <= 0.0:
        mitte = (0.5, 0.5)
        return ((z_klein, z_gross, mitte, mitte) if zoom_in
                else (z_gross, z_klein, mitte, mitte))
    aussen = (0.5 + richtung[0] * weg, 0.5 + richtung[1] * weg)
    mitte = (0.5, 0.5)
    if zoom_in:
        return (z_klein, z_gross, mitte, aussen)
    return (z_gross, z_klein, aussen, mitte)


def _bauen(entry: VisionEntry, regel: Regel, *, zoom_in: bool, richtung_idx,
           weite: str, weg_wunsch: float, z_dauer: float, deckel: float
           ) -> Bewegung | None:
    """Eine Signatur durch die Klemmkette schicken. ``None`` = unzulaessig."""
    richtung = None if richtung_idx is None else _DIRECTIONS[richtung_idx]
    weg_soll = 0.0 if richtung is None else WEITEN[weite] * weg_wunsch
    weg = weg_soll

    # Schritt 3/4: Zoomspanne aus der Dauer, dann der Hebel aus 6.3. Angehoben
    # wird nur, wenn der Schwenk es verlangt und der Deckel es hergibt — mehr
    # Zoom ist hier Zahlungsmittel, nicht Selbstzweck.
    z_klein = 1.0
    z_gross = max(1.0, min(z_dauer, deckel))
    if weg > 0.0:
        z_gross = min(max(z_gross, min(zoom_noetig(weg), Z_ANHEBEN)), deckel)

    # Schritt 4: was die Klemmung am ausgelenkten Ende noch traegt — abzueglich
    # dessen, was das Runden beim Schreiben kostet.
    weg = max(0.0, min(weg, klemm_grenze(z_gross) - RUNDUNGSMARGE))

    def gebaut(strecke: float):
        return _fahrt(strecke, richtung, zoom_in=zoom_in,
                      z_klein=z_klein, z_gross=z_gross)

    # Schritt 5: Schutz ueber die volle Bewegung. Halbieren, hoechstens
    # dreimal — danach reiner Zoom, der wegen des mittigen Deckels aus
    # ``plan_kb`` immer aufgeht.
    z0, z1, c0, c1 = gebaut(weg)
    versuche = 0
    while not schutz_haelt(entry.protect, z0, z1, c0, c1):
        if weg <= 0.0:
            return None
        versuche += 1
        weg = 0.0 if versuche > HALBIERUNGEN else weg / 2.0
        z0, z1, c0, c1 = gebaut(weg)

    gekuerzt = 0.0 if weg_soll <= 0.0 else min(1.0, max(0.0, 1.0 - weg / weg_soll))
    sig = Signatur(zoom_in=zoom_in,
                   richtung=None if weg <= 0.0 else richtung_idx,
                   weite="-" if weg <= 0.0 else weite)
    bew = Bewegung(z0=z0, z1=z1, c0=c0, c1=c1, signatur=sig,
                   passung=0.0, gekuerzt=gekuerzt)

    # Geprueft wird, was **geschrieben** wird. Der interne Float ist nicht die
    # Bewegung; die Zahlen in ``edit.yaml`` sind es. Ein Kandidat, der die
    # Rundung nicht uebersteht, wird gar nicht erst angeboten.
    if not haelt_gerundet(bew, entry.protect):
        return None
    return bew


def haelt_gerundet(bew: Bewegung, boxen) -> bool:
    """Erfuellen die **gerundeten** Werte A1 und A1b?"""
    spec = bew.as_spec()
    z0, z1 = spec.z
    c0, c1 = (spec.c[0], spec.c[1]), (spec.c[2], spec.c[3])
    return klemmung_haelt(z0, z1, c0, c1) and schutz_haelt(boxen, z0, z1, c0, c1)


# --------------------------------------------------------------------------
# Passung
# --------------------------------------------------------------------------

def _passung(bew: Bewegung, entry: VisionEntry, regel: Regel,
             ziel: tuple[float, float] | None) -> float:
    """Wie gut die Bewegung zur Bildaussage passt, in [0, 1]."""
    sig = bew.signatur

    # Zoomrichtung.
    if regel.zoom == 0:
        note = 0.15
    else:
        gewollt = regel.zoom > 0
        note = 0.30 if sig.zoom_in == gewollt else 0.0

    # Schwenkrichtung.
    if sig.richtung is None:
        note += 0.40 if regel.ziel == "keiner" else 0.0
    elif regel.ziel == "keiner":
        note += 0.0
    elif ziel is None:
        # Keine Praeferenz im Bild — jede Richtung ist gleich gut.
        note += 0.20
    else:
        dx, dy = _DIRECTIONS[sig.richtung]
        skalar = dx * ziel[0] + dy * ziel[1]
        # An einer *Achse* sind beide Richtungen gleich gut: ein Panorama laesst
        # sich nach links wie nach rechts abfahren. Auf ein *Motiv* zu gilt das
        # nicht — dort zaehlt das Vorzeichen.
        note += 0.40 * max(0.0, abs(skalar) if regel.ziel == "achse" else skalar)

    # Weite.
    if sig.weite == regel.weite or (sig.weite == "-" and regel.weite == "0"):
        note += 0.10
    elif sig.weite != "-" and regel.weite != "0":
        note += 0.05

    # Ein gekuerzter Schwenk ist schlechter als ein ungekuerzter derselben
    # Richtung — sonst gewinnt die Weite, die ohnehin am Deckel haengt.
    note -= 0.05 * bew.gekuerzt

    # Stichentscheid: der unverbindliche Vorschlag des Modells. Klein genug,
    # dass er nur bei sonst gleichwertigen Kandidaten den Ausschlag gibt (E1).
    if entry.suggest and sig.richtung is not None:
        gemeint = _VORSCHLAEGE.get(entry.suggest.strip().lower())
        if gemeint is not None and gemeint == sig.richtung:
            note += 0.02
    return note


#: Uebersetzung des unverbindlichen ``suggest``-Felds in eine Richtung.
#: Unbekannte Werte bleiben wirkungslos — das Feld ist ein Hinweis, kein Befehl.
_VORSCHLAEGE = {"pan_right": 0, "pan_down": 1, "pan_left": 2, "pan_up": 3}


# --------------------------------------------------------------------------
# Der Planer
# --------------------------------------------------------------------------

def plan_kb(entry: VisionEntry, *, key: str, duration: float,
            defaults: KBDefaults, portrait_komposit: bool = False,
            variety: int = VARIETY) -> Ergebnis | None:
    """Die Bewegung fuer ein Standbild — oder ``None``.

    ``None`` heisst: dieses Bild bekommt **kein** ``kb:`` und laeuft ueber den
    heutigen Pfad. Das ist der Ausfallpfad aus Abschnitt 8, und er kostet keine
    Sonderbehandlung.

    ``duration`` ist die **volle sichtbare Dauer** des Bildes inklusive der
    angrenzenden Uebergangs-Haelften — dieselbe Groesse, ueber die
    :func:`slideshow.kenburns.plan_motion` rechnet.

    ``portrait_komposit`` sagt, ob das Cache-Bild ein Hochformat-Komposit mit
    Balken ist (6.2). Massgeblich ist der **wirksame** Modus am Segment, nicht
    die Vorgabe — deshalb kommt die Auskunft von aussen und nicht aus der
    Analyse.
    """
    regel = REGELN.get(entry.scene)
    if regel is None or not entry.sicher:
        # ``other`` und alles unter der Konfidenzschwelle: heutige Rotation,
        # Schutzboxen aber trotzdem respektiert (E9).
        return _rotation_mit_schutz(entry, key=key, duration=duration,
                                    defaults=defaults)

    if regel.ziel == "keiner" and regel.zoom_total == 0.0:
        # ``document``: statisch, formatfuellend. Der einzige Fall, in dem
        # Stillstand die Absicht ist — und deshalb der einzige, den
        # ``sanity_check`` melden darf (A7).
        sig = Signatur(zoom_in=True, richtung=None, weite="-")
        return Ergebnis(spec=KBSpec(z=(1.0, 1.0), c=(0.5, 0.5, 0.5, 0.5)),
                        signatur=sig, gekuerzt=0.0, kandidaten=1)

    deckel = zoom_deckel(entry, regel)
    if entry.protect:
        # Der mittige Deckel ist die Schranke, unter der ein Fenster ohne
        # Auslenkung garantiert alle Boxen umschliesst. Ohne ihn koennte die
        # Kette in Schritt 5 auf reinen Zoom zurueckfallen und *dort* immer
        # noch anschneiden — der Rueckfall haette dann keinen Boden.
        #
        # Hier steht ``1.0`` als Untergrenze und nicht ``Z_MIN_BEWEGUNG``:
        # verbietet eine Schutzbox jeden Zoom, ist Stillstand das richtige
        # Ergebnis. ``sanity_check`` meldet ihn dann (A7) — und diese Meldung
        # ist zutreffend, denn sie zeigt auf eine Box, die das halbe Bild
        # beansprucht.
        deckel = max(1.0, min(deckel, zoom_deckel_mittig(entry.protect)))
    z_dauer = zoom_from_duration(duration, defaults)
    weg_wunsch = _wegwunsch(entry, regel, duration, defaults)
    ziel = _zielrichtung(entry, regel)

    richtungen: list[int | None] = [None]
    if regel.ziel != "keiner":
        for i in range(len(_DIRECTIONS)):
            # Hochformat-Komposite bekommen nur vertikale Schwenks oder reinen
            # Zoom: ein horizontaler Anteil faehrt in die unscharfen Balken
            # hinein und macht sie sichtbar groesser — der schlechteste Fall.
            # Diagonalen haben einen horizontalen Anteil und fallen mit.
            if portrait_komposit and abs(_DIRECTIONS[i][0]) > 1e-6:
                continue
            richtungen.append(i)

    # Die Zoomrichtung steht in der **innersten** Schleife, und das ist keine
    # Kosmetik: bei gleicher Passung entscheidet die Erzeugungsreihenfolge
    # (``sorted`` ist stabil), und mit der Zoomrichtung aussen standen erst alle
    # Hineinzooms in der Liste. Szenen ohne Achse und ohne Zielpunkt — dort sind
    # alle Richtungen gleich gut — bekamen dadurch *immer* einen Hineinzoom, und
    # der Anteil aus A2 lief ueber 65 %. Innen liegend liefert derselbe
    # Gleichstand abwechselnd hinein und heraus.
    kandidaten: list[Bewegung] = []
    for richtung_idx in richtungen:
        weiten = ("-",) if richtung_idx is None else ("S", "M", "L")
        for weite in weiten:
            for zoom_in in (True, False):
                bew = _bauen(entry, regel, zoom_in=zoom_in,
                             richtung_idx=richtung_idx,
                             weite="L" if weite == "-" else weite,
                             weg_wunsch=weg_wunsch, z_dauer=z_dauer,
                             deckel=deckel)
                if bew is None:
                    continue
                if any(k.signatur == bew.signatur for k in kandidaten):
                    # Ein gekuerzter Schwenk kann auf dieselbe Signatur
                    # zusammenfallen wie ein kuerzerer — dann ist er kein
                    # eigener Kandidat, sondern derselbe noch einmal.
                    continue
                kandidaten.append(Bewegung(
                    z0=bew.z0, z1=bew.z1, c0=bew.c0, c1=bew.c1,
                    signatur=bew.signatur, gekuerzt=bew.gekuerzt,
                    passung=_passung(bew, entry, regel, ziel)))

    if not kandidaten:
        return _rotation_mit_schutz(entry, key=key, duration=duration,
                                    defaults=defaults)

    # ``sorted`` ist stabil: bei gleicher Passung entscheidet die
    # Erzeugungsreihenfolge, und die haengt nur am Bild. Damit ist die Auswahl
    # ueber Laeufe, Rechner und Python-Versionen hinweg dieselbe.
    kandidaten.sort(key=lambda b: b.passung, reverse=True)
    engere = kandidaten[:max(1, variety)]
    gewaehlt = engere[motion_key(key) % len(engere)]
    return Ergebnis(spec=gewaehlt.as_spec(), signatur=gewaehlt.signatur,
                    gekuerzt=gewaehlt.gekuerzt, kandidaten=len(engere))


def _rotation_mit_schutz(entry: VisionEntry, *, key: str, duration: float,
                         defaults: KBDefaults) -> Ergebnis | None:
    """Heutige Rotation — nur gegen den Schutz nachgerechnet (E9).

    Ohne Schutzboxen gibt es nichts zu korrigieren, und das Bild bekommt gar
    kein ``kb:``: die Rotation bleibt genau die, die sie ohne diese Datei auch
    waere. Mit Schutzboxen wird die Rotation nachgerechnet und nur dann
    ueberschrieben, wenn sie tatsaechlich anschneidet.
    """
    if not entry.protect:
        return None
    m = plan_motion(key, duration, defaults)
    if (schutz_haelt(entry.protect, m.z0, m.z1, m.c0, m.c1)
            and klemmung_haelt(m.z0, m.z1, m.c0, m.c1)):
        return None

    z_gross = max(1.0, min(zoom_from_duration(duration, defaults),
                           zoom_deckel(entry, None),
                           zoom_deckel_mittig(entry.protect)))
    zoom_in = m.z1 >= m.z0
    z0, z1 = (1.0, z_gross) if zoom_in else (z_gross, 1.0)
    mitte = (0.5, 0.5)
    sig = Signatur(zoom_in=zoom_in, richtung=None, weite="-")
    sicher = Bewegung(z0=z0, z1=z1, c0=mitte, c1=mitte, signatur=sig, passung=0.0)
    if not haelt_gerundet(sicher, entry.protect):
        # Der mittige Zoom ist der Boden dieser Kette. Traegt selbst der nicht,
        # bleibt nur der vollstaendige Stillstand — immer noch besser, als ein
        # Gesicht anzuschneiden.
        sicher = Bewegung(z0=1.0, z1=1.0, c0=mitte, c1=mitte, signatur=sig,
                          passung=0.0)
    return Ergebnis(spec=sicher.as_spec(), signatur=sig, gekuerzt=1.0,
                    kandidaten=1, schutz_erzwungen=True)


def bericht(ergebnisse: list[Ergebnis]) -> list[str]:
    """Was der Lauf ueber sich selbst sagen kann (Abschnitt 12).

    Die Zahl der gekuerzten Schwenks ist die wichtigste Zeile: ist sie gross,
    ist die Antwort nicht mehr Planerarbeit, sondern eine groessere Vorgabe
    fuer ``zoom_total``.
    """
    if not ergebnisse:
        return []
    gekuerzt = [e for e in ergebnisse
                if e.gekuerzt > 0.01 and not e.schutz_erzwungen]
    halb = [e for e in gekuerzt if e.gekuerzt > 0.5]
    zeilen: list[str] = []
    if gekuerzt:
        zeilen.append(
            f"{len(gekuerzt)} von {len(ergebnisse)} Schwenks gekuerzt, davon "
            f"{len(halb)} auf unter die Haelfte — die Klemmung des Bildrands "
            f"gibt bei diesem `zoom_total` nicht mehr her. Ist die Zahl gross, "
            f"ist der Hebel `defaults.kb.zoom_total`, nicht der Planer.")
    erzwungen = sum(1 for e in ergebnisse if e.schutz_erzwungen)
    if erzwungen:
        zeilen.append(
            f"{erzwungen} {'Bild steht' if erzwungen == 1 else 'Bilder stehen'} "
            f"fast still, weil eine Schutzbox jede Fahrt anschneiden wuerde — in "
            f"`vision.yaml` nachsehen, ob die Box stimmt.")
    eng = sum(1 for e in ergebnisse if e.kandidaten <= 1)
    if eng > len(ergebnisse) // 3:
        zeilen.append(
            f"{eng} Bilder liessen nur eine Bewegung zu — dort gibt es keine "
            f"Abwechslung zu holen (Schutzboxen oder hohe Detaildichte).")
    return zeilen
