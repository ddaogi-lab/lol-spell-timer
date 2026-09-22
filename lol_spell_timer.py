# -*- coding: utf-8 -*-
"""
롤 스펠 타이머 오버레이 v6
- 스펠을 텍스트 대신 실제 아이콘으로 표시 (Riot Data Dragon CDN, 첫 실행 시
  자동 다운로드 후 로컬 캐시. 이후 인터넷 없이 동작)
- 잠금(클릭 통과) 중에도 자물쇠 버튼은 별도 작은 창이라 좌클릭으로 해제 가능
- 컴팩트 가로 폭
- 게임 자동 감지/표시, 롤 창 따라다니기, 투명도, 점멸 오른쪽 고정
- 장화(일반/강화)·우주적 통찰 쿨감 토글, 전역 단축키 Ctrl+1~5 / Alt+A

필요 패키지:  pip install PyQt6 requests keyboard
실행:         python lol_spell_timer.py
"""

import os
import sys
import time
import ctypes
import threading
import traceback

import requests
import urllib3
from PyQt6.QtCore import Qt, QTimer, QPoint, QObject, pyqtSignal, QSize
from PyQt6.QtGui import (
    QFont, QCursor, QPixmap, QPainter, QColor, QIcon, QAction,
    QPainterPath, QRegion,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QFrame, QSystemTrayIcon, QMenu,
)

try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_BASE = "https://127.0.0.1:2999/liveclientdata"
GAME_WINDOW_TITLE = "League of Legends (TM) Client"
IS_WINDOWS = sys.platform == "win32"

DDRAGON_VER = None        # 실행 시 versions.json에서 최신 버전 자동 조회
DDRAGON_VER_FALLBACK = "15.1.1"   # 조회 실패 시 사용할 폴백
APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LoLSpellTimer")
CACHE_DIR = os.path.join(APP_DIR, "icons")
LOG_PATH = os.path.join(APP_DIR, "error.log")


