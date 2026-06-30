.PHONY: setup start stop check logs

# Rebuild venv and install (only when needed)
setup:
	python3 -m venv .venv --clear
	.venv/bin/pip install -e .

# Start DocFlow (sets up venv if missing)
start:
	@test -f .venv/bin/docflow || $(MAKE) setup
	@.venv/bin/docflow start

stop:
	@.venv/bin/docflow stop

check:
	@.venv/bin/docflow check

logs:
	@tail -f ~/.docflow/logs/docflow.log
