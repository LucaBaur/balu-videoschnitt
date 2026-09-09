@echo off
title Balu-Videoschnitt
cd /d "%~dp0"
echo Balu-Videoschnitt startet - der Browser oeffnet sich gleich von selbst.
echo Dieses Fenster zeigt das Protokoll. Fenster schliessen beendet das Programm.
echo.
py app.py
echo.
echo Das Programm wurde beendet.
pause
