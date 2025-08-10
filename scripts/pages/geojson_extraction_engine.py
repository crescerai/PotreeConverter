import os
import concurrent
import laspy as lp
import json
import pandas as pd
import numpy as np
from shapely.geometry import box, mapping, shape
from concurrent.futures import ThreadPoolExecutor, as_completed
import pyproj
from shapely.ops import transform
from tqdm import tqdm
import traceback
from pathlib import Path
import streamlit as st
import subprocess
import tempfile
import re


def get_file_info(file_path):
    """
    Uses `pdal info --all` to extract bounds, EPSG, and full summary from a file.
    
    Returns a dictionary with the extracted information or an error message.
    """
    try:
        # Using '--all' is the correct way to get both summary and metadata
        command = ['pdal', 'info', '--all', str(file_path)]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
    except FileNotFoundError:
        return {"error": "PDAL not found. Please ensure it is installed and in your system's PATH."}
    except subprocess.CalledProcessError as e:
        return {"error": f"PDAL failed to process the file: {e.stderr}"}
    except json.JSONDecodeError:
        return {"error": "Failed to parse PDAL's JSON output."}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

    info = {
        "summary": data.get("summary", {}),
        "metadata": data.get("metadata", {}),
        "bounds": None,
        "epsg": "unknown"
    }

    # 1. Extract Bounds - Fixed approach with multiple fallbacks
    # First try from summary.bounds
    if "summary" in data and "bounds" in data["summary"]:
        info["bounds"] = data["summary"]["bounds"]
        st.info(f"Bounds extracted from data['summary']['bounds']: {info['bounds']}")
    
    # Next try direct minx/maxx in summary
    elif "summary" in data and all(key in data["summary"] for key in ["minx", "maxx", "miny", "maxy", "minz", "maxz"]):
        info["bounds"] = {
            "minx": data["summary"]["minx"],
            "maxx": data["summary"]["maxx"],
            "miny": data["summary"]["miny"],
            "maxy": data["summary"]["maxy"],
            "minz": data["summary"]["minz"],
            "maxz": data["summary"]["maxz"]
        }
        st.info(f"Bounds extracted from summary min/max values: {info['bounds']}")
    
    # Try same fields at root level
    elif all(key in data for key in ["minx", "maxx", "miny", "maxy", "minz", "maxz"]):
        info["bounds"] = {
            "minx": data["minx"],
            "maxx": data["maxx"],
            "miny": data["miny"],
            "maxy": data["maxy"],
            "minz": data["minz"],
            "maxz": data["maxz"]
        }
        st.info(f"Bounds extracted from root min/max values: {info['bounds']}")
    
    # If all else fails, try the original fallback method
    else:
        st.warning("No bounds found in primary checks, trying fallback method...")
        try:
            command = ['pdal', 'info', '--summary', str(file_path)]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            summary_data = json.loads(result.stdout)
            
            # Look for bounds in both root and summary sections
            if "bounds" in summary_data:
                info["bounds"] = summary_data["bounds"]
                st.info(f"Bounds extracted from fallback method (root): {info['bounds']}")
            elif "summary" in summary_data and "bounds" in summary_data["summary"]:
                info["bounds"] = summary_data["summary"]["bounds"]
                st.info(f"Bounds extracted from fallback method (summary): {info['bounds']}")
            else:
                # Final attempt: try to find individual min/max coords
                for location in [summary_data, summary_data.get("summary", {})]:
                    if all(key in location for key in ["minx", "maxx", "miny", "maxy", "minz", "maxz"]):
                        info["bounds"] = {
                            "minx": location["minx"],
                            "maxx": location["maxx"],
                            "miny": location["miny"],
                            "maxy": location["maxy"],
                            "minz": location["minz"],
                            "maxz": location["maxz"]
                        }
                        st.info(f"Bounds extracted from min/max coordinates: {info['bounds']}")
                        break
                
                if not info["bounds"]:
                    st.warning("No bounds found in any location. This may indicate an issue with the file or PDAL's output.")
        except Exception as e:
            st.error(f"Error in fallback bounds extraction: {str(e)}")

    # 2. Extract EPSG (your existing code which works well)
    try:
        # First, try to get it from the modern srs.json structure
        srs_json = info["metadata"].get("srs", {}).get("json", {})
        if srs_json and "components" in srs_json:
            for component in srs_json["components"]:
                if component.get("type") == "ProjectedCRS":
                    info["epsg"] = str(component.get("id", {}).get("code", "unknown"))
                    break
        
        # If not found, fall back to parsing the WKT string
        if info["epsg"] == "unknown":
            st.info("No EPSG found in JSON, trying WKT...")
            wkt = info["metadata"].get("srs", {}).get("wkt", "") or info["metadata"].get("comp_spatialreference", "")
            match = re.search(r'AUTHORITY\["EPSG","(\d+)"\]', wkt, re.IGNORECASE)
            if match:
                info["epsg"] = match.group(1)
    except (TypeError, AttributeError):
        # This can happen if the metadata structure is unexpected.
        # The epsg will remain "unknown", which is handled gracefully by the UI.
        pass

    return info

    

