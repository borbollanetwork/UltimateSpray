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
from ultimatespray.costs import DEFAULT_USD_BRL, PRICE_CHECKED_ON, estimate_cost, format_estimate
from ultimatespray.simulate import build_report, load_users, validate_url, write_report
from ultimatespray.spray import RotatingProxy

logger = logging.getLogger("ultimatespray")

CRED_ARGS = ("profile_name", "access_key", "secret_access_key", "session_token", "region")


class PortugueseArgumentParser(argparse.ArgumentParser):
    """Keep help labels in Portuguese without changing the process-wide locale."""

    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        super().__init__(*args, **kwargs)
        self._positionals.title = "Parâmetros posicionais"
        self._optionals.title = "Opções"
        self.add_argument("-h", "--help", action="help",
                          help="Mostra estas instruções e encerra, sem executar o comando")

    def format_usage(self) -> str:
        return super().format_usage().replace("usage: ", "uso: ", 1)

    def format_help(self) -> str:
        return super().format_help().replace("usage: ", "uso: ", 1)


def _add_credential_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Credenciais e região da AWS (antes do comando)")
    group.add_argument("--profile", "--profile_name", dest="profile_name",
                       metavar="PERFIL", help="Nome do perfil AWS com as credenciais a carregar")
    group.add_argument("--access-key", "--access_key", dest="access_key",
                       metavar="CHAVE", help="Chave de acesso AWS; tem prioridade sobre --profile")
    group.add_argument("--secret-access-key", "--secret_access_key",
                       dest="secret_access_key", metavar="SEGREDO",
                       help="Chave secreta AWS correspondente à chave de acesso")
    group.add_argument("--session-token", "--session_token", dest="session_token",
                       metavar="TOKEN", help="Token de sessão para credenciais temporárias AWS")
    group.add_argument("--region", metavar="REGIÃO",
                       help=f"Região AWS usada pelo comando (padrão: {DEFAULT_REGION})")


