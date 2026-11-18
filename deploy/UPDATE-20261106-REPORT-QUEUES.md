# Atualização de 06/11/2026 — filas de relatórios

## Implantação
Atualizar juntos `server.py`, `public/app.js` e `public/index.html`, e reiniciar o serviço da aplicação. O HTML altera a versão do JavaScript para evitar cache antigo. Preservar os arquivos de dados e a configuração existente.

## Correção
Analítico, estatísticas de vendas e exportações estavam compartilhando a fila pesada com importações históricas, planilhas e consultas automáticas de reposição. A configuração padrão tinha apenas um worker, deixando consultas curtas atrás de tarefas longas.

Agora o Analítico tem um worker próprio; os demais relatórios de vendas têm dois; a geração de PDF/Excel tem um. Importações e pesquisas de oportunidades continuam na fila pesada. A concorrência permanece limitada e os limites existentes de chamadas ao Mercado Livre continuam aplicados.

Os seis fluxos de consulta de relatórios e o acompanhamento de exportações passam a ter consultas de status com timeout de até 20 segundos, até três tentativas para falhas temporárias e limite de acompanhamento de 15 minutos. As tentativas consultam o mesmo identificador; não reenviam a geração. Erros de autenticação ou operação inexistente são apresentados imediatamente. Encerrar o acompanhamento não cancela a tarefa no servidor. Consultas de estatísticas idênticas ainda pendentes reutilizam o mesmo processamento.

## Validação
- Suíte Python: 593 testes aprovados.
- Teste de concorrência: Analítico e exportação concluem com filas pesada e de estatísticas ocupadas.
- Teste de falha: consulta encerra em erro e consultas idênticas pendentes não são duplicadas.
- Teste JavaScript: retentativas no mesmo job, limite de espera, autenticação e uso pelos sete fluxos.

Validação realizada localmente, sem implantação ou chamadas reais ao Mercado Livre. A correção elimina a dependência da fila pesada identificada no código; problemas de infraestrutura/API em produção exigem diagnóstico do servidor.
