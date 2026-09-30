# Migração do CompeTIDOR para uma VPS exclusiva

Destino: Ubuntu 26.04, 8 vCore, 16 GB de RAM e SSD de 480 GB.

Este roteiro preserva o domínio `competidor.umsoftware.com.br`. Manter o domínio,
os dados e a configuração OAuth evita a necessidade normal de reconectar contas.
Tokens já revogados/expirados sem possibilidade de renovação não podem ser
recuperados por uma migração. Não execute duas cópias da aplicação com as mesmas
contas: ambas poderiam renovar tokens, publicar anúncios e executar agendamentos.

Os comandos são para terminal **root**, como nos exemplos enviados. Há etapas
separadas para servidor ANTIGO e NOVO. Não execute um bloco no servidor errado.
Os endereços IP precisam ser substituídos pelos endereços reais. Não envie arquivos
de configuração, backups ou tokens por chat: eles contêm credenciais.

## 1. Antes de marcar a mudança

Reserve uma janela de manutenção. Não inicie publicações, alterações de preço ou
importações durante a cópia final. A transferência do código pode ser antecipada;
a transferência definitiva dos dados deve ocorrer com o CompeTIDOR antigo parado.
O Nginx e as outras aplicações do servidor antigo continuam funcionando.

No painel DNS, anote os registros atuais A e AAAA do domínio e diminua o TTL para
300 segundos, preferencialmente um dia antes. Não altere os registros de outras
aplicações. Anote também regras de firewall, certificados, serviços e tarefas de
cron relacionadas **somente ao CompeTIDOR**.

No ANTIGO:

```bash
hostname
systemctl show competidor -p FragmentPath -p DropInPaths -p ExecStart -p User -p Group
systemctl cat competidor
grep '^COMPETIDOR_DATA_DIR=' /etc/competidor.env
systemctl list-timers --all | grep -i competidor
```

O diretório de dados é o definido em `COMPETIDOR_DATA_DIR`. Na instalação padrão
é `/var/lib/competidor`; se essa variável não estiver configurada, o código usa
`/opt/competidor/data`. Confira também eventuais definições em drop-ins do systemd.
**Não continue enquanto não souber qual diretório a instância em produção usa.**
Todas as subpastas desse diretório fazem parte dos dados: contas/tokens, sessões,
catálogo, custos, metas, relatórios, históricos, agendamentos e demais arquivos.
Copiar somente `app.json` não é suficiente.

Verifique no Nginx qual arquivo corresponde exclusivamente ao domínio:

```bash
grep -R -n 'competidor.umsoftware.com.br' /etc/nginx/sites-enabled /etc/nginx/sites-available
```

Abra esse arquivo e anote os caminhos `ssl_certificate` e `ssl_certificate_key`.
Os exemplos abaixo usam `/etc/nginx/sites-available/competidor`, mas ajuste caso
seu arquivo tenha outro nome. Faça uma captura dos totais por conta, da lista de
contas conectadas e da quantidade de anúncios para comparar após a mudança.

## 2. Preparar a VPS NOVA, sem iniciar a aplicação

Conecte-se à VPS NOVA e confira o host antes de instalar qualquer coisa:

```bash
hostname
cat /etc/os-release
nproc
free -h
df -h /
apt update
apt install -y python3 python3-venv python3-pip nginx git rsync curl ca-certificates certbot python3-certbot-nginx
install -d -m 0755 /opt/competidor
install -d -m 0750 -o www-data -g www-data /var/lib/competidor
install -d -m 0700 /root/competidor-migration
```

Libere no firewall da IONOS as portas 80 e 443. Mantenha o SSH acessível à sua
origem administrativa. A porta 8770 não precisa ser exposta: a aplicação escuta
somente em `127.0.0.1`, atrás do Nginx. Não habilite um firewall novo sem liberar
antes o SSH e conferir o acesso em uma segunda sessão.

## 3. Transferir o código

Primeiro disponibilize a versão corrigida do projeto no ANTIGO. A cópia abaixo
transfere exatamente o código que estiver em `/opt/competidor`; ela não baixa
automaticamente as alterações deste workspace. Se essa versão já foi publicada
no seu repositório, use no ANTIGO:

