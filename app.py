import streamlit as st
import pandas as pd
from supabase import create_client, Client
import re
from datetime import datetime
import PyPDF2

st.set_page_config(page_title="wi SOP creator", layout="wide")

st.markdown("""
    <style>
    @media print {
        body { margin: 0; padding: 0; }
        .stApp { width: 100%; max-width: 100%; }
        header, footer, .stSidebar, .stButton, .stFileUploader { display: none !important; }
        .block-container { padding-top: 0 !important; }
    }
    .print-button {
        display: inline-flex; align-items: center; justify-content: center;
        background-color: #4CAF50; color: white; padding: 10px 20px;
        border: none; border-radius: 4px; cursor: pointer; text-decoration: none;
        font-weight: bold; margin-bottom: 20px;
    }
    .print-button:hover { background-color: #45a049; }
    </style>
    """, unsafe_allow_html=True)

# Supabase configuratie - VERVANG DOOR JOUW WAARDEN in de Streamlit Secrets
try:
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
except Exception:
    url: str = "JOUW_SUPABASE_URL_HIER"
    key: str = "JOUW_SUPABASE_KEY_HIER"

@st.cache_resource
def init_connection():
    return create_client(url, key)

supabase: Client = init_connection()

def check_password():
    """Geeft True terug als de gebruiker een correct wachtwoord had ingevoerd."""
    def password_entered():
        if st.session_state["username"] in st.secrets.get("passwords", {}):
            if st.session_state["password"] == st.secrets["passwords"][st.session_state["username"]]:
                st.session_state["password_correct"] = True
                del st.session_state["password"] 
                return
        st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.header("LUMC - Login")
        st.text_input("Gebruikersnaam", key="username")
        st.text_input("Wachtwoord", type="password", key="password", on_change=password_entered)
        return False
    elif not st.session_state["password_correct"]:
        st.error("Gebruikersnaam of wachtwoord incorrect.")
        st.text_input("Gebruikersnaam", key="username")
        st.text_input("Wachtwoord", type="password", key="password", on_change=password_entered)
        return False
    return True

@st.cache_data(ttl=600)
def laad_chemicalien():
    response = supabase.table('chemicalien').select('*').execute()
    return pd.DataFrame(response.data)

@st.cache_data(ttl=600)
def laad_handmatige_chemicalien():
    try:
         response = supabase.table('handmatige_chemicalien').select('*').execute()
         return pd.DataFrame(response.data)
    except:
         return pd.DataFrame()

# Laad de databases in bij opstarten
if 'df_stoffen' not in st.session_state:
    st.session_state['df_stoffen'] = laad_chemicalien()
    
if 'handmatige_stoffen_data' not in st.session_state:
    df_handmatig = laad_handmatige_chemicalien()
    if not df_handmatig.empty:
        st.session_state['handmatige_stoffen_data'] = df_handmatig.set_index('SORTEERNAAM NL').to_dict('index')
    else:
        st.session_state['handmatige_stoffen_data'] = {}


def extract_text_from_pdf(pdf_file):
    reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text

def vind_stoffen_in_tekst(text, df_chemicalien, extra_handmatige_dict):
    gevonden_stoffen = []
    text_lower = text.lower()
    
    alle_stofnamen = list(df_chemicalien['SORTEERNAAM NL'].dropna().unique()) + list(extra_handmatige_dict.keys())
    for stof in alle_stofnamen:
        stof_str = str(stof).strip()
        if len(stof_str) > 2:
            pattern = r'\b' + re.escape(stof_str.lower()) + r'\b'
            if re.search(pattern, text_lower):
                gevonden_stoffen.append(stof)
            elif stof_str.lower() == "aceton" and "acetone" in text_lower:
                gevonden_stoffen.append(stof)

    cas_patroon = r'\b\d{2,7}-\d{2}-\d\b'
    gevonden_cas_in_tekst = set(re.findall(cas_patroon, text))
    
    if gevonden_cas_in_tekst:
        for cas in gevonden_cas_in_tekst:
            match_db = df_chemicalien[df_chemicalien['CAS-NUMMER'].astype(str).str.strip() == cas]
            if not match_db.empty:
                gevonden_stoffen.append(match_db.iloc[0]['SORTEERNAAM NL'])
            
            for naam, data in extra_handmatige_dict.items():
                if str(data.get('CAS-NUMMER', '')).strip() == cas:
                    gevonden_stoffen.append(naam)
                    
    return list(set(gevonden_stoffen))


