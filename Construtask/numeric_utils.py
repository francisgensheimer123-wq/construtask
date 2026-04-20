from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def coerce_decimal(valor, *, default=Decimal("0.00"), quantize=None, allow_none=False):
    if valor is None:
        return None if allow_none else default

    if isinstance(valor, Decimal):
        resultado = valor
    elif isinstance(valor, (int, float)):
        resultado = Decimal(str(valor))
    else:
        texto = str(valor).strip()
        if not texto:
            return None if allow_none else default

        texto = (
            texto.replace("R$", "")
            .replace("$", "")
            .replace("\u00a0", "")
            .replace(" ", "")
        )

        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            texto = texto.replace(".", "").replace(",", ".")

        try:
            resultado = Decimal(texto)
        except (InvalidOperation, ValueError, TypeError):
            return None if allow_none else default

    if quantize is not None:
        return resultado.quantize(Decimal(quantize), rounding=ROUND_HALF_UP)
    return resultado


def coerce_int(valor, *, default=None):
    decimal_valor = coerce_decimal(valor, default=None, allow_none=True)
    if decimal_valor is None:
        return default
    try:
        return int(decimal_valor)
    except (ValueError, TypeError):
        return default
