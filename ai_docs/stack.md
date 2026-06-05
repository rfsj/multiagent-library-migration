# Stack Tecnológica

## Linguagens e Runtime

| Item | Versão | Observação |
|---|---|---|
| Python | 3.9+ (testado em 3.9.6) | 3.9 é o mínimo suportado; PEP 604 union types precisam de `from __future__ import annotations` |
| Python (recomendado) | 3.11 | Mais performático; `.env.example` documenta `PYTHON_VERSION=3.11` |

## Frameworks Principais

| Framework | Versão | Papel |
|---|---|---|
| **LangChain** | ≥0.3 | Composição de prompts, chains, structured output via Pydantic |
| **LangGraph** | ≥0.6 | Orquestração do workflow multiagente com grafo cíclico |
| **Pydantic** | v2 | Validação de schemas JSON para output estruturado dos LLMs |

## LLM Providers Suportados

O provider é configurado via `LLM_PROVIDER` e `LLM_MODEL` no `.env`:

| Provider | Configuração | Modelos testados |
|---|---|---|
| Google | `LLM_PROVIDER=google` + `GOOGLE_API_KEY` | `gemini-3.1-flash-lite` (recomendado), `gemini-2.5-flash`, `gemini-2.5-pro` |
| Anthropic | `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |

**Guia de seleção de modelo:**

- `gemini-3.1-flash-lite` + `MIGRATION_AST_FALLBACK=1` — melhor custo/benefício para a maioria das tasks
- `gemini-2.5-pro` — maior qualidade, pode dar timeout 504 em arquivos grandes (>200 linhas)
- `claude-sonnet-4-6` — alternativa robusta via Anthropic

## Bibliotecas de Migração (domínio)

| Biblioteca | Versão testada | Papel |
|---|---|---|
| **pandas** | 2.2.3 | Biblioteca de origem (migração de saída) |
| **polars** | 1.17.1 | Biblioteca de destino (migração de entrada) |
| **pytest** | 8.3.4 | Executor de testes dos projetos de benchmark |

## Ferramentas de Desenvolvimento

| Ferramenta | Uso |
|---|---|
| `python-dotenv` | Carrega variáveis de ambiente do `.env` |
| `pyproject.toml` | Configuração do projeto (build system, dependências, linting) |
| `ruff` | Linting e formatação |
| `pytest` | Testes do framework (não dos projetos migrados) |

## Infraestrutura de Execução

- **Local**: Execução direta via `python3 scripts/run_task.py`
- **Docker**: `Dockerfile` + `compose.yaml` disponíveis para execução containerizada
- **Sem cloud required**: todos os experimentos rodam localmente; LLM calls vão para APIs externas

## Arquitetura Geral

```
.env (config)
    ↓
scripts/run_task.py
    ↓
WorkflowState + LangGraph StateGraph
    ↓
┌─────────────────────────────────────────┐
│          WORKFLOW NODES                 │
│                                         │
│  DiagnosisAgent ──→ LLM (diagnosis_v1) │
│       ↓                                 │
│  MigrationAgent ──→ LLM (migration_v1)  │
│       ↓  ↑ retry                        │
│  ValidationAgent ──→ LLM (validation_v1)│
│       ↓  ↑ repair                       │
│  RepairAgent ──→ LLM (repair_v1)       │
│  ImplementationReviewAgent              │
└─────────────────────────────────────────┘
    ↓
ValidationAgent.final_validate()
    ↓
report.json + diff.patch
```

## Estrutura de Artefatos por Execução

```
experiments/runs/<task_id>_<timestamp>/
├── project/                    # Estado final do projeto migrado
├── snapshots/
│   ├── before_migration/       # Snapshot inicial (referência)
│   └── before_step_NNN/        # Snapshot antes de cada step
├── logs/
│   ├── diagnosis_plan.json
│   ├── dataframe_flow_analysis.json
│   ├── step_NNN_migration.json
│   ├── step_NNN_validation.json
│   ├── step_NNN_verdict.json
│   ├── step_NNN_repair_NN.json
│   └── final_validation.json
├── prompts/                    # Cópia dos prompts usados (auditoria)
├── diff.patch
└── report.json
```
