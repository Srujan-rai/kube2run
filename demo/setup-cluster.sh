#!/usr/bin/env bash
set -euo pipefail

PROJECT="${GCP_PROJECT:-project-45a721e5-5e18-42e7-851}"
REGION="${GCP_REGION:-us-central1}"
ZONE="${GCP_ZONE:-us-central1-a}"
CLUSTER="kube2run-demo"

echo "==> Project : $PROJECT"
echo "==> Zone    : $ZONE"
echo "==> Cluster : $CLUSTER"

# Enable required APIs
gcloud services enable container.googleapis.com --project="$PROJECT" --quiet

# Create a minimal 2-node GKE cluster (cheapest: e2-small)
gcloud container clusters create "$CLUSTER" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --num-nodes=2 \
  --machine-type=e2-small \
  --disk-size=20 \
  --no-enable-autoupgrade \
  --no-enable-autorepair \
  --quiet

# Fetch credentials into kubeconfig
gcloud container clusters get-credentials "$CLUSTER" \
  --zone="$ZONE" \
  --project="$PROJECT"

echo ""
echo "==> Cluster ready. Context: $(kubectl config current-context)"
