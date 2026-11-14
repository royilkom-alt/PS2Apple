# PS2Apple changelog

## v1.0.1 · Windows

### Fixes

- **Downhill Domination (USA, `SCUS-97177`)** now uses the dedicated Downhill Revived port on Windows. The generated IPA uses the same Native Metal renderer, launcher, and memory-card handling as the Mac build.
- **Dragon Ball Z: Budokai Tenkaichi 3 (USA, `SLUS-21678`)** now uses its dedicated iPhoneOS runtime. The Windows builder compiles the gameplay overlay and VU programs from the selected disc.
- Windows now recognizes the audited USA profiles for **Mortal Kombat: Shaolin Monks** and **God of War II** by their original boot executables.
- The portable EXE bundles the tools needed for offline ISO-to-IPA builds, including the Downhill WASI compiler, archiver, and WABT. **Check Tools** verifies the bundled toolchain.

### Verified on Windows 11

- Built and validated ARM64 iPhoneOS IPAs for Downhill from an ISO test fixture with the original boot executable, Budokai Tenkaichi 3 from the full USA disc, and a homebrew disc.
- Compared Downhill's generated game code and Native Metal renderer with the Mac build. iSolution recorded 60 FPS on the Mac-generated IPA on iPhone 17 Pro. Gameplay FPS for the Windows-generated IPA on iPhone 18 Pro Max is pending.
