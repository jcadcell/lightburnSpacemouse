# SpaceMouse pan & zoom for LightBurn

LightBurn has no built-in support for 3Dconnexion SpaceMouse devices. This small helper adds
**pan** (slide the cap) and **zoom** (pull up / push down) to LightBurn on this computer, without
installing anything and without changing LightBurn or the 3Dconnexion driver.

Built and tested 2026-11-23 with:

| Item | Version |
|---|---|
| LightBurn | 1.7.07 (`C:\Program Files\LightBurn\`) |
| 3DxWare 10 | 10.9.9.721 (3DxWinCore 17.9.9) |
| Device | 3Dconnexion SpaceMouse Pro, USB `VID_046D&PID_C62B` |
| Python | 3.12.10 (Microsoft Store), standard library only |
| Windows | 11 Pro |

---

## Quick start

1. Double-click **`Start SpaceMouse for LightBurn.bat`**. A console window titled
   *SpaceMouse for LightBurn* opens and says the helper is running.
2. Switch to LightBurn and keep the mouse pointer over the design area.
3. Use the cap:

   | Cap movement | Result in LightBurn |
   |---|---|
   | Slide left / right | Pan left / right |
   | Slide forward / back | Pan up / down |
   | Pull up | Zoom in (around the pointer) |
   | Push down | Zoom out |
   | Tilt / twist | Nothing |
   | SpaceMouse buttons | Whatever 3DxWare is set to do (unchanged) |

4. To stop: close the console window, or press **Ctrl+C** in it.

Starting it twice just prints `Already running.` It does nothing when any other program is in
front, so it can be left running all day.

---

## Files in this folder

| File | What it is |
|---|---|
| `Start SpaceMouse for LightBurn.bat` | Launcher. Runs the script in a visible console window. |
| `spacemouse_lightburn.py` | The helper itself (all logic and settings). |
| `hid_probe.py` | Diagnostic only: tries to read the device via a direct HID handle. Not used by the helper. |
| `CHANGES.md` | Log of every change made to this computer for this project, with undo steps. |
| `README.md` | This document. |
| `LICENSE` | MIT license. |

---

## How it works

### Overview

```
 SpaceMouse ──USB──► Windows HID ──┬──► 3DxWare 10 driver ──► (LightBurn profile: axes OFF, buttons ON)
                                   │
                                   └──► Raw Input copy ──► spacemouse_lightburn.py
                                                              │  only if LightBurn is in front
                                                              │  and the pointer is over it
                                                              ▼
                                                 fake middle-button drag  (pan)
                                                 fake scroll-wheel steps  (zoom)
                                                              │
                                                              ▼
                                                          LightBurn
```

LightBurn already pans when you drag with the middle mouse button and zooms with the scroll
wheel. The helper simply turns SpaceMouse movement into those two mouse actions.

### 1. Keeping 3DxWare out of the way

For programs it doesn't natively support, 3DxWare emulates a scroll wheel from the cap. In
LightBurn that would zoom twice (3DxWare + helper). A per-program 3DxWare profile,
`%APPDATA%\3Dconnexion\3DxWare\Cfg\LightBurn-KMJ.xml`, has all six axes set to
`<Enabled>false</Enabled>`. This affects LightBurn only; buttons still work, and every other
program keeps its normal SpaceMouse behaviour. This is the same setting you see (axes off) in the
3Dconnexion settings panel when LightBurn is the active window.

### 2. Reading the SpaceMouse

Windows' **Raw Input** API lets several programs receive copies of a device's reports at once.
The script creates an invisible window and registers it for HID usage page `0x01`, usage `0x08`
("multi-axis controller") with the `RIDEV_INPUTSINK` flag, which means "deliver even when I'm not
the active window". 3DxWare keeps working normally; the helper just listens.

Only devices whose ID contains `VID_046D` (Logitech-era 3Dconnexion) or `VID_256F` (current
3Dconnexion) are used. The SpaceMouse sends small HID reports while the cap is moved:

| Report ID | Content (signed 16-bit, little-endian) |
|---|---|
| 1 | translation X, Y, Z (and rotation too on newer devices with 13-byte reports) |
| 2 | rotation X, Y, Z |
| 3 | buttons (ignored by the helper) |

Values run from about −350 to +350 at full deflection. Measured on this device: sliding can reach
±350, but light sliding tends to stay around ±100–180, which is why the full-scale setting is 300.

### 3. The update loop

The script runs its own loop rather than a Windows timer (which can't go faster than ~15 ms):

- Every **4 ms** (`TICK_MS`) it runs one update.
- While the cap is untouched it **sleeps** until the SpaceMouse sends something, so idle CPU use
  is effectively 0%. (Measured: 0.0% idle; an earlier version without this used ~12% of a core.)
- `timeBeginPeriod(1)` asks Windows for 1 ms timing precision while the script runs. This is
  per-process on Windows 11 and ends automatically when the script exits.

Each update:

1. **Is LightBurn in front?** It checks the foreground window's program file name ends with
   `lightburn.exe`. If not, it releases any drag and does nothing else.
2. **Smoothing.** The three slide axes go through a low-pass filter with a 60 ms time constant
   (`SMOOTHING`). This removes hand jitter and makes starts/stops ease in and out.
3. **Dead zone and curve.** Values under 30 (`DEADZONE`) count as zero so a resting hand doesn't
   drift the view. Above that, the value is scaled to 0–1 against `FULL_SCALE` and raised to the
   power 1.6 (`CURVE`), so small pushes give fine control and full pushes go fast.

### 4. Pan: a virtual middle-button drag

When you slide the cap (and the pointer is over the LightBurn window):

1. The helper presses the **middle mouse button** at the pointer's current position.
2. Each update it moves the pointer by `speed × time` pixels in the direction you're pushing
   (up to `PAN_SPEED` = 1400 px/s at full deflection). Fractions of a pixel are carried over so
   slow pans stay smooth. LightBurn sees an ordinary middle-drag and pans.
3. **Re-grip:** if the pointer gets more than 450 px (`REGRIP_RADIUS`) from where it started, the
   helper releases the button, puts the pointer back and presses again. This keeps the pointer
   inside the window so you can pan indefinitely.
4. When the cap has been back in the dead zone for 60 ms (`STOP_MS`), it releases the button and
   returns the pointer to where it started.

Direction is "object mode": the drawing moves the way you push the cap (same as dragging it with
the mouse). Flip with `PAN_INVERT_X` / `PAN_INVERT_Y`.

Mouse output uses Windows `SendInput` with absolute coordinates across all monitors, and the
script is per-monitor DPI-aware, so it works on scaled and multi-monitor setups.

### 5. Zoom: virtual scroll-wheel steps

Pulling up / pushing down adds up "wheel distance" at up to `ZOOM_SPEED` = 900 units/s
(120 units = one normal wheel click). Each time the total reaches `ZOOM_STEP` = 20 units, one small
scroll event is sent. LightBurn zooms around the pointer, exactly as with a real wheel. Flip
direction with `ZOOM_INVERT`.

### 6. Safety

- Only acts while LightBurn is the active window; other programs never receive anything from it.
- Pan and zoom only start when the pointer is over the LightBurn window.
- On Ctrl+C, normal exit, or closing the console window, any held middle button is released.
- A named mutex (`Local\SpaceMouseLightBurnHelper`) prevents two copies running.
- Errors inside an update are printed in the console and the helper keeps running.

---

## Settings (top of `spacemouse_lightburn.py`)

Edit with any text editor, save, then close and restart the helper.

| Setting | Default | Effect |
|---|---|---|
| `TARGET_EXE` | `lightburn.exe` | Program the helper works in. |
| `DEADZONE` | 30 | Ignore cap movement smaller than this (raw units). Raise if the view drifts. |
| `FULL_SCALE` | 300 | Raw value treated as "full push". Lower = reaches top speed with less force. |
| `CURVE` | 1.6 | 1 = linear; higher = finer control near centre. |
| `PAN_SPEED` | 1400 | Pan speed in pixels/second at full push. |
| `PAN_INVERT_X` / `PAN_INVERT_Y` | False | Reverse pan direction per axis. |
| `REGRIP_RADIUS` | 450 | Pixels of drag before the invisible re-grip. Larger = fewer tiny hitches on long pans. |
| `ZOOM_SPEED` | 900 | Zoom speed (wheel units/second at full push). Halve if zoom is too fast. |
| `ZOOM_STEP` | 20 | Size of each scroll event. Smaller = finer zoom steps. |
| `ZOOM_INVERT` | False | Reverse zoom direction. |
| `SMOOTHING` | 0.06 | Seconds of smoothing. 0 = off (most responsive), higher = softer but laggier. |
| `TICK_MS` | 4 | Update interval in milliseconds. |
| `STOP_MS` | 60 | How long the cap must be centred before the pan drag is released. |

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Nothing happens in LightBurn | Is the console window open and saying "running"? Is LightBurn the active window and the pointer over it? |
| Zoom happens twice per movement | 3DxWare isn't using the LightBurn profile. With LightBurn in front, open the 3Dconnexion settings and make sure the axes are off; restart LightBurn. |
| View drifts on its own | Raise `DEADZONE`. |
| Too sensitive / too slow | Adjust `PAN_SPEED`, `ZOOM_SPEED`, `FULL_SCALE`. |
| Pan or zoom backwards | Set the matching `*_INVERT` to `True`. |
| Feels laggy | Lower `SMOOTHING` (e.g. 0.03). |
| Middle button seems stuck | Click the middle mouse button once. (Shouldn't happen; the helper releases it on exit.) |
| "Already running." | Another copy is open; use that window or close it first. |
| `python` not found | Python must be on PATH. It's currently the Microsoft Store Python 3.12. |
| Check the device is being read | Run `python spacemouse_lightburn.py --probe` in this folder and move the cap: it prints report counts and min/max per axis. |

### Known limits

- The pointer moves by itself while panning and jumps back afterwards; don't move the real mouse
  at the same time.
- If the pointer is over a side panel, the scroll events may scroll that panel instead of zooming.
- Zoom is made of discrete scroll steps, so it can't be perfectly continuous.
- Tilt/twist are read but unused.
- If LightBurn ever adds native SpaceMouse support, stop using this helper and delete the
  3DxWare LightBurn profile (see `CHANGES.md`).

---

## Removing it completely

1. Close the helper window.
2. Delete `%APPDATA%\3Dconnexion\3DxWare\Cfg\LightBurn-KMJ.xml`
   (restores 3DxWare's default scroll-wheel behaviour in LightBurn).
3. Delete this folder.

Nothing else was installed or changed. See `CHANGES.md` for the full record.

---

## Why not the 3Dconnexion SDK?

The 3DxWare SDK (v4, "Navigation Library") is designed for an application to drive its *own*
3D view, and it delivers motion only to the program that's in front. Since LightBurn is in front
while you work, a separate helper can't use it to move LightBurn's view, so Raw Input + mouse
emulation is used instead.
