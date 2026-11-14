from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import plistlib
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Backend"))

import builder
from disc import DiscError, ISOImage, SECTOR
from ee_discovery import code_segments, recover_entry
from engine_source import ENGINE_ROOT
from ipa import ARM64, DOWNHILL_UI, IOS, MH_EXECUTE, SHARED_UI, check_macho, export_ipa


class AOTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name, slug in (("recipe", "generic"), ("bt3", "budokai-tenkaichi-3")):
            path = ROOT / "Backend" / "ports" / slug / "build.py"
            spec = importlib.util.spec_from_file_location(f"test_{name}_recipe", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            setattr(cls, name, module)

    def test_compiler_source_is_bundled(self):
        self.assertEqual(self.recipe.engine_source(), ENGINE_ROOT)
        self.assertTrue((ENGINE_ROOT / "recomp/main.cpp").is_file())
        self.assertTrue((ENGINE_ROOT / "runtime/src/dispatch.cpp").is_file())
        self.assertNotEqual(self.bt3.ENGINE_SOURCE, ENGINE_ROOT)
        ios_target = (self.bt3.ROOT / "ios/CMakeLists.txt").read_text()
        self.assertIn("${PS2APPLE_SHARED_IOS}/PS2Launcher.m", ios_target)

    def test_desktop_recorder_selects_the_macos_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            recorder = stage / "Desktop Code Recorder"
            recorder.mkdir()
            sdk = "/Xcode/SDKs/MacOSX.sdk"
            with (patch.object(sys, "platform", "darwin"),
                  patch.dict(os.environ, SDKROOT="/Xcode/SDKs/iPhoneOS.sdk"),
                  patch.object(self.recipe, "output", return_value=sdk) as output,
                  patch.object(self.recipe, "run_boot_probe", return_value=0) as run,
                  contextlib.redirect_stdout(io.StringIO())):
                (recorder / "probe.log").touch()
                self.recipe.probe(recorder / "ps2run", stage / "fixture.iso", stage, (1,),
                                  learning=(recorder, "/Xcode/usr/bin/clang++"))
            output.assert_called_once_with(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"])
            self.assertEqual(run.call_args.args[1]["SDKROOT"], sdk)
            self.assertEqual(run.call_args.args[1]["PS2APPLE_BOOT_PROGRESS"], "1")

    def test_boot_is_repeated_after_compiling_recorded_vector_code(self):
        from make_dynamic_iso import make_dynamic_iso
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            iso = stage / "fixture.iso"
            elf = stage / "fixture.elf"
            make_dynamic_iso(iso)
            with ISOImage(iso) as disc:
                serial, entry = disc.boot()
                disc.extract_boot(entry, elf)
            deps = self.recipe.Dependencies(ENGINE_ROOT, stage, "", stage, stage, "")

            def run_probe(*_, **__):
                (stage / "vu1" / "1234_0000.bin").write_bytes(b"recorded")
                return self.recipe.BootProbe(True, "checked", True)

            with (patch.object(self.recipe, "probe", side_effect=run_probe) as probe,
                  patch.object(self.recipe, "run") as run,
                  patch.object(self.recipe, "translate") as translate,
                  contextlib.redirect_stdout(io.StringIO())):
                _, report = self.recipe.prepare_game_code(deps, iso, elf, stage, audited_preview=True)
            self.assertIn("checked", report)
            self.assertEqual(probe.call_count, 2)
            translate.assert_called_once()
            self.assertEqual(sum(str(args[0][0]).endswith("/vurecomp") for args, _ in run.call_args_list), 1)

    def test_re4_imports_only_validly_shaped_vu1_captures(self):
        self.assertFalse(self.recipe.vu1_capture_folder(("SLUS-21134", "0" * 64)))
        self.assertFalse(self.recipe.vu1_capture_folder(("SLUS-21858", "0" * 64)))
        self.assertEqual(self.recipe.vu1_capture_folder(self.recipe.TOMB_USA), "PS2Apple-VU1-SLUS-21858-79191191fb801745")
        folder = self.recipe.vu1_capture_folder(self.recipe.RE4_USA)
        self.assertEqual(folder, "PS2Apple-VU1-SLUS-21134-465d144f526603d0")
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            source, target = stage / folder, stage / "vu1"
            source.mkdir()
            target.mkdir()
            valid = "0000000000000001_0010.bin"
            payload = b"\x01" * 16384
            (source / valid).write_bytes(payload)
            (source / "0000000000000002_0000.bin").write_bytes(payload[:-1])
            (source / "0000000000000003_0001.bin").write_bytes(payload)
            (source / "0000000000000004_4000.bin").write_bytes(payload)
            (source / "seed_00000005_0000.bin").write_bytes(payload)
            (source / "0000000000000006_0000.bin").symlink_to(source / valid)
            self.assertEqual(self.recipe.import_vu1_captures(source, target), 1)
            self.assertEqual(list(target.iterdir()), [target / valid])
            self.assertEqual((target / valid).read_bytes(), payload)

    def test_general_probe_applies_to_untested_desktop_discs(self):
        untested = ("SLUS-29999", "0" * 64)
        with patch.object(sys, "platform", "darwin"):
            self.assertTrue(self.recipe.uses_general_probe(untested, audited_preview=False))
            self.assertFalse(self.recipe.uses_general_probe(untested, audited_preview=True))
            for disc_id in (self.recipe.RE4_USA, self.recipe.NARUTO_PAL, self.recipe.FFX_USA,
                            ("SLUS-21171", "0" * 64), ("SLUS-21858", "0" * 64),
                            ("SLUS-21050", "0" * 64)):
                self.assertFalse(self.recipe.uses_general_probe(disc_id, audited_preview=False), disc_id)
        with patch.object(sys, "platform", "win32"):
            self.assertTrue(self.recipe.uses_general_probe(untested, audited_preview=False))
            self.assertFalse(self.recipe.uses_general_probe(self.recipe.GH80_USA, audited_preview=False))
        with patch.object(sys, "platform", "linux"):
            self.assertFalse(self.recipe.uses_general_probe(untested, audited_preview=False))
        self.assertEqual(self.recipe.vu1_capture_folder(untested, general_probe=True),
                         "PS2Apple-VU1-SLUS-29999-0000000000000000")

    def test_retail_gameplay_probe_rechecks_the_final_scene_after_vu_compilation(self):
        from make_dynamic_iso import make_dynamic_iso
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            iso, elf = stage / "fixture.iso", stage / "fixture.elf"
            make_dynamic_iso(iso)
            with ISOImage(iso) as disc:
                _, entry = disc.boot()
                disc.extract_boot(entry, elf)
            deps = self.recipe.Dependencies(ENGINE_ROOT, stage, "", stage, stage, "")
            gameplay = self.recipe.GAMEPLAY_PROBES[self.recipe.RE4_USA]

            def run_probe(_executable, _iso, _stage, checkpoints, **options):
                self.assertIs(options["gameplay"], gameplay)
                capture = stage / "vu1" / f"{checkpoints[-1]:016X}_0000.bin"
                capture.write_bytes(b"recorded")
                return self.recipe.BootProbe(True, "checked", True)

            with (patch.object(self.recipe, "probe", side_effect=run_probe) as probe,
                  patch.object(self.recipe, "run") as run,
                  patch.object(self.recipe, "translate") as translate,
                  contextlib.redirect_stdout(io.StringIO())):
                _, report = self.recipe.prepare_game_code(deps, iso, elf, stage, disc_id=self.recipe.RE4_USA)
            self.assertEqual([call.args[3] for call in probe.call_args_list],
                             [*gameplay.phases, gameplay.phases[-1]])
            self.assertEqual(sum(str(call.args[0][0]).endswith("/vurecomp") for call in run.call_args_list), 2)
            self.assertIn("5200 guest fields", report)
            translate.assert_called_once()
            self.assertNotIn(("SLUS-21134", "0" * 64), self.recipe.GAMEPLAY_PROBES)
            self.assertNotIn(("SLUS-21171", "0" * 64), self.recipe.GAMEPLAY_PROBES)

    def test_naruto_aot_probe_uses_audited_entries_and_reaches_battle(self):
        from make_dynamic_iso import make_dynamic_iso
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            iso, elf = stage / "Naruto Original Name.iso", stage / "fixture.elf"
            make_dynamic_iso(iso)
            with ISOImage(iso) as disc:
                _, entry = disc.boot()
                disc.extract_boot(entry, elf)
            deps = self.recipe.Dependencies(ENGINE_ROOT, stage, "", stage, stage, "")
            disc_id = self.recipe.NARUTO_PAL
            self.assertIn(disc_id, self.recipe.EE_WINDOW_DISCS)
            self.assertNotIn((disc_id[0], "0" * 64), self.recipe.EE_WINDOW_DISCS)
            self.assertTrue(self.recipe.vu1_capture_folder(disc_id))
            with (patch.object(self.recipe, "probe", return_value=self.recipe.BootProbe(True, "checked", True)) as probe,
                  patch.object(self.recipe, "seed_naruto_ee", return_value={"0072C000_12345678:2ec0"}) as seed,
                  patch.object(self.recipe, "run"), patch.object(self.recipe, "translate"),
                  contextlib.redirect_stdout(io.StringIO())):
                _, report = self.recipe.prepare_game_code(deps, iso, elf, stage, disc_id=disc_id)
            seed.assert_called_once_with(iso, stage / "Overlays")
            self.assertIn("0072C000_12345678:2ec0", (stage / "entries.txt").read_text())
            probe.assert_called_once()
            strict = probe.call_args
            self.assertTrue(strict.kwargs["check_windows"])
            self.assertTrue(strict.kwargs["gameplay"].close_returns_descriptor)
            self.assertIn("5300 guest fields", report)

    def test_completed_audited_boot_with_one_picture_is_a_warning(self):
        from make_dynamic_iso import make_dynamic_iso
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            iso, elf = stage / "fixture.iso", stage / "fixture.elf"
            make_dynamic_iso(iso)
            with ISOImage(iso) as disc:
                _, entry = disc.boot()
                disc.extract_boot(entry, elf)
            picture = b"P6\n1 1\n255\n\x01\x02\x03"
            (stage / "frame_00001.ppm").write_bytes(picture)
            (stage / "frame_00002.ppm").write_bytes(picture)
            (stage / "frame_00003.ppm").write_bytes(b"P6\n3 1\n255\n\x09\x09\x09")
            deps = self.recipe.Dependencies(ENGINE_ROOT, stage, "", stage, stage, "")
            with (contextlib.redirect_stdout(io.StringIO()), patch.object(self.recipe, "run"),
                  patch.object(self.recipe, "translate")):
                result = self.recipe.report_boot_pictures(stage, (1, 2, 3))
                self.assertIn("2/3 pictures, 1 distinct nonblack", result.detail)
                with patch.object(self.recipe, "probe", return_value=result):
                    _, report = self.recipe.prepare_game_code(deps, iso, elf, stage, audited_preview=True)
            self.assertIn("Desktop video unverified", report)

    def test_incomplete_aot_boot_still_fails(self):
        from make_dynamic_iso import make_dynamic_iso
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            iso, elf = stage / "fixture.iso", stage / "fixture.elf"
            make_dynamic_iso(iso)
            with ISOImage(iso) as disc:
                _, entry = disc.boot()
                disc.extract_boot(entry, elf)
            deps = self.recipe.Dependencies(ENGINE_ROOT, stage, "", stage, stage, "")
            result = self.recipe.BootProbe(False, "guest halted at 20 fields")
            with (patch.object(self.recipe, "probe", return_value=result),
                  patch.object(self.recipe, "run"), patch.object(self.recipe, "translate"),
                  contextlib.redirect_stdout(io.StringIO()),
                  self.assertRaisesRegex(builder.BuildError, "guest halted at 20 fields")):
                self.recipe.prepare_game_code(deps, iso, elf, stage)

    def test_inconclusive_video_report_keeps_guest_wait_state(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "probe.log"
            syscall = "[sys] unhandled syscall 0x7b"
            load = "[loadfile] cdrom0:\\IRX\\SOUND.IRX;1"
            unavailable = "[iop] cannot read module cdrom0:\\IRX\\SOUND.IRX;1"
            search = "[cdvdfsv] search cdrom0:\\INTRO.PSS;1 -> missing"
            thread = "[thread] 2 status 4 prio 0 wait 2/2 pc 0x00495528 ra 0x00495DE8"
            sif = "[sif] endpoint 0x80000001 call fn 5 is waiting for its reply"
            bind = "[sif] bind to 0x00534E03 is waiting"
            ipu = "[ipu] commands: stalls 0 out 0 qwords"
            log.write_text("\n".join((syscall, syscall, load, unavailable, search, thread, sif, bind, ipu)))
            tail = self.recipe.probe_tail(log)
            self.assertEqual(self.recipe.boot_diagnostics(log, tail, include_runtime_state=True),
                             (syscall, unavailable, load, search, thread, sif, bind, ipu))
            self.assertEqual(self.recipe.boot_diagnostics(log, tail, include_runtime_state=False), (syscall, unavailable))

    def test_extract_segments_pad_and_validate_revision(self):
        # Two microinstructions separated by a synthetic VIF upload header.
        boot = b"abcdefghheader!!ijklmnop"
        code = boot[:8] + boot[16:24]
        fnv = 1469598103934665603
        for byte in code:
            fnv = ((fnv ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        program = {"id": "12345678", "extent": "0x10", "fnv1a64": hex(fnv), "entries": ["0x0", "0x8"],
                   "segments": [{"offset": "0x0", "length": "0x8"}, {"offset": "0x10", "length": "0x8"}]}
        manifest = {"elf_sha256": hashlib.sha256(boot).hexdigest(), "image_size": "0x4000", "programs": [program]}
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            self.assertEqual(self.bt3.seed_bt3_vu1(boot, dest, manifest), 2)
            for entry in ("0000", "0008"):
                self.assertEqual((dest / f"seed_12345678_{entry}.bin").read_bytes(), code.ljust(16384, b"\0"))
            with self.assertRaisesRegex(builder.BuildError, "does not match"):
                self.bt3.seed_bt3_vu1(boot + b"!", dest, manifest)
            program["fnv1a64"] = "0x0"
            with self.assertRaisesRegex(builder.BuildError, "failed content validation"):
                self.bt3.seed_bt3_vu1(boot, dest, manifest)


def record(name, sector, size, flags=0):
    length = 33 + len(name) + (len(name) % 2 == 0)
    result = bytearray(length)
    result[0] = length
    result[2:10] = struct.pack("<I", sector) + struct.pack(">I", sector)
    result[10:18] = struct.pack("<I", size) + struct.pack(">I", size)
    result[25] = flags
    result[28:32] = b"\x01\0\0\x01"
    result[32] = len(name)
    result[33:33 + len(name)] = name
    return result


def make_iso(path, serial=b"SCUS_971.77", boot=b"BOOT2", filetype=b"\x08\0"):
    data = bytearray(24 * SECTOR)
    # A boot volume descriptor can precede the primary volume descriptor.
    data[16 * SECTOR:16 * SECTOR + 7] = b"\0CD001\x01"
    primary = 17 * SECTOR
    data[primary:primary + 7] = b"\x01CD001\x01"
    root = record(b"\0", 20, SECTOR, 2)
    data[primary + 156:primary + 156 + len(root)] = root
    config = boot + b" = cdrom0:\\\\" + serial + b";1\r\nVER = 1.00\r\n"
    elf = bytearray(128)
    elf[:6] = b"\x7fELF\x01\x01"
    elf[18:20] = filetype
    directory = record(b"SYSTEM.CNF;1", 21, len(config)) + record(serial + b";1", 22, len(elf))
    data[20 * SECTOR:20 * SECTOR + len(directory)] = directory
    data[21 * SECTOR:21 * SECTOR + len(config)] = config
    data[22 * SECTOR:22 * SECTOR + len(elf)] = elf
    path.write_bytes(data)
    return hashlib.sha1(elf).hexdigest()


def fixture_binary(platform=IOS, cpu=ARM64, shared=True):
    version = struct.pack("<6I", 0x32, 24, platform, 0x110000, 0x110000, 0)
    return struct.pack("<8I", 0xFEEDFACF, cpu, 0, MH_EXECUTE, 1, len(version), 0, 0) + version + (SHARED_UI if shared else b"")


def fixture_app(base, platform=IOS):
    app = base / "Fixture.app"
    app.mkdir()
    with (app / "Info.plist").open("wb") as file:
        plistlib.dump({
            "CFBundleExecutable": "Fixture",
            "CFBundleIdentifier": "net.example.fixture",
            "CFBundlePackageType": "APPL",
            "CFBundleSupportedPlatforms": ["iPhoneOS"],
        }, file)
    executable = app / "Fixture"
    executable.write_bytes(fixture_binary(platform))
    executable.chmod(0o755)
    return app


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="PS2 Builder Test ")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.iso = self.path / "My original disc.iso"

    def test_read_ps2_disc_and_extract_boot(self):
        digest = make_iso(self.iso)
        with ISOImage(self.iso) as image:
            serial, entry = image.boot()
            self.assertEqual(serial, "SCUS-97177")
            self.assertEqual(image.fingerprint(entry), digest)
            target = self.path / "extracted/boot.elf"
            image.extract_boot(entry, target)
            self.assertEqual(hashlib.sha1(target.read_bytes()).hexdigest(), digest)

    def test_reject_iso_with_wrong_region(self):
        make_iso(self.iso, b"SCES_504.59")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(DiscError, "SCES-50459"):
            builder.disc_report(self.iso, builder.profile_for(self.path, "downhill"))

    def test_reject_modified_elf_even_when_serial_matches(self):
        make_iso(self.iso)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(DiscError, "unmodified"):
            builder.disc_report(self.iso, builder.profile_for(self.path, "downhill"))

    def test_windows_uses_the_mac_profile_only_for_its_verified_edition(self):
        from windows_build import DISC_PROFILES, inspect

        for serial, port_id in DISC_PROFILES.items():
            with self.subTest(port=port_id):
                file_name = serial[:4] + "_" + serial[5:8] + "." + serial[8:]
                make_iso(self.iso, file_name.encode("ascii"))
                self.assertNotIn("profile", inspect(self.iso))
                port = builder.profile_for(self.path, port_id)
                algorithm, _ = port.fingerprint
                with ISOImage(self.iso) as image:
                    _, entry = image.boot()
                    digest = image.fingerprint(entry, algorithm)
                fixture = builder.Profile(port.directory, {**port.data, f"boot{algorithm.upper()}": digest})
                with patch("windows_build.profile_for", return_value=fixture):
                    picked = inspect(self.iso)
                    build = inspect(self.iso, self.path / "extracted.elf")
                    self.assertEqual(picked, build)
                    self.assertEqual(build["profile"], port_id)
                    self.assertEqual(build["bundleID"], port.data["bundleID"])

    def test_reject_non_ps2_boot_entry(self):
        make_iso(self.iso, boot=b"BOOT")
        with ISOImage(self.iso) as image, self.assertRaisesRegex(DiscError, "BOOT2"):
            image.boot()

    def test_reject_non_mips_elf(self):
        make_iso(self.iso, filetype=b"\x03\0")
        with ISOImage(self.iso) as image, self.assertRaisesRegex(DiscError, "MIPS"):
            image.boot()

    def test_reject_truncated_iso(self):
        make_iso(self.iso)
        with self.iso.open("r+b") as file:
            file.truncate(22 * SECTOR)
        with ISOImage(self.iso) as image, self.assertRaisesRegex(DiscError, "past the end"):
            image.boot()

    def test_catalog_playable_claims_are_scoped_to_tested_editions(self):
        catalog = json.loads((ROOT / "Backend/game-catalog.json").read_text())
        editions = {edition["serial"]: edition for game in catalog["games"] for edition in game["editions"]}
        tested = {serial: edition for serial, edition in editions.items() if "testedFPS" in edition}
        self.assertEqual(set(tested), {"SCUS-97177", "SLUS-21678", "SLUS-21171", "SLUS-21134", "SLUS-21087",
                                      "SLUS-21376", "SLUS-21050", "SLUS-21447", "SLUS-21586", "SLUS-20312",
                                      "SLUS-20370"})
        for edition in tested.values():
            self.assertEqual(edition["testedFPS"], 60)
            self.assertEqual(edition["testedDevice"], "iPhone 17 Pro")
            self.assertNotIn("testedBuild", edition)
        self.assertEqual(tested["SLUS-21134"]["testedMinFPS"], 30)
        self.assertEqual(tested["SLUS-21376"]["testedMinFPS"], 30)
        self.assertTrue(editions["SLUS-21858"]["testedUnstable"])
        self.assertEqual(editions["SLUS-21858"]["testedDevice"], "iPhone 17 Pro")
        for serial in ("SLES-55605", "SLUS-21858"):
            self.assertIn("observedGameplay", editions[serial])
            self.assertNotIn("testedFPS", editions[serial])
        for serial in ("SLUS-21267", "SLUS-21672"):
            self.assertIn("reportedIssue", editions[serial])

    def test_catalog_detection_does_not_enable_ipa_build(self):
        make_iso(self.iso, b"SCUS_973.99")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(builder.identify_disc(self.path, self.iso))
        disc = json.loads(output.getvalue().removeprefix("@@PS2 "))
        self.assertEqual((disc["serial"], disc["title"], disc["region"]), ("SCUS-97399", "God of War", "USA"))
        self.assertFalse(disc["ok"])
        self.assertNotIn("profile", disc)
        self.assertRegex(disc["bootSHA256"], r"^[a-f0-9]{64}$")
        self.assertEqual(disc["bootSize"], 128)

    def test_catalog_pal_edition_does_not_match_ntsc_recipe(self):
        make_iso(self.iso, b"SLES_535.24")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(builder.identify_disc(self.path, self.iso))
        disc = json.loads(output.getvalue().removeprefix("@@PS2 "))
        self.assertEqual(disc["title"], "Mortal Kombat: Shaolin Monks")
        self.assertTrue(disc["region"].startswith("PAL"))
        self.assertFalse(disc["ok"])

    def test_automatic_recipe_routes_an_iso_outside_the_catalog(self):
        make_iso(self.iso, b"SLUS_123.45")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            profile = builder.identify_disc(self.path, self.iso, auto_aot=True)
        disc = json.loads(output.getvalue().removeprefix("@@PS2 "))
        self.assertTrue(disc["ok"] and disc["automatic"])
        self.assertTrue(profile.generic and profile.exports_ipa)
        self.assertEqual(profile.data["bundle"], "SLUS-12345.app")
        self.assertEqual(profile.data["bootSHA256"], disc["bootSHA256"])
        self.assertTrue(profile.data["id"].endswith(disc["bootSHA256"][:16]))

    def test_catalog_disc_routes_unmatched_revisions_without_using_reference_links(self):
        for serial, repo in ((b"SLUS_216.78", "z3xox/BT3-Recomp"), (b"SLUS_216.72", "Ketbome/gh3-decomp"),
                             (b"SLUS_214.47", "noahbaxter/GHClassic"),
                             (b"SLUS_212.67", "victorgbd/NFSMW-Recompiled-Mobile")):
            with self.subTest(serial=serial):
                make_iso(self.iso, serial)
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    profile = builder.identify_disc(self.path, self.iso, auto_aot=True, references=True)
                disc = json.loads(output.getvalue().removeprefix("@@PS2 "))
                self.assertTrue(profile.generic and disc["ok"])
                self.assertEqual(disc["channel"], "general")
                self.assertEqual(disc["sources"][0]["url"], f"https://github.com/{repo}")

    def test_automatic_routing_accepts_every_catalog_edition(self):
        catalog = json.loads((ROOT / "Backend/game-catalog.json").read_text())
        for game in catalog["games"]:
            for edition in game["editions"]:
                serial = edition["serial"]
                name = serial[:4] + "_" + serial[5:8] + "." + serial[8:]
                with self.subTest(serial=serial), contextlib.redirect_stdout(io.StringIO()):
                    make_iso(self.iso, name.encode("ascii"))
                    profile = builder.identify_disc(self.path, self.iso, auto_aot=True)
                    self.assertTrue(profile.generic)
                    self.assertEqual((profile.data["title"], profile.data["region"]), (game["title"], edition["region"]))

    def test_auto_selects_exact_dedicated_pack_preview_or_research(self):
        ports = (("downhill", b"SCUS_971.77", "tested-pack"),
                 ("budokai-tenkaichi-3", b"SLUS_216.78", "tested-pack"),
                 ("god-of-war-ii", b"SCUS_974.81", "ios-preview"),
                 ("shaolin-monks", b"SLUS_210.87", "research"))
        for name, serial, channel in ports:
            with self.subTest(name=name):
                make_iso(self.iso, serial)
                port = builder.profile_for(self.path, name)
                algorithm, _ = port.fingerprint
                field = "bootSHA1" if algorithm == "sha1" else "bootSHA256"
                with ISOImage(self.iso) as image:
                    _, entry = image.boot()
                    data = {**port.data, field: image.fingerprint(entry, algorithm)}
                port = builder.Profile(port.directory, data)
                with (patch.object(builder, "installed_profiles", return_value=[port]),
                      contextlib.redirect_stdout(io.StringIO()) as output):
                    profile = builder.identify_disc(self.path, self.iso, auto_aot=True, references=True)
                self.assertEqual(profile, port)
                self.assertFalse(profile.generic)
                self.assertEqual(json.loads(output.getvalue().removeprefix("@@PS2 "))["channel"], channel)

    def test_shaolin_aot_takes_precedence_and_keeps_its_own_save_container(self):
        make_iso(self.iso, b"SLUS_210.87")
        new = builder.profile_for(self.path, "shaolin-monks-aot")
        old = builder.profile_for(self.path, "shaolin-monks")
        self.assertEqual(new.fingerprint, old.fingerprint)
        self.assertNotEqual(new.data["bundleID"], old.data["bundleID"])
        with ISOImage(self.iso) as image:
            _, entry = image.boot()
            fingerprint = image.fingerprint(entry, "sha256")
        candidates = [
            builder.Profile(port.directory, {**port.data, "bootSHA256": fingerprint})
            for port in (old, new)
        ]
        with patch.object(builder, "installed_profiles", return_value=candidates), contextlib.redirect_stdout(io.StringIO()):
            profile = builder.identify_disc(self.path, self.iso, auto_aot=True)
        self.assertEqual(profile, candidates[1])

    def test_most_wanted_cli_identify_enables_build_with_warning(self):
        make_iso(self.iso, b"SLUS_212.67")
        args = ["builder.py", "--workspace", str(self.path), "identify", "--iso", str(self.iso)]
        with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(builder.main(), 0)
        disc = json.loads(output.getvalue().removeprefix("@@PS2 "))
        self.assertTrue(disc["ok"])
        self.assertEqual((disc["title"], disc["serial"], disc["channel"]),
                         ("Need for Speed: Most Wanted", "SLUS-21267", "general"))
        self.assertIn("stalled at the logo", disc["note"])
        self.assertEqual(disc["sources"], [])

    def test_catalog_metadata_failure_preserves_automatic_routing(self):
        make_iso(self.iso, b"SLUS_212.67")
        with (patch.object(builder, "catalog_disc", side_effect=builder.BuildError("catalog unreadable")),
              contextlib.redirect_stdout(io.StringIO()) as output):
            profile = builder.identify_disc(self.path, self.iso, auto_aot=True)
        self.assertTrue(profile.generic)
        self.assertEqual(profile.data["title"], "SLUS-21267")
        self.assertIn("WARNING: catalog unreadable", output.getvalue())

    def test_nonstandard_ps2_boot_name_uses_a_local_disc_id(self):
        make_iso(self.iso, b"BOOT.ELF")
        with ISOImage(self.iso) as image:
            serial, entry = image.boot()
            self.assertEqual(serial, "PS2-" + image.fingerprint(entry, "sha256")[:12].upper())
        with contextlib.redirect_stdout(io.StringIO()):
            profile = builder.identify_disc(self.path, self.iso, auto_aot=True, references=True)
        self.assertTrue(profile.generic)
        self.assertEqual(profile.data["serial"], serial)

    def test_capture_distinguishes_boot_code_from_new_ram_code(self):
        from make_dynamic_iso import make_dynamic_iso
        make_dynamic_iso(self.iso)
        boot = self.path / "boot.elf"
        with ISOImage(self.iso) as disc:
            _, entry = disc.boot()
            disc.extract_boot(entry, boot)
        elf = boot.read_bytes()
        segments = code_segments(elf)
        address, offset, size = segments[0]
        snapshot = self.path / "ee-miss.bin"
        overlays = self.path / "overlays"
        overlays.mkdir()
        snapshot.write_bytes(struct.pack("<II", address, address) + elf[offset:offset + size])
        self.assertEqual(recover_entry(f"{address:x}", snapshot, elf, segments, overlays), f"{address:x}")
        pc = 0x220000
        snapshot.write_bytes(struct.pack("<4I", pc, pc, 0x03E00008, 0x2402002A))
        with contextlib.redirect_stdout(io.StringIO()):
            token = recover_entry(f"{pc:x}", snapshot, elf, segments, overlays)
        name, entry_offset = token.split(":")
        self.assertEqual(entry_offset, "0")
        self.assertEqual((overlays / f"{name}.bin").read_bytes(), snapshot.read_bytes()[8:])
        with self.assertRaisesRegex(builder.BuildError, "does not contain"):
            recover_entry(f"{pc + 4:x}", snapshot, elf, segments, overlays)

    def test_automatic_build_uses_game_hash_and_stops_on_recipe_failure(self):
        make_iso(self.iso, b"SLUS_212.67")
        with contextlib.redirect_stdout(io.StringIO()):
            profile = builder.identify_disc(self.path, self.iso, auto_aot=True)
        commands = []
        output = self.path / "Output"

        def fail_build(command, **kwargs):
            commands.append(command[3])
            manifest = Path(command[command.index("--profile") + 1])
            self.assertEqual(json.loads(manifest.read_text()), profile.data)
            self.assertEqual(Path(command[command.index("--cache") + 1]).parent.name, "generic")
            if command[3] == "build":
                self.assertEqual(kwargs["env"]["PS2APPLE_SHARED_IOS"], str(builder.BACKEND / "ios"))
                raise builder.BuildError("test compiler failure")

        with (patch.object(builder, "ensure_tools"), patch.object(builder, "run", side_effect=fail_build),
              contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(builder.BuildError, "test compiler failure")):
            builder.build(self.path, profile, self.iso, output)
        self.assertEqual(commands, ["prepare", "build"])
        self.assertFalse(output.exists())

    def test_reject_non_device_mach_o(self):
        for platform in (1, 7, 6):  # macOS, iOS Simulator, Mac Catalyst
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, "iPhoneOS"):
                check_macho(io.BytesIO(fixture_binary(platform)))
        with self.assertRaisesRegex(ValueError, "ARM64"):
            check_macho(io.BytesIO(fixture_binary(cpu=7)))

    def test_export_ipa_preserves_binary_mode_and_refuses_overwrite(self):
        app = fixture_app(self.path)
        target = self.path / "Output Directory" / "Fixture.ipa"
        export_ipa(app, target, "net.example.fixture")
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()),
                             {"Payload/Fixture.app/Info.plist", "Payload/Fixture.app/Fixture"})
            self.assertTrue(archive.getinfo("Payload/Fixture.app/Fixture").external_attr >> 16 & 0o111)
        with self.assertRaises(FileExistsError):
            export_ipa(app, target, "net.example.fixture")

        self.assertEqual(list(target.parent.iterdir()), [target])

    def test_native_ipa_requires_shared_ui(self):
        app = fixture_app(self.path)
        (app / "Fixture").write_bytes(fixture_binary(shared=False))
        output = self.path / "Fixture.ipa"
        with self.assertRaisesRegex(ValueError, "shared launcher"):
            export_ipa(app, output, "net.example.fixture")
        self.assertFalse(output.exists())

    def test_downhill_frontend_requires_its_own_marker(self):
        app = fixture_app(self.path)
        output = self.path / "Fixture.ipa"
        with self.assertRaisesRegex(ValueError, "downhill launcher"):
            export_ipa(app, output, "net.example.fixture", frontend="downhill")
        (app / "Fixture").write_bytes(fixture_binary(shared=False) + DOWNHILL_UI)
        with self.assertRaisesRegex(ValueError, "shared launcher"):
            export_ipa(app, output, "net.example.fixture")
        export_ipa(app, output, "net.example.fixture", frontend="downhill")
        self.assertTrue(output.is_file())

    def test_reject_disc_in_ipa(self):
        app = fixture_app(self.path)
        (app / "game.iso").write_bytes(b"synthetic")
        target = self.path / "Fixture.ipa"
        with self.assertRaisesRegex(ValueError, "disc or BIOS"):
            export_ipa(app, target, "net.example.fixture")
        self.assertFalse(target.exists())
        self.assertEqual(list(self.path.glob("*.partial")), [])

    def test_cancelled_workspace_releases_lock(self):
        with builder.workspace_lock(self.path):
            with self.assertRaisesRegex(builder.BuildError, "Another build"):
                with builder.workspace_lock(self.path):
                    pass
        with builder.workspace_lock(self.path):
            self.assertTrue((self.path / "job.lock").is_file())

    def test_custom_profile_runs_same_backend_and_exports(self):
        digest = make_iso(self.iso, serial=b"SLUS_123.45")
        port = self.path / "Profiles/fixture"
        port.mkdir(parents=True)
        manifest = dict(builder.profile_for(self.path, "downhill").data, id="fixture",
                        serial="SLUS-12345", bootSHA1=digest, bundle="Fixture.app",
                        bundleID="net.example.fixture", frontend="shared", channel="dedicated")
        (port / "profile.json").write_text(json.dumps(manifest))
        (port / "build.py").write_text(f"""
import argparse
import plistlib
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("action")
p.add_argument("--profile")
p.add_argument("--cache")
p.add_argument("--iso", type=Path)
p.add_argument("--elf", type=Path)
p.add_argument("--app", type=Path)
args = p.parse_args()
if args.action == "build":
    assert args.iso.is_file()
    assert args.elf.read_bytes().startswith(b"\\x7fELF")
    args.app.mkdir()
    with (args.app / "Info.plist").open("wb") as stream:
        plistlib.dump({{"CFBundleExecutable": "Fixture", "CFBundleIdentifier": "net.example.fixture",
                        "CFBundlePackageType": "APPL", "CFBundleSupportedPlatforms": ["iPhoneOS"]}}, stream)
    (args.app / "Fixture").write_bytes({fixture_binary()!r})
    (args.app / "Fixture").chmod(0o755)
""")
        destination = self.path / "Output With Spaces"
        with contextlib.redirect_stdout(io.StringIO()) as result:
            builder.identify_disc(self.path, self.iso)
        match = json.loads(result.getvalue().removeprefix("@@PS2 "))
        self.assertEqual(match["profile"], "fixture")
        self.assertTrue(match["ok"])
        log = io.StringIO()
        args = ["builder.py", "--workspace", str(self.path), "build", "--profile", "fixture", "--iso", str(self.iso),
                "--output", str(destination)]
        with patch.object(builder, "ensure_tools"), patch.object(builder, "stage_ps2apple_icon") as stage_icon, \
             patch.dict(builder.TOOLS, {"python@3.12": Path(sys.executable)}), \
             patch.object(sys, "argv", args), patch.object(builder.signal, "signal"):
            with contextlib.redirect_stdout(log):
                self.assertEqual(builder.main(), 0)
        stage_icon.assert_called_once()
        self.assertEqual(stage_icon.call_args.args[0].name, "Fixture.app")
        artifacts = list(destination.glob("*.ipa"))
        self.assertEqual(len(artifacts), 1)
        self.assertIn('"kind": "artifact"', log.getvalue())
        self.assertEqual(list((self.path / "Jobs").iterdir()), [])

    def test_shaolin_ipa_requires_the_exact_boot_sha256(self):
        make_iso(self.iso, serial=b"SLUS_210.87")
        mksm = builder.profile_for(self.path, "shaolin-monks")
        self.assertTrue(mksm.exports_ipa)
        self.assertEqual(mksm.fingerprint[0], "sha256")
        with contextlib.redirect_stdout(io.StringIO()) as result:
            builder.identify_disc(self.path, self.iso)
        match = json.loads(result.getvalue().removeprefix("@@PS2 "))
        self.assertEqual(match["serial"], "SLUS-21087")
        self.assertFalse(match["ok"])
        self.assertNotIn("profile", match)

        destination = self.path / "Port Output"
        with patch.object(builder, "ensure_tools") as install, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(DiscError, "SHA-256"):
                builder.build(self.path, mksm, self.iso, destination)
            install.assert_not_called()
        self.assertFalse(destination.exists())

    def test_source_recipe_matches_exact_disc_and_generates_its_own_artifact(self):
        make_iso(self.iso, serial=b"SLUS_210.87")
        with ISOImage(self.iso) as image:
            _, entry = image.boot()
            digest = image.fingerprint(entry, "sha256")
        source_profile = dict(builder.profile_for(self.path, "shaolin-monks").data,
                              id="fixture-source", bootSHA256=digest, output="sources")
        port = self.path / "Profiles/fixture-source"
        port.mkdir(parents=True)
        (port / "profile.json").write_text(json.dumps(source_profile))
        (port / "prepare.py").write_text("""
import argparse
import hashlib
import json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('action')
p.add_argument('--profile', type=Path)
p.add_argument('--cache')
p.add_argument('--elf', type=Path)
p.add_argument('--output', type=Path)
args = p.parse_args()
if args.action == 'generate':
    manifest = json.loads(args.profile.read_text())
    assert hashlib.sha256(args.elf.read_bytes()).hexdigest() == manifest['bootSHA256']
    args.output.mkdir()
    (args.output / 'PS2Apple-Port.json').write_text(json.dumps(manifest))
""")
        with contextlib.redirect_stdout(io.StringIO()) as result:
            builder.identify_disc(self.path, self.iso)
        self.assertEqual(json.loads(result.getvalue().removeprefix("@@PS2 "))["profile"], "fixture-source")

        destination = self.path / "Port Output With Spaces"
        profile = builder.profile_for(self.path, "fixture-source")
        with self.assertRaisesRegex(builder.BuildError, "runtime before IPA export"):
            builder.build(self.path, profile, self.iso, destination)
        log = io.StringIO()
        with patch.object(builder, "ensure_tools"), patch.dict(builder.TOOLS, {"python@3.12": Path(sys.executable)}):
            with contextlib.redirect_stdout(log):
                builder.generate_sources(self.path, builder.profile_for(self.path, "fixture-source"), self.iso, destination)
        self.assertEqual(len(list(destination.glob("*/PS2Apple-Port.json"))), 1)
        self.assertFalse(list(destination.rglob("*.ipa")))
        self.assertFalse(list(destination.rglob("*.elf")))
        self.assertIn('"kind": "sources"', log.getvalue())
        self.assertEqual(list((self.path / "Jobs").iterdir()), [])

    def test_update_checker_compares_numeric_versions(self):
        response = io.BytesIO(b'{"tag_name":"v0.7.15"}')
        with patch.object(builder.urllib.request, "urlopen", return_value=response):
            with contextlib.redirect_stdout(io.StringIO()) as result:
                builder.check_update("skuza-oss/downhill-revived", "0.7.14", "Port source")
        self.assertIn("update available", result.getvalue())


if __name__ == "__main__":
    unittest.main()
