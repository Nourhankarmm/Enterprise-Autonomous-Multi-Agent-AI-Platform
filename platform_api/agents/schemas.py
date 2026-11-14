"""Typed, bounded planner output. Model proposals never confer privileges."""
import hashlib
import json
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

TOOLS = ('purchase_orders.read','contracts.read','procurement.assess','findings.verify','report.compose')
MAX_ATTEMPTS = 12
RUNTIME_SECONDS = 30
DEMO_GOAL = 'حلل تأخير طلبات الشراء المفتوحة'

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class OrderFilter(StrictModel):
    supplier_id: str | None = Field(max_length=80)
    project_id: str | None = Field(max_length=80)
    order_ids: list[str] = Field(max_length=20)
    outstanding_only: bool

class Task(StrictModel):
    task_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    capability: Literal['purchase_orders.read','contracts.read','procurement.assess','findings.verify','report.compose']
    depends_on: list[str] = Field(max_length=12)
    filters: OrderFilter | None
    max_attempts: int = Field(ge=1,le=3)

class Proposal(StrictModel):
    disposition: Literal['ready','needs_clarification','out_of_scope']
    summary: str = Field(min_length=1,max_length=1000)
    clarification: str | None = Field(max_length=1000)
    tasks: list[Task] = Field(max_length=12)

class Plan(StrictModel):
    version: int = Field(ge=1)
    as_of: date
    proposal: Proposal
    max_total_attempts: Literal[12] = MAX_ATTEMPTS
    runtime_seconds: Literal[30] = RUNTIME_SECONDS
    requires_review: Literal[True] = True

class GoalInput(StrictModel):
    goal: str = Field(min_length=5,max_length=2000)
    as_of: date

class PlanConfirmation(StrictModel):
    version: int = Field(ge=1)
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

class Revision(GoalInput):
    version: int = Field(ge=1)
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

class PlanRejected(ValueError):
    pass


def validate_proposal(value):
    proposal = Proposal.model_validate(value)
    if proposal.disposition != 'ready':
        if proposal.tasks or not proposal.clarification:
            raise PlanRejected('Blocked plans require clarification and zero executable tasks')
        return proposal
    if proposal.clarification is not None:
        raise PlanRejected('Ready plans must not contain an unresolved clarification')
    by_id = {t.task_id:t for t in proposal.tasks}
    if len(by_id) != len(proposal.tasks):
        raise PlanRejected('Duplicate task ID')
    capabilities = [t.capability for t in proposal.tasks]
    if sorted(capabilities) != sorted(TOOLS):
        raise PlanRejected('One read, contract lookup, assessment, verification and report task required')
    if sum(t.max_attempts for t in proposal.tasks) > MAX_ATTEMPTS:
        raise PlanRejected('Attempt budget exceeded')
    done = set()
    while len(done) < len(by_id):
        ready = [t for t in proposal.tasks if t.task_id not in done and set(t.depends_on) <= done]
        if not ready:
            raise PlanRejected('Cyclic or unknown dependency')
        for task in ready:
            if len(set(task.depends_on)) != len(task.depends_on):
                raise PlanRejected('Duplicate dependency')
            done.add(task.task_id)
    required = {
        'purchase_orders.read':set(), 'contracts.read':{'purchase_orders.read'},
        'procurement.assess':{'purchase_orders.read','contracts.read'},
        'findings.verify':{'purchase_orders.read','contracts.read','procurement.assess'},
        'report.compose':{'procurement.assess','findings.verify'},
    }
    for task in proposal.tasks:
        ancestors = set()
        def collect(task_id):
            for dep in by_id[task_id].depends_on:
                if dep not in ancestors:
                    ancestors.add(dep);collect(dep)
        collect(task.task_id)
        inherited = {by_id[dep].capability for dep in ancestors}
        if not required[task.capability] <= inherited:
            raise PlanRejected('Required evidence/verification dependency missing')
        if task.capability == 'purchase_orders.read':
            if task.depends_on or task.filters is None:
                raise PlanRejected('Read task needs explicit filters and no dependencies')
        elif task.filters is not None:
            raise PlanRejected('Only the order-read capability accepts filters')
    return proposal


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def plan_hash(plan, tenant_id, requester_id):
    return canonical_hash({'plan':plan.model_dump(mode='json'),'tenant_id':tenant_id,'requester_id':requester_id})
