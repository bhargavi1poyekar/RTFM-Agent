# Makefile
.PHONY: help install dev-install test lint format clean run

help:
	@echo "Available commands:"
	@echo "  install       Install production dependencies"
	@echo "  dev-install   Install development dependencies"
	@echo "  test          Run tests"
	@echo "  lint          Run linters"
	@echo "  format        Format code"
	@echo "  clean         Clean cache and build files"
	@echo "  run           Run development server"

install:
	pip install -r requirements/base.txt

dev-install:
	pip install -r requirements/dev.txt
	pre-commit install

test:
	pytest tests/ -v --cov=src/rtfm_agent --cov-report=html

lint:
	flake8 src/ tests/
	mypy src/
	pylint src/rtfm_agent/

format:
	black src/ tests/
	isort src/ tests/

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".coverage" -exec rm -rf {} +
	find . -type d -name "htmlcov" -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache

run:
	python run.py