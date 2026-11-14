"""Run the Mac builder for a Windows client through a user-selected shared folder."""
from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
PROTOCOL = 1
JOB_ID = re.compile(r"[0-9a-f]{32}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ACTIONS = {"check", "setup-aot", "updates", "build"}


def publish(path: Path, **data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ps2apple-", delete=False) as stream:
        stage = Path(stream.name)
        try:
            stream.write(json.dumps(data).encode("utf-8"))
            stream.close()
            os.chmod(stage, 0o644)
            stage.replace(path)
        finally:
            stage.unlink(missing_ok=True)


def arguments(job: Path, request: dict, workspace: Path) -> list[str]:
    action = request.get("action")
    if request.get("protocol") != PROTOCOL or not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("Unsupported Windows build request.")
    args = ["/bin/bash", str(BACKEND / "launch.sh"), "--workspace", str(workspace), action]
    if action == "updates":
        repo = request.get("builderRepo")
        if repo:
            if not isinstance(repo, str) or not REPO.fullmatch(repo):
                raise ValueError("Enter a GitHub release repository as OWNER/REPO.")
            args += ["--builder-repo", repo]
    elif action == "build":
        iso = job / "disc.iso"
        if iso.is_symlink() or not iso.is_file():
            raise ValueError("The build request is missing its ISO.")
        output = job / "output"
        output.mkdir()
        args += ["--iso", str(iso), "--output", str(output)]
    return args


def run_job(job: Path, workspace: Path, refresh, stopping):
    queued, claimed = job / "request.json", job / "running.json"
    log_path, result = job / "build.log", job / "result.json"
    code, detail, artifact = 1, "", None
    try:
        if queued.is_symlink() or queued.stat().st_size > 4096:
            raise ValueError("Invalid Windows build request file.")
        queued.replace(claimed)
        request = json.loads(claimed.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("Invalid Windows build request.")
        if (job / "cancel").exists():
            code = 130
            return
        args = arguments(job, request, workspace)
        with log_path.open("xb") as log:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while proc.poll() is None and not stopping() and not (job / "cancel").exists():
                    refresh()
                    time.sleep(.25)
                code = proc.returncode if proc.returncode is not None else 130
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
        if code == 0 and request["action"] == "build":
            outputs = [path for path in (job / "output").glob("*.ipa")
                       if path.is_file() and not path.is_symlink()]
            if len(outputs) != 1:
                raise ValueError("The Mac builder did not return an IPA.")
            artifact = outputs[0].name
    except (OSError, ValueError) as error:
        code, detail = 1, str(error)
    finally:
        (job / "disc.iso").unlink(missing_ok=True)
        publish(result, exitCode=code, detail=detail, artifact=artifact)


def serve(folder: Path, workspace: Path):
    root = folder.expanduser().resolve(strict=True)
    jobs = root / "PS2Apple-Jobs"
    if jobs.is_symlink():
        raise ValueError("Choose a shared folder with a local PS2Apple-Jobs directory.")
    jobs.mkdir(exist_ok=True)
    host = jobs / "host.json"
    stopped = False
    next_refresh = 0.0
    version = (BACKEND / "VERSION").read_text().strip()

    def stop(*_):
        nonlocal stopped
        stopped = True

    def refresh():
        nonlocal next_refresh
        if time.monotonic() >= next_refresh:
            publish(host, protocol=PROTOCOL, version=version, heartbeat=int(time.time()))
            next_refresh = time.monotonic() + 2

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (jobs / "host.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("A PS2Apple Mac host is already serving this folder.") from error
        try:
            for job in jobs.iterdir():
                if job.is_dir() and not job.is_symlink() and JOB_ID.fullmatch(job.name):
                    if (job / "running.json").exists() and not (job / "result.json").exists():
                        (job / "disc.iso").unlink(missing_ok=True)
                        publish(job / "result.json", exitCode=1, detail="The previous Mac host stopped before finishing.")
            refresh()
            print("Windows build host ready.", flush=True)
            while not stopped:
                refresh()
                for job in sorted(jobs.iterdir()):
                    if stopped:
                        break
                    if (JOB_ID.fullmatch(job.name) and not job.is_symlink() and job.is_dir()
                            and (job / "request.json").is_file() and not (job / "result.json").exists()):
                        run_job(job, workspace, refresh, lambda: stopped)
                time.sleep(.25)
        finally:
            host.unlink(missing_ok=True)
