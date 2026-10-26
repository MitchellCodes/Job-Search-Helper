"use strict";

// ---- backend bridge: pywebview inside the app window, plain HTTP in dev/browser mode ----------
const bridgeReady = new Promise((resolve) => {
  if (window.pywebview && window.pywebview.api) return resolve();
  window.addEventListener("pywebviewready", resolve, { once: true });
  setTimeout(resolve, 1500);
});

async function call(name, ...args) {
  await bridgeReady;
  const res = window.pywebview && window.pywebview.api
    ? await window.pywebview.api[name](...args)
    : await (await fetch("/api/" + name, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args),
      })).json();
  if (!res.ok) throw new Error(res.error);
  return res.data;
}

// ---- small helpers ----------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LEVELS = [["entry", "Entry / Junior"], ["mid", "Mid"], ["senior", "Senior"], ["lead", "Lead / Manager"]];
const levelName = (l) => (LEVELS.find((x) => x[0] === l) || [l, l || "—"])[1];
let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (isError ? " error" : "");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), isError ? 9000 : 4500);
}
async function act(fn) { try { return await fn(); } catch (e) { toast(e.message, true); } }

function ring(pct, size = 120, label = "") {
  const r = size / 2 - 9, c = 2 * Math.PI * r, p = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  const col = pct == null ? "var(--line)" : p >= 70 ? "var(--good)" : p >= 40 ? "var(--weak)" : "var(--bad)";
  return `<div class="ringwrap"><svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img"
      aria-label="${esc(label)} ${pct == null ? "unknown" : p + " percent"}">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--line)" stroke-width="9"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${col}" stroke-width="9" stroke-linecap="round"
      stroke-dasharray="${(c * p) / 100} ${c}" transform="rotate(-90 ${size / 2} ${size / 2})"/>
    <text x="50%" y="50%" text-anchor="middle" dominant-baseline="central" font-size="${size / 4.2}" font-weight="700" fill="var(--ink)">${pct == null ? "—" : p + "%"}</text>
  </svg><div class="ringlabel">${esc(label)}</div></div>`;
}
const scoreClass = (s) => (s == null ? "" : s >= 70 ? "hi" : s >= 45 ? "mid" : "lo");
const scoreBadge = (s) => `<span class="score ${scoreClass(s)}">${s == null ? "—" : Math.round(s)}</span>`;
const bar = (pct, thin = false) => `<div class="bar ${thin ? "thin" : ""}"><span style="width:${pct ?? 0}%"></span></div>`;
const legend = `<div class="legend">
  <span><i style="background:var(--good)"></i>In your experience</span>
  <span><i style="background:var(--weak)"></i>Only in your skills list</span>
  <span><i style="background:var(--meaning)"></i>Covered by meaning</span>
  <span><i style="background:var(--alt)"></i>Alternative you have ("X or Y")</span>
  <span><i style="background:var(--bad)"></i>Missing</span></div>`;
const chip = (term, r) => {
  const tip = r.status === "meaning" ? `Similar to your bullet: "${r.via.bullet}" (${Math.round(r.via.similarity * 100)}% similar)`
    : r.status === "alternative" ? `Listing says "${r.via.requirement}" and you have ${r.via.have.join(", ")}`
    : r.status === "weak" ? "Appears in your skills list but not in any experience bullet — show it in context"
    : r.status === "matched" ? "Found in your experience" : "Not found in your resume";
  return `<span class="chip ${r.status}" title="${esc(tip)}">${esc(term)}</span>`;
};

