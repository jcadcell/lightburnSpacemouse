"""
SpaceMouse -> LightBurn pan/zoom helper.

Reads the SpaceMouse directly (Windows Raw Input, shared with 3DxWare, nothing
is installed or changed) and, only while LightBurn is the active window and the
mouse pointer is over it, turns:
  * sliding the cap left/right/forward/back -> a middle-button drag (pan)
  * pulling up / pushing down on the cap      -> scroll-wheel steps (zoom)

Run:   python spacemouse_lightburn.py          (Ctrl+C to stop)
       pythonw spacemouse_lightburn.py         (no console window)
       python spacemouse_lightburn.py --probe  (just print raw SpaceMouse values)
"""

import ctypes
import ctypes.wintypes as wt
import math
import sys
import time

# ----------------------------------------------------------------- settings --
TARGET_EXE = "lightburn.exe"

DEADZONE = 30            # raw units (full deflection is ~350)
FULL_SCALE = 300
CURVE = 1.6              # >1 = finer control near the centre

PAN_SPEED = 1400         # pixels/second at full deflection
PAN_INVERT_X = False     # flip if left/right feels backwards
PAN_INVERT_Y = False     # flip if forward/back feels backwards
REGRIP_RADIUS = 450      # re-centre the virtual drag after this many pixels

ZOOM_SPEED = 900         # wheel units/second at full deflection (120 = 1 notch)
ZOOM_STEP = 20           # send wheel events in chunks of this size (smaller = finer)
ZOOM_INVERT = False      # flip if pull-up/push-down zooms the wrong way

SMOOTHING = 0.06         # seconds; low-pass filter on the cap (0 = off, higher = softer)
TICK_MS = 4              # update interval (was 15)
STOP_MS = 60             # time below deadzone before releasing the drag
# -----------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
except Exception:
    pass

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WM_INPUT = 0x00FF
WM_TIMER = 0x0113
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEHID = 2
RIDI_DEVICENAME = 0x20000007

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD),
                ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU,
                                   wt.HINSTANCE, wt.LPVOID]
user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID,
                                   ctypes.POINTER(wt.UINT), wt.UINT]
user32.GetRawInputDeviceInfoW.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID,
                                          ctypes.POINTER(wt.UINT)]
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, wt.LPVOID]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                                wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CreateMutexW.restype = wt.HANDLE

PROBE = "--probe" in sys.argv

# ------------------------------------------------------------------- state --
axes = {"tx": 0, "ty": 0, "tz": 0, "rx": 0, "ry": 0, "rz": 0}
device_ok = {}          # hDevice -> bool (is it a 3Dconnexion device?)
pan = {"active": False, "origin": None, "pos": None, "idle": 0,
       "fx": 0.0, "fy": 0.0}
zoom_acc = [0.0]
last_tick = [time.perf_counter()]
last_probe = [0.0]
filt = {"tx": 0.0, "ty": 0.0, "tz": 0.0}
probe_range = {}
probe_count = [0]
probe_seen = set()
_fg_cache = {"hwnd": None, "is_target": False}


def is_spacemouse(hdev):
    if hdev in device_ok:
        return device_ok[hdev]
    size = wt.UINT(0)
    user32.GetRawInputDeviceInfoW(hdev, RIDI_DEVICENAME, None, ctypes.byref(size))
    buf = ctypes.create_unicode_buffer(size.value + 1)
    user32.GetRawInputDeviceInfoW(hdev, RIDI_DEVICENAME, buf, ctypes.byref(size))
    name = buf.value.upper()
    ok = "VID_046D" in name or "VID_256F" in name
    device_ok[hdev] = ok
    if PROBE:
        print(f"device: {buf.value}  -> {'used' if ok else 'ignored'}")
    return ok


def i16(b, o):
    return int.from_bytes(b[o:o + 2], "little", signed=True)


def handle_report(data):
    rid = data[0]
    probe_count[0] += 1
    if PROBE and rid not in probe_seen:
        probe_seen.add(rid)
        print(f"first report id {rid}: {data.hex(' ')}")
    if rid == 1 and len(data) >= 7:
        axes["tx"], axes["ty"], axes["tz"] = i16(data, 1), i16(data, 3), i16(data, 5)
        if len(data) >= 13:  # newer devices send rotation in the same report
            axes["rx"], axes["ry"], axes["rz"] = i16(data, 7), i16(data, 9), i16(data, 11)
    elif rid == 2 and len(data) >= 7:
        axes["rx"], axes["ry"], axes["rz"] = i16(data, 1), i16(data, 3), i16(data, 5)


