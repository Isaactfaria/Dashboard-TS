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
APP_BASE         = st.secrets.get("APP_BASE", "https://dashboard-ts.streamlit.app")
TS_CLIENT_ID     = st.secrets["TS_CLIENT_ID"]
TS_CLIENT_SECRET = st.secrets["TS_CLIENT_SECRET"]

AUTH_URL   = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL  = "https://www.bling.com.br/Api/v3/oauth/token"
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"

# Finanças (pode variar por conta Plano/escopo; o código trata erros)
RECEBER_URL = "https://www.bling.com.br/Api/v3/contas/receber"
PAGAR_URL   = "https://www.bling.com.br/Api/v3/contas/pagar"

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

# ============== CAPTURA AUTOMÁTICA DO ?code= (antes de desenhar as abas) ==============
def normalize_qp(d: dict) -> dict:
    return {k: (v[0] if isinstance(v, list) else v) for k, v in d.items()}

def auto_capture_code() -> Optional[tuple[str, str]]:
    try:
        qp = normalize_qp(dict(st.query_params.items()))
        if qp.get("code") and qp.get("state"):
            return qp["code"], qp["state"]
    except Exception:
        pass
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

# ============== LAYOUT EM ABAS ==============
tab_dash, tab_oauth = st.tabs(["📊 Dashboard", "🔐 Integração (OAuth)"])

# ---------------- OAuth TAB ----------------
with tab_oauth:
    st.header("Integração com o Bling (OAuth)")
    st.caption(f"Redirect configurado: `{APP_BASE}`")

    auth_link = build_auth_link(TS_CLIENT_ID, "auth-ts")
    st.markdown(
        f'<a href="{auth_link}" target="_blank" rel="noopener" class="stButton">'
        f'<button>Autorizar TS</button></a>',
        unsafe_allow_html=True,
    )

    with st.expander("Ver URL de autorização (debug)"):
        st.code(auth_link, language="text")

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

# ---------------- Sidebar filtros ----------------
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

