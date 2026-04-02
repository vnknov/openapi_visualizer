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

import html
import json
import sys
import argparse
import hashlib
import base64
from pathlib import Path
from collections import defaultdict

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


# ---------------------------------------------------------------------------
# Color rules - Universal structural patterns
# ---------------------------------------------------------------------------
# Colors are assigned based on schema characteristics rather than domain-specific names:
# - Root entities (API surface): purple - most important, top of hierarchy
# - Large complex entities: blue - core domain objects
# - Error/Response wrappers: red - special handling types
# - Enumerations/constants: amber - configuration/lookup types
# - Small entities: teal - value objects, DTOs
# - Medium entities: coral - supporting entities
# - Leaf entities (no refs): green - simple data containers
# - Default: gray - uncategorized

COLOR_HEX = {
    "purple": "#7F77DD",  # Root/API surface entities
    "blue": "#378ADD",     # Large complex entities (8+ fields)
    "red": "#E24B4A",      # Error/result wrappers
    "amber": "#BA7517",    # Small enums/config (1-2 fields)
    "teal": "#1D9E75",     # Small entities (3-5 fields)
    "coral": "#D85A30",    # Medium entities (6-7 fields)
    "green": "#2D9574",    # Leaf entities (no relationships)
    "gray": "#888780",     # Default/uncategorized
}

METHOD_COLOR = {
    "get": "#1D9E75", "post": "#378ADD", "put": "#BA7517",
    "patch": "#D85A30", "delete": "#E24B4A", "head": "#888780", "options": "#888780",
}


def _count_refs(schema: dict) -> int:
    """Count number of reference fields (relationships) in a schema."""
    count = 0
    for prop in schema.get("properties", {}).values():
        if isinstance(prop, dict):
            if "$ref" in prop or "" in prop:
                count += 1
            elif prop.get("type") == "array":
                items = prop.get("items", {})
                if isinstance(items, dict) and ("$ref" in items or "" in items):
                    count += 1
    return count


def _is_error_or_wrapper(key: str, schema: dict) -> bool:
    """Detect error responses or generic wrapper types."""
    key_lower = key.lower()
    props = schema.get("properties", {})
    prop_names = set(props.keys())

    # Common error patterns
    if any(x in key_lower for x in ("error", "exception", "fault", "problem")):
        return True

    # Response wrapper patterns (has generic fields like data, status, message)
    wrapper_indicators = {"data", "status", "message", "code", "error", "errors", "success"}
    if len(prop_names & wrapper_indicators) >= 2 and len(props) <= 5:
        return True

    return False


