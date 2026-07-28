"""Aufrufe externer Prozesse mit vollstaendigem Logging (Abschnitt 11).

Jeder Aufruf loggt das exakte Kommando; bei Fehlern werden die letzten
stderr-Zeilen im Klartext angezeigt, nicht nur der Returncode.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ExternalToolError, SlideshowError

log = logging.getLogger("slideshow.proc")

#: Wie viele stderr-Zeilen eine Fehlermeldung zeigt.
STDERR_TAIL_LINES = 20


@dataclass
class RunResult:
    cmd: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def stderr_tail(self, n: int = STDERR_TAIL_LINES) -> str:
        return "\n".join(self.stderr.rstrip().splitlines()[-n:])


@dataclass
class DryRun:
    """Sammelt geplante Kommandos, statt sie auszufuehren (``--dry-run``)."""

    enabled: bool = False
    commands: list[list[str]] = field(default_factory=list)

    def record(self, cmd: list[str]) -> None:
        self.commands.append(list(cmd))

    def as_text(self) -> str:
        return "\n".join(shlex.join(c) for c in self.commands)


def quote(cmd: list[str]) -> str:
    return shlex.join(str(c) for c in cmd)


def run(cmd: list[str], *, check: bool = True, timeout: float | None = None,
        cwd: str | os.PathLike | None = None, stdin: str | None = None,
        env: dict[str, str] | None = None, logfile: str | None = None) -> RunResult:
    """Fuehrt ``cmd`` aus und gibt stdout/stderr zurueck."""
    cmd = [str(c) for c in cmd]
    log.debug("exec: %s", quote(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            timeout=timeout, cwd=str(cwd) if cwd else None, input=stdin,
            env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError as exc:
        raise SlideshowError(f"Programm nicht gefunden: {cmd[0]} ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExternalToolError(cmd, -1, f"Timeout nach {exc.timeout} s", logfile) from exc

    result = RunResult(cmd, proc.returncode, proc.stdout or "", proc.stderr or "")
    if result.stderr.strip():
        log.debug("stderr: %s", result.stderr_tail())
    if check and not result.ok:
        raise ExternalToolError(cmd, result.returncode, result.stderr_tail(), logfile)
    return result


def which(name: str) -> str | None:
    return shutil.which(name)


def have(name: str) -> bool:
    return shutil.which(name) is not None


# --------------------------------------------------------------------------
# Werkzeugsuche jenseits des PATH
# --------------------------------------------------------------------------

def _dirs(*names: str) -> list[Path]:
    """Existierende Verzeichnisse aus Umgebungsvariablen, ohne Duplikate."""
    out: list[Path] = []
    for name in names:
        raw = os.environ.get(name)
        if raw:
            p = Path(raw)
            if p not in out:
                out.append(p)
    return out


def _scoop_roots() -> list[Path]:
    roots = _dirs("SCOOP", "SCOOP_GLOBAL")
    roots.append(Path.home() / "scoop")
    programdata = _dirs("ProgramData")
    roots += [p / "scoop" for p in programdata]
    return roots


def _program_dirs() -> list[Path]:
    dirs = _dirs("ProgramFiles", "ProgramFiles(x86)")
    dirs += [p / "Programs" for p in _dirs("LOCALAPPDATA")]
    return dirs


def _melt_candidates() -> list[Path]:
    """Wo ``melt`` liegt, wenn es nicht im PATH steht.

    ``melt`` wird praktisch nie einzeln installiert, sondern kommt als
    Beigabe von Kdenlive oder Shotcut mit — und beide legen nur ihre
    Haupt-Exe in den PATH. Bei scoop etwa entsteht ein Shim fuer
    ``kdenlive.exe``, waehrend ``melt.exe`` unerreichbar in ``bin/`` liegt.
    """
    out: list[Path] = []
    for root in _scoop_roots():
        out.append(root / "apps" / "kdenlive" / "current" / "bin" / "melt.exe")
        out.append(root / "apps" / "shotcut" / "current" / "melt.exe")
    for base in _program_dirs():
        out.append(base / "kdenlive" / "bin" / "melt.exe")
        out.append(base / "Shotcut" / "melt.exe")
    # Linux/WSL: aus dem Distributionspaket, oder ein entpacktes AppImage.
    out += [Path("/usr/bin/melt"), Path("/usr/local/bin/melt")]
    return out


#: Zusaetzliche Suchorte pro Werkzeug. Lazy, weil die Umgebungsvariablen
#: erst zur Laufzeit feststehen (und Tests sie umbiegen duerfen).
_EXTRA_LOCATIONS = {
    "melt": _melt_candidates,
}


def _usable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def resolve_tool(name: str) -> str | None:
    """Vollstaendiger Pfad zu ``name``, oder None.

    Drei Stufen, in dieser Reihenfolge:

    1. ``SLIDESHOW_<NAME>`` — der explizite Override gewinnt immer, sonst
       liesse sich eine falsche Version im PATH nicht uebersteuern.
    2. der PATH (``shutil.which``),
    3. bekannte Installationsorte aus :data:`_EXTRA_LOCATIONS`.

    Stufe 3 existiert, weil ``shutil.which`` allein bei mitgelieferten
    Werkzeugen wie ``melt`` regelmaessig danebengreift und der Report dann
    zur Installation von etwas raet, das laengst installiert ist.
    """
    override = os.environ.get(f"SLIDESHOW_{name.upper().replace('-', '_')}")
    if override:
        if _usable(Path(override)):
            return str(Path(override))
        found = shutil.which(override)
        if found:
            return found
        log.warning("SLIDESHOW_%s zeigt auf nichts Ausfuehrbares: %s",
                    name.upper(), override)

    found = shutil.which(name)
    if found:
        return found

    for cand in _EXTRA_LOCATIONS.get(name, list)():
        if _usable(cand):
            log.debug("%s ausserhalb des PATH gefunden: %s", name, cand)
            return str(cand)
    return None


# --------------------------------------------------------------------------
# ffprobe-Helfer
# --------------------------------------------------------------------------

def ffprobe_json(path: str | os.PathLike, *, extra: list[str] | None = None,
                 ffprobe: str = "ffprobe") -> dict:
    """``ffprobe -print_format json`` inklusive ``stream_side_data`` (Abschnitt 4)."""
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-show_entries", "stream_side_data",
    ]
    if extra:
        cmd += extra
    cmd += [str(path)]
    res = run(cmd)
    try:
        return json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ExternalToolError(cmd, 0, f"ffprobe lieferte kein gueltiges JSON: {exc}") from exc


def ffprobe_packets(path: str | os.PathLike, *, count: int = 300,
                    ffprobe: str = "ffprobe") -> list[dict]:
    """Paket-Timestamps eines Ausschnitts — Basis der VFR-Bestaetigung (4)."""
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,dts_time,duration_time",
        "-read_intervals", f"%+#{count}", "-print_format", "json", str(path),
    ]
    res = run(cmd, check=False)
    if not res.ok:
        return []
    try:
        return json.loads(res.stdout or "{}").get("packets", [])
    except json.JSONDecodeError:
        return []
