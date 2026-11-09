// The agent's log as it is written, split into its turns. It follows the end while you
// are at the end, and holds still once you scroll up.
import { html, Component, useState, useEffect, useLayoutEffect, useRef, getJSON } from "./util.js";

const TURN = /^===== TURN (\d+) =====$/;
const META = /^\[turn \d+|^Drained|^Executor |^stop requested/;

// The log as turns of lines. Text is added as it arrives; a line is held back until its
// newline, so a line split across two polls is not drawn as two.
function parser() {
  const p = {turns: [{n: null, lines: [], v: 0}], tail: ""};
  p.add = text => {
    const parts = (p.tail + text).split("\n");
    p.tail = parts.pop();
    for (const line of parts) {
      const m = line.match(TURN);
      if (m) p.turns.push({n: +m[1], lines: [], v: 0});
      else { const t = p.turns[p.turns.length - 1]; t.lines.push(line); t.v++; }
    }
  };
  return p;
}

// A turn redraws only when lines were added to it.
class Turn extends Component {
  shouldComponentUpdate(next) { return next.v !== this.props.v; }
  render({turn}) {
    return html`<div data-turn=${turn.n}>
      ${turn.n != null && html`<div class="turn">Turn ${turn.n}</div>`}
      <pre>${turn.lines.map(l => META.test(l) ? html`<span class="meta">${l + "\n"}</span>` : l + "\n")}</pre>
    </div>`;
  }
}

function Matches({turns, query}) {
  const q = query.toLowerCase();
  const out = [];
  for (const t of turns) {
    const hits = t.lines.filter(l => l.toLowerCase().includes(q));
    if (!hits.length) continue;
    out.push(html`<div class="match-turn">${t.n == null ? "Start" : `Turn ${t.n}`}</div>`);
    out.push(html`<pre>${hits.map(l => html`<span class="hit">${l}</span>${"\n"}`)}</pre>`);
  }
  return out.length ? out : html`<div class="empty">No lines match.</div>`;
}

export function Log({campaign, run}) {
  const [, redraw] = useState(0);
  const [info, setInfo] = useState({name: "", running: false});
  const [query, setQuery] = useState("");
  const [following, setFollowing] = useState(true);
  const box = useRef(null), state = useRef(null), stick = useRef(true);

  useEffect(() => {
    state.current = {parser: parser(), offset: 0, gen: (state.current ? state.current.gen : 0) + 1};
    stick.current = true; setFollowing(true);
    redraw(n => n + 1);
    let live = true, timer;
    const tick = async () => {
      try {
        const st = state.current;
        const j = await getJSON("/log", {c: campaign, run, from: st.offset});
        if (!live) return;
        if (j.reset) { st.parser = parser(); st.gen++; }
        if (j.text) st.parser.add(j.text);
        st.offset = j.offset;
        setInfo({name: j.name, running: j.running});
        if (j.text || j.reset) redraw(n => n + 1);
      } catch (e) {}
      if (live) timer = setTimeout(tick, 1500);
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [campaign, run]);

  useLayoutEffect(() => {
    if (stick.current && box.current && !query) box.current.scrollTop = box.current.scrollHeight;
  });

  const onScroll = () => {
    const b = box.current;
    const at = b.scrollHeight - b.scrollTop - b.clientHeight < 40;
    stick.current = at;
    if (at !== following) setFollowing(at);
  };
  const toEnd = () => { stick.current = true; setFollowing(true); box.current.scrollTop = box.current.scrollHeight; };
  const toTurn = n => {
    const el = box.current.querySelector(`[data-turn="${n}"]`);
    if (el) { stick.current = false; setFollowing(false); box.current.scrollTop = el.offsetTop; }
  };

  const p = state.current && state.current.parser;
  const turns = p ? p.turns : [];
  const numbered = turns.filter(t => t.n != null);
  const empty = !turns.some(t => t.lines.length) && !(p && p.tail);
  return html`
    <div class="logbar">
      <input type="search" placeholder="Find in log" value=${query} onInput=${e => setQuery(e.target.value)} />
      ${numbered.length > 0 && html`<select class="select" value="" onChange=${e => { toTurn(e.target.value); e.target.value = ""; }}>
        <option value="">Jump to turn…</option>
        ${numbered.slice().reverse().map(t => html`<option value=${t.n}>Turn ${t.n}</option>`)}
      </select>`}
      <span class="spacer" style="flex:1"></span>
      <span class="faint mono">${info.name}</span>
      ${info.running && html`<span class="pill ok"><span class="dot ok live"></span>writing</span>`}
    </div>
    <div class="logwrap">
      <div class="log" ref=${box} onScroll=${onScroll}>
        ${query ? html`<${Matches} turns=${turns} query=${query} />`
          : empty ? html`<div class="empty">${info.name || "No run log yet."}</div>`
          : html`${turns.map((t, i) => html`<${Turn} key=${state.current.gen + "-" + i} turn=${t} v=${t.v} />`)}
                 ${p.tail && html`<pre>${p.tail}</pre>`}`}
      </div>
      ${!following && !query && html`<button class="jump" onClick=${toEnd}>↓ Latest</button>`}
    </div>`;
}
