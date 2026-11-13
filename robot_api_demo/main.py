"""남원고 3차시 - LLM API로 움직이는 로봇 (GroqCloud + Pygame)

실행 흐름 (SPACE를 한 번 누를 때마다 아래가 한 바퀴 돈다)
    get_observation  : 현재 상태(좌표·장애물·이력)를 JSON으로 만든다
    choose_action    : 그 JSON을 실제 Groq API에 보내 '다음 행동 하나'를 받는다
    apply_action     : 받은 행동이 규칙에 맞는지 파이썬이 확인하고 로봇을 움직인다
    render           : 바뀐 상태를 화면에 그린다 (여기서는 API를 부르지 않는다)
"""

import json
import math
import os
import queue
import re
import sys
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

# pygame 2.6.1이 setuptools의 pkg_resources 경고를 띄우는데 수업과 무관하므로 숨긴다.
warnings.filterwarnings("ignore", message="pkg_resources is deprecated")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import groq  # noqa: E402
import pygame  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

# ---------------------------------------------------------------------------
# 1. 설정값
# ---------------------------------------------------------------------------
GRID_SIZE = 5                         # 5 x 5 격자
START = (0, 0)                        # 로봇 시작 위치 (x, y)
GOAL = (4, 4)                         # 초록색 목표
OBSTACLES = [(2, 1), (2, 2), (2, 3)]  # 지나갈 수 없는 칸
ACTIONS = ["up", "down", "left", "right", "wait"]
MOVES = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0), "wait": (0, 0)}
MAX_ATTEMPTS = 20                     # 행동 시도 한도
PROMPT_HISTORY = 5                    # LLM에 알려 줄 최근 이력 개수

DEFAULT_MODEL = "openai/gpt-oss-20b"  # .env의 GROQ_MODEL로 바꿀 수 있다
TIMEOUT_SECONDS = 20.0                # API 응답 대기 한도
INSTRUCTION = "장애물을 피해 초록색 목표까지 이동해 주세요."

SYSTEM_PROMPT = (
    "너는 격자 로봇의 다음 행동 하나를 선택한다. "
    "주어진 목표, 현재 좌표, 장애물과 직전 실행 결과를 참고한다. "
    "지도 밖이나 장애물로 이동하지 않는다. "
    "제공된 legal_actions 중 하나를 선택한다. "
    "좌표의 y는 아래로 증가한다. "
    "목표에 도달하도록 행동하며, 이동 가능할 때 불필요하게 기다리지 않는다. "
    "reason은 짧은 한국어 한 문장으로 작성한다."
)

# 모델이 이 모양의 JSON으로만 답하도록 제한한다 (Groq Structured Outputs, strict 모드).
# 형식만 제한할 뿐 '좋은 행동'을 보장하지는 않으므로 이동 가능 여부는 코드가 다시 확인한다.
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "reason": {"type": "string", "description": "학생에게 보여줄 짧은 한국어 판단 설명"},
    },
    "required": ["action", "reason"],
    "additionalProperties": False,
}
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "robot_action", "strict": True, "schema": ACTION_SCHEMA},
}

# 프로그램 상태 (화면 오른쪽 '상태'에 표시된다)
READY, REQUESTING, SUCCESS, ERROR, LIMIT = "ready", "requesting", "success", "error", "limit"
STATUS_LABEL = {
    READY: "준비",
    REQUESTING: "API 요청 중",
    SUCCESS: "성공",
    ERROR: "오류",
    LIMIT: "시도 한도 도달",
}

# 오류 종류별 제목 (ApiError.kind)
ERROR_TITLE = {
    "no_key": "API 키 없음",
    "auth": "인증 실패",
    "rate_limit": "한도 초과(429)",
    "timeout": "응답 시간 초과",
    "network": "네트워크 오류",
    "model": "모델·요청 형식 오류",
    "server": "Groq 서버 오류",
    "response": "응답 형식 오류",
    "other": "API 오류",
}

MSG_READY = "SPACE를 눌러 LLM에게 로봇의 다음 행동을 물어보세요."
MSG_NO_KEY = (
    "GROQ_API_KEY가 없어 API 요청을 보내지 않습니다.\n"
    "1) .env.example을 복사해 .env 파일을 만들고\n"
    "2) GROQ_API_KEY= 뒤에 멘토가 준 키를 붙여 넣은 뒤\n"
    "3) 프로그램을 다시 실행하세요."
)

