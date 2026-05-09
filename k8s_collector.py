import os
from dataclasses import dataclass, field
from typing import Optional
from kubernetes import client, config
from kubernetes.client.rest import ApiException


def load_k8s_config(context: Optional[str] = None):
    if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
        config.load_incluster_config()
    else:
        config.load_kube_config(context=context)


def detect_cluster_type(v1: client.CoreV1Api) -> str:
    try:
        nodes = v1.list_node(limit=1).items
        if not nodes:
            return "unknown"
        node = nodes[0]
        provider_id = node.spec.provider_id or ""
        labels = node.metadata.labels or {}

        if provider_id.startswith("aws://"):
            return "EKS"
        elif "kubernetes.azure.com" in labels or provider_id.startswith("azure://"):
            return "AKS"
        elif provider_id.startswith("gce://"):
            return "GKE"
        else:
            return "on-prem"
    except ApiException:
        return "unknown"


@dataclass
class ContainerSpec:
    name: str
    image: str
    ports: list
    resources: dict
    env: list
    volume_mounts: list
    liveness_probe: Optional[dict]
    readiness_probe: Optional[dict]
    startup_probe: Optional[dict]
    security_context: Optional[dict]


@dataclass
class DeploymentData:
    name: str
    namespace: str
    replicas: int
    containers: list
    init_containers: list
    volumes: list
    service_account: str
    annotations: dict
    labels: dict
    pod_labels: dict = field(default_factory=dict)  # spec.template.metadata.labels — used for PDB matching
    host_network: bool = False
    termination_grace_period_seconds: int = 30


@dataclass
class ServiceData:
    name: str
    namespace: str
    type: str
    ports: list
    selector: dict
    session_affinity: str
    app_protocol: Optional[str]
    annotations: dict


@dataclass
class HPAData:
    name: str
    namespace: str
    min_replicas: int
    max_replicas: int
    metrics: list
    scale_target_ref: str
    scale_down_stabilization_seconds: Optional[int] = None
    scale_up_stabilization_seconds: Optional[int] = None


@dataclass
class PVCData:
    name: str
    namespace: str
    storage_class: str
    access_modes: list
    storage: str


@dataclass
class ConfigMapData:
    name: str
    namespace: str
    data_keys: list


@dataclass
class SecretData:
    name: str
    namespace: str
    type: str
    data_keys: list


@dataclass
class NetworkPolicyData:
    name: str
    namespace: str
    pod_selector: dict
    ingress_rules: list
    egress_rules: list


@dataclass
class ServiceAccountData:
    name: str
    namespace: str
    annotations: dict


@dataclass
class RoleBindingData:
    name: str
    namespace: str
    subjects: list
    role_ref: dict
    is_cluster_binding: bool


@dataclass
class IngressData:
    name: str
    namespace: str
    rules: list
    tls: list
    annotations: dict


@dataclass
class CronJobData:
    name: str
    namespace: str
    schedule: str
    suspend: bool


@dataclass
class PodDisruptionBudgetData:
    name: str
    namespace: str
    min_available: Optional[str]
    max_unavailable: Optional[str]
    selector: dict


@dataclass
class KEDAScaledObjectData:
    name: str
    namespace: str
    scale_target_ref: str
    min_replicas: int
    max_replicas: int
    triggers: list


@dataclass
class ClusterData:
    cluster_type: str
    namespace: str
    deployments: list = field(default_factory=list)
    services: list = field(default_factory=list)
    hpas: list = field(default_factory=list)
    pvcs: list = field(default_factory=list)
    configmaps: list = field(default_factory=list)
    secrets: list = field(default_factory=list)
    network_policies: list = field(default_factory=list)
    service_accounts: list = field(default_factory=list)
    role_bindings: list = field(default_factory=list)
    ingresses: list = field(default_factory=list)
    cronjobs: list = field(default_factory=list)
    pdbs: list = field(default_factory=list)
    keda_objects: list = field(default_factory=list)


