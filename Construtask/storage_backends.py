from django.core.files.storage import FileSystemStorage


class PersistentMediaStorage(FileSystemStorage):
    """
    Backend explicito para volumes persistentes montados em producao.
    Permite distinguir o filesystem local de desenvolvimento de um storage
    configurado intencionalmente para operacao duravel.
    """

