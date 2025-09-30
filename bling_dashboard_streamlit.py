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
APP_BASE         = st.secrets.get("APP_BASE", "https://dashboard-ts.streamlit.app")
TS_CLIENT_ID     = st.secrets["TS_CLIENT_ID"]
TS_CLIENT_SECRET = st.secrets["TS_CLIENT_SECRET"]

AUTH_URL   = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL  = "https://www.bling.com.br/Api/v3/oauth/token"

# Pedidos (para KPIs de vendas)
ORDERS_URL = "https://www.bling.com.br/Api/v3/pedidos/vendas"

# Receber/Pagar (fallback para DRE, se extratos não estiverem disponíveis)
RECEBER_URL = "https://www.bling.com.br/Api/v3/contas/receber"
PAGAR_URL   = "https://www.bling.com.br/Api/v3/contas/pagar"

PAGE_LIMIT  = 100

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

# ================== CAPTURA AUTOMÁTICA DO ?code= ==================
def normalize_qp(d: dict) -> dict:
    return {k: (v[0] if isinstance(v, list) else v) for k, v in d.items()}

def auto_capture_code() -> Optional[tuple[str, str]]:
    # st.query_params (>=1.33)
    try:
        qp = normalize_qp(dict(st.query_params.items()))
        if qp.get("code") and qp.get("state"):
            return qp["code"], qp["state"]
    except Exception:
        pass
    # compatibilidade: experimental_get_query_params
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

# ================== LAYOUT EM ABAS ==================
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

    # (Removido) Exibição do refresh token nesta aba

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

# ================== BUSCAS ==================
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

