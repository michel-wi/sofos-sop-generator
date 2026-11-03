import streamlit as st
import pandas as pd
from supabase import create_client, Client
import re

st.set_page_config(page_title="Risicobeoordeling SOFOS", layout="wide")

# --- 1. Inlogscherm ---
def check_password():
    def password_entered():
        if st.session_state["username"] == "admin" and st.session_state["password"] == "veilig2024":
            st.session_state["password_correct"] = True
            del st.session_state["password"] 
        else:
            st.session_state["password_correct"] = False
    if "password_correct" not in st.session_state:
        st.header("Login vereist")
        st.text_input("Gebruikersnaam", key="username")
        st.text_input("Wachtwoord", type="password", key="password", on_change=password_entered)
        return False
    elif not st.session_state["password_correct"]:
        st.error("Incorrecte gebruikersnaam of wachtwoord.")
        st.text_input("Gebruikersnaam", key="username")
        st.text_input("Wachtwoord", type="password", key="password", on_change=password_entered)
        return False
    return True

if not check_password():
    st.stop()

# --- 2. Supabase Koppeling & Paginatie ---
@st.cache_data(ttl=600)
def laad_chemicalien():
    # In de Cloudomgeving gebruiken we 'secrets' voor de veiligheid. 
    # Mocht je lokaal testen, vul hier dan tijdelijk je URL en KEY weer in.
    SUPABASE_URL = st.secrets.get("SUPABASE_URL", "Vul_hier_URL_in_voor_lokaal_testen")
    SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "Vul_hier_KEY_in_voor_lokaal_testen")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    all_data = []
    offset = 0
    limit = 1000
    try:
        while True:
            response = supabase.table('chemicalien').select("*").range(offset, offset + limit - 1).execute()
            data = response.data
            if not data:
                break
            all_data.extend(data)
            if len(data) < limit:
                break
            offset += limit
        return pd.DataFrame(all_data)
    except Exception as e:
        st.error(f"Fout bij inladen database: {e}")
        return pd.DataFrame()

# --- 3. Rekenlogica (Exact volgens Excel parameters) ---
def bereken_blootstellingsscore(dampspanning, is_vast, hoeveelheid, locatie, ventilatie, duur, frequentie):
    # Intrinsieke emissie (Ei)
    if is_vast:
        Ei = 0.0 # Vereenvoudigde aanname, afhankelijk van stofheid (bijv. Firm granules = 0.01)
    else:
        if dampspanning >= 800: Ei = 1.00000
        elif dampspanning >= 200: Ei = 0.80000
        elif dampspanning >= 50: Ei = 0.19000
        else: Ei = 0.02267 # Voor vloeistoffen < 50 hPa
        
    # EM_week formule
    score_em = Ei * hoeveelheid * locatie * ventilatie * duur * frequentie
    
    # Blootstellingsklasse bepalen
    if score_em <= 0.00002: klasse = 1
    elif score_em <= 0.002: klasse = 2
    elif score_em <= 0.2: klasse = 3
    else: klasse = 4
        
    return Ei, score_em, klasse

def bereken_coshh_klasse(h_zinnen_tekst):
    if not h_zinnen_tekst or pd.isna(h_zinnen_tekst): 
        return "A"
    
    h_zinnen = str(h_zinnen_tekst).upper()
    h_lijst = re.findall(r'H\d{3}[A-Z]*', h_zinnen)
    
    hoogste_klasse = "A"
    
    # COSHH Indeling
    klasse_E = ["H340", "H350", "H350I", "H330"] # Inhalatie 1
    klasse_D = ["H314", "H318", "H334", "H341", "H351", "H360", "H372"]
    klasse_C = ["H311", "H314", "H315", "H319", "H320", "H331", "H335", "H361", "H362", "H370", "H373"]
    klasse_B = ["H200", "H220", "H221", "H222", "H223", "H224", "H225", "H226", "H228", "H300", "H301", "H302", "H303", "H304", "H305", "H310", "H311", "H312", "H313", "H315", "H316", "H317", "H319", "H320", "H331", "H332", "H333", "H334", "H335", "H336", "H371", "H400"]
    
    for h in h_lijst:
        if h in klasse_E: return "E"
        if h in klasse_D and hoogste_klasse not in ["E"]: hoogste_klasse = "D"
        if h in klasse_C and hoogste_klasse not in ["E", "D"]: hoogste_klasse = "C"
        if h in klasse_B and hoogste_klasse not in ["E", "D", "C"]: hoogste_klasse = "B"
        
    return hoogste_klasse