def get_color(key: str, schema: dict, is_root: bool = False, incoming_refs: int = 0) -> str:
    """
    Assign color based on universal structural characteristics.

    Args:
        key: Schema name
        schema: Schema definition
        is_root: Whether this schema appears at the API surface
        incoming_refs: Number of other schemas referencing this one (popularity)
    """
    props = schema.get("properties", {})
    field_count = len(props)
    required_count = len(schema.get("required", []))
    ref_count = _count_refs(schema)

    # Priority 1: API root schemas (appears at API surface)
    if is_root:
        return "purple"

    # Priority 2: Error and wrapper types
    if _is_error_or_wrapper(key, schema):
        return "red"

    # Priority 3: Leaf entities (no outgoing references) - simple data containers
    if ref_count == 0 and field_count > 0:
        return "green"

    # Priority 4: Size-based coloring for regular entities
    # Large complex entities - likely core domain models
    if field_count >= 8:
        return "blue"

    # Medium entities
    if field_count >= 6:
        return "coral"

    # Small entities - likely DTOs or value objects
    if field_count >= 3:
        return "teal"

    # Very small entities - likely enums, configs, or simple types
    if field_count >= 1:
        return "amber"

    # Default fallback
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

    # FIX: HTML-escape the title before embedding it in the page.
    safe_title = html.escape(title)

    surface_roots = set(schema_usages.keys())

    # Recompute colors with root information and schema popularity
    # Count incoming references for each schema (how many others reference it)
    incoming_refs = defaultdict(int)
    for obj_data in objects.values():
        for edge in obj_data["edge_data"]:
            incoming_refs[edge["ref"]] += 1

    # Update colors based on structural analysis
    for key, obj_data in objects.items():
        is_root = key in surface_roots
        schema = raw_schemas.get(key, {})
        obj_data["color"] = get_color(key, schema, is_root=is_root, incoming_refs=incoming_refs.get(key, 0))

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
                    "example": str(prop_data.get("example", "")) if prop_data.get("example", "") != "" else ""
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

    # Build legend with universal color meanings
    used_colors = sorted({d["color"] for d in objects.values()})
    color_labels = {
        "purple": "API Root",        # Schemas at API surface
        "blue": "Complex (8+)",      # Large entities
        "coral": "Medium (6-7)",     # Medium entities
        "teal": "Small (3-5)",       # Small entities
        "amber": "Minimal (1-2)",    # Very small entities
        "green": "Leaf",             # No relationships
        "red": "Error/Wrapper",      # Error responses
        "gray": "Other",             # Uncategorized
    }
    legend_items = "".join(
        f'<div class="ld"><div class="lc" style="background:{html.escape(COLOR_HEX[c])}"></div>'
        f'<span>{html.escape(color_labels.get(c, c))}</span></div>'
        for c in used_colors
    )
    n_endpoints = len(endpoints)

    # Build the script content first so we can compute its hash for CSP
    script_content = f"""const COLORS    = {jstr(COLOR_HEX)};
const MCOL      = {jstr(METHOD_COLOR)};
const NODES     = {jstr(nodes_js)};
const ENUMS     = {jstr(enums_js)};
const EDGES     = {jstr(edge_list)};
const ENDPOINTS = {jstr(endpoints)};
const ENTITIES  = {jstr(entities_js)};
const CW = {CW}, CH = {CH};

// FIX: Central HTML-escape helper — applied to all schema data going into innerHTML.
// Prevents XSS if schema data ever contains characters like <, >, &, ", '.
function escapeHtml(s) {{
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}}

// Tab switching
function sw(id, btn) {{
  document.querySelectorAll('.tb').forEach(t => t.classList.remove('act'));
  document.querySelectorAll('.tc').forEach(t => t.classList.remove('act'));
  btn.classList.add('act');
  document.getElementById('t' + id).classList.add('act');
}}

// FIX: Attach tab-switch listeners via addEventListener instead of inline onclick.
document.querySelectorAll('.tb').forEach(btn => {{
  btn.addEventListener('click', () => sw(btn.dataset.tab, btn));
}});

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

// FIX: Attach zoom/reset listeners via addEventListener instead of inline onclick.
document.getElementById('zoom-in').addEventListener('click', () => zoomBy(0.2));
document.getElementById('zoom-out').addEventListener('click', () => zoomBy(-0.2));
document.getElementById('zoom-reset').addEventListener('click', resetView);

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
wrap.addEventListener('click', e => {{
  // Clear highlights when clicking on empty space (not on a node)
  if (!e.target.closest('.node-g')) {{
    clickedNode = null;
    updateHighlights();
  }}
}});
window.addEventListener('mousemove', e => {{
  if (!dragging) return;
  tx = stx + (e.clientX - sx); ty = sty + (e.clientY - sy); applyT();
}});
window.addEventListener('mouseup', () => {{ dragging = false; wrap.classList.remove('dragging'); }});

// Build graph
const nodeMap = {{}};
NODES.forEach(n => nodeMap[n.k] = n);

// Build adjacency lists for path finding
const outgoing = {{}}; // node -> [targets]
const incoming = {{}}; // node -> [sources]
EDGES.forEach(e => {{
  if (!outgoing[e.s]) outgoing[e.s] = [];
  if (!incoming[e.t]) incoming[e.t] = [];
  outgoing[e.s].push(e.t);
  incoming[e.t].push(e.s);
}});

// Find all nodes in connected paths from a given node
// Follows incoming edges upward (stops at nodes with no incoming)
// Follows all outgoing edges downward
function findConnectedPaths(nodeKey) {{
  const connected = new Set([nodeKey]);

  // Follow all outgoing edges recursively (downward)
  function followOutgoing(node) {{
    if (outgoing[node]) {{
      outgoing[node].forEach(target => {{
        if (!connected.has(target)) {{
          connected.add(target);
          followOutgoing(target);
        }}
      }});
    }}
  }}

  // Follow incoming edges upward (stop at nodes with no incoming)
  function followIncoming(node) {{
    if (incoming[node]) {{
      incoming[node].forEach(source => {{
        if (!connected.has(source)) {{
          connected.add(source);
          // Only continue upward if this source has incoming edges
          followIncoming(source);
        }}
      }});
    }}
  }}

  // Start from selected node
  followOutgoing(nodeKey);
  followIncoming(nodeKey);

  return connected;
}}

// State for highlighting
let clickedNode = null;
let hoveredNode = null;

// Update highlights based on current state
function updateHighlights() {{
  const activeNode = clickedNode || hoveredNode;
  const connectedNodes = activeNode ? findConnectedPaths(activeNode) : null;

  // Update edges
  edgeElements.forEach((edgeData, idx) => {{
    const {{ path, label, cardBg, cardText, sourceKey, targetKey }} = edgeData;
    const isHighlighted = connectedNodes &&
      connectedNodes.has(sourceKey) && connectedNodes.has(targetKey);

    if (connectedNodes) {{
      if (isHighlighted) {{
        path.setAttribute('opacity', '0.85');
        path.setAttribute('stroke-width', '2.5');
        label.setAttribute('opacity', '1');
        label.setAttribute('font-weight', '700');
        cardBg.setAttribute('opacity', '0.35');
        cardText.setAttribute('font-weight', '700');
      }} else {{
        path.setAttribute('opacity', '0.08');
        path.setAttribute('stroke-width', '1');
        label.setAttribute('opacity', '0.2');
        label.setAttribute('font-weight', '400');
        cardBg.setAttribute('opacity', '0.05');
        cardText.setAttribute('font-weight', '600');
      }}
    }} else {{
      // Reset to default
      path.setAttribute('opacity', '0.28');
      path.setAttribute('stroke-width', '1');
      label.setAttribute('opacity', '0.65');
      label.setAttribute('font-weight', '400');
      cardBg.setAttribute('opacity', '0.12');
      cardText.setAttribute('font-weight', '600');
    }}
  }});

  // Update nodes
  nodeElements.forEach((nodeData) => {{
    const {{ nodeKey, rect }} = nodeData;
    const isHighlighted = connectedNodes && connectedNodes.has(nodeKey);

    if (connectedNodes) {{
      if (isHighlighted) {{
        rect.style.filter = 'brightness(1.0)';
        rect.style.opacity = '1';
      }} else {{
        rect.style.filter = 'brightness(0.97)';
        rect.style.opacity = '0.3';
      }}
    }} else {{
      // Reset to default
      rect.style.filter = '';
      rect.style.opacity = '1';
    }}
  }});
}}

// Store edge and node elements for highlighting
const edgeElements = [];
const nodeElements = [];

// Draw edges
EDGES.forEach(e => {{
  const s = nodeMap[e.s], t = nodeMap[e.t];
  if (!s || !t) return;
  const col = COLORS[e.color] || '#aaa';
  const x1 = s.x + s.w / 2, y1 = s.y + s.h;
  const x2 = t.x + t.w / 2, y2 = t.y;
  const midY = (y1 + y2) / 2;

  const path = svgEl('path', {{
    d: `M${{x1}} ${{y1}} C${{x1}} ${{midY}} ${{x2}} ${{midY}} ${{x2}} ${{y2}}`,
    fill: 'none', stroke: col, 'stroke-width': '1', opacity: '0.28',
    'marker-end': 'url(#ar)'
  }});
  canvas.appendChild(path);

  // Field name label (60% along curve toward target)
  // svgTxt uses textContent internally — safe even without escaping
  const label = svgTxt(e.field, {{
    x: x1 * 0.4 + x2 * 0.6, y: midY - 5,
    'text-anchor': 'middle', 'font-size': '9', fill: col, opacity: '0.65',
    'font-family': 'system-ui,sans-serif'
  }});
  canvas.appendChild(label);

  // Cardinality pill near arrow head
  const cardBg = svgEl('rect', {{
    x: x2 - 18, y: y2 - 17, width: 36, height: 13, rx: '3',
    fill: col, opacity: '0.12'
  }});
  canvas.appendChild(cardBg);

  const cardText = svgTxt(e.cardinality, {{
    x: x2, y: y2 - 7, 'text-anchor': 'middle',
    'font-size': '9', 'font-weight': '600', fill: col,
    'font-family': 'system-ui,sans-serif'
  }});
  canvas.appendChild(cardText);

  // Store references for highlighting
  edgeElements.push({{
    path, label, cardBg, cardText,
    sourceKey: e.s, targetKey: e.t
  }});
}});

// Draw nodes
NODES.forEach(n => {{
  const col = COLORS[n.c] || '#888';
  const g = svgEl('g', {{ class: 'node-g' }});

  // Click handler: select node and show detail
  g.addEventListener('click', (e) => {{
    e.stopPropagation();
    clickedNode = n.k;
    updateHighlights();
    showDetail(n.k);
  }});

  // Hover handlers
  g.addEventListener('mouseenter', () => {{
    hoveredNode = n.k;
    updateHighlights();
  }});

  g.addEventListener('mouseleave', () => {{
    hoveredNode = null;
    updateHighlights();
  }});

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
  const rect = svgEl('rect', {{
    class: 'node-rect',
    x: n.x, y: n.y, width: n.w, height: n.h, rx: '8',
    fill: '#ffffff', stroke: col, 'stroke-width': n.is_root ? '2' : '1.5'
  }});
  g.appendChild(rect);

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

  // Schema name — svgTxt uses textContent so truncated label is safe
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

  // Store node reference for highlighting
  nodeElements.push({{
    nodeKey: n.k,
    rect: rect
  }});
}});

window.addEventListener('load', resetView);
window.addEventListener('resize', resetView);

// Detail panel
function showDetail(key) {{
  const n = nodeMap[key];
  if (!n) return;
  const col = COLORS[n.c];
  const dn = document.getElementById('dp-n');

  // FIX: Build the header safely using DOM methods instead of innerHTML.
  dn.textContent = '';
  dn.style.color = col;
  const nameSpan = document.createElement('span');
  nameSpan.textContent = n.k;         // textContent — no XSS risk
  dn.appendChild(nameSpan);
  if (n.is_root) {{
    const badge = document.createElement('span');
    badge.style.cssText =
      `font-size:9px;font-weight:700;padding:1px 7px;border-radius:8px;` +
      `background:${{col}};color:#fff;margin-left:7px;vertical-align:middle`;
    badge.textContent = 'API ROOT';   // static string — safe
    dn.appendChild(badge);
  }}

  document.getElementById('dp-d').textContent = n.desc || '';
  const df = document.getElementById('dp-f');
  df.innerHTML = '';

  // Fields header
  const fhdr = document.createElement('div');
  fhdr.className = 'dp-section'; fhdr.textContent = 'Fields';
  df.appendChild(fhdr);

  // FIX: Use style.cssText (standard) instead of assigning to .style (non-standard).
  const thdr = document.createElement('div');
  thdr.style.cssText = 'display:flex;gap:5px;padding:2px 0;font-size:10px;color:#bbb;font-weight:600;border-bottom:1px solid #eee;margin-bottom:1px';
  thdr.innerHTML =
    '<span style="width:6px"></span><span style="flex:1">name</span>' +
    '<span style="min-width:80px;text-align:right">type</span>' +
    '<span style="min-width:34px;text-align:right">card.</span>';
  df.appendChild(thdr);

  // FIX: Build field rows with DOM methods so f.name and f.type are never
  // parsed as HTML — they come from schema property names and type strings.
  n.fields.forEach(f => {{
    const card = f.is_array ? (f.required ? '1..*' : '0..*') : (f.required ? '1' : '0..1');
    const row = document.createElement('div');
    row.className = 'fr';

    const dot = document.createElement('span');
    dot.className = 'rb';
    dot.style.background = f.required ? col : '#ddd';

    const nameEl = document.createElement('span');
    nameEl.className = 'fn';
    nameEl.textContent = f.name;      // textContent — safe

    const typeEl = document.createElement('span');
    typeEl.className = 'ft';
    typeEl.textContent = f.type;      // textContent — safe

    const cardEl = document.createElement('span');
    cardEl.style.cssText =
      `min-width:32px;text-align:right;font-size:10px;font-weight:600;` +
      `color:${{f.required ? col : '#bbb'}};padding-left:4px`;
    cardEl.textContent = card;        // static cardinality string — safe

    row.appendChild(dot);
    row.appendChild(nameEl);
    row.appendChild(typeEl);
    row.appendChild(cardEl);
    df.appendChild(row);
  }});

  // API usages
  if (n.usages && n.usages.length) {{
    const uhdr = document.createElement('div');
    uhdr.className = 'dp-section';
    uhdr.textContent = `API endpoints (${{n.usages.length}})`;
    df.appendChild(uhdr);

    // FIX: Build usage rows with DOM methods so u.method, u.path, u.context
    // are never parsed as HTML — they come from OpenAPI path/operation data.
    n.usages.forEach(u => {{
      const row = document.createElement('div');
      row.className = 'usage-row';
      const mc = MCOL[u.method.toLowerCase()] || '#888';

      const pill = document.createElement('span');
      pill.className = 'method-pill';
      pill.style.background = mc;
      pill.textContent = u.method;    // textContent — safe

      const pathEl = document.createElement('span');
      pathEl.className = 'usage-path';
      pathEl.textContent = u.path;    // textContent — safe

      const ctxEl = document.createElement('span');
      ctxEl.className = 'usage-ctx';
      ctxEl.textContent = u.context || ''; // textContent — safe

      row.appendChild(pill);
      row.appendChild(pathEl);
      row.appendChild(ctxEl);
      df.appendChild(row);
    }});
  }}

  document.getElementById('dp').style.display = 'block';
}}

// FIX: Attach close-panel listener via addEventListener instead of inline onclick.
document.getElementById('dp-close').addEventListener('click', () => {{
  document.getElementById('dp').style.display = 'none';
}});

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
  tagEl.className = 'ep-tag';
  tagEl.textContent = tag;            // textContent — safe
  grp.appendChild(tagEl);

  eps.forEach(ep => {{
    const mc = MCOL[ep.method.toLowerCase()] || '#888';
    const card = document.createElement('div');
    card.className = 'ep-card';

    // FIX: Build endpoint header with DOM methods so ep.method, ep.path,
    // ep.summary, ep.operation_id are never parsed as HTML.
    const head = document.createElement('div');
    head.className = 'ep-head';

    const methodBadge = document.createElement('span');
    methodBadge.className = 'ep-method';
    methodBadge.style.background = mc;
    methodBadge.textContent = ep.method;  // textContent — safe

    const meta = document.createElement('div');
    meta.style.cssText = 'min-width:0;flex:1';

    const pathEl = document.createElement('div');
    pathEl.className = 'ep-path';
    pathEl.textContent = ep.path;         // textContent — safe

    const summaryEl = document.createElement('div');
    summaryEl.className = 'ep-summary';
    summaryEl.textContent = ep.summary || ep.operation_id; // textContent — safe

    meta.appendChild(pathEl);
    meta.appendChild(summaryEl);
    head.appendChild(methodBadge);
    head.appendChild(meta);

    const body = document.createElement('div');
    body.className = 'ep-body';

    if (ep.request_schema) {{
      const nn = nodeMap[ep.request_schema];
      const c = nn ? COLORS[nn.c] : '#888';

      const reqLabel = document.createElement('div');
      reqLabel.style.cssText = 'margin:7px 0 3px;font-size:10px;font-weight:700;color:#aaa;letter-spacing:.05em';
      reqLabel.textContent = 'REQUEST';
      body.appendChild(reqLabel);

      // FIX: Build chip with DOM methods so ep.request_schema is safe.
      const chip = document.createElement('span');
      chip.className = 'schema-chip';
      chip.style.color = c;
      const roleSpan = document.createElement('span');
      roleSpan.className = 'chip-role';
      roleSpan.textContent = 'body';
      const schemaName = document.createElement('span');
      schemaName.textContent = ep.request_schema; // textContent — safe
      chip.appendChild(roleSpan);
      chip.appendChild(schemaName);
      chip.addEventListener('click', () => {{
        sw('g', document.querySelector('.tb'));
        // Select the node in the graph
        clickedNode = ep.request_schema;
        updateHighlights();
        showDetail(ep.request_schema);
      }});
      body.appendChild(chip);
    }}

    if (ep.responses.length) {{
      const respLabel = document.createElement('div');
      respLabel.style.cssText = 'margin:8px 0 3px;font-size:10px;font-weight:700;color:#aaa;letter-spacing:.05em';
      respLabel.textContent = 'RESPONSES';
      body.appendChild(respLabel);

      ep.responses.forEach(r => {{
        const nn = nodeMap[r.schema];
        const c = nn ? COLORS[nn.c] : '#888';

        // FIX: Build chip with DOM methods so r.status and r.schema are safe.
        const chip = document.createElement('span');
        chip.className = 'schema-chip';
        chip.style.color = c;
        const roleSpan = document.createElement('span');
        roleSpan.className = 'chip-role';
        roleSpan.textContent = r.status;  // textContent — safe
        const schemaName = document.createElement('span');
        schemaName.textContent = r.schema; // textContent — safe
        chip.appendChild(roleSpan);
        chip.appendChild(schemaName);
        chip.addEventListener('click', () => {{
          sw('g', document.querySelector('.tb'));
          // Select the node in the graph
          clickedNode = r.schema;
          updateHighlights();
          showDetail(r.schema);
        }});
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
  const col = COLORS[n.c] || '#888';
  const card = document.createElement('div');
  card.className = 'sc';
  if (n.is_root) card.style.cssText += `border-color:${{col}};border-width:1.5px`;
  const req = n.fields.filter(f => f.required).length;
  const opt = n.fields.filter(f => !f.required).length;
  const shown = n.fields.slice(0, 7);
  const more = n.fields.length > 7
    ? `<div style="font-size:10px;color:#bbb;padding-top:3px">+${{n.fields.length - 7}} more</div>` : '';

  // FIX: Escape f.name and f.type before inserting into innerHTML string.
  function row(f) {{
    const c = f.is_array ? (f.required ? '1..*' : '0..*') : (f.required ? '1' : '0..1');
    return `<div class="sr">
      <span class="rb" style="background:${{f.required ? col : '#ddd'}}"></span>
      <span class="fn2">${{escapeHtml(f.name)}}</span>
      <span class="ft2">${{escapeHtml(f.type)}}</span>
      <span style="min-width:30px;text-align:right;font-size:10px;font-weight:600;color:${{f.required ? col : '#ccc'}}">${{c}}</span>
    </div>`;
  }}

  // FIX: Escape n.k (schema name) and n.desc before inserting into innerHTML.
  // FIX: Use (n.desc || '') guard so .slice() never throws on null/undefined.
  const rootBadge = n.is_root
    ? `<span class="root-badge" style="background:${{col}}">API ROOT</span>` : '';

  card.innerHTML = `
    <div class="sh">
      <span class="sn" style="color:${{col}}">${{escapeHtml(n.k)}}</span>
      <div style="display:flex;align-items:center;gap:5px">
        ${{rootBadge}}<span class="card-badge">${{req}}✦ ${{opt}}○</span>
      </div>
    </div>
    <div class="sf">
      <div style="font-size:10px;color:#999;margin-bottom:4px;line-height:1.4">${{escapeHtml((n.desc || '').slice(0,130))}}</div>
      ${{shown.map(row).join('')}}${{more}}
    </div>`;
  sg.appendChild(card);
}});

if (ENUMS.length) {{
  const d = document.createElement('div');
  d.className = 'divider'; d.textContent = 'Enum types'; sg.appendChild(d);

  ENUMS.forEach(e => {{
    const col = COLORS[e.c] || '#888';
    const card = document.createElement('div'); card.className = 'sc';

    // FIX: Escape e.k, e.desc, e.v before inserting into innerHTML.
    // FIX: Use (e.desc || '') guard so .slice() never throws on null/undefined.
    card.innerHTML = `
      <div class="sh"><span class="sn" style="color:${{col}}">${{escapeHtml(e.k)}}</span>
        <span class="card-badge">enum</span></div>
      <div class="sf">
        <div style="font-size:10px;color:#999;margin-bottom:3px">${{escapeHtml((e.desc || '').slice(0,110))}}</div>
        <div class="enum-v">${{escapeHtml(e.v)}}</div>
      </div>`;
    sg.appendChild(card);
  }});
}}

// All Entities tab
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

    // Header — FIX: Build with DOM methods so entity.name, entity.type,
    // entity.description are never parsed as HTML.
    const header = document.createElement('div');
    header.style.cssText = `padding:12px 16px;background:${{col}}15;border-bottom:1px solid #e0dfd8`;

    const headerTop = document.createElement('div');
    headerTop.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:6px';

    const nameEl = document.createElement('span');
    nameEl.style.cssText = `font-size:16px;font-weight:600;color:${{col}}`;
    nameEl.textContent = entity.name;        // textContent — safe

    const typeBadge = document.createElement('span');
    typeBadge.style.cssText =
      `font-size:11px;padding:2px 9px;border-radius:12px;background:${{col}};color:#fff;font-weight:600`;
    typeBadge.textContent = entity.type.toUpperCase(); // textContent — safe

    headerTop.appendChild(nameEl);
    headerTop.appendChild(typeBadge);
    header.appendChild(headerTop);

    if (entity.description) {{
      const descEl = document.createElement('div');
      descEl.style.cssText = 'font-size:12px;color:#666;line-height:1.5';
      descEl.textContent = entity.description; // textContent — safe
      header.appendChild(descEl);
    }}
    card.appendChild(header);

    // Content
    const content = document.createElement('div');
    content.style.cssText = 'padding:14px 16px';

    if (entity.type === 'enum') {{
      const enumSection = document.createElement('div');

      const enumLabel = document.createElement('div');
      enumLabel.style.cssText = 'font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px';
      enumLabel.textContent = `Values (${{entity.enum.length}})`;
      enumSection.appendChild(enumLabel);

      const valueGrid = document.createElement('div');
      valueGrid.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px';

      // FIX: Use textContent for each enum value chip — safe.
      entity.enum.forEach(val => {{
        const chip = document.createElement('span');
        chip.style.cssText =
          'font-size:11px;padding:4px 10px;border-radius:5px;background:#f5f4f0;' +
          'border:1px solid #e0dfd8;color:#555;font-family:monospace';
        chip.textContent = val;              // textContent — safe
        valueGrid.appendChild(chip);
      }});

      enumSection.appendChild(valueGrid);
      content.appendChild(enumSection);
    }} else {{
      // Object properties
      const propsSection = document.createElement('div');
      const props = Object.entries(entity.properties);

      const propsLabel = document.createElement('div');
      propsLabel.style.cssText = 'font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px';
      propsLabel.textContent = `Properties (${{props.length}})`;
      propsSection.appendChild(propsLabel);

      props.forEach(([propName, propData]) => {{
        const propCard = document.createElement('div');
        propCard.style.cssText =
          'margin-bottom:10px;padding:10px;border:1px solid #f0efe8;border-radius:6px;background:#fafaf9';

        // FIX: Build property header with DOM methods — propName is safe via textContent.
        const propHeader = document.createElement('div');
        propHeader.style.cssText = 'display:flex;align-items:center;margin-bottom:4px';

        const propNameEl = document.createElement('span');
        propNameEl.style.cssText = 'font-size:13px;font-weight:600;color:#1a1a18';
        propNameEl.textContent = propName;   // textContent — safe

        const reqBadge = document.createElement('span');
        reqBadge.style.cssText = propData.required
          ? `font-size:9px;padding:2px 7px;border-radius:10px;background:${{col}};color:#fff;font-weight:700;margin-left:6px`
          : 'font-size:9px;padding:2px 7px;border-radius:10px;background:#e0dfd8;color:#888;margin-left:6px';
        reqBadge.textContent = propData.required ? 'REQUIRED' : 'optional';

        propHeader.appendChild(propNameEl);
        propHeader.appendChild(reqBadge);
        propCard.appendChild(propHeader);

        // FIX: Build type row with DOM methods — propData.type is safe via textContent.
        const typeRow = document.createElement('div');
        typeRow.style.cssText =
          `font-size:11px;color:${{propData.required ? col : '#999'}};font-family:monospace;margin-bottom:4px`;
        typeRow.textContent = propData.type; // textContent — safe
        if (propData.format) {{
          const fmtSpan = document.createElement('span');
          fmtSpan.style.cssText = 'font-size:10px;color:#aaa';
          fmtSpan.textContent = ` (${{propData.format}})`; // textContent — safe
          typeRow.appendChild(fmtSpan);
        }}
        propCard.appendChild(typeRow);

        // FIX: Description via textContent — safe.
        if (propData.description) {{
          const descEl = document.createElement('div');
          descEl.style.cssText = 'font-size:11px;color:#666;line-height:1.5;margin-top:4px';
          descEl.textContent = propData.description; // textContent — safe
          propCard.appendChild(descEl);
        }}

        // FIX: Enum values via textContent chips — safe.
        if (propData.enum && propData.enum.length > 0) {{
          const enumWrap = document.createElement('div');
          enumWrap.style.cssText = 'margin-top:6px';

          const enumHint = document.createElement('div');
          enumHint.style.cssText = 'font-size:10px;color:#888;margin-bottom:4px';
          enumHint.textContent = 'Possible values:';
          enumWrap.appendChild(enumHint);

          const chipRow = document.createElement('div');
          chipRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px';

          const displayEnums = propData.enum.slice(0, 8);
          displayEnums.forEach(v => {{
            const vChip = document.createElement('span');
            vChip.style.cssText =
              'font-size:10px;padding:2px 7px;border-radius:4px;background:#fff;border:1px solid #d0cfc8;font-family:monospace';
            vChip.textContent = v;           // textContent — safe
            chipRow.appendChild(vChip);
          }});

          if (propData.enum.length > 8) {{
            const moreSpan = document.createElement('span');
            moreSpan.style.cssText = 'font-size:10px;color:#aaa;padding:2px 7px';
            moreSpan.textContent = `+${{propData.enum.length - 8}} more`;
            chipRow.appendChild(moreSpan);
          }}

          enumWrap.appendChild(chipRow);
          propCard.appendChild(enumWrap);
        }}

        // FIX: Example via textContent — safe.
        if (propData.example !== '' && propData.example != null) {{
          const exWrap = document.createElement('div');
          exWrap.style.cssText = 'margin-top:6px;font-size:10px';

          const exLabel = document.createElement('span');
          exLabel.style.cssText = 'color:#888';
          exLabel.textContent = 'Example:';

          const exCode = document.createElement('code');
          exCode.style.cssText =
            'padding:2px 6px;background:#fff;border:1px solid #e0dfd8;border-radius:3px;font-family:monospace';
          exCode.textContent = propData.example; // textContent — safe

          exWrap.appendChild(exLabel);
          exWrap.appendChild(exCode);
          propCard.appendChild(exWrap);
        }}

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

// FIX: Attach entity filter listener via addEventListener instead of inline oninput.
document.getElementById('entity-filter').addEventListener('input', function() {{
  filterEntities(this.value);
}});

// Initialize entities tab
renderEntities();
"""

    # Build complete HTML first with placeholder hash to get final script content
    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- Security: Content Security Policy with hash-based script protection -->
