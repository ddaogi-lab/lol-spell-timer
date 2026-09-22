# LoL Spell Timer

리그 오브 레전드 게임 화면 위에 뜨는 **점멸 스펠 타이머 오버레이**. 적이 점멸 쓰는 걸 봤을 때 단축키(또는 클릭)로 쿨타임을 돌린다. Riot 공식 **Live Client Data API**만 사용하며, 게임 메모리를 읽지 않아 제재 위험이 없다.

> 📝 만든 과정과 삽질 기록: [블로그 글](https://blog.ddaogi96.com/blog/lol-spell-timer/)

## 주요 기능

- 인게임 진입 시 자동으로 적 5명의 챔피언·스펠을 불러와 오버레이 표시 (트레이 상주 → 자동 표시/숨김)
- 스펠 아이콘 클릭 또는 `Ctrl+1~5`로 해당 적 점멸 타이머 시작/취소
- 쿨감 토글: 아이오니아 장화(+10) / 강화 장화(+20) / 우주적 통찰 룬(+18)
- 창모드에서 롤 창을 옮기면 오버레이가 따라다님, 투명도 조절, 클릭 통과(`Alt+A`)
- 표시될 때 화면 가운데에서 시작하고, 화면 밖으로 벗어나면 자동으로 되돌아옴
- 스펠 아이콘은 Riot Data Dragon에서 받아 로컬 캐시 (최신 버전 자동 조회)

## 단축키

| 키 | 기능 |
| --- | --- |
| `Ctrl+1~5` | 1~5번째 적 점멸 타이머 토글 |
| `Alt+A` | 클릭 통과(잠금) 토글 |

## ⚠️ 자동 감지는 하지 않는다

이 도구는 **"내가 직접 본 것"을 빠르게 입력**하는 보조 도구다. 적 스펠 사용을 **자동으로 감지하지 않는다.** 자동 감지는 Riot 서드파티 정책 위반이라 의도적으로 만들지 않았다. (공식 API는 적 스펠 사용 시점을 주지 않고, 게임 메모리 읽기는 Vanguard 제재 대상이다.)

## 실행

[Releases](../../releases)에서 `SpellTimer.exe`를 받아 실행한다. (롤을 **창모드 / 테두리 없음**으로 해야 오버레이가 동작)

## 직접 빌드

```bat
pip install PyQt6 requests keyboard pyinstaller
build_exe.bat
```

또는 직접:

```bat
python -m PyInstaller --onefile --windowed --icon spelltimer.ico --add-data "spelltimer.ico;." --name SpellTimer lol_spell_timer.py
```

## 기술 스택

- Python 3 · **PyQt6** (프레임 없는 항상-위 투명 오버레이)
- **Riot Live Client Data API** (`https://127.0.0.1:2999/liveclientdata`, 자체 서명 인증서라 `verify=False`)
- **Windows API (ctypes)** — 롤 창 추적, 클릭 통과(레이어드 윈도우)
- `keyboard` (전역 단축키) · Riot **Data Dragon** (스펠 아이콘)