# --- 4. Applicatie Interface ---
st.title("Risicobeoordeling SOFOS (Cloud Editie)")

df_stoffen = laad_chemicalien()

if not df_stoffen.empty:
    st.success(f"{len(df_stoffen)} stoffen succesvol geladen.")
    
    stoffen_lijst = [s for s in df_stoffen['SORTEERNAAM NL'].unique() if pd.notnull(s)]
    stoffen_lijst.sort()
    
    # Zoekbalk
    gekozen_stof = st.selectbox("Zoek of selecteer een gevaarlijke stof:", stoffen_lijst)
    stof_data = df_stoffen[df_stoffen['SORTEERNAAM NL'] == gekozen_stof].iloc[0]
    
    h_zinnen_db = stof_data.get('H-ZINNEN (TEKST)', 'Geen')
    st.info(f"**Geregistreerde H-zinnen (volgens database):** {h_zinnen_db}")
    
    st.divider()
    
    # Instellingen
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Stofeigenschappen")
        is_vast = st.checkbox("Dit is een vaste stof (bijv. poeder, korrels)", value=False)
        dampspanning = st.number_input("Dampdruk (hPa) - Raadpleeg SDS/Gestis", value=0.0, step=1.0)
        
    with col2:
        st.subheader("Werkomstandigheden")
        h = st.selectbox("Hoeveelheid", [("Zeer kleine hoeveelheden (max 1g/ml)", 0.03), ("Kleine hoeveelheden (max 10g/ml)", 0.1), ("Schoonmaken kleine objecten (max 100g/ml)", 0.3)])
        v = st.selectbox("Ventilatie", [("Werk in een zuurkast", 0.03), ("Werk met puntafzuiging", 0.3), ("Werk op tafel", 1)])
        d = st.selectbox("Tijdsduur", [("1 tot 30 minuten per keer", 0.06), ("0.5 tot 2 uur per keer", 0.25), ("2 tot 4 uur per keer", 0.5)])
        f = st.selectbox("Frequentie", [("1 keer per week", 0.2), ("2 - 3 keer per week", 0.6), ("4 - 5 keer per week", 1)])

    if st.button("Bereken Risicoprioriteit", type="primary"):
        # Locatie in lab = 1
        Ei, score_EM, exp_klasse = bereken_blootstellingsscore(dampspanning, is_vast, h[1], 1, v[1], d[1], f[1])
        coshh = bereken_coshh_klasse(h_zinnen_db)
        
        st.divider()
        st.header(f"Beoordeling: {gekozen_stof}")
        c1, c2, c3 = st.columns(3)
        
        c1.metric("Blootstellingsscore (EM_week)", f"{score_EM:.7f}")
        c2.metric("Blootstellingsklasse", f"Klasse {exp_klasse}")
        c3.metric("COSHH Gevaarklasse", coshh)
        
        # Test voor Isopentaan validatie: als Ei=1.0, h=0.03, loc=1, vent=0.03, duur=0.06, freq=0.2 dan EM = 0.0000108
        if "ISOPENTAAN" in gekozen_stof.upper() and f"{score_EM:.7f}" == "0.0000108":
            st.success("Validatie succesvol: De berekening komt exact overeen met het originele Isopentaan rekenblad (0.0000108).")

else:
    st.warning("Database kon niet worden geladen. Controleer je Supabase inloggegevens en netwerkverbinding.")