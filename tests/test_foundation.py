import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from platform_api.main import Identity, create_app
from platform_api.domain import assess_order, validate_transition
from platform_api.store import MockERP


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.db")
        self.client = TestClient(create_app(self.path, {
            "alpha-secret": Identity(tenant_id="alpha", role="manager"),
            "beta-secret": Identity(tenant_id="beta", role="analyst"),
            "audit-secret": Identity(tenant_id="alpha", role="auditor"),
        }))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def headers(self, token="alpha-secret"):
        return {"Authorization": f"Bearer {token}"}

    def test_authentication_required(self):
        for headers in ({}, self.headers("invalid")):
            self.assertEqual(self.client.get("/v1/purchase-orders", headers=headers).status_code, 401)

    def test_tenant_claim_cannot_be_overridden(self):
        response = self.client.get("/v1/purchase-orders?tenant_id=beta",
                                   headers=self.headers() | {"X-Tenant-ID": "beta"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["tenant_id"] for row in response.json()["items"]}, {"alpha"})

    def test_cross_tenant_object_access(self):
        self.assertEqual(self.client.get("/v1/purchase-orders/PO-B1", headers=self.headers()).status_code, 404)
        items = self.client.get("/v1/purchase-orders", headers=self.headers("beta-secret")).json()["items"]
        self.assertEqual([item["id"] for item in items], ["PO-B1"])

    def test_role_enforced(self):
        self.assertEqual(self.client.get("/v1/purchase-orders", headers=self.headers("audit-secret")).status_code, 403)

    def test_assessment_golden_cases(self):
        response = self.client.get("/v1/procurement/assessment?as_of=2026-11-14", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        rows = {row["order_id"]: row for row in response.json()["items"]}
        self.assertEqual((rows["PO-A1"]["late_days"], rows["PO-A1"]["beyond_grace_days"], rows["PO-A1"]["risk"]), (13, 8, "HIGH"))
        self.assertEqual(rows["PO-A2"]["risk"], "LOW")
        self.assertEqual(rows["PO-A3"]["status"], "INCOMPLETE_EVIDENCE")
        self.assertIsNone(rows["PO-A3"]["risk"])
        self.assertEqual(len(rows["PO-A1"]["evidence"]), 2)

    def test_date_validation(self):
        self.assertEqual(self.client.get("/v1/procurement/assessment?as_of=oops", headers=self.headers()).status_code, 422)

    def test_completed_and_future_orders_are_not_late(self):
        erp = MockERP(self.path, "beta")
        order = erp.orders()[0]
        self.assertEqual(assess_order(order, erp.contract("S1"), date(2026, 11, 14))["late_days"], 0)
        order = MockERP(self.path, "alpha").orders()[0]
        self.assertEqual(assess_order(order, None, date(2026, 10, 1))["late_days"], 0)

    def test_contract_lookup_is_tenant_scoped(self):
        self.assertEqual(MockERP(self.path, "alpha").contract("S1")["grace_days"], 5)
        self.assertEqual(MockERP(self.path, "beta").contract("S1")["grace_days"], 0)

    def test_terminal_states_cannot_restart(self):
        validate_transition("RUNNING", "WAITING_DOCUMENT")
        for state in ("COMPLETED", "FAILED", "CANCELLED", "REJECTED"):
            with self.assertRaises(ValueError):
                validate_transition(state, "RUNNING")

    def test_fifteen_calculation_cases(self):
        cases_path = Path(__file__).resolve().parents[1] / "evals/datasets/calculation-cases.json"
        for case in json.loads(cases_path.read_text()):
            with self.subTest(case=case["name"]):
                order = {"id": "test", "supplier_id": "s", "project_id": "p",
                         "quantity": 100, "delivered_quantity": case["delivered"],
                         "due_date": case["due"], "critical": case["critical"]}
                contract = None if case["grace"] is None else {
                    "id": "c", "grace_days": case["grace"], "location": "fixture", "version": 1}
                result = assess_order(order, contract, date(2026, 11, 14))
                self.assertEqual((result["late_days"], result["beyond_grace_days"], result["risk"]),
                                 (case["late"], case["beyond"], case["risk"]))


if __name__ == "__main__":
    unittest.main()
