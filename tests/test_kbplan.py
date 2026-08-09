"""Inhaltsabhaengige Kamerafahrt — der Planer
(``docs/briefing-kenburns-inhaltsabhaengig.md``, Abnahmekriterien A1 bis A3b,
A7 und A9).

Geprueft wird die **Rechnung**, nicht das Aussehen: dass keine Schutzbox
angeschnitten wird, dass das geplante Fenster auch das sichtbare ist, dass
zweimal Bauen dasselbe ergibt und dass ein eingefuegtes Kapitel die Bewegung
der uebrigen Bilder nicht anfasst. Ob die Kamera *schoen* faehrt, entscheidet
A11 — ein realer Lauf, angesehen, und nicht automatisierbar.

Die Fixtures sind erfundene ``vision.yaml``-Eintraege. Das Verfahren braucht
keine echten Bilder: es rechnet auf Koordinaten, und die stehen in der Datei.
"""

from __future__ import annotations

import random

import pytest

from slideshow.kbplan import (VARIETY, Signatur, klemmung_haelt, plan_kb,
                              schutz_haelt, zoom_noetig)
from slideshow.kenburns import KBMotion, sanity_check
from slideshow.models import (Chapter, Defaults, KBDefaults, VisionDoc,
                              VisionEntry)

from .test_titles import _beat_region, _manifest

KB = KBDefaults()
FPS = 60.0


def _bauen(manifest, regions, chapters, *, vision=None, overrides=None,
           defaults=None, dauer: float = 90.0):
    """Bauen wie ``slideshow build``, nur ohne Dateien.

    Eigener Helfer statt des Pendants in ``test_titles``: dieser hier reicht
    ``vision`` und ``overrides`` durch, und der dortige soll davon nichts
    wissen muessen.
    """
    from slideshow.build import build_edit_list
    from slideshow.models import BeatMap

    beatmap = BeatMap(audio={"file": manifest.audio.file, "duration": dauer},
                      regions=regions)
    return build_edit_list(None, manifest, beatmap, defaults=defaults or Defaults(),
                           fps=FPS, size=(1280, 720), chapters=chapters,
                           overrides=overrides, vision=vision)


def _spec_teile(spec):
    z0, z1 = spec.z
    return z0, z1, (spec.c[0], spec.c[1]), (spec.c[2], spec.c[3])


def _mischung(n: int = 45, *, seed: int = 7) -> list[VisionEntry]:
    """Ein realistischer Materialmix — so, wie ein Urlaubsfilm aussieht.

    Nicht 45-mal dieselbe Szene: A2 ist eine Zusage ueber einen *Lauf*, und ein
    Lauf ist gemischt. Der homogene Fall steht in seinem eigenen Test, wo er
    hingehoert.
    """
    rng = random.Random(seed)
    klassen = (["landscape_wide"] * 14 + ["portrait_person"] * 8 + ["group"] * 5
               + ["architecture"] * 5 + ["detail_macro"] * 3 + ["action"] * 3
               + ["interior"] * 4 + ["other"] * 3)[:n]
    return [VisionEntry(scene=s, axis=rng.choice(["horizontal", "vertical", "none"]),
                        detail=rng.uniform(0.2, 0.7), conf=0.9,
                        focus=(rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)))
            for s in klassen]


# --------------------------------------------------------------------------
# A1 / A1b — Schutz und Klemmung
# --------------------------------------------------------------------------

