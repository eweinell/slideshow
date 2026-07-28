"""Konsolen- und Datei-Logging. Jedes Subkommando schreibt nach ``logs/``."""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from .paths import Project

_console: Console | None = None


def console() -> Console:
    global _console
    if _console is None:
        _console = Console(highlight=False, soft_wrap=False)
    return _console


def setup_logging(project: Project, subcommand: str, *, verbose: bool = False,
                  quiet: bool = False) -> Path:
    """Richtet Logging ein und gibt den Pfad des Logfiles zurueck."""
    project.logs.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    logfile = project.logs / f"{subcommand}-{stamp}.log"

    root = logging.getLogger("slideshow")
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)

    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(fh)

    ch = RichHandler(console=console(), rich_tracebacks=False, show_path=False,
                     show_time=False, markup=False)
    ch.setLevel(logging.ERROR if quiet else (logging.DEBUG if verbose else logging.INFO))
    ch.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(ch)
    root.propagate = False

    root.debug("Logfile: %s", logfile)
    return logfile
