import streamlit as st
import pandas as pd
import datetime
import plotly.express as px
import json
import gspread
from google.oauth2.service_account import Credentials

# --- 1. PAGE CONFIG MUST BE ABSOLUTELY FIRST ---
st.set_page_config(page_title="Glick Pick Tracker", layout="wide")

# --- 2. GOOGLE SHEETS CONNECTION ---
@st.cache_resource
def init_gsheets():
    # Load the secret JSON and authenticate
    creds_json = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(creds)
    # Open the specific sheet
    return client.open_by_url(st.secrets["SHEET_URL"])

sheet = init_gsheets()

# --- 3. DATABASE FUNCTIONS (NOW CLOUD-BASED) ---
def load_data():
    ws = sheet.worksheet("History")
    records = ws.get_all_records()
    if records:
        df = pd.DataFrame(records)
        df['Date'] = pd.to_datetime(df['Date'], format='mixed')
        return df
    return pd.DataFrame(columns=["Date", "Book", "State", "Event", "Odds", "Edge", "Stake", "Result", "Profit"])

def save_data(df):
    ws = sheet.worksheet("History")
    ws.clear()
    df_save = df.copy()
    
    # FORCING THE DATETIME CONVERSION HERE
    df_save['Date'] = pd.to_datetime(df_save['Date']).dt.strftime('%Y-%m-%d')
    
    # Update the sheet with new data
    ws.update(values=[df_save.columns.values.tolist()] + df_save.fillna('').values.tolist(), range_name='A1')

def load_dropdowns():
    ws = sheet.worksheet("Dropdowns")
    books = ws.col_values(1)[1:] # Skip header
    states = ws.col_values(2)[1:]
    return {
        "books": [b for b in books if b.strip()], 
        "states": [s for s in states if s.strip()]
    }

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

# --- 4. STATE MANAGEMENT & CALLBACKS ---
if 'bankroll' not in st.session_state:
    ws = sheet.worksheet("Bankroll")
    val = ws.acell('B1').value
    st.session_state.bankroll = float(val) if val else 1000.0

def update_bankroll(amount):
    st.session_state.bankroll += amount
    ws = sheet.worksheet("Bankroll")
    ws.update_acell('B1', st.session_state.bankroll)

def set_bankroll(amount):
    st.session_state.bankroll = amount
    ws = sheet.worksheet("Bankroll")
    ws.update_acell('B1', amount)

def handle_odds_change():
    val = st.session_state.odds_input
    if val == -99:
        st.session_state.odds_input = 100
    elif val == 99:
        st.session_state.odds_input = -100

# --- 5. GLOBAL STATS CALCULATION ---
df = load_data()
today = pd.to_datetime(datetime.date.today())
if not df.empty:
    df['Date'] = pd.to_datetime(df['Date'], format='mixed')
    total_profit_today = df[df['Date'] == today]['Profit'].sum()
else:
    total_profit_today = 0.0

# --- 6. UI: HEADER & SIDEBAR ---
st.title("Kelly Criterion & Tracker")

st.sidebar.metric(
    label="💰 Total Bankroll", 
    value=f"${st.session_state.bankroll:,.2f}", 
    delta=f"{total_profit_today:,.2f}"
)

with st.sidebar.expander("⚙️ Adjust Balance"):
    bankroll_action = st.radio("Action", ["Add/Remove", "Set Exact"], horizontal=True, label_visibility="collapsed")
    if bankroll_action == "Add/Remove":
        adj = st.number_input("Amount ($)", value=0.0, step=10.0)
        if st.button("Update Balance", use_container_width=True):
            update_bankroll(adj)
            st.rerun()
    else:
        new_bankroll = st.number_input("Exact ($)", value=float(st.session_state.bankroll), step=10.0)
        if st.button("Set Balance", use_container_width=True):
            set_bankroll(new_bankroll)
            st.rerun()

st.sidebar.divider()
st.sidebar.header("🧮 Kelly Calculator")

if 'odds_input' not in st.session_state:
    st.session_state.odds_input = -110

input_odds = st.sidebar.number_input("American Odds", step=1, key="odds_input", on_change=handle_odds_change)
input_edge_percent = st.sidebar.number_input("Estimated Edge (%)", min_value=0.0, max_value=100.0, value=15.0, step=0.1, format="%.1f")
input_edge = input_edge_percent / 100

