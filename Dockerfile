# ==============================================================================
# LiteLlmRTKSync: LiteLLM virtual key, credential and rate-limit synchronizer
# Imagem oficial baseada em Python 3.14 Alpine
# ==============================================================================

FROM python:3.14-alpine

LABEL org.opencontainers.image.title="LiteLlmRTKSync"
LABEL org.opencontainers.image.description="Inspects LiteLLM virtual keys, provider credentials and rate-limit coherence"
LABEL org.opencontainers.image.authors="Eliel Sousa <eliel@pathbit.co>"
LABEL org.opencontainers.image.source="https://github.com/pathbit/LiteLlmRTKSync"

WORKDIR /app

# Criação obrigatória e isolada do Virtual Environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
# Sem DB_PATH nem OMNIROUTE_URL: as duas vieram dos irmaos, que leem um SQLite
# em disco e falam com o gateway deles. Este projeto le a API administrativa do
# LiteLLM (ver client.py) e nenhum codigo daqui le essas variaveis. O endereco
# do proxy tem um unico dono, o default de config.py, e repeti-lo na imagem foi
# justamente o que deixou os dois valores divergirem.
ENV SYNC_INTERVAL=300
ENV REFRESH_MARGIN=900
ENV WEB_PORT=9090
ENV WEB_HOST=0.0.0.0
ENV ENABLE_WEB_DASHBOARD=1

COPY src/ /app/src/
COPY pyproject.toml /app/

# Instalação do pacote dentro do virtual environment
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Criado na imagem, com o dono que o compose usa: um volume nomeado herda o
# dono do diretorio que cobre. Sem isto ele nasce root e o processo (uid 1000)
# nao consegue escrever o proprio log.
RUN mkdir -p /app/logs && chown -R 1000:1000 /app/logs

EXPOSE 9090

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD /opt/venv/bin/python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz', timeout=3)" || exit 1

ENTRYPOINT ["/opt/venv/bin/python3", "-m", "litellm_rtksync.cli"]
CMD ["--daemon"]
