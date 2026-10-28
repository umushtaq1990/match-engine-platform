# Hardware Inventory (Dev Machine)

Captured 2026-10-28 via PowerShell/WMI/nvidia-smi — measured, not estimated.

| Item | Value |
|---|---|
| OS | Windows 11 Home, build 26200, 64-bit |
| CPU | AMD Ryzen 7 250 (8 cores / 16 logical processors) |
| RAM | ~16 GB total |
| GPU (dedicated) | NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB VRAM, driver 595.95 |
| GPU (integrated) | AMD Radeon 780M |
| Disk (C:) | ~593 GB free |
| WSL2 | Installed, running (`docker-desktop` distro, WSL version 2) |
| Docker Desktop | v29.4.2, Linux containers, engine reports 16 CPUs / ~7.41 GiB RAM allocated to the VM |
| Azure access | Undecided as of project start — design local-first, defer cloud deployment (Phase 11) |

## Implications for sizing
- 16 GB host RAM is the binding constraint, not GPU VRAM. Running Postgres + Qdrant + API + a local LLM simultaneously will be tight; plan to raise Docker Desktop's WSL2 memory allocation (`.wslconfig`) only once we actually hit the limit, not preemptively.
- 8 GB VRAM supports small quantized local LLMs (7B-class) and embedding models comfortably; not enough for large models — keep LLM-based components optional/behind an interface so a mock or remote inference service can substitute.
- Plenty of free disk for container images, model artifacts, and Postgres/Qdrant data.
