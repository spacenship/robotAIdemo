"""학생 수정 1순위: 맵 생성, 관측, 게임 규칙. 이 파일에는 API 호출이 없습니다."""
from dataclasses import dataclass, field
import json
from pathlib import Path
import random

COLS, ROWS = 3, 10        # 전체 맵: 가로 3칸 × 세로 10칸
ZOOM_ROWS = 5             # 화면에 확대해 보여 주는 구간: 3×5칸 (로봇 줄 포함)
AI_ROWS = 3               # AI에게 넘기는 구간: 3×3칸 (로봇 줄 + 앞 2줄)
MAX_LIVES = 3             # 기본 목숨 수. scenario.json의 "lives"나 --lives로 바꿀 수 있습니다.
LIVES_LIMIT = 10          # 설정할 수 있는 최대 목숨 수
START_COL = 1             # 가운데 열에서 출발
DOUBLE_CHANCE = 0.4       # 한 줄에 장애물이 2개 나올 확률 (직전 줄이 2개면 0)
KINDS = ("brick", "sign")  # 벽돌 / 공사중 표지판
# 맵을 글자로 적을 때 쓰는 기호 (scenario.json의 "map", 주행 기록 파일)
MAP_CHARS = {"B": "brick", "S": "sign", "X": "brick", ".": None}
KIND_CHARS = {"brick": "B", "sign": "S", None: "."}

# 열 번호: 0=왼쪽, 1=가운데, 2=오른쪽. 값은 열이 바뀌는 양입니다.
ACTIONS = {"left": -1, "right": 1, "stay": 0}


def _pick(rng, items):
    """rng.random()만 사용합니다. 같은 seed면 파이썬 버전이 달라도 같은 맵이 나옵니다."""
    return items[int(rng.random() * len(items))]


def generate_map(seed: int):
    """seed로 장애물 맵을 만듭니다. grid[0]이 로봇과 가장 가까운 1번째 줄입니다.

    규칙 1) 한 줄에 장애물 1~2개
    규칙 2) 같은 세로줄(열)에 장애물이 3줄 연속 나오지 않음 (최대 연속 2개)
    규칙 3) 장애물 2개짜리 줄은 연속해서 나오지 않음
    이 세 규칙을 지키면 부딪히지 않고 완주할 길이 항상 있습니다(tests/test_game.py에서 확인).
    """
    rng = random.Random(seed)
    singles = [(c,) for c in range(COLS)]
    doubles = [(a, b) for a in range(COLS) for b in range(a + 1, COLS)]
    blocked_rows = []  # 줄마다 장애물이 놓인 열 묶음
    for _ in range(ROWS):
        prev_double = bool(blocked_rows) and len(blocked_rows[-1]) == 2
        options = doubles if (not prev_double and rng.random() < DOUBLE_CHANCE) else singles
        if len(blocked_rows) >= 2:  # 직전 두 줄에 모두 장애물이 있던 열은 제외
            full = set(blocked_rows[-1]) & set(blocked_rows[-2])
            options = [o for o in options if not full & set(o)]
        blocked_rows.append(_pick(rng, options))
    return tuple(tuple(_pick(rng, KINDS) if c in cols else None for c in range(COLS))
                 for cols in blocked_rows)


def parse_map(rows):
    """직접 적은 맵을 grid로 바꿉니다. 첫 줄이 로봇과 가장 가까운 1번째 줄입니다.

    예: ["B..", ".S.", ...] — B=벽돌, S=공사중 표지판, X=벽돌, .=빈칸.
    장애물 생성 규칙은 검사하지 않으므로, 일부러 피할 수 없는 맵도 만들 수 있습니다.
    """
    if (not isinstance(rows, list) or len(rows) != ROWS
            or not all(isinstance(row, str) and len(row) == COLS for row in rows)):
        raise ValueError(f"map은 {COLS}글자 문자열 {ROWS}줄이어야 합니다.")
    try:
        return tuple(tuple(MAP_CHARS[ch.upper()] for ch in row) for row in rows)
    except KeyError:
        raise ValueError("map에는 B(벽돌), S(표지판), X(벽돌), .(빈칸)만 쓸 수 있습니다.") from None


def map_to_text(grid):
    """grid를 1번째 줄부터 "B.S" 같은 문자열 목록으로 바꿉니다."""
    return ["".join(KIND_CHARS[cell] for cell in row) for row in grid]


@dataclass(frozen=True)
class Scenario:
    title: str
    instruction: str
    seed: int | None          # 직접 적은 맵이면 None
    grid: tuple
    lives: int = MAX_LIVES
    file_map: tuple = ()      # scenario.json에 적혀 있던 (seed, map). F5에서 바뀌었는지 비교합니다.

    @property
    def label(self):
        return "직접 만든 맵" if self.seed is None else f"seed {self.seed}"


