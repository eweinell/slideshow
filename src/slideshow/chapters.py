"""Kapitelvorschlaege aus dem Material (Entscheidung 6c des Titelfolien-Briefings).

``slideshow chapters`` schreibt eine ``chapters.yaml`` mit gefundenen Grenzen und
**leeren Ueberschriften**. Das ist die Arbeitsteilung: wo ein Abschnitt endet,
steht im Material; wie er heisst, nicht. Ein Ortsname laesst sich ohne Netz nicht
aus Koordinaten gewinnen, und ein geratener Name ist schlimmer als kein Name —
deshalb bricht ``build`` bei einer leeren Ueberschrift ab, statt sie zu erfinden.

Zwei Signale, beide aus dem Manifest:

**Zeitluecke.** Eine Pause von acht Stunden ist eine Tagesgrenze, eine von
zwanzig fast immer ein Ortswechsel. Das Signal ist immer da — ``capture_time``
steht bei praktisch jedem Foto — aber grob: wer abends im Hotel und morgens im
selben Hotel fotografiert, bekommt eine Grenze ohne Ortswechsel.

**Ortssprung.** GPS steht in den EXIF-Daten der meisten Handyfotos. Ein Sprung
ueber 30 km zwischen zwei aufeinanderfolgenden Aufnahmen *ist* der neue Ort —
das treffsicherste verfuegbare Signal, aber nur so gut wie die Abdeckung: eine
Kamera ohne GPS-Empfaenger liefert es gar nicht.

Beide zusammen sind besser als jedes allein, und keines ersetzt den Blick des
Anwenders. Was hier herauskommt, ist ein **Vorschlag** — eine Datei zum
Durchsehen, Streichen und Ausfuellen.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass

from .models import Manifest, MediaItem
from .probe import chronological, effective_capture_time

#: Ab hier eine Tagesgrenze — meist ein Ortswechsel, aber laengst nicht immer.
GAP_DAY_HOURS = 8.0
#: Ab hier fast immer ein Ortswechsel. Vorgabe fuer die geschriebenen Kapitel.
GAP_PLACE_HOURS = 20.0
#: Sprung zwischen zwei Aufnahmen, ab dem es ein anderer Ort ist.
JUMP_KM = 30.0

_ERDRADIUS_KM = 6371.0088

_MONATE = ["Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"]


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Grosskreisentfernung zweier Punkte in Kilometern (Haversine).

    Die einfache ebene Naeherung taugt hier nicht: bei 55 Grad Nord — Kopenhagen,
    Stockholm — schrumpft ein Laengengrad auf 64 km, und ein Ost-West-Sprung
    waere fast doppelt so gross gerechnet, wie er ist.
    """
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * _ERDRADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


@dataclass
class Vorschlag:
    """Eine gefundene Grenze — *vor* dem Medium ``before``."""

    before: str
    #: Zweite Zeile, vorbelegt aus dem Aufnahmedatum des folgenden Bildes.
    subtitle: str
    #: Zeitluecke in Stunden zum vorigen Medium.
    luecke_h: float
    #: Ortssprung in km, sofern beide Seiten GPS tragen.
    sprung_km: float | None
    #: ``stark`` kommt in die Datei, ``schwach`` nur als auskommentierte Zeile.
    staerke: str

    @property
    def grund(self) -> str:
        teile = [f"{self.luecke_h:.0f} h Pause" if self.luecke_h >= 1
                 else f"{self.luecke_h * 60:.0f} min Pause"]
        if self.sprung_km is None:
            teile.append("kein GPS")
        elif self.sprung_km >= JUMP_KM:
            teile.insert(0, f"{self.sprung_km:.0f} km Ortssprung")
        else:
            # Der Fall, der die Zeitluecke entwertet — und der einzige, in dem
            # eine kleine Zahl etwas aussagt.
            teile.append(f"aber nur {self.sprung_km:.0f} km, gleicher Ort")
        return ", ".join(teile)


