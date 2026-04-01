#!/usr/bin/env python3
"""
OpenAPI Data Model Visualizer
------------------------------
Reads an OpenAPI 3.x JSON file and generates an interactive HTML visualization
with entity graph (pan/zoom), API surface view, and full schema overview.

Usage:
    python openapi_visualizer.py <openapi.json> [-o output.html]

MIT License

Copyright (c) 2025
@vnknov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import json
import sys
import argparse
from pathlib import Path
from collections import defaultdict


# ---------------------------------------------------------------------------
# Color rules
# ---------------------------------------------------------------------------
COLOR_RULES = [
    (lambda k, s: _has_all_fields(s, {"metadata", "prShares", "mrShares"}), "purple"),
    (lambda k, s: "error" in k.lower(),                                      "red"),
    (lambda k, s: any(x in k for x in ("Scope", "Date", "Period", "Range", "Validity")), "amber"),
    (lambda k, s: any(x in k for x in ("Share", "Claim", "Counter")),        "teal"),
    (lambda k, s: any(x in k for x in ("Party", "Account", "Creator", "Publisher")), "coral"),
    (lambda k, s: any(x in k for x in ("Work", "Title", "Reference", "Note", "Original")), "blue"),
    (lambda k, s: True,                                                       "gray"),
]

COLOR_HEX = {
    "purple": "#7F77DD", "blue": "#378ADD", "teal": "#1D9E75",
    "coral":  "#D85A30", "amber": "#BA7517", "gray": "#888780", "red": "#E24B4A",
}

METHOD_COLOR = {
    "get": "#1D9E75", "post": "#378ADD", "put": "#BA7517",
    "patch": "#D85A30", "delete": "#E24B4A", "head": "#888780", "options": "#888780",
}


def _has_all_fields(schema: dict, fields: set) -> bool:
    return fields.issubset(schema.get("properties", {}).keys())


def get_color(key: str, schema: dict) -> str:
    for rule, color in COLOR_RULES:
        try:
            if rule(key, schema):
                return color
        except Exception:
            pass
    return "gray"


# ---------------------------------------------------------------------------
# Path / endpoint parsing  ->  which schemas appear at the API surface
# ---------------------------------------------------------------------------

def resolve_ref(ref: str) -> str:
    return ref.split("/")[-1]


# Some exporters emit "" instead of "$ref" as the key.
# get_ref() handles both transparently.
def get_ref(obj: dict) -> str | None:
    """Return the resolved schema name if obj contains a $ref (or malformed '' key)."""
    for key in ("$ref", ""):
        val = obj.get(key)
        if isinstance(val, str) and val.startswith("#/"):
            return resolve_ref(val)
    return None


def _schema_ref_from_content(content: dict) -> str | None:
    for mime, media in content.items():
        schema = media.get("schema", {})
        ref = get_ref(schema)
        if ref:
            return ref
        if schema.get("type") == "array":
            ref = get_ref(schema.get("items", {}))
            if ref:
                return ref
    return None


def parse_paths(spec: dict) -> tuple[list[dict], dict]:
    """
    Returns:
      endpoints      - list of {method, path, summary, request_schema, responses, tags}
      schema_usages  - schema_name -> [{method, path, context, status}]
    """
    endpoints = []
    schema_usages: dict[str, list] = defaultdict(list)

    for path, path_item in spec.get("paths", {}).items():
        for method, op in path_item.items():
            if method not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            entry = {
                "method": method.upper(),
                "path": path,
                "summary": op.get("description", op.get("summary", ""))[:120],
                "operation_id": op.get("operationId", ""),
                "tags": op.get("tags", []),
                "request_schema": None,
                "responses": [],
            }

            rb = op.get("requestBody", {})
            if rb:
                ref = _schema_ref_from_content(rb.get("content", {}))
                if ref:
                    entry["request_schema"] = ref
                    schema_usages[ref].append({
                        "method": method.upper(), "path": path,
                        "context": "request body", "status": None,
                    })

            for status, resp in op.get("responses", {}).items():
                ref = _schema_ref_from_content(resp.get("content", {}))
                if ref:
                    entry["responses"].append({"status": status, "schema": ref})
                    schema_usages[ref].append({
                        "method": method.upper(), "path": path,
                        "context": f"response {status}", "status": status,
                    })

            endpoints.append(entry)

    return endpoints, dict(schema_usages)


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------

def field_type_label(prop: dict) -> str:
    ref = get_ref(prop)
    if ref:
        return ref
    t = prop.get("type", "")
    fmt = prop.get("format", "")
    if t == "array":
        items = prop.get("items", {})
        inner = get_ref(items) or items.get("type", "any")
        return f"{inner}[]"
    if t == "object" and "additionalProperties" in prop:
        v = prop["additionalProperties"]
        vt = v.get("type", "any") if isinstance(v, dict) else "any"
        return f"Map<String,{vt}>"
    if "enum" in prop:
        vals = prop["enum"]
        s = " | ".join(str(v) for v in vals[:4])
        if len(vals) > 4:
            s += f" +{len(vals)-4}"
        return f"enum: {s}"
    if fmt:
        return f"{t}({fmt})"
    return t or "any"


def is_array_prop(prop: dict) -> bool:
    return prop.get("type") == "array"


def extract_edges(schema: dict, required_set: set) -> list[dict]:
    edges = []
    for fname, fprop in schema.get("properties", {}).items():
        ref = get_ref(fprop)
        arr = False
        if ref is None and fprop.get("type") == "array":
            ref = get_ref(fprop.get("items", {}))
            arr = True
        if ref:
            req = fname in required_set
            card = "1..*" if arr and req else "0..*" if arr else "1" if req else "0..1"
            edges.append({"ref": ref, "field": fname, "required": req,
                          "is_array": arr, "cardinality": card})
    return edges


def parse_schemas(spec: dict) -> tuple[dict, dict]:
    raw = spec.get("components", {}).get("schemas", {})
    objects, enums = {}, {}

    for key, schema in raw.items():
        if schema.get("type") in ("string", "integer") and "enum" in schema:
            vals = schema["enum"]
            sample = " | ".join(str(v) for v in vals[:10])
            if len(vals) > 10:
                sample += f"  (+{len(vals)-10})"
            enums[key] = {"desc": schema.get("description", ""),
                          "values": sample, "color": get_color(key, schema)}
            continue

        if schema.get("type") != "object" and "properties" not in schema:
            continue

        required_set = set(schema.get("required", []))
        fields = []
        for fname, fprop in schema.get("properties", {}).items():
            fields.append({"name": fname, "type": field_type_label(fprop),
                           "required": fname in required_set, "is_array": is_array_prop(fprop)})

        objects[key] = {
            "desc": schema.get("description", ""),
            "fields": fields,
            "color": get_color(key, schema),
            "edge_data": extract_edges(schema, required_set),
        }

    return objects, enums


# ---------------------------------------------------------------------------
# Layout: BFS from actual API-surface roots first
# ---------------------------------------------------------------------------

def compute_layout(objects: dict, edge_pairs: list[tuple],
                   surface_roots: set) -> dict:
    NODE_W = 150
    NODE_H = 44
    H_GAP  = 26
    V_GAP  = 92

    adj: dict[str, list] = defaultdict(list)
    for src, dst, _ in edge_pairs:
        adj[src].append(dst)

    seed = [k for k in surface_roots if k in objects]
    if not seed:
        seed = [sorted(objects.keys(), key=lambda k: -len(adj[k]))[0]] if objects else []

    visited: dict[str, int] = {}
    layers: dict[int, list] = defaultdict(list)
    queue = []
    for k in seed:
        visited[k] = 0
        layers[0].append(k)
        queue.append(k)

    while queue:
        curr = queue.pop(0)
        depth = visited[curr]
        for nb in adj.get(curr, []):
            if nb not in visited and nb in objects:
                visited[nb] = depth + 1
                layers[depth + 1].append(nb)
                queue.append(nb)

    max_layer = max(layers.keys()) if layers else 0
    unreached = [k for k in objects if k not in visited]
    for i, k in enumerate(unreached):
        layers[max_layer + 1 + i // 6].append(k)

    max_per_row = max(len(v) for v in layers.values()) if layers else 1
    canvas_w = max(960, max_per_row * (NODE_W + H_GAP) + 100)

    positions = {}
    for depth, nodes in layers.items():
        total_w = len(nodes) * NODE_W + (len(nodes) - 1) * H_GAP
        start_x = max(20, (canvas_w - total_w) // 2)
        y = depth * (NODE_H + V_GAP) + 36
        for i, k in enumerate(nodes):
            positions[k] = (start_x + i * (NODE_W + H_GAP), y)

    canvas_h = (max(y for _, y in positions.values()) + NODE_H + 70) if positions else 400
    return {"pos": positions, "w": canvas_w, "h": canvas_h,
            "node_w": NODE_W, "node_h": NODE_H}


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def jstr(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def build_html(objects: dict, enums: dict, endpoints: list[dict],
               schema_usages: dict, title: str, raw_schemas: dict) -> str:

    surface_roots = set(schema_usages.keys())

    edge_list = []
    seen: set = set()
    for src, data in objects.items():
        for ed in data["edge_data"]:
            tgt = ed["ref"]
            if tgt not in objects:
                continue
            key = (src, tgt, ed["field"])
            if key in seen:
                continue
            seen.add(key)
            edge_list.append({"s": src, "t": tgt, "field": ed["field"],
                               "cardinality": ed["cardinality"], "color": data["color"]})

    edge_pairs = [(e["s"], e["t"], e["field"]) for e in edge_list]
    layout = compute_layout(objects, edge_pairs, surface_roots)
    pos = layout["pos"]
    CW, CH = layout["w"], layout["h"]
    NW, NH = layout["node_w"], layout["node_h"]

    nodes_js = []
    for k, data in objects.items():
        x, y = pos.get(k, (20, 20))
        nodes_js.append({
            "k": k, "x": x, "y": y, "w": NW, "h": NH,
            "c": data["color"],
            "desc": (data["desc"] or "")[:220],
            "fields": data["fields"],
            "is_root": k in surface_roots,
            "usages": schema_usages.get(k, []),
        })

    enums_js = [{"k": k, "c": d["color"], "v": d["values"], "desc": (d["desc"] or "")[:140]}
                for k, d in enums.items()]

    # Prepare detailed entity data for All Entities tab
    entities_js = []
    for k, schema in raw_schemas.items():
        if k in objects:
            obj_data = objects[k]
            entity = {
                "name": k,
                "type": "object",
                "description": schema.get("description", ""),
                "color": obj_data["color"],
                "required": schema.get("required", []),
                "properties": {}
            }
            for prop_name, prop_data in schema.get("properties", {}).items():
                entity["properties"][prop_name] = {
                    "type": field_type_label(prop_data),
                    "description": prop_data.get("description", ""),
                    "required": prop_name in entity["required"],
                    "enum": prop_data.get("enum", []),
                    "format": prop_data.get("format", ""),
                    "example": prop_data.get("example", "")
                }
            entities_js.append(entity)
        elif k in enums:
            enum_data = enums[k]
            entity = {
                "name": k,
                "type": "enum",
                "description": schema.get("description", ""),
                "color": enum_data["color"],
                "enum": schema.get("enum", [])
            }
            entities_js.append(entity)

    used_colors = sorted({d["color"] for d in objects.values()})
    color_labels = {
        "purple": "Response root", "blue": "Work / Reference", "teal": "Shares / Claims",
        "coral": "Parties", "amber": "Scope / Dates", "gray": "Other", "red": "Errors",
    }
    legend_items = "".join(
        f'<div class="ld"><div class="lc" style="background:{COLOR_HEX[c]}"></div>'
        f'<span>{color_labels.get(c, c)}</span></div>'
        for c in used_colors
    )
    n_endpoints = len(endpoints)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} – Data Model</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#f0efe9;color:#1a1a18;
      line-height:1.5;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
header{{flex-shrink:0;display:flex;align-items:center;gap:12px;padding:10px 20px;
        background:#fff;border-bottom:1px solid #ddd}}
h1{{font-size:14px;font-weight:600}}
h1 span{{font-weight:400;color:#999;margin-left:8px;font-size:12px}}
.tab-bar{{flex-shrink:0;display:flex;border-bottom:1px solid #ddd;background:#fff;padding:0 20px}}
.tb{{font-size:13px;padding:8px 16px;cursor:pointer;color:#777;
     border-bottom:2.5px solid transparent;user-select:none;transition:all .15s}}
.tb.act{{color:#111;border-bottom-color:#111;font-weight:500}}
.tc{{display:none;flex:1;overflow:hidden}}
.tc.act{{display:flex;flex-direction:column}}

/* ── Graph tab ── */
.toolbar{{flex-shrink:0;display:flex;align-items:center;gap:10px;padding:8px 16px;
          background:#faf9f6;border-bottom:1px solid #e8e7e0;font-size:12px;flex-wrap:wrap}}
.legend{{display:flex;flex-wrap:wrap;gap:8px;margin-right:auto}}
.ld{{display:flex;align-items:center;gap:4px;font-size:11px;color:#666}}
.lc{{width:9px;height:9px;border-radius:2px;flex-shrink:0}}
.zoom-btn{{padding:3px 10px;border:1px solid #d0cfc8;border-radius:5px;background:#fff;
           cursor:pointer;font-size:12px;line-height:1.4;color:#444}}
.zoom-btn:hover{{background:#f5f4f0}}
#svg-wrap{{flex:1;overflow:hidden;cursor:grab;position:relative;background:#faf9f6}}
#svg-wrap.dragging{{cursor:grabbing}}
#main-svg{{display:block;width:100%;height:100%}}
.node-g{{cursor:pointer}}
.node-g:hover .node-rect{{filter:brightness(0.93)}}

/* ── Detail panel ── */
#dp{{position:absolute;right:14px;top:10px;width:282px;background:#fff;
     border:1px solid #d0cfc8;border-radius:12px;padding:14px 16px;display:none;
     z-index:50;max-height:calc(100% - 30px);overflow-y:auto;
     box-shadow:0 4px 20px rgba(0,0,0,.12)}}
.dp-hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}}
.dp-close{{background:none;border:none;cursor:pointer;color:#aaa;font-size:20px;line-height:1;padding:0}}
.dp-section{{font-size:10px;font-weight:700;color:#aaa;text-transform:uppercase;
             letter-spacing:.06em;margin:10px 0 4px;padding-top:8px;
             border-top:1px solid #f0efe8}}
.fr{{display:flex;align-items:center;gap:5px;padding:3px 0;border-top:1px solid #f2f1ea;font-size:12px}}
.fn{{font-weight:600;color:#1a1a18;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}}
.ft{{flex:1;text-align:right;color:#999;font-style:italic;white-space:nowrap;padding-left:4px;font-size:11px}}
.rb{{width:6px;height:6px;border-radius:50%;flex-shrink:0}}
.usage-row{{display:flex;align-items:center;gap:7px;padding:4px 0;
            border-top:1px solid #f2f1ea;font-size:11px}}
.method-pill{{font-size:9px;font-weight:700;padding:1px 6px;border-radius:4px;
              color:#fff;white-space:nowrap;flex-shrink:0}}
.usage-path{{font-family:monospace;font-size:10px;color:#555;
             overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}}
.usage-ctx{{font-size:9px;color:#aaa;white-space:nowrap}}

/* ── API Surface tab ── */
#ta{{overflow-y:auto;padding:16px 20px}}
.ep-group{{margin-bottom:20px}}
.ep-tag{{font-size:11px;font-weight:700;letter-spacing:.07em;color:#555;
         text-transform:uppercase;margin-bottom:8px;padding-bottom:5px;
         border-bottom:1px solid #e0dfd8}}
.ep-card{{background:#fff;border:1px solid #e0dfd8;border-radius:8px;margin-bottom:7px;overflow:hidden}}
.ep-head{{display:flex;align-items:flex-start;gap:10px;padding:9px 12px;cursor:pointer}}
.ep-head:hover{{background:#faf9f6}}
.ep-method{{font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;
            color:#fff;white-space:nowrap;flex-shrink:0;margin-top:1px}}
.ep-path{{font-family:monospace;font-size:12px;color:#1a1a18;font-weight:500;flex:1}}
.ep-summary{{font-size:11px;color:#999;margin-top:2px;line-height:1.4}}
.ep-body{{display:none;padding:4px 12px 10px;border-top:1px solid #f0efe8}}
.ep-body.open{{display:block}}
.schema-chip{{display:inline-flex;align-items:center;gap:5px;font-size:11px;
              padding:3px 9px;border-radius:5px;margin:3px 4px 3px 0;
              border:1px solid currentColor;cursor:pointer;transition:opacity .12s}}
.schema-chip:hover{{opacity:.7}}
.chip-role{{font-size:9px;font-weight:700;opacity:.65;text-transform:uppercase}}

/* ── Schema tab ── */
#ts{{overflow-y:auto;padding:16px 20px}}
.sg{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;max-width:1400px}}
.sc{{border:1px solid #e0dfd8;border-radius:8px;overflow:hidden;background:#fff}}
.sh{{padding:7px 10px;border-bottom:1px solid #e8e7e0;background:#faf9f6;
     display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px}}
.sn{{font-size:12px;font-weight:600}}
.sf{{padding:6px 10px}}
.sr{{display:flex;gap:5px;align-items:center;padding:2.5px 0;border-top:1px solid #f5f4f0;font-size:11px}}
.sr:first-child{{border-top:none}}
.fn2{{font-weight:600;color:#1a1a18}}
.ft2{{flex:1;text-align:right;color:#888;font-style:italic;font-size:11px}}
.card-badge{{font-size:10px;padding:1px 7px;border-radius:10px;background:#f0efe8;color:#777}}
.root-badge{{font-size:9px;padding:1px 8px;border-radius:10px;font-weight:700;color:#fff}}
.divider{{grid-column:1/-1;padding:14px 0 6px;font-size:12px;font-weight:600;
          color:#666;border-top:1px solid #e0dfd8;margin-top:6px}}
.enum-v{{font-size:11px;color:#888;line-height:1.8;word-break:break-all}}
</style>
</head>
<body>
<header>
  <h1>{title} – Data Model <span>Generated by openapi_visualizer.py</span></h1>
</header>
<div class="tab-bar">
  <div class="tb act" onclick="sw('g',this)">Entity Graph</div>
  <div class="tb" onclick="sw('a',this)">API Surface ({n_endpoints})</div>
  <div class="tb" onclick="sw('s',this)">All Schemas</div>
  <div class="tb" onclick="sw('e',this)">All Entities</div>
</div>

<!-- GRAPH TAB -->
<div id="tg" class="tc act">
  <div class="toolbar">
    <div class="legend">{legend_items}
      <div class="ld" style="margin-left:4px;color:#ccc">|</div>
      <div class="ld">
        <svg width="16" height="16"><rect x="1" y="1" width="14" height="14" rx="3"
          fill="none" stroke="#666" stroke-width="1.5" stroke-dasharray="4 2"/></svg>
        <span>API root</span>
      </div>
      <div class="ld" style="color:#bbb">● required &nbsp; ○ optional</div>
    </div>
    <button class="zoom-btn" onclick="zoomBy(0.2)">＋</button>
    <button class="zoom-btn" onclick="zoomBy(-0.2)">－</button>
    <button class="zoom-btn" onclick="resetView()">⊡ Reset</button>
    <span id="zoom-pct" style="font-size:11px;color:#aaa;min-width:36px">100%</span>
  </div>
  <div id="svg-wrap">
    <svg id="main-svg" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="ar" viewBox="0 0 10 10" refX="8" refY="5"
                markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke"
                stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </marker>
      </defs>
      <g id="canvas"></g>
    </svg>
  </div>
  <div id="dp">
    <div class="dp-hdr">
      <span id="dp-n" style="font-size:13px;font-weight:600"></span>
      <button class="dp-close"
              onclick="document.getElementById('dp').style.display='none'">×</button>
    </div>
    <p id="dp-d" style="font-size:11px;color:#888;margin:0 0 2px;line-height:1.5"></p>
    <div id="dp-f"></div>
  </div>
</div>

<!-- API SURFACE TAB -->
<div id="ta" class="tc">
  <div id="ep-list"></div>
</div>

<!-- SCHEMAS TAB -->
<div id="ts" class="tc">
  <div class="sg" id="sg"></div>
</div>

<!-- ALL ENTITIES TAB -->
<div id="te" class="tc">
  <div style="padding:16px 20px;border-bottom:1px solid #e0dfd8;background:#fff;position:sticky;top:0;z-index:10">
    <input type="text" id="entity-filter" placeholder="Filter entities by name (e.g., 'Counter', 'Result')..."
           style="width:100%;max-width:500px;padding:8px 12px;font-size:13px;border:1px solid #d0cfc8;
           border-radius:6px;font-family:system-ui,sans-serif"
           oninput="filterEntities(this.value)">
    <div style="margin-top:8px;font-size:11px;color:#888">
      <span id="entity-count"></span>
    </div>
  </div>
  <div style="overflow-y:auto;padding:16px 20px" id="entity-list"></div>
</div>

<script>
const COLORS    = {jstr(COLOR_HEX)};
const MCOL      = {jstr(METHOD_COLOR)};
const NODES     = {jstr(nodes_js)};
const ENUMS     = {jstr(enums_js)};
const EDGES     = {jstr(edge_list)};
const ENDPOINTS = {jstr(endpoints)};
const ENTITIES  = {jstr(entities_js)};
const CW = {CW}, CH = {CH};

// Tab switching
function sw(id, btn) {{
  document.querySelectorAll('.tb').forEach(t => t.classList.remove('act'));
  document.querySelectorAll('.tc').forEach(t => t.classList.remove('act'));
  btn.classList.add('act');
  document.getElementById('t' + id).classList.add('act');
}}

// SVG helpers
const NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs) {{
  const e = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
  return e;
}}
function svgTxt(txt, attrs) {{
  const e = svgEl('text', attrs); e.textContent = txt; return e;
}}

// Pan / Zoom
const wrap   = document.getElementById('svg-wrap');
const canvas = document.getElementById('canvas');
let tx = 0, ty = 0, scale = 1;
let dragging = false, sx = 0, sy = 0, stx = 0, sty = 0;

function applyT() {{
  canvas.setAttribute('transform', `translate(${{tx}},${{ty}}) scale(${{scale}})`);
  document.getElementById('zoom-pct').textContent = Math.round(scale * 100) + '%';
}}
function resetView() {{
  const r = wrap.getBoundingClientRect();
  scale = Math.min(1, r.width / CW, r.height / CH) * 0.92;
  tx = (r.width  - CW * scale) / 2;
  ty = (r.height - CH * scale) / 2;
  applyT();
}}
function zoomBy(d) {{
  const r = wrap.getBoundingClientRect();
  const cx = r.width / 2, cy = r.height / 2;
  const ns = Math.max(0.1, Math.min(4, scale + d));
  tx = cx - (cx - tx) * (ns / scale);
  ty = cy - (cy - ty) * (ns / scale);
  scale = ns; applyT();
}}
wrap.addEventListener('wheel', e => {{
  e.preventDefault();
  const r = wrap.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const ns = Math.max(0.1, Math.min(4, scale + (e.deltaY > 0 ? -0.1 : 0.1)));
  tx = mx - (mx - tx) * (ns / scale);
  ty = my - (my - ty) * (ns / scale);
  scale = ns; applyT();
}}, {{ passive: false }});
wrap.addEventListener('mousedown', e => {{
  if (e.target.closest('.node-g')) return;
  dragging = true; wrap.classList.add('dragging');
  sx = e.clientX; sy = e.clientY; stx = tx; sty = ty;
}});
window.addEventListener('mousemove', e => {{
  if (!dragging) return;
  tx = stx + (e.clientX - sx); ty = sty + (e.clientY - sy); applyT();
}});
window.addEventListener('mouseup', () => {{ dragging = false; wrap.classList.remove('dragging'); }});

// Build graph
const nodeMap = {{}};
NODES.forEach(n => nodeMap[n.k] = n);

// Draw edges
EDGES.forEach(e => {{
  const s = nodeMap[e.s], t = nodeMap[e.t];
  if (!s || !t) return;
  const col = COLORS[e.color] || '#aaa';
  const x1 = s.x + s.w / 2, y1 = s.y + s.h;
  const x2 = t.x + t.w / 2, y2 = t.y;
  const midY = (y1 + y2) / 2;

  canvas.appendChild(svgEl('path', {{
    d: `M${{x1}} ${{y1}} C${{x1}} ${{midY}} ${{x2}} ${{midY}} ${{x2}} ${{y2}}`,
    fill: 'none', stroke: col, 'stroke-width': '1', opacity: '0.28',
    'marker-end': 'url(#ar)'
  }}));

  // Field name label (60% along curve toward target)
  canvas.appendChild(svgTxt(e.field, {{
    x: x1 * 0.4 + x2 * 0.6, y: midY - 5,
    'text-anchor': 'middle', 'font-size': '9', fill: col, opacity: '0.65',
    'font-family': 'system-ui,sans-serif'
  }}));

  // Cardinality pill near arrow head
  canvas.appendChild(svgEl('rect', {{
    x: x2 - 18, y: y2 - 17, width: 36, height: 13, rx: '3',
    fill: col, opacity: '0.12'
  }}));
  canvas.appendChild(svgTxt(e.cardinality, {{
    x: x2, y: y2 - 7, 'text-anchor': 'middle',
    'font-size': '9', 'font-weight': '600', fill: col,
    'font-family': 'system-ui,sans-serif'
  }}));
}});

// Draw nodes
NODES.forEach(n => {{
  const col = COLORS[n.c] || '#888';
  const g = svgEl('g', {{ class: 'node-g' }});
  g.addEventListener('click', () => showDetail(n.k));

  // Dashed ring for root schemas
  if (n.is_root) {{
    g.appendChild(svgEl('rect', {{
      x: n.x - 6, y: n.y - 6, width: n.w + 12, height: n.h + 12, rx: '12',
      fill: 'none', stroke: col, 'stroke-width': '1.5', 'stroke-dasharray': '5 3', opacity: '0.55'
    }}));
  }}

  // Drop shadow
  g.appendChild(svgEl('rect', {{
    x: n.x + 2, y: n.y + 2, width: n.w, height: n.h, rx: '8',
    fill: col, opacity: '0.07'
  }}));

  // Card
  g.appendChild(svgEl('rect', {{
    class: 'node-rect',
    x: n.x, y: n.y, width: n.w, height: n.h, rx: '8',
    fill: '#ffffff', stroke: col, 'stroke-width': n.is_root ? '2' : '1.5'
  }}));

  // Color bar top
  g.appendChild(svgEl('rect', {{
    x: n.x, y: n.y, width: n.w, height: 5, rx: '4',
    fill: col, opacity: n.is_root ? '0.45' : '0.22'
  }}));

  // "API" badge for root schemas
  if (n.is_root) {{
    g.appendChild(svgEl('rect', {{
      x: n.x + 4, y: n.y - 8, width: 24, height: 13, rx: '3', fill: col
    }}));
    g.appendChild(svgTxt('API', {{
      x: n.x + 16, y: n.y + 0.5,
      'text-anchor': 'middle', 'dominant-baseline': 'central',
      'font-size': '7', 'font-weight': '800', fill: '#fff',
      'font-family': 'system-ui,sans-serif'
    }}));
  }}

  // Schema name
  const maxC = Math.floor(n.w / 7.5);
  const lbl = n.k.length > maxC ? n.k.slice(0, maxC - 1) + '…' : n.k;
  g.appendChild(svgTxt(lbl, {{
    x: n.x + n.w / 2, y: n.y + n.h / 2 + 2,
    'text-anchor': 'middle', 'dominant-baseline': 'central',
    'font-size': '11', 'font-weight': '600', fill: col,
    'font-family': 'system-ui,sans-serif'
  }}));

  // req+opt count
  const req = n.fields.filter(f => f.required).length;
  const opt = n.fields.filter(f => !f.required).length;
  g.appendChild(svgTxt(`${{req}}+${{opt}}`, {{
    x: n.x + n.w - 5, y: n.y + n.h - 5,
    'text-anchor': 'end', 'font-size': '8', fill: col, opacity: '0.48',
    'font-family': 'system-ui,sans-serif'
  }}));

  canvas.appendChild(g);
}});

window.addEventListener('load', resetView);
window.addEventListener('resize', resetView);

// Detail panel
function showDetail(key) {{
  const n = nodeMap[key];
  if (!n) return;
  const col = COLORS[n.c];
  const dn = document.getElementById('dp-n');
  if (n.is_root) {{
    dn.innerHTML = n.k +
      `<span style="font-size:9px;font-weight:700;padding:1px 7px;border-radius:8px;
        background:${{col}};color:#fff;margin-left:7px;vertical-align:middle">API ROOT</span>`;
  }} else {{
    dn.textContent = n.k;
  }}
  dn.style.color = col;
  document.getElementById('dp-d').textContent = n.desc || '';
  const df = document.getElementById('dp-f');
  df.innerHTML = '';

  // Fields
  const fhdr = document.createElement('div');
  fhdr.className = 'dp-section'; fhdr.textContent = 'Fields';
  df.appendChild(fhdr);

  const thdr = document.createElement('div');
  thdr.style = 'display:flex;gap:5px;padding:2px 0;font-size:10px;color:#bbb;font-weight:600;border-bottom:1px solid #eee;margin-bottom:1px';
  thdr.innerHTML = '<span style="width:6px"></span><span style="flex:1">name</span>' +
    '<span style="min-width:80px;text-align:right">type</span>' +
    '<span style="min-width:34px;text-align:right">card.</span>';
  df.appendChild(thdr);

  n.fields.forEach(f => {{
    const card = f.is_array ? (f.required ? '1..*' : '0..*') : (f.required ? '1' : '0..1');
    const row = document.createElement('div');
    row.className = 'fr';
    row.innerHTML =
      `<span class="rb" style="background:${{f.required ? col : '#ddd'}}"></span>` +
      `<span class="fn">${{f.name}}</span>` +
      `<span class="ft">${{f.type}}</span>` +
      `<span style="min-width:32px;text-align:right;font-size:10px;font-weight:600;` +
        `color:${{f.required ? col : '#bbb'}};padding-left:4px">${{card}}</span>`;
    df.appendChild(row);
  }});

  // API usages
  if (n.usages && n.usages.length) {{
    const uhdr = document.createElement('div');
    uhdr.className = 'dp-section';
    uhdr.textContent = `API endpoints (${{n.usages.length}})`;
    df.appendChild(uhdr);
    n.usages.forEach(u => {{
      const row = document.createElement('div');
      row.className = 'usage-row';
      const mc = MCOL[u.method.toLowerCase()] || '#888';
      row.innerHTML =
        `<span class="method-pill" style="background:${{mc}}">${{u.method}}</span>` +
        `<span class="usage-path">${{u.path}}</span>` +
        `<span class="usage-ctx">${{u.context}}</span>`;
      df.appendChild(row);
    }});
  }}

  document.getElementById('dp').style.display = 'block';
}}

// API Surface tab
const epList = document.getElementById('ep-list');
const byTag = {{}};
ENDPOINTS.forEach(ep => {{
  const tag = (ep.tags && ep.tags[0]) || 'Other';
  if (!byTag[tag]) byTag[tag] = [];
  byTag[tag].push(ep);
}});

Object.entries(byTag).forEach(([tag, eps]) => {{
  const grp = document.createElement('div');
  grp.className = 'ep-group';
  const tagEl = document.createElement('div');
  tagEl.className = 'ep-tag'; tagEl.textContent = tag;
  grp.appendChild(tagEl);

  eps.forEach(ep => {{
    const mc = MCOL[ep.method.toLowerCase()] || '#888';
    const card = document.createElement('div');
    card.className = 'ep-card';

    const head = document.createElement('div');
    head.className = 'ep-head';
    head.innerHTML =
      `<span class="ep-method" style="background:${{mc}}">${{ep.method}}</span>` +
      `<div style="min-width:0;flex:1">` +
        `<div class="ep-path">${{ep.path}}</div>` +
        `<div class="ep-summary">${{ep.summary || ep.operation_id}}</div>` +
      `</div>`;

    const body = document.createElement('div');
    body.className = 'ep-body';

    if (ep.request_schema) {{
      const nn = nodeMap[ep.request_schema];
      const c = nn ? COLORS[nn.c] : '#888';
      body.innerHTML += `<div style="margin:7px 0 3px;font-size:10px;font-weight:700;color:#aaa;letter-spacing:.05em">REQUEST</div>`;
      const chip = document.createElement('span');
      chip.className = 'schema-chip'; chip.style.color = c;
      chip.innerHTML = `<span class="chip-role">body</span>${{ep.request_schema}}`;
      chip.onclick = () => {{ sw('g', document.querySelector('.tb')); showDetail(ep.request_schema); }};
      body.appendChild(chip);
    }}

    if (ep.responses.length) {{
      body.innerHTML += `<div style="margin:8px 0 3px;font-size:10px;font-weight:700;color:#aaa;letter-spacing:.05em">RESPONSES</div>`;
      ep.responses.forEach(r => {{
        const nn = nodeMap[r.schema];
        const c = nn ? COLORS[nn.c] : '#888';
        const chip = document.createElement('span');
        chip.className = 'schema-chip'; chip.style.color = c;
        chip.innerHTML = `<span class="chip-role">${{r.status}}</span>${{r.schema}}`;
        chip.onclick = () => {{ sw('g', document.querySelector('.tb')); showDetail(r.schema); }};
        body.appendChild(chip);
      }});
    }}

    head.addEventListener('click', () => body.classList.toggle('open'));
    card.appendChild(head); card.appendChild(body);
    grp.appendChild(card);
  }});
  epList.appendChild(grp);
}});

// Schemas tab (root schemas first)
const sg = document.getElementById('sg');
const sorted = [...NODES].sort((a, b) => (b.is_root ? 1 : 0) - (a.is_root ? 1 : 0));

sorted.forEach(n => {{
  const col = COLORS[n.c];
  const card = document.createElement('div');
  card.className = 'sc';
  if (n.is_root) card.style.cssText += `border-color:${{col}};border-width:1.5px`;
  const req = n.fields.filter(f => f.required).length;
  const opt = n.fields.filter(f => !f.required).length;
  const shown = n.fields.slice(0, 7);
  const more = n.fields.length > 7
    ? `<div style="font-size:10px;color:#bbb;padding-top:3px">+${{n.fields.length - 7}} more</div>` : '';

  function row(f) {{
    const c = f.is_array ? (f.required ? '1..*' : '0..*') : (f.required ? '1' : '0..1');
    return `<div class="sr">
      <span class="rb" style="background:${{f.required ? col : '#ddd'}}"></span>
      <span class="fn2">${{f.name}}</span><span class="ft2">${{f.type}}</span>
      <span style="min-width:30px;text-align:right;font-size:10px;font-weight:600;color:${{f.required?col:'#ccc'}}">${{c}}</span>
    </div>`;
  }}

  const rootBadge = n.is_root
    ? `<span class="root-badge" style="background:${{col}}">API ROOT</span>` : '';

  card.innerHTML = `
    <div class="sh">
      <span class="sn" style="color:${{col}}">${{n.k}}</span>
      <div style="display:flex;align-items:center;gap:5px">
        ${{rootBadge}}<span class="card-badge">${{req}}✦ ${{opt}}○</span>
      </div>
    </div>
    <div class="sf">
      <div style="font-size:10px;color:#999;margin-bottom:4px;line-height:1.4">${{n.desc.slice(0,130)}}</div>
      ${{shown.map(row).join('')}}${{more}}
    </div>`;
  sg.appendChild(card);
}});

if (ENUMS.length) {{
  const d = document.createElement('div');
  d.className = 'divider'; d.textContent = 'Enum types'; sg.appendChild(d);
  ENUMS.forEach(e => {{
    const col = COLORS[e.c];
    const card = document.createElement('div'); card.className = 'sc';
    card.innerHTML = `
      <div class="sh"><span class="sn" style="color:${{col}}">${{e.k}}</span>
        <span class="card-badge">enum</span></div>
      <div class="sf">
        <div style="font-size:10px;color:#999;margin-bottom:3px">${{e.desc.slice(0,110)}}</div>
        <div class="enum-v">${{e.v}}</div>
      </div>`;
    sg.appendChild(card);
  }});
}}

// All Entities tab - render detailed entity information
function renderEntities(filter = '') {{
  const entityList = document.getElementById('entity-list');
  entityList.innerHTML = '';

  const filterLower = filter.toLowerCase();
  const filtered = ENTITIES.filter(e =>
    filter === '' || e.name.toLowerCase().includes(filterLower)
  );

  document.getElementById('entity-count').textContent =
    filter === ''
      ? `Showing all ${{ENTITIES.length}} entities`
      : `Showing ${{filtered.length}} of ${{ENTITIES.length}} entities`;

  filtered.forEach(entity => {{
    const col = COLORS[entity.color] || '#888';
    const card = document.createElement('div');
    card.style.cssText = 'background:#fff;border:1px solid #e0dfd8;border-radius:10px;margin-bottom:16px;overflow:hidden';

    // Header
    const header = document.createElement('div');
    header.style.cssText = `padding:12px 16px;background:${{col}}15;border-bottom:1px solid #e0dfd8`;
    header.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <span style="font-size:16px;font-weight:600;color:${{col}}">${{entity.name}}</span>
        <span style="font-size:11px;padding:2px 9px;border-radius:12px;background:${{col}};color:#fff;font-weight:600">
          ${{entity.type.toUpperCase()}}
        </span>
      </div>
      ${{entity.description ? `<div style="font-size:12px;color:#666;line-height:1.5">${{entity.description}}</div>` : ''}}
    `;
    card.appendChild(header);

    // Content
    const content = document.createElement('div');
    content.style.cssText = 'padding:14px 16px';

    if (entity.type === 'enum') {{
      // Enum values
      const enumSection = document.createElement('div');
      enumSection.innerHTML = `
        <div style="font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">
          Values (${{entity.enum.length}})
        </div>
      `;

      const valueGrid = document.createElement('div');
      valueGrid.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px';
      entity.enum.forEach(val => {{
        const chip = document.createElement('span');
        chip.style.cssText = `font-size:11px;padding:4px 10px;border-radius:5px;background:#f5f4f0;
          border:1px solid #e0dfd8;color:#555;font-family:monospace`;
        chip.textContent = val;
        valueGrid.appendChild(chip);
      }});

      enumSection.appendChild(valueGrid);
      content.appendChild(enumSection);
    }} else {{
      // Object properties
      const propsSection = document.createElement('div');
      const props = Object.entries(entity.properties);

      propsSection.innerHTML = `
        <div style="font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">
          Properties (${{props.length}})
        </div>
      `;

      props.forEach(([propName, propData]) => {{
        const propCard = document.createElement('div');
        propCard.style.cssText = 'margin-bottom:10px;padding:10px;border:1px solid #f0efe8;border-radius:6px;background:#fafaf9';

        const typeColor = propData.required ? col : '#999';
        const requiredBadge = propData.required
          ? `<span style="font-size:9px;padding:2px 7px;border-radius:10px;background:${{col}};color:#fff;font-weight:700;margin-left:6px">REQUIRED</span>`
          : `<span style="font-size:9px;padding:2px 7px;border-radius:10px;background:#e0dfd8;color:#888;margin-left:6px">optional</span>`;

        let enumValues = '';
        if (propData.enum && propData.enum.length > 0) {{
          const displayEnums = propData.enum.slice(0, 8);
          const moreCount = propData.enum.length > 8 ? ` +${{propData.enum.length - 8}} more` : '';
          enumValues = `
            <div style="margin-top:6px">
              <div style="font-size:10px;color:#888;margin-bottom:4px">Possible values:</div>
              <div style="display:flex;flex-wrap:wrap;gap:4px">
                ${{displayEnums.map(v =>
                  `<span style="font-size:10px;padding:2px 7px;border-radius:4px;background:#fff;border:1px solid #d0cfc8;font-family:monospace">${{v}}</span>`
                ).join('')}}
                ${{moreCount ? `<span style="font-size:10px;color:#aaa;padding:2px 7px">${{moreCount}}</span>` : ''}}
              </div>
            </div>
          `;
        }}

        const formatInfo = propData.format ? ` <span style="font-size:10px;color:#aaa">(${{propData.format}})</span>` : '';
        const exampleInfo = propData.example ? `
          <div style="margin-top:6px;font-size:10px">
            <span style="color:#888">Example:</span>
            <code style="padding:2px 6px;background:#fff;border:1px solid #e0dfd8;border-radius:3px;font-family:monospace">${{propData.example}}</code>
          </div>
        ` : '';

        propCard.innerHTML = `
          <div style="display:flex;align-items:center;margin-bottom:4px">
            <span style="font-size:13px;font-weight:600;color:#1a1a18">${{propName}}</span>
            ${{requiredBadge}}
          </div>
          <div style="font-size:11px;color:${{typeColor}};font-family:monospace;margin-bottom:4px">
            ${{propData.type}}${{formatInfo}}
          </div>
          ${{propData.description ?
            `<div style="font-size:11px;color:#666;line-height:1.5;margin-top:4px">${{propData.description}}</div>`
            : ''}}
          ${{enumValues}}
          ${{exampleInfo}}
        `;

        propsSection.appendChild(propCard);
      }});

      content.appendChild(propsSection);
    }}

    card.appendChild(content);
    entityList.appendChild(card);
  }});
}}

function filterEntities(value) {{
  renderEntities(value);
}}

// Initialize entities tab
renderEntities();

</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML data-model visualization from an OpenAPI JSON spec."
    )
    parser.add_argument("input", help="Path to OpenAPI JSON file")
    parser.add_argument("-o", "--output", help="Output HTML file (default: <input>_model.html)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding="utf-8") as f:
        spec = json.load(f)

    api_title   = spec.get("info", {}).get("title", input_path.stem)
    api_version = spec.get("info", {}).get("version", "")
    display_title = f"{api_title} v{api_version}" if api_version else api_title

    print(f"  Parsing: {api_title} …")
    raw_schemas = spec.get("components", {}).get("schemas", {})
    objects, enums = parse_schemas(spec)
    endpoints, schema_usages = parse_paths(spec)
    root_count = len([k for k in schema_usages if k in objects])

    print(f"  Found: {len(objects)} object schemas, {len(enums)} enum types")
    print(f"  API surface: {len(endpoints)} endpoints, {root_count} root-level schemas")

    output_path = Path(args.output) if args.output else Path(f"{input_path.stem}_model.html")
    html = build_html(objects, enums, endpoints, schema_usages, display_title, raw_schemas)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Output: {output_path}  ({len(html)//1024} KB)")
    print(f"  Open in browser: file://{output_path.resolve()}")


if __name__ == "__main__":
    main()
