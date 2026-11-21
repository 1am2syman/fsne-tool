"""Persistent storage path configuration, live inspection, and folder browsing.

Precedence for both ``runs`` and ``history`` directories is explicit:
1. Environment variables (``FSNE_RUNS_DIR``, ``FSNE_HISTORY_DIR``) — locked in UI.
2. User preferences persisted in ``<default_data_dir>/settings.json``.
3. Built-in defaults (``<default_data_dir>/runs`` and ``<default_data_dir>/history``).
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import string
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .action_plans import _in_git_repo
from .errors import RunError

if TYPE_CHECKING:
    from .runs import Store

MONTH_PATTERN = re.compile(r"(20\d{2})[-_](0[1-9]|1[0-2])")
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def settings_path() -> Path:
    from .runs import default_data_dir

    override = os.environ.get("FSNE_SETTINGS_PATH")
    return Path(override) if override else default_data_dir() / "settings.json"


def load_settings() -> dict[str, str]:
    """Read saved storage overrides; corrupt or absent files fall back cleanly."""
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for key in ("runs_dir", "history_dir"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def save_settings(runs_dir: str | None, history_dir: str | None) -> Path:
    """Atomically persist user-chosen storage overrides to settings.json."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str] = {}
    if runs_dir:
        payload["runs_dir"] = runs_dir
    if history_dir:
        payload["history_dir"] = history_dir
    fd, tmp_name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _can_write_directory(directory: Path) -> bool:
    """Probe real writability with a zero-byte temp file (accurate on Windows ACLs)."""
    try:
        fd, name = tempfile.mkstemp(prefix=".fsne-probe-", dir=directory)
        os.close(fd)
        Path(name).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _nearest_existing_ancestor(path: Path) -> Path | None:
    current = path
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _directory_bytes(path: Path, *, max_entries: int = 2000) -> int:
    total = 0
    seen = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                seen += 1
                if seen > max_entries:
                    return total
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _history_months(path: Path) -> list[str]:
    months: set[str] = set()
    try:
        for entry in path.glob("*.xlsx"):
            if entry.name.startswith("~$"):
                continue
            match = MONTH_PATTERN.search(entry.stem)
            if match:
                months.add(f"{match.group(1)}-{match.group(2)}")
    except OSError:
        return []
    return sorted(months)


def _count_runs(path: Path) -> int:
    try:
        return sum(1 for _ in path.glob("RUN-*/meta.json"))
    except OSError:
        return 0


def _count_history_files(path: Path) -> int:
    try:
        return sum(1 for p in path.glob("*.xlsx") if p.is_file() and not p.name.startswith("~$"))
    except OSError:
        return 0


def inspect_path(kind: str, raw_path: str) -> dict:
    """Validate and summarize a candidate runs or history folder without mutating disk."""
    if kind not in {"runs", "history"}:
        raise RunError("Unknown storage kind")
    text = raw_path.strip()
    if not text:
        return {"valid": False, "error": "Enter a full folder path.", "kind": kind}
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        example = r"C:\Users\You\Documents\FSNE\runs" if sys.platform == "win32" else "/home/you/Documents/FSNE/runs"
        return {
            "valid": False,
            "error": f"Enter a full path (for example, {example}).",
            "kind": kind,
        }
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        return {"valid": False, "error": f"Invalid path: {exc.strerror or exc}", "kind": kind}

    if resolved.exists():
        if not resolved.is_dir():
            return {
                "valid": False,
                "error": "That path points to a file, not a folder.",
                "resolved": str(resolved),
                "kind": kind,
            }
        writable = _can_write_directory(resolved)
        if not writable:
            return {
                "valid": False,
                "error": "FSNE does not have write permission for this folder.",
                "resolved": str(resolved),
                "exists": True,
                "writable": False,
                "kind": kind,
            }
        if kind == "runs":
            count = _count_runs(resolved)
            return {
                "valid": True,
                "kind": kind,
                "resolved": str(resolved),
                "exists": True,
                "will_create": False,
                "writable": True,
                "count": count,
                "count_label": f"{count} run" if count == 1 else f"{count} runs",
                "bytes": _directory_bytes(resolved),
            }
        count = _count_history_files(resolved)
        months = _history_months(resolved)
        return {
            "valid": True,
            "kind": kind,
            "resolved": str(resolved),
            "exists": True,
            "will_create": False,
            "writable": True,
            "count": count,
            "count_label": f"{count} settled file" if count == 1 else f"{count} settled files",
            "months": months,
            "in_git_repo": _in_git_repo(resolved),
            "bytes": _directory_bytes(resolved),
        }

    ancestor = _nearest_existing_ancestor(resolved)
    if ancestor is None or not ancestor.is_dir() or not _can_write_directory(ancestor):
        return {
            "valid": False,
            "error": "That folder cannot be created (parent drive or folder is unavailable or read-only).",
            "resolved": str(resolved),
            "exists": False,
            "writable": False,
            "kind": kind,
        }
    return {
        "valid": True,
        "kind": kind,
        "resolved": str(resolved),
        "exists": False,
        "will_create": True,
        "writable": True,
        "count": 0,
        "count_label": "New folder (will be created)",
        "months": [] if kind == "history" else None,
        "in_git_repo": _in_git_repo(ancestor) if kind == "history" else None,
        "bytes": 0,
    }


