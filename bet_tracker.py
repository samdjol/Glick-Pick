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

# --- 3. HELPERS: MAPPING & MATCHUPS ---
def clean_book_name(raw_book):
    """Maps raw API book names to standardized display names."""
    if not raw_book:
        return "DraftKings"
    lookup = str(raw_book).lower().replace("_", "").replace(" ", "")
    mapping = {
        "williamhillus": "Caesars",
        "williamhill_us": "Caesars",
        "caesars": "Caesars",
        "caesarssportsbook": "Caesars",
        "draftkings": "DraftKings",
        "fanduel": "FanDuel",
        "betmgm": "BetMGM",
        "bovada": "Bovada",
        "bodog": "Bovada",
        "betrivers": "BetRivers",
        "sugarhouse": "BetRivers",
        "espnbet": "ESPN Bet",
        "barstool": "ESPN Bet"
    }
    return mapping.get(lookup, str(raw_book).title())

def get_matchup_string(item):
    """Constructs: 'PlayerTeam vs/at Opponent'"""
    opp = item.get("opponent", "Unknown")
    home = item.get("home_team", "")
    away = item.get("away_team", "")
    
    opp_l = opp.lower().strip()
    home_l = home.lower().strip() if home else ""
    away_l = away.lower().strip() if away else ""

    if away and away_l != opp_l:
        return f"{away} at {opp}"
    if home and home_l != opp_l:
        return f"{home} vs {opp}"
    if home_l == opp_l and away:
        return f"{away} at {opp}"
    if home and not away and home_l == opp_l:
        return f"Away Team at {opp}"
    return f"{home if home else '???'} vs {opp}"

