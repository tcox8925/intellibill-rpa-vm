# ==========================================================
#  scripts/mapping_server.py
# ==========================================================
"""
Mock UI simulating MyOps.

Shows:
  1. RULES CONFIG — editable rules_matrix fields (filter, identity, sheet, etc.)
     AI-suggested from existing carrier patterns and file inspection.
  2. COLUMN MAPPINGS — DATABASE columns (static) -> FILE columns (dropdown)
     AI-suggested by learning from other carriers' column mappings.

Reads/writes ai_acu_bob_mapping ONLY. Does NOT promote.
Runner promotes on next run.

Usage: #python scripts/mapping_server.py -> http://localhost:5050/review
"""

import json
import webbrowser
from datetime import datetime
from threading import Timer
from flask import Flask, request, jsonify, Response
from utils.db_utils import get_postgres_connection

PORT = 5050
AI_MAPPING_TABLE = "ops_srv.ai_acu_bob_mapping"

app = Flask(__name__)


@app.route("/api/pending")
def api_pending():
    import pandas as pd
    conn = get_postgres_connection()
    df = pd.read_sql(f"""
        SELECT mapping_id, carrier_name, process_type, file_name,
               file_column, canonical_column, confidence, ai_reasoning,
               suggested_rules, status
        FROM {AI_MAPPING_TABLE} WHERE status = 'pending_review'
        ORDER BY file_name, mapping_id
    """, conn)
    conn.close()

    files = {}
    for _, row in df.iterrows():
        fname = row["file_name"]
        if fname not in files:
            rules = {}
            try:
                rules = json.loads(row["suggested_rules"] or "{}")
            except Exception:
                pass
            file_headers = rules.pop("file_headers", [])
            files[fname] = {
                "carrier_name": row["carrier_name"],
                "process_type": row["process_type"],
                "rules": rules,
                "file_headers": file_headers,
                "mappings": [],
            }
        files[fname]["mappings"].append({
            "mapping_id": int(row["mapping_id"]),
            "database_column": row["canonical_column"] or "",
            "file_column": row["file_column"] or "NA",
            "confidence": row["confidence"] or "low",
            "reasoning": row["ai_reasoning"] or "",
        })

    return jsonify({"files": files})


@app.route("/api/submit", methods=["POST"])
def api_submit():
    data = request.json
    reviews = data.get("reviews", [])
    rules = data.get("rules")
    file_name = data.get("file_name")
    if not reviews:
        return jsonify({"error": "No reviews"}), 400

    conn = get_postgres_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    accepted = edited = rules_updated = 0

    try:
        # 1. Update column mappings
        for r in reviews:
            mid = r["mapping_id"]
            accepted_col = r["accepted_file_column"]
            original = r.get("original_file_column", "NA")
            was_edited = "Y" if accepted_col != original else "N"
            status = "edited" if was_edited == "Y" else "accepted"

            cur.execute(f"""
                UPDATE {AI_MAPPING_TABLE}
                SET accepted_column = %s, was_edited = %s, status = %s,
                    reviewed_by = 'user', reviewed_date = %s, modified_date = %s
                WHERE mapping_id = %s
            """, (accepted_col, was_edited, status, now, now, mid))

            if was_edited == "Y":
                edited += 1
            else:
                accepted += 1

        # 2. Update rules (stored in suggested_rules JSON on all rows for this file)
        if rules and file_name:
            cur.execute(f"""
                SELECT mapping_id, suggested_rules FROM {AI_MAPPING_TABLE}
                WHERE file_name = %s LIMIT 1
            """, (file_name,))
            row = cur.fetchone()
            if row:
                try:
                    current = json.loads(row[1] or "{}")
                except Exception:
                    current = {}
                file_headers = current.get("file_headers", [])
                updated_rules = {**rules, "file_headers": file_headers}
                rules_json = json.dumps(updated_rules)
                cur.execute(f"""
                    UPDATE {AI_MAPPING_TABLE}
                    SET suggested_rules = %s, modified_date = %s
                    WHERE file_name = %s
                """, (rules_json, now, file_name))
                rules_updated = 1

        conn.commit()
        cur.close()
        conn.close()

        msg = f"Saved! {accepted} accepted, {edited} edited."
        if rules_updated:
            msg += " Rules configuration updated."
        msg += " Will be promoted on next pipeline run."
        return jsonify({"success": True, "accepted": accepted, "edited": edited,
                        "rules_updated": rules_updated, "message": msg})
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 500


