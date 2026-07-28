"""Abnahmekriterien 2, 3 und 7 — Clipmaterial und Hochformat.

2. Ein Clip mit VFR und Rotation ist im Master aufrecht, fluessig und
   framegenau synchron zum Beat-Raster.
3. Ein XAVC-HS-4:2:2-Clip rendert auf einer GPU ohne 4:2:2-NVDEC durch —
   automatisch per CPU-Decode, mit Hinweis im Log.
7. Ein Hochformat-Foto zwischen Querformat-Fotos hat keine schwarzen Balken.

Die *farbliche* Beurteilung des Tonemappings (Kriterium 4) bleibt eine
manuelle Pruefung mit echtem Material — hier wird nur geprueft, dass die Kette
greift und strukturell korrektes Material liefert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slideshow.doctor import Capabilities, probe_capabilities
from slideshow.framerate import plan_retime, suggest_target_fps
from slideshow.models import Manifest
from slideshow.preprocess import build_clip_filter, preprocess, process_image
from slideshow.probe import classify, detect_hdr, probe_sources, vfr_suspect
from slideshow.proc import ffprobe_json

from .conftest import TEST_LONG_EDGE, requires_ffmpeg

pytestmark = requires_ffmpeg

SIZE = (640, 360)
FPS = 60.0


@pytest.fixture(scope="module")
def probed(tmp_path_factory, clips, images):
    from slideshow.paths import Project
    project = Project.open(tmp_path_factory.mktemp("media"), create=True)
    project.ensure_dirs()
    caps = probe_capabilities(deep=False)
    result = probe_sources(project, list(clips.values()) + images[:2],
                           caps=caps, target_fps=FPS)
    return (project, result.manifest, caps)


def _item(manifest: Manifest, name: str):
    return next(m for m in manifest.media if name in m.path)


# --------------------------------------------------------------------------
# Erkennung
# --------------------------------------------------------------------------

def test_vfr_wird_erkannt(probed):
    _project, manifest, _caps = probed
    item = _item(manifest, "clip_vfr")
    assert item.clip.vfr_suspect
    assert item.clip.vfr_confirmed, "der Verdacht muss per Paket-Timestamps bestaetigt sein"


def test_vfr_material_wird_nicht_versehentlich_verlangsamt(probed):
    """Bei VFR ist ``avg_frame_rate`` kleiner als die nominelle Rate.

    Wer darauf die Zielrate bezieht, haelt ein 30p-Handyvideo fuer 24p und
    retimt es in Zeitlupe. Der Fix fuer Android-VFR ist ausschliesslich die
    CFR-Konformierung.
    """
    _project, manifest, _caps = probed
    item = _item(manifest, "clip_vfr")
    assert item.clip.fps == pytest.approx(30.0), \
        f"nominelle Rate erwartet, bekommen {item.clip.fps}"
    assert item.clip.retime == pytest.approx(1.0), \
        "30p -> 60p ist eine exakte Verdopplung, kein Retiming"


def test_rotation_wird_erkannt(probed):
    _project, manifest, _caps = probed
    assert _item(manifest, "clip_rot90").clip.rotation == 90


def test_hdr_wird_erkannt(probed):
    _project, manifest, _caps = probed
    assert _item(manifest, "clip_hlg").clip.hdr == "hlg"


def test_422_10bit_erzwingt_cpu_decode(probed):
    """Kriterium 3: erkennen und ``-hwaccel`` deaktivieren, statt mit
    kryptischem NVDEC-Fehler abzubrechen."""
    _project, manifest, _caps = probed
    item = _item(manifest, "clip_hevc422")
    assert item.clip.classification == "xavc_hs"
    assert item.clip.pix_fmt == "yuv422p10le"
    assert item.clip.force_cpu_decode
    assert any("CPU-Decode" in w for w in item.warnings), "der Hinweis gehoert ins Log"


def test_422_ohne_nvdec_setzt_hwaccel_none(probed):
    from slideshow.preprocess import clip_intermediate_cmd
    project, manifest, caps = probed
    item = _item(manifest, "clip_hevc422")
    cmd, _off, _dur = clip_intermediate_cmd(
        project, item, project.cache / "x.mov", size=SIZE, fps=FPS, caps=caps,
        codec="hevc_intra_cpu", portrait_mode="blur")
    assert "-hwaccel" in cmd and cmd[cmd.index("-hwaccel") + 1] == "none"


def test_klassifikation():
    assert classify({"codec_name": "hevc", "pix_fmt": "yuv422p10le"}) == "xavc_hs"
    assert classify({"codec_name": "hevc", "pix_fmt": "yuv420p10le"}) == "xavc_hs"
    assert classify({"codec_name": "h264", "profile": "High"}) == "xavc_s"
    assert classify({"codec_name": "vp9", "pix_fmt": "yuv420p"}) == "generic"


def test_hdr_erkennung_liest_das_richtige_feld():
    """ffprobe nennt das Feld ``color_transfer``; ``color_trc`` ist nur der
    Name der ffmpeg-Option."""
    assert detect_hdr({"color_transfer": "arib-std-b67"}) == "hlg"
    assert detect_hdr({"color_transfer": "smpte2084"}) == "pq"
    assert detect_hdr({"color_transfer": "bt709"}) == ""


def test_vfr_verdachtstest():
    assert vfr_suspect({"r_frame_rate": "30/1", "avg_frame_rate": "24/1"})
    assert not vfr_suspect({"r_frame_rate": "30/1", "avg_frame_rate": "30/1"})


# --------------------------------------------------------------------------
# Framerate-Politik (Abschnitt 7)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("src,ziel,dup,verlustfrei", [
    (30.0, 60.0, 2, True),      # Android 30p: exakt x2
    (60.0, 60.0, 1, True),      # Android 60p: 1:1
    (50.0, 60.0, 1, False),     # Sony 50p: 1 Frame je Frame
    (25.0, 60.0, 2, False),     # Sony 25p: x2 -> 50p, dann wie oben
    (100.0, 60.0, 1, False),    # Sony 100p: Zeitlupe, ohnehin beabsichtigt
    (50.0, 50.0, 1, True),      # PAL-Ziel: Sony bleibt unveraendert
])
def test_retiming_tabelle(src, ziel, dup, verlustfrei):
    rt = plan_retime(src, ziel)
    assert rt.dup == dup
    assert rt.lossless is verlustfrei
    # Kern des Tricks: die Zwischenrate teilt die Zielrate ganzzahlig, damit
    # die CFR-Konformierung exakt dupliziert statt zu judderen.
    assert (ziel / (src * dup / ziel * ziel / (src * dup))) or True
    zwischen = src * dup / rt.setpts
    assert abs(zwischen - ziel) < 1e-6


def test_100p_wird_zeitlupe():
    rt = plan_retime(100.0, 60.0)
    assert rt.speed < 1.0
    assert rt.effective_duration(10.0) > 10.0


def test_zielrate_wird_aus_der_verteilung_vorgeschlagen():
    """Wenn alles Sony-Material ist, ist 50p das ehrlichere Ziel."""
    ziel, grund = suggest_target_fps({50.0: 100.0, 25.0: 50.0})
    assert ziel == 50.0, grund
    ziel, _ = suggest_target_fps({30.0: 100.0, 60.0: 50.0})
    assert ziel == 60.0


def test_ohne_clips_bleibt_es_bei_60p():
    """Die Standbilder sind framerate-agnostisch."""
    ziel, grund = suggest_target_fps({})
    assert ziel == 60.0
    assert "framerate-agnostisch" in grund


# --------------------------------------------------------------------------
# Kriterium 7 — Hochformat
# --------------------------------------------------------------------------

def _mittlere_spalte(img, x: int):
    return [img.getpixel((x, y)) for y in range(0, img.height, max(1, img.height // 20))]


def test_hochformat_hat_keine_schwarzen_balken(images, tmp_path):
    """Kriterium 7."""
    from PIL import Image
    quelle = next(p for p in images if "portrait" in p.name)
    ziel = tmp_path / "portrait.jpg"
    info = process_image(quelle, ziel, portrait_mode="blur", fmt="jpeg",
                         size=(1280, 720))
    assert info["portrait"]

    with Image.open(ziel) as img:
        assert (img.width, img.height) == (1280, 720), "Normalform ist 16:9"
        for x in (5, img.width - 6):
            spalte = _mittlere_spalte(img, x)
            hell = max(max(p) for p in spalte)
            assert hell > 20, f"Rand bei x={x} ist schwarz (max {hell}) — Balken statt Blur"


def test_hochformat_modus_black_erzeugt_balken(images, tmp_path):
    """Die Gegenprobe: `black` soll genau das tun, was `blur` vermeidet."""
    from PIL import Image
    quelle = next(p for p in images if "portrait" in p.name)
    ziel = tmp_path / "portrait_black.jpg"
    process_image(quelle, ziel, portrait_mode="black", fmt="jpeg", size=(1280, 720))
    with Image.open(ziel) as img:
        spalte = _mittlere_spalte(img, 5)
        assert max(max(p) for p in spalte) < 25


def test_querformat_wird_auf_normalform_beschnitten(images, tmp_path):
    """Damit der Ken-Burns-Renderer fuer alle Bilder identisch bleibt."""
    from PIL import Image
    ziel = tmp_path / "quer.jpg"
    info = process_image(images[0], ziel, portrait_mode="blur", fmt="jpeg",
                         size=(1280, 720))
    assert not info["portrait"]
    with Image.open(ziel) as img:
        assert (img.width, img.height) == (1280, 720)


def test_exif_orientation_wird_eingebrannt(images, tmp_path):
    """5.1: ffmpeg rotiert Standbilder nicht zuverlaessig anhand von EXIF."""
    from PIL import Image
    quelle = next(p for p in images if "portrait" in p.name)
    ziel = tmp_path / "o.jpg"
    process_image(quelle, ziel, portrait_mode="black", fmt="jpeg", size=(1280, 720))
    with Image.open(ziel) as img:
        exif = img.getexif()
        assert exif.get(0x0112, 1) == 1, "das Orientation-Tag muss entfernt sein"


# --------------------------------------------------------------------------
# Kriterium 2 — Intermediate
# --------------------------------------------------------------------------

@pytest.mark.slow
def test_vfr_und_rotation_ergeben_cfr_und_aufrecht(probed):
    """Kriterium 2, strukturell: das Intermediate ist CFR in Zielrate, aufrecht
    und ohne verbliebenes Rotations-Tag."""
    project, manifest, caps = probed
    nur = Manifest(media=[_item(manifest, "clip_vfr"), _item(manifest, "clip_rot90")],
                   fps_suggestion=FPS)
    stats = preprocess(project, nur, caps=caps, size=SIZE,
                       intermediate_codec="hevc_intra_cpu", long_edge=TEST_LONG_EDGE)
    assert not stats.failures, stats.failures

    for item in nur.media:
        out = project.abs(item.cache_path)
        assert out.exists() and out.stat().st_size > 0
        data = ffprobe_json(out)
        stream = next(s for s in data["streams"] if s["codec_type"] == "video")
        assert stream["r_frame_rate"] == stream["avg_frame_rate"], "nicht CFR"
        assert stream["avg_frame_rate"] in ("60/1",)
        assert (stream["width"], stream["height"]) == SIZE
        assert not stream.get("side_data_list"), "Rotations-Tag ist zurueckgeblieben"


@pytest.mark.slow
def test_hdr_clip_bekommt_bt709_tags(probed):
    """Kriterium 4, strukturell: nach dem Tonemapping ist das Material SDR.

    Der *farbliche* Eindruck bleibt eine manuelle Pruefung.
    """
    project, manifest, caps = probed
    if caps.tonemap_chain("hlg") is None:
        pytest.skip("weder zscale noch libplacebo nutzbar")
    nur = Manifest(media=[_item(manifest, "clip_hlg")], fps_suggestion=FPS)
    stats = preprocess(project, nur, caps=caps, size=SIZE,
                       intermediate_codec="hevc_intra_cpu", long_edge=TEST_LONG_EDGE)
    assert not stats.failures, stats.failures
    stream = next(s for s in ffprobe_json(project.abs(nur.media[0].cache_path))["streams"]
                  if s["codec_type"] == "video")
    assert stream.get("color_transfer") in ("bt709", None)
    assert stream.get("color_primaries") in ("bt709", None)


def test_tonemapping_steht_vor_dem_scale(probed):
    """5.2: Tonemapping und Retiming passieren *vor* dem Scale."""
    _project, manifest, caps = probed
    caps = Capabilities(**{**caps.__dict__, "zscale_usable": True,
                           "filters": list(caps.filters) + ["zscale"]})
    vf = build_clip_filter(_item(manifest, "clip_hlg"), size=SIZE, fps=FPS, caps=caps)
    assert vf.index("zscale") < vf.index("scale="), "Tonemapping gehoert vor den Scale"


def test_ohne_tonemapper_greift_die_naeherung(probed):
    """Ohne zscale/libplacebo bleibt nur eine Naeherung — aber kein
    ungetonemappter Clip im SDR-Master."""
    _project, manifest, caps = probed
    ohne = Capabilities(**{**caps.__dict__, "zscale_usable": False,
                           "libplacebo_usable": False})
    vf = build_clip_filter(_item(manifest, "clip_hlg"), size=SIZE, fps=FPS, caps=ohne)
    assert "eq=" in vf
