"""Fail-closed dependency closure inspection for Windows PE binaries.

The qualification package is a Windows x64 application, so native closure is
checked without importing the optional AI runtime.  The parser intentionally
distinguishes a valid image with no import directory from an image that could
not be parsed.  DLL resolution is also deliberately narrower than a recursive
package-wide filename lookup: only directories that the Windows loader or an
audited runtime mechanism can reach are considered.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SYSTEM_DLLS = {
    "advapi32.dll",
    "api-ms-win-core-file-l1-1-0.dll",
    "api-ms-win-core-file-l1-2-0.dll",
    "api-ms-win-core-handle-l1-1-0.dll",
    "api-ms-win-core-heap-l1-1-0.dll",
    "api-ms-win-core-libraryloader-l1-1-0.dll",
    "api-ms-win-core-localization-l1-1-0.dll",
    "api-ms-win-core-localization-l1-2-0.dll",
    "api-ms-win-core-memory-l1-1-0.dll",
    "api-ms-win-core-processenvironment-l1-1-0.dll",
    "api-ms-win-core-processthreads-l1-1-0.dll",
    "api-ms-win-core-profile-l1-1-0.dll",
    "api-ms-win-core-rtlsupport-l1-1-0.dll",
    "api-ms-win-core-synch-l1-1-0.dll",
    "api-ms-win-core-sysinfo-l1-1-0.dll",
    "api-ms-win-core-timezone-l1-1-0.dll",
    "api-ms-win-core-util-l1-1-0.dll",
    "api-ms-win-crt-conio-l1-1-0.dll",
    "api-ms-win-crt-convert-l1-1-0.dll",
    "api-ms-win-crt-environment-l1-1-0.dll",
    "api-ms-win-crt-filesystem-l1-1-0.dll",
    "api-ms-win-crt-heap-l1-1-0.dll",
    "api-ms-win-crt-locale-l1-1-0.dll",
    "api-ms-win-crt-math-l1-1-0.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll",
    "api-ms-win-crt-stdio-l1-1-0.dll",
    "api-ms-win-crt-string-l1-1-0.dll",
    "api-ms-win-crt-time-l1-1-0.dll",
    "api-ms-win-crt-utility-l1-1-0.dll",
    "avrt.dll",
    "avicap32.dll",
    "bcrypt.dll",
    "bcryptprimitives.dll",
    "cfgmgr32.dll",
    "cabinet.dll",
    "comctl32.dll",
    "comdlg32.dll",
    "combase.dll",
    "crypt32.dll",
    "d3d12.dll",
    "d3d11.dll",
    "d3dcompiler_47.dll",
    "d2d1.dll",
    "dinput8.dll",
    "dnsapi.dll",
    "dbghelp.dll",
    "dxgi.dll",
    "gdi32.dll",
    "gdi32full.dll",
    "imagehlp.dll",
    "dwrite.dll",
    "imm32.dll",
    "iphlpapi.dll",
    "kernel32.dll",
    "kernelbase.dll",
    "mpr.dll",
    "mswsock.dll",
    "mf.dll",
    "mfplat.dll",
    "mfreadwrite.dll",
    "msvcrt.dll",
    "netapi32.dll",
    "msi.dll",
    "ncrypt.dll",
    "ntdll.dll",
    "ole32.dll",
    "oleaut32.dll",
    "opengl32.dll",
    "psapi.dll",
    "propsys.dll",
    "pdh.dll",
    "powrprof.dll",
    "rpcrt4.dll",
    "secur32.dll",
    "setupapi.dll",
    "shell32.dll",
    "shlwapi.dll",
    "user32.dll",
    "userenv.dll",
    "uxtheme.dll",
    "usp10.dll",
    "version.dll",
    "win32u.dll",
    "winhttp.dll",
    "wininet.dll",
    "wintrust.dll",
    "winmm.dll",
    "ws2_32.dll",
    "wsock32.dll",
    "wldap32.dll",
    "xinput1_4.dll",
    "ucrtbase.dll",
}

MSVC_DLLS = {
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vccorlib140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
}

HOST_OPTIONAL_DLLS = {"nvcuda.dll"}

THIRD_PARTY_MARKERS = (
    "avcodec",
    "avdevice",
    "avfilter",
    "avformat",
    "avutil",
    "c10",
    "cublas",
    "cudnn",
    "cudart",
    "cufft",
    "curand",
    "cusolver",
    "cusparse",
    "ffi",
    "iomp",
    "jpeg",
    "libpng",
    "nvrtc",
    "nvtx",
    "onnx",
    "opencv",
    "png",
    "protobuf",
    "swresample",
    "swscale",
    "torch",
    "webp",
)

MACHINE_NAMES = {
    0x014C: "x86",
    0x01C0: "arm",
    0x01C4: "arm_nt",
    0xAA64: "arm64",
    0x8664: "x64",
}


class PEParseError(ValueError):
    """A structurally invalid or truncated PE image."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        message = code if not detail else f"{code}:{detail}"
        super().__init__(message)


