"""Der Titelgenerator (Stufe 1 aus ``docs/briefing-titelfolien.md``).

Geprueft wird hier das **Bild**: Kontrast unter dem Text, Safe Area,
Determinismus, Schriftfindung. Die Einbettung in Planung und Rundlauf steht in
``test_titles.py``, die Anbindung an den Renderpfad in ``test_title_assets.py``.

Alles laeuft ohne ffmpeg — Pillow genuegt. Und alles laeuft auf kleinen
Leinwaenden: der Generator rechnet durchgehend in Anteilen der Bildhoehe, die
Zahlen sind also groessenunabhaengig, und 7680 px waeren in jedem einzelnen
Test reine Wartezeit (dieselbe Ueberlegung wie bei ``TEST_LONG_EDGE`` in
``conftest.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from slideshow.errors import SlideshowError
from slideshow.models import Defaults, TitleSegment
from slideshow.titles import (TEXT_RGB, _contrast, _relative_luminance, find_font,
                              render_title)

#: Klein genug fuer eine schnelle Suite, gross genug fuer lesbaren Satz.
CANVAS = (640, 360)


# --------------------------------------------------------------------------
# Material
# --------------------------------------------------------------------------

def _verlauf(pfad: Path, *, hell: bool, size=(800, 500)) -> Path:
    """Ein Verlauf als Hintergrund — hell oder dunkel.

    Der helle ist der eigentliche Pruefstein von T6: ein Sonnenuntergang bleibt
    auch unscharf hell, und die Standardabdunklung reicht dort nicht.
    """
    band = Image.linear_gradient("L")
    band = band.point((lambda v: 200 + v * 55 // 255) if hell
                      else (lambda v: v * 40 // 255))
    band.convert("RGB").resize(size).save(pfad, format="JPEG", quality=95)
    return pfad


@pytest.fixture
def hell(tmp_path) -> Path:
    return _verlauf(tmp_path / "hell.jpg", hell=True)


@pytest.fixture
def dunkel(tmp_path) -> Path:
    return _verlauf(tmp_path / "dunkel.jpg", hell=False)


def _folie(title: str = "Malmoe", subtitle: str | None = "Tag 11 - 24. Juli",
           bg: str = "cache/img_005.jpg") -> TitleSegment:
    return TitleSegment(title=title, subtitle=subtitle, bg=bg)


def _backen(tmp_path, seg, quelle: Path | None, *, name: str = "folie.jpg",
            defaults: Defaults | None = None, size=CANVAS) -> tuple[Path, dict]:
    d = defaults or Defaults()
    out = tmp_path / name
    info = render_title(seg, d, bg_source=quelle, out=out, size=size,
                        font=find_font())
    return out, info


def _median_luminanz(bild: Image.Image, box) -> float:
    """Leuchtdichte des Grundes unter dem Text — an den fertigen Pixeln gemessen.

    Der Median statt des Mittels, und ``NEAREST`` statt Interpolation: der Text
    ist eingebrannt und deckt einen Teil der Flaeche weiss ab. Er belegt aber
    deutlich weniger als die Haelfte, also trifft der Median den Grund und nicht
    die Schrift. Das ist die von der Messung im Generator *unabhaengige*
    Gegenprobe zu T6.
    """
    patch = bild.crop(tuple(box)).resize((32, 32), Image.NEAREST).convert("RGB")
    roh = patch.tobytes()
    werte = sorted(_relative_luminance(roh[i:i + 3]) for i in range(0, len(roh), 3))
    return werte[len(werte) // 2]


# --------------------------------------------------------------------------
# T6 — Lesbarkeit
# --------------------------------------------------------------------------

def test_heller_hintergrund_wird_bis_zum_kontrast_abgedunkelt(tmp_path, hell):
    """Der Startwert 0,55 traegt einen hellen Verlauf nicht — die Messung muss
    ihn nachfuehren, sonst steht weisser Text auf hellgrauem Grund."""
    d = Defaults()
    _out, info = _backen(tmp_path, _folie(), hell, defaults=d)

    assert info["kontrast"] >= d.title.min_contrast
    assert info["abdunklung"] < d.title.darken, \
        "die Abdunklung wurde nicht nachgefuehrt, die Messung ist ein Stempel"
    assert not info["warnungen"]


def test_dunkler_hintergrund_bleibt_beim_startwert(tmp_path, dunkel):
    """Gegenprobe: wo der Kontrast schon traegt, wird nicht weiter abgedunkelt.
    Sonst waere jede Folie schwarz und der Hintergrund verschenkt."""
    d = Defaults()
    _out, info = _backen(tmp_path, _folie(), dunkel, defaults=d)

    assert info["abdunklung"] == pytest.approx(d.title.darken)
    assert info["kontrast"] >= d.title.min_contrast


@pytest.mark.parametrize("art", ["hell", "dunkel"])
def test_der_gemessene_kontrast_steht_auch_in_den_pixeln(tmp_path, art, request):
    """T6 an der fertigen Datei, ohne der Selbstauskunft des Generators zu
    glauben: unter der Textbounding-Box liegen >= 4,5:1."""
    quelle = request.getfixturevalue(art)
    d = Defaults()
    out, info = _backen(tmp_path, _folie(), quelle, defaults=d)

    with Image.open(out) as bild:
        grund = _median_luminanz(bild.convert("RGB"), info["box"])
    assert _contrast(_relative_luminance(TEXT_RGB), grund) >= d.title.min_contrast


def test_eine_farbflaeche_behaelt_die_gewaehlte_farbe(tmp_path):
    """``bg: "#1b2a3a"`` ist eine Entscheidung des Anwenders, kein Foto.

    Sie pauschal auf 55 % zu daempfen hiesse, sie zu ueberschreiben — die
    Nachfuehrung greift hier erst, wenn die Farbe den Text nicht traegt.
    """
    out, info = _backen(tmp_path, _folie(bg="#1b2a3a"), None)

    assert info["abdunklung"] == 1.0
    with Image.open(out) as bild:
        ecke = bild.convert("RGB").getpixel((2, 2))
    assert all(abs(a - b) <= 3 for a, b in zip(ecke, (0x1B, 0x2A, 0x3A)))


def test_unerreichbarer_kontrast_ist_eine_warnung_kein_fehler(tmp_path):
    """Die Abdunklung hat eine Untergrenze — darunter waere vom Hintergrund
    nichts mehr uebrig, und die Folie koennte gleich ``bg: none`` heissen.

    Verlangt jemand mehr Kontrast, als die Untergrenze auf weissem Grund
    hergibt, entsteht das Bild trotzdem; nur der gemessene Wert steht in der
    Warnung. Ein Abbruch mitten im Renderlauf waere die schlechtere Antwort.
    """
    streng = Defaults()
    streng.title.min_contrast = 12.0
    _out, info = _backen(tmp_path, _folie(bg="#ffffff"), None, defaults=streng)

    assert info["abdunklung"] == 0.25
    assert info["kontrast"] < streng.title.min_contrast
    assert any("Kontrast" in w for w in info["warnungen"])


# --------------------------------------------------------------------------
# T7 — Safe Area und Ueberlauf
# --------------------------------------------------------------------------

def test_die_textflaeche_liegt_vollstaendig_in_der_safe_area(tmp_path, dunkel):
    """Ueberlebt TV-Overscan und einen 4:5-Beschnitt fuer Social Media."""
    d = Defaults()
    _out, info = _backen(tmp_path, _folie(), dunkel, defaults=d)

    w, h = CANVAS
    rand_x, rand_y = d.title.safe * w, d.title.safe * h
    x0, y0, x1, y1 = info["box"]
    assert (x0, y0) >= (rand_x, rand_y)
    assert (x1, y1) <= (w - rand_x, h - rand_y)
    assert info["schrift_faktor"] == 1.0


def test_eine_zu_lange_ueberschrift_wird_verkleinert_statt_beschnitten(tmp_path,
                                                                       dunkel):
    """Verkleinert wird bis 0,7x, danach folgt eine Warnung.

    Abgeschnittener Text ist das eine Ergebnis, das nie herauskommen darf —
    auch nicht bei einer Ueberschrift, die in keiner Groesse vernuenftig
    aussieht. Deshalb bleibt die Tintenflaeche in jedem Fall innerhalb der
    Leinwand.
    """
    lang = _folie(title="Kopenhagen Malmoe Stockholm Goeteborg und Aarhus",
                  subtitle="Drei Wochen, vier Staedte und ein langer Untertitel")
    out, info = _backen(tmp_path, lang, dunkel)

    assert info["schrift_faktor"] <= 0.70
    assert any("Safe Area" in w for w in info["warnungen"])
    x0, y0, x1, y1 = info["box"]
    with Image.open(out) as bild:
        breite, hoehe = bild.size
    assert (x0, y0) >= (0, 0) and (x1, y1) <= (breite, hoehe)


def test_eine_kurze_ueberschrift_wird_nicht_verkleinert(tmp_path, dunkel):
    """Gegenprobe: ohne Ueberlauf bleibt es bei 1,0x. Ein Test, der eine
    dauerhaft verkleinerte Folie nicht bemerkt, prueft die Regel nicht."""
    _out, info = _backen(tmp_path, _folie(title="Ka", subtitle=None), dunkel)
    assert info["schrift_faktor"] == 1.0
    assert not info["warnungen"]


# --------------------------------------------------------------------------
# T8 — fehlende Schrift
# --------------------------------------------------------------------------

def test_ohne_auffindbare_schrift_nennt_die_meldung_den_installationsbefehl(
        monkeypatch):
    """Kein Traceback, sondern ein kopierbarer Befehl — dieselbe Zusage wie bei
    jedem Werkzeug in ``doctor``."""
    from slideshow import titles

    monkeypatch.delenv("SLIDESHOW_FONT", raising=False)
    monkeypatch.setattr(titles, "_FONT_CANDIDATES",
                        {k: [] for k in titles._FONT_CANDIDATES})

    with pytest.raises(SlideshowError) as exc:
        find_font()
    text = str(exc.value)
    assert "apt install" in text or "SLIDESHOW_FONT" in text
    assert "Geprueft" in text


def test_eine_falsche_schriftangabe_nennt_ihre_quelle(tmp_path, monkeypatch):
    """Zwei Wege fuehren zur Schrift; die Meldung muss sagen, welcher gescheitert
    ist — sonst sucht man in der Projektdatei nach einer Umgebungsvariablen."""
    monkeypatch.setenv("SLIDESHOW_FONT", str(tmp_path / "gibtsnicht.ttf"))
    with pytest.raises(SlideshowError) as exc:
        find_font()
    assert "SLIDESHOW_FONT" in str(exc.value)

    monkeypatch.delenv("SLIDESHOW_FONT")
    with pytest.raises(SlideshowError) as exc:
        find_font(str(tmp_path / "auch-nicht.ttf"))
    assert "defaults.title.font" in str(exc.value)


# --------------------------------------------------------------------------
# T2 — Determinismus
# --------------------------------------------------------------------------

def test_zweimal_erzeugt_ergibt_eine_bitgleiche_datei(tmp_path, hell):
    """Ohne das traegt der Cache nicht: der Segment-Cache haengt am Inhaltshash
    des Assets, und ein wackelndes Byte rendert den halben Film neu.

    Der helle Hintergrund ist Absicht — hier laeuft die Kontrastschleife
    tatsaechlich mehrere Schritte, und genau die muss reproduzierbar sein.
    """
    seg = _folie()
    erste, info_a = _backen(tmp_path, seg, hell, name="a.jpg")
    zweite, info_b = _backen(tmp_path, seg, hell, name="b.jpg")

    assert erste.read_bytes() == zweite.read_bytes()
    assert info_a == info_b


# --------------------------------------------------------------------------
# Zeichenvorrat
# --------------------------------------------------------------------------

def test_umlaute_und_mittelpunkt_laufen_durch(tmp_path, dunkel):
    """Der Grund fuer Pillow statt ``drawtext`` (Entscheidung 1a): kein
    Escaping, keine plattformabhaengigen Fallen mit Doppelpunkten.

    Geprueft wird nicht nur, dass es nicht wirft — die Zeichen muessen auch in
    den Pixeln ankommen. Waeren sie still weggefallen, saehe die Folie aus wie
    die ASCII-Fassung.
    """
    umlaut, info = _backen(tmp_path, _folie("Malmö", "Tag 11 · 24. Juli"),
                           dunkel, name="umlaut.jpg")
    ascii_fassung, _ = _backen(tmp_path, _folie("Malmo", "Tag 11 - 24. Juli"),
                               dunkel, name="ascii.jpg")

    assert not info["warnungen"]
    assert umlaut.read_bytes() != ascii_fassung.read_bytes()


def test_eine_folie_ohne_zweite_zeile_ist_kein_sonderfall(tmp_path, dunkel):
    _out, info = _backen(tmp_path, _folie("Malmoe", None), dunkel)
    assert not info["warnungen"]
    assert info["kontrast"] >= Defaults().title.min_contrast


# --------------------------------------------------------------------------
# Hintergrund
# --------------------------------------------------------------------------

def test_fehlender_hintergrund_ergibt_eine_schwarzflaeche(tmp_path):
    """``_titel_hintergrund`` hat den fehlenden Pfad bereits gemeldet; hier
    darf es deshalb keinen zweiten Abbruch geben, nur ein brauchbares Bild."""
    out, info = _backen(tmp_path, _folie(bg="cache/weg.jpg"), None)

    with Image.open(out) as bild:
        assert bild.convert("RGB").getpixel((2, 2)) == (0, 0, 0)
    assert info["abdunklung"] == 1.0


def test_ein_unlesbarer_hintergrund_wird_gemeldet_statt_zu_werfen(tmp_path):
    """Eine kaputte Datei ist kein Grund, den ganzen Renderlauf abzubrechen —
    die Folie entsteht, und die Warnung landet im Bericht."""
    kaputt = tmp_path / "kaputt.jpg"
    kaputt.write_bytes(b"kein bild")
    _out, info = _backen(tmp_path, _folie(bg="cache/img_005.jpg"), kaputt)

    assert any("nicht lesbar" in w for w in info["warnungen"])


def test_eine_helle_stelle_unter_dem_text_zieht_die_abdunklung_nach(tmp_path):
    """Der Grund, warum am hellen Ende gemessen wird und nicht im Mittel.

    Der Hintergrund ist ueberwiegend dunkel und traegt eine helle Flaeche, die
    unter dem Text liegt — der Sonnenfleck im Bild. Ueber die ganze Textflaeche
    gemittelt bliebe er unauffaellig, und die Ueberschrift stuende dort, wo sie
    hell ist, auf hellem Grund.

    Gegenprobe ist derselbe Hintergrund ohne den Fleck: er darf **nicht**
    nachgedunkelt werden, sonst prueft der Test nur, dass irgendwann
    abgedunkelt wird.
    """
    def kulisse(pfad, *, mit_fleck: bool):
        bild = Image.new("RGB", (800, 500), (24, 26, 30))
        if mit_fleck:
            # Etwa ein Fuenftel der Breite, auf Hoehe der Ueberschrift.
            bild.paste((235, 232, 220), (300, 170, 470, 260))
        bild.save(pfad, format="JPEG", quality=95)
        return pfad

    ohne_blur = Defaults()
    ohne_blur.title.blur = 0.0          # sonst verwischt der Fleck ins Umfeld
    seg = _folie(bg="cache/img_005.jpg")

    _p, mit = _backen(tmp_path, seg, kulisse(tmp_path / "fleck.jpg", mit_fleck=True),
                      name="mit.jpg", defaults=ohne_blur)
    _p, ohne = _backen(tmp_path, seg, kulisse(tmp_path / "glatt.jpg", mit_fleck=False),
                       name="ohne.jpg", defaults=ohne_blur)

    assert ohne["abdunklung"] == ohne_blur.title.darken, \
        "ein durchgehend dunkler Grund braucht keine Nachfuehrung"
    assert mit["abdunklung"] < ohne["abdunklung"], \
        "die helle Stelle unter dem Text muss die Abdunklung nachziehen"
    assert mit["kontrast"] >= ohne_blur.title.min_contrast


def test_der_hintergrund_wird_weichgezeichnet(tmp_path):
    """Der Titelhintergrund muss dieselbe Unschaerfe tragen wie das
    Hochformat-Komposit, sonst wirken die beiden wie zwei verschiedene Filme.

    Gemessen an der Streuung ueber ein hartes Streifenmuster: mit Blur ist sie
    deutlich geringer als ohne.

    Gemessen wird der **Variationskoeffizient** (Streuung durch Mittelwert),
    nicht die Streuung selbst. Die Abdunklung ist eine Multiplikation, sie
    skaliert beide Groessen gleich und faellt im Quotienten heraus. Die blosse
    Streuung taugt hier nicht: das scharfe Streifenmuster traegt hellere Stellen
    unter dem Text, bekommt deshalb eine staerkere Abdunklung — und verliert
    dadurch selbst an Streuung. Der Test maesse dann Blur und Abdunklung
    zugleich und wuerde umso schwaecher, je besser die Kontrastregel arbeitet.
    """
    from PIL import ImageStat

    streifen = Image.new("L", (800, 500))
    streifen.putdata([255 if (i % 800) // 20 % 2 else 0
                      for i in range(800 * 500)])
    quelle = tmp_path / "streifen.jpg"
    streifen.convert("RGB").save(quelle, format="JPEG", quality=95)

    ohne = Defaults()
    ohne.title.blur = 0.0
    seg = _folie(bg="cache/img_005.jpg")
    scharf, _ = _backen(tmp_path, seg, quelle, name="scharf.jpg", defaults=ohne)
    weich, _ = _backen(tmp_path, seg, quelle, name="weich.jpg")

    def variation(pfad):
        with Image.open(pfad) as im:
            st = ImageStat.Stat(im.convert("L"))
        return st.stddev[0] / max(1e-9, st.mean[0])

    assert variation(weich) < variation(scharf) * 0.8
