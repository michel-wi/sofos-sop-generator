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

def check_cmr_status(h_zinnen_tekst, db_cmr_val):
    # Check database vinkje
    db_is_cmr = 'aangevinkt' in str(db_cmr_val).strip().lower() or 'true' in str(db_cmr_val).strip().lower() or '1' in str(db_cmr_val).strip().lower()
    
    # Automatische H-zinnen scan voor CMR en verdachte stoffen (H340, H350, H350i, H360, H341, H351, H361)
    h_zinnen = str(h_zinnen_tekst).upper()
    cmr_h_zinnen = ["H340", "H350", "H350I", "H360", "H341", "H351", "H361"]
    h_found = any(h in h_zinnen for h in cmr_h_zinnen)
    
    return db_is_cmr or h_found

def bepaal_risico_kleur(coshh, exp_klasse, is_cmr, lang="NL"):
    if is_cmr or coshh == "E" or exp_klasse >= 3:
        return "Hoog (Rood / CMR Zorgwekkend)" if lang == "NL" else "High (Red / CMR Concern)"
    if exp_klasse == 1 and coshh in ["A", "B"]:
        return "Laag (Groen)" if lang == "NL" else "Low (Green)"
    return "Midden (Oranje)" if lang == "NL" else "Medium (Orange)"

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
    gevonden_stoffen = []
    text_lower = text.lower()
    
    for stof in df['SORTEERNAAM NL'].dropna().unique():
        stof_str = str(stof).strip()
        if len(stof_str) > 2:
            # Strikte hele-woord matching met woordgrenzen (\b) om valse positieven te voorkomen
            pattern = r'\b' + re.escape(stof_str.lower()) + r'\b'
            if re.search(pattern, text_lower):
                gevonden_stoffen.append(stof)
                
    return list(set(gevonden_stoffen))

# --- 3. De Interface & Taalkeuze ---
st.sidebar.header("⚙️ Instellingen / Settings")
taal = st.sidebar.selectbox("Taal / Language", ["Nederlands", "English"])
lang = "NL" if taal == "Nederlands" else "EN"

df_stoffen = laad_chemicalien()
if df_stoffen.empty:
    st.error("Databasefout. Controleer je Secrets.")
    st.stop()

stoffen_lijst = [s for s in df_stoffen['SORTEERNAAM NL'].unique() if pd.notnull(s)]
stoffen_lijst.sort()

if lang == "NL":
    tab1, tab2, tab3 = st.tabs(["📄 NLP Protocol Upload", "📝 Stap 1-3: Invoer & Risicobeoordeling", "🖨️ Stap 4: SOP Document"])
else:
    tab1, tab2, tab3 = st.tabs(["📄 NLP Protocol Upload", "📝 Step 1-3: Input & Risk Assessment", "🖨️ Step 4: SOP Document"])

with tab1:
    st.header("Document of Protocol Analyseren" if lang == "NL" else "Analyze Document or Protocol")
    upload_file = st.file_uploader("Upload PDF of TXT bestand" if lang == "NL" else "Upload PDF or TXT file", type=['pdf', 'txt'])
    plak_tekst = st.text_area("Of plak hier direct de tekst (bijv. uit Word):" if lang == "NL" else "Or paste text directly here (e.g., from Word):", height=150)
    
    geanalyseerde_tekst = ""
    if upload_file:
        if upload_file.name.endswith('.pdf'): geanalyseerde_tekst = extract_text_from_pdf(upload_file)
        elif upload_file.name.endswith('.txt'): geanalyseerde_tekst = upload_file.read().decode('utf-8')
    elif plak_tekst:
        geanalyseerde_tekst = plak_tekst
        
    if geanalyseerde_tekst:
        st.session_state['protocol_tekst'] = geanalyseerde_tekst 
        
        btn_text = "Analyseer Tekst" if lang == "NL" else "Analyze Text"
        if st.button(btn_text, type="primary"):
            gevonden = vind_stoffen_in_tekst(geanalyseerde_tekst, df_stoffen)
            if gevonden:
                if lang == "NL":
                    st.success(f"Gevonden stoffen in database: {', '.join(gevonden)}")
                    st.info("Alle gevonden stoffen zijn nu geselecteerd in Tab 2.")
                else:
                    st.success(f"Found substances in database: {', '.join(gevonden)}")
                    st.info("All found substances are now selected in Step 2.")
                st.session_state['gevonden_stoffen'] = gevonden
            else:
                st.warning("Geen bekende stoffen gevonden." if lang == "NL" else "No known substances found.")

