# Cópia tradicional e rejeição de medidas

Atualizar `server.py` e reiniciar o serviço pelo procedimento habitual. Não há migração nem alteração do frontend.

A montagem da cópia passa a enviar `catalog_product_id` apenas quando a origem é efetivamente uma oferta de catálogo (`catalog_listing=true`). Anúncios tradicionais não carregam esse vínculo nem o atributo `CATALOG_PRODUCT_ID` na nova publicação. Cópias de ofertas de catálogo mantêm o vínculo. A alteração se aplica também a combinações já preparadas, pois o pedido é montado na execução.

A rejeição de embalagem inferior às dimensões do produto agora apresenta os campos recusados para correção, preenchidos com os valores enviados. A aplicação não aumenta dimensões nem peso automaticamente. A mensagem deixa de afirmar que o problema necessariamente está no catálogo; essa validação pode ocorrer em anúncios tradicionais.

Validação local: 628 testes Python aprovados, incluindo isolamento do anúncio tradicional, preservação do modo catálogo e correção manual de comprimento com preservação do GTIN. Nenhum anúncio real foi publicado. Não houve implantação em produção.

O print confirma uma rejeição de comprimento pela API, mas não contém o pedido enviado nem as medidas reais de MLB804502586. Portanto, não comprova se o vínculo herdado foi a causa específica. Caso o Mercado Livre continue recusando a medida, os campos de ajuste permitem informar a embalagem real e reenviar sem reconstruir a combinação.
