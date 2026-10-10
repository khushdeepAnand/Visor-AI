import { test } from "node:test";
import assert from "node:assert/strict";
import { forecastHonestyIssues } from "./check-forecast-honesty.mjs";

test("point-only rendering fails while interval rendering passes", () => {
  assert.ok(forecastHonestyIssues("return <p>{forecast.median}</p>").length);
  assert.deepEqual(forecastHonestyIssues("return <p>{forecast.low} {forecast.median} {forecast.high}</p>"), []);
});
test("unsupported confidence and guaranteed accuracy claims fail", () => {
  assert.ok(forecastHonestyIssues("Confidence {confidence.score}/100").length);
  assert.ok(forecastHonestyIssues("Guaranteed profit with 100% accuracy").length);
  assert.deepEqual(forecastHonestyIssues("Evidence diagnostic {confidence.score}/100; not a calibrated probability"), []);
});