with tab2:
    st.header("Stap 1: Basisgegevens & STOP-strategie" if lang == "NL" else "Step 1: Basic Data & STOP Strategy")
    
    standaard_stoffen = st.session_state.get('gevonden_stoffen', [])
    select_label = "Stoffen voor dit protocol:" if lang == "NL" else "Substances for this protocol:"
    gekozen_stoffen = st.multiselect(select_label, stoffen_lijst, default=standaard_stoffen)
    
    beoordelingen = {}
    
    for stof in gekozen_stoffen:
        stof_data = df_stoffen[df_stoffen['SORTEERNAAM NL'] == stof].iloc[0]
        h_zinnen_db = stof_data.get('H-ZINNEN (TEKST)', 'Geen')
        cas_nummer = stof_data.get('CAS-NUMMER', 'Onbekend')
        db_cmr = stof_data.get('CMR', '')
        
        # Automatische CMR check (database vinkje + automatische H-zinnen scan)
        is_cmr = check_cmr_status(h_zinnen_db, db_cmr)
        
        st.markdown(f"### ⚙️ {stof}")
        if is_cmr:
            msg = f"⚠️ **Let op:** Deze stof is herkend als **CMR-stof** (via database of H-zinnen H340/H350/H360)!" if lang == "NL" else f"⚠️ **Notice:** This substance is recognized as a **CMR substance** (via database or H-statements H340/H350/H360)!"
            st.error(msg)
        
        st.info(f"**CAS:** {cas_nummer} | **H-zinnen:** {h_zinnen_db}")
        
        c1, c2 = st.columns(2)
        with c1:
            is_vast = st.checkbox("Vaste stof" if lang == "NL" else "Solid substance", key=f"vast_{stof}")
            dampspanning = st.number_input("Dampdruk (hPa)" if lang == "NL" else "Vapor pressure (hPa)", value=0.0, key=f"damp_{stof}")
        with c2:
            h = st.selectbox("Hoeveelheid" if lang == "NL" else "Quantity", [("Zeer kleine (max 1g/ml)" if lang=="NL" else "Very small (max 1g/ml)", 0.03), ("Kleine (max 10g/ml)" if lang=="NL" else "Small (max 10g/ml)", 0.1), ("Grote (>10g/ml)" if lang=="NL" else "Large (>10g/ml)", 1.0)], key=f"h_{stof}")
            v = st.selectbox("Ventilatie" if lang == "NL" else "Ventilation", [("Zuurkast" if lang=="NL" else "Fume hood", 0.03), ("Op tafel" if lang=="NL" else "On table", 1)], key=f"v_{stof}")
            d = st.selectbox("Tijdsduur" if lang == "NL" else "Duration", [("1 tot 30 min", 0.06), ("0.5 tot 2 uur" if lang=="NL" else "0.5 to 2 hours", 0.25)], key=f"d_{stof}")
            f = st.selectbox("Frequentie" if lang == "NL" else "Frequency", [("1 keer per week" if lang=="NL" else "Once a week", 0.2), ("4 - 5 keer per week" if lang=="NL" else "4 - 5 times a week", 1)], key=f"f_{stof}")

        sub_label = "Substitutie motivatie (Verplicht bij CMR):" if lang == "NL" else "Substitution motivation (Mandatory for CMR):"
        sub_val = "Essentieel voor onderzoek, alternatief niet mogelijk." if lang == "NL" else "Essential for research, alternative not possible."
        substitutie = st.text_input(sub_label, value=sub_val, key=f"sub_{stof}")
        technisch = st.text_input("Technisch:" if lang == "NL" else "Technical:", value="Werken in zuurkast." if lang=="NL" else "Work in fume hood.", key=f"tech_{stof}")
        organisatorisch = st.text_input("Organisatorisch:" if lang == "NL" else "Organizational:", value="Beperkte tijd." if lang=="NL" else "Limited time.", key=f"org_{stof}")
        pbm = st.text_input("PBM:" if lang == "NL" else "PPE:", value="Labjas, bril, handschoenen." if lang=="NL" else "Lab coat, goggles, gloves.", key=f"pbm_{stof}")
        
        st.divider()
        
        beoordelingen[stof] = {
            'cas': cas_nummer, 'h_zinnen': h_zinnen_db, 'damp': dampspanning, 'vast': is_vast,
            'h_val': h, 'v_val': v, 'd_val': d, 'f_val': f, 'is_cmr': is_cmr,
            'sub': substitutie, 'tech': technisch, 'org': organisatorisch, 'pbm': pbm
        }