kelly_options = {"Full": 1.0, "Half": 0.5, "Quarter": 0.25}
selected_multiplier = st.sidebar.radio("Kelly Multiplier", options=list(kelly_options.keys()), index=2, horizontal=True)
kelly_frac = kelly_options[selected_multiplier]
round_stake = st.sidebar.toggle("Round Stake to Whole $", value=False)

dec_odds = american_to_decimal(input_odds)
b = dec_odds - 1
full_k = input_edge / b if b != 0 else 0

active_k = full_k * kelly_frac
raw_stake = active_k * st.session_state.bankroll

if round_stake:
    suggested_stake = float(round(raw_stake))
else:
    suggested_stake = round(raw_stake, 2)

st.sidebar.metric(f"Suggested Stake ({selected_multiplier})", f"${suggested_stake:,.2f}")
st.sidebar.caption(f"Full: {round(full_k*100, 2)}% | Half: {round((full_k/2)*100, 2)}% | Qtr: {round((full_k/4)*100, 2)}%")


# --- 7. UI: MAIN TABS ---
tabs = st.tabs(["📝 Log New Bet", "📊 Dashboard", "🗄️ History"])

with tabs[0]:
    st.subheader("Enter Wager Details")
    dropdowns = load_dropdowns()
    
    with st.form("bet_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        date = c1.date_input("Date", datetime.date.today())
        
        # Fallbacks just in case the sheet is empty initially
        safe_books = dropdowns["books"] if dropdowns["books"] else ["Add a book below"]
        safe_states = dropdowns["states"] if dropdowns["states"] else ["Add a state below"]
        
        book = c2.selectbox("Sportsbook", safe_books)
        state = c3.selectbox("State", safe_states)
        
        c4, c5, c6 = st.columns(3)
        event = c4.text_input("Event / Matchup")
        final_stake = c5.number_input("Actual Stake ($)", value=float(suggested_stake))
        result = c6.selectbox("Status", ["Pending", "Win", "Loss", "Push"])
        
        submitted = st.form_submit_button("Save Bet to Database")
        
        if submitted:
            if not event.strip():
                st.error("⚠️ Please enter an Event / Matchup before saving.")
            else:
                profit = 0
                if result == "Win":
                    profit = round(final_stake * (dec_odds - 1), 2)
                elif result == "Loss":
                    profit = -final_stake
                
                new_data = {
                    "Date": date, "Book": book, "State": state, "Event": event,
                    "Odds": input_odds, "Edge": input_edge, "Stake": final_stake, 
                    "Result": result, "Profit": profit
                }
                
                df_current = load_data()
                df_current = pd.concat([df_current, pd.DataFrame([new_data])], ignore_index=True)
                save_data(df_current)
                
                # --- AUTOMATIC UPDATES ---
                # Update bankroll if the bet was already settled upon logging
                if profit != 0:
                    update_bankroll(profit)
                
                # Use a toast so the message survives the rerun
                st.toast(f"Successfully logged {event} on {book}!", icon="✅")
                
                # Force app to refresh so Dashboard/History update immediately
                st.rerun()

    with st.expander("⚙️ Manage Sportsbooks & States"):
        mc1, mc2 = st.columns(2)
        with mc1:
            st.write("**Sportsbooks**")
            new_book = st.text_input("New Sportsbook Name")
            if st.button("➕ Add Book"):
                if new_book and new_book not in dropdowns["books"]:
                    dropdowns["books"].append(new_book)
                    save_dropdowns(dropdowns)
                    st.rerun()
            
            rem_book = st.selectbox("Select Book to Remove", dropdowns["books"])
            if st.button("🗑️ Remove Book"):
                if rem_book in dropdowns["books"]:
                    dropdowns["books"].remove(rem_book)
                    save_dropdowns(dropdowns)
                    st.rerun()
                    
        with mc2:
            st.write("**States**")
            new_state = st.text_input("New State Name")
            if st.button("➕ Add State"):
                if new_state and new_state not in dropdowns["states"]:
                    dropdowns["states"].append(new_state)
                    save_dropdowns(dropdowns)
                    st.rerun()
            
            rem_state = st.selectbox("Select State to Remove", dropdowns["states"])
            if st.button("🗑️ Remove State"):
                if rem_state in dropdowns["states"]:
                    dropdowns["states"].remove(rem_state)
                    save_dropdowns(dropdowns)
                    st.rerun()

with tabs[1]:
    if not df.empty:
        total_profit = df['Profit'].sum()
        roi = (total_profit / df['Stake'].sum()) * 100 if df['Stake'].sum() > 0 else 0
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Total P/L", f"${total_profit:,.2f}")
        m2.metric("ROI %", f"{roi:.2f}%")
        m3.metric("Total Bets", len(df))
        
        st.divider()
        st.subheader("📈 Bankroll Trend")
        
        all_books = df['Book'].dropna().unique().tolist()
        selected_books = st.multiselect("Filter by Sportsbook", options=all_books, default=all_books)
        
        if not selected_books:
            filtered_df = df.copy()
        else:
            filtered_df = df[df['Book'].isin(selected_books)].copy()
        
        if not filtered_df.empty:
            filtered_df = filtered_df.sort_values('Date')
            filtered_df['Cumulative Profit'] = filtered_df['Profit'].cumsum()
            filtered_df['Formatted Date'] = pd.to_datetime(filtered_df['Date']).dt.strftime('%Y-%m-%d')
            
            fig = px.line(
                filtered_df, 
                x='Formatted Date', 
                y='Cumulative Profit', 
                title="Total Profit Over Time",
                line_shape="hv", 
                markers=True
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No bets match the selected filter.")
    else:
        st.info("No bets logged yet. Go to the 'Log New Bet' tab.")

with tabs[2]:
    st.subheader("🏟️ Active Wagers")
    pending = df[df['Result'] == 'Pending']

    if pending.empty:
        st.info("No active bets. Time to find an edge!")
    else:
        for i, row in pending.iterrows():
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([3, 1, 1, 0.5])
                col1.write(f"**{row['Event']}** ({row['State']})")
                col1.caption(f"{row['Book']} | {row['Odds']} | {row['Edge']*100:.1f}% Edge")
                
                if col2.button("✅ Win", key=f"win_{i}"):
                    profit = row['Stake'] * (american_to_decimal(row['Odds']) - 1)
                    df.at[i, 'Result'] = 'Win'
                    df.at[i, 'Profit'] = round(profit, 2)
                    save_data(df)
                    update_bankroll(profit)
                    st.rerun()

                if col3.button("❌ Loss", key=f"loss_{i}"):
                    df.at[i, 'Result'] = 'Loss'
                    df.at[i, 'Profit'] = -row['Stake']
                    save_data(df)
                    update_bankroll(-row['Stake'])
                    st.rerun()
                
                if col4.button("🗑️", key=f"del_{i}", help="Delete this bet (no bankroll impact)"):
                    df = df.drop(i)
                    save_data(df)
                    st.toast(f"Deleted bet: {row['Event']}") 
                    st.rerun()

    st.divider()
    st.subheader("📜 Settled Bets History")
    settled = df[df['Result'] != 'Pending'].sort_values('Date', ascending=False)
    
    if not settled.empty:
        st.dataframe(
            settled[['Date', 'Book', 'State', 'Event', 'Odds', 'Stake', 'Result', 'Profit']], 
            use_container_width=True, 
            hide_index=True
        )
        
        with st.expander("🗑️ Delete a Settled Bet"):
            del_c1, del_c2 = st.columns([3, 1])
            settled_options = {}
            for idx, row in settled.iterrows():
                date_str = pd.to_datetime(row['Date']).strftime('%Y-%m-%d')
                label = f"{date_str} | {row['Event']} ({row['Result']} | Profit: ${row['Profit']})"
                settled_options[label] = idx
            
            selected_bet_to_delete = del_c1.selectbox("Select bet to remove:", options=[""] + list(settled_options.keys()))
            
            if del_c2.button("Delete & Refund Bankroll", use_container_width=True, type="primary"):
                if selected_bet_to_delete:
                    target_idx = settled_options[selected_bet_to_delete]
                    profit_to_reverse = df.at[target_idx, 'Profit']
                    update_bankroll(-profit_to_reverse)
                    df = df.drop(target_idx)
                    save_data(df)
                    st.success("Bet permanently deleted and bankroll automatically adjusted!")
                    st.rerun()
                else:
                    st.error("Please select a bet to delete first.")
    else:
        st.info("No settled bets to display yet.")