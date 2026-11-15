"""Run with python -m platform_api.workflows.worker (separate process from API)."""
import asyncio,os
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.exceptions import ApplicationError
from ..database import engine_for
from .service import WorkflowService
from .schemas import WorkflowError
from .temporal import ProcurementWorkflow

class Activities:
    def __init__(self,service):self.service=service

    @activity.defn(name='platform.step')
    async def step(self,request:dict):
        method=request['method'];args=request['args']
        if method not in {'project','analyze','inbox','dispatch'}:raise ApplicationError('Unknown activity',non_retryable=True)
        async def invoke():
            if method=='project':
                tenant,run_id,state,fields=args
                return await asyncio.to_thread(self.service.project,tenant,run_id,state,**fields)
            if method=='dispatch':
                tenant,run_id,decision_id=args
                # Rejection is also rechecked against the latest immutable proposal.
                from sqlalchemy import select
                from ..database import approvals,workflow_actions
                with self.service.engine.connect() as db:
                    decision=db.execute(select(approvals).where(approvals.c.tenant_id==tenant,approvals.c.id==decision_id)).mappings().first()
                    latest=self.service._action(db,tenant,run_id)
                if decision and latest and decision['action_id']==latest['id'] and decision['decision']=='reject':return {'state':'REJECTED'}
            return await asyncio.to_thread(getattr(self.service,method),*args)
        task=asyncio.create_task(invoke())
        while not task.done():
            await asyncio.wait({task},timeout=5)
            activity.heartbeat()
        try:return task.result()
        except WorkflowError as error:
            raise ApplicationError(error.detail,non_retryable=error.status not in {409,503}) from None

async def main():
    path=os.getenv('DATABASE_URL') or os.getenv('DATABASE_PATH','.data/phase1.db')
    engine=engine_for(path)
    client=await Client.connect(os.getenv('TEMPORAL_ADDRESS','127.0.0.1:7233'),namespace=os.getenv('TEMPORAL_NAMESPACE','default'))
    try:
        async with Worker(client,task_queue='procurement-v1',workflows=[ProcurementWorkflow],
            activities=[Activities(WorkflowService(engine,path)).step]):
            await asyncio.Event().wait()
    finally:engine.dispose()

if __name__=='__main__':asyncio.run(main())
