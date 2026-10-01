"""Command-line interface for UltimateSpray.

Modern subcommands (positional args, sensible defaults):

    ultimatespray create https://target.example.com
    ultimatespray create https://target.example.com --regions us-east-1,eu-west-1
    ultimatespray list
    ultimatespray update <api_id> https://new-target
    ultimatespray delete <api_id>
    ultimatespray cleanup --yes
    ultimatespray spray-check https://<id>.execute-api.us-east-1.amazonaws.com/ultimatespray/

The legacy FireProx interface (``--command create --url ...``) still works but
prints a deprecation warning.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from urllib.parse import urlsplit

from ultimatespray import __version__
from ultimatespray.core import DEFAULT_REGION, CredentialError, UltimateSpray
from ultimatespray.costs import DEFAULT_USD_BRL, estimate_cost, format_estimate
from ultimatespray.simulate import build_report, load_users, validate_url, write_report
from ultimatespray.spray import RotatingProxy

logger = logging.getLogger("ultimatespray")

CRED_ARGS = ("profile_name", "access_key", "secret_access_key", "session_token", "region")


def _add_credential_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("AWS credentials")
    group.add_argument("--profile", "--profile_name", dest="profile_name",
                       help="AWS profile name to load credentials")
    group.add_argument("--access-key", "--access_key", dest="access_key",
                       help="AWS access key")
    group.add_argument("--secret-access-key", "--secret_access_key",
                       dest="secret_access_key", help="AWS secret access key")
    group.add_argument("--session-token", "--session_token", dest="session_token",
                       help="AWS session token")
    group.add_argument("--region", help=f"AWS region (default: {DEFAULT_REGION})")


def _add_cost_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Offline cost estimate")
    group.add_argument("--pricing-region", help="Region used for pricing, not deployment")
    group.add_argument("--usd-brl", default=DEFAULT_USD_BRL,
                       help="Planning BRL per USD (default: 5.20; not a live quote)")
    group.add_argument("--request-price-per-million", help="Override USD per million REST calls")
    group.add_argument("--data-out-gb", help="Estimated billable outbound GB; omitted = unknown")
    group.add_argument("--data-price-per-gb", help="Override USD per outbound GB")


def _cost_estimate(args: argparse.Namespace, requests: int, region: str | None = None) -> dict:
    return estimate_cost(
        requests,
        region=region or args.pricing_region or args.region or DEFAULT_REGION,
        usd_brl=args.usd_brl,
        request_price_per_million=args.request_price_per_million,
        data_out_gb=args.data_out_gb,
        data_price_per_gb=args.data_price_per_gb,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ultimatespray",
        description="UltimateSpray - rotating source-IP proxies via AWS API Gateway",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    _add_credential_args(parser)

    # Legacy FireProx flags (deprecated, still routed).
    parser.add_argument("--command", help=argparse.SUPPRESS)
    parser.add_argument("--url", help=argparse.SUPPRESS)
    parser.add_argument("--api_id", help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="subcommand", metavar="<command>")

    p_create = sub.add_parser("create", help="Create a proxy for a target URL")
    p_create.add_argument("url", help="Target URL to proxy")
    p_create.add_argument("--regions", help="Comma-separated regions (one proxy each)")

    sub.add_parser("list", help="List existing proxies")

    p_update = sub.add_parser("update", help="Point an existing proxy at a new URL")
    p_update.add_argument("api_id", help="API ID to update")
    p_update.add_argument("url", help="New target URL")

    p_delete = sub.add_parser("delete", help="Delete a proxy by API ID")
    p_delete.add_argument("api_id", help="API ID to delete")

    p_cleanup = sub.add_parser("cleanup", help="Delete ALL UltimateSpray proxies")
    p_cleanup.add_argument("--yes", action="store_true", help="Skip confirmation")

    p_check = sub.add_parser("spray-check", help="Send test requests through a proxy")
    p_check.add_argument("proxy_url", nargs="+", help="Proxy URL(s) to test")
    p_check.add_argument("--count", type=int, default=5, help="Requests to send")
    p_check.add_argument("--path", default="/", help="Path to request")
    _add_cost_args(p_check)

    p_simulate = sub.add_parser("simulate", help="Preview an offline test without requests")
    p_simulate.add_argument("--users", help="File with one username per line")
    p_simulate.add_argument("--url", help="Target URL to show in the preview")
    p_simulate.add_argument("--output", help="New JSON report file")
    p_simulate.add_argument("--estimated-requests", type=int,
                            help="Hypothetical request volume; default: one per unique list entry")
    _add_cost_args(p_simulate)

    p_estimate = sub.add_parser("estimate-cost", help="Estimate AWS costs offline; sends no requests")
    p_estimate.add_argument("--requests", type=int, required=True, help="Expected total REST calls")
    _add_cost_args(p_estimate)

    return parser


def _client(args: argparse.Namespace) -> UltimateSpray:
    return UltimateSpray(
        profile_name=args.profile_name,
        access_key=args.access_key,
        secret_access_key=args.secret_access_key,
        session_token=args.session_token,
        region=args.region,
    )


def _emit(records, as_json: bool) -> None:
    if as_json:
        print(json.dumps(records, indent=2))
        return
    items = records if isinstance(records, list) else [records]
    for r in items:
        print(f"[{r['created']}] ({r['api_id']}) {r['name']} "
              f"[{r['region']}]: {r['proxy_url']} => {r['target']}")


# ---------------------------------------------------------------- subcommands
def cmd_create(args: argparse.Namespace) -> int:
    regions = [r.strip() for r in args.regions.split(",")] if args.regions else [None]
    results = []
    for region in regions:
        args.region = region or args.region
        results.append(_client(args).create_api(args.url))
    _emit(results if len(results) > 1 else results[0], args.json)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    records = _client(args).list_api()
    if args.json:
        print(json.dumps(records, indent=2))
    elif not records:
        print("No proxies found.")
    else:
        _emit(records, False)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    ok = _client(args).update_api(args.api_id, args.url)
    print(f"Update {args.api_id} => {'Success!' if ok else 'Failed!'}")
    return 0 if ok else 1


def cmd_delete(args: argparse.Namespace) -> int:
    ok = _client(args).delete_api(args.api_id)
    print(f"Delete {args.api_id} => {'Success!' if ok else 'Failed!'}")
    return 0 if ok else 1


def cmd_cleanup(args: argparse.Namespace) -> int:
    client = _client(args)
    if not args.yes:
        reply = input("Delete ALL UltimateSpray proxies in this region? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1
    deleted = client.cleanup()
    print(f"Deleted {len(deleted)} proxy(ies): {', '.join(deleted) or '(none)'}")
    return 0


def cmd_spray_check(args: argparse.Namespace) -> int:
    if args.count < 1:
        raise ValueError("--count must be at least 1")
    regions = set()
    for url in args.proxy_url:
        parts = (urlsplit(url).hostname or "").split(".")
        if len(parts) >= 5 and parts[1] == "execute-api" and parts[3:] in (
            ["amazonaws", "com"], ["amazonaws", "com", "cn"],
        ):
            regions.add(parts[2])
    if len(regions) > 1:
        if not args.pricing_region or args.request_price_per_million is None:
            raise ValueError(
                "Mixed proxy regions: set --pricing-region and --request-price-per-million "
                "to an explicit planning rate"
            )
        if args.data_out_gb is not None and args.data_price_per_gb is None:
            raise ValueError("Mixed proxy regions: set an explicit --data-price-per-gb")
        pricing_region = args.pricing_region
    else:
        pricing_region = next(iter(regions), None)
        if pricing_region and args.pricing_region and pricing_region != args.pricing_region:
            raise ValueError("--pricing-region does not match the proxy region")
    print(format_estimate(_cost_estimate(args, args.count, pricing_region)), flush=True)
    print("Volume cobre as chamadas planejadas; redirecionamentos podem gerar chamadas extras.",
          flush=True)
    proxy = RotatingProxy(args.proxy_url)
    failures = 0
    for i in range(args.count):
        try:
            resp = proxy.get(args.path, timeout=15)
            print(f"[{i + 1}/{args.count}] {resp.status_code} {resp.url}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[{i + 1}/{args.count}] ERROR: {exc}")
    return 1 if failures else 0


def cmd_simulate(args: argparse.Namespace) -> int:
    users_path = args.users or input("Arquivo da lista de usuários: ").strip()
    target_url = args.url or input("URL para o teste: ").strip()
    output_path = args.output or input("Arquivo de saída JSON: ").strip()
    if not output_path:
        raise ValueError("An output file is required")
    users = load_users(users_path)
    url = validate_url(target_url)
    requests = args.estimated_requests if args.estimated_requests is not None else len(users)
    estimate = _cost_estimate(args, requests)
    print("Cenário hipotético; a simulação fará 0 requisições e terá custo AWS de R$ 0,00.")
    if args.estimated_requests is None:
        print("Premissa do cenário: 1 requisição por entrada única; não prevê o fluxo real.")
    print(format_estimate(estimate), flush=True)
    if not sys.stdin.isatty():
        raise ValueError("Interactive terminal required for the password prompt")
    if not getpass.getpass("Senha (não será enviada nem salva): "):
        raise ValueError("A password is required for this preview")
    report = build_report(users, url)
    report["cost_preview"] = {
        "actual_aws_cost_brl": "0.00",
        "hypothetical_estimate": estimate,
    }
    write_report(output_path, report)
    print(f"Simulação concluída: {len(users)} usuário(s), 0 requisições; todos não testados.")
    print(f"Relatório: {output_path}")
    return 0


def cmd_estimate_cost(args: argparse.Namespace) -> int:
    estimate = _cost_estimate(args, args.requests)
    print(json.dumps(estimate, indent=2) if args.json else format_estimate(estimate))
    return 0


DISPATCH = {
    "create": cmd_create,
    "list": cmd_list,
    "update": cmd_update,
    "delete": cmd_delete,
    "cleanup": cmd_cleanup,
    "spray-check": cmd_spray_check,
    "simulate": cmd_simulate,
    "estimate-cost": cmd_estimate_cost,
}


def _route_legacy(args: argparse.Namespace) -> int:
    """Map deprecated --command flags onto the new subcommands."""
    logger.warning(
        "--command is deprecated; use `ultimatespray %s ...` instead", args.command
    )
    args.subcommand = args.command
    if args.command in ("create", "update"):
        args.url = args.url  # already set
    args.regions = None
    if args.command == "cleanup":
        args.yes = True
    handler = DISPATCH.get(args.command)
    if not handler:
        print(f"[ERROR] Unsupported command: {args.command}", file=sys.stderr)
        return 1
    return handler(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        if args.command:  # legacy path
            return _route_legacy(args)
        if not args.subcommand:
            parser.print_help()
            return 1
        return DISPATCH[args.subcommand](args)
    except CredentialError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except (ValueError, KeyError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
