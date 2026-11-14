"""Read the PS2 boot entry directly from a 2048-byte-sector ISO 9660 image."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SECTOR = 2048
BOOT = re.compile(r"^[ \t]*BOOT2[ \t]*=[ \t]*([^\r\n]+)", re.I | re.M)
SERIAL = re.compile(r"([A-Z]{4})[_.-]?(\d{3})[_.-]?(\d{2})", re.I)


class DiscError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    name: bytes
    offset: int
    size: int
    flags: int

    @property
    def directory(self) -> bool:
        return bool(self.flags & 2)


def dual_u32(data: bytes) -> int:
    little = int.from_bytes(data[:4], "little")
    if little != int.from_bytes(data[4:8], "big"):
        raise DiscError("An ISO directory record has inconsistent byte order fields.")
    return little


class ISOImage:
    def __init__(self, path: Path):
        if path.suffix.lower() != ".iso" or not path.is_file():
            raise DiscError("Choose a readable PS2 .iso file.")
        self.path = path
        self.size = path.stat().st_size

    def __enter__(self) -> ISOImage:
        self.stream = self.path.open("rb")
        try:
            self.root = self.read_root()
            return self
        except BaseException:
            self.stream.close()
            raise

    def __exit__(self, *exc):
        self.stream.close()

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > self.size:
            raise DiscError("The ISO is truncated or contains an invalid file extent.")
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise DiscError("The ISO changed or ended while reading it.")
        return data

    def entry(self, record: bytes) -> Entry:
        if len(record) < 34 or record[0] != len(record) or 33 + record[32] > len(record):
            raise DiscError("The ISO contains a malformed directory entry.")
        offset = (dual_u32(record[2:10]) + record[1]) * SECTOR
        size = dual_u32(record[10:18])
        if offset + size > self.size:
            raise DiscError("An ISO file extends past the end of the disc image.")
        return Entry(record[33:33 + record[32]], offset, size, record[25])

    def read_root(self) -> Entry:
        for sector in range(16, 64):
            if (sector + 1) * SECTOR > self.size:
                break
            descriptor = self.read(sector * SECTOR, SECTOR)
            if descriptor[1:7] != b"CD001\x01":
                break
            if descriptor[0] == 1:
                root = self.entry(descriptor[156:156 + descriptor[156]])
                if root.directory and root.size:
                    return root
                break
            if descriptor[0] == 255:
                break
        raise DiscError("No primary ISO 9660 volume was found. Select an uncompressed 2048-byte-sector PS2 ISO.")

    def entries(self, directory: Entry):
        if not directory.directory or not 0 < directory.size <= 32 * 1024 * 1024:
            raise DiscError("The ISO directory is missing or exceeds the supported size.")
        for offset in range(0, directory.size, SECTOR):
            block = self.read(directory.offset + offset, min(SECTOR, directory.size - offset))
            start = 0
            while start < len(block) and block[start]:
                end = start + block[start]
                entry = self.entry(block[start:end])
                if entry.name not in (b"\x00", b"\x01"):
                    yield entry
                start = end

    def find(self, parts: list[str]) -> Entry:
        current = self.root
        for name in parts:
            wanted = name.encode("ascii").upper()
            candidates = [
                entry for entry in self.entries(current)
                if entry.name.upper().split(b";", 1)[0] == wanted
            ]
            if len(candidates) != 1 or candidates[0].flags & 0x80:
                raise DiscError(f"The ISO entry {name} is missing, ambiguous, or split across extents.")
            current = candidates[0]
        return current

    def boot(self) -> tuple[str, Entry]:
        config = self.find(["SYSTEM.CNF"])
        if config.directory or not 0 < config.size <= 64 * 1024:
            raise DiscError("The ISO has an invalid SYSTEM.CNF.")
        text = self.read(config.offset, config.size).split(b"\0", 1)[0].decode("utf-8-sig", "replace")
        matches = BOOT.findall(text)
        if len(matches) != 1:
            raise DiscError("SYSTEM.CNF must contain one PS2 BOOT2 entry.")
        path = matches[0].strip()
        device, colon, name = path.partition(":")
        if colon and device.upper() != "CDROM0":
            raise DiscError("SYSTEM.CNF: BOOT2 must name a file on cdrom0.")
        parts = (name if colon else path).replace("\\", "/").strip("/").split("/")
        versioned = re.fullmatch(r"([^;]+)(?:;[0-9]+)?", parts[-1])
        if not versioned:
            raise DiscError("SYSTEM.CNF names an invalid boot file.")
        parts[-1] = versioned[1]
        if any(not p or p in (".", "..") or not p.isascii() for p in parts):
            raise DiscError("SYSTEM.CNF names an invalid boot path.")
        serial = SERIAL.fullmatch(parts[-1])
        entry = self.find(parts)
        if entry.directory or not 52 <= entry.size <= 128 * 1024 * 1024:
            raise DiscError("The PS2 boot executable is missing, too large or truncated.")
        head = self.read(entry.offset, 20)
        if head[:6] != b"\x7fELF\x01\x01" or head[18:20] != b"\x08\x00":
            raise DiscError("The boot file is not a 32-bit little-endian MIPS ELF.")
        if serial:
            return f"{serial[1].upper()}-{serial[2]}{serial[3]}", entry
        return "PS2-" + self.fingerprint(entry, "sha256")[:12].upper(), entry

    def fingerprint(self, entry: Entry, algorithm: str = "sha1") -> str:
        digest = hashlib.new(algorithm)
        for offset in range(0, entry.size, 1024 * 1024):
            digest.update(self.read(entry.offset + offset, min(1024 * 1024, entry.size - offset)))
        return digest.hexdigest()

    def extract_boot(self, entry: Entry, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as output:
            for offset in range(0, entry.size, 1024 * 1024):
                output.write(self.read(entry.offset + offset, min(1024 * 1024, entry.size - offset)))
