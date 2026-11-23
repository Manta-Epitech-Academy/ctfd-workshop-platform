# Sourced by tools/upload_runtime.sh and tools/upload_subject.sh: the `aws`
# CLI against the content bucket, from the environment
#   RUNTIMES_S3_ENDPOINT    https://… (required)
#   RUNTIMES_S3_BUCKET      bucket name (required)
#   RUNTIMES_S3_REGION      region string the endpoint wants (default us-east-1)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   a key limited to that bucket
# Defines `aws` (the CLI when installed — GitHub runners have it — else
# amazon/aws-cli in Docker), `s3_mount <host-dir> <in-dir>` to make a
# directory visible to the Docker fallback before the first call, and
# S3_IN_DOCKER so a caller knows which paths the CLI sees.
: "${RUNTIMES_S3_ENDPOINT:?set RUNTIMES_S3_ENDPOINT}"
: "${RUNTIMES_S3_BUCKET:?set RUNTIMES_S3_BUCKET}"
: "${AWS_ACCESS_KEY_ID:?set AWS_ACCESS_KEY_ID}"
: "${AWS_SECRET_ACCESS_KEY:?set AWS_SECRET_ACCESS_KEY}"
export AWS_DEFAULT_REGION="${RUNTIMES_S3_REGION:-us-east-1}"
export AWS_EC2_METADATA_DISABLED=true

# Path-style requests, which every S3-compatible service accepts (a bucket
# name with a dot breaks virtual-host TLS otherwise). The CLI reads that from
# its config file only, so write a private one rather than touch ~/.aws.
S3_CONF="$(mktemp)"
trap 'rm -f "$S3_CONF"' EXIT
printf '[default]\ns3 =\n  addressing_style = path\n' > "$S3_CONF"

S3_MOUNTS=(-v "$S3_CONF:/aws-config:ro")
s3_mount() { S3_MOUNTS+=(-v "$1:$2:ro"); }

if command -v aws >/dev/null 2>&1; then
  S3_IN_DOCKER=0
  aws() { AWS_CONFIG_FILE="$S3_CONF" command aws --endpoint-url "$RUNTIMES_S3_ENDPOINT" "$@"; }
else
  # Only what was s3_mount'ed is visible, read-only. Values are passed
  # explicitly: a `docker` that goes through sudo drops the caller's env.
  S3_IN_DOCKER=1
  aws() {
    docker run --rm --network host "${S3_MOUNTS[@]}" \
      -e "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID" -e "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY" \
      -e "AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION" -e AWS_EC2_METADATA_DISABLED=true -e AWS_CONFIG_FILE=/aws-config \
      amazon/aws-cli --endpoint-url "$RUNTIMES_S3_ENDPOINT" "$@"
  }
fi
