# bling_dashboard_streamlit.py
# -*- coding: utf-8 -*-
"""
MVP de dashboard para vendas do Bling (API v3) em Streamlit.

• Autenticação: OAuth2 usando refresh_token (gerado após a 1ª autorização do app no Bling).
• Suporta múltiplas contas (ex.: sua loja e a da sua esposa). Configure via st.secrets ou config.yaml.
• Endpoints usados: /Api/v3/oauth/token (refresh) e /Api/v3/pedidos/vendas (listar vendas).
• Paginação: pagina & limite (ver Boas práticas da API v3 do Bling).

Como rodar:
1) pip install streamlit pydantic requests pyyaml python-dateutil pandas
2) Configure as contas em .streamlit/secrets.toml (ou crie um config.yaml na mesma pasta; exemplo ao final do arquivo).
3) streamlit run bling_dashboard_streamlit.py

Atenção:
- Você precisa criar um APLICATIVO no portal de desenvolvedores do Bling, autorizar nas DUAS contas
  (a sua e a da sua esposa) e capturar o refresh_token de cada instalação.
- O app abaixo NUNCA expõe client_secret nem tokens no front; tudo roda no servidor local do Streamlit.
"""

from __future__ import annotations
import base64
import datetime as dt
from dataclasses import dataclass
from dateutil.relativedelta import relativedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
import yaml

BLING_BASE = "https://www.bling.com.br/Api/v3"
TOKEN_URL = f"{BLING_BASE}/oauth/token"
ORDERS_URL = f"{BLING_BASE}/pedidos/vendas"
DEFAULT_LIMIT = 100  # limite por página conforme padrão do Bling

# ---------------------------
# Config & Models
# ---------------------------
@dataclass
class Account:
    name: str
    client_id: str
    client_secret: str
    refresh_token: str

    # stateful (preenchidos em runtime)
    access_token: Optional[str] = None
    access_token_exp: Optional[dt.datetime] = None


def load_config(path: str = "config.yaml") -> List[Account]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    accounts = []
    for a in raw.get("accounts", []):
        accounts.append(Account(
            name=a["name"],
            client_id=a["client_id"],
            client_secret=a["client_secret"],
            refresh_token=a["refresh_token"],
        ))
    if not accounts:
        raise RuntimeError("Nenhuma conta encontrada no config.yaml > accounts[].")
    return accounts


def load_accounts() -> List[Account]:
    """Carrega contas a partir de st.secrets (se disponível) ou cai no config.yaml.
    
    Formatos suportados em st.secrets:
    1) accounts = [ { name, client_id, client_secret, refresh_token }, ... ]
    2) Chaves pontuais, por exemplo:
       TS_CLIENT_ID, TS_CLIENT_SECRET, TS_REFRESH_TOKEN
       BAZAR_CLIENT_ID, BAZAR_CLIENT_SECRET, BAZAR_REFRESH_TOKEN
    """
    accs: List[Account] = []
    try:
        secrets = st.secrets
    except Exception:
        secrets = {}
    # Formato 1: lista de contas em st.secrets["accounts"]
    try:
        if secrets and "accounts" in secrets and isinstance(secrets["accounts"], (list, tuple)):
            for a in secrets["accounts"]:
                accs.append(Account(
                    name=a["name"],
                    client_id=a["client_id"],
                    client_secret=a["client_secret"],
                    refresh_token=a["refresh_token"],
                ))
    except Exception:
        # ignora e tenta outros formatos
        pass
    # Formato 2: pares de chaves com prefixo
    def _from_prefix(prefix: str, default_name: str) -> Optional[Account]:
        cid = secrets.get(f"{prefix}_CLIENT_ID") if secrets else None
        csec = secrets.get(f"{prefix}_CLIENT_SECRET") if secrets else None
        rtk = secrets.get(f"{prefix}_REFRESH_TOKEN") if secrets else None
        if cid and csec and rtk:
            return Account(name=default_name, client_id=cid, client_secret=csec, refresh_token=rtk)
        return None
    if secrets and not accs:
        # Exemplos comuns
        for prefix, nm in [("TS", "Loja Tiburcio's Stuff"), ("BAZAR", "TS Bazar")]:
            maybe = _from_prefix(prefix, nm)
            if maybe:
                accs.append(maybe)
    if accs:
        return accs
    # fallback: config.yaml
    return load_config()


