# bling_dashboard_streamlit.py
# -*- coding: utf-8 -*-
"""
Bling Dashboard – Cloud Ready (Streamlit)
----------------------------------------
• Lê credenciais das Secrets do Streamlit Cloud
• Área de configuração OAuth dentro do próprio app (gera refresh_token)
• Suporta múltiplas contas (TS e TS Bazar) via secrets
• KPIs + séries + tabela

Secrets esperadas (Settings → Secrets):
TS_CLIENT_ID, TS_CLIENT_SECRET, TS_REFRESH_TOKEN
BAZAR_CLIENT_ID, BAZAR_CLIENT_SECRET, BAZAR_REFRESH_TOKEN

Redirect URI no Bling: use exatamente a URL do app, ex.: https://dashboard-ts.streamlit.app
"""
from __future__ import annotations
import datetime as dt
from dateutil.relativedelta import relativedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

# =========================
# CONFIG BÁSICA
# =========================
APP_BASE = st.secrets.get("APP_BASE", "https://dashboard-ts.streamlit.app")  # troque nos Secrets, se quiser
REDIRECT_URI = APP_BASE  # precisa bater 100% com o cadastrado no Bling
AUTH_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"
DEFAULT_LIMIT = 100

st.set_page_config(page_title="Dashboard de vendas – Bling API v3 (MVP)", layout="wide")
st.title("📊 Dashboard de vendas – Bling API v3 (MVP)")

# =========================
# HELPERS OAUTH
# =========================

def make_auth_link(client_id: str, state: str) -> str:
    from urllib.parse import urlencode
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(q)


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> Dict:
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Falha na troca de code (HTTP {resp.status_code}) – {resp.text}")
    return resp.json()


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Tuple[str, Optional[str], int]:
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Falha no refresh token: {resp.status_code} – {resp.text}")
    j = resp.json()
    return j.get("access_token", ""), j.get("refresh_token"), int(j.get("expires_in", 21600))


