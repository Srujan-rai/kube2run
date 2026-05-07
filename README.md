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
hostPath volumes, PVC mounts, emptyDir, ConfigMap volume mounts, multiple containers, init containers, memory/CPU over Cloud Run limits, multiple ports, non-standard ports, missing health probes, privileged mode, non-GCP image registry, high static replica count, Prometheus annotations, HPA presence.

### Pillar 2 — Traffic
NodePort/LoadBalancer service type, session affinity, TCP on non-HTTP ports, gRPC usage, Kafka consumer pattern, Pub/Sub usage, HPA metric type (CPU vs request-based), HPA min/max replicas, request timeout overflow.

### Pillar 3 — Network and security
NetworkPolicy ingress namespace selectors → internal ingress, egress CIDR rules → VPC connector, AWS IRSA annotation, AKS Workload Identity annotation, TLS certificate Secrets, Docker pull Secrets, ConfigMap volume mounts, cluster-internal DNS hostnames in env vars.
