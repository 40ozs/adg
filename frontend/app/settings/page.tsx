import { fetchAuthConfig } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { SortableTable } from "@/components/SortableTable";

/**
 * Settings.
 *
 * Read-only in this phase, and it shows the two things an operator diagnosing an access
 * problem actually needs: how this deployment authenticates, and what each role grants.
 *
 * Nothing secret is shown because nothing secret is available — `/auth/config` publishes
 * only what a public client already needs to start a sign-in. The page is gated on
 * `settings:read` all the same, since the issuer and client id tell an attacker which
 * tenant to aim at.
 */
export default async function SettingsPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  if (!viewer.principal.capabilities.includes("settings:read")) {
    return (
      <>
        <h1>Settings</h1>
        <div className="banner banner-warning" role="alert">
          <h2>Your account cannot see this</h2>
          <p>
            Settings requires the <code>settings:read</code> capability, which the auditor
            and admin roles grant. This account holds:{" "}
            {viewer.principal.roles.join(", ") || "no role"}.
          </p>
          <p className="muted">
            Hiding this page would not be a control; the API refuses the data either way.
          </p>
        </div>
      </>
    );
  }

  const config = await fetchAuthConfig();

  return (
    <>
      <h1>Settings</h1>

      {!config.ok ? (
        <div className="banner banner-error" role="alert">
          <h2>ADG cannot read its configuration</h2>
          <p>{config.failure.message}</p>
        </div>
      ) : (
        <>
          <div className="card">
            <h2>Authentication</h2>
            <dl className="facts">
              <dt>Mode</dt>
              <dd>
                {config.data.mode}
                {config.data.development && (
                  <>
                    {" "}
                    <span className="status-warn">
                      — development only; verifies no credential
                    </span>
                  </>
                )}
              </dd>
              <dt>Environment</dt>
              <dd>{config.data.environment}</dd>
              <dt>Issuer</dt>
              <dd>{config.data.issuer ?? "—"}</dd>
              <dt>Client id</dt>
              <dd>{config.data.client_id ?? "—"}</dd>
              <dt>Scopes</dt>
              <dd>{config.data.scopes.join(" ") || "—"}</dd>
            </dl>
          </div>

          <div className="card">
            <h2>Roles and capabilities</h2>
            <p className="muted">
              Published by the API. This is the authorization model itself, not a description
              of it — the same table the backend enforces with.
            </p>
            <SortableTable
              caption="A reserved role is provisioned for a future capability and grants nothing."
              columns={[{ label: "Role" }, { label: "State" }, { label: "Capabilities" }]}
              rows={config.data.roles.map((role) => ({
                key: role.role,
                sort: [role.role, role.active, role.capabilities.join(", ") || null],
                cells: [
                  role.role,
                  <span key="state" className={role.active ? "status-ok" : "muted"}>
                    {role.active ? "active" : "reserved"}
                  </span>,
                  role.capabilities.length > 0 ? role.capabilities.join(", ") : "none",
                ],
              }))}
            />
          </div>
        </>
      )}

      <div className="card">
        <h2>This account</h2>
        <dl className="facts">
          <dt>Subject</dt>
          <dd>
            <code>{viewer.principal.subject}</code>
          </dd>
          <dt>Roles</dt>
          <dd>{viewer.principal.roles.join(", ") || "none"}</dd>
          <dt>Capabilities</dt>
          <dd>{viewer.principal.capabilities.join(", ") || "none"}</dd>
          <dt>Credential</dt>
          <dd>{viewer.principal.source}</dd>
          <dt>Session expires</dt>
          <dd>{viewer.principal.expires_at ?? "—"}</dd>
        </dl>
      </div>
    </>
  );
}
