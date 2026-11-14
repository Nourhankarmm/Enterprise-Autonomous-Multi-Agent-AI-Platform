"""Verify fixture/spec consistency, not future workflow implementation."""
import json
import unittest
from datetime import date
from pathlib import Path

from platform_api.domain import TRANSITIONS, assess_order, validate_transition

DATA = Path(__file__).resolve().parents[1] / "evals/datasets"


class PhaseZeroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((DATA / "procurement.json").read_text())
        cls.catalog = json.loads((DATA / "business-scenarios.json").read_text())

    def test_fixture_integrity_and_tenant_overlap(self):
        orders = self.fixture["orders"]
        contracts = self.fixture["contracts"]
        self.assertEqual(len({(r["tenant_id"], r["id"]) for r in orders}), len(orders))
        self.assertEqual(len({(r["tenant_id"], r["supplier_id"]) for r in contracts}), len(contracts))
        self.assertEqual({r["tenant_id"] for r in orders}, {"alpha", "beta"})
        for row in orders:
            date.fromisoformat(row["due_date"])
            self.assertGreaterEqual(row["quantity"], 0)
            self.assertGreaterEqual(row["delivered_quantity"], 0)
        for row in contracts:
            self.assertGreaterEqual(row["grace_days"], 0)
            self.assertGreaterEqual(row["version"], 1)
        # Deliberately colliding supplier IDs ensure global lookup would be unsafe.
        self.assertEqual(sum(r["supplier_id"] == "S1" for r in contracts), 2)

    def test_catalog_is_complete_and_references_existing_fixtures(self):
        cases = self.catalog["cases"]
        self.assertGreaterEqual(len(cases), 15)
        self.assertLessEqual(len(cases), 30)
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        self.assertTrue({"approval", "tenancy", "acl", "injection", "recovery",
                         "idempotency", "reconciliation", "durability", "cancellation",
                         "conflict", "missing_evidence"} <= {c["category"] for c in cases})
        orders = {r["id"] for r in self.fixture["orders"]}
        contracts = {r["id"] for r in self.fixture["contracts"]}
        terminal = {"COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED", "REJECTED"}
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["goal"] and case["input"] and case["expected"])
                for key in ("order_ref", "requested_order_ref"):
                    if case["input"].get(key):
                        self.assertIn(case["input"][key], orders)
                for ref in case["input"].get("order_refs", []):
                    self.assertIn(ref, orders)
                for key in ("contract_ref", "requested_contract_ref", "document_ref"):
                    if case["input"].get(key):
                        self.assertIn(case["input"][key], contracts)
                expected = case["expected"]
                if "operation_state" in expected:
                    self.assertIn(expected["operation_state"], set(TRANSITIONS) | terminal)
                for a, b in zip(expected.get("state_path", []), expected.get("state_path", [])[1:]):
                    validate_transition(a, b)

    def test_existing_calculator_matches_first_three_business_oracles(self):
        for case in self.catalog["cases"][:3]:
            with self.subTest(case=case["id"]):
                order = next(r for r in self.fixture["orders"] if r["id"] == case["input"]["order_ref"])
                contract = next((r for r in self.fixture["contracts"]
                                 if r["id"] == case["input"]["contract_ref"]), None)
                result = assess_order(order, contract, date.fromisoformat(self.catalog["as_of"]))
                for key in ("late_days", "remaining_quantity", "beyond_grace_days", "risk"):
                    if key in case["expected"]:
                        self.assertEqual(result[key], case["expected"][key])

    def test_wait_expiry_can_terminate_without_fabricating_results(self):
        for waiting in ("WAITING_DOCUMENT", "WAITING_CLARIFICATION"):
            validate_transition(waiting, "FAILED")
            validate_transition(waiting, "PARTIALLY_COMPLETED")


if __name__ == "__main__":
    unittest.main()
