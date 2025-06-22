
import os
import laspy
import json
import pandas as pd
from shapely.geometry import box, mapping , shape
from concurrent.futures import ThreadPoolExecutor, as_completed
import pyproj
from shapely.ops import transform
import re
from tqdm import tqdm
import traceback
from pathlib import Path

import streamlit as st

def parse_bbox_from_name(filename):
    m = re.search(r"e(\d+)n(\d+)", filename)
    if m:
        x = int(m.group(1)) * 1000
        y = int(m.group(2)) * 1000
        return x, y
    return None, None

def process_las_file(args):
    path, input_crs = args
    fname = os.path.basename(path)
    try:
        las = laspy.read(path)
        if len(las.x) == 0:
            return {"file": fname, "status": "error", "message": "File contains no points"}
        
        minx, maxx = float(min(las.x)), float(max(las.x))
        miny, maxy = float(min(las.y)), float(max(las.y))
        
        # Check if bounding box is too small
        if (maxx-minx < 1) or (maxy-miny < 1):
            x, y = parse_bbox_from_name(fname)
            if x is not None and y is not None:
                minx, miny = x, y
                maxx, maxy = x+1000, y+1000
            else:
                return {
                    "file": fname, 
                    "status": "error", 
                    "message": f"Degenerate bounding box: {minx},{miny} to {maxx},{maxy}"
                }
        
        bounds = box(minx, miny, maxx, maxy)
        total_points = len(las.x)
        class_counts = {}
        if hasattr(las, 'classification'):
            s = pd.Series(las.classification)
            class_counts = {int(cls): int(cnt) for cls, cnt in s.value_counts().items()}
        
        return {
            "file": fname,
            "status": "success",
            "geometry": mapping(bounds),
            "total_points": total_points,
            "class_distribution": class_counts,
            "crs_used": input_crs
        }
    except Exception as e:
        # Try to extract bounding box from filename
        x, y = parse_bbox_from_name(fname)
        if x is not None and y is not None:
            bounds = box(x, y, x+1000, y+1000)
            return {
                "file": fname,
                "status": "partial",
                "geometry": mapping(bounds),
                "total_points": 0,
                "class_distribution": {},
                "crs_used": input_crs,
                "message": f"Error reading file, used bbox from filename: {str(e)}"
            }
        else:
            return {
                "file": fname,
                "status": "error",
                "message": f"Failed to process: {str(e)}\n{traceback.format_exc()}"
            }

def reproject_geom(geom, from_epsg, to_epsg):
    if from_epsg == to_epsg:
        return geom
    project = pyproj.Transformer.from_crs(from_epsg, to_epsg, always_xy=True).transform
    return mapping(transform(project, shape(geom)))

