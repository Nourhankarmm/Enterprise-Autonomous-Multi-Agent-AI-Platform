"""Local sandbox launcher. Start the official Temporal CLI separately first."""
import argparse,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from platform_api.store import initialize

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8002)
    parser.add_argument('--queue',default='procurement-v1')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    env=os.environ.copy()
    env['TEMPORAL_ADDRESS']=env.get('TEMPORAL_ADDRESS','127.0.0.1:7233')
    env['PLANNER_PROVIDER']='demo'
    env['TEMPORAL_TASK_QUEUE']=args.queue
    env['DATABASE_PATH']=str(root/'.data/phase1.db')
    initialize(env.get('DATABASE_URL') or env['DATABASE_PATH'])
    worker=subprocess.Popen([sys.executable,'-m','platform_api.workflows.worker'],cwd=root,env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    try:
        subprocess.call([sys.executable,'-m','uvicorn','platform_api.main:app','--host','127.0.0.1','--port',str(args.port)],cwd=root,env=env)
    finally:worker.terminate();worker.wait(timeout=15)

if __name__=='__main__':main()
