"""Browser login routes; provider tokens never leave the backend or enter URLs/storage."""
from fastapi import Depends,Request
from fastapi.responses import RedirectResponse,HTMLResponse
from .oidc import OIDCService,OIDCError,FLOW_COOKIE,SESSION_COOKIE,csrf_token,origin

def register(app,engine,identity,config,limiter,rate_enabled,peer_limit,window):
    service=OIDCService(engine,config) if config else None
    app.state.oidc=service
    @app.get('/v1/auth/mode')
    def mode():return {'mode':'oidc' if config else 'local','login_path':'/v1/auth/oidc/start' if config else None,
        'logout_scope':'application_session','automatic_identity_provisioning':False}
    @app.get('/v1/auth/session')
    def session(request:Request,actor=Depends(identity)):
        return {'identity':actor,'csrf_token':csrf_token(request.cookies.get(SESSION_COOKIE,'')) if config else None}
    if not service:return
    @app.exception_handler(OIDCError)
    async def auth_error(request,error):
        # Fixed text only: no authorization codes, token bodies or provider errors are reflected.
        return HTMLResponse('<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>تعذر الدخول</title>'
            '<p>تعذر إتمام تسجيل الدخول. ارجع للتطبيق وحاول مجددًا، أو راجع مسؤول ربط الحساب.</p><a href="/">العودة للتطبيق</a></html>',status_code=error.status)
    def admit(request):
        if rate_enabled:limiter.consume('oidc_peer',request.client.host if request.client else 'unknown',peer_limit,window)
    @app.get('/v1/auth/oidc/start')
    def start(request:Request):
        admit(request)
        if origin(str(request.url))!=origin(config.redirect_uri):
            return RedirectResponse(origin(config.redirect_uri)+'/v1/auth/oidc/start',status_code=303)
        url,browser=service.begin();response=RedirectResponse(url,status_code=303)
        response.set_cookie(FLOW_COOKIE,browser,httponly=True,secure=config.secure_cookie,samesite='lax',max_age=300,path='/v1/auth/oidc')
        return response
    @app.get('/v1/auth/oidc/callback')
    def callback(request:Request):
        admit(request)
        # Manual bounded validation keeps framework errors from reflecting provider query values.
        query=request.query_params
        session,lifetime=service.finish(query.get('state',''),request.cookies.get(FLOW_COOKIE,''),query.get('code',''),query.get('error'))
        response=RedirectResponse('/',status_code=303)
        response.delete_cookie(FLOW_COOKIE,path='/v1/auth/oidc',secure=config.secure_cookie,httponly=True,samesite='lax')
        response.set_cookie(SESSION_COOKIE,session,httponly=True,secure=config.secure_cookie,samesite='lax',max_age=lifetime,path='/')
        return response
