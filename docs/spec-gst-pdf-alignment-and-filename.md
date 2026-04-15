# Spec: Fix GST PDF alignment and download filename behavior

## Deliverable
Update the GST PDF export so its amount fields align reliably and its download
uses a GST-specific filename.

## Rules
- The GST PDF route must return a stable `Content-Disposition` filename for the
  export.
- The GST report UI download action must use the download path, not blob-tab
  opening.
- GST amount values must be rendered by deterministic aligned overlay logic.
- Tests must fail if the filename header or GST download action regresses.
