"""Aufnahmezeiten der Videoclips von den Original-SD-Karten holen.

Exportierte oder umkopierte Clips tragen als ``creation_time`` den Zeitpunkt
des Exports, nicht den der Aufnahme — `probe` sortiert sie dann alle an einen
Tag nach der Reise. Die Originale auf den Karten wissen es besser: Sony legt
neben jedes ``C0001.MP4`` eine ``C0001M01.XML`` mit ``<CreationDate>``.

Ablauf: Karten nacheinander einstecken. Das Skript sucht auf allen
Laufwerken nach ``PRIVATE/M4ROOT`` (und verwandten Sony-Strukturen), nimmt
von jeder Karte, was es findet, und fragt nach der naechsten, bis jeder Clip
des Projekts eine Zeit hat. Die Funde landen sofort in
``<projekt>/clipzeiten-sd.json`` — ein Abbruch kostet nichts, der naechste
Lauf macht dort weiter.

Am Ende traegt es die Zeiten ins ``manifest.json`` ein
(``time_source: sdkarte``). Ein spaeteres `slideshow probe` schreibt das
Manifest neu und verliert sie; dann genuegt ``--ohne-karten``, die Zeiten
stehen ja in der JSON-Datei.

    .venv\\Scripts\\python.exe scripts\\clipzeiten_von_sd.py --project kenia-2026
    .venv\\Scripts\\python.exe scripts\\clipzeiten_von_sd.py --project kenia-2026 --karte E:\\
    .venv\\Scripts\\python.exe scripts\\clipzeiten_von_sd.py --project kenia-2026 --ohne-karten
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import string
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from slideshow.models import Manifest

#: Verzeichnisse, an denen eine Kamerakarte zu erkennen ist. M4ROOT ist der
#: XAVC-S-Standard der Alphas; XDROOT und AVCHD kommen bei aelteren oder
#: professionellen Sony-Modellen vor, DCIM bei fast allen anderen Kameras.
KARTEN_MARKER = ("PRIVATE/M4ROOT", "PRIVATE/XDROOT", "PRIVATE/AVCHD", "DCIM")

VIDEO_ENDUNGEN = {".mp4", ".mov", ".mts", ".m2ts", ".mxf"}

#: Wert fuer ``MediaItem.time_source``. Eigener Name statt ``exif``, damit im
#: Manifest sichtbar bleibt, dass die Zeit nicht aus der Datei selbst stammt.
ZEITQUELLE = "sdkarte"

ERGEBNISDATEI = "clipzeiten-sd.json"


# --------------------------------------------------------------------------
# Karten finden
# --------------------------------------------------------------------------

def kandidaten_wurzeln() -> list[Path]:
    """Alle eingehaengten Laufwerke, auf denen eine Karte stecken koennte."""
    if os.name == "nt":
        wurzeln = [Path(f"{b}:/") for b in string.ascii_uppercase]
    else:
        wurzeln = []
        for basis in (Path("/Volumes"), Path("/media"), Path("/run/media"), Path("/mnt")):
            try:
                # /media/<user>/<karte> und /Volumes/<karte> — zwei Ebenen reichen.
                for p in basis.iterdir():
                    wurzeln.append(p)
                    if p.is_dir():
                        wurzeln.extend(q for q in p.iterdir() if q.is_dir())
            except OSError:
                continue
    return wurzeln


def ist_karte(wurzel: Path) -> bool:
    for marker in KARTEN_MARKER:
        try:
            if (wurzel / marker).is_dir():
                return True
        except OSError:
            # Kartenleser ohne eingelegtes Medium: Windows meldet das als
            # Fehler statt als "gibt es nicht".
            continue
    return False


def gefundene_karten(ausdruecklich: list[Path]) -> list[Path]:
    if ausdruecklich:
        return ausdruecklich
    # Die Systemplatte nie durchsuchen, auch wenn dort ein DCIM liegt — das
    # waere eine Kopie, kein Original, und der Lauf dauerte Minuten.
    system = Path(os.environ.get("SystemDrive", "C:") + "/").resolve() if os.name == "nt" else None
    return [w for w in kandidaten_wurzeln()
            if ist_karte(w) and (system is None or w.resolve() != system)]


def dateien_der_karte(karte: Path) -> dict[str, list[Path]]:
    """Alle Dateien unter den Kartenverzeichnissen, nach grossgeschriebenem Stamm."""
    nach_stamm: dict[str, list[Path]] = {}
    for marker in KARTEN_MARKER:
        start = karte / marker
        try:
            if not start.is_dir():
                continue
        except OSError:
            continue
        for ordner, _, namen in os.walk(start):
            for n in namen:
                p = Path(ordner) / n
                nach_stamm.setdefault(p.stem.upper(), []).append(p)
    return nach_stamm


# --------------------------------------------------------------------------
# Zeit lesen
# --------------------------------------------------------------------------

def zeit_aus_xml(xml: Path) -> str | None:
    """``<CreationDate value="2026-10-24T15:23:11+03:00"/>`` aus Sonys NRT-Metadaten.

    Der Namensraum wechselt zwischen Modellen und Firmwares; gesucht wird
    deshalb nach dem lokalen Elementnamen.
    """
    try:
        wurzel = ET.parse(xml).getroot()
    except (ET.ParseError, OSError):
        return None
    for el in wurzel.iter():
        if el.tag.rsplit("}", 1)[-1] == "CreationDate":
            wert = el.get("value")
            if wert:
                return wert.strip()
    return None


def zeit_aus_container(video: Path) -> str | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format_tags=creation_time",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=60, check=False).stdout
        return (json.loads(out or "{}").get("format", {}).get("tags", {})
                .get("creation_time"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def zeit_fuer_clip(stamm: str, dateien: dict[str, list[Path]]) -> dict | None:
    videos = [p for p in dateien.get(stamm, []) if p.suffix.lower() in VIDEO_ENDUNGEN]
    if not videos:
        return None
    video = videos[0]
    # M01 ist die Begleitdatei der ersten Aufnahme; bei geteilten Dateien
    # (ueber 4 GB) gibt es weitere, die aber dieselbe CreationDate tragen.
    xmls = [p for k, ps in dateien.items() if k.startswith(stamm + "M")
            for p in ps if p.suffix.lower() == ".xml"]
    for xml in sorted(xmls):
        wert = zeit_aus_xml(xml)
        if wert:
            return {"zeit": wert, "quelle": "xml", "datei": str(xml)}
    wert = zeit_aus_container(video)
    if wert:
        return {"zeit": wert, "quelle": "container", "datei": str(video)}
    return None


def parse_zone(text: str) -> dt.timezone:
    """``"+03:00"`` -> ``timezone(+3 h)``."""
    vorzeichen = -1 if text.startswith("-") else 1
    h, _, m = text.lstrip("+-").partition(":")
    return dt.timezone(vorzeichen * dt.timedelta(hours=int(h), minutes=int(m or 0)))


def als_zeitstempel(eintrag: dict, zone: dt.timezone | None) -> float:
    """Dieselbe Konvention wie die Fotos: Wanduhr am Aufnahmeort, lokal gelesen.

    `probe` liest EXIF-Zeiten ohne Zone und deutet sie als Ortszeit dieses
    Rechners. Ein Clip muss deshalb als *Wanduhr* ankommen, nicht als
    absoluter Zeitpunkt — sonst laegen Clips und Fotos um den Abstand
    zwischen Reise- und Heimatzone auseinander.

    Mit ``zone`` wird der absolute Zeitpunkt in diese Zone umgerechnet und
    deren Wanduhr genommen. Das ist der verlaessliche Weg, wenn die Zone in
    der XML nicht stimmt: in `kenia-2026` trugen die Fotos durchgehend +03:00,
    die Clips derselben Reise aber +01, +02, +03 und +04 — und der
    Bildvergleich (Giraffe Centre, Kochkurs, Wasserloch) zeigte, dass dort
    der *Zeitpunkt* stimmte und die Wanduhr um die falsche Zone verschoben
    war. Ohne ``zone`` gilt die Wanduhr der XML unveraendert.

    Die Container-Zeit ist UTC ohne Wanduhr; ohne ``zone`` wird sie als
    echter Zeitpunkt genommen und ist deshalb nur der Notbehelf.
    """
    wert = eintrag["zeit"].replace("Z", "+00:00")
    zeit = dt.datetime.fromisoformat(wert)
    if zone is not None and zeit.tzinfo is not None:
        return zeit.astimezone(zone).replace(tzinfo=None).timestamp()
    if eintrag["quelle"] == "xml":
        return zeit.replace(tzinfo=None).timestamp()
    return zeit.timestamp()


def zonen_der_funde(ergebnisse: dict[str, dict]) -> set[str]:
    zonen = set()
    for e in ergebnisse.values():
        zeit = dt.datetime.fromisoformat(e["zeit"].replace("Z", "+00:00"))
        if e["quelle"] == "xml" and zeit.utcoffset() is not None:
            zonen.add(zeit.strftime("%z"))
    return zonen


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------

def lade_ergebnisse(pfad: Path) -> dict[str, dict]:
    if not pfad.exists():
        return {}
    return json.loads(pfad.read_text(encoding="utf-8"))


def speichere_ergebnisse(pfad: Path, ergebnisse: dict[str, dict]) -> None:
    # Erst daneben schreiben, dann ersetzen: ein Abbruch mitten im Schreiben
    # darf die Funde der vorigen Karten nicht mitnehmen.
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(ergebnisse.items())), indent=1,
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(pfad)


def gesuchte_clips(manifest: Manifest) -> dict[str, list[str]]:
    """Grossgeschriebener Dateistamm -> Medien-IDs (ein Stamm kann mehrfach vorkommen)."""
    gesucht: dict[str, list[str]] = {}
    for m in manifest.clips:
        gesucht.setdefault(Path(m.path).stem.upper(), []).append(m.id)
    return gesucht


def karte_auswerten(karte: Path, offen: set[str], ergebnisse: dict[str, dict]) -> int:
    print(f"\nKarte {karte}: durchsuche ...", flush=True)
    dateien = dateien_der_karte(karte)
    neu = 0
    for stamm in sorted(offen & dateien.keys()):
        eintrag = zeit_fuer_clip(stamm, dateien)
        if eintrag is None:
            print(f"  {stamm}: Datei gefunden, aber keine Zeit lesbar")
            continue
        eintrag["karte"] = str(karte)
        ergebnisse[stamm] = eintrag
        neu += 1
        hinweis = "" if eintrag["quelle"] == "xml" else "   (nur Container-Zeit, UTC)"
        print(f"  {stamm}: {eintrag['zeit']}{hinweis}")
    print(f"  {neu} neue Zeiten von dieser Karte")
    return neu


def karten_abfragen(args, gesucht: dict[str, list[str]], ergebnisse: dict[str, dict],
                    ergebnis_pfad: Path) -> None:
    gesehen: set[str] = set()
    while True:
        offen = set(gesucht) - set(ergebnisse)
        if not offen:
            print("\nAlle Clips haben eine Zeit.")
            return
        print(f"\nNoch offen: {len(offen)} von {len(gesucht)} Clips")

        karten = [k for k in gefundene_karten(args.karte) if str(k) not in gesehen]
        for karte in karten:
            gesehen.add(str(karte))
            if karte_auswerten(karte, offen, ergebnisse):
                speichere_ergebnisse(ergebnis_pfad, ergebnisse)
            offen = set(gesucht) - set(ergebnisse)
        if not offen:
            continue
        if args.karte:
            # Ausdruecklich genannte Karten werden genau einmal gelesen —
            # sonst fragte das Skript endlos nach demselben Pfad.
            return

        if not karten:
            print("Keine (neue) Speicherkarte gefunden.")
        try:
            antwort = input("Naechste Karte einlegen und Enter druecken "
                            "(q = beenden, Rest offen lassen): ").strip().lower()
        except EOFError:
            return
        if antwort == "q":
            return
        # Dasselbe Laufwerk mit einer anderen Karte ist eine neue Karte: der
        # Buchstabe bleibt, der Inhalt wechselt. Deshalb nach jeder Frage
        # alles wieder als ungelesen behandeln — eine schon gelesene Karte
        # liefert einfach nichts Neues.
        gesehen.clear()


def ins_manifest(manifest_pfad: Path, gesucht: dict[str, list[str]],
                 ergebnisse: dict[str, dict], zone: dt.timezone | None) -> int:
    manifest = Manifest.load(manifest_pfad)
    nach_id = {m.id: m for m in manifest.media}
    geaendert = 0
    for stamm, eintrag in ergebnisse.items():
        for mid in gesucht.get(stamm, []):
            m = nach_id[mid]
            ts = als_zeitstempel(eintrag, zone)
            if m.capture_time == ts and m.time_source == ZEITQUELLE:
                continue
            m.capture_time = ts
            m.time_source = ZEITQUELLE
            geaendert += 1
    if geaendert:
        manifest.save(manifest_pfad)
    return geaendert


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Aufnahmezeiten der Clips von den Original-SD-Karten ins Manifest holen.")
    ap.add_argument("--project", required=True, type=Path,
                    help="Projektverzeichnis mit manifest.json")
    ap.add_argument("--karte", action="append", type=Path, default=[],
                    help="Kartenwurzel ausdruecklich angeben (mehrfach moeglich); "
                         "sonst werden alle Laufwerke abgesucht und nachgefragt")
    ap.add_argument("--ohne-karten", action="store_true",
                    help="Keine Karte lesen, nur die gespeicherten Zeiten eintragen "
                         "(nach einem erneuten `slideshow probe`)")
    ap.add_argument("--nicht-eintragen", action="store_true",
                    help="Nur sammeln, manifest.json unveraendert lassen")
    ap.add_argument("--zone", default=None, metavar="+03:00",
                    help="Ortszeit der Reise: rechnet den Zeitpunkt aus der XML in "
                         "diese Zone um, statt deren Wanduhr zu nehmen. Noetig, wenn "
                         "die Kamera fuer Video eine falsche Zone eingestellt hatte")
    args = ap.parse_args(argv)
    zone = parse_zone(args.zone) if args.zone else None

    manifest_pfad = args.project / "manifest.json"
    ergebnis_pfad = args.project / ERGEBNISDATEI
    gesucht = gesuchte_clips(Manifest.load(manifest_pfad))
    if not gesucht:
        print("Das Manifest enthaelt keine Clips.")
        return 0
    ergebnisse = lade_ergebnisse(ergebnis_pfad)
    if ergebnisse:
        print(f"{len(set(ergebnisse) & set(gesucht))} Zeiten aus {ergebnis_pfad} uebernommen")

    if not args.ohne_karten:
        try:
            karten_abfragen(args, gesucht, ergebnisse, ergebnis_pfad)
        except KeyboardInterrupt:
            print("\nAbgebrochen — bisherige Funde bleiben gespeichert.")

    offen = sorted(set(gesucht) - set(ergebnisse))
    if offen:
        print(f"\nOhne Zeit geblieben ({len(offen)}): {', '.join(offen)}")
    notbehelf = sorted(s for s, e in ergebnisse.items() if e["quelle"] != "xml")
    if notbehelf:
        print(f"Nur Container-Zeit (UTC statt Kamera-Wanduhr, ggf. um die "
              f"Zeitzone versetzt): {', '.join(notbehelf)}")

    zonen = zonen_der_funde(ergebnisse)
    if len(zonen) > 1 and zone is None:
        # Die Wanduhr ist dann bei mindestens einem Teil falsch — welcher,
        # sagt nur der Vergleich mit den Fotos, nicht dieses Skript.
        print(f"\nACHTUNG: die Clips tragen verschiedene Zonen ({', '.join(sorted(zonen))}). "
              f"Die Wanduhr ist damit bei einem Teil verschoben. Mit --zone <Ortszeit> "
              f"(z. B. die OffsetTimeOriginal der Fotos) rechnet das Skript um.")

    if args.nicht_eintragen:
        return 0
    geaendert = ins_manifest(manifest_pfad, gesucht, ergebnisse, zone)
    print(f"\n{geaendert} Clips im Manifest aktualisiert ({manifest_pfad}).")
    if geaendert:
        # `order --update` behaelt die bestehende Sortierung; ein Hinweis
        # darauf wuerde versprechen, was er nicht tut.
        print("order.yaml aendert sich dadurch nicht — dort stehen die Clips "
              "weiter, wo sie stehen, und muessen noch an ihre Tage.")
    return 0 if not offen else 1


if __name__ == "__main__":
    sys.exit(main())
