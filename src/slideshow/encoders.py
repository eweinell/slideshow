"""Encoder-Profile.

Alle Segmente muessen mit *identischen* Encoder-Parametern encodiert werden,
sonst scheitert das Concat (8.3). Deshalb gibt es genau einen Ort, an dem die
Parameter entstehen: dieses Modul.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Farb-Tags des Masters. Der Master ist BT.709 SDR (Nicht-Ziel: HDR-Ausgabe).
COLOR_ARGS = [
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-colorspace", "bt709",
    "-color_range", "tv",
]


@dataclass(frozen=True)
class EncoderProfile:
    """Ein vollstaendig bestimmtes Ausgabeformat fuer Segmente."""

    name: str
    codec: str
    pix_fmt: str
    width: int
    height: int
    fps: float
    container: str = "mkv"
    rc_args: tuple[str, ...] = ()
    gpu: bool = False

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def gop_args(self) -> list[str]:
        """Geschlossene GOPs, Segmentanfang ist Keyframe — Voraussetzung fuer
        verlustfreies Concat (8.1)."""
        gop = max(1, int(round(self.fps)))
        if self.codec.endswith("_nvenc"):
            return ["-g", str(gop), "-forced-idr", "1"]
        if self.codec == "libx265":
            return ["-g", str(gop), "-x265-params",
                    f"keyint={gop}:min-keyint={gop}:scenecut=0:open-gop=0:log-level=error"]
        if self.codec == "libx264":
            return ["-g", str(gop), "-x264-params",
                    f"keyint={gop}:min-keyint={gop}:scenecut=0:open-gop=0"]
        return ["-g", str(gop)]

    def video_args(self) -> list[str]:
        """Die vollstaendigen Ausgabe-Argumente. Genau diese Liste geht auch in
        den Cache-Key ein."""
        return [
            "-c:v", self.codec, *self.rc_args, *self.gop_args(),
            "-pix_fmt", self.pix_fmt, *COLOR_ARGS,
            "-fps_mode", "cfr", "-r", _rate(self.fps),
        ]

    def fingerprint(self) -> dict:
        """Was den Cache-Key beeinflusst."""
        return {
            "codec": self.codec, "pix_fmt": self.pix_fmt,
            "size": [self.width, self.height], "fps": self.fps,
            "container": self.container, "args": self.video_args(),
        }


def _rate(fps: float) -> str:
    """Framerate exakt ausdruecken — 30000/1001 statt 29.97."""
    if abs(fps - round(fps)) < 1e-9:
        return str(int(round(fps)))
    for base in (24, 30, 60, 120):
        if abs(fps - base * 1000 / 1001) < 1e-4:
            return f"{base * 1000}/1001"
    return f"{fps:.6f}"


@dataclass
class EncoderChoice:
    """Was ``doctor`` an Encodern gefunden hat."""

    hevc_nvenc: bool = False
    h264_nvenc: bool = False
    av1_nvenc: bool = False
    nvenc_10bit: bool = False
    libx265: bool = False
    libx264: bool = False
    notes: list[str] = field(default_factory=list)


def master_profile(choice: EncoderChoice, *, width: int, height: int, fps: float,
                   codec: str = "auto") -> EncoderProfile:
    """Profil fuer die Segmente des Masters.

    ``codec="auto"`` bevorzugt HEVC-NVENC, faellt auf libx265 und dann libx264
    zurueck. Der Fallback ist kein Notnagel: die Analyse-/Testumgebung hat
    typischerweise keine NVIDIA-GPU, und die Pipeline muss dort vollstaendig
    durchlaufen koennen.
    """
    if codec == "auto":
        if choice.hevc_nvenc:
            codec = "hevc_nvenc"
        elif choice.libx265:
            codec = "libx265"
        elif choice.libx264:
            codec = "libx264"
        else:
            codec = "libx265"

    if codec == "hevc_nvenc":
        pix = "p010le" if choice.nvenc_10bit else "yuv420p"
        return EncoderProfile(
            name="hevc-nvenc", codec="hevc_nvenc", pix_fmt=pix,
            width=width, height=height, fps=fps, gpu=True,
            rc_args=("-preset", "p7", "-tune", "hq", "-rc", "constqp", "-qp", "16"),
        )
    if codec == "av1_nvenc":
        return EncoderProfile(
            name="av1-nvenc", codec="av1_nvenc", pix_fmt="p010le",
            width=width, height=height, fps=fps, gpu=True,
            rc_args=("-preset", "p7", "-rc", "constqp", "-qp", "24"),
        )
    if codec == "libx265":
        return EncoderProfile(
            name="libx265", codec="libx265", pix_fmt="yuv420p10le",
            width=width, height=height, fps=fps,
            rc_args=("-preset", "medium", "-crf", "16"),
        )
    if codec == "libx264":
        return EncoderProfile(
            name="libx264", codec="libx264", pix_fmt="yuv420p",
            width=width, height=height, fps=fps,
            rc_args=("-preset", "medium", "-crf", "16"),
        )
    raise ValueError(f"Unbekannter Ausgabecodec: {codec}")


def preview_profile(fps: float) -> EncoderProfile:
    """1280x720, libx264 — CPU reicht dafuer und umgeht die NVENC-Sessions des
    Hauptrenderers (Abschnitt 10)."""
    return EncoderProfile(
        name="preview-x264", codec="libx264", pix_fmt="yuv420p",
        width=1280, height=720, fps=fps,
        rc_args=("-preset", "veryfast", "-crf", "28"),
    )


def intermediate_args(kind: str) -> tuple[list[str], str]:
    """Encoder-Argumente fuer das Clip-Intermediate (5.2).

    DNxHR HQX ist gross (~700 Mbit/s bei 4K60); als Option gibt es
    HEVC-Intra mit ``-cq 12``.
    """
    if kind == "dnxhr_hqx":
        return (["-c:v", "dnxhd", "-profile:v", "dnxhr_hqx", "-pix_fmt", "yuv422p10le"], "mov")
    if kind == "hevc_intra_nvenc":
        return (["-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "constqp",
                 "-qp", "12", "-g", "1", "-pix_fmt", "p010le"], "mov")
    if kind == "hevc_intra_cpu":
        return (["-c:v", "libx265", "-preset", "fast", "-crf", "12",
                 "-x265-params", "keyint=1:log-level=error", "-pix_fmt", "yuv420p10le"], "mov")
    if kind == "ffv1":
        return (["-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv422p10le"], "mkv")
    raise ValueError(f"Unbekannter Intermediate-Codec: {kind}")
