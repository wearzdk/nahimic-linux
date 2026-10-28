# Nahimic Linux

**简体中文** | [English](README.en.md)

让笔记本扬声器在 Linux 上用上 Nahimic 音效。提供音乐、电影、游戏、通话四种模式，低音、人声、高音、环绕、动态压缩和十段均衡器。支持一键开关对比，音量与系统同步，设置自动保存，关闭面板后音效继续运行。

这是独立的社区项目，与 Nahimic、A-Volute、SteelSeries 及电脑厂商无隶属关系，也未获其认可或赞助。名称、商标和原厂资源归各自权利人所有。

*An independent community project, not affiliated with, endorsed by, or sponsored by Nahimic, A-Volute, SteelSeries, or PC manufacturers.*

![Nahimic Linux 控制面板](docs/panel.png)

## 安装

Arch Linux 及衍生发行版：

```sh
yay -S nahimic-linux
```

也可以使用 `paru -S nahimic-linux`。安装会自动下载所需运行组件、匹配扬声器并启动音效。安装完成后，从应用菜单打开 **Nahimic 音效**，或运行 `nahimic`。首次准备运行环境需要稍等片刻。

目前已验证机械革命无界 14X Pro（Senary 声卡，子系统 ID `1D05E022`）与机械革命 XINGYAO Series P916F（Realtek ALC256，子系统 ID `1D05E004`）的内置扬声器。需要 x86_64、PipeWire、PipeWire Pulse、WirePlumber 0.5 或更新版本，以及 systemd 用户会话。界面使用 Qt，支持 KDE、GNOME 及其他提供上述组件的桌面环境。音效自动附着于内置扬声器，系统中照常选择真实输出设备。切换到耳机、蓝牙或 HDMI 时使用对应设备的原有音频路径；切回扬声器后自动恢复音效。

## 让 AI 帮你安装或适配

可以直接把下面这段话发给 Claude Code、Codex 等 AI 编程助手：

> 阅读 https://github.com/wearzdk/nahimic-linux ，按照 AGENTS.md 检查我的系统和声卡，安装并验证 Nahimic Linux。若型号不一致，请检查现有设备配置，完成本机测试后提交 PR，帮助更多人使用。

下方说明主要供 AI 助手、维护者和需要排查问题的用户参考。

## 使用与排查

面板顶部的开关可即时切换音效与原声。点击「均衡器」打开十段调节窗口；在「设置」中调整开机启动和界面语言。窗口支持拖动标题栏、双击最大化及拖动边缘缩放。

界面支持简体中文、繁体中文、英语、日语、韩语、德语、法语、西班牙语、葡萄牙语、意大利语、俄语和土耳其语。默认跟随系统语言，也可手动选择；切换立即生效并自动保存，不影响正在播放的音效。不支持的系统语言使用英语。

翻译文件位于 `app/locales/`。欢迎修正用词或贡献新语言，新增翻译须包含现有全部条目和格式占位符，并检查长文本在最小窗口下的显示。

```sh
nahimic --status
systemctl --user status nahimic.service
journalctl --user -u nahimic.service -b
```

音效开关只控制内置扬声器；系统默认输出和应用单独选择的设备由系统管理。设置保存在 `${XDG_DATA_HOME:-~/.local/share}/nahimic-linux/`。

## 构建与安装

AUR 的 `PKGBUILD` 位于 `packaging/`。构建依赖 MinGW-w64 GCC、C 编译器、pkg-config、libpulse、Python 和 cabextract。运行依赖 Wine、PySide6、PipeWire、PipeWire Pulse、WirePlumber 0.5+、libpulse、systemd。

```sh
git clone https://aur.archlinux.org/nahimic-linux.git
cd nahimic-linux
makepkg -si
```

构建会从 Microsoft Update 和 Nahimic 官方支持站点获取固定版本组件，并验证归档及实际使用文件的 SHA-256。界面与宿主程序通过 `make` 构建，`make DESTDIR=/tmp/nahimic-stage install` 可检查系统包目录布局。完整安装以 PKGBUILD 为准。

## MSI MEG X570 UNIFY 线路输出

MSI MEG X570 UNIFY（MS-7C35，Realtek ALC1220，子系统 ID `1462CC35`）使用原厂 APO3 运行组件与 `Generic_ExternalSpeakers` 配置。仅匹配 `HDA:10ec1220,1462cc35,` 和 `analog-output-lineout` 端口；插入耳机时旁路音效，HDMI 等其他输出保持原有路径，不套用笔记本扬声器配置。

APO3 文件来自现有固定版本恢复工具归档，与 APO4 的运行组件、原厂设置分开保存。控制面板保持现有操作，APO3 的 Clarity 对应人声调节。

## 贡献设备适配

请先阅读 [AGENTS.md](AGENTS.md)。提交机器型号、声卡硬件 ID、PipeWire 输出信息，以及音效开关、参数保存、服务重启和连续播放的测试结果。每个型号使用与硬件匹配的配置，新增支持以本机验证为依据。

本项目为社区维护项目，未与 Nahimic、SteelSeries 或电脑厂商建立隶属关系。项目代码使用 MIT 许可证；下载的运行组件适用其原有许可。Nahimic 名称及相关商标归其权利人所有。
