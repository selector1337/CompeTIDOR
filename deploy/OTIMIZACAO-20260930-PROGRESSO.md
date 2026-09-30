# Registro de trabalho — servidor dedicado e conciliação

Entrega local concluída e auditada em `AUDITORIA-DEDICADO-20260930.md`.
As seções abaixo são o registro cronológico de desenvolvimento; os pendentes
antigos foram substituídos pela auditoria final. Código, testes e roteiro estão
prontos no workspace. Publicação, execução da migração e conferência dos totais
reais pelo usuário ainda não ocorreram e não são alegadas como realizadas.

Verificação final: 482 testes Python OK, incluindo a integração de conciliação
com duas contas, faturamento mensal, analítico diário e metas por SKU. Consulte
a auditoria para as evidências e limitações do teste HTTP e do ambiente Ubuntu.

## Continuação verificada — paginação unificada e HTTP real

- `fetch_statistics_orders` usa agora o importador adaptativo mensal. Uma busca
  que permanece incompleta ou excede o limite falha explicitamente, sem apresentar
  total parcial como definitivo. Testes verificam recuperação por subintervalos,
  limite global e erro irrecuperável. Suite completa: **481 testes OK**.
- `tests/http_load_probe.py` executado com servidor HTTP real local, dataset
  sintético de 50 mil anúncios, concorrência 16, 96 respostas verificadas. Resultado
  em `tests/http-load-result.json`: nenhuma falha; p95 catálogo 853ms, live626ms,
  health599ms. Medição inclui parsing/decompressão dos clientes no mesmo processo,
  portanto não é benchmark isolado de servidor nem previsão da VPS.
- Com o semáforo normal esgotado e DATA_LOCK retido, live respondeu em41ms e
  health31ms. Perfil dedicado confirmado. JS syntax e money_input passaram.
- Guia agora alerta que a versão corrigida deve ser disponibilizada antes da
  transferência e que não houve push/deploy pelo agente.
- Pendente: auditoria final dos requisitos e revisão das limitações de validação
  no destino real. Não declarar faturamento real corrigido sem executar a
  conciliação nas contas; fixtures demonstram o mecanismo, não os totais reais.


## Continuação verificada — 480 testes e roteiro HTTPS/retorno

- Suite completa executada após as alterações: 480 testes Python OK
  (`test-current.log`). A conciliação no navegador já passou; screenshot
  `tests/reconciliation-desktop.png` inspecionado: campos e resultados legíveis,
  conteúdo de erro escapado. Os valores na imagem são fixtures, não produção.
- Analítico agora preserva dias existentes fora da retenção e recusa dias antigos
  sem cache, em vez de gravar zeros derivados de uma busca indisponível. Teste
  cobre histórico preservado, ausência de chamada API/escrita e falta de dados.
- Criado `deploy/nginx-competidor-https.conf` com TLS, redirect HTTP e mesmos
  parâmetros de proxy/estáticos do template anterior. Guia usa instalação direta.
- Guia ampliado com retorno usando arquivo de dados frio, checksum, extração em
  diretório novo, preservação de tokens atuais, limites antigos e proxy reverso
  durante a volta do DNS. Não executado em Ubuntu: `nginx -t` é gate no destino.
- Continua pendente a medição de carga HTTP real local, revisão do paginador de
  estatísticas (ainda separado do importador adaptativo), e auditoria final do
  conjunto/migração. Não houve deploy nem verificação de valores reais de vendas.


## Continuação verificada — 475 testes, calculadora no navegador

As anotações abaixo complementam e atualizam os pendentes originais:

- `account_client` agora compartilha renovações concorrentes, recupera tokens
  duráveis mais recentes para snapshots antigos e salva antes de devolver o
  cliente. ACCOUNT_REFRESH_LOCK nunca é mantido ao adquirir DATA_LOCK. Revisão
  em nanossegundos também foi adicionada ao callback OAuth e ao merge de contas.
  Cinco testes cobrem concorrência (20 chamadas/uma renovação), reinício, falha,
  reautorização mais recente e chamador já segurando DATA_LOCK.
- Importação mensal divide intervalos incompletos em metades disjuntas por
  milissegundo. Volume >=5000 dispara subdivisão preventiva. Falha de autorização
  não é repetida recursivamente. Limite global continua protegendo volume total.
  Três testes de partição passaram. O caminho `fetch_statistics_orders` ainda
  usa o paginador antigo com aviso de truncamento, sem essa recuperação.
- Histórico automático agora roda meses conhecidos anteriores ao mês passado,
  um par conta/mês por ciclo, com validade e recuo de 15min após erro.
  Corrigido erro confirmado: `sync_previous_month_revenue` substituía um registro
  válido por zero ao falhar; agora preserva valores e registra last_error_at.
  Atualiza também cache analítico e metas. Testes de preservação e rotação passaram.
- Catálogo JSON agora armazena a resposta gzip e a reutiliza nas requisições;
  dez respostas simuladas sem recomprimir verificadas em teste.
