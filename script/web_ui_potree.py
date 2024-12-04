import streamlit as st
import os
from generate_potree import process_directory , process_file

# Title of the app
st.title("Potree Converter")
BASE_OUTPUT_FOLDER = "/app/potree/crescer"

# Input fields
input_folder = st.text_input("Input Folder/File")
with st.sidebar:
	output_folder_base = st.text_input("Base Output Folder", value=BASE_OUTPUT_FOLDER)
	need_cleaning = st.checkbox("Need Cleaning", value=True)


# Display the values
def isnotdir(path):
	return not os.path.isdir(path)
# Generate default output folder based on input folder
if os.path.isdir(input_folder):
	default_output_folder = os.path.join(output_folder_base, os.path.basename(input_folder))
else:
	default_output_folder = output_folder_base

# Output folder with default value
output_folder = st.text_input("Output Folder", value=default_output_folder)


if not os.path.exists(input_folder):
	st.markdown(f"<span style='color:red'>Input Folder/File does not exist: {input_folder}</span>", unsafe_allow_html=True)

if isnotdir(output_folder_base):
	st.markdown(f"<span style='color:red'>Base Output Folder Path not correct: {output_folder_base}</span>", unsafe_allow_html=True)
elif not os.path.exists(output_folder_base):
	st.markdown(f"<span style='color:red'>Base Output Folder does not exist: {output_folder_base}</span>", unsafe_allow_html=True)
	st.write("Base Output Folder:", output_folder_base)
st.write("Converted Files will be stored here:", output_folder)
st.write("Need Cleaning:", need_cleaning)


if st.button("Start Conversion"):
	if os.path.isdir(input_folder):
		with st.spinner('Processing...'):
			process_directory(input_folder, output_folder, need_cleaning)
			st.success('Conversion completed!')
	else:
		if os.path.exists(input_folder):
			with st.spinner('Processing...'):
				process_file(input_folder, output_folder, need_cleaning)
				st.success('Conversion completed!')