@dataclass(frozen=True)
class _PEImage:
    machine: int
    magic: int
    image_base: int
    direct_imports: tuple[str, ...]
    delay_imports: tuple[str, ...]


@dataclass(frozen=True)
class _Section:
    virtual_address: int
    virtual_size: int
    raw_size: int
    raw_pointer: int


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        raise PEParseError(code, detail)


def _rva_offset(
    rva: int, size: int, sections: list[_Section], file_size: int
) -> int | None:
    """Map an RVA range to contiguous raw bytes, rejecting virtual-only data."""

    if rva < 0 or size <= 0:
        return None
    end = rva + size
    for section in sections:
        virtual_end = section.virtual_address + max(section.virtual_size, section.raw_size)
        raw_end = section.virtual_address + section.raw_size
        if section.virtual_address <= rva and end <= raw_end and end <= virtual_end:
            offset = section.raw_pointer + (rva - section.virtual_address)
            if 0 <= offset and offset + size <= file_size:
                return offset
    return None


def _section_for_rva(rva: int, sections: list[_Section]) -> _Section | None:
    for section in sections:
        end = section.virtual_address + section.raw_size
        if section.virtual_address <= rva < end:
            return section
    return None


def _read_rva(data: bytes, rva: int, size: int, sections: list[_Section], code: str) -> bytes:
    offset = _rva_offset(rva, size, sections, len(data))
    if offset is None:
        raise PEParseError(code, f"rva=0x{rva:x},size={size}")
    return data[offset : offset + size]


def _read_c_string(
    data: bytes, rva: int, sections: list[_Section], *, code_prefix: str
) -> str:
    section = _section_for_rva(rva, sections)
    if section is None:
        raise PEParseError(f"{code_prefix}_name_rva_invalid", f"rva=0x{rva:x}")
    offset = _rva_offset(rva, 1, sections, len(data))
    if offset is None:
        raise PEParseError(f"{code_prefix}_name_rva_invalid", f"rva=0x{rva:x}")
    available = min(4096, section.raw_pointer + section.raw_size - offset)
    payload = data[offset : offset + available]
    end = payload.find(b"\x00")
    if end < 0:
        raise PEParseError(f"{code_prefix}_name_unterminated", f"rva=0x{rva:x}")
    raw = payload[:end]
    _require(bool(raw), f"{code_prefix}_name_empty")
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PEParseError(f"{code_prefix}_name_not_ascii") from exc
    _require(
        all(0x20 <= ord(char) < 0x7F for char in value),
        f"{code_prefix}_name_invalid_chars",
    )
    _require(
        "/" not in value and "\\" not in value and ":" not in value,
        f"{code_prefix}_name_path",
    )
    return value.casefold()


def _directory(
    optional: bytes, directory_start: int, directory_count: int, index: int
) -> tuple[int, int] | None:
    if index >= directory_count:
        return None
    offset = directory_start + index * 8
    if offset + 8 > len(optional):
        raise PEParseError("data_directory_truncated", f"index={index}")
    rva = _u32(optional, offset)
    size = _u32(optional, offset + 4)
    if rva == 0 and size == 0:
        return None
    if rva == 0 or size == 0:
        raise PEParseError("declared_directory_empty", f"index={index},rva={rva},size={size}")
    return rva, size


def _validate_thunk(
    data: bytes, rva: int, width: int, sections: list[_Section], code: str
) -> None:
    _read_rva(data, rva, width, sections, code)


