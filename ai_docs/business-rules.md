# Regras de Negócio

## Regras Críticas

### R1 — Testes Nunca São Modificados

**Descrição**: Nenhum agente do sistema pode modificar, criar ou remover arquivos de teste.

**Justificativa**: A única evidência válida de sucesso de migração é que os testes originais continuam passando. Modificar testes para "forçar" um pass invalida o experimento.

**Implementação**: `ValidationAgent.validate_step()` detecta qualquer arquivo modificado fora de `allowed_files` e rejeita o step. O `DiagnosisAgent` nunca inclui arquivos de teste em `migration_steps`.

**Verificação**: `out_of_scope_changes > 0` → verdict `rejected_implementation`.

---

### R2 — Escopo Declarado Explicitamente por Step

**Descrição**: Cada step de migração declara explicitamente quais arquivos pode modificar (`allowed_files`) e, opcionalmente, quais símbolos (`allowed_symbols`).

**Justificativa**: Previne que o MigrationAgent faça "efeitos colaterais" acidentais em outros arquivos.

**Implementação**: 
- `DiagnosisAgent._sanitize_migration_steps()` valida e restringe `allowed_files` a arquivos de produção conhecidos
- `MigrationAgent._validate_step_scope()` lança exceção se o step tentar acessar arquivo fora da whitelist
- `ValidationAgent` verifica o diff após migração

---

### R3 — Nenhuma Regra Específica por Projeto

**Descrição**: O framework não pode ter heurísticas que reconheçam uma task por nomes de colunas, valores esperados, funções, arquivos, caminhos ou mensagens de erro exclusivas de um projeto específico.

**Justificativa**: A generalização é o objetivo principal. Soluções "hard-coded" para uma task não provam capacidade de migração geral.

**Implementação**: `AGENTS.md` define como lei: "Toda melhoria deve aumentar a capacidade geral do framework de receber o maior número possível de projetos reais." Qualquer padrão detectado deve ser descrito em termos de API, AST, fluxo produtor/consumidor ou contrato semântico.

---

### R4 — DiagnosisAgent Opera em Read-Only

**Descrição**: O agente de diagnóstico nunca escreve código, modifica dependências ou remove testes.

**Justificativa**: Separação de responsabilidades. O diagnóstico é análise, não execução.

**Implementação**: Apenas `Path.read_text()` é chamado; nenhum `Path.write_text()` existe no DiagnosisAgent. `"read_only": true` é registrado no `diagnosis_plan.json` como auditoria.

---

### R5 — Migração Deve Preservar Comportamento Observável

**Descrição**: A migração é aceita somente se todos os testes originais passarem após a migração.

**Justificativa**: O objetivo é substituição transparente — o código migrado deve produzir os mesmos resultados observáveis.

**Implementação**: `ValidationAgent.validate_step()` exige `tests["passed"] == True`. `final_validate()` exige a mesma condição na suite completa.

---

### R6 — Biblioteca de Origem Deve Ser Completamente Removida

**Descrição**: Uma migração só é considerada bem-sucedida na validação final quando não restam imports ou chamadas de API da biblioteca de origem.

**Justificativa**: Código híbrido (pandas + polars no mesmo projeto) indica migração parcial, não completa.

**Implementação**: `final_validate()` verifica `old_imports_remaining == 0` e `unmigrated_uses == 0`. Uma conta `old_imports_remaining` via AST (import statements). A outra conta chamadas de API via atributo do alias (ex: `pd.xxx`).

**Exceção**: Uso da biblioteca de origem em arquivos de teste é permitido na validação final (os testes podem importar pandas para comparação ou fixtures).

---

### R7 — Steps com Produtor Falhado Pulam Retries

**Descrição**: Se um step A (produtor) falha após MAX_STEP_RETRIES e um step B subsequente depende do DataFrame de A, o step B é marcado como falho na primeira tentativa sem retries adicionais.

**Justificativa**: Se A ainda retorna DataFrames pandas, qualquer quantidade de retries em B nunca vai funcionar — B receberia pandas e tentaria chamar APIs Polars nele.

**Implementação**: `migration_flow.select_next_step()` detecta `upstream_failed_files` via `dataframe_flow_analysis`. `validation_flow.build_validation_node()` verifica `step.get("upstream_failed_files")` e pula retries imediatamente.

---

### R8 — Replanificação é Limitada

**Descrição**: O sistema pode descartar o plano de migração e reiniciar o diagnóstico com feedback de falha. Isso é permitido no máximo 2 vezes.

**Justificativa**: Previne loops infinitos. Se após 2 replanificações o sistema ainda não consegue migrar, é necessária intervenção humana.

**Implementação**: `MAX_REPLAN_ATTEMPTS = 2` em `validation_flow.py`. Histórico completo de replanificações é registrado em `report.json`.

---

## Validações e Restrições

| Validação | Onde | Consequência de Falha |
|---|---|---|
| Testes antes da migração devem passar | `run_task.py` | Abort (baseline_failed) |
| Testes pós-step devem passar | `validate_step()` | Retry ou rejected |
| Sem mudanças fora de allowed_files | `validate_step()` | Retry ou rejected |
| 0 imports origem no arquivo migrado | `validate_step()` | Retry ou rejected |
| 0 usos de API da origem no símbolo migrado | `validate_step()` | Retry ou rejected |
| 0 imports origem em todo o projeto | `final_validate()` | Status failed |
| Código migrado é Python 3.9 válido | `MigrationAgent` | Retry com feedback de sintaxe |

---

## Políticas de Retry e Replan

```
Por step:
  └─ MAX_STEP_RETRIES = 3
     └─ Se excedido: step vira failed, arquivo recebe comentário MANUAL REVIEW, fluxo continua

Por execução:
  └─ MAX_REPLAN_ATTEMPTS = 2
     └─ Se excedido: workflow aborta (abort_reason registrado)

LLM Structured Output:
  └─ MAX_MIGRATION_STRUCTURED_OUTPUT_ATTEMPTS = 2
     └─ Se LLM retorna None: retry imediato

Implementation Review:
  └─ MAX_IMPLEMENTATION_REVIEW_REVISIONS = 2
     └─ Revisões antes de ir para validação
```

---

## Marcadores de Revisão Manual

Quando um step falha após MAX_STEP_RETRIES, o framework insere um bloco de comentário no arquivo:

```python
# &&&&&&&&&&&&& MIGRATION MANUAL REVIEW START step_001
# File: src/analytics/loaders.py
# Planned change: ...
# Automatic migration failed after 3 attempts.
# Keep the original behavior and migrate this file manually before approval.
# Validation rationale: ...
# Validation feedback: ...
# &&&&&&&&&&&&& MIGRATION MANUAL REVIEW END step_001
```

Esses marcadores são auditáveis e identificam exatamente o que precisa de atenção humana.

---

## Critério de Sucesso de uma Run

Uma run é `status: "success"` somente quando ALL das seguintes condições são verdadeiras:

1. `tests_before == "passed"` (baseline funcionava)
2. `tests_after == "passed"` (migração não quebrou testes)
3. `final_validation.status == "approved"` — que requer:
   - `tests["passed"] == True`
   - `old_imports_remaining == 0`
   - `unmigrated_uses == 0`
   - `out_of_scope_changes == 0`
