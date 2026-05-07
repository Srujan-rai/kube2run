import sys
import click
from datetime import datetime

import k8s_collector as collector
from analyzer import workload, traffic, network, scorer
from report import html_report


def _build_mock_data():
    from k8s_collector import (
        ClusterData, DeploymentData, ServiceData, HPAData,
        ContainerSpec, PVCData, SecretData, NetworkPolicyData, ServiceAccountData,
    )

    containers = [ContainerSpec(
        name="api",
        image="gcr.io/myproject/order-service:v3.0.1",
        ports=[{"port": 8080, "protocol": "TCP", "name": "http"}],
        resources={"limits": {"memory": "512Mi", "cpu": "1"}, "requests": {"memory": "256Mi", "cpu": "500m"}},
        env=[
            {"name": "DB_PASSWORD", "from": "secret", "ref": "db-secret"},
            {"name": "APP_PORT", "value": "8080"},
        ],
        volume_mounts=[],
        liveness_probe={"timeout_seconds": 3, "period_seconds": 10},
        readiness_probe={"timeout_seconds": 3, "period_seconds": 5},
        startup_probe=None,
        security_context={"privileged": False, "run_as_user": 1000, "run_as_non_root": True},
    )]
    dep_ready = DeploymentData(
        name="order-service",
        namespace="prod",
        replicas=3,
        containers=containers,
        init_containers=[],
        volumes=[],
        service_account="order-svc",
        annotations={"prometheus.io/scrape": "true"},
        labels={"app": "order-service"},
    )

    svc_ready = ServiceData(
        name="order-service",
        namespace="prod",
        type="ClusterIP",
        ports=[{"port": 8080, "protocol": "TCP", "name": "http", "app_protocol": "http", "target_port": "8080"}],
        selector={"app": "order-service"},
        session_affinity="None",
        app_protocol="http",
        annotations={},
    )

    hpa_ready = HPAData(
        name="order-service",
        namespace="prod",
        min_replicas=2,
        max_replicas=20,
        metrics=[{"type": "Resource"}],
        scale_target_ref="order-service",
    )

    sa_ready = ServiceAccountData(
        name="order-svc",
        namespace="prod",
        annotations={"eks.amazonaws.com/role-arn": "arn:aws:iam::123456789:role/order-svc"},
    )

    containers_blocked = [ContainerSpec(
        name="legacy-app",
        image="internal-registry.company.com/legacy-app:1.0",
        ports=[{"port": 9090, "protocol": "TCP", "name": "http"}],
        resources={},
        env=[{"name": "CONFIG_FILE", "from": "configmap", "ref": "app-config"}],
        volume_mounts=[{"name": "data", "mount_path": "/data"}],
        liveness_probe=None,
        readiness_probe=None,
        startup_probe=None,
        security_context={"privileged": True},
    )]
    dep_blocked = DeploymentData(
        name="legacy-monolith",
        namespace="prod",
        replicas=15,
        containers=containers_blocked,
        init_containers=[],
        volumes=[{"name": "data", "type": "pvc", "claim_name": "legacy-data"}],
        service_account="default",
        annotations={},
        labels={"app": "legacy-monolith"},
    )

    svc_blocked = ServiceData(
        name="legacy-monolith",
        namespace="prod",
        type="LoadBalancer",
        ports=[{"port": 9090, "protocol": "TCP", "name": "http", "app_protocol": None, "target_port": "9090"}],
        selector={"app": "legacy-monolith"},
        session_affinity="ClientIP",
        app_protocol=None,
        annotations={},
    )

    np_blocked = NetworkPolicyData(
        name="legacy-egress",
        namespace="prod",
        pod_selector={"app": "legacy-monolith"},
        ingress_rules=[{"from": ["namespaceSelector: {}"]}],
        egress_rules=[{"to": [{"cidr": "10.0.0.0/8"}]}],
    )

    return ClusterData(
        cluster_type="EKS",
        namespace="prod",
        deployments=[dep_ready, dep_blocked],
        services=[svc_ready, svc_blocked],
        hpas=[hpa_ready],
        pvcs=[PVCData(name="legacy-data", namespace="prod", storage_class="gp2", access_modes=["ReadWriteOnce"], storage="50Gi")],
        configmaps=[],
        secrets=[],
        network_policies=[np_blocked],
        service_accounts=[sa_ready],
        role_bindings=[],
        ingresses=[],
    )


