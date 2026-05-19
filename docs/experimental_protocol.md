# Protocolo Experimental

## Objetivo

Avaliar um fluxo multiagente para migração controlada de bibliotecas em
projetos Python, começando pelo caso `pandas -> polars`.

## Hipótese inicial

Separar diagnóstico, migração e validação reduz alterações fora do escopo e
melhora a auditabilidade em comparação com uma migração feita em uma única
etapa opaca.

## Fluxo do MVP

1. Copiar a tarefa de benchmark para `experiments/runs`.
2. Instalar dependências do projeto de entrada.
3. Executar testes antes da migração.
4. Executar o agente de diagnóstico em modo somente leitura.
5. Executar o agente de migração uma etapa por vez.
6. Validar cada etapa com agente independente.
7. Executar testes finais.
8. Gerar diff e relatório JSON.

## Controles

- O agente de diagnóstico não altera código.
- O agente de migração altera somente arquivos planejados e dependências.
- Testes não são modificados para forçar aprovação.
- `pandas` não é removido antes da validação final.
- Logs, prompts e versões do ambiente são preservados por execução.

## Caso inicial

`task_001_read_csv_filter` cobre leitura de CSV, filtro, seleção de colunas e
ordenação.
