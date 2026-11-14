# Robot AI Demo · 남원고 4차시

**현재 환경을 읽고, LLM이 행동 하나를 고르고, 게임이 결과를 보여 주는 작은 실습입니다.**

가로 3칸 × 세로 10칸 맵에 무작위로 놓인 장애물(벽돌·공사중 표지판)을 피해 끝까지 완주하는 게임입니다. 위에서 내려다본 2D 화면에서 로봇 줄은 고정되고 맵이 위에서 아래로 다가옵니다. Space 한 번에 Groq API를 한 번 호출하고, 로봇이 왼쪽·오른쪽·정지 중 하나를 실행하면 맵이 한 줄 내려옵니다. 매번 바뀐 3×3칸·최근 행동·피드백을 보내므로, 이전 행동이 다음 판단의 입력이 되는 과정을 관찰할 수 있습니다. 완주를 보장하지 않습니다.

| 구분 | 크기 |
|---|---|
| 전체 맵 (미니맵으로 표시) | 3×10칸 |
| 화면에 확대해 보이는 구간 | 3×5칸 (로봇 줄 + 앞 4줄) |
| AI에게 전달되는 구간 | 3×3칸 (로봇 줄 + 앞 2줄) |

**규칙**: 목숨 3개로 시작하고 장애물에 부딪히면 1개를 잃습니다. 목숨이 0이 되거나 10줄을 모두 지나면 끝나며, 최종 화면에 진행거리(n/10)·남은 목숨·피한 장애물의 수·걸린 시간을 표시합니다. 걸린 시간은 AI 통신과 로봇 이동에 쓴 실제 시간만 더하고, 턴과 턴 사이에 기다린 시간은 세지 않습니다.

**장애물 생성**: 한 줄에 1~2개가 놓입니다. 같은 세로줄에는 최대 2줄까지만 연속으로 나오고, 2개짜리 줄은 연속해서 나오지 않습니다. 이 규칙에서는 부딪히지 않고 완주할 길이 항상 존재합니다(자동 테스트로 확인).

**재현성**: 맵은 random seed로 정해집니다. 같은 seed면 항상 같은 맵입니다. `scenario.json`의 `"seed"` 값을 바꾸거나(`null`이면 실행마다 새 맵) `main.py --seed 7`처럼 명령줄로 지정합니다. 현재 seed는 화면 오른쪽 위와 로그에 표시됩니다.

이 프로그램은 **주변 칸 정보를 텍스트로 입력하는 LLM 에이전트**입니다. 이미지 입력을 받는 VLA 모델이나 실제 로봇 제어는 구현하지 않았습니다. 모델을 학습시키지도 않습니다.

## 1. 코드 받아서 실행하기 (Windows)

