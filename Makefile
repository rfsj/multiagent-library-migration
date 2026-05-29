.PHONY: run-task run-all test docker-build docker-run import-github prepare-benchmark clean

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

import-github:
	python3 scripts/import_github_project.py $(TASK_ID) $(REPO_URL)

prepare-benchmark:
	python3 scripts/prepare_benchmark_project.py $(TASK_ID) --apply

clean:
	rm -rf experiments/runs/*
