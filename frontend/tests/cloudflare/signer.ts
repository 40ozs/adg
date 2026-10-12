/**
 * A stand-in for Cloudflare Access's signing side: an RSA key pair, its published
 * certificate set, and RS256 assertions. Tests only.
 */

function encode(value: unknown): string {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

export class AccessSigner {
  private constructor(
    readonly kid: string,
    private readonly privateKey: CryptoKey,
    private readonly publicJwk: JsonWebKey,
  ) {}

  static async create(kid: string): Promise<AccessSigner> {
    const pair = await crypto.subtle.generateKey(
      {
        name: "RSASSA-PKCS1-v1_5",
        modulusLength: 2048,
        publicExponent: new Uint8Array([1, 0, 1]),
        hash: "SHA-256",
      },
      true,
      ["sign", "verify"],
    );
    return new AccessSigner(kid, pair.privateKey, await crypto.subtle.exportKey("jwk", pair.publicKey));
  }

  /** The `/cdn-cgi/access/certs` document. */
  certificates(): { keys: JsonWebKey[] } {
    return { keys: [{ ...this.publicJwk, kid: this.kid, alg: "RS256", use: "sig" } as JsonWebKey] };
  }

  fetcher(): (url: string) => Promise<Response> {
    return async () => Response.json(this.certificates());
  }

  async sign(
    claims: Record<string, unknown>,
    header: Record<string, unknown> = { alg: "RS256", kid: this.kid, typ: "JWT" },
  ): Promise<string> {
    const signingInput = `${encode(header)}.${encode(claims)}`;
    const signature = await crypto.subtle.sign(
      "RSASSA-PKCS1-v1_5",
      this.privateKey,
      new TextEncoder().encode(signingInput),
    );
    return `${signingInput}.${Buffer.from(signature).toString("base64url")}`;
  }
}
