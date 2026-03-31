from sqlalchemy import Column, Integer, String, Float, TIMESTAMP, text
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    password = Column(String(255), nullable=False)

    age = Column(Integer, nullable=True)
    gender = Column(String(10), nullable=True)
    height = Column(Float, nullable=True)
    current_weight = Column(Float, nullable=True)
    target_weight = Column(Float, nullable=True)
    activity_level = Column(String(50), nullable=True)
   

    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
from sqlalchemy import Column, Integer, String, Float, TIMESTAMP, text, Date, ForeignKey

class WeightRecord(Base):
    __tablename__ = "weight_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    record_date = Column(Date, nullable=False)
    weight = Column(Float, nullable=False)
    note = Column(String(255), nullable=True)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text
from sqlalchemy.sql import func
from app.database import Base

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(20), nullable=False)   # user / assistant
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())    

from sqlalchemy import Column, Integer, String, Boolean, ForeignKey

class ReminderSettings(Base):
    __tablename__ = "reminder_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    wake_up_time = Column(String(10), nullable=True)
    sleep_time = Column(String(10), nullable=True)

    breakfast_time = Column(String(10), nullable=True)
    lunch_time = Column(String(10), nullable=True)
    dinner_time = Column(String(10), nullable=True)

    workout_time = Column(String(10), nullable=True)

    water_interval_minutes = Column(Integer, default=120)
    daily_water_goal_ml = Column(Integer, default=2000)

    enable_meal_reminders = Column(Boolean, default=True)
    enable_water_reminders = Column(Boolean, default=True)
    enable_workout_reminders = Column(Boolean, default=True)