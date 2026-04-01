import streamlit as st
import pandas as pd
import datetime
from zoneinfo import ZoneInfo
import plotly.express as px
import plotly.graph_objects as go
import json
import gspread
from google.oauth2.service_account import Credentials
import streamlit_authenticator as stauth
import requests

# --- 1. PAGE CONFIG & TIMEZONE ---
st.set_page_config(page_title="Glick Pick Tracker - TEST ENV", layout="wide")
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

# --- 3. LIVE MLB DATA ENGINE ---
@st.cache_data(ttl=60) # Rate limit: 60 seconds
def get_live_mlb_stats(game_pk, player_name, market):
    """Fetches live boxscore from MLB and extracts specific player stats."""
    if not game_pk: return None
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        for team in ['home', 'away']:
            players = data['teams'][team]['players']
            for p_id, p_info in players.items():
                if player_name.lower() in p_info['person']['fullName'].lower():
                    stats = p_info['stats']
                    # Pitching (Outs Recorded)
                    if "Outs" in market:
                        pitching = stats.get('pitching', {})
                        ip = pitching.get('inningsPitched', "0.0")
                        f_i = int(float(ip))
                        part = round((float(ip) - f_i) * 10)
                        val = (f_i * 3) + part
                        return {"val": val, "status": data.get('status', {}).get('abstractGameState')}
                    # Batting (Total Bases)
                    elif "Bases" in market:
                        b = stats.get('batting', {})
                        # Bases = 1B + 2*2B + 3*3B + 4*HR
                        # 1B = H - 2B - 3B - HR
                        h, d, t, hr = b.get('hits', 0), b.get('doubles', 0), b.get('triples', 0), b.get('homeRuns', 0)
                        val = (h - d - t - hr) + (d * 2) + (t * 3) + (hr * 4)
                        return {"val": val, "status": data.get('status', {}).get('abstractGameState')}
    except: return None
    return None

# --- 4. HELPERS: MAPPING & MATCHUPS ---
def clean_book_name(raw_book):
    if not raw_book: return "DraftKings"
    lookup = str(raw_book).lower().replace("_", "").replace(" ", "")
    mapping = {
        "williamhillus": "Caesars", "williamhill_us": "Caesars", "caesars": "Caesars",
        "caesarssportsbook": "Caesars", "draftkings": "DraftKings", "fanduel": "FanDuel",
        "betmgm": "BetMGM", "bovada": "Bovada", "bodog": "Bovada", "betrivers": "BetRivers",
        "sugarhouse": "BetRivers", "espnbet": "ESPN Bet", "barstool": "ESPN Bet"
    }
    return mapping.get(lookup, str(raw_book).title())

def get_matchup_string(item):
    opp, home, away = item.get("opponent", "Unknown"), item.get("home_team", ""), item.get("away_team", "")
    opp_l = opp.lower().strip()
    if away and away.lower().strip() != opp_l: return f"{away} at {opp}"
    if home and home.lower().strip() != opp_l: return f"{home} vs {opp}"
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
        picks = []
        for item in data:
            matchup = get_matchup_string(item)
            p_name, p_dir, p_line, p_mkt = item.get("player") or "Unknown", str(item.get("direction", "")).upper(), str(item.get("line", "")), item.get("market", "")
            event = f"[{matchup}] {p_name}: {p_dir} {p_line} {p_mkt}"
            raw_p = item.get("best_price", -110)
            d_time = item.get("game_time") or "TBD"
            sort_key = "23:59"
            if d_time != "TBD":
                try: sort_key = datetime.datetime.strptime(d_time.replace(" ET", ""), "%I:%M %p").strftime("%H:%M")
                except: pass
            picks.append({
                "Event": event, "Price": f"+{raw_p}" if raw_p > 0 else str(raw_p), 
                "Book": clean_book_name(item.get("best_book")), "Time": d_time, "SortKey": sort_key, "raw_odds": raw_p,
                # New Tracking Keys
                "game_pk": item.get("game_pk"), "player_name": p_name, "market": p_mkt, "line": p_line, "dir": p_dir
            })
        return sorted(picks, key=lambda x: x['SortKey'])
    except: return []

# --- 5. DATA HELPERS ---
def get_ws_smart(sheet, name):
    all_ws = {ws.title.lower().strip(): ws for ws in sheet.worksheets()}
    target = name.lower().strip()
    if target in all_ws: return all_ws[target]
    st.error(f"Tab Not Found: '{target}'")
    st.stop()

