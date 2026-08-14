"""Bildanalyse gegen die Claude-API — ``slideshow analyze``
(``docs/briefing-kenburns-inhaltsabhaengig.md``, Abschnitte 3 bis 5, 8 und 9).

**Das Modell wird nicht gefragt, welcher Effekt passt. Es wird gefragt, was auf
dem Bild zu sehen ist.** Der Unterschied ist der ganze Entwurf (E1):

* Das Modell kennt die Segmentdauer nicht, kennt die Nachbarbilder nicht und
  kann die Abwechslungsbedingung nicht erfuellen — die ist global.
* Eine gelieferte Bewegung waere nicht nachvollziehbar pruefbar. Eine
  gelieferte Bounding-Box ist es: sie steht in ``vision.yaml``, laesst sich mit
  einem Blick aufs Bild verifizieren und von Hand korrigieren.
* Bildfakten sind ueber Prompt- und Modellwechsel hinweg stabiler als
  Bewegungsurteile. Ein Modellwechsel darf nicht den kompletten Segment-Cache
  invalidieren.
* Die Regeln "Gesicht nicht anschneiden" und "Makro nicht ueberzoomen" sind
  zehn Zeilen Code (:mod:`slideshow.kbplan`). Dafuer braucht es kein
  Sprachmodell.

Analysiert wird das **normalisierte Cache-Bild**, nicht das Original (E2).
Damit gelten die gelieferten Koordinaten unveraendert im Koordinatensystem des
Ken-Burns-Filters — keine Transformation, kein Versatz, und kein Weg, auf dem
eine Schutzbox unbemerkt verrutscht. Der Preis: bei ``portrait: blur`` sieht
das Modell auch die unscharfen Seitenbalken. Das ist kein Nachteil, sondern
der Punkt — es ist genau das Bild, das gerendert wird.

**Determinismus** entsteht dadurch, dass die Antwort *einmal* in
``vision.yaml`` festgeschrieben wird; ab da ist die ganze Kette rein
deterministisch. Gefragt wird nur, wenn ``hash + prompt + model`` sich geaendert
haben — ein zweiter Lauf ohne neue Bilder macht null Requests (A4).

**Ausfall** ist immer je Bild: fehlender Schluessel, kein Netz, Rate-Limit,
Refusal, unlesbares JSON, zu niedrige Konfidenz. Jeder dieser Faelle laesst
schlicht den Eintrag weg; ``build`` setzt dann kein ``kb:`` und das Bild laeuft
ueber die heutige Rotation. Die Testsuite laeuft ohne Netzzugriff (A5) — das
SDK wird deshalb erst *im Funktionskoerper* importiert.
"""

from __future__ import annotations

import base64
import concurrent.futures as _fut
import io
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .cache import hash_file
from .errors import SlideshowError
from .models import VisionDoc, VisionEntry, dump_vision_yaml

log = logging.getLogger("slideshow.vision")

#: Dateiname im Projektroot.
VISION_NAME = "vision.yaml"

#: Version des Prompts. **Ein bewusst gepflegtes Feld, kein Nebeneffekt einer
#: Textaenderung**: sie geht in den Analyse-Cache-Key ein, und eine Erhoehung
#: bedeutet einen kompletten Neu-Analyselauf — und damit einen kompletten
#: Renderlauf, weil sich jede Bewegung aendern kann (Abschnitt 8).
PROMPT_VERSION = 1

#: Kosten sind kein Argument (rund ein Dollar je 100 Bilder), Genauigkeit
#: schon: aus diesen Antworten kommen **Koordinaten**.
DEFAULT_MODEL = "claude-opus-5"

#: Groesse des Analysebildes. Eine Genauigkeits-, keine Kostenentscheidung:
#: der bildunabhaengige Teil der Rechnung (Ausgabetokens, gecachter Praefix)
#: ist ein Boden, den keine Aufloesung unterschreitet — von 1024x576 auf
#: Thumbnailgroesse spart 36 %, kostet aber Boxgenauigkeit, und eine zu grosse
#: Schutzbox klemmt den Zoom auf Stillstand.
ANALYSE_GROESSE = (1024, 576)
JPEG_QUALITAET = 80

#: Wie viele Bilder ``--count-tokens`` nachmisst. Steht hier und nicht als
#: Vorgabewert allein, weil der Aufrufer die Zahl **vor** dem Zaehlen kennen
#: muss: sie steht in der Datenschutz-Rueckfrage.
PROBEN = 5

#: Modelle ohne ``effort``-Parameter. Ein ``output_config: {"effort": ...}``
#: gegen eines davon ist ein 400 und kein stiller Rueckfall — deshalb steht die
#: Liste hier und nicht als Kommentar.
OHNE_EFFORT = ("claude-haiku-4-5", "claude-3-", "claude-sonnet-4-5")

#: Listenpreise in USD je Million Tokens (Eingabe, Ausgabe). Fuer Sonnet 5 ist
#: bewusst der **Regelpreis** angesetzt und nicht der Einfuehrungspreis: eine
#: datumsabhaengige Zahl im Code rottet, und der Bericht soll nicht besser
#: aussehen, als die naechste Rechnung ausfaellt.
PREISE = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
#: Cache-Schreiben kostet 1,25x Eingabe, Cache-Lesen 0,1x.
CACHE_SCHREIBEN, CACHE_LESEN = 1.25, 0.1
#: Aufpreis eines regionalen Bedrock-Endpunkts. Wer Bedrock wegen der
#: Datenresidenz nimmt, zahlt diese 10 % — sie *sind* die Datenresidenz.
BEDROCK_EU_AUFSCHLAG = 1.10


