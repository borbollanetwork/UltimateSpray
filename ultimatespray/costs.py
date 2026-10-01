"""Offline, reference-price estimates; never accesses AWS or exchange-rate APIs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

PRICE_SOURCE = "https://aws.amazon.com/api-gateway/pricing/"
PRICE_CHECKED_ON = "2026-10-01"
# Regions explicitly covered by AWS's REST API pricing example.
REFERENCE_REGIONS = {"us-east-1", "us-east-2", "us-west-2", "ap-south-1"}
DEFAULT_USD_BRL = "5.20"  # Planning assumption, not a live exchange rate.


def _decimal(value: str, name: str, *, positive: bool = False) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a finite decimal number") from exc
    if not number.is_finite() or number < 0 or number > Decimal("1e12"):
        raise ValueError(f"{name} must be finite and between 0 and 1e12")
    if positive and number == 0:
        raise ValueError(f"{name} must be greater than zero")
    # Normalize excessive input precision before computing or rendering.
    number = number.quantize(Decimal("0.00000001"))
    if positive and number == 0:
        raise ValueError(f"{name} must be at least 0.00000001")
    return number


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def estimate_cost(
    requests: int,
    *,
    region: str = "us-east-1",
    usd_brl: str = DEFAULT_USD_BRL,
    request_price_per_million: str | None = None,
    data_out_gb: str | None = None,
    data_price_per_gb: str | None = None,
) -> dict:
    """Estimate a subtotal at flat rates, before credits, taxes and other services.

    Missing traffic size is unknown, not an assertion of zero traffic cost.
    No claim is made about requests per account or distinct source IPs.
    """
    if isinstance(requests, bool) or not isinstance(requests, int) or not 0 <= requests <= 10**12:
        raise ValueError("requests must be an integer between 0 and 1e12")
    if request_price_per_million is None:
        if region not in REFERENCE_REGIONS:
            raise ValueError(
                f"No reference REST API price for {region}; set --request-price-per-million"
            )
        if requests > 333_000_000:
            raise ValueError("Above the reference pricing tier; set --request-price-per-million")
    if data_price_per_gb is not None and data_out_gb is None:
        raise ValueError("--data-price-per-gb requires --data-out-gb")
    if data_out_gb is not None and region not in REFERENCE_REGIONS and data_price_per_gb is None:
        raise ValueError(f"No reference transfer price for {region}; set --data-price-per-gb")

    exchange = _decimal(usd_brl, "--usd-brl", positive=True)
    request_rate = _decimal(
        request_price_per_million if request_price_per_million is not None else "3.50",
        "--request-price-per-million",
    )
    traffic = _decimal(data_out_gb, "--data-out-gb") if data_out_gb is not None else None
    data_rate = _decimal(
        data_price_per_gb if data_price_per_gb is not None else "0.09",
        "--data-price-per-gb",
    ) if traffic is not None else None
    request_usd = Decimal(requests) / 1_000_000 * request_rate
    data_usd = traffic * data_rate if traffic is not None else None
    subtotal_usd = request_usd + (data_usd if data_usd is not None else Decimal(0))
    return {
        "api_type": "REST Regional",
        "pricing_region": region,
        "requests": requests,
        "usd_brl": _number(exchange),
        "exchange_rate_is_live": False,
        "request_price_usd_per_million": _number(request_rate),
        "billable_data_out_gb": _number(traffic) if traffic is not None else None,
        "data_price_usd_per_gb": _number(data_rate) if data_rate is not None else None,
        "requests_usd": _number(request_usd),
        "data_out_usd": _number(data_usd) if data_usd is not None else None,
        "estimated_subtotal_usd": _number(subtotal_usd),
        "estimated_subtotal_brl": _number(subtotal_usd * exchange),
        "scope": "requests_only" if traffic is None else "requests_and_data",
        "request_price_source": "reference" if request_price_per_million is None else "operator",
        "reference_price_source": PRICE_SOURCE,
        "reference_price_checked_on": PRICE_CHECKED_ON,
        "exclusions": ["taxes", "logs", "cache", "compute", "other_services"],
        "credits_and_free_tier_applied": False,
        "is_spending_limit": False,
    }


def format_estimate(estimate: dict) -> str:
    brl = Decimal(estimate["estimated_subtotal_brl"])
    places = 8 if 0 < brl < Decimal("0.01") else 2
    money = f"{brl:.{places}f}".replace(".", ",")
    count = f"{estimate['requests']:,}".replace(",", ".")
    lines = [
        "Estimativa AWS antes da execução (não é um limite de gasto):",
        f"  REST Regional | região de preço: {estimate['pricing_region']}",
        f"  Requisições previstas: {count}",
        f"  Tarifa: US$ {estimate['request_price_usd_per_million']} / milhão",
        f"  Câmbio de planejamento: R$ {estimate['usd_brl']} / US$ (não consultado ao vivo)",
    ]
    if estimate["billable_data_out_gb"] is None:
        lines.append("  Tráfego de saída: não informado; custo fora do subtotal")
    else:
        lines.append(
            f"  Saída cobrável: {estimate['billable_data_out_gb']} GB × "
            f"US$ {estimate['data_price_usd_per_gb']} / GB"
        )
    lines.extend([
        f"  Subtotal estimado: R$ {money}",
        "  Não inclui impostos, logs, cache, computação nem outros serviços.",
        "  Créditos e franquias não descontados; chamadas adicionais aumentam o custo.",
        f"  Preços de referência conferidos em {PRICE_CHECKED_ON}: {PRICE_SOURCE}",
    ])
    return "\n".join(lines)
