# bling_dashboard_streamlit.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time
import datetime as dt
from dateutil.relativedelta import relativedelta
from typing import Optional, Tuple, List
from urllib.parse import urlencode, urlparse, parse_qs

import pandas as pd
import requests
import streamlit as st
import altair as alt

# ================== CONFIG ==================
APP_BASE         = st.secrets.get("APP_BASE", "https://SEU-APP.streamlit.app")
TS_CLIENT_ID     = st.secrets["TS_CLIENT_ID"]
TS_CLIENT_SECRET = st.secrets["TS_CLIENT_SECRET"]

AUTH_URL  = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"

# Pedidos (para KPIs de vendas)
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"

# Receber/Pagar (fallback para DRE se extratos não estiverem disponíveis)
RECEBER_URL = "https://www.bling.com.br/Api/v3/contas/receber"
PAGAR_URL   = "https://www.bling.com.br/Api/v3/contas/pagar"

PAGE_LIMIT = 100

st.set_page_config(page_title="Dashboard de vendas – Bling (Tiburcio’s Stuff)", layout="wide")

# ================== STATE ==================
st.session_state.setdefault("ts_refresh", st.secrets.get("TS_REFRESH_TOKEN"))
st.session_state.setdefault("ts_access", None)
st.session_state.setdefault("_last_code_used", None)

# ================== OAUTH HELPERS ==================
def build_auth_link(client_id: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": APP_BASE,
        "state": state,
    })

def post_with_backoff(url, auth, data, tries=3, wait=3):
    for i in range(tries):
        r = requests.post(url, auth=auth, data=data, timeout=30)
        if r.status_code == 429 and i < tries - 1:
            time.sleep(wait * (i + 1))
            continue
        return r
    return r

def exchange_code_for_tokens(code: str) -> dict:
    r = post_with_backoff(
        TOKEN_URL,
        auth=(TS_CLIENT_ID, TS_CLIENT_SECRET),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": APP_BASE},
    )
    if r.status_code == 429:
        raise RuntimeError("Rate limit (429) no Bling. Aguarde alguns minutos e tente novamente.")
    if r.status_code != 200:
        raise RuntimeError(f"Falha na troca do code: {r.status_code} - {r.text}")
    return r.json()

