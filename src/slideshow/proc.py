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
