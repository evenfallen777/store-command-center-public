/* ══ THE COMPANY — naming theme (display labels for the god-tier roles) ══
   Frontend mirror of app/naming_theme.py. One setting, `naming_theme`
   (saved via the normal PATCH /api/settings flow — see Settings → Interface),
   picks how the operator/security tier is PRESENTED:

     themed  (DEFAULT) — today's look, exactly: 🌟 God, ✝️ Jesus, 😈 Satan,
                         🔴/🔵 teams, ⛪ Church, 🍺 Bar, 🔞 store.
     neutral           — professional labels for the SAME system: Owner,
                         Constructive/Adversarial Lieutenant, Advisory Hall…

   STRICTLY COSMETIC: internal ids (worldConsole keys, actor names, settings
   keys, endpoints, b.loc venue keys) never change — only rendered strings.
   Every consumer calls at render time, so flipping the theme re-labels on the
   next render. Loads its truth from GET /api/world/theme (and stays synced
   from /api/world/state polls via WTheme.set); until/unless that answers it
   defaults to `themed`, which is byte-identical to today's UI.
   Global-scope classic script; must load after app-core.js (uses api()). */
(function () {
  // mirrors app/naming_theme.py THEMES — the `themed` side IS today's strings
  const THEMES = {
    themed: {
      god:        { label: 'God',         icon: '🌟' },
      jesus:      { label: 'Jesus',       icon: '✝️' },
      satan:      { label: 'Satan',       icon: '😈' },
      redteam:    { label: 'Red Team',    icon: '🔴' },
      blueteam:   { label: 'Blue Team',   icon: '🔵' },
      church:     { label: 'Church',      icon: '⛪' },
      bar:        { label: 'Bar',         icon: '🍺' },
      nsfw_store: { label: 'Video Store', icon: '🔞' },
    },
    neutral: {
      god:        { label: 'Owner',                   icon: '🌟' },
      jesus:      { label: 'Constructive Lieutenant', icon: '🔺' },
      satan:      { label: 'Adversarial Lieutenant',  icon: '🔻' },
      redteam:    { label: 'Red Team (audit)',        icon: '🔴' },
      blueteam:   { label: 'Blue Team (fix)',         icon: '🔵' },
      church:     { label: 'Advisory Hall',           icon: '🧭' },
      bar:        { label: 'Social Lounge',           icon: '🛋️' },
      nsfw_store: { label: 'Private Studio',          icon: '🎬' },
    },
  };
  // short forms for tight UI spots (God-Panel tab strip) — neutral only;
  // themed keeps each surface's own current literal via pick()/null returns.
  const SHORT = { jesus: 'Constructive', satan: 'Adversarial', god: 'Owner' };

  let _theme = 'themed';
  let _map = THEMES.themed;

  const WTheme = {
    get theme() { return _theme; },
    /* set the active theme (+ optionally a server-resolved map) */
    set(t, map) {
      _theme = THEMES[t] ? t : 'themed';
      _map = Object.assign({}, THEMES[_theme], (t === _theme && map) || {});
    },
    name(id)  { return (_map[id] || THEMES.themed[id] || { label: id }).label; },
    icon(id)  { return (_map[id] || THEMES.themed[id] || { icon: '' }).icon || ''; },
    full(id)  { return `${WTheme.icon(id)} ${WTheme.name(id)}`.trim(); },
    /* literal passthrough: EXACT current string in themed mode (guarantees the
       default UI is unchanged), the neutral wording otherwise */
    pick(themed, neutral) { return _theme === 'neutral' ? neutral : themed; },
    /* God-Panel tab strip override — null in themed mode ⇒ keep current markup */
    tab(key) {
      if (_theme !== 'neutral' || !SHORT[key]) return null;
      return { icon: WTheme.icon(key), label: SHORT[key] };
    },
    /* world-map venue override by building loc/kind — null ⇒ keep current.
       (The 🔞 store's gated/boarded state is handled by the caller and is
       never re-labeled — only the OPEN store gets the neutral name.) */
    venue(loc) {
      if (_theme !== 'neutral') return null;
      const id = loc === 'nsfw' ? 'nsfw_store' : loc;
      if (id !== 'church' && id !== 'bar' && id !== 'nsfw_store') return null;
      const e = _map[id];
      return e ? { label: e.label, icon: e.icon } : null;
    },
  };
  window.WTheme = WTheme;

  // one boot fetch; /api/world/state polls keep it synced afterwards
  // (tab-world.js calls WTheme.set(st.naming_theme)). Failure ⇒ themed default.
  try {
    if (typeof api === 'function') {
      api('/api/world/theme').then(d => { if (d && d.theme) WTheme.set(d.theme, d.map); }).catch(() => {});
    }
  } catch (e) { /* default stands */ }
})();
