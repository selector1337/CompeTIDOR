# CompeTIDOR

Aplicação SaaS para operação multicontas no Mercado Livre: OAuth oficial, catálogo, anúncios, alertas, concorrentes, scan de preço e Telegram.

## Primeiro Acesso

Ao iniciar sem dados, a aplicação não cria usuários padrão. No primeiro acesso, ela solicita a criação do usuário `master`.

Não existe login demo, senha padrão ou conta Mercado Livre pré-carregada.

## Rodar Localmente

```powershell
python -m pip install -r requirements.txt
$env:PORT="8765"
$env:MELI_CLIENT_ID="seu_client_id"
$env:MELI_CLIENT_SECRET="seu_client_secret"
$env:MELI_REDIRECT_URI="https://competidor.umsoftware.com.br/api/oauth/callback"
python server.py
```

Acesse `http://127.0.0.1:8765`.

## Variáveis de Produção

Use variáveis de ambiente no servidor. Não suba segredos para o GitHub.

```bash
PORT=8765
COMPETIDOR_DATA_DIR=/var/lib/competidor
MELI_CLIENT_ID=seu_client_id
MELI_CLIENT_SECRET=seu_client_secret
MELI_REDIRECT_URI=https://competidor.umsoftware.com.br/api/oauth/callback
SCAN_INTERVAL_SECONDS=300
AUTO_SYNC_INTERVAL_SECONDS=900
AUTO_SYNC_STARTUP_DELAY_SECONDS=45
AUTO_REFRESH_BATCH_SIZE=400
AUTO_COMPETITION_BATCH_SIZE=12
CATEGORY_ATTRIBUTES_CACHE_SECONDS=3600
MELI_SYNC_STATUSES=active,paused,under_review
MELI_SCAN_MAX_PAGES=1000
MELI_SYNC_INLINE_LIMIT=200
MELI_SYNC_COMPETITION_INLINE_LIMIT=0
MELI_PUBLIC_PAGE_TIMEOUT_SECONDS=4
```

No painel da aplicação no Mercado Livre, configure a URL de notificações como:

```text
https://competidor.umsoftware.com.br/api/notifications/meli
```

Ative pelo menos os tópicos `Items` e `Orders v2`. O endpoint confirma o recebimento imediatamente e processa estoque, preço, status e vendas em uma fila interna.

No painel de desenvolvedores do Mercado Livre, cadastre exatamente:

```text
https://competidor.umsoftware.com.br/api/oauth/callback
```

## O Que Subir Para o GitHub

Suba:

- `server.py`
- `public/`
- `requirements.txt`
- `README.md`
- `.gitignore`

Não suba:

- `data/*.json`
- `data/certs/`
- `*.log`
- `__pycache__/`
- `cloudflared.exe`

O backend cria os arquivos locais em `data/` quando iniciar.

## Deploy Em Servidor Linux Com HTTPS

Exemplo usando Ubuntu, Nginx, systemd e Certbot.

1. Aponte o DNS:

```text
competidor.umsoftware.com.br -> IP público do servidor
```

2. Instale pacotes:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git
```

3. Clone o repositório:

```bash
sudo mkdir -p /opt/competidor
sudo chown $USER:$USER /opt/competidor
git clone https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git /opt/competidor
cd /opt/competidor
```

4. Crie ambiente Python:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

5. Crie variáveis seguras:

```bash
sudo nano /etc/competidor.env
```

Conteúdo:

```bash
PORT=8765
COMPETIDOR_DATA_DIR=/var/lib/competidor
MELI_CLIENT_ID=seu_client_id
MELI_CLIENT_SECRET=seu_client_secret
MELI_REDIRECT_URI=https://competidor.umsoftware.com.br/api/oauth/callback
SCAN_INTERVAL_SECONDS=300
AUTO_SYNC_INTERVAL_SECONDS=900
AUTO_SYNC_STARTUP_DELAY_SECONDS=45
AUTO_REFRESH_BATCH_SIZE=400
AUTO_COMPETITION_BATCH_SIZE=12
CATEGORY_ATTRIBUTES_CACHE_SECONDS=3600
MELI_SYNC_STATUSES=active,paused,under_review
MELI_SCAN_MAX_PAGES=1000
MELI_SYNC_INLINE_LIMIT=200
MELI_SYNC_COMPETITION_INLINE_LIMIT=0
MELI_PUBLIC_PAGE_TIMEOUT_SECONDS=4
```

6. Crie o serviço:

```bash
sudo nano /etc/systemd/system/competidor.service
```

Conteúdo:

```ini
[Unit]
Description=CompeTIDOR
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/competidor
EnvironmentFile=/etc/competidor.env
ExecStart=/opt/competidor/.venv/bin/python /opt/competidor/server.py
Restart=always
RestartSec=5
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
```

7. Permita escrita na pasta de dados:

```bash
sudo mkdir -p /var/lib/competidor
sudo cp -an /opt/competidor/data/. /var/lib/competidor/
sudo chown -R www-data:www-data /var/lib/competidor
sudo chown -R www-data:www-data /opt/competidor
```

8. Suba o serviço:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now competidor
sudo systemctl status competidor
```

9. Configure Nginx:

```bash
sudo nano /etc/nginx/sites-available/competidor
```

Conteúdo:

```nginx
server {
    listen 80;
    server_name competidor.umsoftware.com.br;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

10. Ative o site:

```bash
sudo ln -s /etc/nginx/sites-available/competidor /etc/nginx/sites-enabled/competidor
sudo nginx -t
sudo systemctl reload nginx
```

11. Instale HTTPS:

```bash
sudo certbot --nginx -d competidor.umsoftware.com.br
```

12. Teste:

```bash
curl -I https://competidor.umsoftware.com.br
sudo journalctl -u competidor -f
```

Depois disso, abra `https://competidor.umsoftware.com.br`, crie o usuário master e configure o OAuth do Mercado Livre na página Contas.
