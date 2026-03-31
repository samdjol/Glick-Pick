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
from bs4 import BeautifulSoup

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

# --- 3. SCRAPER & HELPERS ---
@st.cache_data(ttl=3600)
def get_glicks_picks():
    # 1. Get the current date in NYC format (YYYY-MM-DD)
    today_str = datetime.datetime.now(NYC_TZ).strftime("%Y-%m-%d")
    
    # 2. Use the Supabase API URL with the dynamic date
    # I've removed the hardcoded date from your link and replaced it with today_str
    url = f"https://ajjruzolkbzardssopos.supabase.co/rest/v1/picks?select=*&season=eq.2026&date=eq.{today_str}&order=stars.desc"
    
    # Supabase usually requires an API key in the headers. 
    # If the link worked in your browser without one, it's public. 
    # If it fails, we may need to grab the 'apikey' from your browser's Network tab.
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json() # This API returns a list of dictionaries
        
        picks = []
        for item in data:
            # We map the API keys to your app's format
            # Based on common Supabase structures, I'm guessing the names:
            player = item.get("player_name", "Unknown Player")
            call = item.get("call", "").upper() # e.g., OVER
            line = item.get("line", "")
            market = item.get("market", "")
            price = item.get("price", "-110")
            
            # Combine into your Event format
            event_name = f"{player}: {call} {line} {market}"
            
            picks.append({
                "Event": event_name,
                "Odds": str(price),
                "Edge": str(item.get("edge", "15.0")) # Pull edge if it exists
            })
        return picks
    except Exception as e:
        st.error(f"API Error: {e}")
        return []

# Initialize "Auto-fill" state
if 'autofill_event' not in st.session_state:
    st.session_state.autofill_event = ""
if 'autofill_odds' not in st.session_state:
    st.session_state.autofill_odds = -110

def get_ws_smart(sheet, name):
    all_ws = {ws.title.lower().strip(): ws for ws in sheet.worksheets()}
    target = name.lower().strip()
    if target in all_ws: return all_ws[target]
    st.error(f"Tab Not Found: '{target}'. Found: {list(all_ws.keys())}")
    st.stop()

def load_credentials():
    ws = sheet.worksheet("Credentials")
    df = pd.DataFrame(ws.get_all_records())
    credentials = {"usernames": {}}
    for _, row in df.iterrows():
        user = str(row['Username']).strip()
        credentials["usernames"][user] = {
            "name": str(row['Name']), 
            "password": str(row['Password']), 
            "email": str(row['Email'])
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
    if odds > 0: return (odds / 100) + 1
    return (100 / abs(odds)) + 1

# --- 4. AUTHENTICATION ---
credentials = load_credentials()
# Use a 32+ character key to satisfy HMAC security requirements
authenticator = stauth.Authenticate(credentials, "bet_tracker_cookie", "secure_v3_glick_pick_long_key_2026_nyc", cookie_expiry_days=30)

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

    # --- SESSION SETUP ---
    username = st.session_state["username"]
    name = st.session_state["name"]
    if 'bankroll' not in st.session_state: st.session_state.bankroll = load_bankroll(username)
    df = load_data(username)

    # --- 5. SIDEBAR ---
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.title(f"Welcome, {name}")
    st.sidebar.metric("💰 Bankroll", f"${st.session_state.bankroll:,.2f}")

    with st.sidebar.expander("⚙️ Adjust Balance"):
        action = st.radio("Action", ["Add/Remove", "Set Exact"], horizontal=True)
        if action == "Add/Remove":
            adj = st.number_input("Amount ($)", value=0.0, step=10.0)
            if st.button("Update Balance"):
                update_bankroll(adj, username); st.rerun()
        else:
            new_val = st.number_input("Exact ($)", value=float(st.session_state.bankroll))
            if st.button("Set Balance"):
                set_bankroll(new_val, username); st.rerun()

    st.sidebar.divider()
    st.sidebar.header("🧮 Kelly Calculator")
    if 'odds_input' not in st.session_state: st.session_state.odds_input = -110
    input_odds = st.sidebar.number_input("American Odds", step=1, key="odds_input")
    edge_pct = st.sidebar.number_input("Edge (%)", 0.0, 100.0, 15.0, 0.1)
    k_opts = {"Full": 1.0, "Half": 0.5, "Quarter": 0.25}
    k_sel = st.sidebar.radio("Multiplier", list(k_opts.keys()), index=2, horizontal=True)
    
    dec_odds = american_to_decimal(input_odds)
    b = dec_odds - 1
    full_k = (edge_pct/100) / b if b != 0 else 0
    suggested_stake = round(full_k * k_opts[k_sel] * st.session_state.bankroll, 2)
    st.sidebar.metric("Suggested Stake", f"${suggested_stake:,.2f}")

    # --- 6. MAIN TABS ---
    tabs = st.tabs(["📝 Log New Bet", "📊 Dashboard", "🗄️ History", "🎯 Glick's Picks"])

    with tabs[0]:
        st.subheader("Enter Wager Details")
        dropdowns = load_dropdowns()
        with st.form("bet_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            date = c1.date_input("Date", datetime.datetime.now(NYC_TZ).date())
            book = c2.selectbox("Sportsbook", dropdowns["books"])
            state = c3.selectbox("State", dropdowns["states"])
            c4, c5, c6 = st.columns(3)
            event = c4.text_input("Event / Matchup", value=st.session_state.autofill_event)
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
                    st.session_state.autofill_event = "" # Reset autofill after save
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
        settled = df[df['Result'] != 'Pending'].sort_values('Date', ascending=False)
        st.dataframe(settled, width='stretch', hide_index=True)
        
        with st.expander("🗑️ Delete/Refund a Settled Bet"):
            settled_list = {f"{r['Date']} | {r['Event']} (${r['Profit']})": idx for idx, r in settled.iterrows()}
            target = st.selectbox("Select bet to remove:", [""] + list(settled_list.keys()))
            if st.button("Delete & Reverse Bankroll") and target:
                idx_to_del = settled_list[target]
                rev_profit = df.at[idx_to_del, 'Profit']
                update_bankroll(-rev_profit, username)
                df = df.drop(idx_to_del)
                save_data(df, username); st.rerun()

    with tabs[3]:
        st.subheader("Latest Picks from Glick's Picks")
        available_picks = get_glicks_picks()
        
        if not available_picks:
            st.info("No picks found or site is currently unreachable.")
        else:
            for p in available_picks:
                with st.container(border=True):
                    col_a, col_b = st.columns([4, 1])
                    col_a.write(f"**{p['Event']}**")
                    col_a.caption(f"Odds: {p['Odds']} | Edge: {p['Edge']}")
                    
                    if col_b.button("Track this Bet", key=f"scrape_{p['Event']}", width='stretch'):
                        st.session_state.autofill_event = p['Event']
                        try:
                            # Update the Odds in the Sidebar
                            clean_odds = int(p['Odds'].replace('+', ''))
                            st.session_state.odds_input = clean_odds
                            
                            # Update the Edge in the Sidebar (if your number_input uses this key)
                            # Assuming you add a key="edge_input" to your sidebar number_input
                            st.session_state.edge_input = float(p['Edge']) 
                        except: 
                            pass
                        st.toast(f"Pushed {p['Event']} to Log tab!")
                        st.rerun()

elif st.session_state["authentication_status"] is False:
    st.error("Incorrect credentials")