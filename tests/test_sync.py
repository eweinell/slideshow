"""Abnahmekriterium 8, Ende zu Ende — Sync gegen den *bekannten* Beat-Zeitplan.

    Schnittzeitpunkte liegen exakt auf Framegrenzen; die Abweichung zum
    Beat-Raster ist ueber die volle Laufzeit < 1 Frame (kein akkumulierender
    Drift) — auch bei gemischten ``beat``- und ``free``-Regionen.

Anders als :mod:`tests.test_planner`, das gegen ein *konstruiertes* Raster
prueft, laeuft hier die ganze Kette: echte Audio-Analyse auf dem Klick-Track,
echte Regionenerkennung, echter Planer. Verglichen wird gegen die Beat-Zeiten,
mit denen die Fixture erzeugt wurde — die einzige Wahrheit, die es hier gibt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slideshow.planner import resolve, to_frame, to_time
from slideshow.proc import run

from .conftest import build_project, requires_ffmpeg, true_beat_times

pytestmark = requires_ffmpeg

FPS = 60.0
EIN_FRAME = 1.0 / FPS


@pytest.fixture(scope="module")
def gebaut(tmp_path_factory, images, click_two_songs):
    from slideshow.doctor import probe_capabilities
    from slideshow.paths import Project
    project = Project.open(tmp_path_factory.mktemp("sync") / "proj", create=True)
    project.ensure_dirs()
    manifest, edit, plan, cov = build_project(
        project, images=images, audio=click_two_songs,
        caps=probe_capabilities(deep=False), fps=FPS)
    return {"project": project, "edit": edit, "plan": plan, "manifest": manifest,
            "audio": click_two_songs}


def _echte_beats(audio) -> list[tuple[float, float, list[float]]]:
    """Ground Truth je Track: (Anfang, Ende, Beat-Zeiten).

    Nur innerhalb dieser Spannen gibt es echte Schlaege. Der Klick-Track hat
    einen Ausklang von zwei Sekunden, in den die erkannte Beat-Region
    hineinreicht — dort setzt der Planer das Raster korrekt fort, aber es gibt
    nichts, wogegen man messen koennte.
    """
    return [(t["start"], t["end"], true_beat_times(t)) for t in audio.tracks]


def _abstand_zum_beat(cut: float, tracks) -> float | None:
    for start, ende, beats in tracks:
        if start - 1e-6 <= cut <= ende + 1e-6:
            return min(abs(cut - b) for b in beats)
    return None


def test_schnitte_liegen_auf_echten_beats(gebaut):
    """Jeder Schnitt innerhalb einer Beat-Region trifft einen echten Schlag."""
    plan, audio = gebaut["plan"], gebaut["audio"]
    tracks = _echte_beats(audio)

    geprueft = 0
    for i, slot in enumerate(plan.slots[:-1]):
        region = plan.regions[slot.region_index]
        if region.type != "beat":
            continue
        # Der *letzte* Schnitt einer Beat-Region ist die Regionsgrenze selbst:
        # dort hoert die Musik auf, und das faellt nicht mit einem Schlag
        # zusammen. Geprueft werden Schnitte *innerhalb* der Region — und der
        # Vergleich laeuft in Frames, nicht in Sekunden: eine Region, die bei
        # 16.022 s endet, liegt auf Frame 961 alias 16.0167 s.
        if slot.end_f >= to_frame(region.end, FPS):
            continue
        cut = to_time(slot.end_f, FPS)
        delta = _abstand_zum_beat(cut, tracks)
        if delta is None:
            continue
        assert delta < EIN_FRAME, (
            f"Schnitt {i} bei {cut:.4f} s liegt {delta * 1000:.1f} ms neben dem "
            f"naechsten echten Beat — mehr als ein Frame bei {FPS:g}p")
        geprueft += 1
    assert geprueft >= 3, f"nur {geprueft} Schnitte in Beat-Regionen geprueft"


def test_abweichung_waechst_nicht_zum_ende_hin(gebaut):
    """Der Drift-Nachweis: die Abweichung in der zweiten Haelfte darf nicht
    groesser sein als in der ersten."""
    plan, audio = gebaut["plan"], gebaut["audio"]
    tracks = _echte_beats(audio)

    messwerte: list[tuple[float, float]] = []
    for slot in plan.slots[:-1]:
        region = plan.regions[slot.region_index]
        if region.type != "beat":
            continue
        if slot.end_f >= to_frame(region.end, FPS):
            continue
        cut = to_time(slot.end_f, FPS)
        delta = _abstand_zum_beat(cut, tracks)
        if delta is not None:
            messwerte.append((cut, delta))

    assert len(messwerte) >= 4
    mitte = len(messwerte) // 2
    frueh = max(d for _t, d in messwerte[:mitte])
    spaet = max(d for _t, d in messwerte[mitte:])
    assert spaet < EIN_FRAME
    assert spaet <= frueh + EIN_FRAME / 4, (
        f"die Abweichung waechst von {frueh * 1000:.1f} ms auf {spaet * 1000:.1f} ms "
        f"— das ist akkumulierender Drift")


def test_timeline_ist_lueckenlos_ueber_regionsgrenzen(gebaut):
    plan = gebaut["plan"]
    segmente = resolve(plan)
    assert segmente[0].start_f == 0
    for a, b in zip(segmente, segmente[1:]):
        assert a.end_f == b.start_f, "Luecke oder Ueberlappung an einer Segmentgrenze"
    assert segmente[-1].end_f == plan.total_frames


def test_kein_segment_ist_entartet(gebaut):
    """An Regionsgrenzen entstanden frueher 1-Frame-Segmente durch Rundung."""
    plan = gebaut["plan"]
    for s in resolve(plan):
        assert s.frames >= 2, f"Segment {s.index} hat nur {s.frames} Frames"


def test_freie_region_schliesst_ohne_sprung_an(gebaut):
    """Kriterium 9, Timing-Anteil: die Bilder in der Stille fuellen die Luecke
    exakt, ohne Sprung beim Wiedereinsetzen der Musik."""
    plan = gebaut["plan"]
    for i, region in enumerate(plan.regions):
        if region.type != "free":
            continue
        mitglieder = [s for s in plan.slots if s.region_index == i]
        assert mitglieder, "free-Region ohne Bilder"
        anfang = to_time(mitglieder[0].start_f, FPS)
        ende = to_time(mitglieder[-1].end_f, FPS)
        assert abs(anfang - region.start) < EIN_FRAME
        assert abs(ende - region.end) < EIN_FRAME


@pytest.mark.slow
def test_keyframes_liegen_auf_den_segmentgrenzen(gebaut):
    """8.1/8.4: Segmentanfang ist Keyframe — Voraussetzung fuer verlustfreies
    Concat. Nachgewiesen am fertigen Master per ffprobe."""
    from slideshow.doctor import probe_capabilities
    from slideshow.render import render

    project, edit, plan = gebaut["project"], gebaut["edit"], gebaut["plan"]
    out = project.out / "sync.mp4"
    render(project, edit, plan, caps=probe_capabilities(deep=False),
           manifest=gebaut["manifest"], out=out, codec="libx264", jobs_limit=2)

    res = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
               "-of", "csv=p=0", str(out)])
    keyframes = sorted(float(x) for x in res.stdout.replace(",", " ").split()
                       if x.strip() and x.strip() != "N/A")
    assert keyframes, "der Master hat keine Keyframes"

    grenzen = [to_time(s.start_f, FPS) for s in resolve(plan)]
    for t in grenzen:
        naechster = min(abs(t - k) for k in keyframes)
        assert naechster < EIN_FRAME, (
            f"Segmentgrenze bei {t:.4f} s hat keinen Keyframe (naechster "
            f"{naechster * 1000:.1f} ms entfernt)")
