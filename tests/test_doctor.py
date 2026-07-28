"""Abnahmekriterium 1 — ``doctor`` auf einem System ohne ffmpeg."""

from __future__ import annotations

import pytest

from slideshow import doctor, proc
from slideshow.errors import PreflightError


@pytest.fixture
def no_tools(monkeypatch):
    """Simuliert ein System, auf dem gar nichts installiert ist."""
    monkeypatch.setattr(doctor, "have", lambda name: False)
    monkeypatch.setattr(doctor, "which", lambda name: None)
    monkeypatch.setattr(doctor, "resolve_tool", lambda name: None)
    monkeypatch.setattr(doctor, "_py_module", lambda name: False)


def test_doctor_ohne_ffmpeg_kein_traceback(no_tools):
    """Kriterium 1: laeuft durch, statt mit einem Traceback abzustuerzen."""
    rep = doctor.build_report(project=None, deep=True, refresh=True)
    assert rep.failures, "ohne ffmpeg muss es harte Fehlschlaege geben"
    assert rep.worst == doctor.FAIL


def test_doctor_nennt_installationsbefehl(no_tools):
    """Kriterium 1: nennt den korrekten Installationsbefehl."""
    rep = doctor.build_report(project=None, deep=True, refresh=True)
    ffmpeg = next(c for c in rep.checks if c.name == "ffmpeg")
    assert ffmpeg.status == doctor.FAIL
    assert ffmpeg.fix, "zu jedem Fehlschlag gehoert ein kopierbarer Befehl"
    assert "ffmpeg" in ffmpeg.fix.lower()


def test_installationsvorschlag_ist_plattformabhaengig(no_tools, monkeypatch):
    monkeypatch.setattr(doctor, "is_windows", lambda: True)
    assert doctor.install_hint("ffmpeg").startswith("winget install")
    monkeypatch.setattr(doctor, "is_windows", lambda: False)
    assert doctor.install_hint("ffmpeg").startswith("sudo apt")


def test_report_laesst_sich_ausgeben(no_tools):
    """Die Ausgabe selbst darf ebenfalls nicht werfen."""
    doctor.print_report(doctor.build_report(project=None, deep=True, refresh=True))


def test_preflight_bricht_mit_klarer_meldung_ab(no_tools, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "load_capabilities",
                        lambda project, deep=False, refresh=False: doctor.Capabilities())
    with pytest.raises(PreflightError) as exc:
        doctor.preflight(None, "render")
    text = str(exc.value)
    assert "ffmpeg" in text
    assert "slideshow doctor" in text


def test_zoompan_bittiefe_wird_als_pruefpunkt_gemeldet():
    """8.1 markiert die Bittiefe als Verifikationspunkt — der Report muss ihn
    benennen, egal wie er ausfaellt."""
    rep = doctor.build_report(project=None, deep=False, refresh=True)
    if not rep.caps.ffmpeg:
        pytest.skip("ffmpeg fehlt")
    check = next(c for c in rep.checks if c.name == "zoompan-Bittiefe")
    assert check.status in (doctor.OK, doctor.WARN)


def test_worker_zahl_respektiert_nvenc_limit():
    caps = doctor.Capabilities(cpu_cores=32, nvenc_sessions=3)
    assert caps.max_workers() == 3
    caps = doctor.Capabilities(cpu_cores=4, nvenc_sessions=0)
    assert caps.max_workers() == 4
    assert caps.max_workers(2) == 2


