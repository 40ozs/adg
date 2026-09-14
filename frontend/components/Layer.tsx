import type { JSX, ReactNode } from "react";

import { EFFECTIVE_ACCESS_NOTICE, RAW_ACL_NOTICE } from "@/lib/acl";

/**
 * The two kinds of thing this product shows, and the frame that keeps them apart.
 *
 * A raw ACL is a descriptor fact. An effective-access result is an engine answer. Putting
 * them in two tables that look alike is how an auditor concludes that a group on an ACL
 * has access — the single most common wrong reading in this domain, and the reason ADG
 * exists at all.
 *
 * So they never share a frame. Each section is labelled with what kind of statement it is,
 * carries its own caveat wording from `lib/acl.ts`, and is tinted differently; the label
 * is a word, not only a colour, because the colour is not available to every reader.
 */
export function RawLayer({
  title,
  id,
  layer,
  children,
}: {
  title: string;
  id: string;
  /** Which descriptor these entries came off: the share, or the file system. */
  layer: "SMB share" | "NTFS";
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="layer layer-raw" aria-labelledby={id}>
      <header className="layer-header">
        <h2 id={id}>{title}</h2>
        <p className="layer-tag">Raw {layer} ACL — as collected</p>
      </header>
      <p className="muted layer-caveat">{RAW_ACL_NOTICE}</p>
      {children}
    </section>
  );
}

export function EffectiveLayer({
  title,
  id,
  children,
}: {
  title: string;
  id: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="layer layer-effective" aria-labelledby={id}>
      <header className="layer-header">
        <h2 id={id}>{title}</h2>
        <p className="layer-tag">Effective access — computed answer</p>
      </header>
      <p className="muted layer-caveat">{EFFECTIVE_ACCESS_NOTICE}</p>
      {children}
    </section>
  );
}

/** A plain section for collected metadata that is neither an ACL nor an answer. */
export function FactSection({
  title,
  id,
  children,
}: {
  title: string;
  id: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="card" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {children}
    </section>
  );
}