def test_schutzboxen_bleiben_ueber_die_ganze_fahrt_im_bild():
    """A1 als Eigenschaftstest, gegen die **geschriebenen** Werte.

    Geprueft wird ``spec``, nicht der interne Zustand des Planers: was in
    ``edit.yaml`` landet, ist die Bewegung. Der Unterschied ist nicht
    akademisch — das Runden auf vier Nachkommastellen hat genau hier einmal
    einen Schwenk ueber die Klemmgrenze geschoben.
    """
    rng = random.Random(11)
    for i in range(400):
        x0 = rng.uniform(0.0, 0.6)
        y0 = rng.uniform(0.0, 0.6)
        box = (x0, y0, x0 + rng.uniform(0.12, 0.35), y0 + rng.uniform(0.12, 0.35))
        if box[2] > 1.0 or box[3] > 1.0:
            continue
        e = VisionEntry(scene=rng.choice(["portrait_person", "group", "interior",
                                          "landscape_wide", "action"]),
                        axis=rng.choice(["horizontal", "vertical", "none"]),
                        focus=(rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)),
                        protect=[box], detail=rng.uniform(0.0, 1.0), conf=0.9)
        erg = plan_kb(e, key=f"cache/img_{i:04d}.jpg",
                      duration=rng.uniform(2.0, 12.0), defaults=KB)
        assert erg is not None
        z0, z1, c0, c1 = _spec_teile(erg.spec)
        assert schutz_haelt(e.protect, z0, z1, c0, c1), (e.protect, erg.spec)


def test_das_geplante_fenster_ist_auch_das_sichtbare():
    """A1b: ``|c_i - 0,5| <= 0,5 - 1/(2 z_i)`` an beiden Enden.

    Ohne dieses Kriterium prueft A1 ein Rechteck, das der Filter nie zeigt —
    ``zoompan`` klemmt die Fensterposition an den Bildrand, und ein selbst
    geschriebenes ``c:`` umgeht den Deckel aus ``_pan`` vollstaendig.
    """
    rng = random.Random(23)
    for i, e in enumerate(_mischung(60, seed=3)):
        erg = plan_kb(e, key=f"cache/img_{i:04d}.jpg",
                      duration=rng.uniform(2.0, 12.0), defaults=KB)
        if erg is None:
            continue
        z0, z1, c0, c1 = _spec_teile(erg.spec)
        assert klemmung_haelt(z0, z1, c0, c1), erg.spec


def test_ein_weiter_schwenk_verlangt_zoom_und_wird_sonst_gekuerzt():
    """Der Zielkonflikt aus 0.2, unverstellt.

    Ein Motiv 0,12 von der Mitte verlangt ``z >= 1,316``; ``zoom_total`` gibt
    1,30 her. Der Planer kuerzt also — und das ist eine Entscheidung, keine
    Rundung: sie steht im Bericht.
    """
    assert zoom_noetig(0.12) == pytest.approx(1.3158, abs=1e-3)
    e = VisionEntry(scene="portrait_person", focus=(0.38, 0.47), detail=0.1, conf=0.9)
    erg = plan_kb(e, key="cache/weit.jpg", duration=12.0, defaults=KB, variety=1)
    z0, z1, c0, c1 = _spec_teile(erg.spec)
    assert klemmung_haelt(z0, z1, c0, c1)
    # Der Weg zum Motiv ist 0,124; die Klemmung bei z_max = 1,30 gibt 0,115.
    assert max(abs(c1[0] - 0.5), abs(c0[0] - 0.5)) < 0.124


# --------------------------------------------------------------------------
# A2 — Abwechslung, statistisch (siehe E10)
# --------------------------------------------------------------------------

def _lauf(praefix: str, n: int = 45):
    ergebnisse = [plan_kb(e, key=f"{praefix}{i:04d}.jpg", duration=4.0 + i % 5,
                          defaults=KB)
                  for i, e in enumerate(_mischung(n))]
    return [e for e in ergebnisse if e is not None]


def test_richtungen_und_signaturen_verteilen_sich():
    """Die beiden robusten Haelften von A2 — sie gelten fuer **jeden** Lauf.

    Gemessen ueber acht unabhaengige Kennungsmengen: mindestens sieben der acht
    Schwenkrichtungen kamen in jeder vor, und der groesste Signaturanteil lag
    nie ueber 15 %.
    """
    for s in range(8):
        ergebnisse = _lauf(f"cache/set{s}_img_")
        n = len(ergebnisse)
        assert n >= 40
        assert len({e.signatur.richtung for e in ergebnisse} - {None}) >= 6
        haeufigste = max(sum(1 for e in ergebnisse if str(e.signatur) == sig)
                         for sig in {str(e.signatur) for e in ergebnisse})
        assert haeufigste / n <= 0.30


