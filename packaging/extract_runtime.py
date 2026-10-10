"""Extract and verify the runtime data required by supported hardware."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile


def extract(swc, settings, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(__file__).with_name("runtime-sha256.json").read_text())
    with tempfile.TemporaryDirectory(prefix="nahimic-runtime-", dir="/tmp") as temporary:
        work = Path(temporary)
        subprocess.run(["cabextract", "-q", "-d", str(work / "swc"), str(swc)], check=True)
        data = Path(settings).read_bytes()
        start = data.find(b"PK\x03\x04")
        end = data.find(b"PK\x05\x06", start)
        if start < 0 or end < 0:
            raise ValueError("Runtime settings archive is absent")
        comment = struct.unpack_from("<H", data, end + 20)[0]
        with zipfile.ZipFile(io.BytesIO(data[start:end + 22 + comment])) as archive:
            # Both AIstone APO4 cabinets carry the same global, metadata and
            # profile files and only differ in their Devices/*.nsx. The cabinet
            # of the already verified hardware is extracted last so the pinned
            # manifest keeps describing those shared files.
            settings_cabs = {
                "settings-nh3.cab": "Drivers\\EXT\\AIstone\\APO4\\NH3ProductSettings.cab",
                "settings-cnxt.cab": "Drivers\\EXT\\AIstone\\APO4\\NH3CNXTProductSettings.cab",
            }
            for name, member in settings_cabs.items():
                (work / name).write_bytes(archive.read(member))
                subprocess.run(["cabextract", "-q", "-d", str(work / "factory"), str(work / name)], check=True)
        for name, expected in manifest.items():
            group, relative = name.split("/", 1)
            source = work / ("swc" if group == "vendor" else "factory") / relative
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError("Runtime checksum mismatch: " + name)
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


if __name__ == "__main__":
    extract(*sys.argv[1:])
