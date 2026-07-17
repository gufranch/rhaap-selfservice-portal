# Troubleshooting

Every issue below was hit and root-caused for real — either in the lab this
repo is based on, or in a production support case on the same stack
(portal chart 2.2.x / RHDH 1.9 / AAP 2.6).

## 1. Controller route shows raw `<% if (process.env.NODE_ENV === 'production') { %>`

You are browsing the **automation controller** route. Since AAP 2.5 the
controller has no standalone web UI — its route serves an unrendered template
shell. The unified UI (login, Access Management, OAuth Applications) is served
**only by the platform gateway route** (the route named after your
`AnsibleAutomationPlatform` CR, without the `-controller` suffix).

Also make sure you actually created an `AnsibleAutomationPlatform` CR: a
standalone `AutomationController` CR gives you a working API but no UI and no
gateway — and the portal authenticates against the gateway.

## 2. Init container fails: `getaddrinfo ENOTFOUND plugin-registry`

The chart's default `pluginMode` is `tarball`, which downloads plugins from an
in-cluster HTTP service called `plugin-registry` that you would have to deploy
separately (the "plug-in registry" from the disconnected-install docs). If it
does not exist, `install-dynamic-plugins` fails with this error.

Fix: set `redhat-developer-hub.global.pluginMode: oci` (already in
`values/portal-values.yaml`) so plugins are pulled directly from
registry.redhat.io. Requires the `<release>-dynamic-plugins-registry-auth`
secret (see `scripts/create-secrets.sh`).

## 3. Init container fails with `connection reset by peer` or x509 errors

Restricted-network clusters only. The init container runs skopeo *inside* the
container, which inherits neither the cluster-wide proxy nor the node CA trust:

- **Proxy**: add `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` to the init container's
  own `env:` list under `redhat-developer-hub.upstream.backstage.initContainers`.
  The chart defines that block fully, and Helm replaces lists — copy the whole
  block from the chart values and append the proxy variables; a partial
  override silently removes the rest of it.
- **TLS-inspecting proxy (x509: certificate signed by unknown authority)**:
  mount your corporate CA as a ConfigMap at
  `/etc/containers/certs.d/<registry-host>/` in the init container. The file
  key must end in `.crt`. The main container is Node.js and needs
  `NODE_EXTRA_CA_CERTS` pointing at a mounted PEM instead.

## 4. 404 "We couldn't find that page" after a successful login

This is **RBAC permission gating, not a routing problem**. Every portal page
(Templates, History, Collections, Execution Environments, Git Repositories)
and its sidebar entry is wrapped in a permission check on one of:

```
ansible.templates.view
ansible.history.view
ansible.collections.view
ansible.execution-environments.view
ansible.git-repositories.view
```

A user with none of them sees an empty sidebar (plus Administration → RBAC if
they are an RBAC admin) and the home route falls through to the 404 page.

Confirm from the backend log — the permission audit events show exactly what
was evaluated and the result:

```bash
oc logs deploy/redhat-rhaap-portal -n aap -c backstage-backend \
  | grep permission-evaluation \
  | grep -o '"userEntityRef":"[^"]*","permissionName":"[^"]*".*"result":"[^"]*"' \
  | sort | uniq -c | sort -rn
```

Fixes: see the RBAC section of the README (superusers and/or the
`portal-users` role including the five `ansible.*.view` permissions).

### Known limitation: `group:` entries in `superUsers` do nothing

In the RBAC backend shipped with portal 2.2 / RHDH 1.9
(`@backstage-community/plugin-rbac-backend` 7.6.2), the superuser check
matches the individual `userEntityRef` only — it does not expand group
membership. `group:default/aap-admins` in `superUsers` is therefore inert:
only `user:default/admin` (listed individually) bypasses RBAC, and every other
platform administrator gets the 404 until listed individually or granted a
role. Group references DO work for role assignments. Newer upstream versions
of the plugin add group expansion to the superuser check.

### Known docs gap: the official RBAC procedure is not sufficient

The documented initial-RBAC procedure grants only `catalog.entity.read` plus
the scaffolder permissions. That alone does not make the pages render — the
five `ansible.*.view` permissions above are also required. They appear in the
RBAC UI under the **Catalog** plugin (they are registered by the AAP catalog
backend module), see `docs/images/rbac-create-role-catalog-permissions.png`.

## 5. Templates page shows "No templates found" (even for admin)

The job-template sync only reads the AAP organization configured under
`catalog.providers.rhaap.<env>.orgs` (default `Default`). Check the log:

```
[plugin-catalog-rh-aap]: Fetched 0 job templates.
```

means the sync works but that organization has no job templates. Either create
the job templates in that organization or point `orgs:` at the right one.
Additional notes:

- The scheduled providers run **hourly** with a 30s timeout; after a fresh
  deployment the first run can take a while. Use the **Sync now** button on
  the Templates page (as an admin), or `oc rollout restart
  deploy/redhat-rhaap-portal -n aap` to force it.
- Once synced, each user only sees auto-generated templates for AAP job
  templates they hold **Execute** permissions on in AAP itself.

## 6. Login fails or loops

- The OAuth application's **Redirect URI** must match the portal route —
  `https://<portal-route>/api/auth/rhaap/handler/frame`.
- `Allow external users to create OAuth2 tokens` must be enabled in
  Settings → Platform gateway (default on recent 2.6 builds).
- With self-signed ingress certificates, `checkSSL: false` must be set in
  BOTH places (`ansible.rhaap.checkSSL` and
  `auth.providers.rhaap.production.checkSSL`) — `values/portal-values.yaml`
  does this.

## 7. Image pulls fail for the plugin OCI artifacts

- The registry auth secret must be named exactly
  `<release-name>-dynamic-plugins-registry-auth`.
- The plugin artifact is `registry.redhat.io/ansible-automation-platform/automation-portal`
  and its tags follow the **portal plugin** versioning (2.0.x / 2.1.x / 2.2.x) —
  NOT the AAP version. There is no `2.6` tag; `imageTagInfo: '2.2'` is correct
  for AAP 2.6. Available tags:
  `curl -s "https://catalog.redhat.com/api/containers/v1/repositories/registry/registry.access.redhat.com/repository/ansible-automation-platform/automation-portal/images?page_size=100&include=data.repositories.tags.name"`
