import unittest

from stormshift.agents import negotiate, validate_plan
from stormshift.scenario import get_scenario


class AgentTests(unittest.TestCase):
    def test_scenario_returns_copy(self):
        first = get_scenario()
        first["runways"][0]["status"] = "closed"
        self.assertEqual(get_scenario()["runways"][0]["status"], "open")

    def test_balanced_plan_is_valid(self):
        scenario = get_scenario()
        result = negotiate(scenario, "balanced")
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(result["plan"]["landing_runway"], "07L")
        self.assertEqual(result["plan"]["gate_assignments"]["SK218"], "A4")
        self.assertEqual(result["outcome"]["remaining_conflicts"], 0)

    def test_agents_have_distinct_roles(self):
        result = negotiate(get_scenario())
        self.assertEqual(len(result["proposals"]), 3)
        self.assertEqual({p["agent"] for p in result["proposals"]}, {"runway", "gate", "airline"})
        self.assertEqual(len({p["recommendation"] for p in result["proposals"]}), 3)

    def test_preferences_change_plan(self):
        connections = negotiate(get_scenario(), "connections")
        capacity = negotiate(get_scenario(), "capacity")
        self.assertEqual(connections["plan"]["hold_minutes"]["LV412"], 4)
        self.assertEqual(capacity["plan"]["gate_assignments"]["LV412"], "R1")

    def test_validator_rejects_closed_runway(self):
        scenario = get_scenario()
        plan = negotiate(scenario)["plan"]
        plan["landing_runway"] = "07R"
        self.assertIn("计划使用了已关闭跑道", validate_plan(scenario, plan))


if __name__ == "__main__":
    unittest.main()
