"""Schemata fuer ``manifest.json`` und ``edit.yaml`` (Abschnitt 11).

Beide tragen ein ``version``-Feld; unbekannte Versionen werden abgelehnt.
Fehlermeldungen nennen den YAML-Pfad (``segments[12].kb.z``), nicht nur
"invalid config" — und wo moeglich die Zeile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (BaseModel, ConfigDict, Field, ValidationError, field_validator,
                      model_validator)

from . import BEATS_VERSION, EDIT_VERSION, MANIFEST_VERSION
from .errors import SchemaError


# --------------------------------------------------------------------------
# Zeitangaben
# --------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(?:(?:(\d+):)?(\d+):)?(\d+(?:\.\d+)?)$")


def parse_time(value: Any, *, path: str = "") -> float:
    """Akzeptiert Sekunden (``6.5``) oder ``[[HH:]MM:]SS.mmm`` (``"00:08.500"``)."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise SchemaError(f"Zeitangabe muss Zahl oder String sein, nicht {type(value).__name__}",
                          path=path)
    m = _TIME_RE.match(value.strip())
    if not m:
        raise SchemaError(f"Unlesbare Zeitangabe {value!r} (erwartet SS.mmm, MM:SS.mmm "
                          f"oder HH:MM:SS.mmm)", path=path)
    h, mnt, sec = m.groups()
    return int(h or 0) * 3600 + int(mnt or 0) * 60 + float(sec)


def format_time(seconds: float) -> str:
    m, s = divmod(float(seconds), 60.0)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

class ImageInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    width: int = 0
    height: int = 0
    orientation: int = 1
    icc: str = ""
    portrait: bool = False


class ClipInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    codec: str = ""
    profile: str = ""
    level: int = 0
    width: int = 0
    height: int = 0
    pix_fmt: str = ""
    r_frame_rate: str = ""
    avg_frame_rate: str = ""
    fps: float = 0.0
    color_primaries: str = ""
    color_trc: str = ""
    colorspace: str = ""
    rotation: int = 0
    duration: float = 0.0
    bitrate: int = 0
    has_audio: bool = False
    container: str = ""
    #: ``xavc_hs`` | ``xavc_s`` | ``generic`` (Abschnitt 4)
    classification: str = "generic"
    vfr_suspect: bool = False
    vfr_confirmed: bool = False
    vfr_jitter: float = 0.0
    #: ``hlg`` | ``pq`` | ``""``
    hdr: str = ""
    #: setpts-Faktor aus der Framerate-Politik (Abschnitt 7)
    retime: float = 1.0
    retime_note: str = ""
    #: Dauer nach dem Retiming — die Zeitbasis, auf die sich in/out beziehen
    effective_duration: float = 0.0
    #: NVDEC deaktivieren (4:2:2-10-Bit-Falle, 3.3)
    force_cpu_decode: bool = False
    #: Startpunkt des Intermediates in der retimten Zeitbasis. Wurde der Clip
    #: nur ausschnittsweise extrahiert, verschiebt das in/out der Edit-List.
    cache_offset: float = 0.0
    #: Dauer des Intermediates in der retimten Zeitbasis.
    cache_duration: float = 0.0


class MediaItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    path: str
    kind: Literal["image", "clip"]
    size_bytes: int = 0
    camera: str = "unbekannt"
    #: Unix-Zeitstempel des Aufnahmezeitpunkts, *ohne* Uhren-Offset
    capture_time: float | None = None
    #: ``exif`` | ``container`` | ``mtime`` | ``filename`` | ``none``
    time_source: str = "none"
    image: ImageInfo | None = None
    clip: ClipInfo | None = None
    #: Pfad des normalisierten Zwischenprodukts, relativ zum Projektroot
    cache_path: str = ""
    warnings: list[str] = Field(default_factory=list)


class TrackBound(BaseModel):
    file: str = ""
    start: float
    end: float


class AudioInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str = ""
    duration: float = 0.0
    sample_rate: int = 48000
    #: Track-Grenzen aus 5.4 — Seeds fuer die Regionserkennung in 6.2
    tracks: list[TrackBound] = Field(default_factory=list)
    loudnorm: dict[str, Any] = Field(default_factory=dict)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int = MANIFEST_VERSION
    created: str = ""
    #: Uhren-Offsets in Sekunden je Kameramodell (4.4)
    clock_offsets: dict[str, float] = Field(default_factory=dict)
    fps_histogram: dict[str, int] = Field(default_factory=dict)
    fps_suggestion: float = 60.0
    fps_rationale: str = ""
    media: list[MediaItem] = Field(default_factory=list)
    audio: AudioInfo = Field(default_factory=AudioInfo)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != MANIFEST_VERSION:
            raise ValueError(f"manifest.json hat Version {v}, unterstuetzt wird "
                             f"{MANIFEST_VERSION}")
        return v

    # -- Bequemlichkeit -------------------------------------------------
    def by_id(self, mid: str) -> MediaItem | None:
        return next((m for m in self.media if m.id == mid), None)

    def by_cache_path(self, path: str) -> MediaItem | None:
        p = str(path).replace("\\", "/")
        return next((m for m in self.media if m.cache_path == p), None)

    @property
    def images(self) -> list[MediaItem]:
        return [m for m in self.media if m.kind == "image"]

    @property
    def clips(self) -> list[MediaItem]:
        return [m for m in self.media if m.kind == "clip"]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(mode="json"), indent=1, ensure_ascii=False),
                        encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not Path(path).exists():
            raise SchemaError(f"Manifest fehlt: {path}. Zuerst `slideshow probe` laufen lassen.")
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SchemaError(f"manifest.json ist kein gueltiges JSON: {exc}",
                              file=str(path), line=exc.lineno) from exc
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise _to_schema_error(exc, file=str(path)) from exc


# --------------------------------------------------------------------------
# Regionenkarte (`slideshow beats`)
# --------------------------------------------------------------------------

