"""Abnahme des Kontaktbogens (``docs/briefing-auswahl.md``, Abschnitt 5, A9).

Der Bogen ist die einzige Stelle des Auswahl-Briefings, die Bildpunkte
anfasst. Geprueft wird trotzdem fast alles ohne echte Bilder: die Gestalt der
Seite haengt am Manifest, nicht am Motiv. Nur die drei Thumbnail-Tests
brauchen Dateien — und die sagen genau das, worauf es ankommt: dass die
eingebettete Vorschau genommen wird und nicht der teure Weg.

Die harten Zusagen, die hier festgenagelt werden:

- **kein Netzzugriff** — keine externe Adresse, kein CDN, keine Bibliothek;
- **der Bogen schreibt nichts** (Entscheidung 7);
- **``loading="lazy"`` auf jeder Kachel**, sonst dekodiert der Browser 1240
  Bilder beim Oeffnen;
- **der Stand kommt aus ``order.yaml``**, auch nach Handarbeit.
"""

from __future__ import annotations

import datetime as _dt
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from slideshow.models import ImageInfo, Manifest, MediaItem, OrderList
from slideshow.order import load_order, resolve_order
from slideshow.proc import have
from slideshow.select import dump_selection_yaml, select_media
from slideshow.sheet import (build_sections, dump_sheet_html, parse_changes,
                             read_params, selection_from_order, sheet_media,
                             thumbnails)

requires_exiftool = pytest.mark.skipif(not have("exiftool"), reason="exiftool fehlt")
requires_ffmpeg = pytest.mark.skipif(not have("ffmpeg"), reason="ffmpeg fehlt")

#: Tags ohne schliessendes Gegenstueck.
_LEERE = {"meta", "img", "br", "hr", "input", "link", "source"}


# --------------------------------------------------------------------------
# Bestand
# --------------------------------------------------------------------------

def _bild(nr: int, ts: float, *, breite: int = 6232, hoehe: int = 4160,
          hoch: bool = False, sterne: int = 0) -> MediaItem:
    if hoch:
        breite, hoehe = hoehe, breite
    return MediaItem(
        id=f"img_{nr:04d}", path=f"src/img_{nr:04d}.jpg", kind="image",
        size_bytes=5_000_000 + nr, camera="ILCE-6700", capture_time=ts,
        time_source="exif", rating=sterne,
        image=ImageInfo(width=breite, height=hoehe, portrait=hoch))


@pytest.fixture
def bestand() -> Manifest:
    """Vier Tage, Serien im Sekundenabstand, ein zu kleines Bild je Tag."""
    start = _dt.datetime(2026, 7, 24, 9, 0, 0)
    media, nr = [], 0
    for tag in range(4):
        t = start + _dt.timedelta(days=tag)
        for traube in range(6 + 3 * tag):
            for k in range(1 + traube % 3):
                nr += 1
                klein = (traube == 2)
                media.append(_bild(
                    nr, (t + _dt.timedelta(seconds=k * 4)).timestamp(),
                    breite=1024 if klein else 6232,
                    hoehe=768 if klein else 4160,
                    hoch=(traube % 5 == 0)))
            t += _dt.timedelta(minutes=11)
    return Manifest(media=media)


