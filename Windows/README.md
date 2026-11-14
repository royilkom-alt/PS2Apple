# PS2Apple 1.0 for Windows

The portable Windows x64 EXE contains .NET, Python, the PS2 EE and VU recompiler, Zig/Clang, LLVM's Mach-O linker, and the shared PS2Apple iPhoneOS runtime and launcher. It builds a new iPhoneOS ARM64 app from the ISO you select.

1. Open **PS2Apple-1.0-Windows-x64.exe** on Windows 11. On first use, it extracts its tools to your local app data folder. **Check Tools** verifies the bundled compiler.
2. Click **Choose ISO…**, select a PS2 disc, and choose a writable output folder. A valid ISO can be selected even if it has no entry in Game Catalog.
3. Click **Build IPA**. PS2Apple reads the disc's boot executable, generates and tests its ahead-of-time code, and links and validates the new IPA locally. The first build also initializes the compiler cache. During the boot check, the app shows how many PS2 frames have run.
4. Sign and install the IPA for iPhone or iPad. In the launcher, press **Play** and choose the original ISO on first launch.

Building needs no extra installations or network connection. PS2Apple reads the ISO locally. Game Catalog records device gameplay tests. New ISOs can encounter unsupported PS2 behavior during the boot check or in gameplay.

## Prepare a Windows release

The maintainer stages the reusable runtime on macOS with Xcode, Python 3.12+, CMake and Ninja. The resulting Windows EXE uses no Xcode or Mac at build time. Download [Zig 0.15.2](https://ziglang.org/download/) for macOS and Windows, the Windows x64 [LLVM 22.1.7 installer](https://github.com/llvm/llvm-project/releases/tag/llvmorg-22.1.7) (extract `bin/ld64.lld.exe`), its [LLVM license](https://github.com/llvm/llvm-project/blob/llvmorg-22.1.7/llvm/LICENSE.TXT) beside the linker, and the [Python 3.14.10 Windows x64 embeddable ZIP](https://www.python.org/downloads/release/python-31410/).

```sh
python3 scripts/stage-windows-kit.py \
  --zig-host /path/to/macos/zig \
  --zig-windows /path/to/zig-x86_64-windows-0.15.2.zip \
  --lld-windows /path/to/ld64.lld.exe \
  --cache /path/to/build-cache /path/to/new-toolchain-kit

scripts/build-windows-exe.sh /path/to/python-3.14.10-embed-amd64.zip \
  /path/to/new-toolchain-kit dist/windows
```

The EXE packaging step uses the .NET 10 SDK. The link kit contains PS2Apple's generic static libraries, icon assets, and the runtime's iPhoneOS import names. On Windows, `PS2Apple.exe --build "Game.iso" "Output Folder" "Build.log"` exercises the same local builder as the UI.
