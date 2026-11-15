"""Single-use parser child. No network/key configuration is inherited from the API."""
import io,json,sys
from .schemas import MAX_PDF_BYTES,MAX_PAGES,MAX_TEXT

def main():
    if sys.platform!='win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(6,6))
    data=sys.stdin.buffer.read(MAX_PDF_BYTES+1)
    try:
        from pypdf import PdfReader
        import pypdf.filters
        if hasattr(pypdf.filters,'ZLIB_MAX_OUTPUT_LENGTH'):
            pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH=16*1024*1024
        if len(data)>MAX_PDF_BYTES or not data.startswith(b'%PDF-'):raise ValueError()
        reader=PdfReader(io.BytesIO(data),strict=True)
        if reader.is_encrypted:
            print(json.dumps({'error':'ENCRYPTED_PDF'}));return
        if not 1<=len(reader.pages)<=MAX_PAGES:
            print(json.dumps({'error':'PAGE_LIMIT'}));return
        pages=[];total=0
        for page in reader.pages:
            text=page.extract_text() or ''
            total+=len(text)
            if total>MAX_TEXT:
                print(json.dumps({'error':'TEXT_LIMIT'}));return
            pages.append(text)
        print(json.dumps({'pages':pages},ensure_ascii=True))
    except Exception:
        print(json.dumps({'error':'INVALID_PDF'}))

if __name__=='__main__':main()