def suggest(manifest: Manifest, *, min_gap_hours: float = GAP_PLACE_HOURS,
            min_jump_km: float = JUMP_KM) -> list[Vorschlag]:
    """Findet die Abschnittsgrenzen im Material.

    Gearbeitet wird auf der **chronologischen** Reihenfolge inklusive
    Uhren-Offsets — dieselbe, die ``build`` spaeter verwendet. Sonst zeigten die
    Vorschlaege auf Stellen, die es in der fertigen Abfolge gar nicht gibt.
    """
    reihe = [m for m in chronological(manifest)
             if effective_capture_time(m, manifest.clock_offsets) is not None]
    offsets = manifest.clock_offsets
    ersterer = first_day(reihe, offsets)

    out: list[Vorschlag] = []
    for vorher, danach in zip(reihe, reihe[1:]):
        t0 = effective_capture_time(vorher, offsets)
        t1 = effective_capture_time(danach, offsets)
        luecke_h = max(0.0, (t1 - t0) / 3600.0)
        sprung = (distance_km(vorher.gps, danach.gps)
                  if vorher.gps and danach.gps else None)

        # Wo Koordinaten vorliegen, entscheiden sie — auch *gegen* die Uhr.
        # Eine Nacht im selben Hotel ist eine lange Pause und kein neuer
        # Abschnitt; ohne dieses Veto bekaeme jede Reise so viele Kapitel wie
        # Tage. Fehlt GPS, bleibt nur die Zeitluecke, und die ist grob.
        if sprung is not None:
            stark = sprung >= min_jump_km
        else:
            stark = luecke_h >= min_gap_hours
        schwach = luecke_h >= GAP_DAY_HOURS
        if not (stark or schwach):
            continue
        out.append(Vorschlag(
            before=danach.id, subtitle=day_label(t1, ersterer),
            luecke_h=round(luecke_h, 2),
            sprung_km=(round(sprung, 1) if sprung is not None else None),
            staerke="stark" if stark else "schwach"))
    return out


def first_day(reihe: list[MediaItem], offsets: dict[str, float]) -> _dt.date | None:
    for m in reihe:
        ts = effective_capture_time(m, offsets)
        if ts is not None:
            return _dt.datetime.fromtimestamp(ts).date()
    return None


def day_label(ts: float, erster_tag: _dt.date | None) -> str:
    """"Tag 11 · 24. Juli" — dieselbe Form, die ``subtitle: auto`` erzeugt."""
    wann = _dt.datetime.fromtimestamp(ts)
    datum = f"{wann.day}. {_MONATE[wann.month - 1]}"
    if erster_tag is None:
        return datum
    tag = (wann.date() - erster_tag).days + 1
    return f"Tag {tag} · {datum}" if tag >= 1 else datum


def first_image_id(manifest: Manifest, reihenfolge: list[str] | None = None) -> str:
    """Kennung des ersten Bildes der Abfolge — der Grund, auf den ``bg: auto``
    beim Auftakt hinauslaeuft.

    Steht im Kommentar der erzeugten Datei: ``auto`` ist bequem, aber man soll
    sehen, *welches* Bild man da bekommt — sonst laesst es sich nicht
    austauschen, ohne es erst zu suchen.

    ``reihenfolge`` ist die aufgeloeste ID-Folge aus ``order.yaml``, sofern es
    eine gibt. Ohne sie naehme der Kommentar das chronologisch erste Bild und
    nennte damit bei manueller Sortierung schlicht das falsche.
    """
    if reihenfolge:
        bild = {m.id for m in manifest.media if m.kind == "image"}
        return next((mid for mid in reihenfolge if mid in bild), "")
    for m in chronological(manifest):
        if m.kind == "image":
            return m.id
    return ""


#: Was von den Vorschlaegen bleibt, wenn von Hand sortiert wurde.
ORDER_VORBEHALT = (
    "ACHTUNG: order.yaml sortiert nicht chronologisch. Die Grenzen unten sind\n"
    "aus Zeitluecken zwischen *zeitlichen* Nachbarn gerechnet — im Film stehen\n"
    "dort aber thematische Nachbarn. Als Anker taugt hier `group:` statt\n"
    "`before:`: `- {group: am-wasser, title: \"Am Wasser\"}`. Und `subtitle: auto`\n"
    "nimmt das Datum des folgenden Bildes, was ueber einem Block aus mehreren\n"
    "Tagen irrefuehrt — dann `subtitle:` von Hand setzen oder mit `null` weglassen."
)


def coverage_note(manifest: Manifest) -> str:
    """Wie gut das GPS-Signal ueberhaupt traegt.

    Gehoert in den Bericht, weil die Aussagekraft der Vorschlaege daran haengt:
    ohne Koordinaten bleibt nur die grobe Zeitluecke, und das sollte man wissen,
    bevor man der Datei vertraut.
    """
    mit = sum(1 for m in manifest.media if m.gps)
    gesamt = len(manifest.media)
    if not gesamt:
        return "kein Material"
    if not mit:
        return ("kein Foto traegt Koordinaten — erkannt wird allein ueber "
                "Zeitluecken, und die sind gegenueber einem Ortswechsel blind")
    if mit < gesamt:
        return (f"{mit} von {gesamt} Aufnahmen tragen Koordinaten; fuer die "
                f"uebrigen greift nur die Zeitluecke")
    return f"alle {gesamt} Aufnahmen tragen Koordinaten"


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------

