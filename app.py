import streamlit as st
import pandas as pd
from supabase import create_client, Client
import re
from datetime import datetime
import PyPDF2

st.set_page_config(page_title="SOFOS SOP Generator", layout="wide")

# --- CSS om de zijbalk te verbergen bij het printen (Ctrl+P) ---
st.markdown("""
    <style>
    @media print {
        [data-testid="stSidebar"] {
            display: none !important;
        }
    }
    </style>
""", unsafe_allow_html=True)

# --- Sessiebeheer voor handmatige stoffen ---
if 'handmatige_stoffen_data' not in st.session_state:
    st.session_state['handmatige_stoffen_data'] = {}

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
# OPLOSSING: Gebruik cache_resource voor de live verbinding (die niet geserialiseerd kan worden)
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase_client = init_connection()

# OPLOSSING: Gebruik cache_data alleen voor de pure tabel data
@st.cache_data(ttl=600)
def laad_chemicalien(_client):
    all_data = []
    offset = 0
    limit = 1000
    try:
        while True:
            response = _client.table('chemicalien').select("*").range(offset, offset + limit - 1).execute()
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
    db_is_cmr = 'aangevinkt' in str(db_cmr_val).strip().lower() or 'true' in str(db_cmr_val).strip().lower() or '1' in str(db_cmr_val).strip().lower()
    h_zinnen = str(h_zinnen_tekst).upper()
    cmr_h_zinnen = ["H340", "H350", "H350I", "H360", "H341", "H351", "H361"]
    h_found = any(h in h_zinnen for h in cmr_h_zinnen)
    return db_is_cmr or h_found

def bepaal_risico_kleur(coshh, exp_klasse, is_cmr):
    if is_cmr or coshh == "E" or exp_klasse >= 3:
        return "Hoog (Rood / High - Red / CMR Zorgwekkend)"
    if exp_klasse == 1 and coshh in ["A", "B"]:
        return "Laag (Groen / Low - Green)"
    return "Midden (Oranje / Medium - Orange)"

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
            pattern = r'\b' + re.escape(stof_str.lower()) + r'\b'
            if re.search(pattern, text_lower):
                gevonden_stoffen.append(stof)
            elif stof_str.lower() == "aceton" and "acetone" in text_lower:
                gevonden_stoffen.append(stof)
                
    return list(set(gevonden_stoffen))

# --- 3. Interface ---
df_stoffen = laad_chemicalien(supabase_client)

if df_stoffen.empty:
    st.error("Databasefout. Controleer je Secrets.")
    st.stop()

db_stoffen_lijst = [s for s in df_stoffen['SORTEERNAAM NL'].unique() if pd.notnull(s)]
# Voeg de handmatig ingevoerde stoffen toe aan de dropdown lijst
complete_stoffen_lijst = sorted(db_stoffen_lijst + list(st.session_state['handmatige_stoffen_data'].keys()))

tab1, tab2, tab3 = st.tabs(["📄 NLP Protocol Upload", "📝 Stap 1-3: Invoer / Input", "🖨️ Stap 4: SOP Document"])

with tab1:
    st.header("Document of Protocol Analyseren / Analyze Document")
    upload_file = st.file_uploader("Upload PDF of TXT bestand / Upload file", type=['pdf', 'txt'])
    plak_tekst = st.text_area("Of plak hier direct de tekst (NL / EN):", height=150)
    
    geanalyseerde_tekst = ""
    if upload_file:
        if upload_file.name.endswith('.pdf'): geanalyseerde_tekst = extract_text_from_pdf(upload_file)
        elif upload_file.name.endswith('.txt'): geanalyseerde_tekst = upload_file.read().decode('utf-8')
    elif plak_tekst:
        geanalyseerde_tekst = plak_tekst
        
    if geanalyseerde_tekst:
        st.session_state['protocol_tekst'] = geanalyseerde_tekst 
        
        if st.button("Analyseer Tekst / Analyze Text", type="primary"):
            gevonden = vind_stoffen_in_tekst(geanalyseerde_tekst, df_stoffen)
            if gevonden:
                st.success(f"Gevonden stoffen / Found substances: {', '.join(gevonden)}")
                st.session_state['gevonden_stoffen'] = gevonden
                st.info("Stoffen zijn geselecteerd in Stap 2. / Substances selected in Step 2.")
            else:
                st.warning("Geen bekende stoffen gevonden. / No known substances found.")

