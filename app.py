# app.py
# MVP Streamlit — Trasformazione linguistica schede tecniche (Edilizia)
# Ritocchi MVP-safe: contatore, reset, controllo rischi (parole vietate)
#
# Requisiti: streamlit, openai
# Avvio: streamlit run app.py
# API key: esporta OPENAI_API_KEY nell'ambiente

import os
import re
import streamlit as st

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from io import BytesIO
from openai import OpenAI

api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if not api_key:
    st.error("Manca OPENAI_API_KEY (Secrets su Streamlit Cloud o variabile ambiente in locale).")
    st.stop()
client = OpenAI(api_key=api_key)

# -----------------------------
# Config
# -----------------------------
st.set_page_config(page_title="Trasformatore schede tecniche – Edilizia", layout="wide")

api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if not api_key:
    st.warning("Variabile d'ambiente OPENAI_API_KEY non trovata. Impostala prima di avviare l'app.")
    st.stop()

client = OpenAI(api_key=api_key)

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.25"))

MAX_INPUT_CHARS = 20000       # hard cap (evita prompt enormi)
SOFT_WARN_CHARS = 12000       # warning "stai esagerando" (modificabile)
SHOW_RISK_BOX_DEFAULT = True  # di default mostra controllo rischi

# -----------------------------
# Prompt Engine (BLOCCHI FISSI)
# -----------------------------
PROMPT_BASE_SYSTEM = """Agisci come un sistema di trasformazione linguistica specializzato in testi tecnici
del settore edilizia e materiali per l’edilizia.

Il tuo compito NON è migliorare il prodotto, NON è interpretare dati tecnici,
NON è fornire consulenza normativa.

Il tuo unico compito è riscrivere il testo fornito adattandone il linguaggio
al destinatario indicato, mantenendo la piena integrità del contenuto.
"""

PROMPT_PRINCIPI = """Principi obbligatori:
- Non modificare, stimare o reinterpretare dati tecnici.
- Non aggiungere norme, certificazioni o conformità se non presenti nel testo.
- Non introdurre promesse, garanzie o prestazioni implicite.
- Se un’informazione non è presente nel testo, NON deve comparire nell’output.
- È consentito omettere informazioni, ma non crearne di nuove.
"""

PROMPT_SICUREZZA = """Regole di sicurezza:
- Evita superlativi e linguaggio promozionale.
- Evita termini come: garantisce, certificato, conforme, ottimale, ideale,
  innovativo, performante, migliore, massimo.
- Usa un linguaggio prudente, descrittivo e verificabile.
- Non suggerire benefici normativi o prestazionali.
"""

PROMPT_OUTPUT = {
    "Cliente finale": """Tipo di output: CLIENTE FINALE
Regole specifiche:
- Usa linguaggio semplice e comprensibile.
- Elimina numeri tecnici, sigle e parametri.
- Spiega a cosa serve il prodotto e in quali contesti si utilizza.
- Concentrati sugli effetti pratici, non sulle prestazioni tecniche.
- Usa frasi brevi e lessico comune.
""",
    "Commerciale": """Tipo di output: COMMERCIALE
Regole specifiche:
- Mantieni i dati tecnici presenti nel testo originale.
- Descrivi le applicazioni e i contesti d’uso.
- Usa linguaggio professionale e neutro.
- Non trasformare i dati in promesse o vantaggi impliciti.
""",
    "Capitolato": """Tipo di output: CAPITOLATO
Regole specifiche:
- Usa linguaggio tecnico e impersonale.
- Riporta i dati così come sono, senza interpretarli.
- Evita qualsiasi enfasi commerciale.
- Struttura il testo in modo ordinato e descrittivo.
""",
}

FINAL_INSTRUCTION = "Produci esclusivamente il testo trasformato. Non aggiungere commenti, spiegazioni o note."

def build_user_prompt(output_type: str, text: str) -> str:
    return "\n\n".join([
        PROMPT_PRINCIPI.strip(),
        PROMPT_SICUREZZA.strip(),
        PROMPT_OUTPUT[output_type].strip(),
        "Testo da trasformare:\n<<<\n" + text.strip() + "\n>>>",
        FINAL_INSTRUCTION
    ])

# -----------------------------
# Rischi: parole vietate + evidenza
# -----------------------------
BANNED_WORDS = [
    "garantisce", "garanzia", "certificato", "certificata", "conforme", "conformità",
    "ottimale", "ideale", "innovativo", "innovativa", "performante", "migliore", "massimo"
]

def find_banned_words(text: str):
    """Ritorna lista di parole vietate trovate (uniche) + conteggio."""
    found = {}
    lower = text.lower()
    for w in BANNED_WORDS:
        # match parola intera (con accenti e apostrofi gestiti basic)
        pattern = r"\b" + re.escape(w.lower()) + r"\b"
        hits = re.findall(pattern, lower)
        if hits:
            found[w] = len(hits)
    return found

def highlight_banned_words(text: str):
    """Evidenzia parole vietate in HTML (solo display, non modifica contenuto logico)."""
    def repl(match):
        word = match.group(0)
        return f"<mark>{word}</mark>"
    # evidenzia tutte le occorrenze, case-insensitive
    for w in sorted(BANNED_WORDS, key=len, reverse=True):
        pattern = re.compile(rf"\b({re.escape(w)})\b", re.IGNORECASE)
        text = pattern.sub(repl, text)
    return text

