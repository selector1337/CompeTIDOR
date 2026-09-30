# Auditoria da entrega — 30/09/2026

## Requisitos e evidências

| Requisito | Implementação e verificação |
| --- | --- |
| Perfil para VPS exclusiva 8 vCore / 16 GB | Perfil `dedicated-16gb`, ambiente carregado após o antigo, limites systemd 11/13 GiB e CPU 700%. Testes de configuração em `test_dedicated_reliability.py`; perfil confirmado no teste HTTP. |
| Reduzir contenção sem eliminar funcionalidades | Cache da resposta gzip do catálogo; construção única para consultas simultâneas; correção da assinatura do cache JSON; cópia de escrita fora do lock do cache; renovação OAuth compartilhada e persistida. Testes de cache, concorrência e OAuth, incluindo ordem de locks. |
| Monitor não reiniciar por fila de tarefas ocupada | Healthcheck consulta `/api/live`; rota independente do semáforo normal e dos dados. Teste HTTP real respondeu com esse semáforo esgotado e DATA_LOCK retido. Nginx encerra keepalive antes do timeout do backend. |
| `2.250` continuar significando R$ 2.250,00 | Parser monetário brasileiro, validação de formato, normalização ao sair do campo e valor explícito no botão de aplicação. Testes JS e navegador cobrem apagar `,00`, milhares, centavos, valores inválidos e transferência para catálogo/lote. |
| Importação de pedidos completa por período | Busca com teto temporal fixo, deduplicação e divisão de intervalos incompletos; relatórios usam o mesmo importador. Falha persistente ou limite excedido impede confirmar total parcial. Testes de paginação, falha de autorização e limite. |
| Atualizar meses anteriores | Rotação automática de meses conhecidos; conciliação manual de mês inicial/final para todas as contas conectadas. Testes de passagem de ano, intervalo inválido, ausência de contas e preservação do histórico após erro. |
| Conferir pedidos conhecidos ausentes da busca | IDs guardados no histórico e consulta individual quando não retornados; cancelamento confirmado não conta como faturamento. Teste com pedido pago e cancelado. |
| Mesmos pedidos no faturamento, analítico e metas | Teste de integração com duas contas atualiza os três armazenamentos usando código real, excluindo duplicados/cancelados. Os arquivos são simulados em memória; não usa contas reais. |
| Botão para importar período | Dashboard: “Conferir / importar vendas de outros meses”. Execução assíncrona, antes/depois por conta, falhas separadas e inputs bloqueados durante a execução. Teste de navegador e inspeção da imagem. |
| Preservar históricos antigos | Fora da janela integral de retenção da API, não substituir histórico por resultado vazio; analítico reutiliza dados salvos e informa quando faltam dias antigos. Teste sem chamada à API nem escrita de zeros. |
| Migração apenas desta aplicação | Guia `MIGRACAO-UBUNTU-26-16GB.md`: preparo, versão de código, dependências, serviços, HTTPS pronto, backup com backend parado, dados/credenciais, verificações, proxy de transição, DNS, certificados e retorno com dados/tokens atuais. Outros serviços permanecem ativos. |

## Verificações executadas

- 482 testes Python passaram (`test-current.log`).
- Sintaxe de `public/app.js` e teste `tests/money_input.cjs` passaram.
- Testes de navegador `profit_calculator_ui.cjs` e `reconciliation_ui.cjs`
  passaram; imagens revisadas nas etapas correspondentes.
- `tests/http_load_probe.py`: 50.000 anúncios sintéticos, 16 consultas
  simultâneas, 96 respostas HTTP verificadas sem falha. Resultado completo em
  `tests/http-load-result.json`.
- O teste HTTP inclui clientes e servidor no mesmo processo local Windows;
  tempos incluem leitura, descompressão e parsing. Não é previsão de capacidade
  ou SLA da VPS e não mede latência da rede do usuário.

## Limites da verificação e ativação

As mudanças estão no workspace. Não houve commit, push, deploy, migração de dados
reais ou publicação de anúncios. O usuário pediu o roteiro para executar a
migração; a infraestrutura Ubuntu de destino não está acessível neste ambiente.
O roteiro contém os gates de instalação (`pip check`), configuração (`nginx -t`),
saúde, OAuth, dados e renovação TLS que devem passar antes da troca de DNS.

A existência dos pedidos reais relatados não foi conferida aqui: os testes
provam os mecanismos de recuperação e contabilização, não os R$ 15 mil/R$ 20 mil
do caso de produção. Depois de instalar a versão, executar a conciliação por
período e comparar IDs se restar diferença. Dados que a API deixou de fornecer
exigem histórico local, backups ou outra fonte histórica; não serão inventados.

RAM maior fornece folga, mas não garante ausência absoluta de 502. Os picos
anteriores de 4,7 GiB não comprovavam OOM; os logs confirmavam reinícios do
healthcheck. É preciso observar a VPS em uso real após aplicar o código e os
arquivos de serviço/monitor, conforme o guia.
