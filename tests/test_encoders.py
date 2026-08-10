"""Die Ratensteuerung der Master-Profile.

``constqp -qp 16`` gab jedem Frame denselben Quantisierer und brachte den
4K60-Master auf ~100 Mbit/s, ohne dass der Unterschied zu ``cq 24`` sichtbar
waere. Diese Tests halten die Ersetzung fest — und die zwei Stellen, an denen
sie still wieder wirkungslos werden koennte.
"""

from __future__ import annotations

from slideshow.encoders import EncoderChoice, master_profile

ADA = EncoderChoice(hevc_nvenc=True, av1_nvenc=True, nvenc_10bit=True,
                    nvenc_advanced_rc=True)
PASCAL = EncoderChoice(hevc_nvenc=True, nvenc_10bit=True, nvenc_advanced_rc=False)


def _profil(choice, codec):
    return master_profile(choice, width=3840, height=2160, fps=60.0, codec=codec)


def test_der_master_quantisiert_nicht_mehr_mit_festem_qp():
    args = _profil(ADA, "hevc_nvenc").video_args()
    assert "constqp" not in args
    assert args[args.index("-rc") + 1] == "vbr"
    assert args[args.index("-cq") + 1] == "24"


def test_die_qualitaetsvorgabe_wird_nicht_von_einer_bitrate_ausgehebelt():
    """``-cq`` wirkt nur, solange ``-b:v`` auf 0 steht — sonst faellt die
    Ausgabe still auf die Zielbitrate zurueck."""
    for codec in ("hevc_nvenc", "av1_nvenc"):
        args = _profil(ADA, codec).video_args()
        assert args[args.index("-b:v") + 1] == "0", codec


def test_ohne_turing_entfallen_multipass_und_temporal_aq():
    """Aeltere Karten brechen mit diesen Schaltern ab, statt sie zu
    ignorieren. Der Rest der VBR-Steuerung muss trotzdem stehen."""
    args = _profil(PASCAL, "hevc_nvenc").video_args()
    assert "-multipass" not in args
    assert "-temporal-aq" not in args
    assert args[args.index("-rc") + 1] == "vbr"
    assert "-spatial-aq" in args


def test_av1_quantisiert_auf_der_groesseren_skala():
    """AV1 rechnet 0..63 statt 0..51 — derselbe ``cq``-Wert waere dort
    deutlich feiner und damit groesser, nicht kleiner."""
    hevc = _profil(ADA, "hevc_nvenc").video_args()
    av1 = _profil(ADA, "av1_nvenc").video_args()
    assert int(av1[av1.index("-cq") + 1]) > int(hevc[hevc.index("-cq") + 1])


def test_die_bildstruktur_bleibt_unberuehrt():
    """Die Segmente werden mit ``-c copy`` aneinandergehaengt. B-Frames sind
    genau das, was an einer Segmentgrenze Umsortierung und Zeitstempel
    durcheinanderbringen kann — die Ratensteuerung darf sie nicht anfassen."""
    for choice in (ADA, PASCAL):
        args = _profil(choice, "hevc_nvenc").video_args()
        assert "-bf" not in args
        assert "-b_ref_mode" not in args


def test_die_ratensteuerung_steht_im_cache_schluessel():
    """``plan_jobs`` hasht ueber ``fingerprint()``. Waere ``rc_args`` dort
    nicht enthalten, lieferte der naechste Lauf die alten, grossen Segmente
    aus (Invariante 3)."""
    a = _profil(ADA, "hevc_nvenc").fingerprint()
    b = _profil(PASCAL, "hevc_nvenc").fingerprint()
    assert a != b
    assert "-cq" in a["args"]
