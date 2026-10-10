"""Desktop routing and real endpoint volume for the installed application."""
import json
from pathlib import Path
import subprocess


def pulse(*args):
    return subprocess.run(["pactl", *args], check=True, capture_output=True,
                          text=True, timeout=5).stdout.strip()


def atomic_json(path, value):
    path = Path(path)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    pending.replace(path)


def level(sink):
    return (tuple(sink["volume"][ch]["value"] for ch in ("front-left", "front-right")), sink["mute"])


class OutputUnavailable(RuntimeError):
    """The configured speaker endpoint is temporarily unavailable."""


# Hardware-matched built-in speakers and the factory device settings each one
# imports. Adding an entry requires a verified hardware ID, output port and
# local playback test; see AGENTS.md.
SUPPORTED_SPEAKERS = {
    "hda:14f11f87,1d05e022,": "1D05E022_Speakers.nsx",
    "hda:10ec0256,1d05e004,": "1D05E004_InternalSpeakers.nsx",
}


def device_settings(sink):
    components = sink.get("properties", {}).get("alsa.components", "").lower()
    return next((settings for prefix, settings in SUPPORTED_SPEAKERS.items()
                 if prefix in components), None)


def supported_speaker(sink):
    return (device_settings(sink) is not None
            and sink.get("active_port") == "[Out] Speaker")


class DesktopAudio:
    def __init__(self, work, sink, target):
        self.work, self.sink, self.target = Path(work), sink, target
        self.enabled = None
        self.node_id = None
        self.last_status = None

    def sinks(self):
        return {s["name"]: s for s in json.loads(pulse("--format=json", "list", "sinks"))}

    def set_enabled(self, enabled):
        subprocess.run(["pw-metadata", "-n", "filters", str(self.node_id),
                        "filter.smart.disabled", json.dumps(not enabled), "Spa:String:JSON"],
                       check=True, capture_output=True, text=True, timeout=5)
        self.enabled = enabled

    def tick(self, initializing=False):
        preferences = json.loads((self.work / "preferences.json").read_text())
        if type(preferences.get("enabled")) is not bool:
            raise ValueError("Missing boolean enabled preference")
        enabled = preferences["enabled"]
        sinks = self.sinks()
        physical = sinks.get(self.target)
        if physical is None or not supported_speaker(physical):
            raise OutputUnavailable("Waiting for the configured built-in speakers")
        virtual = sinks[self.sink]
        properties = virtual["properties"]
        if (properties.get("filter.smart") != "true"
                or json.loads(properties["filter.smart.target"]) != {"node.name": self.target}
                or properties.get("monitor.channel-volumes") != "false"):
            raise RuntimeError("Speaker filter properties were not applied correctly")
        self.node_id = int(properties["object.id"])
        if enabled != self.enabled:
            self.set_enabled(enabled)
        actual = level(physical)
        streams = json.loads(pulse("--format=json", "list", "sink-inputs"))
        applications = [s for s in streams
                        if s.get("properties", {}).get("node.name") != self.sink + "_render"
                        and s["sink"] == virtual["index"]]
        # Report the actual filter connection, including streams explicitly
        # assigned to speakers while another device is the system default.
        renderers = [s for s in streams
                     if s.get("properties", {}).get("node.name") == self.sink + "_render"]
        if initializing and not renderers:
            return False
        if len(renderers) != 1:
            raise RuntimeError("Original audio playback stream is missing")
        linked = renderers[0]["sink"] == physical["index"]
        if renderers[0]["sink"] not in (physical["index"], 4294967295):
            raise RuntimeError("Effect playback is linked to an unexpected device")
        active = enabled and linked and bool(applications)
        status = {"enabled": enabled, "active": active, "volume": round(max(actual[0]) / 65536 * 100),
                  "muted": actual[1], "applications": len(applications), "output": physical["description"]}
        if status != self.last_status:
            atomic_json(self.work / "desktop-state.json", status)
            self.last_status = status
        return True

    def close(self):
        # WirePlumber restores each stream's own target when the filter is
        # disabled. Never rewrite the system default or individual app targets.
        if self.node_id is not None and self.enabled:
            self.set_enabled(False)
