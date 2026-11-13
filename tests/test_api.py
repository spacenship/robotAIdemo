"""네트워크 대신 MockTransport를 사용. 실제 모델 응답 품질 검증은 아닙니다."""
import json
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace
import unittest

import httpx

from game import Game, load_scenario, get_observation
from llm_client import Settings, RobotLLM, Decision, DecisionError
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

    def test_real_sdk_sends_observation_and_strict_schema(self):
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
