"""Interactive loopback-only identity demo. Secrets stay in process memory, not .env/files."""
import getpass,os,subprocess
from pathlib import Path
from identity_demo_support import LocalIdentityDemo,compose_identity

def main():
    os.chdir(Path(__file__).resolve().parents[1])
    admin=getpass.getpass('Local Keycloak admin password (reuse it if already initialized): ')
    password=getpass.getpass('New synthetic demo user password (12+ characters): ')
    if len(admin)<12 or len(password)<12:raise SystemExit('Use passwords of at least 12 characters')
    os.environ['KEYCLOAK_ADMIN_PASSWORD']=admin
    compose_identity('up','--build','-d','--wait','--wait-timeout','240')
    port=os.getenv('API_BIND_PORT','8000');idp_port=os.getenv('OIDC_BIND_PORT','18080')
    demo=LocalIdentityDemo(admin,'http://localhost:'+idp_port,'http://127.0.0.1:'+port)
    demo.wait();demo.admin();username,_=demo.provision(password,role='manager')
    print('Synthetic identity demo ready at '+demo.app)
    print('Use the enterprise sign-in button. Demo username: '+username)
    print('Use the demo user password entered above. No password was saved by this script.')
    print('Loopback demo only; this is not a production identity deployment.')
if __name__=='__main__':main()
