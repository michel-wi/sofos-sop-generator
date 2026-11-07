import streamlit as st
import pandas as pd
from supabase import create_client, Client
import re
from datetime import datetime
import PyPDF2
import requests

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

@st.cache_data(ttl=86400)
def bepaal_afvalcategorie(stofnaam, h_zinnen, cas_nummer):
    naam = str(stofnaam).lower()
    h = str(h_zinnen).upper()
    
    explosief_hzinnen = ['H200', 'H201', 'H202', 'H203', 'H204', 'H205', 'H240', 'H241']
    if any(hx in h for hx in explosief_hzinnen) or any(x in naam for x in ['pikrinezuur', 'picric', 'azide', 'trinitro', 'perchloraat', 'peroxide']):
        return "Categorie 6: Afvalstoffen met buitengewone risico's (ontplofbaar zoals pikrinezuur)"
    
    if cas_nummer and str(cas_nummer).strip() not in ['Onbekend', '', 'nan']:
        cas_clean = str(cas_nummer).strip()
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{cas_clean}/property/MolecularFormula/JSON"
        try:
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                data = response.json()
                formule = data['PropertyTable']['Properties'][0]['MolecularFormula']
                elementen = set(re.findall(r'[A-Z][a-z]?', formule))
                
                zware_metalen = {'Hg', 'Pb', 'Cd', 'Cr', 'As', 'Ag', 'Cu', 'Ni', 'Co', 'Zn', 'Ba', 'U'}
                if elementen.intersection(zware_metalen):
                    return "Categorie 5: Bijzondere afval stoffen (kwik, zware metalen, batterijen, spuitbussen)"
                
                if 'C' in elementen and 'H' in elementen:
                    halogenen = {'F', 'Cl', 'Br', 'I'}
                    if elementen.intersection(halogenen):
                        return "Categorie 4: Halogeen bevattende organische stoffen"
                    else:
                        return "Categorie 3: Halogeenvrije organische stoffen"
        except Exception:
            pass 
            
    if any(term in naam for term in ['kwik', 'mercury', 'batterij', 'accu', 'spuitbus', 'aerosol', 'tl-lamp', 'zware metaal']):
        return "Categorie 5: Bijzondere afval stoffen (kwik, zware metalen, batterijen, spuitbussen)"
    
    zuren = ['zuur', 'acid', 'hcl', 'hno3', 'h2so4', 'h3po4', 'zoutzuur', 'salpeterzuur', 'zwavelzuur', 'fosforzuur']
    if any(z in naam for z in zuren) and not 'organisch' in naam and not 'acetic' in naam and not 'citric' in naam:
        return "Categorie 1: Zuren anorganische stoffen in oplossing (pH < 7)"
    
    basen = ['hydroxide', 'sodium hydroxide', 'kaliumhydroxide', 'ammoniak', 'ammonia', 'natriumhydroxide', 'koh', 'naoh']
    if any(b in naam for b in basen):
        return "Categorie 2: Alkalische anorganische stoffen in oplossing (pH > 7)"
    
    halogeen_termen = ['chloro', 'bromo', 'fluoro', 'iodo', 'chloor', 'broom', 'jood', 'dichloormethaan', 'dichloromethane', 'chloroform', 'trichloorethyleen']
    if any(ht in naam for ht in halogeen_termen):
        return "Categorie 4: Halogeen bevattende organische stoffen"
        
    organisch_termen = ['ethanol', 'methanol', 'acetone', 'aceton', 'isopropanol', 'butanol', 'ethylde', 'hexane', 'heptaan', 'tolueen', 'xyleen', 'dmso', 'acetonitrile', 'ethylacetate']
    if any(ot in naam for ot in organisch_termen) or 'h225' in h or 'h226' in h:
        return "Categorie 3: Halogeenvrije organische stoffen"
    
    return "Categorie 7: Onbekend! overleg met Dylan"

def extract_text_from_pdf(uploaded_file):
    text = ""
    try:
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text: text += page_text + "\n"
    except Exception: pass
    return text

def vind_stoffen_in_tekst(text, df_chemicalien, extra_handmatige_dict=None):
    if extra_handmatige_dict is None:
        extra_handmatige_dict = {}
        
    gevonden_stoffen = []
    text_lower = text.lower()
    
    alle_stofnamen = list(df_chemicalien['SORTEERNAAM NL'].dropna().unique()) + list(extra_handmatige_dict.keys())
    for stof in alle_stofnamen:
        stof_str = str(stof).strip()
        if len(stof_str) > 2:
            pattern = r'\b' + re.escape(stof_str.lower()) + r'\b'
            if re.search(pattern, text_lower):
                gevonden_stoffen.append(stof_str)
            elif stof_str.lower() == "aceton" and "acetone" in text_lower:
                gevonden_stoffen.append(stof_str)

    cas_col = None
    for col in df_chemicalien.columns:
        if 'cas' in col.lower():
            cas_col = col
            break

    cas_patroon = r'\b\d{2,7}-\d{2}-\d\b'
    gevonden_cas_in_tekst = set(re.findall(cas_patroon, text))
    
    if gevonden_cas_in_tekst:
        for cas in gevonden_cas_in_tekst:
            cas_clean = cas.strip()
            
            if cas_col:
                for _, row in df_chemicalien.iterrows():
                    db_val = row.get(cas_col)
                    if pd.notnull(db_val) and str(db_val).strip() == cas_clean:
                        stof_naam = row.get('SORTEERNAAM NL')
                        if pd.notnull(stof_naam):
                            gevonden_stoffen.append(str(stof_naam).strip())
            
            for naam, data in extra_handmatige_dict.items():
                for k, v in data.items():
                    if 'cas' in k.lower() and str(v).strip() == cas_clean:
                        gevonden_stoffen.append(naam)
                        
    return list(set(gevonden_stoffen))

