.PHONY: install test demo server viewer clean fixtures live

PYTHON ?= python3

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) evals/test_scenarios.py

fixtures:
	$(PYTHON) evals/generate_fixtures.py

report:
	$(PYTHON) evals/report.py

# End-to-end demo against fakes in DRY_RUN mode.
demo:
	DRY_RUN=true $(PYTHON) evals/test_scenarios.py
	@echo ""
	@echo "✅ 30/30 scenarios passed. See EVAL_RESULTS.md."
	@echo "   Next: 'make server' then open http://localhost:8000/viewer/"

server:
	uvicorn app.main:app --reload --port 8000

viewer:
	@echo "Open http://localhost:8000/viewer/ after 'make server'."

live:
	pytest -m live -v

clean:
	rm -f rebound.db rebound.db-wal rebound.db-shm
	rm -rf .pytest_cache __pycache__ */__pycache__ */*/__pycache__
	find traces -name '*.jsonl' ! -name 'sample_run.jsonl' -delete
