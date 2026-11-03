import streamlit as st
import pandas as pd
from supabase import create_client, Client
import re
from datetime import datetime
import PyPDF2
import io

st.set_page_config(page_title="SOFOS SOP Generator", layout="wide")

# --- 1. Inlogscherm ---
def check_password():
    def password_entered():
        if st.session_state["username"] == "admin" and st.session_state["password"] == "veilig2024":
            st.session_state["password_correct"] = True
            del st.session_state["password"] 
        else:
            st.session_state["password_correct"] = False
    if "password_correct" not in st.session_state:
        st.header("SOFOS - Login")
        st.text_input("Gebruikersnaam", key="username")
        st.text_input("Wachtwoord", type="password", key="password", on_change=password_entered)
        return False
    elif not st.session_state["password_correct"]:
        st.error("Incorrect.")
        st.text_input("Gebruikersnaam", key="username")
        st.text_input("Wachtwoord", type="password", key="password", on_change=password_entered)
        return False
    return True

if not check_password():
    st.stop()

# --- 2. Database & Rekenlogica ---
@st.cache_data(ttl=600)
def laad_chemicalien():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)
    
    all_data = []
    offset = 0
    limit = 1000
    try:
        while True:
            response = supabase.table('chemicalien').select("*").range(offset, offset + limit - 1).execute()
            data = response.data
            if not data: break
            all_data.extend(data)
            if len(data) < limit: break
            offset += limit
        return pd.DataFrame(all_data)
    except Exception:
        return pd.DataFrame()

def bereken_blootstellingsscore(dampspanning, is_vast, hoeveelheid, locatie, ventilatie, duur, frequentie):
    if is_vast:
        Ei = 0.01 
    else:
        if dampspanning >= 800: Ei = 1.00000
        elif dampspanning >= 200: Ei = 0.80000
        elif dampspanning >= 50: Ei = 0.19000
        else: Ei = 0.02267 
        
    score_em = Ei * hoeveelheid * locatie * ventilatie * duur * frequentie
    
    if score_em <= 0.00002: klasse = 1
    elif score_em <= 0.002: klasse = 2
    elif score_em <= 0.2: klasse = 3
    else: klasse = 4
    return Ei, score_em, klasse

def bereken_coshh_klasse(h_zinnen_tekst):
    if not h_zinnen_tekst or pd.isna(h_zinnen_tekst): return "A"
    h_zinnen = str(h_zinnen_tekst).upper()
    h_lijst = re.findall(r'H\d{3}[A-Z]*', h_zinnen)
    hoogste_klasse = "A"
    
    klasse_E = ["H340", "H350", "H350I", "H330"] 
    klasse_D = ["H314", "H318", "H334", "H341", "H351", "H360", "H372"]
    klasse_C = ["H311", "H314", "H315", "H319", "H320", "H331", "H335", "H361", "H362", "H370", "H373"]
    klasse_B = ["H200", "H220", "H221", "H222", "H223", "H224", "H225", "H226", "H228", "H300", "H301", "H302", "H303", "H304", "H305", "H310", "H311", "H312", "H313", "H315", "H316", "H317", "H319", "H320", "H331", "H332", "H333", "H334", "H335", "H336", "H371", "H400"]
    
    for h in h_lijst:
        if h in klasse_E: return "E"
        if h in klasse_D and hoogste_klasse not in ["E"]: hoogste_klasse = "D"
        if h in klasse_C and hoogste_klasse not in ["E", "D"]: hoogste_klasse = "C"
        if h in klasse_B and hoogste_klasse not in ["E", "D", "C"]: hoogste_klasse = "B"
    return hoogste_klasse

def bepaal_risico_kleur(coshh, exp_klasse):
    if coshh == "E" or exp_klasse >= 3: return "Hoog (Rood)"
    if exp_klasse == 1 and coshh in ["A", "B"]: return "Laag (Groen)"
    return "Midden (Oranje)"

# --- Nieuwe Module: Tekst & PDF Extractie ---
def extract_text_from_pdf(uploaded_file):
    text = ""
    try:
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    except Exception as e:
        st.error(f"Fout bij lezen PDF: {e}")
    return text

