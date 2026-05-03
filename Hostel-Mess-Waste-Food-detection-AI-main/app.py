from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
from urllib import error as url_error
from urllib import request as url_request
from io import BytesIO
import base64

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
import qrcode

from model import load_dataset, predict_waste, suggested_food_quantity, train_waste_model

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATASET_PATH = BASE_DIR / "dataset.csv"
ATTENDANCE_PATH = BASE_DIR / "attendance.csv"
MENU_PATH = BASE_DIR / "menu.json"
STUDENT_CREDENTIALS_PATH = BASE_DIR / "student_credentials.json"
ADMIN_USERS_PATH = BASE_DIR / "admin_users.json"
FEEDBACK_PATH = BASE_DIR / "menu_feedback.csv"
ATTENDANCE_COLUMNS = ["timestamp", "student_name", "meal_slot", "status", "finalized"]
FEEDBACK_COLUMNS = ["timestamp", "date", "student_name", "meal_slot", "rating", "comment"]
DEFAULT_STUDENT_PIN = "1234"

EAT_STATUSES = {"eat", "will eat", "present", "yes", "y", "1"}

MEAL_WINDOWS = [
    {"meal": "Breakfast", "start": 8 * 60, "end": 10 * 60, "label": "08:00 AM - 10:00 AM"},
    {"meal": "Lunch", "start": 12 * 60, "end": 15 * 60, "label": "12:00 PM - 03:00 PM"},
    {"meal": "Tea", "start": 17 * 60, "end": 18 * 60, "label": "05:00 PM - 06:00 PM"},
    {"meal": "Dinner", "start": 20 * 60, "end": 22 * 60, "label": "08:00 PM - 10:00 PM"},
]

DEFAULT_STUDENT_NAMES = [
    "Aarav Sharma",
    "Ishita Verma",
    "Rohan Das",
    "Sana Khan",
    "Yash Patel",
    "Priya Iyer",
    "Kunal Singh",
    "Meera Nair",
    "Dev Malhotra",
    "Tanvi Joshi",
    "Arjun Roy",
    "Neha Kulkarni",
]

DEFAULT_MENU = {
    "updated_at": "11:30 AM",
    "menus": {
        "Breakfast": "Poha, Boiled Eggs, Banana, Milk",
        "Lunch": "Dal, Rice, Sabzi, Salad",
        "Tea": "Masala Tea, Veg Sandwich, Biscuits",
        "Dinner": "Roti, Paneer Curry, Jeera Rice, Kheer",
    },
}

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
ROLE_ORDER = {"staff": 1, "manager": 2, "superadmin": 3}
DEFAULT_ADMIN_USERS = [
    {"username": "admin", "password": "admin123", "role": "superadmin"},
    {"username": "manager", "password": "manager123", "role": "manager"},
    {"username": "staff", "password": "staff123", "role": "staff"},
]
MEAL_FORECAST_MULTIPLIERS = {
    "Breakfast": 0.78,
    "Lunch": 1.0,
    "Tea": 0.46,
    "Dinner": 0.92,
}

app = Flask(__name__)
app.config["SECRET_KEY"] = "hostel-mess-secret-key"

CHATBOT_API_KEY = os.getenv("CHATBOT_API_KEY", "").strip()
CHATBOT_API_URL = os.getenv(
    "CHATBOT_API_URL",
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
).strip()
CHATBOT_MODEL = os.getenv("CHATBOT_MODEL", "gemini-1.5-flash").strip()
CHATBOT_MODEL_FALLBACKS = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-2.0-flash",
    "gemini-2.0-flash-exp",
]


def load_attendance() -> pd.DataFrame:
    if not ATTENDANCE_PATH.exists():
        pd.DataFrame(columns=ATTENDANCE_COLUMNS).to_csv(ATTENDANCE_PATH, index=False)

    attendance = pd.read_csv(ATTENDANCE_PATH)

    # Keep backward compatibility with older attendance files.
    for col in ATTENDANCE_COLUMNS:
        if col not in attendance.columns:
            attendance[col] = 0 if col == "finalized" else ""

    attendance["finalized"] = pd.to_numeric(attendance["finalized"], errors="coerce").fillna(0)
    attendance["finalized"] = attendance["finalized"].astype(int)

    return attendance[ATTENDANCE_COLUMNS]


def ensure_menu_file() -> None:
    if MENU_PATH.exists():
        return

    MENU_PATH.write_text(json.dumps(DEFAULT_MENU, indent=2), encoding="utf-8")


