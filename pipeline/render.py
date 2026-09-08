"""FR5 (+ FR4 stretch): render the single self-contained dashboard HTML output.

Input:  config.TERRITORIES_CSV (FR4 output — hex_scores rows + territory_id;
        falls back to config.HEX_SCORES_CSV with no territory data if that
        stage hasn't been run)
Output: config.DASHBOARD_HTML — citywide hex choropleth + KPI strip + top-10
        hex list, plus a leadership-vs-operator territory toggle, all in one
        HTML file.
"""

import base64
import html
import json
import os

import branca.colormap as cm
import folium
import h3
import pandas as pd

import config
from pipeline.hex_assign import assign_hexes
from pipeline.score import score_restaurants
from pipeline.territories import territory_rollup


def _map_to_iframe(m: folium.Map, iframe_id: str | None = None) -> str:
    """Embed a folium map as a plain srcdoc iframe.

    Deliberately not using folium's own Map._repr_html_() — that wrapper is
    built for Jupyter and includes a "Make this Notebook Trusted" fallback
    message that shows through whenever its iframe fails to render (a known
    issue with srcdoc iframes on file:// pages in some browsers, which is how
    this dashboard is normally opened). Rendering the map's full document
    ourselves and embedding it in a plain iframe sized to fill its container
    (matching the .map-frame-holder CSS) avoids that fallback entirely.

    srcdoc iframes inherit their parent document's origin, so the parent page
    can reach into contentWindow directly (see focusMapHex in TOGGLE_JS) —
    that's what lets a table row click drive the map inside.
    """
    doc = m.get_root().render()
    id_attr = f' id="{iframe_id}"' if iframe_id else ""
    return f'<iframe{id_attr} srcdoc="{html.escape(doc, quote=True)}" style="width:100%;height:100%;border:none;"></iframe>'

# Sequential magenta ramp, light->dark (Sunday brand): lowest TAM at a pale
# near-white pink, highest TAM at full-saturation brand magenta. A 2-stop
# linear ramp (rather than hand-picked midpoints) keeps full saturation
# reserved for the actual max-score hex — every other hex necessarily lands
# somewhere lighter along the interpolation.
SEQUENTIAL_MAGENTA = ["#FFF3FE", "#FF17E9"]

# Dark basemap so the map canvas matches the dashboard's near-black chrome.
# Two real dark tile services were tried and both broke under this project's
# iframe-srcdoc embedding (see _map_to_iframe): CartoDB's dark_matter serves
# an "API KEY REQUIRED" watermark for anonymous requests, and Esri's Dark Gray
# Canvas silently returns blank tiles when the request has no Referer/Origin
# (true of any srcdoc iframe, which has an opaque origin) — confirmed by
# diffing a standalone render (real tiles, 200s) against the embedded one
# (blank gray, same 200s). Both are real tile-host quirks, not something we
# can code around while keeping the srcdoc-iframe architecture. Sticking with
# the always-reliable OpenStreetMap tiles and darkening them with a CSS filter
# scoped to the Leaflet tile pane only, so hex fills/markers/labels drawn in
# other panes keep their real colors.
MAP_TILES = "OpenStreetMap"
DARK_TILE_FILTER_CSS = (
    "<style>.leaflet-tile-pane{"
    "filter:grayscale(1) invert(1) brightness(0.95) contrast(1.15);"
    "}</style>"
)


def _add_dark_basemap(m: folium.Map) -> None:
    """Darken the map's OpenStreetMap tiles in place (see DARK_TILE_FILTER_CSS)."""
    m.get_root().html.add_child(folium.Element(DARK_TILE_FILTER_CSS))