def _parse_container(c) -> ContainerSpec:
    ports = [{"port": p.container_port, "protocol": p.protocol, "name": p.name}
             for p in (c.ports or [])]
    resources = {}
    if c.resources:
        resources["limits"] = c.resources.limits or {}
        resources["requests"] = c.resources.requests or {}
    env = []
    for e in (c.env or []):
        entry = {"name": e.name}
        if e.value:
            entry["value"] = e.value
        elif e.value_from:
            if e.value_from.config_map_key_ref:
                entry["from"] = "configmap"
                entry["ref"] = e.value_from.config_map_key_ref.name
            elif e.value_from.secret_key_ref:
                entry["from"] = "secret"
                entry["ref"] = e.value_from.secret_key_ref.name
            elif e.value_from.field_ref:
                entry["from"] = "fieldRef"
                entry["ref"] = e.value_from.field_ref.field_path
            elif e.value_from.resource_field_ref:
                entry["from"] = "resourceFieldRef"
                entry["ref"] = e.value_from.resource_field_ref.resource
        env.append(entry)
    volume_mounts = [{"name": vm.name, "mount_path": vm.mount_path}
                     for vm in (c.volume_mounts or [])]

    def probe_to_dict(p):
        if not p:
            return None
        return {"timeout_seconds": p.timeout_seconds, "period_seconds": p.period_seconds}

    sc = None
    if c.security_context:
        sc = {
            "privileged": c.security_context.privileged,
            "run_as_user": c.security_context.run_as_user,
            "run_as_non_root": c.security_context.run_as_non_root,
        }

    return ContainerSpec(
        name=c.name,
        image=c.image or "",
        ports=ports,
        resources=resources,
        env=env,
        volume_mounts=volume_mounts,
        liveness_probe=probe_to_dict(c.liveness_probe),
        readiness_probe=probe_to_dict(c.readiness_probe),
        startup_probe=probe_to_dict(getattr(c, "startup_probe", None)),
        security_context=sc,
    )


def _parse_volume(v) -> dict:
    vol = {"name": v.name, "type": "unknown"}
    if v.host_path:
        vol["type"] = "hostPath"
        vol["path"] = v.host_path.path
    elif v.persistent_volume_claim:
        vol["type"] = "pvc"
        vol["claim_name"] = v.persistent_volume_claim.claim_name
    elif v.empty_dir is not None:
        vol["type"] = "emptyDir"
    elif v.config_map:
        vol["type"] = "configmap"
        vol["name_ref"] = v.config_map.name
    elif v.secret:
        vol["type"] = "secret"
        vol["secret_name"] = v.secret.secret_name
    return vol


