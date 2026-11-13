"""실행 진입점. Space 한 번 = API 요청 한 번 = 한 턴(좌우 이동 + 맵 한 줄 전진)."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Queue, Empty
import random
import sys
from threading import Thread
import time

from game import Game, load_scenario, get_observation, apply_action
from llm_client import ROOT, RobotLLM, DecisionError, load_settings

# 한글 입력 상태에서는 R·M·N 키가 'ㄱ'·'ㅡ'·'ㅜ'로 들어올 수 있어 함께 인정합니다.
RESET_CHARS = ("r", "R", "ㄱ")
MANUAL_CHARS = ("m", "M", "ㅡ")
NEW_MAP_CHARS = ("n", "N", "ㅜ")
MOVE_SECONDS = 0.45  # 로봇 이동(맵이 한 줄 내려오는) 애니메이션 길이


def is_key(event, key, chars):
    return event.key == key or getattr(event, "unicode", "") in chars


def print_json(label, value):
    print(f"\n[{label}]", flush=True)
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


class Controller:
    """네트워크는 작업 스레드, 게임 변경과 그리기는 메인 스레드에서 수행합니다."""
    def __init__(self, scenario_path, llm, settings, *, seed=None, log_dir=ROOT / "logs"):
        self.scenario_path = scenario_path
        self.seed_override = seed  # 명령줄 --seed. scenario.json의 seed보다 우선합니다.
        self.game = Game(load_scenario(scenario_path, seed))
        self.llm, self.settings = llm, settings
        self.busy = False
        self.manual = False
        self.request_count = 0  # R을 눌러도 앱 실행 중 API 요청 수는 유지합니다.
        self.last_decision = None
        self.last_elapsed = None
        # 걸린 시간: AI 통신 + 로봇 이동에 쓴 시간만 더합니다. 턴 사이 대기는 세지 않습니다.
        self.play_seconds = 0.0
        self.turn_started = None
        self.move = None  # 이동 애니메이션 {"start", "from_col"}
        self.move_seconds = MOVE_SECONDS
        self.message = "Space: LLM에게 다음 행동을 물어보세요."
        if not settings.api_key:
            self.message = "API 키가 없습니다. .env 설정 후 재실행하세요. M: 직접 조종"
        self.error = False
        self.api_error = False  # error는 충돌·거부도 포함하고, api_error는 API 실패만 뜻합니다.
        self.next_request_at = 0.0
        self.results = Queue()
        self.log_path = None if log_dir is None else log_dir / (datetime.now().strftime("session_%Y%m%d_%H%M%S_%f") + ".jsonl")

    @property
    def cooldown(self):
        return max(0.0, self.next_request_at - time.monotonic())

    @property
    def moving(self):
        return self.move is not None

    @property
    def move_progress(self):
        """0.0(이동 시작) ~ 1.0(이동 끝). 화면이 이 값으로 맵을 내려 그립니다."""
        if self.move is None or self.move_seconds <= 0:
            return 1.0
        return min(1.0, (time.perf_counter() - self.move["start"]) / self.move_seconds)

    @property
    def state(self):
        """화면에 표시할 현재 상태. 충돌·거부된 행동은 API 오류가 아니므로 '준비'로 남습니다."""
        if self.busy:
            return "API 요청 중"
        if self.moving:
            return "이동 중"
        if self.game.won:
            return "완주 성공"
        if self.game.finished:
            return "게임 오버"
        if self.manual:
            return "직접 조종"
        return "오류" if self.api_error or not self.settings.api_key else "준비"

    def request_step(self):
        # 응답 대기·이동 중 연타해도 요청은 한 개뿐입니다.
        if self.busy or self.moving or self.game.finished or self.manual:
            return False
        if not self.settings.api_key:
            self.message, self.error = "API 키가 없습니다. .env 설정 후 앱을 다시 실행하세요.", True
            return False
        if self.cooldown > 0:
            self.message = f"요청 간격 대기 중입니다. {self.cooldown:.1f}초 후 Space를 누르세요."
            return False
        observation = get_observation(self.game)
        self.busy, self.error, self.api_error = True, False, False
        self.message = "LLM 응답을 기다립니다. 응답이 오면 한 턴을 진행합니다."
        self.request_count += 1
        self.turn_started = time.perf_counter()
        self.next_request_at = time.monotonic() + self.settings.min_interval
        print_json(f"API 요청 {self.request_count} / 관측", observation)

        def worker():
            start = time.perf_counter()
            try:
                decision = self.llm.choose_action(observation)
                self.results.put((observation, decision, None, decision.elapsed))
            except DecisionError as exc:
                self.results.put((observation, None, exc, time.perf_counter() - start))
            except Exception as exc:
                # 인증 헤더가 담길 수 있는 SDK 예외 전체 문자열은 기록하지 않습니다.
                error = DecisionError(f"처리 오류({type(exc).__name__}): 설정과 설치된 패키지를 확인하세요.")
                self.results.put((observation, None, error, time.perf_counter() - start))
        Thread(target=worker, daemon=True).start()
        return True

    def start_move(self, from_col):
        if self.turn_started is None:
            self.turn_started = time.perf_counter()
        self.move = {"start": time.perf_counter(), "from_col": from_col}

    def finish_turn(self):
        """이번 턴에 쓴 실제 시간(AI 통신 + 이동)을 누적합니다."""
        if self.turn_started is not None:
            self.play_seconds += time.perf_counter() - self.turn_started
        self.turn_started, self.move = None, None

    def poll(self):
        """매 프레임 호출: 도착한 API 응답을 적용하고, 끝난 이동을 마무리합니다."""
        if self.moving and self.move_progress >= 1.0:
            self.finish_turn()
        try:
            observation, decision, error, elapsed = self.results.get_nowait()
        except Empty:
            return
        self.busy = False
        self.last_elapsed = elapsed
        record = {"time": datetime.now(timezone.utc).isoformat(),
                  "request": self.request_count, "model": self.settings.model,
                  "seed": self.game.scenario.seed,
                  "observation": observation, "elapsed_seconds": round(elapsed, 3)}
        if error:
            self.last_decision = None
            self.error, self.api_error = True, True
            self.message = str(error)
            self.next_request_at = max(self.next_request_at, time.monotonic() + error.retry_after)
            record.update(error=str(error), raw=error.raw)
            print_json("API 실패 / 이동 없음", {"error": str(error), "raw": error.raw})
            self.finish_turn()  # 실패한 통신 시간도 걸린 시간에 포함합니다.
        else:
            self.last_decision = decision
            from_col = self.game.col
            entry = apply_action(self.game, decision.action, decision.reason)
            self.error = entry["collided"] or not entry["valid"]
            self.message = self.game.last_feedback
            record.update(raw=decision.raw, response_id=decision.response_id,
                          total_tokens=decision.total_tokens, result=entry)
            print_json("API 응답 원문", decision.raw)
            print_json("행동 결과", entry)
            self.start_move(from_col)
        self.write_log(record)

    def write_log(self, record):
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            self.message += " (로그를 저장할 수 없습니다.)"

    def reset(self, *, reload_map=False, new_seed=False):
        """R: 같은 맵 다시 / F5: scenario.json 다시 읽기 / N: 새 seed로 새 맵."""
        if self.busy:
            return
        try:
            if new_seed:
                scenario = load_scenario(self.scenario_path, random.randrange(1_000_000))
            elif reload_map:
                scenario = load_scenario(self.scenario_path, self.seed_override)
            else:
                scenario = self.game.scenario
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.message, self.error = f"설정 오류: {exc}", True
            return
        self.game = Game(scenario)
        self.last_decision, self.last_elapsed = None, None
        self.play_seconds, self.turn_started, self.move = 0.0, None, None
        self.message = (f"새 맵을 만들었습니다. (seed {scenario.seed})" if new_seed
                        else "scenario.json을 다시 읽었습니다." if reload_map
                        else "같은 맵으로 처음부터 다시 시작합니다.")
        self.error, self.api_error = False, False

    def toggle_manual(self):
        if not self.busy and not self.moving:
            self.manual = not self.manual
            self.error, self.api_error = False, False
            self.last_decision = None
            self.message = "직접 조종: ← → 이동, ↑ 정지(직진). API는 호출하지 않습니다." if self.manual else "LLM 모드: Space로 다음 행동을 요청하세요."

    def manual_step(self, action):
        if self.busy or self.moving or not self.manual or self.game.finished:
            return
        from_col = self.game.col
        entry = apply_action(self.game, action, "학생이 방향키로 직접 선택했습니다.", "직접 조종")
        self.message, self.error = self.game.last_feedback, entry["collided"] or not entry["valid"]
        self.start_move(from_col)
        self.write_log({"time": datetime.now(timezone.utc).isoformat(), "mode": "manual",
                        "seed": self.game.scenario.seed, "result": entry})


def check_api(scenario_path, llm, seed=None):
    """키 설정 후 GUI 없이 실제 API 요청 딱 한 번을 점검합니다."""
    game = Game(load_scenario(scenario_path, seed))
    observation = get_observation(game)
    print_json("전송할 관측", observation)
    try:
        decision = llm.choose_action(observation)
    except DecisionError as exc:
        print(f"API 연결 확인 실패: {exc}", file=sys.stderr)
        return 1
    print_json("실제 API 응답", decision.raw)
    print_json("적용 결과", apply_action(game, decision.action, decision.reason))
    print(f"응답 {decision.elapsed:.2f}초 / response_id={decision.response_id}")
    return 0


def run_gui(controller):
    # CLI의 --check-api는 Pygame 화면을 열지 않습니다.
    import pygame
    from view import View
    pygame.init()
    pygame.key.set_repeat()  # 키를 길게 눌러도 API를 반복 호출하지 않습니다.
    info = pygame.display.Info()
    scale = min(1.0, (info.current_w - 60) / 1200, (info.current_h - 100) / 820)
    scale = max(0.5, scale)
    window = pygame.display.set_mode((int(1200 * scale), int(820 * scale)), pygame.RESIZABLE)
    screen = pygame.Surface((1200, 820))
    pygame.display.set_caption("남원고 AFE · Robot AI Demo")
    try:
        pygame.key.stop_text_input()  # 한글 입력기(IME) 창이 뜨지 않게 합니다.
    except Exception:
        pass
    view, clock = View(), pygame.time.Clock()
    arrows = {pygame.K_LEFT: "left", pygame.K_RIGHT: "right", pygame.K_UP: "stay"}
    running = True
    while running:
        # 한 프레임에 대기 중 입력부터 처리하고, 그 뒤 응답을 적용합니다.
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and not getattr(event, "repeat", False):
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    controller.request_step()
                elif is_key(event, pygame.K_r, RESET_CHARS):
                    controller.reset()
                elif is_key(event, pygame.K_n, NEW_MAP_CHARS):
                    controller.reset(new_seed=True)
                elif event.key == pygame.K_F5:
                    controller.reset(reload_map=True)
                elif is_key(event, pygame.K_m, MANUAL_CHARS):
                    controller.toggle_manual()
                elif event.key in arrows:
                    controller.manual_step(arrows[event.key])
        controller.poll()
        view.render(screen, controller)
        # 학교 노트북 해상도에서도 조작 안내가 잘리지 않도록 화면 전체를 맞춥니다.
        width, height = window.get_size()
        ratio = min(width / 1200, height / 820)
        target = (max(1, int(1200 * ratio)), max(1, int(820 * ratio)))
        window.fill("#15370D")
        window.blit(pygame.transform.smoothscale(screen, target),
                    ((width - target[0]) // 2, (height - target[1]) // 2))
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()


def main():
    parser = argparse.ArgumentParser(description="남원고 4차시: LLM API로 장애물을 피하는 로봇")
    parser.add_argument("--check-api", action="store_true", help="실제 API 요청 1회로 연결 확인")
    parser.add_argument("--scenario", type=Path, default=ROOT / "scenario.json")
    parser.add_argument("--seed", type=int, default=None,
                        help="장애물 맵의 random seed. 같은 seed면 같은 맵 (scenario.json보다 우선)")
    args = parser.parse_args()
    llm = None
    try:
        settings = load_settings()
        llm = RobotLLM(settings)
        if args.check_api:
            result = check_api(args.scenario, llm, args.seed)
            llm.close()
            return result
        controller = Controller(args.scenario, llm, settings, seed=args.seed)
        print(f"맵 seed: {controller.game.scenario.seed}", flush=True)
        run_gui(controller)
        # 요청 대기 중 창을 닫으면 daemon 스레드를 기다리지 않고 종료합니다.
        if controller.busy:
            print("요청 대기 중 종료했습니다. 이미 전송된 API 요청은 서버에서 처리될 수 있습니다.")
        else:
            llm.close()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"실행 설정 오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # Windows 터미널의 한글 출력 오류를 피합니다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
