import streamlit as st
import pandas as pd
from supabase import create_client, Client
import re
from datetime import datetime
import PyPDF2

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
    if is_vast: Ei = 0.01 
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

def extract_text_from_pdf(uploaded_file):
    text = ""
    try:
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text: text += page_text + "\n"
    except Exception:
        pass
    return text

def vind_stoffen_in_tekst(text, df):
    gevonden_cas = re.findall(r'[0-9]{2,7}-[0-9]{2}-[0-9]', text)
    gevonden_stoffen = []
    
    for cas in set(gevonden_cas):
        match = df[df['CAS-NUMMER'].astype(str).str.contains(cas, na=False, regex=False)]
        if not match.empty:
            gevonden_stoffen.append(match.iloc[0]['SORTEERNAAM NL'])
            
    for stof in df['SORTEERNAAM NL'].dropna().unique():
        if len(str(stof).strip()) > 3:
            pattern = r'\b' + re.escape(str(stof).lower()) + r'\b'
            if re.search(pattern, text.lower()):
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

with tab1:
    st.header("Document of Protocol Analyseren")
    upload_file = st.file_uploader("Upload PDF of TXT bestand", type=['pdf', 'txt'])
    plak_tekst = st.text_area("Of plak hier direct de tekst (bijv. uit Word):", height=150)
    
    geanalyseerde_tekst = ""
    if upload_file:
        if upload_file.name.endswith('.pdf'): geanalyseerde_tekst = extract_text_from_pdf(upload_file)
        elif upload_file.name.endswith('.txt'): geanalyseerde_tekst = upload_file.read().decode('utf-8')
    elif plak_tekst:
        geanalyseerde_tekst = plak_tekst
        
    if geanalyseerde_tekst:
        st.session_state['protocol_tekst'] = geanalyseerde_tekst 
        
        if st.button("Analyseer Tekst", type="primary"):
            gevonden = vind_stoffen_in_tekst(geanalyseerde_tekst, df_stoffen)
            if gevonden:
                st.success(f"Gevonden stoffen in database: {', '.join(gevonden)}")
                st.session_state['gevonden_stoffen'] = gevonden
                st.info("Alle gevonden stoffen zijn nu geselecteerd in Tab 2. Ga naar Tab 2 om verder te gaan.")
            else:
                st.warning("Geen bekende stoffen gevonden.")

with tab2:
    st.header("Stap 1: Basisgegevens & STOP-strategie")
    
    # Multiselect voor meerdere stoffen
    standaard_stoffen = st.session_state.get('gevonden_stoffen', [])
    gekozen_stoffen = st.multiselect("Stoffen voor dit protocol:", stoffen_lijst, default=standaard_stoffen)
    
    beoordelingen = {}
    
    for stof in gekozen_stoffen:
        st.markdown(f"### ⚙️ Instellingen voor: {stof}")
        stof_data = df_stoffen[df_stoffen['SORTEERNAAM NL'] == stof].iloc[0]
        h_zinnen_db = stof_data.get('H-ZINNEN (TEKST)', 'Geen')
        cas_nummer = stof_data.get('CAS-NUMMER', 'Onbekend')
        
        st.info(f"**CAS:** {cas_nummer} | **H-zinnen:** {h_zinnen_db}")
        
        c1, c2 = st.columns(2)
        with c1:
            is_vast = st.checkbox("Vaste stof", key=f"vast_{stof}")
            dampspanning = st.number_input("Dampdruk (hPa)", value=0.0, key=f"damp_{stof}")
        with c2:
            h = st.selectbox("Hoeveelheid", [("Zeer kleine (max 1g/ml)", 0.03), ("Kleine (max 10g/ml)", 0.1), ("Grote (>10g/ml)", 1.0)], key=f"h_{stof}")
            v = st.selectbox("Ventilatie", [("Zuurkast", 0.03), ("Op tafel", 1)], key=f"v_{stof}")
            d = st.selectbox("Tijdsduur", [("1 tot 30 min", 0.06), ("0.5 tot 2 uur", 0.25)], key=f"d_{stof}")
            f = st.selectbox("Frequentie", [("1 keer per week", 0.2), ("4 - 5 keer per week", 1)], key=f"f_{stof}")

        substitutie = st.text_input("Substitutie:", value="Essentieel voor onderzoek.", key=f"sub_{stof}")
        technisch = st.text_input("Technisch:", value="Werken in zuurkast.", key=f"tech_{stof}")
        organisatorisch = st.text_input("Organisatorisch:", value="Beperkte tijd.", key=f"org_{stof}")
        pbm = st.text_input("PBM:", value="Labjas, bril, handschoenen.", key=f"pbm_{stof}")
        
        st.divider()
        
        beoordelingen[stof] = {
            'cas': cas_nummer, 'h_zinnen': h_zinnen_db, 'damp': dampspanning, 'vast': is_vast,
            'h_val': h, 'v_val': v, 'd_val': d, 'f_val': f,
            'sub': substitutie, 'tech': technisch, 'org': organisatorisch, 'pbm': pbm
        }

