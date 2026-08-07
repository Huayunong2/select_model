.PHONY: test check doctor smoke clean package release-check

PYTHON ?= python

check: clean
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/select_model.py doctor --strict

release-check: clean
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/release_check.py

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v

doctor:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/select_model.py doctor

smoke:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/select_model.py route --input examples/route-input.json --history /tmp/select-model-history.jsonl --format markdown
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/select_model.py dispatch --route examples/route-result.json --context examples/context.json --dry-run

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete

package: release-check clean
	$(PYTHON) scripts/build_skill.py --output dist/skill.zip
