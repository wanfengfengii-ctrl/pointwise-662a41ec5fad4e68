from dataclasses import dataclass

from air.scheduling import Placement, find_schedule


@dataclass(frozen=True)
class JointPlan:
    """One common pre-reveal prefix and a feasible tail for each result."""

    common: tuple[Placement, ...]
    branches: dict[str, tuple[Placement, ...]]


def _sorted_items(items):
    return tuple(sorted(items, key=lambda item: (item.start, item.end, item.request_id)))


def find_joint_plan(items, first_free, reveal_beat, branch_cues):
    """Find a prefix shared by every result and independent legal tails.

    A common item must be able to start before the reveal beat.  The last common
    clip may cross the reveal; its end is therefore used as the tail's
    ``first_free``.  If common playback finishes earlier, background fills the
    time through the reveal beat.
    """
    ordered = _sorted_items(items)

    def search(remaining, free_at, common):
        tail_start = max(free_at, reveal_beat)
        branches = {}
        for result, cue in branch_cues.items():
            schedule = find_schedule([*remaining, cue], tail_start)
            if schedule is None:
                break
            branches[result] = tuple(schedule)
        else:
            return JointPlan(tuple(common), branches)

        for index, item in enumerate(remaining):
            start = max(free_at, item.start)
            # At/after reveal the result is known, so further ordering can be
            # left to the selected tail instead of remaining common.
            if start >= reveal_beat:
                continue
            end = start + item.duration
            if end > item.end:
                continue
            next_remaining = remaining[:index] + remaining[index + 1:]
            placement = Placement(item, start, end)
            plan = search(next_remaining, end, [*common, placement])
            if plan is not None:
                return plan
        return None

    return search(ordered, first_free, [])


def placement_for(schedule, obligation):
    for placement in schedule:
        if placement.obligation is obligation:
            return placement
    raise RuntimeError('planned obligation is missing from its schedule')
