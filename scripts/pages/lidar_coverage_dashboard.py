import os
import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import Fullscreen
import json
import random
import copy
import colorsys
from pathlib import Path

st.set_page_config(layout="wide", page_title="LiDAR FeatureCollection Viewer")
st.title("LiDAR LAS/LAZ FeatureCollection Map Viewer with Filtering")

JSON_DIR = Path(os.getenv("MAP_JSON_PATH", "/app/potree/crescer/map_json"))

def generate_distinct_colors(n):
    colors = []
    for i in range(n):
        hue = i / n
        lightness = 0.5
        saturation = 0.85
        r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
        hex_color = '#{:02x}{:02x}{:02x}'.format(int(r*255), int(g*255), int(b*255))
        colors.append(hex_color)
    return colors

# Sidebar: Upload and config
st.sidebar.header("Input")
uploaded_file = st.sidebar.file_uploader("Upload LAS summary GeoJSON/JSON", type=["json", "geojson"])
data = None

@st.cache_data
def load_json(uploaded_file):
    return json.load(uploaded_file)

if uploaded_file:
    try:
        data = load_json(uploaded_file)
        st.sidebar.success("File uploaded and loaded!")
    except Exception as e:
        st.sidebar.error(f"Error parsing JSON: {e}")
else:
    json_folder = JSON_DIR
    os.makedirs(json_folder, exist_ok=True)  # # Create the folder if it doesn't exist
    json_files = []
    try:
        json_files = [f for f in os.listdir(json_folder) if f.lower().endswith((".json", ".geojson"))]
    except Exception as e:
        st.sidebar.error("Error reading folder '../map_json': " + str(e))
    if json_files:
            geojson_path = st.sidebar.selectbox("Select a JSON file", json_files)
            if geojson_path:
                file_path = os.path.join(json_folder, geojson_path)
                try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                    st.sidebar.success("GeoJSON loaded from folder!")
                except Exception as e:
                    st.sidebar.error(f"Failed to load file: {e}")
    else:
        st.sidebar.error(f"No JSON files found in {JSON_DIR} folder.")


if not data:
    st.info("Upload or provide path to a LAS GeoJSON summary file.")
    st.stop()

features = data.get("features", [])
if not features:
    st.error("No features found in the GeoJSON.")
    st.stop()

st.sidebar.header("Class Filter Settings")
selected_class = st.sidebar.number_input("Select class to analyze (e.g., 41)", min_value=0, value=41)
range_input = st.sidebar.text_input("Enter percentage ranges (e.g., 0-2,2,4-10,10-100)", value="0-1,1-4,4-100")

try:
    group_ranges = []
    for group in range_input.split(','):
        if '-' in group:
            min_val, max_val = map(float, group.split('-'))
        else:
            min_val = float(group)
            max_val = min_val
        group_ranges.append((min_val, max_val))
except Exception as e:
    st.sidebar.error(f"Invalid range input: {e}")
    st.stop()

group_colors = generate_distinct_colors(len(group_ranges))
group_inclusion = []
group_color_pickers = []

st.sidebar.subheader("Group Settings")
for i, (min_val, max_val) in enumerate(group_ranges):
    default_color = group_colors[i]
    st.sidebar.markdown(
        f"<div style='display: flex; align-items: center;'>"
        f"<div style='width: 15px; height: 15px; background-color: {default_color}; margin-right: 10px;'></div>"
        f"<span>Group {i + 1}: {min_val:.0f}–{max_val:.0f}%</span></div>",
        unsafe_allow_html=True
    )
    group_color_pickers.append(default_color)
    include = st.sidebar.checkbox(f"Include group {i + 1} files?", value=False)
    group_inclusion.append(include)

sample_count = st.sidebar.number_input("Number of additional random files to include:", min_value=0, value=len(features), max_value=len(features), step=25)
show_dimmed = st.sidebar.checkbox("Show excluded files on map?", value=False, help="Files which are not selected in range. [Dark Brown one]")
show_rejected = st.sidebar.checkbox("Show rejected files on map?", value=False, help="Rejected files from the selected range. [Less Transparent one]")

# Classification, grouping and filtering
grouped_features = [[] for _ in group_ranges]
excluded_features = []

