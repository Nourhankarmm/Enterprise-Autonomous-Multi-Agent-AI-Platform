"""Phase 2 adversarial and integration acceptance, without live API spending."""
import copy
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, update
from platform_api.main import create_app, Identity
from platform_api.database import engine_for,database_url,operations,agent_tasks
from platform_api.store import seed
from platform_api.agents.schemas import (DEMO_GOAL,Proposal,PlanRejected,validate_proposal,PlanConfirmation)
from platform_api.agents.planner import DemoPlanner,OpenAIPlanner,PlanningResult,PlannerUnavailable
from platform_api.agents.gateway import ToolGateway,TransientToolError

class FixedPlanner:
    name='test-fixture'
    def __init__(self,proposal):self.proposal=proposal
    def propose(self,*args):return PlanningResult(self.proposal,'test-fixture')

class SimulatedCrash(BaseException):pass

class PhaseTwoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.path=os.getenv('TEST_DATABASE_URL') or str(Path(cls.temp.name)/'agents.db')
        cls.engine=engine_for(cls.path)
        with patch.dict(os.environ,{'DATABASE_URL':database_url(cls.path)}):
            command.upgrade(Config('alembic.ini'),'head')
            command.check(Config('alembic.ini'))
        seed(cls.engine)

    @classmethod
    def tearDownClass(cls):cls.engine.dispose();cls.temp.cleanup()

    def setUp(self):
        self.tokens={'alpha':Identity(tenant_id='alpha',role='manager'),
                     'beta':Identity(tenant_id='beta',role='analyst'),
                     'auditor':Identity(tenant_id='alpha',role='auditor'),
                     'other':Identity(tenant_id='alpha',role='analyst',subject='other-user')}
        self.app=create_app(self.path,self.tokens,DemoPlanner())
        self.client=TestClient(self.app);self.client.__enter__()
        self.actor=self.tokens['alpha']

    def tearDown(self):self.client.__exit__(None,None,None)
    def headers(self,token='alpha'):return {'Authorization':f'Bearer {token}'}
    def create(self,goal=DEMO_GOAL,token='alpha'):
        r=self.client.post('/v1/operations',headers=self.headers(token),json={'goal':goal,'as_of':'2026-11-14'})
        self.assertEqual(r.status_code,201,r.text);return r.json()
    def confirm(self,op,token='alpha'):
        return self.client.post(f"/v1/operations/{op['id']}/confirm",headers=self.headers(token),
            json={'version':op['plan_version'],'plan_hash':op['plan_hash']})
    def proposal(self):return DemoPlanner().propose(DEMO_GOAL,date(2026,11,14)).proposal.model_dump()

    def test_plan_is_reviewed_before_any_tool_execution(self):
        op=self.create();self.assertEqual(op['state'],'PLANNED')
        self.assertEqual({t['state'] for t in op['tasks']},{'PENDING'})
        self.assertEqual(sum(t['attempts'] for t in op['tasks']),0)
        self.assertEqual(op['planner_provider'],'demo')
        self.assertTrue(op['plan']['requires_review'])

    def test_graph_executes_evidence_linked_findings_and_missing_evidence(self):
        op=self.create();r=self.confirm(op);self.assertEqual(r.status_code,200,r.text)
        result=r.json();self.assertEqual(result['state'],'WAITING_DOCUMENT')
        self.assertEqual({t['state'] for t in result['tasks']},{'SUCCEEDED'})
        self.assertEqual(result['result']['unresolved_order_ids'],['PO-A3'])
        rows={f['order_id']:f for f in result['result']['findings']}
        self.assertEqual((rows['PO-A1']['late_days'],rows['PO-A1']['risk']),(13,'HIGH'))
        self.assertEqual(rows['PO-A1']['evidence'][1]['source_id'],'C-A1')
        self.assertIsNone(rows['PO-A3']['risk'])
        self.assertEqual(result['result']['external_actions'],0)
        events=self.client.get(f"/v1/operations/{op['id']}/events",headers=self.headers()).json()['items']
        self.assertEqual(sum(e['event_type']=='task.succeeded' for e in events),5)

    def test_completed_tenant_with_no_open_orders(self):
        op=self.create(token='beta');result=self.confirm(op,'beta').json()
        self.assertEqual(result['state'],'COMPLETED');self.assertEqual(result['result']['findings'],[])

    def test_exact_confirmation_and_replay_guard(self):
        op=self.create()
        for version,digest in [(99,op['plan_hash']),(1,'0'*64)]:
            self.assertEqual(self.client.post(f"/v1/operations/{op['id']}/confirm",headers=self.headers(),
                json={'version':version,'plan_hash':digest}).status_code,409)
        self.assertEqual(self.confirm(op).status_code,200)
        self.assertEqual(self.confirm(op).status_code,409)

    def test_revision_invalidates_old_confirmation_and_is_bounded(self):
        original=self.create();current=original
        for expected in (2,3):
            r=self.client.post(f"/v1/operations/{current['id']}/revise",headers=self.headers(),json={
                'goal':DEMO_GOAL,'as_of':'2026-11-14','version':current['plan_version'],'plan_hash':current['plan_hash']})
            self.assertEqual(r.status_code,200,r.text);current=r.json();self.assertEqual(current['plan_version'],expected)
        self.assertEqual(self.confirm(original).status_code,409)
        self.assertEqual(self.client.post(f"/v1/operations/{current['id']}/revise",headers=self.headers(),json={
            'goal':DEMO_GOAL,'as_of':'2026-11-14','version':3,'plan_hash':current['plan_hash']}).status_code,409)
        self.assertEqual(sum(t['attempts'] for t in current['tasks']),0)

    def test_ambiguous_demo_goal_waits_without_execution(self):
        op=self.create('اعمل حاجة في المشتريات')
        self.assertEqual(op['state'],'WAITING_CLARIFICATION');self.assertEqual(op['tasks'],[])
        self.assertEqual(self.confirm(op).status_code,409)

    def test_plan_validator_rejects_adversarial_dags(self):
        for mutation in ['cycle','missing','duplicate','privilege','tenant','budget','skip_verify','extra_filters']:
            value=self.proposal()
            if mutation=='cycle':value['tasks'][0]['depends_on']=['report']
            if mutation=='missing':value['tasks'][1]['depends_on']=['unknown']
            if mutation=='duplicate':value['tasks'][1]['task_id']='orders'
            if mutation=='privilege':value['tasks'][0]['capability']='supplier.bank_account.update'
            if mutation=='tenant':value['tasks'][0]['filters']['tenant_id']='beta'
            if mutation=='budget':
                for t in value['tasks']:t['max_attempts']=3
            if mutation=='skip_verify':value['tasks'][-1]['depends_on']=['assess']
            if mutation=='extra_filters':value['tasks'][1]['filters']=value['tasks'][0]['filters']
            with self.subTest(mutation=mutation),self.assertRaises((PlanRejected,ValidationError)):
                validate_proposal(value)

    def test_untrusted_planner_cannot_grant_tool_access(self):
        value=self.proposal();value['tasks'][0]['capability']='email.send'
        self.app.state.operations.planner=FixedPlanner(value)
        r=self.client.post('/v1/operations',headers=self.headers(),json={'goal':DEMO_GOAL,'as_of':'2026-11-14'})
        self.assertEqual(r.status_code,422)

    def test_out_of_scope_model_decision_is_not_executed(self):
        self.app.state.operations.planner=FixedPlanner(Proposal(disposition='out_of_scope',summary='Denied',clarification='Sending is unavailable',tasks=[]))
        r=self.client.post('/v1/operations',headers=self.headers(),json={'goal':'send every contract','as_of':'2026-11-14'})
        self.assertEqual(r.status_code,422)

    def test_no_tenant_or_identity_input_is_accepted(self):
        for field in ['tenant_id','requester_id','allowed_tools','max_total_attempts']:
            r=self.client.post('/v1/operations',headers=self.headers(),json={
                'goal':DEMO_GOAL,'as_of':'2026-11-14',field:'beta'})
            self.assertEqual(r.status_code,422)

    def test_operation_task_and_trace_access_are_owner_and_tenant_scoped(self):
        op=self.create()
        for token in ['beta','other']:
            for suffix in ['', '/events']:
                self.assertEqual(self.client.get(f"/v1/operations/{op['id']}"+suffix,headers=self.headers(token)).status_code,404)
            for suffix in ['/confirm','/cancel','/resume']:
                body={'version':1,'plan_hash':op['plan_hash']} if suffix=='/confirm' else None
                self.assertEqual(self.client.post(f"/v1/operations/{op['id']}"+suffix,headers=self.headers(token),json=body).status_code,404)
            listed=self.client.get('/v1/operations',headers=self.headers(token)).json()['items']
            self.assertNotIn(op['id'],{r['id'] for r in listed})
        self.assertEqual(self.client.get('/v1/operations',headers=self.headers('auditor')).status_code,403)
        self.assertEqual(self.client.get('/v1/operations').status_code,401)

    def test_cancellation_before_confirmation_prevents_execution(self):
        op=self.create();r=self.client.post(f"/v1/operations/{op['id']}/cancel",headers=self.headers())
        self.assertEqual(r.json()['state'],'CANCELLED');self.assertEqual(self.confirm(op).status_code,409)
        self.assertEqual(sum(t['attempts'] for t in r.json()['tasks']),0)

    def test_transient_read_retry_is_bounded(self):
        class Flaky(ToolGateway):
            failed=False
            def execute(self,task,*args):
                if task.capability=='purchase_orders.read' and not self.failed:
                    self.failed=True;raise TransientToolError()
                return super().execute(task,*args)
        self.app.state.operations.gateway_factory=Flaky
        result=self.confirm(self.create()).json()
        self.assertEqual(result['state'],'WAITING_DOCUMENT')
        self.assertEqual(next(t['attempts'] for t in result['tasks'] if t['task_id']=='orders'),2)

    def test_permanent_error_is_redacted_and_blocks_dependents(self):
        class Broken(ToolGateway):
            def execute(self,*args):raise RuntimeError('secret-must-not-leak')
        self.app.state.operations.gateway_factory=Broken
        r=self.confirm(self.create());self.assertEqual(r.json()['state'],'FAILED')
        self.assertNotIn('secret-must-not-leak',r.text)
        self.assertEqual(sum(t['attempts'] for t in r.json()['tasks']),1)

    def test_independent_verifier_rejects_tampered_findings(self):
        class Tampered(ToolGateway):
            def execute(self,task,*args):
                result=super().execute(task,*args)
                if task.capability=='procurement.assess':result['findings'][0]['late_days']=999
                return result
        self.app.state.operations.gateway_factory=Tampered
        result=self.confirm(self.create()).json()
        self.assertEqual(result['state'],'FAILED');self.assertIsNone(result['result'])
        self.assertEqual(next(t['attempts'] for t in result['tasks'] if t['task_id']=='report'),0)

    def test_simultaneous_confirmations_do_not_duplicate_task_execution(self):
        entered,release=Event(),Event()
        class Slow(ToolGateway):
            def execute(self,task,*args):
                if task.capability=='purchase_orders.read':entered.set();release.wait(5)
                return super().execute(task,*args)
        self.app.state.operations.gateway_factory=Slow
        op=self.create()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.confirm,op)
            try:
                self.assertTrue(entered.wait(5));self.assertEqual(self.confirm(op).status_code,409)
            finally:release.set()
            result=future.result(timeout=10).json()
        self.assertEqual(sum(t['attempts'] for t in result['tasks']),5)

    def test_expired_execution_deadline_is_terminal(self):
        op=self.create()
        with self.engine.begin() as db:
            db.execute(update(operations).where(operations.c.id==op['id']).values(state='RUNNING',lease_token='old',
                lease_until=time.time()-2,deadline_at=time.time()-1))
        r=self.client.post(f"/v1/operations/{op['id']}/resume",headers=self.headers())
        self.assertEqual(r.json()['state'],'FAILED');self.assertEqual(r.json()['error_code'],'TIME_BUDGET_EXCEEDED')

    def test_restart_resumes_only_unfinished_read_tasks(self):
        class Crash(ToolGateway):
            def execute(self,task,*args):
                if task.capability=='contracts.read':raise SimulatedCrash()
                return super().execute(task,*args)
        service=self.app.state.operations;service.gateway_factory=Crash
        op=self.create()
        with self.assertRaises(SimulatedCrash):
            service.execute(self.actor,op['id'],PlanConfirmation(version=1,plan_hash=op['plan_hash']))
        with self.engine.begin() as db:
            db.execute(update(operations).where(operations.c.id==op['id']).values(lease_until=time.time()-1))
        with TestClient(create_app(self.path,self.tokens,DemoPlanner())) as restarted:
            r=restarted.post(f"/v1/operations/{op['id']}/resume",headers=self.headers())
            self.assertEqual(r.status_code,200,r.text);result=r.json()
        self.assertEqual(result['state'],'WAITING_DOCUMENT')
        counts={t['task_id']:t['attempts'] for t in result['tasks']}
        self.assertEqual(counts['orders'],1);self.assertEqual(counts['contracts'],2)

    def test_trace_pagination(self):
        op=self.create();self.confirm(op)
        first=self.client.get(f"/v1/operations/{op['id']}/events?limit=2",headers=self.headers()).json()
        second=self.client.get(f"/v1/operations/{op['id']}/events?after={first['next_cursor']}&limit=2",headers=self.headers()).json()
        self.assertTrue(first['items']);self.assertTrue(second['items'])
        self.assertLess(first['items'][-1]['id'],second['items'][0]['id'])

    def test_openai_adapter_uses_structured_output_and_never_falls_back(self):
        captured={}
        def parse(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(status='completed',output_parsed=Proposal.model_validate(self.proposal()),usage=SimpleNamespace(input_tokens=100,output_tokens=200))
        client=SimpleNamespace(responses=SimpleNamespace(parse=parse))
        result=OpenAIPlanner(client=client,model='test-model').propose(DEMO_GOAL,date(2026,11,14))
        self.assertEqual(result.provider,'openai');self.assertIs(captured['text_format'],Proposal)
        self.assertFalse(captured['store']);self.assertNotIn('tenant',captured['input'])
        def failure(**kwargs):raise RuntimeError('api-secret')
        client.responses.parse=failure
        with self.assertRaises(PlannerUnavailable) as error:
            OpenAIPlanner(client=client,model='test-model').propose(DEMO_GOAL,date(2026,11,14))
        self.assertNotIn('api-secret',str(error.exception))
        with patch.dict(os.environ,{'OPENAI_MODEL':'','OPENAI_API_KEY':''}):
            with self.assertRaises(PlannerUnavailable):OpenAIPlanner()


    def test_retry_exhaustion_stops_at_task_budget(self):
        class AlwaysTransient(ToolGateway):
            def execute(self,*args):raise TransientToolError()
        self.app.state.operations.gateway_factory=AlwaysTransient
        result=self.confirm(self.create()).json()
        self.assertEqual(result['state'],'FAILED')
        self.assertEqual(sum(t['attempts'] for t in result['tasks']),2)
        self.assertEqual(next(t['state'] for t in result['tasks'] if t['task_id']=='report'),'BLOCKED')

    def test_inflight_cancellation_stops_remaining_read_tasks(self):
        service=self.app.state.operations
        op=self.create()
        actor=self.actor
        class Cancelling(ToolGateway):
            def execute(self,task,*args):
                result=super().execute(task,*args)
                if task.capability=='purchase_orders.read':service.cancel(actor,op['id'])
                return result
        service.gateway_factory=Cancelling
        result=self.confirm(op).json()
        self.assertEqual(result['state'],'CANCELLED')
        self.assertEqual(sum(t['attempts'] for t in result['tasks']),1)
        self.assertEqual({t['state'] for t in result['tasks']},{'CANCELLED'})

    def test_live_account_revocation_stops_execution(self):
        from sqlalchemy import insert
        from platform_api.database import users
        import uuid
        user_id=str(uuid.uuid4())
        with self.engine.begin() as db:
            db.execute(insert(users).values(id=user_id,username=user_id,tenant_id='alpha',role='manager',
                password_hash='not-a-login-fixture',active=True))
        actor=Identity(tenant_id='alpha',role='manager',subject=user_id)
        service=self.app.state.operations
        from platform_api.agents.schemas import GoalInput
        op=service.create(actor,GoalInput(goal=DEMO_GOAL,as_of=date(2026,11,14)))
        engine=self.engine
        class Revoking(ToolGateway):
            def execute(self,task,*args):
                result=super().execute(task,*args)
                with engine.begin() as db:db.execute(update(users).where(users.c.id==user_id).values(active=False))
                return result
        service.gateway_factory=Revoking
        result=service.execute(actor,op['id'],PlanConfirmation(version=1,plan_hash=op['plan_hash']))
        self.assertEqual(result['state'],'FAILED');self.assertIsNone(result['result'])
        self.assertEqual(sum(t['attempts'] for t in result['tasks']),1)

    def test_real_openai_sdk_request_and_parser_contract(self):
        import json,httpx
        from openai import OpenAI
        captured={}
        def handler(request):
            captured.update(json.loads(request.content))
            return httpx.Response(200,json={'id':'resp_fixture','object':'response','created_at':0,
                'status':'completed','model':'fixture-model','output':[{'id':'msg_fixture','type':'message',
                'role':'assistant','status':'completed','content':[{'type':'output_text',
                'text':json.dumps(self.proposal()),'annotations':[]}]}],
                'usage':{'input_tokens':20,'output_tokens':30,'total_tokens':50}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
            client=OpenAI(api_key='test-not-a-real-key',http_client=transport,max_retries=0)
            result=OpenAIPlanner(client=client,model='fixture-model').propose(DEMO_GOAL,date(2026,11,14))
        self.assertEqual(result.proposal.disposition,'ready')
        self.assertEqual(result.usage['output_tokens'],30)
        self.assertEqual(captured['text']['format']['type'],'json_schema')
        self.assertTrue(captured['text']['format']['strict'])
        self.assertNotIn('tools',captured)


    def test_known_phase_one_bootstrap_upgrades_without_losing_users(self):
        from sqlalchemy import MetaData,insert
        from platform_api.database import metadata,users
        from platform_api.store import initialize
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'existing.db');engine=engine_for(path);baseline=MetaData()
            for name in ('tenants','suppliers','projects','purchase_orders','contracts','users','sessions','audit_events'):
                metadata.tables[name].to_metadata(baseline)
            baseline.create_all(engine);seed(engine)
            with engine.begin() as db:
                db.execute(insert(users).values(id='legacy-user',username='legacy-user',tenant_id='alpha',
                    role='manager',password_hash='existing-hash-preserved',active=True))
            initialize(path)
            with engine.connect() as db:
                self.assertEqual(db.execute(select(users.c.password_hash).where(users.c.id=='legacy-user')).scalar_one(),'existing-hash-preserved')
                self.assertEqual(db.execute(select(agent_tasks.c.task_id)).all(),[])
            engine.dispose()

    def test_unknown_bootstrap_schema_is_not_silently_adopted(self):
        from sqlalchemy import MetaData,Table,Column,String
        from platform_api.database import metadata
        from platform_api.store import initialize
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'unknown.db');engine=engine_for(path);baseline=MetaData()
            for name in ('tenants','suppliers','projects','purchase_orders','contracts','users','sessions','audit_events'):
                metadata.tables[name].to_metadata(baseline)
            Table('unknown_customer_data',baseline,Column('id',String,primary_key=True))
            baseline.create_all(engine)
            with self.assertRaisesRegex(RuntimeError,'manual migration required'):initialize(path)
            from sqlalchemy import inspect
            self.assertIn('unknown_customer_data',inspect(engine).get_table_names())
            engine.dispose()


    def test_adapter_connection_failure_is_a_retryable_read(self):
        from sqlalchemy.exc import OperationalError
        gateway=ToolGateway(self.path,'alpha')
        def unavailable():raise OperationalError('read',{},Exception('private-db-details'))
        gateway.erp.orders=unavailable
        task=Proposal.model_validate(self.proposal()).tasks[0]
        with self.assertRaises(TransientToolError) as error:gateway.execute(task,{},date(2026,11,14))
        self.assertNotIn('private-db-details',str(error.exception))

if __name__=='__main__':unittest.main()
