"""Die Bildanalyse — Parser, Plausibilitaet, Cache und Ausfall
(``docs/briefing-kenburns-inhaltsabhaengig.md``, Abnahmekriterien A4 bis A6).

**Diese Datei fragt nichts.** Kein Test hier oeffnet eine Verbindung, und keiner
braucht einen API-Schluessel — das ist Abnahmekriterium A5 und nicht bloss
Bequemlichkeit: eine Suite, die ohne Netz rot wird, sagt nichts mehr ueber den
Code aus. Geprueft wird alles, was **um** den Request herum passiert, und das
ist der Teil, in dem die Fehler wohnen: eine halluzinierte Box, ein Modellwechsel,
der still den ganzen Renderlauf entwertet, eine Antwort, die kein JSON ist.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from slideshow import vision
from slideshow.errors import SchemaError
from slideshow.models import VisionDoc, VisionEntry, dump_vision_yaml


def _antwort(**felder) -> str:
    """Eine schemakonforme Modellantwort mit ueberschreibbaren Feldern."""
    daten = {"scene": "landscape_wide", "axis": "horizontal", "horizon": 0.6,
             "focus": [0.38, 0.47], "subjects": [], "protect": [],
             "detail": 0.35, "depth": "flat", "quiet": None,
             "suggest": "pan_right", "conf": 0.9, "note": "Fjord"}
    daten.update(felder)
    return json.dumps(daten)


def _bild(pfad: Path, groesse=(320, 180)) -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", groesse, (90, 120, 160)).save(pfad, "JPEG", quality=90)
    return pfad


# --------------------------------------------------------------------------
# Parser und Plausibilitaet (Abschnitt 12)
# --------------------------------------------------------------------------

def test_eine_gute_antwort_wird_uebernommen():
    e, meldungen = vision.eintrag_aus_antwort(
        _antwort(protect=[[0.3, 0.3, 0.48, 0.74]]), bildhash="abc")
    assert not meldungen
    assert e.scene == "landscape_wide"
    assert e.protect == [(0.3, 0.3, 0.48, 0.74)]
    assert e.hash == "abc" and e.stage == "geometry"


def test_halluzinierte_schutzboxen_fallen_einzeln_heraus():
    """Der Fall aus Abschnitt 12, und der Grund, warum die Pruefung Pflicht ist.

    Das JSON-Schema kann numerische Schranken **nicht** ausdruecken — eine Box
    mit ``-0.3`` ist schemakonform. Faellt sie nicht hier heraus, klemmt sie den
    Zoom auf 1,05 und das Bild steht still; der Ausfall waere still und saehe
    nach einem Fehler im Planer aus.
    """
    e, meldungen = vision.eintrag_aus_antwort(_antwort(protect=[
        [-0.3, 0.1, 0.4, 0.5],        # ausserhalb des Bildes
        [0.0, 0.0, 1.0, 1.0],         # 100 % der Flaeche
        [0.5, 0.5, 0.2, 0.2],         # verdreht
        [0.30, 0.30, 0.48, 0.74],     # gut
    ]), bildhash="abc")
    assert e.protect == [(0.30, 0.30, 0.48, 0.74)]
    assert any("verworfen" in m for m in meldungen)


def test_mehr_als_vier_schutzboxen_werden_gekappt():
    boxen = [[0.1 * i, 0.1 * i, 0.1 * i + 0.2, 0.1 * i + 0.2] for i in range(6)]
    e, meldungen = vision.eintrag_aus_antwort(_antwort(protect=boxen),
                                              bildhash="abc")
    assert len(e.protect) == 4
    assert any("Vierergrenze" in m for m in meldungen)


def test_werte_ausserhalb_des_einheitsintervalls_werden_geklemmt():
    """Geklemmt, nicht verworfen: eine ``conf`` von 1,7 ist als "sehr sicher"
    gemeint und nicht als Unsinn."""
    e, _m = vision.eintrag_aus_antwort(_antwort(conf=1.7, detail=-0.2),
                                       bildhash="abc")
    assert e.conf == 1.0 and e.detail == 0.0


def test_ein_unbrauchbarer_focus_wird_verworfen_nicht_geraten():
    e, meldungen = vision.eintrag_aus_antwort(_antwort(focus=[1.9, 0.5]),
                                              bildhash="abc")
    assert e.focus is None
    assert any("focus" in m for m in meldungen)


def test_eine_lange_notiz_wird_gekuerzt():
    e, _m = vision.eintrag_aus_antwort(_antwort(note="x" * 900), bildhash="abc")
    assert len(e.note) == 300


def test_kein_json_ist_ein_ausfall_und_kein_absturz():
    with pytest.raises(Exception):
        vision.eintrag_aus_antwort("Tut mir leid, ich kann das nicht.",
                                   bildhash="abc")


class _Nachricht:
    def __init__(self, stop_reason="end_turn", text="{}", details=None):
        self.stop_reason = stop_reason
        self.stop_details = details
        self.content = [type("B", (), {"type": "text", "text": text})()]


def test_eine_abgelehnte_anfrage_wird_als_solche_erkannt():
    """``stop_reason`` wird **vor** ``content`` gelesen.

    Bei einer Ablehnung ist ``content`` leer oder unvollstaendig; ein
    ``content[0].text`` liefe dort in einen IndexError, der wie ein Bug
    aussieht und keiner ist.
    """
    nachricht = _Nachricht(stop_reason="refusal",
                           details=type("D", (), {"category": "privacy"})())
    with pytest.raises(ValueError, match="abgelehnt"):
        vision._antworttext(nachricht)


def test_eine_abgeschnittene_antwort_meldet_den_richtigen_grund():
    with pytest.raises(ValueError, match="max_tokens"):
        vision._antworttext(_Nachricht(stop_reason="max_tokens"))


# --------------------------------------------------------------------------
# A4 — Idempotenz des Analyse-Caches
# --------------------------------------------------------------------------

def _projekt_bilder(tmp_path: Path, n: int = 3) -> dict[str, Path]:
    return {f"cache/img_{i:03d}.jpg": _bild(tmp_path / f"cache/img_{i:03d}.jpg")
            for i in range(n)}


def test_ein_zweiter_lauf_ohne_neue_bilder_fragt_nichts(tmp_path: Path):
    """A4 — und damit auch die Zusage, dass Handkorrekturen ueberleben."""
    from slideshow.cache import hash_file

    pfade = _projekt_bilder(tmp_path)
    doc = VisionDoc(model=vision.DEFAULT_MODEL, prompt=vision.PROMPT_VERSION,
                    images={rel: VisionEntry(hash=hash_file(p), scene="group")
                            for rel, p in pfade.items()})
    offen, behalten = vision.offene_bilder(pfade, doc, modell=vision.DEFAULT_MODEL,
                                           prompt=vision.PROMPT_VERSION)
    assert offen == {}
    assert len(behalten) == 3


def test_ein_geaendertes_bild_wird_neu_gefragt(tmp_path: Path):
    from slideshow.cache import hash_file

    pfade = _projekt_bilder(tmp_path)
    doc = VisionDoc(model=vision.DEFAULT_MODEL, prompt=vision.PROMPT_VERSION,
                    images={rel: VisionEntry(hash=hash_file(p), scene="group")
                            for rel, p in pfade.items()})
    _bild(pfade["cache/img_001.jpg"], groesse=(300, 200))       # Inhalt geaendert
    offen, behalten = vision.offene_bilder(pfade, doc, modell=vision.DEFAULT_MODEL,
                                           prompt=vision.PROMPT_VERSION)
    assert list(offen) == ["cache/img_001.jpg"]
    assert len(behalten) == 2


@pytest.mark.parametrize("feld,wert", [("modell", "claude-sonnet-5"), ("prompt", 99)])
def test_modell_oder_promptwechsel_entwertet_alles(tmp_path: Path, feld, wert):
    """Abschnitt 8: ein Wechsel invalidiert den **kompletten** Renderlauf.

    Der Test haelt fest, dass das nicht schleichend passiert, sondern
    vollstaendig — genau deshalb warnt ``analyze`` davor, statt es
    nebenbei zu tun.
    """
    from slideshow.cache import hash_file

    pfade = _projekt_bilder(tmp_path)
    doc = VisionDoc(model=vision.DEFAULT_MODEL, prompt=vision.PROMPT_VERSION,
                    images={rel: VisionEntry(hash=hash_file(p), scene="group")
                            for rel, p in pfade.items()})
    args = {"modell": vision.DEFAULT_MODEL, "prompt": vision.PROMPT_VERSION}
    args[feld] = wert
    offen, behalten = vision.offene_bilder(pfade, doc, **args)
    assert len(offen) == 3 and behalten == {}


# --------------------------------------------------------------------------
# A5 — ohne Netz und ohne SDK
# --------------------------------------------------------------------------

def test_ohne_das_sdk_gibt_es_eine_anweisung_statt_eines_tracebacks(monkeypatch):
    """A5: ``analyze`` ohne installiertes SDK ist ein erklaerter Fehler.

    Und `build` laeuft trotzdem — das prueft
    ``test_kbplan.test_ohne_analyse_baut_alles_wie_bisher``.
    """
    import builtins

    echt = builtins.__import__

    def ohne_anthropic(name, *a, **kw):
        if name == "anthropic":
            raise ImportError("no module named anthropic")
        return echt(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", ohne_anthropic)
    from slideshow.errors import SlideshowError
    with pytest.raises(SlideshowError, match="anthropic"):
        vision.client_bauen()


def test_analysieren_ohne_offene_bilder_baut_keinen_client(tmp_path: Path):
    """Der Weg, auf dem ein zweiter Lauf wirklich nichts tut: er kommt gar
    nicht erst bis zum Client."""
    from slideshow.cache import hash_file

    pfade = _projekt_bilder(tmp_path)
    doc = VisionDoc(model=vision.DEFAULT_MODEL, prompt=vision.PROMPT_VERSION,
                    images={rel: VisionEntry(hash=hash_file(p), scene="group")
                            for rel, p in pfade.items()})
    neu, bericht = vision.analysiere(pfade, vorhanden=doc)
    assert not bericht.gefragt
    assert len(neu.images) == 3


# --------------------------------------------------------------------------
# Bildaufbereitung
# --------------------------------------------------------------------------

def test_das_analysebild_hat_immer_dieselbe_groesse(tmp_path: Path):
    """Eine einheitliche Analysegroesse haelt Kosten **und** Boxgenauigkeit
    ueber den ganzen Lauf gleich — bei ungleichen Vorschauen haenge die
    Urteilsqualitaet daran, wie grosszuegig der Hersteller war."""
    b64 = vision.analysebild(_bild(tmp_path / "a.jpg", groesse=(4000, 2250)))
    with Image.open(io.BytesIO(base64.standard_b64decode(b64))) as im:
        assert im.size == vision.ANALYSE_GROESSE
        assert im.format == "JPEG"


# --------------------------------------------------------------------------
# A6 — Kostenbericht
# --------------------------------------------------------------------------

class _Usage:
    def __init__(self, ein, aus, schreib=0, lies=0):
        self.input_tokens = ein
        self.output_tokens = aus
        self.cache_creation_input_tokens = schreib
        self.cache_read_input_tokens = lies


def test_der_kostenbericht_trifft_die_rechnung_aus_abschnitt_neun():
    """A6: die Abweichung von der Tabelle liegt unter 25 %.

    Hier sogar exakt, weil das Tokenprofil dasselbe ist — der Test haelt fest,
    dass Preise, Cache-Faktoren und Aufsummierung zusammenpassen, nicht dass
    das Modell sich an die Schaetzung haelt.
    """
    mit_cache = vision.Verbrauch()
    mit_cache.dazu(_Usage(40, 250, schreib=1200))
    for _ in range(99):
        mit_cache.dazu(_Usage(826, 250, lies=1200))
    assert mit_cache.kosten("claude-opus-5") == pytest.approx(1.10, abs=0.02)
    assert mit_cache.kosten("claude-sonnet-5") == pytest.approx(0.66, abs=0.02)

    # Haiku 4.5 bekommt bewusst ein **anderes** Profil: sein
    # Cache-Mindestpraefix liegt bei 4096 Tokens, unsere 1200 cachen dort gar
    # nicht. Deshalb spart es weniger, als der Listenpreis verspricht — 0,33
    # statt der 0,22, die ein Cache-Treffer ergaebe. Wer die Zahl mit demselben
    # `usage` wie oben nachrechnet, rechnet mit einem Treffer, den es nie gibt.
    ohne_cache = vision.Verbrauch()
    for _ in range(100):
        ohne_cache.dazu(_Usage(826 + 1200, 250))
    assert ohne_cache.kosten("claude-haiku-4-5") == pytest.approx(0.33, abs=0.02)


def test_der_regionale_bedrock_endpunkt_kostet_zehn_prozent_mehr():
    """Wer Bedrock wegen der Datenresidenz nimmt, zahlt die 10 % — sie *sind*
    die Datenresidenz."""
    v = vision.Verbrauch()
    v.dazu(_Usage(826, 250, schreib=1200))
    global_ = v.kosten("claude-opus-5")
    eu = v.kosten("claude-opus-5", bedrock_regional=True)
    assert eu == pytest.approx(global_ * 1.10)


def test_ein_unbekanntes_modell_meldet_keine_null():
    """``None`` und nicht 0,0: eine Null im Bericht liest sich wie "hat nichts
    gekostet", und das waere eine Falschaussage."""
    v = vision.Verbrauch()
    v.dazu(_Usage(826, 250))
    assert v.kosten("claude-erfunden-9") is None


