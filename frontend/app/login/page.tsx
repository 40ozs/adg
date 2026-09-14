import { fetchAuthConfig } from "@/lib/api/adg";
import { DevelopmentLoginForm } from "@/app/login/LoginForm";

/**
 * Sign in.
 *
 * The page renders whichever of the two modes the API reports, and says which one it is in
 * so many words. The two look nothing alike on purpose: a development sign-in is a list of
 * names with no credential, and it is labelled as exactly that.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; detail?: string }>;
}) {
  const [config, params] = await Promise.all([fetchAuthConfig(), searchParams]);

  return (
    <>
      <h1>Sign in</h1>

      {params.error && (
        <div className="banner banner-error" role="alert">
          <h2>Sign-in did not complete</h2>
          <p>{params.detail ?? "The identity provider returned an error."}</p>
          <p className="muted">
            Reference: <code>{params.error}</code>
          </p>
        </div>
      )}

      {!config.ok ? (
        <div className="banner banner-error" role="alert">
          <h2>ADG cannot reach its API</h2>
          <p>{config.failure.message}</p>
          <p className="muted">
            Nobody can sign in until the API answers. This is not a credential problem.
          </p>
        </div>
      ) : config.data.development ? (
        <div className="card">
          <h2>Development authentication</h2>
          <p>
            This deployment issues its own tokens and verifies no credential. Choose an
            account to see the product as that role sees it. The API refuses to start this
            way with <code>ADG_ENVIRONMENT=production</code>.
          </p>
          <DevelopmentLoginForm accounts={config.data.development_accounts} />
        </div>
      ) : (
        <div className="card">
          <h2>Sign in with your organization account</h2>
          <p>
            This deployment authenticates against{" "}
            <code>{config.data.issuer ?? "the configured identity provider"}</code>.
          </p>
          <p>
            <a className="button" href="/api/auth/oidc/start">
              Continue to your identity provider
            </a>
          </p>
          <p className="muted">
            Access is granted by app role or group assignment in the tenant. An account with
            no ADG role can sign in and will see nothing.
          </p>
        </div>
      )}

      {config.ok && (
        <div className="card">
          <h2>Roles</h2>
          <p className="muted">
            Published by the API, so this list is the authorization model itself rather than
            a description of it.
          </p>
          <div className="table-scroll">
            <table>
              <caption>Roles and the capabilities they grant.</caption>
              <thead>
                <tr>
                  <th scope="col">Role</th>
                  <th scope="col">State</th>
                  <th scope="col">Capabilities</th>
                </tr>
              </thead>
              <tbody>
                {config.data.roles.map((role) => (
                  <tr key={role.role}>
                    <th scope="row">{role.role}</th>
                    <td className={role.active ? "status-ok" : "muted"}>
                      {role.active ? "active" : "reserved"}
                    </td>
                    <td>{role.capabilities.length > 0 ? role.capabilities.join(", ") : "none"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
