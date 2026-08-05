"""Abnahme der Auswahl (``docs/briefing-auswahl.md``, Abschnitt 9).

Diese Tests brauchen weder ffmpeg noch echte Bilder: das Verfahren rechnet auf
Zeitstempeln und EXIF, und beides laesst sich erfinden. Ein Manifest reicht.

Der Bestand wird darum synthetisch gebaut — aber mit den Eigenschaften, an
denen das Verfahren wirklich haengt: **sehr ungleich verteilte Tage** (5 bis
400 Aufnahmen), **Serien** im Sekundenabstand und **zwei Kameras**, die
verschraenkt laufen. Ein gleichmaessiger Bestand wuerde jede der vier Stufen
bestehen lassen, auch eine kaputte.
"""

from __future__ import annotations

import datetime as _dt
import random

import pytest

from slideshow.models import ImageInfo, Manifest, MediaItem
from slideshow.select import (BURST_GAP, Burst, bursts, day_quota,
                              dump_selection_yaml, hard_filter, pick_in_burst,
                              select_media, spread, verwackelt)

from .conftest import TEST_LONG_EDGE, TEST_SIZE

#: Tagesmengen des Testbestands — bewusst um den Faktor 80 gespreizt.
TAGESMENGEN = [5, 400, 40, 12, 90, 8, 200, 30, 15, 60,
               25, 7, 110, 45, 20, 35, 18, 6, 80, 22,
               14, 55, 9, 28, 130, 11, 16, 24, 13, 42]


# --------------------------------------------------------------------------
# Bestand
# --------------------------------------------------------------------------

def _bild(nr: int, ts: float, *, kamera: str = "ILCE-6700", hoch: bool = False,
          breite: int = 6232, hoehe: int = 4160, groesse: int = 6_000_000,
          sterne: int = 0, zeit: float = 0.002, brennweite: float = 25.0) -> MediaItem:
    if hoch:
        breite, hoehe = hoehe, breite
    return MediaItem(
        id=f"img_{nr:05d}", path=f"src/img_{nr:05d}.jpg", kind="image",
        size_bytes=groesse, camera=kamera, capture_time=ts, time_source="exif",
        rating=sterne,
        image=ImageInfo(width=breite, height=hoehe, portrait=hoch,
                        exposure_time=zeit, focal=brennweite, iso=100))


@pytest.fixture
def bestand() -> Manifest:
    """Ein Sammelbecken von rund 1500 Bildern ueber 30 Tage."""
    rng = random.Random(1)
    start = _dt.datetime(2026, 6, 1, 8, 0, 0)
    media, nr = [], 0
    for tag, menge in enumerate(TAGESMENGEN):
        t = start + _dt.timedelta(days=tag)
        rest = menge
        while rest > 0:
            serie = min(rest, rng.choice([1, 1, 1, 2, 3, 5, 8]))
            for k in range(serie):
                nr += 1
                klein = rng.random() < 0.03
                media.append(_bild(
                    nr, (t + _dt.timedelta(seconds=k * 3)).timestamp(),
                    kamera="ILCE-6700" if rng.random() < 0.8 else "Pixel 8",
                    hoch=rng.random() < 0.25,
                    breite=1024 if klein else 6232, hoehe=768 if klein else 4160,
                    groesse=rng.randint(4_000_000, 9_000_000)))
            rest -= serie
            t += _dt.timedelta(minutes=rng.choice([2, 5, 9, 14, 25, 40]))
    return Manifest(media=media)


def _zeiten(manifest: Manifest, ids: list[str]) -> list[float]:
    by_id = {m.id: m for m in manifest.media}
    return sorted(by_id[i].capture_time for i in ids)


# --------------------------------------------------------------------------
# A2 — keine zeitlich benachbarten Aufnahmen
# --------------------------------------------------------------------------

def test_die_auswahl_enthaelt_keine_zwei_aufnahmen_derselben_traube(bestand):
    sel = select_media(bestand, count=187, seed=4711)
    by_id = {m.id: m for m in bestand.media}
    gewaehlt = sorted((by_id[i].capture_time, by_id[i]) for i in sel.ids)
    eng = [(a[1], b[1]) for a, b in zip(gewaehlt, gewaehlt[1:])
           if b[0] - a[0] < BURST_GAP]
    # Uebrig bleiben darf nur die Geraeteausnahme: zwei Kameras, die
    # gleichzeitig laufen, haben getrennte Trauben.
    gleiche_kamera = [(a, b) for a, b in eng if a.camera == b.camera]
    assert not gleiche_kamera, [(a.id, b.id) for a, b in gleiche_kamera]


