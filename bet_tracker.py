import streamlit as st
import pandas as pd
import datetime
import plotly.express as px
import json
import gspread
from google.oauth2.service_account import Credentials
import streamlit_authenticator as stauth

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="Glick Pick Tracker", layout="wide")

# --- 2. GOOGLE SHEETS CONNECTION ---
@st.cache_resource
def init_gsheets():
    creds_json = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_url(st.secrets["SHEET_URL"])

sheet = init_gsheets()

# --- 3. HELPER FUNCTIONS ---
def load_credentials():
    ws = sheet.worksheet("Credentials")
    df = pd.DataFrame(ws.get_all_records())
    credentials = {"usernames": {}}
    for _, row in df.iterrows():
        credentials["usernames"][str(row['Username'])] = {
            "name": str(row['Name']),
            "password": str(row['Password']),
            "email": str(row['Email'])
        }
    return credentials

def load_data(user_prefix):
    try:
        ws = sheet.worksheet(f"{user_prefix}_history")
        data = ws.get_all_records()
        return pd.DataFrame(data) if data else pd.DataFrame(columns=['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit'])
    except:
        return pd.DataFrame(columns=['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit'])

def save_data(df, user_prefix):
    ws = sheet.worksheet(f"{user_prefix}_history")
    ws.clear()
    df_save = df.copy()
    df_save['Date'] = pd.to_datetime(df_save['Date']).dt.strftime('%Y-%m-%d')
    ws.update(values=[df_save.columns.values.tolist()] + df_save.fillna('').values.tolist(), range_name='A1')

def load_bankroll(user_prefix):
    ws = sheet.worksheet(f"{user_prefix}_bankroll")
    val = ws.acell('B1').value
    return float(val) if val else 1000.0

def update_bankroll(amount, user_prefix):
    st.session_state.bankroll += amount
    ws = sheet.worksheet(f"{user_prefix}_bankroll")
    ws.update_acell('B1', st.session_state.bankroll)

def set_bankroll(amount, user_prefix):
    st.session_state.bankroll = amount
    ws = sheet.worksheet(f"{user_prefix}_bankroll")
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
authenticator = stauth.Authenticate(
    credentials,
    "bet_tracker_cookie",
    "random_key_abc_123", # Change this to any string to reset all cookies
    cookie_expiry_days=30
)

# Use st.empty to allow us to completely clear the login form
login_container = st.empty()

# If not logged in, show the login form inside the container
if not st.session_state.get("authentication_status"):
    with login_container.container():
        authenticator.login(location='main')

# Check status again after the login attempt
if st.session_state["authentication_status"]:
    # 1. CLEAR THE LOGIN UI IMMEDIATELY
    login_container.empty()

    # 2. GET USER INFO
    username = st.session_state["username"]
    name = st.session_state["name"]

    # 3. SETUP SESSION DATA
    if 'bankroll' not in st.session_state:
        st.session_state.bankroll = load_bankroll(username)

    df = load_data(username)
    today = datetime.date.today()
    total_profit_today = 0.0
    if not df.empty:
        df['Date'] = pd.to_datetime(df['Date']).dt.date
        total_profit_today = df[df['Date'] == today]['Profit'].sum()

    # --- 5. SIDEBAR ---
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.title(f"Welcome, {name}")
    st.sidebar.metric("💰 Bankroll", f"${st.session_state.bankroll:,.2f}", f"{total_profit_today:,.2f}")

    with st.sidebar.expander("⚙️ Adjust Balance"):
        bankroll_action = st.radio("Action", ["Add/Remove", "Set Exact"], horizontal=True)
        if bankroll_action == "Add/Remove":
            adj = st.number_input("Amount ($)", value=0.0, step=10.0)
            if st.button("Update Balance"):
                update_bankroll(adj, username)
                st.rerun()
        else:
            new_val = st.number_input("Exact ($)", value=float(st.session_state.bankroll))
            if st.button("Set Balance"):
                set_bankroll(new_val, username)
                st.rerun()

    st.sidebar.divider()
    st.sidebar.header("🧮 Kelly Calculator")
    if 'odds_input' not in st.session_state: st.session_state.odds_input = -110
    input_odds = st.sidebar.number_input("American Odds", step=1, key="odds_input")
    input_edge_percent = st.sidebar.number_input("Edge (%)", 0.0, 100.0, 15.0, 0.1)
    
    k_opts = {"Full": 1.0, "Half": 0.5, "Quarter": 0.25}
    k_sel = st.sidebar.radio("Kelly Multiplier", list(k_opts.keys()), index=2, horizontal=True)
    
    dec_odds = american_to_decimal(input_odds)
    b = dec_odds - 1
    full_k = (input_edge_percent/100) / b if b != 0 else 0
    raw_stake = full_k * k_opts[k_sel] * st.session_state.bankroll
    suggested_stake = round(raw_stake, 2)
    st.sidebar.metric("Suggested Stake", f"${suggested_stake:,.2f}")

    # --- 6. MAIN TABS ---
    tabs = st.tabs(["📝 Log New Bet", "📊 Dashboard", "🗄️ History"])

    with tabs[0]:
        st.subheader("Enter Wager Details")
        dropdowns = load_dropdowns()
        with st.form("bet_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            date = c1.date_input("Date", datetime.date.today())
            book = c2.selectbox("Sportsbook", dropdowns["books"])
            state = c3.selectbox("State", dropdowns["states"])
            c4, c5, c6 = st.columns(3)
            event = c4.text_input("Event / Matchup")
            final_stake = c5.number_input("Actual Stake ($)", value=float(suggested_stake))
            result = c6.selectbox("Status", ["Pending", "Win", "Loss", "Push"])
            
            if st.form_submit_button("Save Bet to Database"):
                if not event.strip():
                    st.error("Event name required.")
                else:
                    profit = 0
                    if result == "Win": profit = round(final_stake * (dec_odds - 1), 2)
                    elif result == "Loss": profit = -final_stake
                    
                    new_row = {"Date": date, "Book": book, "State": state, "Event": event, "Odds": input_odds, "Edge": input_edge_percent/100, "Stake": final_stake, "Result": result, "Profit": profit}
                    df_new = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    save_data(df_new, username)
                    if profit != 0: update_bankroll(profit, username)
                    st.toast("Bet Logged!", icon="✅")
                    st.rerun()

    with tabs[1]:
        if not df.empty:
            total_profit = df['Profit'].sum()
            m1, m2, m3 = st.columns(3)
            m1.metric("Total P/L", f"${total_profit:,.2f}")
            m2.metric("Total Bets", len(df))
            
            filtered_df = df.sort_values('Date')
            filtered_df['Cumulative Profit'] = filtered_df['Profit'].cumsum()
            fig = px.line(filtered_df, x='Date', y='Cumulative Profit', title="Profit Trend", markers=True)
            st.plotly_chart(fig, use_container_width=True)

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

elif st.session_state["authentication_status"] is False:
    st.error("Username/password is incorrect")