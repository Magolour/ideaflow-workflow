import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from agent import topo_layers, validate_edges  # noqa: E402

IDS = {"T1", "T2", "T3"}


class TopoLayersTest(unittest.TestCase):
    def test_independent_tasks_share_first_layer(self):
        layers, order = topo_layers(["T1", "T2"], [])
        self.assertEqual(layers, [["T1", "T2"]])
        self.assertEqual(order, ["T1", "T2"])

    def test_chain_produces_one_task_per_layer(self):
        layers, order = topo_layers(["T1", "T2", "T3"],
                                    [{"from": "T1", "to": "T2", "type": "force"},
                                     {"from": "T2", "to": "T3", "type": "force"}])
        self.assertEqual(layers, [["T1"], ["T2"], ["T3"]])
        self.assertEqual(order, ["T1", "T2", "T3"])

    def test_fan_out_and_join(self):
        layers, _ = topo_layers(
            ["T1", "T2", "T3"],
            [{"from": "T1", "to": "T2", "type": "force"},
             {"from": "T1", "to": "T3", "type": "force"}])
        self.assertEqual(layers, [["T1"], ["T2", "T3"]])

    def test_cycle_rejected_with_task_ids(self):
        with self.assertRaises(ValueError) as ctx:
            topo_layers(["T1", "T2"],
                        [{"from": "T1", "to": "T2", "type": "force"},
                         {"from": "T2", "to": "T1", "type": "force"}])
        self.assertIn("循环依赖", str(ctx.exception))
        self.assertIn("T1", str(ctx.exception))

    def test_self_loop_rejected(self):
        with self.assertRaises(ValueError):
            topo_layers(["T1"], [{"from": "T1", "to": "T1", "type": "force"}])

    def test_three_node_cycle_rejected(self):
        edges = [{"from": "T1", "to": "T2", "type": "force"},
                 {"from": "T2", "to": "T3", "type": "force"},
                 {"from": "T3", "to": "T1", "type": "data"}]
        with self.assertRaises(ValueError):
            topo_layers(["T1", "T2", "T3"], edges)

    def test_layers_are_sorted_for_determinism(self):
        layers, _ = topo_layers(["T3", "T1", "T2"], [])
        self.assertEqual(layers, [["T1", "T2", "T3"]])


class ValidateEdgesTest(unittest.TestCase):
    def test_valid_edges_keep_reason(self):
        edges = validate_edges([{"from": "T1", "to": "T2", "type": "force",
                                 "reason": "需要接口"}], IDS)
        self.assertEqual(edges[0]["reason"], "需要接口")

    def test_missing_reason_defaults_to_empty(self):
        edges = validate_edges([{"from": "T1", "to": "T2"}], IDS)
        self.assertEqual(edges[0]["type"], "force")
        self.assertEqual(edges[0]["reason"], "")

    def test_unknown_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            validate_edges([{"from": "T1", "to": "T9", "type": "force"}], IDS)

    def test_self_dependency_rejected(self):
        with self.assertRaises(ValueError):
            validate_edges([{"from": "T1", "to": "T1", "type": "force"}], IDS)

    def test_illegal_edge_type_rejected(self):
        with self.assertRaises(ValueError):
            validate_edges([{"from": "T1", "to": "T2", "type": "soft"}], IDS)

    def test_duplicate_edges_deduplicated(self):
        edges = validate_edges([{"from": "T1", "to": "T2", "type": "force"},
                                {"from": "T1", "to": "T2", "type": "force"}], IDS)
        self.assertEqual(len(edges), 1)

    def test_same_pair_different_type_kept(self):
        edges = validate_edges([{"from": "T1", "to": "T2", "type": "force"},
                                {"from": "T1", "to": "T2", "type": "resource"}], IDS)
        self.assertEqual(len(edges), 2)

    def test_empty_edges_allowed(self):
        self.assertEqual(validate_edges([], IDS), [])
        self.assertEqual(validate_edges(None, IDS), [])


if __name__ == "__main__":
    unittest.main()
