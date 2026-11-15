"""Transactional proposals/commands and guarded dispatch; Temporal owns lifecycle."""
import time,uuid
from types import SimpleNamespace
from sqlalchemy import select,insert,update,func
from sqlalchemy.exc import IntegrityError
from ..database import (workflow_runs as runs,workflow_actions as actions,workflow_commands as commands,
    approvals,action_executions as ledger,operations,agent_tasks,users,audit_events)
from ..agents.service import OperationService,now_iso
from ..agents.schemas import Plan,PlanConfirmation,canonical_hash
from ..agents.gateway import ToolGateway
from ..documents.service import DocumentService
from .schemas import WorkflowError,POLICY,TERMINAL
from .adapter import SandboxMailAdapter,PreSubmissionFailure,UnknownOutcome

class WorkflowService:
    def __init__(self,engine,path,adapter=None):
        self.engine,self.path=engine,path
        self.ops=OperationService(engine,path)
        self.docs=DocumentService(engine)
        self.adapter=adapter or SandboxMailAdapter(engine)

    def actor(self,subject,tenant,role=None):
        with self.engine.connect() as db:
            row=db.execute(select(users).where(users.c.id==subject,users.c.tenant_id==tenant,
                users.c.active.is_(True))).mappings().first()
        if not row or (role and row['role'] not in role):raise WorkflowError(403,'Current permission required')
        return SimpleNamespace(subject=subject,tenant_id=tenant,role=row['role'])

    def _run(self,db,tenant,run_id,lock=False):
        query=select(runs).where(runs.c.tenant_id==tenant,runs.c.id==run_id)
        if lock:query=query.with_for_update()
        row=db.execute(query).mappings().first()
        if not row:raise WorkflowError(404,'Workflow not found')
        return dict(row)

    def _owner(self,actor,row):
        if row['requester_id']!=actor.subject:raise WorkflowError(404,'Workflow not found')
        self.actor(actor.subject,actor.tenant_id,{'manager','analyst'})

    def _audit(self,db,tenant,subject,kind,resource,details=None):
        db.execute(insert(audit_events).values(id=str(uuid.uuid4()),tenant_id=tenant,actor_id=subject,
            action=kind,resource=resource,occurred_at=now_iso(),details=details or {}))

    def _action(self,db,tenant,run_id):
        row=db.execute(select(actions).where(actions.c.tenant_id==tenant,actions.c.workflow_id==run_id)
            .order_by(actions.c.revision.desc()).limit(1)).mappings().first()
        return dict(row) if row else None

    def reserve(self,actor,operation_id,body):
        self.actor(actor.subject,actor.tenant_id,{'manager','analyst'})
        op=self.ops.get(actor,operation_id)
        if op['plan_version']!=body.version or op['plan_hash']!=body.plan_hash:
            raise WorkflowError(409,'Review the exact current plan')
        with self.engine.connect() as db:
            existing=db.execute(select(runs).where(runs.c.tenant_id==actor.tenant_id,runs.c.operation_id==operation_id)).mappings().first()
            if existing:
                self._owner(actor,existing)
                return existing['id']
        if op['state'] not in {'PLANNED','COMPLETED','WAITING_DOCUMENT','WAITING_CLARIFICATION'}:
            raise WorkflowError(409,'Operation cannot start durable execution')
        run_id=str(uuid.uuid5(uuid.NAMESPACE_URL,actor.tenant_id+'/'+operation_id))
        try:
            with self.engine.begin() as db:
                current=db.execute(select(operations).where(operations.c.tenant_id==actor.tenant_id,
                    operations.c.id==operation_id).with_for_update()).mappings().one()
                if current['plan_hash']!=body.plan_hash or current['state'] not in {'PLANNED','COMPLETED','WAITING_DOCUMENT','WAITING_CLARIFICATION'}:
                    raise WorkflowError(409,'Operation changed during durable start')
                db.execute(insert(runs).values(tenant_id=actor.tenant_id,id=run_id,operation_id=operation_id,
                    requester_id=actor.subject,state='START_PENDING',revision=0,created_at=now_iso(),updated_at=now_iso()))
                self._audit(db,actor.tenant_id,actor.subject,'workflow.requested',run_id,{'plan_hash':body.plan_hash})
        except IntegrityError:
            with self.engine.connect() as db:self._owner(actor,self._run(db,actor.tenant_id,run_id))
        return run_id

    def get(self,actor,run_id):
        actor=self.actor(actor.subject,actor.tenant_id,{'manager','analyst','approver'})
        with self.engine.connect() as db:
            row=self._run(db,actor.tenant_id,run_id);action=self._action(db,actor.tenant_id,run_id)
            if row['requester_id']!=actor.subject and (actor.role!='approver' or not action or action['payload'].get('approver_id')!=actor.subject):
                raise WorkflowError(404,'Workflow not found')
            execution=db.execute(select(ledger).where(ledger.c.tenant_id==actor.tenant_id,
                ledger.c.workflow_id==run_id)).mappings().first()
            decisions=[dict(r) for r in db.execute(select(approvals).join(actions,
                (approvals.c.tenant_id==actions.c.tenant_id)&(approvals.c.action_id==actions.c.id)).where(
                    actions.c.tenant_id==actor.tenant_id,actions.c.workflow_id==run_id)).mappings()]
        self.docs.assert_snapshot_access(actor,action['payload'] if action else None)
        if row['requester_id']==actor.subject:self.docs.assert_snapshot_access(actor,row['result'])
        if row['requester_id']!=actor.subject:
            row.pop('result',None);row.pop('snapshot_id',None)
        row.update(action=action,approvals=decisions,execution=dict(execution) if execution else None)
        return row

    def list(self,actor):
        self.actor(actor.subject,actor.tenant_id,{'manager','analyst','approver'})
        with self.engine.connect() as db:
            query=select(runs.c.id,runs.c.state,runs.c.created_at).where(runs.c.tenant_id==actor.tenant_id)
            if actor.role!='approver':query=query.where(runs.c.requester_id==actor.subject)
            ids=db.execute(query.order_by(runs.c.created_at.desc()).limit(50)).mappings().all()
        visible=[]
        for row in ids:
            try:self.get(actor,row['id']);visible.append(dict(row))
            except Exception as error:
                if getattr(error,'status',None) not in {403,404}:raise
        return {'items':visible}

    def approvers(self,actor):
        self.actor(actor.subject,actor.tenant_id,{'manager','analyst'})
        with self.engine.connect() as db:
            return {'items':[dict(r) for r in db.execute(select(users.c.id,users.c.username).where(
                users.c.tenant_id==actor.tenant_id,users.c.active.is_(True),users.c.role=='approver',
                users.c.id!=actor.subject).limit(100)).mappings()]}

    def command(self,actor,run_id,body,kind):
        with self.engine.begin() as db:
            row=self._run(db,actor.tenant_id,run_id,True);self._owner(actor,row)
            old=db.execute(select(commands).where(commands.c.tenant_id==actor.tenant_id,
                commands.c.workflow_id==run_id,commands.c.request_id==body.request_id)).mappings().first()
            if old:
                if old['kind']!=kind:raise WorkflowError(409,'Request ID already used')
                return
            if row['state'] in TERMINAL:raise WorkflowError(409,'Workflow is terminal')
            if kind=='refresh' and row['state'] not in {'WAITING_DOCUMENT','WAITING_CLARIFICATION','WAITING_APPROVAL'}:
                raise WorkflowError(409,'Workflow is not waiting for evidence')
            if kind=='refresh' and db.execute(select(ledger).where(ledger.c.tenant_id==actor.tenant_id,ledger.c.workflow_id==run_id)).first():
                raise WorkflowError(409,'Dispatch already claimed; reconcile first')
            db.execute(insert(commands).values(tenant_id=actor.tenant_id,workflow_id=run_id,
                request_id=body.request_id,kind=kind,actor_id=actor.subject,payload={},created_at=now_iso()))
            self._audit(db,actor.tenant_id,actor.subject,'workflow.'+kind+'_requested',run_id)

    def validate_snapshot(self,actor,snapshot_id):
        op=self.ops.get(actor,snapshot_id)
        if op['state']!='COMPLETED' or not op['result'] or op['result']['unresolved_order_ids']:
            raise WorkflowError(409,'Complete supported findings are required')
        self.docs.assert_snapshot_access(actor,op,current=True)
        plan=Plan.model_validate(op['plan']);gateway=ToolGateway(self.path,actor.tenant_id);gateway.actor=actor
        outputs={}
        for capability in ('purchase_orders.read','contracts.read'):
            task=next(t for t in plan.proposal.tasks if t.capability==capability)
            actual=gateway.execute(task,outputs,plan.as_of)
            saved=next(t['output'] for t in op['tasks'] if t['capability']==capability)
            if actual!=saved:raise WorkflowError(409,'Source data changed; refresh evidence and request new approval')
            outputs[capability]=actual
        return op,canonical_hash({'plan':op['plan_hash'],'sources':outputs,'result':op['result']})

    def draft(self,actor,run_id,body):
        with self.engine.connect() as db:row=self._run(db,actor.tenant_id,run_id)
        self._owner(actor,row)
        candidates=self.approvers(actor)['items']
        approver_id=body.approver_id or (candidates[0]['id'] if len(candidates)==1 else None)
        if not approver_id or approver_id not in {u['id'] for u in candidates}:
            raise WorkflowError(422,'Choose an independent active approver from your company')
        op,evidence_hash=self.validate_snapshot(actor,row['snapshot_id'])
        selected=[f for f in op['result']['findings'] if f['order_id'] in {
            o['id'] for t in op['tasks'] if t['capability']=='purchase_orders.read'
            for o in t['output']['items'] if o['supplier_id']==body.supplier_id}]
        if not selected or not any(f['late_days']>0 for f in selected):raise WorkflowError(422,'Select an overdue supplier in the reviewed findings')
        # No arbitrary destinations, CC/BCC or attachments in the sandbox connector.
        if not body.supplier_id.replace('-','').isalnum():raise WorkflowError(422,'Invalid sandbox supplier')
        action_id=str(uuid.uuid5(uuid.NAMESPACE_URL,run_id+'/'+body.request_id))
        payload={'tenant_id':actor.tenant_id,'operation_id':run_id,'logical_action_id':run_id+'/followup',
            'connector':'sandbox-mail','sender':'procurement@example.invalid',
            'to':[body.supplier_id.lower()+'@suppliers.example.invalid'],'cc':[],'bcc':[],
            'subject':body.subject,'body':body.body,'attachments':[],
            'supplier_id':body.supplier_id,'evidence_hash':evidence_hash,'findings':selected,
            'policy_version':POLICY,'approver_id':approver_id}
        digest=canonical_hash(payload)
        with self.engine.begin() as db:
            row=self._run(db,actor.tenant_id,run_id,True)
            old=db.execute(select(actions).where(actions.c.tenant_id==actor.tenant_id,actions.c.id==action_id)).mappings().first()
            if old:
                if old['action_hash']!=digest:raise WorkflowError(409,'Request ID used with a different proposal')
                return dict(old)
            if row['state']!='WAITING_APPROVAL' or row['snapshot_id']!=op['id']:
                raise WorkflowError(409,'Workflow is not ready for a proposal')
            if db.execute(select(ledger).where(ledger.c.tenant_id==actor.tenant_id,ledger.c.workflow_id==run_id)).first():
                raise WorkflowError(409,'Dispatch already claimed; proposal is frozen')
            revision=row['revision']+1
            changed=db.execute(update(runs).where(runs.c.tenant_id==actor.tenant_id,runs.c.id==run_id,
                runs.c.revision==row['revision']).values(revision=revision))
            if changed.rowcount!=1:raise WorkflowError(409,'Proposal changed')
            record=dict(tenant_id=actor.tenant_id,id=action_id,workflow_id=run_id,revision=revision,
                payload=payload,action_hash=digest,snapshot_id=op['id'],created_at=now_iso())
            db.execute(insert(actions).values(**record))
            self._audit(db,actor.tenant_id,actor.subject,'action.proposed',action_id,{'hash':digest,'revision':revision})
        return record

    def decision(self,actor,action_id,body):
        live=self.actor(actor.subject,actor.tenant_id,{'approver'})
        with self.engine.connect() as db:
            action=db.execute(select(actions).where(actions.c.tenant_id==actor.tenant_id,actions.c.id==action_id)).mappings().first()
            if not action:raise WorkflowError(404,'Action not found')
            row=self._run(db,actor.tenant_id,action['workflow_id'])
        if row['requester_id']==live.subject:raise WorkflowError(403,'Independent approver required')
        if action['payload'].get('approver_id')!=live.subject:raise WorkflowError(404,'Action not found')
        owner=self.actor(row['requester_id'],actor.tenant_id,{'manager','analyst'})
        _,digest=self.validate_snapshot(owner,action['snapshot_id'])
        self.docs.assert_snapshot_access(live,action['payload'],current=True)
        if body.action_hash!=action['action_hash'] or digest!=action['payload']['evidence_hash']:
            raise WorkflowError(409,'Action or evidence changed')
        decision_id=str(uuid.uuid5(uuid.NAMESPACE_URL,action_id+'/'+body.request_id))
        with self.engine.begin() as db:
            row=self._run(db,actor.tenant_id,action['workflow_id'],True)
            existing=db.execute(select(approvals).where(approvals.c.tenant_id==actor.tenant_id,approvals.c.id==decision_id)).mappings().first()
            if existing:
                if existing['decision']!=body.decision or existing['approver_id']!=actor.subject:raise WorkflowError(409,'Decision request already used')
                return dict(existing)
            latest=self._action(db,actor.tenant_id,row['id'])
            if row['state']!='WAITING_APPROVAL' or latest['id']!=action_id:raise WorkflowError(409,'Proposal is stale or no longer waiting')
            if db.execute(select(approvals.c.id).where(approvals.c.tenant_id==actor.tenant_id,
                approvals.c.action_id==action_id,approvals.c.decision=='reject')).first():
                raise WorkflowError(409,'Proposal was rejected; create a new linked operation')
            record=dict(tenant_id=actor.tenant_id,id=decision_id,action_id=action_id,approver_id=actor.subject,
                action_hash=body.action_hash,decision=body.decision,policy_version=POLICY,
                decided_at=time.time(),expires_at=time.time()+86400)
            db.execute(insert(approvals).values(**record))
            db.execute(insert(commands).values(tenant_id=actor.tenant_id,workflow_id=row['id'],request_id=decision_id,
                kind='decision',actor_id=actor.subject,payload={'decision_id':decision_id,'action_id':action_id},created_at=now_iso()))
            self._audit(db,actor.tenant_id,actor.subject,'approval.'+body.decision,action_id,{'hash':body.action_hash})
        return record

    # The following methods are worker-only activities, never directly exposed by HTTP.
    def project(self,tenant,run_id,state,**fields):
        with self.engine.begin() as db:
            row=self._run(db,tenant,run_id,True)
            if state=='COMPLETED' and db.execute(select(commands.c.id).where(commands.c.tenant_id==tenant,
                commands.c.workflow_id==run_id,commands.c.kind=='cancel')).first():state='CANCELLED'
            if state in TERMINAL:
                execution=db.execute(select(ledger).where(ledger.c.tenant_id==tenant,ledger.c.workflow_id==run_id)).mappings().first()
                if execution and execution['state']=='SUCCEEDED':
                    fields['result']={**(row['result'] or {}),'external_actions':1,'send_receipt':execution['receipt']}
            db.execute(update(runs).where(runs.c.tenant_id==tenant,runs.c.id==run_id)
                .values(state=state,updated_at=now_iso(),**fields))
            if row['state']!=state:self._audit(db,tenant,row['requester_id'],'workflow.state',run_id,{'state':state})
        return state

    def inbox(self,tenant,run_id,after):
        with self.engine.connect() as db:
            return [dict(r) for r in db.execute(select(commands).where(commands.c.tenant_id==tenant,
                commands.c.workflow_id==run_id,commands.c.id>after).order_by(commands.c.id).limit(100)).mappings()]

    def analyze(self,tenant,run_id,cycle):
        with self.engine.connect() as db:row=self._run(db,tenant,run_id)
        actor=self.actor(row['requester_id'],tenant,{'manager','analyst'})
        original=self.ops.get(actor,row['operation_id'])
        snapshot_id=original['id'] if cycle==0 else str(uuid.uuid5(uuid.NAMESPACE_URL,run_id+'/analysis/'+str(cycle)))
        if cycle:
            # A new read-only snapshot retains the exact confirmed plan; no hidden model call/replanning.
            with self.engine.begin() as db:
                if not db.execute(select(operations.c.id).where(operations.c.tenant_id==tenant,operations.c.id==snapshot_id)).first():
                    values={k:v for k,v in original.items() if k in operations.c and k not in {'id','lease_token','lease_until','deadline_at'}}
                    values.update(id=snapshot_id,state='PLANNED',result=None,error_code=None,created_at=now_iso(),updated_at=now_iso())
                    db.execute(insert(operations).values(**values))
                    self.ops._insert_tasks(db,actor,snapshot_id,Plan.model_validate(original['plan']))
        op=self.ops.get(actor,snapshot_id)
        if op['state']=='PLANNED':op=self.ops.execute(actor,snapshot_id,PlanConfirmation(version=op['plan_version'],plan_hash=op['plan_hash']),durable=True)
        elif op['state']=='RUNNING':op=self.ops.execute(actor,snapshot_id,resume=True,durable=True)
        self.docs.assert_snapshot_access(actor,op,current=True)
        state=op['state'] if op['state'] in {'WAITING_DOCUMENT','WAITING_CLARIFICATION'} else 'WAITING_APPROVAL' if op['state']=='COMPLETED' else 'FAILED'
        return {'state':state,'snapshot_id':snapshot_id,'result':op['result'],'error_code':op['error_code']}

    def dispatch(self,tenant,run_id,decision_id):
        with self.engine.connect() as db:
            row=self._run(db,tenant,run_id)
            execution=db.execute(select(ledger).where(ledger.c.tenant_id==tenant,ledger.c.workflow_id==run_id)).mappings().first()
            approval=db.execute(select(approvals).where(approvals.c.tenant_id==tenant,approvals.c.id==decision_id)).mappings().first()
            action=self._action(db,tenant,run_id)
        key=canonical_hash({'tenant':tenant,'logical_action':run_id+'/followup'})
        if execution:
            # Reconcile accepted sends before authorization/cancellation checks: they already happened.
            if execution['state']=='SUCCEEDED':return {'state':'COMPLETED','receipt':execution['receipt']}
            accepted=self.adapter.lookup(key)
            if accepted:
                if accepted['action_hash']!=execution['action_hash']:raise WorkflowError(409,'Provider receipt mismatch')
                return self._accepted(tenant,run_id,accepted['receipt'])
            if execution['state'] in {'SENDING','RECONCILING'}:
                return {'state':'RECONCILING'} # An absent lookup does not establish failure.
        if not approval or not action or approval['action_id']!=action['id'] or approval['decision']!='approve':
            return {'state':'WAITING_APPROVAL','error_code':'APPROVAL_REQUIRED'}
        if approval['expires_at']<=time.time() or approval['policy_version']!=POLICY:
            return {'state':'WAITING_APPROVAL','error_code':'APPROVAL_EXPIRED_OR_POLICY_CHANGED'}
        try:
            owner=self.actor(row['requester_id'],tenant,{'manager','analyst'})
            approver=self.actor(approval['approver_id'],tenant,{'approver'})
            if approver.subject==owner.subject:raise WorkflowError(403,'Independent approval required')
            if action['payload'].get('approver_id')!=approver.subject:raise WorkflowError(403,'Assigned approver required')
            _,evidence_hash=self.validate_snapshot(owner,action['snapshot_id'])
            self.docs.assert_snapshot_access(approver,action['payload'],current=True)
            if (approval['action_hash']!=action['action_hash'] or canonical_hash(action['payload'])!=action['action_hash']
                or action['payload']['evidence_hash']!=evidence_hash or action['payload']['policy_version']!=POLICY):
                raise WorkflowError(409,'Action/evidence changed')
        except Exception as error:
            if getattr(error,'status',None) in {403,404,409}:return {'state':'WAITING_APPROVAL','error_code':'STALE_AUTHORIZATION_OR_EVIDENCE'}
            raise
        with self.engine.begin() as db:
            locked=self._run(db,tenant,run_id,True)
            latest=self._action(db,tenant,run_id)
            if latest['id']!=action['id'] or locked['snapshot_id']!=action['snapshot_id']:
                return {'state':'WAITING_APPROVAL','error_code':'PROPOSAL_CHANGED'}
            if db.execute(select(approvals.c.id).where(approvals.c.tenant_id==tenant,
                approvals.c.action_id==action['id'],approvals.c.decision=='reject')).first():return {'state':'REJECTED'}
            if db.execute(select(commands.c.id).where(commands.c.tenant_id==tenant,commands.c.workflow_id==run_id,
                    commands.c.kind=='cancel')).first():return {'state':'CANCELLED'}
            if execution:
                if execution['attempts']>=3:return {'state':'FAILED','error_code':'SEND_ATTEMPTS_EXHAUSTED'}
                claimed=db.execute(update(ledger).where(ledger.c.tenant_id==tenant,ledger.c.workflow_id==run_id,
                    ledger.c.state=='PRE_SUBMISSION_FAILED',ledger.c.attempts==execution['attempts']).values(
                        state='SENDING',attempts=execution['attempts']+1,updated_at=now_iso()))
                if claimed.rowcount!=1:return {'state':'RECONCILING'}
            else:
                try:db.execute(insert(ledger).values(tenant_id=tenant,workflow_id=run_id,action_id=action['id'],
                    action_hash=action['action_hash'],idempotency_key=key,state='SENDING',attempts=1,updated_at=now_iso()))
                except IntegrityError:return {'state':'RECONCILING'}
        try:receipt=self.adapter.send(key,action['payload'],action['action_hash'])
        except PreSubmissionFailure:
            with self.engine.begin() as db:db.execute(update(ledger).where(ledger.c.tenant_id==tenant,ledger.c.workflow_id==run_id)
                .values(state='PRE_SUBMISSION_FAILED',updated_at=now_iso()))
            return {'state':'RETRY_SEND'}
        except Exception:
            with self.engine.begin() as db:db.execute(update(ledger).where(ledger.c.tenant_id==tenant,ledger.c.workflow_id==run_id)
                .values(state='RECONCILING',updated_at=now_iso()))
            return {'state':'RECONCILING'}
        return self._accepted(tenant,run_id,receipt)

    def _accepted(self,tenant,run_id,receipt):
        if not receipt.get('accepted') or not receipt.get('message_id'):return {'state':'RECONCILING'}
        with self.engine.begin() as db:
            db.execute(update(ledger).where(ledger.c.tenant_id==tenant,ledger.c.workflow_id==run_id)
                .values(state='SUCCEEDED',receipt=receipt,updated_at=now_iso()))
            row=self._run(db,tenant,run_id)
            self._audit(db,tenant,row['requester_id'],'action.receipt_recorded',run_id,{'receipt':receipt})
        return {'state':'COMPLETED','receipt':receipt}
