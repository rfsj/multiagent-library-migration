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

## Generalizacao do Multiagente

O objetivo do sistema multiagente e migrar diferentes codigos e projetos Python,
nao se adaptar a um unico repositorio especifico. Toda melhoria deve aumentar a
capacidade geral do framework de receber o maior numero possivel de projetos
reais, mantendo o contrato de auditoria.

Ao adicionar suporte para um caso encontrado em um projeto real, prefira
implementar uma regra, abstracao, scanner, validacao ou contrato reutilizavel
para outros projetos. Evite solucoes que dependam de nomes, caminhos, fixtures
ou estruturas exclusivas de um repositorio, a menos que isso esteja claramente
isolado como uma tarefa de benchmark e documentado como excecao.

Se um projeto real revelar uma limitacao do framework, registre a limitacao de
forma auditavel e evolua o comportamento geral do diagnosis, migration ou
validation. Nao esconda falhas alterando testes ou moldando o projeto de entrada
apenas para um caso passar.

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
- Quando um arquivo tiver multiplas funcoes migraveis, pode definir
  `allowed_symbols` para criar etapas por funcao/classe e medir sucesso
  parcial de forma mais auditavel.
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
- Quando `allowed_symbols` existir, limita a alteracao ao simbolo planejado
  dentro do arquivo.
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
- Em etapas com `allowed_symbols`, validar uso remanescente da biblioteca de
  origem no simbolo planejado, sem exigir que o arquivo inteiro ja esteja
  migrado.
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

## Projetos Reais em Benchmark

O runner nao executa um clone Git arbitrario diretamente. Toda tarefa deve usar
o formato auditavel:

```text
benchmark/<task_id>/
  metadata.json
  input_project/
```

Para importar um projeto GitHub real, use sempre:

```bash
python3 scripts/import_github_project.py <task_id> <repo_url>
```

Esse comando faz clone temporario, remove `.git`, copia o projeto para
`input_project/` e cria `metadata.json`. Nao use apenas `git clone` dentro de
`benchmark/` esperando que o runner reconheca automaticamente o projeto.

Depois de importar, execute:

```bash
python3 scripts/run_task.py <task_id>
```

Se o baseline falhar antes da migracao, use a ferramenta separada de preparacao
de benchmark. Ela nao pertence aos escopos diagnosis, migration ou validation e
nao deve migrar bibliotecas:

```bash
python3 scripts/prepare_benchmark_project.py <task_id>
python3 scripts/prepare_benchmark_project.py <task_id> --apply
```

O modo sem `--apply` apenas registra propostas em
`benchmark/<task_id>/preparation/preparation_report.json`. O modo `--apply`
pode executar correcoes basicas e auditaveis de baseline, como ajustar
`pytest.ini` de `test` para `tests` quando a pasta existe, ou criar um loader
YAML `load_config` quando os testes importam explicitamente
`<pacote>.config.load_config` e o modulo esta ausente.

Essa preparacao deve ser usada apenas para tornar o projeto original testavel
antes da migracao. Nao esconda falhas de migracao nessa etapa.

`benchmark/` e ignorado pelo git para evitar versionar clones externos e
benchmarks locais gerados durante experimentos.

## Diretrizes para Evolucao

- Manter o contrato JSON do `DiagnosisAgent` documentado em
  `docs/planner_json_api.md`.
- Atualizar prompts versionados em `prompts/` quando o comportamento esperado
  dos agentes mudar.
- Adicionar tarefas de benchmark em `benchmark/<task_id>/` com `metadata.json`,
  projeto de entrada e testes.
- Manter o `README.md` como ponto de entrada rapido e os detalhes metodologicos
  em `docs/`.
