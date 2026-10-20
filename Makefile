.PHONY: install generate up test

install:
	uv pip install --python .venv/bin/python -r requirements-dev.txt

generate:
	.venv/bin/python scripts/generate.py

up:
	docker compose up --build

test: generate
	docker compose up -d db
	.venv/bin/python scripts/test.py