def test_die_zoomrichtung_bleibt_im_mittel_ausgeglichen():
    """Der dritte Teil von A2 — und der einzige, der wirklich *statistisch* ist.

    Er ist es aus einem inhaltlichen Grund: die Regeltabelle schreibt vier
    Klassen eine Zoomrichtung vor (Portraet und Innenraum fahren heran, Gruppe
    und Bewegung heraus). Der Anteil eines Laufs haengt damit an seiner
    Klassenmischung, und der Rest an der Streuung des Kennungs-Hashes ueber
    ausgerechnet die Dateinamen dieses Projekts.

    Gemessen ueber acht Mengen: 0,52 bis 0,62 im gepoolten Mittel — **aber eine
    einzelne Menge erreichte 0,71**. Deshalb prueft dieser Test die gepoolte
    Zusage scharf und die einzelne Menge nur gegen eine lockere Schranke. Wer
    im echten Lauf einen Ausreisser sieht, hat keinen Fehler gefunden, sondern
    genau diese Streuung; `--variety` ist dort der Hebel.
    """
    anteile = []
    for s in range(8):
        ergebnisse = _lauf(f"cache/set{s}_img_")
        hinein = sum(1 for e in ergebnisse if e.signatur.zoom_in)
        anteile.append(max(hinein, len(ergebnisse) - hinein) / len(ergebnisse))
    assert sum(anteile) / len(anteile) <= 0.65
    assert max(anteile) <= 0.75


def test_die_wahl_haengt_am_bild_und_nicht_am_platz():
    """Dieselbe Kennung, dieselbe Bewegung — und verschiedene Kennungen
    verteilen sich ueber die Kandidatenmenge."""
    e = VisionEntry(scene="landscape_wide", axis="horizontal", detail=0.3, conf=0.9)
    a = plan_kb(e, key="cache/img_0007.jpg", duration=5.0, defaults=KB)
    b = plan_kb(e, key="cache/img_0007.jpg", duration=5.0, defaults=KB)
    assert a.spec == b.spec

    # Ueber viele Kennungen muessen alle vier Kandidaten vorkommen — sonst
    # waehlt der Hash gar nicht, sondern die Sortierung.
    gesehen = {str(plan_kb(e, key=f"cache/img_{i:04d}.jpg", duration=5.0,
                           defaults=KB).signatur) for i in range(60)}
    assert len(gesehen) == a.kandidaten == VARIETY


def test_variety_eins_ist_reine_passung():
    """``--variety 1`` schaltet die Abwechslung ab: alle Bilder derselben
    Klasse bekommen dann dieselbe bestpassende Bewegung."""
    e = VisionEntry(scene="landscape_wide", axis="horizontal", detail=0.3, conf=0.9)
    specs = {tuple(plan_kb(e, key=f"cache/img_{i}.jpg", duration=5.0, defaults=KB,
                           variety=1).spec.c) for i in range(20)}
    assert len(specs) == 1


# --------------------------------------------------------------------------
# Regeln aus 6.1 und 6.2
# --------------------------------------------------------------------------

def test_hochformat_komposit_bekommt_keinen_horizontalen_schwenk():
    """Regel 6.2: ein horizontaler Schwenk faehrt in die unscharfen Balken.

    Diagonalen fallen mit — sie haben einen horizontalen Anteil, und der ist
    genau das Problem.
    """
    e = VisionEntry(scene="landscape_wide", axis="horizontal", detail=0.3, conf=0.9)
    for i in range(30):
        erg = plan_kb(e, key=f"cache/hoch_{i}.jpg", duration=5.0, defaults=KB,
                      portrait_komposit=True)
        assert erg.signatur.richtung in (None, 1, 3), erg.signatur


