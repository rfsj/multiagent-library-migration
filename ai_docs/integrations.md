# Integrações

## LLM Providers

### Google Generative AI (padrão)

**Tipo**: API HTTP (via `langchain_google_genai`)

**Configuração**:
```env
LLM_PROVIDER=google
LLM_MODEL=gemini-3.1-flash-lite
GOOGLE_API_KEY=<sua-chave>
```

**Modelos testados**:
| Modelo | Qualidade | Velocidade | Observação |
|---|---|---|---|
| `gemini-3.1-flash-lite` | Boa com AST | Rápido | Recomendado para maioria dos casos |
| `gemini-2.5-flash` | Boa | Médio | Alternativa estável |
| `gemini-2.5-pro` | Alta | Lento | Pode dar timeout em arquivos >150 linhas |

**Falhas conhecidas**: Timeout 504 para arquivos grandes com `gemini-2.5-pro`. O LangChain faz retry automático com backoff exponencial.

---

### Anthropic Claude (alternativa)

**Tipo**: API HTTP (via `langchain_anthropic`)

**Configuração**:
```env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=<sua-chave>
```

**Uso**: Não foi o foco dos experimentos documentados, mas o framework suporta via troca de variáveis de ambiente.

---

## Dependências Externas de Execução

### pytest (dos projetos migrados)

**Tipo**: CLI local (via `src/tools/test_runner.py` → `subprocess.run`)

**Uso**: `run_pytest(project_dir, log_file)` executa `python -m pytest -q` no `project_dir` e salva saída em `log_file`.

**Configuração**: O projeto de benchmark define seu próprio `pytest.ini` ou `setup.cfg`.

**Cuidado**: O pytest roda no contexto do projeto migrado, não do framework. Dependências do projeto precisam estar instaladas (feito pelo `ValidationAgent._install_dependencies()`).

---

### pip (instalação de dependências)

**Tipo**: CLI local (via `subprocess.run`)

**Uso**: `pip install -r requirements.txt` antes de cada validação de step e antes da validação final.

**Cuidado**: Se o projeto tiver dependências com conflitos de versão com o ambiente do framework, a instalação pode falhar. Usar virtualenv ou containers para isolamento.

---

### diff (sistema operacional)

**Tipo**: CLI local (via `src/tools/diff_analyzer.py` → `subprocess.run`)

**Uso**: `unified_diff(before, after)` executa `diff -ruN` para gerar o `diff.patch` final.

**Uso adicional**: `analyze_diff(before, after, allowed_files)` usa `filecmp.cmp` (sem subprocess) para detectar `out_of_scope_changes`.

**Dependência**: Disponível por padrão em Linux/macOS. Windows pode precisar de WSL ou Git for Windows.

---

### PyPI API (para hash-locked requirements)

**Tipo**: API HTTP (via `urllib.request`)

**Uso**: Quando `requirements.txt` usa `--hash=sha256:`, o `MigrationAgent` consulta `https://pypi.org/pypi/<package>/json` para resolver hashes SHA256 da versão mais recente.

**Cuidado**: Requer conectividade de rede durante a migração de projetos com requisitos hash-locked.

---

## Formato de Dados: Benchmark Tasks

As tasks de benchmark são o "contrato de entrada" do sistema:

```
benchmark/<task_id>/
├── metadata.json          # Contrato de task
└── input_project/         # Projeto Python testável
    ├── requirements.txt
    ├── pytest.ini
    ├── src/
    │   └── *.py           # Código com biblioteca de origem
    └── tests/
        └── test_*.py
```

**metadata.json**:
```json
{
  "task_id": "task_001_read_csv_filter",
  "source_library": "pandas",
  "target_library": "polars",
  "description": "Descrição do que a task testa",
  "expected_changed_files": ["src/orders/processing.py", "requirements.txt"]
}
```

**Pré-condição**: O `input_project` DEVE passar todos os testes antes da migração. Usar `prepare_benchmark_project.py` para corrigir projetos externos que falham no baseline.

---

## Formato de Dados: Artefatos de Execução

Cada run produz artefatos em `experiments/runs/<task_id>_<timestamp>/`:

```
report.json            # Ponto de entrada para análise de resultados
diff.patch             # Todas as mudanças feitas pelo framework
logs/
  diagnosis_plan.json  # Plano completo com migration_steps
  step_NNN_*.json      # Logs de cada step (migration, validation, verdict, repair)
  final_validation.json
snapshots/             # Estado do projeto em cada ponto no tempo
prompts/               # System prompts usados (para reprodução)
```

Todos os arquivos JSON seguem schemas Pydantic definidos em `src/agents/`.

**`logs/llm_proxy.jsonl`**: Arquivo de log de todas as chamadas ao LLM (uma entrada JSON por linha). Inclui eventos `request` (com modelo, timestamp e mensagens) e `response` (com `generations`, `tool_calls` para Anthropic, `function_call` para Gemini, e `usage`). É o primeiro lugar para inspecionar quando um agente produziu saída inesperada.

---

## Integrações Futuras Potenciais

O sistema foi projetado para ser extensível:

- **Novos providers LLM**: Adicionar um `if provider == "novo_provider"` em `src/llm.py`
- **Novos pares de biblioteca**: Adicionar tasks em `benchmark/` e, opcionalmente, padrões em `pattern_scanner.py` e `ast_transformer.py`
- **CI/CD**: `scripts/run_all.py` retorna exit code 0 apenas se todas as tasks tiverem sucesso; pode ser integrado a pipelines

---

## Dependências Python do Framework

Principais (do `pyproject.toml`):
```
langchain
langgraph
langchain-anthropic
langchain-google-genai
pydantic>=2
python-dotenv
```

Dependências de desenvolvimento:
```
pytest
ruff
```

**Importante**: As dependências de migração (`pandas`, `polars`) ficam nos `requirements.txt` dos projetos de benchmark, não do framework em si. O framework as instala dinamicamente via pip durante a execução.
