"""화면 표현. 게임 규칙이나 API 호출은 포함하지 않습니다."""
from pathlib import Path
import pygame

from game import COLS, ROWS, ZOOM_ROWS, AI_ROWS, MAX_LIVES, get_observation, get_legal_actions

GREEN = "#15370D"
SAGE = "#728E6D"
GOLD = "#E5CD82"
CREAM = "#FFF2B4"
PAPER = "#FFFEF7"
INK = "#20351D"
MUTED = "#52654D"
RED = "#9C3727"
ROAD = "#EDF1E6"
ORANGE = "#D9772B"
BRICK = "#A8452F"
SIGN = "#F2B705"

ASSETS = Path(__file__).resolve().parent / "assets"
CELL = 96                 # 확대 화면 한 칸
ZOOM_X, ZOOM_Y = 64, 180  # 확대 화면 왼쪽 위
MINI = 30                 # 미니맵 한 칸
MINI_X, MINI_Y = 420, 196


def format_time(seconds):
    total = int(round(seconds))
    return f"{total // 60}분 {total % 60}초"


def draw_brick(surface, s):
    body = pygame.Rect(s * 0.08, s * 0.14, s * 0.84, s * 0.72)
    pygame.draw.rect(surface, BRICK, body, border_radius=max(2, s // 16))
    line = max(1, s // 32)
    course = body.height / 4
    for i in range(1, 4):  # 가로 줄눈
        y = body.top + course * i
        pygame.draw.line(surface, "#E8D9C0", (body.left, y), (body.right - 1, y), line)
    for i in range(4):     # 세로 줄눈: 한 줄씩 어긋나게
        offset = body.width / 4 if i % 2 else body.width / 2
        x = body.left + offset
        while x < body.right - 2:
            pygame.draw.line(surface, "#E8D9C0", (x, body.top + course * i),
                             (x, body.top + course * (i + 1) - 1), line)
            x += body.width / 2
    pygame.draw.rect(surface, "#6E2A1C", body, max(1, s // 32), border_radius=max(2, s // 16))


def draw_sign(surface, s):
    cx, cy, r = s / 2, s * 0.40, s * 0.33
    for x in (s * 0.3, s * 0.7):  # 다리
        pygame.draw.line(surface, "#4A4A4A", (x, s * 0.6), (x, s * 0.93), max(2, s // 20))
    diamond = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    pygame.draw.polygon(surface, SIGN, diamond)
    pygame.draw.polygon(surface, "#222222", diamond, max(2, s // 24))
    pygame.draw.rect(surface, "#222222", (cx - s * 0.035, cy - r * 0.55, s * 0.07, r * 0.65))
    pygame.draw.circle(surface, "#222222", (cx, cy + r * 0.42), max(2, s * 0.045))
    bar = pygame.Rect(s * 0.12, s * 0.74, s * 0.76, s * 0.13)  # 줄무늬 가로대
    for i in range(6):
        part = pygame.Rect(bar.left + bar.width * i / 6, bar.top, bar.width / 6 + 1, bar.height)
        pygame.draw.rect(surface, ORANGE if i % 2 == 0 else PAPER, part)
    pygame.draw.rect(surface, "#222222", bar, max(1, s // 40))


def draw_robot(surface, s):
    for x in (s * 0.12, s * 0.76):  # 바퀴
        pygame.draw.rect(surface, "#333333", (x, s * 0.3, s * 0.12, s * 0.46), border_radius=max(2, s // 20))
    body = pygame.Rect(s * 0.22, s * 0.16, s * 0.56, s * 0.7)
    pygame.draw.rect(surface, SAGE, body, border_radius=max(3, s // 8))
    pygame.draw.rect(surface, PAPER, body, max(2, s // 32), border_radius=max(3, s // 8))
    visor = pygame.Rect(s * 0.3, s * 0.24, s * 0.4, s * 0.16)  # 앞(위쪽)을 보는 얼굴
    pygame.draw.rect(surface, INK, visor, border_radius=max(2, s // 20))
    for x in (s * 0.4, s * 0.6):
        pygame.draw.circle(surface, GOLD, (x, s * 0.32), max(2, s * 0.035))


DRAWERS = {"brick": draw_brick, "sign": draw_sign, "robot": draw_robot}


class View:
    def __init__(self):
        # 나눔스퀘어가 설치되어 있으면 우선 사용합니다. 없으면 동봉한 한글 폰트 사용.
        bundled = ASSETS / "NanumGothic-Regular.ttf"
        font_path = pygame.font.match_font("nanumsquare,nanumsquareround") or str(bundled)
        self.fonts = {size: pygame.font.Font(font_path, size) for size in (14, 16, 18, 20, 24, 30)}
        self.sprites = {}

    def sprite(self, name, size):
        """assets/brick.png · sign.png · robot.png가 있으면 그 그림을, 없으면 직접 그린 그림을 씁니다."""
        key = (name, size)
        if key not in self.sprites:
            path = ASSETS / f"{name}.png"
            if path.exists():
                image = pygame.transform.smoothscale(pygame.image.load(str(path)).convert_alpha(), (size, size))
            else:
                image = pygame.Surface((size, size), pygame.SRCALPHA)
                DRAWERS[name](image, size)
            self.sprites[key] = image
        return self.sprites[key]

    def text(self, screen, text, x, y, size=18, color=INK):
        screen.blit(self.fonts[size].render(str(text), True, color), (x, y))

    def centered(self, screen, text, rect, size=18, color=INK):
        image = self.fonts[size].render(str(text), True, color)
        screen.blit(image, image.get_rect(center=pygame.Rect(rect).center))

    def wrapped(self, screen, text, x, y, width, *, size=18, color=INK, lines=4):
        """한글·긴 JSON을 글자 단위로 줄바꿈하고 영역 끝은 말줄임합니다."""
        font = self.fonts[size]
        rows, row = [], ""
        for char in str(text):
            if char == "\n":
                rows.append(row)
                row = ""
            elif font.size(row + char)[0] > width:
                rows.append(row)
                row = char
            else:
                row += char
        rows.append(row)
        if len(rows) > lines:
            rows = rows[:lines]
            while rows[-1] and font.size(rows[-1] + "…")[0] > width:
                rows[-1] = rows[-1][:-1]
            rows[-1] += "…"
        for index, row in enumerate(rows):
            self.text(screen, row, x, y + index * (size + 6), size, color)

    def card(self, screen, rect, color=PAPER):
        pygame.draw.rect(screen, color, rect, border_radius=16)

    def heart(self, screen, x, y, filled):
        color = RED if filled else "#D8D8CC"
        pygame.draw.circle(screen, color, (x + 6, y + 6), 6)
        pygame.draw.circle(screen, color, (x + 16, y + 6), 6)
        pygame.draw.polygon(screen, color, [(x, y + 8), (x + 22, y + 8), (x + 11, y + 21)])

    def draw_zoom(self, screen, controller):
        """확대 3×5칸. 로봇 줄은 맨 아래에 고정되고 맵이 위에서 내려옵니다."""
        g = controller.game
        width, height = COLS * CELL, ZOOM_ROWS * CELL
        area = pygame.Rect(ZOOM_X, ZOOM_Y, width, height)
        t = controller.move_progress
        t = t * t * (3 - 2 * t)  # 부드러운 출발·도착
        base_y = ZOOM_Y + (ZOOM_ROWS - 1) * CELL  # 로봇 줄의 위쪽 y
        pygame.draw.rect(screen, "#C9D3BF", area)
        screen.set_clip(area)
        for m in range(g.row - 1, g.row + ZOOM_ROWS + 1):
            y = round(base_y - (m - g.row + 1 - t) * CELL)
            strip = pygame.Rect(ZOOM_X, y, width, CELL)
            if m < 0:
                continue
            if m == 0 or m > ROWS:  # 출발선 / 결승선 너머
                pygame.draw.rect(screen, GOLD if m == 0 else SAGE, strip.inflate(-3, -3), border_radius=5)
                if m == 0 or m == ROWS + 1:
                    self.centered(screen, "START" if m == 0 else "FINISH", strip, 24, INK if m == 0 else PAPER)
                continue
            for c in range(COLS):
                cell = pygame.Rect(ZOOM_X + c * CELL, y, CELL, CELL)
                pygame.draw.rect(screen, ROAD, cell.inflate(-3, -3), border_radius=5)
                kind = g.cell(m, c)
                if kind:
                    screen.blit(self.sprite(kind, CELL), cell)
                    arriving = controller.moving and (m, c) == (g.row, g.col) and t < 0.8
                    if (m, c) in g.hits and not arriving:  # 부딪힌 장애물 표시
                        pygame.draw.line(screen, RED, cell.inflate(-24, -24).topleft, cell.inflate(-24, -24).bottomright, 7)
                        pygame.draw.line(screen, RED, cell.inflate(-24, -24).topright, cell.inflate(-24, -24).bottomleft, 7)
            self.text(screen, m, ZOOM_X + 8, y + 6, 14, MUTED)
        from_col = controller.move["from_col"] if controller.moving else g.col
        robot_x = round(ZOOM_X + (from_col + (g.col - from_col) * t) * CELL)
        screen.blit(self.sprite("robot", CELL), (robot_x, base_y))
        screen.set_clip(None)
        pygame.draw.rect(screen, GREEN, area.inflate(6, 6), 3, border_radius=6)
        ai = pygame.Rect(ZOOM_X, ZOOM_Y + (ZOOM_ROWS - AI_ROWS) * CELL, width, AI_ROWS * CELL)
        pygame.draw.rect(screen, ORANGE, ai.inflate(-4, -4), 3, border_radius=6)

    def draw_minimap(self, screen, g):
        """전체 맵. 아래가 출발선(S), 위가 결승선(F)입니다."""
        self.text(screen, f"전체 맵 {COLS}×{ROWS}", MINI_X - 8, MINI_Y - 28, 16)

        def row_y(m):
            return MINI_Y + (ROWS + 1 - m) * MINI

        for m in range(ROWS + 2):
            label = "S" if m == 0 else "F" if m > ROWS else m
            self.text(screen, label, MINI_X - 24, row_y(m) + 7, 14, MUTED)
            for c in range(COLS):
                rect = pygame.Rect(MINI_X + c * MINI, row_y(m), MINI - 2, MINI - 2)
                kind = g.cell(m, c)
                color = (GOLD if m == 0 else SAGE if m > ROWS else ROAD if not kind
                         else "#D8B9B0" if (m, c) in g.hits else BRICK if kind == "brick" else SIGN)
                pygame.draw.rect(screen, color, rect, border_radius=3)
        robot_row = min(g.row, ROWS + 1)
        center = (MINI_X + g.col * MINI + MINI // 2 - 1, row_y(robot_row) + MINI // 2 - 1)
        pygame.draw.circle(screen, GREEN, center, 9)
        pygame.draw.circle(screen, PAPER, center, 9, 2)
        for rows, color, grow in ((ZOOM_ROWS, GREEN, 8), (AI_ROWS, ORANGE, 2)):
            top = min(robot_row + rows - 1, ROWS + 1)
            frame = pygame.Rect(MINI_X, row_y(top), COLS * MINI - 2, (top - robot_row + 1) * MINI - 2)
            pygame.draw.rect(screen, color, frame.inflate(grow, grow), 3, border_radius=4)

    def draw_result(self, screen, controller):
        """최종 화면: 진행거리, 남은 목숨, 피한 장애물 수, 걸린 시간."""
        g = controller.game
        panel = pygame.Rect(ZOOM_X - 12, ZOOM_Y + 78, COLS * CELL + 24, 316)
        pygame.draw.rect(screen, PAPER, panel, border_radius=16)
        pygame.draw.rect(screen, GREEN if g.won else RED, panel, 4, border_radius=16)
        self.centered(screen, "완주 성공!" if g.won else "게임 오버", (panel.x, panel.y + 16, panel.width, 44),
                      30, GREEN if g.won else RED)
        lines = (f"진행거리: {g.row}/{ROWS}", f"남은 목숨: {g.lives}개",
                 f"피한 장애물의 수: {g.dodged}개", f"걸린 시간: {format_time(controller.play_seconds)}")
        for index, line in enumerate(lines):
            self.text(screen, line, panel.x + 34, panel.y + 80 + index * 44, 24)
        self.centered(screen, f"seed {g.scenario.seed}  ·  R 같은 맵 다시  ·  N 새 맵",
                      (panel.x, panel.bottom - 44, panel.width, 30), 14, MUTED)

    def render(self, screen, controller):
        g, s = controller.game, controller.game.scenario
        screen.fill(CREAM)
        pygame.draw.rect(screen, GREEN, (0, 0, 1200, 104))
        self.text(screen, "AFE 04  /  OBSERVE · DECIDE · ACT", 30, 17, 16, GOLD)
        self.wrapped(screen, s.title, 30, 47, 800, size=30, color=PAPER, lines=1)
        mode = "직접 조종 · LLM 미사용" if controller.manual else "LLM · API 연동"
        self.text(screen, f"상태: {controller.state}  ·  seed {s.seed}", 918, 22, 18, PAPER)
        self.text(screen, mode, 918, 57, 18, GOLD)

        self.card(screen, (24, 122, 548, 600))
        self.text(screen, f"환경  /  확대 {COLS}×{ZOOM_ROWS}칸", 44, 139, 20)
        self.draw_zoom(screen, controller)
        self.draw_minimap(screen, g)
        self.text(screen, "목숨", MINI_X - 24, 580, 16)
        for index in range(MAX_LIVES):
            self.heart(screen, MINI_X + 18 + index * 28, 580, index < g.lives)
        self.text(screen, f"진행 {g.row}/{ROWS}", MINI_X - 24, 612, 16)
        self.text(screen, f"피함 {g.dodged}개", MINI_X - 24, 638, 16)
        self.text(screen, f"초록 테: 화면 확대 구간   주황 테: AI가 보는 {COLS}×{AI_ROWS}칸", 44, 676, 14, MUTED)
        self.text(screen, "장애물: 벽돌 · 공사중 표지판   빨간 X: 부딪힌 장애물", 44, 698, 14, MUTED)
        if g.finished and not controller.moving:
            self.draw_result(screen, controller)

        self.card(screen, (592, 122, 584, 218))
        self.text(screen, "01  관측 → 현재 상태", 612, 139, 20)
        self.wrapped(screen, s.instruction, 612, 172, 410, size=16, lines=2)
        self.text(screen, f"진행 {g.row}/{ROWS}   목숨 {g.lives}/{MAX_LIVES}   피한 장애물 {g.dodged}개", 612, 222, 18)
        self.wrapped(screen, f"현재 열: {g.col}   허용 행동: " + ", ".join(get_legal_actions(g)), 612, 254, 420, size=16, lines=1)
        self.text(screen, f"AI에 전달: {COLS}×{AI_ROWS}칸", 1040, 143, 14, MUTED)
        for r, row in enumerate(get_observation(g)["view"]):  # 지금 Space를 누르면 전송될 내용
            for c, value in enumerate(row["cells"]):
                rect = pygame.Rect(1048 + c * 34, 168 + r * 34, 31, 31)
                pygame.draw.rect(screen, {"X": BRICK, "R": SAGE}.get(value, ROAD), rect, border_radius=4)
                self.centered(screen, value, rect, 16, PAPER if value != "." else MUTED)
        self.wrapped(screen, "제공자: GroqCloud   모델: " + controller.settings.model, 612, 286, 540, size=14, lines=1)
        elapsed = "-" if controller.last_elapsed is None else f"{controller.last_elapsed:.2f}초"
        self.text(screen, f"API {controller.request_count}회 · 직전 응답 {elapsed} · 누적 시간 {format_time(controller.play_seconds)}", 612, 310, 14, MUTED)

        self.card(screen, (592, 354, 584, 192))
        self.text(screen, "02  판단 → 응답 JSON", 612, 371, 20)
        if controller.busy:
            dots = "." * ((pygame.time.get_ticks() // 500) % 4)
            self.text(screen, "API 응답을 기다리는 중" + dots, 612, 410, 20, MUTED)
        elif controller.last_decision:
            d = controller.last_decision
            self.text(screen, '{"action": "' + d.action + '",', 612, 405, 20, GREEN)
            self.wrapped(screen, ' "reason": "' + d.reason + '"}', 612, 438, 540, size=18, lines=3)
        elif controller.manual:
            self.wrapped(screen, "직접 조종 중입니다. ← → 로 열을 옮기고 ↑ 로 정지(직진)합니다.\n이 모드에서는 API를 호출하지 않습니다.\nM: LLM 모드로 돌아가기", 612, 408, 540, size=18, color=MUTED, lines=4)
        else:
            self.wrapped(screen, "Space를 눌러 실제 LLM 응답을 받아 보세요.\nM을 누르면 API 없이 직접 조종할 수 있습니다.", 612, 408, 540, size=18, color=MUTED, lines=4)

        self.card(screen, (592, 560, 584, 162))
        self.text(screen, "03  실행 → 결과와 다음 관측", 612, 577, 20)
        self.wrapped(screen, controller.message, 612, 612, 262, size=16, color=RED if controller.error else GREEN, lines=4)
        self.text(screen, "최근 5턴", 888, 583, 14, MUTED)
        for index, h in enumerate(g.history[-5:]):
            mark = {"hit": " 충돌", "wall": " 벽", "safe": ""}[h["result"]] + (" (직접)" if h["source"] != "LLM" else "")
            row = f"#{h['turn']} {h['action']} 열 {h['before']}→{h['column']}{mark}"
            self.wrapped(screen, row, 888, 606 + index * 21, 268, size=14, color=RED if h["result"] != "safe" else MUTED, lines=1)

        pygame.draw.rect(screen, GREEN, (0, 740, 1200, 80))
        self.text(screen, "SPACE  AI 한 턴    R  같은 맵 다시    N  새 맵    F5  설정 다시 읽기    M  직접 조종    ESC  종료", 30, 754, 18, PAPER)
        note = "3×3칸을 텍스트로 읽는 LLM 에이전트입니다. 걸린 시간은 AI 통신·로봇 이동 시간만 셉니다."
        if controller.busy:
            note = "API 응답을 기다리는 중에는 맵·모드를 변경할 수 없습니다."
        elif controller.cooldown > 0:
            note = f"다음 API 요청까지 {controller.cooldown:.1f}초"
        self.text(screen, note, 30, 789, 14, GOLD)
