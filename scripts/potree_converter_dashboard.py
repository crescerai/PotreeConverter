import os
import subprocess
import time
import json
from pathlib import Path
import threading
import datetime

import streamlit as st
from lidar_format_corrector import clean_las

POTREE_CONVERTER_PATH = "/app/build/PotreeConverter"
BASE_OUTPUT_FOLDER = Path("/app/potree/crescer")

BASE_OUTPUT_FOLDER.mkdir(exist_ok=True)

def convert_file(in_file: Path, output_dir: Path, remove_int64: bool = False):
    """Convert a single LAS/LAZ file to Potree format with robust potree.js handling"""
    potree_command = [
        POTREE_CONVERTER_PATH,
        str(in_file),
        "-o",
        str(output_dir),
        "-p",
        in_file.stem,
    ]

    if remove_int64:
        try:
            clean_las(in_file)
        except Exception as e:
            display_error(f"Error cleaning file {in_file}: {e}")
            return

    # Create output_dir if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy the potree.js if exists in output directory 
    potree_js_path = output_dir / "libs/potree/potree.js"
    temp_potree_js = output_dir / "potree.js.backup"
    potree_js_content = None
    
    # First save the potree.js content if it exists
    if potree_js_path.exists():
        try:
            with open(potree_js_path, 'r') as f:
                potree_js_content = f.read()
            with open(temp_potree_js, 'w') as f:
                f.write(potree_js_content)
        except Exception as e:
            st.error(f"Error preserving potree.js: {e}")

    try:
        with st.status(f"Converting: {in_file.name}", expanded=True) as status:
            process = subprocess.Popen(
                potree_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            
            for line in process.stdout:
                st.text(line.strip())

            process.wait()

            if process.returncode == 0:
                status.update(label="Conversion Successful!", state="complete", expanded=False)
            else:
                status.update(label="Conversion Failed!", state="error", expanded=True)
    finally:
        # Always try to restore potree.js, even if conversion was interrupted
        if potree_js_content and temp_potree_js.exists():
            try:
                # Ensure the directory exists after conversion
                potree_js_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(potree_js_path, 'w') as f:
                    f.write(potree_js_content)
                temp_potree_js.unlink()  # Remove the backup file
            except Exception as e:
                st.error(f"Error restoring potree.js: {e}")


def convert_directory(in_dir: Path, output_dir: Path, remove_int64: bool = False, 
                     progress_bar=None, file_index=None, total_files=None):
    """Convert a directory of LAS/LAZ files to Potree format"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    files_in_dir = [entry for entry in in_dir.iterdir() if entry.is_file() and entry.suffix in [".las", ".laz"]]
    dirs_in_dir = [entry for entry in in_dir.iterdir() if entry.is_dir()]

    for entry in files_in_dir:
        convert_file(entry, output_dir, remove_int64)
        if progress_bar is not None and file_index is not None and total_files is not None:
            file_index[0] += 1
            progress_bar.progress(file_index[0] / total_files)

    for entry in dirs_in_dir:
        convert_directory(entry, output_dir / entry.name, remove_int64, progress_bar, file_index, total_files)


def get_output_folder(input_folder: Path) -> Path:
    """Determine the default output folder based on input type."""
    if input_folder.is_dir():
        default_output_folder = BASE_OUTPUT_FOLDER / input_folder.name
    else:
        default_output_folder = BASE_OUTPUT_FOLDER
    return default_output_folder


def display_error(message: str):
    """Display an error message in red."""
    st.markdown(f"<span style='color:red'>{message}</span>", unsafe_allow_html=True)


def save_dataset_description(output_folder: Path, description: str, expiry_date=None):
    """Save dataset description with expiry date to a JSON file in the output folder
    
    Args:
        output_folder (Path): Path to output folder
        description (str): User-provided description text
        expiry_date (str, optional): Date when dataset can be deleted
        
    Returns:
        Path: Path to the created description file
    """
    # Create output folder if it doesn't exist
    if not output_folder.exists():
        output_folder.mkdir(parents=True, exist_ok=True)
    
    # Path to description file
    description_file = output_folder / "dataset_description.json"
    
    # Current timestamp
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
    
    # Create data structure
    data = {
        "description": description.strip(),
        "created_at": current_time,
        "last_updated": current_time,
        "expiry_date": expiry_date if expiry_date else None
    }
    
    # If the file already exists, preserve some fields
    if description_file.exists():
        try:
            with open(description_file, 'r') as f:
                existing_data = json.load(f)
                # Keep original creation timestamp
                if "created_at" in existing_data:
                    data["created_at"] = existing_data["created_at"]
        except (json.JSONDecodeError, IOError):
            pass  # Use defaults if file can't be read
    
    # Write the file
    with open(description_file, 'w') as f:
        json.dump(data, f, indent=4)
    
    return description_file


def load_dataset_description(output_folder: Path):
    """Load existing dataset description if available
    
    Args:
        output_folder (Path): Path to output folder
        
    Returns:
        dict: Description data or None if not found
    """
    description_file = output_folder / "dataset_description.json"
    
    if description_file.exists():
        try:
            with open(description_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    
    return None


def background_convert(input_folder: Path, output_folder: Path, need_cleaning: bool, description: str, expiry_date_str=None):
    """Run conversion in background thread, with ability to disconnect from UI"""
    # Save description first
    if description.strip():
        save_dataset_description(output_folder, description, expiry_date_str)
    
    # Create directory if it doesn't exist
    output_folder.mkdir(parents=True, exist_ok=True)
    
    # Log file for background process
    log_file = output_folder / "conversion.log"
    
    with open(log_file, 'w') as f:
        f.write(f"Starting conversion at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Input: {input_folder}\n")
        f.write(f"Output: {output_folder}\n")
        f.write(f"Cleaning needed: {need_cleaning}\n\n")
    
    def process_files():
        start_time = time.time()
        
        try:
            if input_folder.is_dir():
                files_to_convert = list(input_folder.rglob("*.las")) + list(input_folder.rglob("*.laz"))
                total_files = len(files_to_convert)
                
                with open(log_file, 'a') as f:
                    f.write(f"Found {total_files} files to convert\n")
                
                for i, file_path in enumerate(files_to_convert):
                    file_start_time = time.time()
                    output_subdir = output_folder / file_path.relative_to(input_folder).parent
                    output_subdir.mkdir(parents=True, exist_ok=True)
                    
                    with open(log_file, 'a') as f:
                        f.write(f"[{i+1}/{total_files}] Converting {file_path.relative_to(input_folder)}\n")
                    
                    try:
                        # Preserve potree.js if it exists
                        potree_js_path = output_subdir / "libs/potree/potree.js"
                        temp_potree_js = output_subdir / "potree.js"
                        potree_js_content = None
                        
                        if potree_js_path.exists():
                            with open(log_file, 'a') as f:
                                f.write(f"    Preserving existing potree.js\n")
                            try:
                                with open(potree_js_path, 'r') as f_js:
                                    potree_js_content = f_js.read()
                                with open(temp_potree_js, 'w') as f_js:
                                    f_js.write(potree_js_content)
                            except Exception as e:
                                with open(log_file, 'a') as f:
                                    f.write(f"    Error preserving potree.js: {str(e)}\n")
                        
                        # Clean LAS file if needed
                        if need_cleaning:
                            clean_las(file_path)
                        
                        # Run the conversion process
                        potree_command = [
                            POTREE_CONVERTER_PATH,
                            str(file_path),
                            "-o",
                            str(output_subdir),
                            "-p",
                            file_path.stem,
                        ]
                        
                        if need_cleaning:
                            clean_las(file_path)
                        
                        process = subprocess.run(
                            potree_command, 
                            stdout=subprocess.PIPE, 
                            stderr=subprocess.STDOUT,
                            text=True
                        )
                        
                        # Restore potree.js if needed
                        if potree_js_content and temp_potree_js.exists():
                            with open(log_file, 'a') as f:
                                f.write(f"    Restoring potree.js\n")
                            try:
                                with open(potree_js_path, 'w') as f_js:
                                    f_js.write(potree_js_content)
                                temp_potree_js.unlink()
                            except Exception as e:
                                with open(log_file, 'a') as f:
                                    f.write(f"    Error restoring potree.js: {str(e)}\n")
                        
                        file_end_time = time.time()
                        duration = file_end_time - file_start_time
                        
                        with open(log_file, 'a') as f:
                            f.write(f"    Completed in {duration:.2f}s with status {process.returncode}\n")
                            if process.stdout:
                                f.write(f"    Output:\n{process.stdout}\n")
                    
                    except Exception as e:
                        with open(log_file, 'a') as f:
                            f.write(f"    Error: {str(e)}\n")
            
            else:
                # Single file conversion
                with open(log_file, 'a') as f:
                    f.write(f"Converting single file: {input_folder}\n")
                
                try:
                    # Preserve potree.js if it exists
                    potree_js_path = output_folder / "libs/potree/potree.js"
                    temp_potree_js = output_folder / "potree.js"
                    potree_js_content = None
                    
                    if potree_js_path.exists():
                        with open(log_file, 'a') as f:
                            f.write(f"    Preserving existing potree.js\n")
                        try:
                            with open(potree_js_path, 'r') as f_js:
                                potree_js_content = f_js.read()
                            with open(temp_potree_js, 'w') as f_js:
                                f_js.write(potree_js_content)
                        except Exception as e:
                            with open(log_file, 'a') as f:
                                f.write(f"    Error preserving potree.js: {str(e)}\n")
                    
                    # Clean LAS file if needed
                    if need_cleaning:
                        clean_las(input_folder)
                    
                    # Run the conversion process
                    potree_command = [
                        POTREE_CONVERTER_PATH,
                        str(input_folder),
                        "-o",
                        str(output_folder),
                        "-p",
                        input_folder.stem,
                    ]
                    
                    if need_cleaning:
                        clean_las(input_folder)
                    
                    process = subprocess.run(
                        potree_command, 
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.STDOUT,
                        text=True
                    )
                    
                    # Restore potree.js if needed
                    if potree_js_content and temp_potree_js.exists():
                        with open(log_file, 'a') as f:
                            f.write(f"    Restoring potree.js\n")
                        try:
                            with open(potree_js_path, 'w') as f_js:
                                f_js.write(potree_js_content)
                            temp_potree_js.unlink()
                        except Exception as e:
                            with open(log_file, 'a') as f:
                                f.write(f"    Error restoring potree.js: {str(e)}\n")
                    
                    with open(log_file, 'a') as f:
                        f.write(f"    Completed with status {process.returncode}\n")
                        if process.stdout:
                            f.write(f"    Output:\n{process.stdout}\n")
                
                except Exception as e:
                    with open(log_file, 'a') as f:
                        f.write(f"    Error: {str(e)}\n")
            
            end_time = time.time()
            total_time = end_time - start_time
            
            with open(log_file, 'a') as f:
                f.write(f"\nConversion completed at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Total time: {total_time:.2f} seconds\n")
                
        except Exception as e:
            with open(log_file, 'a') as f:
                f.write(f"\nFatal error during conversion: {str(e)}\n")
    
    # Start the background thread
    thread = threading.Thread(target=process_files)
    thread.daemon = False  # Allow thread to run even if main thread exits
    thread.start()
    
    return log_file


def convert(input_folder: Path, output_folder: Path, need_cleaning: bool = False):
    """Perform conversion based on input type with UI integration."""
    
    if input_folder.is_dir():
        files_to_convert = list(input_folder.rglob("*.las")) + list(input_folder.rglob("*.laz"))
        total_files = len(files_to_convert)

        if total_files > 0:
            progress_bar = st.progress(0)
            file_index = [0]  # Use a list to pass by reference
            
            # Add a placeholder for time estimates
            time_estimate = st.empty()
            start_time = time.time()
            avg_time_per_file = None
            
            # Process files and update time estimate
            for i, file_path in enumerate(files_to_convert):
                file_start_time = time.time()
                convert_file(file_path, output_folder / file_path.relative_to(input_folder).parent, need_cleaning)
                file_end_time = time.time()
                
                # Update progress/app/PotreeConverter/build/PotreeConverter
                file_index[0] = i + 1
                progress = file_index[0] / total_files
                progress_bar.progress(progress)
                
                # Calculate and update time estimate
                if avg_time_per_file is None:
                    # First file completed, make initial estimate
                    avg_time_per_file = file_end_time - file_start_time
                else:
                    # Update running average (giving more weight to recent files)
                    avg_time_per_file = 0.7 * avg_time_per_file + 0.3 * (file_end_time - file_start_time)
                
                remaining_files = total_files - file_index[0]
                est_remaining_time = remaining_files * avg_time_per_file
                elapsed_time = time.time() - start_time
                
                # Format time estimates
                elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed_time))
                remaining_str = time.strftime("%H:%M:%S", time.gmtime(est_remaining_time))
                
                time_estimate.info(f"Progress: {file_index[0]}/{total_files} files | " 
                               f"Elapsed: {elapsed_str} | Estimated remaining: {remaining_str}")
            
            st.success("Conversion Complete!")
        else:
            st.warning("No LAS/LAZ files found in the input directory.")

    else:
        convert_file(input_folder, output_folder, need_cleaning)
        st.success("Conversion Complete!")


def main():
    st.set_page_config(page_title="Potree Converter", initial_sidebar_state="collapsed")
    st.title("Potree Converter")

    # Configuration in sidebar
    with st.sidebar:
        st.header("Configuration")
        need_cleaning = st.checkbox("Need Cleaning", value=False)
        
        if need_cleaning:
            st.warning("⚠️ Warning: Clean LAS option will overwrite the original input files!")
            
        st.divider()
        st.markdown("**About**")
        st.markdown("""
        This tool converts LAS/LAZ files to Potree format for 3D visualization in web browsers.
        
        - You can convert a single file or an entire directory
        - Optionally clean files with INT64 fields (overwrites original)
        - Add a description and expiry date to your dataset
        """)

    # Input fields
    input_path_str = st.text_input("Input Folder/File", help="Enter the path to the input folder or file", value="")
    input_folder = Path(input_path_str) if input_path_str else Path()
    has_input = False

    output_folder_str = st.text_input(
        "Output Folder", value=str(get_output_folder(input_folder))
    )
    output_folder = Path(output_folder_str)
    
    # Check for existing description data
    existing_data = load_dataset_description(output_folder)
    
    # Dataset description and expiry section
    st.subheader("Dataset Information")
    
    # Dataset description with existing value if available
    default_description = existing_data.get("description", "") if existing_data else ""
    dataset_description = st.text_area(
        "Dataset Description", 
        help="Add a description for this dataset. It will be saved in the output folder.",
        value=default_description
    )
    
    # Dataset expiry date with calendar picker
    default_expiry = None
    if existing_data and existing_data.get("expiry_date"):
        try:
            default_expiry = datetime.datetime.strptime(
                existing_data["expiry_date"], "%Y-%m-%d"
            ).date()
        except ValueError:
            default_expiry = None
    
    expiry_date = st.date_input(
        "Dataset Expiry Date",
        value=default_expiry,
        help="Date when this dataset can be deleted. Leave blank for no expiry.",
        min_value=datetime.date.today()
    )
    
    # Format expiry date as string for storage
    expiry_date_str = expiry_date.strftime("%Y-%m-%d") if expiry_date else None
    
    # Show existing timestamps if available
    if existing_data:
        with st.expander("Dataset Timestamps"):
            col1, col2 = st.columns(2)
            with col1:
                st.info(f"**Created:** {existing_data.get('created_at', 'Unknown')}")
            with col2:
                st.info(f"**Last Updated:** {existing_data.get('last_updated', 'Unknown')}")

    # Display URL where files will be accessible
    potree_url = str(output_folder).replace(str(BASE_OUTPUT_FOLDER), "http://wall-e:1234/crescer")
    st.write("Converted files will be accessible here:", potree_url)

    # Validate input
    if input_path_str != "" and not input_folder.exists():
        st.error(f"Input Folder/File does not exist: {input_folder}")
        has_input = False
    else:
        has_input = True

    # Show file list if input is a directory
    if has_input and input_path_str != "" and input_folder.is_dir():
        files_to_convert = list(input_folder.rglob("*.las")) + list(input_folder.rglob("*.laz"))
        total_files = len(files_to_convert)
        if total_files == 0:
            st.warning("No LAS/LAZ files found in the input directory.")
        else:
            st.success(f"Found {total_files} LAS/LAZ files in the directory")
            with st.expander(f"View Files"):
                for file in files_to_convert:
                    st.write(file.relative_to(input_folder))

    # Action buttons
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Start Conversion", type="primary", disabled=not has_input):
            # Save description with expiry date
            save_dataset_description(output_folder, dataset_description, expiry_date_str)
            st.info("Dataset information saved.")
                
            st.info("Starting conversion... Please wait for the process to complete.")
            convert(input_folder, output_folder, need_cleaning)
    
    with col2:
        if st.button("Move to Background", disabled=not has_input):
            # Save description with expiry date
            save_dataset_description(output_folder, dataset_description, expiry_date_str)
            
            log_file = background_convert(input_folder, output_folder, need_cleaning, dataset_description)
            st.success(f"""
            ✅ Conversion started in background!
            
            You can now close this tab and the process will continue running on the server.
            Progress will be logged to: `{log_file}`
            """)
            
            st.info("The results will be available at the URL shown above when complete.")


if __name__ == "__main__":
    main()
