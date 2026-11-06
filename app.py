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
    
    halogeen_termen = ['chloro', 'bromo', 'fluoro', 'iodo', 'chloor', 'broom', 'jood', 'dichloormethaan', 'dichloromethane', 'chloroform', 'trichloorethyleen']
    if any(ht in naam for ht in halogeen_termen):
        return "Categorie 4: Halogeen bevattende organische stoffen"
    
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
            gevonden = vind_stoffen_in_tekst(geanalyseerde_tekst, df_stoffen, st.session_state['handmatige_stoffen_data'])
            if gevonden:
                st.success(f"Gevonden stoffen / Found substances: {', '.join(gevonden)}")
                st.session_state['gevonden_stoffen'] = gevonden
                st.info("Stoffen zijn hieronder geselecteerd. / Substances are selected below.")
            else:
                st.warning("Geen bekende stoffen gevonden. / No known substances found.")

    st.markdown("---")
    st.subheader("Stoffen Beheren & Selecteren / Manage & Select Substances")
    
    # 1. Eerst het keuzemenu tonen
    standaard_stoffen = st.session_state.get('gevonden_stoffen', [])
    gekozen_stoffen = st.multiselect("Stoffen voor dit protocol / Substances for this protocol:", complete_stoffen_lijst, default=[s for s in standaard_stoffen if s in complete_stoffen_lijst])
    
    # 2. Daarna pas het handmatig toevoegen veld
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

with tab2:
    st.header("Stap 1-3: Basisgegevens & STOP-strategie / Basic Data & STOP Strategy")
    
    beoordelingen = {}
    
    afval_categorieen = [
        "Categorie 1: Zuren anorganische stoffen in oplossing (pH < 7)",
        "Categorie 2: Alkalische anorganische stoffen in oplossing (pH > 7)",
        "Categorie 3: Halogeenvrije organische stoffen",
        "Categorie 4: Halogeen bevattende organische stoffen",
        "Categorie 5: Bijzondere afval stoffen (kwik, zware metalen, batterijen, spuitbussen)",
        "Categorie 6: Afvalstoffen met buitengewone risico's (ontplofbaar zoals pikrinezuur)"
    ]

    if not gekozen_stoffen:
        st.info("Ga naar Tab 1 om stoffen te analyseren of handmatig te selecteren.")

    for stof in gekozen_stoffen:
        if stof in st.session_state['handmatige_stoffen_data']:
            stof_data = st.session_state['handmatige_stoffen_data'][stof]
            is_handmatig = True
        else:
            match_row = df_stoffen[df_stoffen['SORTEERNAAM NL'] == stof]
            if not match_row.empty:
                stof_data = match_row.iloc[0]
                is_handmatig = stof_data.get('is_handmatig', False)
            else:
                stof_data = {}
                is_handmatig = False
            
        h_zinnen_db = stof_data.get('H-ZINNEN (TEKST', 'Geen') if hasattr(stof_data, 'get') else 'Geen'
        if not h_zinnen_db or h_zinnen_db == 'Geen':
            h_zinnen_db = stof_data.get('H-ZINNEN (TEKST)', 'Geen') if hasattr(stof_data, 'get') else 'Geen'
            
        cas_nummer = stof_data.get('CAS-NUMMER', 'Onbekend') if hasattr(stof_data, 'get') else 'Onbekend'
        db_cmr = stof_data.get('CMR', '') if hasattr(stof_data, 'get') else ''
        
        is_cmr = check_cmr_status(h_zinnen_db, db_cmr)
        
        geschatte_damp = schat_standaard_dampdruk(stof, h_zinnen_db)
        waarschijnlijk_vast = (geschatte_damp == 0.0) and ("water" not in stof.lower()) and ("buffer" not in stof.lower())
        
        voorgestelde_afval = bepaal_afvalcategorie(stof, h_zinnen_db)
        try:
            default_afval_idx = afval_categorieen.index(voorgestelde_afval)
        except ValueError:
            default_afval_idx = 2
        
        st.markdown(f"### ⚙️ {stof}")
        if is_handmatig: st.caption("*(Handmatig toegevoegde stof / Manually added substance)*")
        if is_cmr: st.error("⚠️ **Let op / Notice:** Deze stof is herkend als **CMR-stof / CMR Substance**!")
        st.info(f"**CAS:** {cas_nummer} | **H-zinnen / H-statements:** {h_zinnen_db}")
        
        c1, c2 = st.columns(2)
        with c1:
            is_vast = st.checkbox("Vaste stof (Firm granules) / Solid substance", value=waarschijnlijk_vast, key=f"vast_{stof}")
            st.caption("Controleer de dampdruk in het SDS rubriek 9 of GESTIS. (Check SDS section 9 for exact vapor pressure).")
            dampspanning = st.number_input("Dampdruk / Vapor pressure (hPa)", value=float(geschatte_damp), key=f"damp_{stof}")
        with c2:
            h = st.selectbox("Hoeveelheid / Quantity", [
                ("1. Closed system / Gesloten systeem", 0.0),
                ("2. Very small quantities (1 g or ml), pipetting, weighing / Zeer klein, pipetteren, wegen", 0.03),
                ("3. Small quantities (10 g or ml), pipette, weigh / Klein (max 10 g/ml), pipetteren, wegen", 0.1),
                ("4. Cleaning of small objects / Reinigen kleine objecten (max 100 g/ml)", 0.3),
                ("5. Stir, pour over, immerse / Roeren, overgieten, dompelen (max 1 kg/liter)", 1.0),
                ("6. Application, removal, coating, brushing / Aanbrengen, coaten (meer dan 1 kg/liter)", 3.0),
                ("7. Frothing, mixing / Schuimen, mengen (max 1 kg/liter)", 3.0),
                ("8. Mist / Nevel (meer dan 1 kg/liter)", 10.0)
            ], index=1, key=f"h_{stof}")
            
            v = st.selectbox("Ventilatie / Ventilation", [
                ("1. Work in a fume hood / Werk in een zuurkast", 0.03),
                ("2. Work with point extraction / Werk met puntafzuiging", 0.3),
                ("3. Work on the table / Werk op tafel", 1.0)
            ], index=0, key=f"v_{stof}")
            
            d = st.selectbox("Tijdsduur / Duration", [
                ("a. 1 to 30 minutes at a time / 1 tot 30 minuten per keer", 0.06),
                ("b. 0.5 to 2 hours at a time / 0.5 tot 2 uur per keer", 0.25),
                ("c. 2 to 4 hours at a time / 2 tot 4 uur per keer", 0.5),
                ("d. 4 to 8 hours at a time / 4 tot 8 uur per keer", 1.0)
            ], index=0, key=f"d_{stof}")
            
            f = st.selectbox("Frequentie / Frequency", [
                ("a. Once a year / Een keer per jaar", 0.01),
                ("b. Once a month / Een keer per maand", 0.05),
                ("c. Once every 2 weeks / Een keer per twee weken", 0.1),
                ("d. Once a week / Een keer per week", 0.2),
                ("e. 2 - 3 times a week / 2 tot 3 keer per week", 0.6),
                ("f. 4 - 5 times a week / 4 tot 5 keer per week", 1.0)
            ], index=3, key=f"f_{stof}")

        substitutie = st.text_input("Substitutie motivatie (Verplicht bij CMR) / Substitution motivation (Mandatory for CMR):", value="Essentieel voor onderzoek, alternatief niet mogelijk / Essential for research, alternative not possible.", key=f"sub_{stof}")
        technisch = st.text_input("Technisch / Technical:", value="Werken in zuurkast / Work in fume hood.", key=f"tech_{stof}")
        organisatorisch = st.text_input("Organisatorisch / Organizational:", value="Beperkte tijd / Limited time.", key=f"org_{stof}")
        pbm = st.text_input("PBM / PPE:", value="Labjas, bril, handschoenen / Lab coat, goggles, gloves.", key=f"pbm_{stof}")
        
        afvalstroom = st.selectbox("Afvalcategorie / Waste category", afval_categorieen, index=default_afval_idx, key=f"afval_{stof}")
        
        st.divider()
        beoordelingen[stof] = {
            'cas': cas_nummer, 'h_zinnen': h_zinnen_db, 'damp': dampspanning, 'vast': is_vast,
            'h_val': h, 'v_val': v, 'd_val': d, 'f_val': f, 'is_cmr': is_cmr,
            'sub': substitutie, 'tech': technisch, 'org': organisatorisch, 'pbm': pbm,
            'afval': afvalstroom, 'is_handmatig': is_handmatig
        }

