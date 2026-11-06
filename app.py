import streamlit as st
import pandas as pd
from supabase import create_client, Client
import re
from datetime import datetime
import PyPDF2

st.set_page_config(page_title="WI SOP Generator", layout="wide")

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
        st.header("WI SOP - Login")
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
    
    if any(hx in h for hx in ['H200', 'H201', 'H202', 'H203', 'H204', 'H205']) or 'pikrinezuur' in naam or 'picric' in naam:
        return "Categorie 6: Afvalstoffen met buitengewone risico's (ontplofbaar)"
    if any(term in naam for term in ['kwik', 'mercury', 'batterij', 'accu', 'spuitbus', 'aerosol', 'tl-lamp', 'zware metaal']):
        return "Categorie 5: Bijzondere afval stoffen (kwik, zware metalen, batterijen, spuitbussen)"
    
    zuren = ['zuur', 'acid', 'hcl', 'hno3', 'h2so4', 'h3po4', 'zoutzuur', 'salpeterzuur', 'zwavelzuur', 'fosforzuur']
    if any(z in naam for z in zuren) and not 'organisch' in naam and not 'acetic' in naam and not 'citric' in naam:
        return "Categorie 1: Zuren anorganische stoffen in oplossing (pH < 7)"
    
    basen = ['hydroxide', 'sodium hydroxide', 'kaliumhydroxide', 'ammoniak', 'ammonia', 'natriumhydroxide', 'koh', 'naoh']
    if any(b in naam for b in basen):
        return "Categorie 2: Alkalische anorganische stoffen in oplossing (pH > 7)"
    
    hal
