"""Optional real Temporal history/replay and stopped-worker document-wait acceptance."""
import asyncio,os,tempfile,uuid,unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack
from sqlalchemy import insert,select,func
from temporalio.client import Client
from temporalio.worker import Worker,Replayer
from platform_api.store import initialize
from platform_api.database import engine_for,users,sandbox_messages
from platform_api.agents.service import OperationService
from platform_api.agents.planner import DemoPlanner
from platform_api.agents.schemas import GoalInput,DEMO_GOAL
from platform_api.workflows.service import WorkflowService
from platform_api.workflows.schemas import StartInput,DraftInput,DecisionInput,CommandInput
from platform_api.workflows.worker import Activities
from platform_api.workflows.temporal import ProcurementWorkflow
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
            async def wait(state):
                for _ in range(150):
                    row=await asyncio.to_thread(service.get,owner,run_id)
                    if row['state']==state:return row
                    if row['state']=='FAILED':self.fail(str(row))
                    await asyncio.sleep(.2)
                self.fail('Timed out waiting for '+state)
            def worker():return Worker(client,task_queue=queue,workflows=[ProcurementWorkflow],activities=[Activities(service).step])
            async with worker():
                handle=await client.start_workflow(ProcurementWorkflow.run,{'tenant_id':'alpha','id':run_id,'poll_seconds':1,'rotation_limit':2},
                    id=run_id,task_queue=queue)
                await wait('WAITING_DOCUMENT')
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
            history=await handle.fetch_history()
            await Replayer(workflows=[ProcurementWorkflow]).replay_workflow(history)
            latest=await client.get_workflow_handle(run_id).fetch_history()
            await Replayer(workflows=[ProcurementWorkflow]).replay_workflow(latest)
            engine.dispose()
