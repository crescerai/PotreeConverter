from typing import Optional

import laspy as lp
import numpy as np
import pandas as pd
import polars as pl
import laspy.copc
from pathlib import Path
from typing import Union

# type for path-like objects
PathLike = Union[str, Path]
import logging
logger = logging.getLogger(__name__)

def summary(las):
    logger.info(las)
    logger.info(f'Num points from header: {las.header.point_count}')
    logger.info(f'Num points from data: {len(las.points)}')

    ground_pts = las.classification == 2
    bins, counts = np.unique(las.return_number[ground_pts], return_counts=True)
    logger.info('Ground Point Return Number distribution:')
    for r, c in zip(bins, counts):
        logger.info('    {}:{}'.format(r, c))

    bins, counts = np.unique(las.classification, return_counts=True)
    logger.info('Point classification distribution:')
    for r, c in zip(bins, counts):
        logger.info('    {}:{}'.format(r, c))


def class_dist(las):
    return {
        b: c
        for b, c in zip(*np.unique(las.classification, return_counts=True))
    }


def split_classes(las, path, prefix):
    """"
    Splits up a las file by class and saves them as separate point clouds.
    """
    classes = np.unique(las.classification, return_counts=False)
    for cls in classes:
        new_file = lp.create(point_format=las.header.point_format,
                             file_version=las.header.version)
        new_file.points = las.points[las.classification == cls]
        new_file.write(str(path / f"{prefix}_cls_{cls}.las"))

## it was so if input file is copc save it as laz by modifying heder, working

def create_las(
    df: pd.DataFrame,
    file_loc: PathLike,
    input_las_path: Optional[PathLike] = None,
    add_debug_headers: bool = True,
    change_header_shape: bool = True
):
    """Converts dataframe to las/laz file with all columns preserved.
    Handles conversion from COPC headers to standard LAZ headers if input_las_path is COPC.
    """
    
    # Determine output path and ensure it's .laz
    file_loc = Path(file_loc).with_suffix('.laz')
    
    if input_las_path:
        input_las = lp.read(input_las_path)
        
        # --- START: NEW ATTRIBUTE-BASED COPC DETECTION ---
        
        # Manually check if the input is COPC by looking for VLR attributes
        is_copc = False
        for vlr in input_las.header.vlrs:
            # COPC Info VLR: user_id="copc", record_id=1
            # COPC Hierarchy VLR: user_id="copc", record_id=1000
            if vlr.user_id == "copc" and vlr.record_id in (1, 1000):
                is_copc = True
                break
    
        if is_copc:
            logger.info(f"Input file {input_las_path} is COPC. Creating new standard LAZ header.")
            # Create a new, non-COPC header based on the input's properties
            header = lp.header.LasHeader(
                version='1.4', 
                point_format=input_las.header.point_format.id
            )
            # Copy essential CRS and bounding box info
            header.scales = input_las.header.scales
            header.offsets = input_las.header.offsets
            header.mins = input_las.header.mins
            header.maxs = input_las.header.maxs
            
            # Manually copy VLRs, *excluding* COPC-specific ones
            header.vlrs = [] # Start with an empty list
            for vlr in input_las.header.vlrs:
                # Add VLR to new header ONLY if it's NOT a COPC vlr
                if not (vlr.user_id == "copc" and vlr.record_id in (1, 1000)):
                    header.vlrs.append(vlr)
            
            logger.info(f"Filtered VLRs: Kept {len(header.vlrs)} (e.g., CRS) and removed COPC VLRs.")

        else:
            logger.info(f"Input file {input_las_path} is standard LAS/LAZ. Copying header.")
            # It's a regular LAS/LAZ, so just copy the header
            header = input_las.header.copy()
        
        # --- END: NEW ATTRIBUTE-BASED COPC DETECTION ---

        # Ensure output is compressed LAZ
        header.set_compressed(True)

    else:
        # Original logic for creating a new file from scratch
        logger.info("No input file provided. Creating new LAZ header from scratch.")
        header = lp.header.LasHeader(version='1.4', point_format=8) 
        scale = 1e-3 
        header.scale = [scale, scale, scale]
        header.offset = [
            np.floor(np.min(df['x'])),
            np.floor(np.min(df['y'])),
            np.floor(np.min(df['z']))
        ]
        header.set_compressed(True)


    if change_header_shape:
        header.point_count = len(df)

    # Add extra dimensions for columns not in standard format
    if add_debug_headers:
        standard_dims = set(header.point_format.dimension_names) | {"x", "y", "z"}
        extra_cols = set(df.columns) - standard_dims
        
        for col in extra_cols:
            try:
                if col in header.point_format.extra_dimension_names:
                    logger.info(f"Extra dimension {col} already exists in header.")
                    continue
                    
                if col in ['truth', 'pred', 'class_corrected', 'class_uncorrected', 'point_source_id', 'user_data'] or "class" in col:
                    if col == 'point_source_id':
                        header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.uint16))
                    elif col == 'user_data':
                        header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.uint16))
                    else:
                        header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.uint8))
                elif col == "preds" or "dist" in col or df[col].dtype in [np.float32, np.float64]:
                    header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.float32))
                elif df[col].dtype in [np.int32, np.int64]:
                    header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.int32))
                else:
                    header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.float32))
                    
                logger.info(f"Added extra dimension: {col}")
            except Exception as e:
                logger.warning(f"Could not add extra dimension {col}: {e}")

    print(f"Creating file: {file_loc}")
    outfile = lp.LasData(header)

    # Get all available columns for assignment
    available_columns_for_las = set(
        list(outfile.point_format.dimension_names) + 
        list(outfile.header.point_format.extra_dimension_names) + 
        ["x", "y", "z"]
    )
    
    # Assign data to las file
    for col in df.columns:
        if col in available_columns_for_las:
            try:
                # Handle data type conversion based on the field
                if col == 'point_source_id':
                    outfile[col] = df[col].values.astype(np.uint16)
                elif col == 'user_data':
                    outfile[col] = df[col].values.astype(np.uint16)
                elif col in ['classification', 'return_number', 'number_of_returns', 'scan_direction_flag', 'edge_of_flight_line']:
                    outfile[col] = df[col].values.astype(np.uint8)
                elif col == 'scan_angle_rank':
                    outfile[col] = df[col].values.astype(np.int8)
                elif col in ['intensity', 'red', 'green', 'blue']:
                    outfile[col] = df[col].values.astype(np.uint16)
                elif col in ['x', 'y', 'z', 'gps_time']:
                    outfile[col] = df[col].values.astype(np.float64)
                else:
                    outfile[col] = df[col].values
                    
                logger.debug(f"Assigned column: {col}")
            except Exception as e:
                logger.warning(f"Could not assign column {col}: {e}")
                continue
        else:
            logger.warning(f"Column {col} not available in LAS format")

    # Write the file
    outfile.write(file_loc)
    print(f"Successfully saved {len(df)} points to {file_loc}")


