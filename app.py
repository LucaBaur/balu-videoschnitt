"""Balu-Videoschnitt - lokales Werkzeug am PC.

Schneidet einzelne Szenen aus einer Spielaufzeichnung, versieht sie mit
Markierungen (Pfeile, Kreise, Text, Standbilder) und laedt den fertigen Clip zu
Balu's Training hoch, wo die zugeordneten Spielerinnen ihn sehen.

    py app.py            ->  http://127.0.0.1:8300

Rendern passiert hier, nicht auf dem Homeserver. Die fertigen Clips gehen per
API an Balu's Training; das ist die Ablage.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import render
from balu_api import BaluClient

BASIS = Path(__file__).parent
STATIC = BASIS / "static"
ARBEIT = BASIS / "arbeit"
RENDER = BASIS / "render"
KADER_CACHE = BASIS / "kader_cache.json"
for _d in (ARBEIT, RENDER):
    _d.mkdir(exist_ok=True)


def _env_laden(override: bool = False) -> None:
    """.env einlesen. Mit override=True gewinnen die Datei-Werte gegen bereits
    gesetzte Umgebungsvariablen - damit ein Nachtragen von Zugangsdaten ohne
    Neustart wirkt (Button 'Erneut versuchen')."""
    pfad = BASIS / ".env"
    if not pfad.exists():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        k, v = zeile.split("=", 1)
        k, v = k.strip(), v.strip()
        if not v:
            continue                      # leere Platzhalter nichts überschreiben lassen
        if override or k not in os.environ:
            os.environ[k] = v


_env_laden()

BALU_URL = os.getenv("BALU_URL", "http://homeserver:8200")
_balu = BaluClient(BALU_URL, os.getenv("BALU_TRAINER_NAME", ""),
                   os.getenv("BALU_TRAINER_PIN", ""))


def _balu_auffrischen() -> None:
    """.env neu lesen und den Client anpassen, falls sich URL/Name/PIN geaendert
    haben (dann neu anmelden) - so wirkt ein Korrigieren der .env ohne Neustart."""
    _env_laden(override=True)
    url = os.getenv("BALU_URL", BALU_URL).strip().rstrip("/")
    name = os.getenv("BALU_TRAINER_NAME", "")
    pin = os.getenv("BALU_TRAINER_PIN", "")
    if (url, name, pin) != (_balu.base, _balu.name, _balu.pin):
        _balu.base, _balu.name, _balu.pin = url, name, pin
        _balu._angemeldet = False


app = FastAPI(title="Balu-Videoschnitt")

_quelle: dict = {}                        # {"pfad", "info"}
_kader = {"zeit": 0.0, "daten": []}       # 60 s Cache


class QuelleAnfrage(BaseModel):
    pfad: str


class Annot(BaseModel):
    png_b64: str
    von_s: float = 0.0
    bis_s: float | None = None


class Standbild(BaseModel):
    bei_s: float
    dauer_s: float = 3.0
    png_b64: str


class RenderAnfrage(BaseModel):
    in_s: float
    out_s: float
    fps: float = 30.0
    ton: bool = True
    annots: list[Annot] = []
    standbilder: list[Standbild] = []


class VeroeffentlichenAnfrage(BaseModel):
    datei: str
    titel: str
    notiz: str = ""
    spiel_datum: str = ""
    spieler_ids: list[int] = []


@app.get("/api/status")
def status():
    ff, fp = render.werkzeuge_da()
    return {"ffmpeg": ff, "ffprobe": fp, "balu_url": BALU_URL,
            "quelle": _quelle.get("pfad"), "quelle_info": _quelle.get("info")}


@app.post("/api/datei-dialog")
def datei_dialog():
    """Öffnet den Windows-Dateiauswahl-Dialog (startet im Videos-Ordner) und
    gibt den gewählten Pfad zurück. `pfad` ist null, wenn abgebrochen wurde."""
    ps = (
        'Add-Type -AssemblyName System.Windows.Forms;'
        '$d = New-Object System.Windows.Forms.OpenFileDialog;'
        '$d.Title = "Spielaufnahme wählen";'
        '$d.Filter = "Videos|*.mp4;*.mov;*.mkv;*.avi;*.m4v;*.webm;*.mpg;*.mpeg|Alle Dateien|*.*";'
        '$d.InitialDirectory = [Environment]::GetFolderPath("MyVideos");'
        '$o = New-Object System.Windows.Forms.Form -Property @{TopMost=$true};'
        'if ($d.ShowDialog($o) -eq "OK") { [Console]::Out.Write($d.FileName) }'
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True, text=True, timeout=600)
    except Exception as e:
        raise HTTPException(500, f"Dateidialog nicht möglich: {e}")
    pfad = (r.stdout or "").strip()
    return {"pfad": pfad or None}


@app.post("/api/quelle")
def quelle_setzen(a: QuelleAnfrage):
    p = Path(a.pfad.strip().strip('"'))
    if not p.is_file():
        raise HTTPException(400, f"Datei nicht gefunden: {p}")
    try:
        info = render.probe(p)
    except Exception as e:
        raise HTTPException(400, str(e))
    _quelle["pfad"], _quelle["info"] = str(p), info
    return {"pfad": str(p), "info": info}


@app.get("/api/quelle/stream")
def quelle_stream():
    p = _quelle.get("pfad")
    if not p or not Path(p).is_file():
        raise HTTPException(404, "Keine Quelle geladen")
    return FileResponse(p)                # Range bedient Starlette selbst


@app.get("/api/kader")
def kader():
    _balu_auffrischen()
    jetzt = time.time()
    if _kader["daten"] and jetzt - _kader["zeit"] <= 60:
        return _kader["daten"]
    try:
        daten = _balu.kader()
        _kader["daten"], _kader["zeit"] = daten, jetzt
        try:
            KADER_CACHE.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return daten
    except Exception as e:
        # Fällt auf den letzten erfolgreichen Stand zurück, wenn vorhanden.
        if not _kader["daten"] and KADER_CACHE.exists():
            try:
                _kader["daten"] = json.loads(KADER_CACHE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        if _kader["daten"]:
            return _kader["daten"]
        fehlt = not os.getenv("BALU_TRAINER_NAME") or not os.getenv("BALU_TRAINER_PIN")
        hinweis = (" – BALU_TRAINER_NAME / BALU_TRAINER_PIN in der .env eintragen "
                   "und 'Erneut versuchen'." if fehlt else "")
        raise HTTPException(502, f"Kader nicht ladbar: {e}{hinweis}")


@app.post("/api/render")
def render_starten(a: RenderAnfrage):
    p = _quelle.get("pfad")
    if not p:
        raise HTTPException(400, "Erst eine Quelle laden.")
    ff, fp = render.werkzeuge_da()
    if not (ff and fp):
        raise HTTPException(
            400, "ffmpeg/ffprobe nicht gefunden. Installieren mit "
                 "'winget install Gyan.FFmpeg' oder FFMPEG/FFPROBE in der .env setzen.")
    for f in ARBEIT.glob("*"):
        try:
            f.unlink()
        except OSError:
            pass
    ziel = RENDER / time.strftime("clip-%Y%m%d-%H%M%S.mp4")
    projekt = {
        "quelle": p, "in_s": a.in_s, "out_s": a.out_s, "fps": a.fps, "ton": a.ton,
        "annots": [x.model_dump() for x in a.annots],
        "standbilder": [x.model_dump() for x in a.standbilder],
    }
    try:
        return render.render(projekt, ARBEIT, ziel)
    except Exception as e:
        raise HTTPException(500, str(e))


def _render_pfad(name: str) -> Path:
    p = (RENDER / name).resolve()
    if p.parent != RENDER.resolve() or not p.is_file():
        raise HTTPException(404, "Render-Datei nicht gefunden.")
    return p


@app.get("/api/render/{name}")
def render_datei(name: str):
    return FileResponse(_render_pfad(name), media_type="video/mp4")


@app.post("/api/veroeffentlichen")
def veroeffentlichen(a: VeroeffentlichenAnfrage):
    _balu_auffrischen()
    p = _render_pfad(a.datei)
    if not a.titel.strip():
        raise HTTPException(400, "Titel fehlt.")
    meta = {"titel": a.titel.strip(), "notiz": a.notiz.strip(),
            "spiel_datum": a.spiel_datum.strip(), "spieler_ids": a.spieler_ids}
    try:
        meta["dauer_s"] = round(render.probe(p)["dauer"], 2)
    except Exception:
        pass
    try:
        return _balu.upload_clip(p, meta)
    except Exception as e:
        raise HTTPException(502, f"Upload fehlgeschlagen: {e}")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import threading
    import webbrowser

    import uvicorn

    # Kurz warten, bis der Server steht, dann den Editor im Standardbrowser
    # aufmachen - damit reicht ein Doppelklick auf "starten.bat".
    threading.Timer(1.3, lambda: webbrowser.open("http://127.0.0.1:8300")).start()
    uvicorn.run(app, host="127.0.0.1", port=8300)