def load_data(user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_history")
    data = ws.get_all_records()
    # Expanded columns to include tracking metadata
    required_cols = ['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit', 'game_pk', 'player_name', 'market', 'line', 'dir']
    
    if not data: return pd.DataFrame(columns=required_cols)
    
    df = pd.DataFrame(data)
    # Self-healing: Ensure columns exist in the DataFrame
    for col in required_cols:
        if col not in df.columns: df[col] = ""
    return df

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
        return (val / 100) + 1 if val > 0 else (100 / abs(val)) + 1
    except: return 1.91

# --- 6. AUTHENTICATION ---
@st.cache_data(ttl=3600)
def load_credentials():
    ws = sheet.worksheet("Credentials")
    df = pd.DataFrame(ws.get_all_records())
    credentials = {"usernames": {}}
    for _, row in df.iterrows():
        user = str(row['Username']).strip()
        credentials["usernames"][user] = {"name": str(row['Name']), "password": str(row['Password']), "email": str(row['Email'])}
    return credentials

creds = load_credentials()
authenticator = stauth.Authenticate(creds, "bet_tracker_cookie", "secure_v3_glick_2026", cookie_expiry_days=30)

if not st.session_state.get("authentication_status"):
    authenticator.login(location='main')

if st.session_state["authentication_status"]:
    username, name = st.session_state["username"], st.session_state["name"]
    if 'bankroll' not in st.session_state: st.session_state.bankroll = load_bankroll(username)
    df_current = load_data(username)

    # --- SIDEBAR ---
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.metric("💰 Bankroll", f"${st.session_state.bankroll:,.2f}")
    
    with st.sidebar.expander("⚙️ Adjust Balance"):
        adj_action = st.radio("Action", ["Add/Remove", "Set Exact"], horizontal=True)
        if adj_action == "Add/Remove":
            adj_v = st.number_input("Amount ($)", value=0.0)
            if st.button("Update Balance"): update_bankroll(adj_v, username); st.rerun()
        else:
            set_v = st.number_input("Exact ($)", value=float(st.session_state.bankroll))
            if st.button("Set Balance"): set_bankroll(set_v, username); st.rerun()

    st.sidebar.divider()
    st.sidebar.markdown("### 🧮 Kelly Calculator")
    input_odds = st.sidebar.number_input("American Odds", step=1, value=-110)
    edge_pct = st.sidebar.number_input("Edge (%)", 0.0, 100.0, 15.0, step=1.0)
    round_toggle = st.sidebar.toggle("Round to Whole Number", value=True)
    k_sel = st.sidebar.radio("Multiplier", ["Full", "Half", "Quarter"], index=2, horizontal=True)
    dec_odds = american_to_decimal(input_odds)
    raw_k = (edge_pct/100) / (dec_odds - 1) if (dec_odds - 1) != 0 else 0
    raw_suggested = raw_k * {"Full": 1.0, "Half": 0.5, "Quarter": 0.25}[k_sel] * st.session_state.bankroll
    suggested_stake = round(raw_suggested) if round_toggle else round(raw_suggested, 2)
    st.sidebar.metric("Suggested Stake", f"${suggested_stake:,.2f}")

    # Track logic redirection
    if st.session_state.get('pending_track'):
        track_data = st.session_state.pending_track
        st.session_state['odds_input'] = track_data['odds']
        st.session_state.autofill_event = track_data['event']
        st.session_state.autofill_book = track_data['book']
        # Pass live metadata through redirection
        st.session_state.autofill_metadata = {
            "game_pk": track_data.get("game_pk"),
            "player_name": track_data.get("player_name"),
            "market": track_data.get("market"),
            "line": track_data.get("line"),
            "dir": track_data.get("dir")
        }
        st.session_state['nav_bar_key'] = "📝 Log New Bet"
        del st.session_state['pending_track']

    nav = st.segmented_control("Navigation", ["🎯 Picks", "📝 Log New Bet", "📊 Dashboard", "🗄️ History"], key="nav_bar_key", selection_mode="single", default="🎯 Picks")

    if nav == "🎯 Picks":
        picks = get_glicks_picks()
        if not picks: st.info("No picks found for today.")
        for p in picks:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.write(f"**{p['Event']}**")
                c1.write(f"⏰ **{p['Time']}** | 🏦 **{p['Book']}** | 📈 **{p['Price']}**")
                if c2.button("Track", key=p['Event'], width='stretch'):
                    st.session_state.pending_track = {
                        "event": p['Event'], "book": p['Book'], "odds": p['raw_odds'],
                        "game_pk": p['game_pk'], "player_name": p['player_name'],
                        "market": p['market'], "line": p['line'], "dir": p['dir']
                    }
                    st.rerun()

    elif nav == "📝 Log New Bet":
        st.subheader("Enter Wager Details")
        dropdowns = load_dropdowns()
        
        with st.expander("➕ Add New Book or State"):
            nb_col, ns_col = st.columns(2)
            new_bk = nb_col.text_input("New Book Name")
            if nb_col.button("Add Book") and new_bk:
                if new_bk not in dropdowns['books']:
                    dropdowns['books'].append(new_bk); save_dropdowns(dropdowns); st.rerun()
            new_st = ns_col.text_input("New State Name")
            if ns_col.button("Add State") and new_st:
                if new_st not in dropdowns['states']:
                    dropdowns['states'].append(new_st); save_dropdowns(dropdowns); st.rerun()

        def_bk = st.session_state.get('autofill_book', "")
        if def_bk and def_bk not in dropdowns["books"]:
            dropdowns["books"].append(def_bk); save_dropdowns(dropdowns)
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
                meta = st.session_state.get("autofill_metadata", {})
                odds = st.session_state.get('odds_input', input_odds)
                p = round(stake * (american_to_decimal(odds) - 1), 2) if res == "Win" else (-stake if res == "Loss" else 0)
                
                new_row = {
                    "Date": date, "Book": book, "State": state, "Event": event, "Odds": odds, 
                    "Edge": edge_pct/100, "Stake": stake, "Result": res, "Profit": p,
                    # Live tracking fields
                    "game_pk": meta.get("game_pk", ""), "player_name": meta.get("player_name", ""),
                    "market": meta.get("market", ""), "line": meta.get("line", ""), "dir": meta.get("dir", "")
                }
                
                df_all = pd.concat([df_current, pd.DataFrame([new_row])], ignore_index=True)
                save_data(df_all, username)
                if p != 0: update_bankroll(p, username)
                st.session_state.autofill_event = ""; st.session_state.autofill_book = ""; st.session_state.autofill_metadata = {}
                st.toast("Logged!", icon="✅"); st.rerun()

    elif nav == "📊 Dashboard":
        if not df_current.empty:
            df_dash = df_current.copy()
            df_dash['Date'] = pd.to_datetime(df_dash['Date']).dt.date
            daily_profit = df_dash.groupby('Date')['Profit'].sum().reset_index().sort_values('Date')
            daily_profit['Cumulative Profit'] = daily_profit['Profit'].cumsum()
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Total P/L", f"${df_current['Profit'].sum():,.2f}")
            m2.metric("Days Active", len(daily_profit))
            m3.metric("Avg Daily Profit", f"${daily_profit['Profit'].mean():,.2f}")

            fig_line = px.line(daily_profit, x='Date', y='Cumulative Profit', title="Profit Over Time (End of Day)", markers=True)
            fig_line.update_xaxes(type='date', tickformat='%Y-%m-%d', dtick="D1")
            st.plotly_chart(fig_line, use_container_width=True)
            
            fig_bar = px.bar(daily_profit, x='Date', y='Profit', title="Daily Individual Profit", color='Profit', color_continuous_scale=['red', 'gray', 'green'])
            fig_bar.update_xaxes(type='date', tickformat='%Y-%m-%d', dtick="D1")
            st.plotly_chart(fig_bar, use_container_width=True)

    elif nav == "🗄️ History":
        st.subheader("🏟️ Active Wagers")
        pending = df_current[df_current['Result'] == 'Pending']
        if pending.empty: st.info("No active wagers.")
        else:
            for i, row in pending.iterrows():
                pot_profit = row['Stake'] * (american_to_decimal(row['Odds']) - 1)
                with st.container(border=True):
                    col1, col2, col3, col4 = st.columns([3, 1, 1, 0.5])
                    col1.write(f"**{row['Event']}** | {row['Book']} ({row['Odds']})")
                    col1.write(f"💰 Wager: **\${row['Stake']:.2f}** | 📈 Potential Profit: **\${pot_profit:.2f}**")
                    
                    # --- LIVE STATUS BUTTON ---
                    if row.get('game_pk'):
                        if col1.button("🔄 Check Live Status", key=f"live_{i}"):
                            stats = get_live_mlb_stats(row['game_pk'], row['player_name'], row['market'])
                            if stats:
                                line = float(row['line'])
                                current_val = float(stats['val'])
                                winning = (row['dir'] == 'OVER' and current_val > line) or (row['dir'] == 'UNDER' and current_val < line)
                                icon = "✅" if winning else "❌"
                                st.info(f"Live Update: {row['player_name']} has **{current_val}** {row['market']}. Status: {icon} ({stats['status']})")
                            else: st.warning("Game data not yet available or player not in boxscore.")

                    if col2.button("✅ Win", key=f"w{i}"):
                        df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Win', round(pot_profit, 2)
                        save_data(df_current, username); update_bankroll(pot_profit, username); st.rerun()
                    if col3.button("❌ Loss", key=f"l{i}"):
                        df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Loss', -row['Stake']
                        save_data(df_current, username); update_bankroll(-row['Stake'], username); st.rerun()
                    if col4.button("🗑️", key=f"d{i}"):
                        df_current = df_current.drop(i); save_data(df_current, username); st.rerun()
        
        st.divider()
        st.subheader("📜 Settled History")
        settled = df_current[df_current['Result'] != 'Pending'].sort_values('Date', ascending=False)
        st.dataframe(settled, width='stretch', hide_index=True)
        
        with st.expander("🗑️ Delete/Refund a Settled Bet"):
            if not settled.empty:
                settled_list = {f"{r['Date']} | {r['Event']} (${r['Profit']})": idx for idx, r in settled.iterrows()}
                target = st.selectbox("Select settled bet to remove:", [""] + list(settled_list.keys()))
                if st.button("Delete & Reverse Bankroll") and target:
                    idx_to_del = settled_list[target]
                    update_bankroll(-df_current.at[idx_to_del, 'Profit'], username)
                    save_data(df_current.drop(idx_to_del), username); st.rerun()

elif st.session_state["authentication_status"] is False:
    st.error("Incorrect credentials")