// ---- navigation ------------------------------------------------------------------------------------
const views = { setup: renderSetup, listings: renderListings, roles: renderRoles, terms: renderTerms };
let current = "setup";
async function show(view, arg) {
  current = view;
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === (view === "detail" ? "listings" : view)));
  const main = $("#view");
  main.innerHTML = '<p class="muted">Loading…</p>';
  try { await (view === "detail" ? renderDetail(arg) : views[view](arg)); } catch (e) { main.innerHTML = `<div class="card">Something went wrong: ${esc(e.message)}</div>`; }
  main.scrollTop = 0;
  refreshChrome();
}
async function refreshChrome() {
  try {
    const s = await call("state");
    $("#status").textContent = s.resume ? `Resume: ${s.resume.name} · ${s.listing_count} listing(s)` : "No resume yet";
    const c = await call("term_candidates");
    const b = $("#terms-badge"); b.textContent = c.length; b.classList.toggle("hidden", !c.length);
  } catch (_) { /* chrome is best-effort */ }
}
document.querySelectorAll("#nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

// ---- 1. Resume & search ------------------------------------------------------------------------------------
async function renderSetup() {
  const s = await call("state");
  const search = s.search;
  const claudeNote = s.claude.available
    ? `<p class="ok-note">Claude Code is installed (${esc(s.claude.version || "version unknown")}). Tailoring runs through it on your own Claude sign-in, with no API key.</p>`
    : `<p class="warn-note">Claude Code wasn't found on this computer. Everything except tailoring works without it. To tailor resumes, install Claude Code (claude.com/claude-code), run <code>claude</code> once in a terminal and sign in.</p>`;
  $("#view").innerHTML = `
    <h1>Resume &amp; search</h1>
    <p class="sub">Upload your resume once, then tell the app which role and level you're searching for. Every listing you add is tagged with this search so the app can learn what that role typically asks for.</p>
    <div class="grid2">
      <section class="card">
        <h2>Your resume</h2>
        ${s.resume ? `<p><strong>${esc(s.resume.name)}</strong> <span class="muted">(${esc(s.resume.filename)})</span><br>
          <span class="muted">${s.resume.years_experience != null ? "About " + s.resume.years_experience + " years of experience found." : "Couldn't find dated experience."}</span></p>
          <p class="small muted" style="margin-bottom:6px">Skills the app recognised (${s.resume.skills_found.length}):</p>
          <div class="chips">${s.resume.skills_found.map((t) => `<span class="chip matched">${esc(t)}</span>`).join("") || '<span class="muted">None yet.</span>'}</div>`
          : `<p class="muted">No resume uploaded yet. PDF or DOCX.</p>`}
        <div class="row" style="margin-top:14px">
          <button class="btn primary" id="pick">${s.resume ? "Replace resume…" : "Upload resume…"}</button>
          <button class="btn" id="edit-resume" ${s.resume ? "" : "disabled"}>Check parsed text</button>
        </div>
        <div id="resume-edit"></div>
      </section>
      <section class="card">
        <h2>Currently searching for</h2>
        <label class="field">Job title(s) — one per line or comma separated
          <textarea id="titles" style="min-height:76px" placeholder="Data Analyst&#10;Business Analyst">${esc(search.titles.join("\n"))}</textarea></label>
        <p class="small muted" style="margin:12px 0 6px">Experience level</p>
        <div class="checks" id="levels">${LEVELS.map(([v, n]) => `<label><input type="checkbox" value="${v}" ${search.levels.includes(v) ? "checked" : ""}> ${n}</label>`).join("")}</div>
        <div class="row" style="margin-top:14px"><button class="btn primary" id="save-search">Save search</button></div>
      </section>
    </div>
    <section class="card">
      <h2>Tailoring your resume with Claude</h2>
      ${claudeNote}
      <p class="muted" style="margin-bottom:0">Before anything is sent, your name, email, phone, locations, profile links, employers, schools, and project and
        certificate names are replaced with placeholders. You review the exact text first, and the placeholders are swapped back
        after Claude answers. Your contact block is never sent at all.</p>
    </section>`;
  $("#pick").onclick = () => act(async () => { const r = await call("choose_resume"); if (r) { toast("Resume imported."); show("setup"); } });
  $("#save-search").onclick = () => act(async () => {
    const titles = $("#titles").value.split(/[\n,]/).map((t) => t.trim()).filter(Boolean);
    const levels = [...document.querySelectorAll("#levels input:checked")].map((i) => i.value);
    await call("set_search", titles, levels); toast("Search saved."); refreshChrome();
  });
  $("#edit-resume").onclick = () => act(async () => {
    const r = await call("get_resume");
    $("#resume-edit").innerHTML = `<label class="field" style="margin-top:12px">Parsed text — fix anything that was read wrongly
      <textarea id="rtext" style="min-height:220px">${esc(r.raw_text)}</textarea></label>
      <p class="small muted">Section headings found: ${Object.keys(r.sections).map(esc).join(", ")}</p>
      <div class="row"><button class="btn primary" id="rsave">Save corrections</button></div>`;
    $("#rsave").onclick = () => act(async () => { await call("save_resume_text", r.id, $("#rtext").value); toast("Saved."); show("setup"); });
  });
}

// ---- 2. Listings --------------------------------------------------------------------------------------------------
async function renderListings() {
  const s = await call("state");
  const search = s.search;
  $("#view").innerHTML = `
    <h1>Listings</h1>
    <p class="sub">Find a listing on any job site, select the posting text, copy it (Ctrl+C) and paste it below. Listings you add are tagged with
      <strong>${search.titles.length ? esc(search.titles.join(" / ")) : "no search yet"}</strong>${search.levels.length ? " · " + esc(search.levels.map(levelName).join(", ")) : ""}.</p>
    <section class="card">
      <h2>Add a listing</h2>
      <textarea id="paste" style="min-height:150px" placeholder="Paste the job posting here. To add several at once, put a line containing ==== between them."></textarea>
      <div class="row" style="margin-top:10px">
        <input type="text" id="purl" class="grow" placeholder="Listing address (optional, to find it again)">
        <button class="btn primary" id="add">Add listing(s)</button></div>
    </section>
    <section class="card">
      <div class="row" style="margin-bottom:10px">
        <h2 style="margin:0" class="grow">Best fits</h2>
        <label class="field">Min score<input type="number" id="f-min" min="0" max="100" value="0" style="width:80px"></label>
        <label class="field">Level<select id="f-level"><option value="">Any</option>${LEVELS.map(([v, n]) => `<option value="${v}">${n}</option>`).join("")}</select></label>
        <label class="field">Work type<select id="f-remote"><option value="">Any</option><option>remote</option><option>hybrid</option><option>onsite</option></select></label>
        <label class="field">Exclude words<input type="text" id="f-excl" placeholder="e.g. intern, sales" style="width:150px"></label>
      </div>
      <div id="rank"></div>
    </section>`;
  $("#add").onclick = () => act(async () => {
    const text = $("#paste").value, url = $("#purl").value;
    if (/^\s*====\s*$/m.test(text)) { const rs = await call("import_bulk", text); toast(`Added ${rs.filter((r) => r.created).length} of ${rs.length} listings.`); }
    else { const r = await call("import_text", text, url); toast(r.created ? `Added: ${r.title || "listing"}${r.company ? " · " + r.company : ""}` : "That listing was already added."); }
    $("#paste").value = ""; $("#purl").value = ""; loadRank();
  });
  ["f-min", "f-level", "f-remote", "f-excl"].forEach((id) => $("#" + id).addEventListener("change", loadRank));
  loadRank();
}
async function loadRank() {
  const box = $("#rank"); if (!box) return;
  box.innerHTML = '<p class="muted">Scoring listings against your resume…</p>';
  const excl = $("#f-excl").value.split(",").map((w) => w.trim()).filter(Boolean);
  const rows = await act(() => call("ranked", $("#f-min").value, $("#f-level").value, $("#f-remote").value, excl)) || [];
  const s = await call("state");
  if (!s.resume) { box.innerHTML = '<div class="empty">Upload your resume first (step 1) so listings can be scored.</div>'; return; }
  if (!rows.length) { box.innerHTML = '<div class="empty">No listings yet. Paste one above.</div>'; return; }
  box.innerHTML = `<table><thead><tr><th>Fit</th><th>Listing</th><th>Required skills covered</th><th>Level</th><th>Work type</th><th>Status</th></tr></thead><tbody>${rows.map((r) => `
    <tr class="click" data-id="${r.id}"><td>${scoreBadge(r.total)}</td>
      <td><strong>${esc(r.title || "Untitled")}</strong><br><span class="muted small">${esc(r.company || "")}</span></td>
      <td style="min-width:150px">${r.coverage_percent == null ? '<span class="muted">—</span>' : `${r.coverage_percent}%${bar(r.coverage_percent, true)}`}</td>
      <td>${esc(levelName(r.level))}</td><td>${esc(r.remote || "—")}</td><td>${esc(r.feedback || "")}</td></tr>`).join("")}</tbody></table>`;
  box.querySelectorAll("tr.click").forEach((tr) => (tr.onclick = () => show("detail", Number(tr.dataset.id))));
}

// ---- listing detail ---------------------------------------------------------------------------------------------------
async function renderDetail(id) {
  const { listing: l, score: s } = await call("listing_detail", id);
  const p = l.parsed, cov = s.coverage;
  const compNames = { coverage: "Skill coverage", keyword: "Keyword match", meaning: "Meaning match", title: "Title match", level: "Level fit" };
  const groups = (items) => Object.entries(items).sort((a, b) => ["matched", "alternative", "meaning", "weak", "missing"].indexOf(a[1].status) - ["matched", "alternative", "meaning", "weak", "missing"].indexOf(b[1].status));
  $("#view").innerHTML = `
    <p><button class="btn small" id="back">← All listings</button></p>
    <section class="card"><div class="detail-head">
      ${ring(cov.percent, 140, cov.total ? `${cov.covered} of ${cov.total} required skills` : "required skills")}
      <div class="ringwrap"><div class="score ${scoreClass(s.total)}" style="font-size:34px;padding:8px 18px">${s.total == null ? "—" : Math.round(s.total)}</div><div class="ringlabel">Overall fit (0–100)</div></div>
      <div class="grow"><h1 style="margin-bottom:2px">${esc(p.title || "Untitled listing")}</h1>
        <div class="muted">${esc(p.company || "")}${p.location ? " · " + esc(p.location) : ""}</div>
        ${l.url ? `<div class="small muted" style="word-break:break-all;margin-top:4px">${esc(l.url)}</div>` : ""}
        <div class="facts">${[p.level && "Level: " + levelName(p.level), p.years_required != null && p.years_required + "+ yrs asked", p.remote, p.pay && p.pay.text]
          .filter(Boolean).map((f) => `<span class="fact">${esc(f)}</span>`).join("")}</div>
        <div class="row" style="margin-top:12px">
          <label class="field">Status<select id="fb"><option value="">—</option>${["liked", "applied", "interview", "rejected"].map((f) => `<option ${l.feedback === f ? "selected" : ""}>${f}</option>`).join("")}</select></label>
          <button class="btn small danger" id="del">Delete</button></div></div>
    </div></section>
    <section class="card" id="tailor"></section>
    <div class="grid2">
      <section class="card"><h2>Why this score</h2>
        <ul class="plain">${s.why.map((w) => `<li>${esc(w)}</li>`).join("") || "<li>Not enough information to explain.</li>"}</ul>
        <div style="margin-top:14px">${Object.entries(s.components).map(([k, v]) => `<div class="comp"><span>${compNames[k]}</span>${bar(v == null ? 0 : Math.round(v * 100), true)}<span class="muted">${v == null ? "n/a" : Math.round(v * 100)}</span></div>`).join("")}</div>
        ${s.components.meaning == null ? '<p class="small muted">Meaning match is off (the language model could not load); keywords still work.</p>' : ""}
      </section>
      <section class="card"><h2>Required skills</h2>
        <div class="chips">${groups(s.required).map(([t, r]) => chip(t, r)).join("") || '<span class="muted">No known skills recognised in the requirements. Check “New terms” to teach the app this role’s vocabulary.</span>'}</div>
        ${Object.keys(s.required).length ? legend : ""}
        ${Object.keys(s.nice_to_have).length ? `<h2 style="margin-top:18px">Nice to have · ${s.nice_coverage}% covered</h2>${bar(s.nice_coverage, true)}
          <div class="chips" style="margin-top:8px">${groups(s.nice_to_have).map(([t, r]) => chip(t, r)).join("")}</div>` : ""}
      </section>
    </div>
    ${s.meaning_pairs.length ? `<section class="card"><h2>Requirements your resume covers in different words</h2>
      <p class="muted small" style="margin-top:0">The model matched these by meaning. Consider mirroring the listing's wording so keyword filters also catch it.</p>
      ${s.meaning_pairs.map((m) => `<div class="pair"><strong>Listing:</strong> ${esc(m.requirement)}<br><strong>Your resume:</strong> ${esc(m.bullet)} <span class="muted">(${Math.round(m.similarity * 100)}% similar)</span></div>`).join("")}</section>` : ""}
    <section class="card"><h2>Counts toward this role profile</h2>
      <p class="muted small" style="margin-top:0">Added under the search that was active at the time. If this listing is for a different role or level, change it here so it doesn't skew that role's profile. Clear the title to leave it out of all profiles.</p>
      <div class="row">
        <label class="field grow">Role title<input type="text" id="tag-title" value="${esc(l.search_title || "")}" list="role-titles" placeholder="(none)"></label>
        <label class="field">Level<select id="tag-level"><option value="">(none)</option>${LEVELS.map(([v, n]) => `<option value="${v}" ${l.search_level === v ? "selected" : ""}>${n}</option>`).join("")}</select></label>
        <button class="btn" id="tag-save" style="align-self:flex-end">Save</button></div>
      <datalist id="role-titles">${[...new Set((await call("role_options")).map((o) => o.title))].map((t) => `<option value="${esc(t)}">`).join("")}</datalist></section>
    <section class="card"><h2>Responsibilities &amp; requirements as read</h2>
      <div class="grid2"><div><strong class="small muted">REQUIREMENTS</strong><ul class="plain">${p.requirements.map((r) => `<li>${esc(r)}</li>`).join("") || "<li class='muted'>None found</li>"}</ul></div>
      <div><strong class="small muted">RESPONSIBILITIES</strong><ul class="plain">${(p.sections.responsibilities || []).map((r) => `<li>${esc(r)}</li>`).join("") || "<li class='muted'>None found</li>"}</ul></div></div>
      <details style="margin-top:12px"><summary>Original text</summary><pre class="raw">${esc(l.raw_text)}</pre></details></section>`;
  $("#back").onclick = () => show("listings");
  $("#fb").onchange = (e) => act(() => call("set_feedback", id, e.target.value));
  $("#tag-save").onclick = () => act(async () => {
    const title = $("#tag-title").value.trim();
    await call("set_listing_tags", id, title, title ? $("#tag-level").value : "");
    toast(title ? "Role tag saved." : "Listing is now left out of role profiles.");
  });
  $("#del").onclick = () => { if (confirm("Delete this listing?")) act(async () => { await call("delete_listing", id); show("listings"); }); };
  renderTailor(id);
}

// ---- tailoring with Claude ----------------------------------------------------------------------------------------------
const KIND_LABEL = { NAME: "Name", EMAIL: "Email", PHONE: "Phone", LOCATION: "Location", LINK: "Link", EMPLOYER: "Employer",
  SCHOOL: "School", PROJECT: "Project", CERT: "Certificate", HIDDEN: "Added by you" };

function diffHtml(a, b) {
  const A = a.split(/\s+/).filter(Boolean), B = b.split(/\s+/).filter(Boolean), m = A.length, n = B.length;
  const L = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const oldOut = [], newOut = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (A[i] === B[j]) { oldOut.push(esc(A[i])); newOut.push(esc(B[j])); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) oldOut.push(`<del>${esc(A[i++])}</del>`);
    else newOut.push(`<ins>${esc(B[j++])}</ins>`);
  }
  while (i < m) oldOut.push(`<del>${esc(A[i++])}</del>`);
  while (j < n) newOut.push(`<ins>${esc(B[j++])}</ins>`);
  return [oldOut.join(" "), newOut.join(" ")];
}
const flagHtml = (flags) => flags.map((f) => `<div class="flag ${f.level}">${f.level === "block" ? "Can't apply: " : f.level === "warn" ? "Check: " : ""}${esc(f.text)}</div>`).join("");

