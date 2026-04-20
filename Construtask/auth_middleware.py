"""
Middleware de autenticação para exigir login em todas as views.
Inclui proteção com whitelist de URLs públicas.
"""

import re
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseRedirect


class LoginRequiredMiddleware:
    """
    Middleware que exige autenticação para todas as requisições.
    URLs que não requerem autenticação devem ser listadas em LOGIN_EXEMPT_URLS.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        # URLs isentas de login (regex patterns)
        self.exempt_urls = [
            r'^/accounts/login/',
            r'^/accounts/logout/',
            r'^/admin/',  # Admin do Django tem seu próprio sistema de auth
            r'^/static/',
            r'^/media/',
            r'^/favicon\.ico',
            r'^/robots\.txt',
            r'^/health/$',
            r'^/ready/$',
        ]
        # Adicionar URLs extras configuradas
        extra_exempt = getattr(settings, 'LOGIN_EXEMPT_URLS', [])
        self.exempt_urls.extend(extra_exempt)
    
    def __call__(self, request):
        # Verificar se o path é exempted
        if self._is_exempt(request.path):
            return self.get_response(request)
        
        # Verificar se o usuário está autenticado
        if not request.user.is_authenticated:
            # Redirecionar para login com next parameter
            return redirect_to_login(
                request.get_full_path(),
                login_url=settings.LOGIN_URL
            )
        
        return self.get_response(request)
    
    def _is_exempt(self, path):
        """Verifica se o path corresponde a alguma URL exempta."""
        for pattern in self.exempt_urls:
            if re.match(pattern, path):
                return True
        return False
