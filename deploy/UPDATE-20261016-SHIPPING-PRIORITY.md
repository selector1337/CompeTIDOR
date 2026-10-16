# Prioridade e prazo das consultas de frete — 16/10/2026

O fluxo visível ainda submetia 25 consultas sequenciais à fila de background, onde esperava operações interativas terminarem. Cada consulta usava até 4 tentativas de 20 segundos, portanto o lote podia ultrapassar o prazo de 3 minutos do navegador.

Correções: lotes de cinco anúncios, fila manual com concorrência já limitada, uma tentativa de até 12 segundos por chamada de frete. Falhas são registradas e respeitam o intervalo de novas tentativas existente. O endpoint rejeita lotes maiores que cinco para não descartar IDs silenciosamente. Recarregar o navegador após implantar server.py, public/app.js e public/index.html juntos; reiniciar competidor.

Diagnóstico do anúncio (sem credenciais na saída e sem modificar anúncios ou arquivos de dados):

```bash
cd /opt/competidor
sudo env COMPETIDOR_DATA_DIR=/var/lib/competidor .venv/bin/python deploy/diagnose-shipping.py MLB7762563214
```

Confirme o diretório de dados se sua instalação usa outro caminho. O diagnóstico mostra a cotação interpretada, campos da resposta, regiões de cobertura, tempo e código HTTP quando disponível. Token expirado pode resultar em 401, pois o script não renova tokens nem modifica a base.

A resposta real do Mercado Livre para esse anúncio ainda precisa ser conferida em produção. A existência de uma cotação não pode ser garantida; ausência não será convertida em frete zero. Mudanças verificadas por testes da fila manual, duração configurada por chamada, limites dos lotes e atualização dos cartões.
