FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

RUN apt-get update \
    && apt-get install -y git build-essential cmake python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/*

COPY llama.cpp /opt/llama.cpp
RUN rm -rf /opt/llama.cpp/build
ENV CUDA_HOME=/usr/local/cuda
ENV LD_LIBRARY_PATH=/usr/local/nvidia/lib64:/usr/local/nvidia/lib:/usr/local/cuda/lib64
ENV LIBRARY_PATH=/usr/local/cuda/lib64/stubs
RUN ln -s /usr/local/cuda/lib64/stubs/libcuda.so /usr/lib/x86_64-linux-gnu/libcuda.so.1
RUN cmake -S /opt/llama.cpp -B /opt/llama.cpp/build -DLLAMA_CUDA=ON -DLLAMA_CURL=OFF \
    && cmake --build /opt/llama.cpp/build --target llama-server llama-bench -j

COPY services/llama/entrypoint.py /entrypoint.py
COPY services/llama/chat_template.jinja /chat_template.jinja
RUN chmod +x /entrypoint.py

EXPOSE 8081

ENTRYPOINT ["/entrypoint.py"]