def run_process(input_folder, output_geojson, input_crs="EPSG:6350", batchsize=16, temp_file=None, verbose=False):
    from_epsg = input_crs.replace("EPSG:", "")
    to_epsg = "4326"
    if temp_file is None:
        temp_file = output_geojson + ".ndjson"
    
    # Track processing results
    successful_files = []
    partial_files = []
    failed_files = []
    
    # Find unprocessed files:
    processed_files = set()
    if os.path.exists(temp_file):
        with open(temp_file, "r") as f:
            for line in f:
                try:
                    feat = json.loads(line)
                    processed_files.add(feat["properties"]["file"])
                    successful_files.append(feat["properties"]["file"])
                except Exception:
                    continue
    
    files = [os.path.join(input_folder, f) for f in os.listdir(input_folder) if f.lower().endswith((".las", ".laz"))]
    files = [f for f in files if os.path.basename(f) not in processed_files]
    

    print(f"Found {len(files)} files to process...")
    args = [(f, input_crs) for f in files]
    
    # Process in parallel
    with ThreadPoolExecutor(max_workers=batchsize) as executor, open(temp_file, "a") as outf:
        futures = {executor.submit(process_las_file, arg): arg for arg in args}
        for future in tqdm(as_completed(futures), total=len(futures)):
            result = future.result()
            if result is None:
                continue
                
            file_name = result["file"]
            status = result.get("status", "unknown")
            
            if status == "success":
                # Reproject to EPSG:4326 for web mapping
                result["geometry"] = reproject_geom(result["geometry"], from_epsg, to_epsg)
                feat = {
                    "type": "Feature",
                    "properties": {
                        "file": file_name,
                        "total_points": result["total_points"],
                        "class_distribution": result["class_distribution"],
                        "crs_used": result["crs_used"]
                    },
                    "geometry": result["geometry"]
                }
                outf.write(json.dumps(feat) + "\n")
                outf.flush()  # Realtime write
                successful_files.append(file_name)
                if verbose:
                    print(f"✓ Processed: {file_name} - {result['total_points']} points")
            
            elif status == "partial":
                # Still include in output but with zero points
                result["geometry"] = reproject_geom(result["geometry"], from_epsg, to_epsg)
                feat = {
                    "type": "Feature",
                    "properties": {
                        "file": file_name,
                        "total_points": 0,
                        "class_distribution": {},
                        "crs_used": result["crs_used"],
                        "warning": result.get("message", "Partial processing")
                    },
                    "geometry": result["geometry"]
                }
                outf.write(json.dumps(feat) + "\n")
                outf.flush()
                partial_files.append((file_name, result.get("message", "Unknown error")))
                if verbose:
                    print(f"⚠ Partial: {file_name} - {result.get('message', 'Unknown error')}")
            
            elif status == "error":
                failed_files.append((file_name, result.get("message", "Unknown error")))
                if verbose:
                    print(f"✗ Failed: {file_name} - {result.get('message', 'Unknown error')}")
            
            else:
                failed_files.append((file_name, "Unknown processing status"))
                if verbose:
                    print(f"? Unknown: {file_name} - Unknown processing status")

    # Now combine all NDJSON lines into one GeoJSON FeatureCollection
    features = []
    with open(temp_file, "r") as f:
        for line in f:
            try:
                features.append(json.loads(line))
            except Exception as e:
                print(f"Error parsing line in temp file: {str(e)}")
                continue
    
    crs_obj = {
        "type": "name",
        "properties": {
            "name": "urn:ogc:def:crs:OGC:1.3:CRS84"
        }
    }
    geojson = {
        "type": "FeatureCollection",
        "name": "las_data",
        "crs": crs_obj,
        "features": features
    }
    
    with open(output_geojson, "w") as f:
        json.dump(geojson, f, indent=3)

    
    # Print summary report
    total = len(successful_files) + len(partial_files) + len(failed_files)
    print(f"\nProcessing Summary:")
    print(f"- Successfully processed: {len(successful_files)}/{total} files")
    print(f"- Partially processed: {len(partial_files)}/{total} files")
    print(f"- Failed to process: {len(failed_files)}/{total} files")
    print(f"- GeoJSON written with {len(features)} features to {output_geojson}")
    
    # Print failed files details
    if failed_files:
        print("\nFailed files:")
        for i, (fname, err) in enumerate(failed_files, 1):
            print(f"{i}. {fname}: {err[:100]}{'...' if len(err) > 100 else ''}")
    
    # Write error report
    error_report = str(output_geojson) + ".errors.txt"

    if len(failed_files) != 0 or len(partial_files) != 0:
        with open(error_report, "w") as f:
            f.write(f"Processing report for {input_folder}\n")
            f.write(f"Total files: {total}\n")
            f.write(f"Success: {len(successful_files)}\n")
            f.write(f"Partial: {len(partial_files)}\n")
            f.write(f"Failed: {len(failed_files)}\n\n")
            
            if partial_files:
                f.write("== PARTIALLY PROCESSED FILES ==\n")
                for fname, err in partial_files:
                    f.write(f"{fname}: {err}\n")
                f.write("\n")
            
            if failed_files:
                f.write("== FAILED FILES ==\n")
                for fname, err in failed_files:
                    f.write(f"{fname}: {err}\n\n")
        
        if failed_files or partial_files:
            print(f"\nDetailed error report written to {error_report}")


# Hardcoded output folder
OUTPUT_DIR = Path("/app/potree/crescer/map_json")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)



def main():
    st.title("LAS/LAZ → GeoJSON Processor")

    inp = st.text_input("Input file or folder path")
    input_crs = st.text_input("Input CRS", value="EPSG:6350")
    batchsize = st.number_input("Batch size", value=10, min_value=1, max_value=32)

    ok = False
    paths = []
    if inp:
        p = Path(inp)
        if not p.exists():
            st.error("Path does not exist!")
        else:
            if p.is_file() and p.suffix.lower() in (".las", ".laz"):
                paths = [str(p)]
            elif p.is_dir():
                paths = [str(p / f) for f in os.listdir(p) if f.lower().endswith((".las", ".laz"))]
            else:
                st.error("Unsupported input type.")
            ok = bool(paths)
            if ok:
                st.success(f"{len(paths)} LAS/LAZ files found.")

    if ok:
        suggested = Path(inp).stem + ".geojson"
        output_geojson = st.text_input("Output GeoJSON file name", value=suggested)
        temp_file = str(output_geojson) + ".ndjson"
        st.write(f"📄 Output file: `{output_geojson}` (in `{OUTPUT_DIR}`)")
        already = []
        temp_path = OUTPUT_DIR / temp_file
        st.write(f"📝 Temp file: `{temp_file}` (in `{temp_path}`)")
        if os.path.exists(temp_path):
            with open(temp_path) as f:
                already = {json.loads(l)["properties"]["file"] for l in f}
            st.info(f"{len(already)} files already processed; will process remaining {len(paths) - len(already)}.")
        else:
            st.info("No prior runs – will process all files.")

        if st.button("Start Processing"):
            run_process(
                input_folder=Path(inp),
                output_geojson=OUTPUT_DIR / output_geojson,
                input_crs=input_crs,
                batchsize=batchsize,
                temp_file=OUTPUT_DIR /temp_file,
                verbose=True
            )


if __name__ == "__main__":
    main()