def _add_highlight_support(m: folium.Map) -> None:
    """Expose window.dashboardHighlight(lat, lng, boundary) inside this map's
    own document, so the parent page can drive it from a table row click
    (via iframe.contentWindow — see focusMapHex in TOGGLE_JS). srcdoc iframes
    share the parent's origin, so this cross-document call is allowed.

    Lazily creates a single outline layer and reuses it on every call rather
    than adding a new layer per click; the CSS keyframe animation is
    retriggered by toggling the class off and back on (forcing reflow via
    offsetWidth in between), since re-adding an already-present class is a
    no-op and wouldn't restart the animation.
    """
    map_var = m.get_name()
    script = f"""
    <style>
      .hex-highlight-flash {{ animation: hexHighlightPulse 1s ease-out 2; }}
      @keyframes hexHighlightPulse {{
        0%   {{ stroke: #ffffff; stroke-opacity: 1; stroke-width: 6; }}
        60%  {{ stroke: #FF17E9; stroke-opacity: 0.4; stroke-width: 9; }}
        100% {{ stroke: #FF17E9; stroke-opacity: 1; stroke-width: 4; }}
      }}
    </style>
    <script>
      (function() {{
        var highlightLayer = null;
        window.dashboardHighlight = function(lat, lng, boundary) {{
          if (!highlightLayer) {{
            highlightLayer = L.polygon(boundary, {{
              color: '#FF17E9', weight: 4, fill: false, interactive: false
            }}).addTo({map_var});
          }} else {{
            highlightLayer.setLatLngs(boundary);
          }}
          highlightLayer.bringToFront();
          var el = highlightLayer.getElement && highlightLayer.getElement();
          if (el) {{
            el.classList.remove('hex-highlight-flash');
            void el.offsetWidth;
            el.classList.add('hex-highlight-flash');
          }}
          {map_var}.flyTo([lat, lng], Math.max({map_var}.getZoom(), 14), {{duration: 0.75}});
        }};
      }})();
    </script>
    """
    m.get_root().html.add_child(folium.Element(script))

# Categorical palette, fixed order (dataviz skill default theme, first 5 slots).
# NOTE: a choropleth shows every territory simultaneously (an "all-pairs"
# context), and the skill's palette only validates all-pairs CVD separation
# for its first 3 slots — slots 4-5 (yellow, magenta) are a documented risk
# beyond that (no Node.js runtime available in this environment to re-run the
# validator directly). Mitigation applied below: every territory also gets a
# direct number label on the map and in the table, so identity never depends
# on hue alone.
TERRITORY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]