def test_verschraenkte_kameras_zerreissen_die_traube_nicht(bestand):
    """Regression: bei der Folge Sony, Pixel, Sony trennte ein blosses
    "Geraetewechsel trennt" die beiden Sony-Aufnahmen in verschiedene Trauben —
    zwoelf Sekunden auseinander, und beide waehlbar."""
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    media = [_bild(1, t, kamera="Sony"), _bild(2, t + 6, kamera="Pixel"),
             _bild(3, t + 12, kamera="Sony")]
    trauben = bursts(media, {})
    nach_kamera = {b.items[0].camera: b for b in trauben}
    assert len(trauben) == 2, "je Kamera eine Traube"
    assert len(nach_kamera["Sony"]) == 2, "beide Sony-Aufnahmen in einer Traube"


def test_eine_lange_serie_wird_an_der_groessten_luecke_geteilt():
    """Ohne Deckel verschmilzt eine Stunde im Minutentakt zu einem Eintrag."""
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    # 40 Aufnahmen im 60-s-Takt: jede einzelne Luecke liegt unter `gap`.
    media = [_bild(i, t + i * 60) for i in range(40)]
    ohne = bursts(media, {}, max_span=1e9)
    mit = bursts(media, {}, max_span=600.0)
    assert len(ohne) == 1
    assert len(mit) > 1
    assert all(b.end - b.start <= 600.0 for b in mit)


def test_ein_ortssprung_trennt_auch_ohne_zeitluecke():
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    a, b = _bild(1, t), _bild(2, t + 10)
    a.gps, b.gps = (52.52, 13.40), (55.68, 12.57)      # Berlin -> Kopenhagen
    assert len(bursts([a, b], {})) == 2


# --------------------------------------------------------------------------
# A3 — die Quote trifft
# --------------------------------------------------------------------------

def test_die_quote_vergibt_genau_die_zielzahl(bestand):
    sel = select_media(bestand, count=187, seed=4711)
    assert sum(n for n, _, _ in sel.quote.values()) == 187
    assert len(sel.ids) == 187


def test_kein_tag_mit_material_geht_leer_aus(bestand):
    sel = select_media(bestand, count=187, seed=4711)
    assert len(sel.quote) == len(TAGESMENGEN)
    assert min(n for n, _, _ in sel.quote.values()) >= 1


def test_gedaempft_heisst_zwischen_gleichverteilung_und_proportional():
    """Bei ``alpha = 0,5`` waechst die Quote wie die Wurzel des Materials.

    Die Zielzahl ist bewusst klein gegen den Bestand: bei 100 Plaetzen auf 125
    Trauben bindet ``n_j <= c_j``, der kleine Tag ist bei seinen 25 gedeckelt,
    und gemessen wuerde die Klemmung statt der Daempfung.
    """
    counts = {"a": 100, "b": 25}                    # Verhaeltnis 4:1
    sitze = day_quota(counts, 30, alpha=0.5, floor=0, max_share=1.0)
    # Wurzel von 4 ist 2 — der grosse Tag bekommt doppelt so viele, nicht
    # viermal so viele und nicht gleich viele.
    assert sitze["a"] / sitze["b"] == pytest.approx(2.0, abs=0.15)

    gleich = day_quota(counts, 30, alpha=0.0, floor=0, max_share=1.0)
    assert gleich["a"] == gleich["b"]

    proportional = day_quota(counts, 30, alpha=1.0, floor=0, max_share=1.0)
    assert proportional["a"] / proportional["b"] == pytest.approx(4.0, abs=0.2)