def load_scenario(path: Path, seed: int | None = None, lives: int | None = None) -> Scenario:
    """scenario.json을 읽습니다. seed·lives 인자가 있으면 파일의 값보다 우선합니다.

    맵을 정하는 순서: seed 인자 > 파일의 "map"(직접 적은 맵) > 파일의 "seed".
    파일의 seed가 null이면 실행할 때마다 새 seed를 뽑습니다(화면·기록 파일에 표시).
    """
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    for key, maximum in (("title", 40), ("instruction", 1000)):
        if not isinstance(data.get(key), str) or not 1 <= len(data[key].strip()) <= maximum:
            raise ValueError(f"{key}는 1~{maximum}자 문자열이어야 합니다.")
    if lives is None:
        lives = data.get("lives", MAX_LIVES)
    if type(lives) is not int or not 1 <= lives <= LIVES_LIMIT:
        raise ValueError(f"lives(목숨)는 1~{LIVES_LIMIT} 사이 정수여야 합니다.")
    file_seed, file_rows = data.get("seed"), data.get("map")
    custom = None if file_rows is None else parse_map(file_rows)
    file_map = (file_seed, None if custom is None else tuple(file_rows))
    if seed is None and custom is not None:
        return Scenario(data["title"], data["instruction"], None, custom, lives, file_map)
    if seed is None:
        seed = file_seed
    if seed is None:
        seed = random.randrange(1_000_000)
    if type(seed) is not int or not 0 <= seed <= 999_999_999:
        raise ValueError("seed는 0~999999999 사이 정수 또는 null이어야 합니다.")
    return Scenario(data["title"], data["instruction"], seed, generate_map(seed), lives, file_map)


@dataclass
class Game:
    scenario: Scenario
    col: int = START_COL
    row: int = 0              # 진행거리. 0은 출발선, 1~ROWS는 맵의 줄
    lives: int | None = None  # 비워 두면 scenario.lives로 시작
    dodged: int = 0           # 피한 장애물 수 (부딪힌 줄의 장애물은 세지 않습니다)
    hits: set = field(default_factory=set)  # 부딪힌 칸 (row, col)
    history: list = field(default_factory=list)
    last_feedback: str = "아직 행동하지 않았습니다."

    def __post_init__(self):
        if self.lives is None:
            self.lives = self.scenario.lives

    @property
    def collisions(self):
        return len(self.hits)

    def cell(self, row: int, col: int):
        """맵 밖(출발선·결승선 너머)은 빈칸입니다."""
        return self.scenario.grid[row - 1][col] if 1 <= row <= ROWS else None

    @property
    def won(self):
        return self.row >= ROWS and self.lives > 0

    @property
    def finished(self):
        return self.row >= ROWS or self.lives <= 0


def get_legal_actions(game: Game) -> list[str]:
    """벽(맵 밖)으로 나가는 행동만 뺍니다. 장애물을 피하는 판단은 AI의 몫입니다."""
    return [a for a, dx in ACTIONS.items() if 0 <= game.col + dx < COLS]


def get_observation(game: Game) -> dict:
    """LLM에 보낼 현재 상태. 화면 이미지가 아니라 3×3칸을 텍스트로 전달합니다."""
    view = []
    for ahead in range(AI_ROWS - 1, -1, -1):  # 먼 줄부터, 마지막이 로봇이 있는 줄
        cells = ["X" if game.cell(game.row + ahead, c) else "." for c in range(COLS)]
        if ahead == 0:
            cells[game.col] = "R"
        view.append({"rows_ahead": ahead, "cells": cells})
    return {
        "instruction": game.scenario.instruction,
        "robot_column": game.col,
        "view": view,
        "legal_actions": get_legal_actions(game),
        "lives": game.lives,
        "distance": game.row,
        "rows_to_finish": ROWS - game.row,
        "recent_history": [{k: h[k] for k in ("turn", "action", "column", "result")}
                           for h in game.history[-3:]],
        "last_feedback": game.last_feedback,
    }


def apply_action(game: Game, action: str, reason: str, source: str = "LLM") -> dict:
    """한 턴 = 좌우 이동(또는 정지) 후 맵이 한 줄 내려옵니다. 게임이 행동을 다시 검사합니다."""
    if game.finished:
        raise ValueError("끝난 게임에는 행동할 수 없습니다. R로 다시 시작하세요.")
    before = game.col
    valid = action in get_legal_actions(game)
    if valid:
        game.col += ACTIONS[action]
        feedback = {"left": "왼쪽으로 한 칸 옮기며", "right": "오른쪽으로 한 칸 옮기며",
                    "stay": "같은 열에서"}[action] + " 전진했습니다."
    else:
        why = "벽에 막혀" if action in ACTIONS else "알 수 없는 행동이라"
        feedback = f"{action}: {why} 열을 바꾸지 못하고 그대로 전진했습니다."
    game.row += 1
    obstacles = sum(1 for c in range(COLS) if game.cell(game.row, c))
    collided = game.cell(game.row, game.col) is not None
    if collided:
        game.lives -= 1
        game.hits.add((game.row, game.col))
        feedback += f" 장애물에 부딪혀 목숨이 {game.lives}개 남았습니다."
    else:
        # 부딪힌 줄은 옆 칸에 다른 장애물이 있었더라도 '피한 장애물'로 세지 않습니다.
        game.dodged += obstacles
    if game.lives <= 0:
        feedback += " 목숨을 모두 잃었습니다."
    elif game.won:
        feedback += " 맵을 완주했습니다!"
    game.last_feedback = feedback
    entry = {"turn": len(game.history) + 1, "source": source, "action": action,
             "reason": reason, "before": before, "column": game.col, "row": game.row,
             "valid": valid, "collided": collided, "lives": game.lives,
             "result": "hit" if collided else "safe" if valid else "wall",
             "feedback": feedback}
    game.history.append(entry)
    return entry