<!-- Prevents XSS even if DOM-based injection occurs; blocks all external resources -->
<!-- Script hash is computed at generation time; only exact trusted script will execute -->
<!-- Note: frame-ancestors is ignored in meta tags; use HTTP header for production -->
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-__SCRIPT_HASH_PLACEHOLDER__'; img-src data:; base-uri 'none';">
<title>{safe_title} – Data Model</title>
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
  <h1>{safe_title} – Data Model <span>Generated by openapi_visualizer.py</span></h1>
</header>
<!-- FIX: Removed inline onclick handlers; event listeners are attached in JS below -->
<div class="tab-bar">
  <div class="tb act" data-tab="g">Entity Graph</div>
  <div class="tb" data-tab="a">API Surface ({n_endpoints})</div>
  <div class="tb" data-tab="s">All Schemas</div>
  <div class="tb" data-tab="e">All Entities</div>
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
    <!-- FIX: Removed inline onclick; IDs used so addEventListener can attach below -->
    <button class="zoom-btn" id="zoom-in">＋</button>
    <button class="zoom-btn" id="zoom-out">－</button>
    <button class="zoom-btn" id="zoom-reset">⊡ Reset</button>
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
      <!-- FIX: Removed inline onclick; listener attached in JS below -->
      <button class="dp-close" id="dp-close">×</button>
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
    <!-- FIX: Removed inline oninput; listener attached in JS below -->
    <input type="text" id="entity-filter" placeholder="Filter entities by name (e.g., 'Counter', 'Result')..."
           style="width:100%;max-width:500px;padding:8px 12px;font-size:13px;border:1px solid #d0cfc8;
           border-radius:6px;font-family:system-ui,sans-serif">
    <div style="margin-top:8px;font-size:11px;color:#888">
      <span id="entity-count"></span>
    </div>
  </div>
  <div style="overflow-y:auto;padding:16px 20px" id="entity-list"></div>
