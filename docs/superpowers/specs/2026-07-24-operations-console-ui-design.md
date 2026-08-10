# Operations Console UI Design Specification

**Status:** Approved visual direction; implementation source of truth  
**Language:** English UI; Chinese/Vietnamese only in source and localized content  
**Framework constraint:** Vite + Preact + TypeScript  

## 1. Visual references

Use these mockups as the composition and styling references:

1. `docs/ui-mockups/01-dashboard-inbox.png`
2. `docs/ui-mockups/02-localization-review.png`
3. `docs/ui-mockups/03-render-publish-review.png`

The mockups define hierarchy, density, layout, component geometry and interaction
placement. Their dates, IDs, titles, quota values and media are illustrative.
Runtime data and the approved system design take precedence. In particular, OCI
media capacity is 150 GB, not the 2 TB sample shown in the dashboard mockup.

## 2. Design system

- True white `#FFFFFF` canvas; cool-gray `#F7F9FC` selected/quiet surfaces.
- Near-black navy `#0F172A` primary text; `#475569` secondary text.
- Cobalt `#0969FF` primary/focus; green `#15803D` success; amber `#D97706`
  warning; red `#DC2626` critical.
- Borders `#D8E0EC`; 1 px default; 2 px visible keyboard focus.
- Inter or system sans-serif fallback. Page title 32/38 semibold, section title
  20/28 semibold, body/control 14/20, metadata 12/18.
- Spacing scale 4, 8, 12, 16, 24, 32 px. Control height 40 px. Radius 8 px.
- Shadows are limited to sticky bars and overlays; panels use borders/dividers.
- Icons use one 20 px outline family with consistent 1.5–2 px stroke.

## 3. Application shell and navigation

Desktop uses a 176 px fixed sidebar, fluid content region and optional
272–320 px inspector rail. Persistent navigation is Dashboard, Inbox, Sources,
Jobs, Glossary, Publishing and Settings. The active item uses a pale-blue
background and cobalt left indicator. The administrator control stays at the
sidebar bottom.

At 1024–1279 px, collapse the inspector into a drawer and reduce the sidebar to
icons with accessible tooltips. Below 1024 px, use a top app bar and navigation
drawer; data tables scroll horizontally. Review and publishing screens remain
desktop-first and show a warning below 768 px rather than hiding controls.

## 4. Screen behavior

### Dashboard and inbox

- Summary counters are an open strip, not independent cards.
- Inbox is server-paginated, sortable and filterable; filters are reflected in
  the URL query string.
- Rights, adapter and workflow status always combine icon, text and color.
- `Import` appears only when rights, duration and disk checks pass.
- Critical alerts remain visible until resolved or explicitly acknowledged.
- The operational rail uses actual quota limits and timestamps from the API.

### Localization review

- Route is version-addressed: `/jobs/:jobId/review/script/:scriptVersion`.
- Transcript and translation rows share immutable segment IDs and synchronized
  selection. Virtualize long transcripts; never paginate the two sides
  independently.
- Edits autosave as drafts after 750 ms idle and expose saving/saved/error state.
- Validation issues link to and focus the affected segment.
- `Approve script` remains disabled for blocking validation issues or unsaved
  changes and submits the displayed version/hash only.
- Any approved-content edit creates a new script version; it never mutates the
  approved version.

### Render and publishing review

- Route is `/jobs/:jobId/review/render/:renderVersion`.
- Preview uses the native video element with HTTP Range seeking and an expiring,
  render-bound URL.
- YouTube and Facebook retain separate metadata drafts and validation.
- Before YouTube staging, show `Stage YouTube private`; after processing, replace
  it with the processed state and do not show the staging action.
- Facebook displays `Upload after approval` until its hidden-staging gate passes.
- Final approval is disabled until the render checklist, metadata validation and
  required manual YouTube Studio review are complete.
- The sticky approval bar displays exact render version, checksum/hash,
  destinations and UTC schedule before confirmation.

## 5. Reusable component families

- `AppShell`, `SidebarNav`, `PageHeader`, `WorkflowStepRail`
- `OperationalAlert`, `StatusLabel`, `QuotaMeter`, `HealthList`
- `DataTable`, `FilterBar`, `Pagination`, `EmptyState`, `ErrorState`
- `SegmentReviewGrid`, `SegmentEditor`, `ValidationIssueList`,
  `GlossarySuggestionList`
- `VideoPreview`, `RenderChecklist`, `RetentionNotice`
- `DestinationTabs`, `YouTubeMetadataForm`, `FacebookMetadataForm`
- `StickyActionBar`, `VersionHashSummary`, `ConfirmationDialog`

Components expose loading, empty, disabled, saving, success, warning and failure
states. Destructive actions require a confirmation dialog naming the target and
consequence.

## 6. Accessibility and interaction

- Meet WCAG 2.2 AA contrast and keyboard operation.
- Use semantic landmarks, headings, tables, labels and live regions.
- Focus moves to the first error after failed validation and returns to the
  invoking control after dialogs close.
- Status changes announce through a polite live region; critical lease,
  rights or publication failures use assertive announcements.
- Never rely on color alone. Respect reduced motion; transitions are 120–180 ms.
- Preserve unsaved drafts on navigation failure and warn before abandoning
  confirmed unsaved changes.

## 7. Visual acceptance

Validate at 1536×1024, 1280×800 and 1024×768. Compare browser screenshots to
each reference for shell geometry, typography, spacing, borders, controls,
status treatment and sticky actions. No screen passes visual review with
placeholder copy, default browser controls, invented cards, gradients,
decorative analytics or altered information hierarchy.