# --------------------------------------------------------------------------
# Datenschutz (E6)
# --------------------------------------------------------------------------

#: Was beim ersten Lauf in einem Projekt zu bestaetigen ist. Der Punkt, den ein
#: Nutzer nicht erwartet, steht bewusst zuerst: die Zwei-Jahres-Frist gilt
#: **unabhaengig von jeder Vereinbarung**.
DATENSCHUTZ = [
    "Die Analyse schickt private Fotos mit erkennbaren Personen an einen "
    "externen Dienst.",
    "Von den automatischen Trust-&-Safety-Systemen markierte Inhalte koennen bis "
    "zu ZWEI JAHRE aufbewahrt werden — unabhaengig von jeder Vereinbarung.",
    "Regelfall ohne Markierung: Inhalte werden nicht dauerhaft vorgehalten und "
    "innerhalb von 30 Tagen geloescht; zum Training werden sie ohne "
    "ausdrueckliche Erlaubnis nicht verwendet (Claude-API, nicht die "
    "Consumer-Produkte).",
    "Bilder gehen als base64 im Messages-Request — nicht ueber die Files-API, "
    "die Dateien bis zur ausdruecklichen Loeschung haelt.",
]

#: Dieselbe Auskunft fuer Bedrock. Es ist ausdruecklich eine **andere**
#: Zusagentabelle: dort richtet sich die Datenhaltung nach Amazon Bedrock, nicht
#: nach den Zusagen von Anthropic.
DATENSCHUTZ_BEDROCK = [
    "Die Analyse schickt private Fotos mit erkennbaren Personen an Amazon "
    "Bedrock.",
    "Die Inferenz laeuft auf AWS-verwalteter Infrastruktur; Anthropic-Personal "
    "hat darauf keinen Zugriff.",
    "Ein regionaler Endpunkt (EU-Inferenzprofil) garantiert, dass die Bilder die "
    "gewaehlte Region nicht verlassen — gegen 10 % Aufpreis auf alles.",
    "Es gelten die AWS-Datenschutzbedingungen, nicht die von Anthropic. Sie sind "
    "eigenstaendig zu lesen.",
]


# --------------------------------------------------------------------------
# Prompt und Schema
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Du bist ein Bildanalyse-Werkzeug fuer einen Slideshow-Renderer. Du beschreibst,
was auf einem Foto zu sehen ist. Du entscheidest **nicht**, wie sich die Kamera
bewegen soll — das rechnet der Aufrufer aus deinen Angaben.

Alle Koordinaten sind auf das gezeigte Bild normalisiert: (0,0) ist links oben,
(1,1) rechts unten. Boxen sind [x0, y0, x1, y1] mit x0 < x1 und y0 < y1.

Das Bild kann links und rechts unscharfe Balken haben — dann ist es ein
Hochformat-Foto in einem 16:9-Rahmen. Beschreibe nur das scharfe Motiv; die
Balken sind kein Bildinhalt.

Zu den Feldern:

- scene: die Klasse, die die Bildaussage am besten trifft. `other`, wenn keine
  passt — das ist ein gueltiges Ergebnis und besser als eine geratene Klasse.
- axis: die Achse, entlang derer das Bild "laeuft" — ein Panorama horizontal,
  ein Turm vertikal. `none`, wenn es keine gibt.
- horizon: Hoehe der Horizontlinie, oder null.
- focus: der Punkt, auf den eine Kamerafahrt zulaufen sollte — das Hauptmotiv.
- subjects: erkennbare Motive mit Box, Art und Wichtigkeit (weight 0..1).
- protect: was zu **keinem** Zeitpunkt angeschnitten werden darf — Gesichter,
  Koepfe ueber dem Horizont, Schrift. Sei hier sparsam und genau: jede Box
  begrenzt den Zoom. Hoechstens vier Boxen, jede zwischen 1 % und 80 % der
  Bildflaeche. Gibt es nichts Schuetzenswertes, liefere eine leere Liste.
- detail: Detaildichte 0..1. Ein glatter Himmel ist 0.1, ein Makro mit feiner
  Struktur 0.9. Der Wert deckelt den Zoom.
- depth: `into` bei Tiefenwirkung ins Bild hinein, `out` bei einem Motiv, das
  auf den Betrachter zukommt, sonst `flat`.
- quiet: die groesste zusammenhaengende Flaeche ohne Motiv und ohne starke
  Struktur — dort koennte spaeter Text stehen.
- suggest: unverbindlicher Vorschlag, einer von pan_left, pan_right, pan_up,
  pan_down, zoom_in, zoom_out, still. Wird nur als Stichentscheid benutzt.
- conf: wie sicher du dir bei der Klassifikation bist. Sei ehrlich: unter 0.5
  wird das Bild wie `other` behandelt, und das ist besser als eine falsche
  Klasse mit hoher Konfidenz.
- note: ein knapper deutscher Satz, was zu sehen ist. Er steht zur
  Sichtpruefung in der Datei.

