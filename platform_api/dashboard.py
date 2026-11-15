"""Read-only, owner/assigned-case scoped operations, traces and measured usage."""
from collections import Counter
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from fastapi import Depends,Query
from sqlalchemy import select,exists
from .database import operations,workflow_runs,workflow_actions,audit_events,operation_events,planner_calls
from .workflows.schemas import WorkflowError

def elapsed(start,end):
    if not start or not end:return None
    try:return max(0,round((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()*1000))
    except (ValueError,TypeError):return None

class DashboardService:
    def __init__(self,engine,ops,workflows):self.engine,self.ops,self.workflows=engine,ops,workflows

    def actor(self,actor):return self.workflows.actor(actor.subject,actor.tenant_id,{'manager','analyst','approver'})

    def allowed_operation(self,actor,operation_id):
        try:return self.ops.get(actor,operation_id)
        except Exception as error:
            if getattr(error,'status',None) in {403,404,409}:return None
            raise

    def overview(self,actor):
        actor=self.actor(actor);analyses=[];workflows=[];limited=False
        if actor.role!='approver':
            with self.engine.connect() as db:
                candidates=db.execute(select(operations.c.id).where(operations.c.tenant_id==actor.tenant_id,
                    operations.c.requester_id==actor.subject).order_by(operations.c.created_at.desc(),operations.c.id).limit(101)).scalars().all()
            limited=len(candidates)>100
            for operation_id in candidates[:100]:
                op=self.allowed_operation(actor,operation_id)
                if not op:continue
                analyses.append({k:op[k] for k in ('id','goal','state','created_at','updated_at','plan_version','planner_provider','planner_model','error_code')})
        with self.engine.connect() as db:
            query=select(workflow_runs.c.id).where(workflow_runs.c.tenant_id==actor.tenant_id)
            if actor.role!='approver':query=query.where(workflow_runs.c.requester_id==actor.subject)
            else:
                # Assignment filtering occurs in SQL before the bounded window.
                query=query.where(exists(select(workflow_actions.c.id).where(
                    workflow_actions.c.tenant_id==workflow_runs.c.tenant_id,
                    workflow_actions.c.workflow_id==workflow_runs.c.id,
                    workflow_actions.c.revision==workflow_runs.c.revision,
                    workflow_actions.c.snapshot_id==workflow_runs.c.snapshot_id,
                    workflow_actions.c.payload['approver_id'].as_string()==actor.subject)))
            query=query.order_by(workflow_runs.c.created_at.desc(),workflow_runs.c.id).limit(101)
            ids=db.execute(query).scalars().all()
        limited=limited or len(ids)>100
        for run_id in ids[:100]:
            try:row=self.workflows.get(actor,run_id)
            except Exception as error:
                if getattr(error,'status',None) in {403,404,409}:continue
                raise
            action=row['action'];decisions=[d for d in row['approvals'] if action and d['action_id']==action['id']]
            latest=max(decisions,key=lambda d:d['decided_at']) if decisions else None
            workflows.append({k:row[k] for k in ('id','operation_id','state','created_at','updated_at','error_code')}|{
                'proposal_revision':action['revision'] if action else None,
                'decision':latest['decision'] if latest else None,
                'approval_expires_at':latest['expires_at'] if latest else None,
                'receipt_available':bool(row['execution'] and row['execution']['receipt'])})
        workflows.sort(key=lambda r:(r['created_at'],r['id']),reverse=True)
        counts=Counter(r['state'] for r in workflows)
        return {'analyses':analyses,'workflows':workflows,'workflow_states':dict(counts),
            'analysis_states':dict(Counter(r['state'] for r in analyses)),
            'scope':'own_analyses_and_assigned_workflows','window_limit':100,'limited':limited,
            'generated_at':datetime.now(timezone.utc).isoformat()}

    def trace(self,actor,operation_id):
        actor=self.actor(actor)
        if actor.role=='approver':raise WorkflowError(403,'Procurement read permission required')
        op=self.ops.get(actor,operation_id)
        with self.engine.connect() as db:
            events=[dict(r) for r in db.execute(select(operation_events).where(operation_events.c.tenant_id==actor.tenant_id,
                operation_events.c.operation_id==operation_id).order_by(operation_events.c.id).limit(1001)).mappings()]
        attempts={};timeline=[]
        for event in events[:1000]:
            task_id=event['task_id'];details=event['details'];kind=event['event_type']
            timeline.append({k:event[k] for k in ('id','task_id','event_type','occurred_at')}|{
                'attempt':details.get('attempt'),'version':details.get('version'),'state':details.get('state')})
            if not task_id:continue
            if kind=='task.started':attempts.setdefault(task_id,[]).append({'attempt':details.get('attempt'),
                'started_at':event['occurred_at'],'ended_at':None,'duration_ms':None,'outcome':'RUNNING'})
            elif kind in {'task.succeeded','task.failed','task.retry_scheduled'} and attempts.get(task_id):
                current=attempts[task_id][-1];current.update(ended_at=event['occurred_at'],
                    duration_ms=elapsed(current['started_at'],event['occurred_at']),outcome=kind.removeprefix('task.'))
        tasks=[]
        for task in op['plan']['proposal']['tasks']:
            saved=next(t for t in op['tasks'] if t['task_id']==task['task_id']);output=saved['output'] or {}
            summary={key:len(output[key]) for key in ('items','findings','unresolved_order_ids') if isinstance(output.get(key),(list,dict))}
            tasks.append({k:task[k] for k in ('task_id','capability','depends_on','max_attempts')}|{
                'state':saved['state'],'attempts':saved['attempts'],'error_code':saved['error_code'],
                'output_counts':summary,'timings':attempts.get(task['task_id'],[])})
        return {'operation_id':operation_id,'tasks':tasks,'timeline':timeline,'limited':len(events)>1000,
            'timing_source':'recorded_wall_clock_events','private_reasoning_included':False}

    def workflow_timeline(self,actor,run_id):
        actor=self.actor(actor);row=self.workflows.get(actor,run_id)
        with self.engine.connect() as db:
            resources=[run_id]
            if actor.subject==row['requester_id']:
                resources+=list(db.execute(select(workflow_actions.c.id).where(workflow_actions.c.tenant_id==actor.tenant_id,
                    workflow_actions.c.workflow_id==run_id)).scalars())
            elif row['action']:resources.append(row['action']['id'])
            query=select(audit_events).where(audit_events.c.tenant_id==actor.tenant_id,audit_events.c.resource.in_(resources))
            if actor.subject!=row['requester_id']:query=query.where(audit_events.c.occurred_at>=row['action']['created_at'])
            events=db.execute(query.order_by(audit_events.c.occurred_at,audit_events.c.id).limit(1001)).mappings().all()
        # Only an explicit metadata allowlist is exposed, never arbitrary audit JSON or actor identities.
        visible=[{'id':e['id'],'event_type':e['action'],'occurred_at':e['occurred_at'],
            'details':{k:e['details'][k] for k in ('state','revision','hash','plan_hash','reader_count') if k in e['details']}} for e in events[:1000]]
        return {'workflow_id':run_id,'items':visible,'limited':len(events)>1000,'audit_is_immutable':False}

    def usage(self,actor,operation_id=None):
        actor=self.actor(actor)
        if actor.role=='approver':raise WorkflowError(403,'Own planner usage permission required')
        if operation_id:self.ops.get(actor,operation_id)
        since=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
        with self.engine.connect() as db:
            query=select(planner_calls).where(planner_calls.c.tenant_id==actor.tenant_id,
                planner_calls.c.requester_id==actor.subject,planner_calls.c.occurred_at>=since)
            if operation_id:query=query.where(planner_calls.c.operation_id==operation_id)
            candidates=db.execute(query.order_by(planner_calls.c.occurred_at.desc(),planner_calls.c.id).limit(1001)).mappings().all()
        access={};rows=[]
        for call in candidates[:1000]:
            ref=call['operation_id']
            if ref and ref not in access:access[ref]=self.allowed_operation(actor,ref) is not None
            if ref and not access[ref]:continue
            rows.append(dict(call))
        cost=sum((Decimal(r['estimated_cost_usd']) for r in rows if r['estimated_cost_usd'] is not None),Decimal(0))
        unknown=sum(not r['usage_known'] for r in rows);unpriced=sum(r['estimated_cost_usd'] is None for r in rows)
        return {'items':rows,'summary':{'calls':len(rows),'successful_calls':sum(r['status']=='SUCCESS' for r in rows),
            'failed_or_rejected_calls':sum(r['status']!='SUCCESS' for r in rows),
            'input_tokens':sum(r['usage'].get('input_tokens',0) for r in rows),
            'output_tokens':sum(r['usage'].get('output_tokens',0) for r in rows),
            'unknown_usage_calls':unknown,'unpriced_calls':unpriced,'known_estimate_usd':format(cost,'f'),
            'total_estimate_usd':format(cost,'f') if rows and not unpriced else None},
            'limited':len(candidates)>1000,'window_days':30,'window_limit':1000,'currency':'USD',
            'is_invoice':False,'legacy_calls_included':False}

def register_dashboard_routes(app,engine,identity):
    service=DashboardService(engine,app.state.operations,app.state.workflows);app.state.dashboard=service
    @app.get('/v1/dashboard')
    def dashboard(actor=Depends(identity)):return service.overview(actor)
    @app.get('/v1/operations/{operation_id}/trace')
    def trace(operation_id:str,actor=Depends(identity)):return service.trace(actor,operation_id)
    @app.get('/v1/workflows/{run_id}/timeline')
    def timeline(run_id:str,actor=Depends(identity)):return service.workflow_timeline(actor,run_id)
    @app.get('/v1/telemetry/planner')
    def usage(operation_id:str|None=Query(default=None,max_length=100),actor=Depends(identity)):
        return service.usage(actor,operation_id)