def refresh_access_token(refresh_token: str) -> Tuple[str, Optional[str]]:
    r = post_with_backoff(
        TOKEN_URL,
        auth=(TS_CLIENT_ID, TS_CLIENT_SECRET),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    if r.status_code == 429:
        raise RuntimeError("Rate limit (429) ao renovar token. Tente novamente em alguns minutos.")
    if r.status_code != 200:
        raise RuntimeError(f"Falha ao renovar token: {r.status_code} - {r.text}")
    j = r.json()
    return j.get("access_token", ""), j.get("refresh_token")

# ================== CAPTURA AUTOMÁTICA DO ?code= ==================
def _normalize_qp(d: dict) -> dict:
    return {k: (v[0] if isinstance(v, list) else v) for k, v in d.items()}

def auto_capture_code() -> Optional[tuple[str, str]]:
    # st.query_params (Streamlit >= 1.33)
    try:
        qp = _normalize_qp(dict(st.query_params.items()))
        if qp.get("code") and qp.get("state"):
            return qp["code"], qp["state"]
    except Exception:
        pass
    # compat: experimental_get_query_params
    try:
        qp = _normalize_qp(st.experimental_get_query_params())
        if qp.get("code") and qp.get("state"):
            return qp["code"], qp["state"]
    except Exception:
        pass
    return None

captured = auto_capture_code()
if captured:
    code, state = captured
    if state == "auth-ts" and code and code != st.session_state["_last_code_used"]:
        st.session_state["_last_code_used"] = code
        try:
            tokens = exchange_code_for_tokens(code)
            st.session_state["ts_refresh"] = tokens.get("refresh_token")
            st.session_state["ts_access"]  = tokens.get("access_token")
            st.success("TS autorizado e refresh_token atualizado!")
        except Exception as e:
            st.error(f"Não foi possível autorizar TS: {e}")
        finally:
            try:
                st.query_params.clear()
            except Exception:
                st.query_params = {}
            st.rerun()

# ================== LAYOUT EM ABAS ==================
tab_dash, tab_oauth = st.tabs(["📊 Dashboard", "🔐 Integração (OAuth)"])

# ---------------- OAuth TAB ----------------
with tab_oauth:
    st.header("Integração com o Bling (OAuth)")
    st.caption(f"Redirect configurado: `{APP_BASE}`")

    # 1) Link para autorizar via OAuth
    auth_link = build_auth_link(TS_CLIENT_ID, "auth-ts")
    st.markdown(
        f'<a href="{auth_link}" target="_blank" rel="noopener" class="stButton"><button>Autorizar TS</button></a>',
        unsafe_allow_html=True,
    )
    with st.expander("Ver URL de autorização (debug)"):
        st.code(auth_link, language="text")

    # 2) Finalizar autorização manualmente (colar URL ou code)
    st.subheader("Finalizar autorização (se necessário)")
    st.write("Se voltou do Bling com `?code=...&state=auth-ts`, cole a **URL completa** (ou só o `code`) e clique **Trocar agora**.")
    manual = st.text_input("Cole a URL de retorno do Bling ou apenas o code", key="manual_auth_input")
    if st.button("Trocar agora", key="btn_manual_exchange"):
        code_value = None
        raw = manual.strip()
        if not raw:
            st.error("Cole a URL ou o code.")
        else:
            if raw.startswith("http"):
                try:
                    qs = parse_qs(urlparse(raw).query)
                    code_value  = (qs.get("code") or [None])[0]
                    state_value = (qs.get("state") or [None])[0]
                    if state_value and state_value != "auth-ts":
                        st.error("State diferente de auth-ts. Confira a URL de retorno.")
                        code_value = None
                except Exception as e:
                    st.error(f"URL inválida: {e}")
            else:
                code_value = raw

            if code_value:
                if code_value == st.session_state["_last_code_used"]:
                    st.warning("Este code já foi usado. Gere um novo clicando em Autorizar TS.")
                else:
                    st.session_state["_last_code_used"] = code_value
                    try:
                        tokens = exchange_code_for_tokens(code_value)
                        st.session_state["ts_refresh"] = tokens.get("refresh_token")
                        st.session_state["ts_access"]  = tokens.get("access_token")
                        st.success("TS autorizado e refresh_token atualizado!")
                        try:
                            st.query_params.clear()
                        except Exception:
                            st.query_params = {}
                        st.rerun()
                    except Exception as e:
                        st.error(f"Falha na troca manual do code: {e}")

    st.divider()

    # 3) Refresh token (avançado): ver/copiar/colar/aplicar
    st.subheader("Refresh token (avançado)")
    st.caption("Use este bloco para copiar o refresh_token atual ou colar um novo manualmente.")
    current_rt = st.session_state.get("ts_refresh") or ""
    col_a, col_b = st.columns([3, 1])
    with col_a:
        rt_input = st.text_area(
            "Refresh token (você pode copiar ou colar um novo aqui)",
            value=current_rt,
            height=80,
            key="rt_textarea",
        )
    with col_b:
        st.write("")
        if st.button("Aplicar no app", use_container_width=True):
            if not rt_input.strip():
                st.error("Informe um refresh_token antes de aplicar.")
            else:
                st.session_state["ts_refresh"] = rt_input.strip()
                st.cache_data.clear()
                st.success("Refresh token aplicado para esta sessão do app.")
        if st.button("Renovar access_token agora", use_container_width=True):
            if not st.session_state.get("ts_refresh"):
                st.error("Não há refresh_token carregado. Cole um acima ou autorize.")
            else:
                try:
                    acc, maybe_new_rt = refresh_access_token(st.session_state["ts_refresh"])
                    st.session_state["ts_access"] = acc
                    if maybe_new_rt:
                        st.session_state["ts_refresh"] = maybe_new_rt
                        st.success("Access_token renovado e refresh_token atualizado.")
                    else:
                        st.success("Access_token renovado.")
                except Exception as e:
                    st.error(f"Falha ao renovar token: {e}")

    with st.expander("Como salvar no Secrets do Streamlit (persistente)"):
        st.markdown(
            """
**Passos:**
1. Clique em **Manage app** (canto inferior direito) → **Secrets**.  
2. Adicione/edite a chave `TS_REFRESH_TOKEN` com o valor do refresh token.  
3. Salve.  
4. Recarregue o app.

Exemplo de Secrets (além de `APP_BASE`, `TS_CLIENT_ID`, `TS_CLIENT_SECRET`):
```toml
APP_BASE = "https://SEU-APP.streamlit.app"
TS_CLIENT_ID = "COLE_AQUI"
TS_CLIENT_SECRET = "COLE_AQUI"
TS_REFRESH_TOKEN = "SEU_REFRESH_TOKEN"  # opcional
        """
    )
---------------- Sidebar filtros ----------------

st.sidebar.header("Filtros")
DEFAULT_START = (dt.date.today() - relativedelta(months=1)).replace(day=1)
DEFAULT_END = dt.date.today()
c1, c2 = st.sidebar.columns(2)
with c1:
date_start = st.date_input("Data inicial", value=DEFAULT_START)
with c2:
date_end = st.date_input("Data final", value=DEFAULT_END)
if st.sidebar.button("Atualizar dados"):
st.cache_data.clear()

================== BUSCAS ==================

@st.cache_data(ttl=300, show_spinner=False)
def fetch_orders(refresh_token: str, date_start: dt.date, date_end: dt.date) -> Tuple[pd.DataFrame, Optional[str]]:
access, maybe_new_refresh = refresh_access_token(refresh_token)
headers = {"Authorization": f"Bearer {access}"}
params = {
"dataInicial": date_start.strftime("%Y-%m-%d"),
"dataFinal": date_end.strftime("%Y-%m-%d"),
"limite": PAGE_LIMIT,
"pagina": 1,
}
all_rows: List[dict] = []
while True:
r = requests.get(ORDERS_URL, headers=headers, params=params, timeout=30)
if r.status_code != 200:
raise RuntimeError(f"Erro ao listar pedidos p{params['pagina']}: {r.status_code} - {r.text}")
data = r.json()
rows = data if isinstance(data, list) else data.get("data") or data.get("itens") or []
if not rows:
break
all_rows.extend(rows)
if len(rows) < PAGE_LIMIT:
break
params["pagina"] += 1
def g(d, key, default=None):
    return d.get(key, default) if isinstance(d, dict) else default
def gg(d, k1, k2, default=None):
    return g(g(d, k1, {}), k2, default)

recs = []
for x in all_rows:
    recs.append({
        "id": g(x, "id"),
        "data": g(x, "data"),
        "numero": g(x, "numero"),
        "numeroLoja": g(x, "numeroLoja"),
        "total": g(x, "total"),
        "loja_id": gg(x, "loja", "id"),
    })
df = pd.DataFrame(recs)
if not df.empty:
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df["total"] = pd.to_numeric(df["total"], errors="coerce")
return df, maybe_new_refresh
====== 1) EXTRATOS / CAIXAS & BANCOS (CONFIRMADOS) ======