def test_der_deckel_erzwingt_bei_wenigen_gruppen_keine_gleichverteilung():
    """Regression: bei vier Gruppen *ist* ein Anteil von 25 Prozent schon der
    Gleichanteil. Der Deckel traf dann jede Gruppe und machte `alpha`
    wirkungslos — aus einer Daempfung wurde eine Gleichverteilung."""
    counts = {"a": 64, "b": 4, "c": 4, "d": 4}
    sitze = day_quota(counts, 40, alpha=0.5, floor=1, max_share=0.25)
    assert sitze["a"] > sitze["b"], "die Daempfung muss noch wirken"


def test_eine_gruppe_bekommt_nie_mehr_plaetze_als_sie_trauben_hat():
    """Sonst braeche die Quote die Traubenregel, und die ist wichtiger."""
    counts = {"a": 3, "b": 100}
    sitze = day_quota(counts, 80, alpha=0.0, floor=1, max_share=1.0)
    assert sitze["a"] <= 3


def test_unerfuellbarer_boden_gewinnt_und_wird_gemeldet(bestand):
    """Dreissig Tage und mindestens ein Bild je Tag gehen bei zehn Bildern
    nicht auf. Dann ist ein Tag, der aus dem Film faellt, das schlechtere
    Ergebnis als ein paar Bilder zu viel."""
    sel = select_media(bestand, count=10, seed=1, min_per_day=1)
    assert len(sel.ids) >= len(TAGESMENGEN)
    assert any("geht die Zielzahl nicht auf" in m for m in sel.meldungen)


# --------------------------------------------------------------------------
# A4 — Spreizung
# --------------------------------------------------------------------------

def _variationskoeffizient(zeiten: list[float]) -> float:
    d = [b - a for a, b in zip(zeiten, zeiten[1:])]
    mittel = sum(d) / len(d)
    streuung = (sum((x - mittel) ** 2 for x in d) / len(d)) ** 0.5
    return streuung / mittel


def test_die_spreizung_verteilt_gleichmaessiger_als_der_zufall(bestand):
    """Zufaellig gezogen kaemen bei acht Bildern regelmaessig fuenf vom
    Abendessen — dort wurde am meisten fotografiert."""
    rng = random.Random(7)
    tag = _dt.date(2026, 6, 2)                       # der Tag mit 400 Aufnahmen
    des_tages = [m for m in bestand.media
                 if _dt.datetime.fromtimestamp(m.capture_time).date() == tag]
    sel = select_media(bestand, count=187, seed=4711)
    gewaehlt = [m.capture_time for m in des_tages if m.id in set(sel.ids)]
    assert len(gewaehlt) >= 5

    cv_auswahl = _variationskoeffizient(sorted(gewaehlt))
    stichproben = [_variationskoeffizient(
        sorted(m.capture_time for m in rng.sample(des_tages, len(gewaehlt))))
        for _ in range(100)]
    assert cv_auswahl < sum(stichproben) / len(stichproben)


def test_spread_nimmt_alles_wenn_die_quote_die_trauben_uebersteigt():
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    trauben = [Burst(items=[_bild(i, t + i * 3600)], times=[t + i * 3600])
               for i in range(3)]
    assert len(spread(trauben, 10, random.Random(1))) == 3
    assert spread(trauben, 0, random.Random(1)) == []


# --------------------------------------------------------------------------
# A5 — reproduzierbar, aber nicht starr
# --------------------------------------------------------------------------

def test_derselbe_seed_liefert_dieselbe_auswahl(bestand):
    a = select_media(bestand, count=187, seed=4711)
    b = select_media(bestand, count=187, seed=4711)
    assert a.ids == b.ids
    assert dump_selection_yaml(a, bestand) == dump_selection_yaml(b, bestand)


def test_ein_anderer_seed_liefert_einen_anderen_vorschlag(bestand):
    """Sonst waere der Zufall wirkungslos, und ein zweiter Vorschlag zum
    Vergleichen nicht zu bekommen."""
    a = set(select_media(bestand, count=187, seed=4711).ids)
    b = set(select_media(bestand, count=187, seed=9999).ids)
    assert len(a ^ b) / 2 / len(a) > 0.2


def test_ohne_seed_wird_einer_gezogen_und_protokolliert(bestand):
    sel = select_media(bestand, count=50)
    assert sel.seed > 0
    assert f"Seed {sel.seed}" in dump_selection_yaml(sel, bestand)


