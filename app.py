import streamlit as st
import requests
from requests.auth import HTTPBasicAuth
from google import genai
from google.genai import types
from pydantic import BaseModel
from datetime import datetime, timedelta

# ==========================================
# 1. API CONFIGURATION & SECRETS
# ==========================================
# When deployed to Streamlit Cloud, these pull from your App Secrets automatically.
# For local testing, it fallbacks to st.secrets as well if defined in .streamlit/secrets.toml.
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    ATHLETE_ID = st.secrets["INTERVALS_ICU_ATHLETE_ID"]
    API_KEY = st.secrets["INTERVALS_ICU_API_KEY"]
except KeyError:
    st.error("Missing API keys! Please configure GEMINI_API_KEY, INTERVALS_ICU_ATHLETE_ID, and INTERVALS_ICU_API_KEY in your Streamlit Secrets.")
    st.stop()

AUTH = HTTPBasicAuth("API_KEY", API_KEY)
client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# 2. DATA STRUCTURES (PYDANTIC)
# ==========================================
class WorkoutEvent(BaseModel):
    day_offset: int  # 0 for Monday, 1 for Tuesday, etc.
    title: str
    type: str        # Must be "Run", "Ride", or "Swim"
    description: str # The strict Intervals.icu formatting text block

class WeeklyPlan(BaseModel):
    workouts: list[WorkoutEvent]
    coach_notes: str # Encouragement or high-level focus tips for the week

# ==========================================
# 3. CORE LOGIC FUNCTIONS
# ==========================================
def get_athlete_fitness():
    """Fetches real-time fitness values and threshold targets from Intervals.icu."""
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
    try:
        response = requests.get(url, auth=AUTH)
        response.raise_for_status()
        data = response.json()
        return {
            "ftp": data.get("icu_ftp", 200),
            "ctl": round(data.get("ctl", 0)),
            "atl": round(data.get("atl", 0)),
        }
    except Exception as e:
        st.error(f"Failed to fetch data from Intervals.icu: {e}")
        return {"ftp": 200, "ctl": 0, "atl": 0}

def generate_weekly_plan(user_schedule_input: str, fitness_metrics: dict, current_phase: str):
    """Sends current fitness state and schedule limitations to Gemini to build the plan."""
    system_instruction = f"""
    You are an elite, practical triathlon coach helping an athlete train for Full Ironman Copenhagen in August 2027.
    The athlete has a newborn and a new job. Prioritize high-frequency, short, highly efficient sessions.
    
    Current Plan Phase: {current_phase}
    Current Athlete Context from Intervals.icu:
    - Bike FTP: {fitness_metrics['ftp']}W
    - Fitness Score (CTL): {fitness_metrics['ctl']}
    - Fatigue Score (ATL): {fitness_metrics['atl']}
    
    For the 'description' field of each workout, you MUST use strict Intervals.icu syntax.
    Do not add conversational text inside the description field. 
    Format example for cycling:
    - 10m warm up spinning easy
    - 3x 8m 88-92% FTP / 3m 50% FTP [Focus on aero position]
    - 8m cool down
    
    Format example for running:
    - 10m ramp 50-70% HR
    - 3x 6m 95% HR / 2m easy jog
    - 7m 60% HR
    """

    prompt = f"Here is my available time and constraints for this upcoming week: {user_schedule_input}"

    try:
        response = client.models.generate_content(
            model='gemini-1.5-pro',
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=WeeklyPlan,
                temperature=0.3
            ),
        )
        structured_plan_json = WeeklyPlan.model_validate_json(response.text)
        return structured_plan_json
    except Exception as e:
        st.error(f"Error generating workout plan with Gemini: {e}")
        return None

def push_workouts_to_calendar(weekly_plan):
    """Calculates upcoming calendar dates and uploads the text schemas to Intervals.icu."""
    today = datetime.now()
    # Calculate target date of the upcoming Monday
    next_monday = today + timedelta(days=(0 - today.weekday()) % 7)
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events"
    
    success_count = 0
    for workout in weekly_plan.workouts:
        target_date = next_monday + timedelta(days=workout.day_offset)
        
        event_data = {
            "start_date_local": target_date.strftime("%Y-%m-%dT07:00:00"),
            "type": workout.type,
            "name": workout.title,
            "description": workout.description,
            "category": "WORKOUT"
        }
        
        response = requests.post(url, auth=AUTH, json=event_data)
        if response.status_code in [200, 201]:
            success_count += 1
            
    return success_count

# ==========================================
# 4. STREAMLIT RESPONSIVE UI
# ==========================================
st.set_page_config(page_title="AI Tri-Coach", page_icon="🏃‍♂️", layout="centered")

st.title("🏃‍♂️ AI Tri-Coach: Copenhagen 2027")
st.write("Optimize your micro-sessions around your family and new career.")

# Active Phase Selection
phase_options = [
    "Phase 1: The Transition Zone (Sep-Nov) - Maintenance & Sanity",
    "Phase 2: The Aerobic Engine (Dec-Feb) - Short & Intense",
    "Phase 3: Strength & Volume (Mar-May) - Base Building",
    "Phase 4: Peak & Race Specific (Jun-Aug) - Long Sims & Bricks"
]
selected_phase = st.selectbox("Select Your Current Training Phase:", phase_options)

# Load / Sync Fitness Data
if "metrics" not in st.session_state:
    st.session_state["metrics"] = None

if st.button("🔄 Sync Metrics from Intervals.icu"):
    with st.spinner("Fetching performance data..."):
        metrics = get_athlete_fitness()
        st.session_state["metrics"] = metrics
        st.toast("Performance data synced successfully!", icon="✅")

# Display current profile stats if available
if st.session_state["metrics"]:
    m = st.session_state["metrics"]
    st.info(f"📊 **Current Fitness Profile:** FTP: **{m['ftp']}W** | Fitness (CTL): **{m['ctl']}** | Fatigue (ATL): **{m['atl']}**")

# Schedule text area input
user_schedule = st.text_area(
    "Enter your time availability for next week:",
    placeholder="e.g., Tuesday: 45 min early morning. Thursday: 45 min at lunch. Saturday: 90 min window at 6 AM. Target: 3 focused sessions.",
    height=120
)

# Execution Action Button
if st.button("🎯 Build Plan & Push to Calendar", type="primary"):
    if not user_schedule.strip():
        st.warning("Please outline your upcoming schedule before processing.")
    else:
        # Fallback to automated sync if user forgot to press sync button
        if not st.session_state["metrics"]:
            st.session_state["metrics"] = get_athlete_fitness()
            
        with st.spinner("Analyzing schedule and drafting custom intervals..."):
            plan = generate_weekly_plan(user_schedule, st.session_state["metrics"], selected_phase)
            
            if plan:
                st.subheader("📋 Drafted Weekly Strategy")
                st.write(f"_*Coach Notes:* {plan.coach_notes}_")
                
                # Show preview of sessions
                for w in plan.workouts:
                    with st.expander(f"🔹 {w.title} ({w.type})"):
                        st.code(w.description, language="text")
                
                # Push directly to API
                with st.spinner("Uploading plans directly to calendar..."):
                    uploaded = push_workouts_to_calendar(plan)
                    if uploaded > 0:
                        st.balloons()
                        st.success(f"Success! {uploaded} workouts pushed directly to your Intervals.icu calendar. Check your device app!")
