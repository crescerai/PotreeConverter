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
    python3-pip

RUN pip install streamlit

COPY . .
RUN mkdir build && cd build && cmake ../ && make

CMD ["streamlit", "run", "script/potree_converter_dashboard.py"]