with tab2:
    st.header("Stap 1-3: Basisgegevens & STOP-strategie / Basic Data & STOP Strategy")
    
    # --- HANDMATIGE STOF TOEVOEGEN ---
    with st.expander("➕ Stof ontbreekt? Voeg handmatig toe / Substance missing? Add manually"):
        st.info("Let op: Raadpleeg altijd het SDS van de leverancier. Als de stof vaker wordt gebruikt, mail dan de arbodienst om deze centraal toe te voegen.")
        with st.form("handmatige_stof_form"):
            new_naam = st.text_input("Stofnaam / Substance name")
            new_cas = st.text_input("CAS-nummer / CAS number")
            new_h = st.text_input("H-zinnen (bijv. H225, H319) / H-statements")
            new_cmr = st.checkbox("Is dit een CMR-stof? / Is this a CMR substance?")
            
            if st.form_submit_button("Toevoegen aan database / Add to database"):
                if new_naam:
                    cmr_status = "Aangevinkt" if new_cmr else "Uitgevinkt"
                    st.session_state['handmatige_stoffen_data'][new_naam] = {
                        'CAS-NUMMER': new_cas, 'H-ZINNEN (TEKST)': new_h, 'CMR': cmr_status, 'is_handmatig': True
                    }
                    
                    if supabase_client:
                        try:
                            # Schrijf weg naar de nieuwe tabel in Supabase
                            supabase_client.table('handmatige_chemicalien').insert({
                                "SORTEERNAAM NL": new_naam, "CAS-NUMMER": new_cas, 
                                "H-ZINNEN (TEKST)": new_h, "CMR": cmr_status
                            }).execute()
                            st.success(f"{new_naam} is succesvol opgeslagen in de database!")
                        except Exception as e:
                            st.warning("Let op: Tabel 'handmatige_chemicalien' bestaat nog niet in Supabase. De stof is voor deze sessie wel tijdelijk toegevoegd.")
                    
                    st.rerun()

    # --- KIES STOFFEN ---
    standaard_stoffen = st.session_state.get('gevonden_stoffen', [])
    gekozen_stoffen = st.multiselect("Stoffen voor dit protocol / Substances for this protocol:", complete_stoffen_lijst, default=[s for s in standaard_stoffen if s in complete_stoffen_lijst])
    
    beoordelingen = {}
    
    for stof in gekozen_stoffen:
        # Haal data uit de database of uit de handmatige sessie
        if stof in st.session_state['handmatige_stoffen_data']:
            stof_data = st.session_state['handmatige_stoffen_data'][stof]
            is_handmatig = True
        else:
            stof_data = df_stoffen[df_stoffen['SORTEERNAAM NL'] == stof].iloc[0]
            is_handmatig = False
            
        h_zinnen_db = stof_data.get('H-ZINNEN (TEKST)', 'Geen')
        cas_nummer = stof_data.get('CAS-NUMMER', 'Onbekend')
        db_cmr = stof_data.get('CMR', '')
        
        is_cmr = check_cmr_status(h_zinnen_db, db_cmr)
        
        st.markdown(f"### ⚙️ {stof}")
        if is_handmatig:
            st.caption("*(Handmatig toegevoegde stof / Manually added substance)*")
            
        if is_cmr:
            st.error("⚠️ **Let op / Notice:** Deze stof is herkend als **CMR-stof / CMR Substance**!")
        
        st.info(f"**CAS:** {cas_nummer} | **H-zinnen / H-statements:** {h_zinnen_db}")
        
        c1, c2 = st.columns(2)
        with c1:
            is_vast = st.checkbox("Vaste stof / Solid substance", key=f"vast_{stof}")
            dampspanning = st.number_input("Dampdruk / Vapor pressure (hPa)", value=0.0, key=f"damp_{stof}")
        with c2:
            h = st.selectbox("Hoeveelheid / Quantity", [("Zeer kleine / Very small (max 1g/ml)", 0.03), ("Kleine / Small (max 10g/ml)", 0.1), ("Grote / Large (>10g/ml)", 1.0)], key=f"h_{stof}")
            v = st.selectbox("Ventilatie / Ventilation", [("Zuurkast / Fume hood", 0.03), ("Op tafel / On table", 1)], key=f"v_{stof}")
            d = st.selectbox("Tijdsduur / Duration", [("1 tot 30 min / 1 to 30 min", 0.06), ("0.5 tot 2 uur / 0.5 to 2 hours", 0.25)], key=f"d_{stof}")
            f = st.selectbox("Frequentie / Frequency", [("1 keer per week / Once a week", 0.2), ("4 - 5 keer per week / 4 - 5 times a week", 1)], key=f"f_{stof}")

        substitutie = st.text_input("Substitutie motivatie (Verplicht bij CMR) / Substitution motivation (Mandatory for CMR):", value="Essentieel voor onderzoek, alternatief niet mogelijk / Essential for research, alternative not possible.", key=f"sub_{stof}")
        technisch = st.text_input("Technisch / Technical:", value="Werken in zuurkast / Work in fume hood.", key=f"tech_{stof}")
        organisatorisch = st.text_input("Organisatorisch / Organizational:", value="Beperkte tijd / Limited time.", key=f"org_{stof}")
        pbm = st.text_input("PBM / PPE:", value="Labjas, bril, handschoenen / Lab coat, goggles, gloves.", key=f"pbm_{stof}")
        
        st.divider()
        
        beoordelingen[stof] = {
            'cas': cas_nummer, 'h_zinnen': h_zinnen_db, 'damp': dampspanning, 'vast': is_vast,
            'h_val': h, 'v_val': v, 'd_val': d, 'f_val': f, 'is_cmr': is_cmr,
            'sub': substitutie, 'tech': technisch, 'org': organisatorisch, 'pbm': pbm,
            'is_handmatig': is_handmatig
        }

