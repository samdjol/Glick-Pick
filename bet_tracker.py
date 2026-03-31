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

# --- 3. SUPABASE API (Over/Under Search) ---
@st.cache_data(ttl=3600)
def get_glicks_picks():
    today_str = datetime.datetime.now(NYC_TZ).strftime("%Y-%m-%d")
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
        if not isinstance(data, list): return []

        picks = []
        for item in data:
            player = item.get("player_name") or item.get("player") or "Unknown"
            call = str(item.get("direction") or item.get("call") or item.get("side") or "").upper()
            line = str(item.get("line", ""))
            market = item.get("market") or item.get("prop") or ""
            picks.append({"Event": f"{player}: {call} {line} {market}"})
        return picks
    except:
        return []

# --- 4. DATA HELPERS ---
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

def set_bankroll(amount, user_prefix):
    st.session_state.bankroll = amount
    ws = get_ws_smart(sheet, f"{user_prefix}_bankroll")
    ws.update_acell('B1', amount)

def load_dropdowns():
    ws = sheet.worksheet("Dropdowns")
    books = ws.col_values(1)[1:]
    states = ws.col_values(2)[1:]
    return {"books": [b for b in books if b.strip()], "states": [s for s in states if s.strip()]}

def save_dropdowns(data):
    ws = sheet.worksheet("Dropdowns")
    ws.clear()
    max_len = max(len(data['books']), len(data['states']))
    books = data['books'] + [''] * (max_len - len(data['books']))
    states = data['states'] + [''] * (max_len - len(data['states']))
    rows = [['Books', 'States']] + [[books[i], states[i]] for i in range(max_len)]
    ws.update(values=rows, range_name='A1')

def american_to_decimal(odds):
    try:
        val = int(odds)
        if val > 0: return (val / 100) + 1
        return (100 / abs(val)) + 1
    except: return 1.91

# --- 5. AUTHENTICATION ---
credentials = load_credentials()
authenticator = stauth.Authenticate(credentials, "bet_tracker_cookie", "secure_v3_glick_pick_long_key_2026_nyc", cookie_expiry_days=30)

login_placeholder = st.empty()
if not st.session_state.get("authentication_status"):
    with login_placeholder.container():
        authenticator.login(location='main')

