"""Actual HTTP OCR acceptance in the disposable read-only Compose application."""
import io,json,os,secrets,uuid,urllib.request
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from compose_smoke import compose,request,BASE_URL,wait_ready

def main():
    wait_ready();tenant='ocr-'+uuid.uuid4().hex;password=secrets.token_urlsafe(24)
    bootstrap='''
import json,os,sys,uuid
from sqlalchemy import insert
from platform_api.database import engine_for,tenants,suppliers,users
from platform_api.auth import password_hash
c=json.load(sys.stdin);engine=engine_for(os.environ['DATABASE_URL'])
with engine.begin() as db:
    db.execute(insert(tenants).values(id=c['tenant']))
    db.execute(insert(suppliers).values(tenant_id=c['tenant'],id='S1'))
    db.execute(insert(users).values(id=str(uuid.uuid4()),tenant_id=c['tenant'],username=c['tenant'],role='manager',active=True,password_hash=password_hash(c['password'])))
engine.dispose()
'''
    compose('exec','-T','api','python','-c',bootstrap,input=json.dumps({'tenant':tenant,'password':password}),text=True)
    token=request('/v1/auth/login',payload={'username':tenant,'password':password})['access_token']
    assert request('/v1/document-capabilities',token)['ocr_available']
    image=Image.new('RGB',(1654,2339),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',58)
    for index,line in enumerate(('Grace period: 5 calendar days.','Delivery due date: 2026-11-01')):
        draw.text((100,180+index*120),line,fill='black',font=font)
    output=io.BytesIO();pdf=canvas.Canvas(output,pagesize=(595,842));pdf.drawImage(ImageReader(image),0,0,width=595,height=842);pdf.save();image.close()
    boundary='ocr-'+uuid.uuid4().hex;metadata=json.dumps({'title':'Synthetic scan','supplier_id':'S1','contract_ref':'OCR-TEST','version':1})
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{metadata}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="scan.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()+output.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request(BASE_URL+'/v1/documents',data=body,headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(req,timeout=20) as response:original=json.load(response)
    assert original['state']=='NEEDS_OCR'
    derived=request('/v1/documents/'+original['id']+'/ocr',token,payload={'revision':original['revision'],'version':2},expected=201,timeout=80)
    assert derived['state']=='EXTRACTED' and derived['extraction']['grace_days']==5 and derived['extraction']['due_date']=='2026-11-01'
    assert derived['checksum']==original['checksum'] and not derived['active_source'] and not derived['readers']
    review={'revision':derived['revision'],'checksum':derived['checksum'],'extraction_hash':derived['extraction']['extraction_hash']}
    request('/v1/documents/'+derived['id']+'/review',token,payload=review,expected=409)
    ready=request('/v1/documents/'+derived['id']+'/review',token,payload=review|{'ocr_compared_to_original':True})
    assert ready['state']=='READY' and ready['active_source']
    assert request('/v1/documents/'+original['id'],token)['state']=='NEEDS_OCR'
    request('/v1/auth/logout',token,payload={},expected=204)
    report={'status':'PASS','source_commit':os.getenv('GITHUB_SHA'),'actual_http_and_readonly_container':True,
        'ocr_engine_version':derived['extraction']['ocr']['engine_version'],'original_unchanged':True,
        'new_private_version':True,'comparison_gate_tested':True,'published_after_explicit_review':True,'real_emails_sent':0}
    target=Path('.data/phase7-ocr-container.json');target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
