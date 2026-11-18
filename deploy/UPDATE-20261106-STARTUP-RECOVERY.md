# Recuperação da interface após timeout

Instalar juntos `server.py`, `public/app.js`, `public/styles.css` e `public/index.html`, preservando os arquivos de dados e as correções anteriores. Reiniciar o serviço `competidor` após atualizar. O HTML altera as versões de cache dos recursos.

A tela do print era gerada pelo tratamento de erro da inicialização, que substituía todo o `document.body` por uma mensagem. O novo tratamento preserva o DOM e apresenta um aviso de conexão com nova tentativa automática (5, 10, 20, 40 e até 60 segundos) e botão manual. Sem internet, aguarda reconexão. Não repete salvamentos, publicações ou alterações de preços.

As consultas iniciais têm prazos menores e não abrem confirmação de repetição manual de erro 502. Quando uma consulta inicial falha, a consulta paralela é cancelada para evitar requisições acumuladas. Login manual bem-sucedido encerra qualquer recuperação pendente. Uma consulta GET expirada não mostra aviso de alteração/publicação incerta.

`/api/auth/me` usa apenas o snapshot de autorização e tem dois slots HTTP separados das consultas maiores. Não copia catálogo nem histórico de operações para verificar uma sessão.

Validação: testes de backend e navegador com timeout, dados digitados preservados, botões existentes funcionais, recuperação automática e retorno da conexão. Não houve acesso a produção; esta correção elimina a tela fatal e reduz trabalho na autenticação, mas não comprova nem elimina causas externas de indisponibilidade do servidor ou proxy.
