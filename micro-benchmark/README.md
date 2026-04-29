# Micro-benchmark

A minimal Slurm smoke-test job intended as the simplest catalog template. It does just enough work to confirm that a node can schedule a job, run a CPU-bound loop, perform a small disk read/write, and (optionally) reach the network — useful as a "hello world" before submitting a real workload.

This template does **not** use Apptainer or any HPC application. It runs entirely with packages already present on a typical Linux compute node (`bash`, `coreutils`, `awk`, `dd`, optionally `ping`).

## Requirements

- **SLURM**: For job scheduling and resource management.
- A writable `/tmp` of at least ~70 MB on the compute node.

## Job Steps

The script runs the following sections sequentially:

1. **System metrics**: prints `uptime`, `/proc/loadavg`, `MemTotal`/`MemAvailable` from `/proc/meminfo`, and `df -h` for `/tmp`.
2. **CPU micro-benchmark**: counts prime numbers up to `N` (default `N=50`) using a pure-bash trial-division loop, and reports elapsed time.
3. **I/O micro-benchmark**: writes 64 MB of random bytes to a temp file under `/tmp` with `dd ... conv=fsync`, reads it back, and reports the combined elapsed time.
4. **Network test (optional)**: resolves `google.com` via `getent` and sends one ICMP packet with `ping`. Both calls are wrapped in `timeout` so a black-holed network or slow DNS adds at most a few seconds. Failure is reported but does not fail the job.

## Result Interpretation

All output is written to `micro-benchmark.<jobid>.out` (and stderr to `micro-benchmark.<jobid>.err`). Look for:

- **`Primes found`** and **`CPU time`** — sanity check that the CPU is doing work.
- **`Wrote bytes`** and **`I/O time (write+read)`** — sanity check that `/tmp` is writable and roughly how fast it is.
- **`Resolved`** and the `ping` output — whether the compute node has DNS and outbound ICMP. A `(none)` resolution or a timed-out ping is normal on isolated clusters.

## Customization

- **`N`** (in the script): increase to make the CPU section take longer. Note that the loop is O(N²) in pure bash — values above a few hundred get slow quickly.
- **`size_mb`** (in the script): the size of the I/O test file. Default `64`.
- **`#SBATCH` directives**: adjust `--time`, `--mem`, `--ntasks`, `--cpus-per-task` to fit the partition you target. The script itself is single-threaded, so `--cpus-per-task=1` is sufficient.
