# 09/11/2026 — cópia/publicação: confirmação e desempenho

Atualizar juntos server.py, publication_guard.py (novo arquivo obrigatório), public/app.js e public/index.html. Reiniciar o serviço. Preservar todos os dados, inclusive os novos arquivos publication-attempt-*.json. Não substituir dados de produção pelos dados dos testes.

## Resultado
- Cada publicação identificada pelo preview de cópia ou formulário assistido registra a tentativa antes do POST. Uma resposta 500, timeout ou confirmação incompleta fica pendente; não dispara outro POST de criação.
- A conferência consulta anúncios recentes da conta e exige um único candidato posterior à tentativa, com o mesmo vendedor, SKU, categoria, modalidade, loja, título/nome, preço e vínculo de catálogo. Ausência ou ambiguidade continua pendente, nunca vira sucesso inventado.
- Conferência inicial limitada a três consultas; interface repete o acompanhamento até três vezes, a intervalos de cinco segundos. Persistindo a dúvida, oferece Conferir publicação. Registro sobrevive ao reinício da aplicação.
- Rejeições explícitas de validação permitem correção. Cópias já confirmadas não são repetidas ao retomar lote parcial.
- Tentativas antigas que já registraram HTTP 500, sem o novo registro durável, não são reenviadas automaticamente. Exigem conferir o MLB resultante. O caso do print ainda precisa do código da cópia para comprovar sua correspondência.

## Desempenho
- Descoberta de lojas oficiais, que podia percorrer até 100 anúncios em cada variação, reutilizada por cinco minutos para a mesma conta e catálogo. Cache limitado a 32 entradas.
- Resposta completa de criação dispensa GET imediato redundante.
- Verificação incompleta usa a leitura interativa de menor timeout; consultas em lote da reconciliação têm uma tentativa e timeout de cinco segundos por endpoint.
- Armazenamento de tentativas em pequenos arquivos separados, sem regravar um histórico inteiro a cada anúncio. Concorrência geral não ampliada.

## Validação
611 testes Python aprovados e teste JavaScript de acompanhamento. Casos: 500 após criar, timeout seguido de confirmação, pendência entre chamadas, candidatos ambíguos, anúncio antigo/outra conta rejeitado, validação 400, sucesso sem consultas adicionais e reutilização da descoberta de lojas.

Sem publicação real, implantação ou medição de latência no servidor de produção. Disponibilidade da API e indexação de anúncios continuam externas à aplicação.