# ---------------------------
# OAuth helpers
# ---------------------------

def _basic_auth_header(client_id: str, client_secret: str) -> str:
    token = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(token).decode("ascii")


def refresh_access_token(acc: Account) -> Tuple[str, dt.datetime]:
    """Usa o refresh_token para obter um novo access_token (expira em ~6h)."""
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "1.0",
        "Authorization": _basic_auth_header(acc.client_id, acc.client_secret),
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": acc.refresh_token,
    }
    resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Falha no refresh token de {acc.name}: {resp.status_code} – {resp.text}")
    j = resp.json()
    access = j["access_token"]
    expires_in = int(j.get("expires_in", 21600))  # segundos (doc Bling)
    exp_at = dt.datetime.utcnow() + dt.timedelta(seconds=expires_in - 60)
    # Atualiza refresh_token se o Bling devolver um novo
    if j.get("refresh_token") and j["refresh_token"] != acc.refresh_token:
        acc.refresh_token = j["refresh_token"]
    return access, exp_at


def get_access_token(acc: Account) -> str:
    if not acc.access_token or not acc.access_token_exp or dt.datetime.utcnow() > acc.access_token_exp:
        acc.access_token, acc.access_token_exp = refresh_access_token(acc)
    return acc.access_token


# ---------------------------
# API fetchers
# ---------------------------

