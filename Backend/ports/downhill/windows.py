"""Build the dedicated Downhill iPhoneOS port from the selected disc on Windows."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path

from builder import BuildError, MIN_IOS, progress, run
from ports.downhill import build as recipe

PORT_LIBRARIES = ("libdownhill_host.a", "libdhd_render.a", "libSDL3.a")
WASI_CLANG_VERSION = "23.1.0-rc3"
NATIVE_FLAGS = (
    "-DDHD_IOS=1", "-O3", "-g0", "-ffp-model=strict", "-ffp-contract=off", "-ftrivial-auto-var-init=zero",
    "-fno-strict-aliasing", "-ffunction-sections", "-fdata-sections", "-fwrapv", "-pthread", "-w",
    "-DWASM_RT_STACK_DEPTH_COUNT=1", "-DWASM_RT_MAX_CALL_STACK_DEPTH=512", "-DWASM_RT_THREAD_LOCAL=",
    "-DWASM_RT_SKIP_SIGNAL_RECOVERY=1", "-DWASM_RT_MEMCHECK_GUARD_PAGES=0",
    "-DWASM_RT_MEMCHECK_BOUNDS_CHECK=1", "-DWASM_RT_TRAP_HANDLER=host64_trap",
)


def digest_elf(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha1").hexdigest()


def native_sources(source: Path, work: Path, wabt: Path, module: Path) -> list[tuple[Path, list[str]]]:
    # These are the same wasm2c and ABI preparation steps as the port's iOS release build.
    sys.path.insert(0, str(source / "port/tools"))
    try:
        from build_host64 import TARGETS, glue
        from gen_host64_imports import generate
        from prepare_wasm2c import check_launcher_exports, module_units, prepare_units
        from vu1_aot import generate as generate_vu

        wasm2c = wabt / "bin" / ("wasm2c.exe" if os.name == "nt" else "wasm2c")
        units = module_units(work, partial(run, cwd=work), wasm2c, module, num_outputs=8)
        _, exports = generate(work / "core.h", work, linux=True)
        check_launcher_exports(exports)
        prepare_units(units, work, exact_f32=True)
        header = (wabt / "include/wasm-rt.h").read_text(encoding="utf-8")
        start = header.index("#ifdef WASM_RT_C11_AVAILABLE\n#define WASM_RT_THREAD_LOCAL _Thread_local\n")
        end = header.index("#endif\n", start) + len("#endif\n")
        header = header[:start] + "#ifndef WASM_RT_THREAD_LOCAL\n" + header[start:end] + "#endif\n" + header[end:]
        (work / "wasm-rt.h").write_text(header, encoding="utf-8")
        generate_vu(work / "vu1_aot.inc")
        sources = [(path, []) for path in (*units, wabt / "share/wabt/wasm2c/wasm-rt-impl.c",
                                            work / "imports_gen.c", *glue(TARGETS["ios-arm64"]))]
        sources[-1][1].append("-Dmain=dhd_launcher_main")
        sources.append((source / "port/runtime/vu0_interp.c", ["-DPS2_VU_AOT"]))
        return sources
    finally:
        sys.path.pop(0)


def compile_native(kit, port: Path, source: Path, work: Path, wabt: Path, module: Path, manifest: dict) -> Path:
    work.mkdir()
    previous = Path.cwd()
    try:
        # The port's VU generator resolves the extracted assets relative to its source checkout.
        os.chdir(source)
        sources = native_sources(source, work, wabt, module)
    finally:
        os.chdir(previous)
    include = [work, wabt / "include", wabt / "share/wabt/wasm2c", source / "port/host64",
               source / "port/runtime", source / "port/include", source / "port/host",
               port / "SIMDe", port / "iPhoneOS/SDL"]
    prefix_maps = [(source, "src"), (work, "gen"), (wabt, "wabt"), (port / "SIMDe", "simde")]
    args = [kit.zig, "cc", "-target", f"aarch64-ios.{MIN_IOS}", "-isystem", kit.darwin_headers,
            "-fno-sanitize=all", *NATIVE_FLAGS, *(f"-I{path}" for path in include),
            *(f"-ffile-prefix-map={form}={label}" for path, label in prefix_maps
              for form in dict.fromkeys((str(path), path.as_posix())))]

    def compile_one(item: tuple[Path, list[str]]) -> Path:
        path, flags = item
        obj = work / (path.stem + ".o")
        run([*args, *flags, "-c", path, "-o", obj], cwd=work, timeout=1800)
        return obj

    # Limit concurrent translation of the large wasm2c parts on typical Windows PCs.
    with ThreadPoolExecutor(max_workers=min(2, recipe.JOBS)) as executor:
        futures = {path: executor.submit(compile_one, (path, flags))
                   for path, flags in sorted(sources, key=lambda item: -item[0].stat().st_size)}
        for index, future in enumerate(as_completed(futures.values()), 1):
            future.result()
            progress(.72 + .14 * index / len(sources), f"Compiling Downhill for iPhoneOS · {index}/{len(sources)} objects")
        objects = [futures[path].result() for path, _ in sources]
    return kit.link_ios(work / "DownhillRevived", objects, port / "iPhoneOS", PORT_LIBRARIES, manifest)


def build(kit, elf: Path, stage: Path, profile: dict) -> Path:
    if digest_elf(elf) != profile["bootSHA1"]:
        raise BuildError("The selected disc does not match the Downhill Revived port.")
    port = kit.toolchain / "Downhill"
    manifest_path = port / "iPhoneOS/link.json"
    if not manifest_path.is_file():
        raise BuildError("The dedicated Downhill build tools are missing. Reinstall PS2Apple.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("revision") != profile["revision"] or manifest.get("minimumOS") != MIN_IOS:
        raise BuildError("The bundled Downhill renderer does not match this PS2Apple release.")
    source = stage / "dhd"
    shutil.copytree(port / "Source", source)
    directory = source / "iso"
    directory.mkdir()
    shutil.copyfile(elf, directory / "SCUS_971.77")
    wasi = port / "WASI"
    wabt = port / "WABT"
    env = {name: value for name, value in os.environ.items() if not name.startswith(("DHD_", "WASI_", "WABT_", "SIMDE_"))}
    env.update(
        PATH=os.pathsep.join([str(wasi / "bin"), str(wabt / "bin"), env.get("PATH", "")]),
        DHD_DATA_CLANG=str(port / "COFF/clang.exe"), DHD_DEVCACHES=str(stage / "Tools"),
        DHD_MODULE_CACHE="0", PYTHONUTF8="1", PYTHONUNBUFFERED="1",
        WASI_SYSROOT=(wasi / "share/wasi-sysroot").as_posix(), WABT_DIR=str(wabt), SIMDE_DIR=str(port / "SIMDe"),
    )
    python = [sys.executable, "-X", "utf8", "-u"]
    progress(.22, "Extracting code from the verified Downhill disc")
    run([*python, source / "tools/split.py"], cwd=source, env=env)
    progress(.38, "Compiling the Downhill Revived game core")
    run([*python, source / "port/tools/build_wasm.py", "--fail-fast", "-j", str(recipe.JOBS)],
        cwd=source, env=env)
    progress(.72, "Compiling Downhill for iPhoneOS and linking Native Metal")
    executable = compile_native(kit, port, source, source / "build/host64-ios", wabt,
                                source / "build/wasm/dhd_core.wasm", manifest)

    app = stage / "DownhillRevived.app"
    recipe.app_bundle(source, executable, app, profile)
    return app
