.PHONY: venv test run status docker-build docker-run clean

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

docker-run:
	docker run --rm -it --name litellmrtksync -p 9092:9090 litellmrtksync:latest

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
