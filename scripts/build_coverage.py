"""Map authored business specifications to constituent regression evidence, not E2E claims."""
import argparse,json
from pathlib import Path

MAPPING=[
('B01','partial','test_reviewed_terms_drive_page_cited_deterministic_assessment','Draft creation remains explicit/manual.'),
('B02','component','test_fifteen_calculation_cases','Synthetic grace arithmetic.'),
('B03','component','test_resolving_missing_contract_produces_supported_new_operation','Missing evidence and explicit refresh.'),
('B04','partial','test_erp_contract_date_conflict_blocks_contractual_risk','No legal signature validation.'),
('B05','deferred',None,'Verified amendment source model is unavailable.'),
('B06','deferred',None,'Aggregate inventory/project analysis is unavailable.'),
('B07','component','test_replay_and_new_process_reuse_receipt','Sandbox receipt only.'),
('B08','component','test_revision_invalidates_approval_and_old_hash','Changed payload requires review.'),
('B09','component','test_expired_approval_blocks_then_fresh_review_allows_send','Expiry and fresh review.'),
('B10','component','test_changed_erp_or_contract_requires_fresh_evidence','Current authorization has separate regressions.'),
('B11','component','test_only_live_independent_approver_can_decide','Assigned independent approver.'),
('B12','component','test_cross_tenant_object_access','Tenant-scoped reads.'),
('B13','component','test_private_document_is_hidden_from_same_and_other_company','Current document ACL.'),
('B14','component','test_injection_text_cannot_expand_capabilities_or_send','Hostile synthetic PDF text.'),
('B15','component','test_transient_read_retry_is_bounded','Bounded transient read recovery.'),
('B16','component','test_simultaneous_dispatch_converges_on_one_provider_message','One logical sandbox message.'),
('B17','partial','test_unknown_provider_outcome_is_never_blindly_retried','No production provider/manual reconciliation UI.'),
('B18','component','test_provider_accept_then_worker_crash_reconciles_without_second_send','Actual Temporal process recovery is separately measured.'),
('B19','partial','test_cancel_after_provider_acceptance_still_reports_receipt','Single logical action; not the full multi-action business journey.'),
('B20','component','test_out_of_scope_model_decision_is_not_executed','Unsupported write tools denied.')]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',default='.data/phase6-regressions.json');parser.add_argument('--output',default='.data/phase6-business-coverage.json');args=parser.parse_args()
    results=json.loads(Path(args.input).read_text());records=[]
    for case,status,name,scope in MAPPING:
        evidence=[t for t in results['tests'] if name and t['id'].endswith('.'+name)]
        if name and (len(evidence)!=1 or evidence[0]['status']!='passed'):raise SystemExit('Missing passing constituent evidence: '+case)
        records.append({'case':case,'coverage':status,'scope':scope,'tests':[t['id'] for t in evidence],'full_business_e2e_claim':False})
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'source_regression_commit':results['commit'],'cases':records,'note':'Component evidence is not complete business E2E acceptance.'},indent=2)+'\n')
if __name__=='__main__':main()