def load_menu() -> dict:
    ensure_menu_file()

    try:
        parsed = json.loads(MENU_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        parsed = DEFAULT_MENU.copy()

    menus = parsed.get("menus") if isinstance(parsed, dict) else {}
    if not isinstance(menus, dict):
        menus = {}

    merged_menus = {**DEFAULT_MENU["menus"], **menus}
    updated_at = str(parsed.get("updated_at", DEFAULT_MENU["updated_at"]))

    return {
        "updated_at": updated_at,
        "menus": merged_menus,
    }


def normalize_name(value: str) -> str:
    return str(value).strip().lower()


def ensure_admin_users_file() -> None:
    if ADMIN_USERS_PATH.exists():
        return

    ADMIN_USERS_PATH.write_text(json.dumps(DEFAULT_ADMIN_USERS, indent=2), encoding="utf-8")


def load_admin_users() -> dict[str, dict[str, str]]:
    ensure_admin_users_file()

    try:
        raw = json.loads(ADMIN_USERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = DEFAULT_ADMIN_USERS.copy()

    users = raw if isinstance(raw, list) else DEFAULT_ADMIN_USERS.copy()
    parsed: dict[str, dict[str, str]] = {}

    for user in users:
        if not isinstance(user, dict):
            continue

        username = str(user.get("username", "")).strip()
        password = str(user.get("password", "")).strip()
        role = str(user.get("role", "staff")).strip().lower() or "staff"
        if role not in ROLE_ORDER:
            role = "staff"

        if not username or not password:
            continue

        parsed[username.lower()] = {
            "username": username,
            "password": password,
            "role": role,
        }

    if not parsed:
        for user in DEFAULT_ADMIN_USERS:
            parsed[user["username"]] = user

    return parsed


def ensure_feedback_file() -> None:
    if FEEDBACK_PATH.exists():
        return

    pd.DataFrame(columns=FEEDBACK_COLUMNS).to_csv(FEEDBACK_PATH, index=False)


def load_feedback() -> pd.DataFrame:
    ensure_feedback_file()
    feedback = pd.read_csv(FEEDBACK_PATH)

    for col in FEEDBACK_COLUMNS:
        if col not in feedback.columns:
            feedback[col] = ""

    return feedback[FEEDBACK_COLUMNS]


def save_feedback(feedback: pd.DataFrame) -> None:
    feedback[FEEDBACK_COLUMNS].to_csv(FEEDBACK_PATH, index=False)


def ensure_student_credentials_file() -> None:
    if STUDENT_CREDENTIALS_PATH.exists():
        return

    records = [
        {
            "student_name": name,
            "pin": DEFAULT_STUDENT_PIN,
        }
        for name in DEFAULT_STUDENT_NAMES
    ]
    STUDENT_CREDENTIALS_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


def load_student_credentials() -> dict[str, dict[str, str]]:
    ensure_student_credentials_file()

    try:
        raw = json.loads(STUDENT_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = []

    entries = raw if isinstance(raw, list) else []
    merged: dict[str, dict[str, str]] = {}

    for item in entries:
        if not isinstance(item, dict):
            continue
        name = str(item.get("student_name", "")).strip()
        pin = str(item.get("pin", "")).strip() or DEFAULT_STUDENT_PIN
        if not name:
            continue
        merged[normalize_name(name)] = {
            "student_name": name,
            "pin": pin,
        }

    attendance_names = get_student_names_from_attendance(load_attendance())
    modified = False
    for name in attendance_names:
        norm = normalize_name(name)
        if not norm:
            continue
        if norm not in merged:
            merged[norm] = {
                "student_name": name,
                "pin": DEFAULT_STUDENT_PIN,
            }
            modified = True

    if modified or len(entries) != len(merged):
        serializable = list(merged.values())
        serializable.sort(key=lambda row: normalize_name(row["student_name"]))
        STUDENT_CREDENTIALS_PATH.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

    return merged


def save_student_credentials(credentials: dict[str, dict[str, str]]) -> None:
    serializable = list(credentials.values())
    serializable.sort(key=lambda row: normalize_name(row.get("student_name", "")))
    STUDENT_CREDENTIALS_PATH.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


def save_menu(menu_data: dict) -> None:
    MENU_PATH.write_text(json.dumps(menu_data, indent=2), encoding="utf-8")


def current_and_next_meal(now: datetime) -> tuple[dict | None, dict]:
    now_minutes = now.hour * 60 + now.minute
    active = None

    for meal in MEAL_WINDOWS:
        if now_minutes >= int(meal["start"]) and now_minutes < int(meal["end"]):
            active = meal
            break

    next_meal = next((meal for meal in MEAL_WINDOWS if now_minutes < int(meal["start"])), None)
    if not next_meal:
        next_meal = MEAL_WINDOWS[0]

    return active, next_meal


def seconds_until_meal_event(now: datetime, active_meal: dict | None, next_meal: dict) -> int:
    target = now.replace(second=0, microsecond=0)

    if active_meal:
        end_minutes = int(active_meal["end"])
        target = target.replace(hour=end_minutes // 60, minute=end_minutes % 60)
    else:
        start_minutes = int(next_meal["start"])
        target = target.replace(hour=start_minutes // 60, minute=start_minutes % 60)
        now_minutes = now.hour * 60 + now.minute
        if now_minutes >= int(next_meal["start"]):
            target += timedelta(days=1)

    return max(int((target - now).total_seconds()), 0)


def get_student_names_from_attendance(attendance: pd.DataFrame) -> list[str]:
    if attendance.empty:
        return DEFAULT_STUDENT_NAMES.copy()

    names = (
        attendance["student_name"]
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .drop_duplicates()
        .tolist()
    )

    merged = list(dict.fromkeys(DEFAULT_STUDENT_NAMES + names))
    return merged


def get_day_meal_counts(attendance: pd.DataFrame, date_key: str) -> dict[str, dict[str, int]]:
    counts = {
        meal["meal"]: {"marked": 0, "attending": 0}
        for meal in MEAL_WINDOWS
    }

    if attendance.empty:
        return counts

    df = attendance.copy()
    df["date_key"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.strftime("%Y-%m-%d")
    day_df = df[df["date_key"] == date_key].copy()

    if day_df.empty:
        return counts

    day_df["meal_slot_norm"] = day_df["meal_slot"].astype(str).str.strip().str.lower()
    eat_mask = day_df["status"].astype(str).str.strip().str.lower().isin(EAT_STATUSES)

    for meal in MEAL_WINDOWS:
        meal_key = str(meal["meal"])
        slot_mask = day_df["meal_slot_norm"] == meal_key.lower()
        counts[meal_key] = {
            "marked": int(slot_mask.sum()),
            "attending": int((slot_mask & eat_mask).sum()),
        }

    return counts


def get_student_week_history(attendance: pd.DataFrame, student_name: str, days: int = 7) -> list[dict]:
    target = normalize_name(student_name)
    now = datetime.now()

    date_keys: list[str] = []
    for idx in range(days - 1, -1, -1):
        date_keys.append((now - timedelta(days=idx)).strftime("%Y-%m-%d"))

    empty_template = {"B": 0, "L": 0, "T": 0, "D": 0}
    label_map = {
        "Breakfast": "B",
        "Lunch": "L",
        "Tea": "T",
        "Dinner": "D",
    }

    if attendance.empty or not target:
        return [
            {
                "day": datetime.strptime(key, "%Y-%m-%d").strftime("%a"),
                "date": key,
                "slots": empty_template.copy(),
            }
            for key in date_keys
        ]

    df = attendance.copy()
    df["name_norm"] = df["student_name"].astype(str).str.strip().str.lower()
    df["date_key"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["status_eat"] = df["status"].astype(str).str.strip().str.lower().isin(EAT_STATUSES)

    student_df = df[df["name_norm"] == target].copy()
    student_df["meal_slot"] = student_df["meal_slot"].astype(str).str.strip()

    history: list[dict] = []
    for key in date_keys:
        slots = empty_template.copy()
        day_df = student_df[student_df["date_key"] == key]
        for meal_name, short_code in label_map.items():
            meal_df = day_df[day_df["meal_slot"].str.lower() == meal_name.lower()]
            if meal_df.empty:
                slots[short_code] = 0
            else:
                slots[short_code] = 1 if bool(meal_df.iloc[-1]["status_eat"]) else 0

        history.append(
            {
                "day": datetime.strptime(key, "%Y-%m-%d").strftime("%a"),
                "date": key,
                "slots": slots,
            }
        )

    return history


def status_means_eat(status: str) -> bool:
    return str(status).strip().lower() in EAT_STATUSES


def summarize_attendance_for_date(attendance: pd.DataFrame, date_key: str) -> dict:
    if attendance.empty:
        return {
            "date": date_key,
            "total_entries": 0,
            "will_eat_count": 0,
            "skip_count": 0,
            "unique_students": 0,
            "pending_entries": 0,
            "meal_breakdown": [],
        }

    df = attendance.copy()
    df["date_key"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.strftime("%Y-%m-%d")
    day_df = df[df["date_key"] == date_key].copy()

    if day_df.empty:
        return {
            "date": date_key,
            "total_entries": 0,
            "will_eat_count": 0,
            "skip_count": 0,
            "unique_students": 0,
            "pending_entries": 0,
            "meal_breakdown": [],
        }

    eat_mask = day_df["status"].astype(str).str.strip().str.lower().isin(EAT_STATUSES)
    normalized_names = day_df.loc[eat_mask, "student_name"].astype(str).str.strip().str.lower()
    unique_students = int(normalized_names.replace("", pd.NA).dropna().nunique())

    meal_order = ["Breakfast", "Lunch", "Tea", "Dinner"]
    meal_breakdown: list[dict] = []

    day_meal = day_df["meal_slot"].astype(str).str.strip()
    for meal in meal_order:
        slot_mask = day_meal.str.lower() == meal.lower()
        slot_total = int(slot_mask.sum())
        slot_will_eat = int((slot_mask & eat_mask).sum())
        meal_breakdown.append(
            {
                "meal": meal,
                "total_entries": slot_total,
                "will_eat": slot_will_eat,
                "skip": max(slot_total - slot_will_eat, 0),
            }
        )

    return {
        "date": date_key,
        "total_entries": int(len(day_df)),
        "will_eat_count": int(eat_mask.sum()),
        "skip_count": int(len(day_df) - int(eat_mask.sum())),
        "unique_students": unique_students,
        "pending_entries": int((day_df["finalized"] == 0).sum()),
        "meal_breakdown": meal_breakdown,
    }


def estimate_day_row_from_attendance(students_present: int, dataset: pd.DataFrame) -> dict:
    valid = dataset[dataset["students_present"] > 0].copy()

    default_consumed_per_student = 0.52
    default_waste_per_student = 0.06

    if valid.empty:
        consumed_per_student = default_consumed_per_student
        waste_per_student = default_waste_per_student
        prepared_per_student = consumed_per_student + waste_per_student
    else:
        safe_students = valid["students_present"].replace(0, pd.NA)

        consumed_series = (valid["consumed_kg"] / safe_students).dropna()
        waste_series = (valid["waste_kg"] / safe_students).dropna()
        prepared_series = (valid["prepared_kg"] / safe_students).dropna()

        consumed_per_student = (
            float(consumed_series.mean())
            if not consumed_series.empty
            else default_consumed_per_student
        )
        waste_per_student = (
            float(waste_series.mean()) if not waste_series.empty else default_waste_per_student
        )
        prepared_per_student = (
            float(prepared_series.mean())
            if not prepared_series.empty
            else consumed_per_student + waste_per_student
        )

    prepared_kg = round(max(students_present * prepared_per_student, 0.0), 2)
    consumed_kg = round(max(students_present * consumed_per_student, 0.0), 2)
    waste_kg = round(max(students_present * waste_per_student, 0.0), 2)

    if prepared_kg < consumed_kg + waste_kg:
        prepared_kg = round(consumed_kg + waste_kg, 2)

    return {
        "students_present": students_present,
        "prepared_kg": prepared_kg,
        "consumed_kg": consumed_kg,
        "waste_kg": waste_kg,
    }


def call_chatbot_llm(message: str, dataset: pd.DataFrame, trained: dict) -> dict[str, str | None]:
    if not CHATBOT_API_KEY:
        return {"text": None, "error": "missing_api_key"}

    latest_students = int(dataset["students_present"].iloc[-1]) if not dataset.empty else 0
    latest_waste = float(dataset["waste_kg"].iloc[-1]) if not dataset.empty else 0.0
    latest_food = float(dataset["prepared_kg"].iloc[-1]) if not dataset.empty else 0.0
    model_r2 = float(trained.get("r2", 0.0))

    system_prompt = (
        "You are an assistant for a hostel mess admin dashboard. "
        "Answer clearly in short practical text. "
        "Use kilograms for quantities and avoid hallucinating unknown facts."
    )
    context_prompt = (
        f"Current known context: latest_students={latest_students}, "
        f"latest_waste_kg={latest_waste}, latest_prepared_food_kg={latest_food}, model_r2={model_r2}."
    )

    payload = {
        "systemInstruction": {
            "parts": [{"text": f"{system_prompt}\n{context_prompt}"}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": message}],
            }
        ],
        "generationConfig": {
            "temperature": 0.25,
            "maxOutputTokens": 220,
        },
    }

    # Try configured model first, then fallbacks that are commonly available.
    model_candidates = [CHATBOT_MODEL] + [
        model for model in CHATBOT_MODEL_FALLBACKS if model != CHATBOT_MODEL
    ]
    last_error = "request_failed"

    for model_name in model_candidates:
        endpoint_base = CHATBOT_API_URL.format(model=model_name)
        separator = "&" if "?" in endpoint_base else "?"
        endpoint = f"{endpoint_base}{separator}key={CHATBOT_API_KEY}"

        req = url_request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with url_request.urlopen(req, timeout=20) as response:
                raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                candidates = parsed.get("candidates") or []
                if not candidates:
                    last_error = "empty_ai_response"
                    continue

                first = candidates[0] if isinstance(candidates[0], dict) else {}
                content = first.get("content") or {}
                parts = content.get("parts") or []

                text_chunks = [
                    str(part.get("text", "")).strip()
                    for part in parts
                    if isinstance(part, dict) and str(part.get("text", "")).strip()
                ]
                content_text = "\n".join(text_chunks).strip()
                if content_text:
                    return {"text": content_text, "error": None}

                last_error = "empty_ai_response"
                continue
        except url_error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                body = ""

            if body and "API key not valid" in body:
                return {"text": None, "error": "invalid_api_key"}
            if body and "quota" in body.lower():
                return {"text": None, "error": "quota_exceeded"}

            # If model is not found, try next candidate.
            if exc.code == 404:
                last_error = "model_not_found"
                continue

            return {"text": None, "error": f"http_{exc.code}"}
        except (url_error.URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError):
            last_error = "request_failed"
            continue

    return {"text": None, "error": last_error}


def chatbot_fallback_reason(error_code: str | None) -> str | None:
    if not error_code:
        return None

    error_messages = {
        "missing_api_key": "API key missing in .env.",
        "invalid_api_key": "API key is invalid.",
        "quota_exceeded": "API quota exceeded.",
        "model_not_found": "Configured AI model is unavailable.",
        "empty_ai_response": "AI returned an empty response.",
        "request_failed": "AI request failed due to network/service issue.",
    }
    return error_messages.get(error_code, "AI request failed.")


def chatbot_fallback_message(message: str, students_present: int, predicted_waste: float, food_kg: float, is_weekend: int, is_exam_period: int) -> str:
    if message in {"hi", "hello", "hey", "hii", "yo"}:
        return (
            "Hello Admin! Ask me things like 'How much waste for 120 students?' "
            "or 'How much food should be prepared for 120 students on weekend?'"
        )

    if len(message) < 4:
        return "Please add a complete query with student count, e.g. 'waste for 120 students'."

    if "food" in message or "prepare" in message:
        return (
            f"For about {students_present} students, prepare around {food_kg} kg food "
            f"to reduce shortage while controlling waste"
            f"{' during weekend' if is_weekend else ''}"
            f"{' in exam period' if is_exam_period else ''}."
        )

    return (
        f"Estimated waste for {students_present} students is {predicted_waste} kg "
        f"based on your current dataset"
        f"{' for weekend' if is_weekend else ''}"
        f"{' in exam period' if is_exam_period else ''}."
    )


def save_dataset(df: pd.DataFrame) -> None:
    df.to_csv(DATASET_PATH, index=False)


def parse_bool(value: str | int | bool) -> int:
    return 1 if str(value).strip().lower() in {"1", "true", "yes", "y", "on"} else 0


def admin_required() -> bool:
    return bool(session.get("is_admin"))


def admin_role() -> str:
    role = str(session.get("admin_role", "staff")).strip().lower()
    return role if role in ROLE_ORDER else "staff"


def admin_has_role(min_role: str) -> bool:
    required = ROLE_ORDER.get(min_role, 999)
    current = ROLE_ORDER.get(admin_role(), 0)
    return current >= required


def student_session_name() -> str:
    return str(session.get("student_name", "")).strip()


def student_required() -> bool:
    return bool(student_session_name())


def compute_feedback_summary(feedback: pd.DataFrame, date_key: str) -> dict:
    if feedback.empty:
        return {
            "date": date_key,
            "total_feedback": 0,
            "avg_rating": 0,
            "meal_breakdown": [],
        }

    day_rows = feedback[feedback["date"] == date_key].copy()
    if day_rows.empty:
        return {
            "date": date_key,
            "total_feedback": 0,
            "avg_rating": 0,
            "meal_breakdown": [],
        }

    day_rows["rating"] = pd.to_numeric(day_rows["rating"], errors="coerce").fillna(0)
    meal_breakdown: list[dict] = []
    for meal in [m["meal"] for m in MEAL_WINDOWS]:
        meal_rows = day_rows[day_rows["meal_slot"].astype(str).str.strip().str.lower() == meal.lower()]
        if meal_rows.empty:
            meal_breakdown.append({"meal": meal, "count": 0, "avg_rating": 0})
            continue

        meal_breakdown.append(
            {
                "meal": meal,
                "count": int(len(meal_rows)),
                "avg_rating": round(float(meal_rows["rating"].mean()), 2),
            }
        )

    return {
        "date": date_key,
        "total_feedback": int(len(day_rows)),
        "avg_rating": round(float(day_rows["rating"].mean()), 2),
        "meal_breakdown": meal_breakdown,
    }


@app.route("/")
def index() -> str:
    return redirect(url_for("student_dashboard"))


@app.route("/student")
def student_dashboard() -> str:
    return render_template("index.html")


@app.route("/student/login")
def student_login_page() -> str:
    return render_template("index.html")


@app.route("/student/logout")
def student_logout() -> str:
    session.pop("student_name", None)
    return redirect(url_for("student_dashboard"))


@app.route("/api/student/session", methods=["GET"])
def api_student_session():
    student_name = student_session_name()
    return jsonify(
        {
            "logged_in": bool(student_name),
            "student_name": student_name,
        }
    )


@app.route("/api/student/login", methods=["POST"])
def api_student_login():
    data = request.get_json(silent=True) or request.form
    student_name = str(data.get("student_name", "")).strip()
    pin = str(data.get("pin", "")).strip()

    if not student_name:
        return jsonify({"success": False, "message": "Student name is required."}), 400
    if not pin:
        return jsonify({"success": False, "message": "PIN is required."}), 400

    credentials = load_student_credentials()
    user = credentials.get(normalize_name(student_name))
    if not user:
        return jsonify({"success": False, "message": "Student not found in records."}), 400
    if str(user.get("pin", "")).strip() != pin:
        return jsonify({"success": False, "message": "Invalid PIN."}), 401

    canonical_name = str(user.get("student_name", student_name)).strip() or student_name
    session["student_name"] = canonical_name
    return jsonify({"success": True, "student_name": canonical_name})


@app.route("/api/student/pin/change", methods=["POST"])
def api_student_change_pin():
    if not student_required():
        return jsonify({"success": False, "message": "Please login first."}), 401

    data = request.get_json(silent=True) or request.form
    old_pin = str(data.get("old_pin", "")).strip()
    new_pin = str(data.get("new_pin", "")).strip()

    if not old_pin or not new_pin:
        return jsonify({"success": False, "message": "Both old and new PIN are required."}), 400
    if len(new_pin) < 4:
        return jsonify({"success": False, "message": "New PIN must be at least 4 characters."}), 400

    current_name = student_session_name()
    credentials = load_student_credentials()
    key = normalize_name(current_name)
    user = credentials.get(key)
    if not user:
        return jsonify({"success": False, "message": "Student account not found."}), 404
    if str(user.get("pin", "")).strip() != old_pin:
        return jsonify({"success": False, "message": "Old PIN is incorrect."}), 401

    user["pin"] = new_pin
    credentials[key] = user
    save_student_credentials(credentials)

    return jsonify({"success": True, "message": "PIN updated successfully."})


@app.route("/api/student/logout", methods=["POST"])
def api_student_logout():
    session.pop("student_name", None)
    return jsonify({"success": True})


@app.route("/api/student/bootstrap", methods=["GET"])
def api_student_bootstrap():
    now = datetime.now()
    date_key = now.strftime("%Y-%m-%d")
    attendance = load_attendance()
    menu_data = load_menu()

    active_meal, next_meal = current_and_next_meal(now)
    seconds_remaining = seconds_until_meal_event(now, active_meal, next_meal)
    meal_counts = get_day_meal_counts(attendance, date_key)

    menu_meal = active_meal if active_meal else next_meal
    menu_items = str(menu_data["menus"].get(str(menu_meal["meal"]), "Menu not available yet."))

    logged_student = student_session_name()
    requested_student = request.args.get("student_name", "").strip()
    selected_student = logged_student or requested_student
    if not selected_student:
        all_students = get_student_names_from_attendance(attendance)
        selected_student = all_students[0] if all_students else ""

    selected_student = selected_student.strip()

    selected_history = get_student_week_history(attendance, selected_student, days=7)
    monthly_rows = attendance.copy()
    monthly_rows["dt"] = pd.to_datetime(monthly_rows["timestamp"], errors="coerce")
    monthly_rows = monthly_rows[
        (monthly_rows["dt"].dt.month == now.month) & (monthly_rows["dt"].dt.year == now.year)
    ]
    monthly_rows["name_norm"] = monthly_rows["student_name"].astype(str).str.strip().str.lower()

    student_monthly_count = int(
        (
            monthly_rows["name_norm"] == selected_student.strip().lower()
        ).sum()
    )
    impact_saved_kg = round(1.2 + student_monthly_count * 0.12, 1)

    current_meal_marked = False
    current_meal_status = None
    can_mark_current_meal = bool(logged_student and active_meal)
    if logged_student and active_meal:
        today_mask = (
            pd.to_datetime(attendance["timestamp"], errors="coerce").dt.strftime("%Y-%m-%d")
            == date_key
        )
        student_mask = attendance["student_name"].astype(str).str.strip().str.lower() == logged_student.lower()
        meal_mask = attendance["meal_slot"].astype(str).str.strip().str.lower() == str(active_meal["meal"]).lower()
        current_rows = attendance[today_mask & student_mask & meal_mask].copy()
        if not current_rows.empty:
            current_meal_marked = True
            current_meal_status = str(current_rows.iloc[-1]["status"])
            can_mark_current_meal = False

    return jsonify(
        {
            "server_time": now.isoformat(),
            "today_label": now.strftime("%A, %d %B %Y"),
            "meal_slots": MEAL_WINDOWS,
            "active_meal": active_meal["meal"] if active_meal else None,
            "next_meal": next_meal["meal"],
            "seconds_remaining": seconds_remaining,
            "is_open": bool(active_meal),
            "meal_counts": meal_counts,
            "students": get_student_names_from_attendance(attendance),
            "selected_student": selected_student,
            "logged_student": logged_student,
            "is_logged_in": bool(logged_student),
            "selected_student_history": selected_history,
            "menu_banner": {
                "meal": menu_meal["meal"],
                "items": menu_items,
                "updated_at": menu_data["updated_at"],
            },
            "impact_saved_kg": impact_saved_kg,
            "current_meal_marked": current_meal_marked,
            "current_meal_status": current_meal_status,
            "can_mark_current_meal": can_mark_current_meal,
        }
    )


@app.route("/api/student/history", methods=["GET"])
def api_student_history():
    student_name = request.args.get("student_name", "").strip()
    if not student_name:
        return jsonify({"success": False, "message": "student_name is required."}), 400

    attendance = load_attendance()
    history = get_student_week_history(attendance, student_name, days=7)

    return jsonify({"success": True, "student_name": student_name, "history": history})


@app.route("/mark_attendance", methods=["POST"])
def mark_attendance():
    data = request.get_json(silent=True) or request.form
    student_name = student_session_name() or str(data.get("student_name", "")).strip()
    if not student_name:
        return jsonify({"success": False, "message": "Please login as a student first."}), 401

    current_meal, next_meal = current_and_next_meal(datetime.now())
    meal_slot = str(data.get("meal_slot", current_meal["meal"] if current_meal else next_meal["meal"])).strip() or "General"
    status = str(data.get("status", "Eat")).strip()

    now = datetime.now()
    today_key = now.strftime("%Y-%m-%d")

    attendance_df = load_attendance()
    attendance_df["date_key"] = pd.to_datetime(attendance_df["timestamp"], errors="coerce").dt.strftime("%Y-%m-%d")
    duplicate_mask = (
        (attendance_df["date_key"] == today_key)
        & (attendance_df["student_name"].astype(str).str.strip().str.lower() == student_name.lower())
        & (attendance_df["meal_slot"].astype(str).str.strip().str.lower() == meal_slot.lower())
    )

    if bool(duplicate_mask.any()):
        return jsonify(
            {
                "success": False,
                "message": f"You have already marked your preference for {meal_slot} today.",
            }
        ), 409

    attendance_df.loc[len(attendance_df)] = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "student_name": student_name,
        "meal_slot": meal_slot,
        "status": status,
        "finalized": 0,
    }
    attendance_df.to_csv(ATTENDANCE_PATH, index=False)

    return jsonify({"success": True, "message": "Attendance saved successfully."})


@app.route("/predict_basic", methods=["POST"])
def predict_basic():
    data = request.get_json(silent=True) or request.form
    students_present = int(float(data.get("students_present", 0)))
    is_weekend = parse_bool(data.get("is_weekend", 0))
    is_exam_period = parse_bool(data.get("is_exam_period", 0))

    df = load_dataset(DATASET_PATH)
    train_result = train_waste_model(df)
    model = train_result["model"]

    waste_value = predict_waste(model, students_present, is_weekend, is_exam_period)
    food_value = suggested_food_quantity(df, students_present)

    return jsonify(
        {
            "predicted_waste": waste_value,
            "suggested_food": food_value,
            "model_accuracy": train_result["r2"],
        }
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("login.html", error=None)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    users = load_admin_users()
    user = users.get(username.lower())

    if user and password == user["password"]:
        session["is_admin"] = True
        session["admin_username"] = user["username"]
        session["admin_role"] = user["role"]
        return redirect(url_for("admin_dashboard"))

    return render_template("login.html", error="Invalid username or password")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin_dashboard():
    if not admin_required():
        return redirect(url_for("admin_login"))
    return render_template("admin.html")


@app.route("/api/admin/session", methods=["GET"])
def api_admin_session():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify(
        {
            "is_admin": True,
            "username": str(session.get("admin_username", "admin")),
            "role": admin_role(),
        }
    )


@app.route("/api/summary")
def api_summary():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401

    df = load_dataset(DATASET_PATH)
    train_result = train_waste_model(df)
    model = train_result["model"]

    latest_students = int(df["students_present"].iloc[-1]) if not df.empty else 0

    today = datetime.now().strftime("%Y-%m-%d")
    attendance_summary = summarize_attendance_for_date(load_attendance(), today)
    live_students = attendance_summary["unique_students"]

    total_students = live_students if live_students > 0 else latest_students
    predicted_waste_kg = predict_waste(model, total_students, 0, 0)
    food_kg = suggested_food_quantity(df, total_students)

    return jsonify(
        {
            "total_students": total_students,
            "predicted_waste": predicted_waste_kg,
            "suggested_food": food_kg,
            "r2": train_result["r2"],
            "pending_attendance": attendance_summary["pending_entries"],
            "admin_role": admin_role(),
        }
    )


@app.route("/api/attendance/live", methods=["GET"])
def api_attendance_live():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401

    date_key = request.args.get("date", "").strip() or datetime.now().strftime("%Y-%m-%d")
    attendance = load_attendance()
    summary = summarize_attendance_for_date(attendance, date_key)

    df = attendance.copy()
    df["date_key"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.strftime("%Y-%m-%d")
    day_rows = df[df["date_key"] == date_key].copy()
    day_rows = day_rows.sort_values(by="timestamp", ascending=False).head(20)

    records = day_rows[
        ["timestamp", "student_name", "meal_slot", "status", "finalized"]
    ].to_dict(orient="records")

    return jsonify(
        {
            "date": date_key,
            "summary": summary,
            "records": records,
            "student_names": get_student_names_from_attendance(attendance),
        }
    )


@app.route("/api/admin/attendance/reset", methods=["POST"])
def api_admin_attendance_reset():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401
    if not admin_has_role("manager"):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or request.form
    date_key = str(data.get("date", "")).strip() or datetime.now().strftime("%Y-%m-%d")
    student_name = str(data.get("student_name", "")).strip()
    meal_slot = str(data.get("meal_slot", "")).strip() or "All"

    if not student_name:
        return jsonify({"success": False, "message": "student_name is required."}), 400

    valid_meals = {meal["meal"] for meal in MEAL_WINDOWS}
    if meal_slot != "All" and meal_slot not in valid_meals:
        return jsonify({"success": False, "message": "Invalid meal slot."}), 400

    attendance = load_attendance()
    if attendance.empty:
        return jsonify({"success": False, "message": "No attendance data available."}), 400

    attendance["date_key"] = pd.to_datetime(attendance["timestamp"], errors="coerce").dt.strftime("%Y-%m-%d")
    student_mask = attendance["student_name"].astype(str).map(normalize_name) == normalize_name(student_name)
    date_mask = attendance["date_key"] == date_key
    base_mask = student_mask & date_mask

    if meal_slot == "All":
        target_mask = base_mask
    else:
        meal_mask = attendance["meal_slot"].astype(str).str.strip().str.lower() == meal_slot.lower()
        target_mask = base_mask & meal_mask

    removed_count = int(target_mask.sum())
    if removed_count == 0:
        return jsonify({"success": False, "message": "No matching attendance records found."}), 404

    updated = attendance.loc[~target_mask, ATTENDANCE_COLUMNS].copy()
    updated.to_csv(ATTENDANCE_PATH, index=False)

    return jsonify(
        {
            "success": True,
            "message": f"Removed {removed_count} attendance record(s).",
            "removed_count": removed_count,
        }
    )


@app.route("/api/attendance/finalize-day", methods=["POST"])
def api_finalize_day():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401
    if not admin_has_role("manager"):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or request.form
    date_key = str(data.get("date", "")).strip() or datetime.now().strftime("%Y-%m-%d")
    is_exam_period = parse_bool(data.get("is_exam_period", 0))

    attendance = load_attendance()
    if attendance.empty:
        return jsonify({"success": False, "message": "No attendance data available."}), 400

    attendance["date_key"] = pd.to_datetime(attendance["timestamp"], errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    pending_mask = (attendance["date_key"] == date_key) & (attendance["finalized"] == 0)
    pending_rows = attendance[pending_mask].copy()

    if pending_rows.empty:
        return jsonify(
            {
                "success": False,
                "message": f"No pending attendance entries found for {date_key}.",
            }
        ), 400

    eat_mask = pending_rows["status"].astype(str).str.strip().str.lower().isin(EAT_STATUSES)
    normalized_names = pending_rows.loc[eat_mask, "student_name"].astype(str).str.strip().str.lower()
    unique_students = int(normalized_names.replace("", pd.NA).dropna().nunique())
    students_present = unique_students if unique_students > 0 else int(eat_mask.sum())

    if students_present <= 0:
        return jsonify(
            {
                "success": False,
                "message": "No 'Will Eat' attendance entries available to build daily dataset.",
            }
        ), 400

    dataset = load_dataset(DATASET_PATH)
    estimated = estimate_day_row_from_attendance(students_present, dataset)

    try:
        day_dt = datetime.strptime(date_key, "%Y-%m-%d")
        is_weekend = 1 if day_dt.weekday() >= 5 else 0
    except ValueError:
        is_weekend = 0

    row = {
        **estimated,
        "is_weekend": is_weekend,
        "is_exam_period": is_exam_period,
    }

    dataset.loc[len(dataset)] = row
    save_dataset(dataset)

    attendance.loc[pending_mask, "finalized"] = 1
    attendance[ATTENDANCE_COLUMNS].to_csv(ATTENDANCE_PATH, index=False)

    trained = train_waste_model(dataset)

    return jsonify(
        {
            "success": True,
            "message": f"Day {date_key} finalized. Dataset updated and model refreshed.",
            "students_present": students_present,
            "added_row": row,
            "r2": trained["r2"],
            "processed_entries": int(len(pending_rows)),
        }
    )


@app.route("/api/data", methods=["GET"])
def api_get_data():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401

    df = load_dataset(DATASET_PATH)
    records = df.to_dict(orient="records")

    for idx, row in enumerate(records):
        row["id"] = idx

    return jsonify(records)


@app.route("/api/data/add", methods=["POST"])
def api_add_data():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401
    if not admin_has_role("manager"):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or request.form
    df = load_dataset(DATASET_PATH)

    row = {
        "students_present": int(float(data.get("students_present", 0))),
        "prepared_kg": float(data.get("prepared_kg", 0)),
        "consumed_kg": float(data.get("consumed_kg", 0)),
        "waste_kg": float(data.get("waste_kg", 0)),
        "is_weekend": parse_bool(data.get("is_weekend", 0)),
        "is_exam_period": parse_bool(data.get("is_exam_period", 0)),
    }

    df.loc[len(df)] = row
    save_dataset(df)

    return jsonify({"success": True, "message": "Data added successfully."})


@app.route("/api/data/<int:row_id>/delete", methods=["POST"])
def api_delete_data(row_id: int):
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401
    if not admin_has_role("manager"):
        return jsonify({"error": "Forbidden"}), 403

    df = load_dataset(DATASET_PATH)
    if row_id < 0 or row_id >= len(df):
        return jsonify({"success": False, "message": "Invalid row ID."}), 400

    df = df.drop(index=row_id).reset_index(drop=True)
    save_dataset(df)

    return jsonify({"success": True, "message": "Row deleted."})


@app.route("/api/predict", methods=["POST"])
def api_predict():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or request.form
    students_present = int(float(data.get("students_present", 0)))
    is_weekend = parse_bool(data.get("is_weekend", 0))
    is_exam_period = parse_bool(data.get("is_exam_period", 0))

    df = load_dataset(DATASET_PATH)
    trained = train_waste_model(df)

    predicted_waste = predict_waste(
        trained["model"], students_present, is_weekend, is_exam_period
    )
    suggested_food = suggested_food_quantity(df, students_present)

    return jsonify(
        {
            "predicted_waste": predicted_waste,
            "suggested_food": suggested_food,
            "r2": trained["r2"],
        }
    )


@app.route("/api/predict/meal", methods=["POST"])
def api_predict_by_meal():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or request.form
    students_present = int(float(data.get("students_present", 0)))
    is_weekend = parse_bool(data.get("is_weekend", 0))
    is_exam_period = parse_bool(data.get("is_exam_period", 0))
    meal_slot = str(data.get("meal_slot", "Lunch")).strip() or "Lunch"

    if meal_slot not in MEAL_FORECAST_MULTIPLIERS:
        return jsonify({"success": False, "message": "Invalid meal slot."}), 400

    df = load_dataset(DATASET_PATH)
    trained = train_waste_model(df)
    base_waste = float(
        predict_waste(trained["model"], students_present, is_weekend, is_exam_period)
    )
    base_food = float(suggested_food_quantity(df, students_present))
    multiplier = float(MEAL_FORECAST_MULTIPLIERS[meal_slot])

    return jsonify(
        {
            "meal_slot": meal_slot,
            "predicted_waste": round(base_waste * multiplier, 2),
            "suggested_food": round(base_food * multiplier, 2),
            "multiplier": multiplier,
            "r2": trained["r2"],
        }
    )


@app.route("/api/chatbot", methods=["POST"])
def api_chatbot():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip().lower()

    if not message:
        return jsonify(
            {
                "reply": "Please type a question. Example: 'How much waste for 120 students on weekend?'",
                "predicted_waste": 0,
                "suggested_food": 0,
                "r2": 0,
            }
        )

    df = load_dataset(DATASET_PATH)
    trained = train_waste_model(df)

    # Extract a student count from natural language, fallback to latest known value.
    match = re.search(r"(\d+)", message)
    students_present = (
        int(match.group(1))
        if match
        else int(df["students_present"].iloc[-1])
        if not df.empty
        else 100
    )

    is_weekend = 1 if re.search(r"weekend|saturday|sunday", message) else 0
    is_exam_period = 1 if re.search(r"exam|test|mid|final", message) else 0

    predicted_waste = predict_waste(
        trained["model"], students_present, is_weekend, is_exam_period
    )
    food_kg = suggested_food_quantity(df, students_present)

    ai_result = call_chatbot_llm(message, df, trained)
    ai_text = ai_result.get("text")
    ai_error = ai_result.get("error")
    source = "ai" if ai_text else "rule"

    if ai_text:
        text = ai_text
    else:
        text = chatbot_fallback_message(
            message,
            students_present,
            predicted_waste,
            food_kg,
            is_weekend,
            is_exam_period,
        )

    return jsonify(
        {
            "reply": text,
            "predicted_waste": predicted_waste,
            "suggested_food": food_kg,
            "r2": trained["r2"],
            "source": source,
            "fallback_reason": chatbot_fallback_reason(ai_error)
            if ai_error and source == "rule"
            else None,
        }
    )


@app.route("/api/student/menu-feedback", methods=["POST"])
def api_student_menu_feedback():
    if not student_required():
        return jsonify({"success": False, "message": "Please login first."}), 401

    data = request.get_json(silent=True) or request.form
    now = datetime.now()
    date_key = now.strftime("%Y-%m-%d")
    student_name = student_session_name()
    meal_slot = str(data.get("meal_slot", "")).strip()
    if not meal_slot:
        active, next_meal = current_and_next_meal(now)
        meal_slot = active["meal"] if active else next_meal["meal"]

    rating_raw = data.get("rating", 0)
    comment = str(data.get("comment", "")).strip()

    try:
        rating = int(float(rating_raw))
    except (TypeError, ValueError):
        rating = 0

    if meal_slot not in {m["meal"] for m in MEAL_WINDOWS}:
        return jsonify({"success": False, "message": "Invalid meal slot."}), 400
    if rating < 1 or rating > 5:
        return jsonify({"success": False, "message": "Rating must be between 1 and 5."}), 400

    feedback = load_feedback()
    existing_mask = (
        (feedback["date"].astype(str).str.strip() == date_key)
        & (feedback["student_name"].astype(str).map(normalize_name) == normalize_name(student_name))
        & (feedback["meal_slot"].astype(str).str.strip().str.lower() == meal_slot.lower())
    )

    feedback = feedback.loc[~existing_mask, FEEDBACK_COLUMNS].copy()
    feedback.loc[len(feedback)] = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": date_key,
        "student_name": student_name,
        "meal_slot": meal_slot,
        "rating": rating,
        "comment": comment,
    }
    save_feedback(feedback)

    return jsonify({"success": True, "message": "Feedback submitted. Thank you!"})


@app.route("/api/admin/menu-feedback", methods=["GET"])
def api_admin_menu_feedback():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401

    date_key = request.args.get("date", "").strip() or datetime.now().strftime("%Y-%m-%d")
    feedback = load_feedback()
    return jsonify(compute_feedback_summary(feedback, date_key))

@app.route("/api/admin/menu", methods=["GET", "POST"])
def api_admin_menu():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401
    if request.method == "POST" and not admin_has_role("manager"):
        return jsonify({"error": "Forbidden"}), 403

    if request.method == "GET":
        return jsonify(load_menu())

    data = request.get_json(silent=True) or request.form
    meal = str(data.get("meal", "")).strip()
    items = str(data.get("items", "")).strip()

    if meal not in {m["meal"] for m in MEAL_WINDOWS}:
        return jsonify({"success": False, "message": "Invalid meal slot."}), 400
    if not items:
        return jsonify({"success": False, "message": "Menu items cannot be empty."}), 400

    menu_data = load_menu()
    menu_data["menus"][meal] = items
    menu_data["updated_at"] = datetime.now().strftime("%I:%M %p")
    save_menu(menu_data)

    return jsonify(
        {
            "success": True,
            "message": f"{meal} menu updated.",
            "menu": menu_data,
        }
    )


@app.route("/api/admin/student/pin/reset", methods=["POST"])
def api_admin_student_pin_reset():
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401
    if not admin_has_role("manager"):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or request.form
    student_name = str(data.get("student_name", "")).strip()
    new_pin = str(data.get("new_pin", DEFAULT_STUDENT_PIN)).strip() or DEFAULT_STUDENT_PIN

    if not student_name:
        return jsonify({"success": False, "message": "student_name is required."}), 400
    if len(new_pin) < 4:
        return jsonify({"success": False, "message": "PIN must be at least 4 characters."}), 400

    credentials = load_student_credentials()
    key = normalize_name(student_name)

    existing = credentials.get(key, {"student_name": student_name, "pin": DEFAULT_STUDENT_PIN})
    existing["student_name"] = existing.get("student_name", student_name) or student_name
    existing["pin"] = new_pin
    credentials[key] = existing
    save_student_credentials(credentials)

    return jsonify({"success": True, "message": f"PIN reset for {existing['student_name']}"})


@app.route("/api/student/qr-code", methods=["GET"])
def api_student_qr_code():
    """Generate QR code for student login. QR contains student name and PIN in JSON format."""
    if not student_required():
        return jsonify({"success": False, "message": "Please login first."}), 401

    student_name = student_session_name()
    credentials = load_student_credentials()
    user = credentials.get(normalize_name(student_name))

    if not user:
        return jsonify({"success": False, "message": "Student not found."}), 404

    # Create QR code data with student name and PIN
    qr_data = json.dumps({
        "student_name": student_name,
        "pin": user.get("pin", DEFAULT_STUDENT_PIN)
    })

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to base64
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.getvalue()).decode()

    return jsonify({
        "success": True,
        "qr_code": f"data:image/png;base64,{img_base64}",
        "student_name": student_name,
        "message": "QR code generated successfully."
    })


@app.route("/api/admin/meal-reminders", methods=["GET"])
def api_admin_meal_reminders():
    """Get current meal reminder settings."""
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401
    if not admin_has_role("manager"):
        return jsonify({"error": "Forbidden"}), 403

    reminder_settings = {
        "enabled": os.getenv("MEAL_REMINDERS_ENABLED", "true").lower() == "true",
        "minutes_before": int(os.getenv("REMINDER_MINUTES_BEFORE", "15")),
        "meals": [
            {"meal": "Breakfast", "time": "08:00 AM", "enabled": True},
            {"meal": "Lunch", "time": "12:00 PM", "enabled": True},
            {"meal": "Tea", "time": "05:00 PM", "enabled": True},
            {"meal": "Dinner", "time": "08:00 PM", "enabled": True},
        ],
        "contact_methods": ["dashboard_notification"],
        "next_reminders": []
    }

    # Calculate next reminder times
    now = datetime.now()
    for meal in MEAL_WINDOWS:
        end_time = datetime.combine(now.date(), datetime.min.time()).replace(
            hour=meal["end"] // 60, minute=meal["end"] % 60
        )
        reminder_time = end_time - timedelta(minutes=reminder_settings["minutes_before"])

        if reminder_time > now:
            reminder_settings["next_reminders"].append({
                "meal": meal["meal"],
                "reminder_at": reminder_time.isoformat(),
                "cutoff_at": end_time.isoformat()
            })

    return jsonify(reminder_settings)


@app.route("/api/admin/meal-reminders/test", methods=["POST"])
def api_test_meal_reminder():
    """Send a test meal reminder notification."""
    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 401
    if not admin_has_role("manager"):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or request.form
    meal_name = str(data.get("meal", "Lunch")).strip() or "Lunch"

    if meal_name not in {m["meal"] for m in MEAL_WINDOWS}:
        return jsonify({"success": False, "message": "Invalid meal slot."}), 400

    # Log the test reminder
    now = datetime.now()
    test_log = {
        "timestamp": now.isoformat(),
        "type": "test",
        "meal": meal_name,
        "recipients": "all_students",
        "status": "sent"
    }

    return jsonify({
        "success": True,
        "message": f"Test reminder sent for {meal_name}",
        "reminder": test_log
    })


@app.route("/api/student/meal-reminders", methods=["GET"])
def api_student_meal_reminders():
    """Get upcoming meal reminders for the logged-in student."""
    if not student_required():
        return jsonify({"error": "Unauthorized"}), 401

    now = datetime.now()
    minutes_before = int(os.getenv("REMINDER_MINUTES_BEFORE", "15"))

    upcoming_reminders = []
    for meal in MEAL_WINDOWS:
        end_time = datetime.combine(now.date(), datetime.min.time()).replace(
            hour=meal["end"] // 60, minute=meal["end"] % 60
        )
        reminder_time = end_time - timedelta(minutes=minutes_before)

        if reminder_time > now and reminder_time.date() == now.date():
            upcoming_reminders.append({
                "meal": meal["meal"],
                "reminder_at": reminder_time.isoformat(),
                "cutoff_at": end_time.isoformat(),
                "minutes_until": int((reminder_time - now).total_seconds() / 60),
                "label": meal["label"]
            })

    return jsonify({
        "success": True,
        "student_name": student_session_name(),
        "upcoming_reminders": upcoming_reminders,
        "reminder_minutes_before": minutes_before
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
