# LLM API로 움직이는 로봇 (남원고 3차시 실습)

5×5 격자 위의 로봇이 장애물을 피해 초록색 목표까지 가도록, **실제 LLM API(GroqCloud)에 다음 행동을 물어보는** 작은 실습입니다.
스페이스바를 한 번 누를 때마다 `관측 → 행동 선택 → 실행 → 재관측`이 한 바퀴 돕니다. 로봇이 어디로 갈지는 코드가 정하지 않고, **API 응답**이 정합니다.

- 사용 기술: Python 3.11 이상, Pygame, Groq Python SDK, python-dotenv
- 기본 모델: `openai/gpt-oss-20b` (`.env`의 `GROQ_MODEL`로 바꿀 수 있습니다)
- 검증 환경: Windows 11 · Python 3.13.0 · groq 1.7.0 · pygame 2.6.1 · python-dotenv 1.2.4 (자료 확인 기준 2026-11-01)

## 1. 설치와 실행

`robot_api_demo` 폴더에서 진행합니다. 가상환경을 만들어 이 프로젝트 전용 패키지만 설치합니다. 가상환경을 활성화하지 않고 `.venv`의 Python을 직접 실행하므로 PowerShell 실행 정책을 바꾸지 않아도 됩니다.

### Windows PowerShell

```powershell
cd robot_api_demo
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe main.py
```

`python`을 찾지 못하면 첫 명령을 `py -m venv .venv`로 바꿔 보세요.

### macOS / Linux

```bash
cd robot_api_demo
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
nano .env
.venv/bin/python main.py
```

다음 번부터는 `cd robot_api_demo` 후 실행 명령(Windows: `.\.venv\Scripts\python.exe main.py`, macOS/Linux: `.venv/bin/python main.py`)만 입력하면 됩니다.

> Python은 3.11 이상이 필요합니다. 이 프로젝트는 Python 3.13.0에서 확인했습니다. 더 새로운 Python에서 `pygame` 설치가 실패하면 Python 3.11~3.13을 사용하세요.

## 2. API 키 설정

멘토가 준비한 키를 사용합니다. **학생이 Groq에 가입할 필요는 없습니다.**

1. `.env.example`을 복사해 `.env` 파일을 만듭니다. (위 명령의 `Copy-Item`/`cp`)
2. `.env`를 열어 `GROQ_API_KEY=` 뒤에 멘토가 알려 준 키를 붙여 넣고 저장합니다. 따옴표나 공백 없이 씁니다.

```text
GROQ_API_KEY=멘토가_알려준_키
GROQ_MODEL=openai/gpt-oss-20b
```

3. 프로그램을 (다시) 실행합니다. `.env`는 프로그램을 시작할 때 한 번만 읽습니다.

주의할 점