with tab3:
    st.markdown(f"## Standard Operating Procedure (SOP)")
    st.markdown(f"**Document-ID:** SOP-{datetime.now().strftime('%Y-%m-%d')}")
    st.markdown("---")
    
    st.markdown("### 1. Werkprotocol / Work Protocol")
    if 'protocol_tekst' in st.session_state and st.session_state['protocol_tekst']:
        protocol_geannoteerd = st.session_state['protocol_tekst']
        protocol_geannoteerd = protocol_geannoteerd.replace('\n', '\n\n')
        
        for stof in gekozen_stoffen:
            patroon = re.compile(re.escape(stof), re.IGNORECASE)
            protocol_geannoteerd = patroon.sub(f"**🧪 {stof}**", protocol_geannoteerd)
            
        st.markdown(protocol_geannoteerd)
    else:
        st.markdown("*Geen protocol geüpload. / No protocol uploaded.*")
    
    st.markdown("---")
    st.markdown("### 2. Chemische Risicobeoordelingen / Chemical Risk Assessments")
    
    if not gekozen_stoffen:
        st.markdown("*Geen stoffen geselecteerd. / No substances selected.*")
        
    for index, stof in enumerate(gekozen_stoffen):
        b = beoordelingen[stof]
        Ei, score_EM, exp_klasse = bereken_blootstellingsscore(b['damp'], b['vast'], b['h_val'][1], 1, b['v_val'][1], b['d_val'][1], b['f_val'][1])
        coshh = bereken_coshh_klasse(b['h_zinnen'])
        risico_kleur = bepaal_risico_kleur(coshh, exp_klasse, b['is_cmr'])
        
        st.markdown(f"#### 2.{index+1} {stof} (CAS: {b['cas']})")
        
        # Geef officieel lab-advies in de SOP bij handmatige invoer
        if b.get('is_handmatig'):
            st.info("ℹ️ **Advies / Notice:** Deze stof is handmatig toegevoegd. Raadpleeg altijd het Veiligheidsinformatieblad (SDS) van de leverancier. Indien deze stof veel gebruikt wordt, stuur dan een e-mail naar *Afdeling Veiligheid en Milieu@Lumc.nl* om deze aan de centrale databank toe te voegen.")
            
        if b['is_cmr']:
            st.error("🚨 **OFFICIELE CMR-STOF / OFFICIAL CMR SUBSTANCE (Wettelijke vervangingsverplichting / Legal substitution obligation)**")
            
        st.markdown(f"- **H-zinnen / H-statements:** {b['h_zinnen']} | **Dampdruk / Vapor pressure:** {b['damp']} hPa | **COSHH:** {coshh}")
        st.markdown(f"- **Handeling / Handling:** {b['h_val'][0]}, {b['v_val'][0]}")
        
        if "Groen" in risico_kleur or "Low" in risico_kleur: st.success(f"**Blootstellingsklasse / Exposure class {exp_klasse} | Score:** {risico_kleur}")
        elif "Oranje" in risico_kleur or "Medium" in risico_kleur: st.warning(f"**Blootstellingsklasse / Exposure class {exp_klasse} | Score:** {risico_kleur}")
        else: st.error(f"**Blootstellingsklasse / Exposure class {exp_klasse} | Score:** {risico_kleur}")

        st.markdown(f"**STOP-strategie / strategy:** Substitutie / Substitution: *{b['sub']}* | Technisch / Technical: *{b['tech']}* | Organisatorisch / Organizational: *{b['org']}* | PBM / PPE: *{b['pbm']}*")
        st.write("") 

    st.markdown("---")
    st.markdown("### 3. Calamiteiten & Spill Protocol / Emergencies & Spill Protocol")
    spill_bilingual = """- **Kleine spill / Small spill (< 50 ml):** Ruim direct op met absorptiematerialen (spill kit). Gooi afval in chemische afvalbak. / Clean up immediately using absorption materials (spill kit). Dispose of waste in chemical waste bin.
- **Grote spill / Large spill (> 250 ml vluchtige stoffen / volatile substances):** Ruim dit niet zelf op zonder adembescherming. Ontruim het lab, sluit deuren, waarschuw BHV. / Do not clean this up yourself without respiratory protection. Evacuate the lab, close doors, alert emergency response (BHV)."""
    st.markdown(spill_bilingual)
    
    st.divider()
    st.caption("Druk op Ctrl+P of Cmd+P in je browser om dit complete SOP document als PDF op te slaan. / Press Ctrl+P or Cmd+P in your browser to save this complete SOP document as a PDF.")