def _parse_import_directory(
    data: bytes,
    directory: tuple[int, int] | None,
    sections: list[_Section],
    thunk_width: int,
) -> tuple[str, ...]:
    if directory is None:
        return ()
    rva, size = directory
    _require(size >= 20, "import_directory_too_small")
    names: list[str] = []
    terminated = False
    for descriptor_offset in range(0, size, 20):
        if descriptor_offset + 20 > size:
            raise PEParseError("import_descriptor_truncated")
        descriptor = _read_rva(
            data, rva + descriptor_offset, 20, sections, "import_directory_rva_invalid"
        )
        if descriptor == b"\x00" * 20:
            terminated = True
            break
        original_thunk = _u32(descriptor, 0)
        name_rva = _u32(descriptor, 12)
        first_thunk = _u32(descriptor, 16)
        _require(name_rva != 0, "import_descriptor_name_missing")
        _require(first_thunk != 0, "import_descriptor_first_thunk_missing")
        _validate_thunk(data, first_thunk, thunk_width, sections, "import_first_thunk_rva_invalid")
        if original_thunk:
            _validate_thunk(
                data, original_thunk, thunk_width, sections, "import_original_thunk_rva_invalid"
            )
        names.append(_read_c_string(data, name_rva, sections, code_prefix="import"))
    _require(terminated, "import_directory_terminator_missing")
    return tuple(names)


def _delay_pointer_to_rva(value: int, *, rva_based: bool, image_base: int) -> int:
    if value == 0:
        return 0
    if rva_based:
        return value
    _require(value >= image_base, "delay_import_va_before_image_base")
    return value - image_base


def _parse_delay_import_directory(
    data: bytes,
    directory: tuple[int, int] | None,
    sections: list[_Section],
    thunk_width: int,
    image_base: int,
) -> tuple[str, ...]:
    if directory is None:
        return ()
    rva, size = directory
    _require(size >= 32, "delay_import_directory_too_small")
    names: list[str] = []
    terminated = False
    for descriptor_offset in range(0, size, 32):
        if descriptor_offset + 32 > size:
            raise PEParseError("delay_import_descriptor_truncated")
        descriptor = _read_rva(
            data,
            rva + descriptor_offset,
            32,
            sections,
            "delay_import_directory_rva_invalid",
        )
        if descriptor == b"\x00" * 32:
            terminated = True
            break
        attributes = _u32(descriptor, 0)
        _require(attributes & ~1 == 0, "delay_import_descriptor_attributes_invalid")
        rva_based = bool(attributes & 1)
        name_pointer = _delay_pointer_to_rva(
            _u32(descriptor, 4), rva_based=rva_based, image_base=image_base
        )
        module_handle = _delay_pointer_to_rva(
            _u32(descriptor, 8), rva_based=rva_based, image_base=image_base
        )
        iat_pointer = _delay_pointer_to_rva(
            _u32(descriptor, 12), rva_based=rva_based, image_base=image_base
        )
        int_pointer = _delay_pointer_to_rva(
            _u32(descriptor, 16), rva_based=rva_based, image_base=image_base
        )
        bound_iat = _delay_pointer_to_rva(
            _u32(descriptor, 20), rva_based=rva_based, image_base=image_base
        )
        unload_iat = _delay_pointer_to_rva(
            _u32(descriptor, 24), rva_based=rva_based, image_base=image_base
        )
        _require(name_pointer != 0, "delay_import_descriptor_name_missing")
        _require(iat_pointer != 0, "delay_import_descriptor_iat_missing")
        _require(int_pointer != 0, "delay_import_descriptor_int_missing")
        _validate_thunk(data, iat_pointer, thunk_width, sections, "delay_import_iat_rva_invalid")
        _validate_thunk(data, int_pointer, thunk_width, sections, "delay_import_int_rva_invalid")
        if module_handle:
            _validate_thunk(data, module_handle, 1, sections, "delay_import_module_handle_invalid")
        if bound_iat:
            _validate_thunk(data, bound_iat, thunk_width, sections, "delay_import_bound_iat_invalid")
        if unload_iat:
            _validate_thunk(data, unload_iat, thunk_width, sections, "delay_import_unload_iat_invalid")
        names.append(_read_c_string(data, name_pointer, sections, code_prefix="delay_import"))
    _require(terminated, "delay_import_directory_terminator_missing")
    return tuple(names)


