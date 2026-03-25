# Container Troubleshooting Guide

**Document Version:** 1.0
**Last Updated:** 2026-03-24
**Audience:** Developers, DevOps, operators

## Quick Diagnostics

### Check Docker Status

```bash
# Verify Docker daemon is running
docker ps

# Check Docker version (must be >= 20.10)
docker version

# Verify orchestrator-net exists
docker network ls | grep orchestrator-net

# List active orchestrator containers
docker ps --filter name=orchestrator-
```

### Check Configuration

```bash
# Verify image exists locally
docker image ls | grep ai-sdlc-orchestrator

# Verify env file permissions (must be 600)
ls -la ~/.orchestrator.env

# Verify ANTHROPIC_API_KEY is set
grep ANTHROPIC_API_KEY ~/.orchestrator.env
```

---

## Common Issues & Solutions

### "Docker is not installed or not running"

**Error:**
```
RuntimeError: Docker is not installed or not running
```

**Cause:** Docker daemon is not reachable or not installed.

**Solution:**

1. **Install Docker** (if needed):
   - [macOS](https://docs.docker.com/desktop/install/mac-install/): Install Docker Desktop
   - [Linux](https://docs.docker.com/engine/install/): Install Docker Engine via package manager
   - [Windows](https://docs.docker.com/desktop/install/windows-install/): Install Docker Desktop

2. **Start Docker daemon:**
   - macOS: Open Docker Desktop app
   - Linux: `sudo systemctl start docker`
   - Windows: Start Docker Desktop

3. **Verify Docker is running:**
   ```bash
   docker ps
   # If this works, Docker is running
   ```

4. **Check Docker socket permission** (Linux):
   ```bash
   # If you get "permission denied", add your user to docker group:
   sudo usermod -aG docker $USER
   newgrp docker  # Activate group membership
   docker ps      # Test
   ```

---

### "Docker version X.Y.Z < 20.10.0"

**Error:**
```
RuntimeError: Docker version 19.03.0 < 20.10.0 (minimum required)
```

**Cause:** Docker Engine is older than the minimum supported version.

**Solution:**

1. **Check current version:**
   ```bash
   docker version --format '{{.Server.Version}}'
   ```

2. **Upgrade Docker:**
   - macOS: Update Docker Desktop via System Preferences > Software Update
   - Linux: `sudo apt-get update && sudo apt-get upgrade docker-ce` (Debian/Ubuntu)
   - Linux: `sudo yum update docker` (RHEL/CentOS)

3. **Verify upgrade:**
   ```bash
   docker version --format '{{.Server.Version}}'  # Should be >= 20.10.0
   ```

---

### "orchestrator-net network not found"

**Error:**
```
Error response from daemon: network orchestrator-net not found
```

**Cause:** The Docker bridge network `orchestrator-net` was not created.

**Solution:**

1. **Create the network manually:**
   ```bash
   docker network create orchestrator-net
   ```

2. **Or run the setup script:**
   ```bash
   sudo bash infra/scripts/setup-network-policy.sh
   ```

3. **Verify network exists:**
   ```bash
   docker network ls | grep orchestrator-net
   # Output: orchestrator-net    bridge    local
   ```

---

### "API calls fail with connection timeout"

**Error:**
```
requests.exceptions.ConnectionError: Failed to establish a new connection to api.anthropic.com
```

**Cause:** Container cannot reach api.anthropic.com due to network policy or DNS issues.

**Diagnosis:**

1. **Check if api.anthropic.com IPs are blocked:**
   ```bash
   # Spawn a test container
   docker run --rm --network orchestrator-net ai-sdlc-orchestrator:latest \
     sh -c "curl -s --max-time 5 -o /dev/null -w '%{http_code}' https://api.anthropic.com"

   # Expected output: 401 or 200 (successful connection)
   # Actual: timeout or "Connection refused"?
   ```

2. **Check iptables rules (Linux only):**
   ```bash
   sudo iptables -vnL | grep orchestrator
   # Should see ACCEPT rules for api.anthropic.com on port 443
   ```

3. **Check DNS injection (inside container):**
   ```bash
   docker run --rm --network orchestrator-net ai-sdlc-orchestrator:latest \
     sh -c "cat /etc/hosts | grep api.anthropic.com"
   # Should see: "135.25.123.45 api.anthropic.com"
   ```

**Solutions:**

1. **Refresh network policy** (if Anthropic rotated IPs):
   ```bash
   sudo bash infra/scripts/setup-network-policy.sh --refresh
   ```

2. **Temporarily disable network isolation** (debugging only):
   ```bash
   orchestrate-container "..." --no-network-isolation --env-file ~/.orchestrator.env
   ```

3. **On macOS, use a proxy** (iptables not available):
   ```bash
   # Install mitmproxy
   pip install mitmproxy

   # Run with allowlist
   mitmweb --mode transparent --allowlist "api.anthropic.com"

   # Add to ~/.orchestrator.env
   echo "HTTPS_PROXY=http://127.0.0.1:8080" >> ~/.orchestrator.env
   ```

---

### "Stale Docker image running outdated code"

**Error:** Container runs code from before your recent changes to `src/orchestrator/` or `pyproject.toml`

**Cause:** Docker image was not rebuilt after code changes.

**Solution:**

1. **Rebuild the image:**
   ```bash
   bash infra/scripts/build-image.sh
   ```

2. **Verify the image was updated:**
   ```bash
   docker image ls ai-sdlc-orchestrator:latest
   # Note the creation timestamp
   ```

3. **Re-run orchestration:**
   ```bash
   orchestrate-container "..." --env-file ~/.orchestrator.env
   ```

**Prevention:**
- Add a pre-commit hook to remind you to rebuild:
  ```bash
  # .git/hooks/pre-commit
  #!/bin/bash
  if git diff --cached --name-only | grep -E "^src/orchestrator/|pyproject.toml"; then
    echo "⚠️  Remember to rebuild Docker image: bash infra/scripts/build-image.sh"
  fi
  ```

---

### "Container exits immediately with non-zero code"

**Error:**
```
Container exited with code: 137  # 137 = SIGKILL (OOM)
Container exited with code: 139  # 139 = SIGSEGV (segmentation fault)
```

**Cause:** Container was killed by kernel (OOM, segfault, signal).

**Diagnosis:**

1. **Check Docker logs:**
   ```bash
   # If container is still there (didn't auto-remove)
   docker logs orchestrator-<run_id> | tail -50
   ```

2. **Check host system logs:**
   ```bash
   # macOS
   log stream --process orchestrator 2>&1 | grep -i error

   # Linux
   sudo journalctl -u docker -n 100 --no-pager
   ```

3. **Monitor resource usage during run:**
   ```bash
   docker stats <container_id> --no-stream
   ```

**Solutions:**

1. **If OOM (exit code 137):**
   - Increase memory limit in config/default.yaml:
     ```yaml
     container:
       memory_limit: 16g  # Increase from 8g
     ```

2. **If segfault (exit code 139):**
   - Likely a Python runtime issue; check orchestrator logs:
     ```bash
     tail -100 workspace/logs/<run_id>.log
     ```
   - File an issue with the logs

3. **If SIGKILL (no specific exit code):**
   - Container may have been force-killed; check:
     ```bash
     docker ps -a --filter name=orchestrator- | grep -i exited
     ```

---

### "Permission denied: env_file"

**Error:**
```
PermissionError: [Errno 13] Permission denied: '/home/user/.orchestrator.env'
```

**Cause:** Env file has incorrect permissions (not 600).

**Solution:**

```bash
# Fix permissions
chmod 600 ~/.orchestrator.env

# Verify
ls -la ~/.orchestrator.env
# Output: -rw------- ... .orchestrator.env
```

---

### "Invalid image name"

**Error:**
```
ValueError: Invalid image name: 'AI-SDLC-Orchestrator:latest'
```

**Cause:** Image name contains uppercase or invalid characters.

**Solution:**

1. **Use lowercase image names:**
   ```bash
   orchestrate-container "..." --container-image ai-sdlc-orchestrator:latest
   # Not: AI-SDLC-Orchestrator:latest
   ```

2. **Valid image name patterns:**
   - `myimage` ✅
   - `myrepo/myimage` ✅
   - `myrepo/myimage:v1.0` ✅
   - `localhost:5000/myimage:latest` ✅
   - `MyImage` ❌ (uppercase not allowed)
   - `my@image` ❌ (special chars not allowed)

---

### "Container network isolation fails on macOS"

**Error:**
```
Container can reach google.com (should be blocked)
```

**Cause:** macOS Docker Desktop does not support iptables; network isolation is unavailable.

**Solution:**

1. **Use a proxy-based approach** (recommended):
   ```bash
   # Install mitmproxy
   pip install mitmproxy

   # Start proxy with allowlist
   mitmweb --mode transparent --allowlist "api.anthropic.com" &

   # Add to ~/.orchestrator.env
   echo "HTTPS_PROXY=http://127.0.0.1:8080" >> ~/.orchestrator.env

   # Run containers with proxy
   orchestrate-container "..." --env-file ~/.orchestrator.env
   ```

2. **Or accept reduced security** (for development only):
   ```bash
   orchestrate-container "..." --no-network-isolation --env-file ~/.orchestrator.env
   ```

3. **Switch to Linux** (if available):
   - For maximum security, use a Linux host where iptables rules apply

---

### "AppArmor profile failed to load"

**Error:**
```
$ sudo bash infra/scripts/load-apparmor-profile.sh
AppArmor not available on this system
```

**Cause:** AppArmor is not installed or available on this system.

**Solution:**

1. **Check if AppArmor is available:**
   ```bash
   systemctl is-active apparmor
   # Output: active or inactive
   ```

2. **If AppArmor is inactive:**
   - Install: `sudo apt-get install apparmor apparmor-utils` (Debian/Ubuntu)
   - Enable: `sudo systemctl start apparmor && sudo systemctl enable apparmor`

3. **If on macOS or non-AppArmor system:**
   - AppArmor is not available; skip this step
   - Other security controls (seccomp, capability drops, read-only rootfs) still apply

---

### "Orphaned containers accumulate"

**Error:**
```
$ docker ps -a --filter name=orchestrator- | wc -l
127  # Too many stopped containers
```

**Cause:** Containers exited but were not cleaned up (if --rm flag was not used or failed).

**Solution:**

1. **Remove stopped orchestrator containers:**
   ```bash
   docker container prune --filter "label!=keep" --force
   # Or manually:
   docker ps -a --filter name=orchestrator- --filter status=exited -q | xargs docker rm
   ```

2. **List containers to verify:**
   ```bash
   docker ps -a --filter name=orchestrator- | wc -l
   # Should now be much lower
   ```

3. **Prevent future accumulation:**
   - Ensure `--rm` flag is used (default in build_run_args)
   - Check for subprocess errors that prevent cleanup

---

### "Artifacts not written to host"

**Error:**
```
ls workspace/runs/<run_id>/artifacts/
# ls: cannot access: No such file or directory
```

**Cause:** Container did not complete successfully or bind-mount is misconfigured.

**Diagnosis:**

1. **Check container exit code:**
   ```bash
   # From orchestrate-container output
   # Exit code should be 0 for success
   ```

2. **Check if run directory was created:**
   ```bash
   ls -la workspace/runs/<run_id>/
   # Should contain at least .gitkeep or some files
   ```

3. **Check bind-mount in docker inspect:**
   ```bash
   docker ps --filter name=orchestrator- \
     --format '{{.ID}}' | head -1 | xargs docker inspect | grep -A 5 Mounts
   # Should show: "Source": "/path/to/workspace/runs/<run_id>", "Target": "/workspace/runs/<run_id>"
   ```

**Solutions:**

1. **Verify orchestrator succeeded inside container:**
   ```bash
   # Check Docker logs
   docker logs orchestrator-<run_id> | tail -100
   ```

2. **Manually verify bind-mount:**
   ```bash
   docker run --rm \
     --mount type=bind,source=$(pwd)/workspace/runs/test,target=/workspace/runs/test \
     ai-sdlc-orchestrator:latest \
     sh -c "touch /workspace/runs/test/verify.txt"

   # Check on host
   ls workspace/runs/test/verify.txt
   ```

3. **Check host directory permissions:**
   ```bash
   ls -ld workspace/runs/<run_id>/
   # Should be readable/writable by your user
   ```

---

### "run_id validation fails"

**Error:**
```
ValueError: Invalid run_id format: 'bad-id'
```

**Cause:** run_id does not match `^[0-9a-f]{12}$` pattern (12-character lowercase hex).

**Solution:**

1. **Use auto-generated run_id** (recommended):
   ```bash
   # Omit --run-id; one will be auto-generated
   orchestrate-container "..."
   ```

2. **Or provide valid run_id:**
   ```bash
   # Format: 12-character lowercase hex
   orchestrate-container "..." --run-id 0b60f2baf4b5
   ```

3. **Generate a valid run_id:**
   ```bash
   # Using Python
   import secrets
   run_id = secrets.token_hex(6)  # 6 bytes = 12 hex chars
   echo $run_id
   ```

---

### "Seccomp profile not found"

**Error:**
```
FileNotFoundError: [Errno 2] No such file or directory: '/path/to/seccomp-profile.json'
```

**Cause:** seccomp_profile_path in config does not exist.

**Solution:**

1. **Verify seccomp profile exists:**
   ```bash
   ls -la infra/docker/seccomp-profile.json
   ```

2. **Build the image** (which should include the profile):
   ```bash
   bash infra/scripts/build-image.sh
   ```

3. **Use absolute path in config:**
   ```yaml
   container:
     seccomp_profile_path: /path/to/infra/docker/seccomp-profile.json
   ```

4. **Or omit seccomp_profile_path** (use default Docker seccomp):
   ```yaml
   container:
     seccomp_profile_path: null  # Use Docker default
   ```

---

### "Build image fails"

**Error:**
```
$ bash infra/scripts/build-image.sh
ERROR: failed to solve with frontend dockerfile.v1: invalid reference format
```

**Cause:** VERSION argument or image name is invalid.

**Solution:**

1. **Verify git is initialized:**
   ```bash
   git rev-parse --short HEAD  # Should return a commit SHA
   ```

2. **Manually build with VERSION:**
   ```bash
   docker build -f infra/docker/Dockerfile \
     --build-arg VERSION=1.0.0 \
     -t ai-sdlc-orchestrator:latest \
     .
   ```

3. **Check Dockerfile syntax:**
   ```bash
   docker run --rm -i hadolint/hadolint < infra/docker/Dockerfile
   ```

---

## Performance Issues

### "Orchestration is slow"

**Symptoms:**
- 30+ seconds for each run
- Timeout during agent execution

**Diagnosis:**

1. **Measure startup time:**
   ```bash
   time orchestrate-container "..." --dry-run
   # Compare actual run time with this
   ```

2. **Profile memory usage:**
   ```bash
   docker stats <container_id> --no-stream
   ```

3. **Check network latency:**
   ```bash
   docker run --rm --network orchestrator-net ai-sdlc-orchestrator:latest \
     sh -c "curl -w '@curl-format.txt' -o /dev/null -s https://api.anthropic.com"
   ```

**Solutions:**

1. **Increase resource limits:**
   ```yaml
   container:
     memory_limit: 16g  # From 8g
     cpu_limit: 8.0      # From 4.0
   ```

2. **Use turbo speed mode:**
   ```bash
   orchestrate-container "..." --env-file ~/.orchestrator.env -- --speed turbo
   ```

3. **Check network conditions:**
   ```bash
   # Ping Anthropic API
   curl -s -o /dev/null -w "HTTP %{http_code}, Time: %{time_total}s\n" https://api.anthropic.com
   ```

---

## Log Files & Debugging

### Where to Find Logs

- **Container stderr/stdout:** `docker logs orchestrator-<run_id>`
- **Orchestrator run logs:** `workspace/logs/<run_id>.log`
- **Docker daemon logs:**
  - macOS: Docker Desktop UI > Troubleshoot > Logs
  - Linux: `sudo journalctl -u docker -n 1000 --no-pager`
- **System logs:**
  - macOS: `log stream --predicate 'processImagePath contains "docker"'`
  - Linux: `sudo dmesg | tail -100`

### Enable Debug Logging

```bash
# In container_runner.py, increase logging level
export LOGLEVEL=DEBUG
orchestrate-container "..." --env-file ~/.orchestrator.env
```

### Inspect Container Filesystem

```bash
# Exec into a running container
docker exec -it orchestrator-<run_id> sh

# Check container /etc/hosts (DNS)
cat /etc/hosts | grep api.anthropic.com

# Check mount points
mount | grep workspace

# Check resource limits
cat /sys/fs/cgroup/memory/memory.limit_in_bytes
```

---

## Escalation & Support

If you've tried all solutions above and still have issues:

1. **Collect diagnostics:**
   ```bash
   # Create a diagnostics bundle
   mkdir -p /tmp/orchestrator-debug
   docker ps -a --filter name=orchestrator- > /tmp/orchestrator-debug/containers.txt
   docker images ai-sdlc-orchestrator >> /tmp/orchestrator-debug/images.txt
   docker network ls >> /tmp/orchestrator-debug/networks.txt
   docker version > /tmp/orchestrator-debug/version.txt
   sudo iptables -vnL >> /tmp/orchestrator-debug/iptables.txt 2>/dev/null
   tail -100 workspace/logs/*.log >> /tmp/orchestrator-debug/logs.txt
   ```

2. **File an issue** with:
   - Error message and stack trace
   - Output of diagnostics bundle
   - Steps to reproduce
   - OS and Docker version
   - Container configuration (from config/default.yaml)

3. **Contact the team:**
   - Slack: #orchestrator-support
   - Email: orchestrator-team@example.com

---

## See Also

- [CLAUDE.md — Containerized Orchestration](../CLAUDE.md#containerized-orchestration)
- [CONTAINER_SECURITY_ARCHITECTURE.md — Security Model](./CONTAINER_SECURITY_ARCHITECTURE.md)
- [CONTAINER_API.md — API Reference](./CONTAINER_API.md)
- [Docker Documentation](https://docs.docker.com/)
