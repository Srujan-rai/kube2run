from dataclasses import dataclass
from typing import Optional


@dataclass
class Check:
    id: str
    title: str
    detail: str
    fix: Optional[str]
    kind: str  # "blocker", "warning", "positive", "info"


_CLUSTER_LOCAL_SUFFIX = ".svc.cluster.local"


def analyze(deployment, network_policies=None, service_account=None, secrets=None) -> list:
    checks = []
    network_policies = network_policies or []
    secrets = secrets or []

    for np in network_policies:
        for rule in np.ingress_rules:
            froms = rule.get("from", [])
            if froms:
                checks.append(Check(
                    id="np_ingress_namespace_selector",
                    title="NetworkPolicy restricts ingress by namespace",
                    detail=f"NetworkPolicy '{np.name}' limits ingress to specific namespaces.",
                    fix="Map to Cloud Run --ingress internal. Internal ingress restricts access to same VPC/project. Remove NetworkPolicy after migration.",
                    kind="info",
                ))
                break

        for rule in np.egress_rules:
            for dest in rule.get("to", []):
                cidr = dest.get("cidr", "")
                if cidr and cidr not in ("0.0.0.0/0", "::/0"):
                    checks.append(Check(
                        id="np_egress_cidr",
                        title="NetworkPolicy restricts egress to specific CIDR",
                        detail=f"NetworkPolicy '{np.name}' allows egress to {cidr}.",
                        fix="Cloud Run needs a Serverless VPC Connector to reach private CIDRs. Add --vpc-connector and --vpc-egress all-traffic or private-ranges-only.",
                        kind="warning",
                    ))

    if service_account:
        annotations = service_account.annotations
        irsa = annotations.get("eks.amazonaws.com/role-arn", "")
        aks_wi = annotations.get("azure.workload.identity/client-id", "")
        if irsa:
            checks.append(Check(
                id="irsa_annotation",
                title="AWS IRSA annotation detected",
                detail=f"ServiceAccount '{service_account.name}' has eks.amazonaws.com/role-arn: {irsa}.",
                fix="Map to GCP Workload Identity. Create a GCP ServiceAccount, bind it via --service-account in Cloud Run, and grant required IAM roles.",
                kind="warning",
            ))
        elif aks_wi:
            checks.append(Check(
                id="aks_workload_identity",
                title="AKS Workload Identity annotation detected",
                detail=f"ServiceAccount '{service_account.name}' has azure.workload.identity/client-id: {aks_wi}.",
                fix="Map to GCP Workload Identity Federation or a GCP ServiceAccount with --service-account in Cloud Run.",
                kind="warning",
            ))

    for secret in secrets:
        if secret.type == "kubernetes.io/tls":
            checks.append(Check(
                id="tls_secret",
                title="TLS certificate Secret detected",
                detail=f"Secret '{secret.name}' is type kubernetes.io/tls.",
                fix="Migrate to Google Certificate Manager or Cloud Run managed TLS (automatic). Cloud Run provisions TLS automatically for *.run.app domains.",
                kind="warning",
            ))
        elif secret.type == "kubernetes.io/dockerconfigjson":
            checks.append(Check(
                id="docker_pull_secret",
                title="Docker registry pull secret detected",
                detail=f"Secret '{secret.name}' is a docker registry credential.",
                fix="Migrate image to Google Artifact Registry. Cloud Run pulls from Artifact Registry using the Cloud Run Service Account — no pull secret needed.",
                kind="warning",
            ))

    for c in deployment.containers:
        for e in c.env:
            val = e.get("value", "")
            if val and _CLUSTER_LOCAL_SUFFIX in val:
                checks.append(Check(
                    id="cluster_local_hostname",
                    title="Cluster-internal hostname in environment variable",
                    detail=f"Env var '{e['name']}' references '{val}' — a cluster-internal DNS name.",
                    fix="Replace with Cloud Run service URL or internal Load Balancer URL. Add --vpc-connector if the dependency is in a private VPC.",
                    kind="warning",
                ))

    return checks