def _bookmarks() -> list[dict[str, str]]:
    from .runs import BUNDLED_HISTORY, default_data_dir

    items: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(label: str, path: Path, role: str = "folder") -> None:
        try:
            resolved = str(path.resolve()) if path.exists() else str(path)
        except OSError:
            return
        key = resolved.lower() if sys.platform == "win32" else resolved
        if key in seen:
            return
        seen.add(key)
        items.append({"label": label, "path": resolved, "role": role})

    data_dir = default_data_dir()
    add("FSNE default", data_dir, "default")
    add("Documents", data_dir.parent, "documents")
    add("Home", Path.home(), "home")
    if BUNDLED_HISTORY.is_dir():
        add("Bundled baseline", BUNDLED_HISTORY, "baseline")
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                add(f"Drive ({letter}:)", drive, "drive")
    else:
        add("Root (/)", Path("/"), "drive")
    return items


def _breadcrumbs(path: Path) -> list[dict[str, str]]:
    parts = list(path.parts)
    if not parts:
        return []
    crumbs: list[dict[str, str]] = []
    acc = Path(parts[0])
    crumbs.append({"label": parts[0].rstrip("\\") or "/", "path": str(acc)})
    for part in parts[1:]:
        acc = acc / part
        crumbs.append({"label": part, "path": str(acc)})
    return crumbs


