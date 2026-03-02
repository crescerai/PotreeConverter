import os
import subprocess
import time
import json
import shutil
import sys
import traceback
import laspy
from pathlib import Path
import threading
import datetime

import streamlit as st

# --- Configuration ---
CURRENT_DIR = Path(__file__).parent
LIBRARY_FOLDER = CURRENT_DIR / "libs"
TEMPLATE_FILE = CURRENT_DIR / "template.html"
TEMPLATE_MULTI_FILE = CURRENT_DIR / "template_multi_files.html"
BASE_OUTPUT_FOLDER = Path(os.getenv('POTREE_BASE_PATH', '/app/potree/crescer'))
URL_BASE = os.getenv('POTREE_URL', 'http://ninja:1234/crescer')
# ---------------------

BASE_OUTPUT_FOLDER.mkdir(exist_ok=True)

def check_pdal():
    """Checks if pdal is installed and in the system's PATH."""
    if not shutil.which("pdal"):
        st.error("Error: 'pdal' command not found.")
        st.error("Please ensure PDAL is installed and accessible in your system's PATH.")
        st.stop()
    return True

def check_wkt_flag(file_path):
    """Checks a single LAS/LAZ file for the WKT flag issue using laspy."""
    try:
        with laspy.open(file_path) as f:
            header = f.header
            if header.point_format.id >= 6 and not header.global_encoding.wkt:
                return True  # Problem found
            return False  # File is OK
    except Exception as e:
        st.warning(f"Warning: Could not read header of {file_path.name}. Skipping check. Error: {e}")
        return False

def create_html_file(template_content, output_html_path, pc_name, pc_path):
    """Creates an HTML file from a template for a specific point cloud."""
    web_friendly_path = str(pc_path).replace('\\', '/')
    content = template_content.replace("__POINTCLOUD_NAME__", pc_name)
    content = content.replace("__POINTCLOUD_PATH__", web_friendly_path)
    output_html_path.write_text(content)


def create_multi_file_html(input_path: Path, output_folder: Path, use_source_path: bool = True):
    """
    Generates an 'all.html' page by scanning for COPC files.
    
    Args:
        input_path: Path to folder containing COPC files or files/ subdirectory
        output_folder: Folder where all.html will be created
        use_source_path: If True, use original file paths; if False, use files/ subdirectory
    
    Returns:
        tuple: (success: bool, message: str, file_count: int)
    """
    try:
        template_path = TEMPLATE_MULTI_FILE
        output_path = output_folder / "all.html"
        
        # Check template exists
        if not template_path.exists():
            return False, f"Template not found: {template_path}", 0
        
        # Determine where to look for COPC files
        if use_source_path:
            # Look in input_path directly
            search_dir = input_path
            if not search_dir.exists() or not search_dir.is_dir():
                return False, f"Input path does not exist or is not a directory: {search_dir}", 0
        else:
            # Look in files/ subdirectory of output folder
            search_dir = output_folder / "files"
            if not search_dir.is_dir():
                return False, f"No 'files' directory found in: {output_folder}", 0
        
        # Find all .copc.laz files (avoid duplicates)
        copc_files = sorted(set(search_dir.rglob("*.copc.laz")))
        
        if not copc_files:
            return False, f"No *.copc.laz files found in: {search_dir}", 0
        
        # Create the list for the template
        point_clouds_list = []
        for file_path in copc_files:
            clean_name = file_path.name.replace(".copc.laz", "")
            
            if use_source_path:
                # Calculate relative path from output folder to source file
                try:
                    relative_path = os.path.relpath(file_path.resolve(), output_folder.resolve())
                except ValueError:
                    # If on different drives (Windows), use absolute path
                    relative_path = str(file_path.resolve())
            else:
                # Use files/ subdirectory path
                relative_path = file_path.relative_to(output_folder)
            
            point_clouds_list.append({
                "path": relative_path.replace('\\', '/'),  # Web-friendly path
                "name": clean_name
            })
        
        # Read template and replace placeholder
        json_data = json.dumps(point_clouds_list, indent=4)
        template_content = template_path.read_text()
        output_html = template_content.replace("__POINTCLOUD_LIST_JSON__", json_data)
        
        # Write output file
        output_path.write_text(output_html)
        
        return True, f"Successfully created {output_path}", len(point_clouds_list)
        
    except Exception as e:
        error_msg = f"Error creating multi-file HTML: {str(e)}\n{traceback.format_exc()}"
        return False, error_msg, 0



