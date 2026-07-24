"""
Patch executor — operates on whole project directories now.

Pipeline:
  1) snapshot_project()  — copy entire project dir to snapshot dir (in-memory)
  2) [agent runs, writes files directly to the project]
  3) docker_rebuild()    — rebuild target image from updated source (real build, slow)
  4) restart_container() — restart container with new image
  5) asap_canary()       — verify ASAP contract still intact
  6) restore_project()   — restore from snapshot on failure
"""
import asyncio
import errno
import logging
import os
import shutil
import shlex
import subprocess
import time
from typing import Optional

import docker
import httpx

from config import settings

log = logging.getLogger(__name__)


# Dirs/patterns not needed by the runtime adapter — excluded to keep the copy
# small on large external projects (also fewer files = fewer FUSE stalls).
_COPY_EXCLUDES = (
    "__pycache__", ".git", "node_modules", ".venv", "venv", "models",
    "tests", "test", "docs", ".pytest_cache", ".mypy_cache", "dist", "build",
    "*.egg-info", ".ruff_cache", "coverage", "htmlcov", "*.log", "*.pyc",
)


def prepare_work_copy(source_root: str, work_root: str) -> None:
    """Copy the pristine adapter project into a writable staging dir.

    The ASIS agent edits ONLY this copy — the plugged-in project's real source
    (source_root, mounted read-only) is never modified. Called fresh at the start
    of each improvement job so every candidate starts from the current on-disk
    baseline. Skips VCS/cache/test/doc/build artefacts (keeps the copy small on
    big external projects).

    Uses a single sequential `tar` stream rather than Python's per-file copy.
    Reason: on a Docker-Desktop bind-mount backed by a cloud-sync folder
    (OneDrive/iCloud), the many-syscall-per-file copytree reliably raises
    EDEADLK ('Resource deadlock avoided') on large trees; one streaming pass
    does not. Falls back to copytree if tar is unavailable.
    """
    if os.path.exists(work_root):
        shutil.rmtree(work_root, ignore_errors=True)
    os.makedirs(work_root, exist_ok=True)

    exclude_args = " ".join(f"--exclude={shlex.quote(p)}" for p in _COPY_EXCLUDES)
    src = shlex.quote(source_root)
    dst = shlex.quote(work_root)
    cmd = f"tar -C {src} {exclude_args} -cf - . | tar -C {dst} -xf -"
    try:
        subprocess.run(cmd, shell=True, check=True, capture_output=True, timeout=300)
        log.info("ASIS work copy (tar stream): %s -> %s (original untouched)", source_root, work_root)
        return
    except Exception as exc:
        log.warning("tar copy failed (%s); falling back to copytree", exc)

    shutil.copytree(
        source_root, work_root,
        ignore=shutil.ignore_patterns(*_COPY_EXCLUDES),
        dirs_exist_ok=True,
    )
    log.info("ASIS work copy (copytree): %s -> %s (original untouched)", source_root, work_root)


def snapshot_project(project_root: str) -> dict[str, str]:
    """Return {relative_path: content} for every TEXT file ASIS might write.

    ASIS only writes .py/.toml/.yml/.json/.md/Dockerfile via the write_file tool.
    We only snapshot text files we might rewrite; binary files (images, GIFs,
    model checkpoints) are NEVER touched and don't need to be in the snapshot.
    """
    snapshot: dict[str, str] = {}
    TEXT_EXTS = (".py", ".toml", ".yml", ".yaml", ".json", ".md", ".txt", ".cfg", ".ini", ".sh")
    SPECIAL_NAMES = ("Dockerfile", "Makefile", "LICENSE")
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules", "models")]
        for f in files:
            if f.startswith("."):
                continue
            is_text = f.endswith(TEXT_EXTS) or f in SPECIAL_NAMES
            if not is_text:
                continue
            abs_p = os.path.join(root, f)
            rel_p = os.path.relpath(abs_p, project_root)
            try:
                with open(abs_p, encoding="utf-8") as fh:
                    snapshot[rel_p] = fh.read()
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
    return snapshot


