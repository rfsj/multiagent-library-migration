# Métricas

O relatório JSON contém:

- `tests_before`: resultado dos testes antes da migração.
- `tests_after`: resultado dos testes após a migração.
- `old_imports_remaining`: imports restantes da biblioteca de origem.
- `correctly_migrated_uses`: usos migrados corretamente no escopo da tarefa.
- `unmigrated_uses`: chamadas da biblioteca de origem ainda detectadas.
- `false_positives`: usos migrados indevidamente.
- `false_negatives`: usos que deveriam ser migrados e não foram detectados.
- `transformation_errors`: erros de transformação observados.
- `out_of_scope_changes`: alterações fora dos arquivos permitidos.
- `status`: `success` ou `failed`.

As métricas do MVP são simples e orientadas à tarefa inicial. Em versões
posteriores, elas devem ser expandidas para granularidade por chamada de API.