def resource_path(name):
    """번들 리소스 경로. PyInstaller onefile(sys._MEIPASS)과 스크립트 실행 모두 대응"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


ICON_PATH = resource_path("spelltimer.ico")


def _log_error(where=""):
    """예외 내용을 로그 파일에 기록 (크래시 원인 추적용)"""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


def _log_diag(where="", data=None):
    """실제로 들어온 데이터의 형태를 기록 (한 번만, 디버깅용)"""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        snippet = repr(data)
        if len(snippet) > 2000:
            snippet = snippet[:2000] + " ...(생략)"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] DIAG {where}\n")
            f.write(f"type={type(data).__name__}\n{snippet}\n")
    except Exception:
        pass


def _install_excepthook():
    """잡히지 않은 예외로 프로그램이 조용히 죽는 것을 막고 로그로 남김"""
    def hook(exc_type, exc_value, exc_tb):
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] UNCAUGHT\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
    sys.excepthook = hook


# (이름, 기본 쿨다운, 색, Data Dragon 아이콘 파일명)
SPELL_DATA = {
    "SummonerFlash":    ("점멸", 300, "#f2c14e", "SummonerFlash.png"),
    "SummonerDot":      ("점화", 180, "#e25822", "SummonerDot.png"),
    "SummonerTeleport": ("순간이동", 360, "#7c5cff", "SummonerTeleport.png"),
    "SummonerHaste":    ("유체화", 210, "#5ad1e6", "SummonerHaste.png"),
    "SummonerHeal":     ("회복", 240, "#52d273", "SummonerHeal.png"),
    "SummonerExhaust":  ("탈진", 210, "#d4a017", "SummonerExhaust.png"),
    "SummonerBarrier":  ("방어막", 180, "#f5d76e", "SummonerBarrier.png"),
    "SummonerBoost":    ("정화", 210, "#9ad1f5", "SummonerBoost.png"),
    "SummonerSmite":    ("강타", 90,  "#c0a16b", "SummonerSmite.png"),
    "SummonerSnowball": ("눈덩이", 80, "#bfe8ff", "SummonerSnowball.png"),
    "SummonerMana":     ("총명", 240, "#6db3f2", "SummonerMana.png"),
}
UNKNOWN_SPELL = ("?", 300, "#999999", "")

BOOT_STEPS = [("－", 0), ("장", 10), ("강", 20)]
RUNE_HASTE = 18


def spell_key_from_raw(raw_name: str) -> str:
    for key in SPELL_DATA:
        if key in raw_name:
            return key
    return ""


def order_spells(s1: str, s2: str):
    if s1 == "SummonerFlash" and s2 != "SummonerFlash":
        return s2, s1
    return s1, s2


class IconCache:
    """스펠 아이콘을 로컬 캐시에서 읽고, 없으면 CDN에서 받아 저장 (백그라운드)"""

    def __init__(self):
        self._pixmaps: dict[str, QPixmap] = {}
        os.makedirs(CACHE_DIR, exist_ok=True)

    def get(self, spell_key: str):
        """즉시 사용 가능한 QPixmap 반환. 아직 없으면 None (텍스트 폴백)."""
        if spell_key in self._pixmaps:
            return self._pixmaps[spell_key]
        info = SPELL_DATA.get(spell_key)
        if not info or not info[3]:
            return None
        path = os.path.join(CACHE_DIR, info[3])
        if os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self._pixmaps[spell_key] = pm
                return pm
        return None

    def ensure_async(self, spell_keys, on_ready):
        """필요한 아이콘들을 백그라운드로 받고, 끝나면 on_ready 콜백 호출"""
        def worker():
            global DDRAGON_VER
            sess = requests.Session()
            sess.trust_env = False
            # 최신 버전 확인 (한 번만)
            if DDRAGON_VER is None:
                try:
                    vers = sess.get(
                        "https://ddragon.leagueoflegends.com/api/versions.json",
                        timeout=5).json()
                    DDRAGON_VER = vers[0] if vers else DDRAGON_VER_FALLBACK
                except Exception:
                    DDRAGON_VER = DDRAGON_VER_FALLBACK
            changed = False
            for key in spell_keys:
                info = SPELL_DATA.get(key)
                if not info or not info[3]:
                    continue
                path = os.path.join(CACHE_DIR, info[3])
                if os.path.exists(path):
                    continue
                url = (f"https://ddragon.leagueoflegends.com/cdn/"
                       f"{DDRAGON_VER}/img/spell/{info[3]}")
                try:
                    r = sess.get(url, timeout=5)
                    if r.status_code == 200:
                        with open(path, "wb") as f:
                            f.write(r.content)
                        changed = True
                except Exception:
                    pass
            if changed:
                on_ready()
        threading.Thread(target=worker, daemon=True).start()


class GameWindowTracker:
    def __init__(self):
        self.hwnd = 0
        self._miss_ticks = 0

    def get_pos(self):
        if not IS_WINDOWS:
            return None
        user32 = ctypes.windll.user32
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            self._miss_ticks += 1
            if self._miss_ticks % 33 != 1:
                return None
            self.hwnd = user32.FindWindowW(None, GAME_WINDOW_TITLE)
            if not self.hwnd:
                return None
            self._miss_ticks = 0

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            self.hwnd = 0
            return None
        return QPoint(rect.left, rect.top)


class Bridge(QObject):
    hotkey = pyqtSignal(int)
    lock_toggle = pyqtSignal()
    poll_done = pyqtSignal(object)
    icons_ready = pyqtSignal()


class SpellButton(QPushButton):
    """아이콘(있으면) 또는 텍스트 + 쿨다운 카운트"""

    def __init__(self, spell_key: str, row, icon_cache: IconCache):
        super().__init__()
        self.row = row
        self.spell_key = spell_key
        self.icon_cache = icon_cache
        name, base_cd, color, _ = SPELL_DATA.get(spell_key, UNKNOWN_SPELL)
        self.spell_name = name
        self.base_cd = base_cd
        self.color = color
        self.end_time = 0.0
        self._visual = ""

        self.setFixedSize(30, 30)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.clicked.connect(lambda: self.toggle())
        self.apply_icon()
        self.refresh()

    def apply_icon(self):
        pm = self.icon_cache.get(self.spell_key)
        if pm is not None:
            self.setIcon(QIcon(pm))
            self.setIconSize(QSize(23, 23))
            self.has_icon = True
        else:
            self.has_icon = False
        self._visual = ""   # 스타일 다시 적용되도록
        self.refresh()

    @property
    def running(self) -> bool:
        return self.end_time > time.time()

    def effective_cd(self) -> float:
        haste = BOOT_STEPS[self.row.boot_index][1]
        if self.row.rune_on:
            haste += RUNE_HASTE
        return self.base_cd * 100 / (100 + haste)

    def toggle(self):
        if self.running:
            self.end_time = 0.0
        else:
            self.end_time = time.time() + self.effective_cd()
        self.refresh()

    def refresh(self):
        if self.running:
            remain = int(self.end_time - time.time() + 0.999)
            m, s = divmod(remain, 60)
            text = f"{m}:{s:02d}" if m else f"{s}"
            if self.text() != text:
                self.setText(text)
            if self._visual != "run":
                self._visual = "run"
                self.setIcon(QIcon())   # 쿨다운 중엔 숫자만
                self.setStyleSheet(
                    "QPushButton { background: rgba(150, 30, 30, 255);"
                    "  color: white; border: 1px solid rgba(255,255,255,70);"
                    "  border-radius: 4px; font-weight: bold; font-size: 12px; }")
        else:
            if self._visual != "idle":
                self._visual = "idle"
                if self.has_icon:
                    self.apply_icon_only()
                else:
                    self.setText(self.spell_name)
                    self.setStyleSheet(
                        "QPushButton {"
                        f" background: rgba(30,34,44,255); color: {self.color};"
                        "  border: 1px solid rgba(255,255,255,40);"
                        "  border-radius: 4px; font-weight: bold; font-size: 10px; }"
                        "QPushButton:hover { border: 1px solid rgba(255,255,255,120); }")

    def apply_icon_only(self):
        self.setText("")
        pm = self.icon_cache.get(self.spell_key)
        if pm is not None:
            self.setIcon(QIcon(pm))
            self.setIconSize(QSize(23, 23))
        self.setStyleSheet(
            "QPushButton { background: rgba(20,22,30,255);"
            "  border: 1px solid rgba(255,255,255,40); border-radius: 4px; }"
            "QPushButton:hover { border: 1px solid rgba(255,255,255,140); }")


class ChampionRow(QWidget):
    def __init__(self, index, champ_name, spell1, spell2, icon_cache,
                 move_cb=None):
        super().__init__()
        self.boot_index = 0
        self.rune_on = False
        self.move_cb = move_cb   # move_cb(row, direction): -1 위, +1 아래

        spell1, spell2 = order_spells(spell1, spell2)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        # 순서 변경 화살표 (위/아래 세로 배치)
        arrows = QVBoxLayout()
        arrows.setContentsMargins(0, 0, 0, 0)
        arrows.setSpacing(0)
        self.up_btn = QPushButton("▲")
        self.down_btn = QPushButton("▼")
        for b, d in ((self.up_btn, -1), (self.down_btn, 1)):
            b.setFixedSize(13, 14)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setStyleSheet(
                "QPushButton { background: transparent; color: #9aa3b2;"
                "  border: none; font-size: 8px; }"
                "QPushButton:hover { color: #f2c14e; }")
            b.clicked.connect(lambda _=False, dd=d: self._move(dd))
        arrows.addWidget(self.up_btn)
        arrows.addWidget(self.down_btn)

        self.num = QLabel(str(index))
        self.num.setFixedWidth(8)
        self.num.setToolTip(f"단축키: Ctrl+{index} → 점멸 타이머")
        self.num.setStyleSheet(
            "color: #c2c8d4; font-size: 9px; font-weight: bold;")

        self.name_label = QLabel(champ_name[:5])
        self.name_label.setFixedWidth(38)
        self.name_label.setStyleSheet(
            "color: #f4f5f7; font-size: 9px; font-weight: bold;")

        self.boot_btn = QPushButton(BOOT_STEPS[0][0])
        self.boot_btn.setFixedSize(20, 30)
        self.boot_btn.setToolTip(
            "장화 토글: 없음 → 아이오니아 장화(+10) → 강화 장화/진홍빛 명료(+20)")
        self.boot_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.boot_btn.clicked.connect(self.cycle_boot)

        self.rune_btn = QPushButton("룬")
        self.rune_btn.setFixedSize(20, 30)
        self.rune_btn.setToolTip("우주적 통찰 룬 토글 (+18)")
        self.rune_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.rune_btn.clicked.connect(self.toggle_rune)

        self._style_toggles()

        self.btn1 = SpellButton(spell1, self, icon_cache)
        self.btn2 = SpellButton(spell2, self, icon_cache)

        lay.addLayout(arrows)
        lay.addWidget(self.num)
        lay.addWidget(self.name_label)
        lay.addWidget(self.boot_btn)
        lay.addWidget(self.rune_btn)
        lay.addWidget(self.btn1)
        lay.addWidget(self.btn2)

    def _move(self, direction):
        if self.move_cb:
            self.move_cb(self, direction)

    def set_index(self, index):
        self.num.setText(str(index))
        self.num.setToolTip(f"단축키: Ctrl+{index} → 점멸 타이머")

    def flash_button(self):
        if self.btn2.spell_name == "점멸":
            return self.btn2
        if self.btn1.spell_name == "점멸":
            return self.btn1
        return self.btn2

    def cycle_boot(self):
        self.boot_index = (self.boot_index + 1) % len(BOOT_STEPS)
        self.boot_btn.setText(BOOT_STEPS[self.boot_index][0])
        self._style_toggles()

    def toggle_rune(self):
        self.rune_on = not self.rune_on
        self._style_toggles()

    def _style_toggles(self):
        def style(btn, level):
            bg = ["rgba(40,44,54,255)", "rgba(70,110,200,255)",
                  "rgba(190,60,60,255)"][level]
            btn.setStyleSheet(
                f"QPushButton {{ background: {bg}; color: #dfe6f0;"
                "  border: 1px solid rgba(255,255,255,40); border-radius: 4px;"
                "  font-size: 9px; }")
        style(self.boot_btn, self.boot_index)
        style(self.rune_btn, 1 if self.rune_on else 0)

    def refresh_icons(self):
        self.btn1.apply_icon()
        self.btn2.apply_icon()

    def tick(self):
        self.btn1.refresh()
        self.btn2.refresh()


class TopButton(QPushButton):
    def __init__(self, text, idle, hover, pressed, size=22):
        super().__init__(text)
        self._styles = {"idle": idle, "hover": hover, "pressed": pressed}
        self.setFixedSize(size, size)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._apply("idle")

    def _apply(self, state):
        self.setStyleSheet(
            "QPushButton { border: none; border-radius: 4px;"
            f" font-size: 12px; {self._styles[state]} }}")

    def enterEvent(self, e):
        self._apply("hover"); super().enterEvent(e)

    def leaveEvent(self, e):
        self._apply("idle"); super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._apply("pressed"); super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._apply("hover" if self.underMouse() else "idle")
        super().mouseReleaseEvent(e)


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowOpacity(0.92)
        self._alpha = 92
        self._drag_pos = QPoint()
        self._dragging = False
        self.rows: list[ChampionRow] = []
        self.loaded = False
        self.demo = False
        self.in_game = False
        self.fail_count = 0
        self.click_through = False
        self.tracker = GameWindowTracker()
        self.game_offset = None
        self.last_game_pos = None
        self._positioned = False   # 첫 표시 때 한 번 가운데 정렬 후 위치 기억
        self.icon_cache = IconCache()

        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = False
        self._polling = False

        self.bridge = Bridge()
        self.bridge.poll_done.connect(self._on_poll_done)
        self.bridge.hotkey.connect(self._on_hotkey)
        self.bridge.lock_toggle.connect(self.toggle_click_through)
        self.bridge.icons_ready.connect(self._refresh_all_icons)

        # ---- 패널 ----
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QFrame()
        self.panel.setStyleSheet(
            "QFrame { background: #3a3f47; border-radius: 8px; }")
        outer.addWidget(self.panel)

        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(7, 5, 7, 6)
        lay.setSpacing(3)

        # ---- 상단 바 ----
        top = QHBoxLayout()
        top.setSpacing(3)
        self.title = QLabel("스펠 타이머")
        self.title.setStyleSheet(
            "color: #f2c14e; font-weight: bold; font-size: 11px;")

        self.lock_icon = QLabel("🔓")
        self.lock_icon.setFixedWidth(16)
        self.lock_icon.setToolTip("잠금 상태 (Alt+A로 토글)")
        self.lock_icon.setStyleSheet("font-size: 11px;")

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(92)
        self.opacity_slider.setFixedWidth(60)
        self.opacity_slider.setToolTip("오버레이 투명도")
        self.opacity_slider.valueChanged.connect(self._set_opacity)
        self.opacity_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px;"
            "  background: rgba(255,255,255,50); border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 11px; margin: -4px 0;"
            "  background: #f2c14e; border-radius: 5px; }")

        hide_btn = TopButton(
            "—",
            idle="background: transparent; color: #aab;",
            hover="background: rgba(242,193,78,70); color: #f2c14e;",
            pressed="background: rgba(242,193,78,160); color: #1a1c22;")
        hide_btn.setToolTip("숨기기 (트레이 아이콘으로 다시 열기)")
        hide_btn.clicked.connect(self._hide_with_tray_tip)

        close_btn = TopButton(
            "✕",
            idle="background: transparent; color: #aab;",
            hover="background: rgba(220,70,70,110); color: white;",
            pressed="background: rgba(220,70,70,220); color: white;")
        close_btn.setToolTip("완전히 종료")
        close_btn.clicked.connect(QApplication.quit)

        top.addWidget(self.title)
        top.addWidget(self.lock_icon)
        top.addStretch()
        top.addWidget(self.opacity_slider)
        top.addWidget(hide_btn)
        top.addWidget(close_btn)
        lay.addLayout(top)

        self.status = QLabel("대기 중 — 게임이 시작되면 자동으로 나타나요")
        self.status.setStyleSheet("color: #d8dce2; font-size: 10px;")
        self.status.setFixedWidth(196)
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        if HAS_KEYBOARD:
            hk = QLabel("Ctrl+1~5: 점멸 · Alt+A: 잠금")
            hk.setStyleSheet("color: #aeb4c0; font-size: 9px;")
            lay.addWidget(hk)

        self.demo_btn = QPushButton("연습 모드로 띄워보기")
        self.demo_btn.setStyleSheet(
            "QPushButton { background: rgba(40,44,54,255); color: #cdd;"
            "  border: 1px solid rgba(255,255,255,40); border-radius: 4px;"
            "  padding: 3px; font-size: 10px; }"
            "QPushButton:hover { border: 1px solid rgba(255,255,255,120); }")
        self.demo_btn.clicked.connect(self.load_demo)
        lay.addWidget(self.demo_btn)

        self.exit_demo_btn = QPushButton("연습 모드 종료")
        self.exit_demo_btn.setStyleSheet(
            "QPushButton { background: rgba(60,44,44,255); color: #e0b0b0;"
            "  border: 1px solid rgba(255,150,150,60); border-radius: 4px;"
            "  padding: 3px; font-size: 10px; }"
            "QPushButton:hover { border: 1px solid rgba(255,150,150,160); }")
        self.exit_demo_btn.clicked.connect(self._exit_demo)
        self.exit_demo_btn.hide()
        lay.addWidget(self.exit_demo_btn)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(2)
        lay.addLayout(self.rows_box)

        self._make_tray()
        self._register_hotkeys()

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(200)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_game)
        self.poll_timer.start(2000)

        self.follow_timer = QTimer(self)
        self.follow_timer.timeout.connect(self.follow_game_window)
        self.follow_timer.start(16)   # 약 60fps, 더 촘촘하게

    # ---------- 아이콘 ----------
    def _refresh_all_icons(self):
        for row in self.rows:
            row.refresh_icons()

    def _ensure_icons(self, keys):
        self.icon_cache.ensure_async(
            set(k for k in keys if k),
            lambda: self.bridge.icons_ready.emit())

    # ---------- 클릭 통과 ----------
    def toggle_click_through(self, force=None):
        new_state = (not self.click_through) if force is None else force
        self.click_through = new_state
        self._apply_click_through()
        self.lock_icon.setText("🔒" if new_state else "🔓")
        if new_state:
            self.status.setText("잠금 중 — 점멸: Ctrl+1~5 · 해제: Alt+A")
        else:
            self.status.setText(
                "적 스펠 로드 완료 — 클릭하면 타이머 시작" if self.loaded
                else "대기 중 — 게임이 시작되면 자동으로 나타나요")
        self._update_lock_menu()

    def _set_opacity(self, v):
        self._alpha = max(30, min(100, v))
        self.setWindowOpacity(self._alpha / 100)
        # 클릭 통과(LAYERED) 상태에서도 알파가 유지되도록 다시 적용
        if IS_WINDOWS and self.isVisible():
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            WS_EX_LAYERED = 0x80000
            GWL_EXSTYLE = -20
            LWA_ALPHA = 0x2
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(
                hwnd, 0, int(self._alpha / 100 * 255), LWA_ALPHA)

    def _apply_click_through(self):
        if not IS_WINDOWS or not self.isVisible():
            return
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x20
        WS_EX_LAYERED = 0x80000
        LWA_ALPHA = 0x2
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if self.click_through:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT
            style |= WS_EX_LAYERED
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        # LAYERED 창은 알파가 한 번 설정돼 있어야 클릭 통과가 동작함
        user32.SetLayeredWindowAttributes(
            hwnd, 0, int(self._alpha / 100 * 255), LWA_ALPHA)
        # 스타일 변경을 즉시 반영 (이게 없으면 값만 바뀌고 적용 안 됨)
        SWP_NOMOVE = 0x2
        SWP_NOSIZE = 0x1
        SWP_NOZORDER = 0x4
        SWP_FRAMECHANGED = 0x20
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)

    def _visible_enough(self):
        """오버레이가 어느 모니터에든 절반 이상 보이면 True (화면 밖 판정용)"""
        fr = self.frameGeometry()
        win_area = max(1, fr.width() * fr.height())
        best = 0
        for scr in QApplication.screens():
            inter = fr.intersected(scr.availableGeometry())
            if not inter.isEmpty():
                best = max(best, inter.width() * inter.height())
        return best >= win_area * 0.5

    def _center_on_screen(self):
        """커서가 있는 모니터 가운데로 오버레이 이동 (구석/화면 밖 방지)"""
        self.adjustSize()
        screen = (QApplication.screenAt(QCursor.pos())
                  or QApplication.primaryScreen())
        if screen is None:
            return
        geo = screen.availableGeometry()
        fr = self.frameGeometry()
        x = geo.x() + (geo.width() - fr.width()) // 2
        y = geo.y() + (geo.height() - fr.height()) // 2
        self.move(x, y)

    def show_overlay(self):
        """창을 확실하게 보이게: 잠금/투명 스타일을 풀고 최상단으로 끌어올림"""
        # 잠금이 남아 있으면 해제 (투명 통과 상태로 안 보이는 것 방지)
        if self.click_through:
            self.toggle_click_through(force=False)
        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        # 첫 표시이거나 화면 밖으로 나갔을 때만 가운데로 (그 외엔 마지막 위치 유지)
        if not self._positioned or not self._visible_enough():
            self._center_on_screen()
            # 추적 오프셋 리셋 → 다음 추적부터 이 중앙 위치 기준으로 재계산
            self.game_offset = None
            self._positioned = True
        # Windows에서 확실히 최상단 + 보이게 강제
        if IS_WINDOWS:
            try:
                user32 = ctypes.windll.user32
                hwnd = int(self.winId())
                GWL_EXSTYLE = -20
                WS_EX_TRANSPARENT = 0x20
                WS_EX_LAYERED = 0x80000
                LWA_ALPHA = 0x2
                # 투명 통과 스타일 제거, 알파 복구
                style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                style &= ~WS_EX_TRANSPARENT
                style |= WS_EX_LAYERED
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
                user32.SetLayeredWindowAttributes(
                    hwnd, 0, int(self._alpha / 100 * 255), LWA_ALPHA)
                # 최상단으로 (HWND_TOPMOST = -1)
                SWP_NOMOVE = 0x2
                SWP_NOSIZE = 0x1
                SWP_SHOWWINDOW = 0x40
                user32.SetWindowPos(
                    hwnd, -1, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            except Exception:
                _log_error("show_overlay")

    def showEvent(self, e):
        super().showEvent(e)
        if self.click_through:
            QTimer.singleShot(0, self._apply_click_through)

    # ---------- 단축키 ----------
    def _register_hotkeys(self):
        if not HAS_KEYBOARD:
            return
        try:
            for i in range(1, 6):
                keyboard.add_hotkey(
                    f"ctrl+{i}",
                    lambda n=i: self.bridge.hotkey.emit(n), suppress=False)
            keyboard.add_hotkey(
                "alt+a",
                lambda: self.bridge.lock_toggle.emit(), suppress=False)
        except Exception:
            pass

    def _on_hotkey(self, n):
        if 1 <= n <= len(self.rows):
            self.rows[n - 1].flash_button().toggle()

    # ---------- 트레이 ----------
    def _tray_icon(self):
        """spelltimer.ico를 로드. 없으면 예전처럼 코드로 그린 아이콘으로 폴백."""
        icon = QIcon(ICON_PATH)
        if not icon.isNull():
            return icon
        pix = QPixmap(32, 32)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#f2c14e"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 28, 28)
        p.setPen(QColor("#1a1c22"))
        p.setFont(QFont("Malgun Gothic", 13, QFont.Weight.Bold))
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "F")
        p.end()
        return QIcon(pix)

    def _make_tray(self):
        icon = self._tray_icon()
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("롤 스펠 타이머")
        menu = QMenu()
        act_show = QAction("보이기", self)
        act_show.triggered.connect(self.show_overlay)
        self.act_lock = QAction("클릭 통과 잠금", self)
        self.act_lock.triggered.connect(lambda: self.toggle_click_through())
        act_demo = QAction("연습 모드", self)
        act_demo.triggered.connect(lambda: (self.show_overlay(), self.load_demo()))
        act_quit = QAction("종료", self)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_show)
        menu.addAction(self.act_lock)
        menu.addAction(act_demo)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.show_overlay()
            if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _update_lock_menu(self):
        self.act_lock.setText(
            "클릭 통과 해제" if self.click_through else "클릭 통과 잠금")

    def _hide_with_tray_tip(self):
        self.hide()
        self.tray.showMessage(
            "스펠 타이머", "숨겨졌어요. 트레이 아이콘을 클릭하면 다시 열려요.",
            QSystemTrayIcon.MessageIcon.Information, 1500)

    # ---------- 게임 감지 ----------
    def poll_game(self):
        if self._polling:
            return
        self._polling = True
        threading.Thread(target=self._poll_worker, daemon=True).start()

    def _poll_worker(self):
        try:
            players = self.session.get(
                f"{API_BASE}/playerlist", timeout=1.5).json()
            me = self.session.get(
                f"{API_BASE}/activeplayername", timeout=1.5).json()
            self.bridge.poll_done.emit((players, me))
        except Exception:
            self.bridge.poll_done.emit(None)

    def _on_poll_done(self, data):
        self._polling = False
        try:
            if data is None:
                if self.in_game:
                    self.fail_count += 1
                    if self.fail_count >= 3:
                        self._on_game_end()
                return
            players, me = data
            self.fail_count = 0
            if not self.in_game:
                self.in_game = True
                self.demo = False
                self.show_overlay()
                self.tray.showMessage(
                    "스펠 타이머", "게임 감지! 오버레이를 표시합니다.",
                    QSystemTrayIcon.MessageIcon.Information, 2000)
            if not self.loaded:
                self._load_enemies(players, me)
        except Exception:
            _log_error("on_poll_done")
            self.status.setText("적 정보 처리 중 오류 — 잠시 후 다시 시도해요")

    def _on_game_end(self):
        self.in_game = False
        self.loaded = False
        self.fail_count = 0
        self.game_offset = None
        self.last_game_pos = None
        self.tracker.hwnd = 0
        for row in self.rows:
            row.setParent(None)
        self.rows.clear()
        if self.click_through:
            self.toggle_click_through(force=False)
        self.status.setText("대기 중 — 게임이 시작되면 자동으로 나타나요")
        self.demo_btn.show()
        self.exit_demo_btn.hide()
        self.adjustSize()
        if not self.demo:
            self.hide()

    def _load_enemies(self, players, me):
        try:
            # playerlist가 예상과 다른 형태로 올 수 있어 방어적으로 정규화
            if isinstance(players, dict):
                # 혹시 {"allPlayers": [...]} 같은 형태면 리스트를 꺼냄
                for v in players.values():
                    if isinstance(v, list):
                        players = v
                        break
            if not isinstance(players, list):
                _log_diag("playerlist_not_list", players)
                self.status.setText("적 정보 형식 확인 중...")
                return

            # 딕셔너리인 플레이어만 추림 (문자열 등 섞여 와도 무시)
            valid = [p for p in players if isinstance(p, dict)]
            if not valid:
                _log_diag("no_dict_players", players)
                self.status.setText("적 정보를 찾는 중...")
                return

            me_str = me if isinstance(me, str) else ""

            def is_me(p):
                return (p.get("summonerName") == me_str
                        or p.get("riotIdGameName") == me_str
                        or p.get("riotId", "").split("#")[0] == me_str)

            my_team = next((p.get("team") for p in valid if is_me(p)), None)

            if my_team:
                enemies = [p for p in valid if p.get("team") != my_team]
            else:
                # 내 팀을 못 찾으면 (관전/이름 매칭 실패) 일단 5명 단위로 추정
                enemies = valid[5:] if len(valid) > 5 else valid

            if not enemies:
                self.status.setText("적 정보를 찾는 중...")
                return

            rows_data = []
            for p in enemies:
                champ = (p.get("championName")
                         or p.get("rawChampionName") or "?")
                spells = p.get("summonerSpells")
                spells = spells if isinstance(spells, dict) else {}
                one = spells.get("summonerSpellOne")
                two = spells.get("summonerSpellTwo")
                s1 = one.get("rawDisplayName", "") if isinstance(one, dict) else ""
                s2 = two.get("rawDisplayName", "") if isinstance(two, dict) else ""
                rows_data.append(
                    (champ, spell_key_from_raw(s1), spell_key_from_raw(s2)))
            self._build_rows(rows_data)
            self.status.setText("적 스펠 로드 완료 — 클릭하면 타이머 시작")
        except Exception:
            _log_error("load_enemies")
            _log_diag("load_enemies_exc_players", players)
            self.status.setText("적 정보 형식이 예상과 달라요 (로그 기록됨)")

    def load_demo(self):
        self.demo = True
        self._build_rows([
            ("아리", "SummonerDot", "SummonerFlash"),
            ("리신", "SummonerSmite", "SummonerFlash"),
            ("가렌", "SummonerTeleport", "SummonerFlash"),
            ("진",   "SummonerHeal", "SummonerFlash"),
            ("쓰레쉬", "SummonerExhaust", "SummonerFlash"),
        ])
        self.status.setText("연습 모드 — 클릭하면 타이머 시작")
        self.exit_demo_btn.show()
        self.adjustSize()

    def _exit_demo(self):
        self.demo = False
        self.loaded = False
        for row in self.rows:
            row.setParent(None)
        self.rows.clear()
        self.status.setText("대기 중 — 게임이 시작되면 자동으로 나타나요")
        self.exit_demo_btn.hide()
        self.demo_btn.show()
        self.adjustSize()

    def _build_rows(self, data):
        for row in self.rows:
            row.setParent(None)
        self.rows.clear()
        all_keys = []
        for i, (champ, s1, s2) in enumerate(data, start=1):
            row = ChampionRow(i, champ, s1, s2, self.icon_cache,
                              move_cb=self._move_row)
            self.rows.append(row)
            self.rows_box.addWidget(row)
            all_keys += [s1, s2]
        self.loaded = True
        self.demo_btn.hide()
        if not self.demo:
            self.exit_demo_btn.hide()
        self.adjustSize()
        self._ensure_icons(all_keys)   # 없는 아이콘은 백그라운드로 받아오기

    def _move_row(self, row, direction):
        """챔피언 줄 순서 변경 (위 -1 / 아래 +1). 번호·단축키도 따라감."""
        i = self.rows.index(row)
        j = i + direction
        if not (0 <= j < len(self.rows)):
            return
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        # 레이아웃에서 모두 떼고 새 순서로 다시 추가
        for r in self.rows:
            self.rows_box.removeWidget(r)
        for idx, r in enumerate(self.rows, start=1):
            self.rows_box.addWidget(r)
            r.set_index(idx)
        self.adjustSize()

    def tick(self):
        for row in self.rows:
            row.tick()

    # ---------- 롤 창 위치 추적 ----------
    def follow_game_window(self):
        if not self.isVisible() or self._dragging:
            return
        game_pos = self.tracker.get_pos()
        if game_pos is None:
            return
        if self.game_offset is None:
            self.game_offset = self.pos() - game_pos
            self.last_game_pos = game_pos
            return
        if game_pos != self.last_game_pos:
            self.move(game_pos + self.game_offset)
            self.last_game_pos = game_pos

    # ---------- 둥근 모서리 ----------
    def resizeEvent(self, e):
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 8, 8)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        super().resizeEvent(e)

    # ---------- 창 드래그 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._dragging = False
        game_pos = self.tracker.get_pos()
        if game_pos is not None:
            self.game_offset = self.pos() - game_pos


def main():
    _install_excepthook()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Malgun Gothic", 9))
    app.setWindowIcon(QIcon(ICON_PATH))
    w = Overlay()
    w.show_overlay()   # 첫 실행도 가운데 정렬 경로를 타게
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
