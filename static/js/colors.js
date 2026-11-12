// Maps a rank (1 = secret word) to a pastel colour from green (close) to red (far).
// Uses a log scale so the colour changes meaningfully across 1..vocabulary size.
function rankToColor(rank, total) {
  if (rank <= 1) return "hsl(120, 55%, 82%)";
  const t = Math.min(1, Math.log(rank) / Math.log(Math.max(total, 2)));
  const hue = 120 * (1 - t);
  return `hsl(${hue}, 55%, 82%)`;
}