```bash
cd /opt/competidor
git status --short
git pull --ff-only
test -f deploy/competidor-dedicated-16gb.conf
test -f deploy/nginx-competidor-https.conf
```

Se houver alterações locais ou conflito, preserve-as e resolva antes de continuar;
não use `reset --hard`. Se as correções ainda não foram publicadas, transfira o
código corrigido por seu processo de deploy, preservando `data`, `.venv` e arquivos
de ambiente. Neste atendimento não foi realizado commit, push nem deploy.

No ANTIGO, defina o IP do NOVO e envie o código, preservando o antigo:

```bash
NOVO_IP='SUBSTITUA_PELO_IP_NOVO'
ssh root@"$NOVO_IP" hostname
rsync -a --exclude='.venv/' --exclude='.git/' --exclude='data/' --exclude='__pycache__/' --exclude='tests/' --exclude='tmp*/' /opt/competidor/ root@"$NOVO_IP":/opt/competidor/
```

Confirme a impressão digital SSH por uma fonte confiável do provedor ao conectar
pela primeira vez. Esse comando não apaga arquivos nem para serviços no antigo.
Se o seu repositório estiver em outro caminho, ajuste a origem. A pasta de dados
será transferida separadamente na etapa de parada final.

No NOVO, instale as dependências em um ambiente virtual novo. Não reutilize a
`.venv` do servidor antigo:

```bash
cd /opt/competidor
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m py_compile server.py
bash deploy/install-product-browser.sh
```

Se alguma dependência não instalar no Python fornecido pelo Ubuntu 26.04, resolva
isso **antes de parar o antigo**. Não ignore erros de instalação. O instalador do
navegador instala o Chromium usado pelo importador de produtos; não é preciso
copiar os processos Chrome ou caches do servidor antigo.

## 4. Instalar os serviços e o perfil dedicado no NOVO

```bash
cd /opt/competidor
install -m 0644 deploy/competidor.service /etc/systemd/system/competidor.service
install -d -m 0755 /etc/systemd/system/competidor.service.d
install -m 0644 deploy/competidor-dedicated-16gb.conf /etc/systemd/system/competidor.service.d/90-dedicated-16gb.conf
install -m 0600 deploy/competidor-dedicated.env.example /etc/competidor-dedicated.env
install -m 0755 deploy/competidor-healthcheck.sh /usr/local/sbin/competidor-healthcheck
install -m 0644 deploy/competidor-healthcheck.service /etc/systemd/system/competidor-healthcheck.service
install -m 0644 deploy/competidor-healthcheck.timer /etc/systemd/system/competidor-healthcheck.timer
systemctl daemon-reload
```

Não inicie o serviço nem o timer ainda. Não copie drop-ins de limites antigos:
eles poderiam manter o teto de 5 GB mesmo no host de 16 GB. O perfil novo permite
11 GiB antes da contenção e 13 GiB como limite máximo, reservando memória ao
sistema e ao navegador auxiliar. Isso é um limite, não memória previamente
alocada. O paralelismo da API continua controlado para não provocar erros 429.

## 5. Preparar HTTPS no NOVO

É possível reutilizar temporariamente o certificado válido do mesmo domínio.
No ANTIGO, substitua os dois caminhos abaixo pelos caminhos reais conferidos no
Nginx. Copie apenas o certificado necessário, não toda a configuração das outras
aplicações:

```bash
install -d -m 0700 /root/competidor-migration
install -m 0600 /etc/letsencrypt/live/competidor.umsoftware.com.br/fullchain.pem /root/competidor-migration/fullchain.pem
install -m 0600 /etc/letsencrypt/live/competidor.umsoftware.com.br/privkey.pem /root/competidor-migration/privkey.pem
scp /root/competidor-migration/fullchain.pem /root/competidor-migration/privkey.pem root@"$NOVO_IP":/root/competidor-migration/
```

Se o certificado estiver sob outro nome, ajuste o caminho; não crie arquivos
vazios para contornar a etapa. No NOVO:

```bash
install -d -m 0700 /etc/nginx/tls/competidor
install -m 0600 /root/competidor-migration/fullchain.pem /etc/nginx/tls/competidor/fullchain.pem
install -m 0600 /root/competidor-migration/privkey.pem /etc/nginx/tls/competidor/privkey.pem
```

O modelo HTTPS já inclui o certificado copiado, redirecionamento da porta 80,
arquivos estáticos e os parâmetros de proxy. Instale **somente esse modelo**;
não habilite também o modelo HTTP, pois duplicaria o upstream. No NOVO:

```bash
install -m 0644 /opt/competidor/deploy/nginx-competidor-https.conf /etc/nginx/sites-available/competidor
ln -s /etc/nginx/sites-available/competidor /etc/nginx/sites-enabled/competidor
nginx -t
systemctl reload nginx
```

Se o link já existir, confira se aponta para o arquivo correto, em vez de criar
outro. Não prossiga com erro no `nginx -t`. Antes de iniciar a aplicação, um 502
nesse domínio no NOVO é esperado: ainda não há backend ligado.

## 6. Parada final e backup no ANTIGO

Peça aos usuários que encerrem operações e saiam temporariamente da aplicação.
Espere lotes em execução terminarem. Pare **apenas** estes serviços:

```bash
systemctl stop competidor-healthcheck.timer
systemctl stop competidor-healthcheck.service
systemctl stop competidor.service
systemctl is-active competidor.service
```

O último comando deve mostrar `inactive`. Se houver outros timers/cron próprios
que reiniciem o CompeTIDOR, suspenda-os também. Não pare Nginx, PM2 nem serviços
das outras aplicações. Durante essa manutenção o domínio antigo pode responder
502 até a ativação do encaminhamento descrito adiante.

Defina o caminho **confirmado na etapa 1** e crie um backup fechado:

```bash
DADOS_ANTIGOS='/var/lib/competidor'
BACKUP_DIR="/root/competidor-migration/backup-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$BACKUP_DIR"
tar -C "$DADOS_ANTIGOS" -czf "$BACKUP_DIR/dados.tar.gz" .
cp -a /etc/competidor.env "$BACKUP_DIR/competidor.env"
cp -a /etc/nginx/sites-available/competidor "$BACKUP_DIR/nginx-competidor.conf"
sha256sum "$BACKUP_DIR/dados.tar.gz" > "$BACKUP_DIR/dados.sha256"
tar -tzf "$BACKUP_DIR/dados.tar.gz" > "$BACKUP_DIR/arquivos.txt"
```

Confira o espaço livre antes de criar o backup e preserve-o. A origem está parada,
portanto o conjunto de arquivos permanece consistente durante a transferência.

## 7. Transferência final dos dados e credenciais

Ainda no ANTIGO:

```bash
rsync -a --exclude='ms-playwright/' "$DADOS_ANTIGOS/" root@"$NOVO_IP":/var/lib/competidor/
scp /etc/competidor.env root@"$NOVO_IP":/etc/competidor.env
rsync -a --checksum --dry-run --itemize-changes --exclude='ms-playwright/' "$DADOS_ANTIGOS/" root@"$NOVO_IP":/var/lib/competidor/
```

A última verificação não deve listar arquivos de dados com conteúdo diferente.
Se listar, investigue antes de iniciar o NOVO. A barra final no caminho de origem
é importante: transfere o conteúdo para `/var/lib/competidor`, sem aninhar outra
pasta `competidor`. O cache do Chromium é excluído porque foi instalado no NOVO.

No NOVO:

```bash
chmod 0600 /etc/competidor.env
chown root:root /etc/competidor.env
chown -R www-data:www-data /var/lib/competidor
nano /etc/competidor.env
```

Altere somente os caminhos/portas necessários: `COMPETIDOR_DATA_DIR=/var/lib/competidor`,
`PORT=8770` e `COMPETIDOR_ENABLE_DEV_HTTPS=0`. Preserve client ID, client secret,
redirecionamento OAuth, Telegram e todas as configurações existentes. Não
substitua esse arquivo pelo `.env.example`, pois isso perderia os segredos.
O perfil dedicado é carregado depois por `/etc/competidor-dedicated.env`.

