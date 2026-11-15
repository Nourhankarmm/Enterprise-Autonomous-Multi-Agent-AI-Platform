"""TEST ONLY: crash the worker process after sandbox acceptance, before ledger projection."""
import asyncio,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd() if (Path.cwd()/'platform_api').is_dir() else Path(__file__).resolve().parents[1]))
from temporalio.client import Client
from temporalio.worker import Worker
from platform_api.database import engine_for
from platform_api.workflows.service import WorkflowService
from platform_api.workflows.worker import Activities
from platform_api.workflows.temporal import ProcurementWorkflow

class CrashAfterAcceptance(WorkflowService):
    def _accepted(self,tenant,run_id,receipt):
        os._exit(73) # Provider acceptance is committed in a separate transaction already.

async def main():
    path=os.environ['DATABASE_PATH'];engine=engine_for(path)
    client=await Client.connect(os.environ['TEST_TEMPORAL_ADDRESS'])
    async with Worker(client,task_queue=os.environ['TEMPORAL_TASK_QUEUE'],workflows=[ProcurementWorkflow],
        activities=[Activities(CrashAfterAcceptance(engine,path)).step]):
        await asyncio.Event().wait()
if __name__=='__main__':asyncio.run(main())
