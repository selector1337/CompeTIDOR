# Cópia: consultas paralelas e tipo do vencedor

Atualizar `server.py` e reiniciar o serviço pelo procedimento habitual. Não há migração de dados ou alteração do frontend.

## Cópia de anúncios

- A descoberta de lojas oficiais deixa de usar as tentativas longas da sincronização: consulta lotes com as opções interativas e no máximo dois lotes simultâneos, respeitando os limites globais de requisições.
- A consulta de atributos da categoria e a consulta de lojas do destino acontecem em paralelo. A publicação aguarda ambas e mantém as validações do destino.
- A descrição da cópia usa o timeout interativo. Caso não seja confirmada, o anúncio continua identificado como criado, com aviso explícito, em vez de ocultar o problema.
- A proteção persistente contra publicações duplicadas foi preservada.

## Tipo de anúncio vencedor

- Valores vazios da consulta individual não sobrescrevem o tipo encontrado na consulta em lote.
- Reconhece o tipo em `listing_type.id` e `item.listing_type_id`, além do campo direto. Preserva o tipo já fornecido pela resposta do vencedor.
- Não interrompe a busca de ofertas apenas por já conhecer o vendedor quando ainda falta o tipo.
- Quando falta o tipo na API e na página de catálogo, consulta a página do anúncio vencedor. Só aceita parcelamento com/sem juros quando URL final, vendedor e preço correspondem ao vencedor. Redirecionamentos para outros anúncios são descartados.
- Cache da página específica: cinco minutos quando obtida, trinta segundos para resultado indisponível, com limite de entradas.

Os registros existentes precisam passar por nova atualização da concorrência para receber o tipo. O print apresentado mostra uma consulta de 01/11/2026; a atualização do código não altera essa observação histórica. Se a API e a página pública não permitirem confirmar o tipo, ele permanece indisponível em vez de receber uma classificação presumida.

Validação: 625 testes Python aprovados. Novos casos verificam paralelismo limitado, timeout da descrição, preservação do tipo em respostas parciais, tipos aninhados, correspondência de vendedor/preço e rejeição de redirecionamento. Testes locais não medem a latência real do servidor nem confirmam o tipo atual do anúncio MLB3995384405. Nenhuma publicação ou implantação em produção foi feita.
