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

# ============== CONFIG ==============
APP_BASE         = st.secrets.get("APP_BASE", "https://dashboard-ts.streamlit.app")
TS_CLIENT_ID     = st.secrets["TS_CLIENT_ID"]
TS_CLIENT_SECRET = st.secrets["TS_CLIENT_SECRET"]

AUTH_URL   = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL  = "https://www.bling.com.br/Api/v3/oauth/token"

# Pedidos (para KPIs de vendas)
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"

# Finanças (entradas/saídas pagas)
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
    p_ini, p_fim = paid_param_names
    params = {
        p_ini: date_start.strftime("%Y-%m-%d"),
        p_fim: date_end.strftime("%Y-%m-%d"),
        "situacao": "PAGO",   # pode ser 'PAGA' / 'RECEBIDO' em contas específicas — ajustamos se necessário
        "limite": PAGE_LIMIT,
        "pagina": 1,
    }
    out: List[dict] = []
    while True:
        r = requests.get(fin_url, headers=headers, params=params, timeout=30)
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

    tries = [
        ("dataPagamentoInicial", "dataPagamentoFinal"),
        ("dataBaixaInicial", "dataBaixaFinal"),
    ]

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
        raise RuntimeError(f"Contas a receber (entradas) não disponíveis: {last_err}")

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

    def g(d, k, default=None): return d.get(k, default) if isinstance(d, dict) else default
    def pick_payment_date(d):
        return g(d, "dataPagamento") or g(d, "dataBaixa") or g(d, "dataVencimento") or g(d, "data")
    def pick_value(d):
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
        "valor": -pd.to_numeric(pick_value(x), errors="coerce"),
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

    # Carrega dados
    errors: List[str] = []
    try:
        df_vendas, new_r = fetch_orders(st.session_state["ts_refresh"], date_start, date_end)
        if new_r:
            st.session_state["ts_refresh"] = new_r
    except Exception as e:
        errors.append(f"Vendas: {e}")
        df_vendas = pd.DataFrame()

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

    # ===== Sub-abas: Financeiro (DRE) e Vendas =====
    sub_fin, sub_sales = st.tabs(["📈 Financeiro (DRE mensal)", "🛒 Vendas"])

    # ---------- Financeiro (DRE) ----------
    with sub_fin:
        st.subheader("DRE mensal (simplificado)")
        # Monta DRE mensal com base em pagamentos (entradas/saídas)
        def monthly(df: pd.DataFrame) -> pd.DataFrame:
            if df.empty:
                return pd.DataFrame(columns=["mes","valor"])
            tmp = df.copy()
            tmp["mes"] = tmp["data_pagamento"].dt.to_period("M").dt.to_timestamp()
            out = tmp.groupby("mes", as_index=False)["valor"].sum()
            return out.sort_values("mes")

        m_in  = monthly(entradas).rename(columns={"valor":"receitas"})
        m_out = monthly(saidas).rename(columns={"valor":"despesas"})
        dre = pd.merge(m_in, m_out, on="mes", how="outer").fillna(0.0)
        dre["resultado"] = dre["receitas"] + dre["despesas"]  # despesas já negativas
        dre["acumulado"] = dre["resultado"].cumsum()

        colA, colB, colC = st.columns(3)
        total_in  = float(m_in["receitas"].sum()) if not m_in.empty else 0.0
        total_out = float(m_out["despesas"].sum()) if not m_out.empty else 0.0
        saldo     = total_in + total_out
        colA.metric("Receitas (pagas)", f"R$ {total_in:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
        colB.metric("Despesas (pagas)", f"R$ {abs(total_out):,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
        colC.metric("Resultado do período", f"R$ {saldo:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

        if not dre.empty:
            # Gráfico: barras Receitas/Despesas + linha Resultado
            base = alt.Chart(dre).encode(x=alt.X("mes:T", title="Mês"))
            bars = alt.layer(
                base.mark_bar().encode(y=alt.Y("receitas:Q", title="Valor"), color=alt.value("#4CAF50")),
                base.mark_bar().encode(y="despesas:Q", color=alt.value("#E53935"))
            )
            line = base.mark_line(point=True).encode(y="resultado:Q", color=alt.value("#1E88E5"))
            st.altair_chart(bars + line, use_container_width=True)

            st.subheader("Tabela DRE mensal")
            dre_fmt = dre.copy()
            for c in ["receitas","despesas","resultado","acumulado"]:
                dre_fmt[c] = dre_fmt[c].map(lambda v: f"R$ {v:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
            st.dataframe(dre_fmt, use_container_width=True)
        else:
            st.info("Nenhum lançamento financeiro pago no período.")

        # Detalhes
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

    # ---------- Vendas ----------
    with sub_sales:
        st.subheader("KPIs de vendas")
        if not df_vendas.empty:
            col1, col2, col3 = st.columns(3)
            qtd     = int(df_vendas.shape[0])
            receita = float(pd.to_numeric(df_vendas["total"], errors="coerce").sum())
            ticket  = float(receita / qtd) if qtd else 0.0
            col1.metric("Pedidos", f"{qtd:,}".replace(",", "."))
            col2.metric("Receita (pedidos)", f"R$ {receita:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
            col3.metric("Ticket médio", f"R$ {ticket:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

            st.subheader("Vendas por dia")
            by_day = df_vendas.assign(dia=df_vendas["data"].dt.date).groupby("dia", as_index=False)["total"].sum()
            st.line_chart(by_day.set_index("dia"))

            st.subheader("Tabela de pedidos")
            st.dataframe(df_vendas.sort_values("data", ascending=False), use_container_width=True)
        else:
            st.info("Nenhum pedido encontrado para os filtros informados.")