async function renderTailor(listingId) {
  const box = $("#tailor"); if (!box) return;
  const s = await call("state");
  const head = `<h2>Tailor my resume with Claude</h2>`;
  if (!s.resume) { box.innerHTML = head + '<p class="muted">Upload your resume first (step 1).</p>'; return; }
  const latest = await call("tailor_latest", listingId);
  if (latest) return showTailorResults(listingId, latest);
  box.innerHTML = head + `
    <p class="muted" style="margin-top:0">Claude suggests truthful rewrites and keywords for this listing. You review what will be sent first, then accept or reject each suggestion.</p>
    ${s.claude.available ? "" : '<p class="warn-note">Claude Code wasn\'t found, so this can\'t run yet. See step 1 for setup.</p>'}
    <button class="btn primary" id="t-review" ${s.claude.available ? "" : "disabled"}>Review what will be sent…</button>`;
  $("#t-review").onclick = () => act(() => showTailorReview(listingId));
}

async function showTailorReview(listingId, extraTerms) {
  const box = $("#tailor");
  const [pv, s] = await Promise.all([call("tailor_preview", listingId, extraTerms), call("state")]);
  const blocked = pv.leaks.length > 0;
  const rows = pv.items.map((i) => `<tr><td><code>${esc(i.placeholder)}</code></td><td>${esc(KIND_LABEL[i.kind] || i.kind)}</td><td>${esc(i.original)}</td>
    <td class="muted small">${i.count ? "used " + i.count + "×" : "contact block (not sent)"}</td></tr>`).join("");
  box.innerHTML = `<h2>Review what will be sent</h2>
    <p class="muted" style="margin-top:0">Claude receives the listing and your resume with the details below replaced by placeholders. Nothing is sent until you press “Send to Claude”.</p>
    ${blocked ? `<div class="flag block"><strong>Not safe to send.</strong> The privacy check found personal details that weren't hidden:<ul class="plain">${pv.leaks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>Add them under “Also hide” and update the preview.</div>`
      : '<div class="ok-note">Privacy check passed: none of the details below appear anywhere in the text that will be sent.</div>'}
    ${pv.warnings.map((w) => `<div class="flag warn">${esc(w)}</div>`).join("")}
    <details open style="margin-top:12px"><summary>Replaced with placeholders (${pv.items.length})</summary>
      <table style="margin-top:8px"><thead><tr><th>Placeholder</th><th>Kind</th><th>Your text</th><th></th></tr></thead><tbody>${rows}</tbody></table></details>
    <label class="field" style="margin-top:12px">Also hide (anything else that identifies you, one per line; schools without “University” or “College” in the name are a common miss)
      <textarea id="t-hide" style="min-height:64px">${esc(pv.hide_terms.join("\n"))}</textarea></label>
    <div class="row" style="margin-top:8px"><button class="btn small" id="t-update">Update preview</button></div>
    <details style="margin-top:12px"><summary>The exact resume text Claude will receive</summary><pre class="raw">${esc(pv.resume_text)}</pre></details>
    <details style="margin-top:8px"><summary>The listing text Claude will receive</summary><pre class="raw">${esc(pv.listing_text)}</pre></details>
    <p class="small muted" style="margin-top:12px">Your contact block (name, email, phone, links) is never sent; it is added back from your own file when you save.
      Claude runs with all tools turned off, so it can't read anything else on this computer. Each run counts toward your Claude plan's usage.</p>
    <div class="row" style="margin-top:8px">
      <label class="field">Claude model (optional)<input type="text" id="t-model" value="${esc(s.claude_model || "")}" placeholder="default" style="width:180px"></label>
      <button class="btn primary" id="t-send" style="align-self:flex-end" ${blocked ? "disabled" : ""}>Send to Claude</button>
      <button class="btn" id="t-cancel" style="align-self:flex-end">Cancel</button></div>`;
  const terms = () => $("#t-hide").value.split("\n").map((t) => t.trim()).filter(Boolean);
  $("#t-update").onclick = () => act(() => showTailorReview(listingId, terms()));
  $("#t-cancel").onclick = () => renderTailor(listingId);
  $("#t-send").onclick = () => act(async () => {
    await call("set_claude_model", $("#t-model").value.trim());
    await runTailor(listingId, terms());
  });
}

async function runTailor(listingId, terms) {
  const box = $("#tailor"); const started = Date.now();
  box.innerHTML = `<h2>Claude is working…</h2><p class="muted" id="t-timer">0 s. This usually takes 10 to 60 seconds. You can keep using the rest of the app.</p><div class="bar"><span class="indeterminate"></span></div>`;
  const tick = setInterval(() => { const t = $("#t-timer"); if (t) t.textContent = `${Math.round((Date.now() - started) / 1000)} s. This usually takes 10 to 60 seconds. You can keep using the rest of the app.`; }, 1000);
  try {
    const record = await call("tailor_run", listingId, terms);
    if (!$("#tailor")) return;                      // user navigated away; the result is saved and shows next time
    showTailorResults(listingId, record);
  } catch (e) {
    toast(e.message, true);
    if ($("#tailor")) box.innerHTML = `<h2>Tailor my resume with Claude</h2><div class="flag block">${esc(e.message)}</div><div class="row" style="margin-top:10px"><button class="btn primary" id="t-again">Try again</button></div>`;
    if ($("#t-again")) $("#t-again").onclick = () => act(() => showTailorReview(listingId));
  } finally { clearInterval(tick); }
}

function showTailorResults(listingId, record) {
  const box = $("#tailor"), r = record.result;
  const defaultOn = (item) => item.valid && !item.flags.some((f) => f.level === "warn");
  const check = (item) => `<input type="checkbox" data-accept="${esc(item.id)}" ${defaultOn(item) ? "checked" : ""} ${item.valid ? "" : "disabled"} aria-label="Accept this suggestion">`;
  const summary = r.summary ? (() => { const [o, n] = diffHtml(r.summary.original, r.summary.suggested);
    return `<div class="edit ${r.summary.valid ? "" : "invalid"}"><label class="editrow">${check(r.summary)}<div><strong>Professional summary</strong>
      <div class="diff old">${o}</div><div class="diff new">${n}</div><div class="muted small">${esc(r.summary.reason)}</div>${flagHtml(r.summary.flags)}</div></label></div>`; })() : "";
  const skills = r.skills_to_add.map((k) => `<div class="edit ${k.valid ? "" : "invalid"}"><label class="editrow">${check(k)}<div>
      <strong>Add skill: ${esc(k.skill)}</strong><div class="muted small">Evidence in your resume: “${esc(k.evidence)}”</div>
      <div class="muted small">${esc(k.reason)}</div>${flagHtml(k.flags)}</div></label></div>`).join("");
  const edits = r.bullet_edits.map((e) => { const [o, n] = diffHtml(e.original, e.suggested);
    return `<div class="edit ${e.valid ? "" : "invalid"}"><label class="editrow">${check(e)}<div>
      <div class="diff old">${o}</div><div class="diff new">${n}</div>
      ${e.keywords_added.length ? `<div class="small">Keywords: ${e.keywords_added.map((k) => `<span class="chip meaning">${esc(k)}</span>`).join(" ")}</div>` : ""}
      <div class="muted small">${esc(e.reason)}</div>${flagHtml(e.flags)}</div></label></div>`; }).join("");
  const nothing = !summary && !skills && !edits;
  const m = r.meta || {};
  box.innerHTML = `<h2>Claude's suggestions</h2>
    <p class="muted small" style="margin-top:0">${esc(record.created_at)} UTC${m.model ? " · " + esc(m.model) : ""}${m.seconds ? " · " + m.seconds + " s" : ""}.
      Every suggestion was checked by the app before showing it. <span class="flag-key"><span class="flag warn">Check</span> items are unticked until you've confirmed they're true.</span></p>
    ${nothing ? '<p class="muted">Claude didn\'t suggest any changes that passed the checks.</p>' : ""}
    ${summary}${skills ? `<h2 style="margin-top:14px">Skills to add</h2>${skills}` : ""}${edits ? `<h2 style="margin-top:14px">Bullet edits</h2>${edits}` : ""}
    ${r.gaps.length ? `<h2 style="margin-top:14px">Not on your resume, so not added</h2><p class="muted small" style="margin-top:0">The listing wants these but nothing in your resume supports them. Only claim them if you really have the experience.</p>
      <ul class="plain">${r.gaps.map((g) => `<li><strong>${esc(g.skill)}</strong>: ${esc(g.advice)}</li>`).join("")}</ul>` : ""}
    ${r.notes ? `<p class="muted small">${esc(r.notes)}</p>` : ""}
    <div class="row" style="margin-top:14px">
      <button class="btn primary" id="t-save">Save tailored resume (.docx)…</button>
      <button class="btn" id="t-preview">Preview as text</button>
      <button class="btn" id="t-rerun">Run again…</button>
      <button class="btn small" id="t-sent">What was sent</button></div>
    <div id="t-out"></div>`;
  const accepted = () => [...box.querySelectorAll("[data-accept]:checked")].map((c) => c.dataset.accept);
  $("#t-save").onclick = () => act(async () => {
    const out = await call("tailor_export", record.id, accepted());
    if (out) toast(`Saved: ${out.path}`);
  });
  $("#t-preview").onclick = () => act(async () => { $("#t-out").innerHTML = `<pre class="raw" style="margin-top:12px">${esc(await call("tailor_text", record.id, accepted()))}</pre>`; });
  $("#t-sent").onclick = () => { $("#t-out").innerHTML = `<p class="muted small" style="margin-top:12px">This is exactly the resume text that was sent (personal details were placeholders):</p><pre class="raw">${esc(record.sent_text)}</pre>`; };
  $("#t-rerun").onclick = () => act(() => showTailorReview(listingId));
}

// ---- 3. Role profiles ------------------------------------------------------------------------------------------------------
async function renderRoles() {
  const opts = await call("role_options");
  if (!opts.length) { $("#view").innerHTML = `<h1>Role profiles</h1><div class="card empty">Add listings while a search is set (step 1) and a profile of what that role typically asks for will build here.</div>`; return; }
  $("#view").innerHTML = `<h1>Role profiles</h1>
    <p class="sub">What listings for one role typically ask for, built from the listings you added while searching for it.</p>
    <div class="row" style="margin-bottom:14px"><label class="field">Role<select id="role">${opts.map((o, i) => `<option value="${i}">${esc(o.title)} — ${esc(levelName(o.level))} (${o.listings})</option>`).join("")}</select></label></div>
    <div id="profile"></div>`;
  const load = () => act(async () => {
    const o = opts[$("#role").value], p = await call("role_profile", o.title, o.level);
    const confText = { low: "Low confidence", medium: "Medium confidence", good: "Good confidence" }[p.confidence];
    $("#profile").innerHTML = `
      <section class="card"><div class="detail-head">
        ${ring(p.readiness_percent ?? null, 130, "Your readiness")}
        <div class="grow"><span class="conf ${p.confidence}">${confText}</span>
          <p style="margin:8px 0 0">Built from <strong>${p.listing_count}</strong> listing(s)${p.listing_count < 10 ? " — add about 10+ for a reliable profile" : ""}.</p>
          <p class="muted small">${p.readiness_percent == null ? "Upload a resume to see readiness." : `Your resume covers ${p.readiness_percent}% of the required-skill mentions across these listings.`}
          ${p.typical_years != null ? ` Typical experience asked: ${p.typical_years}+ years.` : ""}</p></div></div></section>
      <div class="grid2">
        <section class="card"><h2>Most-requested skills</h2>${skillTable(p.top_required.slice(0, 15))}</section>
        <section class="card"><h2>Nice to have</h2>${skillTable(p.nice_to_have.slice(0, 12))}</section></div>`;
  });
  $("#role").onchange = load; load();
}
const skillTable = (rows) => rows.length ? rows.map((r) => `<div class="comp" style="grid-template-columns:150px 1fr 90px"><span>${esc(r.term)}</span>${bar(r.percent, true)}<span class="muted small">${r.percent}% (${r.listings})</span></div>`).join("") : '<p class="muted">Nothing yet.</p>';

// ---- 4. New terms -----------------------------------------------------------------------------------------------------------
async function renderTerms() {
  const [cands, names] = await Promise.all([call("term_candidates"), call("skill_names")]);
  $("#view").innerHTML = `<h1>New terms found</h1>
    <p class="sub">Phrases that keep appearing in your saved listings but aren't in the app's skill list yet. Add the ones that are real skills, merge spelling variants into an existing skill, or ignore the rest. Confirmed terms are used for all your listings straight away.</p>
    <datalist id="skills">${names.map((n) => `<option value="${esc(n)}">`).join("")}</datalist>
    ${cands.length ? `<section class="card"><table><thead><tr><th>Phrase</th><th>Seen in</th><th>Example</th><th></th></tr></thead><tbody>${cands.map((c, i) => `
      <tr><td><strong>${esc(c.term)}</strong></td><td>${c.listings} listings</td><td class="muted small">${esc(c.example)}</td>
      <td><div class="row" style="flex-wrap:nowrap">
        <select data-cat="${i}" style="width:110px">${["skill", "tool", "language", "framework", "database", "cloud", "data", "practice", "certification", "domain", "soft"].map((x) => `<option>${x}</option>`).join("")}</select>
        <button class="btn small primary" data-add="${i}">Add</button>
        <input type="text" list="skills" data-merge-in="${i}" placeholder="Merge into…" style="width:130px">
        <button class="btn small" data-merge="${i}">Merge</button>
        <button class="btn small" data-ignore="${i}">Ignore</button></div></td></tr>`).join("")}</tbody></table></section>`
      : '<div class="card empty">Nothing new right now. Add more listings (at least two that mention the same new term) and suggestions appear here.</div>'}`;
  const done = (msg) => { toast(msg); show("terms"); };
  document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => act(async () => { const i = b.dataset.add; await call("accept_term", cands[i].term, $(`[data-cat="${i}"]`).value); done(`Added “${cands[i].term}”.`); })));
  document.querySelectorAll("[data-merge]").forEach((b) => (b.onclick = () => act(async () => { const i = b.dataset.merge, target = $(`[data-merge-in="${i}"]`).value; if (!target) throw new Error("Pick the existing skill to merge into."); await call("accept_term", cands[i].term, "skill", target); done(`Merged into ${target}.`); })));
  document.querySelectorAll("[data-ignore]").forEach((b) => (b.onclick = () => act(async () => { await call("ignore_term", cands[b.dataset.ignore].term); done("Ignored."); })));
}

show("setup");
