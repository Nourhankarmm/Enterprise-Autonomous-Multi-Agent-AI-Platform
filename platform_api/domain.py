"""Deterministic procurement rules. No model-generated arithmetic."""
from datetime import date


def assess_order(order: dict, contract: dict | None, as_of: date) -> dict:
    remaining = max(0, order["quantity"] - order["delivered_quantity"])
    late_days = max(0, (as_of - date.fromisoformat(order["due_date"])).days) if remaining else 0
    result = {
        "order_id": order["id"], "supplier_id": order["supplier_id"],
        "project_id": order["project_id"], "remaining_quantity": remaining,
        "late_days": late_days, "as_of": as_of.isoformat(), "rule_version": "1",
        "evidence": [{"source_type": "purchase_order", "source_id": order["id"]}],
    }
    if contract is None:
        return result | {"status": "INCOMPLETE_EVIDENCE", "risk": None,
                         "beyond_grace_days": None}
    beyond = max(0, late_days - contract["grace_days"])
    risk = "HIGH" if beyond and order["critical"] else "MEDIUM" if beyond else "LOW"
    result["evidence"].append({"source_type": "contract", "source_id": contract["id"],
                               "location": contract["location"], "version": contract["version"]})
    return result | {"status": "ASSESSED", "risk": risk, "beyond_grace_days": beyond}


TRANSITIONS = {
    "DRAFT": {"PLANNED", "CANCELLED"},
    "PLANNED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"WAITING_APPROVAL", "WAITING_DOCUMENT", "WAITING_CLARIFICATION",
                "RECONCILING", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCEL_REQUESTED"},
    "WAITING_APPROVAL": {"RUNNING", "CANCELLED", "REJECTED"},
    "WAITING_DOCUMENT": {"RUNNING", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"},
    "WAITING_CLARIFICATION": {"RUNNING", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"},
    "RECONCILING": {"RUNNING", "FAILED", "CANCEL_REQUESTED"},
    "CANCEL_REQUESTED": {"CANCELLED", "RECONCILING"},
}


def validate_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid transition: {current} -> {target}")
