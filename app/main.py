from dotenv import load_dotenv
import os
from google import genai
from math import isnan
from fastapi import Request, Form, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.security import hash_password
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from starlette.middleware.sessions import SessionMiddleware
from datetime import date, timedelta

from sklearn.linear_model import LinearRegression
import numpy as np
import markdown

from app.database import get_db, engine, Base
from app.models import ChatMessage, User, WeightRecord, ReminderSettings

load_dotenv()

gemini_api_key = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

def is_profile_complete(user):
    return all([
        user.age,
        user.gender,
        user.height,
        user.target_weight,
        user.activity_level
    ])
def suggest_default_reminder_times(wake_up_time=None, goal=None):
    breakfast = "07:00"
    lunch = "12:00"
    dinner = "18:30"
    workout = "17:30"

    if wake_up_time:
        try:
            hour, minute = map(int, wake_up_time.split(":"))
            breakfast_hour = min(hour + 1, 23)
            breakfast = f"{breakfast_hour:02d}:{minute:02d}"
        except:
            pass

    if goal == "Giảm cân":
        workout = "17:30"
    elif goal == "Tăng cân":
        workout = "18:00"
    elif goal == "Duy trì":
        workout = "18:30"

    return breakfast, lunch, dinner, workout

def calculate_weight_streak(records):
    if not records:
        return 0

    unique_dates = sorted({r.record_date for r in records}, reverse=True)

    today = date.today()

    if unique_dates[0] == today:
        streak = 1
        current_date = today
    elif unique_dates[0] == today - timedelta(days=1):
        streak = 1
        current_date = today - timedelta(days=1)
    else:
        return 0

    for d in unique_dates[1:]:
        if d == current_date - timedelta(days=1):
            streak += 1
            current_date = d
        elif d == current_date:
            continue
        else:
            break

    return streak
def calculate_bmi(height_cm, weight_kg):
    if not height_cm or not weight_kg:
        return None
    h = height_cm / 100
    if h <= 0:
        return None
    return round(weight_kg / (h * h), 2)


def calculate_bmr(user, weight_kg):
    if not user or not user.height or not user.age or not weight_kg or not user.gender:
        return None

    if user.gender == "Nam":
        bmr = 10 * weight_kg + 6.25 * user.height - 5 * user.age + 5
    else:
        bmr = 10 * weight_kg + 6.25 * user.height - 5 * user.age - 161

    return round(bmr, 2)


def calculate_tdee(bmr, activity_level):
    if not bmr or not activity_level:
        return None

    activity_map = {
        "Ít vận động": 1.2,
        "Vận động nhẹ": 1.375,
        "Vận động vừa": 1.55,
        "Vận động nhiều": 1.725,
    }

    factor = activity_map.get(activity_level, 1.2)
    return round(bmr * factor, 2)

app = FastAPI()

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev_secret_key")
)

Base.metadata.create_all(bind=engine)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


def get_latest_weight_record(db: Session, user_id: int):
    return (
        db.query(WeightRecord)
        .filter(WeightRecord.user_id == user_id)
        .order_by(WeightRecord.record_date.desc(), WeightRecord.id.desc())
        .first()
    )


@app.get("/")
def home(request: Request):
    user_id = request.session.get("user_id")
    return templates.TemplateResponse(
        request,
        "index.html",
        {"user_id": user_id}
    )

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        request,
        "register.html")


@app.post("/register")
def register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    full_name = full_name.strip()
    email = email.strip().lower()
    password = password.strip()

    if len(password) < 8:
        return templates.TemplateResponse(
            request,
             "register.html", {
           
            "message": "Mật khẩu phải có ít nhất 8 ký tự."
        })

    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        return templates.TemplateResponse(
            request,
             "register.html", {
           
            "message": "Email này đã được sử dụng."
        })

    new_user = User(
        full_name=full_name,
        email=email,
        password=hash_password(password)
    )

    db.add(new_user)
    db.commit()

    return templates.TemplateResponse(
        request,
         "login.html", {
        
        "message": "Đăng ký thành công! Hãy đăng nhập."
    })

from app.security import verify_password

@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "message": ""
        }
    )

@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        email = email.strip().lower()
        password = password.strip()

        user = db.query(User).filter(User.email == email).first()

        if not user:
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "message": "Email hoặc mật khẩu không đúng."
                }
            )

        valid_password = False

        try:
            valid_password = verify_password(password, user.password)
        except Exception:
            # Nếu mật khẩu cũ đang lưu plain text thì tự chuyển sang hash
            if user.password == password:
                valid_password = True
                user.password = hash_password(password)
                db.commit()

        if not valid_password:
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "message": "Email hoặc mật khẩu không đúng."
                }
            )

        request.session["user_id"] = user.id
        return RedirectResponse(url="/dashboard", status_code=302)

    except Exception as e:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "message": f"Lỗi đăng nhập: {str(e)}"
            }
        )

@app.get("/profile")
def profile_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()

    latest_record = get_latest_weight_record(db, user_id)
    allow_current_weight_input = latest_record is None

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            
            "user": user,
            "message": "",
            "allow_current_weight_input": allow_current_weight_input
        }
    )


@app.post("/profile")

