"""Push the two locally built images to private ECR in the authenticated event account."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import tempfile

import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", default="/workshop/scram-image-manifest.json")
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != args.account_id:
        raise SystemExit("Refusing image publication in an unexpected AWS account")
    ecr = session.client("ecr")
    repos = {}
    for name in ("scram-api", "scram-web"):
        try:
            repo = ecr.describe_repositories(repositoryNames=[name])["repositories"][0]
        except ecr.exceptions.RepositoryNotFoundException:
            repo = ecr.create_repository(repositoryName=name, imageTagMutability="IMMUTABLE",
                imageScanningConfiguration={"scanOnPush": True},
                encryptionConfiguration={"encryptionType": "AES256"},
                tags=[{"Key": "Project", "Value": "SCRAM"}, {"Key": "Purpose", "Value": "event-demo"}])["repository"]
        repos[name] = repo["repositoryUri"]
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    manifest = {"region": args.region, "tag": args.tag, "images": {}}
    # ECR's short-lived login exists only for this push and is deleted on exit.
    with tempfile.TemporaryDirectory(prefix="scram-ecr-") as docker_config:
        docker = ["docker", "--config", docker_config]
        subprocess.run([*docker, "login", "--username", user, "--password-stdin", auth["proxyEndpoint"]],
                       input=password, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        for name, uri in repos.items():
            tagged = f"{uri}:{args.tag}"
            subprocess.run([*docker, "tag", f"{name}:bedrock", tagged], check=True)
            subprocess.run([*docker, "push", tagged], check=True)
            detail = ecr.describe_images(repositoryName=name, imageIds=[{"imageTag": args.tag}])["imageDetails"][0]
            manifest["images"][name] = f"{uri}@{detail['imageDigest']}"
    Path(args.output).write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
