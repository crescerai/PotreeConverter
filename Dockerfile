FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y \
    software-properties-common \
    cmake \
    ninja-build \
    g++-14 \
    gcc-14 \
    libtbb-dev \
    python3-pip \
    python3-venv && \
    update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 100 --slave /usr/bin/g++ g++ /usr/bin/g++-14 && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --break-system-packages streamlit

COPY . .
RUN cmake -G Ninja -B build -S . && ninja -C build

CMD ["streamlit", "run", "scripts/webui_potree.py"]
