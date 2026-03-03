"""
Streamlit GUI for applying Potree annotations to LAS/LAZ files.

This application allows users to:
- Input a Potree URL (folder or single HTML file)
- Automatically locate input folders and annotation directories
- Apply annotations to point cloud files
- Save output as LAS, LAZ, or COPC.LAZ format
- Update existing files with annotations (backup mode)
"""

import subprocess
import streamlit as st
import json
import re
import os
from pathlib import Path
from urllib.parse import urlparse
import pandas as pd
from datetime import datetime
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback
import shutil

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).resolve().parent.parent))
from apply_annotation_new_labeling import apply_annotation_on_file

# ==================== CONFIGURATION ====================
POTREE_BASE_PATH = Path(os.getenv("HOST_POTREE_BASE_PATH", os.getenv("POTREE_BASE_PATH", "/app/potree/crescer")))
URL_BASE = os.getenv("POTREE_URL", "http://ninja:1234/crescer")

EXCLUDED_FOLDERS = {"libs", "pointclouds", "annotations"}

# ==================== SESSION STATE ====================
if 'processing_status' not in st.session_state:
    st.session_state.processing_status = {
        'running': False,
        'processed': 0,
        'total': 0,
        'errors': [],
        'success': [],
        'current_file': None
    }

# ==================== HELPER FUNCTIONS ====================

def url_to_filesystem_path(url: str) -> Path:
    """Convert a Potree URL to filesystem path."""
    if url.startswith(URL_BASE):
        relative_path = url[len(URL_BASE):].lstrip('/')
    else:
        parsed = urlparse(url)
        path_parts = parsed.path.split('/crescer/')
        if len(path_parts) > 1:
            relative_path = path_parts[1]
        else:
            raise ValueError(f"Cannot parse URL: {url}")
    
    return POTREE_BASE_PATH / relative_path


def find_dataset_description(folder_path: Path) -> dict:
    """Find and parse dataset_description.json in a folder."""
    desc_file = folder_path / "dataset_description.json"
    
    if not desc_file.exists():
        return None
    
    try:
        with open(desc_file, 'r') as f:
            data = json.load(f)
        
        input_folder = None
        if "description" in data:
            match = re.search(r'Input_Folder:\s*(.+)', data["description"])
            if match:
                input_folder = match.group(1).strip()
        
        return {
            "description": data.get("description", ""),
            "input_folder": input_folder,
            "created_at": data.get("created_at", ""),
            "last_updated": data.get("last_updated", ""),
            "expiry_date": data.get("expiry_date", ""),
            "file_path": str(desc_file)
        }
    except Exception as e:
        st.error(f"Error reading dataset_description.json: {e}")
        return None


def find_all_html_files(folder_path: Path, recursive: bool = True) -> list:
    """Find all HTML files in a folder, excluding certain subfolders."""
    html_files = []
    
    if not folder_path.exists() or not folder_path.is_dir():
        return html_files
    
    # Direct HTML files in this folder
    html_files.extend(folder_path.glob("*.html"))
    
    # Search subfolders if recursive
    if recursive:
        for item in folder_path.iterdir():
            if item.is_dir() and item.name not in EXCLUDED_FOLDERS:
                html_files.extend(find_all_html_files(item, recursive=True))
    
    return sorted(html_files)


def get_annotation_path(html_file: Path) -> Path:
    """Get the corresponding annotation JSON file for an HTML file."""
    parent = html_file.parent
    annotation_dir = parent / "annotations"
    annotation_file = annotation_dir / f"{html_file.stem}.json"
    return annotation_file


