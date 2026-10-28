"""Original effect controls and installed application state."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "nahimic-linux"
RUNTIME = DATA / "runtime"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from desktop_audio import atomic_json, pulse

PROFILES = {
    "Music": ("音乐", "77f15d10-d6b9-11e7-8f1a-0800200c9a66"),
    "Movie": ("电影", "77f15d11-d6b9-11e7-8f1a-0800200c9a66"),
    "Gaming": ("游戏", "77f18421-d6b9-11e7-8f1a-0800200c9a66"),
    "Communication": ("通话", "77f18420-d6b9-11e7-8f1a-0800200c9a66"),
}


class Backend:
    def control(self, *args):
        installation = json.loads((DATA / "installation.json").read_text())
        api_file = ("vendor-apo3/NAHIMICV3NSControl.dll" if installation.get("runtime") == "apo3"
                    else "vendor/NahimicAPO4API.dll")
        api = "Z:" + str(Path("/usr/share/nahimic-linux") / api_file).replace("/", "\\")
        result = subprocess.run(["wine", str(Path(__file__).resolve().parents[1] / "bin/apo_control.exe"), api, *args],
                                env=os.environ | {"WINEPREFIX": str(RUNTIME / "prefix"), "WINEDEBUG": "-all",
                                                  "WINEDLLOVERRIDES": "mscoree,mshtml="},
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[-1200:] or "原厂音效控制失败")
        return result.stdout

    def settings(self):
        output = self.control("--settings")
        profile = re.search(r"(?:global_profile|default_application) id=\S+ name=(\S+)", output)
        if not profile:
            raise RuntimeError("无法读取当前音效模式")
        settings = {}
        for line in output.splitlines():
            match = re.search(r"name=(kSet_\w+) value=([-+\d.eE]+)", line)
            if not match:
                continue
            item = {"value": float(match[2])}
            for key in ("default", "min", "max"):
                found = re.search(rf"\b{key}=([-+\d.eE]+)", line)
                if found:
                    item[key] = float(found[1])
            settings[match[1]] = item
        return {"profile": profile[1], "settings": settings}

    def set_setting(self, name, value):
        self.control("--set-setting", name, str(value))
        state = self.settings()
        if abs(state["settings"][name]["value"] - float(value)) > 1e-5:
            raise RuntimeError("音效参数未生效")
        return state

    def profile(self, name):
        self.control("--global-profile", "{" + PROFILES[name][1] + "}")
        state = self.settings()
        if state["profile"] != name:
            raise RuntimeError("音效模式未生效")
        return state

    def enabled(self, enabled):
        preferences = json.loads((RUNTIME / "preferences.json").read_text())
        preferences["enabled"] = bool(enabled)
        atomic_json(RUNTIME / "preferences.json", preferences)

    def volume(self, value):
        target = json.loads((DATA / "installation.json").read_text())["target"]
        pulse("set-sink-volume", target, str(int(value)) + "%")

    def mute(self, value):
        target = json.loads((DATA / "installation.json").read_text())["target"]
        pulse("set-sink-mute", target, str(int(value)))

    def autostart(self, enabled):
        subprocess.run(["systemctl", "--user", "enable" if enabled else "disable", "nahimic.service"],
                       check=True, capture_output=True, text=True, timeout=10)

    def status(self):
        service = subprocess.run(["systemctl", "--user", "is-active", "nahimic.service"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
        auto = subprocess.run(["systemctl", "--user", "is-enabled", "nahimic.service"],
                              capture_output=True, text=True, timeout=5).returncode == 0
        result = {"ready": False, "service": service, "autostart": auto}
        status_path = RUNTIME / "desktop-state.json"
        if service == "active" and status_path.exists():
            result["waiting_for_speakers"] = json.loads(status_path.read_text()).get("waiting_for_speakers", False)
        path = RUNTIME / "session.json"
        if path.exists():
            session = json.loads(path.read_text())
            if session.get("ready") and service == "active":
                status = RUNTIME / "desktop-state.json"
                if status.exists():
                    result.update(json.loads(status.read_text()))
                    result["ready"] = True
                    result["instance"] = session["pid"]
        return result