with tab3:
    st.markdown(f"## Standard Operating Procedure (SOP)")
    st.markdown(f"**Document-ID:** SOP-{datetime.now().strftime('%Y-%m-%d')}")
    st.markdown("---")
    
    st.markdown("### 1. Werkprotocol / Procedure" if lang == "NL" else "### 1. Work Protocol / Procedure")
    if 'protocol_tekst' in st.session_state and st.session_state['protocol_tekst']:
        protocol_geannoteerd = st.session_state['protocol_tekst']
        protocol_geannoteerd = protocol_geannoteerd.replace('\n', '\n\n')
        
        for stof in gekozen_stoffen:
            patroon = re.compile(re.escape(stof), re.IGNORECASE)
            protocol_geannoteerd = patroon.sub(f"**🧪 {stof}**", protocol_geannoteerd)
            
        st.markdown(protocol_geannoteerd)
    else:
        st.markdown("*Geen protocol geüpload.*" if lang == "NL" else "*No protocol uploaded.*")
    
    st.markdown("---")
    st.markdown("### 2. Chemische Risicobeoordelingen" if lang == "NL" else "### 2. Chemical Risk Assessments")
    
    if not gekozen_stoffen:
        st.markdown("*Geen stoffen geselecteerd.*" if lang == "NL" else "*No substances selected.*")
        
    for index, stof in enumerate(gekozen_stoffen):
        b = beoordelingen[stof]
        Ei, score_EM, exp_klasse = bereken_blootstellingsscore(b['damp'], b['vast'], b['h_val'][1], 1, b['v_val'][1], b['d_val'][1], b['f_val'][1])
        coshh = bereken_coshh_klasse(b['h_zinnen'])
        risico_kleur = bepaal_risico_kleur(coshh, exp_klasse, b['is_cmr'], lang)
        
        st.markdown(f"#### 2.{index+1} {stof} (CAS: {b['cas']})")
        if b['is_cmr']:
            cmr_alert = "🚨 **OFFICIELE CMR-STOF (Wettelijke vervangings- en minimalisatieverplichting van toepassing)**" if lang == "NL" else "🚨 **OFFICIAL CMR SUBSTANCE (Legal substitution and minimization obligation applies)**"
            st.error(cmr_alert)
            
        st.markdown(f"- **H-zinnen:** {b['h_zinnen']} | **Dampdruk / Vapor pressure:** {b['damp']} hPa | **COSHH Klasse / Class:** {coshh}")
        st.markdown(f"- **Handeling / Handling:** {b['h_val'][0]}, {b['v_val'][0]}")
        
        if "Groen" in risico_kleur or "Low" in risico_kleur: st.success(f"**Blootstellingsklasse {exp_klasse} | Score:** {risico_kleur}")
        elif "Oranje" in risico_kleur or "Medium" in risico_kleur: st.warning(f"**Blootstellingsklasse {exp_klasse} | Score:** {risico_kleur}")
        else: st.error(f"**Blootstellingsklasse {exp_klasse} | Score:** {risico_kleur}")

        sub_txt = "Substitutie" if lang == "NL" else "Substitution"
        tech_txt = "Technisch" if lang == "NL" else "Technical"
        org_txt = "Organisatorisch" if lang == "NL" else "Organizational"
        pbm_txt = "PBM" if lang == "NL" else "PPE"
        
        st.markdown(f"**STOP-strategie / strategy:** {sub_txt}: *{b['sub']}* | {tech_txt}: *{b['tech']}* | {org_txt}: *{b['org']}* | {pbm_txt}: *{b['pbm']}*")
        st.write("") 

    st.markdown("---")
    st.markdown("### 3. Calamiteiten & Spill Protocol" if lang == "NL" else "### 3. Emergencies & Spill Protocol")
    spill_nl = """- **Kleine spill (< 50 ml):** Ruim direct op met absorptiematerialen (spill kit). Gooi afval in chemische afvalbak.
- **Grote spill (> 250 ml vluchtige stoffen):** Ruim dit niet zelf op zonder adembescherming. Ontruim het lab, sluit deuren, waarschuw BHV."""
    spill_en = """- **Small spill (< 50 ml):** Clean up immediately using absorption materials (spill kit). Dispose of waste in chemical waste bin.
- **Large spill (> 250 ml volatile substances):** Do not clean this up yourself without respiratory protection. Evacuate the lab, close doors, alert emergency response (BHV)."""
    st.markdown(spill_nl if lang == "NL" else spill_en)
    
    st.divider()
    cap_txt = "Druk op Ctrl+P of Cmd+P in je browser om dit complete SOP document als PDF op te slaan." if lang == "NL" else "Press Ctrl+P or Cmd+P in your browser to save this complete SOP document as a PDF."
    st.caption(cap_txt)
