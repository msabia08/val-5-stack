/* 5-Stack Tracker: shared by every script, loaded first.
 * The squad can be 2 to 5 players (see the Squad tab), so wording like "5-stack" follows the roster size.
 * app.js sets FiveRoster.size from /api/status; the other scripts call stackWord() when they render.
 */
window.FiveRoster = { size: 0 };
window.stackWord = () => (window.FiveRoster.size >= 2 ? `${window.FiveRoster.size}-stack` : 'squad');