def test_das_modulglobale_random_wird_nicht_benutzt(bestand):
    """Sonst haenge das Ergebnis daran, wer vorher gezogen hat."""
    random.seed(1)
    a = select_media(bestand, count=50, seed=4711).ids
    random.seed(999)
    [random.random() for _ in range(100)]
    b = select_media(bestand, count=50, seed=4711).ids
    assert a == b


# --------------------------------------------------------------------------
# Harte Filter und Wahl in der Traube
# --------------------------------------------------------------------------

def test_zu_kleine_bilder_fallen_mit_grund_heraus():
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    gross, klein = _bild(1, t), _bild(2, t + 3600, breite=1024, hoehe=768)
    ok, gruende = hard_filter([gross, klein], min_long_edge=2160)
    assert [m.id for m in ok] == [gross.id]
    assert "1024x768" in gruende[klein.id]


def test_sterne_schlagen_alles_andere_in_der_traube():
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    # Das bewertete Bild ist das erste der Serie und das kleinste — beide
    # Signale sprechen dagegen, die Sterne trotzdem dafuer.
    b = Burst(items=[_bild(1, t, sterne=5, groesse=1_000_000),
                     _bild(2, t + 3, groesse=9_000_000),
                     _bild(3, t + 6, groesse=9_000_000)],
              times=[t, t + 3, t + 6])
    gezogen = [pick_in_burst(b, random.Random(s)).id for s in range(30)]
    assert gezogen.count("img_00001") > 25


def test_rating_min_schliesst_unbewertetes_aus():
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    a, b = _bild(1, t, sterne=2), _bild(2, t + 3600, sterne=0)
    ok, gruende = hard_filter([a, b], rating_min=1)
    assert [m.id for m in ok] == [a.id]
    assert "ohne Bewertung" in gruende[b.id]


def test_die_freihandregel_kennt_die_brennweite():
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    assert verwackelt(_bild(1, t, zeit=1 / 30, brennweite=200.0))
    assert not verwackelt(_bild(2, t, zeit=1 / 500, brennweite=200.0))
    # Ohne Brennweite kein Urteil — geraten wird hier nicht.
    ohne = _bild(3, t, zeit=1.0)
    ohne.image.focal = 0.0
    assert not verwackelt(ohne)


def test_clips_treten_nicht_gegen_bilder_an(bestand):
    """Bei drei Clips auf tausend Bildern fiele sonst regelmaessig keiner an."""
    from slideshow.models import ClipInfo
    t = _dt.datetime(2026, 6, 15, 12, 0, 0).timestamp()
    clip = MediaItem(id="clip_a", path="src/a.mp4", kind="clip", camera="Pixel 8",
                     capture_time=t, time_source="container",
                     clip=ClipInfo(duration=8.0, effective_duration=8.0))
    bestand.media.append(clip)
    sel = select_media(bestand, count=50, seed=3)
    assert "clip_a" in sel.ids


# --------------------------------------------------------------------------
# Die erzeugte Datei
# --------------------------------------------------------------------------

def test_die_datei_laedt_als_order_yaml_und_ergibt_dieselbe_folge(bestand, tmp_path):
    """A6 im Kleinen: was `select` schreibt, muss `build` lesen koennen."""
    from slideshow.order import load_order, resolve_order

    sel = select_media(bestand, count=187, seed=4711)
    pfad = tmp_path / "order.yaml"
    pfad.write_text(dump_selection_yaml(sel, bestand), encoding="utf-8")

    olist, zeilen = load_order(pfad)
    assert olist.rest == "drop"
    ids, _meldungen = resolve_order(bestand, olist, quelle=str(pfad), zeilen=zeilen)
    assert ids == sel.ids


def test_jedes_abgewaehlte_bild_steht_in_der_datei(bestand):
    """A7s Voraussetzung: `order --update` bietet jede ID, die *nirgends* in der
    Datei steht, erneut als neu an — eine Abwahl waere nach dem dritten Lauf
    unauffindbar. Regression: die Geschwister ausgelassener Trauben fehlten,
    weil dort nur ein Vertreter ausgegeben wurde."""
    from slideshow.order import mentioned_ids

    sel = select_media(bestand, count=187, seed=4711)
    text = dump_selection_yaml(sel, bestand)
    erwaehnt = mentioned_ids(text)
    fehlend = [m.id for m in bestand.media if m.id not in erwaehnt]
    assert not fehlend, fehlend[:10]


