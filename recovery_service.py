"""Loopback-only emergency restart service, independent of the main application."""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get('COMPETIDOR_DATA_DIR', '/var/lib/competidor'))
STATE = Path(os.environ.get('RECOVERY_STATE_DIR', '/var/lib/competidor-recovery'))
ORIGIN = os.environ.get('RECOVERY_ORIGIN', 'https://competidor.umsoftware.com.br').rstrip('/')
KEY = secrets.token_bytes(32)
LOCK = threading.Lock()
COMMAND = ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'restart', 'competidor.service']

def read(path, fallback):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError: return fallback

def authorize(cookie):
    jar = SimpleCookie()
    jar.load(cookie or '')
    token = jar.get('competidor_session')
    token = token.value if token else ''
    if not token: return None, ''
    session = next((s for s in read(DATA/'sessions.json', []) if s.get('token') == token and s.get('expires_at', 0) > time.time()), None)
    if not session: return None, ''
    user = next((u for u in read(DATA/'app.json', {}).get('users', []) if u.get('id') == session.get('user_id') and u.get('role') in {'master','admin'} and u.get('status') in {'ativo','active'}), None)
    return user, hmac.new(KEY, token.encode(), hashlib.sha256).hexdigest() if user else ''

def restart(actor):
    print(json.dumps({'event':'restart_requested','actor':actor,'time':time.time()}), flush=True)
    try:
        result = subprocess.run(COMMAND, capture_output=True, timeout=90, check=False)
        status = 'Reinício concluído.' if result.returncode == 0 else 'Falha ao reiniciar. Consulte o journal do serviço de recuperação.'
    except Exception:
        status = 'Falha ao reiniciar. Consulte o journal do serviço de recuperação.'
    with LOCK:
        state = read(STATE/'state.json', {})
        state.update(status=status, running=False)
        save(state)
    print(json.dumps({'event':'restart_finished','actor':actor,'status':status}), flush=True)

def save(state):
    STATE.mkdir(parents=True, exist_ok=True)
    temp = STATE/'state.tmp'
    temp.write_text(json.dumps(state), encoding='utf-8')
    os.replace(temp, STATE/'state.json')

def reserve(actor):
    with LOCK:
        state = read(STATE/'state.json', {})
        if time.time()-state.get('at',0) < 180: return False
        save({'at':time.time(), 'status':'Reiniciando aplicação…', 'running':True})
        threading.Thread(target=restart, args=(actor,), daemon=True).start()
        return True

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def reply(self, code, body, html=False):
        data = body.encode() if html else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def auth(self):
        try: return authorize(self.headers.get('Cookie'))
        except Exception: return None, ''

    def do_GET(self):
        user, csrf = self.auth()
        if not user:
            return self.reply(403, {'error':'Entre na aplicação como Master ou Administrador. Uma sessão válida é necessária.'})
        if self.path == '/recovery/':
            return self.reply(200, (Path(__file__).with_name('recovery.html')).read_text(encoding='utf-8'), True)
        if self.path != '/recovery/status': return self.reply(404, {})
        healthy = False
        try:
            with urllib.request.urlopen('http://127.0.0.1:8770/api/live', timeout=2) as response:
                healthy = response.status == 200 and json.load(response).get('ok') is True
        except Exception: pass
        state = read(STATE/'state.json', {})
        self.reply(200, {'csrf':csrf,'healthy':healthy,'status':state.get('status','Pronto para solicitar reinício.'),'running':state.get('running',False),'cooldown':max(0, int(180-time.time()+state.get('at',0)))})

    def do_POST(self):
        user, csrf = self.auth()
        if not user: return self.reply(403, {'error':'Acesso restrito.'})
        if self.path != '/recovery/restart': return self.reply(404,{})
        if self.headers.get('Origin') != ORIGIN or not hmac.compare_digest(self.headers.get('X-Recovery-CSRF',''),csrf):
            return self.reply(403, {'error':'Origem ou confirmação de sessão inválida. Reabra esta página.'})
        try:
            size = int(self.headers.get('Content-Length',0))
            if not 0 < size <= 1024 or self.headers.get('Content-Type') != 'application/json': raise ValueError()
            body = json.loads(self.rfile.read(size))
            if body.get('confirm') != 'REINICIAR': raise ValueError()
        except Exception: return self.reply(400, {'error':'Confirme o reinício.'})
        if not reserve(user['id']): return self.reply(429, {'error':'Aguarde três minutos entre solicitações de reinício.'})
        self.reply(202, {'ok':True})

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1',8771), Handler).serve_forever()
