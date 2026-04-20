# Infrastructure

Hardware, CUDA, llama.cpp, and systemd service setup for pyrite.

---

## Hardware

**Machine**: pyrite (Ubuntu 24.04, x86-64)

| GPU | Model | VRAM | Compute capability | Role |
|---|---|---|---|---|
| 0 | Tesla P100-PCIE-16GB | 16 GB | sm_60 (Pascal) | LLM + embeddings |
| 1 | NVIDIA GeForce GTX 1070 | 8 GB | sm_61 (Pascal) | Other workloads |

**CUDA constraint**: both GPUs are Pascal architecture (sm_60/sm_61). CUDA 13 requires
sm_75 (Turing) or newer - it is incompatible with this hardware. The system must use
CUDA 12.x.

---

## CUDA setup

### Installed version

CUDA 12.8 runtime, installed from the NVIDIA network apt repo:

```bash
# Add NVIDIA network repo (one-time)
wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb \
    -O /tmp/cuda-keyring.deb
sudo dpkg -i /tmp/cuda-keyring.deb
sudo apt-get update

# Install CUDA 12.8 runtime + cuBLAS (required by llama.cpp)
sudo apt-get install -y cuda-cudart-12-8 cuda-cudart-dev-12-8 libcublas-12-8 libcublas-dev-12-8
```

The active CUDA symlink points to 12.8:

```bash
ls -la /usr/local/cuda
# lrwxrwxrwx ... /usr/local/cuda -> /usr/local/cuda-12.8
```

To verify the runtime is registered with ldconfig:

```bash
ldconfig -p | grep cudart
# libcudart.so.12 (libc6,x86-64) => /usr/local/cuda-12/targets/x86_64-linux/lib/libcudart.so.12
```

### Why not CUDA 13

CUDA 13 was accidentally installed via a local `.deb` repo. It was subsequently removed:

```bash
sudo apt-get purge -y cuda-*-13-0 cuda-toolkit-13* cuda-repo-ubuntu2404-13-0-local
sudo rm -rf /usr/local/cuda-13.0
sudo ln -sfn /usr/local/cuda-12.8 /usr/local/cuda
```

Do not install CUDA 13 on this machine - it will not run on either GPU.

### Pinning GPUs

Both llama.cpp services are pinned to GPU 0 (P100) via `CUDA_VISIBLE_DEVICES=0` in their
service files. This is set in `scripts/nixx-embed.service` and `scripts/llamacpp.service`
(or its override at `/etc/systemd/system/llamacpp.service.d/override.conf`).

---

## llama.cpp

### Source and binary location

```
/opt/llama.cpp/          — source tree (git clone)
/opt/llama.cpp/build/bin/llama-server  — compiled binary
/opt/models/             — model files (owned by llama:llama)
```

### Build instructions

The binary is built against CUDA 12. If it needs to be rebuilt (e.g. after a llama.cpp
update, or if the binary is lost):

```bash
cd /opt/llama.cpp
sudo cmake -B build \
    -DGGML_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES=60   # sm_60 = P100; avoids targeting unsupported arches
sudo cmake --build build --config Release -j$(nproc)
```

`-DCMAKE_CUDA_ARCHITECTURES=60` is important: without it, cmake may attempt to compile
for architectures the driver does not support.

### Models

| File | Size | Used by |
|---|---|---|
| `gpt-oss-20b-mxfp4.gguf` | ~11 GB | LLM inference (port 8502) |
| `mxbai-embed-large-v1-f16.gguf` | ~670 MB | Embeddings (port 8082) |

At 65536 context length (`-c 65536`), the LLM uses ~13.9 GB of the 16 GB P100. Reduce
context in `llamacpp.service` if VRAM budget changes (e.g. if running both services
simultaneously causes OOM).

---

## Systemd services

### Service files

All nixx-related unit files live in `scripts/` and are symlinked (or copied) to
`/etc/systemd/system/`. `llamacpp.service` is the exception - it lives only in
`/etc/systemd/system/` because it is not part of the nixx source tree.

| File | Location | Notes |
|---|---|---|
| `scripts/nixx.target` | `/etc/systemd/system/nixx.target` | Groups nixx services |
| `scripts/nixx-server.service` | `/etc/systemd/system/nixx-server.service` | nixx API server |
| `scripts/nixx-embed.service` | `/etc/systemd/system/nixx-embed.service` | Embedding server |
| `scripts/nixx-pgweb.service` | `/etc/systemd/system/nixx-pgweb.service` | pgweb browser |
| `scripts/llamacpp.service` | `/etc/systemd/system/llamacpp.service` | LLM inference server |

To install service files after changes:

```bash
sudo cp scripts/nixx-embed.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### Starting and stopping

Services are not enabled for auto-boot. Start manually:

```bash
# Start everything
sudo systemctl start llamacpp
sudo systemctl start nixx.target

# Stop everything
sudo systemctl stop nixx.target
sudo systemctl stop llamacpp

# Restart after code changes
sudo systemctl restart nixx-server

# Check status
systemctl status nixx.target nixx-server nixx-embed llamacpp
```

**Note**: restarting `nixx.target` does not cascade to individual services. Restart each
service explicitly when needed.

### llamacpp.service key parameters

```ini
ExecStart=/opt/llama.cpp/build/bin/llama-server \
    -m /opt/models/gpt-oss-20b-mxfp4.gguf \
    --n-gpu-layers 999 \       # all layers on GPU
    -c 65536 \                 # context length
    --flash-attn on \          # required for long context on P100
    --jinja \                  # jinja chat templates
    --host 0.0.0.0 \           # accessible over WireGuard
    --port 8502 \
    --api-key <key> \
    --metrics
```

The server is reached from the nixx API server via `model.perdrizet.org` (nginx on gatekeeper
→ WireGuard → localhost:8502), configured in `.env` as `NIXX_LLM_BASE_URL`.

### Recovering from a failed service

If `llamacpp.service` hits its restart limit (5 attempts in 300 seconds):

```bash
sudo systemctl reset-failed llamacpp
sudo systemctl start llamacpp
```

---

## Diagnosing GPU issues

```bash
# Check which CUDA libraries are registered
ldconfig -p | grep cudart

# Check GPU memory usage
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader

# Check what's using the GPU
nvidia-smi

# Verify llama-server linked correctly
ldd /opt/llama.cpp/build/bin/llama-server | grep cuda

# Check service logs
journalctl -u llamacpp -n 30 --no-pager
journalctl -u nixx-embed -n 20 --no-pager
```

Common failures:

| Symptom | Cause | Fix |
|---|---|---|
| `libcudart.so.12: cannot open shared object file` | CUDA runtime not in ldconfig | Install `cuda-cudart-12-8`, run `sudo ldconfig` |
| `CUDA error: no kernel image is available` | Binary compiled for wrong arch | Rebuild with `-DCMAKE_CUDA_ARCHITECTURES=60` |
| Service hits restart limit | Check logs with `journalctl -u llamacpp -n 50` | `systemctl reset-failed llamacpp && systemctl start llamacpp` |
| OOM / CUDA out of memory | Context too long or another process using GPU | Reduce `-c` in llamacpp.service or free GPU memory |
