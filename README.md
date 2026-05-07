# kube2run

kube2run inspects every Deployment in a Kubernetes cluster and produces a self-contained HTML report that scores each microservice for Cloud Run compatibility — with a verdict, list of blockers and warnings (each with a specific fix), and a ready-to-run `gcloud run deploy` command.

---

## Prerequisites

- Python 3.12+
- `kubectl` configured and pointing at your target cluster (for CLI mode)
- Google Cloud SDK installed (for CronJob upload to GCS)

---

## CLI usage

```bash
pip install -r requirements.txt

# Analyze the 'prod' namespace
python main.py -n prod -o report.html

# Analyze all namespaces
python main.py -n all -o report.html

# Use a specific kubeconfig context
python main.py -n prod --context my-cluster -o report.html

# Run without a cluster (mock data)
python main.py --mock -o report.html

# Target a different Cloud Run region
python main.py -n prod --region europe-west1 -o report.html
```

Open `report.html` in a browser. No external dependencies — the file is fully self-contained.

---

## In-cluster deployment (weekly CronJob)

```bash
# 1. Build and push the image
docker build -t gcr.io/YOUR_PROJECT/kube2run:latest .
docker push gcr.io/YOUR_PROJECT/kube2run:latest

# 2. Apply RBAC (read-only ClusterRole)
kubectl apply -f deploy/rbac.yaml

# 3. Edit deploy/cronjob.yaml — set GCS_BUCKET and update the image reference
#    GCS_BUCKET: your-bucket-name
#    image: gcr.io/YOUR_PROJECT/kube2run:latest

# 4. Deploy the CronJob
kubectl apply -f deploy/cronjob.yaml
```

Report appears every Monday at 08:00 UTC in:
```
gs://YOUR_BUCKET/kube2run/report-YYYYMMDD.html
```

---

## One-off Job in CI

```bash
kubectl create job kube2run-adhoc \
  --from=cronjob/kube2run-analyzer \
  -n kube-system
```

---

## How to read the report

| Verdict | Score | Meaning |
|---------|-------|---------|
| READY | 80–100 | No blockers. Deploy now. |
| MOSTLY READY | 60–79 | Minor configuration changes needed. |
| NEEDS WORK | 35–59 | Moderate refactoring required. |
| NOT READY | 0–34 | Fundamental blockers present. |

Each service card shows:
- **Blockers** — hard incompatibilities with specific fix instructions
- **Warnings** — configuration gaps with fix instructions
- **Passed** — checks that mapped cleanly to Cloud Run
- **gcloud run deploy** — pre-populated command ready to run

Use the filter buttons to focus on a verdict tier. Use the search box to find a specific service.

---

## Environment variables (CronJob)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCS_BUCKET` | Yes | — | GCS bucket name for report upload |
| `NAMESPACE` | No | `all` | Kubernetes namespace(s) to analyze |
| `REGION` | No | `us-central1` | Target Cloud Run region |

---

## Checks performed

### Pillar 1 — Workload

| Check | Kind | Why it matters |
|---|---|---|
| hostPath volume | Blocker | Cloud Run containers cannot access host filesystem |
| PVC volume | Blocker | Cloud Run is stateless; no PVC support |
| ConfigMap volume mount | Blocker | Cloud Run has no ConfigMap mounting; use env vars or GCS |
| privileged: true | Blocker | Cloud Run does not allow privileged containers |
| hostNetwork: true | Blocker | Cloud Run uses isolated network namespaces |
| Memory limit > 32 GiB | Blocker | Cloud Run hard cap is 32 GiB |
| CPU limit > 8 vCPU | Blocker | Cloud Run hard cap is 8 vCPU |
| emptyDir volume | Warning | Data lost on restart; verify no cross-request state |
| Multiple containers | Warning | Cloud Run 2nd gen supports sidecars — verify no IPC dependency |
| Init containers | Warning | Must complete within 4-minute startup budget |
| No resource limits | Warning | Cloud Run requires explicit memory/CPU at deploy time |
| Multiple ports | Warning | Cloud Run routes to single port only |
| Non-standard port | Warning | Must specify --port explicitly |
| No health probes | Warning | Cloud Run needs startup signal |
| Non-GCP image registry | Warning | Migrate ECR/ACR image to Artifact Registry |
| High static replica count | Warning | Use --min-instances instead |
| Prometheus scrape annotation | Warning | Use Cloud Monitoring or OpenTelemetry sidecar |
| Termination grace period > 300s | Warning | Cloud Run max SIGTERM wait is 300s |
| Downward API env var (fieldRef) | Warning | Kubernetes Downward API not supported on Cloud Run |
| >50 environment variables | Warning | Cloud Run has 32 KiB total env var size limit |
| No HPA, replicas > 1 | Warning | Static scaling — use --min/--max-instances instead |
| :latest or untagged image | Warning | Unpinned tags may not pull on redeploy |
| Secret env var reference | Positive | Maps cleanly to --set-secrets / Secret Manager |
| HPA present | Positive | Maps to --min-instances / --max-instances |

### Pillar 2 — Traffic

| Check | Kind | Why it matters |
|---|---|---|
| Session affinity (ClientIP) | Blocker | Cloud Run has no session affinity; stateless required |
| UDP protocol | Blocker | Cloud Run is HTTP-only; UDP not supported |
| Request timeout > 3600s | Blocker | Cloud Run hard limit is 3600s |
| NodePort/LoadBalancer service | Info | Cloud Run manages ingress; service type irrelevant |
| TCP on non-HTTP port | Warning | Must verify HTTP compatibility |
| gRPC usage | Warning | Supported — add --use-http2 |
| Kafka consumer pattern | Warning | Replace with Pub/Sub push or Eventarc |
| HPA CPU metric | Warning | Cloud Run scales on concurrency, not CPU |
| HPA custom/external metric | Warning | Verify concurrency-based scaling is sufficient |
| WebSocket hints | Info | Supported — do not use --use-http2 for WebSocket |
| Pub/Sub usage | Positive | Maps naturally to Cloud Run push subscription |
| HPA min/max replicas | Positive | Maps to --min-instances / --max-instances |

### Pillar 3 — Network and security

| Check | Kind | Why it matters |
|---|---|---|
| Service mesh (Istio/Linkerd) | Blocker | Cloud Run does not support service mesh sidecars |
| NetworkPolicy ingress namespace selector | Info | Maps to --ingress internal |
| NetworkPolicy egress to private CIDR | Warning | VPC connector required |
| AWS IRSA annotation | Warning | Migrate to GCP Workload Identity |
| AKS Workload Identity annotation | Warning | Migrate to GCP Workload Identity Federation |
| TLS certificate Secret | Warning | Migrate to Certificate Manager or Cloud Run managed TLS |
| Docker registry pull secret | Warning | Migrate image to Artifact Registry |
| Cluster-internal DNS hostname in env var | Warning | Replace with Cloud Run URL; add VPC connector if private |
| cert-manager annotation | Warning | Migrate to Google Certificate Manager |
| External Secrets Operator annotation | Positive | Maps cleanly to Secret Manager |
