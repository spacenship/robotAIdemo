"""네트워크 대신 MockTransport를 사용. 실제 모델 응답 품질 검증은 아닙니다."""
import json
import os
from pathlib import Path
from unittest import mock
from threading import Event
import time
import tempfile
from types import SimpleNamespace
import unittest

import httpx

from game import Game, load_scenario, get_observation
from llm_client import Settings, RobotLLM, Decision, DecisionError, PROVIDERS, load_settings
from main import Controller, is_key, RESET_CHARS, MANUAL_CHARS, NEW_MAP_CHARS

ROOT = Path(__file__).resolve().parents[1]


def completion(content, finish="stop"):
    return {"id": "test-response", "object": "chat.completion", "created": 0,
            "model": "openai/gpt-oss-20b", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content},
                 "finish_reason": finish}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}


class APITests(unittest.TestCase):
    def setUp(self):
        self.observation = get_observation(Game(load_scenario(ROOT / "scenario.json")))
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()

    def make_client(self, handler):
        client = RobotLLM(Settings(api_key="test-key-never-real"),
                          http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        self.clients.append(client)
        return client

    def test_each_provider_gets_its_own_url_key_and_model(self):
        seen = []

        def handler(request):
            seen.append((str(request.url), request.headers["authorization"],
                         json.loads(request.content)))
            return httpx.Response(200, json=completion('{"action":"stay","reason":"확인"}'))

        for name, info in PROVIDERS.items():
            settings = Settings(api_key=f"key-{name}", model=f"model-{name}", provider=name)
            client = RobotLLM(settings, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
            self.clients.append(client)
            self.assertEqual(client.choose_action(self.observation).action, "stay")
            url, auth, body = seen[-1]
            self.assertEqual((url, auth, body["model"]), (info["url"], f"Bearer key-{name}", f"model-{name}"))
            self.assertEqual(body[info["token_param"]], settings.max_tokens)
        self.assertIn("generativelanguage.googleapis.com", seen[-1][0])

    def test_request_error_shows_server_reason_without_key(self):
        body = [{"error": {"code": 400, "message": "API key not valid: test-key-never-real"}}]
        client = self.make_client(lambda r: httpx.Response(400, json=body))
        with self.assertRaises(DecisionError) as result:
            client.choose_action(self.observation)
        self.assertIn("API key not valid", str(result.exception))
        self.assertNotIn("test-key-never-real", str(result.exception))

    def test_request_sends_observation_and_strict_schema(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=completion('{"action":"stay","reason":"앞 칸이 비어 있습니다."}'))

        result = self.make_client(handler).choose_action(self.observation)
        self.assertEqual(len(requests), 1)
        body = requests[0]
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertEqual(json.loads(body["messages"][1]["content"]), self.observation)
        self.assertEqual(result.action, "stay")
        self.assertEqual(result.total_tokens, 30)

    def test_invalid_responses_are_not_actions(self):
        for raw in ("", "not JSON", '{"action":"jump","reason":"x"}',
                    '{"action":"stay","reason":123}', '[]',
                    '{"action":"stay","reason":"x","extra":true}'):
            with self.subTest(raw=raw):
                client = self.make_client(lambda r: httpx.Response(200, json=completion(raw)))
                with self.assertRaises(DecisionError):
                    client.choose_action(self.observation)

    def test_rate_limit_is_not_retried_and_returns_cooldown(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(429, headers={"retry-after": "8"},
                                  json={"error": {"message": "rate limit"}})

        with self.assertRaises(DecisionError) as result:
            self.make_client(handler).choose_action(self.observation)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.exception.retry_after, 8)

    def test_auth_and_timeout_are_handled_without_key_leak(self):
        auth = self.make_client(lambda r: httpx.Response(401, json={"error": {"message": "test-key-never-real"}}))
        with self.assertRaises(DecisionError) as result:
            auth.choose_action(self.observation)
        self.assertNotIn("test-key-never-real", str(result.exception))

        def timeout(request):
            raise httpx.ReadTimeout("timeout", request=request)

        with self.assertRaisesRegex(DecisionError, "시간 초과"):
            self.make_client(timeout).choose_action(self.observation)

    def test_missing_key_does_not_send_network_request(self):
        with self.assertRaisesRegex(DecisionError, "키가 없습니다"):
            RobotLLM(Settings()).choose_action(self.observation)

    def test_truncated_output_is_rejected(self):
        client = self.make_client(lambda r: httpx.Response(200, json=completion('{"action":', "length")))
        with self.assertRaisesRegex(DecisionError, "토큰 한도"):
            client.choose_action(self.observation)


def wait_until_idle(controller):
    deadline = time.monotonic() + 3
    while (controller.busy or controller.moving) and time.monotonic() < deadline:
        controller.poll()
        time.sleep(0.005)


def make_controller(client, settings=None, **kwargs):
    controller = Controller(ROOT / "scenario.json", client,
                            settings or Settings(api_key="test-key", min_interval=0),
                            log_dir=None, **kwargs)
    controller.move_seconds = 0.02  # 테스트에서는 이동 애니메이션을 짧게
    return controller


class Unused:
    def choose_action(self, observation):
        raise AssertionError("API를 호출하면 안 됩니다.")


class ControllerTests(unittest.TestCase):
    def test_pending_request_blocks_duplicates_reset_and_manual_mode(self):
        gate = Event()

        class SlowClient:
            calls = 0

            def choose_action(self, observation):
                self.calls += 1
                gate.wait(2)
                return Decision("stay", "테스트 응답", '{"action":"stay","reason":"테스트 응답"}', 0.1)

        client = SlowClient()
        controller = make_controller(client)
        try:
            self.assertTrue(controller.request_step())
            self.assertFalse(controller.request_step())
            controller.reset()
            controller.reset(new_seed=True)
            controller.toggle_manual()
            self.assertFalse(controller.manual)
            self.assertEqual(controller.game.row, 0)
            self.assertEqual(controller.game.scenario.seed, 42)
        finally:
            gate.set()
        wait_until_idle(controller)
        self.assertFalse(controller.busy)
        self.assertEqual(client.calls, 1)
        self.assertEqual((controller.game.col, controller.game.row), (1, 1))
        self.assertEqual(controller.request_count, 1)

    def test_api_failure_leaves_state_unchanged(self):
        class BrokenClient:
            def choose_action(self, observation):
                raise DecisionError("가상 API 오류", retry_after=8)

        controller = make_controller(BrokenClient(), Settings(api_key="test-key"))
        controller.request_step()
        wait_until_idle(controller)
        self.assertEqual((controller.game.col, controller.game.row), (1, 0))
        self.assertEqual(controller.game.lives, 3)
        self.assertGreater(controller.cooldown, 7)
        self.assertTrue(controller.error)
        self.assertEqual(controller.state, "오류")
        controller.reset()
        self.assertEqual(controller.state, "준비")

    def test_state_labels(self):
        controller = make_controller(Unused())
        self.assertEqual(controller.state, "준비")
        controller.toggle_manual()
        self.assertEqual(controller.state, "직접 조종")
        controller.manual_step("stay")
        self.assertEqual(controller.state, "이동 중")
        controller.manual_step("stay")  # 이동 중 입력은 무시합니다.
        self.assertEqual(controller.game.row, 1)
        wait_until_idle(controller)
        controller.toggle_manual()

        # 벽으로 가는 행동은 거부된 행동이지 API 오류가 아닙니다.
        controller.reset()
        controller.game.col = 0
        controller.results.put((get_observation(controller.game),
                                Decision("left", "벽 실험", '{"action":"left","reason":"벽 실험"}', 0.1), None, 0.1))
        controller.poll()
        wait_until_idle(controller)
        self.assertEqual(controller.state, "준비")

        controller.reset()
        controller.game.lives = 0
        self.assertEqual(controller.state, "게임 오버")
        controller.reset()
        controller.game.row = 10
        self.assertEqual(controller.state, "완주 성공")
        self.assertEqual(Controller(ROOT / "scenario.json", Unused(), Settings(), log_dir=None).state, "오류")

    def test_play_time_counts_only_api_and_movement(self):
        class Client:
            def choose_action(self, observation):
                time.sleep(0.1)
                return Decision("stay", "테스트", '{"action":"stay","reason":"테스트"}', 0.1)

        controller = make_controller(Client())
        controller.move_seconds = 0.1
        controller.request_step()
        wait_until_idle(controller)
        first = controller.play_seconds
        self.assertGreater(first, 0.19)
        self.assertLess(first, 0.6)
        time.sleep(0.3)  # 턴과 턴 사이에 기다린 시간은 세지 않습니다.
        self.assertEqual(controller.play_seconds, first)
        controller.toggle_manual()
        controller.manual_step("stay")
        wait_until_idle(controller)
        self.assertGreater(controller.play_seconds, first + 0.09)
        self.assertLess(controller.play_seconds, first + 0.3)
        controller.reset()
        self.assertEqual(controller.play_seconds, 0)

    def test_seed_override_and_new_map(self):
        controller = make_controller(Unused(), seed=7)
        self.assertEqual(controller.game.scenario.seed, 7)
        controller.reset(reload_map=True)
        self.assertEqual(controller.game.scenario.seed, 7)
        grid = controller.game.scenario.grid
        controller.reset()
        self.assertEqual(controller.game.scenario.grid, grid)
        controller.reset(new_seed=True)
        self.assertIn(str(controller.game.scenario.seed), controller.message)

    def test_only_finished_runs_are_saved_one_file_each(self):
        class StayClient:
            def choose_action(self, observation):
                return Decision("stay", "테스트", '{"action":"stay","reason":"테스트"}', 0.01)

        def play(controller, turns=99):
            for _ in range(turns):
                if controller.game.finished:
                    break
                controller.request_step()
                wait_until_idle(controller)

        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            controller = Controller(ROOT / "scenario.json", StayClient(),
                                    Settings(api_key="test-key", min_interval=0),
                                    seed=7, log_dir=log_dir)
            controller.move_seconds = 0.01

            play(controller, turns=2)          # 중간에 끊은 주행은 저장하지 않습니다.
            controller.reset(reload_map=True)
            controller.toggle_manual()         # 직접 조종만 한 판도 저장하지 않습니다.
            while not controller.game.finished:
                controller.manual_step("stay")
                wait_until_idle(controller)
            controller.toggle_manual()
            self.assertFalse(log_dir.exists())

            controller.reset()
            play(controller)
            self.assertTrue(controller.game.finished)
            self.assertEqual([p.name for p in log_dir.iterdir()], ["test1.json"])
            self.assertIn("test1.json", controller.message)
            run = json.loads((log_dir / "test1.json").read_text(encoding="utf-8"))
            game = controller.game
            self.assertEqual(run["environment"]["seed"], 7)
            self.assertEqual(len(run["environment"]["map"]), 10)
            self.assertEqual(run["summary"]["distance"], game.row)
            self.assertEqual(run["summary"]["lives"], game.lives)
            self.assertEqual(run["summary"]["outcome"], "완주 성공" if game.won else "게임 오버")
            self.assertEqual(len(run["turns"]), len(game.history))
            self.assertEqual(run["turns"][0]["action"], "stay")
            self.assertEqual(run["turns"][0]["result"]["turn"], 1)
            self.assertNotIn("test-key", json.dumps(run))

            controller.reset()                 # 다음 주행은 다음 번호로, 기존 파일은 그대로
            play(controller)
            self.assertEqual(sorted(p.name for p in log_dir.iterdir()), ["test1.json", "test2.json"])
            self.assertEqual(json.loads((log_dir / "test1.json").read_text(encoding="utf-8")), run)

            # 주행 목록(H 화면)에는 직접 조종 판까지 3개, 종료 시 한 파일로 저장
            self.assertEqual([r["summary"]["mode"] for r in controller.runs], ["직접 조종", "LLM", "LLM"])
            self.assertEqual([r.get("file") for r in controller.runs], [None, "test1.json", "test2.json"])
            path = controller.save_session()
            self.assertRegex(path.name, r"^session_\d{8}_\d{6}\.json$")
            session = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(session["run_count"], 3)
            self.assertEqual(session["runs"][1]["summary"], run["summary"])
            self.assertEqual(len(session["runs"][0]["turns"]), session["runs"][0]["summary"]["turns"])

    def test_no_session_file_without_finished_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(ROOT / "scenario.json", Unused(), Settings(api_key="k"),
                                    log_dir=Path(directory) / "logs")
            self.assertIsNone(controller.save_session())
            self.assertFalse((Path(directory) / "logs").exists())

    def test_f5_keeps_the_current_map_unless_the_file_map_changed(self):
        data = json.loads((ROOT / "scenario.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"

            def write(**changes):
                data.update(changes)
                path.write_text(json.dumps(data), encoding="utf-8")

            write(seed=42)
            controller = Controller(path, Unused(), Settings(api_key="k"), log_dir=None)
            controller.reset(new_seed=True)
            scenario = controller.game.scenario
            write(instruction="새 지시문", lives=5)
            controller.reset(reload_map=True)       # 설정만 바뀌고 맵은 그대로
            after = controller.game.scenario
            self.assertEqual((after.seed, after.grid), (scenario.seed, scenario.grid))
            self.assertEqual((after.instruction, after.lives, controller.game.lives), ("새 지시문", 5, 5))
            write(seed=7)
            controller.reset(reload_map=True)       # 파일의 seed를 고치면 그 맵으로
            self.assertEqual(controller.game.scenario.seed, 7)
            rows = ["B.."] * 10
            write(map=rows)
            controller.reset(reload_map=True)       # 직접 만든 맵
            self.assertIsNone(controller.game.scenario.seed)
            controller.reset(new_seed=True)
            self.assertIsNotNone(controller.game.scenario.seed)
            write(map="잘못된 맵")
            controller.reset(reload_map=True)       # 설정 오류면 게임은 그대로
            self.assertTrue(controller.error)
            self.assertIn("설정 오류", controller.message)
            self.assertIsNotNone(controller.game.scenario.seed)

            fixed = Controller(path.parent / "scenario.json", Unused(), Settings(api_key="k"),
                               seed=3, lives=2, log_dir=None) if write(map=None) is None else None
            self.assertEqual((fixed.game.scenario.seed, fixed.game.lives), (3, 2))  # 명령줄 값이 우선
            fixed.reset(new_seed=True)
            self.assertEqual(fixed.game.lives, 2)

    def test_switching_provider(self):
        env = {"GROQ_API_KEY": "groq-test", "GEMINI_API_KEY": "", "GEMINI_MODEL": "gemini-test",
               "LLM_PROVIDER": ""}
        with mock.patch.dict(os.environ, env), mock.patch("llm_client.load_dotenv"):
            settings = load_settings()
            self.assertEqual((settings.provider, settings.label), ("groq", "GroqCloud"))
            controller = make_controller(RobotLLM(settings), settings)
            controller.toggle_manual()
            controller.manual_step("stay")
            wait_until_idle(controller)
            controller.toggle_manual()
            controller.switch_provider()            # 진행 중이던 주행은 처음부터
            self.assertEqual((controller.settings.provider, controller.settings.model),
                             ("gemini", "gemini-test"))
            self.assertEqual(controller.game.row, 0)
            self.assertIn("GEMINI_API_KEY", controller.message)
            self.assertFalse(controller.request_step())  # 키가 없으면 요청하지 않음
            controller.switch_provider()
            self.assertEqual(controller.settings.provider, "groq")
            self.assertEqual(controller.state, "준비")
            controller.llm.close()
            with self.assertRaisesRegex(ValueError, "LLM_PROVIDER"):
                load_settings("없는제공자")

    def test_keys_accept_hangul_input(self):
        r_key, m_key, n_key = 114, 109, 110  # pygame.K_r, pygame.K_m, pygame.K_n
        self.assertTrue(is_key(SimpleNamespace(key=r_key, unicode="r"), r_key, RESET_CHARS))
        self.assertTrue(is_key(SimpleNamespace(key=12593, unicode="ㄱ"), r_key, RESET_CHARS))
        self.assertTrue(is_key(SimpleNamespace(key=12641, unicode="ㅡ"), m_key, MANUAL_CHARS))
        self.assertTrue(is_key(SimpleNamespace(key=12636, unicode="ㅜ"), n_key, NEW_MAP_CHARS))
        self.assertFalse(is_key(SimpleNamespace(key=32, unicode=" "), r_key, RESET_CHARS))
        self.assertFalse(is_key(SimpleNamespace(key=1073741886, unicode=""), r_key, RESET_CHARS))


if __name__ == "__main__":
    unittest.main()
