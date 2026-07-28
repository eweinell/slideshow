"""Musiksynchroner 4K-Slideshow-Renderer.

Die Edit-List ist die Single Source of Truth; jeder Renderpfad leitet sich
daraus ab. Alle Zeiten sind absolute Zeitpunkte auf der Master-Timeline,
deren Nullpunkt Sample 0 der Tonspur ist.
"""

__version__ = "0.1.0"

#: Version des ``manifest.json``-Schemas. Unbekannte Versionen werden abgelehnt.
MANIFEST_VERSION = 1

#: Version des ``edit.yaml``-Schemas. Unbekannte Versionen werden abgelehnt.
EDIT_VERSION = 2

#: Version der ``beats``-Regionenkarte.
BEATS_VERSION = 1
