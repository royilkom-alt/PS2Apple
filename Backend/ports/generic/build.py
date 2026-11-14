#!/usr/bin/env python3
"""Translate the selected disc and link the shared ahead-of-time iPhoneOS runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
sys.path.insert(0, str(ROOT))
from aot_deps import WGPU_VERSION, engine_source, prepare_wgpu  # noqa: E402
from aot_maps import seed_disc_ee, seed_naruto_ee, seed_vu  # noqa: E402
from builder import BuildError, MIN_IOS, TOOLS, cancelled, catalog_disc, event, output, progress, run  # noqa: E402
from disc import DiscError  # noqa: E402
from ee_discovery import code_segments, recover_entry  # noqa: E402
from engine_source import ENGINE_REVISION, ENGINE_UPSTREAM, ENGINE_VERSION, RELEASE_VERSION  # noqa: E402
from ios_deps import IOS_GAMEPAD_INFO, SDL_REV, prepare_sdl, sdl_link_flags, xcode_cmake  # noqa: E402
from ipa import check_macho  # noqa: E402

JOBS = max(1, min(4, os.cpu_count() or 1))
BOOT_CHECKPOINTS = (180, 360, 600, 900, 1200)
AUDITED_BOOT_CHECKPOINTS = (180, 600, 1200, 1800, 2400, 3600)
DISCOVERY_ROUNDS = 12
JAK_USA = ("SCUS-97124", "b3051491a4f41bd90b345bbd91fe60b4c85f3060bacd0552903645f8d27268d5")
NARUTO_PAL = ("SLES-55605", "20a43677397731a2a20899336d1165ace5b436906b9b89be90fb10f4558dd19d")
NARUTO4_USA = ("SLUS-21862", "fefb3b546e41e80e829faee107a73f7599167a1446f781207ae69310e5105407")
KINGDOM_USA = ("SLUS-20370", "138632a95376fc72021da42bb819480be0586aff142d011fa051b3320e7fcf3d")
NFSU2_USA = ("SLUS-21065", "a666f3e8869e63ad2f5eff90fc30c4953fcce41166ff006aaf1ed327d180d8ff")
RE4_USA = ("SLUS-21134", "465d144f526603d0dcc35ad1fdb4e82cc3a1481c22289f321482cc910fb9f1ad")
TOMB_USA = ("SLUS-21858", "79191191fb8017456e5d53dcb437cd71d21c7569331cc17bccda1ef1d4f79834")
BLACK_USA = ("SLUS-21376", "141b05209570458af9c8e27ab1911f258da6353909d813cd572df026254ffe4c")
BURNOUT3_USA = ("SLUS-21050", "e118dba952bf9fde6a04ee284bb1f06fa0bb49c9e04bbe762af1039dcc443c66")
FFX_USA = ("SLUS-20312", "0fb25540f918e2ea0351a969b8a6267eb10ed5055b7057d30f3c845db862eb68")
GH2_USA = ("SLUS-21447", "30152bfddac4d73a84ef1debc35fc89409b6cd485deb15f2bbf33d64a7871678")
GH80_USA = ("SLUS-21586", "03cbbf4bc8163f5ce3279872c0ab6459553506f79693efa96b2c482d56f9408f")
SHAOLIN_USA = ("SLUS-21087", "b1ed99d81b07969553ed2f9afaad4caed1bbeb637d53a369f4dafca8eedd2ab2")
EE_RECORDING_DISCS = (JAK_USA,)
EE_WINDOW_DISCS = (*EE_RECORDING_DISCS, NARUTO_PAL, NARUTO4_USA, KINGDOM_USA, NFSU2_USA)
IOP_CLOSE_DISCS = (NARUTO_PAL, NARUTO4_USA)
MFIFO_DISCS = (GH2_USA, GH80_USA)
EE_MAPS = {NARUTO4_USA: "naruto4-usa-ee.json", KINGDOM_USA: "kingdom-usa-ee.json",
           BURNOUT3_USA: "burnout3-usa-ee.json", GH80_USA: "guitar80-usa-ee.json"}
VU1_CAPTURE_DISCS = (RE4_USA, TOMB_USA, NARUTO_PAL, NARUTO4_USA, KINGDOM_USA, BLACK_USA,
                     BURNOUT3_USA, FFX_USA, *MFIFO_DISCS, SHAOLIN_USA)
VU_MAPS = {BLACK_USA: "black-usa-vu.json", SHAOLIN_USA: "shaolin-usa-vu.json",
           NARUTO4_USA: "naruto4-usa-vu.json", KINGDOM_USA: "kingdom-usa-vu.json",
           BURNOUT3_USA: "burnout3-usa-vu.json", FFX_USA: "ffx-usa-vu.json",
           GH2_USA: "guitar2-usa-vu.json", GH80_USA: "guitar80-usa-vu.json"}
TOMB_MISSING_RPC = 0x00534E03
VU1_CAPTURE_LIMIT = 256
BOOT_PRESSES = ("60:start", "110:cross", "300:start", "480:cross", "720:start", "960:cross")
EXTENDED_PRESSES = ("1440:start", "1680:cross", "2160:cross", "2460:start", "3000:cross")
# v1.4.2 captured this entry from the original Most Wanted USA v2.00 boot ELF.
KNOWN_BOOT_ENTRIES = {
    ("SLUS-21267", "aa58f301c7215ab2885ab93dff68de9cb66371a139977e53d0c1b181a61bad14"): ("532430",),
    GH2_USA: ("3d0d20", "3d5420"),
}


@dataclass(frozen=True)
class Dependencies:
    source: Path
    host_tools: Path
    sdk: str
    sdl: Path
    wgpu: Path
    host_compiler: str
    desktop_wgpu: Path | None = None

    def host_tool(self, name: str) -> Path:
        return self.host_tools / name

    def probe_gameplay(self, gameplay: GameplayProbe | None) -> GameplayProbe | None:
        return gameplay

    def make_probe(self, directory: Path, generated: Path, gameplay: GameplayProbe | None, *,
                   live_ee: bool = False) -> Path:
        if not (directory / "CMakeCache.txt").exists():
            flags = host_flags(self.host_compiler, live_ee=live_ee)
            if gameplay and gameplay.metal:
                if self.desktop_wgpu is None:
                    raise BuildError("This disc needs the desktop Metal runtime for the gameplay probe.")
                flags.append(f"-DPS2_WGPU_DIR={self.desktop_wgpu}")
            run([TOOLS["cmake"], "-S", self.source, "-B", directory, *flags,
                 f"-DPS2_GENERATED={generated}"])
        run([TOOLS["cmake"], "--build", directory, "--target", "ps2run", "--parallel", str(JOBS)])
        return directory / "ps2run"


@dataclass(frozen=True)
class BootProbe:
    complete: bool
    detail: str
    moving_video: bool = False
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class GameplayProbe:
    phases: tuple[tuple[int, ...], ...]
    presses: tuple[str, ...]
    axes: tuple[str, ...] = ()
    close_returns_descriptor: bool = False
    metal: bool = False
    wait_for_mfifo_data: bool = False
    boot_presses: tuple[str, ...] = BOOT_PRESSES


GENERAL_DISC_PROBE = GameplayProbe(
    ((180, 600, 1200, 1800, 2600, 3600, 4200),),
    (*EXTENDED_PRESSES, "3400:cross:12", "3900:cross:12"),
)


GAMEPLAY_PROBES = {
    RE4_USA: GameplayProbe(
        ((180, 900, 1800, 2200, 2500, 2850, 3250), (1800, 2200, 2500, 2850, 3250, 4000, 5200)),
        ("1440:start", "1680:cross", "2160:cross", "2420:select:20", "2750:select:20",
         "3120:select:20", "3680:up:200"),
    ),
    TOMB_USA: GameplayProbe(
        ((180, 900, 1800, 3600, 4200), (180, 900, 1800, 3600, 4200, 5400, 6000)),
        (*EXTENDED_PRESSES, "4200:start", "4500:cross", "5400:start", "5800:cross"),
    ),
    NFSU2_USA: GameplayProbe(
        ((180, 600, 1200, 1800, 2400, 3000),),
        ("1320:start", "1500:cross", "1680:cross", "1900:cross", "2160:cross", "2460:cross", "2900:start"),
    ),
    NARUTO_PAL: GameplayProbe(
        ((180, 1440, 1860, 2290, 3450, 4180, 4500, 5300),),
        ("1260:start", "1320:start:10", "1360:start:10", "1400:start:10",
         "1500:up:30", "1580:cross:20", "1910:down:14", "2030:cross:10",
         "2210:cross:12", "2340:cross:10", "2470:cross:10", "2720:cross:10",
         "2910:cross:12", "3110:cross:12", "3290:cross:12", "3450:cross:12",
         "3650:cross:12", "3860:cross:12", "4100:cross:12", "4340:circle:12",
         "4390:circle:12", "4460:circle:12", "4700:circle:12", "4940:circle:12"),
        ("4460:lx:255:360",), close_returns_descriptor=True,
    ),
    NARUTO4_USA: GameplayProbe(
        ((900, 1560, 1760, 2070, 2470, 3200, 3720, 4880, 5500),),
        ("1150:start:15", "1260:cross:15", "1610:down:15", "1710:down:15", "1860:cross:15",
         "2080:cross:15", "2340:cross:15", "2600:cross:15", "2840:cross:15", "3140:cross:15",
         "3460:cross:15", "3740:circle:15", "4020:circle:15", "4300:circle:15",
         "4700:circle:15", "5100:circle:15"),
        ("4020:lx:255:290", "4700:lx:0:240"), close_returns_descriptor=True, metal=True,
    ),
    KINGDOM_USA: GameplayProbe(
        ((1450, 1800, 2300, 4200, 7500, 12400, 13800, 15300, 15750, 16500),),
        ("1320:start:15", "1500:cross:15", "1680:cross:15", "1820:cross:15", "1980:cross:15",
         "2160:cross:15", "2350:cross:15", "2600:cross:15", "2950:cross:15", "3300:cross:15",
         "9500:cross:15", "10050:cross:15", "10450:cross:15", "10950:cross:15", "11550:cross:15",
         "11950:cross:15", "12300:cross:15", "12900:cross:15", "13500:cross:15", "14200:cross:15",
         "14620:cross:15", "14900:cross:15", "15200:cross:15", "15630:cross:15", "16100:cross:15"),
        ("6500:ly:0:160", "11400:lx:0:210", "12200:ly:0:310", "13200:ly:255:330",
         "13700:lx:255:280", "15000:ly:0:380", "15650:lx:0:360", "16200:ly:255:260"), metal=True,
    ),
    FFX_USA: GameplayProbe(
        ((900, 2100, 3000, 4800, 7200, 9450, 10800, 12800, 16000),),
        ("1700:start:12", "2300:up:12", "2500:cross:12", "5200:cross:12", "6100:cross:12",
         "7100:cross:12", "8100:cross:12", "9100:cross:12", "10200:cross:12", "11200:cross:12",
         "11900:cross:12", "12500:cross:12", "13100:cross:12", "14300:cross:12", "15300:cross:12"),
        ("8100:ly:0:440", "10050:lx:0:440", "11450:ly:0:440", "13050:ly:0:480", "14500:ly:0:600"),
        metal=True, boot_presses=("60:start", "110:cross", "480:start", "600:cross", "1200:cross"),
    ),
    GH2_USA: GameplayProbe(
        ((600, 1450, 2500, 3550, 4750, 5400, 6400, 7200),),
        ("1170:up:12", "1260:cross:12", "1430:up:12", "1500:cross:12", "1690:cross:12",
         "1900:start:12", "2160:cross:12", "2460:cross:12", "2800:cross:12", "3200:cross:12",
         "3550:cross:12", "4100:cross:12", "4700:cross:12", "5400:cross:12", "6200:cross:12",
         "7100:cross:12"),
        metal=True, wait_for_mfifo_data=True,
    ),
    GH80_USA: GameplayProbe(
        ((600, 1520, 2420, 3100, 3550, 4800, 5400, 6100, 6900),),
        ("1170:up:12", "1260:cross:12", "1430:up:12", "1500:cross:12", "1690:cross:12",
         "1900:start:12", "2160:cross:12", "2460:cross:12", "2800:cross:12", "3200:cross:12",
         "3550:cross:12", "4100:cross:12", "4700:cross:12", "5400:cross:12", "6200:cross:12"),
        metal=True, wait_for_mfifo_data=True,
    ),
    SHAOLIN_USA: GameplayProbe(
        ((180, 1750, 3300, 4200, 5750, 7000, 8000, 9500, 10000),),
        ("1320:start:15", "1500:cross:20", "1660:cross:20", "1850:cross:20",
         "2040:cross:20", "2240:cross:20", "2460:cross:20", "2700:cross:20",
         "2870:start:12", "3190:cross:20", "3410:cross:20", "3690:start:14",
         "4120:cross:20", "4300:cross:20", "4590:cross:20", "5050:start:15",
         "5640:start:20", "6210:start:20", "6900:start:15", "7410:cross:20",
         "8090:cross:20", "8520:cross:20", "9100:cross:20", "9520:square:50"),
        ("9500:ly:0:440",), metal=True,
    ),
}


def digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def host_flags(compiler: str, *, live_ee: bool = False) -> list[str]:
    return ["-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={TOOLS['ninja']}", "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_CXX_COMPILER={compiler}", f"-DPS2_AOT_ONLY={'OFF' if live_ee else 'ON'}", "-DPS2APPLE_AOT_PROBE=ON",
            "-DCMAKE_DISABLE_FIND_PACKAGE_SDL2=ON"]


def prepare(cache: Path, *, desktop_metal: bool = False) -> Dependencies:
    cache.mkdir(parents=True, exist_ok=True)
    source = engine_source()
    sdk = output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
    compiler = output(["/usr/bin/xcrun", "--sdk", "macosx", "--find", "clang++"])
    xcode = output(["/usr/bin/xcodebuild", "-version"])
    key = hashlib.sha256(f"{sdk}|{xcode}|{MIN_IOS}".encode()).hexdigest()[:12]
    tools = cache / "Toolchains"
    tools.mkdir(exist_ok=True)
    compiler_revision = "|".join(digest(source / path) for path in (
        "recomp/main.cpp", "recomp/vurecomp_gen.cpp", "runtime/include/ps2/vu_ops.hpp",
    ))
    host_key = hashlib.sha256(f"{source}|{ENGINE_VERSION}|{key}|{compiler_revision}".encode()).hexdigest()[:12]
    host = cache / f"PS2Apple-Compiler-{host_key}"
    print(f"Using PS2Apple's bundled EE and VU compiler {ENGINE_VERSION}.", flush=True)
    if not all((host / name).is_file() for name in ("eerecomp", "vurecomp")):
        run([TOOLS["cmake"], "-S", source, "-B", host, *host_flags(compiler)])
        run([TOOLS["cmake"], "--build", host, "--target", "eerecomp", "vurecomp", "--parallel", str(JOBS)])
    wgpu = prepare_wgpu(tools)
    # The same SDL build can be reused if Shaolin Monks has already prepared it.
    existing_sdl = cache.parent.parent / "shaolin-monks" / "9c014184b54f8f4235db9327bf6a5d77566475c5" / "Toolchains"
    existing_sdl = existing_sdl / f"SDL3-3.4.18-Metal-{key}"
    if all((existing_sdl / name).is_file() for name in ("lib/libSDL3.a", "lib/pkgconfig/sdl3.pc", "include/SDL3/SDL.h")):
        sdl = existing_sdl
    else:
        sdl = prepare_sdl(tools, sdk, key, JOBS)
    desktop_wgpu = prepare_wgpu(tools, mac=True) if desktop_metal else None
    return Dependencies(source, host, sdk, sdl, wgpu, compiler, desktop_wgpu)


def translate(deps: Dependencies, elf: Path, generated: Path, entries: Path, modules: Path, overlays: Path,
              *, focused_overlays: bool = False):
    with tempfile.TemporaryDirectory(prefix="translate-", dir=generated.parent) as temporary:
        stage = Path(temporary)
        command = [deps.host_tool("eerecomp"), elf, "--entries", entries, "--modules", modules,
                   "--overlays", overlays, "--out", stage]
        if focused_overlays:
            command.append("--focused-overlays")
        run(command)
        if not (stage / "table.cpp").is_file() or not any(stage.glob("unit_*.cpp")):
            raise BuildError("The static translator did not produce a boot function table.")
        for path in generated.iterdir():
            if not path.name.startswith("vu1_") and not (stage / path.name).exists():
                path.unlink()
        for path in stage.iterdir():
            previous = generated / path.name
            if not previous.is_file() or previous.read_bytes() != path.read_bytes():
                path.replace(previous)


def report_boot_pictures(directory: Path, checkpoints: tuple[int, ...]) -> BootProbe:
    pictures = 0
    nonblack = set()
    for frame in checkpoints:
        image = directory / f"frame_{frame:05d}.ppm"
        if not image.is_file():
            continue
        parts = image.read_bytes().split(b"\n", 3)
        if len(parts) != 4 or parts[0] != b"P6" or parts[2] != b"255":
            continue
        try:
            width, height = map(int, parts[1].split())
        except ValueError:
            continue
        pixels = parts[3]
        if not (width > 0 and height > 0 and len(pixels) == width * height * 3):
            continue
        pictures += 1
        if pixels.count(b"\0") != len(pixels):
            nonblack.add(hashlib.sha256(parts[1] + b"\n" + pixels).digest())
    result = f"Desktop GS checkpoints: {pictures}/{len(checkpoints)} pictures, {len(nonblack)} distinct nonblack pictures."
    if not nonblack:
        result += " No visible picture was confirmed."
    elif len(nonblack) == 1:
        result += " The probe only saw one visible picture."
    print(result, flush=True)
    event("boot", ok=len(nonblack) >= 2, detail=result)
    return BootProbe(True, result, len(nonblack) >= 2)


def probe(executable: Path, iso: Path, stage: Path, checkpoints: tuple[int, ...], *,
          learning: tuple[Path, str] | None = None, check_windows: bool = False,
          missing_rpc: int = 0, gameplay: GameplayProbe | None = None) -> BootProbe:
    log = stage / "probe.log"
    frames = checkpoints[-1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("PS2_")}
    env.update(PS2_EE_LIVE="0", PS2_VU_LIVE="0", PS2_GS="cpu", PS2_THREADS="1",
               PS2APPLE_BOOT_PROGRESS="1",
               PS2_VU_RECORD=str(stage / "vu1"), PS2_VU0_RECORD=str(stage / "vu0"))
    if gameplay and gameplay.metal:
        del env["PS2_GS"]
    if check_windows or learning:
        env["PS2_WINDOW_FULL"] = "1"
    card = stage / "probe.ps2"
    card.unlink(missing_ok=True)
    misses, snapshot = stage / "misses.txt", stage / "ee-miss.bin"
    misses.unlink(missing_ok=True)
    snapshot.unlink(missing_ok=True)
    # Boot from SYSTEM.CNF as the IPA does. Passing the extracted file as argv[0]
    # would tell the guest its program is cdrom0:\BOOT.ELF;1.
    game_dir = stage
    capture_args = []
    if learning:
        game_dir, compiler = learning
        log = game_dir / "probe.log"
        captures = game_dir / "Captures"
        captures.mkdir()
        env.update(PS2_EE_LIVE=str(game_dir / "Native Cache"), PS2_EE_LIVE_BG="0",
                   PS2_EE_LIVE_OPT="-O0", PS2_EE_LIVE_JOBS=str(JOBS), PS2_VU_CXX=compiler)
        if sys.platform == "darwin":
            # The live compiler runs directly, outside CMake's SDK configuration.
            env["SDKROOT"] = output(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"])
        capture_args = ["--collect-ee-code", captures]
    command = [executable, "--disc", iso, "--game-dir", game_dir, "--frames", str(frames), *capture_args,
               "--misses", misses, "--capture-ee-miss", snapshot, "--memcard", card]
    presses = BOOT_PRESSES
    if gameplay:
        command += ["--rtc", "0"]
        presses = gameplay.boot_presses + gameplay.presses
        if gameplay.wait_for_mfifo_data:
            env["PS2_MFIFO_WAIT_FOR_DATA"] = "1"
        if gameplay.close_returns_descriptor:
            command.append("--iop-close-returns-descriptor")
        for axis in gameplay.axes:
            command += ["--axis", axis]
    elif frames > BOOT_CHECKPOINTS[-1]:
        presses += EXTENDED_PRESSES
    for press in presses:
        command += ["--press", press]
    pictures = stage / "Boot Pictures"
    if pictures.exists():
        shutil.rmtree(pictures)
    pictures.mkdir()
    command += ["--frames-dir", pictures, "--frame-every", "0"]
    if missing_rpc:
        command += ["--sif-prompt-missing-server", f"{missing_rpc:X}"]
    for frame in checkpoints:
        command += ["--frame-at", str(frame)]
    print(f"Booting from SYSTEM.CNF and checking up to {frames} guest frames.", flush=True)
    # Replaying recorded EE windows with full code checks can also exceed the short probe budget.
    timeout = 600 if learning or check_windows or gameplay else 360 if frames > BOOT_CHECKPOINTS[-1] else 240
    try:
        returncode = run_boot_probe(command, env, stage, log, frames, timeout)
    except subprocess.TimeoutExpired as error:
        print("\n".join(probe_tail(log)[-18:]), flush=True)
        raise BuildError("The desktop AOT boot probe timed out before reaching its frame limit.") from error
    lines = probe_tail(log)
    if returncode:
        print("\n".join(lines[-20:]), flush=True)
        raise BuildError(f"The desktop AOT boot probe exited with status {returncode}.")
    reason = next((line for line in reversed(lines) if line.startswith("[ee] stopped after ")), "")
    if reason.startswith(f"[ee] stopped after {frames} frames,") and reason.endswith(": frame limit reached"):
        print(f"Desktop AOT boot reached {frames} guest frames.", flush=True)
        report = report_boot_pictures(pictures, checkpoints)
        return BootProbe(True, report.detail, report.moving_video,
                         boot_diagnostics(log, lines, include_runtime_state=not report.moving_video))
    diagnostics = list(boot_diagnostics(log, lines, include_runtime_state=True))
    fatal = next((line for line in reversed(lines) if line.startswith("[ee] stopped at ")), "")
    if fatal and ": unsupported delay slot" in reason:
        reason = f"{reason.split(': ', 1)[0]} · {fatal}"
        diagnostics.append(fatal)
    print("\n".join(dict.fromkeys([*diagnostics, *lines[-18:]])), flush=True)
    return BootProbe(False, reason or "The boot probe did not report a complete run.")


def run_boot_probe(command, env, stage: Path, log: Path, frames: int, timeout: float) -> int:
    event("boot-progress", current=0, total=frames)
    reported = 0
    start = time.monotonic()
    deadline = start + timeout
    # The Windows probe uses software GS. Allow it to finish on slower
    # hardware as long as guest frames advance, with a separate total limit.
    windows_limit = start + max(3 * timeout, frames / 3) if sys.platform == "win32" else None
    try:
        with log.open("wb") as stream, subprocess.Popen(
                command, env=env, cwd=stage, stdout=stream, stderr=subprocess.STDOUT) as child:
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        returncode = child.wait(timeout=min(0.5, remaining))
                    except subprocess.TimeoutExpired:
                        returncode = None
                    for line in reversed(probe_tail(log)):
                        if match := re.fullmatch(r"\[boot-progress\] (\d{1,12})", line):
                            current = int(match[1])
                            if reported < current <= frames:
                                reported = current
                                event("boot-progress", current=current, total=frames)
                                if windows_limit is not None:
                                    deadline = min(windows_limit, time.monotonic() + timeout)
                            break
                    if returncode is not None:
                        return returncode
            except BaseException:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                raise
    finally:
        event("boot-progress-ended")


def probe_tail(log: Path) -> list[str]:
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 32768))
        return stream.read().decode("utf-8", errors="replace").splitlines()


def boot_diagnostics(log: Path, tail: list[str], *, include_runtime_state: bool) -> tuple[str, ...]:
    hints = {}
    loads = {}
    disc_queries = {}
    with log.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            iop_failure = line.startswith(("[iop] cannot read module ", "[iop] missing ",
                                           "[iop] import from unknown library ", "[iop] unknown instruction "))
            unknown_rpc = line.startswith("[sif] call ") and "to unknown endpoint " in line
            if len(hints) < 8 and (iop_failure or unknown_rpc or
                                  line.startswith(("[loadfile] method ", "[sys] unhandled syscall "))):
                hints[line.rstrip()] = None
            if include_runtime_state and line.startswith(("[loadfile] ", "[sif] IOP reboot requested ")):
                loads[line.rstrip()] = None
                if len(loads) > 8:
                    del loads[next(iter(loads))]
            if include_runtime_state and line.startswith(("[cdvdfsv] search ", "[cdvdfsv] N command ", "[cdvdfsv] S command ")):
                key = line.rstrip()
                disc_queries.pop(key, None)
                disc_queries[key] = None
                if len(disc_queries) > 6:
                    del disc_queries[next(iter(disc_queries))]
    if include_runtime_state:
        hints.update(loads)
        hints.update(disc_queries)
        threads = [line for line in tail if line.startswith("[thread] ") and not line.startswith("[thread]   ")]
        modules = [line for line in tail if line.startswith("[iop] module ")]
        waits = [line for line in tail if line.startswith(("[sema] ", "[sif] endpoint ", "[sif] bind to "))]
        sif_summary = [line for line in tail if line.startswith(("[sif] EE binds ", "[sif] EE calls ",
                                                                "[sif] RPC servers ", "[sif] of them with no IOP handler"))]
        video = [line for line in tail if line.startswith(("[gs] ", "[ipu] commands:", "[spin] "))]
        for line in threads[-4:] + modules[-6:] + waits[-6:] + sif_summary[-4:] + video[-3:]:
            hints[line] = None
    return tuple(hints)


def recorded_vu(stage: Path) -> set[Path]:
    return set((stage / "vu0").glob("*.bin")) | set((stage / "vu1").glob("*.bin"))


def vu1_capture_folder(disc_id: tuple[str, str], *, general_probe: bool = False) -> str:
    return f"PS2Apple-VU1-{disc_id[0]}-{disc_id[1][:16]}" if general_probe or disc_id in VU1_CAPTURE_DISCS else ""


def import_vu1_captures(directory: Path, target: Path) -> int:
    if directory.is_symlink() or not directory.is_dir():
        return 0
    count = 0
    for path in sorted(directory.iterdir()):
        match = re.fullmatch(r"[0-9A-F]{16}_([0-9A-F]{4})\.bin", path.name)
        if not match or path.is_symlink() or not path.is_file() or path.stat().st_size != 16384:
            continue
        entry = int(match[1], 16)
        if entry >= 16384 or entry % 8:
            continue
        shutil.copyfile(path, target / path.name)
        count += 1
        if count == VU1_CAPTURE_LIMIT:
            break
    return count


def uses_general_probe(disc_id: tuple[str, str], *, audited_preview: bool) -> bool:
    if (sys.platform not in ("darwin", "win32") or audited_preview or disc_id in GAMEPLAY_PROBES or
            disc_id in EE_WINDOW_DISCS or disc_id in EE_MAPS or disc_id in VU_MAPS):
        return False
    try:
        _, edition = catalog_disc(disc_id[0])
    except BuildError:
        return False
    return "testedFPS" not in edition and not edition.get("testedUnstable")


def record_ee_entries(deps: Dependencies, iso: Path, stage: Path, checkpoints: tuple[int, ...],
                      boot: bytes, segments: list[tuple[int, int, int]], *,
                      gameplay: GameplayProbe | None, missing_rpc: int) -> set[str]:
    recorder = stage / "Desktop Code Recorder"
    executable = deps.make_probe(recorder, stage / "Generated", gameplay, live_ee=True)
    result = probe(executable, iso, stage, checkpoints, learning=(recorder, deps.host_compiler),
                   missing_rpc=missing_rpc, gameplay=gameplay)
    if not result.complete and ": missing code at " not in result.detail:
        raise BuildError(f"The desktop EE recording stopped. {result.detail}")
    captures = sorted((recorder / "Captures").glob("*.bin"))
    overlays = stage / "Overlays"
    learned = set()
    for capture in captures:
        with capture.open("rb") as stream:
            pc = int.from_bytes(stream.read(4), "little")
        learned.add(recover_entry(f"{pc:x}", capture, boot, segments, overlays, preserve_window=True))
    misses = stage / "misses.txt"
    if not result.complete and misses.is_file():
        learned.update(recover_entry(token, stage / "ee-miss.bin", boot, segments, overlays, preserve_window=True)
                       for token in misses.read_text().split())
    print(f"Recorded {len(captures)} desktop EE captures and {len(learned)} entries. Verifying a fully AOT build.", flush=True)
    return learned


def prepare_game_code(deps: Dependencies, iso: Path, elf: Path, stage: Path, *,
                      boot_entries: tuple[str, ...] = (), audited_preview: bool = False, learn_ee: bool = False,
                      general_probe: bool = False, disc_id: tuple[str, str] = ("", "")) -> tuple[Path, str]:
    generated, overlays, modules = (stage / name for name in ("Generated", "Overlays", "Modules"))
    for directory in (generated, overlays, modules, stage / "vu0", stage / "vu1"):
        directory.mkdir()
    entries, misses = stage / "entries.txt", stage / "misses.txt"
    seeds = set(boot_entries)
    if disc_id == NARUTO_PAL:
        seeds.update(seed_naruto_ee(iso, overlays))
        print(f"Using {len(seeds)} audited Naruto EE entries from the selected disc.", flush=True)
    if map_name := EE_MAPS.get(disc_id):
        seeds.update(seed_disc_ee(map_name, iso, overlays))
        print(f"Using {len(seeds)} audited EE entries for {disc_id[0]} from the selected disc.", flush=True)
    entries.write_text("".join(f"{entry}\n" for entry in sorted(seeds)), encoding="ascii")
    if boot_entries:
        print(f"Using known EE boot entries: {', '.join(f'0x{int(entry, 16):08X}' for entry in boot_entries)}", flush=True)
    boot = elf.read_bytes()
    segments = code_segments(boot)
    gameplay = deps.probe_gameplay(GAMEPLAY_PROBES.get(disc_id))
    if general_probe:
        gameplay = replace(GENERAL_DISC_PROBE, metal=deps.desktop_wgpu is not None)
    default_checkpoints = AUDITED_BOOT_CHECKPOINTS if audited_preview else BOOT_CHECKPOINTS
    phases = iter(gameplay.phases if gameplay else (default_checkpoints,))
    checkpoints = next(phases)
    check_windows = learn_ee or general_probe or disc_id in EE_WINDOW_DISCS
    missing_rpc = TOMB_MISSING_RPC if disc_id == TOMB_USA else 0
    if map_name := VU_MAPS.get(disc_id):
        print(f"Recovered {seed_vu(map_name, boot, stage)} VU programs for {disc_id[0]} from the boot ELF.", flush=True)
    vu1_folder = vu1_capture_folder(disc_id, general_probe=general_probe)
    if vu1_folder:
        imported = import_vu1_captures(iso.parent / vu1_folder, stage / "vu1")
        if imported:
            print(f"Imported {imported} VU1 captures. The compiler will check their program keys.", flush=True)
    if b"SNR2" in boot or b"xff2" in boot:
        run([sys.executable, deps.source / "scripts/disc.py", "modules", iso, modules])
    if learn_ee:
        progress(.35, "Recording EE code loaded during the desktop boot")
        translate(deps, elf, generated, entries, modules, overlays)
        learned = record_ee_entries(deps, iso, stage, checkpoints, boot, segments, gameplay=gameplay,
                                    missing_rpc=missing_rpc)
        with entries.open("a", encoding="ascii") as stream:
            stream.writelines(token + "\n" for token in sorted(learned - set(boot_entries)))
    host = stage / "Desktop Probe"
    compiled_vu = set()
    translate_ee = True
    record_on_miss = general_probe and not learn_ee
    last_detail = ""
    for attempt in range(DISCOVERY_ROUNDS):
        probe_progress = .37 + .27 * attempt / DISCOVERY_ROUNDS
        progress(probe_progress, f"Translating and checking the game boot ({attempt + 1}/{DISCOVERY_ROUNDS})")
        if translate_ee:
            translate(deps, elf, generated, entries, modules, overlays, focused_overlays=check_windows)
        recordings = recorded_vu(stage)
        if recordings != compiled_vu:
            print(f"Compiling {len(recordings)} VU recordings before checking the linked game code.", flush=True)
            run([deps.host_tool("vurecomp"), stage / "vu1", generated, "--vu0-dir", stage / "vu0"])
            compiled_vu = recordings
        executable = deps.make_probe(host, generated, gameplay)
        result = probe(executable, iso, stage, checkpoints, check_windows=check_windows,
                       missing_rpc=missing_rpc, gameplay=gameplay)
        last_detail = result.detail
        if result.complete:
            next_phase = next(phases, None)
            if next_phase is not None:
                checkpoints = next_phase
                translate_ee = False
                continue
            if recorded_vu(stage) == compiled_vu:
                report = [f"Desktop AOT boot probe: {checkpoints[-1]} guest fields.", result.detail]
                if gameplay:
                    report.append(f"Verified the desktop probe after compiling {len(compiled_vu)} VU recordings.")
                if general_probe:
                    report.append("General disc probe used scripted menu inputs. Device gameplay and other scenes still need testing.")
                if not result.moving_video:
                    report.append("Desktop video unverified. The exported test IPA requires a device gameplay check.")
                if result.diagnostics:
                    report += ["Desktop boot diagnostics:", *result.diagnostics]
                print("\n".join(report[2:]), flush=True)
                return generated, "\n".join(report)
            translate_ee = False
            continue
        if ": missing code at " not in result.detail or not misses.is_file():
            raise BuildError(f"Cannot boot this disc with the AOT runtime. {result.detail}")
        old = set(entries.read_text().split())
        new = {recover_entry(token, stage / "ee-miss.bin", boot, segments, overlays, preserve_window=check_windows)
               for token in misses.read_text().split()} - old
        if record_on_miss:
            record_on_miss = False
            progress(probe_progress, "Recording additional EE code in one desktop run")
            try:
                new.update(record_ee_entries(deps, iso, stage, checkpoints, boot, segments, gameplay=gameplay,
                                             missing_rpc=missing_rpc) - old)
            except BuildError as error:
                print(f"Desktop EE recording could not finish: {error} Continuing with the strict AOT probe.", flush=True)
        if not new:
            raise BuildError(f"The PS2Apple compiler could not translate the reported EE entry. {result.detail}")
        with entries.open("a", encoding="ascii") as stream:
            stream.writelines(token + "\n" for token in sorted(new))
        print(f"Discovered {len(new)} guest entry points. Compiling them ahead of time.", flush=True)
        translate_ee = True
    raise BuildError(f"The PS2Apple boot probe did not stabilize after {DISCOVERY_ROUNDS} passes. {last_detail}")


def compile_ios(deps: Dependencies, generated: Path, build: Path, *, windows_kit: bool = False) -> Path:
    flags = sdl_link_flags(deps.sdl / "lib/pkgconfig/sdl3.pc")
    if not flags:
        raise BuildError("SDL3 is missing the iPhoneOS framework link flags.")
    shared = Path(os.environ.get("PS2APPLE_SHARED_IOS") or ROOT.parent.parent / "ios")
    config = {"IOS_HOST": ROOT / "ios", "SHARED_IOS": shared, "SDL": deps.sdl / "lib/libSDL3.a",
              "SDL_INCLUDE": deps.sdl / "include", "SDL_FLAGS": ";".join(flags)}
    run([TOOLS["cmake"], "-S", deps.source, "-B", build, *xcode_cmake(deps.sdk),
         "-DPS2_AOT_ONLY=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_SDL2=ON",
         f"-DPS2APPLE_WINDOWS_KIT={'ON' if windows_kit else 'OFF'}",
         f"-DPS2_GENERATED={generated}", f"-DPS2_WGPU_DIR={deps.wgpu}",
         *[f"-DPS2APPLE_{key}={value}" for key, value in config.items()]])
    targets = ["PS2Game", "ps2apple_ios_launcher"] if windows_kit else ["PS2Game"]
    run([TOOLS["cmake"], "--build", build, "--target", *targets, "--parallel", str(JOBS)])
    executable = build / "PS2Game.app" / "PS2Game"
    with executable.open("rb") as stream:
        check_macho(stream)
    return executable


def bundle_info(profile: dict, *, general_probe: bool = False) -> dict:
    landscape = ["UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"]
    info = {
        **IOS_GAMEPAD_INFO,
        "CFBundleExecutable": "PS2Game", "CFBundleIdentifier": profile["bundleID"], "CFBundlePackageType": "APPL",
        "CFBundleName": profile["title"], "CFBundleDisplayName": profile["title"],
        "CFBundleShortVersionString": RELEASE_VERSION, "CFBundleVersion": "1612",
        "CFBundleSupportedPlatforms": ["iPhoneOS"], "MinimumOSVersion": MIN_IOS,
        "LSRequiresIPhoneOS": True, "UIDeviceFamily": [1, 2], "UIFileSharingEnabled": True,
        "LSSupportsOpeningDocumentsInPlace": True, "UILaunchScreen": {}, "UIRequiresFullScreen": True,
        "UISupportedInterfaceOrientations": landscape, "UISupportedInterfaceOrientations~ipad": landscape,
        "PS2DiscSerial": profile["serial"], "PS2BootSHA256": profile["bootSHA256"],
        "PS2AppleEngineVersion": ENGINE_VERSION,
        "PS2AppleHalfRefresh": True,
        "UIApplicationSceneManifest": {
            "UIApplicationSupportsMultipleScenes": False,
            "UISceneConfigurations": {"UIWindowSceneSessionRoleApplication": [{
                "UISceneConfigurationName": "SDLSceneConfiguration", "UISceneDelegateClassName": "SDLUIKitSceneDelegate"
            }]},
        },
    }
    disc_id = (profile["serial"], profile["bootSHA256"])
    if general_probe or disc_id in EE_WINDOW_DISCS:
        info["PS2AppleCheckEEWindows"] = True
    if disc_id in IOP_CLOSE_DISCS:
        info["PS2AppleIOPCloseReturnsDescriptor"] = True
    if disc_id in MFIFO_DISCS:
        info["PS2AppleWaitForMFIFOData"] = True
    if disc_id in (TOMB_USA, BLACK_USA, SHAOLIN_USA, BURNOUT3_USA):
        info["PS2AppleStretchAudio"] = True
    if disc_id == TOMB_USA:
        info["PS2ApplePromptMissingRPC"] = TOMB_MISSING_RPC
    if folder := vu1_capture_folder(disc_id, general_probe=general_probe):
        info["PS2AppleVU1Capture"] = folder
    return info


def bundle_report(boot_report: str) -> str:
    return (
        f"PS2Apple built-in engine {ENGINE_VERSION}\n"
        f"Upstream MIT source: https://github.com/{ENGINE_UPSTREAM}/tree/{ENGINE_REVISION}\n"
        f"https://github.com/gfx-rs/wgpu-native/releases/tag/{WGPU_VERSION}\n"
        f"https://github.com/libsdl-org/SDL/tree/{SDL_REV}\n"
        f"{boot_report}\n"
    )


def app_bundle(deps: Dependencies, executable: Path, app: Path, profile: dict, boot_report: str = "", *,
               general_probe: bool = False) -> None:
    app.mkdir()
    shutil.copy2(executable, app / "PS2Game")
    with (app / "Info.plist").open("wb") as stream:
        plistlib.dump(bundle_info(profile, general_probe=general_probe), stream)
    shutil.copy2(deps.source / "LICENSE", app / "LICENSE-ps2-native-recomp.txt")
    shutil.copytree(ROOT / "ios" / "Licenses", app / "Licenses")
    shutil.copy2(deps.source / "runtime/src/ipu/NOTICE", app / "Licenses/Play-MPEG.txt")
    (app / "PortSource.txt").write_text(bundle_report(boot_report), encoding="utf-8")


def main(*, audited_preview: bool = False, desktop_metal: bool = False) -> int:
    signal.signal(signal.SIGTERM, cancelled)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build"))
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    for name in ("iso", "elf", "app"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare(args.cache.resolve(), desktop_metal=desktop_metal)
            return 0
        if not all((args.profile, args.iso, args.elf, args.app)):
            parser.error("build requires --profile, --iso, --elf and --app")
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        algorithm = "sha1" if audited_preview else "sha256"
        fingerprint = "bootSHA1" if audited_preview else "bootSHA256"
        if digest(args.elf, algorithm) != profile[fingerprint]:
            raise BuildError("The boot ELF changed after the builder inspected it.")
        if audited_preview:
            profile["bootSHA256"] = digest(args.elf)
        disc_id = (profile["serial"], profile["bootSHA256"])
        boot_entries = KNOWN_BOOT_ENTRIES.get(disc_id, ())
        gameplay = GAMEPLAY_PROBES.get(disc_id)
        general_probe = uses_general_probe(disc_id, audited_preview=audited_preview)
        cache = args.cache.resolve()
        deps = prepare(cache, desktop_metal=bool(gameplay and gameplay.metal))
        if general_probe:
            try:
                deps = replace(deps, desktop_wgpu=prepare_wgpu(cache / "Toolchains", mac=True))
            except (BuildError, OSError, zipfile.BadZipFile) as error:
                print(f"Desktop Metal unavailable. The general disc probe will use software GS. {error}", flush=True)
        with tempfile.TemporaryDirectory(prefix="aot-", dir=args.app.parent) as temporary:
            stage = Path(temporary)
            generated, boot_report = prepare_game_code(deps, args.iso.resolve(), args.elf.resolve(), stage,
                                                       boot_entries=boot_entries, audited_preview=audited_preview,
                                                       learn_ee=disc_id in EE_RECORDING_DISCS,
                                                       general_probe=general_probe, disc_id=disc_id)
            progress(.72, "Linking the game's AOT code with the iPhoneOS Metal shader runtime")
            executable = compile_ios(deps, generated, stage / "iPhoneOS")
            progress(.89, "Staging the iPhoneOS app and shared launcher")
            app_bundle(deps, executable, args.app, profile, boot_report, general_probe=general_probe)
        return 0
    except (BuildError, DiscError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
