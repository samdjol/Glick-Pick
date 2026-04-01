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

# --- 3. LIVE MLB DATA HELPER ---
@st.cache_data(ttl=60) # Only refresh every 60 seconds to be respectful
def get_live_mlb_stats(game_pk, player_name, market):
    """Fetches live boxscore and extracts relevant player stats."""
    if not game_pk: return None
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    try:
        resp = requests.get(url, timeout=5).json()
        for side in ['home', 'away']:
            players = resp['teams'][side]['players']
            for p_id, p_info in players.items():
                if player_name.lower() in p_info['person']['fullName'].lower():
                    stats = p_info['stats']
                    # Handle Pitcher Outs
                    if "Outs" in market:
                        ip = stats.get('pitching', {}).get('inningsPitched', "0.0")
                        f_i = int(float(ip))
                        part = round((float(ip) - f_i) * 10)
                        return {"val": (f_i * 3) + part, "status": resp.get('status', {}).get('abstractGameState')}
                    # Handle Batter Bases
                    elif "Bases" in market:
                        b = stats.get('batting', {})
                        total = (b.get('hits',0)-b.get('doubles',0)-b.get('triples',0)-b.get('homeRuns',0)) + \
                                (b.get('doubles',0)*2) + (b.get('triples',0)*3) + (b.get('homeRuns',0)*4)
                        return {"val": total, "status": resp.get('status', {}).get('abstractGameState')}
    except: return None
    return None

