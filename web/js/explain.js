// What "not found" means, in words. The phone only knows what the last scan saw, so an answer of "no" always comes with
// how much of the room that scan covered: the rest is not checked, not absent.

// m: a find_result with found === false. m.checked = {scans, covered_deg, scan_id} from the server.
export function explainNotFound(m) {
  const c = m.checked || {};
  if (!m.concept) return { title: "I did not catch the name", text: "Try a simple name like “keys” or “water bottle”, or type it instead of speaking." };
  if (!c.scans) return { title: "Nothing has been scanned yet", text: `I cannot say where ${m.concept} is because no room has been scanned. Scan your room first, then ask again.` };
  const pct = Math.max(0, Math.min(100, Math.round((c.covered_deg || 0) / 3.6)));
  const rest = pct >= 97
    ? "Nearly the whole room was covered, so it was probably moved, or it is hidden behind something."
    : `The other ${100 - pct}% of the room was not checked, so it may be there. That includes anything on the floor or ceiling the camera never pointed at.`;
  return { title: "Not found in your last scan", text: `I looked through scan #${c.scan_id}, which covered ${pct}% of the room, and did not see ${m.concept} in it. ${rest} Nothing is checked live: this is only what the scan saw.` };
}

// Seconds since something happened, as a short phrase.
export const ago = (s) => (s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`);