def vind_stoffen_in_tekst(text, df):
    gevonden_cas = re.findall(r'[0-9]{2,7}-[0-9]{2}-[0-9]', text)
    gevonden_stoffen = []
    
    # Zoek primair op CAS nummer
    for cas in set(gevonden_cas):
        match = df[df['CAS-NUMMER'].astype(str).str.contains(cas, na=False, regex=False)]
        if not match.empty:
            gevonden_stoffen.append(match.iloc[0]['SORTEERNAAM NL'])
            
    # Zoek secundair op exacte stofnaam als er geen CAS is (Basic NER)
    if not gevonden_stoffen:
        for stof in df['SORTEERNAAM NL'].dropna().unique():
            if str(stof).lower() in text.lower():
                gevonden_stoffen.append(stof)
                
    return list(set(gevonden_stoffen))


# --- 3. De Interface ---
df_stoffen = laad_chemicalien()
if df_stoffen.empty:
    st.error("Databasefout. Controleer je Secrets.")
    st.stop()

stoffen_lijst = [s for s in df_stoffen['SORTEERNAAM NL'].unique() if pd.notnull(s)]
stoffen_lijst.sort()

tab1, tab2, tab3 = st.tabs(["📄 NLP Protocol Upload", "📝 Stap 1-3: Invoer & Risicobeoordeling", "🖨️ Stap 4: SOP Document"])

# --- TAB 1: NLP Protocol Lezer ---
with tab1:
    st.header("Document of Protocol Analyseren")
    st.markdown("Upload een SDS (PDF) of protocol, of plak de tekst. Het systeem zoekt automatisch naar CAS-nummers en stofnamen in de database.")
    
    upload_file = st.file_uploader("Upload PDF of TXT bestand", type=['pdf', 'txt'])
    plak_tekst = st.text_area("Of plak hier direct de tekst (bijv. uit Word):", height=150)
    
    geanalyseerde_tekst = ""
    if upload_file:
        if upload_file.name.endswith('.pdf'):
            geanalyseerde_tekst = extract_text_from_pdf(upload_file)
        elif upload_file.name.endswith('.txt'):
            geanalyseerde_tekst = upload_file.read().decode('utf-8')
    elif plak_tekst:
        geanalyseerde_tekst = plak_tekst
        
    if geanalyseerde_tekst:
        if st.button("Analyseer Tekst", type="primary"):
            gevonden = vind_stoffen_in_tekst(geanalyseerde_tekst, df_stoffen)
            if gevonden:
                st.success(f"Gevonden stoffen in database: {', '.join(gevonden)}")
                st.session_state['voorgeselecteerde_stof'] = gevonden[0]
                st.info("De eerste gevonden stof is nu automatisch geselecteerd in Tab 2. Ga naar Tab 2 om verder te gaan.")
            else:
                st.warning("Geen bekende stoffen of CAS-nummers uit de database gevonden in deze tekst.")

# --- TAB 2: Handmatige Invoer ---
with tab2:
    st.header("Stap 1: Basisgegevens")
    
    standaard_index = 0
    if 'voorgeselecteerde_stof' in st.session_state:
        try:
            standaard_index = stoffen_lijst.index(st.session_state['voorgeselecteerde_stof'])
        except ValueError:
            pass
            
    gekozen_stof = st.selectbox("Zoek of selecteer een stof", stoffen_lijst, index=standaard_index)
    stof_data = df_stoffen[df_stoffen['SORTEERNAAM NL'] == gekozen_stof].iloc[0]
    
    h_zinnen_db = stof_data.get('H-ZINNEN (TEKST)', 'Geen')
    cas_nummer = stof_data.get('CAS-NUMMER', 'Onbekend')
    st.info(f"**CAS:** {cas_nummer} | **H-zinnen:** {h_zinnen_db}")
    
    col1, col2 = st.columns(2)
    with col1:
        is_vast = st.checkbox("Vaste stof (poeder/korrels)", value=False)
        dampspanning = st.number_input("Dampdruk (hPa) uit SDS", value=0.0)
    with col2:
        h = st.selectbox("Hoeveelheid", [("Zeer kleine hoeveelheden (max 1g/ml)", 0.03), ("Kleine hoeveelheden (max 10g/ml)", 0.1), ("Grote hoeveelheden (>10g/ml)", 1.0)])
        v = st.selectbox("Ventilatie", [("Werk in een zuurkast", 0.03), ("Werk op tafel", 1)])
        d = st.selectbox("Tijdsduur", [("1 tot 30 minuten per keer", 0.06), ("0.5 tot 2 uur per keer", 0.25)])
        f = st.selectbox("Frequentie", [("1 keer per week", 0.2), ("4 - 5 keer per week", 1)])

    st.header("Stap 3: Personaliseer (STOP-strategie)")
    st.markdown("Vul de gele velden in om te onderbouwen waarom dit veilig is.")
    substitutie = st.text_area("Substitutie: Waarom is vervanging niet mogelijk?", value="Essentieel voor het onderzoeksproject.")
    technisch = st.text_area("Technische maatregelen:", value="Werken in een zuurkast is verplicht.")
    organisatorisch = st.text_area("Organisatorische maatregelen:", value=f"Tijdsduur beperkt tot {d[0]}.")
    pbm = st.text_area("Persoonlijke bescherming (PBM):", value="Veiligheidsbril, labjas en nitril handschoenen.")