Erfinde nichts. Was du nicht siehst, laesst du weg oder setzt auf null.\
"""

#: JSON-Schema der strukturierten Ausgabe.
#:
#: **Es kann die Wertebereiche nicht erzwingen.** Strukturierte Ausgabe
#: unterstuetzt ``enum``, ``const``, ``anyOf`` und
#: ``additionalProperties: false``, aber **keine** numerischen Schranken
#: (``minimum``/``maximum``) und keine Laengenbegrenzungen. Eine Box mit ``-0.3``
#: oder eine ``conf`` von ``1.7`` ist schemakonform. Das Schema garantiert nur,
#: dass sich die Antwort *parsen* laesst — die Plausibilitaetspruefung in
#: :func:`bereinigen` ist damit Pflicht und nicht Vorsicht.
ANTWORT_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string", "enum": [
            "landscape_wide", "portrait_person", "group", "architecture",
            "detail_macro", "action", "interior", "document", "other"]},
        "axis": {"type": "string", "enum": ["horizontal", "vertical", "none"]},
        "horizon": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "focus": {"anyOf": [{"type": "array", "items": {"type": "number"}},
                            {"type": "null"}]},
        "subjects": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "box": {"type": "array", "items": {"type": "number"}},
                "kind": {"type": "string", "enum": [
                    "person", "face", "text", "animal", "object"]},
                "weight": {"type": "number"},
            },
            "required": ["box", "kind", "weight"],
            "additionalProperties": False,
        }},
        "protect": {"type": "array", "items": {
            "type": "array", "items": {"type": "number"}}},
        "detail": {"type": "number"},
        "depth": {"type": "string", "enum": ["into", "out", "flat"]},
        "quiet": {"anyOf": [{"type": "array", "items": {"type": "number"}},
                            {"type": "null"}]},
        "suggest": {"type": "string", "enum": [
            "pan_left", "pan_right", "pan_up", "pan_down", "zoom_in",
            "zoom_out", "still"]},
        "conf": {"type": "number"},
        "note": {"type": "string"},
    },
    "required": ["scene", "axis", "horizon", "focus", "subjects", "protect",
                 "detail", "depth", "quiet", "suggest", "conf", "note"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Bild aufbereiten
# --------------------------------------------------------------------------

def analysebild(pfad: Path, groesse: tuple[int, int] = ANALYSE_GROESSE) -> str:
    """Cache-Bild auf Analysegroesse bringen und base64-kodiert liefern.

    Skaliert wird auf **feste Kanten**, nicht auf "hoechstens so gross": die
    Cache-Bilder haben alle dasselbe Seitenverhaeltnis (Normalform), und eine
    einheitliche Analysegroesse haelt sowohl die Kosten als auch die
    Boxgenauigkeit ueber den ganzen Lauf gleich.
    """
    from PIL import Image

    with Image.open(pfad) as im:
        im = im.convert("RGB").resize(groesse, Image.LANCZOS)
        puffer = io.BytesIO()
        im.save(puffer, format="JPEG", quality=JPEG_QUALITAET)
    return base64.standard_b64encode(puffer.getvalue()).decode("ascii")


# --------------------------------------------------------------------------
# Antwort auswerten
# --------------------------------------------------------------------------

def bereinigen(roh: dict) -> tuple[dict, list[str]]:
    """Unplausibles verwerfen, statt den ganzen Eintrag fallen zu lassen.

    Eine erfundene Schutzbox klemmt den Zoom auf 1,05 und laesst das Bild
    stehen — der Ausfall waere still und saehe nach einem Fehler im Planer aus.
    Hier faellt sie einzeln heraus und wird gemeldet; der Rest des Eintrags
    bleibt brauchbar.
    """
    daten = dict(roh)
    meldungen: list[str] = []

    def box_ok(b, *, flaeche: bool) -> bool:
        if not isinstance(b, (list, tuple)) or len(b) != 4:
            return False
        try:
            x0, y0, x1, y1 = (float(v) for v in b)
        except (TypeError, ValueError):
            return False
        if any(not 0.0 <= v <= 1.0 for v in (x0, y0, x1, y1)):
            return False
        if x1 <= x0 or y1 <= y0:
            return False
        return not flaeche or 0.01 <= (x1 - x0) * (y1 - y0) <= 0.80

    schutz = [b for b in (daten.get("protect") or []) if box_ok(b, flaeche=True)]
    if len(schutz) != len(daten.get("protect") or []):
        meldungen.append(
            f"{len(daten.get('protect') or []) - len(schutz)} Schutzboxen "
            f"verworfen (ausserhalb des Bildes, verdreht oder unplausibel gross)")
    if len(schutz) > 4:
        meldungen.append(f"{len(schutz) - 4} Schutzboxen ueber der Vierergrenze "
                         f"verworfen")
        schutz = schutz[:4]
    daten["protect"] = [tuple(float(v) for v in b) for b in schutz]

    motive = [s for s in (daten.get("subjects") or [])
              if isinstance(s, dict) and box_ok(s.get("box"), flaeche=False)]
    if len(motive) != len(daten.get("subjects") or []):
        meldungen.append("unplausible Motivboxen verworfen")
    daten["subjects"] = motive[:8]

    for feld in ("focus", "quiet"):
        wert = daten.get(feld)
        if wert is None:
            continue
        if feld == "quiet":
            if not box_ok(wert, flaeche=False):
                daten[feld] = None
                meldungen.append("quiet verworfen")
            continue
        if (not isinstance(wert, (list, tuple)) or len(wert) != 2
                or any(not 0.0 <= float(v) <= 1.0 for v in wert)):
            daten[feld] = None
            meldungen.append("focus verworfen")

    for feld, vorgabe in (("detail", 0.5), ("conf", 1.0), ("horizon", None)):
        wert = daten.get(feld, vorgabe)
        if wert is None:
            continue
        try:
            zahl = float(wert)
        except (TypeError, ValueError):
            daten[feld] = vorgabe
            continue
        # Geklemmt statt verworfen: eine ``conf`` von 1,7 ist als "sehr sicher"
        # gemeint und nicht als Unsinn, und ``detail: -0.2`` als "sehr wenig".
        daten[feld] = min(1.0, max(0.0, zahl))

    # Ein leeres Enum-Feld faellt auf die Vorgabe zurueck. Das Schema laesst das
    # eigentlich nicht zu — aber ein Modellwechsel oder ein Bedrock-Endpunkt
    # ohne strukturierte Ausgabe soll hier nicht mit KeyError abbrechen.
    daten.setdefault("scene", "other")
    daten.setdefault("axis", "none")
    daten.setdefault("depth", "flat")
    daten["suggest"] = str(daten.get("suggest") or "")
    daten["note"] = str(daten.get("note") or "")[:300]
    return daten, meldungen


def eintrag_aus_antwort(text: str, *, bildhash: str) -> tuple[VisionEntry, list[str]]:
    """JSON-Antwort in einen geprueften Eintrag verwandeln."""
    roh = json.loads(text)
    if not isinstance(roh, dict):
        raise ValueError("Antwort ist kein JSON-Objekt")
    daten, meldungen = bereinigen(roh)
    daten["hash"] = bildhash
    daten["stage"] = "geometry"
    return VisionEntry.model_validate(daten), meldungen


# --------------------------------------------------------------------------
# Kosten (Abschnitt 9, Abnahmekriterium A6)
# --------------------------------------------------------------------------

@dataclass
class Verbrauch:
    """Ist-Tokenverbrauch eines Laufs, aufsummiert aus ``usage``."""

    eingabe: int = 0
    ausgabe: int = 0
    cache_geschrieben: int = 0
    cache_gelesen: int = 0
    requests: int = 0

    def dazu(self, usage) -> None:
        self.requests += 1
        self.eingabe += int(getattr(usage, "input_tokens", 0) or 0)
        self.ausgabe += int(getattr(usage, "output_tokens", 0) or 0)
        self.cache_geschrieben += int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0)
        self.cache_gelesen += int(getattr(usage, "cache_read_input_tokens", 0) or 0)

    def kosten(self, modell: str, *, bedrock_regional: bool = False) -> float | None:
        """Ist-Kosten in USD, oder ``None`` bei unbekanntem Modell.

        ``None`` und nicht 0,0: eine Null im Bericht liest sich wie "hat nichts
        gekostet", und das waere eine Falschaussage ueber ein Modell, dessen
        Preis dieses Werkzeug schlicht nicht kennt.
        """
        preis = PREISE.get(modell.split("anthropic.")[-1])
        if preis is None:
            return None
        ein, aus = preis
        usd = ((self.eingabe
                + self.cache_geschrieben * CACHE_SCHREIBEN
                + self.cache_gelesen * CACHE_LESEN) * ein
               + self.ausgabe * aus) / 1_000_000
        return usd * (BEDROCK_EU_AUFSCHLAG if bedrock_regional else 1.0)


#: Tokenprofil eines Requests aus Abschnitt 9 — Grundlage der *Schaetzung*
#: vor dem Lauf. Die Ist-Zahlen kommen hinterher aus ``usage``; weicht beides
#: um mehr als ein Viertel voneinander ab, stimmt eine der Annahmen nicht.
TOKENS_BILD = 786          # (1024 * 576) / 750
TOKENS_TEXT = 40
TOKENS_PRAEFIX = 1200      # Systemprompt, gecacht
TOKENS_AUSGABE = 250       # sparsam, mit `effort: low`


def kosten_schaetzung(anzahl: int, modell: str, *,
                      bedrock_regional: bool = False) -> float | None:
    """Was der Lauf voraussichtlich kostet — vor dem ersten Request.

    Genau ein Request zahlt den Praefix voll (er schreibt den Cache), alle
    weiteren lesen ihn. Genau dafuer laeuft in :func:`analysiere` einer voraus.
    """
    if anzahl <= 0:
        return 0.0
    v = Verbrauch(
        eingabe=(TOKENS_BILD + TOKENS_TEXT) * anzahl,
        ausgabe=TOKENS_AUSGABE * anzahl,
        cache_geschrieben=TOKENS_PRAEFIX,
        cache_gelesen=TOKENS_PRAEFIX * (anzahl - 1),
        requests=anzahl)
    return v.kosten(modell, bedrock_regional=bedrock_regional)


@dataclass
class Bericht:
    """Was ein Analyselauf ueber sich selbst zu sagen hat."""

    analysiert: int = 0
    uebernommen: int = 0
    ausgefallen: int = 0
    verbrauch: Verbrauch = field(default_factory=Verbrauch)
    warnungen: list[str] = field(default_factory=list)

    @property
    def gefragt(self) -> bool:
        return self.verbrauch.requests > 0


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

#: Env-Variablen, mit denen Bedrock **ohne** AWS-Signatur auskommt. Ist eine
#: davon gesetzt, spricht das SDK per Bearer-Token — dann braucht es kein
#: botocore, und ein AWS-Profil waere wirkungslos.
BEDROCK_TOKEN_ENV = ("AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_AWS_API_KEY")


def sigv4_noetig(aws_profile: str | None) -> bool:
    """Ob der Bedrock-Client mit AWS-Signatur arbeitet — und damit botocore braucht.

    Dieselbe Weiche wie ``anthropic.lib.aws._credentials.resolve_auth_mode``,
    hier nachgezogen, weil das Ergebnis am fertigen Client nur privat
    (``_use_sigv4``) abzulesen waere.

    Das erklaert zugleich, wozu es ``--aws-profile`` ueberhaupt gibt: dort
    zaehlt nur ein **Konstruktorargument**. Ein blosses ``AWS_PROFILE`` in der
    Umgebung wird erst in der AWS-Standardkette gesehen und verliert damit
    still gegen einen gesetzten Bearer-Token.
    """
    if aws_profile:
        return True
    return not any(os.environ.get(name) for name in BEDROCK_TOKEN_ENV)


def client_bauen(*, bedrock_region: str | None = None,
                 aws_profile: str | None = None):
    """Den API-Client bauen — Erstanbieter oder Bedrock (E11).

    Der Unterschied ist eine Client-Weiche und ein Praefix am Modellnamen,
    sonst nichts: beide sprechen dieselbe Messages-API. Wer die Fotos nicht aus
    der EU herauslassen will, soll das ohne Umbau tun koennen.

    Anmeldedaten uebergibt diese Funktion bewusst keine — sie loest das SDK aus
    der Umgebung auf (``ANTHROPIC_API_KEY`` beziehungsweise die
    AWS-Standardkette). Einzige Ausnahme ist ``aws_profile``, siehe
    :func:`sigv4_noetig`.

    Der Import steht im Funktionskoerper, damit das SDK eine **optionale**
    Abhaengigkeit bleibt: ``build`` und die gesamte Testsuite laufen ohne es
    (A5).
    """
    try:
        import anthropic
    except ImportError as exc:
        raise SlideshowError(
            "Das Paket `anthropic` fehlt — ohne es kann `analyze` nicht "
            "fragen.\n"
            "  pip install 'slideshow[vision]'   (oder: pip install anthropic)\n"
            "  `slideshow build --no-vision` baut ohne Bildanalyse weiter.") from exc

    if bedrock_region:
        # Hier und nicht erst beim Signieren: das SDK importiert botocore
        # **lazy im ersten Request**, und dort faengt :func:`analysiere` je Bild
        # ab — aus einer fehlenden Abhaengigkeit wuerden 187 gleichlautende
        # Warnungen und eine leere ``vision.yaml`` statt eines Abbruchs.
        if sigv4_noetig(aws_profile):
            try:
                import botocore.auth  # noqa: F401
            except ImportError as exc:
                raise SlideshowError(
                    "Fuer `--bedrock` fehlt botocore — es signiert die "
                    "Anfragen (SigV4).\n"
                    "  pip install 'slideshow[vision]'   (oder: pip install "
                    "'anthropic[bedrock]')") from exc
        return anthropic.AnthropicBedrockMantle(aws_region=bedrock_region,
                                                aws_profile=aws_profile)
    return anthropic.Anthropic()


#: Region-Praefix -> Inferenzprofil auf Bedrock. Das Profil ist **kein
#: Schmuck**: ``anthropic.claude-…`` zeigt auf das Basismodell einer einzelnen
#: Region, und die neueren Modelle gibt es dort nicht (404). Erst
#: ``eu.anthropic.claude-…`` ist das regionale Inferenzprofil — und damit genau
#: das, wofuer :data:`BEDROCK_EU_AUFSCHLAG` die 10 % ansetzt. Ohne den Praefix
#: versprach die Datenschutz-Rueckfrage eine Datenresidenz, die die Modell-ID
#: gar nicht anforderte.
INFERENZPROFIL = {"eu": "eu", "us": "us", "ap": "apac"}


def modellname(modell: str, *, bedrock: bool, region: str | None = None) -> str:
    """Die Modell-ID fuer das gewaehlte Ziel.

    Ein Name mit Punkt gilt als vollqualifiziert und bleibt unangetastet — das
    ist die Ausweichtuer fuer alles, was diese Tabelle nicht kennt
    (``global.anthropic.…``, ein Fremdmodell, eine neue Region).
    """
    if not bedrock or "." in modell:
        return modell
    profil = INFERENZPROFIL.get(str(region or "").split("-")[0])
    if profil is None:
        # Kein stiller Griff zu einer ID, die es vermutlich nicht gibt: die
        # Region steht dem Nutzer offen, die Profiltabelle hier auch.
        log.warning("Kein Inferenzprofil fuer Region %r bekannt — die Anfrage "
                    "geht an das Basismodell `anthropic.%s`. Notfalls das "
                    "Profil ausschreiben: --model <profil>.anthropic.%s",
                    region, modell, modell)
        return f"anthropic.{modell}"
    return f"{profil}.anthropic.{modell}"


#: Was ein Modellname sein muss, um ueberhaupt einer zu sein.
_KURZNAMEN = ("opus", "sonnet", "haiku", "fable")


def modell_pruefen(modell: str) -> None:
    """Einen Tippfehler melden, **bevor** das erste Bild rausgeht.

    Der Fall aus der Praxis war ``--model sonnet-5``: die Kurzform ist keine
    Modell-ID, und der 404 kam erst nach dem Hochladen des ersten Bildes — also
    zu spaet, um noch etwas zu verhindern.
    """
    if modell.startswith("claude-") or "." in modell:
        return
    kurz = modell.split("-")[0]
    hinweis = (f" Gemeint ist vermutlich `claude-{modell}`."
               if kurz in _KURZNAMEN else "")
    raise SlideshowError(
        f"`{modell}` sieht nicht wie eine Modell-ID aus.{hinweis}\n"
        f"  Bekannt sind: {', '.join(sorted(PREISE))}\n"
        f"  Eine vollqualifizierte Bedrock-ID (mit Punkt, etwa "
        f"`eu.anthropic.claude-sonnet-5`) wird unveraendert durchgereicht.")


def _anfrage_bauen(modell: str, b64: str, name: str) -> dict:
    """Die Argumente eines Requests.

    Der gecachte Praefix (Systemprompt) steht vorn und aendert sich ueber den
    ganzen Lauf nicht — das ist die Bedingung dafuer, dass er ueberhaupt
    cacht: Caching ist ein Praefix-Vergleich, und jede Byte-Aenderung davor
    entwertet alles danach. Der volatile Teil (Bild und Dateiname) steht
    hinter dem Haltepunkt.
    """
    args: dict = {
        "model": modell,
        # Grosszuegig, nicht knapp: ``max_tokens`` deckelt auf diesen Modellen
        # **Thinking und Antworttext zusammen**. Ein knapper Wert schneidet
        # nicht die Ausgabe ab, sondern erst das Denken und dann die Antwort —
        # das Ergebnis ist kein sparsamer Lauf, sondern ein unbrauchbares JSON
        # und ein bezahlter Request. Der Sparhebel ist `effort`.
        "max_tokens": 8192,
        "system": [{"type": "text", "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg",
                                         "data": b64}},
            {"type": "text", "text": f"Analysiere dieses Bild ({name})."},
        ]}],
        "output_config": {
            "format": {"type": "json_schema", "schema": ANTWORT_SCHEMA},
        },
    }
    # Die Aufgabe ist Klassifikation, kein Denksport. `effort` ist der
    # Sparhebel — aber nicht jedes Modell kennt ihn, und ein unbekannter
    # Parameter ist dort ein 400 und kein stiller Rueckfall.
    if not any(modell.split("anthropic.")[-1].startswith(m) for m in OHNE_EFFORT):
        args["output_config"]["effort"] = "low"
    return args


#: Fehler, die nicht am Bild liegen koennen. Gegen den Klassennamen und nicht
#: gegen den Typ, weil das SDK hier eine optionale Abhaengigkeit ist (A5) — ein
#: ``except anthropic.NotFoundError`` waere ein Import auf jedem Pfad.
KONFIGURATIONSFEHLER = ("NotFoundError", "AuthenticationError",
                        "PermissionDeniedError")


def ist_konfigurationsfehler(exc: BaseException) -> bool:
    """Ob dieser Fehler sich beim naechsten Bild wortgleich wiederholen wird."""
    return type(exc).__name__ in KONFIGURATIONSFEHLER


def konfigurationshinweis(exc: BaseException, *, modell: str,
                          region: str | None) -> str:
    """Aus dem Abbruch den naechsten Schritt machen.

    Ein 404 auf Bedrock nennt nur die ID, die es nicht gibt — welche es gaebe,
    steht nirgends. Die Antwort darauf ist ein Kommando und keine Vermutung:
    nicht jedes Modell hat in jeder Region ein Inferenzprofil, und manche gibt
    es dort nur unter ihrer datierten ID.
    """
    text = (f"Der Vorablauf ist gescheitert — es wurde nichts weiter gefragt.\n"
            f"  Modell: {modell}\n  {type(exc).__name__}: {exc}")
    if region:
        text += (
            f"\n\nWelche IDs es in {region} gibt:\n"
            f"  aws bedrock list-inference-profiles --region {region} \\\n"
            f"    --query \"inferenceProfileSummaries[?contains("
            f"inferenceProfileId,'anthropic')].inferenceProfileId\" "
            f"--output text\n"
            f"Eine ausgeschriebene ID (mit Punkt) reicht `--model` unveraendert "
            f"durch — noetig etwa dort, wo es ein Modell nur unter seiner "
            f"datierten Kennung gibt.")
    return text


def _antworttext(nachricht) -> str:
    """Den JSON-Text aus einer Antwort holen — und Refusals erkennen.

    ``stop_reason`` wird **vor** ``content`` gelesen: bei einer abgelehnten
    Anfrage ist ``content`` leer oder unvollstaendig, und ein
    ``content[0].text`` liefe dort in einen IndexError, der wie ein Bug
    aussieht und keiner ist.
    """
    grund = getattr(nachricht, "stop_reason", None)
    if grund == "refusal":
        art = getattr(getattr(nachricht, "stop_details", None), "category", None)
        raise ValueError(f"Anfrage abgelehnt ({art or 'ohne Angabe'})")
    if grund == "max_tokens":
        raise ValueError("Antwort abgeschnitten — `max_tokens` zu knapp")
    for block in getattr(nachricht, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError("Antwort enthaelt keinen Text")


# --------------------------------------------------------------------------
# Der Lauf
# --------------------------------------------------------------------------

def offene_bilder(pfade: dict[str, Path], vorhanden: VisionDoc, *, modell: str,
                  prompt: int) -> tuple[dict[str, str], dict[str, VisionEntry]]:
    """Was neu zu fragen ist — und was aus der Datei uebernommen wird (A4).

    Uebernommen wird ein Eintrag, solange **Bild-Hash, Prompt-Version und
    Modell** gleich bleiben. Damit ueberlebt eine von Hand korrigierte Box
    jeden weiteren Lauf, und ein zweiter Lauf ohne neue Bilder macht null
    Requests.
    """
    gleicher_lauf = (vorhanden.model == modell and vorhanden.prompt == prompt)
    zu_fragen: dict[str, str] = {}
    behalten: dict[str, VisionEntry] = {}
    for rel, pfad in pfade.items():
        digest = hash_file(pfad)
        alt = vorhanden.images.get(rel)
        if gleicher_lauf and alt is not None and alt.hash == digest:
            behalten[rel] = alt
            continue
        zu_fragen[rel] = digest
    return zu_fragen, behalten


def analysiere(pfade: dict[str, Path], *, vorhanden: VisionDoc | None = None,
               modell: str = DEFAULT_MODEL, bedrock_region: str | None = None,
               aws_profile: str | None = None,
               jobs: int = 8, fortschritt=None) -> tuple[VisionDoc, Bericht]:
    """Alle noch unbekannten Bilder analysieren.

    ``pfade`` bildet den projektrelativen Cache-Pfad (der Schluessel in
    ``vision.yaml`` und in ``edit.yaml``) auf die Datei ab.
    """
    vorhanden = vorhanden or VisionDoc()
    bericht = Bericht()
    zu_fragen, behalten = offene_bilder(pfade, vorhanden, modell=modell,
                                        prompt=PROMPT_VERSION)
    eintraege: dict[str, VisionEntry] = dict(behalten)
    if not zu_fragen:
        return (_doc(eintraege, pfade, modell), bericht)

    client = client_bauen(bedrock_region=bedrock_region, aws_profile=aws_profile)
    voller_name = modellname(modell, bedrock=bool(bedrock_region),
                             region=bedrock_region)

    def einer(rel: str, digest: str):
        args = _anfrage_bauen(voller_name, analysebild(pfade[rel]), Path(rel).name)
        nachricht = client.messages.create(**args)
        return rel, digest, nachricht

    offen = list(zu_fragen.items())

    # Der Prompt-Cache wird erst lesbar, wenn die erste Antwort **beginnt**.
    # Bei parallelem Fan-out zahlen sonst alle Requests den vollen Praefix.
    # Deshalb laeuft einer allein voraus; auf sein Ergebnis zu warten ist
    # etwas konservativer als noetig und dafuer nur ein Codepfad.
    erste, rest = offen[:1], offen[1:]

    def einsammeln(ergebnis) -> None:
        rel, digest, nachricht = ergebnis
        bericht.verbrauch.dazu(getattr(nachricht, "usage", None))
        try:
            eintrag, meldungen = eintrag_aus_antwort(_antworttext(nachricht),
                                                     bildhash=digest)
        except Exception as exc:                       # noqa: BLE001
            # Je Bild einzeln: unlesbares JSON, Refusal, unplausible Werte —
            # jeder Fall kostet genau dieses eine Bild, nicht den Lauf.
            bericht.ausgefallen += 1
            bericht.warnungen.append(f"{rel}: {exc}")
            return
        for m in meldungen:
            bericht.warnungen.append(f"{rel}: {m}")
        eintraege[rel] = eintrag
        bericht.uebernommen += 1

    fataler: Exception | None = None
    for gruppe, parallel in ((erste, 1), (rest, max(1, jobs))):
        if not gruppe:
            continue
        with _fut.ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(einer, rel, d): rel for rel, d in gruppe}
            for fut in _fut.as_completed(futures):
                rel = futures[fut]
                bericht.analysiert += 1
                try:
                    einsammeln(fut.result())
                except Exception as exc:               # noqa: BLE001
                    bericht.ausgefallen += 1
                    bericht.warnungen.append(f"{rel}: {exc}")
                    if ist_konfigurationsfehler(exc):
                        fataler = exc
                if fortschritt is not None:
                    fortschritt(bericht.analysiert, len(zu_fragen), rel)
        # Die Regel "Ausfall ist immer je Bild" gilt fuer das, was am *Bild*
        # liegt. Ein falscher Modellname, ein fehlendes Recht, ein abgelaufener
        # Schluessel liegen am Lauf — und wiederholen sich 186-mal. Genau
        # dafuer laeuft der erste Request allein voraus.
        if fataler is not None:
            raise SlideshowError(konfigurationshinweis(
                fataler, modell=voller_name, region=bedrock_region))

    return (_doc(eintraege, pfade, modell), bericht)


def analysiere_batch(pfade: dict[str, Path], *, vorhanden: VisionDoc | None = None,
                     modell: str = DEFAULT_MODEL, warten=None
                     ) -> tuple[VisionDoc, Bericht]:
    """Dasselbe ueber die Message-Batches-API — halber Preis, 29 Tage Ablage.

    **Bewusst nicht die Vorgabe** (E4): die Batch-API ist nicht ZDR-faehig und
    speichert die Auftraege 29 Tage. Bei privaten Fotos ist das der falsche
    Tausch fuer 0,55 USD je 100 Bilder. Wem die 29 Tage gleichgueltig sind, der
    waehlt sie ausdruecklich.

    Auf Bedrock gibt es sie nicht — was insofern schade ist, als der Grund, sie
    auf der Erstanbieter-API abzulehnen, dort gerade entfiele.
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    vorhanden = vorhanden or VisionDoc()
    bericht = Bericht()
    zu_fragen, behalten = offene_bilder(pfade, vorhanden, modell=modell,
                                        prompt=PROMPT_VERSION)
    eintraege: dict[str, VisionEntry] = dict(behalten)
    if not zu_fragen:
        return (_doc(eintraege, pfade, modell), bericht)

    client = client_bauen()
    # ``custom_id`` traegt den Index, nicht den Pfad: die IDs sind in Laenge und
    # Zeichenvorrat begrenzt, ein Cache-Pfad ist es nicht.
    reihenfolge = list(zu_fragen.items())
    anfragen = [
        Request(custom_id=f"bild-{i}",
                params=MessageCreateParamsNonStreaming(
                    **_anfrage_bauen(modell, analysebild(pfade[rel]),
                                     Path(rel).name)))
        for i, (rel, _digest) in enumerate(reihenfolge)]

    stapel = client.messages.batches.create(requests=anfragen)
    log.info("Batch %s mit %d Anfragen abgeschickt", stapel.id, len(anfragen))
    if warten is not None:
        warten(stapel.id)

    bericht.analysiert = len(anfragen)
    for ergebnis in client.messages.batches.results(stapel.id):
        # Die Ergebnisse kommen in **beliebiger** Reihenfolge zurueck — nie
        # ueber die Position zuordnen, immer ueber ``custom_id``.
        try:
            index = int(str(ergebnis.custom_id).rsplit("-", 1)[1])
            rel, digest = reihenfolge[index]
        except (ValueError, IndexError):
            bericht.warnungen.append(f"unbekannte custom_id {ergebnis.custom_id!r}")
            continue
        if ergebnis.result.type != "succeeded":
            bericht.ausgefallen += 1
            bericht.warnungen.append(f"{rel}: Batch-Ergebnis {ergebnis.result.type}")
            continue
        nachricht = ergebnis.result.message
        bericht.verbrauch.dazu(getattr(nachricht, "usage", None))
        try:
            eintrag, meldungen = eintrag_aus_antwort(_antworttext(nachricht),
                                                     bildhash=digest)
        except Exception as exc:                       # noqa: BLE001
            bericht.ausgefallen += 1
            bericht.warnungen.append(f"{rel}: {exc}")
            continue
        for m in meldungen:
            bericht.warnungen.append(f"{rel}: {m}")
        eintraege[rel] = eintrag
        bericht.uebernommen += 1

    return (_doc(eintraege, pfade, modell), bericht)