# def create_las(
#     df: pd.DataFrame,
#     file_loc: PathLike,
#     input_las_path: Optional[PathLike] = None,
#     add_debug_headers: bool = True,
#     change_header_shape: bool = True
# ):
#     """Converts dataframe to las/laz file with all columns preserved."""
    
#     # Create a new laspy file
#     header = lp.header.LasHeader(version='1.4', point_format=8)
    
#     if input_las_path:
#         input_las = lp.read(input_las_path)
#         header = input_las.header.copy()  # Use copy to avoid modifying original
#         # Force LAZ compression
#         file_loc = Path(file_loc).with_suffix('.laz')
#         header.set_compressed(True)
#     else:
#         scale = 1e6
#         header.scale = [1. / scale, 1. / scale, 1. / scale]
#         header.offset = [
#             np.floor(np.min(df['x'])),
#             np.floor(np.min(df['y'])),
#             np.floor(np.min(df['z']))
#         ]
#         # header.offsets = [0, 0, 0]  # Set offsets to zero for simplicity
#         file_loc = Path(file_loc).with_suffix('.laz')
#         header.set_compressed(True)


#     if change_header_shape:
#         header.point_count = len(df)

#     # Add extra dimensions for columns not in standard format
#     if add_debug_headers:
#         standard_dims = set(header.point_format.dimension_names) | {"x", "y", "z"}
#         extra_cols = set(df.columns) - standard_dims
        
