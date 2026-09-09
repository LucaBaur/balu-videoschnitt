"""ffmpeg-Pipeline: aus einer Quelle + Markierungen einen fertigen Clip bauen.

Laeuft am PC, nicht auf dem Homeserver - Schneiden und Neukodieren ist fuer die
kleine Server-CPU zu viel. Der Browser liefert die Markierungen schon fertig als
transparente PNGs in Ausgabeaufloesung; ffmpeg schneidet nur, legt sie darueber,
baut die Standbilder und setzt am Ende alles zusammen.

Ablauf:
  1. Schnitt + auf 1280x720 normalisieren            -> base.mp4
  2. feststehende Markierungen darueber (Zeitfenster) -> annotiert.mp4
  3. Standbilder: an den Zeitpunkten splitten, je ein eingefrorener Abschnitt
     mit eigener Markierung dazwischen, dann concat -> Ziel
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

BREITE, HOEHE = 1280, 720
CRF = "21"
PRESET = "veryfast"
AUDIO_RATE = "48000"

def _finde_exe(name: str, env_name: str) -> str:
    """ffmpeg/ffprobe finden - auch wenn sie nicht im PATH stehen (z. B. direkt
    nach der winget-Installation, bevor ein neues Terminal offen war)."""
    vorgabe = os.getenv(env_name)
    if vorgabe and Path(vorgabe).exists():
        return vorgabe
    im_pfad = shutil.which(name)
    if im_pfad:
        return im_pfad
    kandidaten: list[Path] = []
    lokal = os.getenv("LOCALAPPDATA")
    if lokal:
        wg = Path(lokal, "Microsoft", "WinGet", "Packages")
        kandidaten += list(wg.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"))
        kandidaten += list(wg.glob(f"Gyan.FFmpeg*/**/{name}.exe"))
    for basis in (r"C:\ffmpeg\bin", os.getenv("ProgramFiles", ""),
                  os.getenv("ProgramW6432", "")):
        if basis:
            kandidaten.append(Path(basis, "ffmpeg", "bin", f"{name}.exe"))
    for k in kandidaten:
        if k.exists():
            return str(k)
    return name          # letzter Versuch - vielleicht doch im PATH


FFMPEG = _finde_exe("ffmpeg", "FFMPEG")
FFPROBE = _finde_exe("ffprobe", "FFPROBE")

_VF_SKALIEREN = (
    f"scale={BREITE}:{HOEHE}:force_original_aspect_ratio=decrease,"
    f"pad={BREITE}:{HOEHE}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
)


def _exe_da(name: str) -> bool:
    return shutil.which(name) is not None or Path(name).exists()


def werkzeuge_da() -> tuple[bool, bool]:
    return _exe_da(FFMPEG), _exe_da(FFPROBE)


def _run(cmd: list[str], log_cb=None) -> None:
    if log_cb:
        log_cb(" ".join(str(c) for c in cmd))
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg-Fehler (Code {p.returncode}):\n{tail}")


def probe(pfad: str | Path) -> dict:
    """Dauer, Aufloesung, Bildrate und ob ein Tonspur vorhanden ist."""
    pfad = str(pfad)
    aus = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", pfad],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if aus.returncode != 0:
        raise RuntimeError("ffprobe kann die Datei nicht lesen:\n"
                           + (aus.stderr or "").strip()[-400:])
    d = json.loads(aus.stdout or "{}")
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    hat_audio = any(s.get("codec_type") == "audio" for s in streams)

    fr = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/0"
    try:
        z, n = fr.split("/")
        fps = float(z) / float(n) if float(n) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    if not (5.0 <= fps <= 60.0):
        fps = 30.0

    return {
        "dauer": float(d.get("format", {}).get("duration") or 0.0),
        "breite": int(v.get("width") or 0),
        "hoehe": int(v.get("height") or 0),
        "fps": round(fps, 3),
        "hat_audio": hat_audio,
    }


def _png_schreiben(b64: str, ziel: Path) -> None:
    roh = b64.split(",", 1)[-1]          # evtl. "data:image/png;base64," abtrennen
    ziel.write_bytes(base64.b64decode(roh))


def _fps_ganz(fps) -> int:
    try:
        f = float(fps)
    except (TypeError, ValueError):
        f = 30.0
    return round(f) if 5.0 <= f <= 60.0 else 30


def render(projekt: dict, arbeit: Path, ziel: Path, log_cb=None) -> dict:
    arbeit.mkdir(parents=True, exist_ok=True)
    quelle = str(projekt["quelle"])
    in_s = round(float(projekt["in_s"]), 3)
    out_s = round(float(projekt["out_s"]), 3)
    dauer = round(out_s - in_s, 3)
    if dauer <= 0:
        raise RuntimeError("Der Endpunkt liegt nicht hinter dem Startpunkt.")

    fps_i = _fps_ganz(projekt.get("fps"))
    ton = bool(projekt.get("ton", True)) and probe(quelle)["hat_audio"]
    if ton:
        audio_args = ["-c:a", "aac", "-b:a", "128k", "-ar", AUDIO_RATE, "-ac", "2"]
    else:
        audio_args = ["-an"]

    # 1) Schnitt + Normalisieren ----------------------------------------
    base = arbeit / "base.mp4"
    _run([FFMPEG, "-y", "-ss", f"{in_s}", "-i", quelle, "-t", f"{dauer}",
          "-vf", f"{_VF_SKALIEREN},fps={fps_i}",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", PRESET, "-crf", CRF,
          *audio_args, "-movflags", "+faststart", str(base)], log_cb)

    # 2) Feststehende Markierungen -------------------------------------
    annots = projekt.get("annots") or []
    aktuell = base
    if annots:
        eingaben = ["-i", str(base)]
        filter_teile, letzte = [], "0:v"
        for i, a in enumerate(annots):
            p = arbeit / f"annot{i}.png"
            _png_schreiben(a["png_b64"], p)
            eingaben += ["-i", str(p)]
            von = max(0.0, float(a.get("von_s") or 0.0))
            bis = float(a["bis_s"]) if a.get("bis_s") is not None else dauer
            neu = f"v{i + 1}"
            filter_teile.append(
                f"[{letzte}][{i + 1}:v]overlay=enable='between(t,{von},{bis})'[{neu}]")
            letzte = neu
        annotiert = arbeit / "annotiert.mp4"
        _run([FFMPEG, "-y", *eingaben,
              "-filter_complex", ";".join(filter_teile),
              "-map", f"[{letzte}]", *(["-map", "0:a?"] if ton else []),
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", PRESET, "-crf", CRF,
              *(["-c:a", "copy"] if ton else ["-an"]),
              "-movflags", "+faststart", str(annotiert)], log_cb)
        aktuell = annotiert

    # 3) Standbilder ---------------------------------------------------
    # Reihenfolge nach Zeitpunkt; Punkte am Rand oder dicht beieinander raus.
    roh = sorted((s for s in (projekt.get("standbilder") or [])
                  if 0.05 < float(s["bei_s"]) < dauer - 0.05),
                 key=lambda s: float(s["bei_s"]))
    gepaart: list[tuple[float, dict]] = []
    for s in roh:
        t = round(float(s["bei_s"]), 3)
        if not gepaart or t - gepaart[-1][0] > 1.0 / fps_i:
            gepaart.append((t, s))

    if not gepaart:
        shutil.copyfile(aktuell, ziel)
        return _ergebnis(ziel, dauer)

    grenzen = [t for t, _ in gepaart]
    segmente: list[Path] = []
    vor = 0.0
    for idx, ende in enumerate(grenzen + [dauer]):
        teil = arbeit / f"teil{idx}.mp4"
        _run([FFMPEG, "-y", "-ss", f"{vor}", "-i", str(aktuell),
              "-t", f"{round(ende - vor, 3)}",
              "-vf", f"fps={fps_i},setsar=1",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", PRESET, "-crf", CRF,
              *audio_args, str(teil)], log_cb)
        segmente.append(teil)

        if idx < len(grenzen):
            t, s = gepaart[idx]
            frame = arbeit / f"frame{idx}.png"
            _run([FFMPEG, "-y", "-ss", f"{t}", "-i", str(aktuell),
                  "-frames:v", "1", "-update", "1", str(frame)], log_cb)
            marke = arbeit / f"stand{idx}.png"
            _png_schreiben(s["png_b64"], marke)
            fdauer = max(0.3, float(s.get("dauer_s") or 3.0))
            frz = arbeit / f"frz{idx}.mp4"
            cmd = [FFMPEG, "-y", "-loop", "1", "-t", f"{fdauer}", "-i", str(frame),
                   "-i", str(marke)]
            if ton:
                cmd += ["-f", "lavfi", "-t", f"{fdauer}",
                        "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
            cmd += ["-filter_complex",
                    f"[0:v]scale={BREITE}:{HOEHE},setsar=1,fps={fps_i}[bg];"
                    f"[bg][1:v]overlay[v]", "-map", "[v]"]
            cmd += (["-map", "2:a", "-c:a", "aac", "-b:a", "128k",
                     "-ar", AUDIO_RATE, "-ac", "2"] if ton else ["-an"])
            cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", PRESET,
                    "-crf", CRF, "-r", f"{fps_i}", str(frz)]
            _run(cmd, log_cb)
            segmente.append(frz)
        vor = ende

    liste = arbeit / "concat.txt"
    liste.write_text("".join(f"file '{p.as_posix()}'\n" for p in segmente),
                     encoding="utf-8")
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(liste),
          "-c", "copy", "-movflags", "+faststart", str(ziel)], log_cb)

    gesamt = round(dauer + sum(max(0.3, float(s.get("dauer_s") or 3.0))
                               for _, s in gepaart), 2)
    return _ergebnis(ziel, gesamt)


def _ergebnis(ziel: Path, dauer_s: float) -> dict:
    return {"datei": ziel.name, "dauer_s": round(float(dauer_s), 2),
            "groesse": ziel.stat().st_size}
