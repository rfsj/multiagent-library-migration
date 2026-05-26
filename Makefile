.PHONY: run-task run-all test docker-build docker-run clean

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

clean:
	rm -rf experiments/runs/*
