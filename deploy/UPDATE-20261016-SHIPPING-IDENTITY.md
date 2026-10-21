# Preservação de frete na sincronização — 16/10/2026

Diagnóstico fornecido de MLB7763386810: cotação de R$ 106,85 retornou em 0,12 segundo, enquanto a base local estava pending/sem data. O script de diagnóstico não grava a cotação, por intenção.

Encontrada inconsistência no código: a cotação usa shipping_cost_basis(item, account), que inclui seller_id da conta; os três caminhos de sincronização preservavam o snapshot sem informar a conta, enquanto synced_catalog_item não incluía seller_id. Isso podia invalidar um frete correto quando a sincronização recriava o registro.

Correção:
- Registro sincronizado inclui seller_id.
- Os caminhos de atualização de preços, sincronização completa e atualização automática passam a conta ao preservar frete.
- Registros antigos sem seller_id são comparados usando o vendedor da conta, sem necessidade de apagar a base.
- Mudanças reais de vendedor, modalidade ou dimensões continuam invalidando a cotação.

Implantar server.py atualizado e reiniciar competidor. As correções anteriores de public/app.js e public/index.html devem estar implantadas. Recarregar Anúncios; a consulta pendente usa o fluxo já existente e a sincronização seguinte preserva o valor. Não gravamos valor fixo de R$ 106,85 nem inferimos zero.

511 testes aprovados; incluídos três testes de regressão de identidade do vendedor, preservação repetida de registro legado e invalidação por mudanças reais. Script diagnose-shipping.py agora informa se o registro valida com/sem a conta e se possui seller_id, sem exibir credenciais. Não houve acesso à produção.