PAGE_CSS = """
<style>
  :root {
    --surface-1: #141414;
    --page-plane: #000000;
    --text-primary: #ffffff;
    --text-secondary: #c7c7c7;
    --muted: #8f8f8f;
    --gridline: #2a2a2a;
    --border: rgba(255,255,255,0.12);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--page-plane);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 32px 24px 64px; }
  header { border-bottom: 3px solid #FF17E9; padding-bottom: 20px; margin-bottom: 28px; }
  .brand-bar { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }
  .brand-bar img { height: 36px; width: auto; display: block; }
  header h1 { font-size: 22px; margin: 0; }
  header p { margin: 0; color: var(--text-secondary); font-size: 14px; }
  .kpi-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 28px; }
  .kpi-tile {
    background: #181818;
    border: 1px solid var(--border);
    border-top: 3px solid #FF17E9;
    border-radius: 10px;
    padding: 16px 20px 18px;
  }
  .kpi-tile .label { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }
  .kpi-tile .value { font-size: 32px; font-weight: 600; line-height: 1; }
  .kpi-tile .sub { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .panel {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 28px;
  }
  .panel h2 { font-size: 15px; margin: 0 0 14px; }
  .map-frame-holder { width: 100%; height: 560px; border-radius: 6px; overflow: hidden; }
  .map-frame-holder iframe { width: 100%; height: 100%; border: none !important; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--gridline); }
  th { color: var(--text-secondary); font-weight: 500; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr:last-child td { border-bottom: none; }
  .rank-badge {
    display: inline-block; width: 22px; height: 22px; border-radius: 50%;
    background: var(--gridline); color: var(--text-secondary); font-size: 11px;
    text-align: center; line-height: 22px; margin-right: 8px;
  }
  tr.rank-first td {
    background: #FF17E9; color: #12000f; font-weight: 600;
    border-bottom-color: rgba(0,0,0,0.15);
  }
  tr.rank-first .rank-badge { background: #12000f; color: #FF17E9; }
  tr.hex-row { cursor: pointer; }
  tr.hex-row:hover td { background: rgba(255,255,255,0.06); }
  tr.hex-row.rank-first:hover td { background: #ff33ec; }
  footer { color: var(--muted); font-size: 12px; margin-top: 8px; }

  .view-tabs { display: flex; gap: 4px; background: var(--page-plane); border: 1px solid var(--border);
    border-radius: 8px; padding: 3px; width: fit-content; margin-bottom: 16px; }
  .view-tab { border: none; background: transparent; padding: 7px 16px; border-radius: 6px;
    font-size: 13px; font-weight: 500; color: var(--text-secondary); cursor: pointer; font-family: inherit; }
  .view-tab.active { background: var(--surface-1); color: var(--text-primary); box-shadow: 0 1px 2px var(--border); }
  .view-content[hidden] { display: none; }

  /* All map layers (leadership + every territory) are stacked in the same
     fixed-size box via CSS grid and toggled with visibility, never
     display:none — a Leaflet map born inside a display:none container gets
     a zero-size box and mis-renders permanently, even after being shown
     later. visibility:hidden keeps it laid out (and therefore correctly
     sized) the whole time; only the active layer is ever painted. */
  .map-stack {
    position: relative; display: grid; width: 100%; height: 560px;
    border-radius: 6px; overflow: hidden; margin-bottom: 16px;
  }
  .map-stack .map-layer { grid-area: 1 / 1; width: 100%; height: 100%; }
  .map-stack .map-layer.is-inactive { visibility: hidden; pointer-events: none; }
  .map-layer iframe { width: 100%; height: 100%; border: none !important; }

  .territory-swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 8px; vertical-align: middle; }
  .territory-legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 0 0 16px; font-size: 13px; color: var(--text-secondary); }

  .operator-controls { margin-bottom: 14px; }
  .operator-controls select {
    font-family: inherit; font-size: 13px; padding: 7px 10px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--surface-1); color: var(--text-primary);
  }
  .operator-table-wrap[hidden] { display: none; }
  .operator-table-wrap h2 { font-size: 13px; color: var(--text-secondary); font-weight: 500; margin: 0 0 10px; }

  /* Hex list (left) and call list (right) sit side by side, each independently
     scrollable within a fixed height — a hex row click updates the call list
     in place, so working a territory never requires scrolling past ~40 hex
     rows to reach the list you actually work from. Stacks on narrow widths. */
  .operator-split { display: grid; grid-template-columns: 1.15fr 1fr; gap: 18px; align-items: start; }
  .operator-pane {
    background: var(--page-plane); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 16px; max-height: 520px; overflow-y: auto; overflow-x: auto;
  }
  .operator-pane thead th { position: sticky; top: 0; background: var(--page-plane); }
  @media (max-width: 860px) {
    .operator-split { grid-template-columns: 1fr; }
    .operator-pane { max-height: 360px; }
  }

  .call-list-pane h2 { font-size: 13px; color: var(--text-secondary); font-weight: 500; margin: 0 0 8px; }
  .call-list-sub { font-size: 12px; color: var(--muted); margin: 0 0 14px; }
  .call-list-placeholder[hidden] { display: none; }
  .call-list-placeholder {
    font-size: 13px; color: var(--text-secondary); padding: 18px 4px;
    border: 1px dashed var(--border); border-radius: 8px; text-align: center;
  }
  .call-list[hidden] { display: none; }
  .call-item { display: flex; gap: 12px; padding: 10px 4px; border-bottom: 1px solid var(--gridline); }
  .call-item:last-child { border-bottom: none; }
  .call-rank {
    flex: 0 0 auto; width: 24px; height: 24px; border-radius: 50%;
    background: var(--gridline); color: var(--text-secondary); font-size: 12px;
    font-weight: 600; text-align: center; line-height: 24px;
  }
  .call-item:first-child .call-rank { background: #FF17E9; color: #12000f; }
  .call-name { font-size: 14px; font-weight: 600; }
  .call-meta { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
  .chain-tag {
    font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em;
    color: var(--muted); border: 1px solid var(--border); border-radius: 4px;
    padding: 1px 5px; margin-left: 6px; vertical-align: middle;
  }
  .call-empty { font-size: 13px; color: var(--muted); }
</style>
"""

