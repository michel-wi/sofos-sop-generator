import streamlit as st
import pandas as pd
from supabase import create_client, Client
import re
from datetime import datetime
import PyPDF2

st.set_page_config(page_title="SOFOS SOP Generator", layout="wide")

st.markdown("""
    <style>
    @media print {
        [data-testid="stSidebar"] { display: none !important; }
    }
    </style>
""", unsafe_allow_html=True)

if 'handmatige_stoffen_data' not in st.session_state:
    st.session_state['handmatige_stoffen_data'] = {}

def check_password():
    def password_entered():
        if st.session_state["username"] in st.secrets["passwords"]:
            if st.session_state["password"] == st.secrets["passwords"][st.session_state["username"]]:
                st.session_state["password_correct"] = True
                del st.session_state["password"] 
                return
        st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.header("SOFOS - Login")
        st.text_input("Gebruikersnaam", key="username")
        st.text_input("Wachtwoord", type="password", key="password", on_change=password_entered)
        return False
    elif not st.session_state["password_correct"]:
        st.error("Gebruikersnaam of wachtwoord incorrect.")
        st.text_input("Gebruikersnaam", key="username")
        st.text_input("Wachtwoord", type="password", key="password", on_change=password_entered)
        return False
    return True

if not check_password():
    st.stop()

@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase_client = init_connection()

@st.cache_data(ttl=60)
def laad_chemicalien(_client):
    all_data = []
    offset = 0
    limit = 1000
    try:
        while True:
            response = _client.table('chemicalien').select("*").range(offset, offset + limit - 1).execute()
            data = response.data
            if not data: break
            for row in data:
                row['is_handmatig'] = False
            all_data.extend(data)
            if len(data) < limit: break
            offset += limit
    except Exception:
        pass
        
    try:
        response_handmatig = _client.table('handmatige_chemicalien').select("*").execute()
        data_handmatig = response_handmatig.data
        if data_handmatig:
            for row in data_handmatig:
                row['is_handmatig'] = True
            all_data.extend(data_handmatig)
    except Exception:
        pass
        
    return pd.DataFrame(all_data)

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

def bepaal_afvalcategorie(stofnaam, h_zinnen):
    naam = str(stofnaam).lower()
    h = str(h_zinnen).upper()
    
    # Categorie 6: Buitengewone risico's (ontplofbaar)
    if any(hx in h for hx in ['H200', 'H201', 'H202', 'H203', 'H204', 'H205']) or 'pikrinezuur' in naam or 'picric' in naam:
        return "Categorie 6: Afvalstoffen met buitengewone risico's (ontplofbaar)"
    # Categorie 5: Bijzondere afvalstoffen
    if any(term in naam for term in ['kwik', 'mercury', 'batterij', 'accu', 'spuitbus', 'aerosol', 'tl-lamp', 'zware metaal']):
        return "Categorie 5: Bijzondere afval stoffen (kwik, zware metalen, batterijen, spuitbussen)"
    # Categorie 1: Zuren anorganische stoffen (pH < 7)
    zuren = ['zuur', 'acid', 'hcl', 'hno3', 'h2so4', 'h3po4', 'zoutzuur', 'salpeterzuur', 'zwavelzuur', 'fosforzuur']
    if any(z in naam for z in zuren) and not 'organisch' in naam and not 'acetic' in naam and not 'citric' in naam:
        return "Categorie 1: Zuren anorganische stoffen in oplossing (pH < 7)"
    # Categorie 2: Alkalische anorganische stoffen (pH > 7)
    basen = ['hydroxide', 'sodium hydroxide', 'kaliumhydroxide', 'ammoniak', 'ammonia', 'natriumhydroxide', 'koh', 'naoh']
    if any(b in naam for b in basen):
        return "Categorie 2: Alkalische anorganische stoffen in oplossing (pH > 7)"
    # Categorie 4: Halogeen bevattende organische stoffen
    halogeen_termen = ['chloro', 'bromo', 'fluoro', 'iodo', 'chloor', 'broom', 'jood', 'dichloormethaan', 'dichloromethane', 'chloroform', 'trichloorethyleen']
    if any(ht in naam for ht in halogeen_termen):
        return "Categorie 4: Halogeen bevattende organische stoffen"
    # Categorie 3: Halogeenvrije organische stoffen (standaard)
    return "Categorie 3: Halogeenvrije organische stoffen"

def extract_text_from_pdf(uploaded_file):
    text = ""
    try:
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text: text += page_text + "\n"
    except Exception: pass
    return text

