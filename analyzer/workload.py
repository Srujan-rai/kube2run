from dataclasses import dataclass
from typing import Optional


@dataclass
class Check:
    id: str
    title: str
    detail: str
    fix: Optional[str]
    kind: str  # "blocker", "warning", "positive", "info"


def analyze(deployment, hpa=None) -> list:
    checks = []
    containers = deployment.containers
    volumes = deployment.volumes
    annotations = deployment.annotations

    for vol in volumes:
        if vol["type"] == "hostPath":
            checks.append(Check(
                id="hostpath_volume",
                title="hostPath volume mount detected",
                detail=f"Volume '{vol['name']}' mounts host path '{vol.get('path', '')}'.",
                fix="Remove hostPath volumes. Cloud Run containers cannot access host filesystem. Use GCS, Cloud SQL, or Memorystore instead.",
                kind="blocker",
            ))
        elif vol["type"] == "pvc":
            checks.append(Check(
                id="pvc_volume",
                title="PersistentVolumeClaim volume detected",
                detail=f"Volume '{vol['name']}' references PVC '{vol.get('claim_name', '')}'.",
                fix="Migrate stateful storage to Cloud Storage (GCS), Cloud SQL, or Firestore. Cloud Run is stateless and cannot mount PVCs.",
                kind="blocker",
            ))
        elif vol["type"] == "emptyDir":
            checks.append(Check(
                id="emptydir_volume",
                title="emptyDir volume detected",
                detail=f"Volume '{vol['name']}' uses emptyDir — ephemeral in-memory or disk storage.",
                fix="emptyDir data is lost on container restart. Cloud Run supports in-memory /tmp up to 32 GiB. Verify no cross-request state is stored here.",
                kind="warning",
            ))
        elif vol["type"] == "configmap":
            checks.append(Check(
                id="configmap_volume_mount",
                title="ConfigMap volume mount detected",
                detail=f"Volume '{vol['name']}' mounts a ConfigMap as a filesystem path.",
                fix="Cloud Run does not support ConfigMap volume mounts. Convert mounted ConfigMap keys to environment variables or store config files in GCS.",
                kind="blocker",
            ))

    if len(containers) > 1:
        checks.append(Check(
            id="multiple_containers",
            title="Multiple containers (sidecar pattern)",
            detail=f"{len(containers)} containers defined: {', '.join(c.name for c in containers)}.",
            fix="Cloud Run 2nd gen supports sidecar containers. Verify sidecars don't require inter-container IPC or shared process namespace.",
            kind="warning",
        ))

    if deployment.init_containers:
        names = ", ".join(c.name for c in deployment.init_containers)
        checks.append(Check(
            id="init_containers",
            title="Init containers detected",
            detail=f"Init containers: {names}. These run before the main container and add to startup time.",
            fix="Cloud Run has a 4-minute startup timeout. Ensure init containers complete well within this budget.",
            kind="warning",
        ))

    for c in containers:
        limits = c.resources.get("limits", {})
        if limits:
            mem_str = limits.get("memory", "")
            mem_mib = _parse_memory_mib(mem_str)
            if mem_mib and mem_mib > 32768:
                checks.append(Check(
                    id="memory_over_limit",
                    title="Memory limit exceeds Cloud Run maximum",
                    detail=f"Container '{c.name}' requests {mem_str} memory. Cloud Run max is 32 GiB.",
                    fix="Reduce memory footprint below 32 GiB, or split into multiple services. Cloud Run max memory is 32 GiB (32768 MiB).",
                    kind="blocker",
                ))

            cpu_str = limits.get("cpu", "")
            cpu_cores = _parse_cpu_cores(cpu_str)
            if cpu_cores and cpu_cores > 8:
                checks.append(Check(
                    id="cpu_over_limit",
                    title="CPU limit exceeds Cloud Run maximum",
                    detail=f"Container '{c.name}' requests {cpu_str} CPU. Cloud Run max is 8 vCPU.",
                    fix="Reduce CPU requirement below 8 vCPU or split compute-intensive work into separate Cloud Run jobs.",
                    kind="blocker",
                ))
        else:
            checks.append(Check(
                id="no_resource_limits",
                title="No resource limits defined",
                detail=f"Container '{c.name}' has no CPU or memory limits.",
                fix="Set --memory and --cpu flags on gcloud run deploy. Cloud Run requires explicit resource allocation.",
                kind="warning",
            ))

        all_ports = c.ports
        if len(all_ports) > 1:
            checks.append(Check(
                id="multiple_ports",
                title="Multiple ports exposed",
                detail=f"Container '{c.name}' exposes {len(all_ports)} ports: {[p['port'] for p in all_ports]}.",
                fix="Cloud Run routes traffic to a single port. Choose one port with --port flag. Other ports are inaccessible.",
                kind="warning",
            ))
        elif len(all_ports) == 1:
            port_num = all_ports[0]["port"]
            if port_num not in (80, 443, 8080, 3000, 5000):
                checks.append(Check(
                    id="nonstandard_port",
                    title="Non-standard container port",
                    detail=f"Container '{c.name}' listens on port {port_num}.",
                    fix=f"Specify --port {port_num} in gcloud run deploy. Cloud Run defaults to 8080.",
                    kind="warning",
                ))

        has_liveness = c.liveness_probe is not None
        has_readiness = c.readiness_probe is not None
        if not has_liveness and not has_readiness:
            checks.append(Check(
                id="no_health_probes",
                title="No liveness or readiness probes defined",
                detail=f"Container '{c.name}' has no health probes.",
                fix="Add a startup probe or configure Cloud Run health checks via --startup-cpu-boost. Cloud Run needs a signal that the container is ready.",
                kind="warning",
            ))

        sc = c.security_context or {}
        if sc.get("privileged"):
            checks.append(Check(
                id="privileged_container",
                title="Privileged container mode enabled",
                detail=f"Container '{c.name}' runs as privileged.",
                fix="Cloud Run does not allow privileged containers. Remove privileged: true. If kernel capabilities are required, this service cannot run on Cloud Run.",
                kind="blocker",
            ))

        for e in c.env:
            if e.get("from") == "configmap":
                checks.append(Check(
                    id="configmap_env_var",
                    title="ConfigMap environment variable reference",
                    detail=f"Container '{c.name}' sources env var '{e['name']}' from ConfigMap '{e.get('ref', '')}'.",
                    fix="Migrate ConfigMap values to plain --set-env-vars in Cloud Run or store sensitive values in Secret Manager.",
                    kind="warning",
                ))
            elif e.get("from") == "secret":
                checks.append(Check(
                    id="secret_env_var",
                    title="Secret environment variable mapped",
                    detail=f"Container '{c.name}' sources env var '{e['name']}' from Secret '{e.get('ref', '')}'.",
                    fix="Migrate Kubernetes Secret to Google Secret Manager. Use --set-secrets flag in gcloud run deploy.",
                    kind="positive",
                ))

        name_lower = c.name.lower()
        image_lower = c.image.lower()
        if any(r in image_lower for r in [".dkr.ecr.", "azurecr.io"]):
            checks.append(Check(
                id="non_gcr_registry",
                title="Image hosted on non-GCP registry",
                detail=f"Container '{c.name}' image '{c.image}' is on ECR or ACR.",
                fix="Push image to Google Artifact Registry: docker pull <image> && docker tag <image> <region>-docker.pkg.dev/<project>/<repo>/<name> && docker push ...",
                kind="warning",
            ))

    if deployment.replicas > 10:
        checks.append(Check(
            id="high_static_replicas",
            title="High static replica count",
            detail=f"Deployment has {deployment.replicas} replicas configured statically.",
            fix="Cloud Run auto-scales. Set --min-instances for baseline capacity and remove static replica tuning.",
            kind="warning",
        ))

    spec_annotations = annotations
    if spec_annotations.get("prometheus.io/scrape") == "true":
        checks.append(Check(
            id="prometheus_scrape",
            title="Prometheus scrape annotation present",
            detail="Deployment is annotated for Prometheus scraping (prometheus.io/scrape: 'true').",
            fix="Cloud Run doesn't support Prometheus scraping natively. Use Cloud Monitoring with --set-env-vars or OpenTelemetry sidecar.",
            kind="warning",
        ))

    if hpa:
        checks.append(Check(
            id="hpa_present",
            title="HPA defined — auto-scaling ready",
            detail=f"HPA '{hpa.name}' scales {hpa.min_replicas}–{hpa.max_replicas} replicas.",
            fix=None,
            kind="positive",
        ))

    if getattr(deployment, "host_network", False):
        checks.append(Check(
            id="host_network",
            title="hostNetwork: true detected",
            detail="Pod uses host network namespace.",
            fix="Cloud Run containers run in isolated network namespaces. Remove hostNetwork: true. This is a hard blocker.",
            kind="blocker",
        ))

    tgp = getattr(deployment, "termination_grace_period_seconds", 30)
    if tgp > 300:
        checks.append(Check(
            id="termination_grace_period_long",
            title="Termination grace period exceeds Cloud Run maximum",
            detail=f"terminationGracePeriodSeconds is {tgp}s. Cloud Run max is 300s.",
            fix="Reduce terminationGracePeriodSeconds to ≤300. Cloud Run sends SIGTERM and waits up to 300s before force-killing.",
            kind="warning",
        ))

    for c in containers:
        for e in c.env:
            if e.get("from") in ("fieldRef", "resourceFieldRef"):
                checks.append(Check(
                    id="downward_api_env_var",
                    title="Downward API environment variable detected",
                    detail=f"Container '{c.name}' uses '{e['name']}' sourced from {e['from']} ('{e.get('ref', '')}').",
                    fix="Cloud Run does not support Kubernetes Downward API. Replace with static env vars or use the metadata server (metadata.google.internal) for project/region info.",
                    kind="warning",
                ))
            break

    all_env_count = sum(len(c.env) for c in containers)
    if all_env_count > 50:
        checks.append(Check(
            id="large_env_var_count",
            title="High environment variable count",
            detail=f"{all_env_count} env vars across containers. Cloud Run has a 32 KiB total env var size limit.",
            fix="Consolidate config into fewer env vars, use Secret Manager for sensitive values, or load config from GCS at startup.",
            kind="warning",
        ))

    if not hpa:
        if deployment.replicas > 1:
            checks.append(Check(
                id="no_hpa",
                title="No HPA — static replica count",
                detail=f"Deployment has {deployment.replicas} static replicas with no autoscaling.",
                fix="Cloud Run auto-scales by default. Set --min-instances and --max-instances instead of managing replicas manually.",
                kind="warning",
            ))

    for c in containers:
        image = c.image or ""
        tag = image.split(":")[-1] if ":" in image else "latest"
        if tag in ("latest", "") or not tag:
            checks.append(Check(
                id="latest_image_tag",
                title="Image uses :latest or untagged",
                detail=f"Container '{c.name}' image '{image}' is not pinned to a specific digest or version tag.",
                fix="Pin to a specific tag or digest (e.g. image@sha256:...). Cloud Run caches images — :latest may not pull the newest version on redeploy without --no-traffic.",
                kind="warning",
            ))

    return checks


def _parse_memory_mib(mem_str: str) -> Optional[float]:
    if not mem_str:
        return None
    mem_str = str(mem_str).strip()
    try:
        if mem_str.endswith("Gi"):
            return float(mem_str[:-2]) * 1024
        elif mem_str.endswith("Mi"):
            return float(mem_str[:-2])
        elif mem_str.endswith("G"):
            return float(mem_str[:-1]) * 1000
        elif mem_str.endswith("M"):
            return float(mem_str[:-1])
        elif mem_str.endswith("Ki"):
            return float(mem_str[:-2]) / 1024
        else:
            return float(mem_str) / (1024 * 1024)
    except ValueError:
        return None


def _parse_cpu_cores(cpu_str: str) -> Optional[float]:
    if not cpu_str:
        return None
    cpu_str = str(cpu_str).strip()
    try:
        if cpu_str.endswith("m"):
            return float(cpu_str[:-1]) / 1000
        return float(cpu_str)
    except ValueError:
        return None