# Hoofdprogramma
if check_password():

    st.title("wi SOP")

    tab1, tab2, tab3 = st.tabs(["1. Protocol Analyse", "2. Beoordeling", "3. PDF Rapport"])

    with tab1:
        st.header("Stap 1: Protocol Analyse")
        geanalyseerde_tekst = ""
        
        uploaded_file = st.file_uploader("Upload SOP (PDF)", type="pdf")
        
        if uploaded_file is not None:
            geanalyseerde_tekst = extract_text_from_pdf(uploaded_file)
            st.text_area("Geëxtraheerde Tekst", geanalyseerde_tekst, height=200)
            
        tekst_input = st.text_area("Of plak protocoltekst hier:", height=200)
        if tekst_input:
            geanalyseerde_tekst += " " + tekst_input

        df_stoffen = st.session_state['df_stoffen']
        
        if st.button("Analyseer Tekst / Analyze Text", type="primary"):
            gevonden = vind_stoffen_in_tekst(geanalyseerde_tekst, df_stoffen, st.session_state['handmatige_stoffen_data'])
            
            if gevonden:
                st.success(f"Gevonden stoffen / Found substances: {', '.join(gevonden)}")
                st.session_state['geselecteerde_stoffen'] = gevonden
            else:
                st.warning("Geen stoffen uit de database gevonden in de tekst.")

    with tab2:
        st.header("Stap 2: Risicobeoordeling")
        
        alle_bekende_stoffen = list(st.session_state['df_stoffen']['SORTEERNAAM NL'].dropna().unique()) + list(st.session_state['handmatige_stoffen_data'].keys())
        alle_bekende_stoffen = sorted(list(set(alle_bekende_stoffen)))

        geselecteerde_stoffen = st.multiselect(
            "Selecteer stoffen voor beoordeling",
            options=alle_bekende_stoffen,
            default=st.session_state.get('geselecteerde_stoffen', [])
        )

        # HIER IS HET HANDMATIGE FORMULIER WEER TERUGGEPLAATST IN TAB 2
        st.markdown("---")
        with st.expander("➕ Ontbrekende stof handmatig toevoegen / Add missing substance"):
            with st.form("add_stof_form"):
                col1, col2 = st.columns(2)
                with col1:
                    nieuwe_stof_naam = st.text_input("Naam (NL)")
                    nieuw_cas = st.text_input("CAS nummer")
                    nieuwe_h_zinnen = st.text_input("H-zinnen (bijv. H225,H319)")
                with col2:
                    nieuw_gevaar = st.selectbox("COSHH Klasse", ["A", "B", "C", "D", "E"])
                    nieuw_cmr = st.checkbox("Is CMR stof?")
                    nieuwe_dampdruk = st.number_input("Dampdruk (hPa)", min_value=0.0, value=0.0, step=0.1)
                    
                submitted = st.form_submit_button("Toevoegen en Selecteren / Add and Select")
                
                if submitted and nieuwe_stof_naam:
                    try:
                        nieuw_record = {
                            "SORTEERNAAM NL": nieuwe_stof_naam,
                            "CAS-NUMMER": nieuw_cas,
                            "H ZINNEN": nieuwe_h_zinnen,
                            "COSHH_KLASSE": nieuw_gevaar,
                            "CMR": nieuw_cmr,
                            "DAMPDRUK": nieuwe_dampdruk
                        }
                        supabase.table("handmatige_chemicalien").insert(nieuw_record).execute()
                        
                        st.session_state['handmatige_stoffen_data'][nieuwe_stof_naam] = nieuw_record
                        
                        # Voeg toe aan geselecteerde stoffen en herlaad pagina zodat hij in de beoordelingslijst verschijnt
                        huidige_selectie = st.session_state.get('geselecteerde_stoffen', [])
                        if nieuwe_stof_naam not in huidige_selectie:
                            huidige_selectie.append(nieuwe_stof_naam)
                        st.session_state['geselecteerde_stoffen'] = huidige_selectie
                        
                        st.success(f"{nieuwe_stof_naam} succesvol toegevoegd!")
                        st.rerun()  # Dit ververst Tab 2 zodat de multiselect en de expander hieronder direct updaten
                    except Exception as e:
                        st.error(f"Fout bij opslaan naar database: {e}")
        
        st.markdown("---")

        st.session_state['beoordelingen'] = {}

        for stofnaam in geselecteerde_stoffen:
            with st.expander(f"⚙️ {stofnaam}", expanded=True):
                match_db = st.session_state['df_stoffen'][st.session_state['df_stoffen']['SORTEERNAAM NL'] == stofnaam]
                if not match_db.empty:
                    stof_row = match_db.iloc[0].to_dict()
                else:
                    stof_row = st.session_state['handmatige_stoffen_data'].get(stofnaam, {})

                cas_nr = stof_row.get('CAS-NUMMER', 'Onbekend')
                h_zinnen = stof_row.get('H ZINNEN', 'Onbekend')
                
                st.info(f"**CAS:** {cas_nr} | **H-zinnen:** {h_zinnen}")

                # --- SLIMME STANDAARDWAARDEN BEPALEN ---
                is_cmr = bool(stof_row.get('CMR', False)) 
                gevaarklasse = str(stof_row.get('COSHH_KLASSE', 'A')).strip().upper() 
                if not gevaarklasse or gevaarklasse == 'NAN': gevaarklasse = 'A'
                
                try:
                    dampdruk = float(stof_row.get('DAMPDRUK', 0.0))
                except:
                    dampdruk = 0.0

                if is_cmr or gevaarklasse in ['D', 'E'] or dampdruk > 25.0:
                    standaard_vent_index = 0  # Optie 1 (Zuurkast)
                    standaard_tech_tekst = "Werken in zuurkast / Work in fume hood."
                else:
                    standaard_vent_index = 2  # Optie 3 (Tafel)
                    standaard_tech_tekst = "Werken op labtafel / Work on the lab table."

                # Invoervelden
                vaste_stof = st.checkbox(f"Vaste stof / Solid substance", key=f"vast_{stofnaam}")
                
                col1, col2 = st.columns(2)
                with col1:
                    damp = st.number_input("Dampdruk (hPa)", value=dampdruk, key=f"damp_{stofnaam}")
                with col2:
                    hoeveelheid = st.selectbox(
                        "Hoeveelheid / Quantity",
                        options=[
                            "('2. Very small quantities (1 g or ml), pipetting, weighing / Zeer klein', 0.03)",
                            "('3. Small quantities (10 g or ml) / Klein', 0.1)",
                            "('5. Stir, pour over, immerse (Max 1 kg) / Middelmatig', 1.0)"
                        ],
                        key=f"hoeveel_{stofnaam}"
                    )
                    
                    ventilatie = st.selectbox(
                        "Ventilatie / Ventilation",
                        options=[
                            "('1. Work in a fume hood / Werk in een zuurkast', 0.03)",
                            "('2. Work with point extraction / Werk met puntafzuiging', 0.3)",
                            "('3. Work on the table / Werk op de tafel', 1.0)"
                        ],
                        index=standaard_vent_index,
                        key=f"vent_{stofnaam}"
                    )

                substitutie = st.text_input("Substitutie motivatie (Verplicht bij CMR):", value="Essentieel voor onderzoek, alternatief niet mogelijk.", key=f"sub_{stofnaam}")
                
                technisch = st.text_input("Technisch / Technical:", value=standaard_tech_tekst, key=f"tech_{stofnaam}")
                
                organisatorisch = st.text_input("Organisatorisch:", value="Beperkte tijd.", key=f"org_{stofnaam}")
                
                pbm = st.text_input("PBM / PPE:", value="Labjas, bril, handschoenen.", key=f"pbm_{stofnaam}")

                st.session_state['beoordelingen'][stofnaam] = {
                    "cas": cas_nr, "h_zinnen": h_zinnen, "cmr": is_cmr, "klasse": gevaarklasse,
                    "dampdruk": damp, "hoeveelheid": hoeveelheid, "ventilatie": ventilatie,
                    "technisch": technisch, "organisatorisch": organisatorisch, "pbm": pbm
                }


    with tab3:
        st.header("Stap 3: Rapport Genereren")
        
        st.markdown('<a href="javascript:window.print()" class="print-button">🖨️ Print / Opslaan als PDF</a>', unsafe_allow_html=True)
        
        st.markdown("---")
        st.subheader(f"Risicobeoordeling Rapport - {datetime.now().strftime('%d-%m-%Y')}")
        
        beoordelingen = st.session_state.get('beoordelingen', {})
        
        if not beoordelingen:
            st.warning("Voer eerst een beoordeling uit in Tab 2.")
        else:
            for stofnaam, data in beoordelingen.items():
                st.markdown(f"### {stofnaam}")
                st.write(f"**CAS:** {data['cas']} | **H-zinnen:** {data['h_zinnen']} | **Klasse:** {data['klasse']}")
                st.write(f"**Dampdruk:** {data['dampdruk']} hPa")
                st.write(f"**Ventilatie:** {data['ventilatie'].split('/')[0].strip("(' ")}")
                st.write(f"**Technisch:** {data['technisch']}")
                st.write(f"**PBM:** {data['pbm']}")
                st.markdown("---")