with tab3:
    st.markdown(f"## Standard Operating Procedure (SOP)")
    st.markdown(f"**Document-ID:** SOP-{datetime.now().strftime('%Y-%m-%d')}")
    st.markdown("---")
    
    st.markdown("### 1. Werkprotocol / Procedure")
    if 'protocol_tekst' in st.session_state and st.session_state['protocol_tekst']:
        protocol_geannoteerd = st.session_state['protocol_tekst']
        # Markdown regeleindes fixen
        protocol_geannoteerd = protocol_geannoteerd.replace('\n', '\n\n')
        
        # Symbolen plaatsen bij gevonden stoffen
        for stof in gekozen_stoffen:
            patroon = re.compile(re.escape(stof), re.IGNORECASE)
            protocol_geannoteerd = patroon.sub(f"**🧪 {stof}**", protocol_geannoteerd)
            
        st.markdown(protocol_geannoteerd)
    else:
        st.markdown("*Geen protocol geüpload.*")
    
    st.markdown("---")
    st.markdown("### 2. Chemische Risicobeoordelingen")
    
    if not gekozen_stoffen:
        st.markdown("*Geen stoffen geselecteerd in Tab 2.*")
        
    for index, stof in enumerate(gekozen_stoffen):
        b = beoordelingen[stof]
        Ei, score_EM, exp_klasse = bereken_blootstellingsscore(b['damp'], b['vast'], b['h_val'][1], 1, b['v_val'][1], b['d_val'][1], b['f_val'][1])
        coshh = bereken_coshh_klasse(b['h_zinnen'])
        risico_kleur = bepaal_risico_kleur(coshh, exp_klasse)
        
        st.markdown(f"#### 2.{index+1} {stof} (CAS: {b['cas']})")
        st.markdown(f"- **H-zinnen:** {b['h_zinnen']} | **Dampdruk:** {b['damp']} hPa | **COSHH Klasse:** {coshh}")
        st.markdown(f"- **Handeling:** {b['h_val'][0]}, {b['v_val'][0]}")
        
        if "Groen" in risico_kleur: st.success(f"**Blootstellingsklasse {exp_klasse} | Score:** {risico_kleur}")
        elif "Oranje" in risico_kleur: st.warning(f"**Blootstellingsklasse {exp_klasse} | Score:** {risico_kleur}")
        else: st.error(f"**Blootstellingsklasse {exp_klasse} | Score:** {risico_kleur}")

        st.markdown(f"**STOP-strategie:** Substitutie: *{b['sub']}* | Technisch: *{b['tech']}* | Organisatorisch: *{b['org']}* | PBM: *{b['pbm']}*")
        st.write("") 

    st.markdown("---")
    st.markdown("### 3. Calamiteiten & Spill Protocol")
    st.markdown("- **Kleine spill (< 50 ml):** Ruim direct op met absorptiematerialen (spill kit). Gooi afval in chemische afvalbak.")
    st.markdown("- **Grote spill (> 250 ml vluchtige stoffen):** Ruim dit niet zelf op zonder adembescherming. Ontruim het lab, sluit deuren, waarschuw BHV.")
    
    st.divider()
    st.caption("Druk op Ctrl+P of Cmd+P in je browser om dit complete SOP document als PDF op te slaan.")
