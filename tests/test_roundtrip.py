"""Die Edit-List muss reproduzierbar sein.

Prinzip 1 sagt, die Edit-List sei die Single Source of Truth und jeder
Renderpfad leite sich aus ihr ab. Das traegt nur, wenn ``build`` und ``render``
aus derselben Datei dieselbe Timeline bekommen — sonst zeigt der Report etwas
anderes an, als hinterher gerendert wird, und ein zweiter ``render``-Lauf
invalidiert den halben Cache ohne erkennbaren Grund.

Der Fall, der das hier gebrochen hat: ``build`` schrieb die *gemessene* Dauer
eines Slots als ``beats:`` zurueck. Weil der Planer das erste Bild um den
Vorlauf verlaengert, stand dort ``beats: 8.833`` statt ``beats: 8`` — und beim
Laden wurde der Vorlauf ein zweites Mal addiert.
"""

from __future__ import annotations

import pytest

from slideshow.build import plan_from_edit
from slideshow.models import EditList
from slideshow.planner import resolve

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def _slots(plan):
    return [(s.start_f, s.end_f) for s in plan.slots]


def test_plan_ueberlebt_den_weg_durch_die_edit_list(built):
    """``build`` und ``render`` muessen dieselbe Timeline sehen."""
    edit, plan, manifest = built["edit"], built["plan"], built["manifest"]
    erneut = plan_from_edit(edit, manifest)
    assert _slots(erneut) == _slots(plan)
    assert erneut.transitions == plan.transitions
    assert erneut.total_frames == plan.total_frames


def test_plan_ueberlebt_die_yaml_datei(built):
    """Inklusive Serialisierung — Rundungsfehler beim Schreiben zaehlen mit."""
    project, edit, plan, manifest = (built["project"], built["edit"],
                                     built["plan"], built["manifest"])
    edit.save(project.edit)
    geladen = EditList.load(project.edit)
    erneut = plan_from_edit(geladen, manifest)
    assert _slots(erneut) == _slots(plan)
    assert erneut.transitions == plan.transitions


def test_mehrfaches_laden_wandert_nicht(built):
    """Ein dritter und vierter Durchlauf duerfen nichts mehr aendern."""
    project, edit, manifest = built["project"], built["edit"], built["manifest"]
    edit.save(project.edit)
    vorher = None
    for _ in range(3):
        geladen = EditList.load(project.edit)
        plan = plan_from_edit(geladen, manifest)
        jetzt = _slots(plan)
        if vorher is not None:
            assert jetzt == vorher
        vorher = jetzt
        geladen.save(project.edit)


def test_segmentliste_bleibt_stabil(built):
    project, edit, manifest = built["project"], built["edit"], built["manifest"]
    edit.save(project.edit)
    a = [(s.kind, s.start_f, s.end_f) for s in resolve(plan_from_edit(edit, manifest))]
    b = [(s.kind, s.start_f, s.end_f)
         for s in resolve(plan_from_edit(EditList.load(project.edit), manifest))]
    assert a == b


def test_erstes_bild_wird_nicht_zweimal_verlaengert(built):
    """Der konkrete Ausloeser: der Vorlauf aus 6.0 darf sich nicht summieren."""
    edit, plan, manifest = built["edit"], built["plan"], built["manifest"]
    erneut = plan_from_edit(edit, manifest)
    assert erneut.slots[0].end_f == plan.slots[0].end_f
    from slideshow.models import StillSegment
    erstes = next(s for s in edit.segments if isinstance(s, StillSegment))
    if erstes.beats is not None:
        assert erstes.beats == edit.defaults.beats_per_still, \
            "das erste Bild muss die Absicht tragen, nicht seine gemessene Laenge"
