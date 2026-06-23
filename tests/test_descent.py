import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.backend import main


class TestDescentHelpers(unittest.TestCase):
    def test_subtree_ids(self):
        cmap = {None: [{"id": 1}, {"id": 5}], 1: [{"id": 2}, {"id": 3}], 3: [{"id": 4}]}
        self.assertEqual(sorted(main._subtree_ids([1], cmap)), [1, 2, 3, 4])
        self.assertEqual(sorted(main._subtree_ids([5], cmap)), [5])

    def test_valid_ids(self):
        self.assertEqual(main._valid_ids([2, "3", 99, "x", None], {2, 3}), [2, 3])
        self.assertEqual(main._valid_ids(None, {1}), [])

    def test_cap_frontier(self):
        frontier = [{"id": i} for i in range(50)]
        heat = {49: 0.9, 10: 0.8}
        capped = main._cap_frontier(frontier, heat, cap=5)
        self.assertEqual(len(capped), 5)
        # En yüksek ısılı düğümler önce gelmeli
        ids = [n["id"] for n in capped]
        self.assertIn(49, ids)
        self.assertIn(10, ids)


class TestAggregateHeatmap(unittest.TestCase):
    def test_propagates_to_ancestors(self):
        children = {None: [{"id": 1}], 1: [{"id": 2}, {"id": 3}], 3: [{"id": 4}]}
        heat = {4: 0.9, 2: 0.3}
        agg = main._aggregate_heatmap(heat, children)
        self.assertAlmostEqual(agg[4], 0.9)
        self.assertAlmostEqual(agg[3], 0.9)   # çocuğu 4'ten miras
        self.assertAlmostEqual(agg[2], 0.3)
        self.assertAlmostEqual(agg[1], 0.9)   # alt-ağacın en yükseği


class TestShouldRunDescent(unittest.TestCase):
    def test_flat_small_tree_skips(self):
        nodes = [{"id": i} for i in range(10)]  # 10 < eşik (25)
        self.assertFalse(main._should_run_descent(nodes))

    def test_rich_tree_runs(self):
        nodes = [{"id": i} for i in range(40)]  # 40 >= eşik
        self.assertTrue(main._should_run_descent(nodes))


class TestRecursiveDescent(unittest.TestCase):
    def setUp(self):
        # 1(iç)->[2(yaprak),3(iç)->[4(yaprak)]], 5(yaprak)
        self.node_map = {i: {"id": i, "heading": f"n{i}", "is_leaf": 1, "summary": ""} for i in range(1, 6)}
        self.node_map[1]["is_leaf"] = 0
        self.node_map[3]["is_leaf"] = 0
        self.children = {
            None: [self.node_map[1], self.node_map[5]],
            1: [self.node_map[2], self.node_map[3]],
            3: [self.node_map[4]],
        }
        self._orig = main._ask_descent

    def tearDown(self):
        main._ask_descent = self._orig

    def _mock_sequence(self, seq):
        calls = {"i": 0}
        def fake(model, q, front, heat):
            r = seq[calls["i"]]
            calls["i"] += 1
            return r
        main._ask_descent = fake
        return calls

    def test_descend_then_select(self):
        seq = [
            {"select": [], "descend": [1], "confidence": "HIGH"},
            {"select": [2], "descend": [3], "confidence": "HIGH"},
            {"select": [4], "descend": [], "confidence": "HIGH"},
        ]
        self._mock_sequence(seq)
        targets, conf, descended = main.run_recursive_descent(1, "q", self.children, self.node_map, {}, "x", [])
        self.assertEqual(sorted(targets), [2, 4])
        self.assertEqual(conf, "HIGH")
        # İnilen bölüm başlıkları (çocuğu olan) takip edilmeli
        self.assertEqual(sorted(descended), [1, 3])

    def test_low_confidence_breaks(self):
        self._mock_sequence([{"select": [], "descend": [], "confidence": "LOW"}])
        targets, conf, _descended = main.run_recursive_descent(1, "q", self.children, self.node_map, {}, "x", [])
        self.assertEqual(targets, set())
        self.assertEqual(conf, "LOW")

    def test_descend_leaf_becomes_target(self):
        # 5 yaprak; descend'e 5 verilse bile hedef olur
        self._mock_sequence([{"select": [], "descend": [5], "confidence": "HIGH"}])
        targets, conf, _descended = main.run_recursive_descent(1, "q", self.children, self.node_map, {}, "x", [])
        self.assertEqual(sorted(targets), [5])


if __name__ == "__main__":
    unittest.main()