TOGGLE_JS = """
<script>
  function showMap(key) {
    document.querySelectorAll('.map-layer').forEach(function (el) {
      el.classList.toggle('is-inactive', el.dataset.key !== key);
    });
  }
  function showView(view) {
    document.getElementById('content-leadership').hidden = (view !== 'leadership');
    document.getElementById('content-operator').hidden = (view !== 'operator');
    document.getElementById('tab-leadership').classList.toggle('active', view === 'leadership');
    document.getElementById('tab-operator').classList.toggle('active', view === 'operator');
    if (view === 'leadership') {
      showMap('leadership');
    } else {
      showMap('territory-' + document.getElementById('territory-select').value);
    }
  }
  function showOperatorTerritory(territoryId) {
    document.querySelectorAll('.operator-table-wrap').forEach(function (el) {
      el.hidden = (el.dataset.territory !== String(territoryId));
    });
    showMap('territory-' + territoryId);
    resetCallList();
  }
  function focusMapHex(iframeSelector, lat, lng, boundary) {
    var iframe = document.querySelector(iframeSelector);
    if (!iframe || !iframe.contentWindow || typeof iframe.contentWindow.dashboardHighlight !== 'function') return;
    iframe.contentWindow.dashboardHighlight(lat, lng, boundary);
  }
  function showCallList(hexId) {
    var placeholder = document.getElementById('call-list-placeholder');
    if (placeholder) placeholder.hidden = true;
    document.querySelectorAll('.call-list').forEach(function (el) {
      el.hidden = (el.dataset.hex !== hexId);
    });
  }
  function resetCallList() {
    document.querySelectorAll('.call-list').forEach(function (el) { el.hidden = true; });
    var placeholder = document.getElementById('call-list-placeholder');
    if (placeholder) placeholder.hidden = false;
  }
</script>
"""


def _hex_tooltip(row) -> str:
    return (
        f"<b>Score:</b> {row.score:.1f}<br>"
        f"<b>Restaurants:</b> {int(row.restaurant_count)} "
        f"({int(row.chain_count)} likely chain)<br>"
        f"<b>Avg price level:</b> {row.avg_price_level:.2f}"
    )


def _build_score_map(hexes: pd.DataFrame, zoom_start: int = 11, iframe_id: str | None = None) -> str:
    """Citywide (or single-territory) choropleth colored by TAM score."""
    center_lat = hexes["lat"].mean()
    center_lng = hexes["lng"].mean()
    m = folium.Map(location=[center_lat, center_lng], zoom_start=zoom_start, tiles=MAP_TILES)
    _add_dark_basemap(m)
    _add_highlight_support(m)

    colormap = cm.LinearColormap(
        colors=SEQUENTIAL_MAGENTA,
        vmin=float(hexes["score"].min()),
        vmax=float(hexes["score"].max()),
    )
    colormap.caption = "TAM score (restaurant density + price level, chain-downweighted)"

    for row in hexes.itertuples():
        boundary = h3.cell_to_boundary(row.hex_id)
        folium.Polygon(
            locations=boundary,
            color="#ffffff",
            weight=1,
            fill=True,
            fill_color=colormap(row.score),
            fill_opacity=0.82,
            tooltip=folium.Tooltip(_hex_tooltip(row)),
        ).add_to(m)

    colormap.add_to(m)
    if len(hexes) > 1:
        m.fit_bounds([[hexes["lat"].min(), hexes["lng"].min()], [hexes["lat"].max(), hexes["lng"].max()]])
    return _map_to_iframe(m, iframe_id=iframe_id)


def _build_territory_map(hexes: pd.DataFrame) -> str:
    """Leadership-view choropleth colored by territory identity, with a direct
    number label per territory (see TERRITORY_COLORS note on why hue alone
    isn't relied on for 5 series)."""
    center_lat = hexes["lat"].mean()
    center_lng = hexes["lng"].mean()
    m = folium.Map(location=[center_lat, center_lng], zoom_start=11, tiles=MAP_TILES)
    _add_dark_basemap(m)

    for row in hexes.itertuples():
        boundary = h3.cell_to_boundary(row.hex_id)
        color = TERRITORY_COLORS[(row.territory_id - 1) % len(TERRITORY_COLORS)]
        tooltip_html = f"<b>Territory {row.territory_id}</b><br>" + _hex_tooltip(row)
        folium.Polygon(
            locations=boundary,
            color="#ffffff",
            weight=1,
            fill=True,
            fill_color=color,
            fill_opacity=0.75,
            tooltip=folium.Tooltip(tooltip_html),
        ).add_to(m)

    # One direct label per territory at its centroid (secondary encoding, not just hue).
    for territory_id, group in hexes.groupby("territory_id"):
        color = TERRITORY_COLORS[(territory_id - 1) % len(TERRITORY_COLORS)]
        folium.map.Marker(
            [group["lat"].mean(), group["lng"].mean()],
            icon=folium.DivIcon(html=(
                f'<div style="background:{color};color:#fff;font-weight:700;font-size:13px;'
                f'width:26px;height:26px;border-radius:50%;display:flex;align-items:center;'
                f'justify-content:center;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,0.3);">'
                f'{territory_id}</div>'
            )),
        ).add_to(m)

    return _map_to_iframe(m)


