# Metas: fila de preços, estoque e simulação — 23/10/2026

Atualizar server.py, goal_pricing.py e public/ juntos. Não apagar goal_pricing.json nem recadastrar regras: o cursor e os contadores são compatíveis com as regras existentes.

Após os arquivos estarem no repositório:

```bash
cd /opt/competidor
git pull --ff-only
sudo systemctl restart competidor
curl --fail --max-time 10 http://127.0.0.1:8770/api/live
```

Recarregue o navegador com Ctrl+F5. Reiniciar interrompe operações em andamento; prefira um momento sem alterações manuais.

A fila agora continua a partir da próxima regra e prioriza anúncios há mais tempo sem consulta. A validação inicial não reinicia o intervalo da ativação. Conferências de vendas que falham voltam a ser tentadas após um minuto; reduções continuam bloqueadas enquanto não for possível confirmar as vendas. O motivo identifica a conta pendente. Não se alteraram os limites de margem nem a reconciliação de respostas incertas.

A lista informa estoque de um único anúncio base, status consolidado do SKU, próxima janela, reduções confirmadas por anúncio e redução média ponderada. Ativo significa pelo menos um anúncio ativo; pausado significa sem anúncio ativo e com pelo menos um pausado. Referências comerciais ativas são preferidas por modalidade.

Filtros e configuração de preços ficam disponíveis durante atualização. Edições de metas são preservadas, e exportações aguardam salvar essas edições. O modal estima prazo e rentabilidade por anúncio base, com tarifa proporcional e frete constantes e sem novas vendas; a validação efetiva continua usando a API. Quantidades históricas de alterações podem ser parciais quando eventos antigos já saíram do limite de 1.000 registros; novos contadores são persistidos.

Validação em produção: conferir os SKUs enviados, observar o avanço de preços originais confirmados e os eventos novos. Os testes locais não confirmam permissões nem disponibilidade da API em produção.

## Retentativas e diagnóstico

A conferência mensal de vendas roda em um trabalhador separado da fila de preços. Enquanto as vendas estão desatualizadas, a fila pode registrar preços originais e conferir alterações já enviadas, mas não envia novas reduções. Falhas temporárias de precificação são reavaliadas após cinco minutos; respostas incertas são conferidas antes de reenviar para evitar aplicar uma redução duas vezes. Alterações externas de preço permanecem bloqueadas para revisão, sem sobrescrever preços manuais.

O acompanhamento distingue inicialização, confirmação pendente, retentativa, bloqueio externo e janela atingida. O journal registra erros/bloqueios novos e um resumo de progresso a cada cinco minutos enquanto a fila avança. Após atualizar e reiniciar:

```bash
sudo journalctl -u competidor --since '15 minutes ago' --no-pager
```

Uma consulta sem entradas não comprova sucesso nem ausência de falhas. Conferir também o resultado por anúncio, especialmente as contas aguardando atualização das vendas. Não apagar o histórico nem recadastrar as regras existentes.