def dump_chapters_yaml(vorschlaege: list[Vorschlag], *, hinweis: str = "",
                       auftakt: bool = True, auftakt_bild: str = "",
                       vorbehalt: str = "") -> str:
    """Schreibt die Datei — von Hand, nicht ueber ``yaml.dump``.

    Der Grund sind die Kommentare: die Datei ist ein Formular, kein Erzeugnis.
    Sie soll beim Oeffnen erklaeren, was einzutragen ist und warum eine Grenze
    vorgeschlagen wurde, und ein YAML-Dumper wirft Kommentare weg.
    """
    zeilen = [
        "# chapters.yaml — Kapitel der Reise. Wird von `slideshow build` eingelesen.",
        "#",
        "# Erzeugt von `slideshow chapters`. Die Grenzen kommen aus dem Material,",
        "# die Ueberschriften nicht: einen Ortsnamen kann das Werkzeug nicht",
        "# erfinden. Jede leere `title:` bitte ausfuellen — `build` bricht sonst",
        "# mit Zeilennummer ab. Was nicht passt, ersatzlos loeschen.",
    ]
    if hinweis:
        zeilen += ["#", f"# Signal: {hinweis}"]
    if vorbehalt:
        zeilen += ["#"] + [f"# {z}" for z in vorbehalt.splitlines()]
    zeilen += ["", "chapters:"]

    if auftakt:
        zeilen += _auftakt_zeilen(auftakt_bild)

    stark = [v for v in vorschlaege if v.staerke == "stark"]
    schwach = [v for v in vorschlaege if v.staerke == "schwach"]

    if not stark:
        zeilen += ["  # Keine deutliche Abschnittsgrenze gefunden."]
    for v in stark:
        # Der Untertitel steht als `auto` in der Datei und nicht ausgeschrieben:
        # so wandert er mit, wenn das Kapitel verschoben wird. Im Kommentar
        # steht er trotzdem — daran erkennt man beim Ausfuellen, welcher Tag
        # gemeint ist.
        zeilen += [
            f"  # {v.grund} · {v.subtitle}",
            f'  - {{before: {v.before}, title: "", subtitle: auto}}',
        ]

    if schwach:
        zeilen += [
            "",
            "  # Schwaechere Kandidaten — Tagesgrenzen ohne erkannten Ortswechsel.",
            "  # Zum Uebernehmen das `# ` am Zeilenanfang entfernen.",
        ]
        for v in schwach:
            zeilen += [
                f"  # {v.grund} · {v.subtitle}",
                f'  # - {{before: {v.before}, title: "", subtitle: auto}}',
            ]
    return "\n".join(zeilen) + "\n"


def _auftakt_zeilen(auftakt_bild: str = "") -> list[str]:
    """Der Titel vor allem Material — in beiden erzeugten Dateien derselbe.

    ``bg: auto`` auch hier, obwohl der Auftakt nichts *ankuendigt*: das naechste
    Bild gibt es sehr wohl, es ist das erste des Films. Der Titel steht dann
    darueber, unscharf und abgedunkelt, und die Blende danach loest ihn in genau
    dieses Bild scharf auf — der uebliche Filmanfang. Eine Farbflaeche bleibt die
    ruhigere Alternative und steht als Handgriff daneben.

    Ohne ``beats:``. Ein Film faengt haeufig mit einer free-Region an — die ersten
    Sekunden eines Stuecks lassen sich selten rastern —, und dort bliebe die
    Angabe wirkungslos. Die Standzeit kommt aus ``defaults.title``, und wer sie
    anders will, nimmt ``dur:`` in Sekunden.
    """
    welches = f" — hier ist das {auftakt_bild}" if auftakt_bild else ""
    return [
        "  # Auftakt vor allem Material. `bg: auto` nimmt das erste Bild als",
        f"  # unscharfen Grund; die Blende danach loest es scharf auf{welches}.",
        "  # Passt es nicht, ein anderes ueber seine Medien-ID setzen",
        '  # (`bg: img_...`) oder ruhig anfangen: `bg: "#1b2a3a"`.',
        "  # Ohne Kamerafahrt, dafuer besser lesbar: `motion: none`.",
        "  # Laenger stehen lassen: `dur: 6` (Sekunden, gilt ueberall) oder",
        "  # `beats: 16` (nur in einer Beat-Region wirksam).",
        '  - {at: 0, title: "", subtitle: "", bg: auto}',
    ]