def tokens_zaehlen(pfade: dict[str, Path], *, modell: str = DEFAULT_MODEL,
                   proben: int = PROBEN, bedrock_region: str | None = None,
                   aws_profile: str | None = None) -> tuple[int, int]:
    """Vorab nachmessen, was ein Bild wirklich kostet (A6).

    Die Formel aus Abschnitt 9 (``w * h / 750``) ist eine Naeherung, und die
    Deckel sind modellabhaengig. Vor dem ersten grossen Lauf gehoert das an
    echten Bildern nachgemessen statt einer Preistabelle geglaubt.

    Die Weiche gehoert **auch hierher**: gezaehlt wird, indem die fertige
    Anfrage samt Bild abgeschickt wird. Ein Client ohne ``bedrock_region``
    haette die Fotos also an die Erstanbieter-API geschickt, obwohl der Aufrufer
    Bedrock gewaehlt hat — sichtbar wurde das nur, weil dort kein
    ``ANTHROPIC_API_KEY`` stand und das SDK deshalb abbrach.

    Liefert ``(anzahl_proben, tokens_je_bild)``.
    """
    client = client_bauen(bedrock_region=bedrock_region, aws_profile=aws_profile)
    voller_name = modellname(modell, bedrock=bool(bedrock_region),
                             region=bedrock_region)
    stichprobe = list(pfade.values())[:max(1, proben)]
    summe = 0
    for pfad in stichprobe:
        args = _anfrage_bauen(voller_name, analysebild(pfad), pfad.name)
        antwort = client.messages.count_tokens(
            model=args["model"], system=args["system"], messages=args["messages"])
        summe += int(antwort.input_tokens)
    return (len(stichprobe), summe // max(1, len(stichprobe)))


def _doc(eintraege: dict[str, VisionEntry], pfade: dict[str, Path],
         modell: str) -> VisionDoc:
    """Die Datei in der Reihenfolge der Bilder aufbauen.

    Nicht in der Reihenfolge, in der die Antworten eintrafen: die haengt am
    Zufall der Parallelitaet, und eine Datei, die bei jedem Lauf anders
    sortiert ist, laesst sich nicht mehr diffen.
    """
    geordnet = {rel: eintraege[rel] for rel in pfade if rel in eintraege}
    for rel, e in eintraege.items():
        geordnet.setdefault(rel, e)
    return VisionDoc(version=1, model=modell, prompt=PROMPT_VERSION,
                     images=geordnet)


def speichern(doc: VisionDoc, pfad: Path) -> None:
    pfad.write_text(dump_vision_yaml(doc), encoding="utf-8")


def laden(pfad: Path) -> VisionDoc | None:
    """``vision.yaml`` laden, wenn es sie gibt.

    Ein *ausdruecklich* genannter Pfad, den es nicht gibt, ist Sache des
    Aufrufers; die stillschweigend gefundene Datei darf fehlen — dieselbe
    Regel wie bei Kapiteln, Reihenfolge und Feinschliff.
    """
    return VisionDoc.load(pfad) if pfad.exists() else None