# --- 4. DATA HELPERS ---
def clean_book_name(raw_book):
    if not raw_book: return "DraftKings"
    mapping = {"williamhillus": "Caesars", "williamhill_us": "Caesars", "caesars": "Caesars", "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "bovada": "Bovada", "espnbet": "ESPN Bet"}
    return mapping.get(str(raw_book).lower().replace("_","").replace(" ",""), str(raw_book).title())

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
        picks = []
        for item in data:
            matchup = get_matchup_string(item)
            p_name = item.get("player") or "Unknown"
            event = f"[{matchup}] {p_name}: {str(item.get('direction','')).upper()} {item.get('line','')} {item.get('market','')}"
            d_time = item.get("game_time") or "TBD"
            picks.append({
                "Event": event, "Price": item.get("best_price", -110), "Book": clean_book_name(item.get("best_book")), 
                "Time": d_time, "game_pk": item.get("game_pk"), "p_name": p_name, "market": item.get("market"), "line": item.get("line"), "dir": item.get("direction")
            })
        return picks
    except: return []

def load_data(user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_history")
    data = ws.get_all_records()
    # Adding hidden columns for live tracking
    cols = ['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit', 'game_pk', 'p_name', 'market', 'line', 'dir']
    df = pd.DataFrame(data)
    for c in cols: 
        if c not in df.columns: df[c] = ""
    return df

def save_data(df, user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_history")
    ws.clear()
    df_save = df.copy()
    df_save['Date'] = pd.to_datetime(df_save['Date']).dt.strftime('%Y-%m-%d')
    ws.update(values=[df_save.columns.values.tolist()] + df_save.fillna('').values.tolist(), range_name='A1')

def get_ws_smart(sheet, name):
    all_ws = {ws.title.lower().strip(): ws for ws in sheet.worksheets()}
    target = name.lower().strip()
    if target in all_ws: return all_ws[target]
    st.error(f"Tab Not Found: '{target}'"); st.stop()

def update_bankroll(amount, user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_bankroll")
    current = float(ws.acell('B1').value or 1000)
    ws.update_acell('B1', current + amount)
    st.session_state.bankroll = current + amount

# --- 5. AUTH & UI ---
ws_creds = sheet.worksheet("Credentials")
creds_df = pd.DataFrame(ws_creds.get_all_records())
credentials = {"usernames": {row['Username']: {"name": row['Name'], "password": str(row['Password']), "email": row['Email']} for _, row in creds_df.iterrows()}}
authenticator = stauth.Authenticate(credentials, "bet_tracker_cookie", "secure_v3_2026", cookie_expiry_days=30)

if not st.session_state.get("authentication_status"):
    authenticator.login(location='main')

if st.session_state["authentication_status"]:
    username, name = st.session_state["username"], st.session_state["name"]
    df_current = load_data(username)
    if 'bankroll' not in st.session_state: st.session_state.bankroll = float(get_ws_smart(sheet, f"{username}_bankroll").acell('B1').value or 1000)

    # Sidebar
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.metric("💰 Bankroll", f"\${st.session_state.bankroll:,.2f}")
    st.sidebar.markdown("### 🧮 Kelly Calculator")
    input_odds = st.sidebar.number_input("American Odds", step=1, value=-110)
    edge_pct = st.sidebar.number_input("Edge (%)", 0.0, 100.0, 15.0, step=1.0)
    round_toggle = st.sidebar.toggle("Round to Whole Number", value=True)
    k_sel = st.sidebar.radio("Multiplier", ["Full", "Half", "Quarter"], index=2, horizontal=True)
    dec_odds = (int(str(input_odds).replace('+','')) / 100 + 1) if int(str(input_odds).replace('+','')) > 0 else (100 / abs(int(str(input_odds).replace('+',''))) + 1)
    raw_k = (edge_pct/100) / (dec_odds - 1) if (dec_odds - 1) != 0 else 0
    s_stake = round(raw_k * {"Full":1.0, "Half":0.5, "Quarter":0.25}[k_sel] * st.session_state.bankroll) if round_toggle else round(raw_k * 0.25 * st.session_state.bankroll, 2)
    st.sidebar.metric("Suggested Stake", f"\${s_stake:,.2f}")

    nav = st.segmented_control("Navigation", ["🎯 Picks", "📝 Log", "📊 Stats", "🗄️ History"], default="🎯 Picks")

    if nav == "🎯 Picks":
        picks = get_glicks_picks()
        for p in picks:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.write(f"**{p['Event']}**")
                c1.write(f"⏰ {p['Time']} | 🏦 {p['Book']} | 📈 {p['Price']}")
                if c2.button("Track", key=p['Event']):
                    st.session_state.pending = p
                    st.toast("Redirecting to Log...")

    elif nav == "📝 Log":
        p = st.session_state.get('pending', {})
        with st.form("bet_form"):
            c1, c2 = st.columns(2)
            book = c1.selectbox("Book", ["DraftKings", "FanDuel", "BetMGM", "Caesars", "Bovada", "ESPN Bet"])
            event = c2.text_input("Event", p.get('Event', ''))
            stake = st.number_input("Stake", value=float(s_stake))
            if st.form_submit_button("Save Bet"):
                new_row = {"Date": datetime.datetime.now(NYC_TZ).date(), "Book": book, "State": "NY", "Event": event, "Odds": p.get('Price', -110), "Edge": edge_pct/100, "Stake": stake, "Result": "Pending", "Profit": 0, "game_pk": p.get('game_pk'), "p_name": p.get('p_name'), "market": p.get('market'), "line": p.get('line'), "dir": p.get('dir')}
                df_current = pd.concat([df_current, pd.DataFrame([new_row])], ignore_index=True)
                save_data(df_current, username); st.toast("Saved!"); st.rerun()

    elif nav == "🗄️ History":
        st.subheader("🏟️ Active Wagers")
        pending = df_current[df_current['Result'] == 'Pending']
        for i, row in pending.iterrows():
            pot_profit = row['Stake'] * (dec_odds - 1)
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([3, 1, 1, 0.5])
                col1.write(f"**{row['Event']}** | {row['Book']}")
                col1.write(f"💰 Wager: **\${row['Stake']:.2f}** | 📈 Potential: **\${pot_profit:.2f}**")
                
                # --- LIVE UPDATE FEATURE ---
                if row['game_pk']:
                    if col1.button("🔄 Check Live Status", key=f"live_{i}"):
                        live = get_live_mlb_stats(row['game_pk'], row['p_name'], row['market'])
                        if live:
                            st.info(f"Live Update: {row['p_name']} currently has **{live['val']}** {row['market']}. (Game: {live['status']})")
                        else: st.warning("Game hasn't started or player stats unavailable.")

                if col2.button("✅ Win", key=f"w{i}"):
                    df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Win', round(pot_profit, 2)
                    save_data(df_current, username); update_bankroll(pot_profit, username); st.rerun()
                if col3.button("❌ Loss", key=f"l{i}"):
                    df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Loss', -row['Stake']
                    save_data(df_current, username); update_bankroll(-row['Stake'], username); st.rerun()

    elif nav == "📊 Stats":
        if not df_current.empty:
            df_dash = df_current.copy()
            df_dash['Date'] = pd.to_datetime(df_dash['Date']).dt.date
            daily = df_dash.groupby('Date')['Profit'].sum().reset_index()
            daily['CumProfit'] = daily['Profit'].cumsum()
            st.plotly_chart(px.line(daily, x='Date', y='CumProfit', title="Profit Over Time", markers=True))