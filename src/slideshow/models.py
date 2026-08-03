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
    #: Aufnahmeort als ``[lat, lon]`` in Grad, sofern das Material einen
    #: GPS-Fix traegt. Signal fuer die Kapitelerkennung (`slideshow chapters`):
    #: ein Sprung von 30 km zwischen zwei Aufnahmen *ist* der neue Ort.
    gps: tuple[float, float] | None = None
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
    #: Zoomrichtung wechseln lassen. Hundertmal hineinzoomen ermuedet.
    #:
    #: Der Wechsel ist **statistisch, nicht streng abwechselnd**: die Richtung
    #: haengt an der Kennung des Bildes, nicht an seiner Position. Streng
    #: abwechselnd waere nur ueber die Position zu haben — und dann verschoebe
    #: ein eingefuegtes Segment die Bewegung jedes folgenden Bildes.
    alternate: bool = True
    #: ``zoompan`` (8 Bit, schnell) oder ``scale16`` (16 Bit, ohne zoompan, 8.1).
    engine: Literal["zoompan", "scale16"] = "zoompan"
    #: Schwenkweg pro Sekunde — wie ``zoom_rate``, nur fuer die Bildmitte.
    pan_rate: float = 0.03
    #: Klemmung des Gesamt-Schwenkwegs [min, max], normalisiert auf die
    #: Bildkante. Bei ``pan_rate`` 0,03 gilt die Rate damit zwischen 1,7 s und
    #: 6,0 s unveraendert — dasselbe Fenster wie beim Zoom.
    pan_total: tuple[float, float] = (0.05, 0.18)

    @model_validator(mode="before")
    @classmethod
    def _pan_amount_uebernehmen(cls, data):
        """``pan_amount`` aus aelteren Dateien verlustfrei uebersetzen.

        Der alte Wert war eine *feste* Auslenkung je Richtung, also ein
        Gesamtweg von ``2 * pan_amount`` unabhaengig von der Dauer. Genau das
        ergibt eine Klemmung, deren Grenzen zusammenfallen: dann liefert sie
        immer denselben Weg, egal was ``pan_rate`` sagt. Bestehende Projekte
        rendern damit bitgleich weiter.
        """
        if not isinstance(data, dict) or "pan_amount" not in data:
            return data
        data = dict(data)
        alt = float(data.pop("pan_amount"))
        data.setdefault("pan_total", (2.0 * alt, 2.0 * alt))
        return data

    @field_validator("zoom_total", "pan_total")
    @classmethod
    def _grenzen_geordnet(cls, v, info):
        if v[0] > v[1]:
            raise ValueError(f"{info.field_name} muss [min, max] mit min <= max sein")
        return v


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


class TitleDefaults(BaseModel):
    """Gestalt und Choreografie der Titelfolien (``docs/briefing-titelfolien.md``)."""

    model_config = ConfigDict(extra="forbid")

    #: Standzeit in Beat-Regionen. In free-Regionen gilt ``still_seconds`` —
    #: eine Titelfolie steht dort so lange wie die Bilder um sie herum.
    beats: float = 12.0
    #: Musik ist in Phrasen gegliedert; eine Zaesur mitten in der Phrase faellt
    #: als Fehler auf. Titel beginnen deshalb auf einem Vielfachen davon.
    phrase_beats: int = 8
    #: Pfad zur Schriftdatei. ``SLIDESHOW_FONT`` gewinnt (analog SLIDESHOW_MELT).
    font: str = "auto"
    #: Bewegung der Folien: ``kenburns`` faehrt wie ueber jedem Standbild,
    #: ``none`` laesst sie stillstehen.
    #:
    #: Der Text ist in die Pixel eingebrannt und faehrt deshalb mit — bei duennen
    #: Schriften flimmert er dabei, und lesen laesst sich ein stehender Satz
    #: ohnehin ruhiger. Aufgeloest wird das nicht im Renderer, sondern als
    #: gewoehnliches ``kb:`` am Segment (:func:`slideshow.titles.title_kb`).
    motion: Literal["kenburns", "none"] = "kenburns"
    #: Versalhoehe der Ueberschrift als Anteil der Bildhoehe.
    size: float = 0.075
    subtitle_scale: float = 0.42
    #: Blur-Sigma auf 7680er Basis — derselbe Wert wie das Hochformat-Komposit.
    blur: float = 60.0
    #: Startwert der Abdunklung; der Generator fuehrt ihn nach, bis der
    #: gemessene Kontrast ``min_contrast`` traegt.
    darken: float = 0.55
    min_contrast: float = 4.5
    #: Safe Area ringsum, Anteil der Kante. Ueberlebt TV-Overscan.
    safe: float = 0.10
    #: Blendenchoreografie, als Faktor auf die Standardblende. Der Film atmet
    #: in die Zaesur ein (laenger hinein) und setzt danach neu an.
    xfade_in: float = 1.5
    xfade_out: float = 1.0
    #: Blende *aus* einer Folie mit ``bg: auto`` heraus — die Fokusblende
    #: (Entscheidung 5d). Sie loest den unscharfen Hintergrund in dasselbe
    #: Bild scharf auf und braucht dafuer mehr Zeit als ein Bildwechsel.
    xfade_focus: float = 2.0

    @field_validator("phrase_beats")
    @classmethod
    def _phrase_positiv(cls, v: int) -> int:
        if v < 1:
            raise ValueError("phrase_beats muss >= 1 sein")
        return v


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
    #: Ausblende am Filmende, in Sekunden — Bild nach Schwarz und Ton nach
    #: Stille, gleichzeitig. 0 schaltet sie ab. Wird die Tonspur gekuerzt,
    #: bricht die Musik sonst mitten im Stueck ab.
    fade_out: float = 1.5
    kb: KBDefaults = Field(default_factory=KBDefaults)
    xfade: XfadeDefaults = Field(default_factory=XfadeDefaults)
    title: TitleDefaults = Field(default_factory=TitleDefaults)

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


