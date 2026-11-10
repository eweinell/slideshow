"""Cache aufraeumen (Pruefbericht 2026-11, AP2 Schritt d).

Zwei Zusagen: ``clean`` loescht nie, was die aktuelle ``edit.yaml`` nennt, und
``--dry-run`` loescht gar nichts. Dazu das automatische Aufraeumen nach einem
vollen Render, das nur Segmente desselben Profils trifft.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from slideshow.clean import (all_listed, apply_clean, plan_clean, profile_list,
                             remember_segments)
from slideshow.encoders import preview_profile
from slideshow.models import Manifest

from .conftest import requires_ffmpeg


@dataclass
class _Job:
    out: Path


def _segmente(project, *namen: str) -> list[_Job]:
    project.segments.mkdir(parents=True, exist_ok=True)
    jobs = []
    for n in namen:
        p = project.segments / n
        p.write_bytes(b"x" * 100)
        jobs.append(_Job(out=p))
    return jobs


# --------------------------------------------------------------------------
# Segmentlisten je Profil
# --------------------------------------------------------------------------

def test_ein_voller_lauf_loescht_was_der_vorige_brauchte_und_er_nicht(project):
    profil = preview_profile(50.0)
    remember_segments(project, profil, _segmente(project, "seg_a.mkv", "seg_b.mkv"),
                      complete=True)
    anzahl, groesse = remember_segments(
        project, profil, _segmente(project, "seg_a.mkv", "seg_c.mkv"), complete=True)
    assert (anzahl, groesse) == (1, 100)
    assert not (project.segments / "seg_b.mkv").exists()
    assert (project.segments / "seg_a.mkv").exists()


def test_ein_teillauf_loescht_nichts_und_ergaenzt_die_liste(project):
    profil = preview_profile(50.0)
    remember_segments(project, profil, _segmente(project, "seg_a.mkv", "seg_b.mkv"),
                      complete=True)
    assert remember_segments(project, profil, _segmente(project, "seg_c.mkv"),
                             complete=False) == (0, 0)
    assert all((project.segments / n).exists() for n in ("seg_a.mkv", "seg_b.mkv"))
    assert all_listed(project) == {"seg_a.mkv", "seg_b.mkv", "seg_c.mkv"}


def test_ein_segment_eines_anderen_profils_bleibt(project):
    """Vorschau und Master teilen sich kein Segment — aber falls doch (gleicher
    Key), darf der eine Lauf es dem anderen nicht wegnehmen."""
    vorschau, anders = preview_profile(50.0), preview_profile(25.0)
    assert profile_list(project, vorschau) != profile_list(project, anders)
    remember_segments(project, anders, _segmente(project, "seg_gemeinsam.mkv"),
                      complete=True)
    remember_segments(project, vorschau, _segmente(project, "seg_gemeinsam.mkv"),
                      complete=True)
    remember_segments(project, vorschau, _segmente(project, "seg_neu.mkv"),
                      complete=True)
    assert (project.segments / "seg_gemeinsam.mkv").exists()


# --------------------------------------------------------------------------
# slideshow clean
# --------------------------------------------------------------------------

@pytest.fixture
def gebaut(built):
    """Ein gebautes Projekt plus ein Bild, das nicht im Film steht."""
    project, manifest = built["project"], built["manifest"]
    im_film = {getattr(s, "src", None) for s in built["edit"].segments}
    extra = next((m for m in manifest.images if m.cache_path not in im_film), None)
    if extra is None:
        # Alle Bilder stehen im Film: eines kuenstlich hinzufuegen.
        vorlage = manifest.images[0]
        extra = vorlage.model_copy(deep=True)
        extra.id, extra.cache_path = "img_extra", "cache/img_extra.jpg"
        project.abs(extra.cache_path).write_bytes(
            project.abs(vorlage.cache_path).read_bytes())
        manifest.media.append(extra)
    return {**built, "extra": extra}


@requires_ffmpeg
def test_clean_loescht_nichts_was_die_edit_list_nennt(gebaut):
    project, manifest, edit = gebaut["project"], gebaut["manifest"], gebaut["edit"]
    plan = plan_clean(project, edit, manifest)
    weg = {p.resolve() for ds in plan.dateien.values() for p in ds}
    for seg in edit.segments:
        src = getattr(seg, "src", None)
        if src:
            assert project.abs(src).resolve() not in weg, f"{src} steht im Film"


@requires_ffmpeg
def test_clean_raeumt_ein_bild_ausserhalb_des_films_ab(gebaut):
    project, manifest, edit, extra = (gebaut["project"], gebaut["manifest"],
                                      gebaut["edit"], gebaut["extra"])
    datei = project.abs(extra.cache_path)
    plan = plan_clean(project, edit, manifest)
    assert datei in plan.dateien["bilder"]
    assert plan.bytes("bilder") >= datei.stat().st_size

    apply_clean(project, plan, manifest)
    assert not datei.exists()
    # Sonst plante `build` das Bild ein und `render` faende die Datei nicht.
    assert manifest.by_id(extra.id).cache_path == ""


@requires_ffmpeg
def test_clean_dry_run_loescht_nichts(gebaut, capsys):
    from slideshow.cli import main
    project, manifest, extra = gebaut["project"], gebaut["manifest"], gebaut["extra"]
    manifest.save(project.manifest)
    datei = project.abs(extra.cache_path)

    assert main(["--project", str(project.root), "clean", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert datei.exists()
    assert "bilder" in out and "Wuerde loeschen" in out
    assert Manifest.load(project.manifest).by_id(extra.id).cache_path


@requires_ffmpeg
def test_ohne_segmentliste_bleiben_die_segmente_stehen(gebaut):
    """Ein Projekt aus der Zeit vor den Listen: lieber nichts loeschen als
    einen 90-Minuten-Render."""
    project = gebaut["project"]
    _segmente(project, "seg_alt.mkv")
    plan = plan_clean(project, gebaut["edit"], gebaut["manifest"])
    assert plan.dateien["segmente"] == []
    assert any("Segmentliste" in h for h in plan.hinweise)