def _parse_pe_bytes(data: bytes) -> _PEImage:
    _require(len(data) >= 64, "dos_header_truncated")
    _require(data[:2] == b"MZ", "dos_signature_invalid")
    pe_offset = _u32(data, 0x3C)
    _require(pe_offset >= 64 and pe_offset <= len(data) - 24, "pe_header_offset_invalid")
    _require(data[pe_offset : pe_offset + 4] == b"PE\x00\x00", "pe_signature_invalid")
    machine = _u16(data, pe_offset + 4)
    section_count = _u16(data, pe_offset + 6)
    optional_size = _u16(data, pe_offset + 20)
    _require(0 < section_count <= 96, "section_count_invalid")
    optional_offset = pe_offset + 24
    _require(
        optional_size >= 2 and optional_offset + optional_size <= len(data),
        "optional_header_truncated",
    )
    magic = _u16(data, optional_offset)
    if magic == 0x20B:
        minimum_optional_size = 112
        directory_start = 112
        directory_count_offset = 108
        image_base_offset = 24
        image_base_size = 8
        thunk_width = 8
    elif magic == 0x10B:
        minimum_optional_size = 96
        directory_start = 96
        directory_count_offset = 92
        image_base_offset = 28
        image_base_size = 4
        thunk_width = 4
    else:
        raise PEParseError("optional_header_magic_unsupported", f"magic=0x{magic:x}")
    _require(optional_size >= minimum_optional_size, "optional_header_truncated")
    _require(directory_count_offset + 4 <= optional_size, "data_directory_count_truncated")
    optional = data[optional_offset : optional_offset + optional_size]
    directory_count = _u32(optional, directory_count_offset)
    _require(directory_count <= 16, "data_directory_count_invalid", str(directory_count))
    _require(
        directory_start + min(directory_count, 16) * 8 <= optional_size,
        "data_directory_truncated",
    )
    if image_base_size == 8:
        image_base = _u64(optional, image_base_offset)
    else:
        image_base = _u32(optional, image_base_offset)

    sections_offset = optional_offset + optional_size
    section_table_size = section_count * 40
    _require(sections_offset + section_table_size <= len(data), "section_table_truncated")
    sections: list[_Section] = []
    for index in range(section_count):
        offset = sections_offset + index * 40
        section = _Section(
            virtual_address=_u32(data, offset + 12),
            virtual_size=_u32(data, offset + 8),
            raw_size=_u32(data, offset + 16),
            raw_pointer=_u32(data, offset + 20),
        )
        _require(
            section.raw_size == 0 or section.raw_pointer + section.raw_size <= len(data),
            "section_raw_data_truncated",
            f"index={index}",
        )
        _require(
            section.virtual_address + max(section.virtual_size, section.raw_size) <= 0x1_0000_0000,
            "section_rva_overflow",
            f"index={index}",
        )
        sections.append(section)

    import_directory = _directory(optional, directory_start, directory_count, 1)
    delay_import_directory = _directory(optional, directory_start, directory_count, 13)
    direct_imports = _parse_import_directory(data, import_directory, sections, thunk_width)
    delay_imports = _parse_delay_import_directory(
        data, delay_import_directory, sections, thunk_width, image_base
    )
    return _PEImage(
        machine=machine,
        magic=magic,
        image_base=image_base,
        direct_imports=direct_imports,
        delay_imports=delay_imports,
    )


def _read_pe(path: Path) -> tuple[int, list[str], list[str]]:
    """Read a PE and retain the historical tuple-shaped helper contract."""

    image = _parse_pe_bytes(path.read_bytes())
    return image.machine, list(image.direct_imports), list(image.delay_imports)


def _is_system(name: str) -> bool:
    return name in SYSTEM_DLLS or name.startswith("api-ms-") or name.startswith("ext-ms-")


def _is_third_party(name: str) -> bool:
    return any(marker in name for marker in THIRD_PARTY_MARKERS)


