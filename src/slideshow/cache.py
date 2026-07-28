"""Cache-Keys (Abschnitt 11).

``cache_key = blake2b(inhaltshash der quelle(n) + parameter-hash)``, wobei der
Parameter-Hash den vollstaendigen Filtergraph-String, alle Encoder-Parameter
und die ffmpeg-Major-Version einschliesst. Sonst ueberleben stale Segmente ein
ffmpeg-Update oder eine Default-Aenderung unbemerkt.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("slideshow.cache")

_DIGEST_SIZE = 16          # 128 bit reichen als Cache-Key
_CHUNK = 1 << 20


def _blake(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=_DIGEST_SIZE).hexdigest()


def canonical(obj) -> str:
    """Stabile JSON-Serialisierung — Grundlage jedes Parameter-Hashes."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                      default=str)


def param_hash(params) -> str:
    return _blake(canonical(params).encode("utf-8"))


class HashIndex:
    """Memoisiert Inhaltshashes ueber (Groesse, mtime_ns).

    100 x 20 MP jedes Mal komplett zu hashen kostet spuerbar Zeit; der Index
    macht wiederholte Laeufe billig, bleibt aber inhaltsbasiert, weil jede
    Aenderung an Groesse oder mtime neu hasht.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self._data: dict[str, list] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.debug("Hash-Index unlesbar, wird neu aufgebaut: %s", self.path)
            self._data = {}

    def save(self) -> None:
        if not self.path or not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data), encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = False

    def file_hash(self, path: str | os.PathLike) -> str:
        p = Path(path)
        try:
            st = p.stat()
        except OSError as exc:
            raise FileNotFoundError(f"Quelldatei fuer Cache-Key fehlt: {p}") from exc
        key = str(p)
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
                return hit[2]
        digest = hash_file(p)
        with self._lock:
            self._data[key] = [st.st_size, st.st_mtime_ns, digest]
            self._dirty = True
        return digest


def hash_file(path: str | os.PathLike) -> str:
    h = hashlib.blake2b(digest_size=_DIGEST_SIZE)
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def cache_key(source_hashes: list[str], params) -> str:
    """Verbindet Quell- und Parameterhash zum endgueltigen Key.

    Die Quellhashes gehen in gegebener Reihenfolge ein: bei einem xfade sind
    das die beiden Nachbarn, und deren Reihenfolge ist bedeutungstragend.
    """
    joined = "|".join(source_hashes)
    return _blake(f"{joined}#{param_hash(params)}".encode("utf-8"))