def _hex_boundary_js(hex_id: str) -> str:
    """A hex's boundary as a JS array literal (plain numbers only, so it can
    be embedded straight into an onclick attribute with no quoting to escape
    — see focusMapHex/dashboardHighlight)."""
    boundary = h3.cell_to_boundary(hex_id)
    return json.dumps([[round(lat, 6), round(lng, 6)] for lat, lng in boundary])


def _ranked_rows(
    hexes: pd.DataFrame,
    n: int = 10,
    highlight_first: bool = False,
    map_target: str | None = None,
    show_call_list: bool = False,
) -> str:
    rows = []
    for i, row in enumerate(hexes.head(n).itertuples(), start=1):
        classes = (["rank-first"] if (highlight_first and i == 1) else []) + (["hex-row"] if map_target else [])
        row_class = f" class='{' '.join(classes)}'" if classes else ""
        onclick = (
            f" onclick='focusMapHex(\"{map_target}\", {row.lat}, {row.lng}, {_hex_boundary_js(row.hex_id)});"
            + (f" showCallList(\"{row.hex_id}\")" if show_call_list else "")
            + "'"
            if map_target else ""
        )
        rows.append(
            f"<tr{row_class}{onclick}>"
            f"<td><span class='rank-badge'>{i}</span>{row.lat:.4f}, {row.lng:.4f}</td>"
            f"<td class='num'>{int(row.restaurant_count)}</td>"
            f"<td class='num'>{int(row.chain_count)}</td>"
            f"<td class='num'>{row.avg_price_level:.2f}</td>"
            f"<td class='num'>{row.score:.1f}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def _ranked_table(
    hexes: pd.DataFrame,
    n: int = 10,
    highlight_first: bool = False,
    map_target: str | None = None,
    show_call_list: bool = False,
) -> str:
    return f"""<table>
      <thead>
        <tr>
          <th>Hex centroid</th>
          <th class="num">Restaurants</th>
          <th class="num">Likely chains</th>
          <th class="num">Avg price level</th>
          <th class="num">Score</th>
        </tr>
      </thead>
      <tbody>
        {_ranked_rows(hexes, n, highlight_first, map_target, show_call_list)}
      </tbody>
    </table>"""


def _territory_legend(rollup: pd.DataFrame) -> str:
    items = []
    for row in rollup.itertuples():
        color = TERRITORY_COLORS[(row.territory_id - 1) % len(TERRITORY_COLORS)]
        items.append(
            f"<span><span class='territory-swatch' style='background:{color}'></span>"
            f"Territory {row.territory_id} — {row.total_score:,.0f} pts</span>"
        )
    return "<div class='territory-legend'>" + "".join(items) + "</div>"


def _territory_rollup_table(rollup: pd.DataFrame) -> str:
    rows = []
    for row in rollup.itertuples():
        color = TERRITORY_COLORS[(row.territory_id - 1) % len(TERRITORY_COLORS)]
        rows.append(
            f"<tr>"
            f"<td><span class='territory-swatch' style='background:{color}'></span>Territory {row.territory_id}</td>"
            f"<td class='num'>{int(row.hex_count)}</td>"
            f"<td class='num'>{int(row.restaurant_count)}</td>"
            f"<td class='num'>{int(row.chain_count)}</td>"
            f"<td class='num'>{row.avg_price_level:.2f}</td>"
            f"<td class='num'>{row.total_score:,.1f}</td>"
            f"</tr>"
        )
    return f"""<table>
      <thead>
        <tr>
          <th>Territory</th>
          <th class="num">Hexes</th>
          <th class="num">Restaurants</th>
          <th class="num">Likely chains</th>
          <th class="num">Avg price level</th>
          <th class="num">Total score</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows)}
      </tbody>
    </table>"""