## 8. Primeira inicialização no NOVO

```bash
systemctl daemon-reload
systemctl start competidor
systemctl status competidor --no-pager -l
curl --fail --max-time 15 http://127.0.0.1:8770/api/live
curl --fail --max-time 30 http://127.0.0.1:8770/api/health
systemctl show competidor -p MemoryHigh -p MemoryMax -p CPUQuotaPerSecUSec
```

Confirme `ok:true`, o perfil `dedicated-16gb` no diagnóstico e os limites esperados.
Valide o HTTPS local:

```bash
curl --fail --resolve competidor.umsoftware.com.br:443:127.0.0.1 https://competidor.umsoftware.com.br/api/live
```

Na sua máquina Windows, substitua `IP_NOVO`:

```powershell
curl.exe --fail --resolve competidor.umsoftware.com.br:443:IP_NOVO https://competidor.umsoftware.com.br/api/live
```

Para testar pelo navegador antes do DNS, adicione temporariamente no arquivo
`C:\Windows\System32\drivers\etc\hosts` (editor como administrador):
`IP_NOVO competidor.umsoftware.com.br`. Acesse o domínio normal, valide login,
contas, catálogo, custos, metas, relatórios e importador. Depois remova essa linha.
Não reconecte contas que já estejam funcionando. Não publique anúncios apenas
para testar: uma consulta autenticada confirma acesso sem criar duplicações.

## 9. Encaminhar o domínio antigo durante a propagação

Mantenha o backend ANTIGO parado. Para que usuários e notificações ainda chegando
ao IP antigo sejam atendidos no NOVO, altere **somente o bloco HTTPS desse domínio**
no Nginx ANTIGO. Preserve as diretivas de certificado e `server_name`, e substitua
as locations específicas de estáticos e `/` por uma única location abaixo:

```nginx
location / {
    proxy_pass https://IP_NOVO;
    proxy_ssl_server_name on;
    proxy_ssl_name competidor.umsoftware.com.br;
    proxy_ssl_verify on;
    proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
    proxy_set_header Host competidor.umsoftware.com.br;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_connect_timeout 10s;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
}
```

Substitua `IP_NOVO`. O IP deve ser literal, não o próprio domínio antigo, para
evitar um ciclo de proxy. Preserve `client_max_body_size 30m` para uploads. O
encaminhamento deve abranger também JS/CSS e webhooks, não apenas `/api`.

```bash
nginx -t
systemctl reload nginx
```

Não reinicie o Nginx de forma abrupta; um reload válido preserva as outras
aplicações. Teste o domínio pelo IP antigo e confirme que `/api/live` apresenta
o mesmo uptime do NOVO. Mantenha esse encaminhamento durante a propagação DNS.

## 10. Trocar DNS e ativar monitoramento

Altere o registro A de `competidor.umsoftware.com.br` para o IP NOVO. Se existir
AAAA, atualize-o para o IPv6 do NOVO com Nginx/firewall IPv6 configurados ou remova
esse registro até configurar IPv6. Um AAAA antigo pode fazer parte dos clientes
continuar acessando o servidor errado.

No NOVO:

```bash
systemctl enable competidor
systemctl enable --now competidor-healthcheck.timer
systemctl list-timers --all | grep competidor
journalctl -u competidor -n 60 --no-pager
journalctl -t competidor-healthcheck -n 30 --no-pager
```

O monitor precisa consultar `/api/live`. No ANTIGO, deixe o serviço e seu timer
desabilitados para que um reboot não reative a cópia antiga:

```bash
systemctl disable competidor.service competidor-healthcheck.timer
```

## 11. Configurar renovação do certificado no NOVO

Depois que A/AAAA apontarem corretamente e a porta 80 estiver acessível:

```bash
certbot --nginx -d competidor.umsoftware.com.br
nginx -t
certbot renew --dry-run
systemctl list-timers --all | grep certbot
```

Esse passo substitui o certificado copiado temporariamente por um gerenciado no
NOVO e testa sua renovação. Só considere o HTTPS concluído depois do `dry-run`.
Não revogue o certificado antigo durante a propagação: ele ainda pode estar
atendendo o proxy do ANTIGO.

