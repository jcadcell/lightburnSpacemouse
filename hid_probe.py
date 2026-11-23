"""Read the SpaceMouse HID device directly (shared, read-only) for N seconds."""
import ctypes, ctypes.wintypes as wt, sys, time, threading

hid = ctypes.WinDLL("hid")
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("guid", GUID), ("flags", wt.DWORD), ("res", ctypes.c_size_t)]


setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, wt.LPVOID, ctypes.POINTER(GUID), wt.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]


def hid_paths():
    g = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(g))
    h = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)  # PRESENT|INTERFACEDEVICE
    i = 0
    while True:
        d = SP_DEVICE_INTERFACE_DATA(cbSize=ctypes.sizeof(SP_DEVICE_INTERFACE_DATA))
        if not setupapi.SetupDiEnumDeviceInterfaces(h, None, ctypes.byref(g), i, ctypes.byref(d)):
            break
        i += 1
        need = wt.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(d), None, 0, ctypes.byref(need), None)
        buf = ctypes.create_string_buffer(need.value)
        ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8  # cbSize on 64-bit
        if setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(d), buf, need, None, None):
            yield ctypes.wstring_at(ctypes.addressof(buf) + 4)


secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20
for p in hid_paths():
    if "vid_046d&pid_c62b" not in p.lower() and "vid_256f" not in p.lower():
        continue
    print("path:", p)
    for access, label in ((0x80000000, "read"), (0, "query-only")):
        fh = k32.CreateFileW(p, access, 3, None, 3, 0, None)
        if fh in (None, wt.HANDLE(-1).value, -1, 0xFFFFFFFFFFFFFFFF):
            print(f"  open {label}: FAILED err={ctypes.get_last_error()}")
            continue
        print(f"  open {label}: OK")
        if label != "read":
            k32.CloseHandle(fh)
            continue
        counts, last = {}, {}
        stop = time.time() + secs

        def reader():
            b = ctypes.create_string_buffer(64)
            n = wt.DWORD()
            while time.time() < stop:
                if not k32.ReadFile(fh, b, 64, ctypes.byref(n), None):
                    print("  read error", ctypes.get_last_error()); return
                r = b.raw[:n.value]
                counts[r[0]] = counts.get(r[0], 0) + 1
                last[r[0]] = r.hex(" ")
        t = threading.Thread(target=reader, daemon=True); t.start()
        print(f"  wiggle the cap now ({secs:.0f}s)...")
        while time.time() < stop:
            time.sleep(1)
            print("  reports by id:", counts, " last:", last)
        k32.CancelIoEx(fh, None)
        k32.CloseHandle(fh)
