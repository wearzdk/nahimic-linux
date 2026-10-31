"""Native desktop controls for Nahimic speaker effects."""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import os
from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import QTimer, Qt, QLockFile, QRectF, QSize
from PySide6.QtGui import QIcon, QPainter, QColor, QPen, QPixmap, QFont, QLinearGradient
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QSlider, QFrame, QDialog, QToolButton, QButtonGroup, QSizePolicy, QStyle, QStyleOptionToolButton, QStylePainter, QComboBox, QMessageBox)
from backend import Backend, DATA, PROFILES
from i18n import Translations, LANGUAGES
from window_frame import decorate

ASSETS = Path(__file__).with_name('assets')

def asset(name):
    return QPixmap(str(ASSETS / name))

STYLE = """
QWidget { color: #e5eaf0; font-family: 'Noto Sans CJK SC'; font-size: 12px; }
QMainWindow, QWidget#root, QDialog { background: #142130; }
QFrame#navigation { background: #0c1722; border: none; }
QFrame#device { background: #172635; border-right: 1px solid #0b1925; }
QFrame#effect { background: #142130; border: 1px solid #0e1b29; }
QFrame#profileBar { background: #172535; border-bottom: 1px solid #36505c; }
QFrame#toolbar { border-bottom: 1px solid #0c1b28; }
QLabel#heading { font-size: 21px; font-weight: 400; }
QLabel#effectTitle { font-size: 16px; font-weight: 400; }
QLabel#muted, QLabel#caption { color: #8795a2; font-size: 11px; }
QLabel#value { font-family: 'Bahnschrift', 'Noto Sans'; font-size: 40px; font-weight: 300; }
QLabel#readout { font-family: 'Noto Sans'; font-size: 12px; }
QPushButton { background: transparent; border: 1px solid #405363; border-radius: 0; padding: 7px 15px; }
QPushButton:hover { border-color: #00dafa; color: #00dafa; }
QPushButton:focus, QToolButton:focus { border: 1px solid #00dafa; }
QPushButton:disabled { color: #536373; border-color: #283b4b; }
QToolButton { background: transparent; border: none; color: #a2acb6; padding: 6px; }
QToolButton:hover { color: #e7faff; background: #1b3041; }
QToolButton:checked { color: #ffffff; background: #153140; }
QToolButton#navigationButton { padding: 0; }
QWidget#titleBar { background: #0c1722; border-bottom: 1px solid #223343; }
QLabel#windowTitle { color: #9eabb7; font-size: 12px; }
QToolButton#windowButton, QToolButton#closeWindow { padding: 0; border: none; }
QToolButton#closeWindow:hover { background: #b63c43; }
QComboBox { background: #1c3041; border: 1px solid #536879; padding: 8px 12px; min-width: 220px; }
QComboBox QAbstractItemView { background: #172635; color: #e5eaf0; selection-background-color: #20516a; }
QLabel#community { color: #8795a2; font-size: 10px; padding: 6px 12px 0 12px; }
QSlider { background: transparent; }
QSlider::groove:horizontal { height: 12px; background: #365360; }
QSlider::sub-page:horizontal { background: #365360; }
QSlider::handle:horizontal { width: 20px; background: #00d9f2; border: 1px solid #5ce9f4; margin: -1px 0; }
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus { background: #81f4ff; }
QSlider::groove:vertical { width: 2px; background: #3b5864; }
QSlider::add-page:vertical { background: #00cfe7; }
QSlider::handle:vertical { height: 5px; background: #eefaff; border: 1px solid #ffffff; border-radius: 3px; margin: 0 -6px; }
QSlider::handle:disabled { background: #536c79; border-color: #536c79; }
QCheckBox { spacing: 10px; }
QCheckBox:disabled { color: #536373; }
"""