def _run_analysis(cluster_data, region="us-central1"):
    results = []
    hpa_map = {h.scale_target_ref: h for h in cluster_data.hpas}
    svc_map = {s.name: s for s in cluster_data.services}
    sa_map = {sa.name: sa for sa in cluster_data.service_accounts}
    np_by_ns = {}
    for np in cluster_data.network_policies:
        np_by_ns.setdefault(np.namespace, []).append(np)

    for dep in cluster_data.deployments:
        hpa = hpa_map.get(dep.name)
        svc = svc_map.get(dep.name)
        sa = sa_map.get(dep.service_account)
        nps = np_by_ns.get(dep.namespace, [])

        relevant_secrets = [
            s for s in cluster_data.secrets
            if s.namespace == dep.namespace
        ]

        wc = workload.analyze(dep, hpa=hpa)
        tc = traffic.analyze(dep, service=svc, hpa=hpa)
        nc = network.analyze(dep, network_policies=nps, service_account=sa, secrets=relevant_secrets)

        result = scorer.score_and_build(
            deployment=dep,
            workload_checks=wc,
            traffic_checks=tc,
            network_checks=nc,
            hpa=hpa,
            service=svc,
            region=region,
        )
        results.append(result)

    return results


@click.command()
@click.option("--namespace", "-n", default="default", show_default=True,
              help='Kubernetes namespace. Use "all" for all namespaces.')
@click.option("--output", "-o", default="cloudrun_readiness_report.html", show_default=True,
              help="Output HTML report path.")
@click.option("--mock", is_flag=True, default=False,
              help="Use mock data (no cluster required).")
@click.option("--context", default=None,
              help="kubectl context name (optional).")
@click.option("--region", default="us-central1", show_default=True,
              help="Target Cloud Run region.")
def main(namespace, output, mock, context, region):
    """kube2run — Kubernetes to Cloud Run readiness analyzer."""
    click.echo(f"[kube2run] Starting analysis — namespace={namespace}, output={output}")

    if mock:
        click.echo("[kube2run] Using mock cluster data…")
        cluster_data = _build_mock_data()
    else:
        click.echo("[kube2run] Connecting to Kubernetes cluster…")
        try:
            cluster_data = collector.collect(namespace=namespace, context=context)
        except Exception as e:
            click.echo(f"[kube2run] ERROR: Failed to connect to cluster: {e}", err=True)
            sys.exit(1)

    click.echo(f"[kube2run] Cluster type: {cluster_data.cluster_type}")
    click.echo(f"[kube2run] Analyzing {len(cluster_data.deployments)} deployment(s)…")

    results = _run_analysis(cluster_data, region=region)

    click.echo(f"[kube2run] Generating report → {output}")
    html_report.generate(results, cluster_data.cluster_type, namespace, output)

    verdicts = {"READY": 0, "MOSTLY READY": 0, "NEEDS WORK": 0, "NOT READY": 0}
    for r in results:
        verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1

    click.echo("\n── Summary ──────────────────────────────────")
    click.echo(f"  Total services : {len(results)}")
    click.echo(f"  READY          : {verdicts['READY']}")
    click.echo(f"  MOSTLY READY   : {verdicts['MOSTLY READY']}")
    click.echo(f"  NEEDS WORK     : {verdicts['NEEDS WORK']}")
    click.echo(f"  NOT READY      : {verdicts['NOT READY']}")
    if results:
        avg = round(sum(r.score for r in results) / len(results))
        click.echo(f"  Avg score      : {avg}/100")
    click.echo(f"\n  Report saved to: {output}")


if __name__ == "__main__":
    main()
