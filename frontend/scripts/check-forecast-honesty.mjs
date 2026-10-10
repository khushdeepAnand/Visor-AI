/** Lexical regression gate; supplements runtime rendering/evaluation tests. */
import { readdirSync, readFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

export function forecastHonestyIssues(source) {
  const issues = [];
  if (/\b(?:forecast|range|corridor)\!?\.(?:median|median_reference)\b/.test(source)
      && (!/\.(?:low)\b/.test(source) || !/\.(?:high)\b/.test(source))) {
    issues.push("Point forecast appears without lower and upper interval fields");
  }
  if (/\bconfidence\.score\b/.test(source)
      && (!/Evidence diagnostic/.test(source) || !/not a calibrated probability/i.test(source))) {
    issues.push("Diagnostic score must not be presented as calibrated confidence");
  }
  if (/guaranteed\s+(?:profit|accuracy|returns)|100%\s+(?:accurate|accuracy)/i.test(source)) {
    issues.push("Unsupported forecast guarantee");
  }
  if (/passed its promotion gate/.test(source) && !/promotion_receipt|gate_summary|receipt_passed/.test(source)) {
    issues.push("Promotion claim lacks a receipt-backed rendering condition");
  }
  return issues;
}

function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry =>
    entry.isDirectory() ? files(join(directory, entry.name)) : entry.name.endsWith(".tsx") ? [join(directory, entry.name)] : []);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
  const failures = ["components", "app"].flatMap(folder => files(join(root, folder)))
    .flatMap(file => forecastHonestyIssues(readFileSync(file, "utf8")).map(issue => `${file}: ${issue}`));
  if (failures.length) { console.error(failures.join("\n")); process.exitCode = 1; }
  else console.log("Forecast rendering honesty gate passed.");
}
