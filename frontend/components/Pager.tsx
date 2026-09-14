import type { JSX } from "react";
import Link from "next/link";

import type { PageInfo } from "@/lib/contracts";
import type { PageNavigation, Trail } from "@/lib/paging";

/**
 * Moving through a list the API pages server-side.
 *
 * Two rules about what this may say.
 *
 * **It never claims a total it was not given.** `page.total` is null whenever the endpoint
 * cannot count cheaply, and null means not counted — never zero. A pager that rendered
 * "1–100 of 100" from a full page would turn a truncated listing into a complete one, which
 * in an audit tool is a missed finding rather than a cosmetic bug.
 *
 * **It never renders a link it cannot reach.** "Next" exists only when the response
 * carried a cursor; the rest of the time it is inert text, so the end of a list looks like
 * the end of a list.
 *
 * Where the reader *is* — and how to get back to the start of a list — is `PagePosition`,
 * which sits above the data because it has to be readable when the data did not arrive.
 */
export function Pager({
  navigation,
  page,
  subject,
}: {
  navigation: PageNavigation;
  page: PageInfo;
  /** Plural noun for what is being paged: "members", "entries", "shares". */
  subject: string;
}): JSX.Element {
  const showing =
    page.total === null
      ? `Up to ${page.limit} ${subject} per page. ADG did not count the total for this list.`
      : `${page.total} ${subject} in total, up to ${page.limit} per page.`;

  return (
    <nav className="pager" aria-label={`${subject} pages`}>
      <p className="muted">
        Page {navigation.pageNumber}. {showing}
        {page.has_more && navigation.nextHref === null && (
          <> More {subject} exist beyond this page, but the API sent no cursor to reach them.</>
        )}
      </p>
      <p className="pager-links">
        {navigation.firstHref && <Link href={navigation.firstHref}>First page</Link>}
        {navigation.previousHref ? (
          <Link href={navigation.previousHref} rel="prev">
            Previous
          </Link>
        ) : (
          <span className="muted">Previous</span>
        )}
        {navigation.nextHref ? (
          <Link href={navigation.nextHref} rel="next">
            Next
          </Link>
        ) : (
          <span className="muted">Next</span>
        )}
      </p>
    </nav>
  );
}

/**
 * Where in a list the reader is, and the way back to its start.
 *
 * Above the data rather than below it, because it has to be readable when the data did not
 * arrive. A cursor is opaque and only the API can judge one: a hand-edited link that still
 * looks like base64url reaches the API, which refuses it with a 422 and no rows — and
 * without this line the page would then show an error and no way out of it.
 *
 * Silent is the one thing it must not be. A trail that does not parse resets to page one,
 * and a page one displayed as somebody's page four turns a short list into the end of a
 * list.
 */
export function PagePosition({
  trail,
  firstPageHref,
}: {
  trail: Trail;
  firstPageHref: string;
}): JSX.Element | null {
  if (!trail.rejected && trail.cursors.length === 0) {
    return null;
  }

  if (trail.rejected) {
    return (
      <p className="status-warn page-position" role="alert">
        The page position in this link was not one ADG issued, so this is page 1. Do not read
        it as the page you came from. <Link href={firstPageHref}>Start from the first page.</Link>
      </p>
    );
  }

  return (
    <p className="muted page-position">
      Page {trail.cursors.length + 1} of this list.{" "}
      <Link href={firstPageHref}>Back to the first page.</Link>
    </p>
  );
}