def _build_territory_map_layers(hexes: pd.DataFrame) -> str:
    """One map-layer div per territory, zoomed to that territory's own hexes.
    Stacked (not swapped in/out of the DOM) so every map is born with real,
    correctly-sized dimensions — see the .map-stack CSS note."""
    layers = []
    for territory_id, group in hexes.groupby("territory_id"):
        map_html = _build_score_map(group.sort_values("score", ascending=False), zoom_start=13)
        layers.append(
            f'<div class="map-layer is-inactive" data-key="territory-{territory_id}">{map_html}</div>'
        )
    return "".join(layers)


def _load_scored_restaurants() -> pd.DataFrame | None:
    """Restaurant-level priority scores for the operator call list (see
    score_restaurants). Reads straight from the raw ingested CSV, not the
    hex/territory rollups, since a call list needs the individual listings —
    returns None if a sweep hasn't been ingested yet, so the operator view
    can degrade gracefully instead of erroring."""
    if not os.path.exists(config.RAW_RESTAURANTS_CSV):
        return None
    raw = pd.read_csv(config.RAW_RESTAURANTS_CSV)
    return score_restaurants(assign_hexes(raw))


def _call_list_items_html(restaurants: pd.DataFrame) -> str:
    items = []
    for i, row in enumerate(restaurants.itertuples(), start=1):
        price = f"Price level {int(row.price_level)}" if pd.notna(row.price_level) else "Price level unknown"
        rating = f"{row.rating:.1f}★" if pd.notna(row.rating) else "No rating"
        reviews = f"{int(row.user_ratings_total):,} reviews" if pd.notna(row.user_ratings_total) else "No reviews"
        chain_tag = " <span class='chain-tag'>Likely chain</span>" if row.is_chain else ""
        items.append(
            f"<div class='call-item'>"
            f"<div class='call-rank'>{i}</div>"
            f"<div class='call-body'>"
            f"<div class='call-name'>{html.escape(str(row.name))}{chain_tag}</div>"
            f"<div class='call-meta'>{price} · {rating} · {reviews}</div>"
            f"</div></div>"
        )
    return "".join(items) if items else "<p class='call-empty'>No restaurant-level records for this hex.</p>"


def _call_list_panel_html(hex_ids: set, restaurants_scored: pd.DataFrame | None) -> str:
    """Per-hex call lists (see score_restaurants for the ranking), one hidden
    div per hex — mounted-but-hidden like the map-stack layers above, so
    showCallList only has to toggle `hidden`, not render anything on click."""
    if restaurants_scored is None:
        return "<h2>Call list</h2><p class='call-list-sub'>No restaurant-level data available (data/raw_restaurants.csv not found).</p>"
    panels = []
    for hex_id, group in restaurants_scored.groupby("hex_id"):
        if hex_id not in hex_ids:
            continue
        panels.append(f"<div class='call-list' data-hex='{hex_id}' hidden>{_call_list_items_html(group)}</div>")

    return f"""
    <h2>Call list</h2>
    <p class="call-list-sub">Ranked by outreach priority (price level weighted by rating
      and review volume, likely chains down-weighted). Click a hex on the left.</p>
    <div class="call-list-placeholder" id="call-list-placeholder">Click a hex row to see its call list.</div>
    {"".join(panels)}
    """


def _build_operator_controls(hexes: pd.DataFrame, rollup: pd.DataFrame, restaurants_scored: pd.DataFrame | None) -> str:
    options = "".join(
        f"<option value='{row.territory_id}'>Territory {row.territory_id} "
        f"({int(row.hex_count)} hexes, {row.total_score:,.0f} pts)</option>"
        for row in rollup.itertuples()
    )
    table_wraps = []
    for territory_id, group in hexes.groupby("territory_id"):
        group = group.sort_values("score", ascending=False)
        map_target = f".map-layer[data-key=territory-{territory_id}] iframe"
        table_html = _ranked_table(
            group, n=len(group), map_target=map_target,
            show_call_list=restaurants_scored is not None,
        )
        table_wraps.append(f"""
        <div class="operator-table-wrap" data-territory="{territory_id}" {"" if territory_id == 1 else "hidden"}>
          <h2>All {len(group)} hexes in Territory {territory_id}, by score</h2>
          {table_html}
        </div>
        """)

    call_list_html = _call_list_panel_html(set(hexes["hex_id"]), restaurants_scored)

    return f"""
    <div class="operator-controls">
      <label for="territory-select" style="font-size:13px;color:var(--text-secondary);margin-right:8px;">Territory:</label>
      <select id="territory-select" onchange="showOperatorTerritory(this.value)">{options}</select>
    </div>
    <div class="operator-split">
      <div class="operator-pane hex-table-pane">{"".join(table_wraps)}</div>
      <div class="operator-pane call-list-pane">{call_list_html}</div>
    </div>
    """


