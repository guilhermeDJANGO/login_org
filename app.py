import os
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader  # <-- NOVO

# ======================== CONFIG GERAL ========================
st.set_page_config(page_title="Login + Chat", page_icon="🔐", layout="centered")
DB_PATH = Path("users.db")

# ------------------------ GEMINI (AI) ------------------------
API_KEY = (st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
AVAILABLE: list[str] = []
MODEL_ID: str | None = None

if not API_KEY:
    st.error("Defina GEMINI_API_KEY em Settings → Secrets (Cloud) ou .streamlit/secrets.toml (local).")
    st.stop()
if not API_KEY.startswith("AIza"):
    st.error("Sua chave não parece válida (deveria começar com 'AIza'). Gere/copie a chave completa no Google AI Studio.")
    st.stop()

genai.configure(api_key=API_KEY)

# Tenta listar modelos e escolher um compatível com generateContent
try:
    AVAILABLE = [
        m.name for m in genai.list_models()
        if "generateContent" in getattr(m, "supported_generation_methods", [])
    ]
except Exception:
    AVAILABLE = []

def pick_model(candidates: list[str], available: list[str]) -> str | None:
    for pref in candidates:
        for name in available:
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

# =========================== DB ==============================
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

# ============================ UI =============================
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

# ---------------------- ÁREA LOGADA -------------------------
if st.session_state.logged_in:
    st.success(f"✅ Logado como **{st.session_state.username}**.")

    # 🔽 AQUI: duas abas internas — Chat e PDF
    tab_chat, tab_pdf = st.tabs(["🤖 Chat", "📄 PDF → texto"])

    # ============ ABA 1: CHAT (Gemini) ============
    with tab_chat:
        st.header("🤖 Chatbot (Gemini)")

        with st.expander("Modelos disponíveis (debug)"):
            st.write(AVAILABLE or "—")
            st.write("Usando:", st.session_state.get("_gemini_model_id"))

        # (Opcional) Base de conhecimento por TXT
        contexto_fixo = ""
        try:
            contexto_fixo = Path("data/brand_manual.txt").read_text(encoding="utf-8")
        except FileNotFoundError:
            pass

        uploaded = st.file_uploader("Envie um .txt (opcional) para o chat usar como base", type=["txt"])
        contexto_upload = ""
        if uploaded is not None:
            contexto_upload = uploaded.read().decode("utf-8", errors="ignore")
            st.success(f"Arquivo carregado ({len(contexto_upload)} caracteres).")

        contexto = "\n\n".join(s for s in [contexto_fixo, contexto_upload] if s)

        if contexto:
            instrucoes = (
                "Você é um assistente. Use o texto abaixo como base quando for relevante. "
                "Se a pergunta não estiver respondida pelo material, diga que não encontrou.\n\n"
                f"{contexto}"
            )
            model = genai.GenerativeModel(
                st.session_state["_gemini_model_id"],
                system_instruction=instrucoes,
            )
        else:
            model = genai.GenerativeModel(st.session_state["_gemini_model_id"])

        if contexto and st.button("Aplicar base e reiniciar chat"):
            st.session_state.pop("gemini_chat", None)
            st.rerun()

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
                placeholder = st.empty()
                acc = ""
                try:
                    for chunk in st.session_state["gemini_chat"].send_message(prompt, stream=True):
                        acc += chunk.text or ""
                        placeholder.markdown(acc)
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

    # ============ ABA 2: PDF → TEXTO ============
    with tab_pdf:
        st.header("📄 Upload de PDF e extração de texto")

        pdf_file = st.file_uploader("Envie um arquivo PDF", type=["pdf"], key="pdf_uploader")
        if pdf_file is not None:
            try:
                reader = PdfReader(pdf_file)
                parts = []
                for i, page in enumerate(reader.pages, start=1):
                    text = page.extract_text() or ""
                    parts.append(f"\n\n===== Página {i} =====\n{text}")

                full_text = "".join(parts).strip()

                if not full_text:
                    st.warning("Não foi possível extrair texto. Se o PDF for imagem (scaneado), este método não faz OCR.")
                else:
                    st.success(f"Extraído com sucesso — {len(full_text)} caracteres.")
                    st.text_area("Texto extraído", full_text, height=420)
                    st.download_button(
                        "Baixar texto (.txt)",
                        full_text,
                        file_name=f"pdf_text_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    )
            except Exception as e:
                st.error(f"Erro ao ler/extrair o PDF: {e}")

else:
    st.info("Faça login para acessar o conteúdo.")
# =============================================================
