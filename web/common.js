/* 5-Stack Tracker: shared by every script, loaded first.
 * The squad can be 2 to 5 players (see the Squad tab), so wording like "5-stack" follows the roster size.
 * app.js sets FiveRoster.size from /api/status; the other scripts call stackWord() when they render.
 */
window.FiveRoster = { size: 0 };
window.stackWord = () => (window.FiveRoster.size >= 2 ? `${window.FiveRoster.size}-stack` : 'squad');
// The one speaker icon every sound button uses (slots, the casino tables, the daily wheel, the arcade): sound waves
// when it's on, crossed out when it's muted. It takes the button's text colour.
window.speakerIcon = (off, size = 20) => `<svg class="speaker-icon" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"><path d="M4 9h4l5-4v14l-5-4H4z" fill="currentColor"/>${off
  ? '<path d="M16 9.5l5 5M21 9.5l-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
  : '<path d="M16 8.8a4.5 4.5 0 0 1 0 6.4M18.6 6.2a8.2 8.2 0 0 1 0 11.6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>'}</svg>`;
// The scientist (docs/onkey-lore.md): the man who wants Onkey. Wherever he turns up on a page it's this note: his face
// and one line in his accent. Only he has the accent ("zis", "zat", "ze"); Onkey never does. He's kept rare.
window.sciNote = (line, extra = '') => `<div class="sci-note"><img src="/assets/scientist-face.png" alt="The scientist" width="44" height="44">` +
  `<div><p>${line}</p>${extra}</div></div>`;