def _get_paginated_generic(url: str, headers: dict, params: dict) -> List[dict]:
out: List[dict] = []
p = params.copy()
p["limite"] = PAGE_LIMIT
p["pagina"] = 1
while True:
r = requests.get(url, headers=headers, params=p, timeout=30)
if r.status_code != 200:
raise RuntimeError(f"{url} -> {r.status_code} - {r.text}")
j = r.json()
rows = j if isinstance(j, list) else j.get("data") or j.get("itens") or []
if not rows:
break
out.extend(rows)
if len(rows) < PAGE_LIMIT:
break
p["pagina"] += 1
return out

@st.cache_data(ttl=300, show_spinner=False)
def fetch_bank_confirmed(refresh_token: str, date_start: dt.date, date_end: dt.date) -> Tuple[pd.DataFrame, Optional[str]]:
"""
Tenta recuperar movimentos confirmados em 'Caixas & Bancos'.
Como a rota e parâmetros podem variar por conta/escopo no Bling,
testamos combinações comuns de endpoints e nomes de parâmetros.
"""
access, maybe_new_refresh = refresh_access_token(refresh_token)
headers = {"Authorization": f"Bearer {access}"}
candidates = [
    ("https://www.bling.com.br/Api/v3/financeiro/extratos", ("dataInicial", "dataFinal")),
    ("https://www.bling.com.br/Api/v3/financeiro/extratos", ("dataMovimentoInicial", "dataMovimentoFinal")),
    ("https://www.bling.com.br/Api/v3/caixas/extratos", ("dataInicial", "dataFinal")),
    ("https://www.bling.com.br/Api/v3/caixas-bancos/extratos", ("dataInicial", "dataFinal")),
    ("https://www.bling.com.br/Api/v3/contas/extratos", ("dataInicial", "dataFinal")),
]

last_err = None
rows: List[dict] = []
for url, (p_ini, p_fim) in candidates:
    try:
        params = {
            p_ini: date_start.strftime("%Y-%m-%d"),
            p_fim: date_end.strftime("%Y-%m-%d"),
            "apenasConfirmados": "true",
        }
        rows = _get_paginated_generic(url, headers, params)
        if rows:
            break
    except Exception as e:
        last_err = e
        rows = []

if rows == [] and last_err:
    raise RuntimeError(f"Extratos ('Caixas & Bancos') não disponíveis: {last_err}")

def g(d, k, default=None): return d.get(k, default) if isinstance(d, dict) else default
def pick_date(d):
    return (g(d, "data") or g(d, "dataMovimento") or g(d, "dataLancamento")
            or g(d, "dataBaixa") or g(d, "dataCredito") or g(d, "dataDebito"))
