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
@st.cache_data(ttl=300) 
def get_live_mlb_stats(game_pk, player_name, market):
    if not game_pk or game_pk == "": return None
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        for team in ['home', 'away']:
            players = data['teams'][team]['players']
            for p_id, p_info in players.items():
                if player_name.lower() in p_info['person']['fullName'].lower():
                    stats = p_info['stats']
                    if "Outs" in str(market):
                        pitching = stats.get('pitching', {})
                        ip = pitching.get('inningsPitched', "0.0")
                        f_i = int(float(ip))
                        part = round((float(ip) - f_i) * 10)
                        val = (f_i * 3) + part
                        return {"val": val, "status": data.get('status', {}).get('abstractGameState')}
                    elif "Bases" in str(market):
                        b = stats.get('batting', {})
                        h, d, t, hr = b.get('hits', 0), b.get('doubles', 0), b.get('triples', 0), b.get('homeRuns', 0)
                        val = (h - d - t - hr) + (d * 2) + (t * 3) + (hr * 4)
                        return {"val": val, "status": data.get('status', {}).get('abstractGameState')}
    except: return None
    return None

# --- 4. HELPERS ---
def clean_book_name(raw_book):
    if not raw_book: return "DraftKings"
    lookup = str(raw_book).lower().replace("_", "").replace(" ", "")
    mapping = {"williamhillus": "Caesars", "williamhill_us": "Caesars", "caesars": "Caesars", "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "bovada": "Bovada", "espnbet": "ESPN Bet"}
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
    headers = {"apikey": "sb_publishable_aAFvyqUjJFYQsuG8GY2KTA_U4SLd545", "Authorization": "Bearer sb_publishable_aAFvyqUjJFYQsuG8GY2KTA_U4SLd545"}
    try:
        data = requests.get(url, headers=headers).json()
        def parse_time(t_str):
            if not t_str: return pd.Timestamp.max
            try:
                clean_t = str(t_str).replace(' ET', '').strip()
                return pd.to_datetime(clean_t)
            except:
                return pd.Timestamp.max
        data = sorted(data, key=lambda x: parse_time(x.get('game_time')))
        picks = []
        for item in data:
            matchup = get_matchup_string(item)
            p_name = item.get("player") or "Unknown"
            picks.append({
                "Event": f"[{matchup}] {p_name}: {str(item.get('direction','')).upper()} {item.get('line','')} {item.get('market','')}",
                "Price": f"+{item.get('best_price', -110)}" if item.get('best_price', -110) > 0 else str(item.get('best_price', -110)),
                "Book": clean_book_name(item.get("best_book")), "Time": item.get("game_time") or "TBD",
                "game_pk": item.get("game_pk"), "player_name": p_name, "market": item.get("market"), "line": item.get("line"), "dir": item.get("direction"), "raw_odds": item.get("best_price")
            })
        return picks
    except: return []

def load_data(user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_history")
    data = ws.get_all_records()
    req = ['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit', 'game_pk', 'player_name', 'market', 'line', 'dir']
    if not data: return pd.DataFrame(columns=req)
    df = pd.DataFrame(data)
    for col in req:
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
    return {"books": [b for b in ws.col_values(1)[1:] if b.strip()], "states": [s for s in ws.col_values(2)[1:] if s.strip()]}

def save_dropdowns(data):
    ws = sheet.worksheet("Dropdowns")
    ws.clear()
    max_l = max(len(data['books']), len(data['states']))
    rows = [['Books', 'States']] + [[(data['books'] + ['']*max_l)[i], (data['states'] + ['']*max_l)[i]] for i in range(max_l)]
    ws.update(values=rows, range_name='A1')
    st.cache_data.clear()

def american_to_decimal(odds):
    try:
        val = int(str(odds).replace('+', ''))
        return (val / 100) + 1 if val > 0 else (100 / abs(val)) + 1
    except: return 1.91

def get_ws_smart(sheet, name):
    all_ws = {ws.title.lower().strip(): ws for ws in sheet.worksheets()}
    target = name.lower().strip()
    if target in all_ws: return all_ws[target]
    st.error(f"Tab Not Found: '{target}'"); st.stop()

# --- 5. AUTH & UI ---
@st.cache_data(ttl=3600)
def load_credentials():
    ws = sheet.worksheet("Credentials")
    df = pd.DataFrame(ws.get_all_records())
    return {"usernames": {row['Username']: {"name": row['Name'], "password": str(row['Password']), "email": row['Email']} for _, row in df.iterrows()}}

creds = load_credentials()
authenticator = stauth.Authenticate(creds, "bet_tracker_cookie", "secure_v3_2026", cookie_expiry_days=30)

if not st.session_state.get("authentication_status"):
    authenticator.login(location='main')

if st.session_state["authentication_status"]:
    username, name = st.session_state["username"], st.session_state["name"]
    
    # Initialize State Keys
    if 'bankroll' not in st.session_state: st.session_state.bankroll = load_bankroll(username)
    if 'form_stake' not in st.session_state: st.session_state.form_stake = 0.0
    if 'form_odds' not in st.session_state: st.session_state.form_odds = -110

    df_current = load_data(username)

    # Sidebar
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.metric("💰 Bankroll", f"\${st.session_state.bankroll:,.2f}")
    with st.sidebar.expander("⚙️ Adjust Balance"):
        adj_action = st.sidebar.radio("Action", ["Add/Remove", "Set Exact"], horizontal=True)
        if adj_action == "Add/Remove":
            adj_v = st.sidebar.number_input("Amount ($)", value=0.0)
            if st.sidebar.button("Update Balance"): update_bankroll(adj_v, username); st.rerun()
        else:
            set_v = st.sidebar.number_input("Exact ($)", value=float(st.session_state.bankroll))
            if st.sidebar.button("Set Balance"): set_bankroll(set_v, username); st.rerun()

    st.sidebar.divider()
    st.sidebar.markdown("### 🧮 Kelly Calculator")
    
    # Kelly Calculation references the ODDS in the Form (session_state)
    edge_pct = st.sidebar.number_input("Edge (%)", 0.0, 100.0, 15.0, step=1.0)
    round_toggle = st.sidebar.toggle("Round to Whole Number", value=True)
    k_sel = st.sidebar.radio("Multiplier", ["Full", "Half", "Quarter"], index=2, horizontal=True)
    
    dec_odds = american_to_decimal(st.session_state.form_odds)
    raw_k = (edge_pct/100) / (dec_odds - 1) if (dec_odds - 1) != 0 else 0
    s_stake = round(raw_k * {"Full":1.0, "Half":0.5, "Quarter":0.25}[k_sel] * st.session_state.bankroll) if round_toggle else round(raw_k * 0.25 * st.session_state.bankroll, 2)
    
    c1, c2 = st.sidebar.columns([2, 1])
    c1.metric("Suggested", f"\${s_stake:,.2f}")
    if c2.button("Apply"):
        st.session_state.form_stake = float(s_stake)
        st.rerun()

    if st.session_state.get('pending_track'):
        track = st.session_state.pending_track
        st.session_state.autofill_event = track['event']
        st.session_state.autofill_book = track['book']
        st.session_state.form_odds = int(track['odds'])
        st.session_state.autofill_meta = {"game_pk": track['game_pk'], "player_name": track['player_name'], "market": track['market'], "line": track['line'], "dir": track['dir'], "odds": track['odds']}
        st.session_state['nav_bar_key'] = "📝 Log New Bet"
        del st.session_state['pending_track']

    nav = st.segmented_control("Navigation", ["🎯 Picks", "📝 Log New Bet", "📊 Dashboard", "🗄️ History"], key="nav_bar_key", selection_mode="single", default="🎯 Picks")

    if nav == "🎯 Picks":
        picks = get_glicks_picks()
        for p in picks:
            with st.container(border=True):
                col1, col2 = st.columns([4, 1])
                col1.write(f"**{p['Event']}**")
                col1.write(f"⏰ **{p['Time']}** | 🏦 **{p['Book']}** | 📈 **{p['Price']}**")
                if col2.button("Track", key=p['Event']):
                    st.session_state.pending_track = {"event": p['Event'], "book": p['Book'], "odds": p['raw_odds'], "game_pk": p['game_pk'], "player_name": p['player_name'], "market": p['market'], "line": p['line'], "dir": p['dir']}
                    st.rerun()

    elif nav == "📝 Log New Bet":
        st.subheader("Enter Wager Details")
        dropdowns = load_dropdowns()
        
        # Form UI
        def_bk = st.session_state.get('autofill_book', "")
        book_idx = dropdowns["books"].index(def_bk) if def_bk in dropdowns["books"] else 0

        with st.form("bet_form", clear_on_submit=True):
            r1c1, r1c2, r1c3 = st.columns(3)
            date = r1c1.date_input("Date", datetime.datetime.now(NYC_TZ).date())
            book = r1c2.selectbox("Sportsbook", dropdowns["books"], index=book_idx)
            state = r1c3.selectbox("State", dropdowns["states"])
            
            r2c1, r2c2, r2c3 = st.columns(3)
            event = r2c1.text_input("Event", value=st.session_state.get('autofill_event', ""))
            
            # Use Session State Keys to decouple Sidebar Calc from direct Form update
            odds_input = r2c2.number_input("American Odds", value=int(st.session_state.form_odds), step=1, key="form_odds")
            stake_input = r2c3.number_input("Stake ($)", value=float(st.session_state.form_stake), step=1.0, key="form_stake")
            
            r3c1 = st.columns(3)[0]
            res = r3c1.selectbox("Status", ["Pending", "Win", "Loss", "Push"])
            
            if st.form_submit_button("Save Bet"):
                meta = st.session_state.get("autofill_meta", {})
                p = round(stake_input * (american_to_decimal(odds_input) - 1), 2) if res == "Win" else (-stake_input if res == "Loss" else 0)
                
                new_row = {
                    "Date": date, "Book": book, "State": state, "Event": event, 
                    "Odds": odds_input, "Edge": edge_pct/100, "Stake": stake_input, 
                    "Result": res, "Profit": p, "game_pk": meta.get("game_pk", ""), 
                    "player_name": meta.get("player_name", ""), "market": meta.get("market", ""), 
                    "line": meta.get("line", ""), "dir": meta.get("dir", "")
                }
                
                save_data(pd.concat([df_current, pd.DataFrame([new_row])], ignore_index=True), username)
                if p != 0: update_bankroll(p, username)
                
                # Reset
                st.session_state.autofill_event = ""
                st.session_state.autofill_meta = {}
                st.session_state.form_stake = 0.0
                st.rerun()

    elif nav == "📊 Dashboard":
        if not df_current.empty:
            df_dash = df_current.copy()
            df_dash['Date'] = pd.to_datetime(df_dash['Date']).dt.date
            daily = df_dash.groupby('Date')['Profit'].sum().reset_index().sort_values('Date')
            daily['Cumulative Profit'] = daily['Profit'].cumsum()
            m1, m2, m3 = st.columns(3)
            m1.metric("Total P/L", f"\${df_current['Profit'].sum():,.2f}")
            m2.metric("Days Active", len(daily))
            m3.metric("Avg Daily Profit", f"\${daily['Profit'].mean():,.2f}")
            fig_line = px.line(daily, x='Date', y='Cumulative Profit', title="Profit (End of Day)", markers=True)
            fig_line.update_xaxes(type='date', tickformat='%Y-%m-%d', dtick="D1")
            st.plotly_chart(fig_line, use_container_width=True)
            fig_bar = px.bar(daily, x='Date', y='Profit', title="Daily Individual Profit", color='Profit', color_continuous_scale=['red', 'gray', 'green'])
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
                    
                    if row.get('game_pk'):
                        stats = get_live_mlb_stats(row['game_pk'], row['player_name'], row['market'])
                        if stats:
                            is_win = (row['dir'] == 'OVER' and float(stats['val']) > float(row['line'])) or (row['dir'] == 'UNDER' and float(stats['val']) < float(row['line']))
                            icon = "✅" if is_win else "❌"
                            status_val = stats.get('status')
                            status_str = f" ({status_val})" if status_val and str(status_val).lower() != "none" else ""
                            st.caption(f"⚾ **Live: {stats['val']} {row['market']}**{status_str} {icon}")
                        else: st.caption("⚾ *Waiting for game to start/update...*")

                    if col2.button("✅ Win", key=f"w{i}"):
                        df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Win', round(pot_profit, 2)
                        save_data(df_current, username); update_bankroll(pot_profit, username); st.rerun()
                    if col3.button("❌ Loss", key=f"l{i}"):
                        df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Loss', -row['Stake']
                        save_data(df_current, username); update_bankroll(-row['Stake'], username); st.rerun()
                    if col4.button("🗑️", key=f"d{i}"):
                        save_data(df_current.drop(i), username); st.rerun()
        st.divider()
        st.subheader("📜 Settled History")
        display_cols = ['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit']
        settled = df_current[df_current['Result'] != 'Pending'].sort_values('Date', ascending=False)
        st.dataframe(settled[display_cols], width='stretch', hide_index=True)
        
        with st.expander("🗑️ Delete/Refund a Settled Bet"):
            if not settled.empty:
                s_list = {f"{r['Date']} | {r['Event']} (${r['Profit']})": idx for idx, r in settled.iterrows()}
                target = st.selectbox("Select bet to remove:", [""] + list(s_list.keys()))
                if st.button("Delete & Reverse Bankroll") and target:
                    idx = s_list[target]
                    update_bankroll(-df_current.at[idx, 'Profit'], username)
                    save_data(df_current.drop(idx), username); st.rerun()

elif st.session_state["authentication_status"] is False:
    st.error("Incorrect credentials")