# ---------------------------------------------------------------------------
# 2. 화면 크기와 색
# ---------------------------------------------------------------------------
WINDOW_W, WINDOW_H = 1100, 690        # 화면을 그리는 기준 크기 (실제 창은 모니터에 맞춰 줄어든다)
REASON_SCREEN_LIMIT = 80              # 화면에 보여 줄 설명 글자 수 (전체 내용은 터미널에 출력)
CELL = 84
GRID_X, GRID_Y = 56, 58               # 격자 왼쪽 위 모서리 (왼쪽·위쪽 여백에 좌표 숫자)
BOARD_RIGHT = GRID_X + GRID_SIZE * CELL
BANNER_Y = 518
PANEL_X = 540

BG = (243, 245, 248)
WHITE = (255, 255, 255)
GRID_LINE = (190, 198, 210)
TEXT = (28, 33, 43)
MUTED = (105, 115, 130)
BLUE = (38, 112, 224)
GREEN = (46, 170, 92)
OBSTACLE_COLOR = (74, 80, 92)
RED = (214, 66, 66)
ORANGE = (224, 140, 24)
PURPLE = (130, 86, 196)
STATUS_COLOR = {READY: BLUE, REQUESTING: ORANGE, SUCCESS: GREEN, ERROR: RED, LIMIT: PURPLE}

KOREAN_FONTS = [
    "malgungothic", "applesdgothicneo", "applegothic", "nanumgothic", "nanumbarungothic",
    "notosanscjkkr", "notosanskr", "notosanscjk", "unbatang", "gulim", "dotum",
]

# 터미널 로그에서 API 키가 새어 나가지 않도록 가려 줄 문자열 목록
SECRETS = []


def log(text):
    """터미널에 한 줄 출력한다. 키 값이 섞여 있으면 가린다."""
    for secret in SECRETS:
        if secret:
            text = text.replace(secret, "***")
    print(text, flush=True)


# ---------------------------------------------------------------------------
# 3. 게임 상태
# ---------------------------------------------------------------------------
@dataclass
class Game:
    robot: tuple = START
    attempts: int = 0                          # 실행한 행동 시도 횟수
    history: list = field(default_factory=list)  # 실행 이력 전체
    last_feedback: str = "아직 행동하지 않았습니다."
    status: str = READY
    message: str = MSG_READY
    error_title: str = ""
    api_requests: int = 0                      # 실제 API 요청 횟수 (프로그램 시작 후 누적)
    last_elapsed: float | None = None          # 마지막 API 응답 시간(초)
    current: dict | None = None                # '이번 요청' 화면 표시용


def new_game(api_enabled, api_requests=0):
    game = Game(api_requests=api_requests)
    if not api_enabled:
        game.status = ERROR
        game.error_title = ERROR_TITLE["no_key"]
        game.message = MSG_NO_KEY
    return game


def reset_game(game, api_enabled):
    """지도와 실행 이력을 처음 상태로 되돌린다. (API 요청 횟수는 누적이라 유지)"""
    game.__dict__.update(new_game(api_enabled, game.api_requests).__dict__)


def in_bounds(x, y):
    return 0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE


def xy(pos):
    return {"x": pos[0], "y": pos[1]}


# ---------------------------------------------------------------------------
# 4. 관측 → 행동 선택 → 실행
# ---------------------------------------------------------------------------
def get_legal_actions(robot):
    """경계와 장애물만 보고 '실행 가능한' 행동을 계산한다. (최단 경로·정답은 계산하지 않는다)"""
    legal = []
    for action in ACTIONS:
        dx, dy = MOVES[action]
        x, y = robot[0] + dx, robot[1] + dy
        if action == "wait" or (in_bounds(x, y) and (x, y) not in OBSTACLES):
            legal.append(action)
    return legal


def get_observation(game):
    """LLM에게 보여 줄 현재 상태. 행동할 때마다 실제 현재 값으로 새로 만든다."""
    recent = [
        {
            "step": h["step"],
            "action": h["action"],
            "from": xy(h["from"]),
            "to": xy(h["to"]),
            "result": h["result"],
        }
        for h in game.history[-PROMPT_HISTORY:]  # 대화 전체가 아니라 최근 5회만
    ]
    return {
        "instruction": INSTRUCTION,
        "grid": {"width": GRID_SIZE, "height": GRID_SIZE},
        "robot": xy(game.robot),
        "goal": xy(GOAL),
        "obstacles": [xy(o) for o in OBSTACLES],
        "legal_actions": get_legal_actions(game.robot),
        "recent_history": recent,
        "last_feedback": game.last_feedback,
    }