# ====== 1.b) BORDERÔS (CONFIRMADOS) – fallback entre extratos e R/P ======
@st.cache_data(ttl=300, show_spinner=False)
def fetch_bordero_confirmed(refresh_token: str, date_start: dt.date, date_end: dt.date) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Recupera borderôs confirmados como alternativa aos extratos, tentando
    múltiplas rotas e nomes de parâmetros comuns no Bling.
    """
    access, maybe_new_refresh = refresh_access_token(refresh_token)
    headers = {"Authorization": f"Bearer {access}"}

    candidates = [
        ("https://www.bling.com.br/Api/v3/financeiro/borderos", ("dataInicial", "dataFinal")),
        ("https://www.bling.com.br/Api/v3/financeiro/borderos", ("dataCriacaoInicial", "dataCriacaoFinal")),
        ("https://www.bling.com.br/Api/v3/financeiro/borderos", ("dataLiquidacaoInicial", "dataLiquidacaoFinal")),
        ("https://www.bling.com.br/Api/v3/financeiro/borderos", ("dataBaixaInicial", "dataBaixaFinal")),
        ("https://www.bling.com.br/Api/v3/caixas/borderos", ("dataInicial", "dataFinal")),
        ("https://www.bling.com.br/Api/v3/contas/borderos", ("dataInicial", "dataFinal")),
        ("https://www.bling.com.br/Api/v3/borderos", ("dataInicial", "dataFinal")),
    ]

    last_err = None
    rows: List[dict] = []
    for url, (p_ini, p_fim) in candidates:
        try:
            params = {
                p_ini: date_start.strftime("%Y-%m-%d"),
                p_fim: date_end.strftime("%Y-%m-%d"),
                # quando disponível, priorizar confirmados/liquidados
                "apenasConfirmados": "true",
            }
            rows = _get_paginated_generic(url, headers, params)
            if rows:
                try:
                    st.session_state["_fin_source_detail"] = f"{url} {p_ini},{p_fim} rows={len(rows)}"
                except Exception:
                    pass
                break
        except Exception as e:
            last_err = e
            rows = []

    if rows == [] and last_err:
        raise RuntimeError(f"Borderôs não disponíveis: {last_err}")

    def g(d, k, default=None):
        return d.get(k, default) if isinstance(d, dict) else default

    def pick_date(d):
        return (
            g(d, "data")
            or g(d, "dataCriacao")
            or g(d, "dataLiquidacao")
            or g(d, "dataBaixa")
        )

    def pick_amount(d):
        # tenta campos comuns de valor
        valor = (
            g(d, "valorLiquido")
            or g(d, "valorTotal")
            or g(d, "valor")
        )
        v = float(pd.to_numeric(valor, errors="coerce") or 0)
        tipo = (g(d, "tipo") or g(d, "natureza") or g(d, "operacao") or "").upper()
        # Heurística para sinal: recebimentos positivos, pagamentos negativos
        if "RECEB" in tipo or "ENTR" in tipo or tipo.startswith("C"):
            return abs(v)
        if "PAG" in tipo or "SAID" in tipo or tipo.startswith("D"):
            return -abs(v)
        # sem pista: assume positivo
        return v

    df = pd.DataFrame([
        {
            "data": pick_date(x),
            "descricao": g(x, "descricao") or g(x, "observacao") or g(x, "historico"),
            "valor": pick_amount(x),
        }
        for x in rows
    ])

    if not df.empty:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
        df = df.dropna(subset=["data"])
    return df, maybe_new_refresh

# ====== 1) EXTRATOS / CAIXAS & BANCOS (CONFIRMADOS) ======
def _get_paginated_generic(url: str, headers: dict, params: dict) -> List[dict]:
    out: List[dict] = []
    p = params.copy()
    p["limite"] = PAGE_LIMIT
    p["pagina"] = 1
    while True:
        r = requests.get(url, headers=headers, params=p, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"{url} → {r.status_code} – {r.text}")
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
        raise RuntimeError(f"Extratos (‘Caixas & Bancos’) não disponíveis: {last_err}")

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

# ====== 2) RECEBER/PAGAR pagos (fallback) ======
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

# ================== DASHBOARD ==================
with tab_dash:
    st.title("📊 Dashboard de vendas – Bling (Tiburcio’s Stuff)")

    if not st.session_state["ts_refresh"]:
        with st.expander("Avisos/Erros de integração", expanded=True):
            st.info("Autorize a conta **TS** na aba **‘🔐 Integração (OAuth)’** para carregar as vendas/financeiro.")
        st.stop()

    # --- VENDAS
    errors: List[str] = []
    try:
        df_vendas, new_r = fetch_orders(st.session_state["ts_refresh"], date_start, date_end)
        if new_r:
            st.session_state["ts_refresh"] = new_r
    except Exception as e:
        errors.append(f"Vendas: {e}")
        df_vendas = pd.DataFrame()

    # --- FINANCEIRO: preferir extratos confirmados; depois borderôs; fallback receber/pagar pagos
    origem_fin = "Indisponível"
    df_mov = pd.DataFrame(columns=["data", "descricao", "valor"])
    try:
        df_mov, new_r2 = fetch_bank_confirmed(st.session_state["ts_refresh"], date_start, date_end)
        origem_fin = "Caixas & Bancos (confirmados)"
        if new_r2:
            st.session_state["ts_refresh"] = new_r2
    except Exception as e:
        # tenta borderôs antes do fallback R/P
        try:
            df_mov, new_r_bordero = fetch_bordero_confirmed(st.session_state["ts_refresh"], date_start, date_end)
            origem_fin = "Borderôs (confirmados)"
            if new_r_bordero:
                st.session_state["ts_refresh"] = new_r_bordero
        except Exception as e_bordero:
            try:
                df_mov, new_r3 = fetch_cashflow_fallback(st.session_state["ts_refresh"], date_start, date_end)
                origem_fin = "Receber/Pagar pagos (fallback)"
                if new_r3:
                    st.session_state["ts_refresh"] = new_r3
            except Exception as e2:
                errors.append(f"Financeiro: {e}\nBorderôs: {e_bordero}\nFallback: {e2}")
                # mantém df_mov vazio e origem_fin = "Indisponível"

    if errors:
        with st.expander("Avisos/Erros de integração", expanded=True):
            for e in errors:
                st.warning(e)
            try:
                detail = st.session_state.get("_fin_source_detail")
                if detail:
                    st.info(f"Fonte financeira utilizada: {detail}")
            except Exception:
                pass
            # Dica específica para erro de refresh token inválido
            try:
                all_err = "\n".join(errors)
                if "invalid_grant" in all_err or "Invalid refresh token" in all_err:
                    st.info("Seu refresh token está inválido/expirado. Vá na aba '🔐 Integração (OAuth)' e clique em 'Autorizar TS' para gerar um novo.")
            except Exception:
                pass

    # ===== Sub-abas: Vendas primeiro, Financeiro depois
    sub_sales, sub_fin = st.tabs(["🛒 Vendas", "📈 Financeiro (DRE mensal)"])

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

    # ---------- Financeiro (DRE) ----------
    with sub_fin:
        st.subheader(f"DRE mensal (simplificado) – origem: {origem_fin}")
        if df_mov.empty:
            st.info("Nenhum lançamento financeiro no período.")
        else:
            tmp = df_mov.copy()
            tmp["mes"] = pd.to_datetime(tmp["data"]).dt.to_period("M").dt.to_timestamp()

            # Entradas (valores positivos) e Pagos (valores negativos, convertidos para positivo)
            receitas = (
                tmp[tmp["valor"] > 0]
                .groupby("mes", as_index=False)["valor"]
                .sum()
                .rename(columns={"valor": "entradas"})
            )
            despesas = (
                tmp[tmp["valor"] < 0]
                .groupby("mes", as_index=False)["valor"]
                .sum()
                .rename(columns={"valor": "despesas"})
            )
            dre = pd.merge(receitas, despesas, on="mes", how="outer").fillna(0.0)
            # Converter despesas negativas para 'pagos' positivos
            dre["pagos"] = (-dre["despesas"]).clip(lower=0)
            dre["entradas"] = dre.get("entradas", 0.0)
            # Diferença = Entradas - Pagos
            dre["diferenca"] = dre["entradas"] - dre["pagos"]
            dre["acumulado"] = dre["diferenca"].cumsum()

            # Totais do período
            total_recebido = float(dre["entradas"].sum()) if not dre.empty else 0.0
            total_pago     = float(dre["pagos"].sum()) if not dre.empty else 0.0
            diff           = total_recebido - total_pago

            # Duas colunas no topo (Pago à esquerda / Recebido à direita)
            c_pag, c_rec = st.columns(2)
            c_pag.metric("Pago no período",      f"R$ {total_pago:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
            c_rec.metric("Recebido no período", f"R$ {total_recebido:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

            # KPIs adicionais (mantém resumo)
            colA, colB, colC = st.columns(3)
            colA.metric("Receitas (confirmadas)", f"R$ {total_recebido:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
            colB.metric("Despesas (confirmadas)", f"R$ {total_pago:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
            colC.metric("Resultado do período",   f"R$ {diff:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))

            # Gráfico: barras (Entradas x Pagos) + linha (Diferença)
            base = alt.Chart(dre).encode(x=alt.X("mes:T", title="Mês"))
            bars = alt.layer(
                base.mark_bar().encode(y=alt.Y("entradas:Q", title="Valor")),
                base.mark_bar().encode(y="pagos:Q")
            )
            line = base.mark_line(point=True).encode(y="diferenca:Q")
            st.altair_chart(bars + line, use_container_width=True)

            st.subheader("Tabela DRE mensal")
            show = dre[["mes", "entradas", "pagos", "diferenca", "acumulado"]].copy()
            for c in ["entradas", "pagos", "diferenca", "acumulado"]:
                show[c] = show[c].map(lambda v: f"R$ {v:,.2f}".replace(",", "#").replace(".", ",").replace("#", "."))
            st.dataframe(show, use_container_width=True)

            # Detalhe do período (Paguei à esquerda, Recebi à direita)
            st.subheader("Detalhe do período")
            colP, colR = st.columns(2)

            entradas_df = tmp[tmp["valor"] > 0].copy()
            saidas_df   = tmp[tmp["valor"] < 0].copy()
            total_entradas = float(pd.to_numeric(entradas_df["valor"], errors="coerce").sum()) if not entradas_df.empty else 0.0
            total_saidas   = float(pd.to_numeric(saidas_df["valor"], errors="coerce").sum()) if not saidas_df.empty else 0.0

            with colP:
                st.markdown(
                    f"**Pagos no período** — Total: {('R$ ' + format(abs(total_saidas), ',.2f')).replace(',', '#').replace('.', ',').replace('#', '.')}"
                )
                show_s = saidas_df.sort_values("data", ascending=False)[["data", "descricao", "valor"]].copy()
                show_s["valor"] = show_s["valor"].abs()
                st.dataframe(show_s, use_container_width=True)

            with colR:
                st.markdown(
                    f"**Recebidos no período** — Total: {('R$ ' + format(total_entradas, ',.2f')).replace(',', '#').replace('.', ',').replace('#', '.')}"
                )
                show_e = entradas_df.sort_values("data", ascending=False)[["data", "descricao", "valor"]]
                st.dataframe(show_e, use_container_width=True)

            with st.expander("Movimentos brutos (sinais originais)"):
                st.dataframe(df_mov.sort_values("data", ascending=False), use_container_width=True)