def test_bei_portrait_crop_gilt_die_regel_nicht():
    """``crop`` schneidet formatfuellend — dort ist das Bild ein normales
    Vollbild ohne Balken, und ein horizontaler Schwenk ist erlaubt."""
    e = VisionEntry(scene="landscape_wide", axis="horizontal", detail=0.3, conf=0.9)
    richtungen = {plan_kb(e, key=f"cache/quer_{i}.jpg", duration=5.0, defaults=KB,
                          portrait_komposit=False).signatur.richtung
                  for i in range(30)}
    assert richtungen - {None, 1, 3}


def test_ein_makro_wird_nicht_ueberzoomt():
    e = VisionEntry(scene="detail_macro", detail=0.9, conf=0.9)
    erg = plan_kb(e, key="cache/makro.jpg", duration=10.0, defaults=KB)
    z0, z1 = erg.spec.z
    assert max(z0, z1) <= 1.08 + 1e-6
    assert erg.signatur.richtung is None


def test_eine_hohe_detaildichte_deckelt_den_zoom():
    fein = plan_kb(VisionEntry(scene="landscape_wide", detail=0.95, conf=0.9),
                   key="cache/fein.jpg", duration=10.0, defaults=KB)
    glatt = plan_kb(VisionEntry(scene="landscape_wide", detail=0.05, conf=0.9),
                    key="cache/fein.jpg", duration=10.0, defaults=KB)
    assert max(fein.spec.z) < max(glatt.spec.z)


def test_ein_dokument_steht_still_und_darf_das():
    """A7: ``sanity_check`` schweigt zu jeder erzeugten Bewegung — ausser bei
    ``document``, wo der Stillstand die Absicht ist."""
    erg = plan_kb(VisionEntry(scene="document", conf=0.9), key="cache/doc.jpg",
                  duration=5.0, defaults=KB)
    assert erg.spec.z == (1.0, 1.0)
    assert sanity_check(KBMotion(z0=1.0, z1=1.0, c0=(0.5, 0.5), c1=(0.5, 0.5)))


def test_sanity_check_schweigt_zu_allen_uebrigen_bewegungen():
    for i, e in enumerate(_mischung(45)):
        erg = plan_kb(e, key=f"cache/img_{i:04d}.jpg", duration=3.0 + i % 7,
                      defaults=KB)
        if erg is None or e.scene == "document":
            continue
        z0, z1, c0, c1 = _spec_teile(erg.spec)
        assert not sanity_check(KBMotion(z0=z0, z1=z1, c0=c0, c1=c1)), (e.scene,
                                                                        erg.spec)


# --------------------------------------------------------------------------
# E9 — niedrige Konfidenz und `other`
# --------------------------------------------------------------------------

def test_ohne_klasse_und_ohne_schutz_bleibt_es_bei_der_rotation():
    """``other`` ohne Schutzbox bekommt gar kein ``kb:`` — die heutige
    Kennungs-Rotation ist dort schon richtig."""
    assert plan_kb(VisionEntry(scene="other", conf=0.9), key="cache/x.jpg",
                   duration=5.0, defaults=KB) is None


def test_niedrige_konfidenz_gilt_wie_other_aber_der_schutz_bleibt():
    """E9: eine unsichere Klassifikation ist ein schwaches Signal fuer die
    Passung und immer noch besser als nichts fuer den Schutz."""
    box = [(0.02, 0.02, 0.30, 0.40)]
    unsicher = VisionEntry(scene="portrait_person", conf=0.2, protect=box,
                           focus=(0.16, 0.21))
    erg = plan_kb(unsicher, key="cache/unsicher.jpg", duration=8.0, defaults=KB)
    assert erg is not None and erg.schutz_erzwungen
    z0, z1, c0, c1 = _spec_teile(erg.spec)
    assert schutz_haelt(box, z0, z1, c0, c1)


