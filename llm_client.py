"""학생 수정 2순위: 지시문과 실제 Groq API 요청. 게임 규칙과 분리했습니다."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time

from dotenv import load_dotenv
from groq import (Groq, AuthenticationError, PermissionDeniedError, RateLimitError,
                  APITimeoutError, APIConnectionError, APIStatusError)

from game import ACTIONS

ROOT = Path(__file__).resolve().parent

SYSTEM_PROMPT = """You control a robot in a top-down obstacle-dodging game, one action per request.
The road has 3 columns: 0=left, 1=center, 2=right. Each turn the robot first shifts
sideways (left: column-1, right: column+1, stay: same column) and then the map moves
one row closer. So after your action the robot lands in the row with rows_ahead=1,
in its new column.
"view" lists 3 rows from far to near: rows_ahead 2, 1, and 0 (the robot's row).
Each "cells" list is ordered by column 0, 1, 2. "X" = obstacle, "." = free, "R" = robot.
Landing on "X" costs one life. First make sure the landing cell (rows_ahead=1) is free.
Then prefer a column from which the rows_ahead=2 row can still be passed, remembering
the robot can shift at most one column per turn.
Select exactly one of the legal_actions. No tools, code, or multi-action plans.
Return the requested JSON only. The reason is a short Korean explanation of the
chosen action for a student, not a detailed reasoning trace (prefer under 60 chars).
"""

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    model: str = "openai/gpt-oss-20b"
    timeout: float = 20.0
    min_interval: float = 3.0
    max_tokens: int = 1024


def load_settings() -> Settings:
    # 실행 위치와 관계없이 main.py 옆 .env를 읽습니다. OS 환경변수가 우선합니다.
    load_dotenv(ROOT / ".env", override=False)
    try:
        result = Settings(
            api_key=os.getenv("GROQ_API_KEY", "").strip(),
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip(),
            timeout=float(os.getenv("GROQ_TIMEOUT_SECONDS", "20")),
            min_interval=float(os.getenv("MIN_REQUEST_INTERVAL_SECONDS", "3")),
            max_tokens=int(os.getenv("MAX_COMPLETION_TOKENS", "1024")),
        )
        if not (1 <= result.timeout <= 120 and 0 <= result.min_interval <= 300
                and 128 <= result.max_tokens <= 8192 and result.model):
            raise ValueError
        return result
    except ValueError:
        raise ValueError(".env의 모델명·시간·토큰 설정을 .env.example과 비교해 주세요.") from None


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    raw: str
    elapsed: float
    response_id: str = ""
    total_tokens: int | None = None


class DecisionError(Exception):
    def __init__(self, message: str, *, retry_after: float = 0, raw: str = ""):
        super().__init__(message)
        self.retry_after = retry_after
        self.raw = raw


class RobotLLM:
    def __init__(self, settings: Settings, *, http_client=None):
        self.settings = settings
        self.client = None
        if settings.api_key:
            self.client = Groq(api_key=settings.api_key, timeout=settings.timeout,
                               max_retries=0, http_client=http_client)

    def choose_action(self, observation: dict) -> Decision:
        """진짜 API는 여기 한 곳에서만 호출합니다. 자동 재시도나 대체 행동은 없습니다."""
        if self.client is None:
            raise DecisionError("API 키가 없습니다. .env에 GROQ_API_KEY를 넣고 다시 실행하세요.")
        start = time.perf_counter()
        try:
            completion = self.client.chat.completions.create(
                model=self.settings.model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": json.dumps(observation, ensure_ascii=False)}],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "robot_action", "strict": True, "schema": ACTION_SCHEMA}},
                reasoning_effort="low",
                temperature=0.2,
                max_completion_tokens=self.settings.max_tokens,
            )
        except AuthenticationError:
            raise DecisionError("인증 실패(401): Groq에서 발급한 키인지 확인하세요.") from None
        except PermissionDeniedError:
            raise DecisionError("권한 없음(403): 계정의 모델 사용 권한을 확인하세요.") from None
        except RateLimitError as exc:
            try:
                delay = max(0, float(exc.response.headers.get("retry-after", "10")))
            except ValueError:
                delay = 10
            raise DecisionError("요청 한도 초과(429): 잠시 후 Space로 다시 요청하세요.",
                                retry_after=delay) from None
        except APITimeoutError:
            raise DecisionError("응답 시간 초과: 이동하지 않았습니다. 네트워크 확인 후 다시 요청하세요.") from None
        except APIConnectionError:
            raise DecisionError("연결 실패: 인터넷·학교 네트워크 설정을 확인하세요.") from None
        except APIStatusError as exc:
            message = ("요청 오류: GROQ_MODEL과 JSON Schema 지원 여부를 확인하세요."
                       if exc.status_code in (400, 404, 422) else "API 서버 오류: 잠시 후 다시 요청하세요.")
            raise DecisionError(f"{message} (HTTP {exc.status_code})") from None

        if not completion.choices:
            raise DecisionError("API 응답에 선택 결과가 없습니다.")
        choice = completion.choices[0]
        raw = choice.message.content or ""
        if choice.finish_reason == "length":
            raise DecisionError("출력이 토큰 한도로 중단됐습니다. MAX_COMPLETION_TOKENS를 늘려 주세요.", raw=raw)
        try:
            data = json.loads(raw)
            if (not isinstance(data, dict) or set(data) != {"action", "reason"}
                    or not isinstance(data["action"], str) or data["action"] not in ACTIONS
                    or not isinstance(data["reason"], str) or not data["reason"].strip()
                    or len(data["reason"]) > 500):
                raise ValueError
        except (ValueError, TypeError):
            raise DecisionError("응답 형식 오류: action과 reason이 있는 JSON이 필요합니다. 이동하지 않았습니다.", raw=raw) from None
        return Decision(data["action"], data["reason"].strip(), raw,
                        time.perf_counter() - start, completion.id,
                        completion.usage.total_tokens if completion.usage else None)

    def close(self):
        if self.client:
            self.client.close()
