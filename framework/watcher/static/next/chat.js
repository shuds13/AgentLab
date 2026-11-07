// The conversation beside the page. In a campaign, a message goes to the board the
// agent reads between turns (or one worker's inbox); once the run has ended it reopens
// the agent's session, which answers. On the lab page it goes to the secretary.
import { html, useState, useLayoutEffect, useRef, usePoll, getJSON } from "./util.js";

// Messages are separated by a blank line, each "`HH:MM` **who** text".
function parse(body) {
  return (body || "").split(/\n\s*\n/).map(m => m.trim()).filter(Boolean).map(m => {
    const p = m.match(/^`(\d\d:\d\d)`\s+\*\*([\w-]+)\*\*\s+([\s\S]*)$/);
    return p ? {at: p[1], who: p[2], text: p[3]} : {at: "", who: "", text: m};
  });
}

async function fetchText(path, params) {
  const r = await fetch(path + "?" + new URLSearchParams(params));
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.text();
}

export function Chat({lab, campaign, run, worker, ended, onClose}) {
  const {data, reload} = usePoll(
    () => lab ? getJSON("/labchat") : fetchText("/messages", {c: campaign}).then(text => ({text})),
    2000, [lab, campaign]);
  const [draft, setDraft] = useState("");
  const [to, setTo] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const box = useRef(null), near = useRef(true);
  const msgs = parse(data && data.text);

  useLayoutEffect(() => { if (near.current && box.current) box.current.scrollTop = box.current.scrollHeight; });
  const onScroll = () => {
    const b = box.current;
    near.current = b.scrollTop + b.clientHeight > b.scrollHeight - 40;
  };

  const recipient = worker && to === worker.run ? worker : null;
  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    const q = lab ? "lab" : new URLSearchParams({c: campaign,
      ...(recipient ? {run: worker.run, to: worker.run} : run ? {run} : {})});
    setSending(true); setError("");
    try {
      const r = await (await fetch("/say?" + q, {method: "POST", body: text})).json();
      if (r.error) setError(r.error);
      else { setDraft(""); near.current = true; reload(); }
    } catch (e) { setError(String(e)); }
    setSending(false);
  };

  const title = lab ? "Secretary" : "Chat";
  const sub = lab
    ? [data && !data.secretary ? "not running" : "", data && data.slack ? "shared with Slack" : ""].filter(Boolean).join(" · ")
    : ended ? "run ended" : "";
  const placeholder = lab
    ? (data && !data.secretary ? "The secretary is not running; this is queued until it starts"
       : data && data.slack ? "Ask the secretary; the reply is posted to Slack too"
       : "Ask the secretary; it answers from the recorded results")
    : recipient ? `Message ${worker.branch || worker.run} only; it reads between turns`
    : ended ? "Ask the agent about its run; it answers from its session"
    : "Message the agent; it reads between turns";

  return html`<aside class="chat">
    <div class="chathead">
      <b>${title}</b>${sub && html`<span class="faint">${sub}</span>`}
      <button class="x" onClick=${onClose} title="Hide the chat">×</button>
    </div>
    <div class="chatlog" ref=${box} onScroll=${onScroll}>
      ${!data ? html`<div class="empty">Loading…</div>`
        : !msgs.length ? html`<div class="empty">No messages yet.</div>`
        : msgs.map(m => html`<div class="msg ${m.who === "you" ? "you" : ""}">
            <div class="msgmeta">${m.who === "you" ? "" : m.who}<span>${m.at}</span></div>
            <div class="bubble">${m.text}</div>
          </div>`)}
    </div>
    <div class="compose">
      ${error && html`<div class="banner bad">${error}</div>`}
      ${!lab && worker && html`<select class="select" value=${to} onChange=${e => setTo(e.target.value)}>
        <option value="">To everyone</option>
        <option value=${worker.run}>To ${worker.branch || worker.run} only</option>
      </select>`}
      <textarea rows="3" value=${draft} placeholder=${placeholder}
        onInput=${e => setDraft(e.target.value)}
        onKeyDown=${e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}></textarea>
      <div class="composebar">
        <span class="faint">Enter to send · Shift+Enter for a new line</span>
        <button class="btn primary" disabled=${!draft.trim() || sending} onClick=${send}>Send</button>
      </div>
    </div>
  </aside>`;
}
