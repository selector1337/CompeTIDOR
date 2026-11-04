# Atualização de 04/11/2026

Atualizar em conjunto `server.py`, `goal_pricing.py`, `public/app.js`, `public/goal-pricing.js`, `public/index.html` e `public/styles.css`. Reiniciar `competidor` após copiar os arquivos. O HTML inclui nova versão dos recursos para evitar cache antigo. Não substituir os arquivos de dados de produção.

## Comportamento

- Produtos sem estoque: todas as contas agrupam por SKU; uma conta específica mostra seus anúncios individualmente. PDF/Excel respeitam a seleção e o período.
- Perdas de catálogo: início da perda permanece fixo durante a mesma ocorrência, com ordenação decrescente e exportação completa. Não há mais truncamento em 200 linhas. Dados antigos sem registro do início passam a ter a primeira observação nesta versão; não é possível reconstruir a hora exata de perdas antigas a partir da última sincronização.
- Metas: preço original riscado quando superior ao atual, usando o mesmo anúncio de referência. Regras existentes podem ter valores, intervalos, pisos e programação editados sem restaurar preços. Original, últimas ações, histórico e preços pendentes são preservados. A modalidade permanece fixa na regra existente; outra modalidade usa uma nova regra.
- Programação opcional: dias da semana, data específica e horário (Brasília). Campos vazios significam sem restrição. Filtros se combinam. Intervalos que atravessam meia-noite pertencem ao dia de início. Fora da janela, confirmações pendentes continuam sendo verificadas, mas novas reduções não são enviadas. A simulação de duração continua indicando tempo contínuo, sem somar as pausas do calendário.
- Falhas transitórias e divergências continuam sendo conferidas automaticamente. Os bloqueios agora participam da previsão de nova conferência. Não são removidas as proteções de margem, vendas desatualizadas ou alteração externa de preço. Indisponibilidade e rejeições da API não podem ser eliminadas por garantia local.
- Anunciar produtos: seleção das fotos persistente entre reconstruções e uploads; arrastar muda a ordem, inclusive entre fotos importadas e enviadas. A primeira selecionada vira capa. Máximo de 12 selecionadas por anúncio mantido; fotos desmarcadas não ocupam a seleção para novos uploads.
- Retirados tetos locais de combinações em cópia e publicação assistida. As chamadas continuam na fila e sob os controles existentes de concorrência e retentativa.
- Cada sucesso de publicação/cópia é salvo antes de continuar o lote. Catálogo aberto é atualizado em segundo plano em intervalos de 30 segundos, sem atualização enquanto se digita. O próprio fluxo de publicação continua recarregando a lista ao terminar.
- Login sem regravar toda a base a cada tentativa; configuração OAuth deixa de bloquear a abertura da tela. Botão informa o processamento e evita envios duplicados.
- Vendas recentes e SKUs de hoje usam os pedidos já conciliados para faturamento, sem nova busca. Dashboard consulta os dados locais a cada 30 segundos enquanto visível. Gravações concorrentes preservam o snapshot mais recente por conta.

## Validação

### Complemento: salvamento das regras

O endpoint de salvamento agora retorna uma operação assíncrona (HTTP 202). Atualizar `server.py`, `goal_pricing.py`, `public/goal-pricing.js` e `public/index.html` juntos: o frontend acompanha a confirmação, mantém a identidade da requisição em tentativas após falha de conexão e reutiliza a operação já iniciada. Uma falha na consulta do acompanhamento não é tratada como prova de que o cadastro falhou. O retorno da regra salva atualiza a tela diretamente, sem depender de uma segunda consulta. Regras esperando para salvar recebem prioridade sobre o próximo anúncio do mesmo SKU; a alteração já enviada ao Mercado Livre é concluída antes de liberar a configuração.

584 testes de backend passaram, além dos testes de navegador para salvamento, falha de acompanhamento e retomada sem duplicar o POST. Não há promessa de latência fixa durante indisponibilidade da API ou do servidor.

Suíte Python com integrações simuladas; cenários novos de edição ativa, pendência durante edição, calendário noturno, perdas de catálogo, ordenação/exclusão das fotos e aproveitamento dos pedidos já consultados. Testes de navegador para edição de regras, filtros, fotos arrastáveis e login desktop/mobile. Nenhuma publicação ou alteração de preço real foi executada durante a validação.
