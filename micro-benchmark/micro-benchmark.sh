#!/usr/bin/env bash
#SBATCH --job-name=micro-benchmark
#SBATCH --output=micro-benchmark.%j.out
#SBATCH --error=micro-benchmark.%j.err
#SBATCH --time=00:10:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=512M

set -euo pipefail
echo "Job start: $(date)"
echo "Slurm job id: $SLURM_JOB_ID"
echo "Node: $(hostname)"
echo "CPUs allocated: $SLURM_CPUS_ON_NODE (CPUs per task: $SLURM_CPUS_PER_TASK)"
echo

# 1) Collect basic metrics
echo "=== System metrics ==="
echo "Uptime: $(uptime -p || true)"
echo "Loadavg: $(cat /proc/loadavg 2>/dev/null || true)"
echo "Meminfo (MemTotal, MemAvailable):"
grep -E 'MemTotal|MemAvailable' /proc/meminfo || true
echo "Disk usage for /tmp (or /):"
df -h /tmp 2>/dev/null || df -h / || true
echo

# 2) Micro-benchmark CPU: count prime numbers until N
N=50   # adjust if want more or less load
echo "=== CPU micro-benchmark: primes up to $N ==="
start_cpu=$(date +%s.%N)
count_primes() {
  local limit=$1
  local count=0
  for ((i=2;i<=limit;i++)); do
    local isprime=1
    local max=$((i/2))
    for ((j=2;j<=max;j++)); do
      if (( i % j == 0 )); then
        isprime=0
        break
      fi
    done
    (( isprime )) && count=$((count+1))
  done
  echo $count
}
prime_count=$(count_primes $N)
end_cpu=$(date +%s.%N)
cpu_elapsed=$(awk "BEGIN {print $end_cpu - $start_cpu}")
echo "Primes found: $prime_count"
printf "CPU time: %.3f s\n" "$cpu_elapsed"
echo

# 3) Micro-benchmark I/O: read and write a temp file
echo "=== I/O micro-benchmark ==="
tmpfile=$(mktemp /tmp/micro-benchmark.XXXXXX)
size_mb=64   # total to write (flexible)
block_kb=1024
count_blocks=$(( size_mb * 1024 / block_kb ))
echo "Writing $size_mb MB to $tmpfile using dd"
start_io=$(date +%s.%N)
dd if=/dev/urandom of="$tmpfile" bs=${block_kb}K count=$count_blocks oflag=direct conv=fsync 2>&1 | sed 's/^/  /'
sync
read_bytes=$(stat -c%s "$tmpfile" || true)
# read back (sequential read)
dd if="$tmpfile" of=/dev/null bs=1M iflag=direct 2>&1 | sed 's/^/  /'
end_io=$(date +%s.%N)
io_elapsed=$(awk "BEGIN {print $end_io - $start_io}")
echo "Wrote bytes: $read_bytes"
printf "I/O time (write+read): %.3f s\n" "$io_elapsed"
rm -f "$tmpfile"
echo

# 4) Small network test (optional) - resolve and ping a well-known host if networking allowed.
# Hard-capped with `timeout` so a black-holed network or slow DNS cannot stall the job.
if command -v ping >/dev/null 2>&1; then
  echo "=== Network test: DNS resolve & ping google.com (1 packet) ==="
  host=$(timeout 3 getent hosts google.com 2>/dev/null | awk '{print $1; exit}' || true)
  echo "Resolved: ${host:-(none)}"
  timeout 5 ping -c 1 -W 2 google.com 2>&1 | sed 's/^/  /' || echo "  (ping unavailable or timed out)"
  echo
fi

echo "Job end: $(date)"