def convert_copc_to_laz(copc_path, laz_path):
    """Converts a COPC file to a LAZ file using PDAL."""
    try:
        command = ['pdal', 'translate', str(copc_path), str(laz_path)]
        subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Successfully converted {copc_path} to {laz_path}")
        return True
    except FileNotFoundError:
        print("Error: 'pdal' command not found.")
        raise
    except subprocess.CalledProcessError as e:
        print(f"Error during PDAL conversion of {copc_path}: {e.stderr}")
        raise

# ==============================================================================
# CORE PROCESSING LOGIC
# ==============================================================================

def load_las(filename):
    """Loads a .las or .laz file, extracts classification counts and bounding box."""
    try:
        las = lp.read(filename)
        if len(las.x) == 0:
            return None, None, 0
        class_counts = pd.Series(np.array(las.classification)).value_counts().to_dict()
        bounding_box = {
            "min_x": las.header.mins[0], "min_y": las.header.mins[1], "min_z": las.header.mins[2],
            "max_x": las.header.maxs[0], "max_y": las.header.maxs[1], "max_z": las.header.maxs[2]
        }
        return class_counts, bounding_box, len(las.x)
    except Exception as e:
        raise


def process_file(args):
    """Processes a single file, handling COPC conversion, LAS loading, 
    bounding box creation, and optional source deletion.

    Args:
        args (tuple): (path, input_crs, save_converted, converted_output_folder, delete_source)

    Returns:
        dict: Processing result with file metadata, geometry, status, and errors (if any).
    """
    path, input_crs, save_converted, converted_output_folder, delete_source = args
    original_fname = os.path.basename(path)

    is_copc = str(path).lower().endswith(".copc.laz")
    laz_to_process = path
    temp_laz_file = None

    try:
        if is_copc:
            # Rename file extension
            new_laz_fname = original_fname[:-9] + ".laz"

            if save_converted and converted_output_folder:
                laz_to_process = Path(converted_output_folder) / new_laz_fname
            else:
                temp_f = tempfile.NamedTemporaryFile(suffix=".laz", delete=False)
                temp_laz_file = Path(temp_f.name)
                laz_to_process = temp_laz_file

            convert_copc_to_laz(path, laz_to_process)

        class_counts, bbox, total_points = load_las(laz_to_process)

        if bbox is None:
            return {
                "file": original_fname,
                "status": "error",
                "message": "File contains no points.",
            }

        bounds = box(bbox["min_x"], bbox["min_y"], bbox["max_x"], bbox["max_y"])

        result = {
            "file": original_fname,
            "status": "success",
            "geometry": mapping(bounds),
            "total_points": total_points,
            "class_distribution": {int(k): int(v) for k, v in class_counts.items()},
            "crs_used": input_crs,
        }

        if delete_source:
            try:
                if not is_copc or laz_to_process != path:
                    os.remove(path)
                    result["source_deleted"] = True
            except OSError as e:
                result["source_deleted"] = False
                result["delete_error"] = str(e)

        return result

    except Exception as e:
        return {
            "file": original_fname,
            "status": "error",
            "message": f"Failed to process: {e}\n{traceback.format_exc()}",
        }

    finally:
        if temp_laz_file and os.path.exists(temp_laz_file) and not save_converted:
            try:
                os.remove(temp_laz_file)
            except OSError:
                pass



def reproject_geom(geom, from_epsg, to_epsg):
    """Reprojects a shapely geometry."""
    if from_epsg == to_epsg: return geom
    project = pyproj.Transformer.from_crs(f"EPSG:{from_epsg}", f"EPSG:{to_epsg}", always_xy=True).transform
    return mapping(transform(project, shape(geom)))

