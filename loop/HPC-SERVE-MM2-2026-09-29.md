# HPC-SERVE-MM2 — second-model endpoint setup + launch runbook (2026-09-29, INT-040)

**STATUS: SETUP ONLY — NOTHING QUEUED, NOTHING DISPATCHED.** Companion to
`loop/FREEZE-MM2-2026-09-29.md` (the frozen protocol). The owner's launch
go (roster + cost + window) precedes step 1. House rules carry: the
hpc-agent daemon owns every submission — **never manual sbatch**; no
pushes; sends only inside 17:00–03:00.

## 0. Serve state today (verified 2026-09-29, no changes made)

- Seat A's endpoint needs NOTHING new: hermes serves
  `XiaomiMiMo/MiMo-V2.6-Flash-RL` via vLLM tensor-parallel-4 (Slurm job
  `18093231` RUNNING, daemon/night_auto_lander-owned, lane_exempt),
  health-verified 09-29 (lane-port-1 health+models, ctx 131072).
- Wire to HQ: hermes backend port serve-port-a → example-host-d observability proxy
  (lanes: **lane-port-1 = hermes, lane-port-2 = human** — port semantics are
  load-bearing, UNIFIED-ORCHESTRATION-FINDINGS) → HQ forward
  `ssh -L 127.0.0.1:lane-port-1:127.0.0.1:lane-port-1 you@example-host-d`
  → opencode provider `hpc` (`~/.config/opencode/opencode.json`).
- Precedent serve jobs: `glm-llamacpp` (llama.cpp sbatch,
  `slurm/glm53_llamacpp_serve.slurm` — superseded per ADR-0001, GGUF_DIR
  re-home pending) and `mimo-vllm-tp4`. Banned nodes hpc-n968/hpc-n977
  (exclude in any new sbatch).

## 1. The roster (THE OWNER CALL — pick one line)

| Option | `<mm2>` alias | Weights | Server | Fleet cost (per 6 h window) | Notes |
|--------|---------------|---------|--------|------------------------------|-------|
| R1 | `qwen.coder.7b` | Qwen2.5-Coder-7B-Instruct, GGUF Q8 (~8 GB dl) | llama.cpp, 1 GPU, tp1 | ~6 GPU·h | cheapest true second family; different family AND scale from MiMo |
| R2 | `qwen.coder.32b` | Qwen2.5-Coder-32B-Instruct (~65 GB dl) | vLLM, tp2 | ~12 GPU·h | capability-matched-ish comparator; needs scratch df-first |
| R3 | `<weights-on-hand>` | whatever instruction weights already sit on hermes scratch (step 1 census decides) | per weights | ~0 download | lowest total cost; owner confirms the exact id from the census |

Different-family requirement: the paper's cross-model claim needs the
second endpoint to NOT be a Xiaomi/MiMo checkpoint — R3 candidates are
screened on that before owner confirmation.

## 2. Port plan (collision-free by construction)

Second endpoint gets a FRESH chain, touching nothing live:

```
hermes serve job  →  backend port serve-port-b  →  example-host-d proxy lane lane-port-3
                  →  HQ ssh -L 127.0.0.1:lane-port-3:127.0.0.1:lane-port-3 you@example-host-d
                  →  opencode provider hpc2 → http://127.0.0.1:lane-port-3/v1
```

lane-port-3 keeps clear of the reserved semantics (lane-port-1 hermes / lane-port-2 human).
Backend serve-port-b on the serve node mirrors the serve-port-a pattern.

## 3. Serve job template (staged into the hpc-agent daemon lane config)

The daemon's serve lane (ADR-0001: it, not a human, submits) gets ONE new
lane entry. Two template shapes — pick per roster:

llama.cpp (R1/R3-GGUF), job name `mm2-llamacpp-tp1`:

```bash
#!/bin/bash
#SBATCH --job-name=mm2-llamacpp-tp1
#SBATCH --gres=gpu:1
#SBATCH --time=06:00:00
#SBATCH --exclude=hpc-n968,hpc-n977
#SBATCH --output=logs/mm2-llamacpp-%j.out
# GGUF_DIR re-homed per ADR-0001 (old GLM path is dead)
llama-server --host 0.0.0.0 --port serve-port-b \
  --model "$GGUF_DIR/<roster>.q8_0.gguf" \
  --served-model-name <Served-Model-Name> \
  --ctx-size 32768
```

vLLM (R2/R3-safetensors), job name `mm2-vllm-tp2`:

```bash
#!/bin/bash
#SBATCH --job-name=mm2-vllm-tp2
#SBATCH --gres=gpu:2
#SBATCH --time=06:00:00
#SBATCH --exclude=hpc-n968,hpc-n977
#SBATCH --output=logs/mm2-vllm-%j.out
vllm serve <Weights-Path> \
  --served-model-name <Served-Model-Name> \
  --port serve-port-b --tensor-parallel-size 2 \
  --max-model-len 32768
```

`--served-model-name` is pinned and recorded — the whole attribution
chain (SWARM_MODEL → opencode → verdict provenance) hangs on it. The
6 h time limit IS the teardown: when the window closes, the job ends
itself; no scancel choreography.

## 4. Launch runbook (after the owner go; ~30 min of seat work)

1. **Preflight**: `df -h` scratch on hermes (> weights + 20%); `squeue`
   (jobs 18093231 healthy; no queue storm); lane-port-1 tunnel + proxy green;
   send window open (17:00–03:00). GPU census for R3: enumerate hermes
   scratch weights, screen out MiMo-family, hand the list to the owner
   if R3 was chosen.
2. **Stage + queue the serve lane** in the hpc-agent daemon config
   (template §3 filled with the roster line); daemon submits; verify the
   job reaches RUNNING. If still PENDING at window_start − 30 min:
   DEFER the window, disclose (never manual sbatch, never bump queues —
   caps and queues are human).
3. **Backend verify**: `curl -s http://127.0.0.1:serve-port-b/v1/models` ON the
   example-host-d/hermes side shows `<Served-Model-Name>`; one 1-token
   completion round-trips.
4. **Open the HQ leg**: start the lane-port-3 forward (§2 ssh -L, same
   autossh/service pattern the lane-port-1 forward uses); verify from HQ:
   `curl -s http://127.0.0.1:lane-port-3/v1/models`.
5. **opencode provider (launch-time edit)**: add to
   `~/.config/opencode/opencode.json`:
   ```json
   "hpc2": {
     "npm": "@ai-sdk/openai-compatible",
     "name": "HPC <roster> (tunnel :lane-port-3)",
     "options": { "baseURL": "http://127.0.0.1:lane-port-3/v1" },
     "models": { "<Served-Model-Name>": { "name": "<roster>",
       "limit": { "context": 32768, "output": 8192 } } }
   }
   ```
   The `limit.context` MUST match the served `--ctx-size`/
   `--max-model-len` (HQ-INTEGRATION law: pin the full string per
   session). Default `model` key is NOT touched.
6. **Smoke**: `opencode run --pure -m hpc2/<Served-Model-Name> "reply ok"`
   from HQ — exits 0 with text (E9's 2-second billing-death class is the
   failure to watch for).
7. **Canaries, then windows** per FREEZE-MM2 §3 (canaries first, arm S
   then arm X; SWARM_MODEL strings exactly as frozen there).
8. **Close + receipt**: correction-graph read-out both origins; INT-040
   status + KEEPER-LOG entries; serve job ends on its own time limit;
   the hpc2 provider entry and lane-port-3 forward are torn down and the
   teardown recorded (leave-no-orphans law).
