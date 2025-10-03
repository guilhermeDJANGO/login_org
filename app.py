import os
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

import streamlit as st
import google.generativeai as genai

# ======================== CONFIG INICIAL ========================
st.set_page_config(page_title="Login + Chat", page_icon="🔐", layout="centered")
DB_PATH = Path("users.db")

# ------------------------ CHAVE GEMINI --------------------------
# Lê a chave dos Secrets (Cloud) ou variável de ambiente (local)
API_KEY = (st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()

if not API_KEY:
    st.error(
        "GEMINI_API_KEY ausente. Defina em **Manage app → Settings → Secrets** (Cloud) "
        'como:  GEMINI_API_KEY = "AIza...sua_chave..."'
    )
    st.stop()

if not API_KEY.startswith("AIza"):
    st.error(
        "A chave informada **não parece válida** (não começa com `AIza`). "
        "Abra o **Google AI Studio → API keys**, gere uma nova e copie a chave **completa**."
    )
    st.stop()

# Configura a SDK
try:
    genai.configure(api_key=API_KEY)
    st.caption("🔑 Gemini key carregada: ✅")
except Exception as e:
    st.error(f"Falha ao configurar Gemini: {e}")
    st.stop()

# Descobre modelos disponíveis que suportam generateContent
AVAILABLE = []
try:
    AVAILABLE = [
        m.name for m in genai.list_models()
        if "generateContent" in getattr(m, "supported_generation_methods", [])
    ]
except Exception:
    AVAILABLE = []

def pick_model(candidates, available):
    for pref in candidates:
        for name in available:
            # aceita "gemini-1.5-flash-latest" e "models/gemini-1.5-flash-latest"
            if name.endswith(pref) or pref in name:
                return name
    return None

MODEL_ID = pick_model(
    [
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
        "gemini-1.5-pro-latest",
        "gemini-1.5-pro",
        "gemini-pro",
    ],
    AVAILABLE,
) or "gemini-pro"

st.session_state.setdefault("_gemini_model_id", MODEL_ID)
# ===============================================================


# ============================= DB ==============================
def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    with get_conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        con.commit()

def hash_password(p: str) -> str:
    # DEMO: sha256 (em produção prefira passlib[bcrypt])
    return hashlib.sha256(p.encode("utf-8")).hexdigest()

def user_exists(username: str) -> bool:
    with get_conn() as con:
        cur = con.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        return cur.fetchone() is not None

def create_user(username: str, password: str) -> bool:
    try:
        with get_conn() as con:
            con.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, hash_password(password), datetime.utcnow().isoformat(timespec="seconds")),
            )
            con.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def check_credentials(username: str, password: str) -> bool:
    with get_conn() as con:
        cur = con.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        return bool(row and row[0] == hash_password(password))
# ===============================================================


# ============================== UI =============================
init_db()

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""

st.title("🔐 Área de acesso")
tab_login, tab_register = st.tabs(["Entrar", "Cadastrar"])

with tab_login:
    st.subheader("Faça login")
    with st.form("login_form"):
        u = st.text_input("Usuário")
        p = st.text_input("Senha", type="password")
        ok = st.form_submit_button("Entrar")
    if ok:
        if check_credentials(u.strip(), p):
            st.session_state.logged_in = True
            st.session_state.username = u.strip()
            st.success(f"Bem-vindo(a), {st.session_state.username}!")
        else:
            st.error("Usuário ou senha inválidos.")

with tab_register:
    st.subheader("Crie sua conta")
    with st.form("register_form", clear_on_submit=True):
        nu = st.text_input("Novo usuário")
        p1 = st.text_input("Senha", type="password")
        p2 = st.text_input("Confirmar senha", type="password")
        ok2 = st.form_submit_button("Cadastrar")
    if ok2:
        nu = nu.strip()
        if not nu or not p1:
            st.warning("Preencha usuário e senha.")
        elif len(p1) < 6:
            st.warning("A senha deve ter pelo menos 6 caracteres.")
        elif p1 != p2:
            st.warning("As senhas não conferem.")
        elif user_exists(nu):
            st.info("Usuário já existe. Tente outro nome.")
        else:
            if create_user(nu, p1):
                st.success("cadastro cocluido")
            else:
                st.error("Não foi possível criar o usuário.")

st.divider()

# ------------------------- ÁREA LOGADA -------------------------
if st.session_state.logged_in:
    st.success(f"✅ Logado como **{st.session_state.username}**.")
    st.header("🤖 Chatbot (Gemini)")

    with st.expander("Modelos disponíveis (debug)"):
        st.write(AVAILABLE or "—")
        st.write("Usando:", st.session_state.get("_gemini_model_id"))

    # cria o modelo e mantém um chat com histórico na sessão
    model = genai.GenerativeModel(st.session_state["_gemini_model_id"])

    if "gemini_chat" not in st.session_state:
        st.session_state["gemini_chat"] = model.start_chat(history=[])

    # histórico
    for turn in st.session_state["gemini_chat"].history:
        role = "user" if turn.role == "user" else "assistant"
        with st.chat_message(role):
            st.markdown("".join(getattr(p, "text", "") for p in turn.parts))

    # entrada
    prompt = st.chat_input("Pergunte algo…")
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            ph = st.empty()
            acc = ""
            try:
                for chunk in st.session_state["gemini_chat"].send_message(prompt, stream=True):
                    acc += (chunk.text or "")
                    ph.markdown(acc)
            except Exception as e:
                st.error(f"Erro no Gemini: {e}")

    c1, c2 = st.columns(2)
    if c1.button("🧹 Limpar chat"):
        st.session_state["gemini_chat"] = model.start_chat(history=[])
        st.rerun()
    if c2.button("🚪 Sair"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.info("Sessão encerrada.")
else:
    st.info("Faça login para acessar o conteúdo.")
# ===============================================================