def save_profile(
    request: Request,
    age: int = Form(...),
    gender: str = Form(...),
    height: float = Form(...),
    target_weight: float = Form(...),
    activity_level: str = Form(...),
    current_weight: float = Form(None),
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        return RedirectResponse(url="/login", status_code=302)

    latest_record = get_latest_weight_record(db, user_id)
    allow_current_weight_input = latest_record is None

    user.age = age
    user.gender = gender
    user.height = height
    user.target_weight = target_weight
    user.activity_level = activity_level

    db.commit()

    # Chỉ cho nhập cân nặng hiện tại đúng 1 lần đầu
    if allow_current_weight_input and current_weight is not None:
        new_record = WeightRecord(
            user_id=user_id,
            record_date=date.today(),
            weight=current_weight,
            note="Cân nặng khởi tạo từ hồ sơ"
        )
        db.add(new_record)
        db.commit()
    user.is_profile_completed = True
    db.commit()

    # load lại để render đúng trạng thái sau khi lưu
    latest_record = get_latest_weight_record(db, user_id)
    allow_current_weight_input = latest_record is None

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            
            "user": user,
            "message": "Lưu thông tin thành công!",
            "allow_current_weight_input": allow_current_weight_input
        }
    )


@app.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()

    show_profile_notice = False
    if user and not is_profile_complete(user):
        show_profile_notice = True

    latest_record = get_latest_weight_record(db, user_id)
    latest_weight = latest_record.weight if latest_record else None

    bmi = None
    body_type = None
    model_file = None

    if user and user.height and latest_weight:
        height_m = user.height / 100
        bmi = round(latest_weight / (height_m ** 2), 2)

        if bmi < 18.5:
            body_type = "Tạng người gầy"
            model_file = "/static/models/thin.glb"
        elif bmi < 25:
            body_type = "Tạng người cân đối"
            model_file = "/static/models/normal.glb"
        else:
            body_type = "Tạng người đầy đặn"
            model_file = "/static/models/overweight.glb"

    records = (
        db.query(WeightRecord)
        .filter(WeightRecord.user_id == user_id)
        .order_by(WeightRecord.record_date.asc(), WeightRecord.id.asc())
        .all()
    )

    labels = [r.record_date.strftime("%d/%m/%Y") for r in records]
    weights = [r.weight for r in records]

    predicted_7 = None
    predicted_30 = None
    target_date_prediction = None
    has_today_record = False

    if records:
        today = date.today()
        has_today_record = any(r.record_date == today for r in records)

    if len(records) >= 2:
        start_date = records[0].record_date
        X = np.array([(r.record_date - start_date).days for r in records]).reshape(-1, 1)
        y = np.array([r.weight for r in records])

        model = LinearRegression()
        model.fit(X, y)

        last_date = records[-1].record_date
        last_day_number = (last_date - start_date).days

        predict_day_7 = last_day_number + 7
        predict_day_30 = last_day_number + 30

        predicted_7 = float(model.predict([[predict_day_7]])[0])
        predicted_30 = float(model.predict([[predict_day_30]])[0])

        current_weight = records[-1].weight
        max_loss_7 = current_weight - 1
        max_loss_30 = current_weight - 4

        predicted_7 = round(max(predicted_7, max_loss_7), 2)
        predicted_30 = round(max(predicted_30, max_loss_30), 2)

        target_date_prediction = predict_target_date(records, user.target_weight if user else None)

    goal = "Chưa xác định"
    if user and user.target_weight and latest_weight:
        if user.target_weight < latest_weight:
            goal = "Giảm cân"
        elif user.target_weight > latest_weight:
            goal = "Tăng cân"
        else:
            goal = "Duy trì"

    streak = calculate_weight_streak(records)

    progress_percent = 0
    remaining_weight = None
    goal_direction = None

    if records and user and user.target_weight:
        first_weight = records[0].weight
        current_weight = latest_weight
        target_weight = user.target_weight

        if current_weight is not None:
            if target_weight < first_weight:
                goal_direction = "lose"
                total_needed = first_weight - target_weight
                total_done = first_weight - current_weight

                if total_needed > 0:
                    progress_percent = round((total_done / total_needed) * 100, 1)
                    progress_percent = max(0, min(100, progress_percent))

                remaining_weight = round(max(0, current_weight - target_weight), 1)

            elif target_weight > first_weight:
                goal_direction = "gain"
                total_needed = target_weight - first_weight
                total_done = current_weight - first_weight

                if total_needed > 0:
                    progress_percent = round((total_done / total_needed) * 100, 1)
                    progress_percent = max(0, min(100, progress_percent))

                remaining_weight = round(max(0, target_weight - current_weight), 1)

            else:
                goal_direction = "maintain"
                progress_percent = 100
                remaining_weight = 0

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "bmi": bmi,
            "goal": goal,
            "predicted_7": predicted_7,
            "predicted_30": predicted_30,
            "labels": labels,
            "weights": weights,
            "model_file": model_file,
            "body_type": body_type,
            "latest_weight": latest_weight,
            "streak": streak,
            "target_date_prediction": target_date_prediction,
            "has_today_record": has_today_record,
            "progress_percent": progress_percent,
            "remaining_weight": remaining_weight,
            "goal_direction": goal_direction,
            "show_profile_notice": show_profile_notice,
        }
    )

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@app.get("/weight")
def weight_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()

    records = (
        db.query(WeightRecord)
        .filter(WeightRecord.user_id == user_id)
        .order_by(WeightRecord.record_date.asc(), WeightRecord.id.asc())
        .all()
    )

    return templates.TemplateResponse(
        request,
        "weight.html",
        {
            
            "user": user,
            "records": records,
            "message": ""
        }
    )


