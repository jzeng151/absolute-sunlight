"""Validate and prepare a CloudFormation change set. This never executes it."""
import argparse
import json
from pathlib import Path
import secrets

import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--viewer-ip", required=True)
    parser.add_argument("--vpc-id", required=True)
    parser.add_argument("--subnets", required=True, help="Comma-separated public subnet IDs in different AZs")
    parser.add_argument("--prefix-list-id", required=True)
    parser.add_argument("--region", default="us-west-2", choices=["us-west-2"])
    parser.add_argument("--stack", default="scram-event-demo")
    parser.add_argument("--manifest", default="/workshop/scram-image-manifest.json")
    parser.add_argument("--output", default="/workshop/scram-deployment-plan.json")
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    if account != args.account_id:
        raise SystemExit("Refusing deployment plan in an unexpected AWS account")
    manifest = json.loads(Path(args.manifest).read_text())
    ec2 = session.client("ec2")
    subnets = ec2.describe_subnets(SubnetIds=args.subnets.split(","))["Subnets"]
    if len({s["AvailabilityZone"] for s in subnets}) < 2 or any(s["VpcId"] != args.vpc_id for s in subnets):
        raise SystemExit("Select subnets in at least two AZs of the supplied VPC")
    cfn = session.client("cloudformation")
    template = Path(__file__).with_name("cloudformation.yaml").read_text()
    cfn.validate_template(TemplateBody=template)
    params = {"ApiImage": manifest["images"]["scram-api"], "WebImage": manifest["images"]["scram-web"],
              "TaskArchitecture": "ARM64", "VpcId": args.vpc_id, "PublicSubnetIds": args.subnets,
              "CloudFrontPrefixListId": args.prefix_list_id, "AllowedViewerIp": args.viewer_ip,
              "OriginVerifyHeader": secrets.token_hex(24)}
    created = cfn.create_change_set(StackName=args.stack, ChangeSetName="bedrock-" + manifest["tag"],
        ChangeSetType="CREATE", TemplateBody=template, Capabilities=["CAPABILITY_IAM"],
        Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()],
        Tags=[{"Key": "Project", "Value": "SCRAM"}, {"Key": "Purpose", "Value": "event-demo"}])
    cfn.get_waiter("change_set_create_complete").wait(ChangeSetName=created["Id"])
    change_set = cfn.describe_change_set(ChangeSetName=created["Id"])
    plan = {"account_id": account, "region": args.region, "stack": args.stack,
            "change_set_arn": created["Id"], "status": change_set["Status"],
            "viewer_ip": args.viewer_ip, "images": manifest["images"],
            "resources": [{"action": c["ResourceChange"]["Action"],
                           "id": c["ResourceChange"]["LogicalResourceId"],
                           "type": c["ResourceChange"]["ResourceType"]} for c in change_set["Changes"]]}
    Path(args.output).write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
