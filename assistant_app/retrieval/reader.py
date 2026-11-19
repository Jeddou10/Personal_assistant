"""Windows-only secure local reader; other platforms fail closed.

All ancestors are opened without write/delete sharing and held through the read.
OPEN_REPARSE_POINT plus handle attributes rejects symlinks, junctions and other
reparse points, including cloud placeholders. Final handle names and the selected
root's volume/file identity are checked BEFORE bytes and again afterwards.

This deliberately rejects UNC, device paths, remote drives, alternate streams,
and files already open for writing. Name-based secret filtering is conservative,
not a guarantee that an ordinary text document contains no confidential data.
This protects against ordinary filesystem races, not privileged kernel attackers.
"""

import ctypes
import hashlib
import ntpath
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from assistant_app.domain.errors import AppError

from .models import LoadedDocument, RootIdentity

_DIRECTORY = 0x10
_REPARSE = 0x400
_ALLOWED = {".txt", ".md", ".csv", ".json"}
_DENIED = {
    "node_modules",
    "vendor",
    "build",
    "dist",
    "target",
    "coverage",
    "__pycache__",
    "venv",
    "env",
    "site-packages",
    "logs",
    "cache",
    "tmp",
}
_SECRET = re.compile(
    r"(?:^|[._\- ])(?:secrets?|credentials?|passwords?|tokens?|auth|oauth|private|id_rsa|id_ed25519)(?:$|[._\- ])", re.I
)


def safe_path(path: str) -> str:
    """Normalize ordinary absolute DOS paths, without following filesystem links."""
    path = os.fspath(path)
    if not isinstance(path, str) or not path or len(path) > 240 or any(ord(c) < 32 for c in path):
        raise AppError("Invalid retrieval path")
    if path.startswith(("\\\\", "//")):
        raise AppError("Only local drive paths are supported")
    # Win32 abspath may silently strip trailing dots/spaces. Reject before it runs.
    for part in path.replace("/", "\\").split("\\"):
        if part not in ("", ".", "..") and part.endswith((".", " ")):
            raise AppError("Ambiguous retrieval path")
    path = ntpath.abspath(path)
    drive, tail = ntpath.splitdrive(path)
    if not re.fullmatch(r"[A-Za-z]:", drive) or ":" in tail:
        raise AppError("Only ordinary local drive paths are supported")
    for part in tail.replace("/", "\\").split("\\"):
        if part and (part.endswith((".", " ")) or any(c in part for c in '*?<>|"')):
            raise AppError("Ambiguous retrieval path")
    if os.name == "nt":
        # Expand 8.3 aliases, NOT symlinks/reparse targets. Every resulting component
        # still has to pass the held-handle checks. No file contents are accessed.
        from ctypes import wintypes as w

        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        drive_type = dll.GetDriveTypeW
        drive_type.argtypes = [w.LPCWSTR]
        drive_type.restype = w.UINT
        if drive_type(drive + "\\") not in (2, 3, 6):
            raise AppError("Only local retrieval drives are supported")
        expand = dll.GetLongPathNameW
        expand.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        expand.restype = w.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        count = expand(path, buffer, len(buffer))
        if 0 < count < len(buffer):
            path = buffer.value
    return ntpath.normpath(path)


def check_document_name(path: str) -> None:
    parts = ntpath.normpath(path).replace("/", "\\").split("\\")
    for part in parts:
        if part.startswith(".") or part.casefold() in _DENIED or _SECRET.search(part):
            raise AppError("Secret or generated paths are excluded")
    filename = parts[-1].casefold()
    if ntpath.splitext(filename)[1] not in _ALLOWED or any(
        marker in filename for marker in (".min.", ".generated.", "lock.json", "service-account", "service_account")
    ):
        raise AppError("Unsupported or generated document")


def contained(root: str, path: str) -> bool:
    try:
        return ntpath.normcase(ntpath.commonpath((root, path))) == ntpath.normcase(root)
    except ValueError:
        return False


@dataclass(frozen=True)
class HandleInfo:
    attributes: int
    identity: tuple[int, int]
    size: int
    modified: int
    created: int

    @property
    def fingerprint(self) -> tuple[int, ...]:
        return (*self.identity, self.size, self.modified, self.created)