def vind_stoffen_in_tekst(text, complete_stoffen_lijst):
    gevonden_stoffen = []
    text_lower = text.lower()
    for stof in complete_stoffen_lijst:
        stof_str = str(stof).strip()
        if len(stof_str) > 2:
            pattern = r'\b' + re.escape(stof_str.lower()) + r'\b'
            if re.search(pattern, text_lower):
                gevonden_stoffen.append(stof)
            elif stof_str.lower() == "aceton" and "acetone" in text_lower:
                gevonden_stoffen.append(stof)
    return list(set(gevonden_stoffen))

def schat_standaard_dampdruk(stofnaam, h_zinnen):
    naam_lower = stofnaam.lower()
    h_zinnen_str = str(h_zinnen).upper()
    
    if "H224" in h_zinnen_str or "H225" in h_zinnen_str or "aceton" in naam_lower or "chloroform" in naam_lower:
        return 200.0 
    elif "methanol" in naam_lower or "ethanol" in naam_lower or "alcohol" in naam_lower:
        return 50.0   
    elif "water" in naam_lower or "oplossing" in naam_lower or "buffer" in naam_lower:
        return 24.0   
    return 0.0 

df_stoffen = laad_chemicalien(supabase_client)

if df_stoffen.empty:
    st.error("Databasefout. Controleer je Secrets en verbinding.")
    st.stop()

db_stoffen_lijst = [s for s in df_stoffen['SORTEERNAAM NL'].unique() if pd.notnull(s)]
complete_stoffen_lijst = sorted(list(set(db_stoffen_lijst + list(st.session_state['handmatige_stoffen_data'].keys()))))

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
            gevonden = vind_stoffen_in_tekst(geanalyseerde_tekst, complete_stoffen_lijst)
            if gevonden:
                st.success(f"Gevonden stoffen / Found substances: {', '.join(gevonden)}")
                st.session_state['gevonden_stoffen'] = gevonden
                st.info("Stoffen zijn geselecteerd in Stap 2. / Substances selected in Step 2.")
            else:
                st.warning("Geen bekende stoffen gevonden. / No known substances found.")

with tab2:
    st.header("Stap 1-3: Basisgegevens & STOP-strategie / Basic Data & STOP Strategy")
    
    with st.expander("➕ Stof ontbreekt? Voeg handmatig toe / Substance missing? Add manually"):
        st.info("Let op: Raadpleeg altijd het SDS van de leverancier.")
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
                            supabase_client.table('handmatige_chemicalien').insert({
                                "SORTEERNAAM NL": new_naam, "CAS-NUMMER": new_cas, 
                                "H-ZINNEN (TEKST)": new_h, "CMR": cmr_status
                            }).execute()
                            st.success(f"{new_naam} is succesvol opgeslagen in de database!")
                            laad_chemicalien.clear()
                        except Exception:
                            st.warning("Let op: Tabel 'handmatige_chemicalien' bestaat nog niet. Stof is wel tijdelijk toegevoegd.")
                    st.rerun()

    standaard_stoffen = st.session_state.get('gevonden_stoffen', [])
    gekozen_stoffen = st.multiselect("Stoffen voor dit protocol / Substances for this protocol:", complete_stoffen_lijst, default=[s for s in standaard_stoffen if s in complete_stoffen_lijst])
    
    beoordelingen = {}
    
    afval_categorieen = [
        "Categorie 1: Zuren anorganische stoffen in oplossing (pH < 7)",
        "Categorie 2: Alkalische anorganische stoffen in oplossing (pH > 7)",
        "Categorie 3: Halogeenvrije organische stoffen",
        "Categorie 4: Halogeen bevattende organische stoffen",
        "Categorie 5: Bijzondere afval stoffen (kwik, zware metalen, batterijen, spuitbussen)",
        "Categorie 6: Afvalstoffen met buitengewone risico's (ontplofbaar zoals pikrinezuur)"
    ]

    for stof in gekozen_stoffen:
        if stof in st.session_state['handmatige_stoffen_data']:
            stof_data = st.session_state['handmatige_stoffen_data'][stof]
            is_handmatig = True
        else:
            stof_data = df_stoffen[df_stoffen['SORTEERNAAM NL'] == stof].iloc[0]
            is_handmatig = stof_data.get('is_handmatig', False)
            
        h_zinnen_db = stof_data.get('H-ZINNEN (TEKST)', 'Geen')
        cas_nummer = stof_data.get('CAS-NUMMER', 'Onbekend')
        db_cmr = stof_data.get('CMR', '')
        
        is_cmr = check_cmr_status(h_zinnen_db, db_cmr)
        
        geschatte_damp = schat_standaard_dampdruk(stof,
