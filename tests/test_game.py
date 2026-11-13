import json
from pathlib import Path
import tempfile
import unittest

from game import (Game, Scenario, load_scenario, generate_map, get_observation, apply_action,
                  get_legal_actions, COLS, ROWS, AI_ROWS, MAX_LIVES, START_COL)

ROOT = Path(__file__).resolve().parents[1]


def make_game(*rows):
    """테스트용 맵. 'X..' 같은 문자열을 가까운 줄부터 적고, 남는 줄은 왼쪽 장애물로 채웁니다."""
    rows = list(rows) + ["X.."] * (ROWS - len(rows))
    grid = tuple(tuple("brick" if ch == "X" else None for ch in row) for row in rows)
    return Game(Scenario("테스트", "테스트 지시문", 0, grid))


def is_solvable(grid) -> bool:
    """START_COL에서 출발해 한 번도 부딪히지 않고 끝까지 갈 길이 있는지 검사합니다."""
    reachable = {START_COL}
    for row in grid:
        reachable = {c for old in reachable for c in (old - 1, old, old + 1)
                     if 0 <= c < COLS and row[c] is None}
    return bool(reachable)


class MapTests(unittest.TestCase):
    def test_same_seed_gives_same_map(self):
        self.assertEqual(generate_map(42), generate_map(42))
        self.assertNotEqual(generate_map(1), generate_map(2))

    def test_generation_rules_hold_for_many_seeds(self):
        for seed in range(2000):
            grid = generate_map(seed)
            self.assertEqual((len(grid), len(grid[0])), (ROWS, COLS))
            counts = [sum(cell is not None for cell in row) for row in grid]
            self.assertTrue(all(count in (1, 2) for count in counts), seed)
            for i in range(1, ROWS):  # 2개짜리 줄은 연속하지 않음
                self.assertFalse(counts[i] == 2 and counts[i - 1] == 2, seed)
            for i in range(2, ROWS):  # 같은 열에 3줄 연속 금지
                for c in range(COLS):
                    self.assertFalse(grid[i][c] and grid[i - 1][c] and grid[i - 2][c], seed)
            self.assertTrue(is_solvable(grid), seed)

    def test_scenario_seed_and_override(self):
        scenario = load_scenario(ROOT / "scenario.json")
        self.assertEqual(scenario.grid, generate_map(scenario.seed))
        self.assertEqual(load_scenario(ROOT / "scenario.json", 7).seed, 7)

    def test_null_seed_picks_one_and_bad_seed_is_rejected(self):
        data = json.loads((ROOT / "scenario.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            data["seed"] = None
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsInstance(load_scenario(path).seed, int)
            data["seed"] = "abc"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_scenario(path)


class GameTests(unittest.TestCase):
    def test_observation_is_3x3_and_follows_the_robot(self):
        game = make_game("X..", ".X.", "..X")
        before = get_observation(game)
        self.assertEqual(len(before["view"]), AI_ROWS)
        self.assertEqual([row["rows_ahead"] for row in before["view"]], [2, 1, 0])
        self.assertEqual([row["cells"] for row in before["view"]],
                         [[".", "X", "."], ["X", ".", "."], [".", "R", "."]])
        self.assertEqual(before["legal_actions"], ["left", "right", "stay"])
        apply_action(game, "right", "오른쪽으로")
        after = get_observation(game)
        self.assertEqual(after["robot_column"], 2)
        self.assertEqual([row["cells"] for row in after["view"]],
                         [[".", ".", "X"], [".", "X", "."], ["X", ".", "R"]])
        self.assertEqual(after["legal_actions"], ["left", "stay"])
        self.assertEqual(after["distance"], 1)
        self.assertEqual(after["recent_history"][0]["action"], "right")

    def test_every_turn_advances_one_row(self):
        game = make_game()
        for turn, action in enumerate(("stay", "right", "left"), start=1):
            apply_action(game, action, "")
            self.assertEqual(game.row, turn)
        self.assertEqual((game.col, game.lives, game.dodged), (1, MAX_LIVES, 3))

    def test_collision_costs_a_life_and_is_not_counted_as_dodged(self):
        game = make_game(".X.", "XX.")
        entry = apply_action(game, "stay", "충돌 실험")
        self.assertTrue(entry["collided"])
        self.assertEqual((game.lives, game.dodged, game.row), (MAX_LIVES - 1, 0, 1))
        self.assertIn((1, 1), game.hits)
        self.assertIn("목숨이 2개", get_observation(game)["last_feedback"])
        apply_action(game, "right", "")  # 2개짜리 줄을 피하면 2개 모두 피한 것
        self.assertEqual((game.lives, game.dodged), (MAX_LIVES - 1, 2))

    def test_wall_and_unknown_actions_keep_the_column_but_still_advance(self):
        game = make_game("..X", "..X")
        apply_action(game, "left", "")
        entry = apply_action(game, "left", "벽 실험")
        self.assertFalse(entry["valid"])
        self.assertEqual((game.col, game.row, entry["result"]), (0, 2, "wall"))
        self.assertIn("벽", game.last_feedback)
        entry = apply_action(game, "teleport", "없는 행동 실험")
        self.assertFalse(entry["valid"])
        self.assertEqual(game.row, 3)
        self.assertIn("알 수 없는 행동", game.last_feedback)

    def test_three_collisions_end_the_game(self):
        game = make_game(".X.", ".X.", "X..", ".X.")
        for action in ("stay", "stay", "stay", "stay"):
            apply_action(game, action, "")
        self.assertEqual((game.lives, game.row), (0, 4))
        self.assertTrue(game.finished)
        self.assertFalse(game.won)
        with self.assertRaisesRegex(ValueError, "끝난 게임"):
            apply_action(game, "stay", "")

    def test_finishing_the_map_wins(self):
        game = make_game()
        for _ in range(ROWS):
            apply_action(game, "stay", "")
        self.assertTrue(game.won)
        self.assertEqual((game.row, game.lives, game.dodged), (ROWS, MAX_LIVES, ROWS))
        self.assertEqual(len(get_observation(game)["recent_history"]), 3)
        self.assertEqual(get_legal_actions(game), ["left", "right", "stay"])


if __name__ == "__main__":
    unittest.main()