@app.post("/weight")
def save_weight(
    request: Request,
    record_date: str = Form(...),
    weight: float = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    new_record = WeightRecord(
        user_id=user_id,
        record_date=date.fromisoformat(record_date),
        weight=weight,
        note=note
    )

    db.add(new_record)
    db.commit()

    user = db.query(User).filter(User.id == user_id).first()
    records = (
        db.query(WeightRecord)
        .filter(WeightRecord.user_id == user_id)
        .order_by(WeightRecord.record_date.asc(), WeightRecord.id.asc())
        .all()
    )

    return templates.TemplateResponse(
        request,
        "weight.html",
        {
            
            "user": user,
            "records": records,
            "message": "Lưu cân nặng thành công!"
        }
    )


@app.get("/predict")
def predict_weight(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()

    records = (
        db.query(WeightRecord)
        .filter(WeightRecord.user_id == user_id)
        .order_by(WeightRecord.record_date.asc(), WeightRecord.id.asc())
        .all()
    )

    if len(records) < 2:
        return templates.TemplateResponse(
            request,
            "predict.html",
            {
                
                "user": user,
                "message": "Cần ít nhất 2 mốc cân nặng để dự đoán.",
                "predicted_7": None,
                "predicted_30": None,
                "labels": [],
                "weights": [],
                "future_labels": [],
                "future_weights": []
            }
        )

    start_date = records[0].record_date
    X = np.array([(r.record_date - start_date).days for r in records]).reshape(-1, 1)
    y = np.array([r.weight for r in records])

    model = LinearRegression()
    model.fit(X, y)

    last_date = records[-1].record_date
    last_day_number = (last_date - start_date).days

    predict_day_7 = last_day_number + 7
    predict_day_30 = last_day_number + 30

    predicted_7 = float(model.predict([[predict_day_7]])[0])
    predicted_30 = float(model.predict([[predict_day_30]])[0])

    current_weight = records[-1].weight

    max_loss_7 = current_weight - 1
    max_loss_30 = current_weight - 4

    predicted_7 = max(predicted_7, max_loss_7)
    predicted_30 = max(predicted_30, max_loss_30)

    labels = [r.record_date.strftime("%d/%m/%Y") for r in records]
    weights = [r.weight for r in records]

    future_date_7 = last_date + timedelta(days=7)
    future_date_30 = last_date + timedelta(days=30)

    future_labels = [
        future_date_7.strftime("%d/%m/%Y"),
        future_date_30.strftime("%d/%m/%Y")
    ]
    future_weights = [
        round(float(predicted_7), 2),
        round(float(predicted_30), 2)
    ]

    return templates.TemplateResponse(
        request,
        "predict.html",
        {
           
            "user": user,
            "message": "",
            "predicted_7": round(float(predicted_7), 2),
            "predicted_30": round(float(predicted_30), 2),
            "labels": labels,
            "weights": weights,
            "future_labels": future_labels,
            "future_weights": future_weights
        }
    )


@app.get("/health")
def health_analysis(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()

    latest_record = get_latest_weight_record(db, user_id)
    latest_weight = latest_record.weight if latest_record else None

    if not user or not user.height or not latest_weight or not user.age or not user.gender:
        return templates.TemplateResponse(
            request,
            "health.html",
            {
                
                "user": user,
                "message": "Vui lòng cập nhật đầy đủ tuổi, giới tính, chiều cao và nhập ít nhất 1 bản ghi cân nặng.",
                "bmi": None,
                "status": None,
                "bmr": None,
                "tdee": None,
                "goal": None,
                "target_calories": None,
                "exercise": None,
                "diet": None,
                "model_file": None,
                "body_type": None,
                "latest_weight": None
            }
        )

    height_m = user.height / 100
    bmi = round(latest_weight / (height_m ** 2), 2)

    if bmi < 18.5:
        status = "Thiếu cân"
        body_type = "Tạng người gầy"
        model_file = "/static/models/thin.glb"
    elif bmi < 25:
        status = "Bình thường"
        body_type = "Tạng người cân đối"
        model_file = "/static/models/normal.glb"
    else:
        status = "Thừa cân/Béo phì"
        body_type = "Tạng người đầy đặn"
        model_file = "/static/models/overweight.glb"

    if user.gender.lower() == "nam":
        bmr = 10 * latest_weight + 6.25 * user.height - 5 * user.age + 5
    else:
        bmr = 10 * latest_weight + 6.25 * user.height - 5 * user.age - 161

    bmr = round(bmr, 2)

    activity_map = {
        "Ít vận động": 1.2,
        "Vận động nhẹ": 1.375,
        "Vận động vừa": 1.55,
        "Vận động nhiều": 1.725
    }

    activity_factor = activity_map.get(user.activity_level, 1.2)
    tdee = round(bmr * activity_factor, 2)

    goal = "Duy trì"
    if user.target_weight and latest_weight:
        if user.target_weight < latest_weight:
            goal = "Giảm cân"
        elif user.target_weight > latest_weight:
            goal = "Tăng cân"

    if goal == "Giảm cân":
        target_calories = round(tdee - 500, 2)
        exercise = [
            "Đi bộ nhanh 30-45 phút/ngày",
            "Chạy bộ nhẹ hoặc đạp xe 3-5 buổi/tuần",
            "Cardio mức vừa",
            "Tập tạ toàn thân 3 buổi/tuần"
        ]
        diet = [
            "Giảm đồ ngọt và đồ chiên",
            "Ưu tiên cá, thịt nạc, trứng, rau xanh",
            "Kiểm soát khẩu phần tinh bột",
            "Ăn đủ protein"
        ]
    elif goal == "Tăng cân":
        target_calories = round(tdee + 300, 2)
        exercise = [
            "Tập gym tăng cơ",
            "Ưu tiên squat, deadlift, bench press",
            "Tập 4-5 buổi/tuần",
            "Ngủ đủ để phục hồi"
        ]
        diet = [
            "Tăng calo từ nguồn tốt",
            "Ăn thêm bữa phụ",
            "Ưu tiên protein và carb tốt",
            "Dùng sữa, yến mạch, trứng, thịt"
        ]
    else:
        target_calories = round(tdee, 2)
        exercise = [
            "Đi bộ hoặc chạy nhẹ 3-4 buổi/tuần",
            "Tập gym duy trì",
            "Kết hợp cardio và sức mạnh",
            "Giữ vận động đều"
        ]
        diet = [
            "Ăn cân bằng các nhóm chất",
            "Ưu tiên rau xanh và trái cây",
            "Hạn chế đồ ăn nhanh",
            "Duy trì lượng calo hợp lý"
        ]

    return templates.TemplateResponse(
        request,
        "health.html",
        {
            
            "user": user,
            "message": "",
            "bmi": bmi,
            "status": status,
            "bmr": bmr,
            "tdee": tdee,
            "goal": goal,
            "target_calories": target_calories,
            "exercise": exercise,
            "diet": diet,
            "model_file": model_file,
            "body_type": body_type,
            "latest_weight": latest_weight
        }
    )
@app.post("/weight/delete/{record_id}")
def delete_weight_record(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    record = (
        db.query(WeightRecord)
        .filter(WeightRecord.id == record_id, WeightRecord.user_id == user_id)
        .first()
    )

    if record:
        db.delete(record)
        db.commit()

    return RedirectResponse(url="/weight", status_code=302)
@app.get("/forgot-password")
def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        {
            "message": ""
        }
    )


@app.post("/forgot-password")
def forgot_password(
    request: Request,
    email: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        email = email.strip().lower()
        new_password = new_password.strip()
        confirm_password = confirm_password.strip()

        user = db.query(User).filter(User.email == email).first()

        if not user:
            return templates.TemplateResponse(
                request,
                "forgot_password.html",
                {
                    "message": "Email không tồn tại trong hệ thống!"
                }
            )

        if len(new_password) < 8:
            return templates.TemplateResponse(
                request,
                "forgot_password.html",
                {
                    "message": "Mật khẩu phải có ít nhất 8 ký tự!"
                }
            )

        if new_password != confirm_password:
            return templates.TemplateResponse(
                request,
                "forgot_password.html",
                {
                    "message": "Mật khẩu xác nhận không khớp!"
                }
            )

        # 🔥 QUAN TRỌNG NHẤT
        user.password = hash_password(new_password)

        db.commit()

        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "message": "Đổi mật khẩu thành công! Hãy đăng nhập lại."
            }
        )

    except Exception as e:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "forgot_password.html",
            {
                "message": f"Có lỗi xảy ra: {str(e)}"
            }
        )
def predict_target_date(records, target_weight):
    if not records or len(records) < 2 or target_weight is None:
        return None

    first_record = records[0]
    last_record = records[-1]

    days_diff = (last_record.record_date - first_record.record_date).days
    weight_diff = last_record.weight - first_record.weight

    if days_diff <= 0:
        return None

    rate_per_day = weight_diff / days_diff

    current_weight = last_record.weight

    if rate_per_day == 0:
        return None

    remaining_weight = target_weight - current_weight

    # Nếu hướng hiện tại không đi về mục tiêu thì không dự đoán
    if (remaining_weight > 0 and rate_per_day <= 0) or (remaining_weight < 0 and rate_per_day >= 0):
        return None

    estimated_days = abs(remaining_weight / rate_per_day)

    if estimated_days > 365 * 3:
        return None

    return last_record.record_date + timedelta(days=round(estimated_days))
@app.get("/ai-chat")
def ai_chat_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user_id)
        .order_by(asc(ChatMessage.created_at))
        .all()
    )

    rendered_messages = []
    for msg in messages:
        if msg.role == "assistant":
            rendered_content = markdown.markdown(
                msg.content,
                extensions=["extra", "nl2br", "fenced_code"]
            )
        else:
            rendered_content = msg.content.replace("\n", "<br>")

        rendered_messages.append({
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "rendered_content": rendered_content
        })

    return templates.TemplateResponse(
       request,
        "ai_chat.html",
        {
            
            "user": user,
            "messages": rendered_messages
        }
    )