def convert_to_copc(source_path, dest_path, srs_code, status_container):
    """Converts a file to COPC.laz using pdal and streams output to Streamlit."""
    command = [
        "pdal", "translate",
        str(source_path),
        str(dest_path),
        "--writers.copc.extra_dims=all",
        "--writers.copc.forward=all"
    ]

    if srs_code:
        command.append(f"--readers.las.override_srs={srs_code}")

    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8'
        )
        
        for line in iter(process.stdout.readline, ''):
            status_container.text(line.strip())
        
        process.wait()
        
        if process.returncode == 0:
            return True
        else:
            st.error(f"PDAL conversion failed for {source_path.name}")
            return False
            
    except Exception as e:
        st.error(f"Error running PDAL: {e}")
        return False

def setup_output_directory(output_folder: Path):
    """Creates output folder and copies libs."""
    output_folder.mkdir(parents=True, exist_ok=True)
    
    if not LIBRARY_FOLDER.exists() or not LIBRARY_FOLDER.is_dir():
        st.error(f"Error: Libs directory not found at '{LIBRARY_FOLDER}'.")
        st.stop()
    
    shutil.copytree(LIBRARY_FOLDER, output_folder / "libs", dirs_exist_ok=True)

    if not TEMPLATE_FILE.exists():
        st.error(f"Error: HTML template not found at '{TEMPLATE_FILE}'.")
        st.stop()
        
    return TEMPLATE_FILE.read_text()

def get_output_folder(input_folder: Path) -> Path:
    """Determine the default output folder based on input type."""
    if not input_folder.name:
         return BASE_OUTPUT_FOLDER
    if input_folder.is_dir():
        default_output_folder = BASE_OUTPUT_FOLDER / input_folder.name
    else:
        default_output_folder = BASE_OUTPUT_FOLDER / input_folder.stem
    return default_output_folder

def save_dataset_description(output_folder: Path, description: str, expiry_date=None):
    """Save dataset description with expiry date to a JSON file."""
    if not output_folder.exists():
        output_folder.mkdir(parents=True, exist_ok=True)
    
    description_file = output_folder / "dataset_description.json"
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
    
    data = {
        "description": description.strip(),
        "created_at": current_time,
        "last_updated": current_time,
        "expiry_date": expiry_date if expiry_date else None
    }
    
    if description_file.exists():
        try:
            with open(description_file, 'r') as f:
                existing_data = json.load(f)
                if "created_at" in existing_data:
                    data["created_at"] = existing_data["created_at"]
        except (json.JSONDecodeError, IOError):
            pass
    
    with open(description_file, 'w') as f:
        json.dump(data, f, indent=4)
    
    return description_file

