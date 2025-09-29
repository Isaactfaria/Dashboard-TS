# -*- coding: utf-8 -*-
"""
Bling Dashboard – Tiburcio's Stuff (single store)
-------------------------------------------------
Captura robusta do ?code=:
- tenta st.query_params
- tenta experimental_get_query_params()
- fallback: lê window.location.search e captura automaticamente
"""

from __future__ import annotations
import datetime as dt
from dateutil.relativedelta import relativedelta
from typing import Optional, Tuple, List
from urllib.parse import urlencode, parse_qs

import pandas as pd
import requests
import streamlit as st

# ============ CONFIG ============
APP_BASE = st.secrets.get("APP_BASE", "https://dashboard-ts.streamlit.app")
REDIRECT_URI = APP_BASE
TOKEN_URL  = "https://www.bling.com.br/Api/v3/oauth/token"
AUTH_URL   = "https://www.bling.com.br/Api/v3/oauth/authorize"
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"
DEFAULT_LIMIT = 100

st.set_page_config(page_title="Dashboard de vendas – Bling (Tiburcio’s Stuff)", layout="wide")

# ============ STATE ============
st.session_state.setdefault("ts_refresh", st.secrets.get("TS_REFRESH_TOKEN"))

# ============ OAUTH HELPERS ============
def build_auth_link(client_id: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    })

def exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Falha na troca de code: {resp.status_code} – {resp.text}")
    return resp.json()

def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Tuple[str, Optional[str]]:
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Falha no refresh token: {resp.status_code} – {resp.text}")
    j = resp.json()
    return j.get("access_token", ""), j.get("refresh_token")

# ============ CAPTURA DO CODE (logo no início) ============
def _normalize_qp_dict(qd) -> dict:
    out = {}
    for k, v in qd.items():
        out[k] = v[0] if isinstance(v, list) else v
    return out

def try_capture_code() -> Optional[tuple[str, str]]:
    # 1) st.query_params
    try:
        qp = dict(st.query_params.items())
        qp = _normalize_qp_dict(qp)
        code = qp.get("code"); state = qp.get("state")
        if code and state: return code, state
    except Exception:
        pass
    # 2) experimental_get_query_params
    try:
        qp = st.experimental_get_query_params()
        qp = _normalize_qp_dict(qp)
        code = qp.get("code"); state = qp.get("state")
        if code and state: return code, state
    except Exception:
        pass
    return None

captured = try_capture_code()

# Fallback JS: lê window.location.search e envia de volta
if not captured:
    st.html("""
    <script>
      (function(){
        const params = new URLSearchParams(window.location.search);
        const code = params.get('code'); const state = params.get('state');
        if (code && state) {
          const pyInput = window.parent.document.querySelector('input[data-testid="stTextInput"][aria-label="__oauth_code_state"]');
          if (pyInput) { pyInput.value = code+"|"+state; pyInput.dispatchEvent(new Event('input', {bubbles: true})); }
        }
      })();
    </script>
    """, height=0)
    hidden = st.text_input("__oauth_code_state", key="__oauth_code_state", label_visibility="collapsed")
    if hidden and "|" in hidden:
        code, state = hidden.split("|", 1)
        captured = (code, state)

# Se capturou, trocar por tokens imediatamente
if captured:
    code, state = captured
    if state == "auth-ts":
        try:
            j = exchange_code_for_tokens(st.secrets["TS_CLIENT_ID"], st.secrets["TS_CLIENT_SECRET"], code)
            new_ref = j.get("refresh_token")
            if new_ref:
                st.session_state["ts_refresh"] = new_ref
                st.success("TS autorizado e refresh_token atualizado!")
            else:
                st.error("Não veio refresh_token na resposta do Bling.")
        except Exception as e:
            st.error(f"Não foi possível autorizar TS: {e}")
        finally:
            try:
                st.query_params.clear()
            except Exception:
                st.query_params = {}
            st.rerun()

# ============ UI PRINCIPAL ============
st.title("📊 Dashboard de vendas – Bling (Tiburcio’s Stuff)")

# Sidebar – OAuth + Filtros
st.sidebar.header("Configurar conta (OAuth)")
st.sidebar.caption(f"Redirect em uso: {REDIRECT_URI}")

try:
    ts_url = build_auth_link(st.secrets["TS_CLIENT_ID"], "auth-ts")
    st.sidebar.link_button("Autorizar TS", ts_url)