def _default_package_roots(root: Path) -> list[tuple[str, Path, str, bool]]:
    """Return only roots justified by the shipped runtime's inspected code.

    PyTorch's Windows loader adds ``torch/lib`` and the embedded Python
    ``Library/bin``/``bin`` directories.  NumPy's delvewheel patch adds
    ``numpy.libs``.  OpenCV's generated loader may add its ``x64/vc17/bin``
    directory.  We do not add arbitrary package-wide roots, the working
    directory, or a sibling distribution directory just because it exists.
    """

    candidates = (
        (
            "application_runtime_directory",
            root / "runtime" / "python",
            "embedded CPython executable directory",
            True,
        ),
        (
            "pytorch_runtime_dll_directory",
            root / "runtime" / "python" / "Lib" / "site-packages" / "torch" / "lib",
            "torch.__init__._load_dll_libraries uses os.add_dll_directory and preloads this root before torchvision extensions",
            True,
        ),
        (
            "pytorch_embedded_library_directory",
            root / "runtime" / "python" / "Library" / "bin",
            "torch.__init__ checks sys.exec_prefix/Library/bin",
            False,
        ),
        (
            "pytorch_embedded_bin_directory",
            root / "runtime" / "python" / "bin",
            "torch.__init__ checks sys.exec_prefix/bin",
            False,
        ),
        (
            "numpy_runtime_dll_directory",
            root / "runtime" / "python" / "Lib" / "site-packages" / "numpy.libs",
            "NumPy delvewheel patch uses os.add_dll_directory",
            False,
        ),
        (
            "scipy_runtime_dll_directory",
            root / "runtime" / "python" / "Lib" / "site-packages" / "scipy.libs",
            "SciPy wheel runtime DLL directory",
            False,
        ),
        (
            "opencv_runtime_dll_directory",
            root
            / "runtime"
            / "python"
            / "Lib"
            / "site-packages"
            / "cv2"
            / "x64"
            / "vc17"
            / "bin",
            "OpenCV generated loader BINARIES_PATHS",
            False,
        ),
    )
    return [
        (kind, path, reason, preloaded)
        for kind, path, reason, preloaded in candidates
        if path.is_dir()
    ]


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _search_roots(root: Path, dll_search_roots: Iterable[Path] | None) -> list[dict[str, Any]]:
    roots = [
        {
            "kind": "importer_directory",
            "path": ".",
            "reason": "Windows loader importer directory",
            "preload_before_python_extensions": False,
        }
    ]
    for kind, path, reason, preloaded in _default_package_roots(root):
        relative = _relative(root, path)
        if not any(item["path"] == relative for item in roots):
            roots.append(
                {
                    "kind": kind,
                    "path": relative,
                    "reason": reason,
                    "preload_before_python_extensions": preloaded,
                }
            )
    for path in dll_search_roots or ():
        resolved = path.resolve()
        try:
            relative = _relative(root, resolved)
        except ValueError as exc:
            raise ValueError(f"dll_search_root_outside_package:{path}") from exc
        if not resolved.is_dir():
            raise ValueError(f"dll_search_root_missing:{relative}")
        if not any(item["path"] == relative for item in roots):
            roots.append(
                {
                    "kind": "explicit_package_dll_search_root",
                    "path": relative,
                    "reason": "caller-supplied and independently justified",
                    "preload_before_python_extensions": False,
                }
            )
    return roots


