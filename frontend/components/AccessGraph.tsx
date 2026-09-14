import type { JSX } from "react";

import type { AccessExplanationResponse } from "@/lib/derived";
import {
  GRAPH_GEOMETRY,
  layoutGraph,
  nodeKindLabel,
  nodeLabel,
  type GraphLayout,
} from "@/lib/explanation";
import styles from "@/components/explanation.module.css";

/**
 * `User → Group(s) → ACE → Resource`, drawn.
 *
 * Inline SVG and no charting library: the layout is a deterministic function in
 * `lib/explanation.ts` that is asserted against the same node and edge lists the table
 * renders, so the diagram cannot drift from the facts or from the text beside it.
 *
 * **The diagram is deliberately not the control surface.** It is `role="img"` with a
 * written summary, and its internals are hidden from assistive technology — the route
 * table below it carries a real button per route and is where keyboard selection happens.
 * A half-built SVG widget that can be focused but not navigated is worse than an honest
 * picture beside a real table, and the acceptance criterion this screen is written against
 * is that accessibility must not depend on the visualization at all.
 *
 * Clicking a node or an edge still selects the routes through it, because for a mouse user
 * that is the fastest way in, and selection state lives in the parent so both views move
 * together.
 */
export function AccessGraph({
  explanation,
  selectedPathId,
  onSelectPath,
}: {
  explanation: AccessExplanationResponse;
  selectedPathId: string | null;
  onSelectPath: (pathId: string | null) => void;
}): JSX.Element {
  const layout = layoutGraph(explanation.graph);
  const selected = explanation.paths.find((path) => path.id === selectedPathId) ?? null;
  const onPathNodes = new Set(selected?.nodes ?? []);
  const onPathEdges = new Set(selected?.edges ?? []);

  const { nodeWidth, nodeHeight, columnGap, padding } = GRAPH_GEOMETRY;
  const headerHeight = 22;

  /** The routes that travel through a node or edge, so a click can select one. */
  const select = (matcher: (edges: string[], nodes: string[]) => boolean): void => {
    const hit = explanation.paths.find((path) => matcher(path.edges, path.nodes));
    onSelectPath(hit === undefined ? null : hit.id);
  };

  return (
    <div className={styles.diagram}>
      <svg
        role="img"
        aria-label={graphSummary(explanation, layout)}
        width={layout.width}
        height={layout.height + headerHeight}
        viewBox={`0 0 ${layout.width} ${layout.height + headerHeight}`}
      >
        <title>How this principal reaches this directory</title>
        <desc>{graphSummary(explanation, layout)}</desc>
        <g aria-hidden="true">
          {layout.columns.map((column) => (
            <text
              key={`column-${column.index}`}
              className={styles.columnLabel}
              x={padding + column.index * (nodeWidth + columnGap)}
              y={14}
            >
              {column.label}
            </text>
          ))}

          {layout.edges.map((laid) => {
            const highlighted = onPathEdges.has(laid.edge.id);
            const midpoint = (laid.x1 + laid.x2) / 2;
            const d = `M ${laid.x1} ${laid.y1 + headerHeight} C ${midpoint} ${laid.y1 + headerHeight}, ${midpoint} ${laid.y2 + headerHeight}, ${laid.x2} ${laid.y2 + headerHeight}`;
            const classes = [
              styles.edgeLine,
              laid.edge.kind === "deny" ? styles.edgeLineDeny : null,
              laid.edge.kind === "assumed_membership" ? styles.edgeLineAssumed : null,
              highlighted ? styles.edgeLineOnPath : null,
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <g key={laid.edge.id} data-edge={laid.edge.id}>
                <path className={classes} d={d} />
                <path
                  className={styles.edgeTarget}
                  d={d}
                  onClick={() => select((edges) => edges.includes(laid.edge.id))}
                />
              </g>
            );
          })}

          {layout.nodes.map((laid) => {
            const highlighted = onPathNodes.has(laid.node.id);
            const classes = [
              styles.nodeBox,
              laid.node.kind === "principal" ? styles.nodeBoxSubject : null,
              laid.node.kind === "ntfs_ace" || laid.node.kind === "smb_ace"
                ? styles.nodeBoxAce
                : null,
              laid.node.kind === "assumed_trustee" ? styles.nodeBoxAssumed : null,
              highlighted ? styles.nodeBoxOnPath : null,
            ]
              .filter(Boolean)
              .join(" ");
            const label = nodeLabel(laid.node);
            return (
              <g
                key={laid.node.id}
                data-node={laid.node.id}
                onClick={() => select((_edges, nodes) => nodes.includes(laid.node.id))}
              >
                <rect
                  className={classes}
                  x={laid.x}
                  y={laid.y + headerHeight}
                  width={nodeWidth}
                  height={nodeHeight}
                />
                <text className={styles.nodeName} x={laid.x + 8} y={laid.y + headerHeight + 16}>
                  {truncate(label)}
                </text>
                <text className={styles.nodeKind} x={laid.x + 8} y={laid.y + headerHeight + 30}>
                  {nodeKindLabel(laid.node.kind)}
                  {laid.node.sid === null ? "" : ` · ${laid.node.sid}`}
                </text>
                <title>{`${label} — ${nodeKindLabel(laid.node.kind)}${laid.node.sid === null ? "" : ` (${laid.node.sid})`}`}</title>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

/**
 * What the picture says, in words.
 *
 * Read out in place of the diagram, and it names the counts rather than describing shapes:
 * a reader who cannot see it needs to know how many routes there are and whether the set
 * is complete, not that the boxes are arranged in columns.
 */
export function graphSummary(
  explanation: AccessExplanationResponse,
  layout: GraphLayout,
): string {
  const routes = explanation.paths.length;
  return (
    `How ${explanation.subject.display_name ?? explanation.subject.sid} reaches ` +
    `${explanation.resource.path ?? explanation.resource.key}: ` +
    `${layout.nodes.length} nodes and ${layout.edges.length} relationships, ` +
    `carrying ${routes} route${routes === 1 ? "" : "s"}` +
    `${explanation.complete ? "" : " (not all of them were enumerated)"}. ` +
    `The same relationships are listed in the tables below this diagram.`
  );
}

/** Long enough for a group name, short enough to stay inside the box. */
function truncate(value: string): string {
  return value.length > 24 ? `${value.slice(0, 23)}…` : value;
}
