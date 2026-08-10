.PHONY: install test test-contract dev hand lint

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

test:
	pytest tests/

test-contract:
	pytest tests/contract/ -v

dev:
	uvicorn packages.room_server.main:app --reload --port 8000

hand:
	bash scripts/play_hand.sh

lint:
	ruff check . && mypy packages/
