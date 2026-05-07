from dataclasses import dataclass
from typing import Optional


@dataclass
class Check:
    id: str
    title: str
    detail: str
    fix: Optional[str]
    kind: str  # "blocker", "warning", "positive", "info"


def analyze(deployment, service=None, hpa=None) -> list:
    checks = []

    if service:
        svc_type = service.type
        if svc_type in ("NodePort", "LoadBalancer"):
            checks.append(Check(
                id="nodeport_or_lb_service",
                title=f"Service type {svc_type} — Cloud Run handles ingress",
                detail=f"Service '{service.name}' is type {svc_type}.",
                fix="Cloud Run manages its own ingress. Remove NodePort/LoadBalancer Service. Use --ingress all or --ingress internal based on access requirements.",
                kind="info",
            ))

        if service.session_affinity == "ClientIP":
            checks.append(Check(
                id="session_affinity",
                title="Session affinity (ClientIP) detected",
                detail=f"Service '{service.name}' uses ClientIP session affinity.",
                fix="Cloud Run does not support session affinity. Refactor to stateless request handling. Store session state in Memorystore (Redis) or Firestore.",
                kind="blocker",
            ))

        for port in service.ports:
            protocol = port.get("protocol", "TCP")
            port_num = port.get("port")
            app_protocol = port.get("app_protocol")
            if protocol == "TCP" and port_num not in (80, 443, 8080) and app_protocol not in ("http", "http2", "grpc"):
                checks.append(Check(
                    id="tcp_non_http_port",
                    title="TCP service on non-HTTP port",
                    detail=f"Service port {port_num} uses TCP without explicit HTTP app protocol.",
                    fix="Cloud Run only supports HTTP/1.1, HTTP/2, and WebSocket. Verify the service speaks HTTP. Set appProtocol: http or appProtocol: grpc.",
                    kind="warning",
                ))

    for c in deployment.containers:
        env_names = [e["name"].upper() for e in c.env]
        all_env_str = " ".join(e.get("value", "") for e in c.env if e.get("value"))
        name_lower = c.name.lower()
        image_lower = c.image.lower()

        grpc_hints = (
            any("GRPC" in n for n in env_names) or
            "grpc" in name_lower or
            "grpc" in image_lower or
            any((p.get("name") or "").lower() == "grpc" for p in c.ports)
        )
        if grpc_hints:
            checks.append(Check(
                id="grpc_detected",
                title="gRPC usage detected",
                detail=f"Container '{c.name}' shows signs of gRPC usage.",
                fix="Enable HTTP/2 in Cloud Run with --use-http2. gRPC is fully supported on Cloud Run with HTTP/2 end-to-end.",
                kind="warning",
            ))

        kafka_hints = any("KAFKA" in n for n in env_names) or "kafka" in all_env_str.lower()
        if kafka_hints:
            checks.append(Check(
                id="kafka_consumer",
                title="Kafka consumer pattern detected",
                detail=f"Container '{c.name}' has Kafka-related environment variables.",
                fix="Cloud Run is HTTP-triggered. Replace Kafka consumer with Cloud Pub/Sub push subscription or Eventarc trigger. Kafka → Pub/Sub migration may require an application change.",
                kind="warning",
            ))

        pubsub_hints = any("PUBSUB" in n or "PUB_SUB" in n for n in env_names)
        if pubsub_hints:
            checks.append(Check(
                id="pubsub_detected",
                title="Pub/Sub usage detected — event-driven ready",
                detail=f"Container '{c.name}' uses Pub/Sub environment variables.",
                fix=None,
                kind="positive",
            ))

    if hpa:
        for metric in hpa.metrics:
            metric_type = metric.get("type", "")
            if metric_type == "Resource":
                checks.append(Check(
                    id="hpa_cpu_metric",
                    title="HPA scales on CPU metric",
                    detail=f"HPA '{hpa.name}' uses CPU-based scaling.",
                    fix="Cloud Run scales on request concurrency, not CPU. Set --concurrency appropriately. CPU-based HPA metrics don't map directly.",
                    kind="warning",
                ))
            elif metric_type in ("External", "Object", "Pods"):
                checks.append(Check(
                    id="hpa_custom_metric",
                    title="HPA uses custom/external scaling metric",
                    detail=f"HPA '{hpa.name}' uses {metric_type} metric type.",
                    fix="Verify Cloud Run concurrency-based scaling meets requirements. Cloud Run cannot directly replicate custom HPA metrics.",
                    kind="warning",
                ))

        checks.append(Check(
            id="hpa_min_max",
            title=f"HPA min/max: {hpa.min_replicas}–{hpa.max_replicas} replicas",
            detail=f"Maps to Cloud Run --min-instances {hpa.min_replicas} --max-instances {hpa.max_replicas}.",
            fix=None,
            kind="positive",
        ))

    timeout_str = deployment.annotations.get("run.googleapis.com/timeout", "")
    if timeout_str:
        try:
            if int(timeout_str.rstrip("s")) > 3600:
                checks.append(Check(
                    id="timeout_exceeded",
                    title="Request timeout exceeds Cloud Run maximum",
                    detail=f"Annotation run.googleapis.com/timeout = {timeout_str}.",
                    fix="Reduce to ≤3600s. For longer work use Cloud Run Jobs.",
                    kind="blocker",
                ))
        except ValueError:
            pass
    else:
        timeout_fired = False
        for c in deployment.containers:
            if timeout_fired:
                break
            for e in c.env:
                if "TIMEOUT" in e.get("name", "").upper():
                    val = e.get("value", "")
                    try:
                        if int(val) > 3600:
                            checks.append(Check(
                                id="timeout_exceeded",
                                title="Request timeout exceeds Cloud Run maximum",
                                detail=f"Env var '{e['name']}' = {val}s exceeds Cloud Run max of 3600s.",
                                fix="Reduce timeout to ≤3600s or refactor long-running work into Cloud Run Jobs (--max-retries, no HTTP timeout).",
                                kind="blocker",
                            ))
                            timeout_fired = True
                            break
                    except ValueError:
                        pass

    return checks