- 키는 **`.env` 파일에만** 적습니다. 코드·화면·채팅·사진·GitHub에 올리지 마세요. `.gitignore`가 `.env`를 제외하지만, 파일을 직접 공유하면 소용없습니다. 프로그램도 키를 화면이나 터미널에 출력하지 않습니다.
- 키는 멘토의 Groq 계정에 속하며, 그 계정의 이용 한도 안에서 사용됩니다. 한도와 사용할 수 있는 모델은 계정과 시점에 따라 다르므로 [Groq Rate Limits](https://console.groq.com/docs/rate-limits)와 [Supported Models](https://console.groq.com/docs/models), 그리고 콘솔의 Limits 화면을 따릅니다.
- 여러 명이 같은 키를 쓰면 한도를 함께 소모합니다. 필요 이상으로 SPACE를 누르지 마세요. 한도를 넘으면 429 오류가 나고, 잠시 뒤 다시 누르면 됩니다.
- 이 실습은 유료 플랜 전환이나 결제 설정을 요구하지 않으며, 코드도 그런 설정을 하지 않습니다.

## 3. 조작법

| 키 | 동작 |
| --- | --- |
| `SPACE` | 현재 상태를 API에 보내 **행동 하나**를 받고, 그 행동을 실행합니다. 한 번 누르면 요청도 한 번입니다. 누르고 있어도 반복되지 않습니다. |
| `R` | 지도와 실행 이력을 처음 상태로 되돌립니다. (한글 입력 상태에서는 `ㄱ`도 인식합니다) |
| `ESC` / 창 닫기 | 종료 |

- 프로그램을 켜도 자동으로 API를 부르지 않습니다. 자동 연속 실행도 없습니다. 여러 번 눌러 가며 상태가 어떻게 바뀌는지 관찰하세요.
- API 응답을 기다리는 동안(상태: **API 요청 중**)에는 SPACE와 R이 무시됩니다. 창은 계속 반응하고 화면도 갱신됩니다.
- 목표에 도착하면 **성공**으로 표시되고 더 이상 요청하지 않습니다. 행동 시도가 20번에 이르면 **시도 한도 도달**로 멈춥니다. `R`로 초기화하세요.

## 4. 화면과 터미널 보는 법

- 왼쪽: 5×5 격자. 좌표는 `(x, y)`이고 왼쪽 위가 `(0, 0)`, 오른쪽으로 x가, **아래쪽으로 y가 커집니다.** 파란 로봇, 초록 목표, 회색 장애물 `(2,1) (2,2) (2,3)`.
- 왼쪽 아래 상자: 현재 상태(준비 / API 요청 중 / 성공 / 오류 / 시도 한도 도달)와 안내 메시지.
- 오른쪽: 조작법, 제공자·모델, **API 요청 횟수**(프로그램을 켠 뒤 누적), 행동 시도 횟수, 마지막 응답 시간, **이번 요청**(전달한 위치 · 선택한 행동 · 설명 · 실행 후 위치 · 실행 결과), 최근 5회 실행 이력.
- 로봇이 지도 밖이나 장애물로 가려다 거부되면 로봇 칸에 빨간 테두리가 생깁니다. 이때 로봇은 움직이지 않고, 거부 사실이 **다음 요청에 피드백으로 전달**됩니다. 자동으로 고쳐서 다시 요청하지는 않습니다.
- 터미널(프로그램을 실행한 창): LLM에게 보낸 상태 JSON, 응답 `content`, 실행 결과가 출력됩니다. API 키나 인증 헤더는 출력하지 않습니다. 화면에는 설명을 80자까지만 표시하므로 전체 내용은 터미널에서 확인합니다.
- 창은 모니터가 작으면 자동으로 줄어든 크기로 열리고, 마우스로 크기를 바꿀 수 있습니다.

## 5. 코드 흐름 (`main.py`)

```text
SPACE ─▶ get_observation ─▶ choose_action ─▶ apply_action ─▶ render ─▶ (다시 SPACE)
         상태를 JSON으로     실제 API 호출     규칙 확인 후      화면 그리기
         (좌표·이력 등)      → 행동 1개 받기    로봇 이동
```

| 함수 | 역할 |
| --- | --- |
| `get_observation` | 현재 좌표, 목표, 장애물, 실행 가능한 행동, 최근 5회 이력, 직전 결과를 모아 JSON용 dict로 만듭니다. 행동할 때마다 지금 값으로 새로 만듭니다. |
| `get_legal_actions` | 지도 경계와 장애물만 보고 실행 가능한 행동을 계산합니다. 최단 경로나 정답 행동은 계산하지 않습니다. |
| `choose_action` | Groq `chat.completions.create`를 호출하고, 응답 `choices[0].message.content`를 JSON으로 읽어 `action`·`reason`을 검증합니다. |
| `apply_action` | 받은 행동이 규칙에 맞는지 **코드가** 확인하고 좌표를 바꿉니다. 안 맞으면 좌표는 그대로 두고 거부 이유를 기록합니다. 목표 도달·시도 한도도 여기서 판정합니다. |
| `render` | 화면을 그리기만 합니다. 이 함수에서는 API를 호출하지 않습니다. |

그 밖에 알아 둘 점

- **스레드**: 네트워크 호출(`choose_action`)은 작업 스레드(`request_worker`)에서 하고, 게임 상태는 메인 스레드(`poll_results` → `finish_request`)에서만 바꿉니다. 그래서 응답을 기다리는 동안에도 창이 멈추지 않습니다.
- **구조화된 출력**: `response_format`에 `json_schema`와 `strict: true`를 지정해 모델이 `{"action": ..., "reason": ...}` 모양으로만 답하게 합니다. 이는 **형식**만 제한합니다. 고른 행동이 적절한지, 목표에 도착하는지는 보장하지 않으므로 이동 가능 여부와 성공 판정은 파이썬이 합니다.
- **재시도 없음**: 타임아웃 20초, SDK 자동 재시도 0회입니다. 실패하면 로봇은 움직이지 않고 오류만 표시합니다. 미리 정한 경로나 무작위 행동으로 대신하지 않습니다.

## 6. 실제 VLA와 다른 점

이 실습은 VLA(Vision-Language-Action)와 같은 "관측 → 행동 선택 → 실행 → 재관측" 반복 구조를 체험하는 용도이며, VLA 자체는 아닙니다.

| | 이 실습 | 실제 VLA |
| --- | --- | --- |
| 관측 | 코드가 관리하는 좌표·장애물 정보를 **텍스트 JSON**으로 전달 | 카메라 영상 등 **이미지(픽셀)**와 언어 지시를 입력으로 사용 |
| 모델 | 이미 만들어진 범용 LLM을 API로 호출 (학습 없음) | 영상·언어·행동 데이터로 로봇 제어를 위해 학습한 모델 |
| 출력 | 이산적인 행동 5가지 중 하나 (`up` `down` `left` `right` `wait`) | 로봇 팔·바퀴 등을 움직이는 제어 값 |
| 안전 확인 | 코드가 경계·장애물을 다시 검사 | 시스템마다 별도의 안전 장치 필요 |

화면의 픽셀을 보고 판단하는 것이 아니라, 코드가 알고 있는 값을 글자로 알려 주는 점이 가장 큰 차이입니다.

## 7. 오류 해결

화면 왼쪽 아래 상자에 오류 종류와 안내가 표시되고, 자세한 내용은 터미널에 출력됩니다. 오류가 나도 로봇은 움직이지 않으며, 원인을 해결한 뒤 SPACE를 다시 누르면 됩니다.

| 표시 | 원인 | 해결 |
| --- | --- | --- |
| API 키 없음 | `.env`가 없거나 `GROQ_API_KEY`가 비어 있음 | `.env.example`을 `.env`로 복사하고 키를 넣은 뒤 **프로그램을 다시 실행** |
| 인증 실패 (401/403) | 키 오타, 앞뒤 공백·따옴표, 만료·폐기된 키 | 멘토에게 받은 키를 다시 붙여 넣고 프로그램을 다시 실행 |
| 한도 초과 (429) | 요청이 너무 많거나 키의 한도를 다 씀 | 안내된 시간(응답에 대기 시간 정보가 있을 때 표시)만큼 기다린 뒤 SPACE. 여러 명이 키를 함께 쓰면 더 빨리 찹니다 |
| 응답 시간 초과 / 네트워크 오류 | 인터넷 끊김, 학교망·방화벽·프록시 | 인터넷 연결을 확인하고 잠시 뒤 다시 시도. 계속되면 멘토에게 알리기 |
| 모델·요청 형식 오류 (400/404) | `GROQ_MODEL`이 없는 모델이거나 계정에서 쓸 수 없음, `json_schema` strict 미지원 모델 | `.env`의 `GROQ_MODEL`을 [Supported Models](https://console.groq.com/docs/models)에서 쓸 수 있는 모델로 바꾸고 재실행. strict를 지원하는 모델은 [Structured Outputs](https://console.groq.com/docs/structured-outputs) 문서에서 확인 (기본값 `openai/gpt-oss-20b`로 되돌려도 됨) |
| 응답 형식 오류 | 모델이 빈 응답·잘린 응답·거절을 보냄, 허용되지 않은 행동 값 | SPACE로 다시 시도. 반복되면 모델을 바꿔 보기 |
| Groq 서버 오류 (5xx) | Groq 쪽 일시적 문제 | 잠시 뒤 다시 시도 |
| 한글이 □로 보임 | 시스템에서 한글 글꼴을 찾지 못함 | 프로그램이 맑은 고딕·Apple SD Gothic Neo·나눔고딕·Noto Sans CJK KR 등을 찾아 씁니다(Windows에서 확인). 해당 글꼴이 없는 환경이면 한글 글꼴을 설치하세요. 예: Linux `sudo apt install fonts-nanum` |
| `ModuleNotFoundError: No module named 'groq'` (또는 `pygame`, `dotenv`) | `.venv`의 Python으로 실행하지 않았거나 설치하지 않음 | 위 1번의 `.venv` 경로로 `-m pip install -r requirements.txt`와 `main.py`를 다시 실행 |
| `R`이 안 먹음 | 입력기 상태 문제 | 한/영 키로 영문 입력으로 바꾸고 다시 누르기 |

모델이 지원되지 않는다고 해서 가짜 응답으로 대신하지 않습니다. 모델을 바꾸는 것이 해결 방법입니다.

## 8. 알아 두기

- LLM은 같은 상황에서도 다르게 답할 수 있고, **항상 최단 경로로 가거나 반드시 20번 안에 도착한다고 보장할 수 없습니다.** 막힌 곳으로 가려 하거나 쓸데없이 기다리는 모습이 나올 수 있으며, 그것도 관찰할 거리입니다.
- 실행 중에 `.env`를 고쳐도 반영되지 않습니다. 프로그램을 다시 시작하세요.

## 9. 파일 구성

| 파일 | 설명 |
| --- | --- |
| `main.py` | 실행 가능한 프로그램 전체 |
| `requirements.txt` | 직접 의존성과 검증한 버전 |
| `.env.example` | 키와 모델 설정 예시 (키는 비어 있음). 복사해서 `.env`로 사용 |
| `.gitignore` | `.env`, `.venv/`, `__pycache__/` 등을 Git에서 제외 |
| `README.md` | 이 문서 |

## 참고 자료

- [Groq Quickstart](https://console.groq.com/docs/quickstart)
- [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs)
- [Groq Supported Models](https://console.groq.com/docs/models)
- [Groq Rate Limits](https://console.groq.com/docs/rate-limits)
- [Groq Billing FAQs](https://console.groq.com/docs/billing-faqs)
- [Pygame Quick start](https://www.pygame.org/docs/#quick-start)