@app.route("/review")
def review_page():
    return Response(HTML_PAGE, mimetype="text/html")


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Carrier Configuration Review</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root { --bg:#0c0e14; --surface:#14161f; --surface2:#1a1d2a; --border:#252838; --text:#e2e4ea; --dim:#6b7089; --accent:#4f7df5; --green:#30d98b; --greenbg:#0d2e1f; --yellow:#f5c542; --yellowbg:#2a2210; --red:#f5564a; --redbg:#2a1210; --purple:#a78bfa; --purplebg:#1a1244; }
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);line-height:1.5;padding:24px}
.container{max-width:1200px;margin:0 auto}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;padding-bottom:24px;border-bottom:1px solid var(--border)}
header h1{font-size:22px;font-weight:700} header h1 span{color:var(--accent)}
.stats{font-size:13px;color:var(--dim)} .stats b{color:var(--text)}
.empty{text-align:center;padding:80px;color:var(--dim)} .empty h2{color:var(--text);margin-bottom:8px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin-bottom:20px;overflow:hidden}
.card-head{display:flex;align-items:center;gap:12px;padding:16px 20px;background:var(--surface2);border-bottom:1px solid var(--border)}
.card-head h2{font-size:14px;font-family:'JetBrains Mono',monospace;color:var(--accent);flex:1}
.badge{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.badge-acu{background:#1a2744;color:#5b9df5} .badge-bob{background:#2a1744;color:#a78bfa}
.section-label{padding:10px 20px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none}
.section-label:hover{filter:brightness(1.2)}
.section-label .arrow{transition:transform .2s} .section-label.collapsed .arrow{transform:rotate(-90deg)}
.rules-label{color:var(--purple);background:var(--purplebg)}
.mapping-label-bar{color:var(--accent);background:rgba(79,125,245,.08);cursor:default}
.rules-panel{padding:16px 20px;background:rgba(0,0,0,.15);border-bottom:1px solid var(--border)}
.rules-panel.hidden{display:none}
.rules-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px 20px}
.rule-group-label{grid-column:1/-1;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-top:8px;padding-bottom:4px;border-bottom:1px solid var(--border)}
.rule-group-label:first-child{margin-top:0}
.rule-field{display:flex;flex-direction:column;gap:4px}
.rule-field label{font-size:11px;color:var(--dim);font-weight:500}
.rule-field select,.rule-field input{background:var(--bg);color:var(--text);border:1px solid var(--border);padding:6px 10px;border-radius:6px;font-size:12px;font-family:'JetBrains Mono',monospace}
.rule-field select:focus,.rule-field input:focus{border-color:var(--accent);outline:none;box-shadow:0 0 0 3px rgba(79,125,245,.15)}
.rule-field .edited-rule{border-color:var(--yellow)!important;box-shadow:0 0 0 3px rgba(245,197,66,.1)!important}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:10px 16px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:1px;color:var(--dim);background:var(--surface2);border-bottom:1px solid var(--border)}
td{padding:10px 16px;font-size:13px;border-bottom:1px solid rgba(37,40,56,.5);vertical-align:middle}
tr:hover td{background:rgba(79,125,245,.03)}
.dbcol{font-family:'JetBrains Mono',monospace;font-size:12px;color:#7eb8f7;font-weight:500}
select.sel{background:var(--bg);color:var(--text);border:1px solid var(--border);padding:6px 12px;border-radius:6px;font-size:12px;font-family:'JetBrains Mono',monospace;min-width:220px;cursor:pointer}
select.sel:focus{border-color:var(--accent);outline:none;box-shadow:0 0 0 3px rgba(79,125,245,.15)}
select.sel.edited{border-color:var(--yellow);box-shadow:0 0 0 3px rgba(245,197,66,.1)}
.conf{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:600}
.c-high{background:var(--greenbg);color:var(--green)} .c-med{background:var(--yellowbg);color:var(--yellow)} .c-low{background:var(--redbg);color:var(--red)}
.reason{color:var(--dim);font-size:11px;max-width:250px}
.st{font-size:11px;font-weight:600;min-width:70px} .st-p{color:var(--dim)} .st-a{color:var(--green)} .st-e{color:var(--yellow)}
.actions{display:flex;gap:10px;padding:14px 20px;background:var(--surface2);border-top:1px solid var(--border)}
.btn{padding:8px 18px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif}
.btn:active{transform:scale(.97)}
.btn-acc{background:var(--greenbg);color:var(--green);border:1px solid rgba(48,217,139,.2)} .btn-acc:hover{background:#143d29}
.btn-sub{background:var(--accent);color:#fff} .btn-sub:hover{background:#3d6de0} .btn-sub:disabled{opacity:.4;cursor:not-allowed}
.toast{position:fixed;bottom:24px;right:24px;padding:14px 24px;border-radius:10px;font-size:13px;font-weight:600;z-index:100;transform:translateY(100px);opacity:0;transition:all .3s}
.toast.show{transform:translateY(0);opacity:1} .toast-ok{background:var(--greenbg);color:var(--green);border:1px solid rgba(48,217,139,.2)} .toast-err{background:var(--redbg);color:var(--red);border:1px solid rgba(245,86,74,.2)}
.modal-bg{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;z-index:200}
.modal-bg.show{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:28px;max-width:500px;width:90%}
.modal h3{font-size:16px;margin-bottom:16px}
.modal .row{display:flex;justify-content:space-between;padding:8px 0;font-size:13px;border-bottom:1px solid var(--border)}
.modal .row .l{color:var(--dim)} .modal .row .v{font-weight:600}
.modal .note{margin-top:16px;padding:12px;background:var(--bg);border-radius:8px;font-size:12px;color:var(--dim)}
.modal .close{margin-top:16px;width:100%;padding:10px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer}
.loading{text-align:center;padding:60px;color:var(--dim)}
.spin{width:32px;height:32px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:sp .8s linear infinite;margin:0 auto 16px}
@keyframes sp{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1><span>AI</span> Carrier Configuration Review</h1>
    <div class="stats" id="stats"></div>
  </header>
  <div id="content"><div class="loading"><div class="spin"></div>Loading...</div></div>
</div>
<div class="toast" id="toast"></div>
<div class="modal-bg" id="modal"><div class="modal" id="mc"></div></div>

<script>
const RULES_SCHEMA=[
  {field:'contract_type',label:'Contract Type',type:'select',options:['ACA','MDC','SUP'],group:'file'},
  {field:'file_format',label:'File Format',type:'select',options:['csv','xlsx','xls'],group:'file'},
  {field:'file_delimiter',label:'Delimiter',type:'select',options:['comma','pipe','tab'],group:'file'},
  {field:'file_encoding',label:'Encoding',type:'select',options:['utf-8','latin-1'],group:'file'},
  {field:'sheet_name',label:'Sheet Name',type:'text',ph:'Sheet name or NA',group:'file'},
  {field:'ignore_header_rows',label:'Skip Header Rows',type:'select',options:['0','1','2'],group:'file'},
  {field:'filter_rule_type',label:'Filter Type',type:'select',options:['ALL','STATUS','CONTAINS','DATE'],group:'filter'},
  {field:'filter_column',label:'Filter Column',type:'text',ph:'Canonical column name',group:'filter'},
  {field:'filter_values',label:'Filter Values',type:'text',ph:'Comma-separated values',group:'filter'},
  {field:'filter_scope',label:'Filter Scope',type:'select',options:['ROW','AGENT'],group:'filter'},
  {field:'primary_identity_field',label:'Primary Identity',type:'select',options:['NPN','WR','NAME'],group:'identity'},
  {field:'fallback_identity_field',label:'Fallback Identity',type:'select',options:['NAME','WR','NPN'],group:'identity'},
  {field:'default_appointment_type',label:'Default Appt Type',type:'text',ph:'Producer, Subproducer, or NA',group:'identity'},
  {field:'appointment_type_value_map',label:'Appt Type Map',type:'text',ph:'NULL:Producer|*:Subproducer',group:'identity'},
  {field:'rts_flag_applicable',label:'RTS Applicable',type:'select',options:['N','Y'],group:'identity'},
];
const GL={file:'File Configuration',filter:'Filter Configuration',identity:'Identity & Appointment'};

let D=null;
async function load(){
  try{const r=await fetch('/api/pending');D=await r.json();render()}
  catch(e){document.getElementById('content').innerHTML='<div class="empty"><h2>Error</h2><p>'+e+'</p></div>'}}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}

function render(){
  const{files}=D,fnames=Object.keys(files);
  if(!fnames.length){document.getElementById('content').innerHTML='<div class="empty"><h2>All clear</h2><p>No pending mappings.</p></div>';return}
  let tot=0;fnames.forEach(f=>tot+=files[f].mappings.length);
  document.getElementById('stats').innerHTML=`<b>${fnames.length}</b> file(s) &nbsp; <b>${tot}</b> mapping(s)`;

  let html='';
  fnames.forEach((fn,fi)=>{
    const inf=files[fn],bc=inf.process_type==='ACU'?'badge-acu':'badge-bob';
    const rules=inf.rules||{};

    // === RULES FORM ===
    let rh='',lg='';
    RULES_SCHEMA.forEach(rs=>{
      if(rs.group!==lg){rh+=`<div class="rule-group-label">${GL[rs.group]||rs.group}</div>`;lg=rs.group}
      const v=(rules[rs.field]||'').toString(),sv=esc(v),id=`r_${fi}_${rs.field}`;
      if(rs.type==='select'){
        let o=rs.options.map(op=>`<option value="${op}"${op===v?' selected':''}>${op}</option>`).join('');
        if(v&&!rs.options.includes(v))o=`<option value="${sv}" selected>${v}</option>`+o;
        rh+=`<div class="rule-field"><label>${rs.label}</label><select id="${id}" data-field="${rs.field}" data-orig="${sv}">${o}</select></div>`
      }else{
        rh+=`<div class="rule-field"><label>${rs.label}</label><input id="${id}" data-field="${rs.field}" data-orig="${sv}" value="${sv}" placeholder="${rs.ph||''}"></div>`
      }
    });

    // === COLUMN MAPPING TABLE ===
    const fh=inf.file_headers||[];
    const opts=['<option value="NA">NA</option>'].concat(fh.map(h=>`<option value="${esc(h)}">${esc(h)}</option>`)).join('');
    let rows='';
    inf.mappings.forEach(m=>{
      const cc=m.confidence==='high'?'c-high':m.confidence==='medium'?'c-med':'c-low';
      rows+=`<tr data-id="${m.mapping_id}" data-orig="${esc(m.file_column)}">
        <td class="dbcol">${esc(m.database_column)}</td>
        <td><select class="sel" data-id="${m.mapping_id}">${opts}</select></td>
        <td><span class="conf ${cc}">${m.confidence}</span></td>
        <td class="reason">${esc(m.reasoning)}</td>
        <td class="st st-p">pending</td></tr>`});

    html+=`<div class="card" data-file="${esc(fn)}" data-fi="${fi}">
      <div class="card-head"><h2>${esc(fn)}</h2><span class="badge ${bc}">${inf.process_type}</span></div>
      <div class="section-label rules-label" onclick="tog(this)"><span class="arrow">&#x25BE;</span> Rules Configuration (AI-Suggested)</div>
      <div class="rules-panel"><div class="rules-grid">${rh}</div></div>
      <div class="section-label mapping-label-bar"><span class="arrow">&#x25BE;</span> Column Mappings (AI-Suggested)</div>
      <table><thead><tr><th>Database Column</th><th>File Column (mapping)</th><th>Confidence</th><th>Reasoning</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="actions"><button class="btn btn-acc" onclick="accAll(this)">Accept All Mappings</button><button class="btn btn-sub" onclick="submit(this)">Submit All</button></div></div>`
  });

  document.getElementById('content').innerHTML=html;

  // Init column mapping dropdowns
  fnames.forEach(fn=>files[fn].mappings.forEach(m=>{
    const s=document.querySelector(`select[data-id="${m.mapping_id}"]`);
    if(s){
      s.value=m.file_column;
      s.addEventListener('change',function(){
        const row=this.closest('tr'),o=row.dataset.orig,sc=row.querySelector('.st');
        if(this.value!==o){this.classList.add('edited');sc.textContent='edited';sc.className='st st-e'}
        else{this.classList.remove('edited');sc.textContent='accepted';sc.className='st st-a'}
      })
    }
  }));

  // Init rule field listeners
  document.querySelectorAll('.rule-field select,.rule-field input').forEach(el=>{
    function chk(){if(el.value!==el.dataset.orig)el.classList.add('edited-rule');else el.classList.remove('edited-rule')}
    el.addEventListener('change',chk);el.addEventListener('input',chk)
  });

  // Filter field toggle
  document.querySelectorAll('select[data-field="filter_rule_type"]').forEach(sel=>{
    togFilter(sel);sel.addEventListener('change',function(){togFilter(this)})
  });
}

function togFilter(sel){
  const p=sel.closest('.rules-panel');
  const dis=sel.value==='ALL';
  ['filter_column','filter_values','filter_scope'].forEach(f=>{
    const el=p.querySelector(`[data-field="${f}"]`);
    if(el){el.disabled=dis;el.style.opacity=dis?'0.4':'1'}
  })
}

function tog(label){label.classList.toggle('collapsed');const p=label.nextElementSibling;p.classList.toggle('hidden')}
function accAll(b){b.closest('.card').querySelectorAll('tr[data-id]').forEach(r=>{const s=r.querySelector('.sel'),sc=r.querySelector('.st');if(!s.classList.contains('edited')){sc.textContent='accepted';sc.className='st st-a'}})}

async function submit(b){
  const card=b.closest('.card'),fn=card.dataset.file;
  const revs=[];
  card.querySelectorAll('tr[data-id]').forEach(r=>{
    const s=r.querySelector('.sel');
    revs.push({mapping_id:parseInt(r.dataset.id),accepted_file_column:s.value,original_file_column:r.dataset.orig})
  });
  const rules={};
  card.querySelectorAll('.rule-field select,.rule-field input').forEach(el=>{if(el.dataset.field)rules[el.dataset.field]=el.value});

  b.disabled=true;b.textContent='Submitting...';
  try{
    const res=await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({reviews:revs,rules:rules,file_name:fn})});
    const result=await res.json();
    if(result.success){
      showModal(result);card.style.opacity='.5';b.textContent='Done \u2713';
      card.querySelectorAll('.st').forEach(c=>{if(c.classList.contains('st-e'))c.textContent='edited \u2713';else{c.textContent='accepted \u2713';c.className='st st-a'}});
      card.querySelectorAll('.edited-rule').forEach(el=>el.classList.remove('edited-rule'))
    }else{toast(result.error||'Failed','err');b.disabled=false;b.textContent='Submit All'}
  }catch(e){toast('Error: '+e.message,'err');b.disabled=false;b.textContent='Submit All'}
}

function showModal(r){
  let rl=r.rules_updated?'<div class="row"><span class="l">Rules Config</span><span class="v" style="color:var(--purple)">Updated</span></div>':'';
  document.getElementById('mc').innerHTML=`<h3>Saved to Review Table</h3>
    <div class="row"><span class="l">Mappings Accepted</span><span class="v">${r.accepted}</span></div>
    <div class="row"><span class="l">Mappings Edited</span><span class="v" style="color:var(--yellow)">${r.edited}</span></div>${rl}
    <div class="note">${r.message}</div>
    <button class="close" onclick="document.getElementById('modal').classList.remove('show')">Done</button>`;
  document.getElementById('modal').classList.add('show')
}

function toast(m,t){const el=document.getElementById('toast');el.textContent=m;el.className='toast show toast-'+(t||'ok');setTimeout(()=>el.classList.remove('show'),4000)}
load();
</script>
</body></html>"""


if __name__ == "__main__":
    print(f"\n  AI Carrier Configuration Review Server")
    print(f"  http://localhost:{PORT}/review\n")
    Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}/review")).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)