def browse_directory(raw_path: str = "", kind: str = "runs") -> dict:
    """List subdirectories and quick locations for the in-app folder navigator."""
    from .runs import default_data_dir

    bookmarks = _bookmarks()
    text = raw_path.strip()
    if text:
        target = Path(text).expanduser()
        if not target.is_absolute():
            raise RunError("Enter a full folder path to browse")
        if not target.exists():
            ancestor = _nearest_existing_ancestor(target)
            target = ancestor if ancestor and ancestor.is_dir() else default_data_dir().parent
        elif target.is_file():
            target = target.parent
    else:
        default_root = default_data_dir()
        target = default_root if default_root.is_dir() else default_root.parent
        if not target.is_dir():
            target = Path.home()

    resolved = target.resolve()
    folders: list[dict[str, object]] = []
    skip_names = {"$recycle.bin", "system volume information", "recovery", "msocache"}
    try:
        entries = sorted(resolved.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise RunError(f"Could not read folder: {exc.strerror or exc}") from exc

    for entry in entries:
        if entry.name.startswith(".") or entry.name.lower() in skip_names:
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        hint = ""
        if kind == "runs":
            n = _count_runs(entry)
            if n:
                hint = f"{n} run" if n == 1 else f"{n} runs"
        else:
            n = _count_history_files(entry)
            if n:
                hint = f"{n} .xlsx"
        folders.append({"name": entry.name, "path": str(entry), "hint": hint})

    parent = str(resolved.parent) if resolved.parent != resolved else None
    return {
        "path": str(resolved),
        "parent": parent,
        "writable": _can_write_directory(resolved),
        "breadcrumbs": _breadcrumbs(resolved),
        "bookmarks": bookmarks,
        "folders": folders,
    }


def create_subfolder(parent_raw: str, name: str) -> dict:
    """Safely create a child directory inside an existing folder."""
    parent_text = parent_raw.strip()
    folder_name = name.strip()
    if not parent_text or not Path(parent_text).is_absolute():
        raise RunError("Choose an existing parent folder first")
    if not folder_name or folder_name in {".", ".."} or any(sep in folder_name for sep in ("/", "\\", ":", "\0")):
        raise RunError("Enter a valid folder name without path separators")
    if folder_name.upper().split(".", 1)[0] in WINDOWS_RESERVED:
        raise RunError("That folder name is reserved by Windows")
    parent = Path(parent_text).resolve()
    if not parent.is_dir():
        raise RunError("Parent folder does not exist")
    target = parent / folder_name
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError:
        raise RunError(f"A folder named '{folder_name}' already exists here") from None
    except OSError as exc:
        raise RunError(f"Could not create folder: {exc.strerror or exc}") from exc
    return browse_directory(str(target))


def pick_folder_windows(initial_path: str = "", title: str = "Select folder") -> str | None:
    """Open the native Windows Vista+ Explorer folder chooser via COM IFileOpenDialog."""
    if sys.platform != "win32":
        raise RunError("Native Windows folder picker is only available on Windows")

    result_box: dict[str, object] = {}

    def _run_sta() -> None:
        try:
            result_box["path"] = _com_pick_folder(initial_path, title)
        except BaseException as exc:  # noqa: BLE001
            result_box["error"] = exc

    thread = threading.Thread(target=_run_sta, name="fsne-folder-dialog", daemon=True)
    thread.start()
    thread.join(timeout=300)
    if thread.is_alive():
        raise RunError("Folder picker timed out")
    if "error" in result_box:
        err = result_box["error"]
        raise RunError(f"Could not open Windows folder picker: {err}")
    chosen = result_box.get("path")
    return str(chosen) if isinstance(chosen, str) and chosen else None


def _com_pick_folder(initial_path: str, title: str) -> str | None:
    """Minimal COM vtable call for IFileOpenDialog(FOS_PICKFOLDERS)."""
    if sys.platform != "win32":
        return None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def make_guid(text: str) -> GUID:
        u = uuid.UUID(text)
        data4 = (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:])
        return GUID(u.time_low, u.time_mid, u.time_hi_version, data4)

    clsid_file_open_dialog = make_guid("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
    iid_file_open_dialog = make_guid("{D57C7288-D4AD-4768-BE02-9D969532D960}")
    iid_shell_item = make_guid("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")

    ole32 = ctypes.WinDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")

    coinit_apartment = 0x2
    clsctx_inproc_server = 0x1
    fos_pickfolders = 0x20
    fos_forcefilesystem = 0x40
    fos_pathmustexist = 0x800
    sigdn_filesystempath = 0x80058000
    hr_cancelled = -2147023673  # 0x800704C7

    hr = ole32.CoInitializeEx(None, coinit_apartment)
    should_uninit = hr >= 0
    dialog = ctypes.c_void_p()
    item = ctypes.c_void_p()
    init_item = ctypes.c_void_p()
    path_ptr = ctypes.c_wchar_p()

    def vcall(obj: ctypes.c_void_p, index: int, restype, *argtypes):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])

    try:
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid_file_open_dialog), None, clsctx_inproc_server,
            ctypes.byref(iid_file_open_dialog), ctypes.byref(dialog),
        )
        if hr < 0 or not dialog.value:
            raise OSError(f"CoCreateInstance failed (0x{hr & 0xFFFFFFFF:08x})")

        # IFileDialog vtable indices: 3=Show, 9=SetOptions, 10=GetOptions, 12=SetFolder, 17=SetTitle, 20=GetResult
        get_options = vcall(dialog, 10, ctypes.c_long, ctypes.POINTER(ctypes.c_uint32))
        set_options = vcall(dialog, 9, ctypes.c_long, ctypes.c_uint32)
        set_title = vcall(dialog, 17, ctypes.c_long, ctypes.c_wchar_p)
        show = vcall(dialog, 3, ctypes.c_long, ctypes.c_void_p)
        get_result = vcall(dialog, 20, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))

        opts = ctypes.c_uint32(0)
        get_options(dialog, ctypes.byref(opts))
        set_options(dialog, opts.value | fos_pickfolders | fos_forcefilesystem | fos_pathmustexist)
        set_title(dialog, title)

        if initial_path and Path(initial_path).is_dir():
            shell32.SHCreateItemFromParsingName.argtypes = [
                ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
            ]
            shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
            if shell32.SHCreateItemFromParsingName(
                initial_path, None, ctypes.byref(iid_shell_item), ctypes.byref(init_item)
            ) >= 0 and init_item.value:
                set_folder = vcall(dialog, 12, ctypes.c_long, ctypes.c_void_p)
                set_folder(dialog, init_item)

        hr = show(dialog, None)
        if hr == hr_cancelled:
            return None
        if hr < 0:
            raise OSError(f"Dialog show failed (0x{hr & 0xFFFFFFFF:08x})")

        hr = get_result(dialog, ctypes.byref(item))
        if hr < 0 or not item.value:
            return None

        # IShellItem::GetDisplayName is vtable index 5
        get_display_name = vcall(item, 5, ctypes.c_long, ctypes.c_uint32, ctypes.POINTER(ctypes.c_wchar_p))
        hr = get_display_name(item, sigdn_filesystempath, ctypes.byref(path_ptr))
        if hr < 0 or not path_ptr.value:
            return None
        return path_ptr.value
    finally:
        if path_ptr.value:
            ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole32.CoTaskMemFree(ctypes.cast(path_ptr, ctypes.c_void_p))
        for obj in (item, init_item, dialog):
            if obj.value:
                release = vcall(obj, 2, ctypes.c_ulong)
                release(obj)
        if should_uninit:
            ole32.CoUninitialize()


