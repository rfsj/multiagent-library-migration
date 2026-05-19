FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY benchmark ./benchmark
COPY docs ./docs
COPY prompts ./prompts
COPY Makefile ./

RUN pip install --no-cache-dir -e .

CMD ["python", "scripts/run_task.py", "task_001_read_csv_filter"]