class ApiError(Exception):
    """API 호출·응답 처리 실패. kind로 오류 종류를 구분한다."""

    def __init__(self, kind, message, detail="", elapsed=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail      # 터미널에만 출력하는 자세한 내용
        self.elapsed = elapsed    # 응답은 받았지만 쓸 수 없을 때의 응답 시간


def make_client(api_key):
    """Groq 클라이언트. 타임아웃 20초, SDK 자동 재시도 0회 (실패해도 몰래 다시 요청하지 않는다)."""
    return groq.Groq(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0)


def parse_duration(text):
    """'7.66s', '2m59.56s' 같은 대기 시간 표기를 초 단위 숫자로 바꾼다."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+(?:\.\d+)?)s)?", text.strip().rstrip("."))
    if not m or not any(m.groups()):
        return None
    hours, minutes, seconds = m.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def server_message(error, limit=80):
    """서버가 보낸 오류 설명만 꺼내 화면에 들어갈 길이로 줄인다."""
    body = getattr(error, "body", None)
    text = body.get("message") if isinstance(body, dict) else None
    text = text if isinstance(text, str) else str(error.message)
    return text if len(text) <= limit else text[:limit] + "..."


def get_wait_seconds(error):
    """429 응답에서 '몇 초 뒤에 다시 시도하라'는 정보를 찾는다. 없으면 None."""
    value = error.response.headers.get("retry-after")
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    m = re.search(r"try again in ([0-9hms.]+)", str(error.message))
    return parse_duration(m.group(1)) if m else None


def status_error_to_api_error(error):
    """HTTP 오류 응답(groq.APIStatusError)을 학생이 이해할 수 있는 ApiError로 바꾼다."""
    code = error.status_code
    detail = f"HTTP {code}: {error.message}"  # 전체 내용은 터미널에서 확인
    if code in (401, 403):
        return ApiError(
            "auth",
            f"API 키가 올바르지 않거나 사용 권한이 없습니다(HTTP {code}). "
            ".env의 GROQ_API_KEY를 멘토에게 받은 값으로 다시 확인하세요.",
            detail,
        )
    if code == 429:
        wait = get_wait_seconds(error)
        if wait is not None:
            hint = f"약 {math.ceil(wait)}초 뒤에 SPACE를 다시 눌러 보세요."
        else:
            hint = "응답에 대기 시간 정보가 없습니다. 잠시 기다린 뒤 SPACE를 다시 눌러 보세요."
        return ApiError("rate_limit", f"요청 한도를 초과했습니다(HTTP 429). {hint}", detail)
    if code in (400, 404, 422):
        return ApiError(
            "model",
            f"모델 또는 요청 형식 오류(HTTP {code}). GROQ_MODEL이 사용 가능한 모델인지, "
            f"json_schema strict를 지원하는지 확인하세요. (서버: {server_message(error)})",
            detail,
        )
    if code >= 500:
        return ApiError("server", f"Groq 서버에 일시적인 문제가 있습니다(HTTP {code}). 잠시 후 다시 시도하세요.", detail)
    return ApiError("other", f"API 요청이 실패했습니다(HTTP {code}): {server_message(error)}", detail)


def parse_action_response(content, elapsed=None):
    """응답 content(JSON 문자열)를 검사해 (action, reason)을 돌려준다. 이상하면 ApiError."""

    def bad(message):
        return ApiError("response", message, detail=f"content = {content!r}", elapsed=elapsed)

    if not isinstance(content, str) or not content.strip():
        raise bad("모델이 빈 응답을 보냈습니다.")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise bad("응답을 JSON으로 읽을 수 없습니다.") from None
    if not isinstance(data, dict) or set(data) != {"action", "reason"}:
        raise bad("응답에 action·reason 필드가 정확히 들어 있지 않습니다.")
    action, reason = data["action"], data["reason"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise bad(f"허용되지 않은 action 값입니다: {action!r}")
    if not isinstance(reason, str) or not reason.strip():
        raise bad("reason이 비어 있거나 문자열이 아닙니다.")
    return action, reason.strip()


def choose_action(client, model, observation):
    """실제 Groq API를 한 번 호출해 다음 행동을 받는다. (작업 스레드에서 실행된다)

    성공하면 {"action", "reason", "elapsed", "content"}를, 실패하면 ApiError를 낸다.
    API가 실패했을 때 정해 둔 경로나 무작위 행동으로 대신하지 않는다.
    """
    user_message = json.dumps(observation, ensure_ascii=False)  # 매번 새 상태를 직렬화
    started = time.monotonic()
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format=RESPONSE_FORMAT,
        )
    except groq.APITimeoutError as e:  # APIConnectionError의 하위 클래스라 먼저 검사
        raise ApiError(
            "timeout",
            f"{TIMEOUT_SECONDS:.0f}초 안에 응답이 오지 않았습니다. 잠시 후 SPACE를 다시 눌러 보세요.",
            str(e),
        ) from e
    except groq.APIConnectionError as e:
        raise ApiError(
            "network",
            "Groq 서버에 연결하지 못했습니다. 인터넷 연결(와이파이·방화벽)을 확인한 뒤 다시 시도하세요.",
            str(e),
        ) from e
    except groq.APIStatusError as e:
        raise status_error_to_api_error(e) from e
    elapsed = time.monotonic() - started

    if not completion.choices:
        raise ApiError("response", "응답에 choices가 없습니다.", elapsed=elapsed)
    choice = completion.choices[0]
    refusal = getattr(choice.message, "refusal", None)
    if refusal:
        raise ApiError("response", f"모델이 요청을 거절했습니다: {refusal}", elapsed=elapsed)
    content = choice.message.content
    log(f"[응답] {elapsed:.2f}초 / finish_reason={choice.finish_reason} / content = {content}")
    if choice.finish_reason == "length":
        raise ApiError("response", "응답이 길이 제한으로 중간에 잘렸습니다.", detail=f"content = {content!r}", elapsed=elapsed)

    action, reason = parse_action_response(content, elapsed)
    return {"action": action, "reason": reason, "elapsed": elapsed, "content": content}


def apply_action(game, action, reason=""):
    """선택된 행동이 규칙에 맞는지 확인하고, 맞으면 로봇을 움직인다. 결과 기록(dict)을 돌려준다."""
    before = game.robot
    dx, dy = MOVES[action]
    target = (before[0] + dx, before[1] + dy)
    rejected_why = None

    if action == "wait":
        result = "제자리 대기"
        feedback = f"직전 행동 wait: 제자리에서 기다렸습니다. 로봇은 {before}에 있습니다."
    elif action in get_legal_actions(before):  # 규칙 확인은 LLM이 아니라 코드가 한다
        game.robot = target
        result = "이동 성공"
        feedback = f"직전 행동 {action}: {before}에서 {target}로 이동했습니다."
    else:  # 유효하지 않은 행동 → 좌표는 그대로, 이유만 기록
        rejected_why = "지도 밖" if not in_bounds(*target) else "장애물"
        result = f"거부({rejected_why})"
        feedback = f"직전 행동 {action}은(는) {rejected_why} 때문에 거부되었습니다. 로봇은 {before}에 그대로 있습니다."

    game.attempts += 1
    entry = {
        "step": game.attempts,
        "action": action,
        "reason": reason,
        "from": before,
        "to": game.robot,
        "result": result,
    }
    game.history.append(entry)
    game.last_feedback = feedback

    if game.robot == GOAL:
        game.status = SUCCESS
        game.message = f"목표에 도착했습니다! (행동 시도 {game.attempts}회) R을 누르면 다시 시작합니다."
    elif game.attempts >= MAX_ATTEMPTS:
        game.status = LIMIT
        game.message = f"행동 시도 {MAX_ATTEMPTS}회에 도달해 중단했습니다. R을 눌러 초기화한 뒤 다시 해 보세요."
    else:
        game.status = READY
        if rejected_why:
            game.message = f"선택한 행동 '{action}'은(는) {rejected_why} 때문에 거부되어 로봇은 그대로입니다. SPACE를 누르면 이 결과가 LLM에게 전달됩니다."
        else:
            game.message = f"'{action}' 실행: {result}. SPACE로 다음 행동을 요청하세요."
    return entry


# ---------------------------------------------------------------------------
# 5. API 요청 관리 (네트워크는 작업 스레드, 상태 변경은 메인 스레드)
# ---------------------------------------------------------------------------
def request_worker(client, model, observation, results):
    """작업 스레드: API를 호출하고 결과를 큐에 넣기만 한다. 게임 상태는 건드리지 않는다."""
    try:
        results.put(("ok", choose_action(client, model, observation)))
    except ApiError as e:
        results.put(("error", e))
    except Exception as e:  # 예상하지 못한 오류도 화면에 보이도록 한다
        results.put(("error", ApiError("other", f"예상하지 못한 오류가 발생했습니다: {type(e).__name__}", repr(e))))


def pretty_json(data):
    """터미널에서 읽기 쉽게 최상위 키마다 한 줄씩 출력한다. (전송하는 JSON과 내용은 같다)"""
    lines = [f'  "{key}": {json.dumps(value, ensure_ascii=False)}' for key, value in data.items()]
    return "{\n" + ",\n".join(lines) + "\n}"


def start_request(game, client, model, results):
    """SPACE: 요청 한 번을 시작한다."""
    observation = get_observation(game)
    game.api_requests += 1
    game.status = REQUESTING
    game.error_title = ""
    game.message = "Groq API에 요청을 보냈습니다. 응답을 기다리는 중입니다"
    game.current = {"sent": game.robot, "action": None, "reason": None, "after": None, "result": None}
    log(f"\n[요청 #{game.api_requests}] 모델 {model} / 전달한 상태 JSON:")
    log(pretty_json(observation))
    threading.Thread(target=request_worker, args=(client, model, observation, results), daemon=True).start()


def finish_request(game, kind, payload):
    """메인 스레드: 작업 스레드가 돌려준 결과로 상태를 갱신한다."""
    if kind == "ok":
        game.last_elapsed = payload["elapsed"]
        before = game.robot
        entry = apply_action(game, payload["action"], payload["reason"])
        game.current.update(action=entry["action"], reason=entry["reason"], after=entry["to"], result=entry["result"])
        log(f"[실행 #{entry['step']}] {entry['action']}: {before} -> {entry['to']} / {entry['result']} / 이유: {entry['reason']}")
    else:  # 오류: 로봇은 움직이지 않고 화면을 유지한다
        if payload.elapsed is not None:
            game.last_elapsed = payload.elapsed
        game.status = ERROR
        game.error_title = ERROR_TITLE[payload.kind]
        game.message = f"{payload.message}\n(SPACE: 다시 시도)"
        game.current.update(result="오류로 로봇이 움직이지 않았습니다.")
        log(f"[오류] {game.error_title}: {payload.detail or payload.message}")


def poll_results(game, results):
    try:
        kind, payload = results.get_nowait()
    except queue.Empty:
        return
    finish_request(game, kind, payload)


def is_reset_key(event):
    # 한글 입력 상태에서는 R 키가 'ㄱ'으로 들어오므로 함께 인정한다
    return event.key == pygame.K_r or getattr(event, "unicode", "") in ("r", "R", "ㄱ")


def handle_event(event, game, client, model, results):
    """창·키보드 이벤트 처리. 프로그램을 계속하면 True, 끝내려면 False."""
    if event.type == pygame.QUIT:
        return False
    if event.type != pygame.KEYDOWN:  # 키를 누르는 순간 1번만 처리 (누르고 있어도 반복 안 됨)
        return True
    if event.key == pygame.K_ESCAPE:
        return False
    if event.key == pygame.K_SPACE:
        # 키가 없거나, 요청 중이거나, 성공·한도 도달 상태이면 요청하지 않는다
        if client is not None and game.status in (READY, ERROR):
            start_request(game, client, model, results)
    elif is_reset_key(event) and game.status != REQUESTING:  # 요청 중에는 초기화도 막는다
        reset_game(game, client is not None)
        log("\n[초기화] 지도와 실행 이력을 처음 상태로 되돌렸습니다.")
    return True


# ---------------------------------------------------------------------------
# 6. 화면 그리기 (render 안에서는 API를 절대 호출하지 않는다)
# ---------------------------------------------------------------------------
def load_fonts():
    """시스템에서 한글 글꼴을 찾아 크기별로 불러온다."""
    path = pygame.font.match_font(KOREAN_FONTS)
    if path is None:
        keys = ("nanum", "malgun", "notosanskr", "notosanscjk", "hangul", "batang", "gulim", "dotum")
        for name in pygame.font.get_fonts():
            if any(key in name for key in keys):
                path = pygame.font.match_font(name)
                break
    if path is None:
        log("[주의] 한글 글꼴을 찾지 못했습니다. 한글이 네모(□)로 보이면 README의 '오류 해결'을 보세요.")
    sizes = {"title": 26, "body": 17, "small": 15, "tiny": 12}
    return {name: pygame.font.Font(path, size) for name, size in sizes.items()}


def wrap_text(text, font, max_width):
    """글자 폭을 재면서 줄바꿈한다. 공백이 있으면 단어 사이에서, 없으면 글자 사이에서 자른다."""
    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for ch in paragraph:
            if font.size(line + ch)[0] <= max_width:
                line += ch
                continue
            cut = line.rfind(" ")
            if cut > 0:
                lines.append(line[:cut])
                line = line[cut + 1:] + ch
            else:
                lines.append(line)
                line = ch
        lines.append(line)
    return lines


def draw_text(screen, font, text, color, x, y, max_width=None, gap=3):
    """글자를 그리고 다음 줄의 y 좌표를 돌려준다. max_width가 있으면 줄바꿈한다."""
    lines = wrap_text(text, font, max_width) if max_width else str(text).split("\n")
    for line in lines:
        if line:
            screen.blit(font.render(line, True, color), (x, y))
        y += font.get_linesize() + gap
    return y


def draw_kv(screen, font, label, value, x, y, width, label_w, value_color=TEXT):
    """'이름   값' 한 항목. 값이 길면 값 칸 안에서 줄바꿈한다."""
    screen.blit(font.render(label, True, MUTED), (x, y))
    return draw_text(screen, font, value, value_color, x + label_w, y, width - label_w)


def draw_section(screen, fonts, title, x, y, width):
    screen.blit(fonts["body"].render(title, True, BLUE), (x, y))
    y += fonts["body"].get_linesize() + 2
    pygame.draw.line(screen, GRID_LINE, (x, y), (x + width, y))
    return y + 6


def cell_rect(x, y):
    return pygame.Rect(GRID_X + x * CELL, GRID_Y + y * CELL, CELL, CELL)


def draw_robot(screen, rect):
    """기본 도형만으로 로봇을 그린다 (몸통 + 눈 + 입 + 안테나)."""
    body = rect.inflate(-rect.w * 34 // 100, -rect.h * 30 // 100)
    body.y += rect.h * 6 // 100
    cx = rect.centerx
    pygame.draw.line(screen, BLUE, (cx, body.top), (cx, body.top - 9), 3)  # 안테나
    pygame.draw.circle(screen, BLUE, (cx, body.top - 11), 5)
    pygame.draw.rect(screen, BLUE, body, border_radius=10)
    for dx in (-rect.w // 8, rect.w // 8):  # 눈
        pygame.draw.circle(screen, WHITE, (cx + dx, body.centery - 4), 7)
        pygame.draw.circle(screen, TEXT, (cx + dx, body.centery - 4), 3)
    pygame.draw.line(screen, WHITE, (cx - 9, body.bottom - 12), (cx + 9, body.bottom - 12), 3)  # 입


def draw_board(screen, fonts, game):
    draw_text(screen, fonts["small"], "좌표 (x, y): 왼쪽 위가 (0, 0), x는 오른쪽으로, y는 아래쪽으로 증가", MUTED, 24, 10, BOARD_RIGHT - 24)
    for i in range(GRID_SIZE):  # 가장자리 좌표 숫자
        label = fonts["body"].render(str(i), True, MUTED)
        screen.blit(label, label.get_rect(center=(GRID_X + i * CELL + CELL // 2, GRID_Y - 14)))
        screen.blit(label, label.get_rect(center=(GRID_X - 18, GRID_Y + i * CELL + CELL // 2)))

    last = game.history[-1] if game.history else None
    for y in range(GRID_SIZE):
        for x in range(GRID_SIZE):
            rect = cell_rect(x, y)
            pygame.draw.rect(screen, WHITE, rect)
            if (x, y) == GOAL:
                pygame.draw.rect(screen, GREEN, rect.inflate(-8, -8), border_radius=8)
            elif (x, y) in OBSTACLES:
                inner = rect.inflate(-8, -8)
                pygame.draw.rect(screen, OBSTACLE_COLOR, inner, border_radius=6)
                pygame.draw.line(screen, (104, 110, 124), inner.topleft, inner.bottomright, 2)
                pygame.draw.line(screen, (104, 110, 124), inner.topright, inner.bottomleft, 2)
            coord_color = WHITE if (x, y) in OBSTACLES or (x, y) == GOAL else MUTED
            screen.blit(fonts["tiny"].render(f"({x},{y})", True, coord_color), (rect.x + 6, rect.y + 4))
            pygame.draw.rect(screen, GRID_LINE, rect, 1)
    goal_label = fonts["small"].render("목표", True, WHITE)
    goal_rect = cell_rect(*GOAL)
    screen.blit(goal_label, (goal_rect.right - goal_label.get_width() - 8, goal_rect.y + 4))

    robot_rect = cell_rect(*game.robot)
    draw_robot(screen, robot_rect)
    if last and last["result"].startswith("거부"):  # 막힌 행동이면 로봇 칸에 빨간 테두리
        pygame.draw.rect(screen, RED, robot_rect, 4)

    legend_y = GRID_Y + GRID_SIZE * CELL + 10  # 범례
    items = [(BLUE, "로봇"), (GREEN, "목표"), (OBSTACLE_COLOR, "장애물")]
    x = GRID_X
    for color, name in items:
        pygame.draw.rect(screen, color, (x, legend_y + 2, 16, 16), border_radius=4)
        screen.blit(fonts["small"].render(name, True, TEXT), (x + 22, legend_y))
        x += 100


def draw_banner(screen, fonts, game):
    rect = pygame.Rect(20, BANNER_Y, BOARD_RIGHT - 20, WINDOW_H - BANNER_Y - 16)
    color = STATUS_COLOR[game.status]
    pygame.draw.rect(screen, WHITE, rect, border_radius=8)
    pygame.draw.rect(screen, GRID_LINE, rect, 1, border_radius=8)
    pygame.draw.rect(screen, color, (rect.x, rect.y, 8, rect.height), border_top_left_radius=8, border_bottom_left_radius=8)

    head = STATUS_LABEL[game.status]
    if game.status == ERROR and game.error_title:
        head += f" - {game.error_title}"
    message = game.message
    if game.status == REQUESTING:  # 화면이 멈추지 않았다는 것을 보여 주는 움직이는 점
        message += "." * (pygame.time.get_ticks() // 400 % 4)

    screen.set_clip(rect.inflate(-4, -4))
    y = draw_text(screen, fonts["body"], head, color, rect.x + 22, rect.y + 10)
    draw_text(screen, fonts["small"], message, TEXT, rect.x + 22, y + 2, rect.width - 40)
    screen.set_clip(None)


def draw_panel(screen, fonts, game, model, api_enabled):
    panel = pygame.Rect(PANEL_X, 16, WINDOW_W - PANEL_X - 20, WINDOW_H - 32)
    pygame.draw.rect(screen, WHITE, panel, border_radius=10)
    pygame.draw.rect(screen, GRID_LINE, panel, 1, border_radius=10)
    screen.set_clip(panel.inflate(-4, -4))
    x, w = panel.x + 18, panel.width - 36
    small, body = fonts["small"], fonts["body"]

    y = draw_text(screen, fonts["title"], "LLM API 로봇 실습", TEXT, x, panel.y + 12) + 2

    y = draw_section(screen, fonts, "조작", x, y, w)
    y = draw_text(screen, small, "SPACE : 다음 행동을 API에 1회 요청", TEXT, x, y)
    y = draw_text(screen, small, "R : 지도·이력 초기화     ESC : 종료", TEXT, x, y) + 4

    y = draw_section(screen, fonts, "상태", x, y, w)
    color = STATUS_COLOR[game.status]
    pygame.draw.circle(screen, color, (x + 7, y + body.get_linesize() // 2), 6)
    y = draw_text(screen, body, STATUS_LABEL[game.status], color, x + 20, y)
    y = draw_kv(screen, small, "제공자", "GroqCloud", x, y, w, 120)
    y = draw_kv(screen, small, "모델", model, x, y, w, 120)
    y = draw_kv(screen, small, "API 요청 횟수", f"{game.api_requests}회 (누적)", x, y, w, 120)
    y = draw_kv(screen, small, "행동 시도", f"{game.attempts} / {MAX_ATTEMPTS}회", x, y, w, 120)
    elapsed = f"{game.last_elapsed:.2f}초" if game.last_elapsed is not None else "-"
    y = draw_kv(screen, small, "마지막 응답", elapsed, x, y, w, 120) + 4

    y = draw_section(screen, fonts, "이번 요청", x, y, w)
    cur = game.current
    if cur is None:
        y = draw_text(screen, small, "아직 요청하지 않았습니다.", MUTED, x, y)
    else:
        waiting = "응답 대기 중..." if game.status == REQUESTING else "-"
        y = draw_kv(screen, small, "전달한 위치", str(cur["sent"]), x, y, w, 120)
        y = draw_kv(screen, small, "선택한 행동", cur["action"] or waiting, x, y, w, 120, BLUE if cur["action"] else MUTED)
        reason = cur["reason"] or waiting
        if len(reason) > REASON_SCREEN_LIMIT:  # 너무 길면 아래 이력이 잘리므로 줄여서 표시
            reason = reason[:REASON_SCREEN_LIMIT - 1] + "…"
        y = draw_kv(screen, small, "설명", reason, x, y, w, 120)
        y = draw_kv(screen, small, "실행 후 위치", str(cur["after"]) if cur["after"] else waiting, x, y, w, 120)
        failed = cur["result"] is not None and (cur["result"].startswith("거부") or cur["result"].startswith("오류"))
        y = draw_kv(screen, small, "실행 결과", cur["result"] or waiting, x, y, w, 120, RED if failed else TEXT)
    y += 4

    y = draw_section(screen, fonts, f"최근 실행 이력 (최대 {PROMPT_HISTORY}회)", x, y, w)
    if not game.history:
        draw_text(screen, small, "아직 없습니다.", MUTED, x, y)
    for h in game.history[-PROMPT_HISTORY:]:
        line = f"#{h['step']}  {h['action']}  {h['from']} -> {h['to']}  {h['result']}"
        y = draw_text(screen, small, line, RED if h["result"].startswith("거부") else TEXT, x, y, w)
    screen.set_clip(None)


def render(screen, fonts, game, model, api_enabled):
    """현재 상태를 화면에 그린다. 이 함수는 상태를 읽기만 하고 API를 호출하지 않는다."""
    screen.fill(BG)
    draw_board(screen, fonts, game)
    draw_banner(screen, fonts, game)
    draw_panel(screen, fonts, game, model, api_enabled)


def show_on_window(window, screen):
    """그려 둔 화면을 창 크기에 맞춰 보여 준다. 비율을 유지하고 남는 곳은 배경색으로 채운다."""
    width, height = window.get_size()
    ratio = min(width / WINDOW_W, height / WINDOW_H)
    size = (max(1, int(WINDOW_W * ratio)), max(1, int(WINDOW_H * ratio)))
    window.fill(BG)
    window.blit(pygame.transform.smoothscale(screen, size), ((width - size[0]) // 2, (height - size[1]) // 2))
    pygame.display.flip()


# ---------------------------------------------------------------------------
# 7. 프로그램 시작
# ---------------------------------------------------------------------------
def main():
    for stream in (sys.stdout, sys.stderr):  # 터미널 인코딩 때문에 한글 출력이 죽지 않게
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    load_dotenv(Path(__file__).resolve().parent / ".env")  # main.py와 같은 폴더의 .env
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    model = os.environ.get("GROQ_MODEL", "").strip() or DEFAULT_MODEL
    SECRETS.append(api_key)
    client = make_client(api_key) if api_key else None  # 키가 없으면 클라이언트도 만들지 않는다

    log("LLM API 로봇 실습 - 터미널에는 요청 JSON과 응답 content가 출력됩니다. (API 키는 출력하지 않습니다)")
    log(f"제공자: GroqCloud / 모델: {model} / API 키: {'설정됨' if client else '없음 (API 요청 비활성화)'}")
    if client is None:
        log(MSG_NO_KEY)

    pygame.init()
    # 노트북 화면이 작아도 창 전체가 보이도록 처음 크기를 줄인다. 창 크기는 마우스로도 바꿀 수 있다.
    desktop = pygame.display.Info()
    scale = max(0.5, min(1.0, (desktop.current_w - 60) / WINDOW_W, (desktop.current_h - 100) / WINDOW_H))
    window = pygame.display.set_mode((int(WINDOW_W * scale), int(WINDOW_H * scale)), pygame.RESIZABLE)
    screen = pygame.Surface((WINDOW_W, WINDOW_H))  # 항상 같은 크기로 그린 뒤 창에 맞춰 보여 준다
    pygame.display.set_caption("LLM API 로봇 실습")
    pygame.key.set_repeat()  # 키 반복 입력 끄기
    try:
        pygame.key.stop_text_input()  # 한글 입력기(IME) 창이 뜨지 않게
    except Exception:
        pass
    fonts = load_fonts()

    results = queue.Queue()
    game = new_game(client is not None)
    clock = pygame.time.Clock()
    running = True
    while running:
        for event in pygame.event.get():
            if not handle_event(event, game, client, model, results):
                running = False
        poll_results(game, results)  # 작업 스레드의 결과를 메인 스레드에서 반영
        render(screen, fonts, game, model, client is not None)
        show_on_window(window, screen)
        clock.tick(30)
    pygame.quit()


if __name__ == "__main__":
    main()
