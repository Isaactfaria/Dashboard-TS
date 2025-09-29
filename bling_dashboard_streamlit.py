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

# ============== CONFIG ==============
APP_BASE       = st.secrets.get("APP_BASE", "https://dashboard-ts.streamlit.app")
TS_CLIENT_ID   = st.secrets["TS_CLIENT_ID"]
TS_CLIENT_SECRET = st.secrets["TS_CLIENT_SECRET"]

AUTH_URL   = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL  = "https://www.bling.com.br/Api/v3/oauth/token"
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"
PAGE_LIMIT  = 100

st.set_page_config(page_title="Dashboard de vendas – Bling (Tiburcio’s Stuff)", layout="wide")

# ============== STATE ==============
st.session_state.setdefault("ts_refresh", st.secrets.get("TS_REFRESH_TOKEN"))
st.session_state.setdefault("ts_access", None)
st.session_state.setdefault("_last_code_used", None)

# ============== OAUTH HELPERS ==============
def build_auth_link(client_id: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": APP_BASE,
        "state": state,
    })

def post_with_backoff(url, auth, data, tries=3, wait=3):
    """POST com pequeno backoff (protege contra flutuações e 429)."""
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
        raise RuntimeError(f"Falha na troca do code: {r.status_code} – {r.text}")
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
        raise RuntimeError(f"Falha ao renovar token: {r.status_code} – {r.text}")
    j = r.json()
    return j.get("access_token", ""), j.get("refresh_token")

# ============== CAPTURA AUTOMÁTICA DO ?code= ==============
def normalize_qp(d: dict) -> dict:
    return {k: (v[0] if isinstance(v, list) else v) for k, v in d.items()}

def auto_capture_code() -> Optional[tuple[str, str]]:
    # 1) API nova
    try:
        qp = normalize_qp(dict(st.query_params.items()))
        if qp.get("code") and qp.get("state"):
            return qp["code"], qp["state"]
    except Exception:
        pass
    # 2) API antiga (alguns workspaces ainda suportam)
    try:
        qp = normalize_qp(st.experimental_get_query_params())
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

# ============== UI: AUTORIZAÇÃO ==============
st.title("📊 Dashboard de vendas – Bling (Tiburcio’s Stuff)")

st.sidebar.header("Configurar conta (OAuth)")
st.sidebar.caption(f"Redirect em uso: {APP_BASE}")

# Em alguns navegadores, st.link_button pode ser bloqueado.
# Usamos um link HTML explícito que abre em nova aba.
auth_link = build_auth_link(TS_CLIENT_ID, "auth-ts")
st.sidebar.markdown(
    f'<a href="{auth_link}" target="_blank" rel="noopener" class="stButton">'
    f'<button>Autorizar TS</button></a>',
    unsafe_allow_html=True,
)

with st.sidebar.expander("Ver URL de autorização (debug)"):
    st.code(auth_link, language="text")

# Campo MANUAL sempre visível
st.subheader("⚙️ Finalizar autorização (se necessário)")
st.write(
    "Se voltou do Bling com `?code=...&state=auth-ts` e o painel não atualizou, "
    "cole abaixo **a URL completa** da barra do navegador **ou só o `code`** e clique **Trocar agora**."
)
manual = st.text_input(
    "Cole a URL de retorno do Bling ou apenas o code",
    placeholder="https://dashboard-ts.streamlit.app/?code=...&state=auth-ts  ou  e57518... ",
)
colA, colB = st.columns([1, 3])
with colA:
    if st.button("Trocar agora"):
        code_value = None
        raw = manual.strip()
        if not raw:
            st.error("Cole a URL ou o code.")
        else:
            if raw.startswith("http"):
                try:
                    qs = parse_qs(urlparse(raw).query)
                    code_value = (qs.get("code") or [None])[0]
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

with colB:
    with st.expander("Debug rápido da URL atual"):
        try:
            st.write("st.query_params →", dict(st.query_params.items()))
        except Exception:
            st.write("st.query_params indisponível")
        try:
            st.write("experimental_get_query_params →", st.experimental_get_query_params())
        except Exception:
            st.write("experimental_get_query_params indisponível")

# ============== FILTROS ==============
st.sidebar.header("Filtros")
DEFAULT_START = (dt.date.today() - relativedelta(months=1)).replace(day=1)
DEFAULT_END   = dt.date.today()
c1, c2 = st.sidebar.columns(2)
with c1:
    date_start = st.date_input("Data inicial", value=DEFAULT_START)
with c2:
    date_end   = st.date_input("Data final",   value=DEFAULT_END)
if st.sidebar.button("Atualizar dados"):
    st.cache_data.clear()

# ============== BUSCA DE VENDAS ==============
@st.cache_data(ttl=300, show_spinner=False)
def fetch_orders(refresh_token: str, date_start: dt.date, date_end: dt.date) -> Tuple[pd.DataFrame, Optional[str]]:
    access, maybe_new_refresh = refresh_access_token(refresh_token)
    headers = {"Authorization": f"Bearer {access}"}
    params = {
        # estes nomes funcionaram melhor em testes; ajuste se seu app usa variantes
        "dataInicial": date_start.strftime("%Y-%m-%d"),
        "dataFinal":   date_end.strftime("%Y-%m-%d"),
        "limite":      PAGE_LIMIT,
        "pagina":      1,
    }

    all_rows: List[dict] = []
    while True:
        r = requests.get(ORDERS_URL, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Erro ao listar pedidos p{params['pagina']}: {r.status_code} – {r.text}")
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data") or data.get("itens") or []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_LIMIT:
            break
        params["pagina"] += 1

    # normalização simples
    def g(d, key, default=None):  # get safe
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

# sem refresh -> pedir autorização
if not st.session_state["ts_refresh"]:
    with st.expander("Avisos/Erros de integração", expanded=True):
        st.info("Autorize a conta **TS** (clique em **Autorizar TS** ou use o campo acima para colar a URL/code).")
    st.stop()

errors: List[str] = []
try:
    df, new_r = fetch_orders(st.session_state["ts_refresh"], date_start, date_end)
    if new_r:
        st.session_state["ts_refresh"] = new_r
except Exception as e:
    errors.append(f"Tiburcio's Stuff: {e}")
    df = pd.DataFrame()

if errors:
    with st.expander("Avisos/Erros de integração", expanded=True):
        for e in errors:
            st.warning(e)

if df.empty:
    st.info("Nenhum pedido encontrado para os filtros informados.")
    st.stop()

# ============== KPIs E VISUALIZAÇÕES ==============
col1, col2, col3 = st.columns(3)
qtd     = int(df.shape[0])
receita = float(df["total"].sum())
ticket  = float(receita / qtd) if qtd else 0.0
col1.metric("Pedidos", f"{qtd:,}".replace(",", "."))
col2.metric("Receita", f"R$ {receita:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
col3.metric("Ticket médio", f"R$ {ticket:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

st.subheader("Vendas por dia")
by_day = df.assign(dia=df["data"].dt.date).groupby("dia", as_index=False)["total"].sum()
st.line_chart(by_day.set_index("dia"))

st.subheader("Receita por loja (ID)")
by_loja = df.groupby("loja_id", as_index=False)["total"].sum().sort_values("total", ascending=False)
if not by_loja.empty:
    st.bar_chart(by_loja.set_index("loja_id"))

st.subheader("Tabela de pedidos")
st.dataframe(df.sort_values("data", ascending=False))
