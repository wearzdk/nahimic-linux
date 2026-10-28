# Nahimic Linux

[简体中文](README.md) | **English**

Nahimic audio effects for laptop speakers on Linux. Includes Music, Movie, Gaming, and Communication profiles; bass, voice, and treble controls; surround sound; volume stabilization; and a ten-band equalizer. Switch between processed and original audio with one click. Volume stays in sync with the system, settings are saved automatically, and effects keep running after you close the panel.

This is an independent community project. It is not affiliated with, endorsed by, or sponsored by Nahimic, A-Volute, SteelSeries, or PC manufacturers. Names, trademarks, and original assets belong to their respective owners.

![Nahimic Linux control panel](docs/panel.png)

*Chinese interface shown. The app supports 12 interface languages, including English.*

## Install

On Arch Linux and derivatives:

```sh
yay -S nahimic-linux
```

Alternatively, use `paru -S nahimic-linux`. Installation downloads the required runtime components, identifies supported speakers, and starts the audio service. Open **Nahimic** from your application menu or run `nahimic`. Initial runtime setup may take a moment.

Currently tested on the built-in speakers of the **MECHREVO Wujie 14X Pro (机械革命无界 14X Pro)** with Senary audio, subsystem ID `1D05E022`, and of the **MECHREVO XINGYAO Series P916F** with Realtek ALC256, subsystem ID `1D05E004`. Requires x86_64 Linux, PipeWire, PipeWire Pulse, WirePlumber 0.5 or newer, and a systemd user session. The Qt interface works with KDE, GNOME, and other desktop environments that provide these components. Effects attach automatically to the built-in speakers while you select real output devices as usual. Headphones, Bluetooth devices, and HDMI outputs use their own audio paths. Effects resume automatically when the speakers return.

## Recommended: let an AI assistant install or adapt it

Send this prompt to Claude Code, Codex, or another AI coding assistant:

> Read https://github.com/wearzdk/nahimic-linux and follow AGENTS.md to inspect my system and audio hardware, install Nahimic Linux, and verify that it works. If my laptop model differs, inspect the available device configurations and test an adaptation on my machine. Once verified, submit a pull request to help other users.

The remaining instructions are primarily for AI assistants, maintainers, and users troubleshooting an installation.

## Usage and troubleshooting

The power control at the top switches between audio effects and original audio. Open **Equalizer** for the ten-band controls, or **Settings** to configure startup and interface language. Changes appear immediately while the app applies and confirms them in the background. If a write fails, the panel reads the current state and displays an error.

The app uses a custom title bar: drag it to move the window, double-click to maximize or restore, and drag the window edges to resize.

Supported interface languages: Simplified Chinese, Traditional Chinese, English, Japanese, Korean, German, French, Spanish, Portuguese, Italian, Russian, and Turkish. The app follows the system language by default, with English used for unsupported locales. You can also select a language manually. Changes take effect immediately, persist between launches, and do not interrupt audio processing.

Translations are in [app/locales/](app/locales/). Corrections and new languages are welcome. New translations should include every existing message and preserve its format placeholders. Check that longer text fits at the minimum window size.

```sh
nahimic --status
systemctl --user status nahimic.service
journalctl --user -u nahimic.service -b
```

The effects switch applies only to the built-in speakers. Your system manages the default output and each application’s device selection. Settings are stored in `${XDG_DATA_HOME:-~/.local/share}/nahimic-linux/`.

## Build and install from source

The AUR build recipe is in [packaging/PKGBUILD](packaging/PKGBUILD). Build dependencies include MinGW-w64 GCC, a C compiler, pkg-config, libpulse, Python, and cabextract. Runtime dependencies include Wine, PySide6, PipeWire, PipeWire Pulse, WirePlumber 0.5+, libpulse, and systemd. The PKGBUILD contains the complete package dependency lists.

```sh
git clone https://aur.archlinux.org/nahimic-linux.git
cd nahimic-linux
makepkg -si
```

The build downloads pinned runtime components from Microsoft Update and the official Nahimic support site, then verifies SHA-256 hashes for the archives and the runtime files used by the application. Run `make` to build the native host components. To inspect the installation layout without installing into the system, run `make DESTDIR=/tmp/nahimic-stage install`. Use the PKGBUILD for a complete installation, including runtime components.

## MSI MEG X570 UNIFY Line Out

The MSI MEG X570 UNIFY (MS-7C35), Realtek ALC1220 with subsystem ID
`1462CC35`, uses the vendor APO3 runtime and its `Generic_ExternalSpeakers`
profile. Support is restricted to `HDA:10ec1220,1462cc35,` and the
`analog-output-lineout` port. Plugging in headphones bypasses the effects;
HDMI and other outputs retain their normal paths. No laptop tuning is applied.

The build extracts APO3 files from the same pinned restore-tool archive as the
existing settings. APO3 and APO4 keep separate runtime files and factory
settings. The control panel uses the same controls; APO3 Clarity maps to Voice.

## Contribute device support

Read [AGENTS.md](AGENTS.md) first. Include your laptop model, audio hardware IDs, PipeWire output information, and results from testing effect switching, settings persistence, service restarts, and continuous playback. Each model needs a configuration matching its hardware. New device support should be verified on the actual machine before submitting a pull request.

## License and attribution

The community application and host code use the [MIT license](LICENSE). Downloaded runtime components retain their [upstream licensing terms](packaging/LicenseRef-Nahimic); original interface artwork is covered by its [attribution notice](app/assets/NOTICE.txt), not the community code's MIT license. Nahimic and other names and trademarks belong to their respective owners.