def pick_amount(d):
    credito = g(d, "valorCredito") or g(d, "credito") or 0
    debito  = g(d, "valorDebito")  or g(d, "debito")  or 0
    if credito or debito:
        c = float(pd.to_numeric(credito, errors="coerce") or 0)
        d = float(pd.to_numeric(debito,  errors="coerce") or 0)
        return c - d
    valor = g(d, "valorLancamento") or g(d, "valorAbsoluto") or g(d, "valor")
    if valor is not None:
        v = float(pd.to_numeric(valor, errors="coerce") or 0)
        tipo = (g(d, "tipo") or g(d, "natureza") or "").upper()
        if tipo.startswith("D") or "SAID" in tipo: return -abs(v)
        if tipo.startswith("C") or "ENTR" in tipo: return  abs(v)
        return v
    return 0.0

df = pd.DataFrame([{
    "data": pick_date(x),
    "descricao": g(x, "descricao") or g(x, "historico") or g(x, "observacao"),
    "valor": pick_amount(x),
} for x in rows])

if not df.empty:
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df = df.dropna(subset=["data"])
return df, maybe_new_refresh
====== 2) RECEBER/PAGAR pagos (fallback) ======

def _get_paginated_rp(fin_url: str, headers: dict, date_start: dt.date, date_end: dt.date,
paid_param_names: Tuple[str, str]) -> List[dict]:
p_ini, p_fim = paid_param_names
params = {
p_ini: date_start.strftime("%Y-%m-%d"),
p_fim: date_end.strftime("%Y-%m-%d"),
"situacao": "PAGO",
"limite": PAGE_LIMIT,
"pagina": 1,
}
out: List[dict] = []
while True:
r = requests.get(fin_url, headers=headers, params=params, timeout=30)
if r.status_code != 200:
raise RuntimeError(f"{fin_url} -> {r.status_code} - {r.text}")
j = r.json()
rows = j if isinstance(j, list) else j.get("data") or j.get("itens") or []
if not rows:
break
out.extend(rows)
if len(rows) < PAGE_LIMIT:
break
params["pagina"] += 1
return out

@st.cache_data(ttl=300, show_spinner=False)
def fetch_cashflow_fallback(refresh_token: str, date_start: dt.date, date_end: dt.date) -> Tuple[pd.DataFrame, Optional[str]]:
access, maybe_new_refresh = refresh_access_token(refresh_token)
headers = {"Authorization": f"Bearer {access}"}
tries = [
("dataPagamentoInicial", "dataPagamentoFinal"),
("dataBaixaInicial", "dataBaixaFinal"),
]
last_err = None
entradas_raw: List[dict] = []
for pair in tries:
    try:
        entradas_raw = _get_paginated_rp(RECEBER_URL, headers, date_start, date_end, pair)
        break
    except Exception as e:
        last_err = e
        entradas_raw = []
if entradas_raw == [] and last_err:
    raise RuntimeError(f"Contas a receber (entradas) não disponíveis: {last_err}")

last_err = None
saidas_raw: List[dict] = []
for pair in tries:
    try:
        saidas_raw = _get_paginated_rp(PAGAR_URL, headers, date_start, date_end, pair)
        break
    except Exception as e:
        last_err = e
        saidas_raw = []
if saidas_raw == [] and last_err:
    raise RuntimeError(f"Contas a pagar (saídas) não disponíveis: {last_err}")

def g(d, k, default=None): return d.get(k, default) if isinstance(d, dict) else default
def pick_payment_date(d):
    return g(d, "dataPagamento") or g(d, "dataBaixa") or g(d, "dataVencimento") or g(d, "data")
def pick_value(d):
    return g(d, "valorPago") or g(d, "valor")

entradas = pd.DataFrame([{
    "data": pick_payment_date(x),
    "descricao": g(x, "descricao") or g(x, "historico"),
    "valor": pd.to_numeric(pick_value(x), errors="coerce"),
} for x in entradas_raw])
saidas = pd.DataFrame([{
    "data": pick_payment_date(x),
    "descricao": g(x, "descricao") or g(x, "historico"),
    "valor": -pd.to_numeric(pick_value(x), errors="coerce"),
} for x in saidas_raw])

if not entradas.empty:
    entradas["data"] = pd.to_datetime(entradas["data"], errors="coerce")
if not saidas.empty:
    saidas["data"] = pd.to_datetime(saidas["data"], errors="coerce")

df = pd.concat([entradas, saidas], ignore_index=True)
df = df.dropna(subset=["data"])
return df, maybe_new_refresh
================== DASHBOARD ==================

with tab_dash:
st.title("📊 Dashboard de vendas – Bling (Tiburcio’s Stuff)")
================== DASHBOARD ==================

with tab_dash:
st.title("📊 Dashboard de vendas – Bling (Tiburcio’s Stuff)")