def _add_cost_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Estimativa de custos sem acesso à rede")
    group.add_argument("--pricing-region", metavar="REGIÃO",
                       help="Região usada no preço; não cria recursos. Por padrão, usa a região "
                            "do proxy em spray-check, ou --region/us-east-1 nos demais comandos")
    group.add_argument("--usd-brl", default=DEFAULT_USD_BRL, metavar="CÂMBIO",
                       help=f"Opcional: substitui o câmbio embutido de R$ {DEFAULT_USD_BRL} "
                            "por US$ 1; é uma referência fixa, não uma cotação ao vivo")
    group.add_argument("--request-price-per-million", metavar="USD",
                       help="Substitui a tarifa REST por milhão de chamadas; padrão US$ 3.50 "
                            "nas regiões de referência. Exigido em outras regiões")
    group.add_argument("--data-out-gb", metavar="GB",
                       help="Volume estimado de saída cobrável em GB; se omitido, "
                            "o tráfego fica fora do subtotal")
    group.add_argument("--data-price-per-gb", metavar="USD",
                       help="Tarifa de saída por GB; padrão US$ 0.09 nas regiões de referência. "
                            "Requer --data-out-gb")


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
    parser = PortugueseArgumentParser(
        prog="ultimatespray",
        description="UltimateSpray — gerenciamento de proxies AWS API Gateway, "
                    "teste de conectividade e prévia offline de avaliações autorizadas.",
        epilog="Como consultar os parâmetros de um comando:\n"
               "  ultimatespray <comando> --help\n\n"
               "Exemplos:\n"
               "  ultimatespray estimate-cost --requests 150000\n"
               "  ultimatespray --json estimate-cost --requests 150000\n"
               "  ultimatespray --profile meu-perfil --region us-east-1 list\n"
               "  ultimatespray simulate --help\n\n"
               "Opções globais (--profile, --region, --json e --verbose) vêm antes do comando.\n"
               "estimate-cost e simulate não acessam AWS nem enviam requisições.\n"
               "Sem credenciais explícitas, os comandos AWS usam a cadeia padrão do boto3.",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}", help="Mostra a versão e encerra")
    parser.add_argument("-v", "--verbose", action="store_true", help="Ativa os registros detalhados")
    parser.add_argument("--json", action="store_true",
                        help="Saída JSON em create, list e estimate-cost; use antes do comando")
    _add_credential_args(parser)

    # Legacy FireProx flags (deprecated, still routed).
    legacy = parser.add_argument_group("Compatibilidade antiga do FireProx (prefira os subcomandos)")
    legacy.add_argument("--command", metavar="COMANDO",
                        help="Seleciona um comando pela interface antiga")
    legacy.add_argument("--url", metavar="URL",
                        help="URL usada com --command create ou update")
    legacy.add_argument("--api_id", metavar="ID",
                        help="ID da API usado com --command update ou delete")

    sub = parser.add_subparsers(dest="subcommand", metavar="<comando>", title="Comandos disponíveis")

    p_create = sub.add_parser("create", help="Cria um proxy para uma URL",
                             description="Cria uma REST API regional que encaminha chamadas "
                                         "à URL informada. Requer credenciais AWS.")
    p_create.add_argument("url", metavar="URL", help="URL completa de destino do proxy")
    p_create.add_argument("--regions", metavar="REGIÕES",
                         help="Regiões separadas por vírgula; cria um proxy em cada região. "
                              "Se omitido, usa --region ou us-east-1")

    sub.add_parser("list", help="Lista os proxies existentes na região",
                   description="Lista os proxies da região escolhida com --region. "
                               "Requer credenciais AWS; --json permite saída estruturada.")

    p_update = sub.add_parser("update", help="Atualiza a URL de destino de um proxy",
                             description="Altera o destino de uma API existente. Requer credenciais AWS.")
    p_update.add_argument("api_id", metavar="ID", help="ID da API a atualizar")
    p_update.add_argument("url", metavar="URL", help="Nova URL completa de destino")

    p_delete = sub.add_parser("delete", help="Exclui um proxy pelo ID da API",
                             description="Exclui a API indicada na região selecionada. "
                                         "Requer credenciais AWS.")
    p_delete.add_argument("api_id", metavar="ID", help="ID da API a excluir")

    p_cleanup = sub.add_parser("cleanup", help="Exclui os proxies marcados como gerenciados",
                              description="Exclui, na região selecionada, apenas os proxies com a "
                                          "tag ultimatespray:managed=true. Requer credenciais AWS.")
    p_cleanup.add_argument("--yes", action="store_true",
                           help="Executa a exclusão sem a pergunta de confirmação")

    p_check = sub.add_parser("spray-check", help="Testa a conectividade de proxies com chamadas GET",
                            description="Mostra a estimativa de custo e envia chamadas GET pelos "
                                        "proxies existentes. Exibe o status HTTP; não valida credenciais.",
                            epilog="Domínios personalizados: informe --pricing-region para usar a "
                                   "região de preço correta.\n"
                                   "Proxies em regiões diferentes: informe --pricing-region e "
                                   "--request-price-per-million;\n"
                                   "se incluir tráfego, informe também --data-price-per-gb.")
    p_check.add_argument("proxy_url", nargs="+", metavar="URL_PROXY",
                         help="Uma ou mais URLs completas de proxies existentes")
    p_check.add_argument("--count", type=int, default=5, metavar="N",
                         help="Total de chamadas GET planejadas, distribuídas entre os proxies "
                              "(padrão: 5; mínimo: 1)")
    p_check.add_argument("--path", default="/", metavar="CAMINHO",
                         help="Caminho solicitado em cada chamada GET (padrão: /)")
    _add_cost_args(p_check)

    p_simulate = sub.add_parser("simulate", help="Simula entradas e relatório sem enviar requisições",
                               description="Prévia offline: lê a lista, mostra um cenário de custo e "
                                           "gera um relatório com todos os usuários como não testados.",
                               epilog="Se --users, --url ou --output forem omitidos, o programa pergunta "
                                      "os valores.\n"
                                      "A senha é solicitada no terminal, sem envio ou gravação.\n"
                                      "Custo AWS da simulação: R$ 0,00. Requer terminal interativo.")
    p_simulate.add_argument("--users", metavar="ARQUIVO",
                            help="Lista UTF-8 com um usuário por linha; ignora linhas vazias e duplicatas")
    p_simulate.add_argument("--url", metavar="URL",
                            help="URL HTTP(S) mostrada na prévia, sem credenciais, consulta ou fragmento")
    p_simulate.add_argument("--output", metavar="ARQUIVO",
                            help="Caminho do relatório JSON; o arquivo ainda não pode existir")
    p_simulate.add_argument("--estimated-requests", type=int, metavar="N",
                            help="Volume hipotético para o custo; padrão: 1 por entrada única da lista. "
                                 "Não representa requisições reais")
    _add_cost_args(p_simulate)

    p_estimate = sub.add_parser("estimate-cost", help="Estima o custo AWS em reais, sem acessar a rede",
                               description="Calcula um subtotal com preços de referência e câmbio "
                                           f"embutido de R$ {DEFAULT_USD_BRL}/US$. "
                                           "Não exige credenciais AWS.",
                               epilog="Exemplo:\n"
                                      "  ultimatespray estimate-cost --requests 150000\n\n"
                                      "O câmbio é fixo e já vem na ferramenta; --usd-brl é opcional.\n"
                                      "Regiões de referência: us-east-1, us-east-2, us-west-2, ap-south-1.\n"
                                      f"Preços conferidos em {PRICE_CHECKED_ON}; o subtotal não inclui impostos, "
                                      "logs ou outros serviços.\n"
                                      "Créditos e franquias não são descontados. A estimativa não limita gastos.")
    p_estimate.add_argument("--requests", type=int, required=True, metavar="N",
                            help="Total previsto de chamadas REST (obrigatório; inteiro não negativo)")
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
