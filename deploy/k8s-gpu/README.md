# Ookami GPU test on AKS (spot T4)

The full loop on one NVIDIA GPU: TRL LoRA fine-tune, vLLM serving with LoRA, gate vs the base model, promote,
and a call through the gateway. Needs Azure spot quota of at least 4 vCPUs in the cluster's region
(Portal: Quotas > Compute > "Total Regional Spot vCPUs").

```bash
# 1. a spot T4 pool that scales from zero and only takes pods that ask for it
az aks nodepool add -g <rg> --cluster-name <cluster> -n gpuspot --node-vm-size Standard_NC4as_T4_v3 \
  --priority Spot --eviction-policy Delete --spot-max-price -1 \
  --enable-cluster-autoscaler --min-count 0 --max-count 1 --node-count 1 \
  --node-taints nvidia.com/gpu=present:NoSchedule
kubectl get nodes -l agentpool=gpuspot -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}'
# if that prints nothing, install the NVIDIA device plugin (see AKS GPU docs)

# 2. run it
uv build --wheel
kubectl create namespace ookami-test
kubectl -n ookami-test create configmap ookami-wheel --from-file=dist/
kubectl -n ookami-test create configmap ookami-gpu-config --from-file=deploy/k8s-gpu/
kubectl apply -f deploy/k8s-gpu/pod.yaml
kubectl -n ookami-test logs -f ookami-gpu        # ends with GPU-TEST-DONE

# 3. clean up (the pool scales to zero on its own; delete it to be sure nothing bills)
kubectl delete namespace ookami-test
az aks nodepool delete -g <rg> --cluster-name <cluster> -n gpuspot
```
