from air.admission import estimate_preview
from desk.requests import normalize_window


def preview(database, session_id, asset_id, window):
    window = normalize_window(window)
    return estimate_preview(database, session_id, asset_id, window)