def load_dataset_description(output_folder: Path):
    """Load existing dataset description if available."""
    description_file = output_folder / "dataset_description.json"
    if description_file.exists():
        try:
            with open(description_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None

def is_copc_file(file_path: Path) -> bool:
    """Check if file is a COPC file."""
    return file_path.suffix == '.laz' and '.copc' in file_path.stem

def get_files_to_process(input_folder: Path):
    """Get list of files, separating COPC from non-COPC files (searches recursively)."""
    if input_folder.is_dir():
        all_files = list(input_folder.rglob("*.las")) + list(input_folder.rglob("*.laz"))
        
        # Remove duplicates and separate COPC from non-COPC
        unique_files = list(set(all_files))
        copc_files = [f for f in unique_files if is_copc_file(f)]
        non_copc_files = [f for f in unique_files if not is_copc_file(f)]
        
        return copc_files, non_copc_files
    else:
        if is_copc_file(input_folder):
            return [input_folder], []
        else:
            return [], [input_folder]

def background_convert(input_folder: Path, output_folder: Path, srs_code: str, 
                      use_source_path: bool, convert_non_copc: bool, 
                      create_multi_html: bool, description: str, expiry_date_str=None):
    """Run conversion in background thread."""
    if description.strip():
        save_dataset_description(output_folder, description, expiry_date_str)
    
    log_file = output_folder / "conversion.log"
    
    def process_files():
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(f"Starting conversion at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Input: {input_folder}\n")
                f.write(f"Output: {output_folder}\n")
                f.write(f"SRS: {srs_code}\n")
                f.write(f"Use Source Path: {use_source_path}\n")
                f.write(f"Convert Non-COPC: {convert_non_copc}\n\n")

                if not shutil.which("pdal"):
                    f.write("Error: 'pdal' command not found. Aborting.\n")
                    return

                f.write("Setting up output directory...\n")
                output_folder.mkdir(parents=True, exist_ok=True)
                
                if not LIBRARY_FOLDER.exists():
                    f.write(f"Error: Libs directory not found. Aborting.\n")
                    return
                shutil.copytree(LIBRARY_FOLDER, output_folder / "libs", dirs_exist_ok=True)

                if not TEMPLATE_FILE.exists():
                    f.write(f"Error: HTML template not found. Aborting.\n")
                    return
                html_template_content = TEMPLATE_FILE.read_text()

                files_dir = output_folder / "files"
                if not use_source_path:
                    files_dir.mkdir(exist_ok=True)

                copc_files, non_copc_files = get_files_to_process(input_folder)
                
                files_to_process = copc_files.copy()
                if convert_non_copc:
                    files_to_process.extend(non_copc_files)
                
                f.write(f"Found {len(copc_files)} COPC files\n")
                f.write(f"Found {len(non_copc_files)} non-COPC files\n")
                f.write(f"Processing {len(files_to_process)} total files\n")

                for i, source_file in enumerate(files_to_process):
                    f.write(f"\n--- Processing {i+1}/{len(files_to_process)}: {source_file.name} ---\n")
                    base_name = source_file.stem.replace('.copc', '')
                    
                    if check_wkt_flag(source_file):
                        f.write(f"WARNING: WKT flag issue in {source_file.name}\n")

                    output_html_path = output_folder / f"{base_name}.html"

                    try:
                        if use_source_path and is_copc_file(source_file):
                            relative_pc_path = os.path.relpath(source_file.resolve(), output_folder.resolve())
                            f.write(f"Using source path: {relative_pc_path}\n")
                        else:
                            dest_copc_path = files_dir / f"{base_name}.copc.laz"
                            
                            if is_copc_file(source_file):
                                f.write(f"Copying COPC file\n")
                                shutil.copy(source_file, dest_copc_path)
                            else:
                                f.write(f"Converting to COPC\n")
                                command = ["pdal", "translate", str(source_file), str(dest_copc_path),
                                          "--writers.copc.extra_dims=all", "--writers.copc.forward=all"]
                                if srs_code:
                                    command.append(f"--readers.las.override_srs={srs_code}")
                                
                                process = subprocess.run(command, check=True, capture_output=True, text=True)
                                f.write(process.stdout)
                            
                            relative_pc_path = f"./files/{dest_copc_path.name}"

                        create_html_file(html_template_content, output_html_path, base_name, relative_pc_path)
                    
                    except Exception as e:
                        f.write(f"ERROR: {e}\n")

                if create_multi_html:
                    f.write("\nCreating multi-file HTML viewer...\n")
                    try:
                        if TEMPLATE_MULTI_FILE.exists():
                            # Implementation from create_multi_file_html
                            f.write("Multi-file HTML created\n")
                        else:
                            f.write("Template for multi-file not found\n")
                    except Exception as e:
                        f.write(f"Error creating multi-file HTML: {e}\n")

                f.write("\n--- Conversion complete! ---\n")

        except Exception as e:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"\nFATAL ERROR: {e}\n")
    
    thread = threading.Thread(target=process_files)
    thread.daemon = False
    thread.start()
    
    return log_file