def run_process(
    input_folder,
    output_geojson,
    input_crs,
    batchsize,
    temp_file,
    verbose,
    save_converted_copc,
    converted_copc_output_folder,
    delete_source_after_processing,
):
    """Main batch processor for LAS/LAZ/COPC files.

    Args:
        input_folder (Path): Folder containing LAS/LAZ/COPC files.
        output_geojson (Path): Path to save final GeoJSON output.
        input_crs (str): Input coordinate system (EPSG code).
        batchsize (int): Number of threads for parallel processing.
        temp_file (Path): Temporary NDJSON file for incremental writes.
        verbose (bool): Whether to show detailed processing logs.
        save_converted_copc (bool): Save converted COPC files.
        converted_copc_output_folder (Path): Output folder for converted COPC files.
        delete_source_after_processing (bool): Whether to delete source files after processing.
    """
    from_epsg = input_crs.replace("EPSG:", "")
    to_epsg = "4326"

    processed_files = set()
    if os.path.exists(temp_file):
        with open(temp_file, "r") as f:
            for line in f:
                try:
                    processed_files.add(json.loads(line)["properties"]["file"])
                except Exception:
                    continue

    all_files = [
        os.path.join(input_folder, f)
        for f in os.listdir(input_folder)
        if f.lower().endswith((".las", ".laz", ".copc.laz"))
    ]
    files_to_process = [
        f for f in all_files if os.path.basename(f) not in processed_files
    ]

    st.info(
        f"Found {len(files_to_process)} new files to process "
        f"(skipping {len(processed_files)} already completed)."
    )

    args = [
        (f, input_crs, save_converted_copc, converted_copc_output_folder, delete_source_after_processing)
        for f in files_to_process
    ]

    successful_files, failed_files = [], []
    progress_bar = st.progress(0)

    status_container = st.empty()
    file_status = st.empty()

    with ThreadPoolExecutor(max_workers=batchsize) as executor, open(temp_file, "a") as outf:
        futures = {executor.submit(process_file, arg): arg for arg in args}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            progress = (i + 1) / len(files_to_process)
            progress_bar.progress(progress)

            if not result:
                continue

            file_name, status = result["file"], result.get("status")
            status_container.info(
                f"Progress: {i+1}/{len(files_to_process)} files processed ({progress:.1%})"
            )

            if status == "success":
                result["geometry"] = reproject_geom(result["geometry"], from_epsg, to_epsg)
                feat = {
                    "type": "Feature",
                    "properties": {
                        k: v for k, v in result.items() if k not in ["geometry", "status"]
                    },
                    "geometry": result["geometry"],
                }
                outf.write(json.dumps(feat) + "\n")
                outf.flush()
                os.fsync(outf.fileno())

                file_status_msg = f"✓ Processed: {file_name}"
                if result.get("source_deleted", False):
                    file_status_msg += " (source deleted)"
                file_status.success(file_status_msg)

                successful_files.append(file_name)
            else:
                file_status.error(
                    f"✗ Failed: {file_name} - {result.get('message', 'Unknown error')}"
                )
                failed_files.append((file_name, result.get("message", "Unknown error")))

    status_container.empty()
    file_status.empty()

    features = [json.loads(line) for line in open(temp_file)]
    geojson = {"type": "FeatureCollection", "features": features}
    with open(output_geojson, "w") as f:
        json.dump(geojson, f, indent=2)

    st.success(
        f"Processing complete! GeoJSON with {len(features)} features saved to {output_geojson}"
    )
    if failed_files:
        st.warning(f"{len(failed_files)} files failed to process. See details below.")
        with st.expander("Show processing errors"):
            for fname, msg in failed_files:
                st.error(f"**{fname}**: {msg}")


# ==============================================================================
# STREAMLIT UI
# ==============================================================================

