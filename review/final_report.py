import json
from dataclasses import asdict, dataclass

from air.obligations import read_all_obligations
from program.cues import read_armed_cue, read_cue_outputs
from review.reconciliation import PromiseCheck, reconcile_promises
from review.timeline import read_timeline


@dataclass(frozen=True)
class ShiftReport:
    report_id: int
    session_id: int
    outputs: list[dict]
    promises: list[PromiseCheck]


def finalize_report(connection, session_id):
    timeline = read_timeline(connection, session_id)
    obligations = read_all_obligations(connection, session_id)
    outputs = timeline['outputs']
    checks = reconcile_promises(connection, obligations, outputs)
    # There is one report per session, so its number can be derived on close retry.
    report = ShiftReport(session_id, session_id, outputs, checks)
    payload = asdict(report)

    cue = read_armed_cue(connection, session_id)
    if cue is not None and cue.arm_status == 'armed':
        cue_rows = read_cue_outputs(connection, session_id, cue.cue_id)
        if cue.result is None or not cue_rows:
            raise RuntimeError('closed cue session is missing its played cue')
        payload['cue'] = {
            'cue_id': cue.cue_id,
            'reveal_beat': cue.reveal_beat,
            'result': cue.result,
            'release_id': cue_rows[0]['release_id'],
            'beats': [row['beat'] for row in cue_rows],
        }

    connection.execute(
        'INSERT INTO shift_reports (report_id, payload) VALUES (?, ?)',
        (report.report_id, json.dumps(payload, ensure_ascii=False)),
    )
    return report
