.PHONY: run-task run-all test docker-build docker-run docker-planner-eval docker-migration-eval docker-validation-eval docker-full-eval import-github prepare-benchmark clean

run-task:
	python3 scripts/run_task.py task_001_read_csv_filter

run-all:
	python3 scripts/run_all.py

test:
	python3 -m pytest

docker-build:
	docker build -t multiagent-library-migration .

docker-run:
	docker compose run --rm migration-runner

docker-planner-eval:
	docker compose run --rm migration-runner python scripts/run_planner_matrix.py --tasks $${TASK_ID:-task_020_full_analytics_pipeline} --configs $${CONFIGS:-v3_symbol_analysis} --attempts $${ATTEMPTS:-3} --k $${K:-1,3}

docker-migration-eval:
	docker compose run --rm migration-runner python scripts/run_migration_matrix.py --planner-matrix $${PLANNER_MATRIX:?set PLANNER_MATRIX=experiments/evaluations/planner_matrix_<timestamp>} --only-valid-plans --k $${K:-1,3}

docker-validation-eval:
	docker compose run --rm migration-runner python scripts/run_validation_matrix.py --runs $${RUNS:?set RUNS=experiments/evaluations/migration_matrix_<timestamp> or a run_dir}

docker-full-eval:
	docker compose run --rm migration-runner python scripts/run_evaluation_matrix.py --tasks $${TASK_ID:-task_020_full_analytics_pipeline} --configs $${CONFIGS:-v3_symbol_analysis} --attempts $${ATTEMPTS:-3} --k $${K:-1,3}

import-github:
	python3 scripts/import_github_project.py $(TASK_ID) $(REPO_URL)

prepare-benchmark:
	python3 scripts/prepare_benchmark_project.py $(TASK_ID) --apply

clean:
	rm -rf experiments/runs/*