#         for col in extra_cols:
#             try:
#                 # Determine appropriate data type based on column values
#                 if col in ['truth', 'pred', 'class_corrected', 'class_uncorrected', 'point_source_id', 'user_data'] or "class" in col:
#                     if col == 'point_source_id':
#                         # point_source_id should be uint16
#                         header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.uint16))
#                     elif col == 'user_data':
#                         # Changed from uint8 to uint16
#                         header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.uint16))
#                     else:
#                         header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.uint8))
#                 elif col == "preds" or "dist" in col or df[col].dtype in [np.float32, np.float64]:
#                     header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.float32))
#                 elif df[col].dtype in [np.int32, np.int64]:
#                     header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.int32))
#                 else:
#                     # Default to float32 for unknown types
#                     header.add_extra_dim(lp.ExtraBytesParams(name=col, type=np.float32))
                    
#                 logger.info(f"Added extra dimension: {col}")
#             except Exception as e:
#                 logger.warning(f"Could not add extra dimension {col}: {e}")

#     print(f"Creating file: {file_loc}")
#     outfile = lp.LasData(header)

#     # Get all available columns for assignment
#     available_columns_for_las = set(
#         list(outfile.point_format.dimension_names) + 
#         list(outfile.header.point_format.extra_dimension_names) + 
#         ["x", "y", "z"]
#     )
    
#     # Assign data to las file
#     for col in df.columns:
#         if col in available_columns_for_las:
#             try:
#                 # Handle data type conversion based on the field
#                 if col == 'point_source_id':
#                     outfile[col] = df[col].values.astype(np.uint16)
#                 elif col == 'user_data':
#                     outfile[col] = df[col].values.astype(np.uint16)
#                 elif col in ['classification', 'return_number', 'number_of_returns', 'scan_direction_flag', 'edge_of_flight_line']:
#                     outfile[col] = df[col].values.astype(np.uint8)
#                 elif col == 'scan_angle_rank':
#                     outfile[col] = df[col].values.astype(np.int8)
#                 elif col in ['intensity', 'red', 'green', 'blue']:
#                     outfile[col] = df[col].values.astype(np.uint16)
#                 elif col in ['x', 'y', 'z', 'gps_time']:
#                     outfile[col] = df[col].values.astype(np.float64)
#                 else:
#                     # For extra dimensions, use the values as-is
#                     outfile[col] = df[col].values
                    
#                 logger.debug(f"Assigned column: {col}")
#             except Exception as e:
#                 logger.warning(f"Could not assign column {col}: {e}")
#                 continue
#         else:
#             logger.warning(f"Column {col} not available in LAS format")

#     # Write the file
#     outfile.write(file_loc)
#     print(f"Successfully saved {len(df)} points to {file_loc}")


def get_las_columns(las, col_type):
    columns = ["x", "y", "z"]
    if col_type == "features":
        columns = columns + [
            "intensity",
            "return_number",
            "number_of_returns",
            "classification",
            "withheld",
        ]
    elif col_type == "all":
        columns = columns + list(las.point_format.dimension_names)
    else:
        raise Exception(f"col_type can only be features or all but {col_type} provided")
    return columns

# def load_las(filename, sort=False, col_type="all"):
#     """Loads a las file into pandas dataframe, removes NaNs and optionally sorts the df. Defaults to False.

#     Note: Following features are included in the dataframe - x, y, z, intensity, return num., num. of returns, class

#     Args:
#         filename (str): Laspy filename
#         sorted (bool, optional): If True, sort the dataframe by all features except classification.
#         col_type (str, optional): There can be two col types. "features" is used to get all columns that are used by model and "all" returns a dataframe containing all columns used for making prediction.

#     Returns:
#         dataframe: Pandas dataframe of points, possibly sorted.
#     """
#     las = lp.read(filename)
#     columns = get_las_columns(las, col_type=col_type)
    
#     df = pd.DataFrame()
#     for col in columns:
#         df[col] = np.array(las.__getattr__(col))
#     # df.dropna(inplace=True)
#     if sort:
#         # TODO Sort and save?
#         df = df.sort_values(by=[
#             'x', 'y', 'z', 'intensity', 'return_number', 'number_of_returns'
#         ])
#         df = df.reset_index(drop=True)
#     return df

