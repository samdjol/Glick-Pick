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

# --- 3. MLB LIVE DATA ENGINE ---
@st.cache_data(ttl=600)
def get_mlb_player_stats(game_pk, player_name):
    """Fetches live boxscore from MLB and extracts specific player stats."""
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
                    # Pitching
                    pitching = stats.get('pitching', {})
                    ip = pitching.get('inningsPitched', "0.0")
                    f_i = int(float(ip))
                    part = round((float(ip) - f_i) * 10)
                    outs = (f_i * 3) + part
                    # Batting
                    bat = stats.get('batting', {})
                    total_bases = (bat.get('hits', 0) - bat.get('doubles', 0) - bat.get('triples', 0) - bat.get('homeRuns', 0)) + \
                                  (bat.get('doubles', 0) * 2) + (bat.get('triples', 0) * 3) + (bat.get('homeRuns', 0) * 4)
                    return {"outs": outs, "bases": total_bases, "status": data.get('status', {}).get('abstractGameState', 'Unknown')}
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
    headers = {"apikey": "sb_publishable_aAFvyqUjJFYQsuG8GY2KTA_U4SLd545", "Authorization": "Bearer sb_publishable_aAFvyqUjJFYQsuG8GY2KTA_U4SLd545"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        picks = []
        for item in data:
            matchup = get_matchup_string(item)
            p_name = item.get("player", "Unknown")
            event = f"[{matchup}] {p_name}: {str(item.get('direction','')).upper()} {item.get('line','')} {item.get('market','')}"
            raw_p = item.get("best_price", -110)
            picks.append({
                "Event": event, "Price": f"+{raw_p}" if raw_p > 0 else str(raw_p),
                "Book": clean_book_name(item.get("best_book")), "Time": item.get("game_time", "TBD"),
                "SortKey": datetime.datetime.strptime(item.get("game_time", "11:59 PM ET").replace(" ET",""), "%I:%M %p").strftime("%H:%M") if item.get("game_time") else "23:59",
                "game_pk": item.get("game_pk"), "player_name": p_name, "market": item.get("market"), "line": item.get("line"), "dir": item.get("direction")
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
    
    # REQUIRED COLUMNS FOR THE NEW VERSION
    required_cols = ['Date', 'Book', 'State', 'Event', 'Odds', 'Edge', 'Stake', 'Result', 'Profit', 'game_pk', 'player_name', 'market', 'line', 'dir']
    
    if not data:
        return pd.DataFrame(columns=required_cols)
    
    df = pd.DataFrame(data)
    
    # SELF-HEALING: Add missing columns if they don't exist in the Google Sheet
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""
            
    return df

def save_data(df, user_prefix):
    ws = get_ws_smart(sheet, f"{user_prefix}_history")
    ws.clear()
    df_save = df.copy()
    if not df_save.empty:
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

def american_to_decimal(odds):
    try:
        val = int(str(odds).replace('+', ''))
        return (val / 100) + 1 if val > 0 else (100 / abs(val)) + 1
    except: return 1.91

# --- 6. AUTH & UI ---
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
authenticator = stauth.Authenticate(creds, "bet_tracker_cookie", "secure_v2026", cookie_expiry_days=30)

if not st.session_state.get("authentication_status"):
    authenticator.login(location='main')

if st.session_state["authentication_status"]:
    username, name = st.session_state["username"], st.session_state["name"]
    if 'bankroll' not in st.session_state: st.session_state.bankroll = load_bankroll(username)
    df_current = load_data(username)

    # Sidebar
    authenticator.logout('Logout', 'sidebar')
    st.sidebar.metric("💰 Bankroll", f"${st.session_state.bankroll:,.2f}")
    st.sidebar.header("🧮 Kelly Calculator")
    input_odds = st.sidebar.number_input("American Odds", step=1, value=-110)
    edge_pct = st.sidebar.number_input("Edge (%)", 0.0, 100.0, 15.0)
    round_toggle = st.sidebar.toggle("Round to Whole Number", value=True)
    k_sel = st.sidebar.radio("Multiplier", ["Full", "Half", "Quarter"], index=2, horizontal=True)
    
    dec_odds = american_to_decimal(input_odds)
    raw_k = (edge_pct/100) / (dec_odds - 1) if (dec_odds - 1) != 0 else 0
    s_stake = round(raw_k * {"Full":1.0, "Half":0.5, "Quarter":0.25}[k_sel] * st.session_state.bankroll) if round_toggle else round(raw_k * 0.25 * st.session_state.bankroll, 2)
    st.sidebar.metric("Suggested Stake", f"${s_stake:,.2f}")

    nav = st.segmented_control("Nav", ["🎯 Picks", "📝 Log", "📊 Stats", "🗄️ History"], selection_mode="single", default="🎯 Picks")

    if nav == "🎯 Picks":
        picks = get_glicks_picks()
        for p in picks:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.write(f"**{p['Event']}**")
                c1.write(f"⏰ {p['Time']} | 🏦 {p['Book']} | 📈 {p['Price']}")
                if c2.button("Track", key=p['Event'], width='stretch'):
                    st.session_state.pending_track = {"event":p['Event'], "book":p['Book'], "odds":int(p['Price'].replace('+','')), "game_pk":p['game_pk'], "player":p['player_name'], "market":p['market'], "line":p['line'], "dir":p['dir']}
                    st.rerun()

    elif nav == "📝 Log":
        p_data = st.session_state.get('pending_track', {})
        with st.form("bet_form"):
            c1, c2 = st.columns(2)
            book = c1.selectbox("Book", ["DraftKings", "FanDuel", "BetMGM", "Caesars", "Bovada", "BetRivers", "ESPN Bet"], index=0)
            event = c2.text_input("Event", p_data.get('event', ''))
            stake = st.number_input("Stake", value=float(s_stake))
            if st.form_submit_button("Save Bet"):
                new_row = {"Date": datetime.datetime.now(NYC_TZ).date(), "Book": book, "State": "NY", "Event": event, "Odds": p_data.get('odds',-110), "Edge": edge_pct/100, "Stake": stake, "Result": "Pending", "Profit": 0, "game_pk": p_data.get('game_pk', ''), "player_name": p_data.get('player', ''), "market": p_data.get('market', ''), "line": p_data.get('line', ''), "dir": p_data.get('dir', '')}
                df_current = pd.concat([df_current, pd.DataFrame([new_row])], ignore_index=True)
                save_data(df_current, username)
                st.session_state.pending_track = {}
                st.toast("Saved!"); st.rerun()

    elif nav == "🗄️ History":
        st.subheader("🏟️ Active Wagers")
        pending = df_current[df_current['Result'] == 'Pending']
        if pending.empty: st.info("No pending bets.")
        for i, row in pending.iterrows():
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                col1.write(f"**{row['Event']}**")
                
                # Use .get() or check string to avoid KeyError if row is weird
                g_pk = row.get('game_pk')
                if g_pk and str(g_pk).strip() != "":
                    if col2.button("🔍 Auto-Check", key=f"chk{i}"):
                        stats = get_mlb_player_stats(g_pk, row['player_name'])
                        if stats:
                            mkt = str(row['market']).lower()
                            val = stats['outs'] if 'outs' in mkt else stats['bases']
                            # Simple logic for winner
                            line = float(row['line']) if row['line'] else 0
                            is_winning = (row['dir'] == 'OVER' and val > line) or (row['dir'] == 'UNDER' and val < line)
                            status_icon = "✅ Winning" if is_winning else "❌ Losing"
                            st.info(f"Live: {row['player_name']} has **{val}** {row['market']}. Status: {status_icon} ({stats['status']})")
                        else: st.warning("Game data not available yet.")
                
                if col3.button("✅ Win", key=f"w{i}"):
                    p = row['Stake'] * (american_to_decimal(row['Odds']) - 1)
                    df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Win', round(p, 2)
                    save_data(df_current, username); update_bankroll(p, username); st.rerun()
                if col4.button("❌ Loss", key=f"l{i}"):
                    df_current.at[i, 'Result'], df_current.at[i, 'Profit'] = 'Loss', -row['Stake']
                    save_data(df_current, username); update_bankroll(-row['Stake'], username); st.rerun()

    elif nav == "📊 Stats":
        if not df_current.empty:
            st.metric("Total P/L", f"${df_current['Profit'].sum():,.2f}")

elif st.session_state["authentication_status"] is False:
    st.error("Invalid Login")