## 12. Conferência de dados e vendas

Compare com as capturas anteriores: contas conectadas, catálogo, custos, metas,
regras, agendamentos e histórico. Em Dashboard, use **Conferir / importar vendas
de outros meses**, selecione o primeiro e o último mês desejados e aguarde o
resultado por conta. Falhas são apresentadas sem transformar períodos incompletos
em totais definitivos. Compare pedidos pelo mesmo período/fuso e sem cancelados.

Não espere que CPU/RAM maiores corrijam diferenças de critério de faturamento.
Compare IDs dos pedidos quando houver divergência residual. Dados indisponíveis
na API não podem ser inventados para igualar outro relatório.

A API de pedidos informa retenção de até 12 meses. A conciliação protege meses
fora dessa janela integral: mantém o histórico local e apresenta a limitação,
em vez de substituir o histórico por zeros. Para períodos mais antigos sem dados
locais, preserve os backups e obtenha os pedidos em uma fonte histórica disponível.

## 13. Se precisar voltar ao ANTIGO

Depois da primeira inicialização no NOVO, tokens podem ter sido renovados e novos
dados podem ter sido gravados. **Não basta religar o backup antigo.**

1. Interrompa uso e aguarde operações no NOVO terminarem.
2. Pare o timer, o healthcheck e o serviço `competidor` no NOVO.
3. Faça backup dos dados atuais do NOVO e dos dados parados do ANTIGO.
4. Transfira os dados atuais do NOVO de volta ao diretório real do ANTIGO e confira
   os checksums. Preserve credenciais atuais; restaure caminhos de ambiente do ANTIGO.
5. Reinstale no ANTIGO a mesma versão de código compatível com esses dados.
6. Restaure somente a configuração Nginx do CompeTIDOR que foi salva no backup.
7. Inicie o CompeTIDOR ANTIGO e teste localmente antes de trocar DNS de volta.
8. Se o DNS já apontava para o NOVO, mantenha temporariamente o Nginx NOVO
   encaminhando para o ANTIGO, sem nenhum backend local ativo no NOVO.
9. Reative o monitor apenas no host que ficará executando a aplicação.

Ao copiar de volta, utilize diretórios verificados e uma cópia consistente. Não
mescle dois servidores que tenham recebido gravações simultâneas: nesse caso é
necessária uma reconciliação dos dados antes da retomada.

### Comandos para o retorno, com ambas as cópias paradas

No NOVO, depois de concluir as operações em andamento:

```bash
systemctl disable --now competidor-healthcheck.timer
systemctl stop competidor-healthcheck.service
systemctl disable --now competidor.service
systemctl is-active competidor.service
RETORNO_DIR="/root/competidor-migration/retorno-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$RETORNO_DIR"
tar -C /var/lib/competidor -czf "$RETORNO_DIR/dados-atuais.tar.gz" .
sha256sum "$RETORNO_DIR/dados-atuais.tar.gz"
```

O serviço deve estar `inactive`. Anote o caminho exato do arquivo e seu SHA256.
No ANTIGO, confirme que continua parado e receba esse arquivo, sem sobrescrever
o diretório antigo neste momento:

```bash
systemctl stop competidor-healthcheck.timer competidor-healthcheck.service competidor.service
systemctl is-active competidor.service
NOVO_IP='SUBSTITUA_PELO_IP_NOVO'
ARQUIVO_NOVO='/root/competidor-migration/retorno-SUBSTITUA/dados-atuais.tar.gz'
RETORNO_LOCAL="/root/competidor-migration/retorno-recebido-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$RETORNO_LOCAL"
scp root@"$NOVO_IP":"$ARQUIVO_NOVO" "$RETORNO_LOCAL/dados-atuais.tar.gz"
sha256sum "$RETORNO_LOCAL/dados-atuais.tar.gz"
tar -tzf "$RETORNO_LOCAL/dados-atuais.tar.gz" | head -n 20
```

Compare o SHA256 com o NOVO. Ele deve ser idêntico. Extraia em uma pasta nova,
para não misturar arquivos antigos com arquivos atuais nem apagar o backup:

