# app.py
# =============================================================
# Login + Chat (Gemini) + PDF->Texto + SEO Optimizer
# =============================================================

import os
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader

# ----------------------- CONFIG GERAL ------------------------
st.set_page_config(page_title="Login + Chat + PDF + SEO", page_icon="🔐", layout="centered")
DB_PATH = Path("users.db")

# --------------------- GEMINI (API & MODELO) ----------------
API_KEY = (st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
AVAILABLE: list[str] = []
MODEL_ID: str | None = None

def _configure_gemini() -> None:
    global AVAILABLE, MODEL_ID
    if not API_KEY:
        st.error("Defina GEMINI_API_KEY em Settings → Secrets (Cloud) ou .streamlit/secrets.toml (local).")
        st.stop()
    if not API_KEY.startswith("AIza"):
        st.error("Sua chave não parece válida (deveria começar com 'AIza'). Gere/copie a chave completa no Google AI Studio.")
        st.stop()

    genai.configure(api_key=API_KEY)
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

_configure_gemini()

# -------------------------- DB (SQLite) ----------------------
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
    # DEMO: sha256 (para produção use passlib[bcrypt])
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

# --------------------------- UI BASE -------------------------
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

# =============================================================
# ÁREA LOGADA
# =============================================================
if st.session_state.logged_in:
    st.success(f"✅ Logado como **{st.session_state.username}**.")

    # Três abas: Chat, PDF->Texto e SEO Optimizer
    tab_chat, tab_pdf, tab_seo = st.tabs(["🤖 Chat", "📄 PDF → texto", "🪄 SEO Optimizer"])

    # ----------------------- ABA 1: CHAT ----------------------
    with tab_chat:
        st.header("🤖 Chatbot (Gemini)")

        # Debug de modelos (opcional)
        with st.expander("Modelos disponíveis (debug)"):
            st.write(AVAILABLE or "—")
            st.write("Usando:", st.session_state.get("_gemini_model_id"))

        # Base de conhecimento .txt (fixo + upload)
        contexto_fixo = ""
        try:
            contexto_fixo = Path("data/brand_manual.txt").read_text(encoding="utf-8")
        except FileNotFoundError:
            pass

        uploaded_txt = st.file_uploader("Envie um .txt (opcional) para o chat usar como base", type=["txt"])
        contexto_upload = ""
        if uploaded_txt is not None:
            contexto_upload = uploaded_txt.read().decode("utf-8", errors="ignore")
            st.success(f"Arquivo carregado ({len(contexto_upload)} caracteres).")

        contexto = "\n\n".join(s for s in [contexto_fixo, contexto_upload] if s)

        # Cria modelo com/sem system_instruction
        if contexto:
            instrucoes = (
                "Você é um assistente. Use o texto abaixo como base quando for relevante. "
                "Se a pergunta não estiver respondida pelo material, diga que não encontrou.\n\n"
                f"{contexto}"
            )
            model_chat = genai.GenerativeModel(
                st.session_state["_gemini_model_id"],
                system_instruction=instrucoes,
            )
        else:
            model_chat = genai.GenerativeModel(st.session_state["_gemini_model_id"])

        # Reiniciar chat para aplicar nova base
        if contexto and st.button("Aplicar base e reiniciar chat"):
            st.session_state.pop("gemini_chat", None)
            st.rerun()

        # Sessão do chat
        if "gemini_chat" not in st.session_state:
            st.session_state["gemini_chat"] = model_chat.start_chat(history=[])

        # Histórico
        for turn in st.session_state["gemini_chat"].history:
            role = "user" if turn.role == "user" else "assistant"
            with st.chat_message(role):
                st.markdown("".join(getattr(p, "text", "") for p in turn.parts))

        # Input
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

        # Ações
        c1, c2 = st.columns(2)
        if c1.button("🧹 Limpar chat"):
            st.session_state["gemini_chat"] = model_chat.start_chat(history=[])
            st.rerun()
        if c2.button("🚪 Sair"):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.info("Sessão encerrada.")

    # -------------------- ABA 2: PDF → TEXTO ------------------
    with tab_pdf:
        st.header("📄 PDF → texto")
        pdf = st.file_uploader("Envie um PDF", type=["pdf"])
        if pdf is not None:
            try:
                reader = PdfReader(pdf)
                textos = []
                for i, page in enumerate(reader.pages):
                    txt = page.extract_text() or ""
                    textos.append(f"\n--- Página {i+1} ---\n{txt}")
                full_text = "".join(textos).strip()
                if not full_text:
                    st.warning("Não foi possível extrair texto. Se o PDF for escaneado (imagem), será necessário OCR.")
                st.text_area("Texto extraído", full_text, height=400)
                st.download_button(
                    "⬇️ Baixar .txt",
                    full_text,
                    file_name="pdf_texto.txt",
                    mime="text/plain",
                )
            except Exception as e:
                st.error(f"Erro ao ler PDF: {e}")
        else:
            st.info("Envie um PDF para extrair o texto.")

    # --------------------- ABA 3: SEO OPTIMIZER ---------------
    with tab_seo:
        st.header("🪄 SEO Optimizer (Gemini)")
        st.caption("Cole seu texto e gere uma versão otimizada para SEO: título, meta, slug, headings, corpo reescrito, keywords, FAQs e JSON-LD.")

        colA, colB, colC = st.columns(3)
        with colA:
            idioma = st.selectbox("Idioma de saída", ["pt-BR", "en-US", "es-ES"], index=0)
        with colB:
            objetivo = st.selectbox("Objetivo", ["Blog post", "Landing page", "Product page", "Anúncio"], index=0)
        with colC:
            tom = st.selectbox("Tom", ["neutro", "confiável", "didático", "persuasivo"], index=1)

        kws = st.text_input("Palavras-chave alvo (separadas por vírgula)", placeholder="ex.: login streamlit, chatbot gemini, pdf para texto")
        tamanho = st.slider("Tamanho do texto reescrito (aprox.)", min_value=300, max_value=2000, step=100, value=800)
        incluir_schema = st.checkbox("Sugerir JSON-LD (schema.org)", value=True)

        original = st.text_area("Cole aqui seu texto original (post/artigo/copy)", height=280, placeholder="Cole seu conteúdo completo…")
        gerar = st.button("🚀 Gerar versão otimizada")

        model_seo = genai.GenerativeModel(st.session_state["_gemini_model_id"])

        def make_prompt(texto: str) -> str:
            base_jsonld = ' - "jsonLd": objeto JSON-LD schema.org (WebPage/Article), mínimo title, description, inLanguage, dateModified.' if incluir_schema else ''
            return f"""
Você é um especialista SEO sênior. Reescreva e estruture o conteúdo abaixo em **{idioma}**,
otimizando para SEO sem perder a clareza e a naturalidade humana. Contexto:
- Objetivo da página: {objetivo}
- Tom desejado: {tom}
- Tamanho aproximado do corpo reescrito: ~{tamanho} palavras
- Palavras-chave alvo (quando houver): {kws or "não informado"}

Entregue como **JSON** com as chaves (obrigatórias):
- "title": título SEO (<= 60 chars, CTR-friendly)
- "metaDescription": meta description (<= 155 chars, com benefício claro)
- "slug": slug curto e legível (kebab-case)
- "h1": heading principal
- "h2": array com 3–8 subtítulos sugeridos
- "body": corpo reescrito (markdown simples, com H2/H3 quando útil)
- "keywords": array com 5–12 termos/variações
- "faqs": array de objetos {{ "pergunta": "...", "resposta": "..." }}
{base_jsonld}

Regras:
- Não invente fatos. Se faltar info, seja genérico ou omita.
- Evite keyword stuffing. Priorize legibilidade, escaneabilidade e intenção.
- Melhore o copy com microbenefícios e CTAs sutis, quando fizer sentido.
- Em português do Brasil quando idioma for pt-BR.

### CONTEÚDO ORIGINAL
{texto}
"""

        if gerar:
            if not original.strip():
                st.warning("Cole um texto primeiro.")
            else:
                with st.spinner("Gerando versão otimizada…"):
                    try:
                        resp = model_seo.generate_content(make_prompt(original))
                        raw = (resp.text or "").strip()
                    except Exception as e:
                        st.error(f"Erro ao chamar o modelo: {e}")
                        raw = ""

                if not raw:
                    st.stop()

                # Parse do JSON
                cleaned = raw
                if "```" in cleaned:
                    parts = cleaned.split("```")
                    # tenta pegar o bloco interno
                    cleaned = parts[1] if len(parts) >= 2 else cleaned.replace("```", "")
                try:
                    data = json.loads(cleaned)
                except Exception:
                    # fallback: tenta localizar { ... }
                    try:
                        start = cleaned.find("{")
                        end = cleaned.rfind("}") + 1
                        data = json.loads(cleaned[start:end])
                    except Exception:
                        st.error("Não foi possível interpretar o JSON retornado. Mostrando texto bruto abaixo.")
                        st.code(raw, language="json")
                        data = None

                if data:
                    st.subheader("🎯 Resultado SEO")
                    c1, c2 = st.columns([3, 2])
                    with c1:
                        st.markdown(f"**Title**: {data.get('title','')}")
                        st.markdown(f"**Meta description**: {data.get('metaDescription','')}")
                        st.markdown(f"**Slug**: `/{data.get('slug','')}`")
                        st.markdown(f"**H1**: {data.get('h1','')}")
                    with c2:
                        st.markdown("**Keywords sugeridas:**")
                        st.write(data.get("keywords", []))

                    st.markdown("**H2 sugeridos:**")
                    for h in data.get("h2", []):
                        st.markdown(f"- {h}")

                    st.markdown("**Corpo reescrito (markdown):**")
                    st.write(data.get("body", ""))

                    faqs = data.get("faqs", [])
                    if faqs:
                        st.markdown("**FAQs sugeridas:**")
                        for faq in faqs:
                            st.markdown(f"- **{faq.get('pergunta','')}** — {faq.get('resposta','')}")

                    if incluir_schema and data.get("jsonLd"):
                        st.markdown("**JSON-LD (schema.org):**")
                        st.code(json.dumps(data["jsonLd"], ensure_ascii=False, indent=2), language="json")

                    # Downloads
                    from datetime import datetime as _dt
                    stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                    body_txt = data.get("body", "")
                    json_txt = json.dumps(data, ensure_ascii=False, indent=2)

                    st.download_button(
                        "⬇️ Baixar corpo reescrito (.md)",
                        body_txt,
                        file_name=f"seo_body_{stamp}.md",
                        mime="text/markdown",
                    )
                    st.download_button(
                        "⬇️ Baixar pacote SEO (.json)",
                        json_txt,
                        file_name=f"seo_package_{stamp}.json",
                        mime="application/json",
                    )

else:
    st.info("Faça login para acessar o conteúdo.")
