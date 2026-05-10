"""Test helpers — DataFrame-backed mocks shared across the test suite."""

import pandas as pd


class FakeLaps(pd.DataFrame):
    """
    DataFrame subclass that mimics FastF1's Laps API surface.

    Lets tests exercise real pandas filtering (boolean masks, groupby, etc.)
    while supporting the pick_* / split_* methods the tools depend on.
    """

    @property
    def _constructor(self):
        return FakeLaps

    def pick_drivers(self, driver):
        if "Driver" not in self.columns:
            return self
        return self[self["Driver"] == driver]

    def pick_accurate(self):
        if "IsAccurate" in self.columns:
            return self[self["IsAccurate"].fillna(False)]
        return self

    def pick_fastest(self):
        valid = self.dropna(subset=["LapTime"])
        if len(valid) == 0:
            return None
        return valid.sort_values("LapTime").iloc[0]

    def pick_wo_box(self):
        mask = pd.Series(True, index=self.index)
        if "PitInTime" in self.columns:
            mask &= self["PitInTime"].isna()
        if "PitOutTime" in self.columns:
            mask &= self["PitOutTime"].isna()
        return self[mask]

    def pick_track_status(self, status, how="equals"):
        if "TrackStatus" not in self.columns:
            return self
        return self[self["TrackStatus"] == status]

    def split_qualifying_sessions(self):
        if "QSegment" not in self.columns:
            return self, self, self
        return (
            self[self["QSegment"] == 1],
            self[self["QSegment"] == 2],
            self[self["QSegment"] == 3],
        )
