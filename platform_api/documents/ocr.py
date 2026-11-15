"""Explicit, bounded local OCR in a credential-stripped process tree."""
import json,os,shutil,subprocess,sys,threading
from pathlib import Path
import psutil
from .schemas import DocumentError,MAX_PDF_BYTES

def engine_path():
    configured=os.getenv('OCR_TESSERACT_PATH')
    return shutil.which(configured or 'tesseract')

def available():return engine_path() is not None

def kill_tree(process):
    try:
        parent=psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            try:child.kill()
            except psutil.NoSuchProcess:pass
        parent.kill()
    except psutil.NoSuchProcess:pass

def extract_ocr(data):
    engine=engine_path()
    if not engine:raise DocumentError(503,'Local OCR engine is unavailable; configure Tesseract with Arabic and English language data')
    if len(data)>MAX_PDF_BYTES or not data.startswith(b'%PDF-'):raise DocumentError(422,'A bounded PDF is required')
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','LANG','LC_ALL'}}
    env['OMP_THREAD_LIMIT']='1'
    process=subprocess.Popen([sys.executable,'-m','platform_api.documents.ocr_worker',engine],
        cwd=Path(__file__).resolve().parents[2],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    finished=threading.Event();exceeded=threading.Event()
    def monitor():
        try:
            parent=psutil.Process(process.pid)
            while not finished.wait(.05):
                usage=parent.memory_info().rss
                for child in parent.children(recursive=True):
                    try:usage+=child.memory_info().rss
                    except psutil.NoSuchProcess:continue
                if usage>384*1024*1024:
                    exceeded.set();kill_tree(process);return
        except psutil.NoSuchProcess:pass
        except psutil.AccessDenied:exceeded.set();kill_tree(process)
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    try:
        output,_=process.communicate(data,timeout=65)
        if exceeded.is_set() or process.returncode:raise DocumentError(422,'OCR failed within resource limits')
        if len(output)>2*1024*1024:raise DocumentError(422,'OCR output exceeds the limit')
        result=json.loads(output)
        if result.get('error'):raise DocumentError(422,'OCR could not extract supported evidence: '+result['error'])
        if not isinstance(result.get('pages'),list) or not isinstance(result.get('ocr'),dict):raise ValueError()
        return result
    except subprocess.TimeoutExpired:
        kill_tree(process);process.communicate();raise DocumentError(422,'OCR exceeded its time limit') from None
    except (ValueError,KeyError):raise DocumentError(422,'OCR output is invalid') from None
    finally:finished.set();thread.join(timeout=1)