def find_matching_files(html_file: Path, input_folder: Path, update_mode: bool = False) -> dict:
    """
    Find matching LAS/LAZ/COPC file and annotation for an HTML file.
    
    In update mode:
        - Looks for COPC files in parent/files/ folder
        - Annotation in parent/annotations/
    
    In normal mode:
        - Looks for LAS/LAZ in input_folder
        - Annotation in parent/annotations/
    
    Returns dict with:
        - las_file: Path to LAS/LAZ file (or None)
        - annotation_file: Path to annotation JSON (or None)
        - valid: bool indicating if all required files exist
    """
    result = {
        'las_file': None,
        'annotation_file': None,
        'valid': False,
        'missing': []
    }
    
    # Find annotation file
    annotation_file = get_annotation_path(html_file)
    if annotation_file.exists():
        result['annotation_file'] = annotation_file
    else:
        result['missing'].append(f"annotation: {annotation_file.name}")
    
    # Find LAS/LAZ file
    if update_mode:
        # In update mode, look for COPC files in parent/files/
        files_folder = html_file.parent / "files"
        if files_folder.exists():
            # Look for matching COPC file
            copc_file = files_folder / f"{html_file.stem}.copc.laz"
            if copc_file.exists():
                result['las_file'] = copc_file
            else:
                result['missing'].append(f"COPC file: {copc_file.name}")
        else:
            result['missing'].append(f"files folder not found")
    else:
        # In normal mode, look for LAS/LAZ in input folder
        if input_folder and input_folder.exists():
            # Try exact match first
            for ext in ['.laz', '.las' , '.copc.laz']:
                las_file = input_folder / f"{html_file.stem}{ext}"
                if las_file.exists():
                    result['las_file'] = las_file
                    break
            
            if not result['las_file']:
                result['missing'].append(f"LAS/LAZ file: {html_file.stem}.las/laz")
        else:
            result['missing'].append("input folder not specified")
    
    # Check if valid (all required files found)
    result['valid'] = (result['las_file'] is not None and 
                       result['annotation_file'] is not None)
    
    return result


def validate_paths(html_files: list, input_folder: Path, update_mode: bool = False) -> tuple:
    """
    Validate that all required files exist for each HTML file.
    
    Returns:
        Tuple of (valid_items, errors, warnings)
    """
    valid_items = []
    errors = []
    warnings = []
    
    for html_file in html_files:
        match_result = find_matching_files(html_file, input_folder, update_mode)
        
        if match_result['valid']:
            valid_items.append({
                'html': html_file,
                'html_name': html_file.stem,
                'annotation': match_result['annotation_file'],
                'las_file': match_result['las_file'],
                'update_mode': update_mode
            })
        else:
            missing_str = ", ".join(match_result['missing'])
            warnings.append(f"⚠️ Skipping {html_file.name}: Missing {missing_str}")
    
    if not valid_items:
        errors.append("No valid file sets found (HTML + Annotation + LAS/LAZ)")
    
    return valid_items, errors, warnings


def convert_to_copc(source_path, dest_path, srs_code=None):
    """Converts a file to COPC.laz using pdal."""
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
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"\nError converting {source_path.name}.", file=sys.stderr)
        print(f"PDAL Error: {e.stderr}", file=sys.stderr)
        return False


