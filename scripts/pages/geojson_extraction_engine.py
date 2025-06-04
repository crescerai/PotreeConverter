
"""
las_processor.py

Module for processing LAS/LAZ files and generating GeoJSON outputs.
"""

import os
import laspy
import json
import pandas as pd
from shapely.geometry import box, mapping
import re
import logging
from shapely.geometry import shape
import pyproj
from shapely.ops import transform
import streamlit as st
import threading
from multiprocessing import Pool, Manager

# Configure logging to console only
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

def try_extract_crs_from_las(las):
    """
    Attempts to extract the CRS from a LAS file.

    Args:
        las (laspy.LasData): The LAS file object.

    Returns:
        str or None: The EPSG code in 'EPSG:XXXX' format or None if not found.
    """
    try:
        if hasattr(las.header, "epsg_number"):
            epsg = las.header.epsg_number
            if epsg and epsg > 0:
                return f"EPSG:{epsg}"
        if hasattr(las.header, "vlrs"):
            for vlr in las.header.vlrs:
                if hasattr(vlr, "record_id") and vlr.record_id == 2112:
                    wkt = vlr.string
                    if "EPSG" in wkt:
                        idx = wkt.find("EPSG")
                        epsg = ""
                        for c in wkt[idx+5:]:
                            if c.isdigit():
                                epsg += c
                            else:
                                break
                        if epsg:
                            return f"EPSG:{epsg}"
    except Exception as e:
        logger.warning(f"Failed to extract CRS: {e}")
    return None

def parse_bbox_from_name(filename):
    """
    Parses bounding box coordinates from the filename.

    Args:
        filename (str): The filename to parse.

    Returns:
        tuple: (x, y) coordinates or (None, None) if parsing fails.
    """
    m = re.search(r"e(\d+)n(\d+)", filename)
    if m:
        x = int(m.group(1)) * 1000
        y = int(m.group(2)) * 1000
        return x, y
    return None, None

def process_las_file(path, input_crs, counter=None, total=None):
    """
    Processes a single LAS/LAZ file to extract metadata and geometry.

    Args:
        path (str): Path to the LAS/LAZ file.
        input_crs (str): Input CRS in 'EPSG:XXXX' format.
        counter (multiprocessing.Value): Counter for processed files
        total (int): Total number of files to process

    Returns:
        dict or None: Processed data or None if processing fails.
    """
    fname = os.path.basename(path)
    try:
        las = laspy.read(path)
        if len(las.x) == 0 or len(las.y) == 0:
            x, y = parse_bbox_from_name(fname)
            if x is not None and y is not None:
                minx, miny = x, y
                maxx, maxy = x + 1000, y + 1000
            else:
                if counter is not None:
                    with counter.get_lock():
                        counter.value += 1
                return None
        else:
            minx, maxx = float(min(las.x)), float(max(las.x))
            miny, maxy = float(min(las.y)), float(max(las.y))
        bounds = box(minx, miny, maxx, maxy)
        total_points = len(las.x)
        class_counts = {}
        if hasattr(las, 'classification'):
            s = pd.Series(las.classification)
            class_counts = {int(cls): int(cnt) for cls, cnt in s.value_counts().items()}
        detected_crs = try_extract_crs_from_las(las)
        used_crs = input_crs or detected_crs or "EPSG:6350"
        
        # Update counter if provided
        if counter is not None:
            with counter.get_lock():
                counter.value += 1
                
        return {
            "file": fname,
            "geometry": mapping(bounds),
            "total_points": total_points,
            "class_distribution": class_counts,
            "crs_used": used_crs
        }
    except Exception as e:
        logger.error(f"Error processing file {fname}: {e}")
        x, y = parse_bbox_from_name(fname)
        if x is not None and y is not None:
            bounds = box(x, y, x + 1000, y + 1000)
            
            # Update counter if provided
            if counter is not None:
                with counter.get_lock():
                    counter.value += 1
                    
            return {
                "file": fname,
                "geometry": mapping(bounds),
                "total_points": 0,
                "class_distribution": {},
                "crs_used": input_crs
            }
        
        # Update counter even on failure
        if counter is not None:
            with counter.get_lock():
                counter.value += 1
                
        return None

def reproject_geom(geom, from_epsg, to_epsg):
    """
    Reprojects geometry from one CRS to another.

    Args:
        geom (dict): Geometry in GeoJSON format.
        from_epsg (str): Source CRS in 'EPSG:XXXX' format.
        to_epsg (str): Target CRS in 'EPSG:XXXX' format.

    Returns:
        dict: Reprojected geometry in GeoJSON format.
    """
    if from_epsg == to_epsg:
        return geom
    project = pyproj.Transformer.from_crs(from_epsg, to_epsg, always_xy=True).transform
    return mapping(transform(project, shape(geom)))

