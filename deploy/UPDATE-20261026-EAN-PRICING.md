# EAN por SKU e regras por modalidade — 26/10/2026

Atualize juntos `server.py`, `goal_pricing.py`, `public/app.js`, `public/goal-pricing.js`, `public/styles.css` e `public/index.html`.

## EAN

Ao alterar o EAN em um anúncio, a propagação procura o mesmo SKU nas contas oficiais, inclusive quando falta o antigo marcador local `official_source`. Cada anúncio continua sendo confirmado na API antes de registrar sucesso. Os EANs confirmados recebem status/data de atualização e os anúncios propagados são devolvidos à tela para atualização imediata. Rejeições individuais continuam indicadas como falha, sem impedir a tentativa nos demais anúncios.

## Preços automáticos

No modal, escolha uma regra existente em **Regra do SKU** ou selecione **+ Nova regra**. Em **Aplicar aos anúncios**, selecione **Clássico e Premium**, **Somente Clássico** ou **Somente Premium**. A calculadora acompanha essa seleção.

É possível manter uma regra Clássica e outra Premium simultaneamente, com valores, intervalos e limites diferentes. A ativação de regras sobrepostas é recusada para impedir que duas regras reduzam o mesmo anúncio. Pausa, restauração e histórico pertencem à regra selecionada. As regras já cadastradas continuam nas duas modalidades; não apague `goal_pricing.json` nem recadastre regras existentes. Para substituir uma regra antiga por duas, use o fluxo existente de pausa/restauração antes de editar e ativar as novas configurações.

Depois de publicar os arquivos no repositório:

```bash
cd /opt/competidor
git pull --ff-only && sudo systemctl restart competidor
curl --fail --max-time 10 http://127.0.0.1:8770/api/live
```

Reinicie sem operações manuais pendentes e recarregue o navegador com Ctrl+F5. Validação local: 575 testes Python, testes de interface de metas e preços automáticos, incluindo duas regras independentes e seleção de modalidade. Nenhum preço real foi alterado nos testes.