def process_single_file(
    item: dict,
    output_dir: Path,
    output_format: str,
    num_workers: int,
    chunksize: int
) -> dict:
    """
    Process a single file set (HTML + Annotation + LAS).
    
    In update mode:
        - Reads from item['las_file'] (COPC in files/)
        - Writes back to same location
        - Renames annotation with timestamp (no copy, just rename)
    
    In normal mode:
        - Reads from item['las_file'] (from input folder)
        - Writes to output_dir
    """
    try:
        las_file = item['las_file']
        annotation_file = item['annotation']
        update_mode = item.get('update_mode', False)
        
        if update_mode:
            # UPDATE MODE: Write back to same location
            
            # Rename annotation file with timestamp BEFORE processing
            timestamp = int(time.time())
            backup_annotation = annotation_file.parent / f"{timestamp}_{annotation_file.name}"
            annotation_file.rename(backup_annotation)  # Direct rename, no copy
            
            # Create temp file for processing
            temp_output = las_file.parent / f"{las_file.stem}_temp.laz"
            
            # Process using the renamed (backup) annotation
            apply_annotation_on_file(
                str(las_file),
                str(backup_annotation),  # Use the renamed file
                str(temp_output),
                num_workers=num_workers,
                chunksize=chunksize
            )
            
            # Convert back to COPC if needed
            if las_file.suffix == '.laz' and las_file.stem.endswith('.copc'):
                final_output = las_file
                success = convert_to_copc(temp_output, final_output)
                temp_output.unlink()  # Remove temp
                
                if not success:
                    # Restore annotation file on failure
                    backup_annotation.rename(annotation_file)
                    return {
                        'status': 'error',
                        'file': las_file.name,
                        'error': 'PDAL conversion to COPC failed'
                    }
            else:
                # Replace original with temp
                las_file.unlink()
                temp_output.rename(las_file)
            
            return {
                'status': 'success',
                'file': las_file.name,
                'output': str(las_file),
                'backup_annotation': str(backup_annotation),
                'mode': 'update',
                'note': 'Original annotation renamed (not copied)'
            }
        
        else:
            # NORMAL MODE: Write to output directory
            if output_format == 'copc.laz':
                # Create temp .laz and convert to COPC
                temp_output = output_dir / f"{item['html_name']}.laz"
                
                apply_annotation_on_file(
                    str(las_file),
                    str(annotation_file),
                    str(temp_output),
                    num_workers=num_workers,
                    chunksize=chunksize
                )
                
                # Convert to COPC
                final_output = output_dir / f"{item['html_name']}.copc.laz"
                success = convert_to_copc(temp_output, final_output)
                
                if success:
                    temp_output.unlink()  # Remove temp
                    return {
                        'status': 'success',
                        'file': las_file.name,
                        'output': str(final_output),
                        'mode': 'normal'
                    }
                else:
                    return {
                        'status': 'error',
                        'file': las_file.name,
                        'error': 'PDAL conversion to COPC failed'
                    }
            else:
                # Direct LAS/LAZ output
                output_file = output_dir / f"{item['html_name']}.{output_format}"
                
                apply_annotation_on_file(
                    str(las_file),
                    str(annotation_file),
                    str(output_file),
                    num_workers=num_workers,
                    chunksize=chunksize
                )
                
                return {
                    'status': 'success',
                    'file': las_file.name,
                    'output': str(output_file),
                    'mode': 'normal'
                }
                
    except Exception as e:
        # If an error occurs and we've renamed the annotation, try to restore it
        if 'backup_annotation' in locals() and backup_annotation.exists():
            if not annotation_file.exists():
                backup_annotation.rename(annotation_file)
        
        return {
            'status': 'error',
            'file': las_file.name if 'las_file' in locals() else 'unknown',
            'error': str(e),
            'traceback': traceback.format_exc()
        }


# ==================== STREAMLIT UI ====================

st.set_page_config(page_title="Annotation Applicator", layout="wide")

st.title("🎯 Annotation Tool")

with st.expander("ℹ️ How to use this page", expanded=False):
    st.markdown("""
    **What this page does**
    Reads annotations drawn in a Potree 3D viewer and writes the class labels back onto
    the original LAS / LAZ / COPC point-cloud files, so every point carries its annotated
    classification.

    **Requirements**
    - A **Potree viewer URL** (e.g. `http://ninja:1234/crescer/my_dataset`) or the path to
      a specific HTML file inside that dataset folder
    - Annotations **already drawn and saved** inside the Potree viewer before running this
    - **PDAL** installed in PATH — only needed when output format is COPC
    - The input LAS / LAZ files on disk (the tool auto-locates them from the URL / path)

    **Processing modes**
    | Mode | What it does |
    |---|---|
    | **Normal** | Processes files and writes output to a separate folder; originals untouched |
    | **Update** | Backs up existing classifications then overwrites COPC tiles in-place |

    **Quick start**
    1. Select a processing mode (Normal or Update)
    2. Paste the Potree dataset URL or HTML file path
    3. Review the detected input/annotation folders
    4. Choose an output format (LAS / LAZ / COPC)
    5. Click **Start Processing**
    """)

st.info("Potree base path: " + str(POTREE_BASE_PATH) + " and URL base: " + URL_BASE)

# ==================== MODE SELECTION ====================

st.header("0. Processing Mode")

processing_mode = st.radio(
    "Select Mode",
    options=['New Files', 'Update Existing'],
    index=0,
    help="**New Files**: Process files from input folder to output folder\n\n**Update Existing**: Update COPC files in-place and backup annotations"
)

update_mode = (processing_mode == 'Update Existing')

# ==================== INPUT SECTION ====================

st.header("1. Input Configuration")

col1, col2 = st.columns([2, 1])

with col1:
    potree_url = st.text_input(
        "Potree URL (folder or HTML file)",
        placeholder=f"{URL_BASE}/teledyne/1_merged.html",
        help="Enter the URL to a Potree folder or specific HTML file"
    )

