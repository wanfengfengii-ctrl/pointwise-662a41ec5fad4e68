from air.cue_planning import find_joint_plan, placement_for
from air.obligations import Obligation, add_obligation, read_pending_obligations
from air.outcomes import AdmissionOutcome, read_outcome, save_first_outcome
from air.scheduling import find_schedule
from air.state import read_cursor
from program.assets import read_asset
from program.cues import read_armed_cue
from program.database import conflict, read_transaction, write_transaction
from program.opening import read_session, require_open_session, session_is_open


def _selected_cue_obligation(cue, cursor):
    if cue is None or cue.result is None or cue.fulfilled:
        return None
    if (cursor.active is not None and cursor.active.source == 'cue'
            and cursor.active.request_id == cue.cue_id):
        return None
    return cue.selected_obligation()


def candidate_is_feasible(connection, session_id, candidate=None, *, free=None):
    cursor = read_cursor(connection, session_id)
    if free is None:
        free = cursor.first_free
    pending = read_pending_obligations(connection, session_id)
    extra = [] if candidate is None else [candidate]

    cue = read_armed_cue(connection, session_id)

    if cue is not None and cue.arm_status == 'armed' and cue.result is None:
        plan = find_joint_plan(
            [*pending, *extra], free, cue.reveal_beat, cue.branch_obligations(),
        )
        return plan is not None

    obligations = [*pending, *extra]
    cue_obligation = _selected_cue_obligation(cue, cursor)
    if cue_obligation is not None:
        obligations.append(cue_obligation)
    return find_schedule(obligations, free) is not None


def estimate_preview(database, session_id, asset_id, window):
    with read_transaction(database) as connection:
        require_open_session(connection, session_id)
        asset = read_asset(connection, session_id, asset_id, kind='insert')
        candidate = Obligation('', asset_id, *window, asset.frame_count)
        cursor = read_cursor(connection, session_id)
        pending = read_pending_obligations(connection, session_id)
        cue = read_armed_cue(connection, session_id)

        if cue is not None and cue.arm_status == 'armed' and cue.result is None:
            plan = find_joint_plan(
                [*pending, candidate], cursor.first_free,
                cue.reveal_beat, cue.branch_obligations(),
            )
            if plan is None:
                return {'status': 'unschedulable'}

            def candidate_start(result):
                for placement in plan.common:
                    if placement.obligation is candidate:
                        return placement.start
                return placement_for(plan.branches[result], candidate).start

            return {
                'status': 'suggested',
                'start_beats': {
                    result: candidate_start(result)
                    for result in ('connected', 'disconnected')
                },
            }

        obligations = [*pending, candidate]
        cue_obligation = _selected_cue_obligation(cue, cursor)
        if cue_obligation is not None:
            obligations.append(cue_obligation)
        schedule = find_schedule(obligations, cursor.first_free)
        if schedule is None:
            return {'status': 'unschedulable'}
        start_beat = placement_for(schedule, candidate).start
        return {'status': 'suggested', 'start_beat': start_beat}


def admit(database, session_id, request_id, asset_id, window):
    with write_transaction(database) as connection:
        session = read_session(connection, session_id)
        first = read_outcome(connection, session_id, request_id)
        if first is not None:
            if (first.asset_id, first.window) != (asset_id, window):
                raise conflict('request_conflict', 'the request already has different parameters')
            return first

        asset = read_asset(connection, session_id, asset_id, kind='insert')
        status = 'unschedulable'
        if session_is_open(session):
            candidate = Obligation(request_id, asset_id, *window, asset.frame_count)
            if candidate_is_feasible(connection, session_id, candidate):
                status = 'accepted'
        outcome = AdmissionOutcome(session_id, request_id, asset_id, window, status)
        save_first_outcome(connection, outcome)
        if status == 'accepted':
            add_obligation(connection, outcome)
        return outcome
