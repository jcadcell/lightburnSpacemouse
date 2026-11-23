@echo off
title SpaceMouse for LightBurn
cd /d "%~dp0"
echo SpaceMouse pan/zoom helper for LightBurn
echo To stop it: close this window, or press Ctrl+C.
echo.
python spacemouse_lightburn.py
echo.
echo Helper stopped.
pause
