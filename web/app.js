"use strict";
const board = JSON.parse(document.getElementById("board-data").textContent);
const fields = ["release", "eval_version"];
const tasks = [...new Set(board.entries.flatMap(e => Object.keys(e.scores.tasks)))].sort();
let descending = true;
const el = (tag, text, cls) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
const pct = value => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const link = (text, url) => { const node = el("a", text); node.href = url; node.rel = "noopener noreferrer"; return node; };
const label = value => ({reported: "Reported", generalist: "Generalist", "standard-data": "Standard data"}[value] || value);
for (const field of fields) {
  const select = document.getElementById(field);
  for (const value of ["", ...new Set(board.entries.map(e => e[field]).sort())]) {
    const option = el("option", value ? label(value) : "All"); option.value = value; select.append(option);
  }
  select.addEventListener("change", render);
}
for (const value of ["overall", ...tasks]) {
  const option = el("option", value === "overall" ? "Overall · full suite" : `Task ${value.toUpperCase()}`);
  option.value = value; document.getElementById("metric").append(option);
}
document.getElementById("metric").addEventListener("change", render);
document.getElementById("search").addEventListener("input", render);
document.getElementById("sort").addEventListener("click", () => { descending = !descending; document.getElementById("sort").textContent = `Score ${descending ? "↓" : "↑"}`; render(); });
document.getElementById("stats").append(el("span", `${board.entries.length} reviewed entries`), el("span", `${board.releases.length} accepted releases`), el("span", `${board.pending.length} awaiting review`));
function render() {
  const target = document.getElementById("results"); target.replaceChildren();
  const query = document.getElementById("search").value.toLowerCase();
  const metric = document.getElementById("metric").value;
  const selected = board.entries.filter(e => fields.every(f => !document.getElementById(f).value || e[f] === document.getElementById(f).value)
    && `${e.model} ${e.team}`.toLowerCase().includes(query));
  if (!selected.length) {
    const empty = el("section", undefined, "empty");
    empty.append(el("p", board.entries.length ? "No matching entries" : "The next result could be yours.", "empty-title"),
      el("p", board.entries.length ? "Try changing the filters." : board.releases.length ? "Submissions appear after maintainer review." : "Submissions open when the release owner registers an accepted evaluator and reference pins."));
    target.append(empty); return;
  }
  const groups = new Map();
  for (const entry of selected) { const key = JSON.stringify(fields.map(f => entry[f])); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(entry); }
  for (const [key, entries] of [...groups].sort((a,b) => a[0].localeCompare(b[0]))) {
    const score = e => metric === "overall" ? e.scores.overall_sr : e.scores.tasks[metric]?.sr ?? null;
    entries.sort((a, b) => score(a) == null ? (score(b) == null ? a.id.localeCompare(b.id) : 1) : score(b) == null ? -1 : (descending ? -1 : 1) * (score(a) - score(b)) || a.id.localeCompare(b.id));
    const section = el("section", undefined, "group"); section.append(el("h2", JSON.parse(key).map(label).join(" · ")));
    const scroll = el("div", undefined, "table-scroll"), table = el("table"), head = el("thead"), tr = el("tr");
    for (const name of ["Rank", "Model / team", metric === "overall" ? "Overall" : metric.toUpperCase(), "Coverage", "Evidence"]) { const th = el("th", name); th.scope = "col"; tr.append(th); }
    head.append(tr); table.append(head); const body = el("tbody");
    const ranked = entries.filter(e => score(e) != null).map(score).sort((a,b) => b-a);
    for (const entry of entries) {
      const row = el("tr"), value = score(entry), rank = value == null ? "—" : String(ranked.indexOf(value) + 1);
      row.append(el("td", rank, "rank"));
      const model = el("td"); model.append(link(entry.model, entry.code_url), el("small", `${entry.team} / ${entry.id.split("/")[2]} · ${entry.origin === "submitted" ? "Submitted" : "Baseline"}`)); row.append(model);
      row.append(el("td", pct(value), "score"));
      const coverage = el("td"); coverage.append(el("span", entry.scores.complete ? "Full suite" : "Partial suite", `badge ${entry.scores.complete ? "full" : ""}`), el("small", Object.keys(entry.scores.tasks).map(t => t.toUpperCase()).join(", "))); row.append(coverage);
      const evidence = el("td"); evidence.append(link("Review ↗", entry.review.evidence_url), document.createTextNode(" · "), link("Bundle ↗", entry.artifact_bundle.url));
      const details = el("details"); details.append(el("summary", "Cell results"));
      for (const [task, values] of Object.entries(entry.scores.tasks)) for (const [cell, result] of Object.entries(values.cells)) {
        details.append(el("p", `${task.toUpperCase()} / ${cell}: ${result.k}/${result.n} · ${pct(result.sr)} · 95% CI ${pct(result.ci95[0])}–${pct(result.ci95[1])}`));
      }
      evidence.append(details); row.append(evidence); body.append(row);
    }
    table.append(body); scroll.append(table); section.append(scroll); target.append(section);
  }
}
render();
