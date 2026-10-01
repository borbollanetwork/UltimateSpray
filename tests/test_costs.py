import json
from decimal import Decimal
from unittest import mock

import pytest

from ultimatespray import cli
from ultimatespray.costs import estimate_cost, format_estimate


@pytest.mark.parametrize("requests,brl", [
    (150_000, "2.73"), (300_000, "5.46"), (750_000, "13.65"), (1_500_000, "27.30"),
])
def test_reference_request_costs(requests, brl):
    result = estimate_cost(requests, usd_brl="5.20")
    assert Decimal(result["estimated_subtotal_brl"]) == Decimal(brl)
    assert result["data_out_usd"] is None
    assert result["scope"] == "requests_only"
    assert result["credits_and_free_tier_applied"] is False
    assert result["exchange_rate_is_live"] is False


def test_traffic_cost_is_separate_and_added_exactly():
    result = estimate_cost(150_000, data_out_gb="1.5", usd_brl="5.20")
    assert Decimal(result["requests_usd"]) == Decimal("0.525")
    assert Decimal(result["data_out_usd"]) == Decimal("0.135")
    assert Decimal(result["estimated_subtotal_brl"]) == Decimal("3.432")
    assert result["scope"] == "requests_and_data"
    assert "R$ 3,43" in format_estimate(result)


@pytest.mark.parametrize("field,value", [
    ("usd_brl", "0"), ("usd_brl", "0.000000001"), ("usd_brl", "NaN"),
    ("usd_brl", "Infinity"), ("usd_brl", "-5.2"), ("usd_brl", "5,20"),
    ("data_out_gb", "-1"), ("data_out_gb", "NaN"),
    ("request_price_per_million", "-3.5"), ("request_price_per_million", "Infinity"),
])
def test_invalid_cost_assumptions_are_rejected(field, value):
    with pytest.raises(ValueError):
        estimate_cost(150_000, **{field: value})


@pytest.mark.parametrize("requests", [-1, 1.5, True, 10**13])
def test_invalid_request_counts_are_rejected(requests):
    with pytest.raises(ValueError):
        estimate_cost(requests)


def test_unknown_region_requires_explicit_prices():
    with pytest.raises(ValueError, match="request-price-per-million"):
        estimate_cost(150_000, region="sa-east-1")
    with pytest.raises(ValueError, match="data-price-per-gb"):
        estimate_cost(150_000, region="sa-east-1", request_price_per_million="4", data_out_gb="1")
    result = estimate_cost(150_000, region="sa-east-1", request_price_per_million="4",
                           data_out_gb="1", data_price_per_gb="0.15")
    assert result["request_price_source"] == "operator"
    assert Decimal(result["estimated_subtotal_usd"]) == Decimal("0.75")


def test_reference_tier_and_missing_traffic_size_are_not_guessed():
    with pytest.raises(ValueError, match="pricing tier"):
        estimate_cost(333_000_001)
    with pytest.raises(ValueError, match="requires --data-out-gb"):
        estimate_cost(1, data_price_per_gb="0.09")


def test_small_nonzero_cost_is_displayed_as_nonzero():
    assert "R$ 0,00009100" in format_estimate(estimate_cost(5))
    assert "R$ 0,00\n" in format_estimate(estimate_cost(0))


def test_estimator_json_never_initializes_network_or_aws(capsys):
    with mock.patch.object(cli, "_client", side_effect=AssertionError("AWS accessed")), \
            mock.patch.object(cli, "RotatingProxy", side_effect=AssertionError("Network accessed")):
        rc = cli.main(["--json", "estimate-cost", "--requests", "150000"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert Decimal(result["usd_brl"]) == Decimal("5.20")
    assert Decimal(result["estimated_subtotal_brl"]) == Decimal("2.73")


def test_connectivity_estimate_is_visible_before_first_request(capsys):
    with mock.patch.object(cli, "RotatingProxy") as proxy_class:
        def check_visible(*args, **kwargs):
            output = capsys.readouterr().out
            assert "Estimativa AWS antes da execução" in output
            assert "Requisições previstas: 1" in output
            assert "Tráfego de saída: não informado" in output
            return mock.Mock(status_code=200, url="https://proxy.example/")

        proxy_class.return_value.get.side_effect = check_visible
        assert cli.main(["spray-check", "https://proxy.example/", "--count", "1"]) == 0
        proxy_class.return_value.get.assert_called_once()


def test_invalid_estimate_stops_before_network():
    with mock.patch.object(cli, "RotatingProxy") as proxy_class:
        assert cli.main(["spray-check", "https://proxy.example/", "--usd-brl", "NaN"]) == 1
        proxy_class.assert_not_called()


def test_connectivity_pricing_uses_actual_gateway_region(capsys):
    with mock.patch.object(cli, "RotatingProxy") as proxy_class:
        rc = cli.main(["spray-check", "https://id.execute-api.sa-east-1.amazonaws.com/stage/"])
        assert rc == 1
        proxy_class.assert_not_called()
    assert "No reference REST API price for sa-east-1" in capsys.readouterr().err


def test_mismatched_and_mixed_regions_are_not_silently_priced():
    with mock.patch.object(cli, "RotatingProxy") as proxy_class:
        assert cli.main([
            "spray-check", "https://id.execute-api.us-east-1.amazonaws.com/stage/",
            "--pricing-region", "us-west-2",
        ]) == 1
        assert cli.main([
            "spray-check", "https://id.execute-api.us-east-1.amazonaws.com/stage/",
            "https://id.execute-api.eu-west-1.amazonaws.com/stage/",
        ]) == 1
        proxy_class.assert_not_called()
