# Construtask/sentry_middleware.py


class SentryContextMiddleware:
    """
    Enriquece eventos do Sentry com contexto operacional do ConstruTask.
    Deve ser posicionado após AuthenticationMiddleware no MIDDLEWARE.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            self._enriquecer_contexto(request)
        response = self.get_response(request)
        return response

    def _enriquecer_contexto(self, request):
        try:
            import sentry_sdk
            from .permissions import get_empresa_do_usuario

            sentry_sdk.set_user({
                "id": request.user.pk,
                "username": request.user.username,
            })

            empresa = get_empresa_do_usuario(request.user)
            if empresa:
                sentry_sdk.set_tag("empresa.id", empresa.pk)
                sentry_sdk.set_tag("empresa.nome", empresa.nome_fantasia or empresa.nome)

            obra_id = request.session.get("obra_contexto_id")
            if obra_id:
                sentry_sdk.set_tag("obra.id", obra_id)

        except Exception:
            pass