import streamlit as st
import pandas as pd
import datetime
from zoneinfo import ZoneInfo
import plotly.express as px
import json
import gspread
from google.oauth2.service_account import Credentials
import streamlit_authenticator as stauth
import requests

# --- 1. PAGE CONFIG & TIMEZONE ---
st.set_page_config(page_title="Glick Pick Tracker", layout="wide")
NYC_TZ = ZoneInfo("America/New_York")

# --- 2. GOOGLE SHEETS CONNECTION ---
@st.cache_resource
def init_gsheets():
    creds_json = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_url(st.secrets["SHEET_URL"])

sheet = init_gsheets()

# --- 3. SUPABASE API INTEGRATION ---
@st.cache_data(ttl=3600)
def get_glicks_picks():
    # Dynamic date for NYC
    today_str = datetime.datetime.now(NYC_TZ).strftime("%Y-%m-%d")
    
    # Your specific Supabase URL and Key
    url = f"https://ajjruzolkbzardssopos.supabase.co/rest/v1/picks?select=*&season=eq.2026&date=eq.{today_str}&order=stars.desc"
    apikey = "sb_publishable_aAFvyqUjJFYQsuG8GY2KTA_U4SLd545"
    
    headers = {
        "apikey": apikey,
        "Authorization": f"Bearer {apikey}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()

        # Check if the API returned an error message instead of a list
        if not isinstance(data, list):
            # If it's a dict, it might be an error message
            error_msg = data.get('message', 'Unknown API Error')
            return []

        picks = []
        for item in data:
            # Smart mapping: tries multiple common key names for betting APIs
            player = item.get("player_name") or item.get("player") or item.get("name") or "Unknown"
            call = str(item.get("call", "") or item.get("side", "")).upper()
            line = str(item.get("line", ""))
            market = item.get("market", "") or item.get("prop", "")
            price = str(item.get("price") or item.get("odds") or "-110")
            edge = str(item.get("edge", "15.0"))
            
            picks.append({
                "Event": f"{player}: {call} {line} {market}",
                "Odds": price,
                "Edge": edge
            })
        return picks
    except Exception as e:
        return []

# --- 4. HELPER FUNCTIONS ---
def get_ws_smart(sheet, name):
    all_ws = {ws.title.lower().strip(): ws for ws in sheet.worksheets()}
    target = name.lower().strip()
    if target in all_ws: return all_ws[target]
    st.error(f"Tab Not Found: '{target}'")
    st.stop()

def load_credentials():
    ws = sheet.worksheet("Credentials")
    df = pd.DataFrame(ws.get_all_records())
    credentials = {"usernames": {}}
    for _, row in df.iterrows():
        user = str(row['Username']).strip()
        credentials["usernames"][user] = {
            "name": str(row['Name']), "password": str(row['Password']), "email": str(row['Email'])
        }
    return credentials

def load_data(user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_history")
    try:
        data = ws.get_all_records()
        return pd.DataFrame(data) if data else pd.DataFrame(columns=['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit'])
    except:
        return pd.DataFrame(columns=['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit'])

def save_data(df, user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_history")
    ws.clear()
    df_save = df.copy()
    df_save['Date'] = pd.to_datetime(df_save['Date']).dt.strftime('%Y-%m-%d')
    ws.update(values=[df_save.columns.values.tolist()] + df_save.fillna('').values.tolist(), range_name='A1')

def load_bankroll(user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_bankroll")
    val = ws.acell('B1').value
    return float(val) if val else 1000.0

def update_bankroll(amount, user_prefix):
    st.session_state.bankroll += amount
    ws = get_ws_smart(sheet, f"{user_prefix}_bankroll")
    ws.update_acell('B1', st.session_state.bankroll)

def load_dropdowns():
    ws = sheet.worksheet("Dropdowns")
    books = ws.col_values(1)[1:]
    states = ws.col_values(2)[1:]
    return {"books": [b for b in books if b.strip()], "states": [s for s in states if s.strip()]}

def american_to_decimal(odds):
    try:
        val = int(odds)
        if val > 0: return (val / 100) + 1
        return (100 / abs(val)) + 1
    except: return 1.91

# --- 5. AUTHENTICATION ---
credentials = load_credentials()
# Long key to fix the InsecureKeyLengthWarning
authenticator = stauth.Authenticate(credentials, "bet_tracker_cookie", "32char_minimum_secret_key_for_HMAC_SHA256_2026", cookie_expiry_days=30)

login_placeholder = st.empty()
if not st.session_state.get("authentication_status"):
    with login_placeholder.container():
        authenticator.login(location='main')

if st.session_state["authentication_status"]:
    login_placeholder.empty()
    if 'dashboard_entered' not in st.session_state:
        st.success(f"Logged in as {st.session_state['name']}")
        if st.button("🚀 Enter Dashboard", width='stretch'):
            st.session_state['dashboard_entered'] = True
            st.rerun()
        st.stop()

    username = st.session_state["username"]
    name = st.session_state["name"]
    if 'bankroll' not in st.session_state: st.session_state.bankroll = load_bankroll(username)
    df = load_data(username)

    # --- SIDEBAR ---
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.title(f"Welcome, {name}")
    st.sidebar.metric("💰 Bankroll", f"${st.session_state.bankroll:,.2f}")

    with st.sidebar.expander("⚙️ Adjust Balance"):
        adj_action = st.radio("Action", ["Add/Remove", "Set Exact"], horizontal=True)
        if adj_action == "Add/Remove":
            adj = st.number_input("Amount ($)", value=0.0, step=10.0)
            if st.button("Update Balance"):
                update_bankroll(adj, username); st.rerun()

    st.sidebar.divider()
    st.sidebar.header("🧮 Kelly Calculator")
    if 'odds_input' not in st.session_state: st.session_state.odds_input = -110
    input_odds = st.sidebar.number_input("American Odds", step=1, key="odds_input")
    edge_pct = st.sidebar.number_input("Edge (%)", 0.0, 100.0, 15.0, 0.1, key="edge_input")
    k_opts = {"Full": 1.0, "Half": 0.5, "Quarter": 0.25}
    k_sel = st.sidebar.radio("Multiplier", list(k_opts.keys()), index=2, horizontal=True)
    
    dec_odds = american_to_decimal(input_odds)
    full_k = (edge_pct/100) / (dec_odds - 1) if (dec_odds - 1) != 0 else 0
    suggested_stake = round(full_k * k_opts[k_sel] * st.session_state.bankroll, 2)
    st.sidebar.metric("Suggested Stake", f"${suggested_stake:,.2f}")

    # --- 6. MAIN TABS (4 TABS) ---
    tabs = st.tabs(["📝 Log New Bet", "📊 Dashboard", "🗄️ History", "🎯 Glick's Picks"])

    with tabs[0]:
        st.subheader("Enter Wager Details")
        dropdowns = load_dropdowns()
        
        # Pull autofill from session state
        default_event = st.session_state.get('autofill_event', "")
        
        with st.form("bet_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            date = c1.date_input("Date", datetime.datetime.now(NYC_TZ).date())
            book = c2.selectbox("Sportsbook", dropdowns["books"])
            state = c3.selectbox("State", dropdowns["states"])
            c4, c5, c6 = st.columns(3)
            event = c4.text_input("Event / Matchup", value=default_event)
            final_stake = c5.number_input("Actual Stake ($)", value=float(suggested_stake))
            result = c6.selectbox("Status", ["Pending", "Win", "Loss", "Push"])
            
            if st.form_submit_button("Save Bet to Database"):
                if event.strip():
                    p = 0
                    if result == "Win": p = round(final_stake * (dec_odds - 1), 2)
                    elif result == "Loss": p = -final_stake
                    new_row = {"Date": date, "Book": book, "State": state, "Event": event, "Odds": input_odds, "Edge": edge_pct/100, "Stake": final_stake, "Result": result, "Profit": p}
                    df_new = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    save_data(df_new, username)
                    if p != 0: update_bankroll(p, username)
                    st.session_state.autofill_event = ""
                    st.toast("Logged!", icon="✅"); st.rerun()

    with tabs[1]:
        if not df.empty:
            st.metric("Total P/L", f"${df['Profit'].sum():,.2f}")
            filtered_df = df.sort_values('Date')
            filtered_df['Cumulative Profit'] = filtered_df['Profit'].cumsum()
            fig = px.line(filtered_df, x='Date', y='Cumulative Profit', title="Profit Trend", markers=True)
            st.plotly_chart(fig, width='stretch')

    with tabs[2]:
        st.subheader("🏟️ Active Wagers")
        pending = df[df['Result'] == 'Pending']
        for i, row in pending.iterrows():
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([3, 1, 1, 0.5])
                col1.write(f"**{row['Event']}** | {row['Book']}")
                if col2.button("✅ Win", key=f"w{i}"):
                    p = row['Stake'] * (american_to_decimal(row['Odds']) - 1)
                    df.at[i, 'Result'], df.at[i, 'Profit'] = 'Win', round(p, 2)
                    save_data(df, username); update_bankroll(p, username); st.rerun()
                if col3.button("❌ Loss", key=f"l{i}"):
                    df.at[i, 'Result'], df.at[i, 'Profit'] = 'Loss', -row['Stake']
                    save_data(df, username); update_bankroll(-row['Stake'], username); st.rerun()
                if col4.button("🗑️", key=f"d{i}"):
                    df = df.drop(i); save_data(df, username); st.rerun()
        st.divider()
        st.subheader("📜 History")
        st.dataframe(df[df['Result'] != 'Pending'].sort_values('Date', ascending=False), width='stretch', hide_index=True)

    with tabs[3]:
        st.subheader("Latest Picks from Supabase")
        available_picks = get_glicks_picks()
        if not available_picks:
            st.info("No picks found for today.")
        else:
            for p in available_picks:
                with st.container(border=True):
                    cola, colb = st.columns([4, 1])
                    cola.write(f"**{p['Event']}**")
                    cola.caption(f"Odds: {p['Odds']} | Edge: {p['Edge']}%")
                    if colb.button("Track", key=f"api_{p['Event']}", width='stretch'):
                        st.session_state.autofill_event = p['Event']
                        try:
                            st.session_state.odds_input = int(p['Odds'].replace('+', ''))
                            st.session_state.edge_input = float(p['Edge'])
                        except: pass
                        st.toast("Moved to Log tab!")
                        st.rerun()

elif st.session_state["authentication_status"] is False:
    st.error("Incorrect credentials")