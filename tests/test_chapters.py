"""Kapitelerkennung (Stufe 3 aus ``docs/briefing-titelfolien.md``).

Geprueft wird die Heuristik selbst und die erzeugte Datei. Alles ohne ffmpeg —
gearbeitet wird auf einem von Hand gestellten Manifest, damit die Zeiten und
Orte *bekannt* sind und eine Aussage wie "das ist derselbe Ort" pruefbar bleibt
statt bloss plausibel.
"""

from __future__ import annotations

import pytest

from slideshow.chapters import (GAP_DAY_HOURS, coverage_note, distance_km,
                                dump_chapters_yaml, suggest)
from slideshow.models import ChapterList, Manifest, MediaItem

#: Echte Koordinaten — die Abstaende sollen stimmen.
KOPENHAGEN = (55.6761, 12.5683)
MALMOE = (55.6050, 13.0038)          # 28 km, knapp unter der Schwelle
STOCKHOLM = (59.3293, 18.0686)       # gut 500 km
T0 = 1_753_000_000                   # 20. Juli 2025, lokale Zeit


def _bild(nr: int, stunden: float, ort=None, **kw) -> MediaItem:
    return MediaItem(id=f"img_{nr:03d}", path=f"src/img_{nr:03d}.jpg", kind="image",
                     time_source="exif", capture_time=T0 + int(stunden * 3600),
                     gps=ort, **kw)


def _reise(*, mit_gps: bool = True) -> Manifest:
    """Zwei Tage Kopenhagen, dann Malmoe, dann Stockholm."""
    def ort(o):
        return o if mit_gps else None
    media = ([_bild(i, i * 0.5, ort(KOPENHAGEN)) for i in range(6)]
             + [_bild(10 + i, 26 + i * 0.5, ort(KOPENHAGEN)) for i in range(4)]
             + [_bild(20 + i, 50 + i * 0.5, ort(MALMOE)) for i in range(5)]
             + [_bild(30 + i, 100 + i * 0.5, ort(STOCKHOLM)) for i in range(5)])
    return Manifest(media=media, fps_suggestion=60.0)


# --------------------------------------------------------------------------
# Entfernung
# --------------------------------------------------------------------------

def test_entfernung_stimmt_auf_der_kugel():
    """Die ebene Naeherung taugt nicht: bei 55 Grad Nord schrumpft ein
    Laengengrad auf 64 km, und ein Ost-West-Sprung waere fast doppelt so gross
    gerechnet, wie er ist."""
    assert distance_km(KOPENHAGEN, MALMOE) == pytest.approx(28, abs=2)
    assert distance_km(KOPENHAGEN, STOCKHOLM) == pytest.approx(521, abs=10)
    assert distance_km(KOPENHAGEN, KOPENHAGEN) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Heuristik
# --------------------------------------------------------------------------

def test_der_weite_sprung_wird_als_grenze_erkannt():
    stark = [v for v in suggest(_reise()) if v.staerke == "stark"]
    assert [v.before for v in stark] == ["img_030"]
    assert "Ortssprung" in stark[0].grund


def test_gps_darf_der_uhr_auch_widersprechen():
    """Die Nacht im selben Hotel ist eine lange Pause und kein neuer Abschnitt.

    Ohne dieses Veto bekaeme eine Reise so viele Kapitel wie Tage. Geprueft wird
    an einer 24-h-Luecke bei **identischen** Koordinaten: die Zeitschwelle ist
    ueberschritten, das Ergebnis muss trotzdem schwach sein.
    """
    treffer = {v.before: v for v in suggest(_reise())}
    assert treffer["img_010"].luecke_h >= 20
    assert treffer["img_010"].staerke == "schwach"
    assert "gleicher Ort" in treffer["img_010"].grund


def test_ohne_gps_bleibt_die_zeitluecke():
    """Die Gegenprobe: dieselbe Reise ohne Koordinaten kennt kein Veto und
    meldet jede lange Pause."""
    stark = [v.before for v in suggest(_reise(mit_gps=False)) if v.staerke == "stark"]
    assert stark == ["img_010", "img_020", "img_030"]