# ============== BUSCAS ==============
@st.cache_data(ttl=300, show_spinner=False)
def fetch_orders(refresh_token: str, date_start: dt.date, date_end: dt.date) -> Tuple[pd.DataFrame, Optional[str]]:
    access, maybe_new_refresh = refresh_access_token(refresh_token)
    headers = {"Authorization": f"Bearer {access}"}
    params = {
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

def _get_paginated(fin_url: str, headers: dict, date_start: dt.date, date_end: dt.date,
                   paid_param_names: Tuple[str, str]) -> List[dict]:
    """
    Busca paginado em contas a pagar/receber usando nome de parâmetros de data de pagamento.
    paid_param_names: ("dataPagamentoInicial", "dataPagamentoFinal") ou ("dataBaixaInicial", "dataBaixaFinal")
    """
    p_ini, p_fim = paid_param_names
    params = {
        p_ini: date_start.strftime("%Y-%m-%d"),
        p_fim: date_end.strftime("%Y-%m-%d"),
        "situacao": "PAGO",   # para receber/pagar pagos; em algumas contas pode ser "PAGA" / "RECEBIDO"
        "limite": PAGE_LIMIT,
        "pagina": 1,
    }
    out: List[dict] = []
    while True:
        r = requests.get(fin_url, headers=headers, params=params, timeout=30)
        # retorna lista diretamente ou {"data":[...]}
        if r.status_code != 200:
            raise RuntimeError(f"{fin_url} → {r.status_code} – {r.text}")
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
def fetch_cashflow(refresh_token: str, date_start: dt.date, date_end: dt.date) -> Tuple[pd.DataFrame, pd.DataFrame, Optional[str]]:
    access, maybe_new_refresh = refresh_access_token(refresh_token)
    headers = {"Authorization": f"Bearer {access}"}

    # Tentativas com nomes de parâmetros diferentes (varia em instalações do Bling)
    tries = [
        ("dataPagamentoInicial", "dataPagamentoFinal"),
        ("dataBaixaInicial", "dataBaixaFinal"),
    ]

    # RECEBER (entradas)
    last_err = None
    entradas_raw: List[dict] = []
    for pair in tries:
        try:
            entradas_raw = _get_paginated(RECEBER_URL, headers, date_start, date_end, pair)
            break
        except Exception as e:
            last_err = e
            entradas_raw = []
    if entradas_raw == [] and last_err:
        # Passa erro pra frente (para o usuário ver no painel)
        raise RuntimeError(f"Contas a receber (entradas) não disponíveis: {last_err}")

    # PAGAR (saídas)
    last_err = None
    saidas_raw: List[dict] = []
    for pair in tries:
        try:
            saidas_raw = _get_paginated(PAGAR_URL, headers, date_start, date_end, pair)
            break
        except Exception as e:
            last_err = e
            saidas_raw = []
    if saidas_raw == [] and last_err:
        raise RuntimeError(f"Contas a pagar (saídas) não disponíveis: {last_err}")

    # Normalização básica
    def g(d, k, default=None): return d.get(k, default) if isinstance(d, dict) else default
    def pick_payment_date(d):
        return g(d, "dataPagamento") or g(d, "dataBaixa") or g(d, "dataVencimento") or g(d, "data")
    def pick_value(d):
        # tenta pegar valorPago se existir; senão, valor
        return g(d, "valorPago") or g(d, "valor")

    entradas = pd.DataFrame([{
        "data_pagamento": pick_payment_date(x),
        "descricao": g(x, "descricao") or g(x, "historico"),
        "valor": pd.to_numeric(pick_value(x), errors="coerce"),
    } for x in entradas_raw])
    if not entradas.empty:
        entradas["data_pagamento"] = pd.to_datetime(entradas["data_pagamento"], errors="coerce")

    saidas = pd.DataFrame([{
        "data_pagamento": pick_payment_date(x),
        "descricao": g(x, "descricao") or g(x, "historico"),
        "valor": -pd.to_numeric(pick_value(x), errors="coerce"),  # negativo para saídas
    } for x in saidas_raw])
    if not saidas.empty:
        saidas["data_pagamento"] = pd.to_datetime(saidas["data_pagamento"], errors="coerce")

    return entradas, saidas, maybe_new_refresh

# ============== DASHBOARD TAB ==============
with tab_dash:
    st.title("📊 Dashboard de vendas – Bling (Tiburcio’s Stuff)")

    # Sem refresh? Direciona à aba OAuth
    if not st.session_state["ts_refresh"]:
        with st.expander("Avisos/Erros de integração", expanded=True):
            st.info("Autorize a conta **TS** na aba **‘🔐 Integração (OAuth)’** para carregar as vendas.")
        st.stop()

    # Vendas (para KPIs rápidos de vendas)
    errors: List[str] = []
    try:
        df_vendas, new_r = fetch_orders(st.session_state["ts_refresh"], date_start, date_end)
        if new_r:
            st.session_state["ts_refresh"] = new_r
    except Exception as e:
        errors.append(f"Vendas: {e}")
        df_vendas = pd.DataFrame()

    # Fluxo de caixa (entradas/saídas)
    try:
        entradas, saidas, new_r2 = fetch_cashflow(st.session_state["ts_refresh"], date_start, date_end)
        if new_r2:
            st.session_state["ts_refresh"] = new_r2
    except Exception as e:
        errors.append(str(e))
        entradas = pd.DataFrame(columns=["data_pagamento","descricao","valor"])
        saidas   = pd.DataFrame(columns=["data_pagamento","descricao","valor"])

    if errors:
        with st.expander("Avisos/Erros de integração", expanded=True):
            for e in errors:
                st.warning(e)

    # KPIs de vendas (se houver)
    if not df_vendas.empty:
        col1, col2, col3 = st.columns(3)
        qtd     = int(df_vendas.shape[0])
        receita = float(pd.to_numeric(df_vendas["total"], errors="coerce").sum())
        ticket  = float(receita / qtd) if qtd else 0.0
        col1.metric("Pedidos", f"{qtd:,}".replace(",", "."))
        col2.metric("Receita (pedidos)", f"R$ {receita:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
        col3.metric("Ticket médio", f"R$ {ticket:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

    # ---------- Fluxo de caixa ----------
    st.subheader("Fluxo de caixa do período")

    total_in = float(pd.to_numeric(entradas["valor"], errors="coerce").sum()) if not entradas.empty else 0.0
    total_out = float(pd.to_numeric(saidas["valor"], errors="coerce").sum()) if not saidas.empty else 0.0
    saldo = total_in + total_out  # saídas já estão negativas

    c1, c2, c3 = st.columns(3)
    c1.metric("Entradas (pagas)", f"R$ {total_in:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
    c2.metric("Saídas (pagas)",   f"R$ {abs(total_out):,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
    c3.metric("Saldo",            f"R$ {saldo:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

    # Gráfico diário (entradas positivas, saídas negativas)
    fluxo = pd.concat([
        entradas.rename(columns={"data_pagamento":"data"})[["data","valor"]],
        saidas.rename(columns={"data_pagamento":"data"})[["data","valor"]],
    ], ignore_index=True)
    fluxo = fluxo.dropna(subset=["data"])
    if not fluxo.empty:
        by_day = fluxo.assign(dia=fluxo["data"].dt.date).groupby("dia", as_index=False)["valor"].sum()
        st.bar_chart(by_day.set_index("dia"))
    else:
        st.info("Nenhum lançamento financeiro pago no período.")

    # Tabelas detalhadas
    with st.expander("Detalhe – Entradas (contas a receber pagas)"):
        if not entradas.empty:
            st.dataframe(entradas.sort_values("data_pagamento", ascending=False), use_container_width=True)
        else:
            st.write("Sem entradas no período.")

    with st.expander("Detalhe – Saídas (contas a pagar pagas)"):
        if not saidas.empty:
            st.dataframe(saidas.sort_values("data_pagamento", ascending=False), use_container_width=True)
        else:
            st.write("Sem saídas no período.")