# --- TAB 3: PDF / Document generatie ---
with tab3:
    Ei, score_EM, exp_klasse = bereken_blootstellingsscore(dampspanning, is_vast, h[1], 1, v[1], d[1], f[1])
    coshh = bereken_coshh_klasse(h_zinnen_db)
    risico_kleur = bepaal_risico_kleur(coshh, exp_klasse)
    
    st.markdown(f"## Standard Operating Procedure (SOP)")
    st.markdown(f"**Document-ID:** SOP-{datetime.now().strftime('%Y-%m-%d')}-{cas_nummer}")
    st.markdown("---")
    
    st.markdown(f"### 1. Chemische Risicobeoordeling: {gekozen_stof}")
    st.markdown("Specifieke veiligheidsbeoordeling gebaseerd op de Control of Substances Hazardous to Health (COSHH) methodiek.")
    
    st.markdown("#### 1.1 Basisgegevens & Gevarenklasse")
    st.markdown(f"- **CAS Nummer:** {cas_nummer}")
    st.markdown(f"- **H-zinnen (Rubriek 2):** {h_zinnen_db}")
    st.markdown(f"- **Dampdruk (Rubriek 9):** {dampspanning} hPa")
    st.markdown(f"- **COSHH Gevarenklasse:** {coshh}")
    
    st.markdown("#### 1.2 Blootstellingsklasse & Risicoprioriteit")
    st.markdown(f"- **Handeling:** {h[0]}, {v[0]}")
    st.markdown(f"- **Blootstellingsklasse:** {exp_klasse} *(Score: {score_EM:.7f})*")
    
    if "Groen" in risico_kleur: st.success(f"**Risicomatrix Score:** {risico_kleur}")
    elif "Oranje" in risico_kleur: st.warning(f"**Risicomatrix Score:** {risico_kleur}")
    else: st.error(f"**Risicomatrix Score:** {risico_kleur}")

    st.markdown("#### 1.3 STOP-strategie Maatregelen")
    st.markdown(f"- **Substitutie:** {substitutie}")
    st.markdown(f"- **Technische maatregelen:** {technisch}")
    st.markdown(f"- **Organisatorische maatregelen:** {organisatorisch}")
    st.markdown(f"- **Persoonlijke bescherming:** {pbm}")
    
    st.markdown("#### 1.4 Calamiteiten & Spill Protocol")
    st.markdown("- **Kleine spill (< 50 ml):** Ruim direct op met behulp van de aanwezige absorptiematerialen (spill kit). Gooi het afval in de chemische afvalbak.")
    st.markdown("- **Grote spill (> 250 ml van vluchtige stoffen):** Probeer dit niet zelf op te ruimen zonder adembescherming. Ontruim het lab, sluit de deuren en waarschuw de BHV.")
    
    st.divider()
    st.caption("Druk op Ctrl+P of Cmd+P in je browser om dit protocol als PDF op te slaan.")
