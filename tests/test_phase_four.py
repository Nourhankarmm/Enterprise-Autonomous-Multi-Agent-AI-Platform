"""Independent approval, immutable proposals, recovery and dispatch safety regressions."""
import os,time,uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sqlalchemy import insert,select,update,func
from fastapi.testclient import TestClient
import test_phase_three as phase3
from platform_api.main import create_app,Identity
from platform_api.agents.planner import DemoPlanner
from platform_api.agents.schemas import DEMO_GOAL,canonical_hash
from platform_api.database import (contracts,users,orders,workflow_runs,workflow_actions,approvals,
    action_executions,sandbox_messages)
from platform_api.workflows.service import WorkflowService
from platform_api.workflows.schemas import StartInput,DraftInput,DecisionInput,CommandInput,WorkflowError
from platform_api.workflows.adapter import SandboxMailAdapter,PreSubmissionFailure,UnknownOutcome

class PhaseFourTests(unittest.TestCase):
    setUpClass=classmethod(phase3.PhaseThreeTests.setUpClass.__func__)
    tearDownClass=classmethod(phase3.PhaseThreeTests.tearDownClass.__func__)
    headers=phase3.PhaseThreeTests.headers
    upload=phase3.PhaseThreeTests.upload
    doc=phase3.PhaseThreeTests.doc
    publish=phase3.PhaseThreeTests.publish
    share=phase3.PhaseThreeTests.share

    def setUp(self):
        phase3.PhaseThreeTests.setUp(self)
        self.client.__exit__(None,None,None)
        self.approver='approver-'+uuid.uuid4().hex[:12]
        with self.engine.begin() as db:
            db.execute(insert(users).values(id=self.approver,username=self.approver,tenant_id=self.alpha,
                role='approver',active=True,password_hash='not-a-login'))
            db.execute(insert(contracts).values(tenant_id=self.alpha,supplier_id='S3',id='C-S3',grace_days=2,version=1,location='fixture'))
        self.tokens['approver']=Identity(tenant_id=self.alpha,subject=self.approver,role='approver')
        self.app=create_app(self.path,self.tokens,DemoPlanner());self.client=TestClient(self.app);self.client.__enter__()
        self.service=self.app.state.workflows;self.owner=self.tokens['alice'];self.reviewer=self.tokens['approver']
        op=self.client.post('/v1/operations',headers=self.headers(),json={'goal':DEMO_GOAL,'as_of':'2026-11-14'}).json()
        self.op=op
        self.run=self.service.reserve(self.owner,op['id'],StartInput(version=op['plan_version'],plan_hash=op['plan_hash']))
        analysis=self.service.analyze(self.alpha,self.run,0)
        self.service.project(self.alpha,self.run,analysis.pop('state'),**analysis)

    def tearDown(self):phase3.PhaseThreeTests.tearDown(self)
    def draft(self,**fields):
        return self.service.draft(self.owner,self.run,DraftInput(request_id=uuid.uuid4().hex,
            supplier_id='S1',subject='Delivery follow-up',body='Please confirm delivery timing.',**fields))
    def decision(self,action,decision='approve',who=None):
        return self.service.decision(who or self.reviewer,action['id'],DecisionInput(request_id=uuid.uuid4().hex,
            action_hash=action['action_hash'],decision=decision))
    def dispatch(self,decision):return self.service.dispatch(self.alpha,self.run,decision['id'])
    def count_messages(self):
        with self.engine.connect() as db:return db.execute(select(func.count()).select_from(sandbox_messages).where(sandbox_messages.c.tenant_id==self.alpha)).scalar_one()

    def test_start_is_idempotent_and_exact_plan_required(self):
        body=StartInput(version=self.op['plan_version'],plan_hash=self.op['plan_hash'])
        self.assertEqual(self.service.reserve(self.owner,self.op['id'],body),self.run)
        with self.assertRaises(WorkflowError):self.service.reserve(self.owner,self.op['id'],StartInput(version=99,plan_hash='a'*64))

    def test_legacy_controls_cannot_mutate_temporal_owned_operation(self):
        for path in ('cancel','resume','confirm'):
            response=self.client.post('/v1/operations/'+self.op['id']+'/'+path,headers=self.headers(),
                json={'version':1,'plan_hash':self.op['plan_hash']} if path=='confirm' else None)
            self.assertEqual(response.status_code,409,response.text)

    def test_no_approval_means_zero_dispatch(self):
        self.draft();self.assertEqual(self.service.dispatch(self.alpha,self.run,None)['state'],'WAITING_APPROVAL')
        self.assertEqual(self.count_messages(),0)

    def test_only_live_independent_approver_can_decide(self):
        action=self.draft()
        for actor in (self.owner,self.tokens['bob'],self.tokens['carol']):
            with self.assertRaises(WorkflowError):self.decision(action,who=actor)
        with self.engine.begin() as db:db.execute(update(users).where(users.c.id==self.alice).values(role='approver'))
        with self.assertRaises(WorkflowError):self.decision(action,who=Identity(tenant_id=self.alpha,subject=self.alice,role='approver'))

    def test_forged_roles_and_cross_tenant_run_are_denied(self):
        r=self.client.get('/v1/workflows/'+self.run,headers=self.headers('carol'));self.assertEqual(r.status_code,404)
        r=self.client.post('/v1/approvals/'+self.draft()['id']+'/decision',headers=self.headers(),json={
            'request_id':uuid.uuid4().hex,'action_hash':'a'*64,'decision':'approve','role':'approver'})
        self.assertEqual(r.status_code,422)

    def test_proposal_contains_exact_canonical_action_and_sandbox_only_destination(self):
        action=self.draft();payload=action['payload']
        self.assertEqual(canonical_hash(payload),action['action_hash'])
        self.assertEqual(payload['to'],['s1@suppliers.example.invalid'])
        self.assertEqual(payload['attachments'],[]);self.assertEqual(payload['connector'],'sandbox-mail')
        response=self.client.post('/v1/workflows/'+self.run+'/draft',headers=self.headers(),json={
            'request_id':uuid.uuid4().hex,'supplier_id':'S1','subject':'x','body':'x','to':['evil@example.com']})
        self.assertEqual(response.status_code,422)

    def test_revision_invalidates_approval_and_old_hash(self):
        old=self.draft();decision=self.decision(old);new=self.draft()
        self.assertNotEqual(old['id'],new['id'])
        self.assertEqual(self.dispatch(decision)['state'],'WAITING_APPROVAL')
        with self.assertRaises(WorkflowError):self.decision(old)
        self.assertEqual(self.count_messages(),0)

    def test_expired_approval_blocks_then_fresh_review_allows_send(self):
        action=self.draft();decision=self.decision(action)
        with self.engine.begin() as db:db.execute(update(approvals).where(approvals.c.id==decision['id']).values(expires_at=time.time()-1))
        self.assertEqual(self.dispatch(decision)['state'],'WAITING_APPROVAL')
        self.assertEqual(self.count_messages(),0)
        fresh=self.decision(action);self.assertEqual(self.dispatch(fresh)['state'],'COMPLETED')

    def test_revoked_approver_and_requester_permissions_block_send(self):
        for subject in (self.approver,self.alice):
            action=self.draft();decision=self.decision(action)
            with self.engine.begin() as db:db.execute(update(users).where(users.c.id==subject).values(active=False))
            self.assertEqual(self.dispatch(decision)['state'],'WAITING_APPROVAL')
            with self.engine.begin() as db:db.execute(update(users).where(users.c.id==subject).values(active=True))
        self.assertEqual(self.count_messages(),0)

    def test_changed_erp_or_contract_requires_fresh_evidence(self):
        action=self.draft();decision=self.decision(action)
        with self.engine.begin() as db:db.execute(update(orders).where(orders.c.tenant_id==self.alpha,orders.c.id=='PO-A1').values(delivered_quantity=21))
        self.assertEqual(self.dispatch(decision)['state'],'WAITING_APPROVAL')
        self.assertEqual(self.count_messages(),0)

    def test_changed_action_or_policy_blocks_dispatch(self):
        action=self.draft();decision=self.decision(action)
        payload=dict(action['payload']);payload['body']='tampered'
        with self.engine.begin() as db:db.execute(update(workflow_actions).where(workflow_actions.c.id==action['id']).values(payload=payload))
        self.assertEqual(self.dispatch(decision)['state'],'WAITING_APPROVAL');self.assertEqual(self.count_messages(),0)

    def test_replay_and_new_process_reuse_receipt(self):
        action=self.draft();decision=self.decision(action);first=self.dispatch(decision)
        self.assertEqual(first['state'],'COMPLETED')
        restarted=WorkflowService(self.engine,self.path)
        self.assertEqual(restarted.dispatch(self.alpha,self.run,decision['id']),first)
        self.assertEqual(self.count_messages(),1)

    def test_provider_accept_then_worker_crash_reconciles_without_second_send(self):
        action=self.draft();decision=self.decision(action)
        real=self.service._accepted
        with patch.object(self.service,'_accepted',side_effect=RuntimeError('worker died')):
            with self.assertRaises(RuntimeError):self.dispatch(decision)
        self.assertEqual(self.count_messages(),1)
        with patch.object(self.service.adapter,'send',side_effect=AssertionError('must lookup')):
            self.assertEqual(self.dispatch(decision)['state'],'COMPLETED')
        self.assertEqual(self.count_messages(),1)

    def test_unknown_provider_outcome_is_never_blindly_retried(self):
        decision=self.decision(self.draft())
        with patch.object(self.service.adapter,'send',side_effect=UnknownOutcome('unknown')) as send:
            self.assertEqual(self.dispatch(decision)['state'],'RECONCILING')
            self.assertEqual(self.dispatch(decision)['state'],'RECONCILING')
            self.assertEqual(send.call_count,1)
        self.assertEqual(self.count_messages(),0)

    def test_known_presubmission_failure_retries_are_bounded(self):
        decision=self.decision(self.draft())
        with patch.object(self.service.adapter,'send',side_effect=PreSubmissionFailure()) as send:
            for _ in range(3):self.assertEqual(self.dispatch(decision)['state'],'RETRY_SEND')
            self.assertEqual(self.dispatch(decision)['state'],'FAILED');self.assertEqual(send.call_count,3)

    def test_cancellation_before_dispatch_has_no_side_effect(self):
        decision=self.decision(self.draft())
        self.service.command(self.owner,self.run,CommandInput(request_id=uuid.uuid4().hex),'cancel')
        self.assertEqual(self.dispatch(decision)['state'],'CANCELLED');self.assertEqual(self.count_messages(),0)

    def test_cancel_after_provider_acceptance_still_reports_receipt(self):
        decision=self.decision(self.draft());accepted=self.dispatch(decision)
        self.service.command(self.owner,self.run,CommandInput(request_id=uuid.uuid4().hex),'cancel')
        self.assertEqual(self.dispatch(decision)['receipt'],accepted['receipt']);self.assertEqual(self.count_messages(),1)

    def test_rejected_proposal_cannot_be_approved_later(self):
        action=self.draft();self.decision(action,'reject')
        with self.assertRaises(WorkflowError):self.decision(action)
        self.assertEqual(self.count_messages(),0)

    def test_commands_and_decisions_are_idempotent(self):
        body=CommandInput(request_id=uuid.uuid4().hex)
        self.service.command(self.owner,self.run,body,'refresh');self.service.command(self.owner,self.run,body,'refresh')
        self.assertEqual(len(self.service.inbox(self.alpha,self.run,0)),1)
        action=self.draft();body=DecisionInput(request_id=uuid.uuid4().hex,action_hash=action['action_hash'],decision='approve')
        first=self.service.decision(self.reviewer,action['id'],body)
        self.assertEqual(self.service.decision(self.reviewer,action['id'],body),first)

    def test_new_document_refresh_creates_new_snapshot_without_rewriting_old_result(self):
        old=self.service.get(self.owner,self.run)['snapshot_id']
        with self.engine.begin() as db:db.execute(update(orders).where(orders.c.tenant_id==self.alpha,orders.c.id=='PO-A1').values(delivered_quantity=30))
        new=self.service.analyze(self.alpha,self.run,1)
        self.assertNotEqual(new['snapshot_id'],old)
        original=self.service.ops.get(self.owner,old)
        self.assertNotEqual(original['result'],new['result'])
        self.assertEqual(self.service.analyze(self.alpha,self.run,1),new)

    def test_document_acl_applies_to_approver_and_revocation_blocks_dispatch(self):
        doc=self.doc();self.assertEqual(self.publish(doc).status_code,200)
        analyzed=self.service.analyze(self.alpha,self.run,1);self.service.project(self.alpha,self.run,analyzed.pop('state'),**analyzed)
        action=self.draft()
        with self.assertRaises(Exception):self.decision(action)
        doc=self.service.docs.get(self.owner,doc['id'])
        self.assertEqual(self.share(doc,[self.approver]).status_code,200)
        decision=self.decision(action)
        doc=self.service.docs.get(self.owner,doc['id']);self.assertEqual(self.share(doc,[]).status_code,200)
        self.assertEqual(self.dispatch(decision)['state'],'WAITING_APPROVAL');self.assertEqual(self.count_messages(),0)

    def test_terminal_workflows_cannot_be_restarted_or_edited(self):
        self.service.project(self.alpha,self.run,'CANCELLED')
        with self.assertRaises(WorkflowError):self.draft()
        with self.assertRaises(WorkflowError):self.service.command(self.owner,self.run,CommandInput(request_id=uuid.uuid4().hex),'refresh')

    def test_unconfigured_temporal_returns_clear_error_without_execution(self):
        with patch.dict(os.environ,{'TEMPORAL_ADDRESS':''}):
            response=self.client.post('/v1/operations/'+self.op['id']+'/durable',headers=self.headers(),json={
                'version':self.op['plan_version'],'plan_hash':self.op['plan_hash']})
            self.assertEqual(response.status_code,503)

    def test_simultaneous_dispatch_converges_on_one_provider_message(self):
        decision=self.decision(self.draft())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.dispatch(decision),range(2)))
        final=self.dispatch(decision)
        self.assertEqual(final['state'],'COMPLETED');self.assertEqual(self.count_messages(),1)
        self.assertTrue(all(r['state'] in {'COMPLETED','RECONCILING'} for r in results))

    def test_approver_evidence_view_requires_explicit_acl(self):
        doc=self.doc();self.publish(doc)
        path='/v1/documents/'+doc['id']+'/pages/2'
        self.assertEqual(self.client.get(path,headers=self.headers('approver')).status_code,404)
        doc=self.service.docs.get(self.owner,doc['id']);self.share(doc,[self.approver])
        self.assertEqual(self.client.get(path,headers=self.headers('approver')).status_code,200)
        self.assertEqual(self.client.post('/v1/documents',headers=self.headers('approver'),data={}).status_code,403)

    def test_only_assigned_approver_can_view_or_decide(self):
        action=self.draft();other='other-'+uuid.uuid4().hex
        with self.engine.begin() as db:db.execute(insert(users).values(id=other,username=other,tenant_id=self.alpha,
            role='approver',active=True,password_hash='not-a-login'))
        actor=Identity(tenant_id=self.alpha,subject=other,role='approver')
        with self.assertRaises(WorkflowError):self.service.get(actor,self.run)
        with self.assertRaises(WorkflowError):self.decision(action,who=actor)
        self.assertEqual(self.service.list(actor)['items'],[])

    def test_no_eligible_approver_blocks_draft_instead_of_self_approval(self):
        with self.engine.begin() as db:db.execute(update(users).where(users.c.id==self.approver).values(active=False))
        with self.assertRaises(WorkflowError):self.draft()
        self.assertEqual(self.count_messages(),0)