def _logo_data_uri() -> str:
    with open(config.LOGO_PATH, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_dashboard(hexes: pd.DataFrame) -> str:
    has_territories = "territory_id" in hexes.columns

    total_restaurants = int(hexes["restaurant_count"].sum())
    total_chain = int(hexes["chain_count"].sum())
    aggregate_score = hexes["score"].sum()
    hex_count = len(hexes)

    map_html = _build_score_map(hexes, iframe_id="main-map-frame")
    top10_html = _ranked_table(hexes, n=10, highlight_first=True, map_target="#main-map-frame")

    territory_section = ""
    if has_territories:
        rollup = territory_rollup(hexes)
        leadership_map_html = _build_territory_map(hexes)
        territory_layers_html = _build_territory_map_layers(hexes)
        restaurants_scored = _load_scored_restaurants()
        operator_controls_html = _build_operator_controls(hexes, rollup, restaurants_scored)
        territory_section = f"""
  <div class="panel">
    <h2>Territories</h2>
    <div class="view-tabs">
      <button id="tab-leadership" class="view-tab active" onclick="showView('leadership')">Leadership view</button>
      <button id="tab-operator" class="view-tab" onclick="showView('operator')">Operator view</button>
    </div>

    <div class="map-stack">
      <div class="map-layer" data-key="leadership">{leadership_map_html}</div>
      {territory_layers_html}
    </div>

    <div id="content-leadership" class="view-content">
      {_territory_legend(rollup)}
      <h2 style="margin-top:20px;">Territory rollup</h2>
      {_territory_rollup_table(rollup)}
    </div>

    <div id="content-operator" class="view-content" hidden>
      {operator_controls_html}
    </div>
  </div>
"""

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{config.CITY_NAME} — GTM TAM &amp; Territory Map</title>
{PAGE_CSS}
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand-bar">
      <img src="{_logo_data_uri()}" alt="Sunday">
      <h1>{config.CITY_NAME} — Restaurant TAM Heatmap</h1>
    </div>
  </header>

  <div class="kpi-row">
    <div class="kpi-tile">
      <div class="label">Total addressable restaurants</div>
      <div class="value">{total_restaurants:,}</div>
      <div class="sub">{total_chain:,} flagged as likely chains, down-weighted in scoring</div>
    </div>
    <div class="kpi-tile">
      <div class="label">Estimated aggregate opportunity</div>
      <div class="value">{aggregate_score:,.0f}</div>
      <div class="sub">Sum of TAM score across all scored hexes</div>
    </div>
    <div class="kpi-tile">
      <div class="label">Hexes scored</div>
      <div class="value">{hex_count:,}</div>
      <div class="sub">Resolution-{config.SCORE_H3_RESOLUTION} hexes with at least one operational restaurant</div>
    </div>
  </div>

  <div class="panel">
    <h2>TAM score by hex</h2>
    <div class="map-frame-holder">{map_html}</div>
  </div>

  <div class="panel">
    <h2>Top 10 hexes by score</h2>
    {top10_html}
  </div>
{territory_section}
</div>
{TOGGLE_JS}
</body>
</html>
"""


def run(input_path: str | None = None, output_path: str = config.DASHBOARD_HTML) -> str:
    if input_path is None:
        input_path = config.TERRITORIES_CSV if os.path.exists(config.TERRITORIES_CSV) else config.HEX_SCORES_CSV
    hexes = pd.read_csv(input_path)
    html = render_dashboard(hexes)
    with open(output_path, "w") as f:
        f.write(html)
    return output_path


if __name__ == "__main__":
    path = run()
    print(f"Dashboard written to {path}")
