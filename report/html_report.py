from datetime import datetime, timezone
from jinja2 import Environment, BaseLoader

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cloud Run Readiness Report — {{ cluster_type }} / {{ namespace }}</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f4f8; color: #1a202c; font-size: 14px; }
  .header { background: #1a202c; color: #fff; padding: 24px 32px; }
  .header h1 { font-size: 22px; font-weight: 700; }
  .header .meta { margin-top: 6px; color: #a0aec0; font-size: 12px; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: #fff; border-radius: 8px; padding: 16px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
  .card .num { font-size: 32px; font-weight: 700; }
  .card .label { font-size: 11px; color: #718096; text-transform: uppercase; letter-spacing: .05em; margin-top: 4px; }
  .card.ready .num { color: #276749; }
  .card.mostly .num { color: #2c7a7b; }
  .card.needs .num { color: #b7791f; }
  .card.notready .num { color: #c53030; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; align-items: center; }
  .filter-btn { border: 1px solid #e2e8f0; background: #fff; padding: 6px 14px; border-radius: 20px; cursor: pointer; font-size: 12px; font-weight: 500; transition: all .15s; }
  .filter-btn:hover, .filter-btn.active { background: #2d3748; color: #fff; border-color: #2d3748; }
  .search { margin-left: auto; border: 1px solid #e2e8f0; padding: 6px 12px; border-radius: 6px; width: 220px; font-size: 13px; }
  .service-card { background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 16px; overflow: hidden; }
  .service-header { padding: 16px 20px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; cursor: pointer; }
  .service-name { font-size: 16px; font-weight: 700; flex: 1; min-width: 160px; }
  .service-image { font-size: 11px; color: #718096; font-family: monospace; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .badge.ready { background: #c6f6d5; color: #276749; }
  .badge.mostly { background: #b2f5ea; color: #234e52; }
  .badge.needs { background: #fefcbf; color: #744210; }
  .badge.notready { background: #fed7d7; color: #742a2a; }
  .score-bar-wrap { width: 120px; }
  .score-label { font-size: 11px; color: #718096; }
  .score-bar { height: 6px; background: #e2e8f0; border-radius: 3px; margin-top: 4px; }
  .score-fill { height: 100%; border-radius: 3px; }
  .fill-ready { background: #48bb78; }
  .fill-mostly { background: #38b2ac; }
  .fill-needs { background: #ecc94b; }
  .fill-notready { background: #fc8181; }
  .meta-badges { display: flex; gap: 6px; flex-wrap: wrap; }
  .meta-badge { background: #edf2f7; border-radius: 4px; padding: 2px 8px; font-size: 11px; color: #4a5568; }
  .service-body { border-top: 1px solid #e2e8f0; padding: 0 20px; }
  .section { border-bottom: 1px solid #f7fafc; }
  .section-header { padding: 12px 0; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; cursor: pointer; display: flex; align-items: center; gap: 8px; }
  .section-header .count { background: #e2e8f0; border-radius: 10px; padding: 1px 8px; font-size: 11px; }
  .section-header.blocker-header .count { background: #fed7d7; color: #742a2a; }
  .section-header.warning-header .count { background: #fefcbf; color: #744210; }
  .section-header.pass-header .count { background: #c6f6d5; color: #276749; }
  .section-content { padding: 0 0 12px 0; display: none; }
  .section-content.open { display: block; }
  .check-item { padding: 8px 0; border-bottom: 1px solid #f7fafc; }
  .check-item:last-child { border-bottom: none; }
  .check-title { font-weight: 600; font-size: 13px; display: flex; align-items: center; gap: 8px; }
  .check-detail { color: #718096; font-size: 12px; margin-top: 3px; }
  .check-fix { background: #f0fff4; border-left: 3px solid #48bb78; padding: 6px 10px; margin-top: 6px; font-size: 12px; color: #276749; border-radius: 0 4px 4px 0; }
  .check-fix.blocker-fix { background: #fff5f5; border-left-color: #fc8181; color: #742a2a; }
  .check-fix.warning-fix { background: #fffff0; border-left-color: #f6e05e; color: #744210; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
  .dot-red { background: #fc8181; }
  .dot-yellow { background: #f6e05e; }
  .dot-green { background: #68d391; }
  .gcloud-section { padding: 16px 0; }
  .gcloud-label { font-size: 11px; font-weight: 600; color: #718096; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }
  .gcloud-cmd { background: #1a202c; color: #a0ec8b; padding: 14px 16px; border-radius: 6px; font-family: monospace; font-size: 12px; white-space: pre; overflow-x: auto; }
  .arrow { margin-left: auto; font-size: 12px; color: #a0aec0; transition: transform .2s; }
  .arrow.open { transform: rotate(180deg); }
  @media print {
    body { background: #fff; }
    .toolbar, .search { display: none; }
    .service-body { display: block !important; }
    .section-content { display: block !important; }
  }
</style>
</head>
<body>
<div class="header">
  <h1>Cloud Run Readiness Report</h1>
  <div class="meta">Cluster: <strong>{{ cluster_type }}</strong> &nbsp;|&nbsp; Namespace: <strong>{{ namespace }}</strong> &nbsp;|&nbsp; Generated: {{ timestamp }} &nbsp;|&nbsp; {{ results|length }} services analyzed</div>
</div>
<div class="container">
  <div class="summary-grid">
    <div class="card"><div class="num">{{ results|length }}</div><div class="label">Total</div></div>
    <div class="card ready"><div class="num">{{ results|selectattr('verdict','eq','READY')|list|length }}</div><div class="label">Ready</div></div>
    <div class="card mostly"><div class="num">{{ results|selectattr('verdict','eq','MOSTLY READY')|list|length }}</div><div class="label">Mostly Ready</div></div>
    <div class="card needs"><div class="num">{{ results|selectattr('verdict','eq','NEEDS WORK')|list|length }}</div><div class="label">Needs Work</div></div>
    <div class="card notready"><div class="num">{{ results|selectattr('verdict','eq','NOT READY')|list|length }}</div><div class="label">Not Ready</div></div>
    <div class="card"><div class="num">{{ avg_score }}</div><div class="label">Avg Score</div></div>
  </div>
  <div class="toolbar">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="READY">Ready</button>
    <button class="filter-btn" data-filter="MOSTLY READY">Mostly Ready</button>
    <button class="filter-btn" data-filter="NEEDS WORK">Needs Work</button>
    <button class="filter-btn" data-filter="NOT READY">Not Ready</button>
    <input class="search" type="text" placeholder="Filter by service name…" id="searchInput">
  </div>
  {% for r in results %}
  {% set vclass = 'ready' if r.verdict == 'READY' else ('mostly' if r.verdict == 'MOSTLY READY' else ('needs' if r.verdict == 'NEEDS WORK' else 'notready')) %}
  <div class="service-card" data-verdict="{{ r.verdict }}" data-name="{{ r.name }}">
    <div class="service-header" onclick="toggleCard(this)">
      <div>
        <div class="service-name">{{ r.name }}</div>
        <div class="service-image">{{ r.image }}</div>
      </div>
      <div class="meta-badges">
        <span class="meta-badge">ns: {{ r.namespace }}</span>
        <span class="meta-badge">{{ r.replicas }} replica{{ 's' if r.replicas != 1 else '' }}</span>
        <span class="meta-badge">mem: {{ r.memory }}</span>
        <span class="meta-badge">cpu: {{ r.cpu }}</span>
      </div>
      <span class="badge {{ vclass }}">{{ r.verdict }}</span>
      <div class="score-bar-wrap">
        <div class="score-label">Score: {{ r.score }}/100</div>
        <div class="score-bar"><div class="score-fill fill-{{ vclass }}" style="width:{{ r.score }}%"></div></div>
      </div>
      <span class="arrow" id="arrow-{{ loop.index }}">▼</span>
    </div>
    <div class="service-body" style="display:none" id="body-{{ loop.index }}">
      {% if r.blockers %}
      <div class="section">
        <div class="section-header blocker-header" onclick="toggleSection(this)">
          <span class="dot dot-red"></span> Blockers <span class="count">{{ r.blockers|length }}</span>
          <span class="arrow">▼</span>
        </div>
        <div class="section-content">
          {% for c in r.blockers %}
          <div class="check-item">
            <div class="check-title"><span class="dot dot-red"></span>{{ c.title }}</div>
            <div class="check-detail">{{ c.detail }}</div>
            {% if c.fix %}<div class="check-fix blocker-fix">Fix: {{ c.fix }}</div>{% endif %}
          </div>
          {% endfor %}
        </div>
      </div>
      {% endif %}
      {% if r.warnings %}
      <div class="section">
        <div class="section-header warning-header" onclick="toggleSection(this)">
          <span class="dot dot-yellow"></span> Warnings <span class="count">{{ r.warnings|length }}</span>
          <span class="arrow">▼</span>
        </div>
        <div class="section-content">
          {% for c in r.warnings %}
          <div class="check-item">
            <div class="check-title"><span class="dot dot-yellow"></span>{{ c.title }}</div>
            <div class="check-detail">{{ c.detail }}</div>
            {% if c.fix %}<div class="check-fix warning-fix">Fix: {{ c.fix }}</div>{% endif %}
          </div>
          {% endfor %}
        </div>
      </div>
      {% endif %}
      {% if r.passed %}
      <div class="section">
        <div class="section-header pass-header" onclick="toggleSection(this)">
          <span class="dot dot-green"></span> Passed <span class="count">{{ r.passed|length }}</span>
          <span class="arrow">▼</span>
        </div>
        <div class="section-content">
          {% for c in r.passed %}
          <div class="check-item">
            <div class="check-title"><span class="dot dot-green"></span>{{ c.title }}</div>
            <div class="check-detail">{{ c.detail }}</div>
          </div>
          {% endfor %}
        </div>
      </div>
      {% endif %}
      <div class="gcloud-section">
        <div class="gcloud-label">gcloud run deploy command</div>
        <pre class="gcloud-cmd">{{ r.gcloud_command }}</pre>
      </div>
    </div>
  </div>
  {% endfor %}
</div>
<script>
function toggleCard(header) {
  const card = header.closest('.service-card');
  const body = card.querySelector('[id^="body-"]');
  const arrow = card.querySelector('[id^="arrow-"]');
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : 'block';
  arrow.classList.toggle('open', !isOpen);
}
function toggleSection(header) {
  const content = header.nextElementSibling;
  const arrow = header.querySelector('.arrow');
  content.classList.toggle('open');
  if (arrow) arrow.classList.toggle('open', content.classList.contains('open'));
}
document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const f = btn.dataset.filter;
    document.querySelectorAll('.service-card').forEach(card => {
      card.style.display = (f === 'all' || card.dataset.verdict === f) ? '' : 'none';
    });
  });
});
document.getElementById('searchInput').addEventListener('input', function() {
  const q = this.value.toLowerCase();
  document.querySelectorAll('.service-card').forEach(card => {
    card.style.display = card.dataset.name.toLowerCase().includes(q) ? '' : 'none';
  });
});
</script>
</body>
</html>
"""


def generate(results, cluster_type: str, namespace: str, output_path: str):
    avg_score = 0
    if results:
        avg_score = round(sum(r.score for r in results) / len(results))

    env = Environment(loader=BaseLoader())
    tmpl = env.from_string(_TEMPLATE)
    html = tmpl.render(
        results=results,
        cluster_type=cluster_type,
        namespace=namespace,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        avg_score=avg_score,
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
