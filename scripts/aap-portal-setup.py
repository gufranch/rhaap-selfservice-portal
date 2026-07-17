#!/usr/bin/env python3
"""Configure the AAP gateway for the self-service portal, via the gateway API.

Creates:
  - an OAuth application (authorization-code / confidential) with the portal
    callback URL as redirect URI,
  - a personal access token (write scope) for catalog synchronization,
  - optionally test users (--test-users): `testadmin` (platform superuser)
    and `testuser` (member of the Default organization).

Writes the values the portal needs to portal-creds.json (git-ignored) —
consume it with scripts/create-secrets.sh.

Usage:
  export AAP_HOST=aap-aap.apps.<cluster-domain>          # gateway route host
  export PORTAL_HOST=redhat-rhaap-portal-<namespace>.apps.<cluster-domain>
  export AAP_PW=$(oc extract secret/aap-admin-password -n aap --to=-)
  python3 scripts/aap-portal-setup.py [--test-users]
"""
import json, os, ssl, sys, base64, secrets, string
from urllib import request, error

AAP_HOST = os.environ["AAP_HOST"]
PORTAL_URL = "https://" + os.environ["PORTAL_HOST"]
PW = os.environ["AAP_PW"]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE  # lab clusters use self-signed ingress certs
AUTH = "Basic " + base64.b64encode(f"admin:{PW}".encode()).decode()


def api(method, path, payload=None):
    req = request.Request(f"https://{AAP_HOST}{path}", method=method)
    req.add_header("Authorization", AUTH)
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with request.urlopen(req, data=data, context=CTX) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if body else {}
    except error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


out = {"aap-host-url": f"https://{AAP_HOST}"}

# Sanity check + Default org id
status, orgs = api("GET", "/api/gateway/v1/organizations/?name=Default")
assert status == 200 and orgs.get("results"), f"gateway API not reachable as admin: {status}"
org_id = orgs["results"][0]["id"]

# 1. OAuth application. The client_secret is only shown once at creation —
#    if the app already exists, delete it in Access Management and rerun.
status, app = api("POST", "/api/gateway/v1/applications/", {
    "name": "self-service-portal",
    "organization": org_id,
    "authorization_grant_type": "authorization-code",
    "client_type": "confidential",
    "redirect_uris": f"{PORTAL_URL}/api/auth/rhaap/handler/frame {PORTAL_URL}",
})
if status != 201:
    sys.exit(f"OAuth application creation failed ({status}): {app}")
out["oauth-client-id"] = app["client_id"]
out["oauth-client-secret"] = app["client_secret"]

# 2. Personal access token for the catalog sync
status, tok = api("POST", "/api/gateway/v1/tokens/", {"scope": "write", "description": "portal sync"})
assert status == 201, (status, tok)
out["aap-token"] = tok["token"]

# 3. Optional test users
if "--test-users" in sys.argv:
    testpw = "Portal-" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10)) + "!"
    users = {}
    for uname, superuser in (("testadmin", True), ("testuser", False)):
        status, u = api("POST", "/api/gateway/v1/users/", {
            "username": uname, "password": testpw, "is_superuser": superuser,
            "email": f"{uname}@example.com", "first_name": uname.capitalize(),
        })
        print(f"user {uname}: {'created' if status == 201 else u}")
        users[uname] = u.get("id")
    # make testuser a member of the Default org (portal only syncs users of the configured org)
    status, roles = api("GET", "/api/gateway/v1/role_definitions/?name=Organization%20Member")
    if roles.get("results") and users.get("testuser"):
        api("POST", "/api/gateway/v1/role_user_assignments/", {
            "role_definition": roles["results"][0]["id"],
            "user": users["testuser"], "object_id": str(org_id),
        })
    out["test-users-password"] = testpw

path = os.path.join(os.path.dirname(__file__), "..", "portal-creds.json")
with open(path, "w") as f:
    json.dump(out, f, indent=1)
os.chmod(path, 0o600)
print(f"\nWrote {os.path.abspath(path)} — now run scripts/create-secrets.sh")