def load_las(filename, sort=False, col_type="all"):
    """Loads a LAS file into pandas dataframe, removes NaNs and optionally sorts the df.

    Note:
        - Features included: x, y, z, intensity, return num., num. of returns, class.
        - ExtraBytes arrays (multi-dimensional) are expanded into separate columns.

    Args:
        filename (str): Path to LAS/LAZ file.
        sort (bool, optional): If True, sort the dataframe by x, y, z, intensity, return_number, number_of_returns.
        col_type (str, optional): "features" for model features, "all" for all columns.

    Returns:
        pd.DataFrame: Pandas dataframe of points, possibly sorted.
    """
    las = lp.read(filename)
    columns = get_las_columns(las, col_type=col_type)

    data = {}

    for col in columns:
        arr = getattr(las, col)

        # If ExtraBytes or another multi-dim field -> expand into multiple columns
        if isinstance(arr, np.ndarray) and arr.ndim == 2:
            # for i in range(arr.shape[1]):
            #     data[f"{col}_{i}"] = arr[:, i]
            pass
        else:
            data[col] = np.array(arr)

    df = pd.DataFrame(data)

    if sort:
        sort_cols = [c for c in ["x", "y", "z", "intensity", "return_number", "number_of_returns"] if c in df.columns]
        df = df.sort_values(by=sort_cols).reset_index(drop=True)

    return df




def load_las_polars(filename: str, col_type="features") -> pl.DataFrame:
    las = lp.read(filename)
    columns = get_las_columns(las, col_type)
    data = {}
    for col in columns:
        data[col] = np.array(las.__getattr__(col))
    df = pl.LazyFrame(data)
    # return df.drop_nulls()
    return df





####################


import json
import numpy as np
# from iris.las_tools import load_las, create_las
import pandas as pd
from multiprocessing import Pool, cpu_count
from tqdm import tqdm
from pathlib import Path
import typer