def test_die_schaetzung_zahlt_den_praefix_genau_einmal():
    """Genau ein Request schreibt den Cache, alle weiteren lesen ihn — dafuer
    laeuft in ``analysiere`` einer voraus."""
    eins = vision.kosten_schaetzung(1, "claude-opus-5")
    hundert = vision.kosten_schaetzung(100, "claude-opus-5")
    assert hundert == pytest.approx(1.10, abs=0.05)
    assert hundert < eins * 100


# --------------------------------------------------------------------------
# Das Dateiformat
# --------------------------------------------------------------------------

def test_vision_yaml_ueberlebt_den_rundlauf(tmp_path: Path):
    from slideshow.models import VisionSubject

    doc = VisionDoc(model="claude-opus-5", prompt=1, images={
        "cache/img_0042.jpg": VisionEntry(
            hash="3f1c8a9e", scene="landscape_wide", axis="horizontal",
            horizon=0.61, focus=(0.38, 0.47),
            subjects=[VisionSubject(box=(0.30, 0.34, 0.46, 0.72), kind="person",
                                    weight=0.9)],
            protect=[(0.30, 0.30, 0.48, 0.74)], detail=0.35, depth="into",
            quiet=(0.05, 0.62, 0.55, 0.95), suggest="pan_right", conf=0.88,
            note="Fjord im Weitwinkel")})
    pfad = tmp_path / "vision.yaml"
    pfad.write_text(dump_vision_yaml(doc), encoding="utf-8")
    assert VisionDoc.load(pfad) == doc


