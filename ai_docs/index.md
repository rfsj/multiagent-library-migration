# Documentação: multiagent-library-migration

## Visão Geral

Framework de pesquisa experimental para migração controlada e auditável de bibliotecas Python usando um pipeline multiagente baseado em LLMs. O caso de uso validado é `pandas → polars`.

O objetivo é estudar a viabilidade de automatizar migrações de bibliotecas preservando comportamento observável, mantendo auditabilidade completa e sem permitir que os agentes "trapaceiem" modificando testes ou criando regras específicas por projeto.

## Documentação Disponível

### Arquitetura e Stack
- [Stack Tecnológica](stack.md) — Linguagens, frameworks, LLM providers, ferramentas de execução
- [Padrões de Design](patterns.md) — Arquitetura multiagente, LangGraph, padrões de retry e auditoria

### Funcionalidades e Regras
- [Funcionalidades](features.md) — Pipeline de migração, agentes, ferramentas de análise
- [Regras de Negócio](business-rules.md) — Contratos de escopo, critérios de aceitação, restrições de generalização
- [Gotchas](gotchas.md) — Limitações de modelos LLM, armadilhas pandas→polars, comportamentos não óbvios

### Integrações
- [Integrações](integrations.md) — LLM providers, dependências externas, formato de experimentos

### Roadmap
- [Melhorias Sugeridas](improvements.md) — Otimizações priorizadas para próximas iterações

## Início Rápido

```bash
# 1. Configurar ambiente
cp .env.example .env
# Editar .env com sua chave de API e modelo

# 2. Rodar uma task de benchmark
python3 scripts/run_task.py task_001_read_csv_filter

# 3. Ver resultado
cat experiments/runs/task_001_*/report.json

# 4. Rodar todas as tasks
python3 scripts/run_all.py
```

## Estrutura do Repositório

```
multiagent-library-migration/
├── src/
│   ├── agents/          # 5 agentes LLM (diagnosis, migration, validation, repair, review)
│   ├── graph/           # Orquestração LangGraph (state, workflow, flows)
│   ├── tools/           # Ferramentas determinísticas (scanner, ast_transformer, diff)
│   └── evaluation/      # Métricas e geração de relatórios
├── prompts/             # System prompts versionados para cada agente
├── benchmark/           # Tasks de benchmark (input_project + metadata.json)
├── experiments/runs/    # Artefatos gerados por cada execução (gitignored)
├── scripts/             # Entry points (run_task.py, run_all.py, import_github_project.py)
├── docs/                # Documentação do protocolo experimental
└── tests/               # Testes do próprio framework
```

## Conceitos-Chave

| Termo | Definição |
|---|---|
| **Task** | Um projeto Python de benchmark com código fonte pandas + testes |
| **Run** | Uma execução de uma task, gera artefatos auditáveis em `experiments/runs/` |
| **Step** | Uma etapa de migração (normalmente um arquivo ou grupo acoplado) |
| **Verdict** | Decisão da validação sobre um step: `accepted`, `rejected_implementation`, `rejected_plan` |
| **Retry** | Re-execução de um step com feedback estruturado (máx 3 tentativas) |
| **Replan** | Re-diagnóstico completo com feedback de falha (máx 2 vezes) |
| **AST Fallback** | Transformador determinístico que corrige padrões pandas que o LLM deixou escapar |