class Region(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["beat", "free"]
    start: float
    end: float
    bpm: float | None = None
    offset: float | None = None
    conf: float | None = None
    reason: str = ""
    #: True nur bei echter Stille. Eine free-Region entsteht aus zwei sehr
    #: verschiedenen Gruenden: da ist nichts zu hoeren, oder da ist etwas zu
    #: hoeren, das sich nur nicht rastern liess. Nur der erste Fall
    #: rechtfertigt das lange Standbild aus ``hold_seconds`` — im zweiten
    #: laeuft Musik, und die Bildwechsel duerfen nicht ausfallen.
    quiet: bool = False
    #: Regions-Defaults, die die globalen ueberschreiben (6.3, Praezedenz 3)
    beats_per_still: int | None = None
    still_seconds: float | None = None

    @model_validator(mode="after")
    def _quiet_aus_reason_ableiten(self) -> "Region":
        """Aeltere Karten und handgeschriebene Regionen kennen ``quiet`` nicht.

        ``stille`` als *alleiniger* Grund ist eindeutig — nur so schreibt die
        Analyse eine reine Stille-Region. Sobald noch etwas dahinter steht
        (``stille+niedrige Rhythmus-Konfidenz``), laeuft in der Region Musik,
        und dann ist sie es gerade nicht.
        """
        if "quiet" not in self.model_fields_set and self.reason == "stille":
            self.quiet = True
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start

    def beat_duration(self) -> float:
        if not self.bpm:
            raise SchemaError("Beat-Region ohne bpm", path="regions")
        return 60.0 / self.bpm


class BeatMap(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int = BEATS_VERSION
    audio: dict[str, Any] = Field(default_factory=dict)
    regions: list[Region] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Edit-List
# --------------------------------------------------------------------------

class KBSpec(BaseModel):
    """Ken-Burns-Vorgabe an einem Segment. Ueberschreibt die Defaults."""

    model_config = ConfigDict(extra="forbid")

    #: Start-/Ziel-Zoom. Ohne Angabe wird aus zoom_rate und Dauer gerechnet (8.1).
    z: tuple[float, float] | None = None
    #: Start-/Ziel-Bildmitte als normalisierte Koordinaten [x0, y0, x1, y1].
    c: tuple[float, float, float, float] | None = None
    ease: Literal["smoothstep", "linear"] | None = None
    engine: Literal["zoompan", "scale16"] | None = None

    @field_validator("z")
    @classmethod
    def _positive(cls, v):
        if v and (v[0] <= 0 or v[1] <= 0):
            raise ValueError("Zoomwerte muessen > 0 sein")
        return v

    @field_validator("c")
    @classmethod
    def _normalised(cls, v):
        if v and any(not 0.0 <= x <= 1.0 for x in v):
            raise ValueError("Bildmitten sind normalisierte Koordinaten in [0, 1]")
        return v


class KBDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Zoom pro Sekunde — der Zoombetrag ergibt sich aus der Dauer, nicht umgekehrt.
    zoom_rate: float = 0.05
    #: Klemmung [min, max] des Gesamtzooms.
    zoom_total: tuple[float, float] = (0.08, 0.30)
    ease: Literal["smoothstep", "linear"] = "smoothstep"
    #: Zoomrichtung alternieren. Hundertmal hineinzoomen ermuedet.
    alternate: bool = True
    #: ``zoompan`` (8 Bit, schnell) oder ``scale16`` (16 Bit, ohne zoompan, 8.1).
    engine: Literal["zoompan", "scale16"] = "zoompan"
    #: Maximale Auslenkung der Bildmitte beim automatischen Schwenk.
    pan_amount: float = 0.06


class XfadeDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Standarddauer einer Blende in Beats (in free-Regionen: Sekunden via `dur`).
    beats: float = 1.0
    dur: float | None = None
    mode: str = "dissolve"
    #: Automatisch zwischen alle benachbarten Segmente Blenden setzen.
    #: Default an: harte Schnitte zwischen 100 Standbildern wirken abgehackt,
    #: und die Uebergaenge sind ohnehin eigene, einzeln loeschbare Segmente.
    auto: bool = True


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beats_per_still: int = 8
    still_seconds: float = 4.0
    still_tolerance: tuple[float, float] = (3.0, 6.0)
    snap_back: bool = True
    portrait: Literal["blur", "black", "crop"] = "blur"
    clip_snap_tol: float = 1.0
    #: Lange Stille (> hold_seconds) bekommt ein ruhiges Einzelbild.
    hold_seconds: float = 12.0
    kb: KBDefaults = Field(default_factory=KBDefaults)
    xfade: XfadeDefaults = Field(default_factory=XfadeDefaults)

    @field_validator("still_tolerance")
    @classmethod
    def _ordered(cls, v):
        if v[0] >= v[1]:
            raise ValueError("still_tolerance muss [min, max] mit min < max sein")
        return v


class StillSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["still"] = "still"
    src: str
    #: Dauer in Beats — nur in einer beat-Region gueltig (6.3, Praezedenz 2).
    beats: float | None = None
    #: Explizite Sekunden — gewinnt immer (6.3, Praezedenz 1).
    dur: float | None = None
    #: Ruhiges Bild ueber lange Stille.
    hold: bool = False
    #: Nach einem Override auf den naechsten Beat aufrunden.
    snap_back: bool | None = None
    portrait: Literal["blur", "black", "crop"] | None = None
    kb: KBSpec | None = None


class ClipSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["clip"] = "clip"
    src: str
    #: in/out beziehen sich immer auf das (ggf. retimte) Intermediate (Prinzip 3).
    in_: float = Field(0.0, alias="in")
    out: float | None = None
    #: ``out`` = Out-Punkt aufs Beat-Raster ziehen, ``none`` = freie Laenge (6.6).
    snap: Literal["out", "none"] = "out"
    snap_back: bool | None = None

    @field_validator("in_", "out", mode="before")
    @classmethod
    def _times(cls, v):
        return None if v is None else parse_time(v)


class XfadeSegment(BaseModel):
    """Uebergang als eigenes Segment — der Trick, der Unabhaengigkeit und
    damit das Caching rettet (6.6/8.2)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["xfade"] = "xfade"
    #: Indizes der Nachbarsegmente in ``segments``.
    from_: int = Field(..., alias="from")
    to: int
    beats: float | None = None
    dur: float | None = None
    mode: str = "dissolve"


Segment = Annotated[StillSegment | ClipSegment | XfadeSegment, Field(discriminator="type")]


class EditList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = EDIT_VERSION
    fps: float = 60.0
    size: tuple[int, int] = (3840, 2160)
    audio: dict[str, Any] = Field(default_factory=dict)
    defaults: Defaults = Field(default_factory=Defaults)
    segments: list[Segment] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != EDIT_VERSION:
            raise ValueError(f"edit.yaml hat Version {v}, unterstuetzt wird {EDIT_VERSION}")
        return v

    @field_validator("fps")
    @classmethod
    def _fps(cls, v: float) -> float:
        if not 1 <= v <= 240:
            raise ValueError(f"unplausible Framerate: {v}")
        return v

    @property
    def regions(self) -> list[Region]:
        raw = self.audio.get("regions") or []
        out = []
        for i, r in enumerate(raw):
            try:
                out.append(Region.model_validate(r))
            except ValidationError as exc:
                raise _to_schema_error(exc, prefix=f"audio.regions[{i}]") from exc
        return out

    @property
    def audio_file(self) -> str:
        return str(self.audio.get("file", ""))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_edit_yaml(self), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "EditList":
        p = Path(path)
        if not p.exists():
            raise SchemaError(f"Edit-List fehlt: {p}. Zuerst `slideshow build` laufen lassen.")
        text = p.read_text(encoding="utf-8")
        try:
            data = yaml.load(text, Loader=_LineLoader)
        except yaml.YAMLError as exc:
            line = getattr(getattr(exc, "problem_mark", None), "line", None)
            raise SchemaError(f"edit.yaml ist kein gueltiges YAML: {exc}",
                              file=str(p), line=(line + 1) if line is not None else None) from exc
        if not isinstance(data, dict):
            raise SchemaError("edit.yaml muss ein Mapping auf oberster Ebene sein", file=str(p))
        lines = _line_index(data)
        try:
            return cls.model_validate(_strip_lines(data))
        except ValidationError as exc:
            raise _to_schema_error(exc, file=str(p), lines=lines) from exc


# --------------------------------------------------------------------------
# YAML mit Zeilennummern
# --------------------------------------------------------------------------

_LINE_KEY = "__line__"


class _LineLoader(yaml.SafeLoader):
    """SafeLoader, der jedem Mapping seine Quellzeile anhaengt."""


def _construct_mapping(loader, node, deep=False):
    mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=deep)
    mapping[_LINE_KEY] = node.start_mark.line + 1
    return mapping


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _strip_lines(obj):
    if isinstance(obj, dict):
        return {k: _strip_lines(v) for k, v in obj.items() if k != _LINE_KEY}
    if isinstance(obj, list):
        return [_strip_lines(v) for v in obj]
    return obj


def _line_index(obj, prefix: str = "", out: dict[str, int] | None = None) -> dict[str, int]:
    """Bildet Pfade wie ``segments[3].kb`` auf Zeilennummern ab."""
    out = {} if out is None else out
    if isinstance(obj, dict):
        if _LINE_KEY in obj:
            out[prefix] = obj[_LINE_KEY]
        for k, v in obj.items():
            if k == _LINE_KEY:
                continue
            _line_index(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _line_index(v, f"{prefix}[{i}]", out)
    return out


#: Werte des ``type``-Discriminators. Pydantic schiebt sie als eigene Ebene in
#: den Fehlerpfad (``segments[0].still.kb.z``) — fuer den Leser der YAML-Datei
#: ist das Rauschen, dort steht kein solcher Schluessel.
_DISCRIMINATORS = {"still", "clip", "xfade"}


def _loc_to_path(loc: tuple) -> str:
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] += f"[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            name = str(item)
            if name.endswith("Segment") or name.startswith("function-"):
                continue
            if name in _DISCRIMINATORS and parts and parts[-1].endswith("]"):
                continue
            name = {"in_": "in", "from_": "from"}.get(name, name)
            parts.append(name)
    return ".".join(parts)


def _to_schema_error(exc: ValidationError, *, file: str | None = None,
                     prefix: str = "", lines: dict[str, int] | None = None) -> SchemaError:
    err = exc.errors()[0]
    path = _loc_to_path(err["loc"])
    if prefix:
        path = f"{prefix}.{path}" if path else prefix
    line = None
    if lines:
        probe = path
        while probe and line is None:
            line = lines.get(probe)
            probe = probe.rsplit(".", 1)[0] if "." in probe else \
                (probe.split("[")[0] if "[" in probe else "")
    msg = err["msg"]
    if err["type"] == "extra_forbidden":
        msg = "unbekanntes Feld"
    extra = ""
    if len(exc.errors()) > 1:
        extra = f"  (+{len(exc.errors()) - 1} weitere Fehler)"
    return SchemaError(msg + extra, path=path or None, file=file, line=line)


# --------------------------------------------------------------------------
# YAML-Ausgabe
# --------------------------------------------------------------------------

class _Dumper(yaml.SafeDumper):
    """Kompakte, menschenlesbare Ausgabe: Listen eingerueckt, Tupel als Flow."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _flow_list(dumper, data):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


_Dumper.add_representer(tuple, _flow_list)


def dump_edit_yaml(edit: EditList) -> str:
    data = edit.model_dump(mode="json", by_alias=True, exclude_none=True)
    head = {k: data.pop(k) for k in ("version", "fps", "size") if k in data}
    for key in ("size",):
        if key in head:
            head[key] = tuple(head[key])
    audio = data.pop("audio", {})
    defaults = data.pop("defaults", {})
    segments = data.pop("segments", [])

    out = [
        "# Edit-List — Single Source of Truth. Von Hand editierbar.",
        "# Zeiten sind absolute Zeitpunkte auf der Master-Timeline;",
        "# Nullpunkt ist Sample 0 der Tonspur.",
        yaml.dump(head, Dumper=_Dumper, sort_keys=False, allow_unicode=True).rstrip(),
        "",
        yaml.dump({"audio": _compact_regions(audio)}, Dumper=_Dumper, sort_keys=False,
                  allow_unicode=True, default_flow_style=False).rstrip(),
        "",
        yaml.dump({"defaults": defaults}, Dumper=_Dumper, sort_keys=False,
                  allow_unicode=True).rstrip(),
        "",
        "segments:",
    ]
    for seg in segments:
        out.append("  - " + yaml.dump(seg, Dumper=_Dumper, sort_keys=False,
                                      allow_unicode=True, width=200,
                                      default_flow_style=True).strip())
    if data:
        out.append(yaml.dump(data, Dumper=_Dumper, sort_keys=False,
                             allow_unicode=True).rstrip())
    return "\n".join(out) + "\n"


def _compact_regions(audio: dict) -> dict:
    """Regionen als Flow-Mappings — eine Zeile pro Region, wie im Briefing."""
    out = dict(audio)
    if "regions" in out:
        out["regions"] = [_Flow(r) for r in out["regions"]]
    return out


class _Flow(dict):
    pass


_Dumper.add_representer(
    _Flow, lambda d, data: d.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True))
