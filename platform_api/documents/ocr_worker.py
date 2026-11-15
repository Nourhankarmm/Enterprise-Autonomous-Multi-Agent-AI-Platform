"""TESTABLE local raster OCR; no API/database/model credentials are inherited."""
import csv,io,json,math,subprocess,sys,tempfile
from pathlib import Path
from .schemas import MAX_PDF_BYTES,MAX_TEXT

def parse_tsv(text):
    lines={};confidences=[];words=0
    for row in csv.DictReader(io.StringIO(text),delimiter='\t'):
        if row.get('level')!='5' or not row.get('text','').strip():continue
        confidence=float(row['conf'])
        if not math.isfinite(confidence) or not 0<=confidence<=100:raise ValueError()
        key=tuple(row[k] for k in ('block_num','par_num','line_num'))
        lines.setdefault(key,[]).append(row['text'].strip());confidences.append(confidence);words+=1
    return '\n'.join(' '.join(v) for v in lines.values()),{
        'word_count':words,'mean_confidence':round(sum(confidences)/words,2) if words else 0,
        'min_confidence':round(min(confidences),2) if words else 0}

def run(data,engine):
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(data),strict=True)
    if reader.is_encrypted:raise ValueError('ENCRYPTED_PDF')
    if not 1<=len(reader.pages)<=5:raise ValueError('OCR_FIVE_PAGE_LIMIT')
    version=subprocess.run([engine,'--version'],capture_output=True,timeout=5,check=True).stdout.decode('utf-8').splitlines()[0][:100]
    pages=[];metadata=[];pixels=0
    with tempfile.TemporaryDirectory() as directory,pdfium.PdfDocument(data) as document:
        for index,page in enumerate(document):
            try:
                # Rasterize every page: hidden/searchable PDF text is not trusted as the scan transcript.
                width,height=page.get_size();scale=200/72
                count=math.ceil(width*scale)*math.ceil(height*scale);pixels+=count
                if count>8_000_000 or pixels>24_000_000:raise ValueError('OCR_PIXEL_LIMIT')
                bitmap=page.render(scale=scale)
                try:
                    image=bitmap.to_pil();source=Path(directory)/'page.png'
                    try:image.save(source)
                    finally:image.close()
                finally:bitmap.close()
                target=Path(directory)/'result'
                subprocess.run([engine,str(source),str(target),'-l','ara+eng','--psm','6','tsv'],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=12,check=True)
                output=target.with_suffix('.tsv')
                if output.stat().st_size>1024*1024:raise ValueError('OCR_TEXT_LIMIT')
                text,stats=parse_tsv(output.read_text(encoding='utf-8'))
                pages.append(text);metadata.append({'page':index+1,**stats})
                if sum(map(len,pages))>MAX_TEXT:raise ValueError('OCR_TEXT_LIMIT')
            finally:page.close()
    return {'pages':pages,'ocr':{'engine':'tesseract','engine_version':version,'languages':'ara+eng','dpi':200,'pages':metadata}}

def main():
    if sys.platform!='win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(50,50))
        resource.setrlimit(resource.RLIMIT_FSIZE,(2*1024*1024,2*1024*1024))
    data=sys.stdin.buffer.read(MAX_PDF_BYTES+1)
    try:
        if len(data)>MAX_PDF_BYTES or not data.startswith(b'%PDF-'):raise ValueError('INVALID_PDF')
        result=run(data,sys.argv[1])
    except ValueError as error:
        reason=str(error)
        result={'error':reason if reason in {'ENCRYPTED_PDF','OCR_FIVE_PAGE_LIMIT','OCR_PIXEL_LIMIT','OCR_TEXT_LIMIT','INVALID_PDF'} else 'INVALID_OCR_INPUT'}
    except Exception:result={'error':'ENGINE_FAILURE_OR_UNSUPPORTED_SCAN'}
    print(json.dumps(result,ensure_ascii=True))
if __name__=='__main__':main()
