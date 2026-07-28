"""Fehlertypen.

Grundprinzip 4: fail loud, fail early. Jede Phase validiert ihre Eingaben und
bricht mit praeziser Meldung ab, statt spaeter einen kaputten Master zu bauen.
"""

from __future__ import annotations


class SlideshowError(Exception):
    """Basisklasse. Wird von der CLI als saubere Fehlermeldung ausgegeben,
    nicht als Traceback."""

    exit_code = 1


class PreflightError(SlideshowError):
    """Ein harter FAIL aus ``doctor``."""

    exit_code = 2


class SchemaError(SlideshowError):
    """Schema- oder Semantikfehler in manifest.json / edit.yaml.

    Traegt den YAML-Pfad (``segments[12].kb.z``), damit die Meldung auf die
    Stelle zeigt und nicht nur "invalid config" sagt.
    """

    exit_code = 3

    def __init__(self, message: str, *, path: str | None = None, file: str | None = None,
                 line: int | None = None):
        self.path = path
        self.file = file
        self.line = line
        super().__init__(message)

    def __str__(self) -> str:
        where = []
        if self.file:
            loc = self.file
            if self.line is not None:
                loc += f":{self.line}"
            where.append(loc)
        if self.path:
            where.append(self.path)
        prefix = " ".join(where)
        base = super().__str__()
        return f"{prefix}: {base}" if prefix else base


class ExternalToolError(SlideshowError):
    """Ein externer Prozess (ffmpeg, exiftool, ...) ist fehlgeschlagen.

    Zeigt die letzten stderr-Zeilen im Klartext, nicht nur den Returncode.
    """

    exit_code = 4

    def __init__(self, cmd: list[str], returncode: int, stderr_tail: str,
                 logfile: str | None = None):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        self.logfile = logfile
        shown = " ".join(cmd)
        if len(shown) > 400:
            shown = shown[:400] + " ..."
        msg = [f"Externer Prozess fehlgeschlagen (exit {returncode}):", f"  {shown}"]
        if stderr_tail.strip():
            msg.append("  --- letzte stderr-Zeilen ---")
            msg.extend(f"  {ln}" for ln in stderr_tail.rstrip().splitlines())
        if logfile:
            msg.append(f"  vollstaendiges Log: {logfile}")
        super().__init__("\n".join(msg))


class UsageError(SlideshowError):
    """Falsche Bedienung der CLI."""

    exit_code = 64
