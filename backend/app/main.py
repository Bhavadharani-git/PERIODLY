from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from pathlib import Path
import os
from hmac import compare_digest
from .config import APP_ENV, IS_PRODUCTION, PERIODLY_API_KEY, CORS_ORIGINS
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database import Base, engine, get_db
from . import models as m, schemas as s

# PERIODLY Excel export support (Python standard library only)
import io
import zipfile
import re
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape
from urllib.parse import quote as _url_quote
from fastapi.responses import Response

# Local development can bootstrap a missing SQLite schema. Production must
# use Alembic migrations before the API process starts (see Dockerfile).
if not IS_PRODUCTION:
    Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="PERIODLY API",
    version="1.0.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key"],
    )


@app.middleware("http")
async def production_api_key_guard(request: Request, call_next):
    """Require a shared admin key for API routes in production deployments.

    This is a single-admin gate, not a replacement for per-user accounts/RBAC.
    Always deploy behind HTTPS and keep the key out of source control.
    """
    path = request.url.path
    if IS_PRODUCTION and path.startswith("/api/") and path != "/api/health" and request.method != "OPTIONS":
        supplied = request.headers.get("X-API-Key", "")
        if not compare_digest(supplied, PERIODLY_API_KEY):
            return JSONResponse(
                status_code=401,
                content={"detail": "Admin access key required or invalid. Enter the PERIODLY admin key and retry."},
            )
    return await call_next(request)


@app.get("/api/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database connection failed") from exc
    return {"status": "ok", "application": "PERIODLY", "database": "connected"}

