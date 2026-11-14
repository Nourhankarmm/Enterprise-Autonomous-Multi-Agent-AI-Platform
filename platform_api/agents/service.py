"""Reviewed plans and bounded LangGraph execution with transactional task snapshots.

This is a synchronous, read-only Phase 2 runner, not the Temporal workflow engine.
"""
import time
import uuid
from datetime import datetime, timezone, date
from typing import TypedDict, Annotated
from operator import add
from sqlalchemy import select, insert, update, delete, func
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from .schemas import Plan, PlanRejected, validate_proposal, plan_hash
from .planner import configured_planner
from .gateway import ToolGateway, TransientToolError
from ..database import operations, agent_tasks, operation_events, audit_events, users

class OperationError(Exception):
    def __init__(self,status,detail):self.status,self.detail=status,detail
class LeaseLost(Exception):pass
class RunCancelled(Exception):pass
class RunExpired(Exception):pass

class ExecutionState(TypedDict):
    completed: Annotated[list[str], add]


def now_iso():return datetime.now(timezone.utc).isoformat()

class OperationService:
    def __init__(self,engine,path,planner=None,gateway_factory=ToolGateway):
        self.engine,self.path,self.planner,self.gateway_factory=engine,path,planner,gateway_factory

    def scope(self,actor,operation_id):
        return (operations.c.tenant_id==actor.tenant_id,operations.c.id==operation_id,
                operations.c.requester_id==actor.subject)

    def event(self,db,actor,operation_id,kind,task_id=None,details=None):
        details=details or {}
        db.execute(insert(operation_events).values(tenant_id=actor.tenant_id,operation_id=operation_id,
            task_id=task_id,event_type=kind,occurred_at=now_iso(),details=details))
        db.execute(insert(audit_events).values(id=str(uuid.uuid4()),tenant_id=actor.tenant_id,
            actor_id=actor.subject,action=kind,resource=operation_id,occurred_at=now_iso(),details=details))

    def _get(self,db,actor,operation_id):
        op=db.execute(select(operations).where(*self.scope(actor,operation_id))).mappings().first()
        if op is None:raise OperationError(404,'Operation not found')
        return dict(op)

    def get(self,actor,operation_id):
        with self.engine.connect() as db:
            op=self._get(db,actor,operation_id)
            tasks=[dict(r) for r in db.execute(select(agent_tasks).where(
                agent_tasks.c.tenant_id==actor.tenant_id,agent_tasks.c.operation_id==operation_id)).mappings()]
        for key in ('lease_token','lease_until','deadline_at'):op.pop(key,None)
        op['tasks']=tasks
        return op

    def list(self,actor):
        with self.engine.connect() as db:
            rows=db.execute(select(operations.c.id,operations.c.goal,operations.c.state,
                operations.c.plan_version,operations.c.planner_provider,operations.c.created_at).where(
                operations.c.tenant_id==actor.tenant_id,operations.c.requester_id==actor.subject)
                .order_by(operations.c.created_at.desc()).limit(50)).mappings()
            return {'items':[dict(row) for row in rows]}

    def events(self,actor,operation_id,after=0,limit=100):
        with self.engine.connect() as db:
            self._get(db,actor,operation_id)
            rows=[dict(r) for r in db.execute(select(operation_events).where(
                operation_events.c.tenant_id==actor.tenant_id,operation_events.c.operation_id==operation_id,
                operation_events.c.id>after).order_by(operation_events.c.id).limit(limit)).mappings()]
        return {'items':rows,'next_cursor':rows[-1]['id'] if rows else after}

    def _plan(self,goal,as_of,version,actor):
        planner=self.planner or configured_planner()
        result=planner.propose(goal,as_of)
        proposal=validate_proposal(result.proposal)
        if proposal.disposition=='out_of_scope':raise OperationError(422,proposal.clarification)
        plan=Plan(version=version,as_of=as_of,proposal=proposal)
        return plan,plan_hash(plan,actor.tenant_id,actor.subject),result

    def _insert_tasks(self,db,actor,operation_id,plan):
        for task in plan.proposal.tasks:
            db.execute(insert(agent_tasks).values(tenant_id=actor.tenant_id,operation_id=operation_id,
                task_id=task.task_id,capability=task.capability,state='PENDING',attempts=0))

    def create(self,actor,body):
        plan,digest,result=self._plan(body.goal,body.as_of,1,actor)
        operation_id=str(uuid.uuid4())
        with self.engine.begin() as db:
            db.execute(insert(operations).values(tenant_id=actor.tenant_id,id=operation_id,
                requester_id=actor.subject,goal=body.goal,state='PLANNED' if plan.proposal.disposition=='ready' else 'WAITING_CLARIFICATION',
                plan_version=1,plan=plan.model_dump(mode='json'),plan_hash=digest,replans=0,
                planner_provider=result.provider,planner_model=result.model,planner_usage=result.usage,
                created_at=now_iso(),updated_at=now_iso()))
            self._insert_tasks(db,actor,operation_id,plan)
            self.event(db,actor,operation_id,'plan.created',details={'version':1,'provider':result.provider,'plan_hash':digest})
        return self.get(actor,operation_id)

    def revise(self,actor,operation_id,body):
        with self.engine.connect() as db:old=self._get(db,actor,operation_id)
        if old['state'] not in {'PLANNED','WAITING_CLARIFICATION'} or old['replans']>=2:
            raise OperationError(409,'Plan cannot be revised or revision budget exhausted')
        if old['plan_version']!=body.version or old['plan_hash']!=body.plan_hash:
            raise OperationError(409,'Plan version/hash changed; review the latest plan')
        plan,digest,result=self._plan(body.goal,body.as_of,old['plan_version']+1,actor)
        with self.engine.begin() as db:
            changed=db.execute(update(operations).where(*self.scope(actor,operation_id),
                operations.c.state==old['state'],operations.c.plan_hash==old['plan_hash'],
                operations.c.replans==old['replans']).values(goal=body.goal,
                state='PLANNED' if plan.proposal.disposition=='ready' else 'WAITING_CLARIFICATION',
                plan_version=plan.version,plan=plan.model_dump(mode='json'),plan_hash=digest,
                replans=old['replans']+1,planner_provider=result.provider,planner_model=result.model,
                planner_usage={key:old['planner_usage'].get(key,0)+result.usage.get(key,0) for key in ('input_tokens','output_tokens')},updated_at=now_iso()))
            if changed.rowcount!=1:raise OperationError(409,'Operation changed during planning')
            db.execute(delete(agent_tasks).where(agent_tasks.c.tenant_id==actor.tenant_id,agent_tasks.c.operation_id==operation_id))
            self._insert_tasks(db,actor,operation_id,plan)
            self.event(db,actor,operation_id,'plan.revised',details={'version':plan.version,'plan_hash':digest})
        return self.get(actor,operation_id)

    def cancel(self,actor,operation_id):
        with self.engine.begin() as db:
            op=self._get(db,actor,operation_id)
            if op['state'] not in {'PLANNED','WAITING_CLARIFICATION','WAITING_DOCUMENT','RUNNING','CANCEL_REQUESTED'}:
                raise OperationError(409,'Terminal operation cannot be cancelled')
            target='CANCEL_REQUESTED' if op['state'] in {'RUNNING','CANCEL_REQUESTED'} and (op['lease_until'] or 0)>time.time() else 'CANCELLED'
            changed=db.execute(update(operations).where(*self.scope(actor,operation_id),operations.c.state==op['state'])
                .values(state=target,updated_at=now_iso()))
            if changed.rowcount!=1:raise OperationError(409,'Operation changed')
            if target=='CANCELLED':
                db.execute(update(agent_tasks).where(agent_tasks.c.tenant_id==actor.tenant_id,
                    agent_tasks.c.operation_id==operation_id,agent_tasks.c.state.in_(['PENDING','RUNNING']))
                    .values(state='CANCELLED'))
            self.event(db,actor,operation_id,'operation.cancel_requested' if target=='CANCEL_REQUESTED' else 'operation.cancelled')
        return self.get(actor,operation_id)

    def execute(self,actor,operation_id,confirmation=None,resume=False):
        lease=str(uuid.uuid4());clock=time.time()
        with self.engine.begin() as db:
            op=self._get(db,actor,operation_id)
            plan=Plan.model_validate(op['plan']);validate_proposal(plan.proposal)
            if plan_hash(plan,actor.tenant_id,actor.subject)!=op['plan_hash']:
                raise OperationError(409,'Stored plan integrity check failed')
            if resume:
                if op['state']!='RUNNING' or (op['lease_until'] or 0)>clock:
                    raise OperationError(409,'Only interrupted read-only execution with an expired lease can resume')
            elif op['state']!='PLANNED' or not confirmation or confirmation.version!=op['plan_version'] or confirmation.plan_hash!=op['plan_hash']:
                raise OperationError(409,'Confirm the exact current plan version and hash')
            deadline=op['deadline_at'] if resume else clock+plan.runtime_seconds
            changed=db.execute(update(operations).where(*self.scope(actor,operation_id),
                operations.c.state==op['state'],operations.c.plan_hash==op['plan_hash'],
                operations.c.lease_token==op['lease_token'] if op['lease_token'] else operations.c.lease_token.is_(None))
                .values(state='RUNNING',lease_token=lease,lease_until=clock+10,deadline_at=deadline,updated_at=now_iso()))
            if changed.rowcount!=1:raise OperationError(409,'Operation already claimed or changed')
            self.event(db,actor,operation_id,'operation.resumed' if resume else 'plan.confirmed',details={'version':plan.version,'plan_hash':op['plan_hash']})
        gateway=self.gateway_factory(self.path,actor.tenant_id)
        graph=StateGraph(ExecutionState)
        for task in plan.proposal.tasks:
            def run_task(state,current=task):
                self._run_task(actor,operation_id,lease,current,plan,gateway)
                return {'completed':[current.task_id]}
            graph.add_node(task.task_id,run_task)
        for task in plan.proposal.tasks:
            graph.add_edge(task.depends_on or START,task.task_id)
        report=next(t.task_id for t in plan.proposal.tasks if t.capability=='report.compose')
        graph.add_edge(report,END)
        try:
            with tracing_context(enabled=False):
                graph.compile().invoke({'completed':[]},{'recursion_limit':32,'callbacks':[]})
            with self.engine.begin() as db:
                self._guard(db,actor,operation_id,lease)
                result=db.execute(select(agent_tasks.c.output).where(agent_tasks.c.tenant_id==actor.tenant_id,
                    agent_tasks.c.operation_id==operation_id,agent_tasks.c.task_id==report)).scalar_one()
                final='WAITING_DOCUMENT' if result['unresolved_order_ids'] else 'COMPLETED'
                db.execute(update(operations).where(*self.scope(actor,operation_id),operations.c.lease_token==lease)
                    .values(state=final,result=result,lease_token=None,lease_until=None,updated_at=now_iso()))
                self.event(db,actor,operation_id,'operation.finished',details={'state':final})
        except RunCancelled:self._finish(actor,operation_id,lease,'CANCELLED',None)
        except RunExpired:self._finish(actor,operation_id,lease,'FAILED','TIME_BUDGET_EXCEEDED')
        except LeaseLost:pass # Another runner/cancellation now owns the state; never overwrite it.
        except OperationError:self._finish(actor,operation_id,lease,'FAILED','AUTHORIZATION_REVOKED')
        except PlanRejected:self._finish(actor,operation_id,lease,'FAILED','PLAN_OR_VERIFICATION_REJECTED')
        except Exception:self._finish(actor,operation_id,lease,'FAILED','TASK_EXECUTION_FAILED')
        return self.get(actor,operation_id)

    def _guard(self,db,actor,operation_id,lease):
        op=self._get(db,actor,operation_id)
        if op['lease_token']!=lease:raise LeaseLost()
        if op['state']=='CANCEL_REQUESTED':raise RunCancelled()
        if op['state']!='RUNNING':raise LeaseLost()
        if time.time()>op['deadline_at']:raise RunExpired()
        if time.time()>op['lease_until']:raise LeaseLost()
        # Live account/role revocation applies between every tool invocation.
        user=db.execute(select(users).where(users.c.id==actor.subject)).mappings().first()
        if actor.subject!='development-token' and (not user or not user['active'] or
                user['tenant_id']!=actor.tenant_id or user['role'] not in {'manager','analyst'}):
            raise OperationError(403,'Execution authorization revoked')
        changed=db.execute(update(operations).where(*self.scope(actor,operation_id),operations.c.lease_token==lease,
            operations.c.state=='RUNNING').values(lease_until=time.time()+10))
        if changed.rowcount!=1:raise LeaseLost()

    def _run_task(self,actor,operation_id,lease,task,plan,gateway):
        conditions=(agent_tasks.c.tenant_id==actor.tenant_id,agent_tasks.c.operation_id==operation_id)
        while True:
            with self.engine.begin() as db:
                self._guard(db,actor,operation_id,lease)
                rows={r['task_id']:dict(r) for r in db.execute(select(agent_tasks).where(*conditions)).mappings()}
                row=rows[task.task_id]
                if row['state']=='SUCCEEDED':return
                if any(rows[dep]['state']!='SUCCEEDED' for dep in task.depends_on):raise PlanRejected('Dependency unfinished')
                if row['attempts']>=task.max_attempts or sum(r['attempts'] for r in rows.values())>=plan.max_total_attempts:
                    raise PlanRejected('Attempt budget exhausted')
                attempt=row['attempts']+1
                db.execute(update(agent_tasks).where(*conditions,agent_tasks.c.task_id==task.task_id)
                    .values(state='RUNNING',attempts=attempt,error_code=None))
                self.event(db,actor,operation_id,'task.started',task.task_id,{'attempt':attempt,'capability':task.capability})
                outputs={r['capability']:r['output'] for r in rows.values() if r['state']=='SUCCEEDED'}
            try:
                result=gateway.execute(task,outputs,plan.as_of)
            except Exception as error:
                retry=isinstance(error,TransientToolError) and attempt<task.max_attempts
                with self.engine.begin() as db:
                    self._guard(db,actor,operation_id,lease)
                    db.execute(update(agent_tasks).where(*conditions,agent_tasks.c.task_id==task.task_id)
                        .values(state='PENDING' if retry else 'FAILED',error_code='TRANSIENT_READ_ERROR' if retry else 'TOOL_FAILED'))
                    self.event(db,actor,operation_id,'task.retry_scheduled' if retry else 'task.failed',task.task_id)
                if retry:
                    time.sleep(.05*attempt)
                    continue
                raise
            with self.engine.begin() as db:
                self._guard(db,actor,operation_id,lease)
                db.execute(update(agent_tasks).where(*conditions,agent_tasks.c.task_id==task.task_id)
                    .values(state='SUCCEEDED',output=result,error_code=None))
                self.event(db,actor,operation_id,'task.succeeded',task.task_id,{'attempt':attempt})
            return

    def _finish(self,actor,operation_id,lease,state,error):
        with self.engine.begin() as db:
            changed=db.execute(update(operations).where(*self.scope(actor,operation_id),operations.c.lease_token==lease)
                .values(state=state,error_code=error,lease_token=None,lease_until=None,updated_at=now_iso()))
            if changed.rowcount:
                for previous,target in [('RUNNING','CANCELLED' if state=='CANCELLED' else 'FAILED'),
                                        ('PENDING','CANCELLED' if state=='CANCELLED' else 'BLOCKED')]:
                    db.execute(update(agent_tasks).where(agent_tasks.c.tenant_id==actor.tenant_id,
                        agent_tasks.c.operation_id==operation_id,agent_tasks.c.state==previous)
                        .values(state=target,error_code=error))
                self.event(db,actor,operation_id,'operation.finished',details={'state':state,'error_code':error})
