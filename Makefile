PY := .venv/bin/python

.PHONY: setup test demo demo-claude bench

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -e '.[dev,claude]'

test:
	$(PY) -m pytest -q

demo:
	$(PY) -m demo.run_demo

demo-claude:
	$(PY) -m demo.run_demo --judge claude

bench:
	$(PY) -m bench.run_bench
