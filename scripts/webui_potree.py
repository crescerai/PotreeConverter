import os
from pathlib import Path
import subprocess
import streamlit as st

POTREE_CONVERTER_PATH = "/app/build/PotreeConverter"
BASE_OUTPUT_FOLDER = Path("/app/potree/crescer")

BASE_OUTPUT_FOLDER.mkdir(exist_ok=True)

def convert_file(in_file: Path, output_dir: Path):
    """Runs PotreeConverter on a single file and streams output to Streamlit."""
    potree_command = [
        POTREE_CONVERTER_PATH,
        str(in_file),
        "-o",
        str(output_dir),
        "-p",
        in_file.stem,
    ]

    with st.status(f"Converting: {in_file.name}", expanded=True) as status:
        process = subprocess.Popen(
            potree_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )

        for line in process.stdout:
            st.text(line.strip())  # Display output line by line in Streamlit

        process.wait()
        if process.returncode == 0:
            status.update(
                label="Conversion Successful!", state="complete", expanded=False
            )
        else:
            status.update(label="Conversion Failed!", state="error", expanded=True)


def convert_directory(in_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    for entry in in_dir.iterdir():
        if entry.is_dir():
            convert_directory(entry, output_dir / entry.name)
        elif entry.suffix in [".las", ".laz"]:
            convert_file(entry, output_dir)


def get_output_folder(input_folder: Path) -> Path:
    """Determine the default output folder based on input type."""
    return (
        (BASE_OUTPUT_FOLDER / input_folder.name)
        if input_folder.is_dir()
        else BASE_OUTPUT_FOLDER
    )


def display_error(message: str):
    """Display an error message in red."""
    st.markdown(f"<span style='color:red'>{message}</span>", unsafe_allow_html=True)


def convert(input_folder: Path, output_folder: Path):
    """Perform conversion based on input type."""
    if input_folder.is_dir():
        convert_directory(input_folder, output_folder)
    else:
        convert_file(input_folder, output_folder)
    st.success("Conversion Complete!")


def main():
    st.title("Potree Converter")

    # Input fields
    input_path_str = st.text_input("Input Folder/File")
    input_folder = Path(input_path_str)

    output_folder = st.text_input(
        "Output Folder", value=str(get_output_folder(input_folder))
    )

    # Validate input
    if not input_folder.exists():
        display_error(f"Input Folder/File does not exist: {input_folder}")

    st.write("Converted files will be stored here:", output_folder)

    if st.button("Convert"):
        convert(input_folder, Path(output_folder))


if __name__ == "__main__":
    main()
