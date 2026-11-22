"""Global school timetable optimization with Google OR-Tools CP-SAT.

Hard rules are model constraints. Preferences are objective penalties and can
never cause the solver to violate a hard rule.
"""
from __future__ import annotations

from collections import defaultdict
from math import ceil
from time import monotonic
from typing import Any

from ortools.sat.python import cp_model


def _csv_set(value: str | None) -> set[str]:
    return {part.strip() for part in (value or "").split(",") if part.strip()}


def _time_band(period: int, teaching_slots: list[Any] | None, period_count: int) -> str:
    if teaching_slots and 0 < period <= len(teaching_slots):
        value = getattr(teaching_slots[period - 1], "start_time", None)
        if value:
            try:
                hour, minute = (int(piece) for piece in value.split(":", 1))
                if hour < 11 or (hour == 11 and minute < 30):
                    return "morning"
                if hour < 13:
                    return "midday"
                return "afternoon"
            except (TypeError, ValueError):
                pass
    ratio = (period - 1) / max(1, period_count)
    return "morning" if ratio < 1 / 3 else "midday" if ratio < 2 / 3 else "afternoon"


def _status_name(status: int) -> str:
    return {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }.get(status, f"STATUS_{status}")


def solve_school_timetable(
    *,
    school_id: int,
    school: Any,
    classes: list[Any],
    teachers: list[Any],
    subjects: list[Any],
    rooms: list[Any],
    mappings: list[Any],
    availability_rows: list[Any],
    requirements: list[Any],
    locked_entries: list[Any],
    teacher_preferences: list[Any],
    subject_profiles: list[Any],
    periods: list[int],
    days: list[str],
    double_pairs: set[tuple[int, int]],
    teaching_slots: list[Any] | None,
    time_limit_seconds: int = 20,
) -> dict[str, Any]:
    """Return an optimized list of new timetable rows and solver diagnostics."""
    model = cp_model.CpModel()
    class_by_id = {row.id: row for row in classes}
    teacher_by_id = {row.id: row for row in teachers}
    subject_by_id = {row.id: row for row in subjects}
    room_by_id = {row.id: row for row in rooms}
    eligible: dict[int, set[int]] = defaultdict(set)
    for row in mappings:
        if row.teacher_id in teacher_by_id and row.subject_id in subject_by_id:
            eligible[row.subject_id].add(row.teacher_id)

    availability = {
        (row.teacher_id, row.day, int(row.period)): bool(row.available)
        for row in availability_rows
    }
    teacher_pref = {row.teacher_id: row for row in teacher_preferences}
    subject_profile = {row.subject_id: row for row in subject_profiles}
    requirement_map = {
        (row.class_section_id, row.subject_id): max(0, int(row.periods_per_week or 0))
        for row in requirements
    }

    # Existing locked rows are fixed inputs. Reject inconsistent locks rather
    # than silently saving a plan that already violates a hard rule.
    conflicts: list[dict[str, Any]] = []
    locked_class_slots: set[tuple[int, str, int]] = set()
    locked_teacher_slots: set[tuple[int, str, int]] = set()
    locked_room_slots: set[tuple[int, str, int]] = set()
    locked_counts: dict[tuple[int, int], int] = defaultdict(int)
    locked_teacher_load: dict[int, int] = defaultdict(int)
    locked_class_day: dict[tuple[int, str], int] = defaultdict(int)
    locked_subject_day: dict[tuple[int, int, str], int] = defaultdict(int)
    locked_total = 0

    for entry in locked_entries:
        class_obj = class_by_id.get(entry.class_section_id)
        teacher = teacher_by_id.get(entry.teacher_id)
        subject = subject_by_id.get(entry.subject_id)
        if not class_obj or not teacher or not subject:
            conflicts.append({"type": "invalid_school_reference", "entry_id": entry.id})
            continue
        class_key = (entry.class_section_id, entry.day, int(entry.period))
        teacher_key = (entry.teacher_id, entry.day, int(entry.period))
        room_key = (entry.room_id, entry.day, int(entry.period)) if entry.room_id else None
        if class_key in locked_class_slots:
            conflicts.append({"type": "class_slot", "entry_id": entry.id})
        if teacher_key in locked_teacher_slots:
            conflicts.append({"type": "teacher_slot", "entry_id": entry.id})
        if room_key and room_key in locked_room_slots:
            conflicts.append({"type": "room_slot", "entry_id": entry.id})
        if entry.day not in days or int(entry.period) not in periods:
            conflicts.append({"type": "outside_configured_slot", "entry_id": entry.id})
        if entry.teacher_id not in eligible.get(entry.subject_id, set()):
            conflicts.append({"type": "teacher_not_qualified", "entry_id": entry.id})
        day_list = _csv_set(teacher.available_days)
        explicit = availability.get((entry.teacher_id, entry.day, int(entry.period)))
        if explicit is False or (explicit is None and day_list and entry.day not in day_list):
            conflicts.append({"type": "teacher_unavailable", "entry_id": entry.id})
        if entry.room_id:
            room = room_by_id.get(entry.room_id)
            if not room or int(room.capacity or 0) < int(class_obj.strength or 0):
                conflicts.append({"type": "invalid_or_small_room", "entry_id": entry.id})
        locked_class_slots.add(class_key)
        locked_teacher_slots.add(teacher_key)
        if room_key:
            locked_room_slots.add(room_key)
        locked_counts[(entry.class_section_id, entry.subject_id)] += 1
        locked_teacher_load[entry.teacher_id] += 1
        locked_class_day[(entry.class_section_id, entry.day)] += 1
        locked_subject_day[(entry.class_section_id, entry.subject_id, entry.day)] += 1
        locked_total += 1

    for teacher in teachers:
        teacher_max = int(teacher.max_periods) if teacher.max_periods is not None else 10**9
        if locked_teacher_load[teacher.id] > teacher_max:
            conflicts.append({"type": "locked_teacher_over_weekly_max", "teacher_id": teacher.id})
    for cls in classes:
        for subject in subjects:
            required = requirement_map.get((cls.id, subject.id), max(0, int(subject.weekly_periods or 0)))
            if locked_counts[(cls.id, subject.id)] > required:
                conflicts.append({
                    "type": "locked_subject_over_requirement",
                    "class_id": cls.id,
                    "subject_id": subject.id,
                    "locked": locked_counts[(cls.id, subject.id)],
                    "required": required,
                })
    if conflicts:
        return {"error": "Locked lessons contain hard-constraint conflicts.", "locked_conflicts": conflicts}

    # Split remaining requirements into double-period blocks and single periods.
    tasks: list[dict[str, int]] = []
    requirements_info: list[dict[str, Any]] = []
    total_required = 0
    for cls in classes:
        class_required = 0
        for subject in subjects:
            required = requirement_map.get((cls.id, subject.id), max(0, int(subject.weekly_periods or 0)))
            already = locked_counts[(cls.id, subject.id)]
            remaining = max(0, required - already)
            total_required += required
            class_required += required
            requirements_info.append({
                "class_id": cls.id, "class_name": cls.class_name,
                "section": cls.section, "subject_id": subject.id,
                "subject_name": subject.name, "required": required,
            })
            block_size = 2 if subject.requires_double else 1
            if block_size == 2:
                while remaining >= 2:
                    tasks.append({"class_id": cls.id, "subject_id": subject.id, "size": 2})
                    remaining -= 2
            while remaining > 0:
                tasks.append({"class_id": cls.id, "subject_id": subject.id, "size": 1})
                remaining -= 1

    # Candidate placement variables. Every variable represents either one
    # lesson period or a consecutive double-period block with the same teacher
    # and room. A task is optional so the model can explain impossible demand.
    x_by_task: dict[int, list[Any]] = defaultdict(list)
    options_by_task_day: dict[tuple[int, str], list[Any]] = defaultdict(list)
    class_slot_vars: dict[tuple[int, str, int], list[Any]] = defaultdict(list)
    teacher_slot_vars: dict[tuple[int, str, int], list[Any]] = defaultdict(list)
    room_slot_vars: dict[tuple[int, str, int], list[Any]] = defaultdict(list)
    subject_day_terms: dict[tuple[int, int, str], list[tuple[Any, int]]] = defaultdict(list)
    class_day_terms: dict[tuple[int, str], list[tuple[Any, int]]] = defaultdict(list)
    teacher_load_terms: dict[int, list[tuple[Any, int]]] = defaultdict(list)
    task_candidates: dict[int, int] = defaultdict(int)
    preference_terms: list[Any] = []
    task_days: dict[int, dict[str, Any]] = {}
    option_meta: dict[str, dict[str, Any]] = {}

    first_period = min(periods)
    last_period = max(periods)
    candidate_id = 0
    for task_id, task in enumerate(tasks):
        cls = class_by_id[task["class_id"]]
        subject = subject_by_id[task["subject_id"]]
        size = task["size"]
        subject_teachers = sorted(eligible.get(subject.id, set()))
        if size == 2:
            starts = [p1 for p1, p2 in sorted(double_pairs) if p2 == p1 + 1]
        else:
            starts = list(periods)
        for day in days:
            for start in starts:
                block = [start] if size == 1 else [start, start + 1]
                if any((cls.id, day, p) in locked_class_slots for p in block):
                    continue
                for teacher_id in subject_teachers:
                    teacher = teacher_by_id[teacher_id]
                    day_set = _csv_set(teacher.available_days)
                    feasible = True
                    for period in block:
                        explicit = availability.get((teacher_id, day, period))
                        if explicit is False or (explicit is None and day_set and day not in day_set):
                            feasible = False
                            break
                        if (teacher_id, day, period) in locked_teacher_slots:
                            feasible = False
                            break
                    if not feasible:
                        continue
                    if rooms:
                        candidate_rooms = [r for r in rooms if int(r.capacity or 0) >= int(cls.strength or 0)]
                    else:
                        candidate_rooms = [None]
                    for room in candidate_rooms:
                        if room and any((room.id, day, p) in locked_room_slots for p in block):
                            continue
                        variable = model.NewBoolVar(f"x_{candidate_id}")
                        candidate_id += 1
                        option_meta[variable.Name()] = {
                            "class_id": cls.id, "subject_id": subject.id,
                            "teacher_id": teacher_id, "room_id": room.id if room else None,
                            "day": day, "periods": list(block),
                        }
                        x_by_task[task_id].append(variable)
                        options_by_task_day[(task_id, day)].append(variable)
                        task_candidates[task_id] += 1
                        for period in block:
                            class_slot_vars[(cls.id, day, period)].append(variable)
                            teacher_slot_vars[(teacher_id, day, period)].append(variable)
                            if room:
                                room_slot_vars[(room.id, day, period)].append(variable)
                        subject_day_terms[(cls.id, subject.id, day)].append((variable, size))
                        class_day_terms[(cls.id, day)].append((variable, size))
                        teacher_load_terms[teacher_id].append((variable, size))

                        # User-configured soft preferences.
                        cost = 0
                        pref = teacher_pref.get(teacher_id)
                        if pref:
                            wanted_days = _csv_set(pref.preferred_days)
                            wanted_periods = set()
                            for value in (pref.preferred_periods or "").split(","):
                                try:
                                    if value.strip(): wanted_periods.add(int(value.strip()))
                                except ValueError:
                                    pass
                            wanted_band = (pref.preferred_time_of_day or "any").lower()
                            if wanted_days and day not in wanted_days:
                                cost += 18
                            for period in block:
                                if wanted_periods and period not in wanted_periods:
                                    cost += 10
                                if wanted_band != "any" and _time_band(period, teaching_slots, len(periods)) != wanted_band:
                                    cost += 12
                                if pref.avoid_first_period and period == first_period:
                                    cost += 9
                                if pref.avoid_last_period and period == last_period:
                                    cost += 9
                        profile = subject_profile.get(subject.id)
                        if profile:
                            difficulty = max(1, min(5, int(profile.difficulty or 3)))
                            wanted_band = (profile.preferred_time_of_day or "auto").lower()
                            if wanted_band == "auto":
                                wanted_band = "morning" if difficulty >= 4 else "any" if difficulty <= 2 else "midday"
                            for period in block:
                                actual_band = _time_band(period, teaching_slots, len(periods))
                                if wanted_band != "any" and actual_band != wanted_band:
                                    cost += 8 + difficulty * 2
                                elif wanted_band == "any" and difficulty >= 4 and actual_band == "afternoon":
                                    cost += (difficulty - 3) * 3
                        if cost:
                            preference_terms.append(variable * int(cost))

    # Every block is placed no more than once.
    for task_id in range(len(tasks)):
        vars_for_task = x_by_task.get(task_id, [])
        if vars_for_task:
            model.Add(sum(vars_for_task) <= 1)

    # No class, teacher or room can be in two places at the same time.
    for variables in class_slot_vars.values():
        model.Add(sum(variables) <= 1)
    for variables in teacher_slot_vars.values():
        model.Add(sum(variables) <= 1)
    for variables in room_slot_vars.values():
        model.Add(sum(variables) <= 1)

    # Teacher weekly maxima include already locked lessons.
    for teacher in teachers:
        terms = [var * size for var, size in teacher_load_terms.get(teacher.id, [])]
        teacher_max = int(teacher.max_periods) if teacher.max_periods is not None else total_required
        remaining_capacity = teacher_max - locked_teacher_load[teacher.id]
        model.Add(sum(terms) <= max(0, remaining_capacity))

    # Softly distribute each subject through the week. If no profile exists,
    # default to one lesson per day as a preference, not a hard restriction.
    soft_terms: list[Any] = list(preference_terms)

    # Smart Gap Minimization: within each class/day, penalize an empty teaching
    # period only when there is at least one lesson before it and one after it.
    # This keeps the first/last lesson flexible while preferring compact blocks.
    # Breaks are already excluded from `periods`, so only teaching periods are
    # considered. This is a soft objective and never overrides hard constraints.
    internal_gap_terms: list[Any] = []
    for cls in classes:
        for day_index, day in enumerate(days):
            occupied_by_period: dict[int, Any] = {}
            for period in periods:
                occupied = model.NewBoolVar(
                    f"class_{cls.id}_{day_index}_occupied_{period}"
                )
                new_vars = class_slot_vars.get((cls.id, day, period), [])
                fixed = 1 if (cls.id, day, period) in locked_class_slots else 0
                model.Add(occupied == fixed + sum(new_vars))
                occupied_by_period[period] = occupied

            for index in range(1, len(periods) - 1):
                period = periods[index]
                has_lesson_before = model.NewBoolVar(
                    f"class_{cls.id}_{day_index}_has_before_{period}"
                )
                has_lesson_after = model.NewBoolVar(
                    f"class_{cls.id}_{day_index}_has_after_{period}"
                )
                model.AddMaxEquality(
                    has_lesson_before, [occupied_by_period[p] for p in periods[:index]]
                )
                model.AddMaxEquality(
                    has_lesson_after, [occupied_by_period[p] for p in periods[index + 1:]]
                )

                is_internal_gap = model.NewBoolVar(
                    f"class_{cls.id}_{day_index}_internal_gap_{period}"
                )
                current_occupied = occupied_by_period[period]
                model.Add(is_internal_gap <= has_lesson_before)
                model.Add(is_internal_gap <= has_lesson_after)
                model.Add(is_internal_gap + current_occupied <= 1)
                model.Add(
                    is_internal_gap
                    >= has_lesson_before + has_lesson_after - current_occupied - 1
                )
                internal_gap_terms.append(is_internal_gap)
                soft_terms.append(is_internal_gap * 35)

    for cls in classes:
        for subject in subjects:
            profile = subject_profile.get(subject.id)
            max_per_day = max(1, int(profile.max_periods_per_day or 2)) if profile else 1
            for day in days:
                terms = [var * size for var, size in subject_day_terms.get((cls.id, subject.id, day), [])]
                locked = locked_subject_day[(cls.id, subject.id, day)]
                excess = model.NewIntVar(0, max(0, total_required), f"sub_excess_{cls.id}_{subject.id}_{days.index(day)}")
                model.Add(excess >= locked + sum(terms) - max_per_day)
                soft_terms.append(excess * (24 if profile else 100))

    # Balance class daily workload around the average required load.
    for cls in classes:
        required_for_class = sum(
            requirement_map.get((cls.id, subject.id), max(0, int(subject.weekly_periods or 0)))
            for subject in subjects
        )
        daily_target = ceil(required_for_class / max(1, len(days)))
        for day_index, day in enumerate(days):
            terms = [var * size for var, size in class_day_terms.get((cls.id, day), [])]
            excess = model.NewIntVar(0, max(0, total_required), f"class_day_excess_{cls.id}_{day_index}")
            model.Add(excess >= locked_class_day[(cls.id, day)] + sum(terms) - daily_target)
            soft_terms.append(excess * 5)

    # Soft minimum-gap preference between repeated instances of the same subject.
    tasks_by_requirement: dict[tuple[int, int], list[int]] = defaultdict(list)
    for task_id, task in enumerate(tasks):
        tasks_by_requirement[(task["class_id"], task["subject_id"])].append(task_id)
    for (class_id, subject_id), task_ids in tasks_by_requirement.items():
        profile = subject_profile.get(subject_id)
        min_gap = max(0, int(profile.min_gap_days or 0)) if profile else 0
        if min_gap <= 0:
            continue
        for task_id in task_ids:
            day_vars: dict[str, Any] = {}
            for day in days:
                day_options = options_by_task_day.get((task_id, day), [])
                if not day_options:
                    continue
                day_var = model.NewBoolVar(f"task_{task_id}_day_{days.index(day)}")
                model.Add(day_var == sum(day_options))
                day_vars[day] = day_var
            task_days[task_id] = day_vars
        for i, first_task in enumerate(task_ids):
            for second_task in task_ids[i + 1:]:
                first_map = task_days.get(first_task, {})
                second_map = task_days.get(second_task, {})
                for day_a, var_a in first_map.items():
                    for day_b, var_b in second_map.items():
                        distance = abs(days.index(day_a) - days.index(day_b))
                        if 0 < distance <= min_gap:
                            together = model.NewBoolVar(f"gap_pair_{first_task}_{second_task}_{days.index(day_a)}_{days.index(day_b)}")
                            model.AddBoolAnd([var_a, var_b]).OnlyEnforceIf(together)
                            model.AddBoolOr([var_a.Not(), var_b.Not(), together])
                            soft_terms.append(together * ((min_gap - distance + 1) * 14))

    # Penalize teacher runs that exceed their preferred consecutive-period cap.
    for teacher in teachers:
        pref = teacher_pref.get(teacher.id)
        if not pref:
            continue
        max_run = max(1, int(pref.max_consecutive_periods or 4))
        for day_index, day in enumerate(days):
            occupied_vars: dict[int, Any] = {}
            for period in periods:
                occupied = model.NewBoolVar(f"teacher_{teacher.id}_{day_index}_period_{period}")
                new_vars = teacher_slot_vars.get((teacher.id, day, period), [])
                fixed = 1 if (teacher.id, day, period) in locked_teacher_slots else 0
                model.Add(occupied == fixed + sum(new_vars))
                occupied_vars[period] = occupied
            window_size = max_run + 1
            for offset in range(max(0, len(periods) - window_size + 1)):
                window = periods[offset:offset + window_size]
                if len(window) != window_size:
                    continue
                if any((window[k], window[k + 1]) not in double_pairs for k in range(len(window) - 1)):
                    continue
                excess = model.NewIntVar(0, window_size, f"teacher_run_excess_{teacher.id}_{day_index}_{offset}")
                model.Add(excess >= sum(occupied_vars[p] for p in window) - max_run)
                soft_terms.append(excess * 8)

    # Balance total teacher workload. This is a soft objective so qualifications
    # and maximum workloads remain the controlling hard limits.
    total_allocated_expr = locked_total + sum(
        var * size
        for values in teacher_load_terms.values()
        for var, size in values
    )
    teacher_count = max(1, len(teachers))
    for teacher in teachers:
        workload = locked_teacher_load[teacher.id] + sum(
            var * size for var, size in teacher_load_terms.get(teacher.id, [])
        )
        deviation = model.NewIntVar(0, max(1, total_required * teacher_count), f"teacher_load_deviation_{teacher.id}")
        model.AddAbsEquality(deviation, teacher_count * workload - total_allocated_expr)
        soft_terms.append(deviation)

    allocated_period_terms = [
        var * tasks[task_id]["size"]
        for task_id, variables in x_by_task.items()
        for var in variables
    ]
    allocated_new_periods = model.NewIntVar(0, max(0, total_required), "allocated_new_periods")
    model.Add(allocated_new_periods == sum(allocated_period_terms))

    # Phase 1 maximizes the number of periods allocated. Phase 2 holds that
    # maximum fixed and optimizes the soft scheduling preferences.
    total_time = max(5, min(int(time_limit_seconds or 20), 60))
    phase_one = cp_model.CpSolver()
    phase_one.parameters.max_time_in_seconds = max(2.0, total_time * 0.45)
    phase_one.parameters.num_search_workers = 8
    phase_one.parameters.random_seed = 42
    started = monotonic()
    model.Maximize(allocated_new_periods)
    phase_one_status = phase_one.Solve(model)
    if phase_one_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        chosen_solver = phase_one
    else:
        return {"error": "The CP-SAT engine could not find a feasible solution.", "solver_status": _status_name(phase_one_status)}

    best_allocated = int(round(phase_one.Value(allocated_new_periods)))
    model.Add(allocated_new_periods == best_allocated)
    model.Minimize(sum(soft_terms) if soft_terms else 0)
    phase_two = cp_model.CpSolver()
    elapsed = monotonic() - started
    phase_two.parameters.max_time_in_seconds = max(1.0, total_time - elapsed)
    phase_two.parameters.num_search_workers = 8
    phase_two.parameters.random_seed = 42
    phase_two_status = phase_two.Solve(model)
    if phase_two_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        chosen_solver = phase_two
    else:
        chosen_solver = phase_one

    new_entries: list[dict[str, Any]] = []
    for task_id, variables in x_by_task.items():
        selected = next((var for var in variables if chosen_solver.Value(var)), None)
        if selected is None:
            continue
        # Reverse-map the chosen bool to its candidate metadata.
        selected_name = selected.Name()
        chosen_meta = option_meta[selected_name]
        for period in chosen_meta["periods"]:
            new_entries.append({
                "school_id": school_id,
                "class_section_id": chosen_meta["class_id"],
                "teacher_id": chosen_meta["teacher_id"],
                "subject_id": chosen_meta["subject_id"],
                "room_id": chosen_meta["room_id"],
                "day": chosen_meta["day"],
                "period": period,
                "locked": False,
            })

    allocated_counts = dict(locked_counts)
    occupied_by_class_day: dict[tuple[int, str], set[int]] = defaultdict(set)
    for class_id, day, period in locked_class_slots:
        occupied_by_class_day[(class_id, day)].add(period)
    for row in new_entries:
        key = (row["class_section_id"], row["subject_id"])
        allocated_counts[key] = allocated_counts.get(key, 0) + 1
        occupied_by_class_day[(row["class_section_id"], row["day"])].add(
            int(row["period"])
        )

    internal_gap_details: list[dict[str, Any]] = []
    total_internal_gaps = 0
    for cls in classes:
        for day in days:
            occupied = occupied_by_class_day.get((cls.id, day), set())
            if len(occupied) < 2:
                continue
            first, last = min(occupied), max(occupied)
            gap_periods = [period for period in periods if first < period < last and period not in occupied]
            if gap_periods:
                total_internal_gaps += len(gap_periods)
                internal_gap_details.append({
                    "class_id": cls.id,
                    "class_name": cls.class_name,
                    "section": cls.section,
                    "day": day,
                    "gap_periods": gap_periods,
                    "gap_count": len(gap_periods),
                })

    unallocated = []
    for info in requirements_info:
        key = (info["class_id"], info["subject_id"])
        allocated = allocated_counts.get(key, 0)
        missing = max(0, info["required"] - allocated)
        if not missing:
            continue
        subject = subject_by_id[info["subject_id"]]
        if not eligible.get(subject.id):
            reason = "No teacher is mapped to this subject. Add a teacher-subject mapping."
        elif rooms and not any(int(room.capacity or 0) >= int(class_by_id[info["class_id"]].strength or 0) for room in rooms):
            reason = "No configured room has sufficient capacity for this class."
        elif subject.requires_double and not double_pairs:
            reason = "This subject requires consecutive double periods, but no adjacent teaching-period pair is configured."
        elif not any(task_candidates.get(task_id, 0) for task_id, task in enumerate(tasks) if task["class_id"] == info["class_id"] and task["subject_id"] == info["subject_id"]):
            reason = "No qualified teacher has an available slot that satisfies the configured hard constraints."
        else:
            reason = "Other class, teacher, room or weekly-workload constraints prevented all required periods from being placed."
        unallocated.append({
            "class_id": info["class_id"], "class_name": info["class_name"],
            "section": info["section"], "subject_id": info["subject_id"],
            "subject_name": info["subject_name"], "required": info["required"],
            "allocated": allocated, "remaining": missing, "reason": reason,
        })

    optimal = phase_one_status == cp_model.OPTIMAL and phase_two_status == cp_model.OPTIMAL
    overall_status = "OPTIMAL" if optimal else "FEASIBLE"
    return {
        "new_entries": sorted(new_entries, key=lambda row: (row["day"], row["period"], row["class_section_id"])),
        "unallocated": unallocated,
        "required_periods": total_required,
        "allocated_periods": sum(min(info["required"], allocated_counts.get((info["class_id"], info["subject_id"]), 0)) for info in requirements_info),
        "unallocated_periods": sum(item["remaining"] for item in unallocated),
        "solver_status": overall_status,
        "phase_one_status": _status_name(phase_one_status),
        "phase_two_status": _status_name(phase_two_status),
        "optimal": optimal,
        "candidate_placements": candidate_id,
        "task_blocks": len(tasks),
        "internal_gaps": {
            "total": total_internal_gaps,
            "by_class_day": internal_gap_details,
        },
        "soft_preferences_applied": {
            "minimize_internal_class_gaps": True,
            "teacher_profiles": len(teacher_preferences),
            "subject_profiles": len(subject_profiles),
            "teacher_days_and_period_preferences": True,
            "teacher_preferred_time_of_day": True,
            "avoid_first_or_last_period": True,
            "teacher_consecutive_period_target": True,
            "subject_difficulty_and_time_of_day": True,
            "subject_daily_limit_preference": True,
            "minimum_gap_between_subject_days": True,
            "balanced_class_daily_load": True,
            "balanced_teacher_workload": True,
            "spread_subjects_across_week": True,
        },
    }
