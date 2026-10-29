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
| Azure access | Undecided as of project start — **resolved Phase 11**: no real Azure subscription used (see `docs/assumptions-register.md` A1) |

## Implications for sizing
- 16 GB host RAM is the binding constraint, not GPU VRAM. In practice, the final stack (Postgres,
  api, mlflow, prometheus, cadvisor, grafana) never came close to this limit — no local LLM or
  vector DB (Qdrant) was ever actually added, since Use Case F (semantic/similarity search) was
  never scheduled and Use Case C (free-text extraction) remains deferred per assumption A5b. The
  `.wslconfig` memory ceiling was never hit or raised.
- 8 GB VRAM supports small quantized local LLMs (7B-class) and embedding models comfortably — moot
  for this project in practice, since no LLM/embedding component was ever built (see above).
- Plenty of free disk for container images — confirmed in practice: all 9 service images combined
  total well under the free disk headroom (see the offline bundle size in `docs/offline-deployment.md`).
