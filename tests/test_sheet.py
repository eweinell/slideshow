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