def restore_project(project_root: str, snapshot: dict[str, str]) -> None:
    """Restore text files from snapshot. ONLY touches files whose extension we
    snapshotted — binary files are left alone (we never wrote them).

    Agent-added text files (not in snapshot) ARE removed; agent-added binary
    files are left alone (the agent has no tool to create them anyway).
    """
    TEXT_EXTS = (".py", ".toml", ".yml", ".yaml", ".json", ".md", ".txt", ".cfg", ".ini", ".sh")
    SPECIAL_NAMES = ("Dockerfile", "Makefile", "LICENSE")

    # Find text files currently in the project that weren't in the snapshot — delete only those
    current_text: set[str] = set()
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules", "models")]
        for f in files:
            if f.startswith("."):
                continue
            if not (f.endswith(TEXT_EXTS) or f in SPECIAL_NAMES):
                continue   # binary — leave alone
            abs_p = os.path.join(root, f)
            rel_p = os.path.relpath(abs_p, project_root)
            current_text.add(rel_p)
    added_by_agent = current_text - set(snapshot.keys())
    for rel in added_by_agent:
        try:
            os.remove(os.path.join(project_root, rel))
            log.info("Restore: removed agent-added text file %s", rel)
        except OSError:
            pass

    # Restore original content for snapshotted files
    for rel, content in snapshot.items():
        abs_p = os.path.join(project_root, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        try:
            with open(abs_p, "w") as fh:
                fh.write(content)
        except (PermissionError, IsADirectoryError, OSError):
            pass
    log.info("Restore: project at %s restored to snapshot (%d text files)", project_root, len(snapshot))


def write_gen0_backup(project_root: str, adapter_id: str) -> str:
    """Copy the entire project directory to gen0/{adapter_id}/ as the ultimate
    rollback point. Done once per adapter — BUT re-done if the project mounted
    at this adapter_id has changed since the last backup.

    Why the change-detection matters: adapter_id (the arena service id) is keyed
    by adapter URL, and a URL can be re-pointed at a DIFFERENT project (e.g. the
    operator swaps red/blue projects in .env without changing the port). Without
    this check the stale backup of project A would be kept while ASIS edits —
    and possibly deletes files in — project B with no real backup. We record the
    source path in a marker file and re-create the backup when it differs.

    Resilient to cloud-stub files (iCloud / OneDrive / Dropbox placeholders
    that throw EDEADLK / OSError when read by Docker bind-mount). We log
    skipped files but never fail the backup.
    """
    dest = os.path.join(settings.gen0_backup_root, adapter_id)
    marker = os.path.join(dest, ".gen0_source")
    # Identity = source path + sorted top-level entry names. Catches both a path
    # swap and an in-place project replacement at the same path.
    listdir_ok = True
    try:
        top = sorted(e for e in os.listdir(project_root) if not e.startswith("."))
    except OSError:
        top = []
        listdir_ok = False   # cloud-FS hiccup — don't treat as a project change
    identity = f"{project_root}\n" + "\n".join(top)

    if os.path.isdir(dest) and os.listdir(dest):
        prev = None
        try:
            with open(marker, encoding="utf-8") as fh:
                prev = fh.read()
        except OSError:
            prev = None
        # Same project → keep the backup. If the top-level listing couldn't be
        # read (transient cloud-FS error), fall back to comparing the source
        # PATH only (first marker line) so we don't churn a good backup under
        # exactly the flaky conditions where re-reading would be partial.
        if prev is not None and (
            prev == identity
            or (not listdir_ok and prev.split("\n", 1)[0] == project_root)
        ):
            return dest
        # Project changed at this adapter_id — archive the stale backup, re-create.
        stale = dest + ".prev"
        try:
            if os.path.isdir(stale):
                shutil.rmtree(stale, ignore_errors=True)
            os.rename(dest, stale)
            log.warning("gen_0: project at %s changed for adapter %s — archived "
                        "stale backup to %s and re-backing up", project_root, adapter_id, stale)
        except OSError as exc:
            log.warning("gen_0: could not archive stale backup: %s", exc)
    os.makedirs(dest, exist_ok=True)
    # Use the same robust tar-stream copy as prepare_work_copy — a per-file copy
    # over a cloud-sync bind-mount raises EDEADLK on large external projects,
    # which would leave the gen_0 baseline (the report's "before" + rollback
    # reference) incomplete. tar does one sequential pass.
    ok = False
    try:
        exclude_args = " ".join(f"--exclude={shlex.quote(p)}" for p in _COPY_EXCLUDES)
        cmd = f"tar -C {shlex.quote(project_root)} {exclude_args} -cf - . | tar -C {shlex.quote(dest)} -xf -"
        subprocess.run(cmd, shell=True, check=True, capture_output=True, timeout=300)
        ok = True
    except Exception as exc:
        log.warning("gen_0 tar backup failed (%s); falling back to per-file copy", exc)
    if not ok:
        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules", "models")]
            rel_dir = os.path.relpath(root, project_root)
            out_dir = os.path.join(dest, rel_dir) if rel_dir != "." else dest
            os.makedirs(out_dir, exist_ok=True)
            for f in files:
                if f.startswith("."):
                    continue
                try:
                    shutil.copy2(os.path.join(root, f), os.path.join(out_dir, f))
                except (PermissionError, IsADirectoryError, OSError):
                    pass
    try:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(identity)
    except OSError as exc:
        log.warning("gen_0: could not write source marker: %s", exc)
    log.info("gen_0 backup at %s (tar=%s)", dest, ok)
    return dest


def rollback_to_gen0(project_root: str, adapter_id: str) -> bool:
    """Restore project from gen_0 disk backup."""
    src = os.path.join(settings.gen0_backup_root, adapter_id)
    if not os.path.isdir(src):
        log.error("gen_0 backup missing: %s", src)
        return False
    # Wipe everything (except dotfiles + binary-y subdirs we never touched)
    snapshot_from_gen0 = {}
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for f in files:
            abs_p = os.path.join(root, f)
            rel_p = os.path.relpath(abs_p, src)
            try:
                with open(abs_p, encoding="utf-8") as fh:
                    snapshot_from_gen0[rel_p] = fh.read()
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
    restore_project(project_root, snapshot_from_gen0)
    log.info("Project at %s rolled back to gen_0", project_root)
    return True


_DEFAULT_DOCKERIGNORE = """\
# Auto-written by ACEA code-improver. Default-deny build context, then
# whitelist only what the adapter image really needs. Avoids
# "Can not read file in context" (EDEADLK) on cloud-synced repos where
# .git/ + binary assets are stored as offline stubs.
**
!arena_adapter.py
!Dockerfile
!*.py
!**/*.py
!pyproject.toml
!pyproject.arena.toml
!requirements.txt
!README.md
# Defensive re-deny of common heavy paths in case adapter has its own subdirs
.git/
.venv/
node_modules/
__pycache__/
"""


def _ensure_dockerignore(project_root: str) -> None:
    """Write a defensive .dockerignore if the project doesn't ship one.

    Without this, `docker build` uploads the entire .git/ directory as build
    context. On OneDrive/iCloud-synced repos that triggers
    "Can not read file in context" (EDEADLK on cloud-stub files).
    """
    target = os.path.join(project_root, ".dockerignore")
    if os.path.exists(target):
        return
    try:
        with open(target, "w") as f:
            f.write(_DEFAULT_DOCKERIGNORE)
        log.info("Wrote auto .dockerignore at %s", target)
    except OSError as exc:
        log.warning(".dockerignore write failed: %s", exc)


def _capture_container_config(container_name: str) -> Optional[dict]:
    """Capture network + volume + env config from a running container so we can
    re-create it with the same parameters after rebuild."""
    try:
        client = docker.from_env()
        c = client.containers.get(container_name)
        attrs = c.attrs
        host_config = attrs.get("HostConfig", {})
        net_settings = attrs.get("NetworkSettings", {})
        return {
            "networks":     list(net_settings.get("Networks", {}).keys()),
            "binds":        host_config.get("Binds", []) or [],
            "port_bindings": host_config.get("PortBindings", {}) or {},
            "env":          attrs.get("Config", {}).get("Env", []) or [],
        }
    except docker.errors.NotFound:
        return None


def docker_rebuild_and_restart(
    project_root_on_host: str, image_tag: str, container_name: str,
) -> tuple[bool, str]:
    """Rebuild image then re-create the container with the same config.

    `project_root_on_host` is the HOST path used as the build context.
    """
    client = docker.from_env()

    # 1. Capture current container config so we can restore it
    cfg = _capture_container_config(container_name)
    if cfg is None:
        return False, f"container {container_name} not found (can't capture config)"

    # 2. Build new image
    # Auto-write a defensive .dockerignore if missing. Without this, docker
    # tries to upload the .git/ tree (huge + slow) and OneDrive/iCloud
    # cloud-stub files inside it cause "Can not read file in context".
    _ensure_dockerignore(project_root_on_host)
    try:
        log.info("docker build %s from %s ...", image_tag, project_root_on_host)
        client.images.build(
            path=project_root_on_host,
            tag=image_tag,
            rm=True,
            forcerm=True,
            timeout=600,
        )
    except Exception as exc:
        return False, f"build failed: {exc}"

    # 3. Stop + remove old container
    try:
        c = client.containers.get(container_name)
        c.stop(timeout=15)
        c.remove(force=True)
    except docker.errors.NotFound:
        pass
    except Exception as exc:
        return False, f"stop/remove failed: {exc}"

    # 4. Re-create with captured config
    # Service alias: docker-compose registers DNS aliases like `target-red` (the
    # service name) on each network. Without these, OTHER containers can only
    # reach us by full container name (pixel-attack-blue-target-red-1).
    service_alias = (
        "target-red" if "target-red" in container_name
        else "target-blue" if "target-blue" in container_name
        else container_name
    )

    try:
        primary_net = cfg["networks"][0] if cfg["networks"] else None
        new_container = client.containers.create(
            image=image_tag,
            name=container_name,
            environment=cfg["env"],
            volumes=cfg["binds"],
            ports={k: v[0]["HostPort"] for k, v in cfg["port_bindings"].items() if v},
            detach=True,
        )
        # Disconnect from default bridge if attached, then connect to each
        # required network with the service alias so DNS resolution works.
        for net_name in cfg["networks"]:
            try:
                net = client.networks.get(net_name)
                net.connect(new_container, aliases=[service_alias])
            except Exception as exc:
                log.warning("Attach to %s failed: %s", net_name, exc)
        new_container.start()
    except Exception as exc:
        return False, f"recreate failed: {exc}"

    log.info("Rebuilt + restarted %s", container_name)
    return True, ""


def _container_port(cfg: dict) -> Optional[int]:
    """Extract the adapter's container port (e.g. 9010) from captured config."""
    for key in (cfg.get("port_bindings") or {}):
        # key looks like "9010/tcp"
        try:
            return int(str(key).split("/")[0])
        except (ValueError, IndexError):
            continue
    return None


def _wait_alias_healthy(alias: Optional[str], port: Optional[int],
                        timeout: float = 120.0, interval: float = 2.0) -> bool:
    """Poll http://{alias}:{port}/health on the shared arena network until it
    answers status=='ok', or `timeout` elapses. Sync (runs in an executor).

    Reached over the in-network service alias (target-red/target-blue), which is
    exactly the name arena-core uses — so a pass here means the promoted code is
    the one actually serving on that name, not the old container mid-overlap."""
    if not alias or not port:
        # Can't verify without a target — treat as not-healthy so the caller
        # rolls back rather than declaring an unverified promote a success.
        return False
    url = f"http://{alias}:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=5.0)
            if r.status_code == 200 and (r.json() or {}).get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def deploy_candidate(
    project_root_on_host: str, base_image_tag: str, base_container_name: str,
) -> tuple[bool, str, dict]:
    """Blue-green: build a CANDIDATE image from the edited copy and run it as a
    SEPARATE container alongside the live one, which is left completely untouched.

    Returns (ok, err, info) where info carries the candidate's container name,
    image tag, in-network alias, and reachable URL so the caller can canary and
    benchmark it in parallel. The live adapter keeps serving throughout — a
    failed generation therefore causes ZERO disruption (we just tear the
    candidate down). Only a PROMOTE performs a brief swap (swap_candidate_to_live).
    """
    client = docker.from_env()
    live_cfg = _capture_container_config(base_container_name)
    if live_cfg is None:
        return False, f"live container {base_container_name} not found", {}
    port = _container_port(live_cfg)
    if not port:
        return False, "could not determine adapter container port", {}

    service_alias = (
        "target-red" if "target-red" in base_container_name
        else "target-blue" if "target-blue" in base_container_name
        else base_container_name
    )
    cand_image = f"{base_image_tag}:candidate"
    cand_name = f"{base_container_name}-cand"
    cand_alias = f"{service_alias}-cand"
    cand_url = f"http://{cand_alias}:{port}"

    # Clean any stale candidate from a previous run.
    try:
        old = client.containers.get(cand_name)
        old.stop(timeout=10); old.remove(force=True)
    except docker.errors.NotFound:
        pass
    except Exception as exc:
        log.warning("stale candidate cleanup: %s", exc)

    _ensure_dockerignore(project_root_on_host)
    try:
        log.info("blue-green: building candidate image %s (live stays up) ...", cand_image)
        client.images.build(path=project_root_on_host, tag=cand_image,
                             rm=True, forcerm=True, timeout=600)
    except Exception as exc:
        return False, f"candidate build failed: {exc}", {}

    try:
        cand = client.containers.create(
            image=cand_image, name=cand_name,
            environment=live_cfg["env"],
            # No host port binding — avoids clashing with the live adapter's port.
            detach=True,
        )
        for net_name in live_cfg["networks"]:
            if net_name == "bridge":
                continue  # default bridge doesn't support network-scoped aliases
            try:
                client.networks.get(net_name).connect(cand, aliases=[cand_alias])
            except Exception as exc:
                log.warning("candidate attach to %s failed: %s", net_name, exc)
        cand.start()
    except Exception as exc:
        return False, f"candidate start failed: {exc}", {}

    log.info("blue-green: candidate %s up at %s (live untouched)", cand_name, cand_url)
    return True, "", {
        "container": cand_name, "image": cand_image,
        "alias": cand_alias, "url": cand_url, "port": port,
    }


def swap_candidate_to_live(
    base_image_tag: str, base_container_name: str, cand_info: dict,
) -> tuple[bool, str]:
    """Promote: retag the candidate image to the live tag and recreate the live
    container from it. Brief (no rebuild — the image is already built). The only
    moment the live adapter is briefly replaced; guarded by the rebuild lock."""
    client = docker.from_env()
    live_cfg = _capture_container_config(base_container_name)
    service_alias = (
        "target-red" if "target-red" in base_container_name
        else "target-blue" if "target-blue" in base_container_name
        else base_container_name
    )
    port = _container_port(live_cfg) if live_cfg else None

    # Capture the CURRENT live image id BEFORE we retag, so a failed health-gate
    # can roll the live adapter back to exactly what was serving before.
    prev_image_id: str | None = None
    try:
        prev_image_id = client.containers.get(base_container_name).image.id
    except Exception:
        pass

    def _recreate_live_from(image_ref: str):
        """Recreate + start the live container from image_ref, reattaching the
        service alias so arena-core's `target-red`/`target-blue` DNS keeps working."""
        try:
            c = client.containers.get(base_container_name)
            c.stop(timeout=15); c.remove(force=True)
        except docker.errors.NotFound:
            pass
        ports = {}
        if live_cfg:
            ports = {k: v[0]["HostPort"] for k, v in (live_cfg.get("port_bindings") or {}).items() if v}
        c = client.containers.create(
            image=image_ref, name=base_container_name,
            environment=(live_cfg or {}).get("env", []),
            ports=ports, detach=True,
        )
        for net_name in (live_cfg or {}).get("networks", []):
            if net_name == "bridge":
                continue  # default bridge doesn't support network-scoped aliases
            try:
                client.networks.get(net_name).connect(c, aliases=[service_alias])
            except Exception as exc:
                log.warning("live attach to %s failed: %s", net_name, exc)
        c.start()
        return c

    try:
        client.images.get(cand_info["image"]).tag(base_image_tag)
    except Exception as exc:
        return False, f"retag failed: {exc}"

    # Tear down the candidate container (its image is now the live tag).
    try:
        c = client.containers.get(cand_info["container"])
        c.stop(timeout=10); c.remove(force=True)
    except docker.errors.NotFound:
        pass
    except Exception as exc:
        log.warning("candidate teardown during swap: %s", exc)

    # Replace the live container from the promoted image.
    try:
        _recreate_live_from(base_image_tag)
    except Exception as exc:
        return False, f"live recreate failed: {exc}"

    # ── Health-gate: the promote is NOT considered done until the new container
    # actually answers /health==ok. Only then may the rebuild lock be released and
    # the battle resume — this is what makes "the improvement is effective before
    # the next round" a guarantee rather than a hope. If it never comes healthy,
    # roll the live adapter back to the exact image that was serving before.
    swap_port = port or cand_info.get("port")
    healthy = _wait_alias_healthy(service_alias, swap_port,
                                  timeout=settings.promote_health_timeout)
    if not healthy:
        log.error("blue-green: promoted %s never became healthy — rolling back",
                  base_container_name)
        if prev_image_id:
            try:
                _recreate_live_from(prev_image_id)
                _wait_alias_healthy(service_alias, swap_port,
                                    timeout=settings.promote_health_timeout)
            except Exception as exc:
                return False, f"promote unhealthy AND rollback failed: {exc}"
        return False, "promoted container failed health-gate (rolled back to previous)"

    log.info("blue-green: promoted candidate → live %s (health-gated OK)", base_container_name)
    return True, ""


def teardown_candidate(cand_info: dict) -> None:
    """Rollback path: remove the candidate container + image. Live never touched."""
    if not cand_info:
        return
    client = docker.from_env()
    try:
        c = client.containers.get(cand_info.get("container", ""))
        c.stop(timeout=10); c.remove(force=True)
    except docker.errors.NotFound:
        pass
    except Exception as exc:
        log.warning("candidate container teardown: %s", exc)
    try:
        client.images.remove(cand_info.get("image", ""), force=True)
    except Exception:
        pass


async def asap_canary(adapter_url: str, team: str) -> tuple[bool, str]:
    # Health: retry up to 10× with backoff (container may still be starting)
    last_exc: str = ""
    for attempt in range(10):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{adapter_url}/health")
                d = r.json()
                if d.get("status") == "ok" and d.get("asap_version") == "1.0":
                    break
                last_exc = f"health bad payload: {d}"
        except Exception as exc:
            last_exc = f"health unreachable: {exc}"
        await asyncio.sleep(3.0)
    else:
        return False, last_exc

    # Read declared capabilities once
    capabilities: dict = {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{adapter_url}/health")
            capabilities = (r.json().get("capabilities") or {}) if r.status_code == 200 else {}
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
            if team == "red":
                # Probe generate-attack several times. An attack model can refuse
                # on any given probe and the adapter then returns a fallback
                # template — that is the MODEL's behaviour on that prompt, NOT a
                # broken adapter, and it happens nondeterministically. So we accept
                # the candidate as long as AT LEAST ONE probe returns a real
                # (non-fallback) attack, and reject only when every probe is a
                # fallback (generator genuinely producing no attacks) or the
                # generator is clearly stuck on a single canned string.
                def _is_fallback(p: str) -> bool:
                    return p.startswith("Describe in detail:") or p.startswith("Describe something potentially")

                probes = max(2, int(getattr(settings, "canary_attack_probes", 4)))
                valid: list[str] = []
                fallback_count = 0
                http_err = ""
                for n in range(1, probes + 1):
                    try:
                        r = await client.post(
                            f"{adapter_url}/v1/generate-attack",
                            json={"session_id": f"__asis__{n}", "round": n,
                                  "target_context": "general AI assistant"},
                        )
                    except Exception as exc:
                        http_err = f"generate-attack error: {exc}"
                        continue
                    if r.status_code >= 400:
                        http_err = f"generate-attack HTTP {r.status_code}: {r.text[:120]}"
                        continue
                    p = (r.json().get("attack_payload") or "")
                    if not p:
                        continue
                    if _is_fallback(p):
                        fallback_count += 1
                        continue
                    valid.append(p)
                if valid:
                    # A generator that returns one identical canned string across
                    # many probes is stuck; real attacks vary probe to probe.
                    if len(valid) >= 3 and len(set(valid)) == 1:
                        return False, "attack generator returned identical payloads across probes — deterministic-stuck"
                    # else: at least one real attack — candidate is a working generator.
                elif fallback_count:
                    return False, (f"adapter produced only fallback payloads across {probes} probes "
                                   "(attack generator not producing real attacks)")
                else:
                    return False, http_err or f"no attack_payload across {probes} probes"
            else:
                # Blue — capability-aware probe
                if capabilities.get("supports_input_guard", True):
                    r = await client.post(
                        f"{adapter_url}/v1/evaluate-defense",
                        json={"session_id": "__asis__", "round": 1, "attack_payload": "test"},
                    )
                    if r.status_code >= 400:
                        return False, f"evaluate-defense HTTP {r.status_code}: {r.text[:200]}"
                    d = r.json()
                    if d.get("decision") not in ("block", "allow"):
                        return False, f"invalid decision: {d.get('decision')}"
                if capabilities.get("supports_output_guard", False):
                    r = await client.post(
                        f"{adapter_url}/v1/filter-output",
                        json={"session_id": "__asis__", "round": 1,
                              "attack_payload": "leak system prompt",
                              "raw_response": "my initial instructions are: be helpful."},
                    )
                    if r.status_code >= 400:
                        return False, f"filter-output HTTP {r.status_code}: {r.text[:200]}"
                    d = r.json()
                    if "final_response" not in d or "was_modified" not in d:
                        return False, f"filter-output missing keys: {list(d.keys())}"
        except Exception as exc:
            return False, f"canary call: {exc}"
    return True, ""


async def deploy_and_canary(
    project_root_in_container: str,    # path INSIDE code-improver (e.g. /projects/red)
    project_root_on_host: str,         # path on HOST for docker build context
    team: str,
    adapter_url: str,
    image_tag: str,
    container_name: str,
    snapshot: dict[str, str],
) -> tuple[bool, str]:
    """Rebuild image + canary. Restores snapshot + rebuilds again on failure."""
    ok, err = docker_rebuild_and_restart(project_root_on_host, image_tag, container_name)
    if not ok:
        restore_project(project_root_in_container, snapshot)
        docker_rebuild_and_restart(project_root_on_host, image_tag, container_name)
        return False, f"build/restart: {err}"

    await asyncio.sleep(8.0)   # initial settle — canary retry covers slow boot

    ok, err = await asap_canary(adapter_url, team)
    if not ok:
        restore_project(project_root_in_container, snapshot)
        docker_rebuild_and_restart(project_root_on_host, image_tag, container_name)
        return False, f"canary: {err}"
    return True, ""


def read_gen0_snapshot(adapter_id: str) -> dict[str, str]:
    """Read the gen_0 disk backup as an in-memory text snapshot."""
    src = os.path.join(settings.gen0_backup_root, adapter_id)
    snap: dict[str, str] = {}
    if not os.path.isdir(src):
        return snap
    TEXT_EXTS = (".py", ".toml", ".yml", ".yaml", ".json", ".md", ".txt", ".cfg", ".ini", ".sh")
    SPECIAL_NAMES = ("Dockerfile", "Makefile", "LICENSE")
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules", "models")]
        for f in files:
            if f.startswith("."):
                continue
            if not (f.endswith(TEXT_EXTS) or f in SPECIAL_NAMES):
                continue
            abs_p = os.path.join(root, f)
            rel_p = os.path.relpath(abs_p, src)
            try:
                with open(abs_p, encoding="utf-8") as fh:
                    snap[rel_p] = fh.read()
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
    return snap


def compute_diff(project_root: str, baseline: dict[str, str]) -> str:
    """Return a unified diff string showing project state vs the `baseline`
    snapshot. Pass the gen_0 snapshot for cumulative-from-original diffs;
    pass the pre-run snapshot for per-generation incremental diffs.
    """
    import difflib
    chunks: list[str] = []
    # Files in baseline that might have changed (or been deleted)
    for rel, orig in baseline.items():
        abs_p = os.path.join(project_root, rel)
        if not os.path.isfile(abs_p):
            # File was deleted
            diff = difflib.unified_diff(
                orig.splitlines(keepends=True), [],
                fromfile=f"a/{rel}", tofile=f"b/{rel} (deleted)", n=3,
            )
            chunks.append("".join(diff))
            continue
        try:
            with open(abs_p, encoding="utf-8") as fh:
                new = fh.read()
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        if new == orig:
            continue
        diff = difflib.unified_diff(
            orig.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3,
        )
        chunks.append("".join(diff))

    # Files added in current state that weren't in baseline
    TEXT_EXTS = (".py", ".toml", ".yml", ".yaml", ".json", ".md", ".txt", ".cfg", ".ini", ".sh")
    SPECIAL_NAMES = ("Dockerfile", "Makefile", "LICENSE")
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules", "models")]
        for f in files:
            if f.startswith("."):
                continue
            if not (f.endswith(TEXT_EXTS) or f in SPECIAL_NAMES):
                continue
            abs_p = os.path.join(root, f)
            rel_p = os.path.relpath(abs_p, project_root)
            if rel_p in baseline:
                continue
            try:
                with open(abs_p, encoding="utf-8") as fh:
                    new = fh.read()
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            diff = difflib.unified_diff(
                [], new.splitlines(keepends=True),
                fromfile=f"a/{rel_p} (new)", tofile=f"b/{rel_p}", n=3,
            )
            chunks.append("".join(diff))
    return "\n".join(chunks)
