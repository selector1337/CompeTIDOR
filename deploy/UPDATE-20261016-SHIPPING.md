# Frete visível nos anúncios — 16/10/2026

Correções complementares:
- A consulta de frete na página não é mais ignorada quando preços estão sendo atualizados.
- Estados pendentes entram na fila; erros antigos e indisponibilidade não bloqueiam novas tentativas para sempre no navegador.
- Fretes são atualizados diretamente no cartão, mesmo com a tela rolada, sem reconstruir campos em edição.
- Lotes de até 25 anúncios continuam nos demais itens selecionados para consulta. Falhas e respostas sem resultados recebem intervalo local de 15 minutos, evitando loops. Indisponibilidade informada pelo ML respeita intervalo de 6 horas.
- Consultas de fundo não ficam aguardando confirmação de erro 502; prazo de envio de 30 segundos e acompanhamento de até 3 minutos.
- Se uma sincronização já concluiu o frete, a resposta devolve a cotação salva para atualizar a tela sem outra consulta externa.
- Rótulos distinguem aguardando consulta, consultando, falha e ausência de cotação no ML. Frete ausente não é convertido para zero.

Implantar juntos server.py, public/app.js e public/index.html; reiniciar competidor e recarregar a página. O cache de app.js é 20261016-shipping-visible.

Testes locais: recuperação de catálogo, acompanhamento assíncrono e consulta de frete com preços em andamento, tela rolada, múltiplos lotes, falha com intervalo e cotação zero. Teste Python da recuperação de cotação salva. Não houve acesso à conta de produção ou consulta real ao anúncio do print.
