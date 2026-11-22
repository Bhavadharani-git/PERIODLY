from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from .database import Base

class School(Base):
    __tablename__ = "schools"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(160), nullable=False)
    academic_year = Column(String(30), default="2026-2027")
    working_days = Column(String(120), default="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday")
    periods_per_day = Column(Integer, default=8)

class ClassSection(Base):
    __tablename__ = "class_sections"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    class_name = Column(String(60), nullable=False)
    section = Column(String(30), default="A")
    strength = Column(Integer, default=0)

class Teacher(Base):
    __tablename__ = "teachers"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    name = Column(String(120), nullable=False)
    max_periods = Column(Integer, default=30)
    available_days = Column(String(200), default="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday")

class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    name = Column(String(100), nullable=False)
    code = Column(String(20), default="")
    weekly_periods = Column(Integer, default=5)
    requires_double = Column(Boolean, default=False)

class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    name = Column(String(80), nullable=False)
    capacity = Column(Integer, default=60)

class TeacherSubject(Base):
    __tablename__ = "teacher_subjects"
    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    __table_args__ = (UniqueConstraint("teacher_id", "subject_id"),)

class Availability(Base):
    __tablename__ = "availability"
    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)
    day = Column(String(20), nullable=False)
    period = Column(Integer, nullable=False)
    available = Column(Boolean, default=True)

class TimetableEntry(Base):
    __tablename__ = "timetable_entries"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    class_section_id = Column(Integer, ForeignKey("class_sections.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True)
    day = Column(String(20), nullable=False)
    period = Column(Integer, nullable=False)
    locked = Column(Boolean, default=False)


class PeriodTiming(Base):
    __tablename__ = "period_timings"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    period_number = Column(Integer, nullable=False)
    label = Column(String(60), default="")
    start_time = Column(String(10), nullable=False)
    end_time = Column(String(10), nullable=False)
    is_break = Column(Boolean, default=False)

class WeeklyRequirement(Base):
    __tablename__ = "weekly_requirements"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    class_section_id = Column(Integer, ForeignKey("class_sections.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    periods_per_week = Column(Integer, nullable=False, default=5)


class TeacherPreference(Base):
    """Soft scheduling preferences for a teacher; never override hard availability."""
    __tablename__ = "teacher_preferences"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)
    preferred_days = Column(String(200), default="")
    preferred_periods = Column(String(100), default="")
    preferred_time_of_day = Column(String(20), default="any")
    avoid_first_period = Column(Boolean, default=False)
    avoid_last_period = Column(Boolean, default=False)
    max_consecutive_periods = Column(Integer, default=4)
    __table_args__ = (UniqueConstraint("school_id", "teacher_id"),)


class SubjectSchedulingProfile(Base):
    """Soft scheduling profile for a subject (difficulty and distribution)."""
    __tablename__ = "subject_scheduling_profiles"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    difficulty = Column(Integer, default=3)
    preferred_time_of_day = Column(String(20), default="auto")
    max_periods_per_day = Column(Integer, default=2)
    min_gap_days = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("school_id", "subject_id"),)