for feat in features:
    class_dist = feat.get("properties", {}).get("class_distribution", {})
    total = sum(class_dist.values())
    class_val = class_dist.get(str(selected_class), 0)
    pct = round(class_val / total * 100, 2) if total > 0 else 0

    matched = False
    for i, (min_v, max_v) in enumerate(group_ranges):
        if min_v <= pct < max_v or (min_v == max_v and pct == min_v):
            grouped_features[i].append((feat, pct))
            matched = True
            break
    if not matched:
        excluded_features.append((feat, pct))

# Collect features: always included + random sampled
selected_features = []
remaining_pool = []

for i, feats in enumerate(grouped_features):
    if group_inclusion[i]:
        selected_features.extend([f for f, _ in feats])
    else:
        remaining_pool.extend([f for f, _ in feats])

if len(remaining_pool) <= sample_count:
    sampled_random = remaining_pool
else:
    random.seed(42)
    sampled_random = random.sample(remaining_pool, sample_count)

selected_features.extend(sampled_random)

# Build map data and color them
map_features = []
for i, feats in enumerate(grouped_features):
    color = group_color_pickers[i]
    for feat, _ in feats:
        feat["map_color"] = color
        feat["highlight"] = (feat in selected_features)
        feat["visible"] = (group_inclusion[i] or feat in sampled_random) or show_rejected

for feat, _ in excluded_features:
    feat["map_color"] = "#808080"
    feat["highlight"] = False
    feat["visible"] = show_dimmed

map_features = [feat for feat in features if feat.get("visible")]

# Prepare download
data_filtered = copy.deepcopy(data)
data_filtered["features"] = map_features
filtered_json_str = json.dumps(data_filtered, indent=2)
st.sidebar.download_button("Download filtered JSON", filtered_json_str, file_name="filtered_data.json")

# Mapping
all_coords = []
for feat in map_features:
    geom = feat.get("geometry")
    if geom and geom.get("type") == "Polygon":
        for ring in geom["coordinates"]:
            all_coords.extend(ring)

if not all_coords:
    st.error("No valid polygon coordinates found.")
    st.stop()

lats = [c[1] for c in all_coords]
lons = [c[0] for c in all_coords]
center_lat = sum(lats) / len(lats)
center_lon = sum(lons) / len(lons)
bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]

map_style = st.sidebar.selectbox("Map Style", ["OpenStreetMap", "CartoDB positron", "Esri Satellite"])
tile_dict = {
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB positron": "CartoDB positron",
    "Esri Satellite": "Esri.WorldImagery"  
}
tiles = tile_dict.get(map_style, "OpenStreetMap")

m = folium.Map(location=[center_lat, center_lon], zoom_start=15, tiles=tiles, control_scale=True,)
Fullscreen(position='topright').add_to(m)

for feat in map_features:
    props = feat.get("properties", {})
    geom = feat.get("geometry")
    coords = geom["coordinates"][0]
    polygon = [[lat, lon] for lon, lat in coords]

    color = feat["map_color"]
    fill_opacity = 0.2 if feat["highlight"] else 0.8

    classification = props.get("class_distribution", {})
    total_class = sum(classification.values())
    class_lines = [
        f"Class {k}: {v:,} ({(v / total_class * 100):.1f}%)"
        for k, v in classification.items()
    ]
    popup_html = (
        f"<b>{props.get('file','')}</b><br>"
        f"Total points: {props.get('total_points', 0):,}<br>"
        f"Class distribution:<br>" + "<br>".join(class_lines)
    )

    folium.Polygon(
        locations=polygon,
        color=color,
        weight=3,
        fill=True,
        fill_opacity=fill_opacity,
        tooltip=props.get("file", ""),
        popup=folium.Popup(popup_html, max_width=300)
    ).add_to(m)

m.fit_bounds(bounds)

st.markdown(
    "<style>.stfolium-container { width: 100%; height: 100%; margin: 0 auto; }</style>",
    unsafe_allow_html=True
)
st_folium(m, returned_objects=[], key="static_map", use_container_width=True)


with st.expander("Show raw JSON data"):
    st.json(data_filtered)