except Exception:
    st.sidebar.error("Preencha TS_CLIENT_ID e TS_CLIENT_SECRET nos Secrets.")

with st.sidebar.expander("Ver URL de autorização (debug)"):
    st.code(ts_url if "ts_url" in locals() else "—", language="text")

# Filtros
st.sidebar.header("Filtros")
DEFAULT_START = (dt.date.today() - relativedelta(months=1)).replace(day=1)
DEFAULT_END   = dt.date.today()
c1, c2 = st.sidebar.columns(2)
with c1:
    date_start = st.date_input("Data inicial", value=DEFAULT_START)
with c2:
    date_end   = st.date_input("Data final",   value=DEFAULT_END)
loja_id_str = st.sidebar.text_input("ID da Loja (opcional)")
loja_id_val = int(loja_id_str) if loja_id_str.strip().isdigit() else None
if st.sidebar.button("Atualizar dados"): st.cache_data.clear()

# ============ DADOS ============
@st.cache_data(ttl=300, show_spinner=False)
def fetch_orders(client_id: str, client_secret: str, refresh_token: str,
                 date_start: dt.date, date_end: dt.date,
                 loja_id: Optional[int] = None) -> Tuple[pd.DataFrame, Optional[str]]:
    access, maybe_new_refresh = refresh_access_token(client_id, client_secret, refresh_token)
    headers = {"Authorization": f"Bearer {access}"}
    params = {
        "dataInicial": date_start.strftime("%Y-%m-%d"),
        "dataFinal":   date_end.strftime("%Y-%m-%d"),
        "limite":      DEFAULT_LIMIT,
        "pagina":      1,
    }
    if loja_id is not None:
        params["idLoja"] = loja_id

    all_rows: List[dict] = []
    while True:
        r = requests.get(ORDERS_URL, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Erro ao listar pedidos p{params['pagina']}: {r.status_code} – {r.text}")
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data") or data.get("itens") or []
        if not rows: break
        all_rows.extend(rows)
        if len(rows) < DEFAULT_LIMIT: break
        params["pagina"] += 1

    def safe(d, *keys, default=None):
        cur = d
        for k in keys: cur = None if cur is None else cur.get(k)
        return default if cur is None else cur

    recs = []
    for x in all_rows:
        recs.append({
            "id": x.get("id"),
            "data": x.get("data"),
            "numero": x.get("numero"),
            "numeroLoja": x.get("numeroLoja"),
            "total": x.get("total"),
            "loja_id": safe(x, "loja", "id"),
        })
    df = pd.DataFrame.from_records(recs)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
        df["total"] = pd.to_numeric(df["total"], errors="coerce")
    return df, maybe_new_refresh

# Sem refresh não chama API
if not st.session_state["ts_refresh"]:
    with st.expander("Avisos/Erros de integração", expanded=True):
        st.info("Autorize a conta **TS** para carregar as vendas (clique em **Autorizar TS**).")
    st.stop()

errors: List[str] = []
try:
    df, new_r = fetch_orders(
        st.secrets["TS_CLIENT_ID"], st.secrets["TS_CLIENT_SECRET"], st.session_state["ts_refresh"],
        date_start, date_end, loja_id_val
    )
    if new_r:
        st.session_state["ts_refresh"] = new_r
except Exception as e:
    errors.append(f"Tiburcio's Stuff: {e}")
    df = pd.DataFrame()

if errors:
    with st.expander("Avisos/Erros de integração", expanded=True):
        for e in errors: st.warning(e)

if df.empty:
    st.info("Nenhum pedido encontrado para os filtros informados.")
    st.stop()

# KPIs
col1, col2, col3 = st.columns(3)
qtd     = int(df.shape[0])
receita = float(df["total"].sum())
ticket  = float(receita / qtd) if qtd else 0.0
col1.metric("Pedidos", f"{qtd:,}".replace(",", "."))
col2.metric("Receita", f"R$ {receita:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
col3.metric("Ticket médio", f"R$ {ticket:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

# Gráficos
st.subheader("Vendas por dia")
by_day = df.assign(dia=df["data"].dt.date).groupby("dia", as_index=False)["total"].sum()
st.line_chart(by_day.set_index("dia"))

st.subheader("Receita por loja (ID)")
by_loja = df.groupby("loja_id", as_index=False)["total"].sum().sort_values("total", ascending=False)
if not by_loja.empty:
    st.bar_chart(by_loja.set_index("loja_id"))

st.subheader("Tabela de pedidos")
st.dataframe(df.sort_values("data", ascending=False))
