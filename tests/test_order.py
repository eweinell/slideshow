"""Manuelle Reihenfolge (``docs/briefing-manuelle-reihenfolge.md``, Stufen 1–2).

Geprueft wird, dass die Reihenfolge **wirkt**, dass kein Bild dabei still
verschwindet und dass das erzeugte Formular die Handarbeit ueberlebt. Alles ohne
ffmpeg: gearbeitet wird auf einem von Hand gestellten Manifest und einer von Hand
gestellten Regionenkarte, damit die Abfolge *bekannt* ist statt bloss plausibel.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from slideshow.build import build_edit_list, plan_from_edit
from slideshow.chapters import first_image_id
from slideshow.errors import SchemaError
from slideshow.models import (BeatMap, Chapter, Defaults, ImageInfo, Manifest,
                              MediaItem, OrderList, Region, StillSegment,
                              dump_edit_yaml)
from slideshow.order import (anchor_chapters, dump_order_yaml, group_media,
                             is_chronological, item_lines, load_order,
                             resolve_order, update_order_text)

FPS = 60.0
T0 = 1_753_000_000                   # 20. Juli 2025, lokale Zeit

#: Echte Koordinaten — der Abstand soll die JUMP_KM-Schwelle wirklich reissen.
KOPENHAGEN = (55.6761, 12.5683)
STOCKHOLM = (59.3293, 18.0686)       # gut 500 km


# --------------------------------------------------------------------------
# Material von Hand
# --------------------------------------------------------------------------

def _manifest(n: int = 8, *, stunden: float = 6.0) -> Manifest:
    """``n`` Bilder im Abstand von ``stunden`` — Tagesgrenzen sind damit bekannt."""
    media = [MediaItem(id=f"img_{i:03d}", path=f"src/img_{i:03d}.jpg", kind="image",
                       cache_path=f"cache/img_{i:03d}.jpg", time_source="exif",
                       capture_time=T0 + int(i * stunden * 3600),
                       # Wie nach einem `probe`: jedes dritte Bild hochkant.
                       # Der Generator schreibt das als Kontext neben die Zeile.
                       image=ImageInfo(width=6000, height=4000, portrait=i % 3 == 0))
             for i in range(n)]
    m = Manifest(media=media, fps_suggestion=FPS)
    m.audio.file = "cache/mix.flac"
    m.audio.duration = 90.0
    return m


def _bauen(manifest: Manifest, *, order=None, order_notes=None, chapters=None,
           dauer: float = 90.0):
    regions = [Region(type="beat", start=0.0, end=dauer, bpm=120.0, offset=0.0, conf=0.9)]
    beatmap = BeatMap(audio={"file": manifest.audio.file, "duration": dauer},
                      regions=regions)
    return build_edit_list(None, manifest, beatmap, defaults=Defaults(), fps=FPS,
                           size=(1280, 720), order=order, order_notes=order_notes,
                           chapters=chapters)


def _folge(edit) -> list[str]:
    """Die Kennungen der Standbilder in der Reihenfolge der Edit-List."""
    return [s.src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for s in edit.segments if isinstance(s, StillSegment)]


def _olist(text: str) -> OrderList:
    return OrderList.model_validate(yaml.safe_load(text))


def _datei(tmp_path, text: str):
    p = tmp_path / "order.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# O1 — die Reihenfolge wirkt
# --------------------------------------------------------------------------

def test_die_reihenfolge_aus_der_datei_gewinnt_gegen_die_uhr():
    manifest = _manifest()
    umgekehrt = [m.id for m in reversed(manifest.media)]
    edit, _plan, _cov = _bauen(manifest, order=umgekehrt)
    assert _folge(edit) == umgekehrt


def test_umsortieren_verschiebt_keine_musik():
    """Die Abfolge aendert die Reihenfolge der Bilder, nicht die Laufzeit.

    Das ist die eigentliche Zusage: wer umsortiert, bekommt denselben Film in
    anderer Reihenfolge — nicht einen anderen Film.
    """
    manifest = _manifest()
    chrono, _p1, _c1 = _bauen(manifest)
    gedreht, _p2, _c2 = _bauen(manifest, order=[m.id for m in reversed(manifest.media)])
    assert chrono.audio["duration"] == gedreht.audio["duration"]
    assert len(chrono.segments) == len(gedreht.segments)


def test_eine_teilmenge_ergibt_einen_kuerzeren_film():
    """`rest: drop` ist Auswahl, nicht nur Notausgang — der Film wird kuerzer."""
    manifest = _manifest()
    edit, _plan, cov = _bauen(manifest, order=[f"img_{i:03d}" for i in range(4)])
    assert _folge(edit) == ["img_000", "img_001", "img_002", "img_003"]
    assert cov.stills == 4


# --------------------------------------------------------------------------
# O8 — ohne order.yaml aendert sich nichts
# --------------------------------------------------------------------------

def test_ohne_reihenfolge_bleibt_es_chronologisch():
    manifest = _manifest()
    edit, _plan, _cov = _bauen(manifest)
    assert _folge(edit) == [m.id for m in manifest.media]


def test_die_chronologische_folge_ausdruecklich_anzugeben_aendert_nichts():
    """Die Probe darauf, dass der neue Weg keinen zweiten Renderpfad aufmacht."""
    manifest = _manifest()
    ohne, _p1, _c1 = _bauen(manifest)
    mit, _p2, _c2 = _bauen(manifest, order=[m.id for m in manifest.media])
    assert dump_edit_yaml(ohne) == dump_edit_yaml(mit)


# --------------------------------------------------------------------------
# O2 — kein Bild verschwindet still
# --------------------------------------------------------------------------

def test_fehlendes_material_bricht_ab_und_nennt_die_ids(tmp_path):
    manifest = _manifest()
    p = _datei(tmp_path, "order: [img_000, img_001]\n")
    olist, zeilen = load_order(p)
    with pytest.raises(SchemaError) as exc:
        resolve_order(manifest, olist, quelle=str(p), zeilen=zeilen)
    text = str(exc.value)
    assert "6 Medien stehen nicht" in text
    assert "- img_002" in text          # kopierbar, nicht nur gezaehlt
    assert "rest: append" in text and "rest: drop" in text


def test_rest_append_haengt_chronologisch_hinten_an(tmp_path):
    manifest = _manifest()
    p = _datei(tmp_path, "rest: append\norder: [img_003, img_001]\n")
    olist, zeilen = load_order(p)
    ids, meldungen = resolve_order(manifest, olist, quelle=str(p), zeilen=zeilen)
    assert ids[:2] == ["img_003", "img_001"]
    assert ids[2:] == ["img_000", "img_002", "img_004", "img_005", "img_006", "img_007"]
    assert len(meldungen) == 1 and "laufen hinten chronologisch mit" in meldungen[0]


def test_rest_drop_laesst_material_weg_und_sagt_es(tmp_path):
    manifest = _manifest()
    p = _datei(tmp_path, "rest: drop\norder: [img_003, img_001]\n")
    olist, zeilen = load_order(p)
    ids, meldungen = resolve_order(manifest, olist, quelle=str(p), zeilen=zeilen)
    assert ids == ["img_003", "img_001"]
    assert len(meldungen) == 1 and "bleiben weg" in meldungen[0]


def test_die_meldung_ueber_weggelassenes_material_steht_im_bericht():
    """`rest: drop` darf nicht nur in der Datei stehen — sonst faellt ein
    vergessenes Flag erst beim Ansehen des fertigen Films auf."""
    manifest = _manifest()
    _edit, plan, _cov = _bauen(manifest, order=["img_000", "img_001"],
                               order_notes=["6 Medien … bleiben weg (`rest: drop`)"])
    assert any("bleiben weg" in w for w in plan.warnings)


def test_eine_leere_reihenfolge_ist_ein_fehler(tmp_path):
    """Mit `rest: error` faengt schon das fehlende Material den Fall ab; mit
    `rest: drop` bliebe sonst ein Film aus null Bildern uebrig."""
    p = _datei(tmp_path, "rest: drop\norder: []\n")
    olist, zeilen = load_order(p)
    with pytest.raises(SchemaError, match="kein einziges Medium"):
        resolve_order(_manifest(), olist, quelle=str(p), zeilen=zeilen)


# --------------------------------------------------------------------------
# O3 — Fehler melden sich mit Zeile
# --------------------------------------------------------------------------

def test_unbekannte_id_bricht_ab_mit_zeile(tmp_path):
    p = _datei(tmp_path, "groups:\n"
                         "  - name: ankunft\n"
                         "    items:\n"
                         "      - img_000\n"
                         "      - img_verschollen\n")
    olist, zeilen = load_order(p)
    with pytest.raises(SchemaError) as exc:
        resolve_order(_manifest(), olist, quelle=str(p), zeilen=zeilen)
    assert exc.value.line == 5
    assert "img_verschollen" in str(exc.value)
    assert "ankunft" in str(exc.value)


def test_doppelte_id_bricht_ab_und_nennt_beide_zeilen(tmp_path):
    """Entscheidung 4: ein doppeltes Bild macht `before:` in chapters.yaml
    stillschweigend mehrdeutig — es traefe immer das erste Vorkommen."""
    p = _datei(tmp_path, "groups:\n"
                         "  - name: ankunft\n"
                         "    items: [img_000, img_001]\n"
                         "  - name: abende\n"
                         "    items:\n"
                         "      - img_001\n")
    olist, zeilen = load_order(p)
    with pytest.raises(SchemaError) as exc:
        resolve_order(_manifest(), olist, quelle=str(p), zeilen=zeilen)
    text = str(exc.value)
    assert "Zeilen 3, 6" in text
    assert "'ankunft'" in text and "'abende'" in text


def test_build_ueberspringt_unbekannte_ids_nicht_mehr():
    """Der Ausgangszustand liess sie wortlos fallen — genau der stille
    Ignorierfall, den Prinzip 4 ausschliesst."""
    with pytest.raises(SchemaError, match="img_gibtsnicht"):
        _bauen(_manifest(), order=["img_000", "img_gibtsnicht"])


def test_kommentare_liefern_keine_falschen_zeilen():
    """Ein erlaeuterndes `# statt img_042` darf nicht als Fundstelle gelten."""
    zeilen = item_lines("groups:\n  - items:\n      - img_001   # statt img_042\n")
    assert zeilen["img_001"] == [3]
    assert "img_042" not in zeilen


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

def test_beide_formen_bedeuten_dasselbe():
    flach = OrderList(order=["img_000", "img_001"])
    gruppiert = OrderList(groups=[{"name": "a", "items": ["img_000", "img_001"]}])
    assert [g.items for g in flach.blocks] == [g.items for g in gruppiert.blocks]


def test_beide_formen_zugleich_sind_ein_fehler(tmp_path):
    p = _datei(tmp_path, "order: [img_000]\ngroups: []\n")
    with pytest.raises(SchemaError, match="genau eines von"):
        OrderList.load(p)


def test_keine_form_anzugeben_ist_ein_fehler(tmp_path):
    p = _datei(tmp_path, "rest: drop\n")
    with pytest.raises(SchemaError, match="genau eines von"):
        OrderList.load(p)


def test_unbekanntes_feld_bricht_mit_zeile_ab(tmp_path):
    p = _datei(tmp_path, "order: [img_000]\nreset: drop\n")
    with pytest.raises(SchemaError) as exc:
        OrderList.load(p)
    assert exc.value.path == "reset"


def test_unbekannte_version_wird_abgelehnt_statt_geraten(tmp_path):
    p = _datei(tmp_path, "version: 2\norder: [img_000]\n")
    with pytest.raises(SchemaError, match="Version 2"):
        OrderList.load(p)


def test_ein_vertippter_rest_wert_faellt_auf(tmp_path):
    p = _datei(tmp_path, "rest: apend\norder: [img_000]\n")
    with pytest.raises(SchemaError):
        OrderList.load(p)


# --------------------------------------------------------------------------
# O5 — Rundlauf
# --------------------------------------------------------------------------

def test_die_edit_list_laedt_sich_zurueck_wie_geschrieben():
    manifest = _manifest()
    umgekehrt = [m.id for m in reversed(manifest.media)]
    edit, plan, _cov = _bauen(manifest, order=umgekehrt)
    plan2 = plan_from_edit(edit, manifest)
    assert [s.intent.src for s in plan2.slots] == [s.intent.src for s in plan.slots]
    assert [s.start_f for s in plan2.slots] == [s.start_f for s in plan.slots]


# --------------------------------------------------------------------------
# O6 — Kapitel ueberleben das Umsortieren
# --------------------------------------------------------------------------

def test_ein_kapitel_haengt_an_der_id_nicht_an_der_uhrzeit():
    """`before: img_002` sitzt an der *neuen* Position, nicht an der
    chronologischen."""
    manifest = _manifest()
    umgekehrt = [m.id for m in reversed(manifest.media)]
    edit, _plan, _cov = _bauen(manifest, order=umgekehrt,
                               chapters=[Chapter(before="img_002", title="Am Wasser",
                                                 subtitle=None)])
    folge = _folge(edit)
    titel = [i for i, s in enumerate(edit.segments) if s.type == "title"]
    davor = [i for i, s in enumerate(edit.segments)
             if isinstance(s, StillSegment) and s.src.endswith("img_002.jpg")]
    assert len(titel) == 1
    assert titel[0] < davor[0]
    assert folge.index("img_002") == len(folge) - 3


# --------------------------------------------------------------------------
# O7 — die Chronologie-Warnung greift und schweigt richtig
# --------------------------------------------------------------------------

def _warnungen(manifest, order, subtitle):
    _edit, plan, _cov = _bauen(
        manifest, order=order,
        chapters=[Chapter(before=order[0] if order else "img_000",
                          title="Am Wasser", subtitle=subtitle)])
    return [w for w in plan.warnings if "nicht chronologisch" in w]


def test_subtitle_auto_ueber_mehreren_tagen_wird_gemeldet():
    manifest = _manifest(stunden=12.0)          # jedes zweite Bild ein neuer Tag
    umgekehrt = [m.id for m in reversed(manifest.media)]
    hinweise = _warnungen(manifest, umgekehrt, "auto")
    assert len(hinweise) == 1
    assert "Am Wasser" in hinweise[0] and "`subtitle: null`" in hinweise[0]


def test_ohne_subtitle_auto_gibt_es_nichts_zu_melden():
    manifest = _manifest(stunden=12.0)
    umgekehrt = [m.id for m in reversed(manifest.media)]
    assert _warnungen(manifest, umgekehrt, "Tag der Kueste") == []


def test_eine_chronologische_folge_bleibt_still():
    """Die Pruefung haengt an der gemessenen Monotonie, nicht am blossen
    Vorhandensein einer Reihenfolge — sonst warnte sie auch den an, der nur
    zwei Bilder getauscht hat."""
    manifest = _manifest(stunden=12.0)
    chrono = [m.id for m in manifest.media]
    assert _warnungen(manifest, chrono, "auto") == []
    assert _warnungen(manifest, None, "auto") == []


def test_ein_abschnitt_aus_einem_einzigen_tag_bleibt_still():
    """Umsortiert, aber der Block umfasst nur einen Tag — dann stimmt das
    Datum, das `subtitle: auto` einsetzt."""
    manifest = _manifest(n=6, stunden=1.0)      # alle sechs am selben Tag
    getauscht = ["img_001", "img_000", "img_002", "img_003", "img_004", "img_005"]
    assert _warnungen(manifest, getauscht, "auto") == []


# --------------------------------------------------------------------------
# Der Auftakt-Kommentar in chapters.yaml
# --------------------------------------------------------------------------

def test_der_auftakt_nennt_das_erste_bild_der_gewaehlten_folge():
    manifest = _manifest()
    umgekehrt = [m.id for m in reversed(manifest.media)]
    assert first_image_id(manifest) == "img_000"
    assert first_image_id(manifest, umgekehrt) == "img_007"


def test_slideshow_chapters_scheitert_nicht_an_einem_zwischenstand(tmp_path, capsys):
    """`chapters` braucht die Reihenfolge nur fuer einen Kommentar.

    Wer gerade sortiert, hat regelmaessig eine unfertige ``order.yaml`` liegen,
    die ``build`` zu Recht ablehnt. Daran duerfen die Kapitelvorschlaege nicht
    scheitern — sie sagen es und rechnen chronologisch weiter.
    """
    from slideshow.cli import main

    _manifest(n=3).save(tmp_path / "manifest.json")
    (tmp_path / "order.yaml").write_text("order: [img_002, img_verschollen]\n",
                                         encoding="utf-8")
    assert main(["--project", str(tmp_path), "chapters"]) == 0
    ausgabe = capsys.readouterr().out
    assert "bleibt aussen vor" in ausgabe
    assert (tmp_path / "chapters.yaml").exists()


# --------------------------------------------------------------------------
# Stufe 2 — der Generator
# --------------------------------------------------------------------------

def _mit_orten(manifest: Manifest, ab: int, ort) -> Manifest:
    for m in manifest.media[:ab]:
        m.gps = KOPENHAGEN
    for m in manifest.media[ab:]:
        m.gps = ort
    return manifest


def test_nach_tagen_gruppiert_der_generator_am_kalendertag():
    """Nicht an der Zeitluecke: 23:50 und 00:10 sind 20 Minuten auseinander und
    trotzdem zwei Tage. Nur so heisst `tag-2` auch, was `subtitle: auto` als
    "Tag 2" ausschreibt."""
    bloecke = group_media(_manifest(n=6, stunden=12.0), by="day")
    assert [b.name for b in bloecke] == ["tag-1", "tag-2", "tag-3"]
    assert [len(b.items) for b in bloecke] == [2, 2, 2]


def test_nach_orten_gruppiert_der_generator_am_gps_sprung():
    bloecke = group_media(_mit_orten(_manifest(n=6), 4, STOCKHOLM), by="place")
    assert [b.name for b in bloecke] == ["ort-1", "ort-2"]
    assert [len(b.items) for b in bloecke] == [4, 2]
    assert "km weiter" in bloecke[1].grund


def test_ohne_gruppierung_bleibt_alles_ein_block():
    bloecke = group_media(_manifest(n=6, stunden=12.0), by="none")
    assert [b.name for b in bloecke] == ["alle"]
    assert len(bloecke[0].items) == 6


def test_material_ohne_zeitstempel_landet_in_einer_eigenen_gruppe():
    manifest = _manifest(n=4)
    manifest.media[2].capture_time = None
    manifest.media[2].time_source = "none"
    bloecke = group_media(manifest, by="day")
    assert bloecke[-1].name == "ohne-datum"
    assert [m.id for m in bloecke[-1].items] == ["img_002"]


def test_die_erzeugte_datei_laedt_sich_zurueck(tmp_path):
    """Das Formular muss gueltiges YAML sein — sonst ist es ein Aufsatz."""
    manifest = _manifest(n=6, stunden=12.0)
    p = _datei(tmp_path, dump_order_yaml(group_media(manifest, by="day"), manifest))
    olist, zeilen = load_order(p)
    ids, meldungen = resolve_order(manifest, olist, quelle=str(p), zeilen=zeilen)
    assert ids == [m.id for m in manifest.media]
    assert meldungen == []              # vollstaendig, also nichts zu melden
    assert zeilen["img_000"]            # die Kontextkommentare stoeren die Zeilen nicht


def test_jede_zeile_traegt_den_kontext_zum_sortieren():
    manifest = _manifest(n=2)
    text = dump_order_yaml(group_media(manifest, by="day"), manifest)
    zeile = next(z for z in text.splitlines() if "img_000" in z)
    assert "Tag 1" in zeile and ("quer" in zeile or "hoch" in zeile)


def test_der_kopf_sagt_dass_gruppen_keine_titel_sind():
    manifest = _manifest(n=2)
    text = dump_order_yaml(group_media(manifest, by="day"), manifest)
    assert "NICHT im Film" in text and "chapters.yaml" in text


# --------------------------------------------------------------------------
# Stufe 2 — nachpflegen
# --------------------------------------------------------------------------

def _bestand() -> str:
    return ("version: 1\nrest: drop\n\ngroups:\n"
            "  - name: am-wasser\n    items:\n"
            "      - img_003   # Tag 1\n"
            "      - img_001   # Tag 1\n"
            "    # - img_002   # zu dunkel, bleibt draussen\n")


def test_update_behaelt_die_sortierung_und_haengt_neues_an():
    manifest = _manifest(n=6)
    text, meldungen = update_order_text(_bestand(), _olist(_bestand()), manifest)
    assert text.index("img_003") < text.index("img_001")       # Sortierung steht
    assert "- name: neu" in text
    assert "img_000" in text.split("- name: neu")[1]
    assert any("neue Medien angehaengt" in m for m in meldungen)


def test_update_wirft_handgeschriebene_kommentare_nicht_weg():
    """Bei `rest: drop` ist eine auskommentierte Zeile die Auswahl, und ihr
    Kommentar sagt, warum das Bild draussen bleibt."""
    text, _meldungen = update_order_text(_bestand(), _olist(_bestand()), _manifest(n=6))
    assert "# - img_002   # zu dunkel, bleibt draussen" in text


def test_update_bietet_abgewaehltes_material_nicht_erneut_an():
    """Sonst stuende jedes verworfene Foto nach dem dritten Lauf dreimal drin."""
    text, meldungen = update_order_text(_bestand(), _olist(_bestand()), _manifest(n=6))
    assert "img_002" not in text.split("- name: neu")[1]
    assert any("bewusst abgewaehlt" in m for m in meldungen)


def test_update_ist_idempotent():
    manifest = _manifest(n=6)
    einmal, _m1 = update_order_text(_bestand(), _olist(_bestand()), manifest)
    zweimal, meldungen = update_order_text(einmal, _olist(einmal), manifest)
    assert zweimal == einmal
    assert any("nichts nachzupflegen" in m for m in meldungen)


def test_verschwundenes_material_wird_auskommentiert_statt_geloescht():
    """Die Zeile steht an der Stelle, an die das Bild einsortiert war — wer eine
    Datei umbenannt hat, findet sie so wieder."""
    manifest = _manifest(n=6)
    manifest.media = [m for m in manifest.media if m.id != "img_001"]
    text, meldungen = update_order_text(_bestand(), _olist(_bestand()), manifest)
    assert "# - img_001   # Tag 1   # nicht mehr im Manifest" in text
    assert any("nicht mehr im Manifest" in m for m in meldungen)


def test_update_kommt_auch_mit_der_flachen_flow_form_zurecht():
    """Eine Blockzeile neben `order: [...]` waere kein gueltiges YAML mehr."""
    bestand = "version: 1\norder: [img_003, img_001]\n"
    text, _meldungen = update_order_text(bestand, _olist(bestand), _manifest(n=4))
    olist = _olist(text)
    assert olist.blocks[0].items == ["img_003", "img_001", "img_000", "img_002"]


# --------------------------------------------------------------------------
# Stufe 2 — `group:` als Kapitelanker
# --------------------------------------------------------------------------

def test_group_zeigt_auf_das_erste_medium_der_gruppe():
    olist = _olist("groups:\n  - {name: am-wasser, items: [img_004, img_001]}\n"
                   "  - {name: abende, items: [img_005, img_000]}\n")
    kapitel = [Chapter(group="abende", title="Abende", subtitle=None)]
    aufgeloest = anchor_chapters(kapitel, olist, ["img_004", "img_001",
                                                  "img_005", "img_000"])
    assert aufgeloest[0].before == "img_005"
    assert aufgeloest[0].group is None


def test_group_ueberlebt_das_umsortieren_innerhalb_der_gruppe():
    """Genau der Grund fuer den Anker: `before: img_005` braeche hier, `group:`
    nicht."""
    def anker(items):
        olist = _olist(f"groups:\n  - {{name: abende, items: [{', '.join(items)}]}}\n")
        return anchor_chapters([Chapter(group="abende", title="Abende", subtitle=None)],
                               olist, items)[0].before

    assert anker(["img_005", "img_000"]) == "img_005"
    assert anker(["img_000", "img_005"]) == "img_000"


def test_group_springt_ueber_weggelassenes_material():
    """`rest: drop` kann das erste Bild einer Gruppe genommen haben — die Folie
    gehoert dann vor das erste noch vorhandene."""
    olist = _olist("groups:\n  - {name: abende, items: [img_005, img_000]}\n")
    aufgeloest = anchor_chapters([Chapter(group="abende", title="Abende", subtitle=None)],
                                 olist, ["img_000"])
    assert aufgeloest[0].before == "img_000"


def test_group_ohne_order_yaml_ist_ein_fehler():
    with pytest.raises(SchemaError, match="keine order.yaml"):
        anchor_chapters([Chapter(group="abende", title="Abende")], None, None)


def test_eine_unbekannte_gruppe_wird_benannt():
    olist = _olist("groups:\n  - {name: abende, items: [img_000]}\n")
    with pytest.raises(SchemaError) as exc:
        anchor_chapters([Chapter(group="am-wasser", title="X")], olist, ["img_000"])
    assert "'am-wasser'" in str(exc.value) and "abende" in str(exc.value)


def test_eine_leergeraeumte_gruppe_nennt_den_grund():
    olist = _olist("groups:\n  - {name: abende, items: [img_005]}\n")
    with pytest.raises(SchemaError, match="rest: drop"):
        anchor_chapters([Chapter(group="abende", title="Abende")], olist, ["img_000"])


def test_genau_ein_anker_gilt_jetzt_fuer_drei():
    Chapter(group="abende", title="Abende")
    with pytest.raises(ValidationError):
        Chapter(group="abende", before="img_000", title="Abende")
    with pytest.raises(ValidationError):
        Chapter(title="Abende")


def test_ein_unaufgeloester_anker_verpufft_nicht_still():
    """Der Riegel in `insert_titles`: dort sind die Gruppen vergessen."""
    with pytest.raises(SchemaError, match="unaufgeloesten"):
        _bauen(_manifest(), chapters=[Chapter(group="abende", title="Abende")])


# --------------------------------------------------------------------------
# Stufe 2 — der Vorbehalt in `slideshow chapters`
# --------------------------------------------------------------------------

def test_monotonie_wird_gemessen_nicht_vermutet():
    manifest = _manifest(n=5)
    assert is_chronological(manifest, [m.id for m in manifest.media])
    assert not is_chronological(manifest, [m.id for m in reversed(manifest.media)])
    # Zwei getauschte Nachbarn sind bereits nicht mehr chronologisch — die
    # Warnung darueber haengt deshalb zusaetzlich am Tagesumfang des Blocks.
    assert not is_chronological(manifest, ["img_001", "img_000", "img_002",
                                           "img_003", "img_004"])


def test_der_vorbehalt_steht_im_kopf_der_erzeugten_datei():
    from slideshow.chapters import ORDER_VORBEHALT, dump_chapters_yaml
    text = dump_chapters_yaml([], vorbehalt=ORDER_VORBEHALT)
    assert "# ACHTUNG: order.yaml sortiert nicht chronologisch." in text
    assert "`group:`" in text
    # Muss trotzdem ladbar bleiben — der Vorbehalt ist ein Kommentar.
    assert "chapters" in yaml.safe_load(text)