df_stoffen = laad_chemicalien(supabase_client)

if df_stoffen.empty:
    st.error("Databasefout. Controleer je Secrets en verbinding.")
    st.stop()

# --- BOUW HET ZOEKBare WEERGAVE WOORDENBOEK OP (NAAM + CAS) ---
weergave_dict = {"-- Handmatig invoeren --": "-- Handmatig invoeren --"}
cas_col_global = next((col for col in df_stoffen.columns if 'cas' in col.lower()), 'CAS-NUMMER')
db_stoffen_lijst = []

for _, row in df_stoffen.drop_duplicates(subset=['SORTEERNAAM NL']).iterrows():
    naam = str(row.get('SORTEERNAAM NL', ''))
    if pd.notnull(naam) and naam.strip() and naam.strip().lower() != 'nan':
        naam = naam.strip()
        db_stoffen_lijst.append(naam)
        cas = str(row.get(cas_col_global, 'Onbekend')).strip()
        if cas.lower() == 'nan' or not cas: cas = 'Onbekend'
        weergave_dict[naam] = f"{naam} (CAS: {cas})"

for naam, data in st.session_state['handmatige_stoffen_data'].items():
    cas = str(data.get('CAS-NUMMER', 'Onbekend')).strip()
    if cas.lower() == 'nan' or not cas: cas = 'Onbekend'
    weergave_dict[naam] = f"{naam} (CAS: {cas})"

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
            gevonden = vind_stoffen_in_tekst(geanalyseerde_tekst, df_stoffen, st.session_state['handmatige_stoffen_data'])
            if gevonden:
                st.success(f"Gevonden stoffen / Found substances: {', '.join(gevonden)}")
                st.session_state['gevonden_stoffen'] = gevonden
                st.info("Stoffen zijn hieronder geselecteerd. / Substances are selected below.")
            else:
                st.warning("Geen bekende stoffen gevonden. / No known substances found.")

    st.markdown("---")
    st.subheader("Stoffen Beheren & Selecteren / Manage & Select Substances")
    
    standaard_stoffen = st.session_state.get('gevonden_stoffen', [])
    
    # We voegen de format_func toe, waardoor hij in het scherm zoekt op "Naam (CAS: ...)"
    gekozen_stoffen = st.multiselect(
        "Stoffen voor dit protocol (Zoek op naam of CAS-nummer) / Substances for this protocol:", 
        complete_stoffen_lijst, 
        default=[s for s in standaard_stoffen if s in complete_stoffen_lijst],
        format_func=lambda x: weergave_dict.get(x, x)
    )
    
    with st.expander("➕ Stof ontbreekt? Voeg handmatig toe / Substance missing? Add manually"):
        st.info("Let op: Raadpleeg altijd het SDS van de leverancier.")
        
        new_naam = st.text_input("Stofnaam / Afkorting (Substance name)")
        is_synoniem = st.checkbox("Other name (Vink aan als dit een synoniem of afkorting is)")
        
        final_orig = ""
        final_cas = ""
        final_h = ""
        final_cmr = "Uitgevinkt"
        
        if is_synoniem:
            # Drop-down met format_func voor de CAS weergave
            koppel_keuze = st.selectbox(
                "Koppel aan bestaande stof (Zoek op original name of CAS):", 
                ["-- Handmatig invoeren --"] + sorted(db_stoffen_lijst),
                format_func=lambda x: weergave_dict.get(x, x)
            )
            
            if koppel_keuze != "-- Handmatig invoeren --":
                match_row = df_stoffen[df_stoffen['SORTEERNAAM NL'] == koppel_keuze].iloc[0]
                
                final_orig = koppel_keuze
                final_cas = str(match_row.get('CAS-NUMMER', ''))
                final_h = str(match_row.get('H-ZINNEN (TEKST)', ''))
                is_cmr_calc = check_cmr_status(final_h, match_row.get('CMR', ''))
                final_cmr = "Aangevinkt" if is_cmr_calc else "Uitgevinkt"
                
                st.text_input("Original name (Oorspron