def test_die_bloecke_taugen_als_kapitelanker(bestand):
    """Ein Kapitel je Tag ohne Zusatzarbeit — `chapters --from-groups`."""
    from slideshow.order import group_anchors, load_order, resolve_order

    sel = select_media(bestand, count=187, seed=4711)
    import io
    import yaml
    text = dump_selection_yaml(sel, bestand)
    assert yaml.safe_load(io.StringIO(text)), "gueltiges YAML"

    pfad = _schreibe(text)
    olist, _z = load_order(pfad)
    ids, _m = resolve_order(bestand, olist, quelle=str(pfad))
    anker = group_anchors(olist, bestand, ids)
    assert len(anker) == len(TAGESMENGEN)
    assert sum(a.anzahl for a in anker) == len(ids)
    assert all(not a.mehrtaegig for a in anker), "ein Block ist genau ein Tag"


def test_material_ohne_zeitstempel_bleibt_draussen_und_wird_genannt(bestand):
    t = _dt.datetime(2026, 6, 1, 10, 0, 0).timestamp()
    verwaist = _bild(99999, t)
    verwaist.time_source = "none"
    verwaist.capture_time = None
    bestand.media.append(verwaist)

    sel = select_media(bestand, count=50, seed=1)
    assert verwaist.id not in sel.ids
    assert verwaist.id in {m.id for m in sel.ohne_datum}
    assert verwaist.id in dump_selection_yaml(sel, bestand)


def test_eine_zielzahl_von_null_ist_ein_fehler(bestand):
    from slideshow.errors import SlideshowError
    with pytest.raises(SlideshowError):
        select_media(bestand, count=0)


# --------------------------------------------------------------------------
# A8 — preprocess folgt der Auswahl
# --------------------------------------------------------------------------

def test_preprocess_normalisiert_nur_die_auswahl(project, images, caps):
    """Ohne das spart die Auswahl Renderzeit, aber keine Vorbereitungszeit —
    und die ist bei tausend Bildern der groessere Posten."""
    from slideshow.preprocess import preprocess
    from slideshow.probe import probe_sources

    manifest = probe_sources(project, images, caps=caps).manifest
    auswahl = {m.id for m in manifest.media[:3]}

    stats = preprocess(project, manifest, caps=caps, size=TEST_SIZE,
                       long_edge=TEST_LONG_EDGE, only=auswahl)

    assert stats.images_done == 3
    assert stats.skipped == len(manifest.media) - 3
    assert len(list(project.cache.glob("*.jpg"))) == 3


def test_ein_nachtraeglich_hereingenommenes_bild_wird_nachgeholt(project, images, caps):
    """Wer eine Zeile in ``order.yaml`` tauscht, laesst `preprocess` erneut
    laufen — und darf dann nicht auf alles warten, was schon fertig ist."""
    from slideshow.preprocess import preprocess
    from slideshow.probe import probe_sources

    manifest = probe_sources(project, images, caps=caps).manifest
    ids = [m.id for m in manifest.media]
    lauf = dict(caps=caps, size=TEST_SIZE, long_edge=TEST_LONG_EDGE)

    preprocess(project, manifest, only=set(ids[:3]), **lauf)
    zweiter = preprocess(project, manifest, only=set(ids[:4]), **lauf)

    assert zweiter.images_done == 1, "nur das neue Bild"
    assert zweiter.images_cached == 3, "die drei anderen liegen fertig da"
    assert len(list(project.cache.glob("*.jpg"))) == 4


def test_ohne_auswahl_aendert_sich_nichts(project, images, caps):
    from slideshow.preprocess import preprocess
    from slideshow.probe import probe_sources

    manifest = probe_sources(project, images, caps=caps).manifest
    stats = preprocess(project, manifest, caps=caps, size=TEST_SIZE,
                       long_edge=TEST_LONG_EDGE)
    assert stats.images_done == len(manifest.media)
    assert stats.skipped == 0


def _schreibe(text: str):
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "order.yaml"
    p.write_text(text, encoding="utf-8")
    return p
