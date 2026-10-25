# Cadastro de regras durante a automação

## Ajustes visuais

Inclua `public/styles.css` e `public/index.html` para os novos estilos do faturamento diário, saúde das contas e paginação de metas. Os filtros de anúncios e metas passam a distribuir os campos conforme a largura disponível, com mais espaço para o mês e as opções. Após instalar, recarregue com Ctrl+F5. Esta revisão visual não modifica as regras de redução; os 33 testes específicos de automação foram executados novamente com sucesso.

Atualize `goal_pricing.py`, `public/goal-pricing.js` e `public/index.html` juntos. Inclua também `server.py` caso a correção anterior da calculadora ainda não tenha sido instalada.

O cadastro usa controle por SKU: consultas ao Mercado Livre para outros produtos não bloqueiam novos cadastros. As gravações da fila mesclam somente a regra processada com o estado mais recente, preservando cadastros e pausas recebidos durante o ciclo. A intenção de alteração continua gravada antes de cada envio ao Mercado Livre.

Ações sobre o próprio SKU em processamento aguardam o término da etapa protegida para preservar confirmações em andamento. O formulário informa que a solicitação está sendo salva. A fila relê a regra antes de processá-la, respeitando pausas recentes.

Depois de publicar os arquivos no repositório, no servidor:

```bash
cd /opt/competidor
git pull --ff-only && sudo systemctl restart competidor
curl --fail --max-time 10 http://127.0.0.1:8770/api/live
```

Recarregue com Ctrl+F5. Não apague `goal_pricing.json` nem recadastre regras. Reinicie em um momento sem ações manuais pendentes. Esta alteração não redefine preços, intervalos, limites ou histórico das regras existentes.

## Recuperação de divergências e conferência de vendas

Inclua também `server.py` nesta atualização. Anúncios com divergência de preço passam por nova consulta após cinco minutos, sujeitos à fila. Bloqueios antigos também voltam à conferência. O bloqueio é removido automaticamente somente quando o preço consultado volta ao último preço confirmado ou corresponde ao envio pendente. Divergências persistentes continuam protegidas contra sobrescrita; o aviso não identifica a origem da alteração.

A conferência mensal começa pelas contas com vendas mais desatualizadas. Falhas por conta são registradas no journal; o ciclo tenta novamente após 60 segundos quando encontra erros. Novas reduções continuam dependendo de vendas recentes de todas as contas conectadas, pois a pausa após uma venda vale para o SKU inteiro. Confirmações pendentes podem ser recuperadas sem enviar nova redução enquanto as vendas estão desatualizadas.

Para diagnosticar uma conta que continua pendente após atualizar:

```bash
sudo journalctl -u competidor --since '30 minutes ago' --no-pager
```
