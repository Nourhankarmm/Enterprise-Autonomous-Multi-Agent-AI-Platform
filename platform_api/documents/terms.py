"""Conservative explicit-clause extraction; unsupported prose always needs review."""
import hashlib,re
from datetime import date
from ..agents.schemas import canonical_hash

DIGITS=str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789')
RULES=[
 ('grace',r'^\s*Grace\s+period\s*:\s*(\d{1,3})\s+(calendar|business)\s+days?\.?\s*$'),
 ('grace_ar',r'^\s*فترة\s+السماح\s*:\s*(\d{1,3})\s+(?:أيام|ايام|يوماً|يوم)\s+(تقويمية|عمل)\.?\s*$'),
 ('no_grace',r'^\s*No\s+grace\s+period\.?\s*$'),
 ('no_grace_ar',r'^\s*لا\s+توجد\s+فترة\s+سماح\.?\s*$'),
 ('due',r'^\s*Delivery\s+due\s+date\s*:\s*(\d{4}-\d{2}-\d{2})\.?\s*$'),
 ('due_ar',r'^\s*تاريخ\s+التسليم\s*:\s*(\d{4}-\d{2}-\d{2})\.?\s*$'),
 ('supplier',r'^\s*Supplier\s+ID\s*:\s*([A-Za-z0-9_-]+)\s*$'),
 ('supplier_ar',r'^\s*رقم\s+المورد\s*:\s*([A-Za-z0-9_-]+)\s*$')]


def text_hash(text):return hashlib.sha256(text.encode()).hexdigest()


def extract_terms(pages,supplier_id,ocr=None):
    facts=[]
    for page_number,text in enumerate(pages,1):
        normalized=text.translate(DIGITS)
        for kind,pattern in RULES:
            for match in re.finditer(pattern,normalized,re.MULTILINE|re.IGNORECASE):
                raw=text[match.start():match.end()]
                quote=raw.strip();start=match.start()+len(raw)-len(raw.lstrip())
                fact={'page':page_number,'start':start,'end':start+len(quote),'quote':quote,'text_hash':text_hash(text)}
                if kind.startswith('no_grace'):fact.update(field='grace_days',value=0,day_basis='calendar')
                elif kind.startswith('grace'):
                    fact.update(field='grace_days',value=int(match.group(1)),
                        day_basis='calendar' if match.group(2).lower() in {'calendar','تقويمية'} else 'business')
                elif kind.startswith('due'):
                    try:value=date.fromisoformat(match.group(1)).isoformat()
                    except ValueError:value=None
                    fact.update(field='due_date',value=value)
                else:fact.update(field='supplier_id',value=match.group(1))
                facts.append(fact)
    grace=[f for f in facts if f['field']=='grace_days']
    dues=[f for f in facts if f['field']=='due_date']
    suppliers=[f for f in facts if f['field']=='supplier_id']
    values={(f['value'],f['day_basis']) for f in grace}
    if any(not t.strip() for t in pages):state='NEEDS_OCR';reason='NO_EXTRACTABLE_TEXT'
    elif any(f['value']!=supplier_id for f in suppliers):state='CONFLICT';reason='SUPPLIER_LINK_CONFLICT'
    elif len(values)>1 or len({f['value'] for f in dues})>1:state='CONFLICT';reason='CONTRADICTORY_CLAUSES'
    elif any(f['value'] is None for f in dues):state='NEEDS_REVIEW';reason='INVALID_DATE'
    elif not grace:state='NEEDS_REVIEW';reason='GRACE_NOT_EXPLICIT'
    elif grace[0]['day_basis']!='calendar':state='NEEDS_REVIEW';reason='BUSINESS_CALENDAR_REQUIRED'
    elif grace[0]['value']>365:state='NEEDS_REVIEW';reason='GRACE_OUTSIDE_DEMO_POLICY'
    else:state='EXTRACTED';reason='EXPLICIT_CLAUSES_PENDING_REVIEW'
    result={'state':state,'reason':reason,'facts':facts,'parser_version':'explicit-clauses-v1',
        'grace_days':grace[0]['value'] if len(values)==1 else None,
        'day_basis':grace[0]['day_basis'] if len(values)==1 else None,
        'due_date':dues[0]['value'] if dues and len({f['value'] for f in dues})==1 else None,
        'supplier_link':'EXPLICIT' if suppliers else 'MANUAL_REVIEW_REQUIRED'}
    if ocr is not None:
        result['ocr']=ocr
        # Confidence is a conservative gate, never a calibrated correctness probability.
        if any(p['min_confidence']<60 or not p['word_count'] for p in ocr['pages']):
            result.update(state='NEEDS_REVIEW',reason='OCR_LOW_CONFIDENCE')
    result['extraction_hash']=canonical_hash(result)
    return result


def chunks_for_page(text,page):
    start=0;chunks=[]
    while start<len(text):
        end=min(len(text),start+1200)
        chunks.append({'chunk_id':f'p{page}-{start}','page':page,'start_offset':start,
                       'end_offset':end,'text':text[start:end],'text_hash':text_hash(text[start:end])})
        if end==len(text):break
        start=end-150
    return chunks
