import re


CNPJ_MASK = "00.000.000/0001-00"


def apenas_digitos_cnpj(valor):
    return re.sub(r"\D", "", str(valor or ""))


def formatar_cnpj(valor):
    digitos = apenas_digitos_cnpj(valor)[:14]
    if len(digitos) != 14:
        return str(valor or "").strip()
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:14]}"
