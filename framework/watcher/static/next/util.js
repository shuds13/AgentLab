// Shared helpers: the UI library, fetching, polling and formatting.
import { h, html, render, Component, useState, useEffect, useLayoutEffect, useRef, useMemo }
  from "./vendor/htm-preact-3.1.1.js";

export { h, html, render, Component, useState, useEffect, useLayoutEffect, useRef, useMemo };

export async function getJSON(path, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== ""));
  const r = await fetch(path + (q.toString() ? "?" + q : ""));
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

// Calls `fetcher` now and again `ms` after each reply, until `deps` change. A reply that
// arrives after they changed is dropped, so a slow one cannot overwrite the new view.
// `reload` fetches at once, keeping what is shown until the reply comes. With `keep`,
// a change of `deps` also keeps it, so switching what is shown does not empty the page
// (and lose its scroll position) while the new reply is on its way.
export function usePoll(fetcher, ms, deps, {keep = false} = {}) {
  const [state, setState] = useState({data: null, error: null});
  const again = useRef(() => {});
  useEffect(() => {
    let live = true, timer, seq = 0;
    if (!keep) setState({data: null, error: null});
    const tick = async () => {
      clearTimeout(timer);
      const mine = ++seq;
      try {
        const data = await fetcher();
        if (live && mine === seq) setState({data, error: null});
      } catch (error) {
        if (live && mine === seq) setState(s => ({data: s.data, error}));
      }
      if (live && mine === seq) timer = setTimeout(tick, ms);
    };
    again.current = tick;
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, deps);
  return {...state, reload: () => again.current()};
}

export const short = n => n == null ? "—"
  : n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M"
  : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n);

export const hms = s => {
  if (s == null) return "—";
  s = Math.round(s);
  const d = Math.floor(s / 86400), hr = Math.floor(s % 86400 / 3600),
        m = Math.floor(s % 3600 / 60), sec = s % 60;
  if (d) return `${d}d ${hr}h`;
  if (hr) return `${hr}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
};

export const usd = v => v == null ? "—" : "$" + v.toFixed(2);
export const score = v => v == null ? "" : String(+(+v).toPrecision(7));
export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// "2026-10-29T00:10:53" as "29 Oct, 00:10".
export const when = iso => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString([], {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"});
};

// A run's state as a tone for its dot and pill.
export const tone = (status, live) =>
  status === "running" ? (live === false ? "bad" : "ok")
  : status === "not responding" || /crash|error|fail/i.test(status || "") ? "bad"
  : "";

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {}
  const t = document.createElement("textarea");
  t.value = text; document.body.appendChild(t); t.select();
  const ok = document.execCommand("copy");
  t.remove();
  return ok;
}
