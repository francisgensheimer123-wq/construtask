from decimal import Decimal, InvalidOperation, ROUND_DOWN

from django import template

from Construtask.status_semantics import get_status_badge_class, get_status_stage_label

register = template.Library()


@register.filter
def trunc2(value):
    if value in (None, ""):
        return "0,00"

    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value

    decimal_value = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return f"{decimal_value:.2f}".replace(".", ",")


@register.filter
def money_br(value):
    if value in (None, ""):
        value = Decimal("0")

    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value

    decimal_value = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    sinal = "-" if decimal_value < 0 else ""
    decimal_value = abs(decimal_value)
    inteiro, fracao = f"{decimal_value:.2f}".split(".")
    inteiro_formatado = f"{int(inteiro):,}".replace(",", ".")
    return f"{sinal}$ {inteiro_formatado},{fracao}"


@register.filter
def workflow_badge_class(value):
    return get_status_badge_class(value)


@register.filter
def workflow_stage_display(value):
    return get_status_stage_label(value)
