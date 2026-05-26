# AGENTS.md

Este projeto avalia um fluxo multiagente para migracao controlada de
bibliotecas em projetos Python. O caso inicial e `pandas -> polars`, usando uma
tarefa de benchmark pequena e reproduzivel.

O objetivo principal e preservar auditabilidade: cada execucao deve registrar
o plano, as alteracoes, as validacoes, os testes, o diff e o relatorio final em
`experiments/runs/`.

## Escopo do Projeto

- Migrar usos suportados da biblioteca de origem para a biblioteca alvo.
- Manter o comportamento observavel do projeto de entrada.
- Evitar alteracoes fora dos arquivos planejados.
- Preservar testes existentes como criterio de regressao.
- Produzir evidencias replicaveis para comparacao experimental.

## Fluxo dos Agentes

O workflow e dividido em tres partes: diagnosis, migration e validation. Cada
parte tem responsabilidades separadas para reduzir acoplamento e facilitar
auditoria.

## Isolamento de Escopo

Toda alteracao deve pertencer a apenas um escopo por vez: diagnosis, migration
ou validation. Nao altere outro escopo para facilitar a implementacao do escopo
atual.

Se uma tarefa de um escopo exigir mudanca em outro escopo, nao faca essa
alteracao automaticamente. Registre a necessidade, explique o motivo e sugira
uma alternativa; prossiga apenas quando houver autorizacao explicita.

### 1. Diagnosis

Responsavel por entender o projeto antes de qualquer edicao.

- Executa em modo somente leitura.
- Localiza arquivos de dependencia, arquivos afetados e testes relacionados.
- Identifica usos da biblioteca de origem.
- Estima a complexidade por arquivo afetado.
- Gera uma lista ordenada de `migration_steps`.
- Define `allowed_files` para cada etapa.
- Escreve `logs/diagnosis_plan.json`.

Contrato principal:

- Nao modificar codigo.
- Nao alterar dependencias.
- Nao remover ou editar testes.
- Planejar passos atomicos, preferencialmente um arquivo por etapa.
- Declarar explicitamente qualquer arquivo que a migracao podera modificar.

### 2. Migration

Responsavel por executar uma etapa planejada por vez.

- Consome os `migration_steps` produzidos no diagnostico.
- Altera apenas arquivos listados em `allowed_files` e, quando necessario,
  arquivos de dependencia.
- Atualiza a implementacao da biblioteca de origem para a biblioteca alvo.
- Mantem a menor mudanca suficiente para preservar o comportamento.
- Escreve logs por etapa, como `logs/step_001_migration.json`.

Contrato principal:

- Nao editar testes para fazer a migracao passar.
- Nao fazer refatoracoes cosmeticas fora do escopo.
- Nao tocar em arquivos nao autorizados pelo plano.
- Preferir mudancas pequenas, revisaveis e associadas a uma etapa especifica.

### 3. Validation

Responsavel por validar cada etapa e a execucao final de forma independente.

- Compara o snapshot anterior com o projeto migrado.
- Detecta arquivos alterados fora do escopo.
- Instala dependencias quando necessario.
- Executa a suite de testes com `pytest`.
- Verifica se ainda existem imports ou usos da biblioteca de origem.
- Escreve logs por etapa e `logs/final_validation.json`.

Contrato principal:

- Rejeitar etapas com alteracoes fora de `allowed_files`.
- Rejeitar etapas quando testes falham.
- Rejeitar a validacao final se restarem usos da biblioteca de origem.
- Registrar o motivo da aprovacao ou rejeicao em JSON.

## Artefatos de Execucao

Cada execucao deve preservar:

- `report.json`
- `diff.patch`
- `logs/tests_before.log`
- `logs/diagnosis_plan.json`
- `logs/*_migration.json`
- `logs/*_validation.json`
- `logs/final_validation.json`
- `prompts/*.md`

## Diretrizes para Evolucao

- Manter o contrato JSON do `DiagnosisAgent` documentado em
  `docs/planner_json_api.md`.
- Atualizar prompts versionados em `prompts/` quando o comportamento esperado
  dos agentes mudar.
- Adicionar tarefas de benchmark em `benchmark/<task_id>/` com `metadata.json`,
  projeto de entrada e testes.
- Manter o `README.md` como ponto de entrada rapido e os detalhes metodologicos
  em `docs/`.
