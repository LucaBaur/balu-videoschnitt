"""Kleiner Client fuer Balu's Training.

Meldet sich einmal als Trainerin an (Session-Cookie) und laedt fertige Clips
hoch bzw. holt den Kader fuer die Zuordnung. Mehr braucht das Werkzeug von der
App nicht.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests


class LoginFehler(RuntimeError):
    pass


class BaluClient:
    def __init__(self, base: str, name: str, pin: str) -> None:
        self.base = base.rstrip("/")
        self.name = name
        self.pin = pin
        self.s = requests.Session()
        self._angemeldet = False

    # -- intern -------------------------------------------------------------
    def _login(self) -> None:
        if not self.name or not self.pin:
            raise LoginFehler("BALU_TRAINER_NAME / BALU_TRAINER_PIN fehlen in der .env.")
        try:
            r = self.s.post(f"{self.base}/api/login",
                            json={"name": self.name, "pin": self.pin}, timeout=15)
        except requests.RequestException as e:
            raise LoginFehler(f"Balu's Training nicht erreichbar unter {self.base}: {e}")
        if r.status_code != 200:
            # NICHT wiederholen - nach 3 Fehlversuchen sperrt der Server die PIN.
            raise LoginFehler(
                f"Login fehlgeschlagen ({r.status_code}): {r.text[:200]}. "
                "PIN in der .env pruefen - der Server sperrt den Zugang nach 3 Fehlversuchen.")
        self._angemeldet = True

    def _ensure(self) -> None:
        if not self._angemeldet:
            self._login()

    # -- oeffentlich ------------------------------------------------------
    def kader(self) -> list[dict]:
        self._ensure()
        r = self.s.get(f"{self.base}/api/spieler", timeout=15)
        if r.status_code == 401:            # Session abgelaufen -> einmal neu
            self._angemeldet = False
            self._ensure()
            r = self.s.get(f"{self.base}/api/spieler", timeout=15)
        r.raise_for_status()
        return r.json()

    def upload_clip(self, mp4: Path, meta: dict) -> dict:
        self._ensure()
        with open(mp4, "rb") as f:
            r = self.s.post(
                f"{self.base}/api/clips",
                files={"video": (mp4.name, f, "video/mp4")},
                data={"meta": json.dumps(meta)}, timeout=180)
        if r.status_code == 401:
            self._angemeldet = False
            self._ensure()
            with open(mp4, "rb") as f:
                r = self.s.post(
                    f"{self.base}/api/clips",
                    files={"video": (mp4.name, f, "video/mp4")},
                    data={"meta": json.dumps(meta)}, timeout=180)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
        return r.json()
