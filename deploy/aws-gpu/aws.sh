#!/bin/bash
# Launch a spot GPU instance, run Ookami's full test on it, and tear everything down.
#   deploy/aws-gpu/aws.sh up | test | down | status
# Everything it creates is tagged project=ookami-test and named ookami-test.
set -euo pipefail
REGION=${REGION:-us-east-1}
TYPE=${TYPE:-g5.xlarge}
NAME=ookami-test
AWS=${AWS:-aws}
KEY=~/.ssh/$NAME.pem
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
STATE=$HERE/.state
a() { $AWS --region "$REGION" "$@"; }

up() {
  local ami ip sg vpc iid
  ami=$(a ssm get-parameter --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
        --query Parameter.Value --output text)
  if [ ! -f "$KEY" ]; then
    a ec2 create-key-pair --key-name $NAME --query KeyMaterial --output text > "$KEY"; chmod 600 "$KEY"
  fi
  vpc=$(a ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
  sg=$(a ec2 describe-security-groups --filters Name=group-name,Values=$NAME Name=vpc-id,Values=$vpc \
       --query 'SecurityGroups[0].GroupId' --output text)
  if [ "$sg" = "None" ]; then
    sg=$(a ec2 create-security-group --group-name $NAME --description "ookami test: ssh from one IP" --vpc-id "$vpc" \
         --tag-specifications "ResourceType=security-group,Tags=[{Key=project,Value=$NAME}]" --query GroupId --output text)
  fi
  ip=$(curl -s https://checkip.amazonaws.com)
  a ec2 authorize-security-group-ingress --group-id "$sg" --protocol tcp --port 22 --cidr "$ip/32" 2>/dev/null || true
  iid=$(a ec2 run-instances --image-id "$ami" --instance-type "$TYPE" --key-name $NAME --security-group-ids "$sg" \
        --instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}' \
        --instance-initiated-shutdown-behavior terminate \
        --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=150,VolumeType=gp3,DeleteOnTermination=true}' \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=project,Value=$NAME}]" \
        --query 'Instances[0].InstanceId' --output text)
  echo "IID=$iid" > "$STATE"; echo "SG=$sg" >> "$STATE"
  echo "launched $iid ($TYPE spot, $ami); waiting for it to run"
  a ec2 wait instance-running --instance-ids "$iid"
  ip=$(a ec2 describe-instances --instance-ids "$iid" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  echo "IP=$ip" >> "$STATE"
  until ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -i "$KEY" ubuntu@"$ip" true 2>/dev/null; do sleep 5; done
  echo "ready: ssh -i $KEY ubuntu@$ip"
}

test_() {
  source "$STATE"
  (cd "$ROOT" && rm -rf dist && uv build --wheel -q)
  ssh -i "$KEY" ubuntu@"$IP" 'mkdir -p ~/kit ~/wheel && rm -f ~/wheel/*'
  scp -q -i "$KEY" "$ROOT"/dist/*.whl ubuntu@"$IP":~/wheel/
  scp -q -i "$KEY" "$HERE"/ookami.yaml "$HERE"/make_data.py "$HERE"/test.sh ubuntu@"$IP":~/kit/
  ssh -i "$KEY" ubuntu@"$IP" 'sudo docker run --rm --gpus all --ipc=host --entrypoint bash \
      -v ~/kit:/kit -v ~/wheel:/wheel -v ~/hf:/root/.cache/huggingface vllm/vllm-openai:latest /kit/test.sh' \
      2>&1 | tee "$HERE/last-run.log"
}

down() {
  [ -f "$STATE" ] && source "$STATE" || true
  if [ -n "${IID:-}" ]; then
    a ec2 terminate-instances --instance-ids "$IID" >/dev/null && a ec2 wait instance-terminated --instance-ids "$IID"
    echo "terminated $IID"
  fi
  local sg
  sg=$(a ec2 describe-security-groups --filters Name=group-name,Values=$NAME --query 'SecurityGroups[0].GroupId' --output text)
  [ "$sg" != "None" ] && a ec2 delete-security-group --group-id "$sg" && echo "deleted security group $sg"
  a ec2 delete-key-pair --key-name $NAME && rm -f "$KEY" && echo "deleted key pair"
  rm -f "$STATE"
  echo "left running with tag project=$NAME:"
  a ec2 describe-instances --filters Name=tag:project,Values=$NAME Name=instance-state-name,Values=pending,running \
    --query 'Reservations[].Instances[].InstanceId' --output text
}

status() { [ -f "$STATE" ] && cat "$STATE"; a ec2 describe-instances --filters Name=tag:project,Values=$NAME \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,InstanceType,PublicIpAddress]' --output table; }

case "${1:-}" in
  up) up ;; test) test_ ;; down) down ;; status) status ;;
  *) echo "usage: $0 up|test|down|status"; exit 2 ;;
esac
