"""Bezugssysteme der Pfadumrechnung.

``rel()`` bekommt Pfade so, wie sie auf der Kommandozeile stehen — relativ zum
Arbeitsverzeichnis der Shell. ``abs()`` bekommt Manifest-Eintraege — relativ
zum Projektroot. Die beiden Richtungen duerfen nicht dasselbe Bezugssystem
annehmen, sonst verschiebt sich Quellmaterial genau dann, wenn jemand
``--project`` benutzt und nicht im Projektverzeichnis steht.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from slideshow.errors import SlideshowError
from slideshow.models import Manifest, MediaItem
from slideshow.paths import Project
from slideshow.probe import _verify_paths


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Project, Path]:
    """Projektroot und Materialordner nebeneinander unter einem Elternteil."""
    root = tmp_path / "proj"
    material = tmp_path / "material"
    root.mkdir()
    material.mkdir()
    (material / "DSC1.jpg").write_bytes(b"x")
    return Project.open(root), material


def test_rel_loest_relative_pfade_gegen_das_arbeitsverzeichnis_auf(tree, monkeypatch):
    """Der Regressionsfall: CWD ist der Elternordner, Projektroot ein Kind.

    Vorher wurde ``proj/material/DSC1.jpg`` daraus — ein Pfad, den es nie gab.
    """
    project, material = tree
    monkeypatch.chdir(material.parent)

    assert project.rel(Path("material/DSC1.jpg")) == "../material/DSC1.jpg"
    assert project.abs(project.rel(Path("material/DSC1.jpg"))) == material / "DSC1.jpg"


def test_rel_ist_identisch_egal_von_wo_aufgerufen(tree, monkeypatch):
    """Dasselbe Material, zwei Arbeitsverzeichnisse, ein Ergebnis."""
    project, material = tree
    absolut = material / "DSC1.jpg"

    monkeypatch.chdir(material.parent)
    von_oben = project.rel(Path(os.path.relpath(absolut)))
    monkeypatch.chdir(material)
    von_innen = project.rel(Path("DSC1.jpg"))

    assert von_oben == von_innen == "../material/DSC1.jpg"


def test_rel_im_projektroot_bleibt_ohne_praefix(tree, monkeypatch):
    """Material *im* Projekt bleibt schlicht relativ — der haeufige Fall."""
    project, _material = tree
    (project.root / "DSC2.jpg").write_bytes(b"x")
    monkeypatch.chdir(project.root)

    assert project.rel(Path("DSC2.jpg")) == "DSC2.jpg"


def test_abs_bleibt_rootbezogen(tree, monkeypatch):
    """Manifest-Pfade sind projektrelativ und duerfen nicht mit der CWD wandern."""
    project, material = tree
    monkeypatch.chdir(material)

    assert project.abs("DSC2.jpg") == project.root / "DSC2.jpg"


def _manifest(*paths: str) -> Manifest:
    return Manifest(media=[MediaItem(id=f"img_{i}", path=p, kind="image")
                           for i, p in enumerate(paths)])


def test_probe_bricht_bei_unerreichbarem_medienpfad_ab(tree):
    """Fail loud, fail early: der Fehler gehoert in probe, nicht in preprocess."""
    project, _material = tree

    with pytest.raises(SlideshowError) as exc:
        _verify_paths(project, _manifest("gibtsnicht/DSC1.jpg"))

    meldung = str(exc.value)
    assert "gibtsnicht/DSC1.jpg" in meldung, "der kaputte Pfad muss dastehen"
    assert str(project.root) in meldung, "und der Root, gegen den er aufgeloest wurde"


def test_probe_laesst_erreichbares_material_durch(tree, monkeypatch):
    project, material = tree
    monkeypatch.chdir(material.parent)

    _verify_paths(project, _manifest(project.rel(Path("material/DSC1.jpg"))))