class WindowsAPI:
    """Small injectable handle boundary; no bytes are read by open/info/final_path."""

    def __init__(self):
        if os.name != "nt":
            raise AppError("Secure retrieval is supported only on Windows")
        from ctypes import wintypes as w

        class ByHandleInfo(ctypes.Structure):
            _fields_ = [
                ("attributes", w.DWORD),
                ("created", w.FILETIME),
                ("accessed", w.FILETIME),
                ("modified", w.FILETIME),
                ("volume", w.DWORD),
                ("size_high", w.DWORD),
                ("size_low", w.DWORD),
                ("links", w.DWORD),
                ("index_high", w.DWORD),
                ("index_low", w.DWORD),
            ]

        self.info_type = ByHandleInfo
        self.dll = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateFileW": ([w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            "GetFileInformationByHandle": ([w.HANDLE, ctypes.POINTER(ByHandleInfo)], w.BOOL),
            "GetFinalPathNameByHandleW": ([w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
            "GetDriveTypeW": ([w.LPCWSTR], w.UINT),
            "ReadFile": ([w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.dll, name)
            function.argtypes = args
            function.restype = result

    def open(self, path: str, directory: bool):
        # FILE_READ_ATTRIBUTES for ancestors; GENERIC_READ for the file.
        access = 0x80 if directory else 0x80000000
        handle = self.dll.CreateFileW(path, access, 1, None, 3, 0x00200000 | 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise AppError("Cannot securely open retrieval source")
        return handle

    def close(self, handle):
        self.dll.CloseHandle(handle)

    def local(self, path: str) -> bool:
        return self.dll.GetDriveTypeW(ntpath.splitdrive(path)[0] + "\\") in (2, 3, 6)

    def info(self, handle) -> HandleInfo:
        value = self.info_type()
        if not self.dll.GetFileInformationByHandle(handle, ctypes.byref(value)):
            raise AppError("Cannot verify retrieval handle")

        def timestamp(t):
            return (t.dwHighDateTime << 32) | t.dwLowDateTime

        return HandleInfo(
            value.attributes,
            (value.volume, (value.index_high << 32) | value.index_low),
            (value.size_high << 32) | value.size_low,
            timestamp(value.modified),
            timestamp(value.created),
        )

    def final_path(self, handle) -> str:
        buffer = ctypes.create_unicode_buffer(32768)
        count = self.dll.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not count or count >= len(buffer):
            raise AppError("Cannot verify retrieval handle path")
        path = buffer.value
        if path.startswith("\\\\?\\"):
            path = path[4:]
        return safe_path(path)

    def read(self, handle, maximum: int) -> bytes:
        from ctypes import wintypes as w

        result = bytearray()
        while len(result) <= maximum:
            size = min(65536, maximum + 1 - len(result))
            buffer = ctypes.create_string_buffer(size)
            count = w.DWORD()
            if not self.dll.ReadFile(handle, buffer, size, ctypes.byref(count), None):
                raise AppError("Cannot read retrieval source")
            if not count.value:
                break
            result.extend(buffer.raw[: count.value])
        if len(result) > maximum:
            raise AppError("Document exceeds byte budget")
        return bytes(result)


class WindowsDocumentLoader:
    """Injected APIs are trusted test seams, not a mechanism for weakening security."""

    def __init__(self, *, api=None):
        self._api = api

    @property
    def api(self):
        if self._api is None:
            self._api = WindowsAPI()
        return self._api

    def _verify(self, handle, path: str, directory: bool) -> HandleInfo:
        info = self.api.info(handle)
        final = self.api.final_path(handle)
        if (
            info.attributes & _REPARSE
            or bool(info.attributes & _DIRECTORY) != directory
            or ntpath.normcase(final) != ntpath.normcase(path)
            or not self.api.local(final)
        ):
            raise AppError("Retrieval handle is outside verified scope")
        return info

    @contextmanager
    def _chain(self, path: str, directory: bool):
        handles = []
        try:
            if not self.api.local(path):
                raise AppError("Remote retrieval roots are excluded")
            drive, tail = ntpath.splitdrive(path)
            components = [drive + "\\"]
            for part in tail.strip("\\").split("\\"):
                if part:
                    components.append(ntpath.join(components[-1], part))
            for i, component in enumerate(components):
                is_dir = directory or i < len(components) - 1
                handle = self.api.open(component, is_dir)
                handles.append((handle, component, is_dir))
                self._verify(handle, component, is_dir)
            yield handles
        finally:
            for handle, _, _ in reversed(handles):
                self.api.close(handle)

    def approve_root(self, path: str) -> RootIdentity:
        path = safe_path(path)
        with self._chain(path, True) as chain:
            for handle, component, directory in chain:
                info = self._verify(handle, component, directory)
            return RootIdentity(uuid4().hex, path, info.identity)

    def read(self, root: RootIdentity, path: str, max_bytes: int) -> LoadedDocument:
        path = safe_path(path)
        check_document_name(path)
        if not contained(root.path, path) or ntpath.normcase(path) == ntpath.normcase(root.path):
            raise AppError("Document is outside approved roots")
        with self._chain(path, False) as chain:

            def verify_scope():
                found_root = False
                for handle, component, directory in chain:
                    info = self._verify(handle, component, directory)
                    if ntpath.normcase(component) == ntpath.normcase(root.path):
                        if info.identity != root.identity:
                            raise AppError("Approved root identity changed")
                        found_root = True
                if not found_root:
                    raise AppError("Approved root is absent from handle chain")
                if not contained(root.path, self.api.final_path(chain[-1][0])):
                    raise AppError("Document handle escaped approved root")
                return info

            before = verify_scope()
            if before.size > max_bytes:
                raise AppError("Document exceeds byte budget")
            data = self.api.read(chain[-1][0], max_bytes)
            after = verify_scope()
            if after != before or len(data) != before.size or len(data) > max_bytes:
                raise AppError("Document changed during secure read")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise AppError("Document must be strict UTF-8") from None
        if "\x00" in text:
            raise AppError("Binary documents are excluded")
        return LoadedDocument(
            ntpath.relpath(path, root.path).replace("\\", "/"),
            text,
            hashlib.sha256(data).hexdigest(),
            len(data),
            before.fingerprint,
        )