- `tests/profit_calculator_ui.cjs` usa funções reais, Edge headless, APIs externas
  bloqueadas: apagar ,00, valores grandes, inválidos, margem e transferência para
  lote/catálogo passaram. Imagem tests/profit-calculator-desktop.png inspecionada,
  sem sobreposição. Teste inicialmente tinha expectativa matemática errada (>2250
  para margem20%); foi corrigido para fórmula exata, sem mudar regra comercial.
- Última suite completa: **475 testes OK**. Nenhuma mudança em produção.

Próximos pontos prioritários: verificar comportamento da API para meses antigos
fora da retenção (não confundir retorno vazio com prova de ausência de vendas);
considerar guardar IDs e data da conferência para auditoria/reparo. Testar o
formulário de conciliação no navegador e zero contas conectadas. Medir carga real
local, revisar concorrência HTTP/JSON, completar guia/template HTTPS/rollback e
atualizar cache-busting. Não declarar conclusão enquanto esses gates estiverem
pendentes. Atualizar a revisão de todos os requisitos ao final.

## Alterações feitas em 30/09

- Calculadora de rentabilidade usa `parseBrazilianMoney`: 2.250 e 2.250,00 são
  2250; valores malformados são rejeitados; blur normaliza e botão mostra valor.
- Perfil `dedicated-16gb` e drop-in de limites 11/13 GiB. Arquivo de ambiente
  separado carregado após o antigo para não herdar shared-8gb por precedência.
- Cache JSON identifica a versão antes da leitura, evitando atribuir assinatura
  nova a conteúdo antigo durante os.replace. Cópia de escrita fora do lock do
  cache, mantendo DATA_LOCK para consistência.
- Seleção do mês anterior no loop automático respeita validade de 24 horas;
  anteriormente ignorava qualquer mês já consultado com sucesso para sempre.
- Dashboard: formulário de conciliação de mês inicial/final, execução assíncrona,
  resultado por conta com antes/depois. Reutiliza conferência mensal e diário
  analítico. Preserva tokens renovados no caminho de sucesso da reconciliação.
- Guia detalhado inicial MIGRACAO-UBUNTU-26-16GB.md: cópia fria consistente,
  preservação de ambiente/dados, backend único, HTTPS, proxy de transição e rollback.

## Evidência

Baseline inspecionado: 460 testes Python. Foram acrescentados 4 testes de perfil,
validade histórica e faixa de conciliação. Teste JS money_input.cjs verifica
milhares, decimais e remoção de ,00. Ainda falta verificação no navegador real.
As imagens originais mostram exatamente 2.250 sendo interpretado como 2.25.
O acesso direto à página completa da documentação de pedidos retornou HTTP 403;
a pesquisa retornou o resumo oficial. Não tratar limites de busca como confirmados.

## Requisitos ainda não concluídos / próxima revisão

1. Auditar/importar pedidos: não basta teste de datas. Testar paginação incompleta,
   limites/janelas adaptativas, duplicados, cache canônico, pedidos históricos e
   efeito sobre Dashboard/Analítico/Metas. A nova UI de intervalo ainda não foi
   testada no navegador. Histórico anterior ao mês passado ainda não é revisado
   automaticamente pelo loop; avaliar rotação de meses e ledger durável.
2. account_client renova tokens em memória e muitos caminhos só persistem mais
   tarde. Corrigir renovação concorrente e persistência imediata mesmo quando uma
   operação posterior falhar; auditar ordem de locks para evitar deadlock.
3. Medir sob carga local: latência /api/live e /api/health, consultas simultâneas,
   pico de memória e trabalho duplicado (JSON e gzip). BoundedThreadingHTTPServer
   atualmente não limita threads de conexão; avaliar sem bloquear liveness.
4. Acrescentar testes da corrida de assinatura no read_json e cópia fora do lock.
5. Testar calculadora real via Playwright: apagar ,00, colar valores grandes,
   inválidos, margem calculada, botão para preço em lote. Atualizar cache-busting.
6. Revisar guia de migração: fornecer template HTTPS pronto (hoje pede edição),
   comandos completos de rollback, validação compatibilidade Ubuntu26/Python,
   ambiente/precedência, preservação OAuth, cron/timers, arquivos fora de DATA.
   Não houve migração nem acesso ao servidor. Não prometer zero reconexões para
   tokens já revogados nem zero502 sem observar produção.
7. Garantir que nenhuma conta conectada => mensagem clara, não conclusão vazia.
   Conferir persistência de auditoria e retomada da conciliação após desconexão.
8. Testes finais completos, visual QA, revisão segurança e auditoria requisito
   por requisito antes de chamar update_goal complete.

Não há .git utilizável neste workspace: `git status` retorna not a git repository,
embora exista um diretório .git. Não foi criado commit/push. Não alterar metadados.
Não usar subagentes: instrução vigente permite só com autorização explícita.