# --------------------------------------------------------------------------
# Ein Kapitel je Block aus order.yaml (`slideshow chapters --from-groups`)
#
# Der Weg fuer den Film, dessen Kapitel *nicht* aus dem Material fallen,
# sondern beim Sortieren von Hand gezogen wurden: ein Kapitel je Reiseabschnitt
# ueber mehrere Tage, innen chronologisch. Dort taugt ``suggest`` nicht — es
# rechnet Zeitluecken zwischen *zeitlichen* Nachbarn, im Film stehen aber
# thematische. Die Grenzen sind hier keine Vermutung mehr: sie stehen schon in
# ``order.yaml``, und was fehlt, ist nur die Ueberschrift.
# --------------------------------------------------------------------------

@dataclass
class GruppenAnker:
    """Ein Block aus ``order.yaml``, beschrieben als Kapitelkandidat."""

    #: Name des Blocks — er wird zum ``group:``-Anker, nicht zur Ueberschrift.
    name: str
    #: Wie viele Medien noch drinstehen (nach ``rest: drop``).
    anzahl: int
    #: Zeitlicher Umfang als Text, fuer den Kommentar ueber der Zeile.
    spanne: str
    #: Umfasst der Block mehr als einen Kalendertag? Dann ist ``subtitle: auto``
    #: falsch — es naehme den Tag des ersten Bildes fuer fuenf Reisetage.
    mehrtaegig: bool


def dump_group_chapters_yaml(anker: list[GruppenAnker], *, auftakt: bool = True,
                             auftakt_bild: str = "") -> str:
    """Schreibt ``chapters.yaml`` mit einem Eintrag je Block.

    ``subtitle:`` wird hier **vorentschieden** statt gemeldet: ueber einem Block
    aus einem einzigen Tag stimmt ``auto``, ueber fuenf Reisetagen nie. Der
    Unterschied steht im Material und nicht im Ermessen — anders als die
    Ueberschrift, die leer bleibt.
    """
    zeilen = [
        "# chapters.yaml — Kapitel der Reise. Wird von `slideshow build` eingelesen.",
        "#",
        "# Erzeugt von `slideshow chapters --from-groups`: ein Eintrag je Block aus",
        "# order.yaml. Die Grenzen sind damit die, die beim Sortieren gezogen wurden —",
        "# nicht geraten. Die Ueberschriften nicht: einen Ortsnamen kann das Werkzeug",
        "# nicht erfinden. Jede leere `title:` bitte ausfuellen — `build` bricht sonst",
        "# mit Zeilennummer ab. Ein Block, der keine Folie bekommen soll, wird hier",
        "# ersatzlos geloescht; in order.yaml bleibt er stehen.",
        "#",
        "# `group:` zeigt auf das erste Medium des Blocks und ueberlebt jedes weitere",
        "# Umsortieren *innerhalb* des Blocks — ein `before: img_042` zeigte nach dem",
        "# naechsten Handgriff mitten hinein.",
        "",
        "chapters:",
    ]
    if auftakt:
        zeilen += _auftakt_zeilen(auftakt_bild)
    if not anker:
        return "\n".join(zeilen + ["  # Keine benannten Bloecke in order.yaml."]) + "\n"

    for nr, a in enumerate(anker):
        zeilen.append("")
        zeilen.append(f"  # {a.anzahl} Medien · {a.spanne}")
        if a.mehrtaegig:
            zeilen.append("  # mehr als ein Tag — `subtitle: auto` naehme davon nur "
                          "den ersten")
        untertitel = "auto" if not a.mehrtaegig else "null"
        eintrag = f'  - {{group: {a.name}, title: "", subtitle: {untertitel}}}'
        if nr == 0 and auftakt:
            # Beide saessen vor demselben Bild, und zwei Titelfolien
            # hintereinander sieht man erst im fertigen Film. Auskommentiert
            # statt weggelassen: welche der beiden gilt, ist eine Entscheidung
            # und keine Rechnung.
            zeilen += [
                "  # Faellt mit dem Auftakt oben zusammen — beide zugleich ergaeben",
                "  # zwei Folien hintereinander. Wer den Abschnitt lieber benennt als",
                "  # den Film, loescht den Auftakt und entfernt hier das `# `.",
                "  # " + eintrag.strip(),
            ]
        else:
            zeilen.append(eintrag)
    return "\n".join(zeilen) + "\n"
