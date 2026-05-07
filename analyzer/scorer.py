from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ServiceResult:
    name: str
    namespace: str
    image: str
    replicas: int
    memory: str
    cpu: str
    score: int
    verdict: str
    workload_checks: list
    traffic_checks: list
    network_checks: list
    blockers: list
    warnings: list
    passed: list
    gcloud_command: str
    hpa_min: int = 0
    hpa_max: int = 10
    port: int = 8080


def score_and_build(deployment, workload_checks, traffic_checks, network_checks,
                    hpa=None, service=None, network_policies=None, region="us-central1",
                    project="YOUR_PROJECT") -> ServiceResult:

    all_checks = workload_checks + traffic_checks + network_checks

    blockers = [c for c in all_checks if c.kind == "blocker"]
    warnings = [c for c in all_checks if c.kind == "warning"]
    passed = [c for c in all_checks if c.kind == "positive"]

    score = 100 - (len(blockers) * 25) - (len(warnings) * 8)
    score = max(0, score)

    if score >= 80:
        verdict = "READY"
    elif score >= 60:
        verdict = "MOSTLY READY"
    elif score >= 35:
        verdict = "NEEDS WORK"
    else:
        verdict = "NOT READY"

    container = deployment.containers[0] if deployment.containers else None
    memory = "512Mi"
    cpu = "1"
    port = 8080
    if container:
        limits = container.resources.get("limits", {})
        memory = limits.get("memory", "512Mi") or "512Mi"
        cpu = limits.get("cpu", "1") or "1"
        if container.ports:
            port = container.ports[0]["port"]

    hpa_min = 0
    hpa_max = 10
    if hpa:
        hpa_min = hpa.min_replicas or 0
        hpa_max = hpa.max_replicas or 10

    concurrency = 80
    mem_mib = _parse_memory_mib(memory)
    if mem_mib and mem_mib >= 8192:
        concurrency = 20
    elif mem_mib and mem_mib >= 4096:
        concurrency = 40

    needs_vpc = any(c.id in ("np_egress_cidr", "cluster_local_hostname") for c in all_checks)
    internal_ingress = any(c.id == "np_ingress_namespace_selector" for c in all_checks)

    sa_name = deployment.service_account
    if sa_name == "default":
        gcp_sa = f"{deployment.name}-sa@{project}.iam.gserviceaccount.com"
    else:
        gcp_sa = f"{sa_name}@{project}.iam.gserviceaccount.com"

    image = container.image if container else "IMAGE"

    plain_env_vars = []
    secret_flags = []
    if container:
        for e in container.env:
            if e.get("from") == "secret":
                secret_flags.append(f"{e['name']}={e.get('ref', e['name'])}:latest")
            elif e.get("from") != "configmap" and e.get("value"):
                plain_env_vars.append(f"{e['name']}={e['value']}")

    cmd_parts = [
        f"gcloud run deploy {deployment.name}",
        f"  --image {image}",
        f"  --region {region}",
        f"  --min-instances {hpa_min}",
        f"  --max-instances {hpa_max}",
        f"  --concurrency {concurrency}",
        f"  --memory {memory}",
        f"  --cpu {cpu}",
        f"  --ingress {'internal' if internal_ingress else 'all'}",
    ]
    if needs_vpc:
        cmd_parts.append(f"  --vpc-connector {region}-vpc-connector")
    cmd_parts.append(f"  --service-account {gcp_sa}")
    if secret_flags:
        cmd_parts.append(f"  --set-secrets {','.join(secret_flags)}")
    if plain_env_vars:
        cmd_parts.append(f"  --set-env-vars {','.join(plain_env_vars)}")
    cmd_parts.append(f"  --port {port}")

    gcloud_command = " \\\n".join(cmd_parts)

    return ServiceResult(
        name=deployment.name,
        namespace=deployment.namespace,
        image=image,
        replicas=deployment.replicas,
        memory=memory,
        cpu=cpu,
        score=score,
        verdict=verdict,
        workload_checks=workload_checks,
        traffic_checks=traffic_checks,
        network_checks=network_checks,
        blockers=blockers,
        warnings=warnings,
        passed=passed,
        gcloud_command=gcloud_command,
        hpa_min=hpa_min,
        hpa_max=hpa_max,
        port=port,
    )


def _parse_memory_mib(mem_str: str):
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
        else:
            return float(mem_str) / (1024 * 1024)
    except ValueError:
        return None
