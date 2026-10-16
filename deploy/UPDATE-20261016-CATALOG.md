# Recuperação do carregamento de anúncios — 16/10/2026

- Catálogo não depende mais da conclusão de /api/item-logs. O histórico é carregado separadamente depois do catálogo e não impede a exibição dos anúncios.
- Consultas de catálogo e histórico em segundo plano não abrem confirmação de retry de 502. Catálogo tem prazo de 60 segundos; histórico, 20 segundos.
- Falha no catálogo encerra o carregamento, libera a requisição em andamento e mostra erro + botão Tentar novamente na lista. Re-renderizações não repetem automaticamente a mesma consulta que falhou.
- Preservada a deduplicação de requisições simultâneas e os anúncios já carregados em caso de falha ao atualizar. Catálogo vazio é uma resposta válida; resposta sem lista não é tratada como sucesso.

A leitura local de /api/catalog não faz cotação remota de frete. Sem logs atuais de produção, não é possível atribuir a indisponibilidade do servidor à última mudança de fretes. Esta correção trata os bloqueios confirmados no fluxo da interface.

## Implantar
Atualizar public/app.js e public/index.html juntos. O HTML utiliza app.js?v=20261016-catalog-recovery. Reabrir/recarregar a página após atualizar os arquivos no servidor.

## Testes
node tests/catalog_loading.cjs
node tests/async_tracking.cjs
node --check public/app.js

Simulados: histórico lento/timeout, erro 502 no catálogo, prazo expirado, resposta inválida, retry manual e consultas simultâneas. Não foram feitas alterações em produção.