def test_eine_randstaendige_schutzbox_zwingt_den_zoom_zurueck():
    """Der Boden der Klemmkette: reiner, mittiger Zoom — und der geht immer auf.

    Eine Box am Bildrand passt zwar in ein Fenster ihrer Groesse, liegt aber
    in einem *mittigen* Fenster erst bei kleinerem Zoom. Genau diesen
    Unterschied deckt Schritt 1 der Kette nicht ab.
    """
    box = [(0.0, 0.0, 0.25, 0.25)]
    e = VisionEntry(scene="landscape_wide", axis="horizontal", protect=box,
                    detail=0.1, conf=0.9)
    erg = plan_kb(e, key="cache/rand.jpg", duration=10.0, defaults=KB)
    z0, z1, c0, c1 = _spec_teile(erg.spec)
    assert schutz_haelt(box, z0, z1, c0, c1)
    assert max(z0, z1) == pytest.approx(1.0, abs=0.01)


# --------------------------------------------------------------------------
# Einbettung in `build` — A3b, A8, A9
# --------------------------------------------------------------------------

def _vision(n: int = 12, **felder) -> VisionDoc:
    mix = _mischung(n, seed=5)
    return VisionDoc(version=1, model="claude-opus-5", prompt=1,
                     images={f"cache/img_{i:03d}.jpg": e for i, e in enumerate(mix)},
                     **felder)


def _kb_je_bild(edit) -> dict[str, object]:
    return {s.src: s.kb for s in edit.segments
            if s.type == "still" and s.kb is not None}


def test_build_schreibt_die_fahrt_in_die_edit_list():
    edit, _plan, _cov = _bauen(_manifest(12), [_beat_region()], [],
                               vision=_vision(12))
    gesetzt = _kb_je_bild(edit)
    assert len(gesetzt) >= 8
    for spec in gesetzt.values():
        z0, z1, c0, c1 = _spec_teile(spec)
        assert klemmung_haelt(z0, z1, c0, c1)


def test_zweimal_bauen_ergibt_dieselbe_datei():
    """A3 — Determinismus. Ab ``vision.yaml`` ist die ganze Kette rein
    deterministisch; die Modellantwort steht ja fest in der Datei."""
    from slideshow.models import dump_edit_yaml

    doc = _vision(12)
    a, _p, _c = _bauen(_manifest(12), [_beat_region()], [], vision=doc)
    b, _p, _c = _bauen(_manifest(12), [_beat_region()], [], vision=doc)
    assert dump_edit_yaml(a) == dump_edit_yaml(b)


def test_ein_eingefuegtes_kapitel_laesst_die_uebrigen_bewegungen_unberuehrt():
    """A3b — die Zusage aus 2a401f9, und dieses Briefing darf sie nicht brechen.

    Genau daran ist die gierige Sequenzauswahl aus Rev. 1 gescheitert: sie
    machte die Bewegung eines Bildes von seinen Nachbarn abhaengig, und ein
    eingefuegtes Kapitel haette den Rest des Films neu gerendert.
    """
    doc = _vision(12)
    ohne, plan_ohne, _c = _bauen(_manifest(12), [_beat_region()], [], vision=doc)
    mit, plan_mit, _c = _bauen(_manifest(12), [_beat_region()],
                               [Chapter(before="img_006", title="Bergen")],
                               vision=doc)

    vorher, nachher = _kb_je_bild(ohne), _kb_je_bild(mit)
    gemeinsam = set(vorher) & set(nachher)
    assert len(gemeinsam) >= 8

    # Verglichen wird nur, wo die **sichtbare Dauer** gleich geblieben ist.
    #
    # Das ist keine Abschwaechung des Kriteriums, sondern seine genaue Fassung:
    # der Zoombetrag leitet sich seit je aus der Dauer ab (Architektur-Invariante
    # 5), und eine eingefuegte Folie belegt einen Slot — das letzte Bild wird
    # dadurch anders gestreckt. Diese Abhaengigkeit hat die heutige Rotation
    # genauso und ist aelter als dieses Briefing. Was A3b zusagt und was hier
    # geprueft wird, ist die **Wahl**: sie darf nicht von der Position abhaengen.
    dauern_ohne = _dauern(plan_ohne)
    dauern_mit = _dauern(plan_mit)
    gleich_lang = [src for src in gemeinsam
                   if dauern_ohne.get(src) == pytest.approx(dauern_mit.get(src))]
    assert len(gleich_lang) >= 8

    # Das Bild direkt hinter der Folie ist die eine erlaubte Ausnahme: dort
    # uebernimmt die Fokusblende (Rangfolge 6.4).
    abweichend = [src for src in gleich_lang if vorher[src] != nachher[src]]
    assert abweichend in ([], ["cache/img_006.jpg"])