with col2:
    if not update_mode:
        recursive_search = st.checkbox(
            "Search subfolders",
            value=True,
            help="Search for HTML files in subfolders (excludes libs, pointclouds, annotations)"
        )
    else:
        recursive_search = st.checkbox(
            "Search subfolders",
            value=False,
            help="In update mode, typically process single HTML file"
        )

# Parse URL and find files
if potree_url:
    try:
        filesystem_path = url_to_filesystem_path(potree_url)
        
        st.success(f"✅ Resolved path: `{filesystem_path}`")
        
        # Check if it's a file or folder
        if filesystem_path.suffix == '.html':
            html_files = [filesystem_path]
            folder_path = filesystem_path.parent
            st.info(f"📄 Processing single file: **{filesystem_path.name}**")
        else:
            folder_path = filesystem_path
            html_files = find_all_html_files(folder_path, recursive=recursive_search)
            st.info(f"📁 Found **{len(html_files)}** HTML files")
        
        # Display dataset information
        dataset_info = find_dataset_description(folder_path)
        
        if dataset_info and not update_mode:
            st.subheader("📋 Dataset Information")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Created", dataset_info.get("created_at", "N/A"))
            with col2:
                st.metric("Last Updated", dataset_info.get("last_updated", "N/A"))
            with col3:
                expiry = dataset_info.get("expiry_date") or "None"
                st.metric("Expiry", expiry)
            
            st.text_area(
                "Description",
                value=dataset_info.get("description", ""),
                height=100,
                disabled=True
            )
            
            suggested_input = dataset_info.get("input_folder")
            if suggested_input:
                st.info(f"💡 Suggested input folder: `{suggested_input}`")
        else:
            suggested_input = None
        
        # ==================== FOLDER SELECTION ====================
        
        if not update_mode:
            st.header("2. Folder Configuration")
            
            col1, col2 = st.columns(2)
            
            with col1:
                default_input = suggested_input if suggested_input else ""
                input_folder = st.text_input(
                    "Input Folder (LAS/LAZ files)",
                    value=default_input,
                    help="Folder containing the LAS/LAZ files to process"
                )
            
            with col2:
                default_output = str(folder_path / "annotated_output")
                output_folder = st.text_input(
                    "Output Folder",
                    value=default_output,
                    help="Folder where processed files will be saved"
                )
            
            input_path = Path(input_folder) if input_folder else None
            output_path = Path(output_folder) if output_folder else None
        else:
            st.header("2. Update Mode Configuration")
            st.info("📝 **Update Mode**: Files will be updated in-place from the `files/` folder. Annotations will be backed up with timestamps.")
            input_path = None
            output_path = folder_path  # Not used in update mode
        
        # ==================== PROCESSING OPTIONS ====================
        
        st.header("3. Processing Options")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if not update_mode:
                output_format = st.radio(
                    "Output Format",
                    options=['laz', 'las', 'copc.laz'],
                    index=0,
                    help="File format for output files (LAZ is compressed, COPC.LAZ is cloud-optimized)"
                )
            else:
                output_format = 'copc.laz'  # Fixed for update mode
                st.info("Output format: **COPC.LAZ** (fixed for update mode)")
        
        with col2:
            num_workers = st.slider(
                "Number of Workers",
                min_value=1,
                max_value=32,
                value=16,
                help="Number of parallel processes"
            )
        
        with col3:
            chunksize = st.number_input(
                "Chunk Size",
                min_value=10000,
                max_value=1000000,
                value=100000,
                step=10000,
                help="Number of points processed per chunk"
            )
        
        # ==================== VALIDATION ====================
        
        valid_items, validation_errors, validation_warnings = validate_paths(
            html_files, 
            input_path,
            update_mode
        )
        
        if validation_errors:
            st.error("❌ **Validation Errors:**")
            for error in validation_errors:
                st.error(f"- {error}")
        
        if validation_warnings:
            with st.expander("⚠️ Warnings", expanded=False):
                for warning in validation_warnings:
                    st.warning(warning)
        
        if valid_items:
            st.success(f"✅ **Validation Successful:** {len(valid_items)} complete file sets found")
            
            # Show file matching details
            with st.expander("📊 File Matching Details", expanded=True):
                match_df = pd.DataFrame([
                    {
                        'HTML File': item['html_name'] + '.html',
                        'Annotation': item['annotation'].name,
                        'LAS/LAZ File': item['las_file'].name,
                        'Mode': 'Update' if item.get('update_mode') else 'New'
                    }
                    for item in valid_items
                ])
                st.dataframe(match_df, use_container_width=True)
                
                # Show individual file details
                if st.checkbox("Show detailed file paths"):
                    for idx, item in enumerate(valid_items):
                        st.markdown(f"**{idx+1}. {item['html_name']}**")
                        st.markdown(f"- HTML: `{item['html']}`")
                        st.markdown(f"- Annotation: `{item['annotation']}`")
                        st.markdown(f"- LAS/LAZ: `{item['las_file']}`")
                        st.markdown("---")
            
            # ==================== PROCESSING ====================
            
            st.header("4. Processing")
            
            # Create output directory (only for normal mode)
            if not update_mode and output_path:
                output_path.mkdir(parents=True, exist_ok=True)
            
            if st.button("🚀 Start Processing", type="primary", disabled=st.session_state.processing_status['running']):
                st.session_state.processing_status = {
                    'running': True,
                    'processed': 0,
                    'total': len(valid_items),
                    'errors': [],
                    'success': [],
                    'current_file': None
                }
                
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                # Process each file set
                for item in valid_items:
                    st.session_state.processing_status['current_file'] = item['html_name']
                    status_text.text(f"Processing {item['html_name']}...")
                    
                    result = process_single_file(
                        item,
                        output_path if not update_mode else item['las_file'].parent,
                        output_format,
                        num_workers,
                        chunksize
                    )
                    
                    if result['status'] == 'success':
                        st.session_state.processing_status['success'].append(result)
                    else:
                        st.session_state.processing_status['errors'].append(result)
                    
                    st.session_state.processing_status['processed'] += 1
                    progress = st.session_state.processing_status['processed'] / st.session_state.processing_status['total']
                    progress_bar.progress(progress)
                
                st.session_state.processing_status['running'] = False
                status_text.text("Processing complete!")
                
                # Show results
                st.success(f"✅ Successfully processed {len(st.session_state.processing_status['success'])} files")
                
                # Show success details
                if st.session_state.processing_status['success']:
                    with st.expander("📋 Successfully Processed Files", expanded=True):
                        success_df = pd.DataFrame([
                            {
                                'File': s['file'],
                                'Output': Path(s['output']).name,
                                'Mode': s.get('mode', 'unknown'),
                                'Backup': Path(s['backup_annotation']).name if 'backup_annotation' in s else 'N/A'
                            }
                            for s in st.session_state.processing_status['success']
                        ])
                        st.dataframe(success_df, use_container_width=True)
                
                # Show errors
                if st.session_state.processing_status['errors']:
                    st.error(f"❌ {len(st.session_state.processing_status['errors'])} files failed")
                    
                    with st.expander("📋 View Error Details", expanded=False):
                        error_summary = pd.DataFrame([
                            {
                                'File': error['file'],
                                'Error': error['error'][:100] + '...' if len(error['error']) > 100 else error['error']
                            }
                            for error in st.session_state.processing_status['errors']
                        ])
                        
                        st.dataframe(error_summary, use_container_width=True)
                        
                        st.markdown("### Detailed Tracebacks")
                        
                        for idx, error in enumerate(st.session_state.processing_status['errors']):
                            st.markdown(f"**{idx + 1}. {error['file']}**")
                            st.error(error['error'])
                            
                            if 'traceback' in error:
                                with st.container():
                                    st.markdown(f"<details><summary>Click to view full traceback</summary>", 
                                               unsafe_allow_html=True)
                                    st.code(error.get('traceback', 'No traceback available'), language='python')
                                    st.markdown("</details>", unsafe_allow_html=True)
                            
                            if idx < len(st.session_state.processing_status['errors']) - 1:
                                st.markdown("---")
            
            # Show processing status
            if st.session_state.processing_status['running']:
                st.info(f"⏳ Processing: {st.session_state.processing_status['current_file']}")
                
    except Exception as e:
        st.error(f"Error processing URL: {e}")
        st.code(traceback.format_exc(), language='python')