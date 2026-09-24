# Forge smoke test on Kubernetes (CPU)

Runs Forge's local backend inside one pod: llama.cpp serves Qwen2.5-0.5B (GGUF) behind a managed LiteLLM
gateway, then a chat call goes through the gateway and `forge eval` gates it. No GPU and no registry needed:
the wheel and config are mounted from ConfigMaps. This is not the Kubernetes backend (0.4); it checks that
the package installs and runs in a container on a cluster.

```bash
uv build --wheel
kubectl create namespace forge-test
kubectl -n forge-test create configmap forge-wheel --from-file=dist/
kubectl -n forge-test create configmap forge-smoke-config --from-file=deploy/k8s-smoke/
kubectl apply -f deploy/k8s-smoke/pod.yaml
kubectl -n forge-test logs -f forge-smoke      # ends with SMOKE-DONE
kubectl delete namespace forge-test
```
