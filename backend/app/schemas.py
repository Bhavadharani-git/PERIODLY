from pydantic import BaseModel, ConfigDict
from typing import Optional

class SchoolIn(BaseModel):
    name: str
    academic_year: str = "2026-2027"
    working_days: str = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
    periods_per_day: int = 8

class ClassIn(BaseModel):
    school_id: int
    class_name: str
    section: str = "A"
    strength: int = 0

class TeacherIn(BaseModel):
    school_id: int
    name: str
    max_periods: int = 30
    available_days: str = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"

class SubjectIn(BaseModel):
    school_id: int
    name: str
    code: str = ""
    weekly_periods: int = 5
    requires_double: bool = False

class RoomIn(BaseModel):
    school_id: int
    name: str
    capacity: int = 60

class MappingIn(BaseModel):
    teacher_id: int
    subject_id: int

class AvailabilityIn(BaseModel):
    teacher_id: int
    day: str
    period: int
    available: bool = True

class EntryCreate(BaseModel):
    school_id: int
    class_section_id: int
    teacher_id: int
    subject_id: int
    room_id: Optional[int] = None
    day: str
    period: int
    locked: bool = False

class EntryUpdate(BaseModel):
    teacher_id: Optional[int] = None
    subject_id: Optional[int] = None
    room_id: Optional[int] = None
    day: Optional[str] = None
    period: Optional[int] = None
    locked: Optional[bool] = None


class TeacherPreferenceIn(BaseModel):
    school_id: int
    teacher_id: int
    preferred_days: str = ""
    preferred_periods: str = ""
    preferred_time_of_day: str = "any"
    avoid_first_period: bool = False
    avoid_last_period: bool = False
    max_consecutive_periods: int = 4

class SubjectSchedulingProfileIn(BaseModel):
    school_id: int
    subject_id: int
    difficulty: int = 3
    preferred_time_of_day: str = "auto"
    max_periods_per_day: int = 2
    min_gap_days: int = 0