```bash
DADOS_RETORNO="/var/lib/competidor-retorno-$(date +%Y%m%d-%H%M%S)"
install -d -m 0750 -o www-data -g www-data "$DADOS_RETORNO"
tar -C "$DADOS_RETORNO" -xzf "$RETORNO_LOCAL/dados-atuais.tar.gz"
chown -R www-data:www-data "$DADOS_RETORNO"
printf 'Diretorio de dados para configurar: %s\n' "$DADOS_RETORNO"
nano /etc/competidor.env
systemctl cat competidor
```

Em `/etc/competidor.env`, aponte `COMPETIDOR_DATA_DIR` para o caminho exibido.
Verifique se algum drop-in redefine esse caminho. Se a unidade usa
`ReadWritePaths`, autorize também a nova pasta em um drop-in específico do
CompeTIDOR (`systemctl edit competidor`):

```ini
[Service]
ReadWritePaths=/var/lib/competidor-retorno-SUBSTITUA
```

Use o caminho real, sem `SUBSTITUA`. Preserve os limites do host antigo; não
transfira o perfil de 16 GB para o servidor de 8 GB. O código antigo deve ser a
mesma versão usada no NOVO para ler esses dados. Se houve atualização posterior
no NOVO, transfira essa versão e instale suas dependências na `.venv` do ANTIGO
antes de iniciar. Nunca copie uma `.venv` entre servidores.

Restaure o arquivo Nginx salvo na etapa 6. Substitua o caminho do backup abaixo
pelo existente; confira-o com `ls` antes da cópia:

```bash
BACKUP_ORIGINAL='/root/competidor-migration/backup-SUBSTITUA'
ls -l "$BACKUP_ORIGINAL/nginx-competidor.conf"
cp -a /etc/nginx/sites-available/competidor "$RETORNO_LOCAL/nginx-antes-do-retorno.conf"
cp -a "$BACKUP_ORIGINAL/nginx-competidor.conf" /etc/nginx/sites-available/competidor
systemctl daemon-reload
systemctl start competidor
curl --fail --max-time 15 http://127.0.0.1:8770/api/live
nginx -t
systemctl reload nginx
curl --fail --max-time 15 --resolve competidor.umsoftware.com.br:443:127.0.0.1 https://competidor.umsoftware.com.br/api/live
```

Prossiga apenas se os testes responderem com sucesso. No NOVO, configure o
encaminhamento da etapa 9 invertendo o destino para o **IP ANTIGO**. O backend
NOVO permanece parado. Teste esse encaminhamento e só então retorne A/AAAA ao
ANTIGO. Habilite novamente apenas no ANTIGO:

```bash
systemctl enable competidor
systemctl enable --now competidor-healthcheck.timer
```

Mantenha o proxy do NOVO durante a propagação. Esse procedimento preserva os
dados e tokens atuais; os diretórios anteriores permanecem disponíveis para
auditoria, sem serem usados por uma segunda instância.

## 14. Depois da mudança

Mantenha o backup e o encaminhamento antigo por pelo menos o tempo de propagação
observado; 48 horas é uma janela prática inicial, mas confira acessos ao domínio
antigo antes de remover o proxy. O ANTIGO continua hospedando suas outras
aplicações e não deve ser cancelado por causa desta migração.

Observe os logs durante uso real:

```bash
journalctl -u competidor --since '1 hour ago' --no-pager
journalctl -t competidor-healthcheck --since '1 hour ago' --no-pager
tail -n 40 /var/log/nginx/error.log
free -h
systemctl show competidor -p MemoryCurrent -p MemoryHigh -p MemoryMax
```

As verificações locais do projeto não substituem o teste no Ubuntu 26.04 nem a
observação sob a carga real. Não há garantia de eliminar qualquer 502 externo
com o upgrade; este roteiro evita a execução duplicada, perda de tokens e os
limites de RAM herdados do host compartilhado.

Referências oficiais: [Ubuntu 26.04](https://documentation.ubuntu.com/release-notes/26.04/),
[pedidos Mercado Livre](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas).