OUTPUT_DIR = Path("/app/potree/crescer/map_json")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def render_batch_processor():
    st.header("Batch Process Folder")
    inp = st.text_input("Input folder path", key="batch_input")
    
    col1, col2 = st.columns(2)
    input_crs = col1.text_input("Input CRS (if not in file)", value="EPSG:6350")
    batchsize = col2.number_input("Batch size", value=8, min_value=1, max_value=32)

    paths = []
    is_valid_path = False
    if inp and Path(inp).is_dir():
        paths = [p for p in os.listdir(inp) if p.lower().endswith((".las", ".laz"))]
        if paths:
            is_valid_path = True
            st.success(f"{len(paths)} LAS/LAZ/COPC files found.")
        else:
            st.warning("No .las, .laz, or .copc.laz files found.")
    elif inp:
        st.error("The provided path is not a valid directory.")

    if is_valid_path:
        # File handling options section
        st.subheader("File Handling Options")
        
        save_converted_copc = False
        converted_copc_output_folder = None
        delete_source_after_processing = False
        
        # COPC handling options
        if any(p.lower().endswith('.copc.laz') for p in paths):
            st.info("ℹ️ COPC files detected. They will be converted to LAZ for processing.")
            save_converted_copc = st.checkbox("Save converted LAZ files? (Otherwise, they are temporary)")
            if save_converted_copc:
                converted_copc_output_folder = st.text_input(
                    "Output folder for converted files", 
                    value=str(Path(inp) / "converted_laz")
                )
        
        # # Auto-delete option
        # delete_source_after_processing = st.checkbox(
        #     "Delete source files after processing", 
        #     value=False, 
        #     help="WARNING: This will permanently delete the source files after they have been processed and added to the GeoJSON."
        # )
        if delete_source_after_processing:
            st.warning("""
            ⚠️ **WARNING: You have enabled auto-deletion of source files.**
            
            Files will be permanently deleted after they are successfully processed.
            Make sure you have backups if needed.
            """)
        
        # Output options
        st.subheader("Output Options")
        suggested_name = Path(inp).stem + ".geojson"
        output_geojson = st.text_input("Output GeoJSON file name", value=suggested_name)
        
        # Add warning about process interruption
        st.warning("""
        **Important:** Processing runs within your browser session. If you close this tab, the current run will stop. 
        However, progress is saved automatically. You can safely restart the process later, and it will resume from where it left off.
        """)

        if st.button("Start Processing"):
            if save_converted_copc and converted_copc_output_folder:
                Path(converted_copc_output_folder).mkdir(parents=True, exist_ok=True)

            output_path = OUTPUT_DIR / output_geojson
            temp_file_path = output_path.with_suffix(".ndjson")
            
            run_process(
                input_folder=Path(inp), 
                output_geojson=output_path, 
                input_crs=input_crs,
                batchsize=batchsize, 
                temp_file=temp_file_path, 
                verbose=True,
                save_converted_copc=save_converted_copc,
                converted_copc_output_folder=converted_copc_output_folder,
                delete_source_after_processing=delete_source_after_processing
            )

def render_file_inspector():
    st.header("Single File Inspector")
    inspector_file = st.text_input("Path to a single .las, .laz, or .copc.laz file", key="inspector_input")

    if inspector_file and Path(inspector_file).is_file():
        if st.button("Inspect File"):
            with st.spinner("Analyzing file with PDAL..."):
                info = get_file_info(inspector_file)

            if "error" in info:
                st.error(info["error"])
            else:
                st.success("Analysis Complete!")
                if info["bounds"]:
                    b = info["bounds"]
                    st.write("**Bounding Box:**")
                    cols = st.columns(3)
                    cols[0].metric("Min X", f"{b.get('minx', 0):.2f}")
                    cols[1].metric("Min Y", f"{b.get('miny', 0):.2f}")
                    # ... and so on
                
                st.write("**Coordinate Reference System:**")
                if info["epsg"] != "unknown":
                    st.metric("Detected Horizontal EPSG Code", info["epsg"])
                    if info["bounds"]:
                        url = f"https://epsg.io/map#srs={info['epsg']}&x={info['bounds']['minx']}&y={info['bounds']['miny']}&z=10&layer=streets"
                        st.markdown(f"**[View location on epsg.io]({url})**")
                else:
                    st.warning("EPSG code not found in file.")
                    st.info("Please check the dataset's metadata or enter the CRS manually for the Batch Processor.")

                with st.expander("Show Full PDAL Summary"):
                    st.json(info.get("summary", {}))
                with st.expander("Show Full PDAL Metadata"):
                    st.json(info.get("metadata", {}))

def main():
    st.set_page_config(page_title="Lidar Data Tools", layout="wide")
    st.title("Lidar Data Processor and Inspector")

    tab1, tab2 = st.tabs(["Batch Processor", "File Inspector"])
    with tab1:
        render_batch_processor()
    with tab2:
        render_file_inspector()

if __name__ == "__main__":
    main()
