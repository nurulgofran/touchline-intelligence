import assert from "node:assert/strict";
import test from "node:test";
import { formatClock, formatDuration } from "../.test-dist/types.js";

test("the second half uses continuous match time, not added time", () => {
  for (const [milliseconds, expected] of [
    [-1000, "00:00"],
    [2_699_000, "44:59"],
    [2_700_000, "45:00"],
    [3_600_000, "60:00"],
    [3_808_000, "63:28"],
    [5_399_000, "89:59"],
    [5_400_000, "90:00"],
  ]) {
    assert.equal(formatClock(milliseconds), expected);
  }
  assert.equal(formatDuration(5_400_000), "90:00");
});