@app.post("/ai-chat/send")
def ai_chat_send(
    request: Request,
    message: str = Form(...),
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)
    if not gemini_client:
        return JSONResponse(
        {"error": "Chưa cấu hình GEMINI_API_KEY trên server"},
        status_code=500
    )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse({"error": "Không tìm thấy người dùng"}, status_code=404)

    latest_record = (
        db.query(WeightRecord)
        .filter(WeightRecord.user_id == user_id)
        .order_by(desc(WeightRecord.record_date), desc(WeightRecord.id))
        .first()
    )

    latest_weight = latest_record.weight if latest_record else None
    bmi = calculate_bmi(user.height, latest_weight) if latest_weight else None
    bmr = calculate_bmr(user, latest_weight) if latest_weight else None
    tdee = calculate_tdee(bmr, user.activity_level) if bmr else None

    user_msg = ChatMessage(user_id=user_id, role="user", content=message)
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user_id)
        .order_by(desc(ChatMessage.created_at))
        .limit(12)
        .all()
    )
    history = list(reversed(history))

    system_prompt = f"""
Bạn là trợ lý AI về sức khỏe và cân nặng.
Hãy trả lời bằng tiếng Việt, dễ hiểu, thân thiện, thực tế.
Ưu tiên câu trả lời có cấu trúc rõ ràng:
- có thể dùng gạch đầu dòng
- có thể chia mục nhỏ
- ngắn gọn nhưng hữu ích

Chỉ tư vấn phổ thông về dinh dưỡng, cân nặng, luyện tập và thói quen sống lành mạnh.
Không chẩn đoán bệnh. Không thay thế bác sĩ.
Nếu câu hỏi có dấu hiệu nghiêm trọng, hãy khuyên người dùng gặp bác sĩ hoặc chuyên gia y tế.

Thông tin người dùng:
- Họ tên: {user.full_name}
- Tuổi: {user.age if user.age else 'Chưa cập nhật'}
- Giới tính: {user.gender if user.gender else 'Chưa cập nhật'}
- Chiều cao: {str(user.height) + ' cm' if user.height else 'Chưa cập nhật'}
- Cân nặng hiện tại: {str(latest_weight) + ' kg' if latest_weight else 'Chưa cập nhật'}
- Cân nặng mục tiêu: {str(user.target_weight) + ' kg' if user.target_weight else 'Chưa cập nhật'}
- Mức độ vận động: {user.activity_level if user.activity_level else 'Chưa cập nhật'}
- BMI: {bmi if bmi else 'Chưa cập nhật'}
- BMR: {str(bmr) + ' kcal' if bmr else 'Chưa cập nhật'}
- TDEE: {str(tdee) + ' kcal' if tdee else 'Chưa cập nhật'}
"""

    history_text = ""
    for msg in history:
        role_name = "Người dùng" if msg.role == "user" else "AI"
        history_text += f"{role_name}: {msg.content}\n"

    prompt = f"""
{system_prompt}

Lịch sử hội thoại gần đây:
{history_text}

Câu hỏi mới của người dùng:
{message}
"""
    

    try:
        response = gemini_client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt
        )

        answer = response.text.strip() if response.text else "Mình chưa thể trả lời lúc này."

    except Exception as e:
        print("GEMINI ERROR:", repr(e))
        return JSONResponse({"error": f"Lỗi Gemini: {str(e)}"}, status_code=500)

    assistant_msg = ChatMessage(user_id=user_id, role="assistant", content=answer)
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    rendered_answer = markdown.markdown(
        answer,
        extensions=["extra", "nl2br", "fenced_code"]
    )

    return JSONResponse({
        "answer": answer,
        "rendered_answer": rendered_answer,
        "user_message_id": user_msg.id,
        "assistant_message_id": assistant_msg.id
    })