class Toggle(QCheckBox):
    """Native checkbox input using the original effect power artwork."""
    def __init__(self, label, parent=None, mute=False):
        super().__init__(label, parent)
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(30, 30) if mute else self.setFixedSize(38, 42)
        self.mute_icon = mute

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        if self.mute_icon:
            name = 'MuteNormalCheckedCheckBox24x24.png' if self.isChecked() else 'MuteNormalUncheckedCheckBox24x24.png'
        elif not self.isEnabled(): name = 'DesactivatedFXCheckBox45x50.png'
        elif self.isChecked(): name = 'NormalCheckedFXCheckbox45x50.png'
        elif self.underMouse(): name = 'OverUncheckedFXCheckBox45x50.png'
        else: name = 'NormalUncheckedFXCheckBox45x50.png'
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        picture = asset(name)
        painter.drawPixmap(self.rect(), picture)
        if self.hasFocus():
            painter.setPen(QPen(QColor('#00dafa'), 1, Qt.DotLine))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


class ControlSlider(QSlider):
    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                           Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
            self.sliderReleased.emit()

    def wheelEvent(self, event):
        before = self.value()
        super().wheelEvent(event)
        if self.value() != before: self.sliderReleased.emit()


class SpacedToolButton(QToolButton):
    """Keep native button semantics while giving icon and text an explicit gap."""
    def sizeHint(self):
        size = super().sizeHint()
        if self.toolButtonStyle() == Qt.ToolButtonTextBesideIcon:
            size.setWidth(self.fontMetrics().horizontalAdvance(self.text()) + self.iconSize().width() + 44)
            size.setHeight(max(size.height(), 48))
        else:
            size.setHeight(max(size.height(), self.iconSize().height() + self.fontMetrics().height() + 26))
        return size

    def paintEvent(self, event):
        option = QStyleOptionToolButton(); self.initStyleOption(option)
        option.text = ''; option.icon = QIcon()
        painter = QStylePainter(self); painter.drawComplexControl(QStyle.CC_ToolButton, option)
        icon_size = self.iconSize(); rect = self.rect()
        mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
        if self.toolButtonStyle() == Qt.ToolButtonTextBesideIcon:
            icon_rect = rect.adjusted(16, 0, 0, 0)
            icon_rect.setTop((self.height() - icon_size.height()) // 2)
            icon_rect.setSize(icon_size)
            text_rect = rect.adjusted(16 + icon_size.width() + 10, 0, -12, 0)
            alignment = Qt.AlignLeft | Qt.AlignVCenter
        else:
            total = icon_size.height() + 8 + self.fontMetrics().height()
            top = (self.height() - total) // 2
            icon_rect = rect.adjusted((self.width()-icon_size.width())//2, top, 0, 0); icon_rect.setSize(icon_size)
            text_rect = rect.adjusted(8, top + icon_size.height() + 8, -8, -6)
            alignment = Qt.AlignHCenter | Qt.AlignTop
        self.icon().paint(painter, icon_rect, Qt.AlignCenter, mode)
        painter.setPen(QColor('#e5eaf0' if self.isEnabled() else '#536373'))
        painter.drawText(text_rect, alignment, self.text())


class ProfileButton(SpacedToolButton):
    def paintEvent(self, event):
        if self.isChecked():
            painter = QPainter(self)
            glow = QLinearGradient(0, 0, 0, self.height())
            glow.setColorAt(0, QColor('#172535')); glow.setColorAt(1, QColor('#164456'))
            painter.fillRect(self.rect(), glow)
            painter.end()
        super().paintEvent(event)
        if self.isChecked():
            painter = QPainter(self)
            painter.fillRect(0, self.height() - 3, self.width(), 3, QColor('#00dcf5'))


class Panel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.backend = Backend()
        self.translations = Translations(DATA / 'interface.ini')
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.jobs = deque()
        self.awaiting_state = {}
        self.error_message = None
        self.last_status = None
        self.busy_timer = QTimer(self); self.busy_timer.setSingleShot(True)
        self.busy_timer.timeout.connect(self.show_pending_status)
        self.settings_loaded = False
        self.loaded_pid = None
        self.values = {}; self.switches = {}; self.modes = {}; self.effect_images = {}
        self.setWindowTitle('Nahimic Linux')
        self.setWindowIcon(QIcon(str(Path(__file__).with_name('nahimic.svg'))))
        self.resize(1100, 690); self.setMinimumSize(1000, 660)
        central = QWidget(); central.setObjectName('root'); self.setCentralWidget(central)
        frame = QVBoxLayout(central); frame.setContentsMargins(1, 1, 1, 1); frame.setSpacing(0)
        content = QWidget(); frame.addWidget(content, 1)
        self.titlebar = decorate(self, frame)
        shell = QHBoxLayout(content); shell.setContentsMargins(0, 0, 0, 0); shell.setSpacing(0)

        navigation = QFrame(); navigation.setObjectName('navigation'); navigation.setFixedWidth(160)
        nav = QVBoxLayout(navigation); nav.setContentsMargins(0, 18, 0, 22); nav.setSpacing(2)
        self.audio_navigation = SpacedToolButton(); self.audio_navigation.setObjectName('navigationButton')
        self.audio_navigation.setText('音频'); self.audio_navigation.setIcon(QIcon(str(ASSETS/'Audio16x16.png')))
        self.audio_navigation.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.audio_navigation.setCheckable(True); self.audio_navigation.setChecked(True)
        self.audio_navigation.clicked.connect(lambda: self.audio_navigation.setChecked(True))
        self.audio_navigation.setFixedWidth(160); nav.addWidget(self.audio_navigation)
        settings = SpacedToolButton(); settings.setObjectName('navigationButton'); settings.setText('设置')
        settings.setIcon(QIcon(str(ASSETS/'Settings16x16.png'))); settings.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        settings.setFixedWidth(160); settings.clicked.connect(self.show_preferences); nav.addWidget(settings)
        nav.addStretch()
        logo = QLabel(); logo.setPixmap(asset('LogoNahimicSteelSeries.png').scaledToWidth(94, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter); nav.addWidget(logo)
        community = QLabel('社区版本 · 非官方'); community.setObjectName('community'); community.setWordWrap(True); community.setAlignment(Qt.AlignCenter); nav.addWidget(community)
        shell.addWidget(navigation)

        self.controls = QWidget(); body = QHBoxLayout(self.controls); body.setContentsMargins(0,0,0,0); body.setSpacing(0)
        shell.addWidget(self.controls, 1)
        device = QFrame(); device.setObjectName('device'); device.setFixedWidth(88)
        volume_layout = QVBoxLayout(device); volume_layout.setContentsMargins(6, 22, 6, 25); volume_layout.setSpacing(10)
        speaker = QLabel(); speaker.setPixmap(asset('Speakers30x30.png')); speaker.setAlignment(Qt.AlignCenter); volume_layout.addWidget(speaker)
        label = QLabel('扬声器'); label.setWordWrap(True); label.setObjectName('caption'); label.setAlignment(Qt.AlignCenter); volume_layout.addWidget(label)
        self.mute = Toggle('静音', mute=True); self.mute.clicked.connect(lambda checked: self.submit(lambda: self.backend.mute(checked), 'state', 'muted', checked))
        volume_layout.addWidget(self.mute, 0, Qt.AlignHCenter)
        self.volume = ControlSlider(Qt.Vertical); self.volume.setRange(0,100); self.volume.setFixedWidth(26); self.volume.setAccessibleName('扬声器音量')
        self.volume.sliderReleased.connect(lambda: self.submit(lambda v=self.volume.value(): self.backend.volume(v), 'state', 'volume', self.volume.value()))
        volume_layout.addWidget(self.volume, 1, Qt.AlignHCenter)
        self.volume_value = QLabel('—'); self.volume_value.setObjectName('readout'); self.volume_value.setAlignment(Qt.AlignCenter)
        self.volume.valueChanged.connect(lambda v: self.volume_value.setText(f'{v}%')); volume_layout.addWidget(self.volume_value)
        body.addWidget(device)
        page = QVBoxLayout(); page.setContentsMargins(0,0,0,0); page.setSpacing(0); body.addLayout(page,1)

        header = QHBoxLayout(); header.setContentsMargins(16, 10, 20, 6); header.setSpacing(12)
        self.power = Toggle('启用音效'); self.power.clicked.connect(lambda checked: self.submit(lambda: self.backend.enabled(checked), 'state', 'enabled', checked))
        header.addWidget(self.power)
        self.power_label = QLabel('正在连接'); self.power_label.setObjectName('caption'); self.power_label.setMinimumWidth(130); self.power_label.setWordWrap(True); header.addWidget(self.power_label)
        title = QLabel('音频'); title.setObjectName('heading'); title.setAlignment(Qt.AlignCenter); header.addWidget(title,1); header.addSpacing(180)
        self.power.toggled.connect(lambda checked: self.translations.bind(
            self.power_label, 'text', '音效已开启' if checked else '音效已关闭'))
        page.addLayout(header)

        self.profiles = QFrame(); self.profiles.setObjectName('profileBar')
        profile_layout = QHBoxLayout(self.profiles); profile_layout.setContentsMargins(0,0,0,0); profile_layout.setSpacing(1)
        group = QButtonGroup(self); group.setExclusive(True)
        for name in ('Music','Movie','Communication','Gaming'):
            button = ProfileButton(); button.setText(PROFILES[name][0]); button.setIcon(QIcon(str(ASSETS/(name+'30x30.png'))))
            button.setIconSize(QSize(30,30)); button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setCheckable(True); button.setFixedHeight(82); button.setMinimumWidth(100)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setStyleSheet('QToolButton { padding: 5px; background: transparent; }')
            button.clicked.connect(lambda checked, n=name: self.submit(lambda: self.backend.profile(n),'settings','profile',n))
            group.addButton(button); self.modes[name]=button; profile_layout.addWidget(button,1)
        page.addWidget(self.profiles)
        toolbar = QFrame(); toolbar.setObjectName('toolbar'); tools = QHBoxLayout(toolbar); tools.setContentsMargins(12,7,16,7)
        eq_button = SpacedToolButton(); eq_button.setText('均衡器'); eq_button.setIcon(QIcon(str(ASSETS/'EqualiserNormal24x24.png')))
        eq_button.setIconSize(QSize(24,24)); eq_button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        eq_button.clicked.connect(self.show_equalizer); tools.addWidget(eq_button); tools.addStretch()
        self.status = QLabel('正在连接音效服务…'); self.status.setObjectName('caption'); self.status.setWordWrap(True); tools.addWidget(self.status)
        page.addWidget(toolbar)

        upper = QHBoxLayout(); upper.setContentsMargins(0,0,0,0); upper.setSpacing(0)
        surround = QFrame(); surround.setObjectName('effect'); surround_layout = QHBoxLayout(surround); surround_layout.setContentsMargins(20,8,14,8); surround_layout.setSpacing(12)
        globe = QLabel(); globe.setFixedSize(170,170); globe.setAlignment(Qt.AlignCenter); surround_layout.addWidget(globe)
        self.effect_images['kSet_SpkVirtualSurroundState'] = (globe, 'SoundSurroundOn255x255.png', 'SoundSurroundOff255x255.png')
        surround_text = QVBoxLayout(); surround_text.setSpacing(18)
        row=QHBoxLayout(); label=QLabel('环绕声'); label.setObjectName('effectTitle'); label.setWordWrap(True); row.addWidget(label); row.addStretch()
        switch=self.effect_switch('kSet_SpkVirtualSurroundState','启用环绕声'); row.addWidget(switch); surround_text.addLayout(row)
        description=QLabel('让声音从四周传来，带来更有空间感的聆听体验。'); description.setObjectName('caption'); description.setWordWrap(True)
        surround_text.addWidget(description); surround_text.addStretch(); surround_layout.addLayout(surround_text,1)
        upper.addWidget(surround,2)
        compressor=QFrame(); compressor.setObjectName('effect'); compressor_layout=QVBoxLayout(compressor); compressor_layout.setContentsMargins(22,14,14,16); compressor_layout.setSpacing(9)
        row=QHBoxLayout(); label=QLabel('音量稳定器'); label.setObjectName('effectTitle'); label.setWordWrap(True); row.addWidget(label); row.addStretch()
        row.addWidget(self.effect_switch('kSet_CompressorState','启用音量稳定器')); compressor_layout.addLayout(row)
        wave=QLabel(); wave.setFixedSize(100,75); wave.setAlignment(Qt.AlignCenter); compressor_layout.addWidget(wave,0,Qt.AlignHCenter)
        self.effect_images['kSet_CompressorState']=(wave,'VolumeStabiliserOn80x60.png','VolumeStabilizerOff80x60.png')
        description=QLabel('保持音量均衡，减少声音忽大忽小。'); description.setObjectName('caption'); description.setWordWrap(True); description.setAlignment(Qt.AlignCenter); compressor_layout.addWidget(description); compressor_layout.addStretch()
        upper.addWidget(compressor,1); page.addLayout(upper,1)

        lower=QHBoxLayout(); lower.setContentsMargins(0,0,0,0); lower.setSpacing(0)
        for title,state,gain,description in (
            ('人声','kSet_VoiceBoostState','kSet_VoiceBoostGainDB','调整对白与歌声的清晰度'),
            ('低音','kSet_BassBoostState','kSet_BassBoostGainDB','调整低频声音的力度与厚度'),
            ('高音','kSet_TrebleBoostState','kSet_TrebleBoostGainDB','调整声音细节与明亮度')):
            effect=QFrame(); effect.setObjectName('effect'); column=QVBoxLayout(effect); column.setContentsMargins(22,12,14,16); column.setSpacing(6)
            row=QHBoxLayout(); row.addSpacing(38); label=QLabel(title); label.setObjectName('effectTitle'); label.setWordWrap(True); label.setAlignment(Qt.AlignCenter); row.addWidget(label,1)
            row.addWidget(self.effect_switch(state,'启用'+title)); column.addLayout(row)
            readout=QHBoxLayout(); readout.setSpacing(6); readout.addStretch()
            value=QLabel('0'); value.setObjectName('value'); value.setFixedHeight(54); readout.addWidget(value)
            unit=QLabel('dB'); unit.setObjectName('caption'); readout.addWidget(unit,0,Qt.AlignVCenter); readout.addStretch(); column.addLayout(readout)
            slider=ControlSlider(Qt.Horizontal); slider.setFixedWidth(130); slider.setAccessibleName(title+'增益')
            slider.valueChanged.connect(lambda v,l=value:l.setText(str(v)))
            slider.sliderReleased.connect(lambda n=gain,s=slider:self.submit(lambda v=s.value():self.backend.set_setting(n,v),'settings',n,s.value()))
            self.values[gain]=slider; column.addWidget(slider,0,Qt.AlignHCenter)
            note=QLabel(description); note.setObjectName('caption'); note.setAlignment(Qt.AlignCenter); note.setWordWrap(True); note.setFixedHeight(36); column.addWidget(note); column.addStretch()
            lower.addWidget(effect,1)
        page.addLayout(lower,1)

        self.equalizer=QDialog(self); self.equalizer.setWindowTitle('均衡器'); self.equalizer.resize(750,470); self.equalizer.setMinimumSize(660,440)
        eq=QVBoxLayout(); eq.setContentsMargins(24,16,24,24); eq.setSpacing(20)
        row=QHBoxLayout(); label=QLabel('均衡器'); label.setObjectName('effectTitle'); label.setWordWrap(True); row.addWidget(label); row.addStretch(); row.addWidget(self.effect_switch('kSet_EQState','启用均衡器')); eq.addLayout(row)
        bands=QHBoxLayout(); bands.setSpacing(12)
        scale=QVBoxLayout(); scale.setContentsMargins(0,25,0,25)
        self.scale_high=QLabel('+12'); self.scale_low=QLabel('−12')
        for i,label in enumerate((self.scale_high,QLabel('0'),self.scale_low)):
            label.setObjectName('caption'); scale.addWidget(label)
            if i<2:scale.addStretch()
        bands.addLayout(scale)
        for band,label in (('31Hz','31'),('62Hz','62'),('125Hz','125'),('250Hz','250'),('500Hz','500'),('1kHz','1k'),('2kHz','2k'),('4kHz','4k'),('8kHz','8k'),('16kHz','16k')):
            name='kSet_EQ'+band+'GainDB'; column=QVBoxLayout(); value=QLabel('0'); value.setObjectName('readout'); value.setAlignment(Qt.AlignCenter); column.addWidget(value)
            slider=ControlSlider(Qt.Vertical); slider.setMinimumHeight(180); slider.setFixedWidth(24); self.translations.bind(slider, 'accessibleName', '{frequency} Hz 增益', frequency=label)
            slider.valueChanged.connect(lambda v,l=value:l.setText(f'{v:+d}' if v else '0'))
            slider.sliderReleased.connect(lambda n=name,s=slider:self.submit(lambda v=s.value():self.backend.set_setting(n,v),'settings',n,s.value()))
            self.values[name]=slider; column.addWidget(slider,1,Qt.AlignHCenter)
            frequency=QLabel(label); frequency.setObjectName('caption'); frequency.setAlignment(Qt.AlignCenter); column.addWidget(frequency); bands.addLayout(column,1)
        eq.addLayout(bands,1)
        done=QPushButton('完成'); done.clicked.connect(self.equalizer.accept); eq.addWidget(done,0,Qt.AlignRight)

        self.preferences = QDialog(self); self.preferences.setWindowTitle('设置'); self.preferences.setMinimumWidth(510)
        preferences_outer = QVBoxLayout(self.preferences); preferences_outer.setContentsMargins(1,1,1,1); preferences_outer.setSpacing(0)
        preferences_body = QWidget(); preferences_outer.addWidget(preferences_body)
        preferences_layout = QVBoxLayout(preferences_body); preferences_layout.setContentsMargins(24,20,24,24); preferences_layout.setSpacing(18)
        language_row = QHBoxLayout(); language_row.setSpacing(18)
        language_label = QLabel('语言'); language_row.addWidget(language_label)
        self.language = QComboBox(); self.language.setAccessibleName('语言')
        self.language.addItem(self.translations.text('跟随系统'), 'system')
        for code, label in LANGUAGES.items(): self.language.addItem(label, code)
        self.language.setCurrentIndex(self.language.findData(self.translations.choice))
        self.language.activated.connect(self.change_language); language_row.addWidget(self.language,1); preferences_layout.addLayout(language_row)
        self.auto = QCheckBox('登录后自动运行音效')
        self.auto.clicked.connect(lambda checked: self.submit(lambda: self.backend.autostart(checked), 'state', 'autostart', checked)); preferences_layout.addWidget(self.auto)
        note = QLabel('关闭窗口后，音效继续运行。模式和参数会自动保存。'); note.setObjectName('muted'); note.setWordWrap(True); preferences_layout.addWidget(note)
        about = QLabel('关于'); about.setObjectName('effectTitle'); preferences_layout.addWidget(about)
        version = QLabel('Nahimic Linux  0.3.3'); version.setObjectName('caption'); preferences_layout.addWidget(version)
        disclaimer = QLabel('这是独立的社区项目，与 Nahimic、A-Volute、SteelSeries 及电脑厂商无隶属关系，也未获其认可或赞助。相关名称、商标和原厂资源归各自权利人所有。')
        disclaimer.setWordWrap(True); disclaimer.setObjectName('muted'); disclaimer.setMaximumWidth(460); preferences_layout.addWidget(disclaimer)
        close_button = QPushButton('完成'); close_button.clicked.connect(self.preferences.accept); preferences_layout.addWidget(close_button,0,Qt.AlignRight)
        self.preferences_titlebar = decorate(self.preferences, preferences_outer)
        # Keep dialog chrome flush to the window while retaining content padding.
        eq_outer = QVBoxLayout(); eq_outer.setContentsMargins(1,1,1,1); eq_outer.setSpacing(0)
        eq_body = QWidget(); eq_body.setLayout(eq); eq_outer.addWidget(eq_body)
        self.equalizer.setLayout(eq_outer)
        self.equalizer_titlebar = decorate(self.equalizer, eq_outer)
        self.translations.capture(self)
        self.controls.setEnabled(False); self.equalizer.setEnabled(False)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(100)
        self.status_timer=QTimer(self);self.status_timer.timeout.connect(self.refresh);self.status_timer.start(2000)
        self.refresh()

    def change_language(self, index):
        try:
            self.translations.select(self.language.itemData(index))
        except OSError as error:
            self.language.setCurrentIndex(self.language.findData(self.translations.choice))
            QMessageBox.warning(self, self.translations.text('设置'), str(error))
            return
        self.language.setItemText(0, self.translations.text('跟随系统'))
        self.preferences.adjustSize()

    def effect_switch(self, name, label):
        switch=Toggle(label)
        switch.clicked.connect(lambda checked:self.submit(lambda:self.backend.set_setting(name,int(checked)),'settings',name,int(checked)))
        switch.toggled.connect(self.update_effect_images)
        self.switches[name]=switch
        return switch

    def show_equalizer(self):
        self.equalizer.show(); self.equalizer.raise_(); self.equalizer.activateWindow()

    def show_preferences(self):
        self.preferences.show()
        self.preferences.raise_()
        self.preferences.activateWindow()

    def pending_keys(self):
        keys = {job[2] for job in self.jobs if job[2] is not None}
        if self.pending and self.pending[2] is not None:
            keys.add(self.pending[2])
        return keys | self.awaiting_state.keys()

    def show_pending_status(self):
        if self.pending_keys() and not self.error_message:
            self.translations.bind(self.status, 'text', '正在应用…')

    def submit(self, function, kind, key=None, value=None):
        if key is not None:
            self.error_message = None
            if kind == 'state':
                self.awaiting_state[key] = (value, None)
            if not self.busy_timer.isActive():
                self.busy_timer.start(400)
        job = (function, kind, key, value)
        if self.pending:
            if kind != 'status':
                # Only coalesce adjacent edits; a profile change remains an ordering barrier.
                if key is not None and self.jobs and self.jobs[-1][2] == key:
                    self.jobs[-1] = job
                else:
                    self.jobs.append(job)
            return
        self.pending = (self.pool.submit(function), kind, key, value)

    def refresh(self):
        if not self.pending:
            if self.jobs:
                function, kind, key, value = self.jobs.popleft()
                self.pending = (self.pool.submit(function), kind, key, value)
            else:
                self.submit(self.backend.status, 'status')

    def set_available(self, ready):
        available = ready and self.settings_loaded
        self.controls.setEnabled(available)
        self.equalizer.setEnabled(available)
        self.auto.setEnabled(available)

    def show_status(self):
        if self.error_message:
            self.translations.bind(self.status, 'text', '操作未完成：{error}', error=self.error_message)
        elif self.pending_keys():
            if not self.busy_timer.isActive(): self.show_pending_status()
        elif self.last_status:
            result = self.last_status
            if result['ready']:
                self.translations.bind(self.status, 'text', '音效正在运行' if result['active'] else
                    '正在播放原声' if not result['enabled'] else '音效已开启，等待扬声器播放')
            elif result.get('waiting_for_speakers'):
                self.translations.bind(self.status, 'text', '等待内置扬声器，其他输出正常使用')
            else:
                self.translations.bind(self.status, 'text', '正在准备音效，请稍候…' if
                    result['service'] in ('active', 'activating') else '音效服务未运行，请关闭并重新打开软件。')

    def apply_status(self, result):
        self.last_status = result
        ready = result['ready']
        if not ready or result['instance'] != self.loaded_pid:
            if ready: self.loaded_pid = result['instance']
            self.settings_loaded = False
        self.set_available(ready)
        queued = {job[2] for job in self.jobs}
        for key, (wanted, deadline) in list(self.awaiting_state.items()):
            if key in queued or deadline is None:
                continue
            if key in result and result[key] == wanted:
                del self.awaiting_state[key]
            elif time.monotonic() >= deadline:
                del self.awaiting_state[key]
                self.error_message = self.translations.text('设置未获系统确认，已重新读取当前状态。')
        protected = self.pending_keys()
        if 'autostart' not in protected: self.auto.setChecked(result['autostart'])
        if ready:
            if 'enabled' not in protected:
                self.power.setChecked(result['enabled'])
                self.translations.bind(self.power_label, 'text', '音效已开启' if result['enabled'] else '音效已关闭')
            if 'volume' not in protected and not self.volume.isSliderDown():
                self.volume.setValue(result['volume'])
            if 'muted' not in protected: self.mute.setChecked(result['muted'])
            if not self.settings_loaded:
                self.jobs.appendleft((self.backend.settings, 'settings', None, None))
        else:
            self.translations.bind(self.power_label, 'text', '等待连接')
        self.show_status()

    def poll(self):
        if not self.pending or not self.pending[0].done(): return
        future, kind, key, value = self.pending
        self.pending = None
        try:
            result = future.result()
            if kind == 'settings':
                self.apply_settings(result)
                self.set_available(bool(self.last_status and self.last_status['ready']))
            elif kind == 'state':
                # Desktop state is published asynchronously by the audio service.
                if key not in {job[2] for job in self.jobs}:
                    self.awaiting_state[key] = (value, time.monotonic() + 5)
            else:
                self.apply_status(result)
        except Exception as error:
            self.error_message = str(error)
            if kind == 'settings':
                if key == 'profile':
                    # Do not apply edits to a different profile after a failed switch.
                    self.jobs = deque(job for job in self.jobs if job[1] != 'settings')
                if key is not None:
                    self.jobs.appendleft((self.backend.settings, 'settings', None, None))
                else:
                    self.settings_loaded = False
                    self.set_available(False)
            elif kind == 'state' and key not in {job[2] for job in self.jobs}:
                self.awaiting_state.pop(key, None)
            elif kind == 'status':
                self.set_available(False)
            self.show_status()
        if not self.pending_keys(): self.busy_timer.stop()
        self.show_status()
        if self.jobs or kind != 'status': self.refresh()

    def update_effect_images(self):
        for name, (label, on, off) in self.effect_images.items():
            enabled = self.switches[name].isChecked()
            if label.property('effectEnabled') != enabled:
                label.setPixmap(asset(on if enabled else off).scaled(
                    label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                label.setProperty('effectEnabled', enabled)

    def apply_settings(self,state):
        if state['profile'] not in self.modes:
            raise ValueError(self.translations.text('未知音效预设：{profile}', profile=state['profile']))
        protected = self.pending_keys()
        if 'profile' in protected:
            return
        for name, button in self.modes.items():
            button.setChecked(name == state['profile'])
        for name,slider in self.values.items():
            if name in protected or slider.isSliderDown(): continue
            item=state['settings'][name];slider.setRange(round(item['min']),round(item['max']));slider.setValue(round(item['value']))
        for name,switch in self.switches.items():
            if name not in protected: switch.setChecked(bool(state['settings'][name]['value']))
        self.scale_high.setText(f"+{round(state['settings']['kSet_EQ31HzGainDB']['max'])}")
        self.scale_low.setText(str(round(state['settings']['kSet_EQ31HzGainDB']['min'])))
        self.update_effect_images()
        self.settings_loaded=True


def main():
    app=QApplication(sys.argv);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    runtime=Path(os.environ['XDG_RUNTIME_DIR']);lock=QLockFile(str(runtime/'nahimic-panel.lock'))
    socket_name=str(runtime/'nahimic-panel.socket')
    if not lock.tryLock(0):
        socket=QLocalSocket();socket.connectToServer(socket_name)
        if not socket.waitForConnected(1000):raise RuntimeError("音效窗口已运行，但无法联系窗口")
        socket.write(b'show');socket.waitForBytesWritten(1000)
        return
    QLocalServer.removeServer(socket_name);server=QLocalServer()
    if not server.listen(socket_name):raise RuntimeError(server.errorString())
    subprocess.run(['systemctl','--user','start','nahimic.service'],check=True,timeout=15)
    panel=Panel()
    def activate():
        connection=server.nextPendingConnection();connection.close();connection.deleteLater()
        if panel.settings_loaded:
            panel.submit(panel.backend.settings, 'settings')
        panel.showNormal();panel.raise_();panel.activateWindow()
    server.newConnection.connect(activate)
    panel.show();app.exec();panel.pool.shutdown(wait=True,cancel_futures=True)


if __name__=='__main__':main()
