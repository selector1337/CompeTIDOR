# Atualização — tipo vencedor e exclusão de anúncios

Implantação: atualizar server.py e reiniciar o serviço. Preservar dados existentes. Os tipos vencedores aparecem após a próxima atualização de concorrência; o HTML/JavaScript da atualização anterior continua compatível.

## Catálogo
A identificação antes encerrava as alternativas quando o vendedor já era conhecido, mesmo sem listing_type_id. Agora consulta o item, a resposta em lote e a oferta de mesmo MLB no catálogo para completar o tipo.

Tipo oficial tem prioridade. Como alternativa, parcelamento explícito sem juros corresponde a Premium e com juros a Clássico, conforme solicitado. Não há inferência a partir da ausência de juros, pagamento à vista ou parcela única. Na página pública, a informação deve estar na área principal da buybox e o vendedor e preço devem coincidir com o vencedor oficial. Outro vendedor com mesmo preço não confirma o tipo.

## Exclusão
Consulta o estado oficial atual antes de agir. Não tenta encerrar novamente itens já encerrados ou itens under_review/forbidden, que usam diretamente a exclusão. Após falha de escrita ou resposta inconclusiva, consulta o anúncio antes de repetir; tentativas limitadas e só sucesso confirmado remove da lista local. A conta solicitada deve corresponder ao anúncio.

Registros de exclusão são preservados entre gravações concorrentes e impedem que sincronizações antigas recoloquem o anúncio no catálogo. Repetir uma exclusão já registrada retorna sucesso sem nova chamada de alteração.

Referência oficial: https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/produto-sincronizacao-de-publicacoes

Validação: 603 testes Python aprovados. Inclui tipo por juros, oferta de outro vendedor rejeitada, exclusão de bloqueado, confirmação após timeout sem segundo PUT, falha sem confirmação e gravação concorrente. Sem exclusão real ou implantação no servidor.
