"""Bounded child-process PDF extraction. This is isolation, not a security sandbox."""
import json,os,subprocess,sys,threading
from pathlib import Path
import psutil
from .schemas import DocumentError,MAX_PDF_BYTES


def extract_pages(data):
    if len(data)>MAX_PDF_BYTES:raise DocumentError(413,'PDF exceeds the 2 MiB limit')
    if not data.startswith(b'%PDF-'):raise DocumentError(422,'A valid PDF file is required')
    root=Path(__file__).resolve().parents[2]
    env={key:value for key,value in os.environ.items() if key in {'SYSTEMROOT','SystemRoot','WINDIR','TEMP','TMP','PATH','LANG','LC_ALL'}}
    process=subprocess.Popen([sys.executable,'-m','platform_api.documents.extract_worker'],cwd=root,env=env,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    finished=threading.Event();memory_exceeded=threading.Event()
    def monitor():
        try:
            child=psutil.Process(process.pid)
            while not finished.wait(.05):
                if child.memory_info().rss>256*1024*1024:
                    memory_exceeded.set();process.kill();return
        except psutil.NoSuchProcess:return
        except psutil.AccessDenied:
            memory_exceeded.set();process.kill();return
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    try:
        output,_=process.communicate(data,timeout=8)
        if memory_exceeded.is_set():raise DocumentError(422,'PDF extraction exceeded the memory limit')
        if process.returncode:raise DocumentError(422,'PDF extraction failed within resource limits')
        result=json.loads(output)
        if result.get('error'):raise DocumentError(422,'PDF could not be processed: '+result['error'])
        return result['pages']
    except subprocess.TimeoutExpired:
        process.kill();process.communicate()
        raise DocumentError(422,'PDF extraction exceeded the eight-second limit') from None
    except (ValueError,KeyError):raise DocumentError(422,'PDF extraction failed') from None
    finally:
        finished.set();thread.join(timeout=1)