</div>

<!-- Security Note: API schema is embedded in page source by design for visualization.
     Ensure this HTML file is only served to authorized users or kept internal. -->
<script>
{script_content}</script>
</body>
</html>"""

    # The browser hashes the exact content between <script> and </script>, including the newline
    # after the opening tag. We need to match that exactly.
    # The rendered HTML will have: <script>\n{script_content}</script>
    # So we hash: \n + script_content
    rendered_script = "\n" + script_content

    # Compute SHA-256 hash of the final script as the browser will see it
    script_hash = base64.b64encode(
        hashlib.sha256(rendered_script.encode('utf-8')).digest()
    ).decode('ascii')

    # Substitute the hash into the HTML template
    final_html = html_template.replace('__SCRIPT_HASH_PLACEHOLDER__', script_hash)

    return final_html


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML data-model visualization from an OpenAPI JSON or YAML spec."
    )
    parser.add_argument("input", help="Path to OpenAPI JSON or YAML file")
    parser.add_argument("-o", "--output", help="Output HTML file (default: <input>_model.html)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Determine file type and load accordingly
    file_ext = input_path.suffix.lower()

    with open(input_path, encoding="utf-8") as f:
        if file_ext in ('.yaml', '.yml'):
            if not YAML_AVAILABLE:
                print(f"Error: PyYAML is required for YAML files. Install with: pip install pyyaml", file=sys.stderr)
                sys.exit(1)
            spec = yaml.safe_load(f)
        elif file_ext == '.json':
            spec = json.load(f)
        else:
            # Try to auto-detect by attempting JSON first, then YAML
            content = f.read()
            try:
                spec = json.loads(content)
            except json.JSONDecodeError:
                if YAML_AVAILABLE:
                    try:
                        spec = yaml.safe_load(content)
                    except yaml.YAMLError:
                        print(f"Error: Could not parse file as JSON or YAML", file=sys.stderr)
                        sys.exit(1)
                else:
                    print(f"Error: File appears to be YAML but PyYAML is not installed. Install with: pip install pyyaml", file=sys.stderr)
                    sys.exit(1)

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
    html_out = build_html(objects, enums, endpoints, schema_usages, display_title, raw_schemas)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    print(f"  Output: {output_path}  ({len(html_out)//1024} KB)")
    print(f"  Open in browser: file://{output_path.resolve()}")


if __name__ == "__main__":
    main()
