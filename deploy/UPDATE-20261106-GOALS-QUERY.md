# Metas de Venda: consulta independente da fila

Atualizar juntos `server.py`, `public/sales-goals.js` e `public/index.html`, mantendo os demais arquivos das atualizações anteriores. Reiniciar o serviço `competidor` após instalar os arquivos. Não substituir arquivos de dados.

- `/api/sales-goals/query` responde diretamente com os dados locais, sem criar operação na fila de ações manuais e sem polling de job.
- A consulta tem capacidade HTTP separada, limitada a duas leituras concorrentes. Não consome os slots das ações manuais nem do acompanhamento de operações.
- A leitura das metas não adquire `DATA_LOCK` e não grava um mês vazio ao abrir a tela. A migração das metas antigas é interpretada na leitura e persistida pelo próximo salvamento, preservando o histórico.
- As regras de preço são indexadas por SKU uma vez por consulta, evitando percorrer todas as regras para cada produto.
- Falhas transitórias de leitura têm uma retentativa automática e botão para tentar novamente. Dados já carregados do mesmo mês e edições locais são mantidos. Nenhum preço ou meta é reenviado por essa recuperação.
- Salvamento de metas, atualização oficial das vendas e automação continuam em seus fluxos existentes.

O erro do print indica falha de acompanhamento, mas não comprova sozinho que a operação no servidor falhou. Esta correção elimina a dependência desse acompanhamento para abrir a lista. A aplicação ainda depende da disponibilidade do servidor; não houve acesso ao servidor de produção durante a correção.
