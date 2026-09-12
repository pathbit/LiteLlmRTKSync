"""Linha de comando do LiteLlmRTKSync."""

import argparse
import signal
import sys
import time

from .config import Settings
from .engine import LiteLLMSyncEngine, log_msg
from .limits import SEVERIDADE_INCOERENTE, SEVERIDADE_SEM_TETO, avaliar
from .logs import setup_logging


def imprimir_status(settings: Settings) -> int:
    """Tabela de estado. Devolve o código de saída: 1 se houver incoerência."""
    motor = LiteLLMSyncEngine(settings)
    if not motor.client.health():
        print(f"[ERRO] Proxy LiteLLM inacessível em {settings.litellm_url}", file=sys.stderr)
        return 2

    chaves = motor.client.list_keys()
    times = motor.client.list_teams()
    modelos = motor.client.list_models()

    largura = 78
    print("\n" + "=" * largura)
    print("[*] LITELLMRTKSYNC · ESTADO DO PROXY LITELLM")
    print(f"   Proxy: {settings.litellm_url}")
    print("=" * largura)

    from .models import VirtualKey, summarize

    virtuais = [VirtualKey(k) for k in chaves]
    resumo = summarize(virtuais, settings.refresh_margin)
    print(f"\n[*] Chaves virtuais ({len(virtuais)}):")
    print(f"  {'APELIDO':<26} {'TIME':<18} {'ESTADO':<14} {'VALIDADE'}")
    print("  " + "-" * (largura - 4))
    for chave in virtuais:
        restante = chave.remaining_seconds
        if restante is None:
            validade = "sem validade declarada"
        elif restante <= 0:
            validade = "vencida"
        else:
            validade = f"{restante // 3600}h {(restante % 3600) // 60}min"
        print(f"  {chave.alias[:25]:<26} {(chave.team_id or '-')[:17]:<18} "
              f"{chave.health_status(settings.refresh_margin):<14} {validade}")

    print(f"\n[*] Resumo: " + ", ".join(f"{v} {k}" for k, v in resumo.items() if v))
    print(f"[*] Times: {len(times)}   Modelos cadastrados: {len(modelos)}")

    relatorio = avaliar(chaves, times, settings.platform_caps())
    incoerentes = relatorio.por_severidade(SEVERIDADE_INCOERENTE)
    sem_teto = relatorio.por_severidade(SEVERIDADE_SEM_TETO)

    print(f"\n[*] Coerência dos limites ({relatorio.chaves_avaliadas} chave(s) avaliada(s)):")
    if not incoerentes and not sem_teto:
        print("  Nenhuma incoerência: todo limite respeita o nível acima.")
    for achado in incoerentes:
        print(f"  [INCOERENTE] {achado.mensagem}")
    for achado in sem_teto:
        print(f"  [SEM TETO  ] {achado.mensagem}")

    print("\n" + "=" * largura + "\n")
    return 1 if incoerentes else 0


def rodar_daemon(settings: Settings) -> None:
    motor = LiteLLMSyncEngine(settings)
    rodando = True

    def ao_receber_sinal(sinal, quadro):
        nonlocal rodando
        print(f"\n[!] Sinal {sinal} recebido. Encerrando o LiteLlmRTKSync...", flush=True)
        rodando = False

    signal.signal(signal.SIGINT, ao_receber_sinal)
    signal.signal(signal.SIGTERM, ao_receber_sinal)

    print("=" * 70, flush=True)
    print("[*] LITELLMRTKSYNC · LITELLM VIRTUAL KEY & LIMIT SYNCHRONIZER", flush=True)
    print(f"   Proxy:     {settings.litellm_url}", flush=True)
    print(f"   Intervalo: {settings.sync_interval}s · Margem: {settings.refresh_margin}s", flush=True)
    print("=" * 70, flush=True)

    servidor = None
    if settings.enable_web:
        from .web import start_web

        servidor = start_web(settings, motor)

    try:
        while rodando:
            motor.sync_all()
            for _ in range(settings.sync_interval):
                if not rodando:
                    break
                time.sleep(1)
    finally:
        if servidor is not None:
            servidor.shutdown()
            servidor.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="litellmrtksync",
        description="LiteLlmRTKSync · inspeção de chaves virtuais, credenciais e limites do LiteLLM",
    )
    parser.add_argument("--url", help="Endereço do proxy LiteLLM (padrão: LITELLM_URL)")
    parser.add_argument("--status", action="store_true", help="Imprime o estado e sai")
    parser.add_argument("--once", action="store_true", help="Executa um ciclo e sai")
    parser.add_argument("--daemon", action="store_true", help="Modo contínuo (padrão)")
    parser.add_argument("--interval", type=int, help="Intervalo entre ciclos, em segundos")
    parser.add_argument("--margin", type=int, help="Margem de aviso de vencimento, em segundos")
    parser.add_argument("--no-web", action="store_true", help="Desliga o painel embutido")
    parser.add_argument("--port", type=int, help="Porta do painel embutido")

    args = parser.parse_args()
    settings = Settings.from_env()

    # As opções são aplicadas ANTES de resolver estado persistente: com um
    # diretório de dados diferente, o log e a credencial de recuperação
    # precisam nascer no lugar certo desde o começo.
    if args.url:
        settings.litellm_url = args.url.rstrip("/")
    if args.interval:
        settings.sync_interval = args.interval
        # O agendador lê cron_interval, derivado do ambiente antes disto.
        settings.cron_interval = args.interval
    if args.margin:
        settings.refresh_margin = args.margin
    if args.no_web:
        settings.enable_web = False
    if args.port:
        settings.web_port = args.port

    logger = setup_logging(settings.get_log_dir())

    valor, gerado = settings.ensure_recovery_hash()
    if gerado and valor:
        # O valor NÃO vai para o log: é uma credencial funcional, e o stdout do
        # container é coletado e lido por muita gente. O log diz onde lê-lo.
        logger.warning(
            "[AUTH] Credencial de recuperação gerada para o usuário 'admin'. Leia com: "
            "docker exec <container> cat %s  (ou fixe a sua com DASHBOARD_RECOVERY_HASH)",
            settings.get_recovery_file_path(),
        )

    if args.status:
        sys.exit(imprimir_status(settings))

    if args.once:
        resultado = LiteLLMSyncEngine(settings).sync_all()
        log_msg(
            "INFO",
            f"Ciclo concluído: {resultado['keys']} chave(s), {resultado['models']} modelo(s), "
            f"{resultado['expiring']} vencendo, {resultado['limit_findings']} incoerência(s) de limite.",
        )
        sys.exit(0 if resultado["success"] else 1)

    rodar_daemon(settings)


if __name__ == "__main__":
    main()
