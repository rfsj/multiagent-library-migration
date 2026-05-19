.PHONY: run-task run-all test clean

run-task:
	python3 scripts/run_task.py task_001_read_csv_filter

run-all:
	python3 scripts/run_all.py

test:
	python3 -m pytest

clean:
	rm -rf experiments/runs/*
