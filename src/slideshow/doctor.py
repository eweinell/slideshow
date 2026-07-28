"""Phase 0 — Preflight (Abschnitt 3).

``doctor`` prueft die Umgebung, in der es laeuft — unter WSL die
Analyse-Toolchain, unter Windows die Render-Toolchain — und gibt pro
Fehlschlag einen kopierbaren Installationsbefehl aus.

Wichtigste Eigenschaft: das hier darf auf einem System *ohne* ffmpeg nicht mit
einem Traceback abstuerzen (Abnahmekriterium 1). Jede Sonde ist deshalb
einzeln gekapselt.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import __version__
from .encoders import EncoderChoice
from .errors import PreflightError
from .logging_setup import console
from .paths import Project, is_windows, is_wsl, platform_label
from .proc import have, resolve_tool, run, which

log = logging.getLogger("slideshow.doctor")

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_ORDER = {OK: 0, WARN: 1, FAIL: 2}

#: Installationsvorschlaege. Die winget-IDs aendern sich; deshalb der Hinweis,
#: sie vorher mit ``winget search`` zu verifizieren.
WINGET_HINT = "IDs ggf. mit `winget search <name>` verifizieren, sie aendern sich."

_INSTALL = {
    "ffmpeg": ("winget install Gyan.FFmpeg",
               "sudo apt install -y ffmpeg"),
    "ffprobe": ("winget install Gyan.FFmpeg",
                "sudo apt install -y ffmpeg"),
    "python3": ("winget install Python.Python.3.12",
                "sudo apt install -y python3 python3-venv"),
    "exiftool": ("winget install OliverBetz.ExifTool",
                 "sudo apt install -y libimage-exiftool-perl"),
    "magick": ("winget install ImageMagick.ImageMagick",
               "sudo apt install -y imagemagick"),
    # melt kommt als Beigabe von Kdenlive/Shotcut und landet dabei nicht im
    # PATH; resolve_tool() findet es trotzdem. Der Hinweis nennt deshalb auch
    # den Override fuer den Fall, dass es woanders liegt.
    "melt": ("winget install KDE.Kdenlive   # oder: scoop install extras/kdenlive\n"
             "  # liegt melt woanders: setx SLIDESHOW_MELT \"C:\\Pfad\\zu\\melt.exe\"",
             "sudo apt install -y melt"),
    "nvidia-smi": ("NVIDIA-Treiber >= 550 installieren (GeForce Experience oder nvidia.com)",
                   "Treiber gehoert auf die Windows-Seite; unter WSL nicht noetig"),
}

_PY_PKGS = "pip install librosa soundfile numpy pyyaml pillow rich pydantic"


def install_hint(tool: str) -> str:
    win, nix = _INSTALL.get(tool, ("", ""))
    return win if is_windows() else nix


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""

    @property
    def hard(self) -> bool:
        return self.status == FAIL


@dataclass
class Capabilities:
    """Was die Umgebung tatsaechlich kann. Wird gecacht und von den spaeteren
    Phasen gelesen (Encoderwahl, Worker-Zahl, hwaccel-Entscheidungen)."""

    platform: str = ""
    ffmpeg: str = ""
    ffprobe: str = ""
    ffmpeg_version: list[int] = field(default_factory=list)
    encoders: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    hwaccels: list[str] = field(default_factory=list)
    zoompan_10bit: bool = False
    nvenc_10bit: bool = False
    av1_nvenc: bool = False
    nvdec_422_10bit: bool = False
    nvenc_sessions: int = 0
    #: ``libplacebo`` steht in der Filterliste, braucht zum Laufen aber ein
    #: Vulkan-Geraet. Ob es *benutzbar* ist, sagt nur der Praxistest.
    libplacebo_usable: bool = False
    zscale_usable: bool = False
    gpu_name: str = ""
    gpu_vram_mb: int = 0
    cpu_cores: int = 0
    exiftool: bool = False
    magick: bool = False
    #: Pfad zur melt-Binary (nicht nur ein Flag): sie liegt haeufig ausserhalb
    #: des PATH, ein spaeterer MLT-Renderpfad muss sie also voll qualifiziert
    #: aufrufen koennen.
    melt: str = ""
    librosa: bool = False
    aubio: bool = False
    deep: bool = False

    @property
    def ffmpeg_major(self) -> int:
        return self.ffmpeg_version[0] if self.ffmpeg_version else 0

    def has_filter(self, name: str) -> bool:
        return name in self.filters

    def has_encoder(self, name: str) -> bool:
        return name in self.encoders

    def encoder_choice(self) -> EncoderChoice:
        return EncoderChoice(
            hevc_nvenc=self.has_encoder("hevc_nvenc") and self.nvenc_10bit,
            h264_nvenc=self.has_encoder("h264_nvenc"),
            av1_nvenc=self.av1_nvenc,
            nvenc_10bit=self.nvenc_10bit,
            libx265=self.has_encoder("libx265"),
            libx264=self.has_encoder("libx264"),
        )

    def tonemap_chain(self, kind: str = "hlg") -> str | None:
        """Filterkette fuer HDR->SDR (5.2), oder None wenn nichts Sauberes da ist.

        Zwei Dinge, die in der Kette aus dem Briefing fehlen und ohne die sie
        in der Praxis abbricht:

        1. **Die Eingangs-Charakteristik muss explizit stehen.** ``zscale``
           bricht sonst mit ``code 3074: no path between colorspaces`` ab,
           sobald einer der Tags am Quellstream fehlt — was bei Handymaterial
           regelmaessig vorkommt. ``tin``/``min``/``pin`` setzen den Pfad fest.
        2. **``npl`` haengt vom Format ab.** HLG referenziert 1000 nits; mit
           ``npl=100`` bliebe das Bild flau — genau der Fehler, den
           Abnahmekriterium 4 ausschliesst. PQ kodiert absolute Luminanz, dort
           ist 100 der uebliche Zielwert.

        Es reicht ausserdem nicht, den Filter in der Liste zu finden:
        ``libplacebo`` ist in Standard-Builds einkompiliert, scheitert ohne
        Vulkan-Geraet aber zur Laufzeit — und zwar mit einer *leeren*
        Ausgabedatei statt einer klaren Meldung. Deshalb entscheidet der
        Praxistest.
        """
        if self.libplacebo_usable:
            return ("libplacebo=tonemapping=bt.2390:colorspace=bt709:color_primaries=bt709"
                    ":color_trc=bt709:range=tv:format=yuv420p10le")
        if self.zscale_usable:
            tin = "smpte2084" if kind == "pq" else "arib-std-b67"
            npl = 100 if kind == "pq" else 1000
            return (f"zscale=tin={tin}:min=bt2020nc:pin=bt2020:t=linear:npl={npl},"
                    f"tonemap=hable:desat=0,"
                    f"zscale=p=bt709:t=bt709:m=bt709:r=tv")
        return None

    def max_workers(self, requested: int | None = None) -> int:
        """N unabhaengige ffmpeg-Prozesse, begrenzt durch
        min(CPU-Kerne, NVENC-Session-Limit) (8.1)."""
        n = self.cpu_cores or (os.cpu_count() or 4)
        if self.nvenc_sessions:
            n = min(n, self.nvenc_sessions)
        if requested:
            n = min(n, requested) if requested > 0 else n
        return max(1, n)


# --------------------------------------------------------------------------
# Einzelsonden — jede fuer sich gekapselt
# --------------------------------------------------------------------------

def _version_tuple(text: str) -> list[int]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return []
    return [int(g) for g in m.groups() if g is not None]


def _tool_version(exe: str, args: list[str]) -> str | None:
    """Gibt die erste Ausgabezeile zurueck, oder None wenn das Tool fehlt.

    Aufgerufen wird der von :func:`resolve_tool` gefundene Pfad, nicht der
    blosse Name — sonst scheitert die Abfrage bei allem, was ausserhalb des
    PATH liegt.
    """
    path = resolve_tool(exe)
    if not path:
        return None
    try:
        res = run([path, *args], check=False, timeout=30)
    except Exception as exc:                       # noqa: BLE001 - Sonde darf nie werfen
        log.debug("Versionsabfrage %s fehlgeschlagen: %s", exe, exc)
        return None
    text = (res.stdout or "") + (res.stderr or "")
    return text.strip().splitlines()[0] if text.strip() else ""


#: Die Flagspalte von ``-filters`` ist nicht ueber die ffmpeg-Versionen
#: stabil: bis 7.x drei Zeichen (``T.C``, mit Command-Support), ab 8.x nur
#: noch zwei (``TS``). Ein fester Zaehler laesst die Liste bei der jeweils
#: anderen Version *stumm* leer — und dann meldet der Report zoompan, xfade,
#: scale und format als fehlend, obwohl ein Full-Build installiert ist.
_FILTER_PATTERN = r"^\s*[A-Z.]{2,3}\s+(\S+)"
_ENCODER_PATTERN = r"^\s*[A-Z.]{6}\s+(\S+)"


def _ffmpeg_list(ffmpeg: str, what: str, pattern: str) -> list[str]:
    try:
        res = run([ffmpeg, "-hide_banner", what], check=False, timeout=60)
    except Exception:                              # noqa: BLE001
        return []
    # re.MULTILINE ist zwingend: ohne das matcht `^` nur den Anfang der
    # gesamten Ausgabe, und die Liste bleibt stumm leer.
    names = re.findall(pattern, res.stdout or "", re.MULTILINE)
    # Die Legende ueber der Tabelle ("T.. = Timeline support") hat dasselbe
    # Format wie eine Eintragszeile und liefert sonst ein "=" als Namen.
    return sorted({n for n in names if n[:1].isalnum()})


def _probe_zoompan_10bit(ffmpeg: str) -> bool:
    """Verifikationspunkt aus 8.1: unterstuetzt ``zoompan`` >8 Bit?

    Empirisch statt aus der Doku: wir schicken 10 Bit hinein und schauen, ob
    ffmpeg still einen ``auto_scale`` davorhaengt, der auf 8 Bit konvertiert.
    """
    try:
        res = run([
            ffmpeg, "-hide_banner", "-v", "verbose", "-f", "lavfi",
            "-i", "testsrc2=s=64x64:r=25", "-t", "0.1",
            "-vf", "format=yuv420p10le,zoompan=z=1.1:d=1:s=64x64:fps=25",
            "-f", "null", "-",
        ], check=False, timeout=60)
    except Exception:                              # noqa: BLE001
        return False
    err = res.stderr or ""
    if "Parsed_zoompan" not in err:
        return False
    # Welche auto_scale-Instanzen wurden unmittelbar vor zoompan eingefuegt?
    inserted = set(re.findall(
        r"auto-inserting filter '(auto_scale_\d+)' between the filter '[^']+' "
        r"and the filter 'Parsed_zoompan_\d+'", err))
    if not inserted:
        return True                                # keine Konvertierung noetig -> 10 Bit ok
    for name in inserted:
        for line in err.splitlines():
            if line.startswith(f"[{name} ") and "->" in line and "fmt:" in line:
                target = line.rsplit("fmt:", 1)[1].split()[0]
                if _bit_depth(target) >= 10:
                    return True
        return False
    return False


def _probe_filter(ffmpeg: str, chain: str) -> bool:
    """Laesst sich die Filterkette tatsaechlich ausfuehren?

    Ein Filter kann einkompiliert und trotzdem unbenutzbar sein, weil ihm zur
    Laufzeit ein Geraet fehlt (Vulkan bei ``libplacebo``). Ein einzelner Frame
    genuegt, um das zu klaeren.
    """
    try:
        res = run([ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi",
                   "-i", "testsrc2=s=64x64:r=25", "-t", "0.05",
                   "-vf", chain, "-f", "null", "-"], check=False, timeout=60)
    except Exception:                              # noqa: BLE001
        return False
    return res.ok


def _bit_depth(pix_fmt: str) -> int:
    m = re.search(r"(\d+)(?:le|be)?$", pix_fmt or "")
    if m and m.group(1) not in ("420", "422", "444", "24", "32"):
        return int(m.group(1))
    return 8


def _test_encode(ffmpeg: str, args: list[str]) -> bool:
    """Praxistest statt Modelltabelle (3.3)."""
    try:
        res = run([ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi",
                   "-i", "testsrc=s=1280x720:r=30", "-t", "1", *args,
                   "-f", "null", "-"], check=False, timeout=120)
    except Exception:                              # noqa: BLE001
        return False
    return res.ok


def _probe_gpu() -> tuple[str, int]:
    if not have("nvidia-smi"):
        return ("", 0)
    try:
        res = run(["nvidia-smi", "--query-gpu=name,memory.total",
                   "--format=csv,noheader,nounits"], check=False, timeout=30)
    except Exception:                              # noqa: BLE001
        return ("", 0)
    line = (res.stdout or "").strip().splitlines()
    if not line:
        return ("", 0)
    parts = [p.strip() for p in line[0].split(",")]
    name = parts[0] if parts else ""
    vram = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return (name, vram)


def _probe_nvenc_sessions(ffmpeg: str, codec: str, limit: int = 16) -> int:
    """Empirisch proben: N triviale Encodes parallel, hochzaehlen bis
    ``OpenEncodeSessionEx failed``. Gestartete Prozesse sauber terminieren."""
    best = 0
    for n in range(1, limit + 1):
        procs = []
        try:
            for _ in range(n):
                procs.append(subprocess.Popen(
                    [ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi",
                     "-i", "testsrc=s=256x144:r=30", "-t", "8",
                     "-c:v", codec, "-f", "null", "-"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True))
            time.sleep(1.5)
            failed = False
            for p in procs:
                if p.poll() is not None and p.returncode != 0:
                    err = (p.stderr.read() if p.stderr else "") or ""
                    if "OpenEncodeSessionEx" in err or "sessions" in err.lower():
                        failed = True
                    else:
                        failed = True
            if failed:
                return best or 1
            best = n
        except Exception:                          # noqa: BLE001
            return best or 1
        finally:
            for p in procs:
                if p.poll() is None:
                    p.terminate()
            for p in procs:
                try:
                    p.wait(timeout=5)
                except Exception:                  # noqa: BLE001
                    p.kill()
    return best


def _probe_nvdec_422(ffmpeg: str, workdir: Path) -> bool | None:
    """HEVC-4:2:2-10-Bit-Decode per NVDEC — die Falle aus 3.3.

    Gibt None zurueck, wenn der Test mangels 4:2:2-Encoder nicht gebaut werden
    konnte. Sonst True/False.
    """
    sample = workdir / "nvdec422_probe.mp4"
    try:
        built = run([ffmpeg, "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                     "-i", "testsrc2=s=640x360:r=25", "-t", "0.5",
                     "-c:v", "libx265", "-pix_fmt", "yuv422p10le",
                     "-x265-params", "log-level=error", str(sample)],
                    check=False, timeout=180)
        if not built.ok or not sample.exists():
            return None
        res = run([ffmpeg, "-hide_banner", "-v", "error", "-hwaccel", "cuda",
                   "-i", str(sample), "-f", "null", "-"], check=False, timeout=120)
        return res.ok
    except Exception:                              # noqa: BLE001
        return None
    finally:
        sample.unlink(missing_ok=True)


def _py_module(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)
    caps: Capabilities = field(default_factory=Capabilities)

    def add(self, name: str, status: str, detail: str = "", fix: str = "") -> None:
        self.checks.append(Check(name, status, detail, fix))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    @property
    def worst(self) -> str:
        return max((c.status for c in self.checks), key=lambda s: _ORDER[s], default=OK)


#: Hochzaehlen, sobald sich Felder *oder* die Erhebung aendern. Zwei Gruende:
#: ein alter Cache laedt Werte im alten Typ direkt in die Dataclass (``melt``
#: war ein bool — Dataclasses konvertieren nicht, der Fehler faellt erst
#: spaeter und an ganz anderer Stelle auf); und die ffmpeg-Signatur allein
#: erkennt keine korrigierte Sonde, sodass etwa eine faelschlich leere
#: Filterliste ohne ``--refresh`` beliebig lange weitergereicht wuerde.
_CACHE_VERSION = 3


def _cache_path(project: Project | None) -> Path | None:
    return (project.cache / "doctor.json") if project else None


def load_capabilities(project: Project | None, *, deep: bool = False,
                      refresh: bool = False) -> Capabilities:
    """Capabilities laden — aus dem Cache, wenn ffmpeg unveraendert ist.

    ``doctor`` laeuft implizit vor jedem Subkommando; die teuren GPU-Sonden
    duerfen dabei nicht jedes Mal neu laufen.
    """
    path = _cache_path(project)
    if path and path.exists() and not refresh:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("v") != _CACHE_VERSION:
                raise ValueError("Cache-Version veraltet")
            cached = Capabilities(**data["caps"])
            fresh_sig = _ffmpeg_signature()
            if data.get("sig") == fresh_sig and (cached.deep or not deep):
                return cached
        except Exception:                          # noqa: BLE001
            log.debug("doctor-Cache unlesbar, wird neu erhoben")
    caps = probe_capabilities(deep=deep, project=project)
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"v": _CACHE_VERSION, "sig": _ffmpeg_signature(),
                                        "caps": asdict(caps)},
                                       indent=1), encoding="utf-8")
        except OSError:
            pass
    return caps


def _ffmpeg_signature() -> str:
    exe = which("ffmpeg")
    if not exe:
        return "missing"
    try:
        st = os.stat(exe)
        return f"{exe}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return exe


def probe_capabilities(*, deep: bool = False, project: Project | None = None) -> Capabilities:
    caps = Capabilities(platform=platform_label(), cpu_cores=os.cpu_count() or 1, deep=deep)
    caps.ffmpeg = which("ffmpeg") or ""
    caps.ffprobe = which("ffprobe") or ""
    caps.exiftool = have("exiftool")
    caps.magick = have("magick") or have("convert")
    caps.melt = resolve_tool("melt") or ""
    caps.librosa = _py_module("librosa")
    caps.aubio = _py_module("aubio")

    if not caps.ffmpeg:
        return caps

    line = _tool_version("ffmpeg", ["-version"]) or ""
    caps.ffmpeg_version = _version_tuple(line)
    caps.encoders = _ffmpeg_list("ffmpeg", "-encoders", _ENCODER_PATTERN)
    caps.filters = _ffmpeg_list("ffmpeg", "-filters", _FILTER_PATTERN)
    caps.hwaccels = [l.strip() for l in
                     (run(["ffmpeg", "-hide_banner", "-hwaccels"], check=False).stdout or
                      "").splitlines()[1:] if l.strip()]
    caps.zoompan_10bit = _probe_zoompan_10bit("ffmpeg")
    # Die Testquelle muss HDR-Tags tragen, sonst scheitert zscale mangels
    # bekanntem Ausgangs-Farbraum und der Test misst nur sich selbst.
    hdr_src = "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc"
    caps.libplacebo_usable = ("libplacebo" in caps.filters and _probe_filter(
        "ffmpeg", f"{hdr_src},libplacebo=tonemapping=bt.2390:format=yuv420p"))
    caps.zscale_usable = ("zscale" in caps.filters and _probe_filter(
        "ffmpeg", f"{hdr_src},zscale=tin=arib-std-b67:min=bt2020nc:pin=bt2020:"
                  f"t=linear:npl=1000,tonemap=hable,zscale=p=bt709:t=bt709:m=bt709"))

    if deep:
        caps.gpu_name, caps.gpu_vram_mb = _probe_gpu()
        if "hevc_nvenc" in caps.encoders:
            caps.nvenc_10bit = _test_encode(
                "ffmpeg", ["-pix_fmt", "p010le", "-c:v", "hevc_nvenc"])
            if caps.nvenc_10bit:
                caps.nvenc_sessions = _probe_nvenc_sessions("ffmpeg", "hevc_nvenc")
        if "av1_nvenc" in caps.encoders:
            caps.av1_nvenc = _test_encode("ffmpeg", ["-pix_fmt", "p010le", "-c:v", "av1_nvenc"])
        if "cuda" in caps.hwaccels and caps.gpu_name:
            workdir = project.cache if project else Path(os.getcwd())
            workdir.mkdir(parents=True, exist_ok=True)
            res = _probe_nvdec_422("ffmpeg", workdir)
            caps.nvdec_422_10bit = bool(res)
    return caps


def build_report(project: Project | None = None, *, deep: bool = True,
                 refresh: bool = True) -> DoctorReport:
    caps = load_capabilities(project, deep=deep, refresh=refresh)
    rep = DoctorReport(caps=caps)
    rep.add("Umgebung", OK, f"{platform_label()}, slideshow {__version__}")

    # --- 3.1 Binaries -------------------------------------------------
    _check_binary(rep, "ffmpeg", ["-version"], (6, 0), hard=True,
                  note="getestete Basis; 6.x bringt xfade-/zoompan-Fixes")
    _check_binary(rep, "ffprobe", ["-version"], (6, 0), hard=True)
    _check_binary(rep, "python3", ["--version"], (3, 11), hard=True)
    _check_binary(rep, "exiftool", ["-ver"], None, hard=False,
                  note="fuer EXIF/ICC-Auswertung")
    _check_binary(rep, "magick", ["-version"], (7, 0), hard=False,
                  note="optional, Fallback: Pillow + lcms")
    _check_binary(rep, "melt", ["-version"], None, hard=False,
                  note="nur fuer den MLT-Renderpfad")

    if is_windows():
        _check_binary(rep, "nvidia-smi", [], (550, 0), hard=False,
                      note="Treiber >= 550 fuer NVENC/NVDEC")

    # Python-Pakete
    missing_py = [m for m in ("numpy", "yaml", "pydantic", "PIL", "rich", "soundfile")
                  if not _py_module(m)]
    if missing_py:
        rep.add("Python-Pakete", FAIL, f"fehlen: {', '.join(missing_py)}", _PY_PKGS)
    else:
        rep.add("Python-Pakete", OK, "numpy, pyyaml, pydantic, pillow, rich, soundfile")
    if caps.librosa:
        rep.add("Beat-Analyse", OK, "librosa (mel-basierte Onset-Erkennung)")
    else:
        # Kein FAIL: die Onset-Erkennung hat einen numpy-Fallback (Spectral
        # Flux), der auf perkussivem Material praktisch gleichwertig ist.
        # librosa zieht numba/llvmlite nach und ist schwergewichtig.
        rep.add("Beat-Analyse", WARN,
                "kein librosa — numpy-Fallback (Spectral Flux) wird verwendet; "
                "auf perkussivem Material gleichwertig, auf weichem Material "
                "schwaecher",
                "pip install librosa   # optional")

    if not caps.ffmpeg:
        # Ohne ffmpeg ist alles Weitere sinnlos — aber kein Traceback (Kriterium 1).
        rep.add("ffmpeg-Faehigkeiten", FAIL, "uebersprungen, ffmpeg fehlt",
                install_hint("ffmpeg"))
        return rep

    # --- 3.2 ffmpeg-Faehigkeiten --------------------------------------
    needed_f = ["zoompan", "xfade", "scale", "format"]
    missing_f = [f for f in needed_f if f not in caps.filters]
    if missing_f:
        rep.add("ffmpeg-Filter", FAIL, f"fehlen: {', '.join(missing_f)}",
                install_hint("ffmpeg") + "   # Full-Build noetig")
    else:
        rep.add("ffmpeg-Filter", OK, ", ".join(needed_f))

    usable = [n for n, ok in (("libplacebo", caps.libplacebo_usable),
                              ("zscale", caps.zscale_usable)) if ok]
    listed = [f for f in ("zscale", "libplacebo") if f in caps.filters]
    if caps.tonemap_chain() is None:
        detail = ("weder zscale noch libplacebo — HLG/PQ-Clips nur per Naeherung "
                  "(eq/curves) oder anderer ffmpeg-Build noetig")
        if listed:
            detail = (f"{', '.join(listed)} ist einkompiliert, scheitert aber im "
                      f"Praxistest (libplacebo braucht ein Vulkan-Geraet). "
                      f"HLG/PQ-Clips laufen ueber die Naeherung.")
        rep.add("HDR-Tonemapping", WARN, detail,
                install_hint("ffmpeg") + "   # Full-Build mit zscale")
    else:
        rep.add("HDR-Tonemapping", OK,
                f"nutzbar: {', '.join(usable)} (Praxistest bestanden)")

    enc_have = [e for e in ("hevc_nvenc", "h264_nvenc", "av1_nvenc") if e in caps.encoders]
    cpu_have = [e for e in ("libx265", "libx264") if e in caps.encoders]
    if enc_have:
        rep.add("NVENC-Encoder", OK, ", ".join(enc_have))
    elif cpu_have:
        rep.add("NVENC-Encoder", WARN,
                f"kein NVENC einkompiliert — CPU-Fallback: {', '.join(cpu_have)}",
                install_hint("ffmpeg") + "   # Full-Build inkl. NVENC/NVDEC")
    else:
        rep.add("Video-Encoder", FAIL, "weder NVENC noch libx264/libx265",
                install_hint("ffmpeg"))

    rep.add("zoompan-Bittiefe",
            OK if caps.zoompan_10bit else WARN,
            "zoompan rechnet in >8 Bit" if caps.zoompan_10bit else
            "zoompan ist 8-Bit-only; ffmpeg konvertiert still herunter. Der "
            "10-Bit-Gewinn entsteht nur im Encoder. Bei sichtbarem Banding: "
            "kb.engine=scale16 setzen (16-Bit-Pfad ohne zoompan)",
            "" if caps.zoompan_10bit else "slideshow build --kb-engine scale16")

    # --- 3.3 GPU ------------------------------------------------------
    if deep:
        if caps.gpu_name:
            rep.add("GPU", OK, f"{caps.gpu_name}, {caps.gpu_vram_mb} MB VRAM")
            rep.add("NVENC 10-Bit-HEVC", OK if caps.nvenc_10bit else WARN,
                    "Praxistest bestanden" if caps.nvenc_10bit else
                    "Praxistest fehlgeschlagen — Ausgabe faellt auf 8 Bit zurueck")
            if caps.nvenc_sessions:
                rep.add("NVENC-Sessions", OK, f"{caps.nvenc_sessions} parallel "
                        f"(begrenzt den Render-Pool)")
            rep.add("NVDEC 4:2:2 10 Bit", OK if caps.nvdec_422_10bit else WARN,
                    "vorhanden" if caps.nvdec_422_10bit else
                    "nicht vorhanden (erst ab Blackwell) — XAVC-HS-4:2:2-Material "
                    "wird automatisch per CPU decodiert")
            rep.add("AV1-NVENC", OK if caps.av1_nvenc else WARN,
                    "verfuegbar (optionales Ausgabeformat)" if caps.av1_nvenc
                    else "nicht verfuegbar (erst ab Ada/RTX 40)")
        else:
            rep.add("GPU", WARN, "keine NVIDIA-GPU erkannt — es wird auf der CPU "
                    "encodiert (libx265/libx264)")

    # --- 3.4 Ressourcen ----------------------------------------------
    rep.add("CPU-Kerne", OK, f"{caps.cpu_cores} (Worker-Pool: {caps.max_workers()})")
    if project:
        _check_disk(rep, project)

    if is_wsl():
        rep.add("Ausfuehrungsmodell", WARN,
                "Analyse laeuft hier korrekt; das Rendering gehoert aus "
                "Performancegruenden nativ nach Windows (9p-Durchgriff auf /mnt/c "
                "ist bei 100 x 20 MP deutlich langsamer)",
                "In PowerShell: slideshow render edit.yaml -o out/master.mp4")
    return rep


def _check_binary(rep: DoctorReport, exe: str, args: list[str],
                  minimum: tuple[int, ...] | None, *, hard: bool,
                  note: str = "") -> None:
    if exe == "nvidia-smi" and not args:
        args = ["--version"]
    line = _tool_version(exe, args)
    if line is None:
        fix = install_hint(exe)
        if is_windows() and fix.startswith("winget"):
            fix = f"{fix}\n  # {WINGET_HINT}"
        rep.add(exe, FAIL if hard else WARN, "nicht gefunden", fix)
        return
    ver = _version_tuple(line)
    if minimum and ver and tuple(ver[:len(minimum)]) < minimum:
        want = ".".join(str(x) for x in minimum)
        rep.add(exe, FAIL if hard else WARN,
                f"{line} — benoetigt >= {want}", install_hint(exe))
        return
    detail = line or "vorhanden"
    if note:
        detail = f"{detail}  ({note})"
    # Ausserhalb des PATH gefunden: kein Mangel, aber der Pfad gehoert in den
    # Report, sonst ist nicht nachvollziehbar, welche Binary gemessen wurde.
    path = resolve_tool(exe)
    if path and not which(exe):
        detail = f"{detail}  [nicht im PATH: {path}]"
    rep.add(exe, OK, detail)


def _check_disk(rep: DoctorReport, project: Project) -> None:
    try:
        project.root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(project.root)
    except OSError as exc:
        rep.add("Speicherplatz", WARN, f"nicht ermittelbar: {exc}")
        return
    free_gb = usage.free / 1e9
    rep.add("Speicherplatz", OK, f"{free_gb:.1f} GB frei unter {project.root}")


def estimate_space(*, images: int, clip_seconds: float, timeline_seconds: float,
                   intermediate_mbit: float = 700.0) -> dict[str, float]:
    """Schaetzung in Bytes, *bevor* irgendetwas geschrieben wird (3.4).

    Der Segment-Cache kann groesser als der Master werden — bei ``constqp`` ist
    die Bitrate motivabhaengig, deshalb konservativ mit 150 Mbit/s.
    """
    img = images * 15e6
    seg = timeline_seconds * 150e6 / 8
    inter = clip_seconds * intermediate_mbit * 1e6 / 8
    master = timeline_seconds * 100e6 / 8
    total = img + seg + inter + master
    return {"images": img, "segments": seg, "intermediate": inter,
            "master": master, "total": total, "required": total * 1.5}


def check_space(project: Project, est: dict[str, float]) -> Check:
    """Bei < 1,5x Bedarf: FAIL (3.4)."""
    try:
        free = shutil.disk_usage(project.root).free
    except OSError as exc:
        return Check("Speicherbedarf", WARN, f"nicht ermittelbar: {exc}")
    need = est["required"]
    detail = (f"Bedarf ~{est['total'] / 1e9:.1f} GB "
              f"(Bilder {est['images'] / 1e9:.1f} / Segmente {est['segments'] / 1e9:.1f} / "
              f"Intermediates {est['intermediate'] / 1e9:.1f} / Master {est['master'] / 1e9:.1f}), "
              f"mit Reserve {need / 1e9:.1f} GB, frei {free / 1e9:.1f} GB")
    if free < need:
        return Check("Speicherbedarf", FAIL, detail,
                     "Cache-Ziel wechseln (--project) oder Platz schaffen")
    return Check("Speicherbedarf", OK, detail)


# --------------------------------------------------------------------------
# Ausgabe und implizite Vorpruefung
# --------------------------------------------------------------------------

def print_report(rep: DoctorReport) -> None:
    from rich.table import Table
    con = console()
    table = Table(title=f"slideshow doctor — geprueft: {rep.caps.platform}",
                  title_justify="left", show_lines=False)
    table.add_column("Status", width=6)
    table.add_column("Pruefung", style="bold", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    colors = {OK: "green", WARN: "yellow", FAIL: "red"}
    for c in rep.checks:
        table.add_row(f"[{colors[c.status]}]{c.status}[/]", c.name, c.detail)
    con.print(table)

    fixes = [c for c in rep.checks if c.fix and c.status in (WARN, FAIL)]
    if fixes:
        con.print("\n[bold]Vorschlaege:[/bold]")
        for c in fixes:
            con.print(f"  [{colors[c.status]}]{c.status}[/] {c.name}")
            for line in c.fix.splitlines():
                con.print(f"      {line}")
        if is_windows():
            con.print(f"\n  [dim]{WINGET_HINT}[/dim]")

    if rep.failures:
        con.print(f"\n[red]{len(rep.failures)} harte(r) Fehlschlag/Fehlschlaege.[/red]")
    elif rep.warnings:
        con.print(f"\n[yellow]{len(rep.warnings)} Warnung(en), aber lauffaehig.[/yellow]")
    else:
        con.print("\n[green]Alles gruen.[/green]")


#: Welches Subkommando welche Werkzeuge zwingend braucht.
REQUIREMENTS = {
    "probe": {"ffprobe"},
    "audio": {"ffmpeg"},
    "preprocess": {"ffmpeg"},
    "beats": {"ffmpeg"},
    "build": set(),
    "render": {"ffmpeg", "ffprobe"},
    "export-mlt": set(),
    "selftest": {"ffmpeg", "ffprobe"},
}


def preflight(project: Project | None, subcommand: str) -> Capabilities:
    """Implizite Vorpruefung vor jedem Subkommando; harte FAILs brechen ab."""
    needs = REQUIREMENTS.get(subcommand, set())
    caps = load_capabilities(project, deep=False)
    missing: list[str] = []
    fixes: list[str] = []

    for tool in ("ffmpeg", "ffprobe"):
        if tool in needs and not getattr(caps, tool):
            missing.append(tool)
            fixes.append(install_hint(tool))
    if "analysis" in needs and not (caps.librosa or caps.aubio):
        missing.append("librosa/aubio")
        fixes.append("pip install librosa")

    if missing:
        lines = [f"Preflight fehlgeschlagen fuer `slideshow {subcommand}` "
                 f"({platform_label()}).",
                 f"Fehlt: {', '.join(missing)}", "", "Installation:"]
        lines += [f"  {f}" for f in dict.fromkeys(fixes) if f]
        lines += ["", "Vollstaendiger Report: slideshow doctor"]
        raise PreflightError("\n".join(lines))

    if caps.ffmpeg and caps.ffmpeg_major and caps.ffmpeg_major < 6:
        log.warning("ffmpeg %s ist aelter als die getestete Basis 6.0",
                    ".".join(str(x) for x in caps.ffmpeg_version))
    return caps