@st.cache_data(ttl=300)
def get_glicks_picks():
    today_str = datetime.datetime.now(NYC_TZ).strftime("%Y-%m-%d")
    url = f"https://ajjruzolkbzardssopos.supabase.co/rest/v1/picks?select=*&season=eq.2026&date=eq.{today_str}"
    apikey = "sb_publishable_aAFvyqUjJFYQsuG8GY2KTA_U4SLd545"
    headers = {"apikey": apikey, "Authorization": f"Bearer {apikey}", "Content-Type": "application/json"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if not isinstance(data, list): return []

        picks = []
        for item in data:
            matchup = get_matchup_string(item)
            p_name = item.get("player") or "Unknown"
            p_dir = str(item.get("direction", "")).upper()
            p_line = str(item.get("line", ""))
            p_mkt = item.get("market", "")
            event = f"[{matchup}] {p_name}: {p_dir} {p_line} {p_mkt}"
            
            raw_p = item.get("best_price", -110)
            price = f"+{raw_p}" if raw_p > 0 else str(raw_p)
            book = clean_book_name(item.get("best_book", "DraftKings"))
            
            display_time = item.get("game_time") or "TBD"
            sort_key = "23:59"
            if display_time != "TBD":
                try:
                    t_str = display_time.replace(" ET", "").strip()
                    t_obj = datetime.datetime.strptime(t_str, "%I:%M %p")
                    sort_key = t_obj.strftime("%H:%M")
                except: pass

            picks.append({
                "Event": event, "Price": price, "Book": book,
                "Time": display_time, "SortKey": sort_key
            })
        
        picks.sort(key=lambda x: x['SortKey'])
        return picks
    except: return []

# --- 4. DATA HELPERS ---
def get_ws_smart(sheet, name):
    all_ws = {ws.title.lower().strip(): ws for ws in sheet.worksheets()}
    target = name.lower().strip()
    if target in all_ws: return all_ws[target]
    st.error(f"Tab Not Found: '{target}'")
    st.stop()

@st.cache_data(ttl=3600)
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

@st.cache_data(ttl=3600)
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
    st.cache_data.clear()

def american_to_decimal(odds):
    try:
        val = int(str(odds).replace('+', ''))
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
    
    if st.session_state.get('pending_track'):
        track_data = st.session_state.pending_track
        st.session_state['odds_input'] = track_data['odds']
        st.session_state.autofill_event = track_data['event']
        st.session_state.autofill_book = track_data['book']
        st.session_state['nav_bar_key'] = "📝 Log New Bet"
        del st.session_state['pending_track']

    if 'dashboard_entered' not in st.session_state:
        st.success("Logged in!")
        if st.button("🚀 Enter Dashboard", width='stretch'):
            st.session_state['dashboard_entered'] = True
            st.rerun()
        st.stop()

    username = st.session_state["username"]
    name = st.session_state["name"]
    if 'bankroll' not in st.session_state: st.session_state.bankroll = load_bankroll(username)
    df_current = load_data(username)

    # --- 7. SIDEBAR ---
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.title(f"Welcome, {name}")
    st.sidebar.metric("💰 Bankroll", f"${st.session_state.bankroll:,.2f}")

    with st.sidebar.expander("⚙️ Adjust Balance"):
        adj_action = st.sidebar.radio("Action", ["Add/Remove", "Set Exact"], horizontal=True)
        if adj_action == "Add/Remove":
            adj_v = st.sidebar.number_input("Amount ($)", value=0.0)
            if st.sidebar.button("Update Balance"): update_bankroll(adj_v, username); st.rerun()
        else:
            set_v = st.sidebar.number_input("Exact ($)", value=float(st.session_state.bankroll))
            if st.sidebar.button("Set Balance"): set_bankroll(set_v, username); st.rerun()

    st.sidebar.divider()
    st.sidebar.header("🧮 Kelly Calculator")
    if 'odds_input' not in st.session_state: st.session_state.odds_input = -110
    if 'edge_input' not in st.session_state: st.session_state.edge_input = 15.0
    
    input_odds = st.sidebar.number_input("American Odds", step=1, key="odds_input")
    edge_pct = st.sidebar.number_input("Edge (%)", 0.0, 100.0, step=0.1, key="edge_input")
    round_toggle = st.sidebar.toggle("Round to Whole Number", value=True)
    
    k_map = {"Full": 1.0, "Half": 0.5, "Quarter": 0.25}
    k_sel = st.sidebar.radio("Multiplier", list(k_map.keys()), index=2, horizontal=True)
    
    dec_odds = american_to_decimal(input_odds)
    full_k = (edge_pct/100) / (dec_odds - 1) if (dec_odds - 1) != 0 else 0
    raw_suggested = full_k * k_map[k_sel] * st.session_state.bankroll
    suggested_stake = round(raw_suggested) if round_toggle else round(raw_suggested, 2)
    st.sidebar.metric("Suggested Stake", f"${suggested_stake:,.2f}")

    # --- 8. NAV BAR ---
    nav_labels = ["🎯 Glick's Picks", "📝 Log New Bet", "📊 Dashboard", "🗄️ History"]
    if 'nav_bar_key' not in st.session_state: st.session_state['nav_bar_key'] = nav_labels[0]

    active_page = st.segmented_control("Navigation", nav_labels, selection_mode="single", key="nav_bar_key", label_visibility="collapsed")
    st.divider()

    # --- 9. CONTENT ---
    if active_page == "🎯 Glick's Picks":
        st.subheader("Latest Picks")
        picks = get_glicks_picks()
        if not picks: st.info("No picks found for today.")
        else:
            for p in picks:
                with st.container(border=True):
                    ca, cb = st.columns([4, 1])
                    ca.write(f"**{p['Event']}**")
                    ca.write(f"⏰ **{p['Time']}** | 🏦 **{p['Book']}** | 📈 **{p['Price']}**")
                    if cb.button("Track", key=f"api_{p['Event']}", width='stretch'):
                        try:
                            clean_odds = int(str(p['Price']).replace('+', ''))
                        except: clean_odds = -110
                        st.session_state.pending_track = {"event": p['Event'], "book": p['Book'], "odds": clean_odds}
                        st.rerun()

    elif active_page == "📝 Log New Bet":
        st.subheader("Enter Wager Details")
        dropdowns = load_dropdowns()
        def_bk = st.session_state.get('autofill_book', "")
        if def_bk and def_bk not in dropdowns["books"]:
            dropdowns["books"].append(def_bk)
            save_dropdowns(dropdowns)
        book_idx = dropdowns["books"].index(def_bk) if def_bk in dropdowns["books"] else 0

        with st.form("bet_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            date = c1.date_input("Date", datetime.datetime.now(NYC_TZ).date())
            book = c2.selectbox("Sportsbook", dropdowns["books"], index=book_idx)
            state = c3.selectbox("State", dropdowns["states"])
            c4, c5, c6 = st.columns(3)
            event = c4.text_input("Event / Matchup", value=st.session_state.get('autofill_event', ""))
            stake = c5.number_input("Actual Stake ($)", value=float(suggested_stake))
            res = c6.selectbox("Status", ["Pending", "Win", "Loss", "Push"])
            if st.form_submit_button("Save Bet"):
                if event.strip():
                    p = 0
                    if res == "Win": p = round(stake * (dec_odds - 1), 2)
                    elif res == "Loss": p = -stake
                    new_row = {"Date": date, "Book": book, "State": state, "Event": event, "Odds": st.session_state.get('odds_input', -110), "Edge": edge_pct/100, "Stake": stake, "Result": res, "Profit": p}
                    df_all = load_data(username)
                    df_all = pd.concat([df_all, pd.DataFrame([new_row])], ignore_index=True)
                    save_data(df_all, username)
                    if p != 0: update_bankroll(p, username)
                    st.session_state.autofill_event = ""; st.session_state.autofill_book = ""
                    st.toast("Logged!", icon="✅"); st.rerun()

    elif active_page == "📊 Dashboard":
        if not df_current.empty:
            st.metric("Total P/L", f"${df_current['Profit'].sum():,.2f}")
            fdf = df_current.sort_values('Date')
            fdf['Cumulative Profit'] = fdf['Profit'].cumsum()
            st.plotly_chart(px.line(fdf, x='Date', y='Cumulative Profit', title="Profit Trend", markers=True), width='stretch')

    elif active_page == "🗄️ History":
        st.subheader("🏟️ Active Wagers")
        pending = df_current[df_current['Result'] == 'Pending']
        if pending.empty:
            st.info("No active wagers.")
        else:
            for i, row in pending.iterrows():
                # Calculate potential profit for display
                dec_odds_val = american_to_decimal(row['Odds'])
                pot_profit = row['Stake'] * (dec_odds_val - 1)
                
                with st.container(border=True):
                    col1, col2, col3, col4 = st.columns([3, 1, 1, 0.5])
                    col1.write(f"**{row['Event']}** | {row['Book']} ({row['Odds']})")
                    col1.write(f"💰 Wager: ${row['Stake']:.2f} | 📈 Potential Profit: ${pot_profit:.2f}")
                    
                    if col2.button("✅ Win", key=f"w{i}"):
                        p = round(pot_profit, 2)
                        df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Win', p
                        save_data(df_current, username); update_bankroll(p, username); st.rerun()
                    if col3.button("❌ Loss", key=f"l{i}"):
                        df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Loss', -row['Stake']
                        save_data(df_current, username); update_bankroll(-row['Stake'], username); st.rerun()
                    if col4.button("🗑️", key=f"d{i}"):
                        df_current = df_current.drop(i); save_data(df_current, username); st.rerun()
        
        st.divider()
        st.subheader("📜 History")
        settled = df_current[df_current['Result'] != 'Pending'].sort_values('Date', ascending=False)
        st.dataframe(settled, width='stretch', hide_index=True)

elif st.session_state["authentication_status"] is False:
    st.error("Incorrect credentials")