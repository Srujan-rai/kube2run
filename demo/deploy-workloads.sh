#!/usr/bin/env bash
set -euo pipefail

echo "==> Deploying Cloud Run COMPATIBLE workloads…"
kubectl apply -f k8s/compatible/api-service.yaml
kubectl apply -f k8s/compatible/worker-service.yaml

echo ""
echo "==> Deploying Cloud Run INCOMPATIBLE workloads…"
kubectl apply -f k8s/incompatible/legacy-app.yaml
kubectl apply -f k8s/incompatible/stateful-cache.yaml
kubectl apply -f k8s/incompatible/grpc-service.yaml

echo ""
echo "==> Waiting for deployments to roll out…"
kubectl rollout status deployment/api-service     --timeout=120s
kubectl rollout status deployment/worker-service  --timeout=120s
kubectl rollout status deployment/legacy-app      --timeout=120s
kubectl rollout status deployment/stateful-cache  --timeout=120s
kubectl rollout status deployment/grpc-service    --timeout=120s

echo ""
echo "==> All workloads deployed."
kubectl get deployments,services,hpa,networkpolicies,pvc
