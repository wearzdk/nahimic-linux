"""Application entry point and first-run setup."""
import argparse
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
from desktop_audio import SUPPORTED_SPEAKERS, atomic_json, pulse, speaker_endpoint, supported_speaker, runtime_files, runtime_generation

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "nahimic-linux"
RUNTIME = DATA / "runtime"
SHARE = Path("/usr/share/nahimic-linux")
MARKER = "nahimic-linux-v1\n"


def detect():
    sinks = json.loads(pulse("--format=json", "list", "sinks"))
    matches = [s for s in sinks if speaker_endpoint(s)]
    if len(matches) != 1:
        supported = "、".join(settings.split("_")[0] for settings in SUPPORTED_SPEAKERS.values()) + "、1462CC35"
        raise RuntimeError(f"未找到受支持的扬声器或线路输出（{supported}）。请选择扬声器输出后重试。")
    return matches[0]["name"]


def marker(path):
    if path.exists():
        if path.read_text() != MARKER:
            raise RuntimeError("Unexpected runtime owner: " + str(path))
    else:
        path.write_text(MARKER)


def initialize():
    target = detect()
    DATA.mkdir(parents=True, exist_ok=True)
    if (RUNTIME / "prefix").exists() and any((RUNTIME / "prefix").iterdir()) and not (RUNTIME / "prefix/.nahimic-linux-owner").exists():
        raise RuntimeError("Existing Wine prefix has no Nahimic owner marker")
    (RUNTIME / "prefix").mkdir(parents=True, exist_ok=True, mode=0o700)
    marker(RUNTIME / ".nahimic-session")
    marker(RUNTIME / "prefix/.nahimic-linux-owner")
    if not (RUNTIME / "preferences.json").exists():
        atomic_json(RUNTIME / "preferences.json", {"enabled": True})
    sinks = json.loads(pulse("--format=json", "list", "sinks"))
    physical = next(s for s in sinks if s["name"] == target)
    atomic_json(DATA / "installation.json", {"version": "0.3.3", "target": target,
                                             "runtime": runtime_generation(physical)})
    return target


def systemctl(*args):
    subprocess.run(["systemctl", "--user", *args], check=True, timeout=45)


def migrate_local():
    unit = Path.home() / ".config/systemd/user/nahimic.service"
    if not unit.is_file() or str(DATA / "current/host/run_local.py") not in unit.read_text():
        return
    systemctl("disable", "--now", "nahimic.service")
    backup = DATA / ("local-backup-" + str(time.time_ns()))
    backup.mkdir()
    unit.rename(backup / unit.name)
    launcher = Path.home() / ".local/bin/nahimic"
    if launcher.is_file() and str(DATA / "current/app/main.py") in launcher.read_text():
        launcher.rename(backup / "nahimic-launcher")
        launcher.symlink_to("/usr/bin/nahimic")
    desktop = Path.home() / ".local/share/applications/nahimic.desktop"
    if desktop.is_file() and str(DATA / "current") in desktop.read_text():
        desktop.rename(backup / "nahimic.desktop")
    print("Previous local installation retained:", backup)


def activate():
    initialize()
    migrate_local()
    systemctl("daemon-reload")
    first = not (DATA / "activated").exists()
    if first:
        systemctl("enable", "nahimic.service")
        (DATA / "activated").write_text(MARKER)
    enabled = subprocess.run(["systemctl", "--user", "is-enabled", "--quiet", "nahimic.service"]).returncode == 0
    if enabled:
        systemctl("restart", "nahimic.service")


def serve():
    installation = DATA / "installation.json"
    target = json.loads(installation.read_text())["target"] if installation.exists() else initialize()
    session_path = RUNTIME / "session.json"
    if session_path.exists():
        session = json.loads(session_path.read_text())
        session["ready"] = False
        atomic_json(session_path, session)
    stopped = False
    child = None

    def stop(signum, frame):
        nonlocal stopped
        stopped = True
        if child is not None and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        print("Waiting for configured speakers:", target, flush=True)
        atomic_json(RUNTIME / "desktop-state.json", {"waiting_for_speakers": True})
        while not stopped:
            sinks = json.loads(pulse("--format=json", "list", "sinks"))
            cards = json.loads(pulse("--format=json", "list", "cards"))
            if any(s["name"] == target and supported_speaker(s, cards) for s in sinks):
                break
            time.sleep(0.5)
        if stopped:
            return
        atomic_json(RUNTIME / "desktop-state.json", {"waiting_for_speakers": False})
        physical = next(s for s in sinks if s["name"] == target)
        dll, _, factory = runtime_files(physical)
        child = subprocess.Popen([sys.executable, str(ROOT / "host/run_local.py"),
                 "--exe", str(ROOT / "bin/apo_probe.exe"),
                 "--dll", str(SHARE / dll),
                 "--settings", str(SHARE / factory), "--target", target,
                 "--state-dir", str(RUNTIME)])
        if stopped:
            child.terminate()
        code = child.wait()
        if stopped:
            return
        if code != 75:
            raise SystemExit(code)


def main():
    parser = argparse.ArgumentParser(description="Nahimic speaker effects")
    modes = parser.add_mutually_exclusive_group()
    for name in ("service", "activate", "autostart", "status"):
        modes.add_argument("--" + name, action="store_true")
    args = parser.parse_args()
    if args.status:
        sys.path.insert(0, str(ROOT / "app"))
        from backend import Backend
        print(json.dumps(Backend().status(), ensure_ascii=False, indent=2))
    elif args.service:
        serve()
    elif args.activate:
        activate()
    elif args.autostart:
        if not (DATA / "activated").exists():
            activate()
    else:
        if not (DATA / "activated").exists():
            activate()
        os.execv(sys.executable, [sys.executable, str(ROOT / "app/main.py")])


if __name__ == "__main__":
    main()
