from dataclasses import replace

from air.cue_planning import find_joint_plan
from air.obligations import (
    mark_fulfilled,
    mark_started,
    read_pending_obligations,
)
from air.scheduling import find_schedule
from air.state import ActiveClip, advance_cursor, append_output, read_cursor
from program.assets import read_asset, read_asset_frame
from program.background import background_output
from program.cues import mark_cue_fulfilled, read_armed_cue
from program.database import write_transaction
from program.opening import require_open_session


def _clip_output(connection, session_id, beat, clip):
    asset = read_asset(connection, session_id, clip.asset_id)
    return {
        'beat': beat,
        'source': clip.source,
        'output_id': asset.asset_id,
        'release_id': asset.release_id,
        'frame_index': clip.frame_index,
        'sample': read_asset_frame(connection, asset, clip.frame_index),
        'request_id': clip.request_id,
    }


def _common_clip_due(connection, session_id, beat, pending, cue):
    branch_cues = cue.branch_obligations()
    ordered = sorted(pending, key=lambda item: (item.start, item.end, item.request_id))
    for obligation in ordered:
        start = max(beat, obligation.start)
        if start != beat:
            continue
        end = beat + obligation.duration
        if end > obligation.end:
            continue
        remaining = [item for item in pending if item is not obligation]
        if find_joint_plan(
                remaining, end, cue.reveal_beat, branch_cues) is not None:
            mark_started(connection, session_id, obligation.request_id)
            return ActiveClip(
                obligation.asset_id, 'promise', obligation.request_id, 0,
                obligation.duration,
            )

    # Background now is legal only if the all-background/common-tail plan exists.
    if find_joint_plan(
            pending, beat, cue.reveal_beat, branch_cues) is None:
        raise RuntimeError('formally admitted obligations cannot preserve a cue prefix')
    return None


def _clip_due(connection, session_id, beat):
    pending = read_pending_obligations(connection, session_id)
    cue = read_armed_cue(connection, session_id)

    if cue is not None and cue.arm_status == 'armed' and cue.result is None:
        return _common_clip_due(connection, session_id, beat, pending, cue)

    obligations = list(pending)
    if cue is not None and cue.arm_status == 'armed':
        cue_obligation = cue.selected_obligation()
        if cue_obligation is not None:
            obligations.append(cue_obligation)

    schedule = find_schedule(obligations, beat)
    if schedule is None:
        raise RuntimeError('formally accepted obligations cannot be played')
    if not schedule or schedule[0].start > beat:
        return None

    obligation = schedule[0].obligation
    if obligation.role == 'cue':
        return ActiveClip(
            obligation.asset_id, 'cue', obligation.request_id, 0,
            obligation.duration,
        )

    mark_started(connection, session_id, obligation.request_id)
    return ActiveClip(
        obligation.asset_id, 'promise', obligation.request_id, 0,
        obligation.duration,
    )


def tick(database, session_id):
    with write_transaction(database) as connection:
        session = require_open_session(connection, session_id)
        cursor = read_cursor(connection, session_id)
        cue = read_armed_cue(connection, session_id)
        if (cue is not None and cue.arm_status == 'armed' and cue.result is None
                and cursor.beat >= cue.reveal_beat):
            return {
                'status': 'pending',
                'session_id': session_id,
                'cue_id': cue.cue_id,
                'next_beat': cursor.beat,
            }

        clip = cursor.active or _clip_due(connection, session_id, cursor.beat)
        next_active = None
        if clip is None:
            output = background_output(cursor.beat, session['background_id'])
        else:
            output = _clip_output(connection, session_id, cursor.beat, clip)
            if clip.remaining > 1:
                next_active = replace(clip, frame_index=clip.frame_index + 1)
            elif clip.source == 'promise':
                mark_fulfilled(connection, session_id, clip.request_id)
            elif clip.source == 'cue':
                mark_cue_fulfilled(connection, session_id, clip.request_id)
        append_output(connection, session_id, output)
        advance_cursor(connection, session_id, cursor, next_active)
        return output