def scan_native_closure(
    root: Path, *, dll_search_roots: Iterable[Path] | None = None
) -> dict[str, Any]:
    """Scan package-local PE files and classify every imported DLL.

    ``dll_search_roots`` is intentionally opt-in for qualification callers
    that have separately audited a runtime DLL-directory mechanism.  A path in
    an unrelated package directory is never considered merely because its
    filename matches an import.
    """

    root = root.resolve()
    binaries = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".dll", ".exe", ".pyd"}
    )
    roots = _search_roots(root, dll_search_roots)
    parsed: dict[Path, _PEImage] = {}
    file_records: list[dict[str, Any]] = []
    parse_failures: list[dict[str, Any]] = []
    architectures: dict[str, int | None] = {}
    imports: list[dict[str, Any]] = []
    no_imports: list[str] = []

    for path in binaries:
        relative = path.relative_to(root).as_posix()
        try:
            image = _parse_pe_bytes(path.read_bytes())
        except OSError as exc:
            failure = {
                "path": relative,
                "status": "PARSE_FAILED",
                "error_code": "file_unreadable",
                "detail": type(exc).__name__,
            }
            parse_failures.append(failure)
            file_records.append(failure)
            architectures[relative] = None
            continue
        except PEParseError as exc:
            failure = {
                "path": relative,
                "status": "PARSE_FAILED",
                "error_code": exc.code,
                "detail": exc.detail or None,
            }
            parse_failures.append(failure)
            file_records.append(failure)
            architectures[relative] = None
            continue
        parsed[path] = image
        architectures[relative] = image.machine
        direct = list(image.direct_imports)
        delayed = list(image.delay_imports)
        if not direct and not delayed:
            no_imports.append(relative)
        file_records.append(
            {
                "path": relative,
                "status": "PARSED",
                "machine": image.machine,
                "architecture": MACHINE_NAMES.get(image.machine, "unknown"),
                "imports": direct,
                "delay_imports": delayed,
                "parse_failure": None,
            }
        )

    package_candidates: dict[str, list[Path]] = {}
    for path in binaries:
        package_candidates.setdefault(path.name.casefold(), []).append(path)

    def reachable_candidates(importer: Path, dependency: str) -> list[tuple[Path, str, bool]]:
        directories: list[tuple[Path, str, bool]] = [(importer.parent, "importer_directory", False)]
        for root_record in roots[1:]:
            directories.append(
                (
                    root / Path(root_record["path"]),
                    root_record["kind"],
                    bool(root_record["preload_before_python_extensions"]),
                )
            )
        matches: list[tuple[Path, str, bool]] = []
        seen: set[Path] = set()
        for directory, kind, preloaded in directories:
            for candidate in package_candidates.get(dependency, []):
                if candidate.parent == directory.resolve() and candidate not in seen:
                    seen.add(candidate)
                    matches.append((candidate, kind, preloaded))
        return matches

    for importer, image in parsed.items():
        relative_importer = importer.relative_to(root).as_posix()
        dependencies = [(name, False) for name in image.direct_imports] + [
            (name, True) for name in image.delay_imports
        ]
        for dependency, delay in dependencies:
            if _is_system(dependency):
                category = "A"
                state = "system"
                resolved = None
                candidates: list[str] = []
                resolution_kind = "windows_system_directory"
            elif dependency in HOST_OPTIONAL_DLLS:
                category = "A"
                state = "host_optional_driver"
                resolved = None
                candidates = []
                resolution_kind = "optional_nvidia_host_driver_boundary"
            else:
                matches = reachable_candidates(importer, dependency)
                candidates = [_relative(root, candidate) for candidate, _, _ in matches]
                preloaded = [match for match in matches if match[2]]
                if importer.suffix.casefold() == ".pyd" and len(preloaded) == 1:
                    resolved = preloaded[0][0]
                    resolution_kind = "preloaded_" + preloaded[0][1]
                    category = "C" if dependency in MSVC_DLLS else (
                        "D" if _is_third_party(dependency) else "B"
                    )
                    state = "package_local"
                elif len(matches) == 1:
                    resolved = matches[0][0]
                    resolution_kind = matches[0][1]
                    category = "C" if dependency in MSVC_DLLS else (
                        "D" if _is_third_party(dependency) else "B"
                    )
                    state = "package_local"
                elif not matches:
                    resolved = None
                    resolution_kind = None
                    category = "E"
                    state = "missing"
                else:
                    resolved = None
                    resolution_kind = None
                    category = "E"
                    state = "ambiguous"
            imports.append(
                {
                    "importer": relative_importer,
                    "importer_architecture": MACHINE_NAMES.get(image.machine, "unknown"),
                    "importer_parent_directory": Path(relative_importer).parent.as_posix(),
                    "dependency": dependency,
                    "category": category,
                    "state": state,
                    "delay_load": delay,
                    "resolved_path": _relative(root, resolved) if resolved else None,
                    "candidate_paths": candidates,
                    "resolution_kind": resolution_kind,
                }
            )

    category_counts = {
        category: sum(item["category"] == category for item in imports) for category in "ABCDEF"
    }
    unresolved = [item for item in imports if item["state"] in {"missing", "ambiguous"}]
    non_x64 = [
        path for path, machine in architectures.items() if machine is not None and machine != 0x8664
    ]
    unknown_architecture = [
        path
        for path, machine in architectures.items()
        if machine is None or machine == 0 or machine not in MACHINE_NAMES
    ]
    acceptable_architecture = not parse_failures and not non_x64 and not unknown_architecture
    return {
        "schema_version": "native-closure-v2",
        "root": ".",
        "files_scanned": len(binaries),
        "files": file_records,
        "architectures": architectures,
        "imports": imports,
        "category_counts": category_counts,
        "unresolved": unresolved,
        "parse_failures": parse_failures,
        "non_x64": non_x64,
        "unknown_architecture": unknown_architecture,
        "architecture_acceptable": acceptable_architecture,
        "no_imports": no_imports,
        "reachability_model": {
            "schema_version": "windows-dll-reachability-v1",
            "rules": roots,
            "global_filename_matching": False,
            "ambiguous_same_name_policy": "BLOCKED",
        },
        "status": "PASS"
        if acceptable_architecture and not parse_failures and not unresolved
        else "BLOCKED",
    }
