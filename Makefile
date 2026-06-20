.PHONY: install lint test test-python test-frontend build dev

install:
	pip install -e ".[dev]"
	pnpm install

lint:
	ruff check backend/ tests/
	ruff format --check backend/ tests/
	mypy backend/
	pnpm -C frontend lint

test: test-python test-frontend

test-python:
	pytest -m "not gpu"

test-frontend:
	pnpm -C frontend test

build:
	pnpm -C frontend build

dev:
	pnpm -C frontend dev