def on_wm_input(lparam):
    size = wt.UINT(0)
    hdr = ctypes.sizeof(RAWINPUTHEADER)
    user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), hdr)
    buf = ctypes.create_string_buffer(size.value)
    if user32.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), hdr) != size.value:
        return
    h = RAWINPUTHEADER.from_buffer_copy(buf.raw[:hdr])
    if h.dwType != RIM_TYPEHID or not is_spacemouse(h.hDevice):
        return
    size_hid = int.from_bytes(buf.raw[hdr:hdr + 4], "little")
    count = int.from_bytes(buf.raw[hdr + 4:hdr + 8], "little")
    base = hdr + 8
    for i in range(count):
        handle_report(buf.raw[base + i * size_hid: base + (i + 1) * size_hid])


# ------------------------------------------------------------------ output --
def send_mouse(flags, dx=0, dy=0, data=0):
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(dx, dy, ctypes.c_uint32(data).value, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def move_abs(x, y):
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    nx = int(round((x - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((y - vy) * 65535 / max(vh - 1, 1)))
    send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)


def cursor():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p


def foreground_is_target():
    fg = user32.GetForegroundWindow()
    if fg == _fg_cache["hwnd"]:
        return fg, _fg_cache["is_target"]
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    ok = False
    hp = kernel32.OpenProcess(0x1000, False, pid.value)
    if hp:
        n = wt.DWORD(1024)
        b = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(hp, 0, b, ctypes.byref(n)):
            ok = b.value.lower().endswith("\\" + TARGET_EXE)
        kernel32.CloseHandle(hp)
    _fg_cache.update(hwnd=fg, is_target=ok)
    return fg, ok


def cursor_over(fg):
    w = user32.WindowFromPoint(cursor())
    return bool(w) and user32.GetAncestor(w, 2) == fg  # GA_ROOT


def shape(v):
    a = abs(v)
    if a <= DEADZONE:
        return 0.0
    t = min((a - DEADZONE) / (FULL_SCALE - DEADZONE), 1.0) ** CURVE
    return t if v > 0 else -t


def release_pan():
    if pan["active"]:
        send_mouse(MOUSEEVENTF_MIDDLEUP)
        o = pan["origin"]
        move_abs(o[0], o[1])
    pan.update(active=False, idle=0, fx=0.0, fy=0.0)


def tick():
    now = time.perf_counter()
    dt = min(now - last_tick[0], 0.1)
    last_tick[0] = now

    if PROBE:
        for k, v in axes.items():
            lo, hi = probe_range.get(k, (0, 0))
            probe_range[k] = (min(lo, v), max(hi, v))
        if now - last_probe[0] > 2.0:
            last_probe[0] = now
            print(f"reports={probe_count[0]:6d}  min/max: " +
                  "  ".join(f"{k}[{lo},{hi}]" for k, (lo, hi) in probe_range.items()))
        return

    fg, ok = foreground_is_target()
    if not ok:
        release_pan()
        zoom_acc[0] = 0.0
        return

    a = 1.0 - math.exp(-dt / SMOOTHING) if SMOOTHING > 0 else 1.0
    for k in ("tx", "ty", "tz"):
        filt[k] += (axes[k] - filt[k]) * a
    sx, sy, sz = shape(filt["tx"]), shape(filt["ty"]), shape(filt["tz"])

    # ---- pan (middle-button drag) ----
    if sx or sy:
        if not pan["active"]:
            if cursor_over(fg):
                c = cursor()
                pan.update(active=True, origin=(c.x, c.y), pos=[c.x, c.y], idle=0)
                send_mouse(MOUSEEVENTF_MIDDLEDOWN)
        if pan["active"]:
            pan["idle"] = 0
            vx = sx * PAN_SPEED * dt * (-1 if PAN_INVERT_X else 1)
            vy = sy * PAN_SPEED * dt * (-1 if PAN_INVERT_Y else 1)
            pan["fx"] += vx
            pan["fy"] += vy
            ix, iy = int(pan["fx"]), int(pan["fy"])
            pan["fx"] -= ix
            pan["fy"] -= iy
            if ix or iy:
                p = pan["pos"]
                p[0] += ix
                p[1] += iy
                move_abs(p[0], p[1])
                o = pan["origin"]
                if abs(p[0] - o[0]) > REGRIP_RADIUS or abs(p[1] - o[1]) > REGRIP_RADIUS:
                    send_mouse(MOUSEEVENTF_MIDDLEUP)
                    move_abs(o[0], o[1])
                    send_mouse(MOUSEEVENTF_MIDDLEDOWN)
                    pan["pos"] = [o[0], o[1]]
    elif pan["active"]:
        pan["idle"] += dt * 1000
        if pan["idle"] >= STOP_MS:
            release_pan()

    # ---- zoom (wheel) ----
    if sz and (pan["active"] or cursor_over(fg)):
        # pulling the cap up gives negative tz on most devices -> zoom in
        zoom_acc[0] += -sz * ZOOM_SPEED * dt * (-1 if ZOOM_INVERT else 1)
        while abs(zoom_acc[0]) >= ZOOM_STEP:
            step = ZOOM_STEP if zoom_acc[0] > 0 else -ZOOM_STEP
            send_mouse(MOUSEEVENTF_WHEEL, data=step)
            zoom_acc[0] -= step
    else:
        zoom_acc[0] = 0.0


# ------------------------------------------------------------------- setup --
@WNDPROC
def wndproc(hwnd, msg, wparam, lparam):
    try:
        if msg == WM_INPUT:
            on_wm_input(lparam)
    except Exception as e:  # never let an exception kill the message loop
        print("error:", e)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def main():
    if not PROBE:
        kernel32.CreateMutexW(None, False, "Local\\SpaceMouseLightBurnHelper")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            print("Already running.")
            return

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW(lpfnWndProc=wndproc, hInstance=hinst,
                   lpszClassName="SpaceMouseLightBurnHelper")
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "SpaceMouse LightBurn helper",
                                  0, 0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    rid = RAWINPUTDEVICE(0x01, 0x08, RIDEV_INPUTSINK, hwnd)  # multi-axis controller
    if not user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
        raise ctypes.WinError(ctypes.get_last_error())

    print("Probing SpaceMouse (Ctrl+C to stop)..." if PROBE else
          "SpaceMouse -> LightBurn helper running (Ctrl+C to stop).")

    # If the console window is closed mid-pan, let go of the middle button.
    global _ctrl_handler
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.DWORD)
    def _ctrl_handler(event):
        if event == 2:  # CTRL_CLOSE_EVENT
            try:
                release_pan()
            except Exception:
                pass
        return False
    kernel32.SetConsoleCtrlHandler(_ctrl_handler, True)

    # Our own loop instead of a WM_TIMER (which can't go below ~15 ms).
    # timeBeginPeriod(1) asks Windows for 1 ms sleep precision while we run;
    # it is per-process and ends automatically when this script exits.
    winmm = ctypes.WinDLL("winmm")
    winmm.timeBeginPeriod(1)
    msg = wt.MSG()
    next_tick = time.perf_counter()
    try:
        while True:
            resting = (not pan["active"] and not any(axes[k] for k in filt)
                       and all(abs(v) < 0.5 for v in filt.values()))
            if resting and not PROBE:
                # cap untouched: sleep until the SpaceMouse sends something
                for k in filt:
                    filt[k] = 0.0
                user32.MsgWaitForMultipleObjects(0, None, False, 500, 0x04FF)
                last_tick[0] = time.perf_counter()
                next_tick = last_tick[0]
            wait = max(0, int((next_tick - time.perf_counter()) * 1000))
            user32.MsgWaitForMultipleObjects(0, None, False, wait, 0x04FF)  # QS_ALLINPUT
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                if msg.message == 0x0012:  # WM_QUIT
                    return
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            now = time.perf_counter()
            if now >= next_tick:
                try:
                    tick()
                except Exception as e:
                    print("error:", e)
                next_tick = max(next_tick + TICK_MS / 1000, now)
    except KeyboardInterrupt:
        pass
    finally:
        release_pan()
        winmm.timeEndPeriod(1)


if __name__ == "__main__":
    main()
