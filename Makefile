PY := .venv/bin/python

.PHONY: setup test demo demo-claude agent agent-injection agent-injection-subtle bench

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -e '.[dev,claude]'

test:
	$(PY) -m pytest -q

demo:
	$(PY) -m demo.run_demo

demo-claude:
	$(PY) -m demo.run_demo --judge claude

agent:
	$(PY) -m demo.run_agent

agent-injection:
	$(PY) -m demo.run_agent --scenario injection --naive

agent-injection-subtle:
	$(PY) -m demo.run_agent --scenario injection-subtle --naive

bench:
	$(PY) -m bench.run_bench
