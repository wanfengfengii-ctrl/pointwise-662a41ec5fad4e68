from dataclasses import replace

from air.cue_planning import find_joint_plan
from air.obligations import read_pending_obligations
from air.state import read_cursor
from desk.requests import normalize_window
from program.assets import read_asset
from program.cues import CueArm, read_arm, read_armed_cue, save_choice, insert_arm
from program.database import conflict, entity_id, invalid, name, missing, write_transaction
from program.opening import read_session, require_open_session


def _validate_reveal(reveal_beat):
    if type(reveal_beat) is not int:
        raise invalid('reveal_beat must be an integer beat')
    return reveal_beat


def _validate_branch(value, field):
    if not isinstance(value, dict):
        raise invalid(f'{field} must be an object with asset_id and window')
    asset_id = entity_id(value.get('asset_id'), f'{field}.asset_id')
    window = normalize_window(value.get('window'))
    return asset_id, window


def arm_cue(database, session_id, cue_id, reveal_beat, connected, disconnected):
    session_id = entity_id(session_id, 'session_id')
    cue_id = name(cue_id, 'cue_id')
    reveal_beat = _validate_reveal(reveal_beat)
    connected_asset, connected_window = _validate_branch(connected, 'connected')
    disconnected_asset, disconnected_window = _validate_branch(
        disconnected, 'disconnected',
    )
    request_signature = (
        reveal_beat, connected_asset, connected_window,
        disconnected_asset, disconnected_window,
    )

    with write_transaction(database) as connection:
        read_session(connection, session_id)
        previous = read_arm(connection, session_id, cue_id)
        if previous is not None:
            previous_signature = (
                previous.reveal_beat, previous.connected_asset_id,
                (previous.connected_start, previous.connected_end),
                previous.disconnected_asset_id,
                (previous.disconnected_start, previous.disconnected_end),
            )
            if previous_signature != request_signature:
                raise conflict('cue_conflict', 'the cue already has different parameters')
            return previous.arm_response()

        other = read_armed_cue(connection, session_id)
        if other is not None:
            raise conflict('cue_conflict', 'the session already has a registered cue')

        require_open_session(connection, session_id)
        cursor = read_cursor(connection, session_id)
        if reveal_beat <= cursor.beat:
            raise conflict('cue_reveal_due', 'the reveal beat has already arrived or passed')
        for field_name, window in (
                ('connected', connected_window),
                ('disconnected', disconnected_window)):
            if window[0] < reveal_beat:
                raise invalid(f'{field_name}.window may not begin before the reveal beat')

        connected_asset_row = read_asset(
            connection, session_id, connected_asset, kind='insert',
        )
        disconnected_asset_row = read_asset(
            connection, session_id, disconnected_asset, kind='insert',
        )
        candidate = CueArm(
            session_id=session_id,
            cue_id=cue_id,
            reveal_beat=reveal_beat,
            connected_asset_id=connected_asset,
            connected_start=connected_window[0],
            connected_end=connected_window[1],
            connected_duration=connected_asset_row.frame_count,
            disconnected_asset_id=disconnected_asset,
            disconnected_start=disconnected_window[0],
            disconnected_end=disconnected_window[1],
            disconnected_duration=disconnected_asset_row.frame_count,
            arm_status='armed',
        )
        pending = read_pending_obligations(connection, session_id)
        plan = find_joint_plan(
            pending, cursor.first_free, reveal_beat, candidate.branch_obligations(),
        )
        status = 'armed' if plan is not None else 'unschedulable'
        arm = replace(candidate, arm_status=status)
        insert_arm(connection, arm)
        return arm.arm_response()


def choose_cue(database, session_id, cue_id, result):
    session_id = entity_id(session_id, 'session_id')
    cue_id = name(cue_id, 'cue_id')
    if result not in ('connected', 'disconnected'):
        raise invalid("result must be 'connected' or 'disconnected'")

    with write_transaction(database) as connection:
        read_session(connection, session_id)
        arm = read_arm(connection, session_id, cue_id)
        if arm is None or arm.arm_status != 'armed':
            raise missing('live cue')

        if arm.result is not None:
            if arm.result != result:
                raise conflict('cue_choice_conflict', 'the cue result was already chosen')
            return arm.choice_response()

        require_open_session(connection, session_id)
        cursor = read_cursor(connection, session_id)
        if cursor.beat != arm.reveal_beat:
            raise conflict('cue_not_due', 'the cue can only be chosen at its reveal beat')

        save_choice(connection, session_id, cue_id, result)
        chosen = read_arm(connection, session_id, cue_id)
        return chosen.choice_response()
