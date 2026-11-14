"""Recover audited EE entry points and VU code ranges from the original disc."""
from __future__ import annotations

import binascii
import hashlib
import json
from pathlib import Path

from builder import BuildError
from disc import ISOImage

MAPS = Path(__file__).with_name("maps")


def seed_vu(map_name: str, boot: bytes, stage: Path) -> int:
    # The caller has checked the boot ELF hash. The VU compiler also checks
    # the reachable instruction hash of each reconstructed program.
    programs = json.loads((MAPS / map_name).read_text(encoding="ascii"))
    count = 0
    for unit, size in ((0, 4096), (1, 16384)):
        for key, entry, ranges in programs[str(unit)]:
            image = bytearray(size)
            for address, offset, length in ranges:
                if not (0 <= address < address + length <= size and 0 <= offset < offset + length <= len(boot)):
                    raise BuildError(f"A VU program in {map_name} points outside the audited boot executable.")
                image[address:address + length] = boot[offset:offset + length]
            (stage / f"vu{unit}" / f"{key}_{entry:04X}.bin").write_bytes(image)
            count += 1
    return count


def seed_naruto_ee(iso: Path, overlays: Path) -> set[str]:
    overlay = json.loads((MAPS / "naruto-pal-ee.json").read_text(encoding="ascii"))
    entries = {f"{pc:x}" for pc in overlay["boot"]}
    with ISOImage(iso) as disc:
        for name, record in overlay["overlays"].items():
            item = disc.find(["PRG", name + ".BIN"])
            if item.directory or item.size != record["size"]:
                raise BuildError(f"Unexpected {name}.BIN size for the audited Naruto disc.")
            image = disc.read(item.offset, item.size)
            if hashlib.sha256(image).hexdigest() != record["sha256"]:
                raise BuildError(f"{name}.BIN does not match the audited Naruto disc.")
            entries.update(seed_ee_windows(image, record, overlays, name + ".BIN"))
    return entries


def seed_ee_windows(image: bytes, record: dict, overlays: Path, name: str) -> set[str]:
    entries = set()
    for base, offsets in record["windows"]:
        offset = base - record["load"]
        start = max(0, offset)
        end = min(len(image), offset + (64 << 10))
        if not start < end or any(not start <= offset + entry <= end - 4 for entry in offsets):
            raise BuildError(f"An EE entry points outside {name}.")
        data = bytes(start - offset) + image[start:end]
        stem = f"{base:08X}_{binascii.crc32(data):08X}"
        (overlays / f"{stem}.bin").write_bytes(data)
        entries.update(f"{stem}:{entry:x}" for entry in offsets)
    return entries


def seed_disc_ee(map_name: str, iso: Path, overlays: Path) -> set[str]:
    mapping = json.loads((MAPS / map_name).read_text(encoding="ascii"))
    entries = {f"{pc:x}" for pc in mapping["boot"]}
    with ISOImage(iso) as disc:
        for record in mapping["regions"]:
            if parts := record.get("file"):
                name = "/".join(parts)
                item = disc.find(parts)
                if item.directory or item.size != record["size"]:
                    raise BuildError(f"Unexpected {name} size in {map_name}.")
                offset = item.offset
            else:
                offset = record["offset"]
                name = f"the disc range at 0x{offset:X}"
            image = disc.read(offset, record["size"])
            if hashlib.sha256(image).hexdigest() != record["sha256"]:
                raise BuildError(f"The selected ISO does not match {name} in {map_name}.")
            entries.update(seed_ee_windows(image, record, overlays, name))
    return entries
