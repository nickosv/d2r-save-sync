"""Windows Desktop and Start menu shortcuts that launch the window with pythonw.

Smart App Control blocks unsigned executables, including a PyInstaller build
and the .exe files pip would generate for console scripts. The shortcut
target is pythonw.exe from the python.org install, with the arguments
`-m d2rsync`. Creating the .lnk file uses the shell COM interfaces directly.
No batch file, PowerShell script, or extra executable is written.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

SHORTCUT_NAME = "D2R Save Sync.lnk"
MODULE_NAME = "d2rsync"
ARGUMENTS = f"-m {MODULE_NAME}"
DESCRIPTION = "D2R Save Sync"


class ShortcutError(Exception):
    """The Desktop or Start menu shortcut could not be created."""


def icon_path() -> Path:
    path = Path(__file__).resolve().parent / "assets" / "d2rsync.ico"
    if not path.is_file():
        raise ShortcutError(f"application icon is missing: {path}")
    return path


def resolve_pythonw(executable: Path) -> Path:
    """Return pythonw.exe for this interpreter.

    `py -m` reports python.exe. The window must be started with the matching
    pythonw.exe so Windows does not open a console.
    """
    executable = Path(executable)
    if executable.name.lower() == "pythonw.exe":
        return executable
    candidate = executable.with_name("pythonw.exe")
    if candidate.is_file():
        return candidate
    raise ShortcutError(
        "pythonw.exe was not found next to "
        f"{executable}. Install Python from python.org with "
        "`winget install Python.Python.3.12`, open a new PowerShell window, "
        "and run `py -m d2rsync --create-shortcuts` again."
    )


def install_shortcuts(destinations: list[Path] | None = None) -> list[Path]:
    """Write Desktop and Start menu shortcuts. Returns the .lnk paths."""
    if sys.platform != "win32":
        raise ShortcutError(
            "Desktop and Start menu shortcuts are created on Windows. "
            "Start the window with `python3 -m d2rsync`."
        )
    pythonw = resolve_pythonw(Path(sys.executable))
    icon = icon_path()
    folders = list(destinations) if destinations is not None else _default_folders()
    written: list[Path] = []
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / SHORTCUT_NAME
        write_shell_link(
            destination,
            target=pythonw,
            arguments=ARGUMENTS,
            icon=icon,
            working_directory=Path.home(),
            description=DESCRIPTION,
        )
        written.append(destination)
    return written


def _default_folders() -> list[Path]:
    return [_known_folder(_FOLDERID_DESKTOP), _known_folder(_FOLDERID_PROGRAMS)]


# FOLDERID_Desktop, FOLDERID_Programs (Start Menu\Programs)
_FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
_FOLDERID_PROGRAMS = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"
_KF_FLAG_CREATE = 0x00008000


def _known_folder(guid: str) -> Path:
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    folder_id = _guid(GUID, guid)
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    pointer = ctypes.c_void_p()
    result = shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_id),
        _KF_FLAG_CREATE,
        None,
        ctypes.byref(pointer),
    )
    if result < 0 or not pointer.value:
        raise ShortcutError(f"could not find a Windows shortcut folder (0x{result & 0xFFFFFFFF:08X})")
    try:
        return Path(ctypes.wstring_at(pointer))
    finally:
        ole32.CoTaskMemFree(pointer)


def write_shell_link(
    destination: Path,
    *,
    target: Path,
    arguments: str,
    icon: Path,
    working_directory: Path,
    description: str,
) -> None:
    """Create one .lnk through IShellLinkW and IPersistFile."""
    if sys.platform != "win32":
        raise ShortcutError("shell links are created on Windows")
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    clsid = _guid(GUID, "00021401-0000-0000-C000-000000000046")
    iid_link = _guid(GUID, "000214F9-0000-0000-C000-000000000046")
    iid_file = _guid(GUID, "0000010B-0000-0000-C000-000000000046")
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    started = ole32.CoInitialize(None)
    if started < 0:
        raise ShortcutError(f"could not start COM (0x{started & 0xFFFFFFFF:08X})")
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        created = ole32.CoCreateInstance(
            ctypes.byref(clsid),
            None,
            1,
            ctypes.byref(iid_link),
            ctypes.byref(link),
        )
        _check(created, "create the shortcut")
        _call(link, 20, [ctypes.c_wchar_p], str(target))
        _call(link, 11, [ctypes.c_wchar_p], arguments)
        _call(link, 9, [ctypes.c_wchar_p], str(working_directory))
        _call(link, 7, [ctypes.c_wchar_p], description)
        _call(link, 17, [ctypes.c_wchar_p, ctypes.c_int], str(icon), 0)
        _query(link, iid_file, persist)
        _call(persist, 6, [ctypes.c_wchar_p, ctypes.c_int], str(destination), 1)
    finally:
        if persist.value:
            _release(persist)
        if link.value:
            _release(link)
        ole32.CoUninitialize()


def read_shell_link(path: Path) -> tuple[str, str, str]:
    """Return (target, arguments, icon) stored in a .lnk. Windows only."""
    if sys.platform != "win32":
        raise ShortcutError("shell links are read on Windows")
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    clsid = _guid(GUID, "00021401-0000-0000-C000-000000000046")
    iid_link = _guid(GUID, "000214F9-0000-0000-C000-000000000046")
    iid_file = _guid(GUID, "0000010B-0000-0000-C000-000000000046")
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    started = ole32.CoInitialize(None)
    if started < 0:
        raise ShortcutError(f"could not start COM (0x{started & 0xFFFFFFFF:08X})")
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        created = ole32.CoCreateInstance(
            ctypes.byref(clsid),
            None,
            1,
            ctypes.byref(iid_link),
            ctypes.byref(link),
        )
        _check(created, "open the shortcut")
        _query(link, iid_file, persist)
        _call(persist, 5, [ctypes.c_wchar_p, ctypes.c_ulong], str(path), 0)
        target = _read_text(link, 3, [ctypes.c_void_p, ctypes.c_ulong])
        arguments = _read_text(link, 10, [])
        icon = _read_text(link, 16, [ctypes.POINTER(ctypes.c_int)])
        return target, arguments, icon
    finally:
        if persist.value:
            _release(persist)
        if link.value:
            _release(link)
        ole32.CoUninitialize()


def _guid(cls, text: str):
    value = uuid.UUID(text)
    guid = cls()
    guid.Data1 = value.time_low
    guid.Data2 = value.time_mid
    guid.Data3 = value.time_hi_version
    for index, byte in enumerate(value.bytes[8:]):
        guid.Data4[index] = byte
    return guid


def _check(result: int, action: str) -> None:
    if int(result) < 0:
        raise ShortcutError(f"could not {action} (0x{int(result) & 0xFFFFFFFF:08X})")


def _method(unknown, index: int, argtypes: list):
    import ctypes

    table = ctypes.cast(unknown, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return prototype(table[index])


def _call(unknown, index: int, argtypes: list, *args) -> None:
    result = _method(unknown, index, argtypes)(unknown, *args)
    _check(result, "update the shortcut")


def _query(unknown, iid, out) -> None:
    import ctypes

    result = _method(unknown, 0, [ctypes.POINTER(type(iid)), ctypes.POINTER(ctypes.c_void_p)])(
        unknown,
        ctypes.byref(iid),
        ctypes.byref(out),
    )
    _check(result, "read the shortcut")


def _release(unknown) -> None:
    import ctypes

    table = ctypes.cast(unknown, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    prototype(table[2])(unknown)


def _read_text(link, index: int, extra_types: list) -> str:
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    argtypes = [ctypes.POINTER(ctypes.c_wchar), ctypes.c_int, *extra_types]
    extras = []
    if extra_types:
        if extra_types[0] is ctypes.POINTER(ctypes.c_int):
            extras.append(ctypes.byref(ctypes.c_int()))
        else:
            # GetPath may write a WIN32_FIND_DATAW. Give it a real buffer.
            extras.extend([ctypes.create_string_buffer(2048), 0])
    result = _method(link, index, argtypes)(link, buffer, len(buffer), *extras)
    _check(result, "read the shortcut")
    return buffer.value