def test_die_schwelle_laesst_sich_senken():
    """Kopenhagen und Malmoe liegen 28 km auseinander — knapp unter der
    Vorgabe. Genau dafuer gibt es den Schalter."""
    stark = [v.before for v in suggest(_reise(), min_jump_km=20.0)
             if v.staerke == "stark"]
    assert "img_020" in stark


def test_tagesgrenzen_bleiben_als_schwacher_kandidat():
    schwach = [v for v in suggest(_reise()) if v.staerke == "schwach"]
    assert schwach, "eine 24-h-Pause soll sichtbar bleiben, nur nicht gesetzt"
    assert all(v.luecke_h >= GAP_DAY_HOURS for v in schwach)


def test_material_ohne_zeitstempel_erzeugt_keine_grenzen():
    mf = Manifest(media=[MediaItem(id=f"img_{i}", path=f"s/{i}.jpg", kind="image")
                         for i in range(5)], fps_suggestion=60.0)
    assert suggest(mf) == []


def test_kurze_pausen_ergeben_nichts():
    mf = Manifest(media=[_bild(i, i * 0.25, KOPENHAGEN) for i in range(10)],
                  fps_suggestion=60.0)
    assert suggest(mf) == []


# --------------------------------------------------------------------------
# Bericht
# --------------------------------------------------------------------------

def test_die_abdeckung_wird_benannt():
    """Die Aussagekraft haengt am GPS — das muss dastehen, bevor jemand der
    Datei vertraut."""
    assert "alle 20" in coverage_note(_reise())
    assert "kein Foto" in coverage_note(_reise(mit_gps=False))

    gemischt = _reise()
    gemischt.media[0].gps = None
    assert "19 von 20" in coverage_note(gemischt)


# --------------------------------------------------------------------------
# Die erzeugte Datei
# --------------------------------------------------------------------------

def test_die_datei_ist_gueltiges_yaml_und_traegt_leere_ueberschriften(tmp_path):
    p = tmp_path / "chapters.yaml"
    p.write_text(dump_chapters_yaml(suggest(_reise())), encoding="utf-8")

    import yaml
    daten = yaml.safe_load(p.read_text(encoding="utf-8"))
    eintraege = daten["chapters"]
    assert eintraege, "der Auftakt und die starke Grenze muessen drinstehen"
    assert all(e["title"] == "" for e in eintraege)
    assert eintraege[0]["at"] == 0, "der Auftakt steht vor allem Material"
    assert [e["before"] for e in eintraege if "before" in e] == ["img_030"]


def test_build_bricht_bei_leerer_ueberschrift_mit_zeilennummer_ab(tmp_path):
    """Der gewollte Abbruch: die Datei ist ein Formular, kein Erzeugnis."""
    from slideshow.errors import SchemaError

    p = tmp_path / "chapters.yaml"
    p.write_text(dump_chapters_yaml(suggest(_reise())), encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        ChapterList.load(p)
    assert "ausfuellen" in str(exc.value)
    assert exc.value.line, "die Meldung muss auf die Zeile zeigen"


def test_ausgefuellt_laedt_die_datei_durch(tmp_path):
    p = tmp_path / "chapters.yaml"
    text = dump_chapters_yaml(suggest(_reise()))
    p.write_text(text.replace('title: ""', 'title: Stockholm'), encoding="utf-8")

    kapitel = ChapterList.load(p).chapters
    assert [k.title for k in kapitel] == ["Stockholm"] * len(kapitel)
    assert kapitel[0].at == 0
    assert kapitel[-1].before == "img_030"


def test_schwache_kandidaten_stehen_auskommentiert_bereit():
    """Sie sollen sichtbar sein, ohne gesetzt zu sein — ein Handgriff entfernt
    das Kommentarzeichen."""
    text = dump_chapters_yaml(suggest(_reise()))
    assert "# - {before: img_010" in text
    assert "gleicher Ort" in text

    import yaml
    gesetzt = [e["before"] for e in yaml.safe_load(text)["chapters"] if "before" in e]
    assert "img_010" not in gesetzt


def test_ohne_auftakt_faellt_der_erste_eintrag_weg():
    import yaml
    text = dump_chapters_yaml(suggest(_reise()), auftakt=False)
    assert all("at" not in e for e in yaml.safe_load(text)["chapters"])