def collect(namespace: str = "default", context: Optional[str] = None) -> ClusterData:
    load_k8s_config(context=context)

    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    autoscaling_v1 = client.AutoscalingV1Api()
    try:
        autoscaling_v2 = client.AutoscalingV2Api()
        use_v2 = True
    except Exception:
        use_v2 = False
    networking_v1 = client.NetworkingV1Api()
    rbac_v1 = client.RbacAuthorizationV1Api()

    cluster_type = detect_cluster_type(v1)
    all_ns = namespace == "all"

    def list_deployments():
        if all_ns:
            return apps_v1.list_deployment_for_all_namespaces().items
        return apps_v1.list_namespaced_deployment(namespace).items

    def list_services():
        if all_ns:
            return v1.list_service_for_all_namespaces().items
        return v1.list_namespaced_service(namespace).items

    def list_hpas():
        if use_v2:
            if all_ns:
                return autoscaling_v2.list_horizontal_pod_autoscaler_for_all_namespaces().items
            return autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(namespace).items
        else:
            if all_ns:
                return autoscaling_v1.list_horizontal_pod_autoscaler_for_all_namespaces().items
            return autoscaling_v1.list_namespaced_horizontal_pod_autoscaler(namespace).items

    def list_pvcs():
        if all_ns:
            return v1.list_persistent_volume_claim_for_all_namespaces().items
        return v1.list_namespaced_persistent_volume_claim(namespace).items

    def list_configmaps():
        if all_ns:
            return v1.list_config_map_for_all_namespaces().items
        return v1.list_namespaced_config_map(namespace).items

    def list_secrets():
        if all_ns:
            return v1.list_secret_for_all_namespaces().items
        return v1.list_namespaced_secret(namespace).items

    def list_network_policies():
        if all_ns:
            return networking_v1.list_network_policy_for_all_namespaces().items
        return networking_v1.list_namespaced_network_policy(namespace).items

    def list_service_accounts():
        if all_ns:
            return v1.list_service_account_for_all_namespaces().items
        return v1.list_namespaced_service_account(namespace).items

    def list_role_bindings():
        rbs = rbac_v1.list_role_binding_for_all_namespaces().items if all_ns else \
              rbac_v1.list_namespaced_role_binding(namespace).items
        crbs = rbac_v1.list_cluster_role_binding().items
        return rbs, crbs

    def list_ingresses():
        if all_ns:
            return networking_v1.list_ingress_for_all_namespaces().items
        return networking_v1.list_namespaced_ingress(namespace).items

    deployments = []
    for d in list_deployments():
        spec = d.spec.template.spec
        containers = [_parse_container(c) for c in (spec.containers or [])]
        init_containers = [_parse_container(c) for c in (spec.init_containers or [])]
        volumes = [_parse_volume(v) for v in (spec.volumes or [])]
        deployments.append(DeploymentData(
            name=d.metadata.name,
            namespace=d.metadata.namespace,
            replicas=d.spec.replicas or 1,
            containers=containers,
            init_containers=init_containers,
            volumes=volumes,
            service_account=spec.service_account_name or "default",
            annotations=d.metadata.annotations or {},
            labels=d.metadata.labels or {},
            pod_labels=d.spec.template.metadata.labels or {} if d.spec.template.metadata else {},
            host_network=bool(spec.host_network),
            termination_grace_period_seconds=spec.termination_grace_period_seconds or 30,
        ))

    services = []
    for s in list_services():
        ports = [{"port": p.port, "target_port": str(p.target_port), "protocol": p.protocol,
                  "name": p.name, "app_protocol": getattr(p, "app_protocol", None)}
                 for p in (s.spec.ports or [])]
        services.append(ServiceData(
            name=s.metadata.name,
            namespace=s.metadata.namespace,
            type=s.spec.type or "ClusterIP",
            ports=ports,
            selector=s.spec.selector or {},
            session_affinity=s.spec.session_affinity or "None",
            app_protocol=None,
            annotations=s.metadata.annotations or {},
        ))

    hpas = []
    for h in list_hpas():
        metrics = []
        if hasattr(h.spec, "metrics") and h.spec.metrics:
            for m in h.spec.metrics:
                metrics.append({"type": m.type})
        scale_down_stab = None
        scale_up_stab = None
        if use_v2:
            behavior = getattr(h.spec, "behavior", None)
            if behavior:
                sd = getattr(behavior, "scale_down", None)
                if sd:
                    v = getattr(sd, "stabilization_window_seconds", None)
                    if v is not None:
                        scale_down_stab = v
                su = getattr(behavior, "scale_up", None)
                if su:
                    v = getattr(su, "stabilization_window_seconds", None)
                    if v is not None:
                        scale_up_stab = v
        hpas.append(HPAData(
            name=h.metadata.name,
            namespace=h.metadata.namespace,
            min_replicas=h.spec.min_replicas or 1,
            max_replicas=h.spec.max_replicas or 10,
            metrics=metrics,
            scale_target_ref=h.spec.scale_target_ref.name,
            scale_down_stabilization_seconds=scale_down_stab,
            scale_up_stabilization_seconds=scale_up_stab,
        ))

    pvcs = []
    for p in list_pvcs():
        storage = ""
        if p.spec.resources and p.spec.resources.requests:
            storage = p.spec.resources.requests.get("storage", "")
        pvcs.append(PVCData(
            name=p.metadata.name,
            namespace=p.metadata.namespace,
            storage_class=p.spec.storage_class_name or "",
            access_modes=p.spec.access_modes or [],
            storage=storage,
        ))

    configmaps = []
    for cm in list_configmaps():
        configmaps.append(ConfigMapData(
            name=cm.metadata.name,
            namespace=cm.metadata.namespace,
            data_keys=list((cm.data or {}).keys()),
        ))

    secrets = []
    for s in list_secrets():
        secrets.append(SecretData(
            name=s.metadata.name,
            namespace=s.metadata.namespace,
            type=s.type or "Opaque",
            data_keys=list((s.data or {}).keys()),
        ))

    network_policies = []
    for np in list_network_policies():
        ingress_rules = []
        for rule in (np.spec.ingress or []):
            entry = {}
            if rule._from:
                entry["from"] = [str(f) for f in rule._from]
            ingress_rules.append(entry)
        egress_rules = []
        for rule in (np.spec.egress or []):
            entry = {}
            if rule.to:
                entry["to"] = []
                for t in rule.to:
                    if t.ip_block:
                        entry["to"].append({"cidr": t.ip_block.cidr})
            egress_rules.append(entry)
        network_policies.append(NetworkPolicyData(
            name=np.metadata.name,
            namespace=np.metadata.namespace,
            pod_selector=np.spec.pod_selector.match_labels or {} if np.spec.pod_selector else {},
            ingress_rules=ingress_rules,
            egress_rules=egress_rules,
        ))

    service_accounts = []
    for sa in list_service_accounts():
        service_accounts.append(ServiceAccountData(
            name=sa.metadata.name,
            namespace=sa.metadata.namespace,
            annotations=sa.metadata.annotations or {},
        ))

    role_bindings = []
    rbs, crbs = list_role_bindings()
    for rb in rbs:
        role_bindings.append(RoleBindingData(
            name=rb.metadata.name,
            namespace=rb.metadata.namespace,
            subjects=[{"kind": s.kind, "name": s.name} for s in (rb.subjects or [])],
            role_ref={"kind": rb.role_ref.kind, "name": rb.role_ref.name},
            is_cluster_binding=False,
        ))
    for crb in crbs:
        role_bindings.append(RoleBindingData(
            name=crb.metadata.name,
            namespace="cluster",
            subjects=[{"kind": s.kind, "name": s.name} for s in (crb.subjects or [])],
            role_ref={"kind": crb.role_ref.kind, "name": crb.role_ref.name},
            is_cluster_binding=True,
        ))

    ingresses = []
    for ing in list_ingresses():
        rules = []
        for r in (ing.spec.rules or []):
            rule = {"host": r.host, "paths": []}
            if r.http:
                for p in r.http.paths:
                    rule["paths"].append({"path": p.path, "backend": str(p.backend)})
            rules.append(rule)
        tls = [{"hosts": t.hosts, "secret_name": t.secret_name} for t in (ing.spec.tls or [])]
        ingresses.append(IngressData(
            name=ing.metadata.name,
            namespace=ing.metadata.namespace,
            rules=rules,
            tls=tls,
            annotations=ing.metadata.annotations or {},
        ))

    batch_v1 = client.BatchV1Api()
    cronjobs = []
    try:
        raw_cjs = batch_v1.list_cron_job_for_all_namespaces().items if all_ns else \
                  batch_v1.list_namespaced_cron_job(namespace).items
        for cj in raw_cjs:
            cronjobs.append(CronJobData(
                name=cj.metadata.name,
                namespace=cj.metadata.namespace,
                schedule=cj.spec.schedule or "",
                suspend=bool(cj.spec.suspend),
            ))
    except Exception:
        pass

    pdbs = []
    try:
        policy_v1 = client.PolicyV1Api()
        raw_pdbs = policy_v1.list_pod_disruption_budget_for_all_namespaces().items if all_ns else \
                   policy_v1.list_namespaced_pod_disruption_budget(namespace).items
        for pdb in raw_pdbs:
            min_avail = str(pdb.spec.min_available) if pdb.spec.min_available is not None else None
            max_unavail = str(pdb.spec.max_unavailable) if pdb.spec.max_unavailable is not None else None
            sel = {}
            if pdb.spec.selector and pdb.spec.selector.match_labels:
                sel = pdb.spec.selector.match_labels
            pdbs.append(PodDisruptionBudgetData(
                name=pdb.metadata.name,
                namespace=pdb.metadata.namespace,
                min_available=min_avail,
                max_unavailable=max_unavail,
                selector=sel,
            ))
    except Exception:
        pass

    keda_objects = []
    try:
        custom_api = client.CustomObjectsApi()
        if all_ns:
            raw_keda = custom_api.list_cluster_custom_object(
                group="keda.sh", version="v1alpha1", plural="scaledobjects"
            ).get("items", [])
        else:
            raw_keda = custom_api.list_namespaced_custom_object(
                group="keda.sh", version="v1alpha1", namespace=namespace, plural="scaledobjects"
            ).get("items", [])
        for item in raw_keda:
            spec = item.get("spec", {})
            meta = item.get("metadata", {})
            triggers = [
                {"type": t.get("type"), "metadata": t.get("metadata", {})}
                for t in spec.get("triggers", [])
            ]
            keda_objects.append(KEDAScaledObjectData(
                name=meta.get("name", ""),
                namespace=meta.get("namespace", ""),
                scale_target_ref=spec.get("scaleTargetRef", {}).get("name", ""),
                min_replicas=spec.get("minReplicaCount", 0),
                max_replicas=spec.get("maxReplicaCount", 10),
                triggers=triggers,
            ))
    except Exception:
        pass

    return ClusterData(
        cluster_type=cluster_type,
        namespace=namespace,
        deployments=deployments,
        services=services,
        hpas=hpas,
        pvcs=pvcs,
        configmaps=configmaps,
        secrets=secrets,
        network_policies=network_policies,
        service_accounts=service_accounts,
        role_bindings=role_bindings,
        ingresses=ingresses,
        cronjobs=cronjobs,
        pdbs=pdbs,
        keda_objects=keda_objects,
    )
