/**
 * @param {number | null} baseline
 * @param {number} score
 * @returns {string}
 */
export function formatRelativeDelta(baseline, score) {
  if (baseline === null || baseline === 0) return "N/A";
  const change = (((score - baseline) / baseline) * 100).toFixed(1);
  if (Number(change) === 0) return "0.0%";
  return `${Number(change) > 0 ? "+" : ""}${change}%`;
}
