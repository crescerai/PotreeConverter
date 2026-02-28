# Use an official Ubuntu base image
FROM ubuntu:22.04

# Set environment variables to avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y \
    software-properties-common \
    cmake \
    build-essential \
    libtbb-dev \
    libboost-all-dev \
    libgeos-dev \
    libpdal-dev \
    pdal \
    python3-pip

RUN pip install streamlit laspy==2.5.4 laszip==0.2.3 lazrs==0.6.1 folium tqdm shapely streamlit_folium icecream pyproj polars typer

COPY . .
RUN mkdir build && cd build && cmake ../ && make

CMD ["streamlit", "run", "scripts/potree_converter_dashboard.py"]