def process_folder(input_folder, output_geojson, progress_bar, status_text, input_crs="EPSG:6350", batchsize=16):
    """
    Processes all LAS/LAZ files in a folder and writes the output to a GeoJSON file.

    Args:
        input_folder (str): Path to the folder containing LAS/LAZ files.
        output_geojson (str): Path to the output GeoJSON file.
        progress_bar (st.progress): Streamlit progress bar component
        status_text (st.empty): Streamlit empty component for status text
        input_crs (str, optional): Input CRS. Defaults to "EPSG:6350".
        batchsize (int, optional): Number of files to process in parallel. Defaults to 16.
    """
    files = [os.path.join(input_folder, f) for f in os.listdir(input_folder) if f.lower().endswith((".las", ".laz"))]
    total_files = len(files)
    
    if total_files == 0:
        status_text.error("No LAS/LAZ files found in the input folder.")
        return
    
    # Create a shared counter for tracking progress
    with Manager() as manager:
        counter = manager.Value('i', 0)
        args = [(f, input_crs, counter, total_files) for f in files]
        features = []
        
        # Update the progress bar in a separate thread
        stop_event = threading.Event()
        
        def update_progress():
            while not stop_event.is_set():
                if total_files > 0:
                    progress = min(counter.value / total_files, 1.0)
                    progress_bar.progress(progress)
                    status_text.text(f"Processing: {counter.value}/{total_files} files ({progress*100:.1f}%)")
                threading.Event().wait(0.1)  # Update every 100ms
        
        # Start progress updater thread
        progress_thread = threading.Thread(target=update_progress)
        progress_thread.daemon = True
        progress_thread.start()
        
        try:
            with Pool(batchsize) as pool:
                results = pool.starmap(process_las_file, args)
                for result in results:
                    if result is not None:
                        geom = reproject_geom(result["geometry"], result["crs_used"], "EPSG:4326")
                        feat = {
                            "type": "Feature",
                            "properties": {
                                "file": result["file"],
                                "total_points": result["total_points"],
                                "class_distribution": result["class_distribution"],
                                "crs_used": result["crs_used"]
                            },
                            "geometry": geom
                        }
                        features.append(feat)
                        
            geojson = {
                "type": "FeatureCollection",
                "features": features
            }
            
            with open(output_geojson, "w") as f:
                json.dump(geojson, f)
                
            progress_bar.progress(1.0)
            status_text.success(f"Processed {len(features)} files. Output written to {output_geojson}")
            logger.info(f"Processed {len(features)} files. Output written to {output_geojson}")
            
        finally:
            # Stop the progress updater thread
            stop_event.set()
            progress_thread.join(timeout=1.0)

# Streamlit UI
st.title("LAS/LAZ File Processor")

input_folder = st.text_input("Input Folder Path", value="path/to/input_folder")
output_file_name = st.text_input("Output JSON File Name ", value="output.geojson")
# Construct the output path in the 'map_json' folder located in the parent directory of this script
output_geojson = os.path.join(os.path.dirname(os.path.dirname(__file__)), "map_json", output_file_name)
#  if a file is already present, it will be overwritten add a warning
if os.path.exists(output_geojson):
    st.warning(f"Output file {output_geojson} already exists and will be overwritten.")
input_crs = st.text_input("Input CRS (e.g., EPSG:6350)", value="EPSG:6350")
batchsize = st.slider("Batch Size", min_value=1, max_value=32, value=16)

# Create placeholder for progress bar and status
progress_bar = st.progress(0)
status_text = st.empty()

def run_processing():
    """
    Runs the LAS/LAZ processing in a separate thread.
    """
    os.makedirs(os.path.dirname(output_geojson), exist_ok=True)
    try:
        process_folder(
            input_folder=input_folder,
            output_geojson=output_geojson,
            progress_bar=progress_bar,
            status_text=status_text,
            input_crs=input_crs,
            batchsize=batchsize
        )
    except Exception as e:
        logger.error(f"Error during processing: {e}")
        status_text.error(f"Error during processing: {e}")

if st.button("Start Processing"):
    # Reset progress bar and status
    progress_bar.progress(0)
    status_text.text("Starting processing...")
    
    # Start processing in a thread
    threading.Thread(target=run_processing).start()
    # update the status text
    status_text.text("Processing started in the background. You can close this window.")


# Check if output file exists
if os.path.exists(output_geojson):
    with open(output_geojson, "rb") as f:
        st.download_button(
            label="Download Processed GeoJSON",
            data=f,
            file_name=os.path.basename(output_geojson),
            mime="application/json"
        )