def test_eine_box_steht_in_einer_zeile(tmp_path: Path):
    """Das Format ist zur Sichtpruefung da: eine Schutzbox ueber fuenf Zeilen
    laesst sich mit dem Bild daneben nicht vergleichen."""
    doc = VisionDoc(model="m", prompt=1, images={
        "cache/a.jpg": VisionEntry(hash="h", protect=[(0.1, 0.2, 0.3, 0.4)])})
    text = dump_vision_yaml(doc)
    assert "- [0.1, 0.2, 0.3, 0.4]" in text


def test_eine_von_hand_verdrehte_box_meldet_datei_und_zeile(tmp_path: Path):
    """Prinzip 4 an dieser Datei: ein Tippfehler bricht mit Pfad und Zeile ab.

    Der Gegensatz zur Modellantwort ist Absicht — dort faellt die eine Box
    heraus, hier soll der Mensch erfahren, dass seine Korrektur nicht gilt.
    """
    pfad = tmp_path / "vision.yaml"
    pfad.write_text(
        "version: 1\nmodel: m\nprompt: 1\nimages:\n"
        "  cache/a.jpg:\n    hash: h\n    scene: group\n"
        "    protect:\n      - [0.6, 0.6, 0.2, 0.2]\n", encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        VisionDoc.load(pfad)
    assert "verdreht" in str(exc.value) or "leer" in str(exc.value)


def test_eine_zu_grosse_schutzbox_wird_beim_laden_abgelehnt(tmp_path: Path):
    pfad = tmp_path / "vision.yaml"
    pfad.write_text(
        "version: 1\nmodel: m\nprompt: 1\nimages:\n"
        "  cache/a.jpg:\n    hash: h\n    scene: group\n"
        "    protect:\n      - [0.0, 0.0, 0.99, 0.99]\n", encoding="utf-8")
    with pytest.raises(SchemaError, match="80"):
        VisionDoc.load(pfad)


def test_eine_unbekannte_version_wird_abgelehnt(tmp_path: Path):
    pfad = tmp_path / "vision.yaml"
    pfad.write_text("version: 7\nmodel: m\nprompt: 1\nimages: {}\n",
                    encoding="utf-8")
    with pytest.raises(SchemaError):
        VisionDoc.load(pfad)


def test_das_stage_feld_ist_vorgesehen():
    """14.2: ``vision.yaml`` hat zwei Faktenmengen mit verschiedenen
    Anforderungen. Die koordinatenfreie darf spaeter frueh und billig auf
    Thumbnails laufen — das Feld steht **jetzt** da, damit die Datei dafuer
    nicht umgebaut werden muss."""
    assert VisionEntry().stage == "geometry"
    assert VisionEntry(stage="labels").stage == "labels"


def test_das_schema_verlangt_alle_felder():
    """Strukturierte Ausgabe garantiert eine parsebare Antwort — aber nur,
    wenn jedes Feld auch verlangt wird."""
    eigenschaften = set(vision.ANTWORT_SCHEMA["properties"])
    assert set(vision.ANTWORT_SCHEMA["required"]) == eigenschaften
    assert vision.ANTWORT_SCHEMA["additionalProperties"] is False


def test_haiku_bekommt_kein_effort():
    """``effort`` ist auf Haiku 4.5 ein 400 und kein stiller Rueckfall — und
    das Briefing nennt Haiku ausdruecklich als Rauchtest-Modell."""
    opus = vision._anfrage_bauen("claude-opus-5", "x", "a.jpg")
    haiku = vision._anfrage_bauen("claude-haiku-4-5", "x", "a.jpg")
    assert opus["output_config"]["effort"] == "low"
    assert "effort" not in haiku["output_config"]


def test_der_gecachte_praefix_steht_vorn_und_ist_stabil():
    """Caching ist ein Praefix-Vergleich: jede Byte-Aenderung davor entwertet
    alles danach. Der Systemprompt darf deshalb nicht vom Bild abhaengen."""
    a = vision._anfrage_bauen("claude-opus-5", "AAAA", "a.jpg")
    b = vision._anfrage_bauen("claude-opus-5", "BBBB", "b.jpg")
    assert a["system"] == b["system"]
    assert a["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_bedrock_bekommt_das_praefix_am_modellnamen():
    assert vision.modellname("claude-opus-5", bedrock=True) == "anthropic.claude-opus-5"
    assert vision.modellname("claude-opus-5", bedrock=False) == "claude-opus-5"
    assert (vision.modellname("anthropic.claude-opus-5", bedrock=True)
            == "anthropic.claude-opus-5")


def test_fuer_bedrock_gilt_die_andere_zusagentabelle():
    """E11: wer ueber Bedrock geht, bekommt **nicht** die Zusagen von
    Anthropic zu sehen — sie gelten dort nicht."""
    assert vision.DATENSCHUTZ != vision.DATENSCHUTZ_BEDROCK
    assert any("AWS" in z for z in vision.DATENSCHUTZ_BEDROCK)
    assert any("zwei jahre" in z.lower() or "ZWEI JAHRE" in z
               for z in vision.DATENSCHUTZ)
