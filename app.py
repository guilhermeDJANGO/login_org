# app.py
import os
import json
import time
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

import streamlit as st

# Google Gemini
import google.generativeai as genai
import google.api_core.exceptions as gexc

# ======================== CONFIG GERAL ========================
st.set_page_config(page_title="Login + Chat + PDF + SEO + Email", page_icon="🔐", layout="centered")
DB_PATH = Path("users.db")

# ====================== OBTÉM API KEY ========================
API_KEY = (st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
if not API_KEY:
    st.error("Defina GEMINI_API_KEY em Settings → Secrets (Cloud) ou em .streamlit/secrets.toml (local).")
    st.stop()
if not API_KEY.startswith("AIza"):
    st.error("Sua chave não parece válida (deveria começar com 'AIza'). Gere/copie no Google AI Studio.")
    st.stop()

genai.configure(api_key=API_KEY)

# ============== LISTA MODELOS E ESCOLHE UM COMPATÍVEL =========
def safe_list_models():
    try:
        return [
            m.name for m in genai.list_models()
            if "generateContent" in getattr(m, "supported_generation_methods", [])
        ]
    except Exception:
        return []

AVAILABLE = safe_list_models()

def pick_model(candidates, available):
    for pref in candidates:
        for name in available:
            if name.endswith(pref) or pref in name:
                return name
    return None

PREFERRED = [
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
    "gemini-1.5-pro-latest",
    "gemini-1.5-pro",
    "gemini-pro",
]
MODEL_ID = pick_model(PREFERRED, AVAILABLE) or "gemini-1.5-flash"
st.session_state.setdefault("_gemini_model_id", MODEL_ID)

# ===================== RATE-LIMIT / BACKOFF ===================
MIN_INTERVAL = 30  # segundos entre chamadas por usuário (throttling)

def send_with_retry(chat, prompt, max_tries=3, initial_wait=2.0):
    """Envia mensagem com retry/backoff em caso de 429 (quota/limite)."""
    wait = initial_wait
    for i in range(max_tries):
        try:
            return chat.send_message(prompt, stream=True)
        except gexc.ResourceExhausted:
            if i == max_tries - 1:
                raise
            time.sleep(wait)
            wait *= 2
        except Exception:
            raise

def ensure_throttle():
    now = time.time()
    last = st.session_state.get("_last_call_ts", 0)
    if now - last < MIN_INTERVAL:
        falta = int(MIN_INTERVAL - (now - last))
        st.warning(f"Espere {falta}s para a próxima ação (limite de cota).")
        st.stop()
    st.session_state["_last_call_ts"] = now

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

# ====================== PDF → TEXTO helper ====================
def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extrai texto de PDF usando PyPDF2; se falhar, tenta pypdf ou pdfplumber."""
    text = ""
    # PyPDF2
    try:
        import PyPDF2
        from io import BytesIO
        reader = PyPDF2.PdfReader(BytesIO(file_bytes))
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception:
        pass
    # pypdf
    try:
        import pypdf
        from io import BytesIO
        reader = pypdf.PdfReader(BytesIO(file_bytes))
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception:
        pass
    # pdfplumber
    try:
        import pdfplumber
        from io import BytesIO
        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text
    except Exception:
        pass
    return ""

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

    tab_chat, tab_pdf, tab_seo, tab_email = st.tabs(
        ["💬 Chat (Gemini)", "📄 PDF → Texto", "🪄 SEO Optimizer", "📧 Assistente de E-mail"]
    )

    # -------------------------- CHAT -------------------------
    with tab_chat:
        st.subheader("Chat com Gemini")
        with st.expander("Modelos disponíveis (debug)"):
            st.write(AVAILABLE or "—")
            st.write("Usando:", st.session_state.get("_gemini_model_id"))

        model_id = st.session_state["_gemini_model_id"]
        model = genai.GenerativeModel(model_id)

        if "gemini_chat" not in st.session_state:
            st.session_state["gemini_chat"] = model.start_chat(history=[])

        for turn in st.session_state["gemini_chat"].history:
            role = "user" if turn.role == "user" else "assistant"
            with st.chat_message(role):
                st.markdown("".join(getattr(p, "text", "") for p in turn.parts))

        prompt = st.chat_input("Pergunte algo…")
        if prompt:
            ensure_throttle()
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                placeholder = st.empty()
                acc = ""
                try:
                    stream = send_with_retry(st.session_state["gemini_chat"], prompt)
                except gexc.ResourceExhausted:
                    fallback_id = "gemini-1.5-flash"
                    if st.session_state.get("_gemini_model_id") != fallback_id:
                        st.info("Cota atingida no modelo atual. Alternando para gemini-1.5-flash…")
                        st.session_state["_gemini_model_id"] = fallback_id
                        model = genai.GenerativeModel(fallback_id)
                        st.session_state["gemini_chat"] = model.start_chat(history=[])
                        stream = send_with_retry(st.session_state["gemini_chat"], prompt, max_tries=1)
                    else:
                        st.error("Cota atingida. Tente novamente em alguns segundos ou habilite billing no projeto.")
                        stream = None
                if stream:
                    try:
                        for chunk in stream:
                            acc += chunk.text or ""
                            placeholder.markdown(acc)
                    except Exception as e:
                        st.error(f"Erro no Gemini: {e}")

        c1, c2 = st.columns(2)
        if c1.button("🧹 Limpar chat"):
            st.session_state["gemini_chat"] = genai.GenerativeModel(st.session_state["_gemini_model_id"]).start_chat(history=[])
            st.rerun()
        if c2.button("🚪 Sair"):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.info("Sessão encerrada.")
            st.rerun()

    # ---------------------- PDF → TEXTO ----------------------
    with tab_pdf:
        st.subheader("Converter PDF em texto")
        pdf_file = st.file_uploader("Envie um PDF", type=["pdf"])
        if pdf_file is not None:
            data = pdf_file.read()
            with st.spinner("Extraindo texto..."):
                txt = extract_text_from_pdf(data)
            if txt.strip():
                st.success(f"Texto extraído ({len(txt)} caracteres).")
                st.text_area("Conteúdo extraído", txt, height=300)
                st.download_button("⬇️ Baixar .txt", txt, file_name="pdf_texto.txt")
            else:
                st.error("Não foi possível extrair texto deste PDF.")

    # ---------------------- SEO Optimizer --------------------
    with tab_seo:
        st.subheader("Otimização de SEO (Gemini)")

        col1, col2 = st.columns(2)
        idioma = col1.selectbox("Idioma de saída", ["pt-BR", "en-US", "es-ES"], index=0)
        objetivo = col2.selectbox("Objetivo", ["Blog post", "Landing page", "Página de produto", "Anúncio"], index=0)

        col3, col4 = st.columns(2)
        tom = col3.selectbox("Tom do texto", ["Didático", "Neutro", "Persuasivo", "Técnico"], index=0)
        tamanho = col4.selectbox("Tamanho desejado", ["600–900", "800–1200", "1200–1800"], index=1)

        keywords = st.text_input("Palavras-chave (separe por ; )", help="Ex.: tênis Nike; Air Max; Pegasus; tamanho tênis Nike")
        incluir_jsonld = st.checkbox("Sugerir JSON-LD (schema.org)", value=True)

        texto_original = st.text_area("Cole aqui seu texto original (post/artigo/copy)", height=220)

        if st.button("Gerar versão otimizada"):
            if not texto_original.strip():
                st.warning("Cole um texto para otimizar.")
            else:
                ensure_throttle()
                prompt = f"""
Você é um assistente de SEO. Reescreva e estruture o texto a seguir, retornando um JSON com os campos:
title (<=60 chars), meta_description (<=155), slug (kebab-case), h1, h2 (lista),
body_markdown (conteúdo em markdown), keywords_sugeridas (lista), faqs (lista de objetos),
{"json_ld (string JSON-LD)" if incluir_jsonld else "sem json_ld"}.
Idioma: {idioma}. Objetivo: {objetivo}. Tom: {tom}. Tamanho: {tamanho}.
Palavras-chave alvo: {keywords or "(não informado)"}.

TEXTO ORIGINAL:
\"\"\"{texto_original}\"\"\""""

                model = genai.GenerativeModel(st.session_state["_gemini_model_id"])
                try:
                    chat = model.start_chat(history=[])
                    stream = send_with_retry(chat, prompt)
                    full = ""
                    for ch in stream:
                        full += ch.text or ""
                    start = full.find("{")
                    end = full.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        full_json = full[start:end+1]
                    else:
                        full_json = full
                    data = json.loads(full_json)
                except gexc.ResourceExhausted:
                    st.error("Cota atingida. Tente novamente após alguns segundos ou habilite billing.")
                    data = None
                except Exception as e:
                    st.error(f"Falha ao interpretar a resposta do modelo: {e}")
                    st.code(full if 'full' in locals() else "", language="json")
                    data = None

                if data:
                    st.success("Versão otimizada gerada!")
                    st.write("**Title**:", data.get("title", ""))
                    st.write("**Meta description**:", data.get("meta_description", ""))
                    st.write("**Slug**:", data.get("slug", ""))
                    st.write("**H1**:", data.get("h1", ""))
                    st.write("**H2 sugeridos**:", data.get("h2", []))

                    st.markdown("### Corpo reescrito (Markdown)")
                    body_md = data.get("body_markdown", "")
                    st.markdown(body_md)

                    st.markdown("### Keywords sugeridas")
                    st.write(data.get("keywords_sugeridas", []))

                    st.markdown("### FAQs")
                    faqs = data.get("faqs", [])
                    if isinstance(faqs, list) and faqs:
                        for f in faqs:
                            q = f.get("pergunta") or f.get("question") or ""
                            a = f.get("resposta") or f.get("answer") or ""
                            st.markdown(f"**Q:** {q}\n\n**A:** {a}\n")

                    if incluir_jsonld and "json_ld" in data:
                        st.markdown("### JSON-LD sugerido")
                        st.code(data["json_ld"], language="json")

                    st.download_button("⬇️ Baixar corpo (.md)", body_md, file_name="seo_body.md")
                    st.download_button("⬇️ Baixar pacote (.json)", json.dumps(data, ensure_ascii=False, indent=2), file_name="seo_package.json")

    # -------------------- ASSISTENTE DE E-MAIL -------------------
    with tab_email:
        st.subheader("Facilitar leitura e resposta de e-mail")

        colA, colB = st.columns(2)
        tom_email = colA.selectbox("Tom da resposta", ["Formal", "Casual", "Neutro"], index=0)
        objetivo = colB.selectbox("Objetivo", ["Responder dúvidas", "Enviar proposta", "Confirmar recebimento", "Agendar reunião"], index=0)

        assunto_contexto = st.text_input("(Opcional) Assunto do e-mail ou contexto curto")
        email_bruto = st.text_area("Cole aqui o e-mail recebido (texto)", height=250, placeholder="Cole o conteúdo do e-mail que você recebeu…")

        colC, colD = st.columns(2)
        incluir_topicos = colC.checkbox("Gerar tópicos/bullets", value=True)
        tamanho = colD.selectbox("Comprimento da resposta", ["Curta (3–5 linhas)", "Média (1–2 parágrafos)", "Longa (detalhada)"], index=1)

        if st.button("Gerar resumo e rascunho de resposta"):
            if not email_bruto.strip():
                st.warning("Cole o e-mail recebido para gerar o resumo/rascunho.")
            else:
                ensure_throttle()
                prompt_email = f"""
Você é um assistente que ajuda a compreender e responder e-mails de forma clara e eficiente.

Entrada:
- Tom da resposta: {tom_email}
- Objetivo: {objetivo}
- Assunto/contexto (se houver): {assunto_contexto or "(não informado)"}
- Incluir tópicos/bullets: {"sim" if incluir_topicos else "não"}
- Comprimento desejado: {tamanho}

Tarefa:
1) RESUMO do e-mail (pontos, pedidos, prazos, anexos).
2) ASSUNTO sugerido coerente.
3) RASCUNHO DE RESPOSTA no tom indicado, pronto para copiar/colar.
4) 3–5 PERGUNTAS DE FOLLOW-UP.
5) Se "{incluir_topicos}" inclua uma versão em BULLETS.

E-mail recebido:
\"\"\"{email_bruto}\"\"\" 

Formato de saída em JSON:
{{
  "resumo": "...",
  "assunto_sugerido": "...",
  "resposta_sugerida": "...",
  "bullets": ["...","..."],
  "follow_up": ["...","..."]
}}
"""
                try:
                    model = genai.GenerativeModel(st.session_state["_gemini_model_id"])
                    chat = model.start_chat(history=[])
                    stream = send_with_retry(chat, prompt_email)
                    full = ""
                    for ch in stream:
                        full += ch.text or ""
                    start = full.find("{")
                    end = full.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        payload = json.loads(full[start:end+1])
                    else:
                        payload = json.loads(full)

                    st.success("Resumo e rascunho gerados!")

                    st.markdown("### 🧩 Resumo")
                    st.write(payload.get("resumo", ""))

                    st.markdown("### 📨 Assunto sugerido")
                    st.write(payload.get("assunto_sugerido", ""))

                    st.markdown("### ✍️ Resposta sugerida")
                    resposta_txt = payload.get("resposta_sugerida", "")
                    st.text_area("Rascunho (edite antes de copiar)", value=resposta_txt, height=220, key="draft_email")

                    if incluir_topicos:
                        st.markdown("### • Versão em bullets")
                        for item in payload.get("bullets", []):
                            st.markdown(f"- {item}")

                    st.markdown("### ❓ Follow-up")
                    for q in payload.get("follow_up", []):
                        st.markdown(f"- {q}")

                    st.download_button("⬇️ Baixar rascunho (.txt)", data=resposta_txt, file_name="resposta_email.txt")

                except gexc.ResourceExhausted:
                    st.error("Cota atingida. Aguarde alguns segundos ou habilite billing no projeto.")
                except Exception as e:
                    st.error(f"Erro ao gerar o rascunho: {e}")
                    if "full" in locals():
                        st.code(full, language="json")

else:
    st.info("Faça login para acessar o conteúdo.")
