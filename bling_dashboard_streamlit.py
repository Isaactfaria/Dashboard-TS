# bling_dashboard_streamlit.py
# -*- coding: utf-8 -*-

import time
import datetime as dt
from dateutil.relativedelta import relativedelta
from urllib.parse import urlencode, urlparse, parse_qs
from typing import Optional, Tuple, List, Dict, Any

import pandas as pd
import requests
import streamlit as st
import altair as alt

# ======================================================
# CONFIG BÁSICA
# ======================================================
st.set_page_config(
    page_title="Dashboard de Vendas – Bling (Tiburcio’s Stuff)",
    layout="wide",
)

APP_BASE         = st.secrets.get("APP_BASE", "https://SEU-APP.streamlit.app")
TS_CLIENT_ID     = st.secrets["TS_CLIENT_ID"]
TS_CLIENT_SECRET = st.secrets["TS_CLIENT_SECRET"]
DEFAULT_REFRESH  = st.secrets.get("TS_REFRESH_TOKEN")

AUTH_URL   = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL  = "https://www.bling.com.br/Api/v3/oauth/token"
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"
RECEBER_URL = "https://www.bling.com.br/Api/v3/contas/receber"
PAGAR_URL   = "https://www.bling.com.br/Api/v3/contas/pagar"
PAGE_LIMIT = 100

# ======================================================
# STATE
# ======================================================
st.session_state.setdefault("ts_refresh", DEFAULT_REFRESH)
st.session_state.setdefault("ts_access", None)
st.session_state.setdefault("_last_code_used", None)

# ======================================================
# HELPERS
# ======================================================
def fmt_brl(x: float) -> str:
    return f"R$ {x:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")

def build_auth_link(client_id: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": APP_BASE,
        "state": state,
    })

def post_with_backoff(url: str, auth: tuple, data: Dict[str, Any], tries: int = 3, wait: int = 3) -> requests.Response:
    for i in range(tries):
        r = requests.post(url, auth=auth, data=data, timeout=30)
        if r.status_code == 429 and i < tries - 1:
            time.sleep(wait * (i + 1))
            continue
        return r
    return r

# ======================================================
# OAUTH
# ======================================================
def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    r = post_with_backoff(
        TOKEN_URL,
        auth=(TS_CLIENT_ID, TS_CLIENT_SECRET),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": APP_BASE},
    )
    if r.status_code != 200:
        raise RuntimeError(f"Falha na troca do code: {r.status_code} – {r.text}")
    return r.json()

def refresh_access_token(refresh_token: str) -> Tuple[str, Optional[str]]:
    r = post_with_backoff(
        TOKEN_URL,
        auth=(TS_CLIENT_ID, TS_CLIENT_SECRET),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    if r.status_code != 200:
        raise RuntimeError(f"Falha ao renovar token: {r.status_code} – {r.text}")
    j = r.json()
    return j.get("access_token", ""), j.get("refresh_token")

def auto_capture_code() -> Optional[Tuple[str, str]]:
    try:
        qp = dict(st.query_params.items())
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

# ======================================================
# SIDEBAR – FILTROS
# ======================================================
st.sidebar.header("Filtros")
DEFAULT_START = (dt.date.today() - relativedelta(months=1)).replace(day=1)
DEFAULT_END   = dt.date.today()
c1, c2 = st.sidebar.columns(2)
with c1:
    date_start = st.date_input("Data inicial", value=DEFAULT_START)
with c2:
    date_end   = st.date_input("Data final", value=DEFAULT_END)
if st.sidebar.button("Atualizar dados"):
    st.cache_data.clear()

# ======================================================
# FETCH – VENDAS
# ======================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_orders(refresh_token: str, date_start: dt.date, date_end: dt.date) -> pd.DataFrame:
    access, maybe_new_refresh = refresh_access_token(refresh_token)
    if maybe_new_refresh:
        st.session_state["ts_refresh"] = maybe_new_refresh
    headers = {"Authorization": f"Bearer {access}"}
    params = {
        "dataInicial": date_start.strftime("%Y-%m-%d"),
        "dataFinal":   date_end.strftime("%Y-%m-%d"),
        "limite":      PAGE_LIMIT,
        "pagina":      1,
    }
    all_rows: List[Dict[str, Any]] = []
    while True:
        r = requests.get(ORDERS_URL, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Erro ao listar pedidos: {r.status_code} – {r.text}")
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data") or []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_LIMIT:
            break
        params["pagina"] += 1
    df = pd.DataFrame(all_rows)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
        df["total"] = pd.to_numeric(df["total"], errors="coerce")
    return df

# ======================================================
# DASHBOARD TABS
# ======================================================
tab_dash, tab_oauth = st.tabs(["📊 Dashboard", "🔐 Integração (OAuth)"])

# ---------------- DASHBOARD ----------------
with tab_dash:
    st.title("📊 Dashboard de Vendas – Tiburcio’s Stuff")
    if not st.session_state["ts_refresh"]:
        st.warning("Autorize primeiro na aba Integração (OAuth).")
        st.stop()

    try:
        df_vendas = fetch_orders(st.session_state["ts_refresh"], date_start, date_end)
        if df_vendas.empty:
            st.info("Nenhum pedido encontrado para o período.")
        else:
            pedidos = len(df_vendas)
            receita = df_vendas["total"].sum()
            ticket  = receita / pedidos if pedidos else 0

            c1, c2, c3 = st.columns(3)
            c1.metric("Pedidos", pedidos)
            c2.metric("Receita", fmt_brl(receita))
            c3.metric("Ticket médio", fmt_brl(ticket))

            by_day = df_vendas.groupby(df_vendas["data"].dt.date)["total"].sum().reset_index()
            st.line_chart(by_day.set_index("data"))
            st.dataframe(df_vendas)
    except Exception as e:
        st.error(f"Erro ao carregar vendas: {e}")

# ---------------- OAUTH ----------------
with tab_oauth:
    st.header("Integração com o Bling (OAuth)")
    st.caption(f"Redirect configurado: `{APP_BASE}`")

    auth_link = build_auth_link(TS_CLIENT_ID, "auth-ts")
    st.markdown(
        f'<a href="{auth_link}" target="_blank" rel="noopener" class="stButton"><button>Autorizar TS</button></a>',
        unsafe_allow_html=True,
    )
    with st.expander("Ver URL de autorização (debug)"):
        st.code(auth_link, language="text")

    st.subheader("Finalizar autorização manual")
    manual = st.text_input("Cole a URL de retorno do Bling ou apenas o code")
    if st.button("Trocar agora"):
        raw = manual.strip()
        if raw:
            if raw.startswith("http"):
                qs = parse_qs(urlparse(raw).query)
                code_value  = (qs.get("code") or [None])[0]
            else:
                code_value = raw
            if code_value:
                try:
                    tokens = exchange_code_for_tokens(code_value)
                    st.session_state["ts_refresh"] = tokens.get("refresh_token")
                    st.session_state["ts_access"]  = tokens.get("access_token")
                    st.success("TS autorizado e refresh_token atualizado!")
                except Exception as e:
                    st.error(f"Falha na troca manual: {e}")

    # 🔑 Novo bloco – Mostrar/copiar refresh token
    st.subheader("Refresh token atual")
    if st.session_state.get("ts_refresh"):
        st.code(st.session_state["ts_refresh"], language="text")
        st.info("Copie o valor acima e cole nos *Secrets* do Streamlit como `TS_REFRESH_TOKEN`.")
    else:
        st.warning("Nenhum refresh token disponível ainda. Autorize primeiro.")
