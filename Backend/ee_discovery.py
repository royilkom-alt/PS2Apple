"""Convert a desktop boot miss into an entry for the offline EE compiler."""
from __future__ import annotations

import binascii
import re
import struct
from pathlib import Path

from builder import BuildError

RAM_BYTES = 32 << 20
SNAPSHOT_BYTES = 64 << 10
ENTRY = re.compile(r"(?:(?P<module>[a-zA-Z0-9_]+):)?(?P<address>[a-fA-F0-9]{1,8})")
WINDOW = re.compile(r"(?P<base>[A-Fa-f0-9]{8})_[A-Fa-f0-9]{8}")


def code_segments(elf: bytes) -> list[tuple[int, int, int]]:
    """Return the address, file offset and initialized size of ELF code segments."""
    if len(elf) < 52 or elf[:6] != b"\x7fELF\x01\x01":
        raise BuildError("The PS2 boot executable is not a little-endian ELF32 file.")
    headers, = struct.unpack_from("<I", elf, 28)
    size, count = struct.unpack_from("<HH", elf, 42)
    if size < 32 or headers + size * count > len(elf):
        raise BuildError("The PS2 ELF program headers are incomplete.")
    segments = []
    for index in range(count):
        kind, offset, address, _, files, memory, flags, _ = struct.unpack_from("<8I", elf, headers + index * size)
        if kind != 1 or not files:
            continue
        if offset + files > len(elf) or files > memory:
            raise BuildError("A PS2 ELF segment exceeds its file or memory size.")
        if flags & 1:
            segments.append((address, offset, files))
    return segments


def recover_entry(token: str, snapshot: Path, elf: bytes, segments: list[tuple[int, int, int]], overlays: Path,
                  *, preserve_window: bool = False) -> str:
    match = ENTRY.fullmatch(token)
    if not match:
        raise BuildError("The boot probe reported a malformed EE entry point.")
    relative = int(match["address"], 16)
    module = match["module"]
    window = WINDOW.fullmatch(module) if module else None
    if module and not window:
        return f"{module}:{relative:x}"
    pc = int(window["base"], 16) + relative if window else relative
    if not snapshot.is_file() or not 8 < snapshot.stat().st_size <= SNAPSHOT_BYTES + 8:
        raise BuildError(f"The guest jumped to 0x{pc:08X} without a usable EE code capture.")
    record = snapshot.read_bytes()
    recorded_pc, base = struct.unpack_from("<II", record)
    image = record[8:]
    offset = pc - base
    if (recorded_pc != pc or base < 0x1000 or base + len(image) > RAM_BYTES or
            (base | pc | len(image)) & 3 or not 0 <= offset <= len(image) - 4):
        raise BuildError(f"The EE capture does not contain the reported address 0x{pc:08X}.")
    nearby = image[offset:offset + 32]
    if window and not preserve_window:
        original = overlays / f"{module}.bin"
        if not original.is_file():
            raise BuildError(f"The EE overlay {module} is missing from the build workspace.")
        with original.open("rb") as stream:
            stream.seek(relative)
            if stream.read(len(nearby)) == nearby:
                return f"{module}:{relative:x}"
        print(f"EE code at 0x{pc:08X} changed after capture. Translating the current RAM image.", flush=True)
    elif not preserve_window:
        for address, file_offset, length in segments:
            if address <= pc < address + length:
                end = min(len(nearby), address + length - pc)
                start = file_offset + pc - address
                if nearby[:end] == elf[start:start + end]:
                    return f"{pc:x}"
                break
    if not any(nearby):
        raise BuildError(f"The guest jumped into empty EE memory at 0x{pc:08X}.")
    name = f"{base:08X}_{binascii.crc32(image):08X}"
    path = overlays / f"{name}.bin"
    if path.exists():
        if path.read_bytes() != image:
            raise BuildError(f"Two EE captures produced the same image ID at 0x{base:08X}.")
    else:
        path.write_bytes(image)
        print(f"Captured EE code at 0x{pc:08X} · RAM image {name} · {len(image):,} bytes", flush=True)
    return f"{name}:{offset:x}"
