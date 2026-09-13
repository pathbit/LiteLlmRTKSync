.PHONY: venv test run status docker-build docker-run clean

# Cria o .env a partir do .env.example. Nunca sobrescreve um .env existente:
# ele carrega os seus segredos, e um `make setup` distraido nao pode apaga-los.
# O docker compose le esse .env sozinho, por estar ao lado do compose.
setup:
	@if [ -f .env ]; then \
		echo ".env ja existe — preservado."; \
	else \
		cp .env.example .env; \
		echo ".env criado a partir de .env.example."; \
	fi
	@echo ""
	@echo "Preencha no .env antes de subir a stack:"
	@grep -nE '^[A-Z_]+=$$' .env | sed 's/^/   linha /' || echo "   (nada obrigatorio em branco)"
	@echo ""
	@echo "O painel usa DASHBOARD_USER e DASHBOARD_PASSWORD. Sem senha definida,"
	@echo "o primeiro acesso usa a credencial de recuperacao gerada no boot."


VENV ?= .venv
PYTHON ?= $(shell which $(VENV)/bin/python3 2>/dev/null || which python3)

venv:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e .

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p "test_*.py"

run:
	PYTHONPATH=src $(PYTHON) -m litellm_rtksync.cli --daemon

status:
	PYTHONPATH=src $(PYTHON) -m litellm_rtksync.cli --status

docker-build:
	docker build -t litellmrtksync:latest -t ghcr.io/pathbit/litellmrtksync:latest .

# 9093 e a porta de host deste painel (9091 e do 9RTKSync, 9092 do OminiRTkSync).
# Publicar na 9092 ocupava a porta do irmao e mostrava este painel no endereco
# que a documentacao atribui ao outro produto. Comentario FORA da receita: linha
# iniciada por # dentro de um alvo vai para o shell e aparece na saida.
docker-run:
	docker run --rm -it --name litellmrtk-sync -p 127.0.0.1:9093:9090 litellmrtksync:latest

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
