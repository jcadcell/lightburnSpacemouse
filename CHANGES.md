# SpaceMouse → LightBurn pan/zoom: change log

Every change made to this computer for this project is listed here, with how to undo it.
Files inside this project folder itself (the script, this log) are not listed individually.

## Starting state (2026-11-23, nothing changed yet)

- LightBurn 1.7.07 at `C:\Program Files\LightBurn\`
- 3DxWare 10.9.9.721 (3DxWinCore 17.9.9), running
- Device: 3Dconnexion SpaceMouse Pro, USB `VID_046D&PID_C62B`
- Python 3.12.10 (Microsoft Store install), already present, no extra packages needed

Already present before this work (not created by Claude): `%APPDATA%\3Dconnexion\3DxWare\Cfg\SearchHost-KMJ.xml`
(written by the 3DxWare settings panel at 3:50 PM today, probably when its settings were opened while Windows Search was focused).

## Changes

### 1. New 3DxWare profile for LightBurn: all SpaceMouse axes turned off in LightBurn only
- **File created:** `%APPDATA%\3Dconnexion\3DxWare\Cfg\LightBurn-KMJ.xml`
- **Why:** stops 3DxWare's own scroll-wheel zoom in LightBurn, so it doesn't double up with the helper script. SpaceMouse buttons are unaffected; other programs are unaffected.
- **How it was made:** copy of `SearchHost-KMJ.xml` with the program name changed to LightBurn and every `<Enabled>true</Enabled>` changed to `false`.
- **Undo:** delete that file (or in 3DxWare settings with LightBurn in front, re-enable the axes / "Restore defaults").

### 2. Project folder
- **Folder:** `C:\app\lightburnSpacemouse` (holds the script, launcher, docs and this log).
- **Undo:** delete the folder.

### How the helper runs (no lasting effect)
- Started manually with `Start SpaceMouse for LightBurn.bat` in this folder; it shows a console window. Close the window (or Ctrl+C) to stop it.
- Not installed as a service, no startup entry, no scheduled task, nothing in the registry.
- While running it asks Windows for 1 ms timer precision (`timeBeginPeriod`); this ends automatically when it stops.

### Testing only (no lasting effect)
- Ran `spacemouse_lightburn.py --probe` and `hid_probe.py`: read-only, nothing installed, no settings changed.
