# 09/11/2026 — publicação independente de módulo adicional e redução de espera

## Implantação
Atualizar server.py e reiniciar o serviço. Manter o frontend da atualização anterior. publication_guard.py deixou de ser requisito de execução: a implementação está em server.py, e o arquivo antigo é somente uma fachada de compatibilidade para ferramentas/testes.

Não apagar os arquivos publication-attempt-*.json, regras, anúncios ou demais dados. A proteção persistente contra reenvio de POST incerto foi mantida.

## Correções
- Eliminado o import que causava No module named publication_guard antes da publicação.
- Cópias individuais e Anunciar Produtos usam uma fila própria, limitada a dois workers, independente de lotes de cópia e outras ações manuais. Mantidos os limites globais de chamadas da API e as demais filas.
- Descoberta de lojas oficiais passa a usar cache por conta por cinco minutos. A chave anterior incluía todo o catálogo e era invalidada a cada criação, repetindo uma busca de até 100 anúncios. Lojas locais continuam sendo incorporadas separadamente.
- Leitura dos atributos de categoria e complementação de fotos da cópia usam as opções interativas de timeout/retentativas em vez das esperas longas padrão.
- Falha na leitura interativa do anúncio de origem não inicia outra cadeia lenta da mesma leitura. Não são publicados dados inventados em caso de falha.
- Acompanhamento informa as etapas de leitura da origem, loja oficial e envio/conferência.

## Validação
615 testes Python e teste JavaScript de acompanhamento aprovados. Testes novos cobrem criação com import de publication_guard explicitamente bloqueado, ausência de segunda leitura após timeout, opções interativas nas consultas e escolha da fila independente. O teste de descoberta verifica que novos anúncios no catálogo não invalidam o cache da conta.

Alterações verificadas localmente. Sem implantação, cópias reais ou medição de latência no servidor de produção. O erro do print ocorreu antes do envio por esse caminho; atrasos de API/OAuth/infraestrutura precisam ser diferenciados pelas etapas de acompanhamento se persistirem após implantação.