def fetch_orders(acc: Account, date_start: dt.date, date_end: dt.date,
                 loja_id: Optional[int] = None,
                 limite: int = DEFAULT_LIMIT,
                 max_pages: int = 200) -> List[Dict]:
    """Busca pedidos de venda no intervalo [date_start, date_end].
    Usa paginação; retorna lista de dicts de pedidos brutos.
    """
    bearer = get_access_token(acc)
    all_rows: List[Dict] = []
    pagina = 1
    params_base = {
        "dataInicial": date_start.strftime("%Y-%m-%d"),
        "dataFinal": date_end.strftime("%Y-%m-%d"),
        "limite": limite,
    }
    if loja_id is not None:
        params_base["idLoja"] = loja_id

    while pagina <= max_pages:
        params = dict(params_base)
        params["pagina"] = pagina
        r = requests.get(ORDERS_URL, headers={"Authorization": f"Bearer {bearer}"}, params=params, timeout=30)
        if r.status_code == 401:
            # token expirou no meio – força refresh e repete esta página
            acc.access_token = None
            bearer = get_access_token(acc)
            r = requests.get(ORDERS_URL, headers={"Authorization": f"Bearer {bearer}"}, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Erro ao listar pedidos ({acc.name}) p{pagina}: {r.status_code} – {r.text}")
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data") or data.get("itens") or []
        if not rows:
            break
        all_rows.extend(rows)
        # Heurística: se retornou menos que o limite, acabou; senão incrementa página
        if len(rows) < limite:
            break
        pagina += 1
    return all_rows


# ---------------------------
# Transformações
# ---------------------------

def normalize_orders(raw_rows: List[Dict], account_name: str) -> pd.DataFrame:
    """Normaliza campos essenciais do pedido de venda."""
    if not raw_rows:
        return pd.DataFrame(columns=[
            "account", "id", "data", "numero", "numeroLoja", "total",
            "contato_id", "vendedor_id", "loja_id",
        ])
    def safe(d: Dict, *keys, default=None):
        cur = d
        for k in keys:
            cur = None if cur is None else cur.get(k)
        return default if cur is None else cur

    recs = []
    for x in raw_rows:
        recs.append({
            "account": account_name,
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
    # Tipagens e datas
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
        df["total"] = pd.to_numeric(df["total"], errors="coerce")
    return df


def kpis(df: pd.DataFrame) -> Dict[str, float]:
    if df.empty:
        return {"Pedidos": 0, "Receita": 0.0, "Ticket médio": 0.0}
    pedidos = int(df.shape[0])
    receita = float(df["total"].sum())
    ticket = float(receita / pedidos) if pedidos else 0.0
    return {"Pedidos": pedidos, "Receita": receita, "Ticket médio": ticket}


# ---------------------------
# UI (Streamlit)
# ---------------------------
st.set_page_config(page_title="Bling – Vendas", layout="wide")
st.title("📊 Dashboard de Vendas – Bling API v3 (MVP)")

st.sidebar.header("Filtros")
DEFAULT_START = (dt.date.today() - relativedelta(months=1)).replace(day=1)
DEFAULT_END = dt.date.today()

col1, col2 = st.sidebar.columns(2)
with col1:
    date_start = st.date_input("Data inicial", value=DEFAULT_START)
with col2:
    date_end = st.date_input("Data final", value=DEFAULT_END)

loja_id = st.sidebar.text_input("ID da Loja (opcional)")
loja_id_val = int(loja_id) if loja_id.strip().isdigit() else None

st.sidebar.caption("Para filtrar por loja específica, informe o ID conforme cadastro no Bling.")

# Carrega contas (st.secrets ou config.yaml)
try:
    accounts = load_accounts()
except Exception as e:
    st.error(f"Erro ao carregar contas (st.secrets/config.yaml): {e}")
    st.stop()

if st.sidebar.button("Atualizar dados"):
    st.session_state.pop("df_all", None)

@st.cache_data(ttl=300)
def load_data_cached(_accounts: List[Account], _start: dt.date, _end: dt.date, _loja: Optional[int]):
    dfs = []
    errors = []
    for acc in _accounts:
        try:
            raw = fetch_orders(acc, _start, _end, loja_id=_loja)
            df = normalize_orders(raw, acc.name)
            dfs.append(df)
        except Exception as ex:
            errors.append(f"{acc.name}: {ex}")
    df_all = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return df_all, errors

with st.spinner("Buscando pedidos no Bling..."):
    df_all, errs = load_data_cached(accounts, date_start, date_end, loja_id_val)

if errs:
    with st.expander("Avisos/Erros de integração", expanded=False):
        for e in errs:
            st.warning(e)

if df_all.empty:
    st.info("Nenhum pedido encontrado para os filtros informados.")
    st.stop()

# KPIs
metrics = kpis(df_all)
mc1, mc2, mc3 = st.columns(3)
mc1.metric("Pedidos", f"{metrics['Pedidos']:,}".replace(",","."))
mc2.metric("Receita", f"R$ {metrics['Receita']:,.2f}".replace(",","#").replace(".",",").replace("#","."))
mc3.metric("Ticket médio", f"R$ {metrics['Ticket médio']:,.2f}".replace(",","#").replace(".",",").replace("#","."))

# Séries de tempo
st.subheader("Vendas por dia")
by_day = (
    df_all.assign(dia=df_all["data"].dt.date)
         .groupby(["dia","account"], as_index=False)["total"].sum()
)
st.line_chart(by_day.pivot(index="dia", columns="account", values="total"))

# Top lojas (IDs) e top pedidos por valor
colA, colB = st.columns([2, 1])
with colA:
    st.subheader("Receita por loja (ID)")
    by_loja = df_all.groupby("loja_id", as_index=False)["total"].sum().sort_values("total", ascending=False)
    st.bar_chart(by_loja.set_index("loja_id"))
with colB:
    st.subheader("Top 10 pedidos")
    top10 = df_all.sort_values("total", ascending=False).head(10)
    st.dataframe(top10[["account","id","numero","data","total"]])

st.subheader("Tabela de pedidos")
st.dataframe(df_all.sort_values("data", ascending=False))

st.caption(
    """
    • Fonte: Bling API v3 – endpoint /pedidos/vendas (Bearer).\n
    • Paginação: parâmetros 'pagina' e 'limite'.\n
    • Para dados em tempo quase real, recomendo ativar Webhooks para 'Pedido de Venda' e alimentar um banco (ex.: SQLite/Postgres) – próximo passo.
    """
)

# ---------------------------
# Exemplo de config.yaml
# ---------------------------
#
# accounts:
#   - name: "Loja Tiburcio's Stuff"
#     client_id: "SEU_CLIENT_ID"
#     client_secret: "SEU_CLIENT_SECRET"
#     refresh_token: "SEU_REFRESH_TOKEN"
#   - name: "Loja Esposa"
#     client_id: "CLIENT_ID_DELA"
#     client_secret: "CLIENT_SECRET_DELA"
#     refresh_token: "REFRESH_TOKEN_DELA"