with tab3:
    st.markdown(f"## Standard Operating Procedure (SOP)")
    st.markdown(f"**Document-ID:** SOP-{datetime.now().strftime('%Y-%m-%d')}")
    st.markdown("---")
    st.markdown("### 1. Werkprotocol / Work Protocol")
    if 'protocol_tekst' in st.session_state and st.session_state['protocol_tekst']:
        protocol_geannoteerd = st.session_state['protocol_tekst'].replace('\n', '\n\n')
        for stof in gekozen_stoffen:
            patroon = re.compile(re.escape(stof), re.IGNORECASE)
            protocol_geannoteerd = patroon.sub(f"**🧪 {stof}**", protocol_geannoteerd)
        st.markdown(protocol_geannoteerd)
    else: st.markdown("*Geen protocol geüpload. / No protocol uploaded.*")
    
    st.markdown("---")
    st.markdown("### 2. Chemische Risicobeoordelingen / Chemical Risk Assessments")
    if not gekozen_stoffen: st.markdown("*Geen stoffen geselecteerd. / No substances selected.*")
        
    for index, stof in enumerate(gekozen_stoffen):
        b = beoordelingen[stof]
        Ei, score_EM, exp_klasse = bereken_blootstellingsscore(b['damp'], b['vast'], b['h_val'][1], 1, b['v_val'][1], b['d_val'][1], b['f_val'][1])
        coshh = bereken_coshh_klasse(b['h_zinnen'])
        risico_kleur = bepaal_risico_kleur(coshh, exp_klasse, b['is_cmr'])
        
        st.markdown(f"#### 2.{index+1} {stof} (CAS: {b['cas']})")
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
        st.markdown(f"**Afvalverwerking / Waste Disposal:** ♻️ *{b['afval']}*")
        st.write("") 

    st.markdown("---")
    st.markdown("### 3. Calamiteiten & Spill Protocol / Emergencies & Spill Protocol")
    spill_bilingual = """- **Kleine spill / Small spill (< 50 ml):** Ruim direct op met absorptiematerialen (spill kit). Gooi afval in chemische afvalbak. / Clean up immediately using absorption materials (spill kit). Dispose of waste in chemical waste bin.
- **Grote spill / Large spill (> 250 ml vluchtige stoffen / volatile substances):** Ruim dit niet zelf op zonder adembescherming. Ontruim het lab, sluit deuren, waarschuw BHV. / Do not clean this up yourself without respiratory protection. Evacuate the lab, close doors, alert emergency response (BHV)."""
    st.markdown(spill_bilingual)
    st.divider()
    st.caption("Druk op Ctrl+P of Cmd+P in je browser om dit complete SOP document als PDF op te slaan. / Press Ctrl+P or Cmd+P in your browser to save this complete SOP document as a PDF.")
