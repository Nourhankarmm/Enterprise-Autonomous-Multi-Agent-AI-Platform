"""Optional real Temporal history/replay and stopped-worker document-wait acceptance."""
import asyncio,os,tempfile,uuid,unittest,subprocess,sys,json
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import patch
from sqlalchemy import insert,select,func
from temporalio.client import Client
from temporalio.worker import Worker,Replayer
from platform_api.store import initialize
from platform_api.database import engine_for,users,sandbox_messages,action_executions
from platform_api.agents.service import OperationService
from platform_api.agents.planner import DemoPlanner
from platform_api.agents.schemas import GoalInput,DEMO_GOAL
from platform_api.agents.schemas import canonical_hash
from platform_api.workflows.service import WorkflowService
from platform_api.workflows.schemas import StartInput,DraftInput,DecisionInput,CommandInput
from platform_api.workflows.worker import Activities
from platform_api.workflows.temporal import ProcurementWorkflow
from platform_api.workflows.adapter import UnknownOutcome
from platform_api.documents.schemas import DocumentInput,ReviewInput

@unittest.skipUnless(os.getenv('TEST_TEMPORAL_ADDRESS'),'Set TEST_TEMPORAL_ADDRESS to run actual Temporal acceptance')
class TemporalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_replay_and_worker_restart_with_document_arrival(self):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as cleanup:
            path=str(Path(directory)/'durable.db');initialize(path);engine=engine_for(path)
            cleanup.callback(engine.dispose)
            owner=SimpleNamespace(tenant_id='alpha',subject='owner',role='manager')
            approver=SimpleNamespace(tenant_id='alpha',subject='reviewer',role='approver')
            with engine.begin() as db:
                for actor in (owner,approver):db.execute(insert(users).values(id=actor.subject,username=actor.subject,
                    tenant_id='alpha',role=actor.role,password_hash='not-a-login',active=True))
            service=WorkflowService(engine,path)
            op=await asyncio.to_thread(OperationService(engine,path,DemoPlanner()).create,owner,
                GoalInput(goal=DEMO_GOAL,as_of=date(2026,11,14)))
            run_id=service.reserve(owner,op['id'],StartInput(version=op['plan_version'],plan_hash=op['plan_hash']))
            queue='acceptance-'+uuid.uuid4().hex
            client=await Client.connect(os.environ['TEST_TEMPORAL_ADDRESS'])
            async def wait(state,target=None):
                for _ in range(150):
                    row=await asyncio.to_thread(service.get,owner,target or run_id)
                    if row['state']==state:return row
                    if row['state']=='FAILED':self.fail(str(row))
                    await asyncio.sleep(.2)
                self.fail('Timed out waiting for '+state)
            def worker():return Worker(client,task_queue=queue,workflows=[ProcurementWorkflow],activities=[Activities(service).step])
            async with worker():
                handle=await client.start_workflow(ProcurementWorkflow.run,{'tenant_id':'alpha','id':run_id,'poll_seconds':1,'rotation_limit':2},
                    id=run_id,task_queue=queue)
                await wait('WAITING_DOCUMENT')
                # Exercise the durable expiry branch with a persisted past deadline.
                expiring_op=await asyncio.to_thread(OperationService(engine,path,DemoPlanner()).create,owner,
                    GoalInput(goal=DEMO_GOAL,as_of=date(2026,11,14)))
                expiring_id=service.reserve(owner,expiring_op['id'],StartInput(version=1,plan_hash=expiring_op['plan_hash']))
                partial=await asyncio.to_thread(service.analyze,'alpha',expiring_id,0)
                service.project('alpha',expiring_id,partial['state'],snapshot_id=partial['snapshot_id'],result=partial['result'])
                expiry_handle=await client.start_workflow(ProcurementWorkflow.run,{'tenant_id':'alpha','id':expiring_id,
                    'poll_seconds':1,'continuation':{'cursor':0,'cycle':0,'state':'WAITING_DOCUMENT',
                    'expiry':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),'analysis':partial}},
                    id=expiring_id,task_queue=queue)
                self.assertEqual(await asyncio.wait_for(expiry_handle.result(),25),'PARTIALLY_COMPLETED')
                self.assertEqual(service.get(owner,expiring_id)['error_code'],'DOCUMENT_WAIT_EXPIRED')
                for _ in range(100):
                    if (await client.get_workflow_handle(run_id).describe()).run_id!=handle.first_execution_run_id:break
                    await asyncio.sleep(.2)
                else:self.fail('Waiting history did not continue as new')
            # No worker exists while evidence and refresh command are committed.
            pdf=Path('evals/datasets/contracts/supplier-s3.pdf').read_bytes()
            doc=await asyncio.to_thread(service.docs.upload,owner,DocumentInput(title='S3 evidence',supplier_id='S3',contract_ref='S3-PDF',version=1),pdf)
            service.docs.review(owner,doc['id'],ReviewInput(revision=doc['revision'],checksum=doc['checksum'],extraction_hash=doc['extraction']['extraction_hash']))
            service.command(owner,run_id,CommandInput(request_id=uuid.uuid4().hex),'refresh')
            async with worker():
                row=await wait('WAITING_APPROVAL')
                draft=service.draft(owner,run_id,DraftInput(request_id=uuid.uuid4().hex,supplier_id='S1',subject='Follow-up',body='Please confirm delivery.'))
            # Approval is durable even when the worker is stopped.
            service.decision(approver,draft['id'],DecisionInput(request_id=uuid.uuid4().hex,action_hash=draft['action_hash'],decision='approve'))
            async with worker():
                self.assertEqual(await asyncio.wait_for(handle.result(),40),'COMPLETED')
                row=service.get(owner,run_id);self.assertEqual(row['execution']['state'],'SUCCEEDED')
                with engine.connect() as db:self.assertEqual(db.execute(select(func.count()).select_from(sandbox_messages)).scalar_one(),1)
                # Cancellation must retain reconciliation until an unknown provider outcome is resolved.
                next_op=await asyncio.to_thread(OperationService(engine,path,DemoPlanner()).create,owner,
                    GoalInput(goal=DEMO_GOAL,as_of=date(2026,11,14)))
                next_id=service.reserve(owner,next_op['id'],StartInput(version=1,plan_hash=next_op['plan_hash']))
                second=await client.start_workflow(ProcurementWorkflow.run,{'tenant_id':'alpha','id':next_id,'poll_seconds':1},id=next_id,task_queue=queue)
                await wait('WAITING_APPROVAL',next_id)
                action=service.draft(owner,next_id,DraftInput(request_id=uuid.uuid4().hex,supplier_id='S1',subject='Follow-up',body='Please confirm delivery.'))
                real_send=service.adapter.send
                with patch.object(service.adapter,'send',side_effect=UnknownOutcome()):
                    service.decision(approver,action['id'],DecisionInput(request_id=uuid.uuid4().hex,action_hash=action['action_hash'],decision='approve'))
                    await wait('RECONCILING',next_id)
                    service.command(owner,next_id,CommandInput(request_id=uuid.uuid4().hex),'cancel')
                    await wait('CANCEL_REQUESTED',next_id)
                    key=canonical_hash({'tenant':'alpha','logical_action':next_id+'/followup'})
                    real_send(key,action['payload'],action['action_hash'])
                    self.assertEqual(await asyncio.wait_for(second.result(),25),'CANCELLED')
                cancelled=service.get(owner,next_id)
                self.assertEqual(cancelled['result']['external_actions'],1)
                self.assertTrue(cancelled['execution']['receipt']['accepted'])
            history=await handle.fetch_history()
            await Replayer(workflows=[ProcurementWorkflow]).replay_workflow(history)
            latest=await client.get_workflow_handle(run_id).fetch_history()
            await Replayer(workflows=[ProcurementWorkflow]).replay_workflow(latest)
            # Kill a real separate worker exactly after provider acceptance and before ledger projection.
            crash_op=await asyncio.to_thread(OperationService(engine,path,DemoPlanner()).create,owner,
                GoalInput(goal=DEMO_GOAL,as_of=date(2026,11,14)))
            crash_id=service.reserve(owner,crash_op['id'],StartInput(version=1,plan_hash=crash_op['plan_hash']))
            crash_queue='crash-'+uuid.uuid4().hex
            crash_handle=await client.start_workflow(ProcurementWorkflow.run,{'tenant_id':'alpha','id':crash_id,'poll_seconds':1},
                id=crash_id,task_queue=crash_queue)
            env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','LANG','LC_ALL'}}
            env.update(DATABASE_PATH=path,TEST_TEMPORAL_ADDRESS=os.environ['TEST_TEMPORAL_ADDRESS'],TEMPORAL_TASK_QUEUE=crash_queue)
            process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('crash_worker.py'))],env=env,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            try:
                await wait('WAITING_APPROVAL',crash_id)
                action=service.draft(owner,crash_id,DraftInput(request_id=uuid.uuid4().hex,supplier_id='S1',subject='Crash recovery',body='Please confirm delivery.'))
                decision=service.decision(approver,action['id'],DecisionInput(request_id=uuid.uuid4().hex,
                    action_hash=action['action_hash'],decision='approve'))
                self.assertEqual(await asyncio.wait_for(asyncio.to_thread(process.wait),40),73)
                key=canonical_hash({'tenant':'alpha','logical_action':crash_id+'/followup'})
                with engine.connect() as db:
                    self.assertEqual(db.execute(select(func.count()).select_from(sandbox_messages).where(sandbox_messages.c.idempotency_key==key)).scalar_one(),1)
                    self.assertEqual(db.execute(select(action_executions.c.state).where(action_executions.c.workflow_id==crash_id)).scalar_one(),'SENDING')
                async with Worker(client,task_queue=crash_queue,workflows=[ProcurementWorkflow],activities=[Activities(service).step]):
                    self.assertEqual(await asyncio.wait_for(crash_handle.result(),90),'COMPLETED')
                recovered=service.get(owner,crash_id);self.assertEqual(recovered['execution']['state'],'SUCCEEDED')
                self.assertEqual(service.dispatch('alpha',crash_id,decision['id'])['receipt'],recovered['execution']['receipt'])
                with engine.connect() as db:
                    self.assertEqual(db.execute(select(func.count()).select_from(sandbox_messages).where(sandbox_messages.c.idempotency_key==key)).scalar_one(),1)
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=10)
            if os.getenv('TEMPORAL_PROOF_OUTPUT'):
                target=Path(os.environ['TEMPORAL_PROOF_OUTPUT']);target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(json.dumps({'verified_at':datetime.now(timezone.utc).isoformat(),
                    'source_commit':os.getenv('EVALUATION_SOURCE_SHA'),'actual_temporal_service':True,
                    'document_and_approval_wait_worker_restart':True,'continued_history_replayed':True,
                    'expired_document_deadline_tested':True,'unknown_send_cancellation_reconciled':True,
                    'real_worker_crash_after_acceptance_exit_code':73,'ledger_before_recovery':'SENDING',
                    'recovered_state':'COMPLETED','provider_messages_for_crash_action':1,
                    'automatic_duplicate_messages_for_crash_action':0,'real_emails_sent':0},indent=2)+'\n',encoding='utf-8')
            engine.dispose()