# =========================
# BUSCA DE VENDAS
# =========================
@st.cache_data(ttl=300)
def fetch_orders(client_id: str, client_secret: str, refresh_token: str,
                 date_start: dt.date, date_end: dt.date,
                 loja_id: Optional[int] = None) -> pd.DataFrame:
    access, maybe_new_refresh, _ = refresh_access_token(client_id, client_secret, refresh_token)
    headers = {"Authorization": f"Bearer {access}"}
    params = {
        "dataInicial": date_start.strftime("%Y-%m-%d"),
        "dataFinal": date_end.strftime("%Y-%m-%d"),
        "limite": DEFAULT_LIMIT,
        "pagina": 1,
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
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < DEFAULT_LIMIT:
            break
        params["pagina"] += 1

    # Normalização essencial
    def safe(d, *keys, default=None):
        cur = d
        for k in keys:
            cur = None if cur is None else cur.get(k)
        return default if cur is None else cur

    recs = []
    for x in all_rows:
        recs.append({
            "id": x.get("id"),
            "data": x.get("data"),
            "numero": x.get("numero"),
            "numeroLoja": x.get("numeroLoja"),
            "total": x.get("total"),
            "contato_id": safe(x, "contato", "id"),
            "vendedor_id": safe(x, "vendedor", "id"),
            "loja_id": safe(x, "loja", "id"),
        })
    df = pd.DataFrame.from_records(recs)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
        df["total"] = pd.to_numeric(df["total"], errors="coerce")
    return df


# =========================
# UI – AUTORIZAÇÃO DENTRO DO APP
# =========================
with st.expander("⚙️ Avisos/Erros de integração", expanded=False):
    st.write("Se der 'invalid_grant: Invalid refresh token', gere um refresh_token novo clicando em Autorizar abaixo e cole nos Secrets.")

st.sidebar.header("Configurar contas (OAuth)")
colA, colB = st.sidebar.columns(2)

# Links de autorização
try:
    ts_link = make_auth_link(st.secrets["TS_CLIENT_ID"], "auth-ts")
    colA.link_button("Autorizar TS", ts_link)
except Exception:
    colA.write("Preencha TS_CLIENT_ID nos Secrets")

try:
    bazar_link = make_auth_link(st.secrets["BAZAR_CLIENT_ID"], "auth-bazar")
    colB.link_button("Autorizar Bazar", bazar_link)
except Exception:
    colB.write("Preencha BAZAR_CLIENT_ID nos Secrets")

# Captura de code pela URL do app
query_params = st.query_params
code = query_params.get("code", None)
if code:
    st.info("Recebi o código do Bling. Trocando por tokens…")
    tried = []
    for label in ("TS", "BAZAR"):
        try:
            cid = st.secrets[f"{label}_CLIENT_ID"]
            csec = st.secrets[f"{label}_CLIENT_SECRET"]
            j = exchange_code_for_tokens(cid, csec, code)
            st.success(f"✅ {('Loja Tiburcio\'s Stuff' if label=='TS' else 'TS Bazar')} autorizada!")
            st.write("Cole este **refresh_token** nos Secrets e clique Rerun:")
            st.code(j.get("refresh_token", "(sem refresh_token)"), language="text")
            st.write("(Opcional) access_token para teste imediato:")
            st.code(j.get("access_token", ""), language="text")
            st.stop()
        except Exception as e:
            tried.append(str(e))
    st.error("Não consegui trocar o code por tokens. Verifique se o redirect_uri no Bling é exatamente o URL do app.")
    st.caption("Detalhes: " + " | ".join(tried))

# =========================
# FORM DE FILTROS
# =========================
st.sidebar.header("Filtros")
DEFAULT_START = (dt.date.today() - relativedelta(months=1)).replace(day=1)
DEFAULT_END = dt.date.today()
col1, col2 = st.sidebar.columns(2)
with col1:
    date_start = st.date_input("Data inicial", value=DEFAULT_START)
with col2:
    date_end = st.date_input("Data final", value=DEFAULT_END)
loja_id_str = st.sidebar.text_input("ID da Loja (opcional)")
loja_id_val = int(loja_id_str) if loja_id_str.strip().isdigit() else None

if st.sidebar.button("Atualizar dados"):
    st.cache_data.clear()

# =========================
# MONTAGEM DAS CONTAS A PARTIR DOS SECRETS
# =========================
accounts: List[Dict] = []
if all(k in st.secrets for k in ("TS_CLIENT_ID","TS_CLIENT_SECRET","TS_REFRESH_TOKEN")):
    accounts.append({
        "name": "Loja Tiburcio's Stuff",
        "client_id": st.secrets["TS_CLIENT_ID"],
        "client_secret": st.secrets["TS_CLIENT_SECRET"],
        "refresh_token": st.secrets["TS_REFRESH_TOKEN"],
    })
if all(k in st.secrets for k in ("BAZAR_CLIENT_ID","BAZAR_CLIENT_SECRET","BAZAR_REFRESH_TOKEN")):
    accounts.append({
        "name": "TS Bazar",
        "client_id": st.secrets["BAZAR_CLIENT_ID"],
        "client_secret": st.secrets["BAZAR_CLIENT_SECRET"],
        "refresh_token": st.secrets["BAZAR_REFRESH_TOKEN"],
    })

if not accounts:
    st.warning("Configure os Secrets das contas (CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN) e recarregue o app.")
    st.stop()

# =========================
# CARREGAR DADOS
# =========================
errors = []
dfs = []
for acc in accounts:
    try:
        df = fetch_orders(acc["client_id"], acc["client_secret"], acc["refresh_token"],
                          date_start, date_end, loja_id_val)
        df["account"] = acc["name"]
        dfs.append(df)
    except Exception as e:
        errors.append(f"{acc['name']}: {e}")

df_all = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

if errors:
    with st.expander("Avisos/Erros de integração", expanded=True):
        for e in errors:
            st.warning(e)

if df_all.empty:
    st.info("Nenhum pedido encontrado para os filtros informados.")
    st.stop()

# =========================
# KPIs E VISUALIZAÇÕES
# =========================
metrics_cols = st.columns(3)
qtd = int(df_all.shape[0])
receita = float(df_all["total"].sum()) if not df_all.empty else 0.0
ticket = float(receita / qtd) if qtd else 0.0
metrics_cols[0].metric("Pedidos", f"{qtd:,}".replace(",","."))
metrics_cols[1].metric("Receita", f"R$ {receita:,.2f}".replace(",","#").replace(".",",").replace("#","."))
metrics_cols[2].metric("Ticket médio", f"R$ {ticket:,.2f}".replace(",","#").replace(".",",").replace("#","."))

st.subheader("Vendas por dia")
by_day = (
    df_all.assign(dia=df_all["data"].dt.date)
         .groupby(["dia","account"], as_index=False)["total"].sum()
)
st.line_chart(by_day.pivot(index="dia", columns="account", values="total"))

colA, colB = st.columns([2, 1])
with colA:
    st.subheader("Receita por loja (ID)")
    by_loja = df_all.groupby(["loja_id"], as_index=False)["total"].sum().sort_values("total", ascending=False)
    if not by_loja.empty:
        st.bar_chart(by_loja.set_index("loja_id"))
with colB:
    st.subheader("Top 10 pedidos")
    top10 = df_all.sort_values("total", ascending=False).head(10)
    st.dataframe(top10[["account","id","numero","data","total"]])

st.subheader("Tabela de pedidos")
st.dataframe(df_all.sort_values("data", ascending=False))