def create_record(db, model, payload):
    obj = model(**payload.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

def list_records(db, model, school_id=None):
    q = db.query(model)
    if school_id is not None and hasattr(model, "school_id"):
        q = q.filter(model.school_id == school_id)
    return q.order_by(model.id.desc()).all()

@app.post("/api/schools")
def add_school(data: s.SchoolIn, db: Session = Depends(get_db)):
    return create_record(db, m.School, data)

@app.get("/api/schools")
def schools(db: Session = Depends(get_db)):
    return list_records(db, m.School)

@app.post("/api/classes")
def add_class(data: s.ClassIn, db: Session = Depends(get_db)):
    return create_record(db, m.ClassSection, data)

@app.get("/api/classes")
def classes(school_id: int, db: Session = Depends(get_db)):
    return list_records(db, m.ClassSection, school_id)

@app.post("/api/teachers")
def add_teacher(data: s.TeacherIn, db: Session = Depends(get_db)):
    return create_record(db, m.Teacher, data)

@app.get("/api/teachers")
def teachers(school_id: int, db: Session = Depends(get_db)):
    return list_records(db, m.Teacher, school_id)

@app.post("/api/subjects")
def add_subject(data: s.SubjectIn, db: Session = Depends(get_db)):
    return create_record(db, m.Subject, data)

@app.get("/api/subjects")
def subjects(school_id: int, db: Session = Depends(get_db)):
    return list_records(db, m.Subject, school_id)

@app.post("/api/rooms")
def add_room(data: s.RoomIn, db: Session = Depends(get_db)):
    return create_record(db, m.Room, data)

@app.get("/api/rooms")
def rooms(school_id: int, db: Session = Depends(get_db)):
    return list_records(db, m.Room, school_id)

@app.post("/api/teacher-subjects")
def map_teacher_subject(data: s.MappingIn, db: Session = Depends(get_db)):
    return create_record(db, m.TeacherSubject, data)

@app.post("/api/availability")
def set_availability(data: s.AvailabilityIn, db: Session = Depends(get_db)):
    return create_record(db, m.Availability, data)

@app.get("/api/timetable")
def timetable(school_id: int, class_section_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(m.TimetableEntry).filter(m.TimetableEntry.school_id == school_id)
    if class_section_id:
        q = q.filter(m.TimetableEntry.class_section_id == class_section_id)
    return q.order_by(m.TimetableEntry.day, m.TimetableEntry.period).all()

def _validate_timetable_candidate(
    db: Session,
    school_id: int,
    class_section_id: int,
    teacher_id: int,
    subject_id: int,
    room_id: int | None,
    day: str,
    period: int,
    exclude_entry_id: int | None = None,
):
    """Validate a manual timetable assignment against the active school constraints."""
    school = db.query(m.School).filter_by(id=school_id).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    class_obj = db.query(m.ClassSection).filter_by(
        id=class_section_id, school_id=school_id
    ).first()
    teacher = db.query(m.Teacher).filter_by(
        id=teacher_id, school_id=school_id
    ).first()
    subject = db.query(m.Subject).filter_by(
        id=subject_id, school_id=school_id
    ).first()

    if not class_obj:
        raise HTTPException(status_code=400, detail="Selected class does not belong to this school.")
    if not teacher:
        raise HTTPException(status_code=400, detail="Selected teacher does not belong to this school.")
    if not subject:
        raise HTTPException(status_code=400, detail="Selected subject does not belong to this school.")

    allowed_days = [d.strip() for d in (school.working_days or "").split(",") if d.strip()]
    if day not in allowed_days:
        raise HTTPException(status_code=400, detail=f"{day} is not a configured working day.")
    if period < 1 or period > int(school.periods_per_day or 0):
        raise HTTPException(
            status_code=400,
            detail=f"Period must be between 1 and {school.periods_per_day}."
        )

    mapping = db.query(m.TeacherSubject).filter_by(
        teacher_id=teacher_id, subject_id=subject_id
    ).first()
    if not mapping:
        raise HTTPException(
            status_code=409,
            detail=f"{teacher.name} is not mapped to {subject.name}. Add the teacher-subject mapping first."
        )

    teacher_days = [d.strip() for d in (teacher.available_days or "").split(",") if d.strip()]
    if teacher_days and day not in teacher_days:
        raise HTTPException(
            status_code=409,
            detail=f"{teacher.name} is not available on {day}."
        )

    explicit_availability = db.query(m.Availability).filter_by(
        teacher_id=teacher_id, day=day, period=period
    ).first()
    if explicit_availability and not explicit_availability.available:
        raise HTTPException(
            status_code=409,
            detail=f"{teacher.name} is marked unavailable on {day}, period {period}."
        )

    if room_id is not None:
        room = db.query(m.Room).filter_by(id=room_id, school_id=school_id).first()
        if not room:
            raise HTTPException(status_code=400, detail="Selected room does not belong to this school.")
        if int(room.capacity or 0) < int(class_obj.strength or 0):
            raise HTTPException(
                status_code=409,
                detail=f"{room.name} capacity ({room.capacity}) is below class strength ({class_obj.strength})."
            )

    # Class, teacher and room must each be free in the chosen slot.
    conflicts_query = db.query(m.TimetableEntry).filter(
        m.TimetableEntry.school_id == school_id,
        m.TimetableEntry.day == day,
        m.TimetableEntry.period == period,
    )
    if exclude_entry_id is not None:
        conflicts_query = conflicts_query.filter(m.TimetableEntry.id != exclude_entry_id)
    slot_entries = conflicts_query.all()

    if any(row.class_section_id == class_section_id for row in slot_entries):
        raise HTTPException(status_code=409, detail="This class already has a lesson in that period.")
    if any(row.teacher_id == teacher_id for row in slot_entries):
        raise HTTPException(status_code=409, detail="This teacher is already assigned in that period.")
    if room_id is not None and any(row.room_id == room_id for row in slot_entries):
        raise HTTPException(status_code=409, detail="This room is already occupied in that period.")

    # Weekly teacher maximum.
    teacher_load_query = db.query(m.TimetableEntry).filter_by(
        school_id=school_id, teacher_id=teacher_id
    )
    if exclude_entry_id is not None:
        teacher_load_query = teacher_load_query.filter(m.TimetableEntry.id != exclude_entry_id)
    if teacher_load_query.count() + 1 > int(teacher.max_periods or 0):
        raise HTTPException(
            status_code=409,
            detail=f"{teacher.name} would exceed the weekly maximum of {teacher.max_periods} periods."
        )

    # Respect a class-specific weekly requirement when configured; otherwise use the subject default.
    requirement = db.query(m.WeeklyRequirement).filter_by(
        school_id=school_id,
        class_section_id=class_section_id,
        subject_id=subject_id,
    ).first()
    weekly_limit = int(
        requirement.periods_per_week if requirement else subject.weekly_periods
    )
    allocation_query = db.query(m.TimetableEntry).filter_by(
        school_id=school_id,
        class_section_id=class_section_id,
        subject_id=subject_id,
    )
    if exclude_entry_id is not None:
        allocation_query = allocation_query.filter(m.TimetableEntry.id != exclude_entry_id)
    if allocation_query.count() + 1 > weekly_limit:
        raise HTTPException(
            status_code=409,
            detail=f"{subject.name} would exceed its weekly requirement of {weekly_limit} periods for this class."
        )


@app.post("/api/timetable")
def create_timetable_entry(data: s.EntryCreate, db: Session = Depends(get_db)):
    _validate_timetable_candidate(
        db=db,
        school_id=data.school_id,
        class_section_id=data.class_section_id,
        teacher_id=data.teacher_id,
        subject_id=data.subject_id,
        room_id=data.room_id,
        day=data.day,
        period=data.period,
    )
    obj = m.TimetableEntry(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@app.patch("/api/timetable/{entry_id}")
def update_entry(entry_id: int, data: s.EntryUpdate, db: Session = Depends(get_db)):
    obj = db.query(m.TimetableEntry).filter(m.TimetableEntry.id == entry_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Timetable entry not found")

    payload = data.model_dump(exclude_unset=True)
    if not payload:
        return obj

    # A locked lesson must be explicitly unlocked before its content or slot can be changed.
    if obj.locked and any(key != "locked" for key in payload):
        raise HTTPException(
            status_code=409,
            detail="This lesson is locked. Unlock it first, then edit it."
        )

    if set(payload.keys()) == {"locked"} and payload["locked"] is False:
        obj.locked = False
        db.commit()
        db.refresh(obj)
        return obj

    candidate = {
        "school_id": obj.school_id,
        "class_section_id": obj.class_section_id,
        "teacher_id": payload.get("teacher_id", obj.teacher_id),
        "subject_id": payload.get("subject_id", obj.subject_id),
        "room_id": payload.get("room_id", obj.room_id),
        "day": payload.get("day", obj.day),
        "period": payload.get("period", obj.period),
    }
    _validate_timetable_candidate(
        db=db,
        school_id=candidate["school_id"],
        class_section_id=candidate["class_section_id"],
        teacher_id=candidate["teacher_id"],
        subject_id=candidate["subject_id"],
        room_id=candidate["room_id"],
        day=candidate["day"],
        period=candidate["period"],
        exclude_entry_id=obj.id,
    )

    for key, value in payload.items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return obj


@app.delete("/api/timetable")
def clear_unlocked(school_id: int, db: Session = Depends(get_db)):
    deleted = db.query(m.TimetableEntry).filter(m.TimetableEntry.school_id == school_id, m.TimetableEntry.locked == False).delete()
    db.commit()
    return {"deleted": deleted}



# AI scheduling preference APIs. These are soft preferences: hard constraints
# (availability, qualification, workload and collision checks) always take priority.
PREFERENCE_TIME_BANDS = {"any", "morning", "midday", "afternoon"}
SUBJECT_TIME_BANDS = {"auto", "any", "morning", "midday", "afternoon"}


def _validate_preference_school_links(db: Session, school_id: int, teacher_id: int | None = None, subject_id: int | None = None):
    if not db.query(m.School).filter_by(id=school_id).first():
        raise HTTPException(status_code=404, detail="School not found")
    if teacher_id is not None and not db.query(m.Teacher).filter_by(id=teacher_id, school_id=school_id).first():
        raise HTTPException(status_code=400, detail="Selected teacher does not belong to this school")
    if subject_id is not None and not db.query(m.Subject).filter_by(id=subject_id, school_id=school_id).first():
        raise HTTPException(status_code=400, detail="Selected subject does not belong to this school")


def _validate_teacher_preference_values(data: dict, school: m.School):
    band = str(data.get("preferred_time_of_day", "any")).lower()
    if band not in PREFERENCE_TIME_BANDS:
        raise HTTPException(status_code=422, detail="Teacher preferred_time_of_day must be any, morning, midday or afternoon")
    max_consecutive = int(data.get("max_consecutive_periods", 4))
    if max_consecutive < 1 or max_consecutive > max(1, int(school.periods_per_day or 1)):
        raise HTTPException(status_code=422, detail="max_consecutive_periods must be between 1 and the school's teaching periods per day")
    valid_days = {part.strip() for part in (school.working_days or "").split(",") if part.strip()}
    preferred_days = [part.strip() for part in str(data.get("preferred_days", "")).split(",") if part.strip()]
    invalid_days = [day for day in preferred_days if day not in valid_days]
    if invalid_days:
        raise HTTPException(status_code=422, detail=f"Unknown preferred day(s): {', '.join(invalid_days)}")
    preferred_periods = [part.strip() for part in str(data.get("preferred_periods", "")).split(",") if part.strip()]
    try:
        period_numbers = [int(part) for part in preferred_periods]
    except ValueError:
        raise HTTPException(status_code=422, detail="preferred_periods must be comma-separated period numbers")
    if any(period < 1 or period > int(school.periods_per_day or 0) for period in period_numbers):
        raise HTTPException(status_code=422, detail="preferred_periods contains a period outside the school's teaching day")


@app.get("/api/teacher-preferences")
def get_teacher_preferences(school_id: int, db: Session = Depends(get_db)):
    return db.query(m.TeacherPreference).filter_by(school_id=school_id).order_by(m.TeacherPreference.id).all()


@app.post("/api/teacher-preferences")
def create_teacher_preference(data: s.TeacherPreferenceIn, db: Session = Depends(get_db)):
    payload = data.model_dump()
    _validate_preference_school_links(db, payload["school_id"], teacher_id=payload["teacher_id"])
    school = db.query(m.School).filter_by(id=payload["school_id"]).first()
    _validate_teacher_preference_values(payload, school)
    existing = db.query(m.TeacherPreference).filter_by(school_id=payload["school_id"], teacher_id=payload["teacher_id"]).first()
    if existing:
        raise HTTPException(status_code=409, detail="A preference profile already exists for this teacher. Edit the existing profile.")
    obj = m.TeacherPreference(**payload)
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@app.put("/api/teacher-preferences/{record_id}")
def update_teacher_preference(record_id: int, payload: dict, db: Session = Depends(get_db)):
    obj = db.query(m.TeacherPreference).filter_by(id=record_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Teacher preference not found")
    values = {key: value for key, value in payload.items() if key in {
        "teacher_id", "preferred_days", "preferred_periods", "preferred_time_of_day",
        "avoid_first_period", "avoid_last_period", "max_consecutive_periods"
    }}
    teacher_id = int(values.get("teacher_id", obj.teacher_id))
    _validate_preference_school_links(db, obj.school_id, teacher_id=teacher_id)
    school = db.query(m.School).filter_by(id=obj.school_id).first()
    candidate = {key: getattr(obj, key) for key in [
        "preferred_days", "preferred_periods", "preferred_time_of_day", "max_consecutive_periods"
    ]}
    candidate.update(values)
    _validate_teacher_preference_values(candidate, school)
    duplicate = db.query(m.TeacherPreference).filter(
        m.TeacherPreference.school_id == obj.school_id,
        m.TeacherPreference.teacher_id == teacher_id,
        m.TeacherPreference.id != record_id,
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="A preference profile already exists for this teacher")
    for key, value in values.items():
        setattr(obj, key, value)
    db.commit(); db.refresh(obj)
    return obj


@app.delete("/api/teacher-preferences/{record_id}")
def delete_teacher_preference(record_id: int, db: Session = Depends(get_db)):
    obj = db.query(m.TeacherPreference).filter_by(id=record_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Teacher preference not found")
    db.delete(obj); db.commit()
    return {"deleted": True, "id": record_id}


def _validate_subject_profile_values(data: dict, school: m.School):
    band = str(data.get("preferred_time_of_day", "auto")).lower()
    if band not in SUBJECT_TIME_BANDS:
        raise HTTPException(status_code=422, detail="Subject preferred_time_of_day must be auto, any, morning, midday or afternoon")
    difficulty = int(data.get("difficulty", 3))
    if difficulty < 1 or difficulty > 5:
        raise HTTPException(status_code=422, detail="difficulty must be between 1 and 5")
    max_per_day = int(data.get("max_periods_per_day", 2))
    if max_per_day < 1 or max_per_day > max(1, int(school.periods_per_day or 1)):
        raise HTTPException(status_code=422, detail="max_periods_per_day must be between 1 and the school's teaching periods per day")
    min_gap = int(data.get("min_gap_days", 0))
    if min_gap < 0 or min_gap >= max(1, len([d for d in (school.working_days or "").split(",") if d.strip()])):
        raise HTTPException(status_code=422, detail="min_gap_days must be zero or less than the number of working days")


@app.get("/api/subject-profiles")
def get_subject_profiles(school_id: int, db: Session = Depends(get_db)):
    return db.query(m.SubjectSchedulingProfile).filter_by(school_id=school_id).order_by(m.SubjectSchedulingProfile.id).all()


@app.post("/api/subject-profiles")
def create_subject_profile(data: s.SubjectSchedulingProfileIn, db: Session = Depends(get_db)):
    payload = data.model_dump()
    _validate_preference_school_links(db, payload["school_id"], subject_id=payload["subject_id"])
    school = db.query(m.School).filter_by(id=payload["school_id"]).first()
    _validate_subject_profile_values(payload, school)
    existing = db.query(m.SubjectSchedulingProfile).filter_by(school_id=payload["school_id"], subject_id=payload["subject_id"]).first()
    if existing:
        raise HTTPException(status_code=409, detail="A scheduling profile already exists for this subject. Edit the existing profile.")
    obj = m.SubjectSchedulingProfile(**payload)
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@app.put("/api/subject-profiles/{record_id}")
def update_subject_profile(record_id: int, payload: dict, db: Session = Depends(get_db)):
    obj = db.query(m.SubjectSchedulingProfile).filter_by(id=record_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Subject scheduling profile not found")
    values = {key: value for key, value in payload.items() if key in {
        "subject_id", "difficulty", "preferred_time_of_day", "max_periods_per_day", "min_gap_days"
    }}
    subject_id = int(values.get("subject_id", obj.subject_id))
    _validate_preference_school_links(db, obj.school_id, subject_id=subject_id)
    school = db.query(m.School).filter_by(id=obj.school_id).first()
    candidate = {key: getattr(obj, key) for key in [
        "difficulty", "preferred_time_of_day", "max_periods_per_day", "min_gap_days"
    ]}
    candidate.update(values)
    _validate_subject_profile_values(candidate, school)
    duplicate = db.query(m.SubjectSchedulingProfile).filter(
        m.SubjectSchedulingProfile.school_id == obj.school_id,
        m.SubjectSchedulingProfile.subject_id == subject_id,
        m.SubjectSchedulingProfile.id != record_id,
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="A scheduling profile already exists for this subject")
    for key, value in values.items():
        setattr(obj, key, value)
    db.commit(); db.refresh(obj)
    return obj


@app.delete("/api/subject-profiles/{record_id}")
def delete_subject_profile(record_id: int, db: Session = Depends(get_db)):
    obj = db.query(m.SubjectSchedulingProfile).filter_by(id=record_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Subject scheduling profile not found")
    db.delete(obj); db.commit()
    return {"deleted": True, "id": record_id}


@app.get("/api/dashboard/{school_id}")
def dashboard(school_id: int, db: Session = Depends(get_db)):
    return {
        "classes": db.query(m.ClassSection).filter_by(school_id=school_id).count(),
        "teachers": db.query(m.Teacher).filter_by(school_id=school_id).count(),
        "subjects": db.query(m.Subject).filter_by(school_id=school_id).count(),
        "rooms": db.query(m.Room).filter_by(school_id=school_id).count(),
        "scheduled_periods": db.query(m.TimetableEntry).filter_by(school_id=school_id).count()
    }

@app.get("/api/generate/preflight/{school_id}")
def generate_preflight(school_id: int, db: Session = Depends(get_db)):
    """Check whether the school's saved data is ready for timetable generation."""
    school = db.query(m.School).filter_by(id=school_id).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    classes = db.query(m.ClassSection).filter_by(school_id=school_id).all()
    teachers = db.query(m.Teacher).filter_by(school_id=school_id).all()
    subjects = db.query(m.Subject).filter_by(school_id=school_id).all()
    rooms = db.query(m.Room).filter_by(school_id=school_id).all()
    days = [d.strip() for d in (school.working_days or "").split(",") if d.strip()]
    timings = db.query(m.PeriodTiming).filter_by(school_id=school_id).order_by(m.PeriodTiming.period_number).all()
    teaching_timing_count = sum(1 for row in timings if not row.is_break)
    periods_per_day = int(school.periods_per_day or 0)

    teacher_ids = {t.id for t in teachers}
    subject_ids = {s.id for s in subjects}
    mappings = db.query(m.TeacherSubject).filter(
        m.TeacherSubject.teacher_id.in_(teacher_ids or {-1}),
        m.TeacherSubject.subject_id.in_(subject_ids or {-1}),
    ).all()
    mapped_teacher_ids = {}
    for row in mappings:
        mapped_teacher_ids.setdefault(row.subject_id, set()).add(row.teacher_id)

    requirement_rows = db.query(m.WeeklyRequirement).filter_by(school_id=school_id).all()
    requirement_map = {
        (row.class_section_id, row.subject_id): max(0, int(row.periods_per_week or 0))
        for row in requirement_rows
    }
    total_required = 0
    class_overloads = []
    for cls in classes:
        class_required = 0
        for subject in subjects:
            required = requirement_map.get(
                (cls.id, subject.id), max(0, int(subject.weekly_periods or 0))
            )
            total_required += required
            class_required += required
        class_capacity = len(days) * periods_per_day
        if class_required > class_capacity:
            class_overloads.append({
                "class_id": cls.id,
                "class_name": cls.class_name,
                "section": cls.section,
                "required": class_required,
                "capacity": class_capacity,
            })

    missing_mappings = [
        {"subject_id": subject.id, "subject_name": subject.name,
         "weekly_periods": int(subject.weekly_periods or 0)}
        for subject in subjects if not mapped_teacher_ids.get(subject.id)
    ]
    room_capacity_gaps = []
    if rooms:
        for cls in classes:
            if not any(int(room.capacity or 0) >= int(cls.strength or 0) for room in rooms):
                room_capacity_gaps.append({
                    "class_id": cls.id, "class_name": cls.class_name,
                    "section": cls.section, "strength": int(cls.strength or 0),
                })

    locked = db.query(m.TimetableEntry).filter_by(school_id=school_id, locked=True).all()
    locked_class_slots = set()
    locked_teacher_slots = set()
    locked_room_slots = set()
    locked_conflicts = []
    for entry in locked:
        class_key = (entry.class_section_id, entry.day, entry.period)
        teacher_key = (entry.teacher_id, entry.day, entry.period)
        room_key = (entry.room_id, entry.day, entry.period) if entry.room_id else None
        if class_key in locked_class_slots:
            locked_conflicts.append({"type": "class", "entry_id": entry.id})
        if teacher_key in locked_teacher_slots:
            locked_conflicts.append({"type": "teacher", "entry_id": entry.id})
        if room_key and room_key in locked_room_slots:
            locked_conflicts.append({"type": "room", "entry_id": entry.id})
        if entry.day not in days or entry.period < 1 or entry.period > periods_per_day:
            locked_conflicts.append({"type": "outside_configured_slot", "entry_id": entry.id})
        locked_class_slots.add(class_key)
        locked_teacher_slots.add(teacher_key)
        if room_key:
            locked_room_slots.add(room_key)

    warnings = []
    if not classes:
        warnings.append("No classes are configured.")
    if not teachers:
        warnings.append("No teachers are configured.")
    if not subjects:
        warnings.append("No subjects are configured.")
    if not days or periods_per_day <= 0:
        warnings.append("Configure at least one working day and one teaching period per day.")
    if timings and teaching_timing_count != periods_per_day:
        warnings.append(
            f"School settings specify {periods_per_day} periods per day, but timings contain {teaching_timing_count} teaching periods."
        )
    if missing_mappings:
        warnings.append(f"{len(missing_mappings)} subject(s) have no qualified teacher mapping.")
    if class_overloads:
        warnings.append(f"{len(class_overloads)} class(es) require more periods than their weekly timetable capacity.")
    if room_capacity_gaps:
        warnings.append(f"{len(room_capacity_gaps)} class(es) have no configured room with sufficient capacity.")
    if locked_conflicts:
        warnings.append(f"{len(locked_conflicts)} conflict(s) already exist among locked timetable entries.")

    return {
        "school_id": school_id,
        "school_name": school.name,
        "ready": not warnings,
        "classes": len(classes),
        "teachers": len(teachers),
        "subjects": len(subjects),
        "rooms": len(rooms),
        "working_days": days,
        "periods_per_day": periods_per_day,
        "required_periods": total_required,
        "weekly_class_capacity": len(classes) * len(days) * periods_per_day,
        "mapped_subjects": len(subjects) - len(missing_mappings),
        "subjects_without_teacher_mapping": missing_mappings,
        "class_capacity_overloads": class_overloads,
        "classes_without_suitable_room": room_capacity_gaps,
        "locked_lessons": len(locked),
        "locked_conflicts": locked_conflicts,
        "warnings": warnings,
        "recommendation": "Resolve the warnings where possible, then run generation. The scheduler will still report any requirements it cannot place.",
    }


@app.post("/api/generate/{school_id}")
def generate(school_id: int, time_limit_seconds: int = 20, db: Session = Depends(get_db)):
    """Generate and persist a school-wide timetable with OR-Tools CP-SAT."""
    try:
        from .optimizer import solve_school_timetable
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="The CP-SAT optimization engine is not installed. Run: python -m pip install -r requirements.txt",
        ) from exc

    school = db.query(m.School).filter_by(id=school_id).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    classes = db.query(m.ClassSection).filter_by(school_id=school_id).all()
    teachers = db.query(m.Teacher).filter_by(school_id=school_id).all()
    subjects = db.query(m.Subject).filter_by(school_id=school_id).all()
    rooms = db.query(m.Room).filter_by(school_id=school_id).all()
    if not classes or not teachers or not subjects:
        raise HTTPException(status_code=400, detail="Add at least one class, teacher and subject before generating.")

    days = [part.strip() for part in (school.working_days or "").split(",") if part.strip()]
    if not days:
        raise HTTPException(status_code=400, detail="Configure at least one working day.")

    timing_rows = (
        db.query(m.PeriodTiming)
        .filter_by(school_id=school_id)
        .order_by(m.PeriodTiming.period_number)
        .all()
    )
    teaching_slots = [row for row in timing_rows if not row.is_break] if timing_rows else None
    if timing_rows:
        if len(teaching_slots) != int(school.periods_per_day or 0):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"School settings specify {school.periods_per_day} teaching periods, "
                    f"but {len(teaching_slots)} non-break periods are configured in timings. "
                    "Correct the timings before generating."
                ),
            )
        periods = list(range(1, len(teaching_slots) + 1))
        double_pairs = set()
        for index in range(len(timing_rows) - 1):
            first, second = timing_rows[index], timing_rows[index + 1]
            if not first.is_break and not second.is_break:
                first_period = sum(1 for row in timing_rows[:index + 1] if not row.is_break)
                double_pairs.add((first_period, first_period + 1))
    else:
        periods = list(range(1, int(school.periods_per_day or 0) + 1))
        double_pairs = {(period, period + 1) for period in periods[:-1]}
    if not periods:
        raise HTTPException(status_code=400, detail="No teaching periods are configured.")

    teacher_ids = [row.id for row in teachers]
    subject_ids = [row.id for row in subjects]
    mappings = db.query(m.TeacherSubject).filter(
        m.TeacherSubject.teacher_id.in_(teacher_ids),
        m.TeacherSubject.subject_id.in_(subject_ids),
    ).all()
    availability_rows = db.query(m.Availability).filter(
        m.Availability.teacher_id.in_(teacher_ids)
    ).all()
    requirements = db.query(m.WeeklyRequirement).filter_by(school_id=school_id).all()
    locked_entries = db.query(m.TimetableEntry).filter_by(
        school_id=school_id, locked=True
    ).all()
    teacher_preferences = db.query(m.TeacherPreference).filter_by(school_id=school_id).all()
    subject_profiles = db.query(m.SubjectSchedulingProfile).filter_by(school_id=school_id).all()

    result = solve_school_timetable(
        school_id=school_id,
        school=school,
        classes=classes,
        teachers=teachers,
        subjects=subjects,
        rooms=rooms,
        mappings=mappings,
        availability_rows=availability_rows,
        requirements=requirements,
        locked_entries=locked_entries,
        teacher_preferences=teacher_preferences,
        subject_profiles=subject_profiles,
        periods=periods,
        days=days,
        double_pairs=double_pairs,
        teaching_slots=teaching_slots,
        time_limit_seconds=time_limit_seconds,
    )
    if result.get("error"):
        if result.get("locked_conflicts"):
            raise HTTPException(
                status_code=409,
                detail={"message": result["error"], "locked_conflicts": result["locked_conflicts"]},
            )
        raise HTTPException(status_code=422, detail=result["error"])

    # Replace only unlocked lessons after a solver result exists. A database
    # failure rolls back the entire replacement; existing locked rows remain.
    new_entries = result.pop("new_entries")
    try:
        db.query(m.TimetableEntry).filter_by(school_id=school_id, locked=False).delete(
            synchronize_session=False
        )
        for item in new_entries:
            db.add(m.TimetableEntry(**item))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Timetable generation could not be saved. The previous timetable was retained.",
        ) from exc

    locked_count = len(locked_entries)
    result.update({
        "created": len(new_entries),
        "locked_preserved": locked_count,
        "locked_conflicts": [],
        "algorithm": "Google OR-Tools CP-SAT global constraint optimization",
        "optimization_attempts": 1,
        "optimization_score_periods": result["allocated_periods"],
        "time_limit_seconds": max(5, min(int(time_limit_seconds or 20), 60)),
        "constraints_enforced": [
            "exact class-subject weekly requirements when feasible",
            "one lesson per class slot",
            "one assignment per teacher slot",
            "teacher-subject qualification",
            "teacher working-day and explicit slot availability",
            "teacher weekly maximum periods",
            "room slot uniqueness and room capacity when rooms are configured",
            "locked lesson preservation and validation",
            "adjacent double-period blocks where required",
        ],
        "optimization_objectives": [
            "maximize the number of required periods allocated",
            "respect teacher and subject preferences",
            "minimize internal gaps between lessons for each class and day",
            "spread repeated subjects across working days",
            "balance daily class workload",
            "balance total teacher workload",
        ],
        "note": (
            "The timetable was optimized with CP-SAT. The allocation count is proven maximum for this run."
            if result.get("phase_one_status") == "OPTIMAL"
            else "A feasible CP-SAT timetable was found within the selected time limit; a longer run may improve the allocation or preferences."
        ),
    })
    return result


# Generic CRUD helpers for configuration modules
CONFIG_MODELS = {
    "schools": m.School,
    "classes": m.ClassSection,
    "teachers": m.Teacher,
    "subjects": m.Subject,
    "rooms": m.Room,
}

@app.put("/api/config/{kind}/{record_id}")
def update_config(kind: str, record_id: int, payload: dict, db: Session = Depends(get_db)):
    model = CONFIG_MODELS.get(kind)
    if model is None:
        raise HTTPException(404, "Unknown configuration module")
    obj = db.query(model).filter(model.id == record_id).first()
    if obj is None:
        raise HTTPException(404, "Record not found")
    allowed = {c.name for c in model.__table__.columns} - {"id"}
    for key, value in payload.items():
        if key in allowed:
            setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return {c.name: getattr(obj, c.name) for c in model.__table__.columns}

@app.delete("/api/config/{kind}/{record_id}")
def delete_config(kind: str, record_id: int, db: Session = Depends(get_db)):
    model = CONFIG_MODELS.get(kind)
    if model is None:
        raise HTTPException(404, "Unknown configuration module")
    obj = db.query(model).filter(model.id == record_id).first()
    if obj is None:
        raise HTTPException(404, "Record not found")
    db.delete(obj)
    db.commit()
    return {"deleted": True, "id": record_id}

@app.get("/api/config/{kind}")
def get_config(kind: str, school_id: int | None = None, db: Session = Depends(get_db)):
    model = CONFIG_MODELS.get(kind)
    if model is None:
        raise HTTPException(404, "Unknown configuration module")
    q = db.query(model)
    if school_id is not None and hasattr(model, "school_id"):
        q = q.filter(model.school_id == school_id)
    return [{c.name: getattr(obj, c.name) for c in model.__table__.columns} for obj in q.order_by(model.id.desc()).all()]


# Remaining PERIODLY configuration modules
from pydantic import BaseModel

class TimingPayload(BaseModel):
    school_id: int
    period_number: int
    label: str = ""
    start_time: str
    end_time: str
    is_break: bool = False

class RequirementPayload(BaseModel):
    school_id: int
    class_section_id: int
    subject_id: int
    periods_per_week: int

class MappingPayload(BaseModel):
    teacher_id: int
    subject_id: int

class AvailabilityPayload(BaseModel):
    teacher_id: int
    day: str
    period: int
    available: bool = True

@app.get("/api/timings")
def get_timings(school_id: int, db: Session = Depends(get_db)):
    return db.query(m.PeriodTiming).filter_by(school_id=school_id).order_by(m.PeriodTiming.period_number).all()

@app.post("/api/timings")
def create_timing(data: TimingPayload, db: Session = Depends(get_db)):
    school = db.query(m.School).filter_by(id=data.school_id).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    obj = m.PeriodTiming(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@app.post("/api/timings/generate-default/{school_id}")
def generate_default_timings(school_id: int, db: Session = Depends(get_db)):
    school = db.query(m.School).filter_by(id=school_id).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    if school.periods_per_day != 8:
        raise HTTPException(
            status_code=400,
            detail="This default schedule is designed for 8 teaching periods."
        )

    existing_count = (
        db.query(m.PeriodTiming)
        .filter_by(school_id=school_id)
        .count()
    )
    if existing_count:
        raise HTTPException(
            status_code=409,
            detail="Timings already exist for this school. Delete them before generating a new schedule."
        )

    schedule = [
        {"label": "Period 1", "start_time": "09:00", "end_time": "09:45", "is_break": False},
        {"label": "Period 2", "start_time": "09:45", "end_time": "10:30", "is_break": False},
        {"label": "Morning Break", "start_time": "10:30", "end_time": "10:45", "is_break": True},
        {"label": "Period 3", "start_time": "10:45", "end_time": "11:30", "is_break": False},
        {"label": "Period 4", "start_time": "11:30", "end_time": "12:15", "is_break": False},
        {"label": "Lunch Break", "start_time": "12:15", "end_time": "13:00", "is_break": True},
        {"label": "Period 5", "start_time": "13:00", "end_time": "13:45", "is_break": False},
        {"label": "Period 6", "start_time": "13:45", "end_time": "14:30", "is_break": False},
        {"label": "Period 7", "start_time": "14:30", "end_time": "15:15", "is_break": False},
        {"label": "Period 8", "start_time": "15:15", "end_time": "16:00", "is_break": False},
    ]

    created = []
    try:
        for slot_number, slot in enumerate(schedule, start=1):
            timing = m.PeriodTiming(
                school_id=school_id,
                period_number=slot_number,
                label=slot["label"],
                start_time=slot["start_time"],
                end_time=slot["end_time"],
                is_break=slot["is_break"],
            )
            db.add(timing)
            created.append(timing)

        db.commit()
        for timing in created:
            db.refresh(timing)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not generate period timings."
        )

    return {
        "message": "School timings generated successfully",
        "school_id": school_id,
        "total_slots": len(created),
        "teaching_periods": 8,
        "breaks": 2,
        "timings": created,
    }

@app.put("/api/timings/{record_id}")
def update_timing(record_id: int, data: TimingPayload, db: Session = Depends(get_db)):
    obj = db.query(m.PeriodTiming).filter_by(id=record_id).first()
    if not obj: raise HTTPException(404, "Timing not found")
    for k,v in data.model_dump().items(): setattr(obj,k,v)
    db.commit(); db.refresh(obj); return obj

@app.delete("/api/timings/{record_id}")
def delete_timing(record_id: int, db: Session = Depends(get_db)):
    obj = db.query(m.PeriodTiming).filter_by(id=record_id).first()
    if not obj: raise HTTPException(404, "Timing not found")
    db.delete(obj); db.commit(); return {"deleted":True}

@app.get("/api/requirements")
def get_requirements(school_id: int, db: Session = Depends(get_db)):
    return db.query(m.WeeklyRequirement).filter_by(school_id=school_id).all()

@app.post("/api/requirements")
def create_requirement(data: RequirementPayload, db: Session = Depends(get_db)):
    obj=m.WeeklyRequirement(**data.model_dump()); db.add(obj); db.commit(); db.refresh(obj); return obj

@app.put("/api/requirements/{record_id}")
def update_requirement(record_id: int, data: RequirementPayload, db: Session = Depends(get_db)):
    obj=db.query(m.WeeklyRequirement).filter_by(id=record_id).first()
    if not obj: raise HTTPException(404,"Requirement not found")
    for k,v in data.model_dump().items(): setattr(obj,k,v)
    db.commit(); db.refresh(obj); return obj

@app.delete("/api/requirements/{record_id}")
def delete_requirement(record_id: int, db: Session = Depends(get_db)):
    obj=db.query(m.WeeklyRequirement).filter_by(id=record_id).first()
    if not obj: raise HTTPException(404,"Requirement not found")
    db.delete(obj); db.commit(); return {"deleted":True}

@app.get("/api/mappings")
def get_mappings(school_id: int, db: Session = Depends(get_db)):
    return db.query(m.TeacherSubject).join(m.Teacher, m.TeacherSubject.teacher_id==m.Teacher.id).filter(m.Teacher.school_id==school_id).all()

@app.post("/api/mappings")
def create_mapping(data: MappingPayload, db: Session = Depends(get_db)):
    exists=db.query(m.TeacherSubject).filter_by(teacher_id=data.teacher_id,subject_id=data.subject_id).first()
    if exists: raise HTTPException(409,"This teacher-subject mapping already exists")
    obj=m.TeacherSubject(**data.model_dump()); db.add(obj); db.commit(); db.refresh(obj); return obj

@app.delete("/api/mappings/{record_id}")
def delete_mapping(record_id: int, db: Session = Depends(get_db)):
    obj=db.query(m.TeacherSubject).filter_by(id=record_id).first()
    if not obj: raise HTTPException(404,"Mapping not found")
    db.delete(obj); db.commit(); return {"deleted":True}

@app.get("/api/availability")
def get_availability(school_id: int, db: Session = Depends(get_db)):
    return db.query(m.Availability).join(m.Teacher, m.Availability.teacher_id==m.Teacher.id).filter(m.Teacher.school_id==school_id).all()

@app.post("/api/availability")
def create_availability(data: AvailabilityPayload, db: Session = Depends(get_db)):
    obj=m.Availability(**data.model_dump()); db.add(obj); db.commit(); db.refresh(obj); return obj

@app.put("/api/availability/{record_id}")
def update_availability(record_id: int, data: AvailabilityPayload, db: Session = Depends(get_db)):
    obj=db.query(m.Availability).filter_by(id=record_id).first()
    if not obj: raise HTTPException(404,"Availability record not found")
    for k,v in data.model_dump().items(): setattr(obj,k,v)
    db.commit(); db.refresh(obj); return obj

@app.delete("/api/availability/{record_id}")
def delete_availability(record_id: int, db: Session = Depends(get_db)):
    obj=db.query(m.Availability).filter_by(id=record_id).first()
    if not obj: raise HTTPException(404,"Availability record not found")
    db.delete(obj); db.commit(); return {"deleted":True}


# -----------------------------------------------------------------------------
# PERIODLY: Excel workbook export (no additional Python packages required)
# -----------------------------------------------------------------------------
def _xlsx_col_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_sheet_name(name: str, used: set[str]) -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]", " ", str(name or "Sheet"))
    cleaned = re.sub(r"\s+", " ", cleaned).strip() or "Sheet"
    cleaned = cleaned[:31]
    candidate = cleaned
    counter = 2
    while candidate.lower() in used:
        suffix = f" ({counter})"
        candidate = cleaned[:31 - len(suffix)] + suffix
        counter += 1
    used.add(candidate.lower())
    return candidate


def _xlsx_cell_xml(ref: str, value, style_id: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" s="{style_id}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style_id}"><v>{value}</v></c>'
    text = _xml_escape(str(value))
    return f'<c r="{ref}" s="{style_id}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _xlsx_worksheet_xml(rows: list[list], widths: list[float] | None = None, autofilter: bool = False) -> str:
    max_cols = max((len(row) for row in rows), default=1)
    max_rows = max(len(rows), 1)
    cols_xml = ""
    if widths:
        col_defs = []
        for index, width in enumerate(widths[:max_cols], start=1):
            col_defs.append(f'<col min="{index}" max="{index}" width="{max(8, min(float(width), 55)):.1f}" customWidth="1"/>')
        cols_xml = "<cols>" + "".join(col_defs) + "</cols>"
    row_xml = []
    for row_num, row in enumerate(rows, start=1):
        cells = []
        for col_num, value in enumerate(row, start=1):
            ref = f"{_xlsx_col_name(col_num)}{row_num}"
            style = 1 if row_num == 1 else 2
            cells.append(_xlsx_cell_xml(ref, value, style))
        row_attrs = ' ht="28" customHeight="1"' if row_num == 1 else ''
        row_xml.append(f'<row r="{row_num}"{row_attrs}>' + "".join(cells) + "</row>")
    dimension = f"A1:{_xlsx_col_name(max_cols)}{max_rows}"
    filter_xml = f'<autoFilter ref="A1:{_xlsx_col_name(max_cols)}{max_rows}"/>' if autofilter and max_rows > 1 else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        + cols_xml + '<sheetData>' + "".join(row_xml) + '</sheetData>' + filter_xml
        + '<pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
        '</worksheet>'
    )


def _build_periodly_xlsx(sheet_specs: list[tuple[str, list[list], list[float] | None, bool]]) -> bytes:
    used_names: set[str] = set()
    safe_specs = [(_xlsx_sheet_name(name, used_names), rows, widths, filter_enabled)
                  for name, rows, widths, filter_enabled in sheet_specs]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
''' + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(safe_specs)+1)) + '</Types>''')
        workbook.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        sheet_nodes = []
        relation_nodes = []
        for index, (name, rows, widths, filter_enabled) in enumerate(safe_specs, start=1):
            sheet_nodes.append(f'<sheet name="{_xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>')
            relation_nodes.append(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
            workbook.writestr(f"xl/worksheets/sheet{index}.xml", _xlsx_worksheet_xml(rows, widths, filter_enabled))
        style_id = len(safe_specs) + 1
        relation_nodes.append(f'<Relationship Id="rId{style_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        workbook.writestr("xl/workbook.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<bookViews><workbookView/></bookViews><sheets>''' + "".join(sheet_nodes) + '</sheets></workbook>''')
        workbook.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">''' + "".join(relation_nodes) + '</Relationships>''')
        workbook.writestr("xl/styles.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF173B83"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFD8E0EE"/></left><right style="thin"><color rgb="FFD8E0EE"/></right><top style="thin"><color rgb="FFD8E0EE"/></top><bottom style="thin"><color rgb="FFD8E0EE"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>''')
    return output.getvalue()


@app.get("/api/export/timetable.xlsx")
def export_timetable_excel(school_id: int, db: Session = Depends(get_db)):
    school = db.query(m.School).filter(m.School.id == school_id).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    classes = db.query(m.ClassSection).filter_by(school_id=school_id).order_by(m.ClassSection.class_name, m.ClassSection.section).all()
    teachers = db.query(m.Teacher).filter_by(school_id=school_id).order_by(m.Teacher.name).all()
    subjects = db.query(m.Subject).filter_by(school_id=school_id).order_by(m.Subject.name).all()
    rooms = db.query(m.Room).filter_by(school_id=school_id).order_by(m.Room.name).all()
    timings = db.query(m.PeriodTiming).filter_by(school_id=school_id).order_by(m.PeriodTiming.period_number).all()
    entries = db.query(m.TimetableEntry).filter_by(school_id=school_id).all()
    requirements = db.query(m.WeeklyRequirement).filter_by(school_id=school_id).all()

    class_by_id = {obj.id: obj for obj in classes}
    teacher_by_id = {obj.id: obj for obj in teachers}
    subject_by_id = {obj.id: obj for obj in subjects}
    room_by_id = {obj.id: obj for obj in rooms}
    school_days = [day.strip() for day in (school.working_days or "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday").split(",") if day.strip()]
    day_rank = {day: index for index, day in enumerate(school_days)}
    sorted_entries = sorted(entries, key=lambda row: (day_rank.get(row.day, 999), row.period, row.class_section_id))
    teaching_timings = [slot for slot in timings if not slot.is_break]
    time_by_period = {index + 1: slot for index, slot in enumerate(teaching_timings)}

    summary_rows = [
        ["PERIODLY — School Timetable Export", ""],
        ["School", school.name],
        ["Academic Year", school.academic_year or ""],
        ["School ID", school.id],
        ["Working Days", ", ".join(school_days)],
        ["Teaching Periods per Day", school.periods_per_day],
        ["Classes / Sections", len(classes)],
        ["Teachers", len(teachers)],
        ["Subjects", len(subjects)],
        ["Rooms", len(rooms)],
        ["Scheduled Lessons", len(entries)],
        ["Locked Lessons", sum(1 for row in entries if row.locked)],
        ["Exported At", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["Note", "Verify this timetable against school policy before publishing."],
    ]
    sheets = [("Summary", summary_rows, [28, 76], False)]

    master = [["Class", "Section", "Day", "Period", "Start Time", "End Time", "Subject", "Teacher", "Room", "Locked"]]
    for entry in sorted_entries:
        cls = class_by_id.get(entry.class_section_id)
        teacher = teacher_by_id.get(entry.teacher_id)
        subject = subject_by_id.get(entry.subject_id)
        room = room_by_id.get(entry.room_id) if entry.room_id else None
        timing = time_by_period.get(entry.period)
        master.append([
            cls.class_name if cls else "Unknown class", cls.section if cls else "",
            entry.day, entry.period, timing.start_time if timing else "", timing.end_time if timing else "",
            subject.name if subject else "Unknown subject", teacher.name if teacher else "Unknown teacher",
            room.name if room else "", "Yes" if entry.locked else "No"
        ])
    sheets.append(("Master Schedule", master, [18, 12, 15, 10, 13, 13, 22, 24, 18, 10], True))

    # Class-wise sheets: chronological rows include breaks from the school's configured timings.
    def slot_rows_for_class(class_obj):
        rows = [["Period / Time"] + school_days]
        lookup = {}
        for entry in entries:
            if entry.class_section_id == class_obj.id:
                lookup.setdefault((entry.day, entry.period), []).append(entry)
        logical_period = 0
        slots = timings or [None] * int(school.periods_per_day or 8)
        for slot in slots:
            if slot is not None and slot.is_break:
                label = f"{slot.label} ({slot.start_time}–{slot.end_time})"
                rows.append([label] + ["" for _ in school_days])
                continue
            logical_period += 1
            if logical_period > int(school.periods_per_day or 8):
                continue
            timing = time_by_period.get(logical_period)
            period_label = f"P{logical_period}"
            if timing:
                period_label += f" ({timing.start_time}–{timing.end_time})"
            cells = []
            for day in school_days:
                placed = lookup.get((day, logical_period), [])
                display = []
                for entry in placed:
                    subject = subject_by_id.get(entry.subject_id)
                    teacher = teacher_by_id.get(entry.teacher_id)
                    room = room_by_id.get(entry.room_id) if entry.room_id else None
                    text = (subject.name if subject else "Subject") + "\n" + (teacher.name if teacher else "Teacher")
                    if room:
                        text += "\nRoom: " + room.name
                    if entry.locked:
                        text += "\n[LOCKED]"
                    display.append(text)
                cells.append("\n\n".join(display))
            rows.append([period_label] + cells)
        return rows

    for cls in classes:
        label = f"Class {cls.class_name} {cls.section}"
        sheets.append((label, slot_rows_for_class(cls), [25] + [23] * len(school_days), False))

    # Teacher-wise sheets.
    def slot_rows_for_teacher(teacher_obj):
        rows = [["Period / Time"] + school_days]
        lookup = {}
        for entry in entries:
            if entry.teacher_id == teacher_obj.id:
                lookup.setdefault((entry.day, entry.period), []).append(entry)
        logical_period = 0
        slots = timings or [None] * int(school.periods_per_day or 8)
        for slot in slots:
            if slot is not None and slot.is_break:
                rows.append([f"{slot.label} ({slot.start_time}–{slot.end_time})"] + ["" for _ in school_days])
                continue
            logical_period += 1
            if logical_period > int(school.periods_per_day or 8):
                continue
            timing = time_by_period.get(logical_period)
            period_label = f"P{logical_period}" + (f" ({timing.start_time}–{timing.end_time})" if timing else "")
            cells = []
            for day in school_days:
                placed = lookup.get((day, logical_period), [])
                display = []
                for entry in placed:
                    cls = class_by_id.get(entry.class_section_id)
                    subject = subject_by_id.get(entry.subject_id)
                    room = room_by_id.get(entry.room_id) if entry.room_id else None
                    text = (cls.class_name if cls else "Class") + (f" - {cls.section}" if cls and cls.section else "")
                    text += "\n" + (subject.name if subject else "Subject")
                    if room:
                        text += "\nRoom: " + room.name
                    if entry.locked:
                        text += "\n[LOCKED]"
                    display.append(text)
                cells.append("\n\n".join(display))
            rows.append([period_label] + cells)
        return rows

    for teacher in teachers:
        sheets.append((f"Teacher {teacher.name}", slot_rows_for_teacher(teacher), [25] + [23] * len(school_days), False))

    workload = [["Teacher", "Scheduled Periods", "Maximum Weekly Periods", "Remaining Capacity", "Status"]]
    for teacher in teachers:
        assigned = sum(1 for entry in entries if entry.teacher_id == teacher.id)
        maximum = int(teacher.max_periods or 0)
        remaining = max(0, maximum - assigned) if maximum else ""
        status = "Over configured limit" if maximum and assigned > maximum else "Within configured limit"
        workload.append([teacher.name, assigned, maximum, remaining, status])
    sheets.append(("Teacher Workload", workload, [28, 20, 25, 22, 28], True))

    requirement_rows = [["Class", "Section", "Subject", "Required / Week", "Allocated", "Remaining"]]
    allocated_lookup = {}
    for entry in entries:
        key = (entry.class_section_id, entry.subject_id)
        allocated_lookup[key] = allocated_lookup.get(key, 0) + 1
    for req in requirements:
        cls = class_by_id.get(req.class_section_id)
        subject = subject_by_id.get(req.subject_id)
        allocated = allocated_lookup.get((req.class_section_id, req.subject_id), 0)
        required = int(req.periods_per_week or 0)
        requirement_rows.append([
            cls.class_name if cls else "Unknown class", cls.section if cls else "",
            subject.name if subject else "Unknown subject", required, allocated, max(0, required-allocated)
        ])
    sheets.append(("Weekly Requirements", requirement_rows, [18, 12, 24, 18, 14, 14], True))

    content = _build_periodly_xlsx(sheets)
    safe_school = re.sub(r"[^A-Za-z0-9_-]+", "_", school.name or "School").strip("_") or "School"
    filename = f"PERIODLY_{safe_school}_Timetable.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# Serve the single-page frontend from the same origin when the release package is
# run as a container. Local users may continue opening frontend/index.html directly.
_DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
_FRONTEND_DIR = Path(os.getenv("PERIODLY_FRONTEND_DIR", str(_DEFAULT_FRONTEND_DIR)))
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
