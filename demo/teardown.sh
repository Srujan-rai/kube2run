#!/usr/bin/env bash
set -euo pipefail

PROJECT="${GCP_PROJECT:-project-45a721e5-5e18-42e7-851}"
ZONE="${GCP_ZONE:-us-central1-a}"
CLUSTER="kube2run-demo"

echo "==> Deleting cluster $CLUSTER in $ZONE…"
gcloud container clusters delete "$CLUSTER" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet

echo "==> Cluster deleted."