class TitleSegment(BaseModel):
    """Eine Titel- oder Zwischenfolie.

    Eigener Typ statt eines ``title:``-Blocks am Still (Entscheidung 2a): eine
    Folie ohne Ueberschrift scheitert damit beim Laden mit Pfad und Zeile statt
    beim Rendern mit einem leeren Bild.

    Einen ``src``-Schluessel gibt es bewusst **nicht**. Der Pfad des gebackenen
    Assets ergibt sich aus dem Inhalt dieses Segments
    (:func:`slideshow.titles.title_asset`) — stuende er zusaetzlich in der
    Datei, gaebe es zwei Wahrheiten, und eine von Hand geaenderte Ueberschrift
    wuerde weiter auf das alte Bild zeigen.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["title"] = "title"
    title: str
    subtitle: str | None = None
    #: ``auto`` (erstes Bild des neuen Abschnitts, unscharf) | Pfad | ``#rrggbb``
    #: | ``none``. ``build`` schreibt den aufgeloesten Wert zurueck.
    bg: str = "auto"
    #: Wie beim Still — ``beats`` nur in Beat-Regionen, ``dur`` in free-Regionen.
    beats: float | None = None
    dur: float | None = None
    hold: bool = False
    snap_back: bool | None = None
    #: ``lower-third`` ist fuer Stufe 2 reserviert und rendert vorerst wie ``card``.
    style: Literal["card", "lower-third"] = "card"
    #: Bewegung nur fuer diese Folie; ohne Angabe gilt ``defaults.title.motion``.
    motion: Literal["kenburns", "none"] | None = None
    kb: KBSpec | None = None

    @field_validator("title")
    @classmethod
    def _ueberschrift_pflicht(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Titelfolie ohne Ueberschrift. Einen Ortsnamen kann "
                             "das Werkzeug nicht erfinden — bitte ausfuellen.")
        return v

    @field_validator("bg")
    @classmethod
    def _hintergrund_plausibel(cls, v: str) -> str:
        if v.startswith("#") and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError(f"unlesbare Farbangabe {v!r} (erwartet #rrggbb)")
        return v


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


Segment = Annotated[StillSegment | TitleSegment | ClipSegment | XfadeSegment,
                    Field(discriminator="type")]


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
# Kapitel (`chapters.yaml`)
# --------------------------------------------------------------------------

class Chapter(BaseModel):
    """Ein Kapitel der Reise — Eingabe fuer ``slideshow build``.

    Verankert wird an **Medien-IDs**, nicht an Segmentindizes oder Zeiten
    (Entscheidung 6b): IDs sind gegen Umsortieren und gegen zusaetzliche Bilder
    stabil, alles andere verrutscht beim naechsten ``build``.
    """

    model_config = ConfigDict(extra="forbid")

    #: Medien-ID, **vor** der die Folie steht.
    before: str | None = None
    #: Position in der Medienfolge; ``0`` ist der Auftakt vor allem Material.
    at: int | None = None
    title: str
    subtitle: str | None = "auto"
    #: ``auto`` | **Medien-ID** | ``cache/…``-Pfad | ``#rrggbb`` | ``none``.
    #: Die ID ist die Bequemlichkeit dieser Datei — in ``chapters.yaml`` stehen
    #: sonst nur IDs, und einen Cache-Pfad muesste man nachschlagen. ``build``
    #: loest sie auf und schreibt den Pfad nach ``edit.yaml``.
    bg: str = "auto"
    beats: float | None = None
    dur: float | None = None
    style: Literal["card", "lower-third"] = "card"
    #: Wie am Segment; ohne Angabe gilt ``defaults.title.motion``.
    motion: Literal["kenburns", "none"] | None = None
    kb: KBSpec | None = None

    @field_validator("title")
    @classmethod
    def _ueberschrift_pflicht(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "Kapitel ohne Ueberschrift. Aus Koordinaten laesst sich ohne Netz "
                "kein Ortsname gewinnen, und ein geratener Name ist schlimmer als "
                "keiner — bitte `title:` ausfuellen.")
        return v

    @model_validator(mode="after")
    def _genau_ein_anker(self) -> "Chapter":
        if (self.before is None) == (self.at is None):
            raise ValueError("genau eines von `before:` (Medien-ID) und `at:` "
                             "(Position) angeben")
        return self


class ChapterList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    chapters: list[Chapter] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "ChapterList":
        return _load_yaml_model(cls, path, was="Kapiteldatei", schluessel="chapters")


# --------------------------------------------------------------------------
# order.yaml — die Reihenfolge von Hand
# (docs/briefing-manuelle-reihenfolge.md)
# --------------------------------------------------------------------------

class OrderGroup(BaseModel):
    """Ein Block der manuellen Reihenfolge — die Arbeitseinheit beim Sortieren.

    Der ``name`` erscheint **nicht im Film** (Entscheidung 2). Es liegt nahe,
    aus ``name: am-wasser`` von selbst eine Folie "Am Wasser" zu machen; genau
    das darf nicht passieren, denn dann gaebe es zwei Wege, eine Ueberschrift zu
    erklaeren — ``order.yaml`` und ``chapters.yaml`` —, die auseinanderlaufen
    koennen. Der Text einer Folie wohnt in ``chapters.yaml``, dort und nur dort.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    items: list[str] = Field(default_factory=list)