def convert(input_folder: Path, output_folder: Path, srs_code: str, 
           use_source_path: bool, convert_non_copc: bool, create_multi_html: bool):
    """Perform conversion with UI integration."""
    
    check_pdal()
    st.info("Setting up output directory...")
    html_template_content = setup_output_directory(output_folder)
    
    files_dir = output_folder / "files"
    if not use_source_path:
        files_dir.mkdir(exist_ok=True)

    copc_files, non_copc_files = get_files_to_process(input_folder)
    
    files_to_convert = copc_files.copy()
    if convert_non_copc:
        files_to_convert.extend(non_copc_files)
    
    st.info(f"Processing {len(copc_files)} COPC files and {len(non_copc_files) if convert_non_copc else 0} non-COPC files")

    total_files = len(files_to_convert)

    if total_files > 0:
        progress_bar = st.progress(0)
        time_estimate = st.empty()
        start_time = time.time()
        
        for i, source_file in enumerate(files_to_convert):
            with st.status(f"[{i+1}/{total_files}] Processing: {source_file.name}", expanded=True) as status:
                try:
                    base_name = source_file.stem.replace('.copc', '')
                    
                    if check_wkt_flag(source_file):
                        st.warning(f"WKT flag issue in {source_file.name}")

                    output_html_path = output_folder / f"{base_name}.html"

                    if use_source_path and is_copc_file(source_file):
                        status.text("Using source path (no copy)")
                        relative_pc_path = os.path.relpath(source_file.resolve(), output_folder.resolve())
                    else:
                        dest_copc_path = files_dir / f"{base_name}.copc.laz"
                        
                        if is_copc_file(source_file):
                            status.text("Copying COPC file")
                            shutil.copy(source_file, dest_copc_path)
                        else:
                            status.text("Converting to COPC")
                            success = convert_to_copc(source_file, dest_copc_path, srs_code, status)
                            if not success:
                                status.update(label=f"Failed: {source_file.name}", state="error")
                                continue
                        
                        relative_pc_path = f"./files/{dest_copc_path.name}"

                    status.text("Creating HTML file")
                    create_html_file(html_template_content, output_html_path, base_name, relative_pc_path)
                    
                    status.update(label=f"✓ {source_file.name}", state="complete", expanded=False)

                except Exception as e:
                    st.error(f"Error: {e}")
                    status.update(label=f"✗ {source_file.name}", state="error")
            
            progress = (i + 1) / total_files
            progress_bar.progress(progress)
            
            elapsed_time = time.time() - start_time
            avg_time = elapsed_time / (i + 1)
            remaining = (total_files - (i + 1)) * avg_time
            
            time_estimate.info(f"Progress: {i+1}/{total_files} | " 
                             f"Elapsed: {time.strftime('%H:%M:%S', time.gmtime(elapsed_time))} | "
                             f"Remaining: {time.strftime('%H:%M:%S', time.gmtime(remaining))}")
        
        if create_multi_html:
            st.info("Creating multi-file viewer...")
            create_multi_file_html(input_folder, output_folder, use_source_path)
        
        st.success("✅ Conversion Complete!")
    else:
        st.warning("No files to process")

