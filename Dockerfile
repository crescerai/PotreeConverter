# Use an official Ubuntu base image
FROM ubuntu:22.04

# Set environment variables to avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install required dependencies, including TBB
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    nano \
    curl \
    git \
    wget \
    unzip \
    libboost-all-dev \
    libpdal-dev \
    libgeos-dev \
    libtbb-dev \
    nodejs \
    npm \
    sed \
	python3-pip \
    && apt-get clean

# Clone and build PotreeConverter
RUN git clone https://github.com/crescerai/PotreeConverter.git /app/PotreeConverter && \
    cd /app/PotreeConverter && \
    git switch classmap_modification && \
    mkdir build && cd build && \
    cmake .. && make

# Clone the Potree repository
RUN git clone https://github.com/potree/potree.git /app/potree

# Change directory to Potree and install dependencies
WORKDIR /app/potree
RUN npm install

# Modify gulpfile.js to update port and add host
RUN sed -i 's/port: 1234,/host: "0.0.0.0",\n                port: 1234,/g' /app/potree/gulpfile.js

# Expose any required ports (adjust based on Potree default)
EXPOSE 1234

# Install Python dependencies from requirements.txt
COPY requirements.txt /app/requirements.txt
RUN pip3 install -r /app/requirements.txt
# RUN pip install -e .
COPY script/web_ui_potree.py /app/web_ui_potree.py
COPY script/generate_potree.py /app/generate_potree.py
COPY script/clean_file.py /app/clean_file.py

# RUN mkdir -p ~/.streamlit && \
#     echo "[server]\nheadless = true\naddress = \"0.0.0.0\"\nenableCORS = false" > ~/.streamlit/config.toml

# Set the default command to start both the Potree web application and the Streamlit app
CMD ["sh", "-c", "npm start & streamlit run /app/web_ui_potree.py"]
