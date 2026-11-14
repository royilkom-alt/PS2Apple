#!/usr/bin/env python3
"""The desktop app's CLI backend. A port profile owns only the title-specific build."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from disc import DiscError, ISOImage
from ipa import FRONTENDS, MH_OBJECT, check_macho, export_ipa, stage_ps2apple_icon
from engine_source import ENGINE_REVISION, ENGINE_VERSION, RELEASE_VERSION
from port_search import port_references

BACKEND = Path(__file__).resolve().parent
WORKSPACE = Path.home() / "Library/Application Support/PS2 Native Easy Builder"
BREW = Path("/opt/homebrew/bin/brew")
TOOLS = {
    "python@3.12": Path("/opt/homebrew/opt/python@3.12/bin/python3.12"),
    "cmake": Path("/opt/homebrew/bin/cmake"),
    "ninja": Path("/opt/homebrew/bin/ninja"),
    "rustup": Path("/opt/homebrew/opt/rustup/bin/rustup"),
}
PORT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MIN_IOS = "18.0"


class BuildError(RuntimeError):
    pass


def event(kind: str, **data):
    print("@@PS2 " + json.dumps({"kind": kind, **data}, ensure_ascii=True), flush=True)


def progress(value: float, detail: str):
    print(detail, flush=True)
    event("progress", value=value, detail=detail)


def run(command, *, cwd=None, env=None, timeout=None):
    args = [str(x) for x in command]
    print("+ " + shlex.join(args), flush=True)
    process = subprocess.run(args, cwd=cwd, env=env, timeout=timeout)
    if process.returncode:
        raise BuildError(f"{Path(args[0]).name} exited with status {process.returncode}. See the preceding log.")


def capture(command, *, input=None):
    return subprocess.run(
        [str(x) for x in command], input=input, capture_output=True, text=True, errors="replace"
    )


def output(command, *, cwd=None, env=None):
    args = [str(x) for x in command]
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise BuildError(f"{shlex.join(args)} exited with status {result.returncode}\n{result.stderr.strip()}")
    return result.stdout.strip()


def checkout(destination: Path, repo: str, revision: str, patch: Path | None = None):
    if destination.is_dir():
        head = output(["git", "rev-parse", "HEAD"], cwd=destination)
        origin = output(["git", "config", "--local", "--get", "remote.origin.url"], cwd=destination)
        if head != revision or origin != repo:
            raise BuildError(f"Unexpected cached Git revision in {destination}. Move the cache aside and retry.")
        if patch:
            reverse = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)], cwd=destination)
            if reverse.returncode:
                raise BuildError(f"The cached iOS source was modified in {destination}. Move the cache aside and retry.")
        return
    with tempfile.TemporaryDirectory(prefix="fetch-", dir=destination.parent) as temporary:
        clone = Path(temporary) / "source"
        run(["git", "init", "--quiet", clone])
        run(["git", "remote", "add", "origin", repo], cwd=clone)
        run(["git", "fetch", "--no-tags", "--depth=1", "origin", revision], cwd=clone)
        run(["git", "-c", "advice.detachedHead=false", "checkout", "--detach", "FETCH_HEAD"], cwd=clone)
        if output(["git", "rev-parse", "HEAD"], cwd=clone) != revision:
            raise BuildError("Git returned a different revision than the port profile requested.")
        if patch:
            run(["git", "apply", "--check", patch], cwd=clone)
            run(["git", "apply", patch], cwd=clone)
        clone.rename(destination)


@dataclass(frozen=True)
class Profile:
    directory: Path
    data: dict

    @property
    def recipe(self):
        return self.directory / ("build.py" if self.exports_ipa else "prepare.py")

    @property
    def exports_ipa(self) -> bool:
        return self.data.get("output", "ipa") == "ipa"

    @property
    def generic(self) -> bool:
        return self.directory == BACKEND / "ports" / "generic"

    @property
    def channel(self) -> str:
        return self.data.get("channel", "general" if self.generic else "dedicated")

    @property
    def fingerprint(self) -> tuple[str, str]:
        if "bootSHA256" in self.data:
            return "sha256", self.data["bootSHA256"]
        return "sha1", self.data["bootSHA1"]


def profile_for(workspace: Path, port_id: str) -> Profile:
    if not PORT_ID.fullmatch(port_id):
        raise BuildError("Invalid port profile ID.")
    bundled = BACKEND / "ports" / port_id
    directory = bundled if (bundled / "profile.json").is_file() else workspace / "Profiles" / port_id
    try:
        data = json.loads((directory / "profile.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1 or data.get("id") != port_id:
            raise ValueError("Unexpected profile schema or ID.")
        output = data.get("output", "ipa")
        if output not in ("ipa", "sources"):
            raise ValueError("Unknown port output type.")
        if data.get("frontend", "shared") not in FRONTENDS:
            raise ValueError("Unknown port launcher.")
        if data.get("channel", "dedicated") not in ("dedicated", "tested-pack", "ios-preview", "research"):
            raise ValueError("Unknown port channel.")
        patterns = {
            "serial": r"[A-Z]{4}-[0-9]{5}",
            "revision": r"[a-f0-9]{40}",
            "upstream": REPO,
            "version": r"\d+\.\d+\.\d+",
        }
        if output == "sources" or "bootSHA256" in data:
            patterns["bootSHA256"] = r"[a-f0-9]{64}"
        if output == "ipa":
            patterns["bundle"] = r"[A-Za-z0-9_-]+\.app"
            patterns["bundleID"] = r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
            if "bootSHA256" not in data:
                patterns["bootSHA1"] = r"[a-f0-9]{40}"
        for key, pattern in patterns.items():
            if not re.fullmatch(pattern, data[key]):
                raise ValueError(f"Invalid field {key}.")
        if not all(isinstance(data[k], str) and data[k].strip() for k in ("title", "region", "stage")):
            raise ValueError("Missing profile display fields.")
        profile = Profile(directory, data)
        if not profile.recipe.is_file():
            raise ValueError(f"The profile needs {profile.recipe.name}.")
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise BuildError(f"Port profile {port_id} is missing or invalid.") from error
    return profile


def installed_profiles(workspace: Path):
    """Read installed profiles in stable order, with bundled IDs taking precedence."""
    seen = set()
    for root in (BACKEND / "ports", workspace / "Profiles"):
        try:
            paths = sorted(root.iterdir())
        except OSError:
            continue
        for path in paths:
            if not (path / "profile.json").is_file() or path.name in seen:
                continue
            seen.add(path.name)
            try:
                yield profile_for(workspace, path.name)
            except BuildError:
                continue


@contextmanager
def workspace_lock(workspace: Path):
    import fcntl

    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "job.lock").open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BuildError("Another build or dependency installation is using this workspace.") from error
        yield


def missing_tools(*, require_rust: bool = False) -> list[str]:
    missing = []
    for name, path in TOOLS.items():
        if name == "rustup" and not require_rust:
            continue
        try:
            working = os.access(path, os.X_OK) and capture([path, "--version"]).returncode == 0
        except OSError:
            working = False
        if not working:
            missing.append(name)
    return missing


def check_tools(*, require_rust: bool = False) -> dict[str, tuple[bool, str]]:
    apple = sys.platform == "darwin" and os.uname().machine == "arm64"
    available = BREW.is_file() and os.access(BREW, os.X_OK)
    missing = missing_tools(require_rust=require_rust)
    tool_names = "Python, CMake, Ninja and Rustup" if require_rust else "Python, CMake and Ninja"
    checks = {
        "arm64": (apple, "Apple Silicon / ARM64" if apple else f"{sys.platform} / {os.uname().machine}"),
        "homebrew": (available, "/opt/homebrew/bin/brew" if available else "Apple Silicon Homebrew is required."),
        "xcode": (False, "Install full Xcode and open it once."),
        "sdk": (False, "An active iPhoneOS device SDK is required."),
        "compiler": (False, "The ARM64 iOS compiler probe requires Xcode and iPhoneOS SDK."),
        "tools": (not missing, f"{tool_names} available" if not missing else ", ".join(missing)),
    }
    if not apple or not os.environ.get("DEVELOPER_DIR"):
        return checks
    xcode = capture(["/usr/bin/xcodebuild", "-version"])
    first_launch = capture(["/usr/bin/xcodebuild", "-checkFirstLaunchStatus"])
    version = " ".join(xcode.stdout.strip().splitlines())
    if xcode.returncode == 0 and first_launch.returncode == 0:
        checks["xcode"] = True, version
    else:
        checks["xcode"] = False, (xcode.stderr or first_launch.stderr or "Complete Xcode first launch.").strip()[-1000:]
    sdk = capture(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
    sdk_path = sdk.stdout.strip()
    if sdk.returncode or not Path(sdk_path).is_dir() or not sdk_path:
        checks["sdk"] = False, (sdk.stderr or checks["sdk"][1]).strip()[-1000:]
        return checks
    sdk_version = capture(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-version"])
    try:
        current = tuple(int(part) for part in sdk_version.stdout.strip().split(".")[:2])
        minimum = tuple(map(int, MIN_IOS.split(".")))
        if sdk_version.returncode or current < minimum:
            raise ValueError()
    except ValueError:
        checks["sdk"] = False, f"An iPhoneOS SDK version {MIN_IOS} or newer is required."
        return checks
    checks["sdk"] = True, Path(sdk_path).name
    source = "#include <TargetConditionals.h>\n#if !TARGET_OS_IOS || TARGET_OS_SIMULATOR || !__aarch64__\n#error Not iOS ARM64\n#endif\nint probe(void) { return 0; }\n"
    with tempfile.TemporaryDirectory(prefix="ps2-compiler-") as temporary:
        obj = Path(temporary) / "probe.o"
        compiled = capture(
            ["/usr/bin/xcrun", "--sdk", "iphoneos", "clang", "-target", f"arm64-apple-ios{MIN_IOS}",
             "-isysroot", sdk_path, "-x", "c", "-c", "-o", obj, "-"], input=source
        )
        if compiled.returncode:
            checks["compiler"] = False, compiled.stderr.strip()[-1000:] or "Xcode Clang could not compile for iOS ARM64."
        else:
            try:
                with obj.open("rb") as stream:
                    check_macho(stream, MH_OBJECT)
                checks["compiler"] = True, "Xcode Clang emitted an iPhoneOS ARM64 object"
            except (OSError, ValueError) as error:
                checks["compiler"] = False, str(error)
    return checks


def show_checks(checks):
    for key, (ok, detail) in checks.items():
        print(f"{'OK' if ok else 'CHECK'} {key} · {detail}", flush=True)
        event("check", key=key, ok=ok, detail=detail)


def ensure_tools(*, require_rust: bool = False):
    checks = check_tools(require_rust=require_rust)
    show_checks(checks)
    if not checks["arm64"][0]:
        raise BuildError("This build requires macOS on an Apple Silicon Mac.")
    if not checks["xcode"][0] and os.environ.get("DEVELOPER_DIR"):
        run(["/usr/bin/xcodebuild", "-runFirstLaunch"])
        checks = check_tools(require_rust=require_rust)
        show_checks(checks)
    for key in ("xcode", "sdk", "compiler", "homebrew"):
        if not checks[key][0]:
            raise BuildError(checks[key][1])
    if not checks["tools"][0]:
        for formula in missing_tools(require_rust=require_rust):
            present = capture([BREW, "list", "--versions", formula]).returncode == 0
            run([BREW, "reinstall" if present else "install", formula])
        checks = check_tools(require_rust=require_rust)
        show_checks(checks)
        if not checks["tools"][0]:
            raise BuildError("Some Homebrew tools are still unavailable: " + checks["tools"][1])


def identify_disc(workspace: Path, iso: Path, *, auto_aot: bool = False, references: bool = False):
    """Prefer an exact IPA recipe and fall back to AOT for any valid PS2 disc."""
    with ISOImage(iso) as image:
        serial, entry = image.boot()
        try:
            title, edition = catalog_disc(serial)
        except BuildError as error:
            print(f"WARNING: {error}", flush=True)
            title, edition = None, {}
        region = edition.get("region")
        sources = port_references(edition) if references else []
        digest = image.fingerprint(entry, "sha256")
        disc = {"serial": serial, "bootSHA256": digest, "bootSize": entry.size, "sources": sources,
                "note": edition.get("compatibilityNote")}
        candidates = [p for p in installed_profiles(workspace) if p.data["serial"] == serial]
        candidates.sort(key=lambda profile: (not profile.exports_ipa, profile.channel == "research"))
        hashes = {"sha256": digest}
        for profile in candidates:
            if auto_aot and not profile.exports_ipa:
                continue
            algorithm, expected = profile.fingerprint
            if algorithm not in hashes:
                hashes[algorithm] = image.fingerprint(entry, algorithm)
            if hashes[algorithm] == expected:
                detail = {
                    "tested-pack": "Dedicated game pack · Boot ELF verified",
                    "ios-preview": "Audited desktop port · iPhone gameplay pending",
                    "research": "Research port · iPhone gameplay unverified",
                }.get(profile.channel, "Dedicated port · Boot ELF verified")
                event("disc", **disc, profile=profile.data["id"], ok=True, channel=profile.channel,
                      title=profile.data["title"], region=profile.data["region"],
                      detail=f"{serial} · {detail}")
                return profile
        if auto_aot:
            data = {"schema": 1, "id": f"aot-{ENGINE_VERSION}-{serial.lower()}-{digest[:16]}",
                    "title": title or serial, "region": region or "PS2", "serial": serial, "bootSHA256": digest,
                    "revision": ENGINE_REVISION, "output": "ipa", "bundle": f"{serial}.app",
                    "bundleID": f"net.isolution.ps2apple.{serial.lower()}"}
            event("disc", **disc, title=data["title"], region=data["region"], ok=True, automatic=True, channel="general",
                  profile=data["id"], detail=f"{serial} · Boot ELF verified · AOT test build")
            return Profile(BACKEND / "ports" / "generic", data)
        event("disc", **disc, title=title, region=region, ok=False,
              detail=f"{serial} · No matching dedicated profile for this boot ELF.")


def catalog_disc(serial: str) -> tuple[str | None, dict]:
    try:
        catalog = json.loads((BACKEND / "game-catalog.json").read_text(encoding="utf-8"))
        if catalog["schema"] != 1:
            raise ValueError("Unexpected catalog schema.")
        for game in catalog["games"]:
            for edition in game["editions"]:
                if edition["serial"] == serial:
                    return game["title"], edition
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise BuildError("The bundled game catalog could not be read. Disc identification will use the serial.") from error
    return None, {}


def disc_report(iso: Path, profile: Profile, extract: Path | None = None):
    with ISOImage(iso) as image:
        serial, entry = image.boot()
        event("disc", serial=serial, detail=f"Detected {serial}")
        if serial != profile.data["serial"]:
            raise DiscError(f"This ISO is {serial}. The selected profile requires {profile.data['serial']}.")
        algorithm, expected = profile.fingerprint
        actual = image.fingerprint(entry, algorithm)
        if actual != expected:
            label = "SHA-256" if algorithm == "sha256" else "SHA-1"
            raise DiscError(f"{serial} boot ELF {label} is {actual}. This port requires the unmodified supported revision.")
        if extract:
            image.extract_boot(entry, extract)
    event("disc", serial=serial, profile=profile.data["id"], ok=True, automatic=profile.generic, channel=profile.channel,
          detail=f"{serial} · Boot ELF verified")


def recipe_command(profile: Profile, workspace: Path, action: str, manifest: Path | None = None):
    cache_id = "generic" if profile.generic or profile.channel == "ios-preview" else profile.data["id"]
    return [
        TOOLS["python@3.12"], "-u", profile.recipe, action, "--profile", manifest or profile.directory / "profile.json",
        "--cache", workspace / "Ports" / cache_id / profile.data["revision"],
    ]


def setup(workspace: Path, profile: Profile):
    with workspace_lock(workspace):
        progress(.08, "Checking Xcode and installing missing tools")
        ensure_tools(require_rust=profile.data["id"] == "downhill")
        progress(.24, "Preparing the selected port's dependencies")
        run(recipe_command(profile, workspace, "prepare"))
        progress(1, "Dependencies ready")


def build(workspace: Path, profile: Profile, iso: Path, output: Path):
    if not profile.exports_ipa:
        raise BuildError(f"{profile.data['title']} needs a playable iPhoneOS runtime before IPA export. Use Generate Port Sources.")
    progress(.01, "Checking the ISO and output paths")
    iso = iso.expanduser().resolve()
    output = output.expanduser().resolve()
    with workspace_lock(workspace):
        jobs = workspace / "Jobs"
        jobs.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=profile.data["id"] + "-", dir=jobs) as directory:
            stage = Path(directory)
            elf = stage / "boot.elf"
            manifest = None
            if profile.generic:
                manifest = stage / "profile.json"
                manifest.write_text(json.dumps(profile.data), encoding="utf-8")
            progress(.04, "Validating the selected PS2 disc")
            disc_report(iso, profile, extract=elf)
            progress(.12, "Checking and repairing the toolchain")
            ensure_tools(require_rust=profile.data["id"] == "downhill")
            progress(.20, "Preparing the PS2Apple compiler" if profile.generic else "Preparing the pinned port dependencies")
            run(recipe_command(profile, workspace, "prepare", manifest))
            result = stage / profile.data["bundle"]
            progress(.33, "Building the game for iPhoneOS ARM64")
            run(recipe_command(profile, workspace, "build", manifest) + ["--iso", iso, "--elf", elf, "--app", result],
                env=dict(os.environ, PS2APPLE_SHARED_IOS=str(BACKEND / "ios")))
            progress(.91, "Packaging the PS2Apple icon and verifying the IPA")
            stage_ps2apple_icon(result, MIN_IOS)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = output / f"{result.stem}-{profile.data['serial']}-{stamp}-{uuid.uuid4().hex[:6]}.ipa"
            export_ipa(result, target, profile.data["bundleID"], frontend=profile.data.get("frontend", "shared"))
            detail = "Unsigned iPhoneOS ARM64 IPA"
            if profile.channel in ("general", "ios-preview", "research"):
                detail += " · Device gameplay unverified"
            event("artifact", path=str(target), detail=detail)
            progress(1, "IPA exported and verified")


def generate_sources(workspace: Path, profile: Profile, iso: Path, output: Path):
    if profile.exports_ipa:
        raise BuildError("This profile exports a Native IPA. Use Build Native IPA.")
    iso = iso.expanduser().resolve()
    output = output.expanduser().resolve()
    with workspace_lock(workspace):
        jobs = workspace / "Jobs"
        jobs.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=profile.data["id"] + "-", dir=jobs) as directory:
            elf = Path(directory) / "boot.elf"
            progress(.04, "Validating the PS2 disc and extracting the boot executable")
            disc_report(iso, profile, extract=elf)
            progress(.12, "Checking the build toolchain")
            ensure_tools()
            progress(.20, "Preparing the static recompiler")
            run(recipe_command(profile, workspace, "prepare"))
            output.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = output / f"{profile.data['id']}-{profile.data['serial']}-{stamp}-{uuid.uuid4().hex[:6]}"
            progress(.45, "Generating the game's C++ port sources")
            run(recipe_command(profile, workspace, "generate") + ["--elf", elf, "--output", target])
            if not (target / "PS2Apple-Port.json").is_file():
                raise BuildError("The port recipe did not finish generating its source workspace.")
            event("sources", path=str(target), detail="C++ port source workspace")
            progress(1, "Port sources generated")


def check_update(repo: str, current: str, label: str):
    if not REPO.fullmatch(repo):
        raise BuildError("Enter a GitHub release repository as OWNER/REPO.")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"User-Agent": "PS2Apple", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        release = json.load(response)
    tag = release.get("tag_name") if isinstance(release, dict) else None
    if not isinstance(tag, str):
        raise BuildError("The release feed returned no version tag.")
    latest = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag)
    installed = tuple(map(int, current.split(".")))
    newer = latest is not None and tuple(map(int, latest.groups())) > installed
    url = f"https://github.com/{repo}/releases"
    detail = f"{label} · installed v{current} · latest {tag}"
    if newer:
        detail += " · update available"
    print(detail, flush=True)
    event("update", detail=detail, url=url)


def check_updates(profile: Profile | None, builder_repo: str | None):
    sources = [(profile.data["upstream"], profile.data["version"], "Port source")] if profile else []
    if builder_repo:
        sources.append((builder_repo, RELEASE_VERSION, "Builder"))
    if not sources:
        event("update", detail=f"PS2Apple {RELEASE_VERSION} installed. Enter a release repository to check for a newer builder.")
        return
    failures = 0
    for repo, current, label in sources:
        try:
            check_update(repo, current, label)
        except (BuildError, OSError, ValueError, KeyError) as error:
            failures += 1
            event("update", detail=f"{label} update check failed · {error}")
    if failures == len(sources):
        raise BuildError("Update information is unavailable. See the log or try again later.")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("check")
    actions.add_parser("setup-tools")
    actions.add_parser("setup-aot")
    actions.add_parser("identify").add_argument("--iso", type=Path, required=True)
    if (BACKEND / "windows_bridge.py").is_file():
        actions.add_parser("serve-windows").add_argument("--folder", type=Path, required=True)
    for action in ("inspect", "setup", "build", "generate", "updates"):
        sub = actions.add_parser(action)
        sub.add_argument("--profile", default="auto" if action in ("build", "updates") else "downhill")
        if action in ("inspect", "build", "generate"):
            sub.add_argument("--iso", type=Path, required=True)
        if action in ("build", "generate"):
            sub.add_argument("--output", type=Path, required=True)
        if action == "updates":
            sub.add_argument("--builder-repo")
    return parser.parse_args()


def cancelled(_signal, _frame):
    raise SystemExit(130)


def main() -> int:
    signal.signal(signal.SIGTERM, cancelled)
    args = parse_args()
    try:
        if args.action == "serve-windows":
            from windows_bridge import serve
            serve(args.folder, args.workspace)
        elif args.action == "check":
            show_checks(check_tools())
        elif args.action in ("setup-tools", "setup-aot"):
            with workspace_lock(args.workspace):
                ensure_tools()
                if args.action == "setup-aot":
                    progress(.24, "Preparing the automatic AOT compiler and Metal shader runtime")
                    run([TOOLS["python@3.12"], "-u", BACKEND / "ports/generic/build.py", "prepare",
                         "--cache", args.workspace / "Ports" / "generic" / ENGINE_REVISION])
                    progress(1, "PS2Apple build dependencies ready")
        elif args.action == "identify":
            identify_disc(args.workspace, args.iso, auto_aot=True)
        elif args.action == "build" and args.profile == "auto":
            profile = identify_disc(args.workspace, args.iso, auto_aot=True)
            build(args.workspace, profile, args.iso, args.output)
        elif args.action == "updates" and args.profile == "auto":
            check_updates(None, args.builder_repo)
        else:
            profile = profile_for(args.workspace, args.profile)
            if args.action == "inspect":
                disc_report(args.iso, profile)
            elif args.action == "setup":
                setup(args.workspace, profile)
            elif args.action == "build":
                build(args.workspace, profile, args.iso, args.output)
            elif args.action == "generate":
                generate_sources(args.workspace, profile, args.iso, args.output)
            else:
                check_updates(profile, args.builder_repo)
    except (BuildError, DiscError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", flush=True)
        event("error", detail=str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
