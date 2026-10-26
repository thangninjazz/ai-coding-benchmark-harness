.PHONY: install lint test verify docker docker-test clean

PY ?= python

install:
	$(PY) -m pip install -r requirements-dev.txt

lint:
	$(PY) -m ruff check .
	$(PY) -m mypy

test:  ## fast self-tests
	$(PY) -m pytest -m "not slow and not docker" --cov=harness --cov-fail-under=85

verify:  ## test the tests: gold passes, baseline fails, every mutant is killed (x3)
	$(PY) -m harness.cli verify --repeat 3

docker:
	docker build -t ai-benchmark-harness:latest .

docker-test: docker  ## run the reference solutions in a locked-down container, then the backend tests
	docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
		--pids-limit 256 --memory 1g --tmpfs /tmp:rw,size=128m \
		ai-benchmark-harness:latest run-all --verify-gold
	$(PY) -m pytest -m docker -v

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
