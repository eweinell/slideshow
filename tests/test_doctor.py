"""Abnahmekriterium 1 — ``doctor`` auf einem System ohne ffmpeg."""

from __future__ import annotations

import pytest

from slideshow import doctor
from slideshow.errors import PreflightError


@pytest.fixture
def no_tools(monkeypatch):
    """Simuliert ein System, auf dem gar nichts installiert ist."""
    monkeypatch.setattr(doctor, "have", lambda name: False)
    monkeypatch.setattr(doctor, "which", lambda name: None)
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


def test_speicherschaetzung_fordert_reserve():
    est = doctor.estimate_space(images=100, clip_seconds=120, timeline_seconds=400)
    assert est["required"] == pytest.approx(est["total"] * 1.5)
    assert est["total"] > est["master"]
