# -*- coding: utf-8 -*-
"""
Bling Dashboard – 2 lojas
--------------------------------
- Client ID / Secret via Secrets
- Refresh token inicial no código
- Auto-refresh em memória
- Botões de validação para renovar tokens direto pelo app
"""

import datetime as dt
from dateutil.relativedelta import relativedelta
from typing import List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

# =====================================================================
# REFRESH TOKENS INICIAIS (cole aqui os que você já gerou!)
# =====================================================================
REFRESH_TS     = "COLE_AQUI_O_REFRESH_TOKEN_TS"
REFRESH_BAZAR  = "COLE_AQUI_O_REFRESH_TOKEN_BAZAR"

# =====================================================================
# ENDPOINTS BLING
# =====================================================================
TOKEN_URL  = "https://www.bling.com.br/Api/v3/oauth/token"
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"
DEFAULT_LIMIT = 100

st.set_page_config(page_title="Dashboard de vendas – Bling API v3", layout="wide")
st.title("📊 Dashboard de vendas – Bling API v3")

# =====================================================================
# STATE – refresh tokens são atualizados em memória
# =====================================================================
if "refresh_ts" not in st.session_state:
    st.session_state["refresh_ts"] = REFRESH_TS
if "refresh_bazar" not in st.session_state:
    st.session_state["refresh_bazar"] = REFRESH_BAZAR

# =====================================================================
# FUNÇÕES DE TOKEN E BUSCA
# =====================================================================
def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Tuple[str, Optional[str]]:
    """Gera um novo access_token a partir de um refresh_token.
       Retorna (access_token, refresh_token_novo_ou_None)."""
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

def exchange_code_for_tokens(client_id: str, client_secret: str, code: str, redirect_uri: str):
    """Troca um authorization code por tokens."""
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Falha na troca de code: {resp.status_code} – {resp.text}")
    return resp.json()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_orders(client_id: str, client_secret: str, refresh_token: str,
                 date_start: dt.date, date_end: dt.date,
                 loja_id: Optional[int] = None) -> Tuple[pd.DataFrame, Optional[str]]:
    # 1) Refresh
    access, maybe_new_refresh = refresh_access_token(client_id, client_secret, refresh_token)
    # 2) GET paginado
    headers = {"Authorization": f"Bearer {access}"}
    params = {
        "dataInicial": date_start.strftime("%Y-%m-%d"),
        "dataFinal":   date_end.strftime("%Y-%m-%d"),
        "limite":      DEFAULT_LIMIT,
        "pagina":      1,
    }
    if loja_id is not None:
        params["idLoja"] = loja_id

    all_rows = []
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

    recs = []
    for x in all_rows:
        recs.append({
            "id": x.get("id"),
            "data": x.get("data"),
            "numero": x.get("numero"),
            "numeroLoja": x.get("numeroLoja"),
            "total": x.get("total"),
            "loja_id": (x.get("loja") or {}).get("id"),
        })
    df = pd.DataFrame.from_records(recs)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
        df["total"] = pd.to_numeric(df["total"], errors="coerce")
    return df, maybe_new_refresh

# =====================================================================
# SIDEBAR – Filtros + Botões de autenticação
# =====================================================================
st.sidebar.header("Configurar contas (OAuth)")
colb1, colb2 = st.sidebar.columns(2)
if colb1.button("Autorizar TS"):
    st.info("Clique no link de autorização do Bling para TS e cole aqui o ?code=...")
    st.session_state["waiting_code"] = "TS"
if colb2.button("Autorizar Bazar"):
    st.info("Clique no link de autorização do Bling para Bazar e cole aqui o ?code=...")
    st.session_state["waiting_code"] = "BAZAR"

auth_code = st.sidebar.text_input("Cole aqui o código do Bling")
if auth_code and "waiting_code" in st.session_state:
    try:
        if st.session_state["waiting_code"] == "TS":
            j = exchange_code_for_tokens(
                st.secrets["TS_CLIENT_ID"], st.secrets["TS_CLIENT_SECRET"], auth_code,
                "https://dashboard-ts.streamlit.app"
            )
            st.session_state["refresh_ts"] = j.get("refresh_token")
            st.success("TS autorizado com sucesso!")
        elif st.session_state["waiting_code"] == "BAZAR":
            j = exchange_code_for_tokens(
                st.secrets["BAZAR_CLIENT_ID"], st.secrets["BAZAR_CLIENT_SECRET"], auth_code,
                "https://dashboard-ts.streamlit.app"
            )
            st.session_state["refresh_bazar"] = j.get("refresh_token")
            st.success("Bazar autorizado com sucesso!")
        del st.session_state["waiting_code"]
    except Exception as e:
        st.error(f"Erro ao autorizar: {e}")

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
if st.sidebar.button("Atualizar dados"):
    st.cache_data.clear()

# =====================================================================
# EXECUÇÃO
# =====================================================================
errors: List[str] = []
dfs: List[pd.DataFrame] = []

# TS
try:
    df_ts, new_r_ts = fetch_orders(
        st.secrets["TS_CLIENT_ID"], st.secrets["TS_CLIENT_SECRET"], st.session_state["refresh_ts"],
        date_start, date_end, loja_id_val
    )
    if new_r_ts:
        st.session_state["refresh_ts"] = new_r_ts
    df_ts["account"] = "Loja Tiburcio's Stuff"
    dfs.append(df_ts)
except Exception as e:
    errors.append(f"Loja Tiburcio's Stuff: {e}")

# Bazar
try:
    df_bz, new_r_bz = fetch_orders(
        st.secrets["BAZAR_CLIENT_ID"], st.secrets["BAZAR_CLIENT_SECRET"], st.session_state["refresh_bazar"],
        date_start, date_end, loja_id_val
    )
    if new_r_bz:
        st.session_state["refresh_bazar"] = new_r_bz
    df_bz["account"] = "TS Bazar"
    dfs.append(df_bz)
except Exception as e:
    errors.append(f"TS Bazar: {e}")

df_all = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

if errors:
    with st.expander("Avisos/Erros de integração", expanded=True):
        for e in errors: st.warning(e)

if df_all.empty:
    st.info("Nenhum pedido encontrado para os filtros informados.")
    st.stop()

# =====================================================================
# KPIs
# =====================================================================
colM1, colM2, colM3 = st.columns(3)
qtd     = int(df_all.shape[0])
receita = float(df_all["total"].sum())
ticket  = float(receita / qtd) if qtd else 0.0
colM1.metric("Pedidos", f"{qtd:,}".replace(",", "."))
colM2.metric("Receita", f"R$ {receita:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
colM3.metric("Ticket médio", f"R$ {ticket:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

# =====================================================================
# GRÁFICOS/TABELAS
# =====================================================================
st.subheader("Vendas por dia")
by_day = (df_all.assign(dia=df_all["data"].dt.date)
                 .groupby(["dia","account"], as_index=False)["total"].sum())
st.line_chart(by_day.pivot(index="dia", columns="account", values="total"))

colA, colB = st.columns([2, 1])
with colA:
    st.subheader("Receita por loja (ID)")
    by_loja = df_all.groupby("loja_id", as_index=False)["total"].sum().sort_values("total", ascending=False)
    if not by_loja.empty:
        st.bar_chart(by_loja.set_index("loja_id"))
with colB:
    st.subheader("Top 10 pedidos")
    top10 = df_all.sort_values("total", ascending=False).head(10)
    st.dataframe(top10[["account","id","numero","data","total"]])

st.subheader("Tabela de pedidos")
st.dataframe(df_all.sort_values("data", ascending=False))