# -----------------------------
# Session state (per reset puliti)
# -----------------------------
if "input_text" not in st.session_state:
    st.session_state.input_text = ""
if "output_text" not in st.session_state:
    st.session_state.output_text = ""
if "output_type" not in st.session_state:
    st.session_state.output_type = "Cliente finale"
if "show_risks" not in st.session_state:
    st.session_state.show_risks = SHOW_RISK_BOX_DEFAULT
if "just_generated" not in st.session_state:
    st.session_state["just_generated"] = False

# -----------------------------
# Crea PDF
# -----------------------------
def create_pdf(text: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    styles = getSampleStyleSheet()
    story = []

    for line in text.split("\n"):
        story.append(Paragraph(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), styles["Normal"]))
        story.append(Spacer(1, 8))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()




# -----------------------------
# UI
# -----------------------------
st.title("Trasformatore linguistico per schede tecniche – Edilizia (MVP)")

with st.sidebar:
    st.subheader("Impostazioni (MVP)")
    model = st.text_input("Modello", value=DEFAULT_MODEL)
    temperature = st.slider("Temperature", 0.0, 1.0, float(DEFAULT_TEMPERATURE), 0.05)
    st.checkbox("Modalità 'Rischi' (parole vietate)", key="show_risks")
    st.caption("Consiglio: temperature 0.2–0.3 per ridurre variabilità.")

col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("Input")

    # Pulsanti reset input
    c1a, c1b = st.columns([1, 1])
    with c1a:
        if st.button("Pulisci input", use_container_width=True):
            st.session_state.input_text = ""
    with c1b:
        st.caption("")  # spazio

    st.text_area(
        "Incolla qui il testo tecnico originale",
        height=420,
        placeholder="Incolla qui la scheda tecnica / descrizione / capitolato…",
        key="input_text",
    )

    # Contatore caratteri + warning
    input_len = len(st.session_state.input_text)
    st.caption(f"Caratteri input: {input_len:,} / {MAX_INPUT_CHARS:,}")
    if input_len > SOFT_WARN_CHARS and input_len <= MAX_INPUT_CHARS:
        st.warning("Input molto lungo: per l'MVP conviene incollare solo le sezioni davvero utili (descrizione + dati + impiego).")
    if input_len > MAX_INPUT_CHARS:
        st.error("Input oltre il limite dell'MVP. Riduci il testo (taglia parti ripetitive / 'voce di capitolato' già pronta).")

    st.radio("Seleziona output", ["Cliente finale", "Commerciale", "Capitolato"], horizontal=True, key="output_type")

    generate = st.button("Genera testo", type="primary", use_container_width=True, disabled=(input_len == 0 or input_len > MAX_INPUT_CHARS))

with col2:
    st.subheader("Output")

    # Pulsanti reset output
    c2a, c2b = st.columns([1, 1])
    with c2a:
        if st.button("Pulisci output", use_container_width=True):
            st.session_state.output_text = ""
    with c2b:
        st.caption("")

        if st.session_state.get("just_generated", False):
            st.success("Fatto.")
            st.session_state["just_generated"] = False




    # Output principale
    st.code(st.session_state.output_text or "", language="markdown")
    st.caption(f"Lunghezza output: {len(st.session_state.output_text or '')} caratteri")

    # ⬇️ Scarica PDF
    if st.session_state.output_text.strip():
        pdf_bytes = create_pdf(st.session_state.output_text)

        st.download_button(
            label="⬇️ Scarica PDF",
            data=pdf_bytes,
            file_name="output_scheda_tecnica.pdf",
            mime="application/pdf"
        )

    # Controllo rischi
    if st.session_state.show_risks and st.session_state.output_text.strip():
        st.markdown("---")
        st.subheader("Controllo rischi (MVP)")
        found = find_banned_words(st.session_state.output_text)


        if not found:
            st.success("Nessuna parola vietata trovata.")
        else:
            st.error("Trovate parole potenzialmente rischiose nell'output (da rivedere):")
            # lista parole + conteggio
            for w, n in found.items():
                st.write(f"- **{w}**: {n}")

            # evidenziazione
            highlighted = highlight_banned_words(st.session_state.output_text)
            st.markdown(
                f"<div style='padding:10px;border:1px solid #ddd;border-radius:8px'>{highlighted}</div>",
                unsafe_allow_html=True
            )

# -----------------------------
# Action
# -----------------------------
if generate:
    text = st.session_state.input_text.strip()
    out_type = st.session_state.output_type

    user_prompt = build_user_prompt(out_type, text)

    with st.spinner("Generazione in corso…"):
        try:
           resp = client.chat.completions.create(
               model=model,
               temperature=temperature,
               messages=[
                   {"role": "system", "content": PROMPT_BASE_SYSTEM},
                   {"role": "user", "content": user_prompt},
               ],
           )
           content = resp.choices[0].message.content
           result = (content or "").strip()

           # Debug minimo se arriva vuoto
           if not result:
               st.warning("L'API ha restituito un output vuoto. Mostro un debug minimo sotto.")
               st.write("Model:", model)
               st.write("Finish reason:", resp.choices[0].finish_reason)
               st.write("Raw content is None?:", content is None)

        except Exception as e:
           st.error(f"Errore durante la generazione: {e}")
           st.stop()


    st.session_state.output_text = result
    st.session_state["just_generated"] = True
    st.rerun()

    st.success("Fatto. Controlla rapidamente l'output (soprattutto se compaiono parole 'rischiose').")
