"""Verify a binary pg_dump in a new disposable database; never overwrites the source."""
import json,secrets,subprocess
from pathlib import Path
from compose_smoke import compose

STATS="""SELECT json_build_object(
 'orders',(SELECT count(*) FROM purchase_orders),
 'users',(SELECT count(*) FROM users),
 'oidc_identities',(SELECT count(*) FROM oidc_identities),
 'sessions',(SELECT count(*) FROM sessions),
 'identity_integrity',(SELECT encode(sha256(convert_to(string_agg(issuer||':'||subject||':'||user_id||':'||active::text,',' ORDER BY id),'UTF8')),'hex') FROM oidc_identities),
 'session_integrity',(SELECT encode(sha256(convert_to(string_agg(token_hash||':'||user_id||':'||auth_method||':'||coalesce(oidc_identity_id,'')||':'||expires_at::text,',' ORDER BY token_hash),'UTF8')),'hex') FROM sessions),
 'documents',(SELECT count(*) FROM documents),
 'document_bytes',(SELECT string_agg(checksum||':'||encode(sha256(content),'hex'),',' ORDER BY id) FROM documents),
 'document_acl',(SELECT count(*) FROM document_acl),
 'amendments',(SELECT count(*) FROM amendment_proposals),
 'active_amendments',(SELECT count(*) FROM active_amendments),
 'workflows',(SELECT count(*) FROM workflow_runs),
 'approvals',(SELECT count(*) FROM approvals),
 'executions',(SELECT count(*) FROM action_executions),
 'sandbox_messages',(SELECT count(*) FROM sandbox_messages),
 'planner_calls',(SELECT count(*) FROM planner_calls))"""

def stats(database):
    result=compose('exec','-T','db','psql','-U','platform','-d',database,'-Atc',STATS,capture_output=True,text=True)
    return json.loads(result.stdout)

def main():
    target='phase6_restore_'+secrets.token_hex(8)
    assert target.startswith('phase6_restore_') and target.removeprefix('phase6_restore_').isalnum()
    before=stats('platform')
    dump=compose('exec','-T','db','pg_dump','-U','platform','-d','platform','-Fc',capture_output=True).stdout
    if not dump.startswith(b'PGDMP'):raise AssertionError('Expected custom-format PostgreSQL dump')
    created=False
    try:
        compose('exec','-T','db','createdb','-U','platform',target);created=True
        compose('exec','-T','db','pg_restore','-U','platform','-d',target,'--no-owner','--exit-on-error',input=dump)
        after=stats(target)
        if before!=after:raise AssertionError('Restored domain data/checksums differ from the source')
        # No binary dump or rows are published; only verification counts are reported.
        unchanged=stats('platform')==before
        if not unchanged:raise AssertionError('Source domain data changed during restore verification')
        report={'backup_restore':'PASS','source_unchanged':unchanged,
            'verified_counts':{k:v for k,v in after.items() if k not in {'document_bytes','identity_integrity','session_integrity'}},
            'pdf_bytes_and_checksums_verified':True,'identity_and_session_relationships_verified':True}
        target_report=Path('.data/phase6-backup-restore.json');target_report.parent.mkdir(parents=True,exist_ok=True)
        target_report.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    finally:
        if created:compose('exec','-T','db','dropdb','-U','platform',target)
if __name__=='__main__':main()
