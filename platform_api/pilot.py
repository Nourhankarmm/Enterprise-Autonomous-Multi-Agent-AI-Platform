"""Privacy-minimal configuration preflight; never certifies an external deployment."""
import argparse,json,os
from pathlib import Path
from .oidc import OIDCConfig

def inspect_configuration():
    mode=os.getenv('AUTH_MODE','local');hosts=os.getenv('ALLOWED_HOSTS','')
    checks={'oidc_authentication_selected':mode=='oidc',
        'postgresql_selected':os.getenv('DATABASE_URL','').startswith('postgresql'),
        'https_required':os.getenv('REQUIRE_HTTPS')=='1',
        'exact_hosts_configured':bool(hosts) and '*' not in hosts and '/' not in hosts,
        'development_tokens_disabled':os.getenv('ENABLE_DEV_TOKENS')!='1',
        'rate_limits_enabled':os.getenv('RATE_LIMIT_ENABLED','1')=='1',
        'confidential_client_configured':bool(os.getenv('OIDC_CLIENT_SECRET') or os.getenv('OIDC_CLIENT_SECRET_FILE'))}
    try:
        config=OIDCConfig.from_environment() if mode=='oidc' else None
        checks['oidc_https_only']=bool(config and config.issuer.startswith('https://') and config.secure_cookie and not config.allow_http and not config.backchannel_origin)
    except RuntimeError:checks['oidc_https_only']=False
    return {'status':'CONFIGURATION_BLOCKED' if not all(checks.values()) else 'EXTERNAL_VERIFICATION_REQUIRED',
        'scope':'Calling process configuration only; not a live deployment audit','checks':checks,
        'external_verification_required':[
            'Organization ownership of the issuer and explicit subject-to-user mappings',
            'MFA/assurance policy and provider-to-application offboarding procedures',
            'End-to-end TLS, trusted ingress and callback-query log redaction',
            'Least-privilege application database role and tenant-security review',
            'Authenticated production Temporal with TLS, persistent storage and recovery drills',
            'Actual-environment backup restoration, retention and incident response exercise'],
        'production_ready':False,'secrets_or_provider_tokens_in_report':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output');parser.add_argument('--allow-demo',action='store_true');args=parser.parse_args()
    report=inspect_configuration();serialized=json.dumps(report,indent=2)+'\n'
    if args.output:
        target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(serialized,encoding='utf-8')
    print(serialized)
    # The flag acknowledges the demo boundary for CI; it never turns the report into READY.
    raise SystemExit(0 if args.allow_demo else 1)
if __name__=='__main__':main()
