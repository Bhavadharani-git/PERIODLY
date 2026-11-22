from types import SimpleNamespace as NS

import pytest

pytest.importorskip("ortools")
from app.optimizer import solve_school_timetable


def row(**kwargs):
    return NS(**kwargs)


def test_two_class_solver_honours_locks_availability_rooms_and_preferences():
    school = row(id=1, name="Test School", working_days="Monday,Tuesday", periods_per_day=4)
    classes = [
        row(id=1, school_id=1, class_name="Class 1", section="A", strength=25),
        row(id=2, school_id=1, class_name="Class 1", section="B", strength=24),
    ]
    teachers = [
        row(id=1, school_id=1, name="Teacher A", max_periods=10, available_days="Monday,Tuesday"),
        row(id=2, school_id=1, name="Teacher B", max_periods=10, available_days="Monday,Tuesday"),
    ]
    subjects = [
        row(id=1, school_id=1, name="Mathematics", weekly_periods=4, requires_double=False),
        row(id=2, school_id=1, name="English", weekly_periods=4, requires_double=False),
    ]
    rooms = [
        row(id=1, school_id=1, name="Room A", capacity=30),
        row(id=2, school_id=1, name="Room B", capacity=30),
    ]
    # Both teachers are qualified for both subjects so the solver can route
    # around explicit unavailability while still enforcing teacher collisions.
    mappings = [
        row(teacher_id=1, subject_id=1), row(teacher_id=1, subject_id=2),
        row(teacher_id=2, subject_id=1), row(teacher_id=2, subject_id=2),
    ]
    requirements = [
        row(class_section_id=class_id, subject_id=subject_id, periods_per_week=3)
        for class_id in (1, 2) for subject_id in (1, 2)
    ]
    availability = [row(teacher_id=1, day="Tuesday", period=1, available=False)]
    locked = [row(
        id=1, school_id=1, class_section_id=1, teacher_id=1, subject_id=1,
        room_id=1, day="Monday", period=1, locked=True,
    )]
    teacher_preferences = [row(
        teacher_id=1, preferred_days="Monday", preferred_periods="1,2",
        preferred_time_of_day="morning", avoid_first_period=False,
        avoid_last_period=True, max_consecutive_periods=3,
    )]
    subject_profiles = [row(
        subject_id=1, difficulty=4, preferred_time_of_day="auto",
        max_periods_per_day=2, min_gap_days=0,
    )]

    result = solve_school_timetable(
        school_id=1, school=school, classes=classes, teachers=teachers,
        subjects=subjects, rooms=rooms, mappings=mappings,
        availability_rows=availability, requirements=requirements,
        locked_entries=locked, teacher_preferences=teacher_preferences,
        subject_profiles=subject_profiles, periods=[1, 2, 3, 4],
        days=["Monday", "Tuesday"],
        double_pairs={(1, 2), (2, 3), (3, 4)}, teaching_slots=None,
        time_limit_seconds=20,
    )

    assert "error" not in result
    assert result["required_periods"] == 12
    assert result["allocated_periods"] == 12
    assert result["unallocated_periods"] == 0
    assert result["internal_gaps"]["total"] == 0
    assert result["soft_preferences_applied"]["teacher_profiles"] == 1
    assert result["soft_preferences_applied"]["subject_profiles"] == 1

    all_rows = [
        {"class_section_id": 1, "teacher_id": 1, "room_id": 1, "day": "Monday", "period": 1},
        *result["new_entries"],
    ]
    class_slots = set()
    teacher_slots = set()
    room_slots = set()
    teacher_load = {1: 0, 2: 0}
    for item in all_rows:
        class_key = (item["class_section_id"], item["day"], item["period"])
        teacher_key = (item["teacher_id"], item["day"], item["period"])
        room_key = (item["room_id"], item["day"], item["period"])
        assert class_key not in class_slots
        assert teacher_key not in teacher_slots
        assert room_key not in room_slots
        class_slots.add(class_key)
        teacher_slots.add(teacher_key)
        room_slots.add(room_key)
        teacher_load[item["teacher_id"]] += 1
        assert item["room_id"] in (1, 2)
        assert not (item["teacher_id"] == 1 and item["day"] == "Tuesday" and item["period"] == 1)

    assert len(all_rows) == 12
    assert all(load <= 10 for load in teacher_load.values())


def test_required_double_period_is_consecutive():
    school = row(id=1, name="Double Period School", working_days="Monday,Tuesday", periods_per_day=4)
    classes = [row(id=1, school_id=1, class_name="Class 1", section="A", strength=20)]
    teachers = [row(id=1, school_id=1, name="Science Teacher", max_periods=10, available_days="Monday,Tuesday")]
    subjects = [row(id=1, school_id=1, name="Science", weekly_periods=2, requires_double=True)]

    result = solve_school_timetable(
        school_id=1, school=school, classes=classes, teachers=teachers,
        subjects=subjects, rooms=[], mappings=[row(teacher_id=1, subject_id=1)],
        availability_rows=[], requirements=[row(class_section_id=1, subject_id=1, periods_per_week=2)],
        locked_entries=[], teacher_preferences=[], subject_profiles=[],
        periods=[1, 2, 3, 4], days=["Monday", "Tuesday"],
        double_pairs={(1, 2), (2, 3), (3, 4)}, teaching_slots=None,
        time_limit_seconds=15,
    )

    assert "error" not in result
    assert result["required_periods"] == 2
    assert result["allocated_periods"] == 2
    assert len(result["new_entries"]) == 2
    selected = sorted(result["new_entries"], key=lambda item: item["period"])
    assert selected[0]["day"] == selected[1]["day"]
    assert selected[1]["period"] == selected[0]["period"] + 1
