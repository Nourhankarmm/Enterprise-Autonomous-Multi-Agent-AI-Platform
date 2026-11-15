"""Run ordinary regressions and save a privacy-minimal machine-readable result."""
import argparse,json,platform,subprocess,sys,time,unittest
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

class RecordedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.records=[];self.started={};self.subtest_errors={}
    def startTest(self,test):self.started[test.id()]=time.perf_counter();super().startTest(test)
    def record(self,test,status,error=None):
        self.records.append({'id':test.id(),'status':status,'duration_ms':round((time.perf_counter()-self.started[test.id()])*1000,3),
            **({'error_type':error[0].__name__} if error else {})})
    def addSuccess(self,test):self.record(test,'passed');super().addSuccess(test)
    def addSkip(self,test,reason):self.record(test,'skipped');super().addSkip(test,reason)
    def addFailure(self,test,error):self.record(test,'failed',error);super().addFailure(test,error)
    def addError(self,test,error):self.record(test,'error',error);super().addError(test,error)
    def addSubTest(self,test,subtest,error):
        if error:self.subtest_errors[test.id()]=error
        super().addSubTest(test,subtest,error)
    def stopTest(self,test):
        if test.id() in self.subtest_errors and not any(r['id']==test.id() for r in self.records):
            self.record(test,'failed',self.subtest_errors[test.id()])
        super().stopTest(test)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='.data/phase6-regressions.json');args=parser.parse_args()
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
    started=time.perf_counter();suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(verbosity=2,resultclass=RecordedResult).run(suite)
    report={'schema_version':1,'executed_at':datetime.now(timezone.utc).isoformat(),'commit':commit,
        'source_dirty_at_start':dirty,'python':platform.python_version(),'database':'PostgreSQL if TEST_DATABASE_URL is configured; otherwise isolated SQLite fixtures',
        'duration_seconds':round(time.perf_counter()-started,3),'tests_run':result.testsRun,
        'passed':sum(r['status']=='passed' for r in result.records),'failed':len(result.failures),
        'errors':len(result.errors),'skipped':len(result.skipped),'status':'PASS' if result.wasSuccessful() else 'FAIL',
        'tests':result.records,'notes':['Skipped Temporal integration is executed separately against an actual service in Compose.',
            'Test names and assertion outcomes are recorded; no exception messages, credentials or provider bodies are retained in this JSON.']}
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
if __name__=='__main__':main()