Python **3.11 이상**과 Git이 필요합니다. Python은 [공식 다운로드](https://www.python.org/downloads/)에서 설치합니다. 아래 순서는 프로젝트 폴더에서 `git init`과 `git remote add origin ...`까지 마친 상태를 기준으로 합니다.

VS Code에서 **파일 → 폴더 열기**로 `git init`을 한 폴더를 열고, **터미널 → 새 터미널**(PowerShell)에서 차례로 실행합니다.

**① 원격 주소 확인** — `origin`에 이 저장소 주소가 나오는지 봅니다.

```powershell
git remote -v
```

**② 코드 받기**

```powershell
git pull origin main
```

받은 뒤 폴더에 `main.py`, `game.py`, `README.md`가 보이면 성공입니다. 나중에 코드가 수정되면 같은 명령으로 최신 내용을 다시 받습니다.

**③ 가상환경 만들고 패키지 설치** (처음 한 번)

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`python`을 찾지 못하면 첫 명령을 `py -m venv .venv`로 바꿔 보세요. 가상환경 활성화 없이 `.venv`의 Python을 직접 사용하므로 PowerShell 실행 정책을 바꾸지 않아도 됩니다.

**④ API 키 설정** (처음 한 번)

```powershell
Copy-Item .env.example .env
```

멘토가 준비한 Groq API 키를 받아 `.env`를 열고 `GROQ_API_KEY=` 바로 뒤에 따옴표 없이 붙여 넣고 저장합니다. 학생이 Groq에 가입할 필요는 없습니다. 본인 계정의 키를 쓰려면 [Groq API Keys](https://console.groq.com/keys)에서 발급합니다. 이미 `.env`를 설정했다면 복사 명령을 다시 실행하지 마세요.

**⑤ 연결 확인** — 실제 API를 한 번 호출합니다. `[실제 API 응답]`과 `action`이 출력되면 성공입니다.

```powershell
.\.venv\Scripts\python.exe main.py --check-api
```

**⑥ 게임 실행** — 두 번째 실행부터는 이 명령만 쓰면 됩니다.

```powershell
.\.venv\Scripts\python.exe main.py
```

VS Code에서 `Ctrl+Shift+P` → **Python: Select Interpreter** → `.venv`를 선택해 두면 편집기의 오류 표시도 같은 환경을 따릅니다.

키는 `.env`에만 넣습니다. `.gitignore`가 `.env`·`.venv`·로그를 제외하므로 commit·push해도 키는 올라가지 않습니다. `.env`를 바꿨다면 앱을 종료하고 다시 실행하세요. 같은 이름의 OS 환경변수가 있다면 그 값이 우선합니다.

### 코드 받기가 안 될 때

| 표시 | 해결 |
|---|---|
| `fatal: not a git repository` | 터미널이 `git init`을 한 폴더에서 열렸는지 확인합니다. VS Code의 ‘폴더 열기’로 그 폴더를 다시 엽니다. |
| `fatal: 'origin' does not appear to be a git repository` | 원격 설정이 빠졌습니다. `git remote add origin https://github.com/spacenship/robotAIdemo.git` |
| `git remote -v`의 주소가 다름 | `git remote set-url origin https://github.com/spacenship/robotAIdemo.git` |
| `untracked working tree files would be overwritten` | 폴더에 같은 이름의 파일이 이미 있습니다. 그 파일을 다른 폴더로 옮긴 뒤 다시 pull합니다. |
| `refusing to merge unrelated histories` | 이 폴더에서 이미 commit을 만든 경우입니다. 아래처럼 새 폴더에 clone으로 받는 편이 간단합니다. |
| `python`과 `py`를 모두 찾지 못함 | Python 설치와 터미널 재시작을 확인합니다. |

`git init`을 하지 않은 새 PC에서는 clone 한 번으로 받을 수 있습니다. 이때는 새로 생긴 `robotAIdemo` 폴더를 VS Code로 열고 ③부터 진행합니다.

```powershell
git clone https://github.com/spacenship/robotAIdemo.git
```

### macOS / Linux

```bash
git pull origin main
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# .env에 GROQ_API_KEY 입력
.venv/bin/python main.py --check-api
.venv/bin/python main.py
```

## 2. 조작과 관찰

| 입력 | 결과 |
|---|---|
| Space | 현재 관측 전송 → 실제 LLM 응답 → 한 턴(좌우 이동 + 맵 한 줄 전진) |
| R | 같은 맵(같은 seed)으로 처음부터. 앱 실행 중 API 요청 수는 유지 |
| N | 새 seed를 뽑아 새 맵으로 시작 |
| F5 | 수정한 `scenario.json` 다시 읽기. 게임 초기화 |
| M | LLM 모드 ↔ 직접 조종 모드 전환 |
| ← → ↑ | 직접 조종 모드에서만 왼쪽·오른쪽·정지(직진). API 호출 없음 |
| Esc / 창 닫기 | 종료 |

API를 기다리는 중에는 R·N·F5·M·추가 요청을 막습니다. 정상 응답 뒤에도 최소 3초 요청 간격이 적용됩니다. 오류 시에는 이동하지 않으며 자동 재시도하지 않습니다. 키가 없어도 창을 열고 **M**으로 게임 규칙을 확인할 수 있습니다. 직접 조종 결과는 LLM 결과와 구분해 표시합니다.

화면 위쪽에서 현재 상태(준비 / API 요청 중 / 이동 중 / 완주 성공 / 게임 오버 / 오류 / 직접 조종)와 seed를, 왼쪽에서 확대 화면(초록 테)·미니맵·목숨을, 오른쪽에서 AI에 전달되는 3×3칸(주황 테), 허용 행동, 제공자·모델, API 요청 수, 응답 시간, action·reason, 최근 5턴 이력을 봅니다. 충돌이나 벽에 막힌 행동은 이력에 빨간색으로 표시되고, 그 결과는 다음 요청의 `last_feedback`으로 LLM에 전달됩니다. 전체 관측 JSON과 응답 원문은 터미널 및 `logs/session_*.jsonl`에 기록합니다. API 키나 인증 헤더는 기록하지 않습니다.

## 3. 코드 읽기: 네 군데만 먼저 보기

| 파일 / 함수 | 역할 | 학생이 해 볼 수정 |
|---|---|---|
| `scenario.json` | 제목·지시문·seed | 게임 이름, 지시문, seed |
| `game.py` 위쪽 상수 / `generate_map()` | 맵 크기·목숨·장애물 생성 규칙 | 맵 길이, 2개짜리 줄 확률, 새 장애물 종류 |
| `game.py` / `get_observation()` | 현재 상태를 JSON으로 만들기 | AI가 보는 줄 수, 새 상태 추가 |
| `llm_client.py` / `choose_action()` | 실제 API에 요청하고 JSON 읽기 | `SYSTEM_PROMPT`의 행동 조건 수정 |
| `game.py` / `apply_action()` | 한 턴 실행: 이동·충돌·목숨 계산 | 아이템·점수·성공 조건 추가 |
| `main.py` | 키 입력, API 대기, 결과 적용 | 처음에는 읽기만 해도 충분 |
| `view.py` | 확대 화면·미니맵·최종 화면 그리기 | 색·그림 수정 |

열 번호는 0=왼쪽, 1=가운데, 2=오른쪽입니다. LLM에 보내는 관측의 핵심은 아래와 같은 3×3칸입니다. `rows_ahead`가 0인 줄이 로봇이 있는 줄이고, `X`는 장애물, `.`은 빈칸, `R`은 로봇입니다.

```json
"view": [
  {"rows_ahead": 2, "cells": ["X", "X", "."]},
  {"rows_ahead": 1, "cells": ["X", ".", "."]},
  {"rows_ahead": 0, "cells": [".", "R", "."]}
]
```

```json
{"action": "stay", "reason": "바로 앞 칸이 비어 있어 그대로 갑니다."}
```

위 JSON은 **응답 형식 예시**입니다. 실제 출력은 모델이 결정합니다. 가능한 행동은 `left`(왼쪽 1칸), `right`(오른쪽 1칸), `stay`(정지)이며, 어떤 행동이든 실행 뒤에는 맵이 한 줄 내려옵니다. `legal_actions`는 벽 밖으로 나가는 행동만 빼 주며, 장애물을 피하는 방향을 추천하지 않습니다. 벽 쪽 행동을 받으면 열을 바꾸지 않고 그대로 전진합니다. JSON 형식이 정확해도 판단은 틀릴 수 있어 게임에서 다시 검사합니다.

장애물·로봇 그림은 `view.py`가 직접 그립니다. `assets/` 폴더에 `brick.png`, `sign.png`, `robot.png`를 넣으면 그 그림을 대신 사용합니다(정사각형 PNG 권장).

## 4. 학생 아이디어를 반영하는 순서

1. **주제와 목표 한 문장**을 정합니다. 예: 창고 로봇이 물건을 배송한다.
2. 우선 `scenario.json`의 제목·지시문·seed를 바꾸고 F5로 확인합니다. 이 단계는 외형과 맵 변경입니다.
3. 새 규칙이 필요하면 ‘관측할 상태 / 선택할 행동 / 성공·실패 조건’을 먼저 적습니다.
4. `Game` 상태 → `get_observation()` → `apply_action()` → 화면 순으로 수정합니다.
5. 행동 종류까지 추가한다면 `ACTIONS`, `ACTION_SCHEMA`, 프롬프트, 게임 규칙을 함께 수정합니다.

**지시문에 ‘짐을 집어라’만 추가해도 집기 기능이 생기지는 않습니다.** 배터리·배송·순찰도 해당 상태와 규칙을 코드로 구현해야 합니다. 오늘은 맵 변경 하나와 규칙 변경 하나를 목표로 잡으면 좋습니다.

비교 실험: 같은 seed(같은 맵)에서 지시문이나 모델만 바꿔 보고, R로 처음 상태를 맞춘 뒤 최종 화면의 진행거리·남은 목숨·피한 장애물 수·걸린 시간을 기록합니다. 모델 응답은 실행마다 달라질 수 있습니다.

## 5. 무료 API 모델 추천

공식 문서 확인일: **2026-11-08**. 제공 모델과 한도는 바뀔 수 있으므로 수업 전 [계정 Limits](https://console.groq.com/settings/limits)에서 확인합니다.

| 용도 | Groq 모델 ID | 이유 |
|---|---|---|
| 기본 실습 | `openai/gpt-oss-20b` | 무료 플랜 대상, 작은 텍스트 상태에서 행동 선택 실습, Strict JSON Schema 지원 |
| 비교 실험 | `openai/gpt-oss-120b` | 같은 JSON 구조로 모델을 바꿔 판단 결과를 비교할 수 있음 |

두 모델의 문서상 무료 기본 한도는 각각 **분당 30요청, 하루 1,000요청, 분당 8,000토큰, 하루 200,000토큰**입니다. 요청 수·토큰 수 중 어느 한도든 먼저 닿으면 제한됩니다. 실제 계정 한도가 우선하며, 같은 조직의 사용량은 합산됩니다. 5명이 한 조직에서 동시에 실행하면 3초 간격만으로 제한을 피할 수 있다는 보장은 없습니다. 교사 시연 후 순서대로 실행하고 429가 뜨면 기다렸다가 다시 Space를 누릅니다.

모델 변경은 `.env`의 `GROQ_MODEL=openai/gpt-oss-120b`를 저장하고 재실행하면 됩니다. 현재 코드는 이 두 모델의 Strict JSON Schema와 reasoning 설정을 기준으로 만들었습니다. 다른 모델 이름만 넣으면 호환되지 않을 수 있습니다. Groq는 실행 서비스이고 모델 개발사는 OpenAI입니다. OpenAI API 키가 아니라 **Groq API 키**를 사용합니다.

공식 참고 자료:

- [Groq Quickstart](https://console.groq.com/docs/quickstart)
- [GPT-OSS 20B 모델](https://console.groq.com/docs/model/openai/gpt-oss-20b)
- [Structured Outputs](https://console.groq.com/docs/structured-outputs)
- [무료 플랜 Rate Limits](https://console.groq.com/docs/rate-limits)
- [Billing FAQ](https://console.groq.com/docs/billing-faqs)
- [GitHub 저장소 복제](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)

## 6. 오류 해결

| 표시 | 확인할 것 |
|---|---|
| API 키 없음 / 401 | `.env` 파일명, 키 값, Groq에서 발급했는지, 앱 재시작 |
| 403 | 계정의 모델 접근 권한 |
| 429 | 요청·토큰 한도. 자동 재시도 없이 잠시 후 다시 Space |
| 400 / 404 / 422 | 모델 ID, 모델의 Strict JSON Schema 지원 여부 |
| 시간 초과 / 연결 실패 | 인터넷, 학교 네트워크, Groq 서비스 상태 |
| 토큰 한도로 중단 | `.env`의 `MAX_COMPLETION_TOKENS=2048`로 올리고 재실행. 토큰 한도 소모 증가에 유의 |
| 한글이 안 보임 | `assets/NanumGothic-Regular.ttf` 포함 여부 |
| 설정 오류 | `scenario.json`의 title·instruction이 문자열인지, seed가 정수 또는 null인지 |

## 7. 검증 명령

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

자동 테스트는 게임 규칙, SDK 요청 형식, 응답 파싱, 오류 처리, 중복 요청 방지를 **모의 HTTP 응답**으로 확인합니다. 실제 모델 응답 품질이나 계정 연결을 증명하지는 않습니다. `main.py --check-api`는 자신의 키로 실제 API를 딱 한 번 호출하므로 별도로 실행합니다.

2026-11-13에 Windows 11 · Python 3.13.0에서 자동 테스트 22개 통과와, 창 없이 렌더링한 화면(시작·이동 중·게임 오버·완주)을 확인했습니다. `--check-api`로 실제 API 응답 1회도 확인했습니다. 실제 창에서의 키 조작과 LLM으로 한 판을 끝까지 진행하는 것은 이 버전에서 아직 확인하지 못했으므로 수업 전에 게임을 실행해 확인해야 합니다.

한글 폰트는 시스템의 나눔스퀘어를 우선 사용하고, 없으면 동봉한 나눔고딕을 사용합니다. 배포 폰트의 저작권·라이선스는 `assets/OFL.txt`에 있습니다.
