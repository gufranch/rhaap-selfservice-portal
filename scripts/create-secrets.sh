#!/usr/bin/env bash
# Creates the two Kubernetes secrets the portal chart needs:
#
#  1. secrets-rhaap-portal — AAP connection values consumed by the chart's
#     extraEnvVars (aap-host-url, oauth-client-id, oauth-client-secret, aap-token).
#     Values come from portal-creds.json written by scripts/aap-portal-setup.py.
#
#  2. <release>-dynamic-plugins-registry-auth — registry.redhat.io credentials
#     for the install-dynamic-plugins init container (skopeo). The secret NAME
#     is hardcoded by the chart to "<release-name>-dynamic-plugins-registry-auth".
#     Source (pick one):
#       a) an existing auth.json from `podman login registry.redhat.io --authfile auth.json`
#          → export REGISTRY_AUTH_FILE=/path/to/auth.json
#       b) derived from the cluster global pull secret (works on ROSA/OSD and
#          most managed clusters; requires read access to openshift-config).
set -euo pipefail

NAMESPACE="${NAMESPACE:-aap}"
RELEASE="${RELEASE:-redhat-rhaap-portal}"
CREDS="$(dirname "$0")/../portal-creds.json"

[ -f "$CREDS" ] || { echo "portal-creds.json not found — run scripts/aap-portal-setup.py first"; exit 1; }

echo "==> Creating secret secrets-rhaap-portal in namespace $NAMESPACE"
oc create secret generic secrets-rhaap-portal -n "$NAMESPACE" \
  --from-literal=aap-host-url="$(python3 -c "import json;print(json.load(open('$CREDS'))['aap-host-url'])")" \
  --from-literal=oauth-client-id="$(python3 -c "import json;print(json.load(open('$CREDS'))['oauth-client-id'])")" \
  --from-literal=oauth-client-secret="$(python3 -c "import json;print(json.load(open('$CREDS'))['oauth-client-secret'])")" \
  --from-literal=aap-token="$(python3 -c "import json;print(json.load(open('$CREDS'))['aap-token'])")"

echo "==> Creating secret ${RELEASE}-dynamic-plugins-registry-auth"
TMP_AUTH="$(mktemp)"
trap 'rm -f "$TMP_AUTH"' EXIT
if [ -n "${REGISTRY_AUTH_FILE:-}" ]; then
  cp "$REGISTRY_AUTH_FILE" "$TMP_AUTH"
else
  echo "    (deriving registry.redhat.io entry from the cluster global pull secret)"
  oc get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | \
    python3 -c "import json,sys; d=json.load(sys.stdin)['auths']; json.dump({'auths':{'registry.redhat.io':d['registry.redhat.io']}}, sys.stdout)" > "$TMP_AUTH"
fi
oc create secret generic "${RELEASE}-dynamic-plugins-registry-auth" \
  --from-file=auth.json="$TMP_AUTH" -n "$NAMESPACE"

echo "==> Done"
