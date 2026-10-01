/* 5-Stack Tracker: the match recap at the top of the Matches tab (data from /api/recap, built by fivestack/recap.py).
 *
 * FiveRecap.html(recap, helpers) returns the cards: header with older / newer buttons, highlights, scoreboard against
 * each player's usual game and forecast, round by round, and betting and rewards. Hover details use the same
 * data-tip tooltips as the charts (FiveViz.mount). Plain JS, no dependencies; loaded before app.js.
 */
window.FiveRecap = (() => {
  'use strict';

  let h = null; // helpers from app.js: esc, fmt, slot(puuid)

  // A one-line explanation with the rest folded behind "How this works" (the full text is still one click away).
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;
  const tip = (title, rows) => ` data-tip="${h.esc(JSON.stringify({ t: title, r: rows }))}" tabindex="0"`;
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const num = (key, v) => (v == null ? '–' : key === 'hs_pct' ? `${Math.round(v)}%` : key === 'kd' ? v.toFixed(2)
    : ['acs', 'adr'].includes(key) ? Math.round(v).toLocaleString() : String(Math.round(v * 10) / 10));
  const signed = (v) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(Math.round(v)).toLocaleString()}`;
  const swatch = (puuid) => (puuid ? `<span class="swatch s${h.slot(puuid) || 1}"></span>` : '');
  const ordinal = (n) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th'}`;

  function header(r) {
    const m = r.match;
    const when = m.started_ts ? new Date(m.started_ts * 1000) : null;
    const bits = [
      when ? when.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) + ' · ' + when.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '',
      m.game_length_ms ? `${Math.round(m.game_length_ms / 60000)} min` : '',
      `game ${m.night_game} of the night (${m.night_record[0]}–${m.night_record[1]})`,
      `${ordinal(m.number)} ${stackWord()} game`,
      m.overtime ? 'overtime' : '',
      m.ending === 'forfeit' ? (m.result === 'win' ? 'the other team surrendered' : 'the squad surrendered') : '',
    ].filter(Boolean);
    const nav = (id, label) => `<button type="button" class="btn ghost small recap-nav" data-recap="${h.esc(id || '')}"${id ? '' : ' disabled'}>${label}</button>`;
    return `<section class="card recap-head">
      <div class="recap-title"><span class="chip ${h.esc(m.result || '')}">${h.fmt.res(m.result)}</span><b class="recap-score">${m.rounds_won}–${m.rounds_lost}</b>` +
      `<span class="recap-map">${h.esc(m.map || 'Unknown map')}</span><span class="muted">${h.esc(m.mode_label || '')}</span>` +
      `<span class="recap-navs">${nav(m.older, '‹ Older')}${nav(m.newer, 'Newer ›')}</span></div>
      <div class="muted small">${h.esc(bits.join(' · '))}</div></section>`;
  }

  function highlights(r) {
    const hs = r.highlights;
    if (!hs.length) {
      return `<section class="card"><h2>Highlights</h2><p class="muted">Nothing out of the ordinary this game. Records and firsts need ${r.min_record_games} earlier games to count.</p></section>`;
    }
    const who = (x) => (x.puuid ? `${swatch(x.puuid)}${h.esc(x.nickname || '')}` : '<span class="muted">Squad</span>');
    const cards = hs.slice(0, r.top_cards).map((x) => `<div class="hl-card tone-${h.esc(x.tone)}">
        <div class="hl-who">${who(x)}</div>
        <div class="hl-title">${h.esc(x.title)}</div>${x.detail ? `<div class="hl-detail">${h.esc(x.detail)}</div>` : ''}</div>`).join('');
    const rest = hs.slice(r.top_cards);
    const more = rest.length ? `<details class="hl-more"><summary>${plural(rest.length, 'more highlight')}</summary><ul>${rest.map((x) =>
      `<li class="tone-${h.esc(x.tone)}">${who(x)} <b>${h.esc(x.title)}</b>${x.detail ? ` <span class="muted">· ${h.esc(x.detail)}</span>` : ''}</li>`).join('')}</ul></details>` : '';
    return `<section class="card"><h2>Highlights</h2><div class="hl-grid">${cards}</div>${more}</section>`;
  }

  // A stat cell: this game's value, and an arrow when it's well above or below the player's usual game.
  function statCell(p, key, better = 1) {
    const v = p[key], u = p.usual && p.usual[key], fc = p.forecast && p.forecast[key];
    let arrow = '<span class="recap-arrow"></span>'; // an empty slot keeps the numbers in line with the arrowed ones
    if (u != null && v != null && Math.abs(v - u) >= Math.max(0.15 * Math.abs(u), key === 'kd' ? 0.2 : 1)) {
      const good = (v > u) === (better > 0);
      arrow = `<span class="recap-arrow ${good ? 'up' : 'down'}">${v > u ? '▲' : '▼'}</span>`;
    }
    const rows = [['this game', num(key, v), null]];
    if (u != null) rows.push(['usual', num(key, u), null]);
    if (fc) rows.push(['forecast', `${num(key, fc.range[0])}–${num(key, fc.range[1])}`, '--accent'], ['typical', num(key, fc.typical), null]);
    return `<td class="num"${tip(`${p.nickname} · ${key === 'hs_pct' ? 'HS %' : key.toUpperCase()}`, rows)}>${num(key, v)}${arrow}</td>`;
  }

  function scoreboard(r) {
    const rows = r.players.map((p) => {
      const rd = p.rounds;
      const extras = rd ? [rd.k5 ? `${rd.k5 > 1 ? rd.k5 + '× ' : ''}ACE` : '', rd.k4 ? `${rd.k4 > 1 ? rd.k4 + '× ' : ''}4K` : '', rd.k3 ? `${rd.k3 > 1 ? rd.k3 + '× ' : ''}3K` : '',
        ...rd.clutches.map((c) => `1v${c.vs}`)].filter(Boolean) : [];
      return `<tr><th scope="row">${swatch(p.puuid)}${h.esc(p.nickname)}<div class="muted small">${h.esc(p.agent || '?')}</div></th>` +
        statCell(p, 'kills') + statCell(p, 'deaths', -1) + statCell(p, 'assists') + statCell(p, 'kd') + statCell(p, 'acs') + statCell(p, 'adr') + statCell(p, 'hs_pct') +
        `<td class="num">${Math.round(p.damage_share * 100)}%</td><td class="num">${rd ? rd.fb : '–'}</td>` +
        `<td>${extras.map((x) => `<span class="tag">${h.esc(x)}</span>`).join(' ')}</td></tr>`;
    }).join('');
    return `<section class="card"><h2>Scoreboard</h2><div class="table-wrap"><table class="compact recap-board"><thead><tr><th>Player</th>` +
      '<th class="num">K</th><th class="num">D</th><th class="num">A</th><th class="num">K/D</th><th class="num">ACS</th><th class="num">ADR</th><th class="num">HS %</th>' +
      '<th class="num" title="Share of the squad\'s damage">Dmg</th><th class="num" title="Rounds opened with the first kill">FB</th><th>Big rounds</th></tr></thead>' +
      `<tbody>${rows}</tbody></table></div>${how('▲ / ▼ mark a stat well above or below the player\'s usual game.', `Green is good; for deaths, fewer is good. Hover a number for their usual ${stackWord()} game and the forecast from the Forecasts tab.`)}</section>`;
  }

  function rounds(r) {
    if (!r.rounds.length) return '';
    const name = new Map(r.players.map((p) => [p.puuid, p.nickname]));
    const cells = r.rounds.map((x) => {
      const marks = [];
      if (x.planted) marks.push(`<span class="rd-mark ${x.planted}">${h.esc(x.site || '•')}</span>`);
      if (x.defused) marks.push('<span class="rd-mark def">D</span>');
      const multi = Object.entries(x.multi || {});
      multi.forEach(([, n]) => marks.push(`<span class="rd-mark big">${n >= 5 ? 'ACE' : n + 'K'}</span>`));
      if (x.clutch && x.clutch.won && x.clutch.vs >= 2) marks.push(`<span class="rd-mark big">1v${x.clutch.vs}</span>`);
      const rows = [['score', `${x.score[0]}–${x.score[1]}`, null]];
      if (x.side) rows.push(['side', x.side, null]);
      if (x.first_blood) rows.push(['first blood', x.first_blood.ours ? `${name.get(x.first_blood.puuid) || '?'} got it` : `${name.get(x.first_blood.puuid) || '?'} died first`, x.first_blood.ours ? '--div-pos' : '--div-neg']);
      if (x.planted) rows.push(['spike', `${x.planted === 'us' ? 'we' : 'they'} planted ${x.site || ''}${x.defused ? ', defused' : ''}`, null]);
      multi.forEach(([p, n]) => rows.push(['multi-kill', `${name.get(p) || '?'}: ${n >= 5 ? 'ace' : n + ' kills'}`, null]));
      if (x.clutch) rows.push(['clutch', `${name.get(x.clutch.puuid) || '?'} 1v${x.clutch.vs}, ${x.clutch.won ? 'won' : 'lost'}`, null]);
      const gap = x.n === 13 || (x.n > 24 && x.n % 2 === 1) ? ' rd-gap' : '';
      const fb = x.first_blood ? `<span class="rd-fb ${x.first_blood.ours ? 'ours' : 'theirs'}"></span>` : '';
      return `<div class="rd ${x.won ? 'won' : 'lost'}${gap}"${tip(`Round ${x.n}: ${x.won ? 'won' : 'lost'}`, rows)}>${fb}<span class="rd-n">${x.n}</span>` +
        `<div class="rd-marks">${marks.join('')}</div></div>`;
    }).join('');
    const sides = r.rounds[0].side ? `First half on ${r.rounds[0].side}, second on ${r.rounds[0].side === 'attack' ? 'defence' : 'attack'} (read from who planted). ` : '';
    return `<section class="card"><h2>Round by round</h2><div class="rd-strip">${cells}</div>
      ${how('Hover a round for details.', `${sides}Letters are spike plants by site (blue: ours, red: theirs), D a defuse; the corner dot shows who got the first kill.`)}</section>`;
  }

  function betting(r) {
    const b = r.betting, w = r.rewards;
    if (!b && !w.length) return '';
    const money = (v) => `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${signed(v)}</span>`;
    const bets = b ? `<div><h3>Betting</h3><p class="muted small">${plural(b.bets, 'bet')} · ${Math.round(b.staked).toLocaleString()} wagered · the house ${b.house >= 0 ? 'took' : 'paid out'} ${Math.abs(Math.round(b.house)).toLocaleString()}</p>
        <table class="compact"><tbody>${b.bettors.map((x) => `<tr><td>${h.esc(x.bettor)}</td><td class="num muted">${plural(x.bets, 'bet')}</td><td class="num">${money(x.net)}</td></tr>`).join('')}</tbody></table>
        <h3>Best bets</h3><table class="compact"><tbody>${b.best.filter((x) => x.net > 0).map((x) => `<tr><td>${h.esc(x.bettor)}</td><td>${h.esc(x.description || '')}</td><td class="num muted">${h.esc(x.american)}</td><td class="num">${money(x.net)}</td></tr>`).join('') || '<tr><td class="muted">No winning bets</td></tr>'}</tbody></table></div>`
      : '<div><h3>Betting</h3><p class="muted">No bets settled on this game.</p></div>';
    const rewards = w.length ? `<div><h3>Game rewards</h3><table class="compact"><tbody>${w.map((x) => `<tr><td>${swatch(x.puuid)}${h.esc(x.nickname || x.bettor)}</td>` +
      `<td class="num muted">${Math.round(x.base)} + ${Math.round(x.bonus)} bonus</td><td class="num">${money(x.base + x.bonus)}</td></tr>`).join('')}</tbody></table></div>`
      : '<div><h3>Game rewards</h3><p class="muted">No rewards for this game.</p></div>';
    return `<section class="card"><h2>Betting and rewards</h2><div class="recap-cols">${bets}${rewards}</div></section>`;
  }

  function html(r, helpers) {
    h = helpers;
    if (!r) return '';
    return `<div id="recap">${header(r)}${highlights(r)}${scoreboard(r)}${rounds(r)}${betting(r)}</div>`;
  }

  return { html };
})();