def storage_status(store: Store) -> dict:
    """Return full active storage paths, provenance, counts, and capabilities."""
    from .runs import BUNDLED_HISTORY, default_data_dir

    base = default_data_dir()
    saved = load_settings()
    env_runs = bool(os.environ.get("FSNE_RUNS_DIR"))
    env_hist = bool(os.environ.get("FSNE_HISTORY_DIR"))

    runs_default = (base / "runs").resolve()
    hist_default = (base / "history").resolve()
    runs_info = inspect_path("runs", str(store.dir))
    hist_info = inspect_path("history", str(store.history_dir))

    runs_source = "env" if env_runs else ("settings" if "runs_dir" in saved else "default")
    hist_source = "env" if env_hist else ("settings" if "history_dir" in saved else "default")

    bundled_count = _count_history_files(BUNDLED_HISTORY) if BUNDLED_HISTORY.is_dir() else 0
    return {
        "settings_file": str(settings_path()),
        "busy": store.is_busy(),
        "can_native_browse": sys.platform == "win32",
        "bundled_history_count": bundled_count,
        "runs": {
            "path": str(store.dir),
            "default_path": str(runs_default),
            "source": runs_source,
            "locked": env_runs,
            "env_var": "FSNE_RUNS_DIR",
            **{k: v for k, v in runs_info.items() if k != "kind"},
        },
        "history": {
            "path": str(store.history_dir),
            "default_path": str(hist_default),
            "source": hist_source,
            "locked": env_hist,
            "env_var": "FSNE_HISTORY_DIR",
            **{k: v for k, v in hist_info.items() if k != "kind"},
        },
    }
