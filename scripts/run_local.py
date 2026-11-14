"""Start the local demo, optionally prompting for a live planner API key in memory."""
import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--model',help='OpenAI model ID available to your account; enables the live planner')
parser.add_argument('--port',type=int,default=8001)
args=parser.parse_args()
env=os.environ.copy()
if args.model:
    env['PLANNER_PROVIDER']='openai'
    env['OPENAI_MODEL']=args.model
    env['OPENAI_API_KEY']=env.get('OPENAI_API_KEY') or getpass.getpass('OpenAI API key (hidden, not saved): ')
    if not env['OPENAI_API_KEY']:parser.error('An API key is required for the live planner')
else:
    env['PLANNER_PROVIDER']='demo'
print(f"Starting {'live OpenAI' if args.model else 'offline demo'} planner at http://127.0.0.1:{args.port}")
try:
    raise SystemExit(subprocess.call([sys.executable,'-m','uvicorn','platform_api.main:app','--host','127.0.0.1','--port',str(args.port)],env=env,cwd=Path(__file__).resolve().parents[1]))
except KeyboardInterrupt:
    pass