@app.post("/ai-chat/delete/{message_id}")
def delete_ai_chat_message(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)

    msg = (
        db.query(ChatMessage)
        .filter(ChatMessage.id == message_id, ChatMessage.user_id == user_id)
        .first()
    )

    if not msg:
        return JSONResponse({"error": "Không tìm thấy tin nhắn"}, status_code=404)

    db.delete(msg)
    db.commit()

    return JSONResponse({"success": True, "message": "Đã xóa tin nhắn"})
@app.post("/ai-chat/clear")
def clear_ai_chat(
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)

    db.query(ChatMessage).filter(ChatMessage.user_id == user_id).delete()
    db.commit()

    return JSONResponse({"success": True, "message": "Đã xóa toàn bộ cuộc trò chuyện"})

def save_chat_message(db: Session, user_id: int, role: str, content: str):
    msg = ChatMessage(
        user_id=user_id,
        role=role,
        content=content
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def calculate_user_health_info(user, latest_weight):
    if not user:
        return None

    # Bắt buộc phải có các dữ liệu này
    if not user.height or latest_weight is None or not user.age or not user.gender or not user.activity_level:
        return None

    try:
        height_m = user.height / 100
        weight = float(latest_weight)
        bmi = round(weight / (height_m ** 2), 2)

        age = int(user.age)
        gender = (user.gender or "").strip().lower()
        activity_level = (user.activity_level or "").strip()

        if gender == "nam":
            bmr = 10 * weight + 6.25 * user.height - 5 * age + 5
        else:
            bmr = 10 * weight + 6.25 * user.height - 5 * age - 161

        activity_map = {
            "Ít vận động": 1.2,
            "Vận động nhẹ": 1.375,
            "Vận động vừa": 1.55,
            "Vận động nhiều": 1.725
        }

        activity_factor = activity_map.get(activity_level, 1.2)
        tdee = round(bmr * activity_factor, 0)

        goal = "Duy trì"
        target_calories = int(tdee)

        if user.target_weight:
            if float(user.target_weight) < weight:
                goal = "Giảm cân"
                target_calories = int(tdee - 500)
            elif float(user.target_weight) > weight:
                goal = "Tăng cân"
                target_calories = int(tdee + 300)

        return {
            "bmi": bmi,
            "bmr": round(bmr, 0),
            "tdee": int(tdee),
            "goal": goal,
            "target_calories": max(target_calories, 1200)
        }
    except Exception:
        return None


def get_meal_plan_1_day(goal, calories):
    if goal == "Giảm cân":
        return f"""
        <h4>📋 Thực đơn gợi ý 1 ngày ({goal})</h4>
        <p><strong>Mục tiêu năng lượng:</strong> khoảng {calories} kcal/ngày</p>

        <p><strong>Bữa sáng:</strong> 2 quả trứng luộc, 2 lát bánh mì nguyên cám, 1 quả táo</p>
        <p><strong>Bữa phụ sáng:</strong> 1 hũ sữa chua không đường</p>
        <p><strong>Bữa trưa:</strong> 150g ức gà áp chảo, 1/2 chén cơm gạo lứt, salad rau xanh</p>
        <p><strong>Bữa phụ chiều:</strong> 1 quả chuối nhỏ hoặc 10 hạt hạnh nhân</p>
        <p><strong>Bữa tối:</strong> 150g cá hấp, rau luộc, 1 củ khoai lang nhỏ</p>

        <p><strong>Lưu ý:</strong> Uống đủ nước, hạn chế nước ngọt và đồ chiên rán.</p>
        """
    elif goal == "Tăng cân":
        return f"""
        <h4>📋 Thực đơn gợi ý 1 ngày ({goal})</h4>
        <p><strong>Mục tiêu năng lượng:</strong> khoảng {calories} kcal/ngày</p>

        <p><strong>Bữa sáng:</strong> Bánh mì trứng, 1 ly sữa, 1 quả chuối</p>
        <p><strong>Bữa phụ sáng:</strong> 1 hũ sữa chua, 1 nắm hạt dinh dưỡng</p>
        <p><strong>Bữa trưa:</strong> 200g thịt bò, 1 chén cơm, rau xanh, canh</p>
        <p><strong>Bữa phụ chiều:</strong> Sinh tố bơ hoặc chuối sữa</p>
        <p><strong>Bữa tối:</strong> 200g cá hồi hoặc thịt gà, khoai tây, rau củ</p>
        <p><strong>Bữa phụ tối:</strong> 1 ly sữa ấm hoặc yến mạch</p>

        <p><strong>Lưu ý:</strong> Chia nhỏ bữa ăn và ưu tiên protein, tinh bột tốt.</p>
        """
    else:
        return f"""
        <h4>📋 Thực đơn gợi ý 1 ngày ({goal})</h4>
        <p><strong>Mục tiêu năng lượng:</strong> khoảng {calories} kcal/ngày</p>

        <p><strong>Bữa sáng:</strong> Yến mạch với sữa chua và trái cây</p>
        <p><strong>Bữa phụ sáng:</strong> 1 quả táo hoặc cam</p>
        <p><strong>Bữa trưa:</strong> 150g thịt nạc, 1 chén cơm vừa, rau luộc, canh</p>
        <p><strong>Bữa phụ chiều:</strong> 1 hũ sữa chua không đường</p>
        <p><strong>Bữa tối:</strong> Cá hấp hoặc ức gà, rau xanh, 1 củ khoai lang nhỏ</p>

        <p><strong>Lưu ý:</strong> Duy trì ăn uống đều đặn và cân bằng dinh dưỡng.</p>
        """


def get_meal_plan_7_days(goal, calories):
    if goal == "Giảm cân":
        days = [
            "Ngày 1: Yến mạch, ức gà, salad, cá hấp",
            "Ngày 2: Trứng luộc, thịt bò xào rau, khoai lang, sữa chua",
            "Ngày 3: Bánh mì nguyên cám, cá hồi, rau luộc, trái cây ít ngọt",
            "Ngày 4: Yến mạch chuối, ức gà áp chảo, canh rau, hạnh nhân",
            "Ngày 5: Trứng + táo, thịt nạc, cơm gạo lứt, rau củ hấp",
            "Ngày 6: Sữa chua yến mạch, cá hấp, salad dưa leo, khoai lang",
            "Ngày 7: Bún gạo lứt, thịt gà xé, rau xanh, trái cây"
        ]
    elif goal == "Tăng cân":
        days = [
            "Ngày 1: Bánh mì trứng, thịt bò, cơm, sữa",
            "Ngày 2: Yến mạch sữa, cá hồi, khoai tây, sinh tố bơ",
            "Ngày 3: Phở bò, thịt gà, cơm, trái cây",
            "Ngày 4: Bún thịt, trứng, sữa chua, các loại hạt",
            "Ngày 5: Bánh mì ốp la, cá, cơm, sinh tố chuối",
            "Ngày 6: Xôi, thịt bò, rau xanh, sữa",
            "Ngày 7: Mì nui, ức gà, khoai lang, trái cây"
        ]
    else:
        days = [
            "Ngày 1: Yến mạch, thịt gà, cơm vừa đủ, rau xanh",
            "Ngày 2: Bánh mì trứng, cá hấp, canh rau, trái cây",
            "Ngày 3: Sữa chua, thịt nạc, cơm, salad",
            "Ngày 4: Yến mạch chuối, cá hồi, rau luộc, táo",
            "Ngày 5: Bánh mì nguyên cám, thịt bò, canh rau",
            "Ngày 6: Trứng luộc, ức gà, khoai lang, sữa chua",
            "Ngày 7: Bún gạo lứt, cá, rau xanh, trái cây"
        ]

    day_html = "".join(
        [f"<p><strong>{item.split(':')[0]}:</strong> {item.split(':', 1)[1].strip()}</p>" for item in days]
    )

    return f"""
    <h4>🗓️ Thực đơn gợi ý 7 ngày ({goal})</h4>
    <p><strong>Mục tiêu năng lượng:</strong> khoảng {calories} kcal/ngày</p>
    {day_html}
    <p><strong>Lưu ý:</strong> Có thể thay đổi món tương đương để đỡ ngán nhưng vẫn giữ đúng mục tiêu calo.</p>
    """


def get_meal_plan_30_days(goal, calories):
    if goal == "Giảm cân":
        content = """
        <p><strong>Tuần 1:</strong> Giảm nước ngọt, đồ chiên, tăng rau xanh và protein nạc.</p>
        <p><strong>Tuần 2:</strong> Duy trì cơm vừa phải, ưu tiên cá, gà, trứng, khoai lang.</p>
        <p><strong>Tuần 3:</strong> Ăn đều 3 bữa chính + 1-2 bữa phụ nhẹ, tránh ăn đêm.</p>
        <p><strong>Tuần 4:</strong> Ổn định khẩu phần, theo dõi cân nặng và điều chỉnh lượng calo nếu cần.</p>
        """
    elif goal == "Tăng cân":
        content = """
        <p><strong>Tuần 1:</strong> Tăng số bữa ăn, bổ sung sữa, trứng, thịt, cơm và khoai.</p>
        <p><strong>Tuần 2:</strong> Duy trì thừa nhẹ calo, ưu tiên protein và tinh bột tốt.</p>
        <p><strong>Tuần 3:</strong> Kết hợp thêm sinh tố, hạt dinh dưỡng, sữa chua, bơ đậu phộng.</p>
        <p><strong>Tuần 4:</strong> Theo dõi cân nặng mỗi tuần để tăng calo hợp lý, tránh tăng mỡ quá nhanh.</p>
        """
    else:
        content = """
        <p><strong>Tuần 1:</strong> Thiết lập thói quen ăn đúng giờ, cân bằng đủ 4 nhóm chất.</p>
        <p><strong>Tuần 2:</strong> Duy trì khẩu phần ổn định, ưu tiên thực phẩm tươi và ít chế biến.</p>
        <p><strong>Tuần 3:</strong> Kiểm soát đồ ngọt, nước ngọt, giữ nhịp sinh hoạt điều độ.</p>
        <p><strong>Tuần 4:</strong> Tiếp tục chế độ ăn lành mạnh và theo dõi cân nặng định kỳ.</p>
        """

    return f"""
    <h4>📆 Thực đơn gợi ý 30 ngày ({goal})</h4>
    <p><strong>Mục tiêu năng lượng:</strong> khoảng {calories} kcal/ngày</p>
    {content}
    <p><strong>Gợi ý chung:</strong> Với kế hoạch 30 ngày, bạn nên theo dõi cân nặng mỗi tuần để điều chỉnh khẩu phần phù hợp.</p>
    """

@app.post("/ai-chat/meal-plan-7-days")
def generate_meal_plan_7_days(request: Request, db: Session = Depends(get_db)):
    try:
        user_id = request.session.get("user_id")
        if not user_id:
            return JSONResponse({"error": "Bạn cần đăng nhập."}, status_code=401)

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return JSONResponse({"error": "Không tìm thấy người dùng."}, status_code=404)

        latest_record = (
            db.query(WeightRecord)
            .filter(WeightRecord.user_id == user_id)
            .order_by(desc(WeightRecord.record_date), desc(WeightRecord.id))
            .first()
        )

        latest_weight = latest_record.weight if latest_record else None
        info = calculate_user_health_info(user, latest_weight)

        if not info:
            return JSONResponse(
                {"error": "Vui lòng cập nhật đầy đủ chiều cao, tuổi, giới tính, mức vận động và thêm ít nhất 1 bản ghi cân nặng."},
                status_code=400
            )

        user_prompt = "🗓️ Gợi ý thực đơn 7 ngày"
        answer = get_meal_plan_7_days(info["goal"], info["target_calories"])

        user_msg = save_chat_message(db, user_id, "user", user_prompt)
        assistant_msg = save_chat_message(db, user_id, "assistant", answer)

        return JSONResponse({
            "user_message_id": user_msg.id,
            "user_content": user_prompt,
            "assistant_message_id": assistant_msg.id,
            "answer": answer,
            "rendered_answer": answer
        })

    except Exception as e:
        return JSONResponse({"error": f"Lỗi tạo thực đơn 7 ngày: {str(e)}"}, status_code=500)
@app.post("/ai-chat/meal-plan-30-days")
def generate_meal_plan_30_days(request: Request, db: Session = Depends(get_db)):
    try:
        user_id = request.session.get("user_id")
        if not user_id:
            return JSONResponse({"error": "Bạn cần đăng nhập."}, status_code=401)

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return JSONResponse({"error": "Không tìm thấy người dùng."}, status_code=404)

        latest_record = (
            db.query(WeightRecord)
            .filter(WeightRecord.user_id == user_id)
            .order_by(desc(WeightRecord.record_date), desc(WeightRecord.id))
            .first()
        )

        latest_weight = latest_record.weight if latest_record else None
        info = calculate_user_health_info(user, latest_weight)

        if not info:
            return JSONResponse(
                {"error": "Vui lòng cập nhật đầy đủ chiều cao, tuổi, giới tính, mức vận động và thêm ít nhất 1 bản ghi cân nặng."},
                status_code=400
            )

        user_prompt = "📆 Gợi ý thực đơn 30 ngày"
        answer = get_meal_plan_30_days(info["goal"], info["target_calories"])

        user_msg = save_chat_message(db, user_id, "user", user_prompt)
        assistant_msg = save_chat_message(db, user_id, "assistant", answer)

        return JSONResponse({
            "user_message_id": user_msg.id,
            "user_content": user_prompt,
            "assistant_message_id": assistant_msg.id,
            "answer": answer,
            "rendered_answer": answer
        })

    except Exception as e:
        return JSONResponse({"error": f"Lỗi tạo thực đơn 30 ngày: {str(e)}"}, status_code=500)

@app.post("/ai-chat/meal-plan")
def ai_meal_plan_1_day(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)

    if not gemini_client:
        return JSONResponse(
            {"error": "Chưa cấu hình GEMINI_API_KEY trên server"},
            status_code=500
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse({"error": "Không tìm thấy người dùng"}, status_code=404)

    user_prompt = "📋 Gợi ý thực đơn 1 ngày"

    latest_record = (
        db.query(WeightRecord)
        .filter(WeightRecord.user_id == user_id)
        .order_by(desc(WeightRecord.record_date), desc(WeightRecord.id))
        .first()
    )

    latest_weight = latest_record.weight if latest_record else None
    bmi = calculate_bmi(user.height, latest_weight) if latest_weight else None
    bmr = calculate_bmr(user, latest_weight) if latest_weight else None
    tdee = calculate_tdee(bmr, user.activity_level) if bmr else None

    prompt = f"""
Bạn là chuyên gia hỗ trợ dinh dưỡng cơ bản.
Hãy gợi ý một thực đơn 1 ngày bằng tiếng Việt, dễ áp dụng, phù hợp người Việt Nam.

Yêu cầu:
- chia thành: bữa sáng, bữa trưa, bữa tối, 1 bữa phụ
- món ăn đơn giản, dễ mua ở Việt Nam
- ghi ngắn gọn
- cuối cùng thêm 3 lưu ý nhỏ

Thông tin người dùng:
- Tuổi: {user.age if user.age else 'Chưa cập nhật'}
- Giới tính: {user.gender if user.gender else 'Chưa cập nhật'}
- Chiều cao: {str(user.height) + ' cm' if user.height else 'Chưa cập nhật'}
- Cân nặng hiện tại: {str(latest_weight) + ' kg' if latest_weight else 'Chưa cập nhật'}
- Cân nặng mục tiêu: {str(user.target_weight) + ' kg' if user.target_weight else 'Chưa cập nhật'}
- Mức vận động: {user.activity_level if user.activity_level else 'Chưa cập nhật'}
- BMI: {bmi if bmi else 'Chưa cập nhật'}
- TDEE: {str(tdee) + ' kcal' if tdee else 'Chưa cập nhật'}
"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt
        )

        answer = response.text.strip() if response.text else "Mình chưa thể gợi ý thực đơn lúc này."

        # Lưu user message
        user_msg = save_chat_message(db, user_id, "user", user_prompt)

        # Lưu assistant message
        assistant_msg = save_chat_message(db, user_id, "assistant", answer)

        rendered_answer = markdown.markdown(
            answer,
            extensions=["extra", "nl2br", "fenced_code"]
        )

        return JSONResponse({
            "user_message_id": user_msg.id,
            "user_content": user_prompt,
            "assistant_message_id": assistant_msg.id,
            "answer": answer,
            "rendered_answer": rendered_answer
        })

    except Exception as e:
        return JSONResponse({"error": f"Lỗi Gemini: {str(e)}"}, status_code=500)

@app.get("/reminders")
def reminders_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()
    settings = db.query(ReminderSettings).filter(ReminderSettings.user_id == user_id).first()

    latest_record = get_latest_weight_record(db, user_id)
    latest_weight = latest_record.weight if latest_record else None

    goal = "Chưa xác định"
    if user and user.target_weight and latest_weight:
        if user.target_weight < latest_weight:
            goal = "Giảm cân"
        elif user.target_weight > latest_weight:
            goal = "Tăng cân"
        else:
            goal = "Duy trì"

    if not settings:
        breakfast, lunch, dinner, workout = suggest_default_reminder_times(None, goal)

        settings = ReminderSettings(
            user_id=user_id,
            wake_up_time="06:30",
            sleep_time="22:30",
            breakfast_time=breakfast,
            lunch_time=lunch,
            dinner_time=dinner,
            workout_time=workout,
            water_interval_minutes=120,
            daily_water_goal_ml=2000,
            enable_meal_reminders=True,
            enable_water_reminders=True,
            enable_workout_reminders=True
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)

    return templates.TemplateResponse(
        request,
        "reminders.html",
        {
            
            "user": user,
            "settings": settings,
            "goal": goal
        }
    )
@app.post("/reminders")
def save_reminders(
    request: Request,
    wake_up_time: str = Form(...),
    sleep_time: str = Form(...),
    breakfast_time: str = Form(...),
    lunch_time: str = Form(...),
    dinner_time: str = Form(...),
    workout_time: str = Form(...),
    water_interval_minutes: int = Form(...),
    daily_water_goal_ml: int = Form(...),
    enable_meal_reminders: str = Form(None),
    enable_water_reminders: str = Form(None),
    enable_workout_reminders: str = Form(None),
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    settings = db.query(ReminderSettings).filter(ReminderSettings.user_id == user_id).first()

    if not settings:
        settings = ReminderSettings(user_id=user_id)
        db.add(settings)

    settings.wake_up_time = wake_up_time
    settings.sleep_time = sleep_time
    settings.breakfast_time = breakfast_time
    settings.lunch_time = lunch_time
    settings.dinner_time = dinner_time
    settings.workout_time = workout_time
    settings.water_interval_minutes = water_interval_minutes
    settings.daily_water_goal_ml = daily_water_goal_ml

    settings.enable_meal_reminders = bool(enable_meal_reminders)
    settings.enable_water_reminders = bool(enable_water_reminders)
    settings.enable_workout_reminders = bool(enable_workout_reminders)

    db.commit()

    return RedirectResponse(url="/reminders", status_code=303)