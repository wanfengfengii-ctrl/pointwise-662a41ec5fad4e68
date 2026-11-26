import argparse
import json
import unittest
from urllib.request import urlopen

from check_project import main as check_original
from tests.support import LiveCase


CLIPS = {"A": [10, 11], "B": [70, 71, 72], "X": [30], "Y": [40], "I": [50]}


class CueScenarios(LiveCase):
    def open_clips(self, clips):
        assets = {tag: self.issue(tag, samples) for tag, samples in clips.items()}
        opened = self.call("open", program_id="live-link", release_ids=list(assets.values()),
                           ident_release_ids=[assets["I"]] if "I" in assets else [])
        return opened["session_id"], assets

    def setup_windows(self, deadline):
        session, assets = self.open_clips(CLIPS)
        for tag, end in (("A", deadline), ("B", 7)):
            receipt = self.submit(session, tag, assets[tag], [0, end])
            self.assertEqual(receipt["status"], "accepted")
        return session, assets

    def arm_fields(self, session, assets, reveal=3, connected=(3, 4), disconnected=(5, 6)):
        return dict(session_id=session, cue_id="live-link", reveal_beat=reveal,
                    connected=dict(asset_id=assets["X"], window=list(connected)),
                    disconnected=dict(asset_id=assets["Y"], window=list(disconnected)))

    def choose_fields(self, session, result):
        return dict(session_id=session, cue_id="live-link", result=result)

    def samples(self, rows):
        return [row["sample"] for row in rows]

    def expect_wait(self, session, reveal, rows):
        waiting = self.call("tick", http=202, session_id=session)
        self.assertEqual(waiting, dict(status="pending", session_id=session,
                                       cue_id="live-link", next_beat=reveal))
        self.assertEqual(self.call("timeline", session_id=session)["outputs"], rows)

    def check_report(self, session, reveal, result, release, beat, rows, ordinary):
        report = self.read_report(session)
        cue = dict(cue_id="live-link", reveal_beat=reveal, result=result,
                   release_id=release, beats=[beat])
        self.assertEqual({key: report["cue"][key] for key in cue}, cue)
        self.assertEqual(report["outputs"], rows)
        self.assertCountEqual([(p["request_id"], p["fulfilled"]) for p in report["promises"]],
                              [(request, True) for request in ordinary])
        actual = [row for row in rows if row["source"] == "cue"]
        self.assertEqual([(row["request_id"], row["beat"], row["release_id"], row["frame_index"])
                          for row in actual], [("live-link", beat, release, 0)])
        return report

    def baseline(self):
        with urlopen(self.base + "/healthz", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response), {"status": "ready"})
        for deadline in (5, 6):
            with self.subTest(deadline=deadline):
                session, _ = self.setup_windows(deadline)
                rows = self.play(session, 5)
                self.assertEqual(self.samples(rows), [10, 11, 70, 71, 72])
                self.assertEqual(self.read_report(session)["outputs"], rows)

    def narrow(self):
        session, assets = self.setup_windows(5)
        fields = self.arm_fields(session, assets)
        denied = self.call("cue/arm", **fields)
        self.assertEqual(denied, {**fields, "status": "unschedulable"})
        self.assertEqual(self.call("timeline", session_id=session)["outputs"], [])
        self.assertEqual(self.samples(self.play(session, 5)), [10, 11, 70, 71, 72])
        self.assertNotIn("cue", self.read_report(session))

    def branches(self):
        for result, tag, tail, beat in (
            ("connected", "X", [30, 10, 11], 3),
            ("disconnected", "Y", [10, 11, 40], 5),
        ):
            with self.subTest(result=result):
                session, assets = self.setup_windows(6)
                fields = self.arm_fields(session, assets)
                armed = self.call("cue/arm", **fields)
                self.assertEqual(armed, {**fields, "status": "armed"})
                choice = self.choose_fields(session, result)
                self.call("cue/choose", http=409, **choice)
                ident = self.call("ident", session_id=session, request_id="station",
                                  asset_id=assets["I"])
                self.assertEqual(ident["status"], "unschedulable")
                preview = self.call("preview", session_id=session, asset_id=assets["X"], window=[3, 4])
                self.assertEqual(preview["status"], "unschedulable")
                pin = self.submit(session, "ordinary-pin", assets["X"], [3, 4])
                self.assertEqual(pin["status"], "unschedulable")
                rows = self.play(session, 3)
                self.assertEqual(self.samples(rows), [70, 71, 72])
                self.expect_wait(session, 3, rows)
                self.assertEqual(self.call("cue/choose", **choice), {**choice, "status": "chosen"})
                self.assertEqual(self.call("timeline", session_id=session)["outputs"], rows)
                suffix = self.play(session, 3)
                self.assertEqual(self.samples(suffix), tail)
                report = self.check_report(session, 3, result, assets[tag], beat,
                                           rows + suffix, ["A", "B"])
                print(json.dumps({"result": result, "samples": self.samples(report["outputs"]),
                                  "cue": report["cue"]}, ensure_ascii=False), flush=True)

    def crossing(self):
        session, assets = self.open_clips({**CLIPS, "A": [10, 11, 12]})
        self.assertEqual(self.submit(session, "A", assets["A"], [0, 3])["status"], "accepted")
        fields = self.arm_fields(session, assets, 2, (3, 4), (3, 4))
        armed = self.call("cue/arm", **fields)
        self.assertEqual(armed["status"], "armed")
        rows = self.play(session, 2)
        self.assertEqual(self.samples(rows), [10, 11])
        self.expect_wait(session, 2, rows)
        choice = self.choose_fields(session, "disconnected")
        chosen = self.call("cue/choose", **choice)
        self.assertEqual(chosen, {**choice, "status": "chosen"})
        self.restart()
        self.assertEqual(self.call("cue/arm", **fields), armed)
        self.assertEqual(self.call("cue/choose", **choice), chosen)
        self.call("cue/choose", http=409, **self.choose_fields(session, "connected"))
        self.call("cue/arm", http=409, **{**fields, "reveal_beat": 1})
        self.call("cue/arm", http=409, **{**fields, "cue_id": "another-cue"})
        last = self.play(session, 1)[0]
        self.assertEqual((last["beat"], last["request_id"], last["frame_index"], last["sample"]),
                         (2, "A", 2, 12))
        self.call("close", http=409, session_id=session)
        suffix = self.play(session, 1)
        self.assertEqual(self.samples(suffix), [40])
        report = self.check_report(session, 2, "disconnected", assets["Y"], 3,
                                   [*rows, last, *suffix], ["A"])
        self.restart()
        self.assertEqual(self.read_report(session), report)
        self.assertEqual(self.call("cue/choose", **choice), chosen)

    def preview(self):
        session, assets = self.open_clips({**CLIPS, "A": [10]})
        self.assertEqual(self.submit(session, "A", assets["A"], [3, 5])["status"], "accepted")
        fields = self.arm_fields(session, assets, 1, (5, 6), (4, 5))
        self.assertEqual(self.call("cue/arm", **fields)["status"], "armed")
        result = self.call("preview", session_id=session, asset_id=assets["B"], window=[1, 8])
        self.assertEqual(result, {"status": "suggested", "start_beats": {"connected": 1, "disconnected": 5}})
        self.assertEqual(self.call("timeline", session_id=session)["outputs"], [])
        self.call("lookup", http=404, session_id=session, request_id="B")
        self.assertEqual(self.submit(session, "B", assets["B"], [1, 8])["status"], "accepted")


def main():
    parser = argparse.ArgumentParser(description="Live link cue scenarios")
    parser.add_argument("mode", choices=("baseline", "verify", "demo"))
    args = parser.parse_args()
    if args.mode == "verify" and check_original():
        return 1
    cases = {
        "baseline": ("baseline",),
        "verify": ("narrow", "branches", "crossing", "preview"),
        "demo": ("branches",),
    }[args.mode]
    suite = unittest.TestSuite(CueScenarios(case) for case in cases)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