def _fake_exe(path):
    """Eine ausfuehrbare Attrappe anlegen (unter POSIX braucht sie das x-Bit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    path.chmod(0o755)
    return path


def test_melt_wird_neben_dem_path_gefunden(tmp_path, monkeypatch):
    """Scoop shimt nur kdenlive.exe; melt.exe bleibt in bin/ liegen. Der
    Report darf dann nicht zur Installation von Kdenlive raten."""
    exe = _fake_exe(tmp_path / "scoop" / "apps" / "kdenlive" / "current" / "bin" / "melt.exe")
    monkeypatch.setattr(proc.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setenv("SCOOP", str(tmp_path / "scoop"))
    assert proc.resolve_tool("melt") == str(exe)


def test_override_schlaegt_den_path(tmp_path, monkeypatch):
    exe = _fake_exe(tmp_path / "eigenes_melt.exe")
    monkeypatch.setattr(proc.shutil, "which", lambda name, *a, **k: "/usr/bin/melt")
    monkeypatch.setenv("SLIDESHOW_MELT", str(exe))
    assert proc.resolve_tool("melt") == str(exe)


def test_ungueltiger_override_faellt_auf_den_path_zurueck(tmp_path, monkeypatch):
    monkeypatch.setattr(proc.shutil, "which",
                        lambda name, *a, **k: "/usr/bin/melt" if name == "melt" else None)
    monkeypatch.setenv("SLIDESHOW_MELT", str(tmp_path / "gibt_es_nicht.exe"))
    assert proc.resolve_tool("melt") == "/usr/bin/melt"


def test_fehlendes_werkzeug_bleibt_none(tmp_path, monkeypatch):
    monkeypatch.setattr(proc.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.delenv("SLIDESHOW_MELT", raising=False)
    monkeypatch.setattr(proc, "_EXTRA_LOCATIONS",
                        {"melt": lambda: [tmp_path / "gibt_es_nicht.exe"]})
    assert proc.resolve_tool("melt") is None


# Kopfzeilen und je zwei Eintraege, wie ffmpeg sie ausgibt. Bis 7.x hat die
# Flagspalte drei Zeichen (T.C, mit Command-Support), ab 8.x nur noch zwei.
_FILTERS_7 = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  ------
 ... abench           A->A       Benchmark part of a filtergraph.
 TSC zoompan          V->V       Apply Zoom & Pan effect.
 TS. xfade            VV->V      Cross fade one video with another video.
"""

_FILTERS_8 = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  ------
 .. abench            A->A       Benchmark part of a filtergraph.
 TS zoompan           V->V       Apply Zoom & Pan effect.
 T. xfade             VV->V      Cross fade one video with another video.
"""

_ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC (codec h264)
 V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)
"""


@pytest.mark.parametrize("ausgabe", [_FILTERS_7, _FILTERS_8], ids=["ffmpeg7", "ffmpeg8"])
def test_filterliste_ueber_ffmpeg_versionen_hinweg(ausgabe, monkeypatch):
    """Die Flagspalte hat je nach Version zwei oder drei Zeichen. Ein fester
    Zaehler laesst die Liste bei der anderen Version stumm leer — und dann
    meldet der Report Standardfilter als fehlend."""
    monkeypatch.setattr(doctor, "run",
                        lambda *a, **k: proc.RunResult(["ffmpeg"], 0, ausgabe, ""))
    namen = doctor._ffmpeg_list("ffmpeg", "-filters", doctor._FILTER_PATTERN)
    assert namen == ["abench", "xfade", "zoompan"]
    assert "=" not in namen, "die Legende darf nicht als Filter durchgehen"


def test_encoderliste_ohne_legendenartefakt(monkeypatch):
    monkeypatch.setattr(doctor, "run",
                        lambda *a, **k: proc.RunResult(["ffmpeg"], 0, _ENCODERS, ""))
    namen = doctor._ffmpeg_list("ffmpeg", "-encoders", doctor._ENCODER_PATTERN)
    assert namen == ["hevc_nvenc", "libx264"]


def test_standardfilter_werden_gefunden():
    """Regression: mit einem echten Full-Build duerfen zoompan/xfade/scale/
    format nicht als fehlend gemeldet werden."""
    rep = doctor.build_report(project=None, deep=False, refresh=True)
    if not rep.caps.ffmpeg:
        pytest.skip("ffmpeg fehlt")
    check = next(c for c in rep.checks if c.name == "ffmpeg-Filter")
    assert check.status == doctor.OK, check.detail


def test_speicherschaetzung_fordert_reserve():
    est = doctor.estimate_space(images=100, clip_seconds=120, timeline_seconds=400)
    assert est["required"] == pytest.approx(est["total"] * 1.5)
    assert est["total"] > est["master"]
