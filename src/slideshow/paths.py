"""Projektlayout und Pfaduebersetzung zwischen WSL und Windows.

Alle Pfade in Manifest und Edit-List werden relativ zum Projektroot und mit
POSIX-Trennern gespeichert, nie absolut. Das haelt das Projekt zwischen WSL
und Windows portabel (Abschnitt 2).
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import SlideshowError


@functools.lru_cache(maxsize=1)
def is_wsl() -> bool:
    """True, wenn wir unter WSL laufen (``/proc/version`` enthaelt ``microsoft``)."""
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="replace") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


@functools.lru_cache(maxsize=1)
def is_windows() -> bool:
    return os.name == "nt"


@functools.lru_cache(maxsize=1)
def platform_label() -> str:
    """Benennt die Seite, die gerade geprueft/ausgefuehrt wird."""
    if is_windows():
        return "Windows (Render-Toolchain)"
    if is_wsl():
        return "WSL (Analyse-Toolchain)"
    return "Linux (Analyse-Toolchain)"


def to_windows_path(path: str | os.PathLike) -> str:
    """WSL-Pfad -> Windows-Pfad via ``wslpath -w``."""
    p = str(path)
    if not is_wsl():
        return p
    exe = shutil.which("wslpath")
    if not exe:
        return p
    try:
        out = subprocess.run([exe, "-w", p], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return p
    return out.stdout.strip() or p


def to_unix_path(path: str | os.PathLike) -> str:
    """Windows-Pfad -> WSL-Pfad via ``wslpath -u``."""
    p = str(path)
    if not is_wsl():
        return p
    exe = shutil.which("wslpath")
    if not exe:
        return p
    try:
        out = subprocess.run([exe, "-u", p], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return p
    return out.stdout.strip() or p


@dataclass(frozen=True)
class Project:
    """Das Projektverzeichnis mit seinem festen Layout."""

    root: Path

    @classmethod
    def open(cls, root: str | os.PathLike | None = None, *, create: bool = False) -> "Project":
        r = Path(root or os.getcwd()).expanduser().resolve()
        if not r.exists():
            if not create:
                raise SlideshowError(f"Projektverzeichnis existiert nicht: {r}")
            r.mkdir(parents=True, exist_ok=True)
        return cls(r)

    # -- feste Orte -----------------------------------------------------
    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def edit(self) -> Path:
        return self.root / "edit.yaml"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def segments(self) -> Path:
        return self.cache / "segments"

    def ensure_dirs(self) -> None:
        for d in (self.cache, self.out, self.logs, self.segments):
            d.mkdir(parents=True, exist_ok=True)

    # -- Pfadumrechnung -------------------------------------------------
    def rel(self, path: str | os.PathLike) -> str:
        """Dateisystempfad -> projektrelativer POSIX-Pfad.

        Liegt der Pfad ausserhalb des Projektroots (typisch: Quellmaterial),
        wird ein relativer Pfad mit ``..`` erzeugt, damit auch das portabel
        bleibt, solange die Verzeichnisse zueinander gleich liegen.

        Ein *relativer* Eingabepfad wird gegen das **aktuelle Verzeichnis**
        aufgeloest, nicht gegen den Projektroot: die Argumente kommen von der
        Kommandozeile, und die meint die Shell des Nutzers. Gegen den Root
        aufgeloest wuerde ``--project foo probe foo/`` die Bilder als
        ``foo/DSC.jpg`` (= ``foo/foo/DSC.jpg``) ablegen — probe meldet Erfolg,
        preprocess findet die Quellen zwei Phasen spaeter nicht mehr.
        Der Gegenpart ``abs()`` bleibt bewusst rootbezogen: er bekommt
        Manifest-Eintraege, und die *sind* projektrelativ.
        """
        p = Path(path).expanduser()
        p = p if p.is_absolute() else (Path.cwd() / p)
        p = _norm(p)
        try:
            return PurePosixPath(p.relative_to(self.root)).as_posix()
        except ValueError:
            # relpath() liefert Systemtrenner; PurePosixPath wuerde ein
            # ``..\material\DSC.jpg`` als *einen* Namen durchreichen, statt es
            # zu zerlegen — die Backslashes landeten so im Manifest.
            return Path(os.path.relpath(p, self.root)).as_posix()

    def abs(self, relpath: str | os.PathLike) -> Path:
        """Projektrelativer Pfad -> absoluter Pfad auf dieser Plattform."""
        p = Path(str(relpath).replace("\\", "/"))
        return p if p.is_absolute() else _norm(self.root / p)


def _norm(p: Path) -> Path:
    """resolve() ohne Symlink-Aufloesung zu erzwingen (Windows-Netzpfade)."""
    try:
        return p.resolve()
    except OSError:
        return Path(os.path.normpath(str(p)))