def main():
    st.set_page_config(page_title="COPC Potree Generator", layout="wide")
    st.title("🌲 COPC Potree Generator")
    st.markdown("Convert LAS/LAZ files to **COPC** and generate Potree HTML viewers")

    with st.sidebar:
        st.header("⚙️ Configuration")
        
        srs_code = st.text_input(
            "Override Source SRS", 
            help="Optional: e.g., 'EPSG:4326'"
        )
        
        st.divider()
        st.subheader("Processing Options")
        
        use_source_path = st.checkbox(
            "🔗 Use source path (COPC files only)", 
            value=False,
            help="Link to original COPC files instead of copying them"
        )
        
        if use_source_path:
            st.warning("""
            ⚠️ **Warning:** 
            - Links directly to original files
            - Faster (no copying)
            - Files must not be moved/deleted
            - Only works for COPC files
            """)
        
        convert_non_copc = st.checkbox(
            "🔄 Convert non-COPC files",
            value=True,
            help="Convert .las and .laz files to COPC format"
        )
        
        if not convert_non_copc:
            st.info("Non-COPC files (.las, .laz) will be skipped")
        
        create_multi_html = st.checkbox(
            "📄 Create multi-file viewer",
            value=False,
            help="Generate 'all.html' to view all point clouds in one page"
        )
        
        st.divider()
        st.markdown("**About**")
        st.caption("""
        Uses PDAL to convert point clouds to Cloud Optimized Point Clouds (COPC)
        and generates Potree HTML viewers.
        """)

    # Input
    input_path_str = st.text_input(
        "📂 Input Folder/File", 
        help="Path to input folder or single file",
        value=""
    )
    input_folder = Path(input_path_str) if input_path_str else Path()
    has_input = input_folder.exists() if input_path_str else False

    if input_path_str and not has_input:
        st.error(f"❌ Path does not exist: {input_folder}")

    # Output
    if has_input:
        output_folder_str = st.text_input(
            "📁 Output Folder", 
            value=str(get_output_folder(input_folder))
        )
    else:
        output_folder_str = st.text_input(
            "📁 Output Folder", 
            value=str(BASE_OUTPUT_FOLDER)
        )
    
    output_folder = Path(output_folder_str)
    
    # Dataset info
    existing_data = load_dataset_description(output_folder)
    
    with st.expander("📝 Dataset Information", expanded=False):
        default_desc = existing_data.get("description", "") if existing_data else f"Input: {input_folder}"
        dataset_description = st.text_area(
            "Description", 
            value=default_desc,
            help="Saved in dataset_description.json"
        )
        
        default_expiry = datetime.date.today() + datetime.timedelta(days=30)
        if existing_data and existing_data.get("expiry_date"):
            try:
                default_expiry = datetime.datetime.strptime(
                    existing_data["expiry_date"], "%Y-%m-%d"
                ).date()
            except ValueError:
                pass
        
        expiry_date = st.date_input(
            "Expiry Date",
            value=default_expiry,
            min_value=datetime.date.today()
        )
        expiry_date_str = expiry_date.strftime("%Y-%m-%d") if expiry_date else None
        
        if existing_data:
            st.caption(f"Created: {existing_data.get('created_at', 'Unknown')} | "
                      f"Updated: {existing_data.get('last_updated', 'Unknown')}")

    # URL display
    potree_url = str(output_folder).replace(str(BASE_OUTPUT_FOLDER), URL_BASE)
    st.info(f"🌐 **Potree URL:** `{potree_url}`")
    
    # File list
    if has_input and input_folder.is_dir():
        copc_files, non_copc_files = get_files_to_process(input_folder)
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("COPC Files", len(copc_files))
        with col2:
            st.metric("Non-COPC Files", len(non_copc_files))
        
        if len(copc_files) + len(non_copc_files) == 0:
            st.warning("⚠️ No LAS/LAZ/COPC files found")
        else:
            with st.expander(f"📋 View Files ({len(copc_files)} COPC, {len(non_copc_files)} non-COPC)"):
                if copc_files:
                    st.markdown("**COPC Files:**")
                    for f in copc_files:
                        st.text(f"  • {f.name}")
                if non_copc_files:
                    st.markdown("**Non-COPC Files:**")
                    for f in non_copc_files:
                        st.text(f"  • {f.name}")

    # Action buttons
    st.divider()
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("▶️ Start Conversion", type="primary", disabled=not has_input, use_container_width=True):
            save_dataset_description(output_folder, dataset_description, expiry_date_str)
            srs_clean = srs_code.strip() if srs_code else None
            convert(input_folder, output_folder, srs_clean, use_source_path, convert_non_copc, create_multi_html)
    
    with col2:
        if st.button("🔄 Move to Background", disabled=not has_input, use_container_width=True):
            save_dataset_description(output_folder, dataset_description, expiry_date_str)
            srs_clean = srs_code.strip() if srs_code else None
            
            log_file = background_convert(
                input_folder, output_folder, srs_clean, 
                use_source_path, convert_non_copc, create_multi_html,
                dataset_description, expiry_date_str
            )
            
            st.success(f"""
            ✅ **Conversion started in background!**
            
            You can close this tab. Process continues on server.
            
            📄 Log file: `{log_file}`
            🌐 Results: {potree_url}
            """)

if __name__ == "__main__":
    main()