def main(
    input_dir: Path = typer.Option(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Input directory containing LAS/LAZ files",
    ),
    output_dir: Path = typer.Option(
        ...,
        file_okay=False,
        dir_okay=True,
        writable=True,
        help="Output directory for processed files",
    ),
    annotation_dir: Path = typer.Option(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Path to JSON annotation file",
    ),
    num_workers: int = typer.Option(16, help="Number of worker processes"),
    chunksize: int = typer.Option(100_000, help="Number of rows per chunk"),
):
    las_files = list(input_dir.glob("*.las")) + list(input_dir.glob("*.laz"))
    print(f"Found {len(las_files)} LAS/LAZ files in {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    for las_file in las_files:
        output_file = output_dir / las_file.name
        annotation_loc = annotation_dir / f"{las_file.stem}.json"
        if not annotation_loc.exists():
            print(f"Warning: Annotation file {annotation_loc} does not exist. Skipping.")
            continue
        print(f"Processing {las_file} -> {output_file}")
        apply_annotation_on_file(
            str(las_file),
            str(annotation_loc),
            str(output_file),
            num_workers=num_workers,
            chunksize=chunksize,
        )


def apply_annotation_on_file(
    file_loc, annotation_loc, output_loc, num_workers=16, chunksize=100000
):
    # load LAS file to DataFrame
    df = load_las(file_loc)

    # load annotations
    with open(annotation_loc, "r") as f:
        annotation = json.load(f)

    # split df into chunks
    sub_dfs = [df.iloc[i : i + chunksize].copy() for i in range(0, len(df), chunksize)]

    if num_workers is None:
        num_workers = cpu_count()

    # process in parallel with progress bar
    with Pool(processes=num_workers) as pool:
        results = list(
            tqdm(
                pool.imap(_process_chunk, [(sub_df, annotation) for sub_df in sub_dfs]),
                total=len(sub_dfs),
                desc="Processing Chunks",
            )
        )

    # concatenate results back
    processed_df = pd.concat(results, ignore_index=True)

    # optionally save
    create_las(processed_df, output_loc, file_loc)


def _process_chunk(args):
    """Helper wrapper so Pool.map works with tqdm"""
    sub_df, artifact = args
    return apply_artifact(sub_df, artifact)


def apply_artifact(df, saved_artifact):
    classification_idx = generate_classification_idx(saved_artifact["classification"])
    annotations = saved_artifact["labels"]
    if annotations is None:
        return df
    manuallyLabeledClassification = manuallyLabel(df, annotations, classification_idx)
    df["classification"] = get_class_code(manuallyLabeledClassification).astype(int)
    return df


def generate_classification_idx(classification_map):
    keys = [k for k in classification_map.keys() if k != "DEFAULT"]
    sorted_keys = sorted(map(int, keys))
    # prepend -1 for DEFAULT
    classification_idx = [-1] + sorted_keys
    return classification_idx


def manuallyLabel(df, annotation, classification_idx=None):
    """
    DataFrame-based helper that uses your column names:
      x,y,z, point_source_id, classification
    Returns a NumPy array of outLabel (length N).
    """
    points = df[["x", "y", "z"]].to_numpy(dtype=float)
    point_source_id = df["point_source_id"].to_numpy()
    classification = df["classification"].to_numpy(dtype=float)
    return manually_label(
        points, classification, point_source_id, annotation, classification_idx
    )


def get_class_code(cls_idx):
    # GLSL: if(cls_idx > 49.0) return cls_idx - 50.0; else return cls_idx;
    return np.where(cls_idx > 49.0, cls_idx - 50.0, cls_idx)


def manually_label(
    points, classification, point_source_id, annotation, classification_idx=None
):
    """
    Python equivalent of GLSL manuallyLabel(classification):
      out = label(out, -1);
      for each zoom-in box i: out = label(out, i);

    Args:
        points: (N,3) float32/64
        classification: (N,) float labels (may already contain 50+ encoded)
        point_source_id: (N,) float/int
        annotation: dict as provided
        classification_idx: optional list/array that maps bit position -> class code
            (GLSL uniform classificationIdx). If None, identity mapping is used.

    Returns:
        (N,) float array 'outLabel'
    """
    class_index_map = _build_class_index_map(classification_idx)

    out = classification.astype(float).copy()
    # Global pass (bboxId = -1)
    out = _label_pass(points, out, point_source_id, annotation, -1, class_index_map)

    # Then each zoom-in box
    ziboxes = annotation.get("zoomInBoxes", [])
    for i in range(len(ziboxes)):
        out = _label_pass(points, out, point_source_id, annotation, i, class_index_map)
    return out


def _build_class_index_map(classification_idx):
    """
    classification_idx: list/array like GLSL 'classificationIdx'
      Index in the list is the bit-position; value is the class code.
      In GLSL they search i such that classificationIdx[i] == cls_code, i is bit.
    If None, we fall back to identity mapping: bit i -> class i.
    """
    if classification_idx is None:
        # identity up to, say, 256 classes
        return {float(i): i for i in range(256)}
    mapping = {}
    for i, code in enumerate(classification_idx):
        mapping[float(code)] = int(i)
    return mapping


def _label_pass(points, cls_in, point_source_id, annotation, bbox_id, class_index_map):
    """
    Equivalent of GLSL label(cls_idx, bboxId):
      - gate by zoomInBoxes[bboxId] if bboxId>=0
      - then segmentLabel, volumeLabel, polygonLabel (in that order)
    """
    # Gate
    if bbox_id >= 0:
        ziboxes = annotation.get("zoomInBoxes", [])
        if bbox_id >= len(ziboxes):
            gate_mask = np.zeros(points.shape[0], dtype=bool)
        else:
            gate_mask = _points_in_box_mask(points, ziboxes[bbox_id]["volume"])
    else:
        gate_mask = np.ones(points.shape[0], dtype=bool)

    out = _segment_label(
        cls_in, point_source_id, annotation.get("segmentMap", []), bbox_id, gate_mask
    )
    out = _volume_label(
        points,
        out,
        annotation.get("labelingVolumes", []),
        bbox_id,
        gate_mask,
        class_index_map,
    )
    out = _polygon_label(
        points,
        out,
        annotation.get("labelingPolygons", []),
        bbox_id,
        gate_mask,
        class_index_map,
    )
    return out


def _points_in_box_mask(points, volume):
    """
    Equivalent of GLSL: isPointInsideBox(clipBox) where clipBox ~= inverse(box_model_matrix).
    Here, we compute inverse(model) and check unit cube [-0.5,0.5]^3 in local space.
    """
    pos = np.asarray(volume["position"], dtype=float)
    rot = volume["rotation"]
    if len(rot) == 4:
        rx, ry, rz, order = rot
    else:
        rx, ry, rz = rot
        order = "XYZ"
    scale = np.asarray(volume["scale"], dtype=float)
    M_box = create_model_matrix(pos, (rx, ry, rz), scale, order)
    M_inv = np.linalg.inv(M_box)

    N = points.shape[0]
    points_h = np.hstack([points, np.ones((N, 1), dtype=float)])
    local = (M_inv @ points_h.T).T[:, :3]

    inside = (
        (local[:, 0] >= -0.5)
        & (local[:, 0] <= 0.5)
        & (local[:, 1] >= -0.5)
        & (local[:, 1] <= 0.5)
        & (local[:, 2] >= -0.5)
        & (local[:, 2] <= 0.5)
    )
    return inside


def create_model_matrix(position, rotation, scale, order="XYZ"):
    """
    Create a 4x4 model (local) matrix from position, rotation, and scale.

    Parameters:
        position: (3,) array-like [px, py, pz]
        rotation: (3,) array-like [rx, ry, rz] in radians
        scale:    (3,) array-like [sx, sy, sz]
        order:    string, rotation order, default 'XYZ'

    Returns:
        4x4 numpy array representing the model matrix
    """
    px, py, pz = position
    rx, ry, rz = rotation
    sx, sy, sz = scale

    Rx = np.array(
        [[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]],
        dtype=float,
    )
    Ry = np.array(
        [[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]],
        dtype=float,
    )
    Rz = np.array(
        [[np.cos(rz), -np.sin(rz), 0], [np.sin(rz), np.cos(rz), 0], [0, 0, 1]],
        dtype=float,
    )

    rotation_matrices = {"X": Rx, "Y": Ry, "Z": Rz}
    R = np.eye(3)
    for axis in order:
        R = R @ rotation_matrices[axis]

    S = np.diag([sx, sy, sz])
    RS = R @ S

    model_matrix = np.eye(4)
    model_matrix[:3, :3] = RS
    model_matrix[:3, 3] = [px, py, pz]
    return model_matrix


def _segment_label(to_return, point_source_id, segment_map, bbox_id, gate_mask):
    """
    segment_map: list of dicts with keys {id, label, bboxId}
    """
    if not segment_map:
        return to_return

    to_ret = to_return.copy()
    psid = np.asarray(point_source_id, dtype=float)

    for seg in segment_map:
        if seg.get("bboxId", -1) != bbox_id:
            continue
        sid = float(seg["id"])
        lab = float(seg["label"])
        seg_mask = np.isclose(psid, sid, atol=0.1) & gate_mask
        to_ret[seg_mask] = 50.0 + lab  # GLSL: to_return = label + 50.0
    return to_ret


def _volume_label(
    points, to_return, labeling_volumes, bbox_id, gate_mask, class_index_map
):
    """
    labeling_volumes: list of dicts with keys {bboxId, volume{...}, label, classLocked}
    """
    if not labeling_volumes:
        return to_return

    out = to_return.copy()
    for lv in labeling_volumes:
        if lv.get("bboxId", -1) != bbox_id:
            continue
        vol = lv["volume"]
        lab = float(lv["label"])
        enc_locked = int(lv.get("classLocked", 0))

        inside = _points_in_box_mask(points, vol)
        # class lock based on current out (per-point)
        locked = is_class_locked(enc_locked, out, class_index_map)
        apply_mask = gate_mask & inside & (~locked)
        out[apply_mask] = 50.0 + lab
    return out


def is_class_locked(encoded_locked, cls_current, class_index_map):
    """
    Vectorized lock check.
    encoded_locked: int (bitmask) OR array of ints broadcastable to cls_current
    cls_current: array of current class indices (float)
    class_index_map: dict {class_code(float): bit_index(int)}
    Returns: boolean array mask of same shape as cls_current
    """
    cls_code = get_class_code(cls_current).astype(float)
    # Map each cls_code to a bit index; unseen codes -> 0 by default
    # (You can choose to set unseen -> unlocked or locked; GLSL searches and defaults to 0)
    idx = np.vectorize(lambda c: class_index_map.get(float(c), 0))(cls_code).astype(int)
    bit = np.left_shift(1, idx)  # 1 << idx
    # Ensure integer array for mask operation
    enc = np.asarray(encoded_locked, dtype=np.int64)
    # Broadcast to shape
    enc = np.broadcast_to(enc, cls_code.shape)
    return (enc & bit) != 0


def _polygon_label(
    points, to_return, labeling_polygons, bbox_id, gate_mask, class_index_map
):
    """
    labeling_polygons: list of dicts with keys {bboxId, polygon{...}, label, classLocked}
    """
    if not labeling_polygons:
        return to_return

    out = to_return.copy()
    for lp in labeling_polygons:
        if lp.get("bboxId", -1) != bbox_id:
            continue
        poly = lp["polygon"]
        lab = float(lp["label"])
        enc_locked = int(lp.get("classLocked", 0))

        inside_poly = _points_in_polygon_mask(points, poly)
        locked = is_class_locked(enc_locked, out, class_index_map)
        apply_mask = gate_mask & inside_poly & (~locked)
        out[apply_mask] = 50.0 + lab
    return out


def _points_in_polygon_mask(points, polygon):
    # Three.js-style arrays are column-major; transpose to row-major for NumPy
    view_matrix = np.array(polygon["viewMatrix"], dtype=float).reshape(4, 4).T
    proj_matrix = np.array(polygon["projMatrix"], dtype=float).reshape(4, 4).T
    polygon_ndc = np.array([v["position"][:2] for v in polygon["markers"]], dtype=float)
    return points_in_polygon_ndc(points, view_matrix, proj_matrix, polygon_ndc)


def points_in_polygon_ndc(points, view_matrix, proj_matrix, polygon_ndc, eps=1e-12):
    """
    Check if 3D points lie inside a polygon (polygon in NDC XY).
    Equivalent of GLSL pointInLabelingPolygon.

    Args:
        points (np.ndarray): (N,3) xyz points in world space
        view_matrix (np.ndarray): (4,4) view matrix
        proj_matrix (np.ndarray): (4,4) projection matrix
        polygon_ndc (np.ndarray): (M,2) polygon vertices already in NDC
        eps (float): epsilon to avoid division by zero

    Returns:
        np.ndarray: (N,) boolean mask, True if point is inside polygon
    """
    N = points.shape[0]

    wvp = proj_matrix @ view_matrix

    homog_points = np.hstack([points, np.ones((N, 1))])  # (N,4)
    clip_coords = homog_points @ wvp.T  # (N,4)

    w = clip_coords[:, 3]
    ndc = clip_coords[:, :3] / (w[:, None] + eps)

    # Reject points behind near plane (GLSL: if ndc.z < -1.0 -> false)
    valid_mask = clip_coords[:, 2] >= -1.0

    x, y = ndc[:, 0], ndc[:, 1]
    px, py = polygon_ndc[:, 0], polygon_ndc[:, 1]

    inside = np.zeros(N, dtype=bool)
    j = len(px) - 1
    for i in range(len(px)):
        xi, yi = px[i], py[i]
        xj, yj = px[j], py[j]
        cond = ((yi > y) != (yj > y)) & (
            x < (xj - xi) * (y - yi) / ((yj - yi) + eps) + xi
        )
        inside ^= cond
        j = i
    return inside & valid_mask


def points_in_box(points, box):
    """
    Your earlier signature suggested a single box. This wraps _points_in_box_mask.
    'box' is a dict with keys: position, rotation([rx,ry,rz,"XYZ"]), scale.
    Returns (N,1) boolean mask for consistency with your draft.
    """
    return _points_in_box_mask(points, box).reshape(-1, 1)


def points_in_polygon(points, polygon):
    """
    Wrapper matching your draft for a single polygon dict.
    Returns (N,1) boolean mask.
    """
    return _points_in_polygon_mask(points, polygon).reshape(-1, 1)


if __name__ == "__main__":
    typer.run(main)
    # file_input="/home/sachin/potree_setup/potree/crescer/potree_copc_test1/copc_test/files/33_456000_4497000.copc.laz"
    # # file_input="/home/sachin/potree_setup/potree/crescer/potree_copc_test1/copc_test/files/33_456000_4497000.laz"
    # annotation_input="/home/sachin/potree_setup/potree/crescer/potree_copc_test1/copc_test/annotations/33_456000_4497000.json"
    # output_file="/home/sachin/potree_setup/potree/crescer/potree_copc_test1/copc_test/files/33_456000_4497000_temp_annotated_copc.laz"
    # apply_annotation_on_file(
    #     file_input,
    #     annotation_input,
    #     output_file,
    #     num_workers=8,
    #     chunksize=100_000,
    # )