class OrderList(BaseModel):
    """``order.yaml`` — die Reihenfolge der Medien.

    Eingabe wie ``chapters.yaml``, nicht Erzeugnis: ``build`` liest die Datei
    und schreibt sie nie. Verankert wird an **Medien-IDs**, weil die nur am
    Dateinamen haengen und damit als einzige gegen ein erneutes ``probe``
    stabil sind.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    #: Was mit Material geschieht, das die Datei nicht nennt (Entscheidung 3).
    #: Vorgabe ``error``: der teuerste Fehler dieser Datei ist das *stille*
    #: Verschwinden von Bildern — man rendert eine Stunde und zaehlt hinterher.
    rest: Literal["error", "append", "drop"] = "error"
    #: Bloecke mit Namen. Genau eines von ``groups`` und ``order``.
    groups: list[OrderGroup] | None = None
    #: Flache Kurzform; bedeutet dasselbe wie eine einzige namenlose Gruppe.
    #: Zwei Formen kosten zehn Zeilen und ersparen dem, der nur drei Bilder
    #: tauschen will, eine Verschachtelung, die er nicht braucht.
    order: list[str] | None = None

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"order.yaml hat Version {v}, unterstuetzt wird 1")
        return v

    @model_validator(mode="after")
    def _genau_eine_form(self) -> "OrderList":
        if (self.groups is None) == (self.order is None):
            raise ValueError("genau eines von `groups:` (Bloecke mit Namen) und "
                             "`order:` (flache Liste) angeben")
        return self

    @property
    def blocks(self) -> list[OrderGroup]:
        """Beide Formen als dasselbe: eine Liste von Bloecken."""
        if self.groups is not None:
            return self.groups
        return [OrderGroup(items=list(self.order or []))]

    @classmethod
    def load(cls, path: Path) -> "OrderList":
        return _load_yaml_model(cls, path, was="Reihenfolgedatei", schluessel="groups")


def _load_yaml_model(cls, path: Path, *, was: str, schluessel: str):
    """YAML laden, Zeilennummern behalten, gegen das Modell pruefen.

    Gemeinsam fuer alle *Eingabe*-Dateien neben der Edit-List. Der Zweck ist
    nicht Kuerze, sondern dass ein Tippfehler in jeder dieser Dateien dieselbe
    Meldung mit Datei und Zeile ergibt statt einer je Dateityp.
    """
    p = Path(path)
    if not p.exists():
        raise SchemaError(f"{was} fehlt: {p}")
    try:
        data = yaml.load(p.read_text(encoding="utf-8"), Loader=_LineLoader)
    except yaml.YAMLError as exc:
        line = getattr(getattr(exc, "problem_mark", None), "line", None)
        raise SchemaError(f"{p.name} ist kein gueltiges YAML: {exc}", file=str(p),
                          line=(line + 1) if line is not None else None) from exc
    if not isinstance(data, dict):
        raise SchemaError(f"{p.name} muss ein Mapping auf oberster Ebene sein "
                          f"(`{schluessel}:` als Schluessel)", file=str(p))
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
_DISCRIMINATORS = {"still", "title", "clip", "xfade"}


def _loc_to_path(loc: tuple) -> str:
    parts: list[str] = []
    for pos, item in enumerate(loc):
        if isinstance(item, int):
            if parts:
                parts[-1] += f"[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            name = str(item)
            if name.endswith("Segment") or name.startswith("function-"):
                continue
            # Der Discriminator steht immer *vor* dem eigentlichen Feldpfad,
            # nie am Ende. Ohne diese Bedingung frisst die Regel den Feldnamen
            # ``title`` — der heisst zufaellig wie sein eigener Typ, und ein
            # ``Chapter`` ohne Ueberschrift meldete dann nur ``chapters[1]``.
            if (name in _DISCRIMINATORS and parts and parts[-1].endswith("]")
                    and pos < len(loc) - 1):
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
