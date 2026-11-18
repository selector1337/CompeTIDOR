# Atualização 09/11/2026 — margens, tipo vencedor e confirmação de publicação

Atualizar server.py, public/app.js, public/styles.css e public/index.html juntos; reiniciar o serviço. Nenhum arquivo de dados deve ser substituído.

Metas: margem original estimada no preço original do mesmo anúncio base, com custo, tarifa proporcional e frete atuais; margem atual; margem mínima das regras ativas aplicáveis ao tipo. Dados ausentes não viram zero. Regras desativadas não estabelecem limite ativo. Não há reconstrução de custos históricos.

Catálogo: tipo Clássico/Premium do anúncio vencedor consultado na API, aproveitando a consulta já existente para identificar o vencedor. Não informado quando a API não fornece o tipo; nenhum tipo é inferido do próprio anúncio. O campo aparece após atualização de concorrência.

Publicação: confirmação com MLB conhecido é preservada quando persistência ou auditoria local falham depois. Vínculo de catálogo confirmado também preserva o sucesso e mostra aviso de registro local pendente. Falha ao atualizar a lista da interface não passa a ser erro de publicação. Nenhuma publicação real realizada nos testes.

Validação: suíte Python e teste de interface desktop/mobile. O erro de medidas dos prints ainda requer o MLB/log da operação para confirmar a causa específica; não é possível afirmar que a API rejeitou antes ou depois da criação apenas pelo print. Não implantado no servidor.
