# bling_oauth_bootstrap.py
# -*- coding: utf-8 -*-
"""
Bootstrap 100% automático para pegar o authorization code, trocar por tokens
(access_token e refresh_token) e gerar o config.yaml do dashboard.

Como usar (Windows/Mac/Linux):
1) pip install requests pyyaml
2) python bling_oauth_bootstrap.py
   - o script abre o navegador pedindo autorização no Bling
   - captura automaticamente o "code" pelo REDIRECT_URI configurado (ex.: http://localhost:8001/callback)
   - troca por tokens e grava config.yaml
3) Depois rode o dashboard:
   streamlit run bling_dashboard_streamlit.py

Observações:
- As credenciais (CLIENT_ID e CLIENT_SECRET) e o REDIRECT_URI estão definidos no código abaixo.
- Garanta que o REDIRECT_URI esteja cadastrado exatamente igual no app do Bling.
- Para adicionar outra conta, rode novamente e informe outro "account_name" quando solicitado.
"""

import http.server
import socketserver
import threading
import webbrowser
import urllib.parse as urlparse
import requests
import yaml
import os
import sys
from typing import Optional

CLIENT_ID = "2b9d9677cf98c166950656fd9d7161795d499e58"
CLIENT_SECRET = "f3504e6d70c566bff108d0feabe4f477bab3be49b7198498ff57e730db89"
REDIRECT_URI = "http://localhost:8001/callback"
AUTH_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"
CONFIG_PATH = os.path.join(os.getcwd(), "config.yaml")

# Variáveis globais para comunicar com o handler HTTP
_received_code: Optional[str] = None
_http_error: Optional[str] = None

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global _received_code, _http_error
        try:
            parsed = urlparse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
                return
            qs = urlparse.parse_qs(parsed.query)
            code = (qs.get("code") or [None])[0]
            if not code:
                _http_error = "Nao veio 'code' na URL de callback."
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Erro: nao veio 'code' na URL.")
                return
            _received_code = code
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Autorizacao recebida! Pode voltar ao terminal.")
        except Exception as e:
            _http_error = str(e)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Erro interno: {e}".encode("utf-8"))

    # Evita logs ruidosos no console
    def log_message(self, format, *args):
        return


def open_authorization_page():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        # opcional: state para validar retorno
        "state": "bling-oauth-local",
    }
    url = AUTH_URL + "?" + urlparse.urlencode(params)
    print("\nAbrindo o navegador para autorizar o aplicativo no Bling...\n")
    print(url)
    webbrowser.open(url)


def run_local_server_until_code(timeout_seconds: int = 300) -> str:
    global _received_code, _http_error
    _received_code = None
    _http_error = None

    # Descobre porta a partir do REDIRECT_URI para evitar divergencia
    parsed_redirect = urlparse.urlparse(REDIRECT_URI)
    port = parsed_redirect.port or (443 if parsed_redirect.scheme == "https" else 80)

    with socketserver.TCPServer(("", port), CallbackHandler) as httpd:
        httpd.timeout = 1
        print(f"Aguardando o callback em {REDIRECT_URI} ...")
        # roda até receber o code ou estourar timeout
        import time
        start = time.time()
        while True:
            httpd.handle_request()
            if _received_code:
                return _received_code
            if _http_error:
                raise RuntimeError(_http_error)
            if time.time() - start > timeout_seconds:
                raise TimeoutError("Timeout aguardando o callback do OAuth2.")


def exchange_code_for_tokens(code: str) -> dict:
    auth = requests.auth.HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    resp = requests.post(TOKEN_URL, auth=auth, data=data, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Falha ao trocar code por tokens: {resp.status_code} - {resp.text}")
    return resp.json()


def upsert_config_yaml(account_name: str, refresh_token: str):
    cfg = {"accounts": []}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or cfg
        except Exception:
            pass
    # remove entrada antiga com o mesmo nome
    cfg_accounts = [a for a in cfg.get("accounts", []) if a.get("name") != account_name]
    cfg_accounts.append({
        "name": account_name,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    })
    cfg["accounts"] = cfg_accounts
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def main():
    print("=== Bling OAuth Bootstrap ===")
    print("Este assistente vai autorizar o app, pegar os tokens e gravar o config.yaml.\n")
    account_name = input("Nome para esta conta no dashboard (ex.: Loja Tiburcio's Stuff): ") or "Loja Tiburcio's Stuff"

    # 1) Abrir autorização
    open_authorization_page()

    # 2) Subir servidor local e esperar o code
    code = run_local_server_until_code()
    print(f"\nCode recebido com sucesso: {code[:8]}... (ocultando o restante)\n")

    # 3) Trocar code -> tokens
    tokens = exchange_code_for_tokens(code)
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    print("Tokens obtidos!")
    masked_access = (access or "")[:12]
    masked_refresh = (refresh or "")[:12]
    print(f"access_token (oculto): {masked_access}...")
    print(f"refresh_token (oculto): {masked_refresh}...\n")

    # 4) Gravar/atualizar config.yaml
    upsert_config_yaml(account_name, refresh)
    print(f"config.yaml atualizado em: {CONFIG_PATH}\n")
    print("Próximo passo:")
    print("  1) pip install streamlit pydantic requests pyyaml python-dateutil pandas")
    print("  2) streamlit run bling_dashboard_streamlit.py")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado pelo usuário.")
    except Exception as e:
        print(f"Erro: {e}")
        sys.exit(1)
