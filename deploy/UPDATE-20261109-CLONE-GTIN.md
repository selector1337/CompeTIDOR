# Correção do GTIN na cópia de anúncios

Atualizar `server.py` e reiniciar o serviço da aplicação pelo procedimento habitual. Não há migração de dados nem alteração de dependências.

- A cópia prioriza o código de barras textual quando o atributo da origem também possui um `value_id` interno.
- O erro `item.attribute.invalid_product_identifier` pode conter uma exigência de GTIN ausente. Quando o código é recuperado da origem, ele permanece no pedido seguinte, sem ser removido novamente nem virar uma pergunta ao usuário.
- Uma rejeição efetiva de GTIN obrigatório preserva o valor enviado no campo de conferência, inclusive quando diferente do código original. A mensagem informa a rejeição pela API, em vez de afirmar que o usuário não preencheu o campo.
- O tratamento de kits e a proteção contra publicação duplicada permanecem cobertos pela suíte existente.

Validação local: 619 testes Python aprovados, incluindo recuperação automática do GTIN, preservação nas retentativas, código textual versus ID interno e rejeição efetiva da API. Não foram publicados anúncios nem executada atualização em produção.

O print corresponde à mensagem gerada pelo tratamento antigo de rejeição. Sem a resposta original da API, não é possível determinar por que o Mercado Livre recusou o código naquele pedido específico. A atualização não transforma um código rejeitado em publicação aceita; corrige a perda da informação e a classificação equivocada da resposta.