if st.session_state["authentication_status"]:
    login_placeholder.empty()
    if 'dashboard_entered' not in st.session_state:
        st.success(f"Logged in!")
        if st.button("🚀 Enter Dashboard", width='stretch'):
            st.session_state['dashboard_entered'] = True
            st.rerun()
        st.stop()

    username = st.session_state["username"]
    name = st.session_state["name"]
    if 'bankroll' not in st.session_state: st.session_state.bankroll = load_bankroll(username)
    df = load_data(username)

    # --- SIDEBAR: REINSTATED BALANCE ADJUSTMENT ---
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.title(f"Welcome, {name}")
    st.sidebar.metric("💰 Bankroll", f"${st.session_state.bankroll:,.2f}")

    with st.sidebar.expander("⚙️ Adjust Balance"):
        adj_action = st.radio("Action", ["Add/Remove", "Set Exact"], horizontal=True)
        if adj_action == "Add/Remove":
            adj_val = st.number_input("Amount ($)", value=0.0, step=10.0)
            if st.button("Update Balance"):
                update_bankroll(adj_val, username); st.rerun()
        else:
            exact_val = st.number_input("Exact ($)", value=float(st.session_state.bankroll))
            if st.button("Set Balance"):
                set_bankroll(exact_val, username); st.rerun()

    st.sidebar.divider()
    st.sidebar.header("🧮 Kelly Calculator")
    if 'odds_input' not in st.session_state: st.session_state.odds_input = -110
    if 'edge_input' not in st.session_state: st.session_state.edge_input = 15.0
    input_odds = st.sidebar.number_input("American Odds", step=1, key="odds_input")
    edge_pct = st.sidebar.number_input("Edge (%)", 0.0, 100.0, step=0.1, key="edge_input")
    k_sel = st.sidebar.radio("Multiplier", ["Full", "Half", "Quarter"], index=2, horizontal=True)
    k_map = {"Full": 1.0, "Half": 0.5, "Quarter": 0.25}
    
    dec_odds = american_to_decimal(input_odds)
    full_k = (edge_pct/100) / (dec_odds - 1) if (dec_odds - 1) != 0 else 0
    suggested_stake = round(full_k * k_map[k_sel] * st.session_state.bankroll, 2)
    st.sidebar.metric("Suggested Stake", f"${suggested_stake:,.2f}")

    # --- 6. NAVIGATION CONTROLLER ---
    nav_labels = ["🎯 Glick's Picks", "📝 Log New Bet", "📊 Dashboard", "🗄️ History"]
    if st.session_state.get('redirect_to'):
        st.session_state['nav_bar'] = st.session_state['redirect_to']
        del st.session_state['redirect_to']
    if 'nav_bar' not in st.session_state:
        st.session_state['nav_bar'] = nav_labels[0]

    active_page = st.segmented_control("Navigation", nav_labels, selection_mode="single", key="nav_bar", label_visibility="collapsed")
    st.divider()

    # --- 7. CONTENT ---
    if active_page == "🎯 Glick's Picks":
        st.subheader("Latest Picks")
        picks = get_glicks_picks()
        if not picks: st.info("No picks found for today.")
        else:
            for p in picks:
                with st.container(border=True):
                    ca, cb = st.columns([4, 1])
                    ca.write(f"**{p['Event']}**")
                    if cb.button("Track", key=f"api_{p['Event']}", width='stretch'):
                        st.session_state.autofill_event = p['Event']
                        st.session_state['redirect_to'] = "📝 Log New Bet"
                        st.rerun()

    elif active_page == "📝 Log New Bet":
        st.subheader("Enter Wager Details")
        dropdowns = load_dropdowns()
        default_ev = st.session_state.get('autofill_event', "")
        with st.form("bet_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            date = c1.date_input("Date", datetime.datetime.now(NYC_TZ).date())
            book = c2.selectbox("Sportsbook", dropdowns["books"])
            state = c3.selectbox("State", dropdowns["states"])
            c4, c5, c6 = st.columns(3)
            event = c4.text_input("Event / Matchup", value=default_ev)
            stake = c5.number_input("Actual Stake ($)", value=float(suggested_stake))
            res = c6.selectbox("Status", ["Pending", "Win", "Loss", "Push"])
            if st.form_submit_button("Save Bet to Database"):
                if event.strip():
                    p = 0
                    if res == "Win": p = round(stake * (dec_odds - 1), 2)
                    elif res == "Loss": p = -stake
                    new_row = {"Date": date, "Book": book, "State": state, "Event": event, "Odds": input_odds, "Edge": edge_pct/100, "Stake": stake, "Result": res, "Profit": p}
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    save_data(df, username)
                    if p != 0: update_bankroll(p, username)
                    st.session_state.autofill_event = ""
                    st.toast("Logged!", icon="✅"); st.rerun()

        with st.expander("⚙️ Manage Sportsbooks & States"):
            mc1, mc2 = st.columns(2)
            with mc1:
                nb = st.text_input("New Book")
                if st.button("➕ Add Book") and nb:
                    dropdowns["books"].append(nb); save_dropdowns(dropdowns); st.rerun()
            with mc2:
                ns = st.text_input("New State")
                if st.button("➕ Add State") and ns:
                    dropdowns["states"].append(ns); save_dropdowns(dropdowns); st.rerun()

    elif active_page == "📊 Dashboard":
        if not df.empty:
            st.metric("Total P/L", f"${df['Profit'].sum():,.2f}")
            fdf = df.sort_values('Date')
            fdf['Cumulative Profit'] = fdf['Profit'].cumsum()
            st.plotly_chart(px.line(fdf, x='Date', y='Cumulative Profit', title="Profit Trend", markers=True), width='stretch')

    elif active_page == "🗄️ History":
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
        settled = df[df['Result'] != 'Pending'].sort_values('Date', ascending=False)
        st.dataframe(settled, width='stretch', hide_index=True)
        
        with st.expander("🗑️ Delete/Refund a Settled Bet"):
            settled_list = {f"{r['Date']} | {r['Event']} (${r['Profit']})": idx for idx, r in settled.iterrows()}
            target = st.selectbox("Select bet to remove:", [""] + list(settled_list.keys()))
            if st.button("Delete & Reverse Bankroll") and target:
                idx_to_del = settled_list[target]
                update_bankroll(-df.at[idx_to_del, 'Profit'], username)
                df = df.drop(idx_to_del)
                save_data(df, username); st.rerun()

elif st.session_state["authentication_status"] is False:
    st.error("Incorrect credentials")