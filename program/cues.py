from dataclasses import dataclass

from air.obligations import Obligation


RESULTS = ('connected', 'disconnected')


@dataclass(frozen=True)
class CueArm:
    session_id: int
    cue_id: str
    reveal_beat: int
    connected_asset_id: int
    connected_start: int
    connected_end: int
    connected_duration: int
    disconnected_asset_id: int
    disconnected_start: int
    disconnected_end: int
    disconnected_duration: int
    arm_status: str
    result: str | None = None
    fulfilled: bool = False

    def asset_id(self, result):
        return (self.connected_asset_id if result == 'connected'
                else self.disconnected_asset_id)

    def window(self, result):
        if result == 'connected':
            return self.connected_start, self.connected_end
        return self.disconnected_start, self.disconnected_end

    def duration(self, result):
        return (self.connected_duration if result == 'connected'
                else self.disconnected_duration)

    def branch_obligation(self, result):
        start, end = self.window(result)
        return Obligation(
            self.cue_id, self.asset_id(result), start, end, self.duration(result),
            role='cue',
        )

    def branch_obligations(self):
        return {result: self.branch_obligation(result) for result in RESULTS}

    def selected_obligation(self):
        if self.result is None or self.fulfilled:
            return None
        return self.branch_obligation(self.result)

    def arm_response(self):
        connected_window = [self.connected_start, self.connected_end]
        disconnected_window = [self.disconnected_start, self.disconnected_end]
        return {
            'session_id': self.session_id,
            'cue_id': self.cue_id,
            'reveal_beat': self.reveal_beat,
            'connected': {
                'asset_id': self.connected_asset_id,
                'window': connected_window,
            },
            'disconnected': {
                'asset_id': self.disconnected_asset_id,
                'window': disconnected_window,
            },
            'status': self.arm_status,
        }

    def choice_response(self):
        return {
            'session_id': self.session_id,
            'cue_id': self.cue_id,
            'result': self.result,
            'status': 'chosen',
        }


def _row_to_arm(row):
    if row is None:
        return None
    return CueArm(
        session_id=row['session_id'],
        cue_id=row['cue_id'],
        reveal_beat=int(row['reveal_beat']),
        connected_asset_id=row['connected_asset_id'],
        connected_start=int(row['connected_start']),
        connected_end=int(row['connected_end']),
        connected_duration=row['connected_duration'],
        disconnected_asset_id=row['disconnected_asset_id'],
        disconnected_start=int(row['disconnected_start']),
        disconnected_end=int(row['disconnected_end']),
        disconnected_duration=row['disconnected_duration'],
        arm_status=row['arm_status'],
        result=row['chosen_result'],
        fulfilled=bool(row['fulfilled']),
    )


_COLUMNS = '''session_id, cue_id, reveal_beat,
              connected_asset_id, connected_start, connected_end, connected_duration,
              disconnected_asset_id, disconnected_start, disconnected_end,
              disconnected_duration, arm_status, chosen_result, fulfilled'''


def read_arm(connection, session_id, cue_id):
    row = connection.execute(
        f'SELECT {_COLUMNS} FROM cue_arms WHERE session_id = ? AND cue_id = ?',
        (session_id, cue_id),
    ).fetchone()
    return _row_to_arm(row)


def read_armed_cue(connection, session_id):
    row = connection.execute(
        f'''SELECT {_COLUMNS} FROM cue_arms
            WHERE session_id = ? AND arm_status = 'armed'
            ORDER BY cue_id LIMIT 1''',
        (session_id,),
    ).fetchone()
    return _row_to_arm(row)


def insert_arm(connection, arm):
    connection.execute(
        '''INSERT INTO cue_arms (
               session_id, cue_id, reveal_beat,
               connected_asset_id, connected_start, connected_end, connected_duration,
               disconnected_asset_id, disconnected_start, disconnected_end,
               disconnected_duration, arm_status, chosen_result, fulfilled
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            arm.session_id, arm.cue_id, str(arm.reveal_beat),
            arm.connected_asset_id, str(arm.connected_start), str(arm.connected_end),
            arm.connected_duration,
            arm.disconnected_asset_id, str(arm.disconnected_start),
            str(arm.disconnected_end), arm.disconnected_duration,
            arm.arm_status, arm.result,
            1 if arm.fulfilled else 0,
        ),
    )


def save_choice(connection, session_id, cue_id, result):
    updated = connection.execute(
        '''UPDATE cue_arms SET chosen_result = ?
           WHERE session_id = ? AND cue_id = ? AND arm_status = 'armed'
                 AND chosen_result IS NULL''',
        (result, session_id, cue_id),
    )
    if updated.rowcount != 1:
        raise RuntimeError('cue choice could not be saved')


def mark_cue_fulfilled(connection, session_id, cue_id):
    connection.execute(
        '''UPDATE cue_arms SET fulfilled = 1
           WHERE session_id = ? AND cue_id = ? AND arm_status = 'armed' ''',
        (session_id, cue_id),
    )


def read_cue_outputs(connection, session_id, cue_id):
    return connection.execute(
        '''SELECT beat, release_id, frame_index, sample
           FROM outputs
           WHERE session_id = ? AND source = 'cue' AND request_id = ?
           ORDER BY beat''',
        (session_id, cue_id),
    ).fetchall()
