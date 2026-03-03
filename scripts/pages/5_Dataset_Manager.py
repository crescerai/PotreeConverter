import streamlit as st
import json
import os
import shutil
from pathlib import Path
import re
import pandas as pd
import ast
from datetime import datetime

BASE_OUTPUT_FOLDER = Path(os.getenv("POTREE_BASE_PATH", "/app/potree/crescer"))
EXCLUDE_FOLDERS = {"libs", "pointclouds"}

def find_all_datasets(base_folder: Path) -> list[Path]:
    """
    Recursively find all valid dataset folders under `base_folder`.
    
    If a folder contains libs, pointclouds, and files, it will be added as a dataset,
    and the script will continue recursing into any extra subdirectories.
    """
    datasets = []

    has_libs = (base_folder / "libs").is_dir()
    has_pointclouds = (base_folder / "pointclouds").is_dir()
    has_files = (base_folder / "files").is_dir()
    
    is_root = (base_folder == BASE_OUTPUT_FOLDER)

    # Check if this qualifies as a dataset (ignoring the root folder itself)
    if not is_root and has_libs and (has_pointclouds or has_files):
        desc_file = base_folder / "dataset_description.json"

        if not desc_file.exists():
            data = {
                "folder_path": str(base_folder.relative_to(BASE_OUTPUT_FOLDER)),
                "description": "Auto generated dataset description",
                "expiry_date": "",
                "created_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_updated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(desc_file, "w") as f:
                json.dump(data, f, indent=4)

        # "Return the current one" -> Append the valid dataset to our list
        datasets.append(base_folder)

        # If all 3 are present, skip the early return so we can recurse into extra folders
        if has_libs and has_pointclouds and has_files:
            pass # Let the script fall through to the for-loop below
        else:
            return datasets  # For standard datasets, stop recursing here

    # "And also go inside the folder" -> Check for extra directories
    # Because 'libs', 'pointclouds', and 'files' are in EXCLUDE_FOLDERS, 
    # the script will automatically bypass them and only dive into the extra folders.
    for item in base_folder.iterdir():
        if item.is_dir() and item.name not in EXCLUDE_FOLDERS:
            datasets.extend(find_all_datasets(item))

    return datasets

def load_description(dataset_folder):
    desc_file = dataset_folder / "dataset_description.json"
    if desc_file.exists():
        try:
            with open(desc_file) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def set_folder_description(dataset_folder, description, expiry_date):
    """Update dataset description and expiry date with timestamp update"""
    desc_file = dataset_folder / "dataset_description.json"
    data = load_description(dataset_folder)
    
    # Update the fields
    data["description"] = description
    data["expiry_date"] = expiry_date
    
    # Always update the timestamp when called
    data["last_updated"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(desc_file, "w") as f:
        json.dump(data, f, indent=4)

def rename_folder(old_path, new_path):
    if not old_path.exists():
        raise Exception("Old folder path does not exist.")
    if new_path.exists():
        raise Exception("New folder path already exists.")
    shutil.move(str(old_path), str(new_path))



def read_classification_schemes(potree_js_path):
    """
    Parse all classification schemes from potree.js.
    Returns a dict: {scheme_name: {class_id: {name, color, visible}}}
    """
    if not potree_js_path.exists():
        return {}
    js = potree_js_path.read_text()
    match = re.search(r"const ClassificationScheme\s*=\s*({.*?});", js, re.DOTALL)
    if not match:
        return {}
    scheme_block = match.group(1)
    # Replace JS true/false/null with Python equivalents
    scheme_block = (
        scheme_block.replace("true", "True")
        .replace("false", "False")
        .replace("null", "None")
    )
    # Convert JS-style keys to quoted keys
    scheme_block = re.sub(r'(\w+):', r'"\1":', scheme_block)
    # Remove trailing commas
    scheme_block = re.sub(r',\s*}', '}', scheme_block)
    scheme_block = re.sub(r',\s*]', ']', scheme_block)
    try:
        schemes = ast.literal_eval(scheme_block)
        return schemes
    except Exception:
        return {}

def write_classification_schemes(potree_js_path, new_schemes):
    """
    Overwrite the ClassificationScheme object in potree.js with new_schemes.
    """
    if not potree_js_path.exists():
        raise Exception("potree.js not found.")
    backup_path = potree_js_path.parent / ("potree.js.bak")
    shutil.copy2(potree_js_path, backup_path)
    js = potree_js_path.read_text()
    # Serialize new_schemes as JS
    def python_to_js(obj):
        if isinstance(obj, dict):
            return "{" + ", ".join([f"{k if isinstance(k,int) or k.isdigit() else json.dumps(str(k))}: {python_to_js(v)}" for k, v in obj.items()]) + "}"
        if isinstance(obj, list):
            return "[" + ", ".join([python_to_js(x) for x in obj]) + "]"
        if isinstance(obj, bool):
            return "true" if obj else "false"
        if obj is None:
            return "null"
        if isinstance(obj, (int, float)):
            return str(obj)
        return json.dumps(obj)
    schemes_js = python_to_js(new_schemes)
    new_const = f"const ClassificationScheme = {schemes_js};"
    js_new = re.sub(
        r"const ClassificationScheme\s*=\s*{.*?};",
        new_const,
        js,
        flags=re.DOTALL,
    )
    potree_js_path.write_text(js_new)

def color_str_to_array(s):
    # Accepts rgb(x, y, z) or #hex
    if s.startswith("rgb"):
        m = re.match(r"rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", s)
        if not m: return None
        r, g, b = map(int, m.groups())
        return [r/255, g/255, b/255, 1.0]
    if s.startswith("#") and len(s)==7:
        r = int(s[1:3],16)
        g = int(s[3:5],16)
        b = int(s[5:7],16)
        return [r/255, g/255, b/255, 1.0]
    return None

def is_expired(expiry_date):
    """Check if a dataset is expired based on its expiry date"""
    if not expiry_date or str(expiry_date).strip() == "":
        return False
        
    try:
        # Handle both string dates and datetime objects
        if isinstance(expiry_date, str):
            expiry = datetime.strptime(expiry_date, "%Y-%m-%d").date()
        else:
            expiry = expiry_date
        today = datetime.now().date()
        return expiry < today
    except (ValueError, TypeError):
        return False

# --- Streamlit UI ---

st.set_page_config(page_title="Dataset Manager", layout="wide")
st.title("🗃️ Dataset Manager")

with st.expander("ℹ️ How to use this page", expanded=False):
    st.markdown("""
    **What this page does**
    Lists every Potree dataset under the output folder and lets you manage them:
    rename datasets, edit descriptions, set expiry dates, and customise the
    point-cloud classification colour scheme shown in the viewer.

    **Requirements**
    - Access to the Potree base output folder (`POTREE_BASE_PATH` env var,
      default `/app/potree/crescer`)
    - Datasets must have been created by the **COPC Converter** page (page 2) — a valid
      dataset folder contains `libs/`, `pointclouds/`, and `files/` sub-folders

    **Dataset table**
    Each row shows a dataset with editable fields. Change values and click
    **Save Changes** to persist them to `dataset_description.json` inside that folder.

    | Field | Description |
    |---|---|
    | **Name** | Display name shown in viewer index |
    | **Description** | Free-text notes visible in the index page |
    | **Expiry date** | Dataset is flagged as expired after this date (YYYY-MM-DD) |

    **Classification Scheme editor**
    Edit RGB or hex colours for each point-cloud class. Changes are written directly
    into `potree.js` inside the dataset folder — the viewer will reflect them on next
    page load. Use the sidebar **RGB/Hex to Potree Color** converter to format colours
    correctly.
    """)

# 1. Sidebar color converter
st.sidebar.header("RGB/Hex to Potree Color")
rgb_input = st.sidebar.text_input("Enter color (rgb(x, y, z) or #hex):", "")
if rgb_input:
    arr = color_str_to_array(rgb_input.strip())
    if arr:
        st.sidebar.success(f"color: [{arr[0]:.2f}, {arr[1]:.2f}, {arr[2]:.2f}, 1.0]")
    else:
        st.sidebar.error("Invalid format.")

# 2. Find all datasets
all_datasets = find_all_datasets(BASE_OUTPUT_FOLDER)

rows = []
for folder in all_datasets:
    desc = load_description(folder)
    rows.append({
        "folder_path": str(folder.relative_to(BASE_OUTPUT_FOLDER)),
        "description": desc.get("description", ""),
        "expiry_date": desc.get("expiry_date", ""),
        "created_at": desc.get("created_at", ""),
        "last_updated": desc.get("last_updated", "")
    })

df = pd.DataFrame(rows)

if not df.empty:
    st.subheader("Datasets (edit and save to apply changes)")
    edited_df = st.data_editor(
        df,
        num_rows="fixed",
        use_container_width=True,
        key="dataset_editor"
    )

    if st.button("Save/Update Dataset Info"):
        # Store list of rows that were modified
        modified_rows = []
        
        # Identify which rows have changes
        for i in range(len(df)):
            # Convert fields to strings for reliable comparison
            old_path = str(df.loc[i, "folder_path"])
            new_path = str(edited_df.loc[i, "folder_path"])
            old_desc = str(df.loc[i, "description"])
            new_desc = str(edited_df.loc[i, "description"])
            old_expiry = str(df.loc[i, "expiry_date"])
            new_expiry = str(edited_df.loc[i, "expiry_date"])
            
            # If any field changed, mark this row as modified
            if old_path != new_path or old_desc != new_desc or old_expiry != new_expiry:
                modified_rows.append(i)
        
        # Process only the modified rows
        for i in modified_rows:
            old_folder_path = df.loc[i, "folder_path"]
            new_folder_path = edited_df.loc[i, "folder_path"]
            new_description = edited_df.loc[i, "description"]
            new_expiry = edited_df.loc[i, "expiry_date"]
            
            old_folder = BASE_OUTPUT_FOLDER / old_folder_path
            new_folder = BASE_OUTPUT_FOLDER / new_folder_path
            
            # Rename folder if path changed
            if old_folder_path != new_folder_path:
                try:
                    rename_folder(old_folder, new_folder)
                    target_folder = new_folder
                except Exception as e:
                    st.error(f"Error renaming folder: {e}")
                    continue
            else:
                target_folder = old_folder
            
            # Update the dataset
            try:
                set_folder_description(target_folder, new_description, new_expiry)
            except Exception as e:
                st.error(f"Error updating dataset: {e}")
        
        # Report results
        if modified_rows:
            st.success(f"Updated {len(modified_rows)} datasets with new information.")
            # Force a rerun to refresh the display with updated data
            st.rerun()
        else:
            st.info("No changes detected in any dataset.")

else:
    st.warning("No datasets found.")

# 3. Classification editor section

st.markdown("---")
st.subheader("Classification Scheme Editor")

if not df.empty:
    folder_sel = st.selectbox("Select a dataset", df["folder_path"])
    if folder_sel:
        potree_js_path = BASE_OUTPUT_FOLDER / folder_sel / "libs/potree/potree.js"
        schemes = read_classification_schemes(potree_js_path)
        if not schemes:
            st.warning("No classification schemes found in potree.js.")
        else:
            scheme_names = list(schemes.keys())
            scheme_sel = st.selectbox("Select scheme", scheme_names)
            # Convert to editable DataFrame
            if scheme_sel:
                scheme_data = schemes[scheme_sel]
                # To DataFrame
                class_rows = []
                for cid, cval in scheme_data.items():
                    if not isinstance(cval, dict): continue
                    class_rows.append({
                        "id": cid,
                        "name": cval.get("name",""),
                        "color": str(cval.get("color","")),
                        "visible": cval.get("visible",True)
                    })
                class_df = pd.DataFrame(class_rows)
                st.write("Edit classes below. Use color array format (e.g. [0.0, 0.8, 0.0, 1.0])")
                edited_class_df = st.data_editor(class_df, num_rows="dynamic", key="class_editor")
                if st.button("Save Classification Scheme"):
                    # Convert back to dict
                    new_scheme = {}
                    for _, row in edited_class_df.iterrows():
                        # Support int or string keys
                        try:
                            idx = int(row["id"])
                        except Exception:
                            idx = str(row["id"])
                        try:
                            color = ast.literal_eval(row["color"])
                        except Exception:
                            color = [0,0,0,1]
                        new_scheme[idx] = {
                            "visible": bool(row["visible"]),
                            "name": row["name"],
                            "color": color
                        }
                    # Write back
                    schemes[scheme_sel] = new_scheme
                    try:
                        write_classification_schemes(potree_js_path, schemes)
                        st.success("Classification scheme saved! Backup as potree.js.bak created.")
                    except Exception as e:
                        st.error(f"Failed to write: {e}")
else:
    st.info("Add a dataset first to edit classification schemes.")