def _dauern(plan) -> dict[str, float]:
    from slideshow.build import _sichtbare_dauer

    return {s.intent.src: _sichtbare_dauer(plan, i)
            for i, s in enumerate(plan.slots) if s.intent.title is None}


def test_die_fokusblende_ueberlebt_den_planer():
    """A9 — der stille Fehler aus 0.3.

    Ein ``kb:`` am Bild *nach* einer Titelfolie schaltet die gekoppelte Fahrt
    ab; der Schaerfezug wird dann zum Schnitt zwischen zwei aehnlichen Bildern.
    Der Film rendert trotzdem — er sieht nur schlechter aus, und genau deshalb
    braucht es diesen Test.
    """
    edit, plan, _cov = _bauen(_manifest(12), [_beat_region()],
                              [Chapter(before="img_006", title="Bergen")],
                              vision=_vision(12))
    paare = [(i, s) for i, s in enumerate(plan.slots) if s.intent.title is not None]
    assert paare, "kein Titel im Plan — der Test prueft sonst nichts"
    for i, slot in paare:
        folge = plan.slots[i + 1]
        if slot.intent.title.bg != folge.intent.src:
            continue                       # keine Fokusblende
        assert slot.intent.kb is not None and folge.intent.kb is not None
        # Die gekoppelte Fahrt: die Folie endet, wo das Bild beginnt.
        assert slot.intent.kb.z[1] == pytest.approx(folge.intent.kb.z[0])
        assert slot.intent.kb.c[2:] == folge.intent.kb.c[:2]


def test_ein_kb_von_hand_gewinnt_gegen_den_planer():
    """A8 — Rang 1 der Rangfolge aus 6.4.

    ``motion: none`` ist genau so ein ``kb:``, nur bequemer geschrieben: wer
    ein Bild ausdruecklich stillstellt, bekommt keinen Schwenk untergeschoben.
    """
    from slideshow.models import MediaOverride, Overrides

    ov = Overrides(media={"img_003": MediaOverride(motion="none")})
    edit, _plan, _cov = _bauen(_manifest(12), [_beat_region()], [],
                               vision=_vision(12), overrides=ov)
    still = next(s for s in edit.segments
                 if s.type == "still" and s.src == "cache/img_003.jpg")
    assert still.kb.z == (1.0, 1.0)
    assert still.kb.c == (0.5, 0.5, 0.5, 0.5)


def test_ohne_analyse_baut_alles_wie_bisher():
    """Der Ausfallpfad aus Abschnitt 8, an seinem groebsten Ende."""
    edit, _plan, _cov = _bauen(_manifest(12), [_beat_region()], [], vision=None)
    assert not _kb_je_bild(edit)


def test_fehlende_eintraege_fallen_einzeln_auf_die_rotation_zurueck():
    doc = _vision(12)
    for weg in ("cache/img_002.jpg", "cache/img_005.jpg"):
        doc.images.pop(weg)
    edit, plan, _cov = _bauen(_manifest(12), [_beat_region()], [], vision=doc)
    gesetzt = _kb_je_bild(edit)
    assert "cache/img_002.jpg" not in gesetzt
    assert any("2 ohne Analyse" in w for w in plan.warnings), plan.warnings


def test_signatur_ist_lesbar():
    assert str(Signatur(zoom_in=True, richtung=2, weite="L")) == "ein/2/L"
    assert str(Signatur(zoom_in=False, richtung=None, weite="-")) == "aus/-/-"
    assert VARIETY >= 1