@pytest.fixture
def bogen(bestand):
    """``select`` -> ``order.yaml``-Text -> rekonstruierte Auswahl -> HTML."""
    sel = select_media(bestand, count=20, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    olist = OrderList.model_validate(_yaml(text))
    rekonstruiert = selection_from_order(bestand, olist, text)
    thumbs = {m.id: Path("cache/thumbs") / f"{m.id}.jpg"
              for m in sheet_media(rekonstruiert, bestand)}
    html = dump_sheet_html(rekonstruiert, thumbs, bestand, base=Path("."))
    return {"sel": sel, "text": text, "rekonstruiert": rekonstruiert,
            "html": html, "manifest": bestand}


def _yaml(text: str) -> dict:
    import yaml
    return yaml.safe_load(text) or {}


# --------------------------------------------------------------------------
# A9 — die Datei oeffnet ohne Netz und ohne Server
# --------------------------------------------------------------------------

def test_der_bogen_kommt_ohne_netzzugriff_aus(bogen):
    html = bogen["html"]
    for muster in ("http:", "https:", "//", "cdn", "<script src", "<link "):
        assert muster not in html, f"externer Verweis: {muster!r}"


def test_der_bogen_bindet_keine_daten_uris_ein(bogen):
    """1240 base64-Thumbnails waeren ~27 MB in einer Datei, die kein Editor
    mehr oeffnet. Verwiesen wird relativ nach ``cache/thumbs/``."""
    assert "data:" not in bogen["html"]
    assert 'src="cache/thumbs/' in bogen["html"]


def test_jede_kachel_laedt_verzoegert(bogen):
    bilder = re.findall(r"<img [^>]*>", bogen["html"])
    assert bilder, "keine einzige Kachel"
    assert all('loading="lazy"' in b for b in bilder)


def test_der_bogen_ist_gueltiges_html(bogen):
    class _Waage(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stapel: list[str] = []
            self.fehler: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag not in _LEERE:
                self.stapel.append(tag)

        def handle_endtag(self, tag):
            if tag in _LEERE:
                return
            if not self.stapel or self.stapel[-1] != tag:
                self.fehler.append(f"</{tag}> passt nicht zu {self.stapel[-3:]}")
                return
            self.stapel.pop()

    w = _Waage()
    w.feed(bogen["html"])
    assert not w.fehler, w.fehler[:3]
    assert not w.stapel, f"offen geblieben: {w.stapel}"
    assert bogen["html"].startswith("<!DOCTYPE html>")


def test_der_bogen_bleibt_unter_zwei_megabyte(bestand):
    """A9 rechnet mit 1240 Medien. Die Grenze haelt nur, solange die Kacheln
    relativ verweisen — mit ``data:``-URIs waere sie zwanzigfach gerissen."""
    start = _dt.datetime(2026, 7, 24, 9, 0, 0)
    media, nr = [], 0
    t = start
    while nr < 1240:
        for k in range(3):
            nr += 1
            media.append(_bild(nr, (t + _dt.timedelta(seconds=k * 3)).timestamp()))
        t += _dt.timedelta(minutes=7)
    gross = Manifest(media=media)
    sel = select_media(gross, count=187, seed=1)
    text = dump_selection_yaml(sel, gross)
    rekonstruiert = selection_from_order(gross, OrderList.model_validate(_yaml(text)),
                                         text)
    thumbs = {m.id: Path("cache/thumbs") / f"{m.id}.jpg"
              for m in sheet_media(rekonstruiert, gross)}
    html = dump_sheet_html(rekonstruiert, thumbs, gross, base=Path("."))
    assert len(re.findall(r"<figure ", html)) >= 1000
    assert len(html.encode("utf-8")) < 2 * 1024 * 1024, len(html)


# --------------------------------------------------------------------------
# Entscheidung 7 — der Bogen schreibt nichts
# --------------------------------------------------------------------------

def test_der_bogen_schreibt_nichts(bogen):
    """Kein Netzaufruf, kein Formular, keine Speicherung — nur Markieren und
    Kopieren. ``order.yaml`` bleibt die einzige Wahrheit."""
    html = bogen["html"]
    for verboten in ("fetch(", "XMLHttpRequest", "localStorage", "<form",
                     "sessionStorage", "navigator.sendBeacon"):
        assert verboten not in html, verboten
    assert "clipboard" in html, "der Rueckweg ueber die Zwischenablage fehlt"


# --------------------------------------------------------------------------
# Inhalt
# --------------------------------------------------------------------------

def test_jede_gewaehlte_und_abgewaehlte_kennung_steht_auf_dem_bogen(bogen):
    """Der Bogen ist die vollstaendige Liste — die ``order.yaml`` fasst
    ausgelassene Trauben zu einem Vertreter zusammen, hier fehlt nichts."""
    html = bogen["html"]
    sel = bogen["rekonstruiert"]
    for mid in sel.ids:
        assert f'data-id="{mid}"' in html, f"gewaehlt, aber nicht gezeigt: {mid}"
    abgewaehlt = [m.id for b in sel.ausgelassen for m in b.items]
    abgewaehlt += [x.id for liste in sel.alternativen.values() for x in liste]
    for mid in abgewaehlt:
        assert f'data-id="{mid}"' in html, f"abgewaehlt, aber nicht gezeigt: {mid}"


def test_unter_jeder_kachel_steht_die_medien_id(bogen):
    """Die ID ist der Griff zum Tauschen in ``order.yaml`` — sie gehoert
    sichtbar hin, nicht in ein Tooltip.

    Geprueft wird der Text der Bildunterschrift, nicht ihr Markup: beim
    gewaehlten Bild traegt die Kennung zusaetzlich die Hervorhebung, und daran
    soll dieser Test nicht haengen.
    """
    unterschriften = re.findall(r"<figcaption>(.*?)</figcaption>", bogen["html"])
    texte = [re.sub(r"<[^>]+>", "", u) for u in unterschriften]
    assert texte, "keine einzige Bildunterschrift"
    for mid in bogen["rekonstruiert"].ids[:5]:
        assert any(t.startswith(mid) for t in texte), mid


def test_das_gewaehlte_bild_steht_gross_und_seine_geschwister_klein(bogen):
    sel = bogen["rekonstruiert"]
    gross = set(re.findall(r'<figure class="gross" data-id="([^"]+)"', bogen["html"]))
    assert gross == set(sel.ids)
    geschwister = {x.id for liste in sel.alternativen.values() for x in liste}
    klein = set(re.findall(r'<figure data-id="([^"]+)"', bogen["html"]))
    assert geschwister <= klein


def test_ausgelassene_trauben_stehen_ohne_grosses_bild(bogen):
    treffer = re.findall(r'<div class="traube ausgelassen">(.*?)</div>',
                         bogen["html"])
    assert treffer, "keine ausgelassene Traube auf dem Bogen"
    assert all('class="gross"' not in t for t in treffer)


def test_harte_ausschluesse_tragen_ihren_grund_als_badge(bogen):
    sel = bogen["rekonstruiert"]
    assert sel.gruende, "der Bestand sollte zu kleine Bilder enthalten"
    grund = next(iter(sel.gruende.values()))
    assert f'<span class="badge">{grund}</span>' in bogen["html"]


def test_die_kopfzeile_nennt_zielzahl_seed_und_parameter(bogen):
    html = bogen["html"]
    assert "Zielzahl 20" in html
    assert "Seed 4711" in html
    assert "Traubenabstand 90&nbsp;s" in html
    assert "Mindestlangkante 2160&nbsp;px" in html
    assert 'class="balken"' in html, "die Quote je Tag fehlt"


def test_nur_die_auswahl_laesst_die_geschwister_weg(bogen):
    sel = bogen["rekonstruiert"]
    thumbs = {m.id: Path("cache/thumbs") / f"{m.id}.jpg"
              for m in sheet_media(sel, bogen["manifest"], nur_auswahl=True)}
    html = dump_sheet_html(sel, thumbs, bogen["manifest"], base=Path("."),
                           nur_auswahl=True)
    gezeigt = set(re.findall(r'data-id="([^"]+)"', html))
    assert gezeigt == set(sel.ids)


# --------------------------------------------------------------------------
# Der Stand kommt aus order.yaml — auch nach Handarbeit
# --------------------------------------------------------------------------

def test_read_params_liest_den_kopf_von_select(bogen):
    p = read_params(bogen["text"])
    assert p["seed"] == 4711
    assert p["ziel"] == 20
    assert p["gap"] == pytest.approx(90.0)
    assert p["min_long_edge"] == 2160


def test_ohne_kopf_gelten_die_vorgaben_und_der_bogen_sagt_es(bestand):
    olist = OrderList.model_validate(
        {"version": 1, "rest": "drop",
         "groups": [{"name": "alle", "items": [bestand.media[0].id]}]})
    sel = selection_from_order(bestand, olist, "# von Hand\n")
    assert sel.params["gap"] == 90.0
    assert any("nicht von `slideshow select`" in m for m in sel.meldungen)


def test_der_bogen_zeigt_den_von_hand_geaenderten_stand(bogen, bestand, tmp_path):
    """Der eigentliche Grund, warum ``sheet`` nicht neu wuerfeln darf.

    Getauscht wird wie in der Wirklichkeit: die Kommentarzeile eintragen, die
    gewaehlte auskommentieren. Danach muss der Bogen das Geschwister gross
    zeigen und das vorher gewaehlte Bild klein.
    """
    sel = bogen["rekonstruiert"]
    alt = next(mid for mid in sel.ids if sel.alternativen.get(mid))
    neu = sel.alternativen[alt][0].id

    pfad = tmp_path / "order.yaml"
    getauscht = bogen["text"].replace(f"      - {alt}",
                                      f"      - {neu}   # getauscht")
    pfad.write_text(getauscht, encoding="utf-8")

    olist, zeilen = load_order(pfad)
    ids, _ = resolve_order(bestand, olist, quelle=str(pfad), zeilen=zeilen)
    danach = selection_from_order(bestand, olist, pfad.read_text(encoding="utf-8"),
                                 ids=ids)

    assert neu in danach.ids and alt not in danach.ids
    assert alt in [x.id for x in danach.alternativen[neu]]

    thumbs = {m.id: Path("cache/thumbs") / f"{m.id}.jpg"
              for m in sheet_media(danach, bestand)}
    html = dump_sheet_html(danach, thumbs, bestand, base=Path("."))
    assert f'<figure class="gross" data-id="{neu}"' in html
    assert f'<figure data-id="{alt}"' in html


def test_zwei_bilder_aus_einer_traube_sind_handarbeit_und_werden_gemeldet(bogen,
                                                                          bestand):
    sel = bogen["rekonstruiert"]
    alt = next(mid for mid in sel.ids if sel.alternativen.get(mid))
    neu = sel.alternativen[alt][0].id
    olist = OrderList.model_validate(
        {"version": 1, "rest": "drop",
         "groups": [{"name": "alle", "items": [*sel.ids, neu]}]})
    danach = selection_from_order(bestand, olist, bogen["text"])
    assert any("mehr als ein Bild" in m for m in danach.meldungen)
    # Beide stehen gross da — der Bogen behauptet nicht, einer sei der Ersatz.
    assert neu in danach.ids and alt in danach.ids


def test_abschnitte_folgen_den_kalendertagen(bogen):
    abschnitte = build_sections(bogen["rekonstruiert"], bogen["manifest"])
    assert len(abschnitte) == 4, [a.titel for a in abschnitte]
    assert abschnitte[0].titel.startswith("Tag 1")
    assert "gewaehlt" in abschnitte[0].info


# --------------------------------------------------------------------------
# Hervorhebung des gewaehlten Bildes
# --------------------------------------------------------------------------

def test_das_gewaehlte_bild_hat_eine_eigene_farbe(bogen):
    """Groesse allein traegt beim Scrollen nicht, und sobald markiert wird,
    konkurriert sie mit Gruen (herein) und Rot (hinaus). Drei Zustaende
    brauchen drei Sprachen."""
    html = bogen["html"]
    assert "--drin:" in html, "eigene Farbe fuer den Ausgangszustand"
    assert "figure.gross img" in html and "var(--drin)" in html
    # Die Markierungen muessen die Grundfarbe schlagen, sonst sieht man den
    # Tausch nicht: ihre Regeln stehen deshalb *nach* der Grundregel.
    assert html.index("figure.gross img") < html.index("figure.rein img")
    assert html.index("figure.rein img") < html.index("figure.raus img")


def test_die_kennung_des_gewaehlten_bildes_ist_hervorgehoben(bogen):
    """Die Kachel ist beim Scrollen oft halb aus dem Bild, die Zeile darunter
    nicht."""
    gewaehlt = bogen["rekonstruiert"].ids[0]
    assert f"<strong>{gewaehlt}</strong>" in bogen["html"]


# --------------------------------------------------------------------------
# Der Rueckweg: Aenderungsliste lesen und anwenden
# --------------------------------------------------------------------------

def test_die_aenderungsliste_wird_zerlegt():
    text = ("# Kontaktbogen: 2 herein, 1 hinaus.\n"
            "# In order.yaml eintragen, wo das getauschte Bild steht:\n"
            "      - img_0007\n"
            "      - img_0011\n"
            "# und diese Zeilen auskommentieren:\n"
            "      #  raus: img_0005\n")
    rein, raus, unklar = parse_changes(text)
    assert rein == ["img_0007", "img_0011"]
    assert raus == ["img_0005"]
    assert unklar == []


def test_ein_bom_am_anfang_stoert_nicht():
    """PowerShell setzt eines vor jede Pipe an ein fremdes Programm, und ein
    Browser-Download traegt es je nach Editor auch. Ohne diese Duldung scheitert
    ausgerechnet die *erste* Aenderung — mit einer Meldung, die auf eine Zeile
    zeigt, die voellig richtig aussieht."""
    rein, raus, unklar = parse_changes("\ufeff      - img_0007\n")
    assert rein == ["img_0007"]
    assert unklar == []


def test_zeilenenden_aus_windows_stoeren_nicht():
    rein, raus, unklar = parse_changes(
        "# Kopf\r\n      - img_0007\r\n      #  raus: img_0005\r\n")
    assert (rein, raus, unklar) == (["img_0007"], ["img_0005"], [])


def test_unverstandene_zeilen_verschwinden_nicht_stillschweigend():
    """Bei 160 Aenderungen faellt eine verschluckte Zeile nicht auf — und
    hinterher fehlen drei Bilder im Film."""
    rein, raus, unklar = parse_changes("      - img_0007\nirgendwas kaputtes\n")
    assert rein == ["img_0007"]
    assert [z for _nr, z in unklar] == ["irgendwas kaputtes"]


def test_der_rundlauf_bogen_bis_datei_schliesst_sich(bestand):
    """Markieren, Liste erzeugen, einspielen, Bogen neu — der neue Stand muss
    genau die getauschten Bilder zeigen."""
    from slideshow.order import apply_changes

    sel = select_media(bestand, count=20, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    vorher = list(sel.ids)

    # Ein gewaehltes Bild hinaus, ein Geschwister derselben Traube herein —
    # genau der Handgriff, den ein Klick im Bogen ausloest.
    raus_id = next(mid for mid in vorher if sel.alternativen.get(mid))
    rein_id = sel.alternativen[raus_id][0].id
    liste = (f"# Kontaktbogen: 1 herein, 1 hinaus.\n"
             f"      - {rein_id}\n"
             f"      #  raus: {raus_id}\n")

    rein, raus, _u = parse_changes(liste)
    neu, meldungen = apply_changes(text, bestand, rein, raus)

    olist = OrderList.model_validate(_yaml(neu))
    danach = [mid for g in olist.blocks for mid in g.items]
    assert rein_id in danach, meldungen
    assert raus_id not in danach
    assert len(danach) == len(vorher), "ein Tausch aendert die Anzahl nicht"

    # Und der Bogen zeigt den neuen Stand, ohne neu zu wuerfeln.
    frisch = selection_from_order(bestand, olist, neu)
    assert rein_id in frisch.ids and raus_id not in frisch.ids


def test_hereingenommenes_steht_an_der_chronologisch_richtigen_stelle(bestand):
    from slideshow.order import apply_changes

    sel = select_media(bestand, count=20, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    by_id = {m.id: m for m in bestand.media}
    kandidat = next(x.id for mid in sel.ids
                    for x in sel.alternativen.get(mid, []))

    neu, _m = apply_changes(text, bestand, [kandidat], [])
    olist = OrderList.model_validate(_yaml(neu))
    danach = [mid for g in olist.blocks for mid in g.items]

    zeiten = [by_id[mid].capture_time for mid in danach]
    assert zeiten == sorted(zeiten), "die Folge bleibt chronologisch"


def test_ein_bereits_gelistetes_bild_wird_nicht_doppelt_eingetragen(bestand):
    """Idempotent: dieselbe Liste zweimal eingespielt aendert beim zweiten Mal
    nichts. Bei 160 Aenderungen ist die halb angewandte Liste der schlechtere
    Ausgang."""
    from slideshow.order import apply_changes

    sel = select_media(bestand, count=20, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    drin = sel.ids[0]

    neu, meldungen = apply_changes(text, bestand, [drin], [])
    assert neu.count(f"- {drin}\n") + neu.count(f"- {drin} ") <= 2
    olist = OrderList.model_validate(_yaml(neu))
    danach = [mid for g in olist.blocks for mid in g.items]
    assert danach.count(drin) == 1
    assert any("bereits" in m for m in meldungen)


def test_widerspruechliche_listen_brechen_ab(bestand):
    from slideshow.errors import SchemaError
    from slideshow.order import apply_changes

    sel = select_media(bestand, count=20, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    with pytest.raises(SchemaError, match="zugleich herein und hinaus"):
        apply_changes(text, bestand, [sel.ids[0]], [sel.ids[0]])


def test_unbekannte_kennungen_brechen_ab(bestand):
    from slideshow.errors import SchemaError
    from slideshow.order import apply_changes

    sel = select_media(bestand, count=20, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    with pytest.raises(SchemaError, match="nicht im Manifest"):
        apply_changes(text, bestand, ["img_9999"], [])


def test_herausgenommenes_bleibt_als_kommentar_stehen(bestand):
    """Auskommentiert, nicht geloescht — die Zeile steht an der Stelle, an die
    das Bild einsortiert war, und `order --update` bietet es nie wieder als neu
    an."""
    from slideshow.order import apply_changes, mentioned_ids

    sel = select_media(bestand, count=20, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    weg = sel.ids[0]

    neu, _m = apply_changes(text, bestand, [], [weg])
    assert weg in mentioned_ids(neu), "als Kommentar weiterhin erwaehnt"
    olist = OrderList.model_validate(_yaml(neu))
    assert weg not in [mid for g in olist.blocks for mid in g.items]


def test_der_bogen_bietet_das_speichern_der_aenderungen_an(bogen):
    """Ueber die Zwischenablage 160 Zeilen einzusortieren ist ein Nachmittag."""
    html = bogen["html"]
    assert "herunterladen()" in html
    assert "auswahl.txt" in html
    assert "order --apply" in html
    # Ein Blob, keine Anfrage nach draussen.
    assert "createObjectURL" in html


# --------------------------------------------------------------------------
# Ansicht "Reihenfolge" — ansehen und umsortieren
# --------------------------------------------------------------------------

def _folge_bogen(manifest: Manifest, text: str, **kw) -> str:
    """Bogen mit beiden Ansichten, so wie ``cmd_sheet`` ihn baut."""
    from slideshow.order import order_fingerprint
    from slideshow.sheet import folge_bloecke

    olist = OrderList.model_validate(_yaml(text))
    sel = selection_from_order(manifest, olist, text)
    thumbs = {m.id: Path("cache/thumbs") / f"{m.id}.jpg"
              for m in sheet_media(sel, manifest)}
    return dump_sheet_html(sel, thumbs, manifest, base=Path("."),
                           folge=folge_bloecke(olist, manifest),
                           stand=order_fingerprint(olist), **kw)


def _folge_ids(html: str) -> list[str]:
    teil = html.split('<main id="folge"', 1)[1].split("</main>", 1)[0]
    return re.findall(r'<figure class="folge[^"]*"[^>]*data-id="([^"]+)"', teil)


def _gelistet(text: str) -> list[tuple[str, list[str]]]:
    olist = OrderList.model_validate(_yaml(text))
    return [(g.name, list(g.items)) for g in olist.blocks]


def _ausgabe(bloecke: list[tuple[str, list[str]]], *, stand: str | None,
             raus: set[str] = frozenset()) -> str:
    """Was ``folgezeilen()`` im Browser erzeugt — hier in Python nachgebaut."""
    zeilen = ["# Kontaktbogen: neue Reihenfolge — 1 verschoben, 0 herein, 0 hinaus.",
              "# Einspielen: slideshow order --apply auswahl.txt",
              "# reihenfolge"]
    if stand is not None:
        zeilen.append(f"# stand: {stand}")
    for name, ids in bloecke:
        zeilen.append(f"# block: {name}")
        zeilen += [f"      #  raus: {i}" if i in raus else f"      - {i}" for i in ids]
    return "\n".join(zeilen) + "\n"


@pytest.fixture
def auswahl(bestand):
    """Eine ``order.yaml`` aus ``select`` mit mehreren Tagesbloecken."""
    sel = select_media(bestand, count=20, seed=4711)
    return dump_selection_yaml(sel, bestand)


def _umsortieren(bestand, text, bloecke, *, raus=frozenset(), stand="passend"):
    from slideshow.order import apply_reorder, order_fingerprint
    from slideshow.sheet import parse_reorder

    olist = OrderList.model_validate(_yaml(text))
    if stand == "passend":
        stand = order_fingerprint(olist)
    folge = parse_reorder(_ausgabe(bloecke, stand=stand, raus=raus))
    assert folge is not None and not folge.unklar
    return apply_reorder(text, bestand, olist, folge.bloecke, stand=folge.stand)


def test_die_reihenfolge_zeigt_nur_die_gelisteten_in_dateireihenfolge(bestand, auswahl):
    """Keine Geschwister, keine ausgelassenen Trauben — und die Abfolge ist die
    der Datei, nicht die der Uhr."""
    html = _folge_bogen(bestand, auswahl)
    soll = [mid for _n, ids in _gelistet(auswahl) for mid in ids]
    assert _folge_ids(html) == soll


def test_die_reihenfolge_ist_nach_den_bloecken_der_datei_gegliedert(bestand, auswahl):
    html = _folge_bogen(bestand, auswahl)
    namen = re.findall(r'<section class="block" data-name="([^"]*)"', html)
    assert namen == [n for n, _ids in _gelistet(auswahl)]


def test_eine_thematisch_umsortierte_datei_erscheint_so_wie_sie_dasteht(bestand, auswahl):
    """Der Kern der Ansicht: sie zeigt den Film, nicht die Aufnahmezeit."""
    bloecke = _gelistet(auswahl)
    name, ids = bloecke[0]
    umgedreht = auswahl
    for a, b in zip(ids, reversed(ids)):
        umgedreht = umgedreht.replace(f"- {a} ", f"- @{b} ", 1)
    umgedreht = umgedreht.replace("- @", "- ")
    html = _folge_bogen(bestand, umgedreht)
    assert _folge_ids(html)[:len(ids)] == list(reversed(ids))


def test_der_bogen_geht_auf_wunsch_in_der_reihenfolge_auf(bestand, auswahl):
    assert '<body data-ansicht="auswahl">' in _folge_bogen(bestand, auswahl)
    assert '<body data-ansicht="folge">' in _folge_bogen(bestand, auswahl,
                                                          ansicht="folge")


def test_ohne_bloecke_gibt_es_keinen_umschalter(bogen):
    """Ein Bogen ohne ``folge`` ist der alte — kein toter Knopf."""
    assert 'id="folge"' not in bogen["html"]
    assert "ansicht('folge')" not in bogen["html"]


def test_der_bogen_mit_beiden_ansichten_bleibt_gueltig_und_ohne_netz(bestand, auswahl):
    html = _folge_bogen(bestand, auswahl)
    for muster in ("http:", "https:", "//", "cdn", "<script src", "<link "):
        assert muster not in html, f"externer Verweis: {muster!r}"
    for verboten in ("fetch(", "XMLHttpRequest", "localStorage", "<form",
                     "sessionStorage", "navigator.sendBeacon"):
        assert verboten not in html, verboten
    assert all('loading="lazy"' in b for b in re.findall(r"<img [^>]*>", html))


def test_der_bogen_traegt_die_pruefsumme_der_datei(bestand, auswahl):
    from slideshow.order import order_fingerprint
    html = _folge_bogen(bestand, auswahl)
    stand = order_fingerprint(OrderList.model_validate(_yaml(auswahl)))
    assert f'data-stand="{stand}"' in html


def test_clips_sind_in_der_reihenfolge_mit_dauer_gekennzeichnet(bestand, auswahl):
    from slideshow.models import ClipInfo

    erstes = _gelistet(auswahl)[0][1][0]
    media = [m if m.id != erstes else m.model_copy(update={
        "kind": "clip", "image": None,
        "clip": ClipInfo(duration=4.24, width=3840, height=2160)})
        for m in bestand.media]
    html = _folge_bogen(Manifest(media=media), auswahl)
    teil = html.split('<main id="folge"', 1)[1]
    kachel = re.search(rf'<figure class="([^"]*)"[^>]*data-id="{erstes}".*?</figure>',
                       teil).group(0)
    assert 'class="folge clip"' in kachel
    assert "Clip 4,2 s" in kachel


# -- das Rueckweg-Format ------------------------------------------------------

def test_eine_tauschliste_ist_keine_reihenfolge():
    """Sonst schriebe ``--apply`` aus drei Tauschen eine Folge aus drei Bildern."""
    from slideshow.sheet import parse_reorder
    assert parse_reorder("      - img_0007\n      #  raus: img_0005\n") is None


def test_die_reihenfolge_wird_zerlegt():
    from slideshow.sheet import parse_reorder
    folge = parse_reorder("﻿# Kopf\r\n# reihenfolge\r\n# stand: 0a1b2c\r\n"
                          "# block: tag-1\r\n      - img_0002\r\n"
                          "      #  raus: img_0001\r\n# block: tag-2\r\n"
                          "      - img_0003\r\n")
    assert folge.stand == "0a1b2c"
    assert folge.bloecke == [("tag-1", [("img_0002", False), ("img_0001", True)]),
                             ("tag-2", [("img_0003", False)])]
    assert folge.unklar == []


def test_ein_eintrag_vor_dem_ersten_block_wird_nicht_geraten():
    from slideshow.sheet import parse_reorder
    folge = parse_reorder("# reihenfolge\n      - img_0002\n# block: a\n")
    assert [z for _nr, z in folge.unklar] == ["- img_0002"]


# -- Einspielen ---------------------------------------------------------------

def test_nur_die_bewegte_zeile_wandert(bestand, auswahl):
    """Das letzte Bild eines Blocks nach vorn gezogen: eine Zeile (samt ihren
    ``statt:``-Zeilen) wandert, alles andere bleibt stehen. Mit der naheliegenden
    Platz-fuer-Platz-Fuellung rutschte jeder Eintrag des Blocks um eins, und die
    Kommentare dazwischen stuenden neben fremden Bildern — an ``london-2026``
    gemessen: 20 geaenderte Zeilen fuer drei Handgriffe."""
    import difflib
    bloecke = _gelistet(auswahl)
    bi = max(range(len(bloecke)), key=lambda i: len(bloecke[i][1]))
    name, ids = bloecke[bi]
    neu_ids = [ids[-1]] + ids[:-1]
    neu, _m = _umsortieren(bestand, auswahl,
                           bloecke[:bi] + [(name, neu_ids)] + bloecke[bi + 1:])
    assert _gelistet(neu)[bi] == (name, neu_ids)
    alt_z, neu_z = auswahl.splitlines(), neu.splitlines()
    sm = difflib.SequenceMatcher(a=alt_z, b=neu_z, autojunk=False)
    weg = sum(i2 - i1 for op, i1, i2, _j1, _j2 in sm.get_opcodes() if op != "equal")
    dazu = sum(j2 - j1 for op, _i1, _i2, j1, j2 in sm.get_opcodes() if op != "equal")
    nr = next(i for i, z in enumerate(alt_z) if re.match(rf"\s*- {ids[-1]}\b", z))
    from slideshow.order import _gepaeck
    groesse = _gepaeck(alt_z, nr) - nr
    assert (weg, dazu) == (groesse, groesse), (weg, dazu, groesse)


def test_ein_tausch_im_block_erfindet_und_verliert_keine_zeile(bestand, auswahl):
    """Alles ausser der bewegten Zeile — ausgelassene Trauben, Kopf,
    Kommentare — bleibt Zeichen fuer Zeichen stehen."""
    bloecke = _gelistet(auswahl)
    name, ids = bloecke[0]
    neu_ids = [ids[1], ids[0]] + ids[2:]
    neu, meldungen = _umsortieren(bestand, auswahl,
                                  [(name, neu_ids)] + bloecke[1:])
    assert _gelistet(neu)[0] == (name, neu_ids)
    assert _gelistet(neu)[1:] == bloecke[1:]
    assert sorted(neu.splitlines()) == sorted(auswahl.splitlines()), \
        "eine Umsortierung erfindet und verliert keine Zeile"
    assert any("1 Medien an neuer Stelle" in m for m in meldungen), meldungen


def test_die_alternativen_wandern_mit_ihrem_bild(bestand):
    """Die ``statt:``-Zeilen beschreiben *dieses* Bild. Blieben sie stehen,
    stuenden die Geschwister unter einem fremden."""
    sel = select_media(bestand, count=20, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    bloecke = _gelistet(text)
    bi, mit = next((bi, mid) for bi, (_n, ids) in enumerate(bloecke)
                   for mid in ids if sel.alternativen.get(mid))
    name, ids = bloecke[bi]
    neu_ids = [mit] + [i for i in ids if i != mit] if ids[0] != mit \
        else [i for i in ids if i != mit] + [mit]
    neu, _m = _umsortieren(bestand, text,
                           bloecke[:bi] + [(name, neu_ids)] + bloecke[bi + 1:])
    zeilen = neu.splitlines()
    nr = next(i for i, z in enumerate(zeilen) if re.match(rf"\s*- {mit}\b", z))
    geschwister = sel.alternativen[mit][0].id
    assert "statt:" in zeilen[nr + 1] and geschwister in zeilen[nr + 1]


def test_ein_bild_wandert_in_einen_anderen_block(bestand, auswahl):
    bloecke = _gelistet(auswahl)
    (n0, a), (n1, b) = bloecke[0], bloecke[1]
    wandert = a[-1]
    neu, meldungen = _umsortieren(bestand, auswahl,
                                  [(n0, a[:-1]), (n1, [wandert] + b)] + bloecke[2:])
    assert _gelistet(neu)[:2] == [(n0, a[:-1]), (n1, [wandert] + b)]
    assert any("1 Medien an neuer Stelle" in m for m in meldungen), \
        "der Blockwechsel zaehlt als Verschiebung, auch ohne Positionswechsel"


def test_ein_leergezogener_block_wird_zur_leeren_liste(bestand, auswahl):
    """``items:`` ohne Eintrag darunter waere YAML-null und brache das Laden."""
    bloecke = _gelistet(auswahl)
    (n0, a), (n1, b) = bloecke[0], bloecke[1]
    neu, _m = _umsortieren(bestand, auswahl, [(n0, []), (n1, a + b)] + bloecke[2:])
    assert _gelistet(neu)[:2] == [(n0, []), (n1, a + b)]
    assert re.search(rf"- name: {n0}\n\s+items: \[\]", neu)


def test_ein_leerer_block_laesst_sich_fuellen(bestand, auswahl):
    """``technisch-ausgeschlossen`` steht mit ``items: []`` in der Datei —
    hinein gezogen werden muss trotzdem gehen, sonst ist die Liste kaputt."""
    bloecke = _gelistet(auswahl)
    leer = next(i for i, (_n, ids) in enumerate(bloecke) if not ids)
    (n0, a) = bloecke[0]
    neu_bloecke = [list(x) for x in bloecke]
    neu_bloecke[0] = [n0, a[1:]]
    neu_bloecke[leer] = [bloecke[leer][0], [a[0]]]
    neu, _m = _umsortieren(bestand, auswahl, [tuple(x) for x in neu_bloecke])
    assert _gelistet(neu)[leer] == (bloecke[leer][0], [a[0]])


def test_hinausgenommenes_wird_auskommentiert(bestand, auswahl):
    from slideshow.order import mentioned_ids
    bloecke = _gelistet(auswahl)
    weg = bloecke[0][1][0]
    neu, meldungen = _umsortieren(bestand, auswahl, bloecke, raus={weg})
    assert weg not in [i for _n, ids in _gelistet(neu) for i in ids]
    assert weg in mentioned_ids(neu)
    assert any("auskommentiert" in m for m in meldungen)


def test_hereingenommenes_steht_wo_der_bogen_es_hinsetzt(bestand, auswahl):
    """In der Reihenfolge steht ein angeklicktes Geschwister dort, wohin es
    gezogen wurde — nicht dort, wo die Uhr es hinstellen wuerde."""
    bloecke = _gelistet(auswahl)
    gelistet = {i for _n, ids in bloecke for i in ids}
    neu_id = next(m.id for m in bestand.media if m.id not in gelistet)
    n0, a = bloecke[-1] if bloecke[-1][1] else bloecke[0]
    ziel = [(n, ([neu_id] + ids if n == n0 else ids)) for n, ids in bloecke]
    neu, meldungen = _umsortieren(bestand, auswahl, ziel)
    assert dict(_gelistet(neu))[n0][0] == neu_id
    assert any("hereingenommen" in m for m in meldungen)


def test_eine_inzwischen_geaenderte_datei_wird_nicht_ueberschrieben(bestand, auswahl):
    """Die Liste ist eine vollstaendige Folge — von Hand verschobene Zeilen
    gingen sonst kommentarlos verloren."""
    from slideshow.errors import SchemaError
    with pytest.raises(SchemaError, match="nachdem der Kontaktbogen erzeugt"):
        _umsortieren(bestand, auswahl, _gelistet(auswahl), stand="000000000000")


def test_ein_fehlendes_medium_bricht_ab(bestand, auswahl):
    from slideshow.errors import SchemaError
    bloecke = _gelistet(auswahl)
    n0, a = bloecke[0]
    with pytest.raises(SchemaError, match="fehlen in der Liste"):
        _umsortieren(bestand, auswahl, [(n0, a[1:])] + bloecke[1:], stand=None)


def test_andere_bloecke_brechen_ab(bestand, auswahl):
    from slideshow.errors import SchemaError
    bloecke = _gelistet(auswahl)
    with pytest.raises(SchemaError, match="passen nicht"):
        _umsortieren(bestand, auswahl, [("erfunden", bloecke[0][1])] + bloecke[1:],
                     stand=None)


def test_flow_stil_laesst_sich_nicht_umsortieren(bestand):
    from slideshow.errors import SchemaError
    ids = [m.id for m in bestand.media[:3]]
    text = f"version: 1\nrest: drop\norder: [{', '.join(ids)}]\n"
    with pytest.raises(SchemaError, match="Flow-Stil|einen Eintrag je Zeile"):
        _umsortieren(bestand, text, [("", list(reversed(ids)))])


def test_die_flache_form_laesst_sich_umsortieren(bestand):
    ids = [m.id for m in bestand.media[:4]]
    text = ("version: 1\nrest: drop\norder:\n"
            + "".join(f"  - {i}   # Kommentar {i}\n" for i in ids))
    neu, _m = _umsortieren(bestand, text, [("", [ids[2], ids[0], ids[1], ids[3]])])
    assert _gelistet(neu) == [("", [ids[2], ids[0], ids[1], ids[3]])]
    assert f"- {ids[2]}   # Kommentar {ids[2]}" in neu, "der Kommentar reist mit"


def test_der_rundlauf_reihenfolge_bis_bogen_schliesst_sich(bestand, auswahl):
    """Umsortieren, einspielen, Bogen neu: die Ansicht zeigt die neue Folge,
    und die Pruefsumme passt zur neuen Datei."""
    from slideshow.order import order_fingerprint
    bloecke = _gelistet(auswahl)
    n0, a = bloecke[0]
    neu, _m = _umsortieren(bestand, auswahl, [(n0, list(reversed(a)))] + bloecke[1:])
    html = _folge_bogen(bestand, neu)
    assert _folge_ids(html)[:len(a)] == list(reversed(a))
    stand = order_fingerprint(OrderList.model_validate(_yaml(neu)))
    assert f'data-stand="{stand}"' in html
    # Dieselbe Liste noch einmal: die Datei steht schon so da.
    zweit, meldungen = _umsortieren(bestand, neu,
                                    [(n0, list(reversed(a)))] + bloecke[1:])
    assert zweit == neu
    assert any("nichts zu tun" in m for m in meldungen)


# --------------------------------------------------------------------------
# Thumbnails
# --------------------------------------------------------------------------

def _jpeg(pfad: Path, breite: int, hoehe: int) -> Path:
    from PIL import Image, ImageDraw
    pfad.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (breite, hoehe), (40, 90, 160))
    d = ImageDraw.Draw(img)
    for x in range(0, breite, 60):
        d.line([(x, 0), (x, hoehe)], fill=(240, 230, 60), width=3)
    img.save(pfad, quality=92)
    return pfad


def _manifest_fuer(project, pfade: list[Path]) -> Manifest:
    media = []
    for i, p in enumerate(pfade):
        media.append(MediaItem(id=f"img_{i:03d}", path=project.rel(p), kind="image",
                               capture_time=1.0 * i, time_source="exif",
                               image=ImageInfo(width=1600, height=1000)))
    return Manifest(media=media)


@requires_exiftool
def test_die_eingebettete_vorschau_wird_ohne_decodierung_genommen(project, tmp_path):
    """Der Kern von 5.2: was im Header liegt, wird nicht neu gerechnet."""
    from slideshow.proc import run
    quelle = _jpeg(tmp_path / "src" / "gross.jpg", 2400, 1600)
    vorschau = _jpeg(tmp_path / "src" / "klein.jpg", 640, 426)
    run(["exiftool", "-overwrite_original",
         f"-ThumbnailImage<={vorschau}", str(quelle)], check=False)

    manifest = _manifest_fuer(project, [quelle])
    thumbs, stats = thumbnails(project, manifest.media, size=320)

    assert stats.aus_vorschau == 1, (stats.aus_vorschau, stats.skaliert)
    assert stats.skaliert == 0
    ziel = thumbs["img_000"]
    assert ziel.exists() and ziel.parent.name == "thumbs"
    # Byteweise die eingebettete Vorschau — nicht neu komprimiert.
    assert ziel.read_bytes()[:2] == b"\xff\xd8"


@requires_exiftool
def test_eine_zu_kleine_vorschau_wird_verworfen(project, tmp_path):
    """Ein 160x120-Thumbnail in einer 320-px-Kachel sieht aus wie ein
    unscharfes Bild — und man sieht ihm nicht an, dass es die Vorschau war."""
    from slideshow.proc import run
    quelle = _jpeg(tmp_path / "src" / "gross.jpg", 2400, 1600)
    winzig = _jpeg(tmp_path / "src" / "winzig.jpg", 160, 120)
    run(["exiftool", "-overwrite_original",
         f"-ThumbnailImage<={winzig}", str(quelle)], check=False)

    manifest = _manifest_fuer(project, [quelle])
    _thumbs, stats = thumbnails(project, manifest.media, size=320)
    assert stats.aus_vorschau == 0


@requires_ffmpeg
def test_ohne_eingebettete_vorschau_wird_skaliert(project, tmp_path):
    quelle = _jpeg(tmp_path / "src" / "nackt.jpg", 2400, 1600)
    manifest = _manifest_fuer(project, [quelle])
    thumbs, stats = thumbnails(project, manifest.media, size=320)

    assert stats.skaliert == 1 and stats.aus_vorschau == 0
    from PIL import Image
    with Image.open(thumbs["img_000"]) as img:
        assert max(img.size) <= 320


@requires_ffmpeg
def test_ein_zweiter_lauf_erzeugt_nur_fehlendes(project, tmp_path):
    pfade = [_jpeg(tmp_path / "src" / f"b{i}.jpg", 900, 600) for i in range(3)]
    manifest = _manifest_fuer(project, pfade)

    thumbs, erst = thumbnails(project, manifest.media, size=320)
    assert erst.erzeugt == 3 and erst.aus_cache == 0
    stempel = {mid: p.stat().st_mtime_ns for mid, p in thumbs.items()}

    _thumbs, zweit = thumbnails(project, manifest.media, size=320)
    assert zweit.aus_cache == 3 and zweit.erzeugt == 0

    thumbs["img_001"].unlink()
    thumbs3, dritt = thumbnails(project, manifest.media, size=320)
    assert dritt.aus_cache == 2 and dritt.erzeugt == 1
    assert thumbs3["img_000"].stat().st_mtime_ns == stempel["img_000"]


def test_ohne_quelldatei_bleibt_die_kachel_leer_statt_zu_brechen(project):
    """Ein Manifest kann auf Material zeigen, das umgezogen ist. Der Bogen
    soll dann eine Kachel weniger haben und nicht abbrechen."""
    m = MediaItem(id="img_weg", path="src/weg.jpg", kind="image",
                  capture_time=1.0, time_source="exif",
                  image=ImageInfo(width=6000, height=4000))
    thumbs, stats = thumbnails(project, [m], size=320)
    assert thumbs == {} and stats.fehlend == ["img_weg"]

    manifest = Manifest(media=[m])
    olist = OrderList.model_validate(
        {"version": 1, "rest": "drop", "groups": [{"name": "a", "items": ["img_weg"]}]})
    sel = selection_from_order(manifest, olist, "")
    html = dump_sheet_html(sel, thumbs, manifest, base=Path("."))
    assert "kein Thumbnail" in html and "<img" not in html
