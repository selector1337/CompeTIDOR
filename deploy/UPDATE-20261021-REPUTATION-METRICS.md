# Reputação: métricas oficiais e ausência de dados

A Dashboard exibe percentual, quantidade de vendas afetadas e período retornados em seller_reputation.metrics de /users/{seller_id}. Reclamações, cancelamentos pelo vendedor e atraso no despacho são independentes; não usar transactions.canceled, pois inclui outros cancelamentos.

Mediações: exibidas quando houver um campo metrics.mediations confirmado na resposta. A documentação pública consultada não garante esse campo. A ausência é exibida como "Não informado", nunca 0%. A quantidade de mediações abertas de /claims não equivale ao percentual de reputação.

Agências ML e Flex: removidos os antigos scores artificiais de 100 menos a taxa geral de atraso. Não foi identificado endpoint público documentado que devolva as notas específicas do painel do vendedor. A interface informa a indisponibilidade. Para integrar esses indicadores é necessário obter do Mercado Livre um recurso autorizado com período e definição da métrica; não é correto apresentar uma amostra de pedidos como nota oficial.

Dados sem retorno não viram zero. Dados preservados de consulta anterior são identificados; métricas sob proteção mostram também a taxa sem proteção, quando enviada pela API. Não são aplicados limites arbitrários de cores.

Depois de publicar os arquivos no repositório, atualizar o servidor e reiniciar competidor. Recarregar o navegador com Ctrl+F5. A sincronização automática consulta a nova versão na próxima execução; não é necessário instalar dependências. Não houve chamada à produção na validação local.

Fontes:
- https://developers.mercadolivre.com.br/en_us/tools/sellers-reputation
- https://developers.mercadolivre.com.br/pt_br/envios-flex
- https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios
