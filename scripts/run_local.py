"""Start the local demo, optionally prompting for a live planner API key in memory."""
import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--provider',choices=['demo','openai','gemini'],help='Planner provider; defaults to demo, or OpenAI if --model is supplied')
parser.add_argument('--model',help='Optional model ID; Gemini defaults to gemini-3.1-flash-lite')
parser.add_argument('--port',type=int,default=8001)
args=parser.parse_args()
env=os.environ.copy()
provider=args.provider or ('openai' if args.model else 'demo')
env['PLANNER_PROVIDER']=provider
if provider=='openai':
    if not args.model:parser.error('--model is required for OpenAI')
    env['OPENAI_MODEL']=args.model
    env['OPENAI_API_KEY']=env.get('OPENAI_API_KEY') or getpass.getpass('OpenAI API key (hidden, not saved): ')
    if not env['OPENAI_API_KEY']:parser.error('An API key is required for the live planner')
elif provider=='gemini':
    env['GEMINI_MODEL']=args.model or 'gemini-3.1-flash-lite'
    env['GEMINI_API_KEY']=env.get('GEMINI_API_KEY') or getpass.getpass('Gemini API key (hidden, not saved): ')
    if not env['GEMINI_API_KEY']:parser.error('A Gemini API key is required')
print(f"Starting {provider} planner at http://127.0.0.1:{args.port}")

try:
    raise SystemExit(subprocess.call([sys.executable,'-m','uvicorn','platform_api.main:app','--host','127.0.0.1','--port',str(args.port)],env=env,cwd=Path(__file__).resolve().parents[1]))
except KeyboardInterrupt:
    pass
