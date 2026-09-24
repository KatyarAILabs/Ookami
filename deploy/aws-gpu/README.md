# Forge on an AWS spot GPU

Runs the whole of Forge on one NVIDIA GPU:
- validate the config;
- serve with vLLM behind LiteLLM;
- fine-tune with TRL (LoRA);
- gate against the base with vLLM multi-LoRA;
- promote the trained version;
- serve it and route tickets through the gateway.

Uses one `g5.xlarge` spot instance (A10G, 24 GB) on the Deep Learning AMI; the test runs inside the `vllm/vllm-openai` container.

```bash
deploy/aws-gpu/aws.sh up       # key pair, SSH-from-your-IP security group, spot instance
deploy/aws-gpu/aws.sh test     # builds the wheel, copies it over, runs test.sh; log in last-run.log
deploy/aws-gpu/aws.sh down     # terminates the instance, deletes the security group and key pair
```

- **Settings:** `REGION` (default us-east-1), `TYPE` (default g5.xlarge; g4dn.xlarge for a T4) and `AWS` (the CLI command).
- **Quota:** the account needs "All G and VT Spot Instance Requests" quota of at least 4 vCPUs in the region.
